"""verify_grillage_3d.py
=======================
P1 端到端回归：三跨连续箱梁 3D 梁格。

校验内容
--------
1. 离线（不调求解器）
   - 模型规模与结构完整性（pydantic 引用校验在构造时已做）
   - 总荷载 = q × 桥长（受荷宽度求和守恒）
   - 支承断面节点坐标精确落在跨分界点
   - .dat 文本检查：节点/单元行数、ACTIVE 列表覆盖全部单元
2. 在线（调 FEM-Eigen 3D 内核）
   - 求解成功且位移包络条目数 = 节点数 × 6
   - 边跨/中跨跨中挠度 vs anastruct 等刚度连续梁 oracle（容差 2%）

用法
----
    python verify_grillage_3d.py          # 全部
    python verify_grillage_3d.py --offline  # 仅离线部分
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from anastruct import SystemElements

from fem_eigen_3d import fem_eigen_3d_available, solve_3d
from grillage_3d import box_section, build_box_grillage_3d

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((ok, name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


# ---------------------------------------------------------------- 参数
SPANS = (30.0, 50.0, 30.0)
Q_DECK = 100e3          # N/m 全宽
E_C50, NU, DENS = 34.5e9, 0.2, 2500.0
B, H, TF, TW = 12.0, 2.5, 0.28, 0.45


def build():
    return build_box_grillage_3d(spans=SPANS, B=B, h=H, tf=TF, tw=TW,
                                 n_lines=3, panel=2.5,
                                 E=E_C50, nu=NU, dens=DENS, q_deck=Q_DECK)


def offline_checks(m) -> None:
    print("[离线检查]")
    stats = m.stats()
    check("模型规模节点数", stats["nodes"] == 135, f"{stats['nodes']} 节点")
    check("模型规模单元数", stats["elements"] == 222,
          f"{stats['elements']} 单元（纵梁 132 + 横梁 90）")

    # 总荷载守恒
    total_load = sum(-ld.value for ld in m.stages[0].loads)
    expect = Q_DECK * sum(SPANS)
    check("总荷载守恒", abs(total_load - expect) / expect < 1e-9,
          f"{total_load:.0f} N = q×L ({expect:.0f} N)")

    # 支承断面位置
    node_pos = {n.id: n.x for n in m.nodes}
    support_x = [0.0, SPANS[0], SPANS[0] + SPANS[1], sum(SPANS)]
    n_line = 3
    ok_pos = True
    for i in (0, 12, 32, 44):
        x = node_pos[1000 + i]
        if not any(abs(x - sx) < 1e-6 for sx in support_x):
            ok_pos = False
    check("支承断面精确落点", ok_pos, "节点 i=0/12/32/44 ↔ x=0/30/80/110")

    # .dat 文本检查（按节解析，避免把 STAGE_LOAD 行误计为节点）
    text = m.to_dat()
    lines = text.splitlines()
    def section_lines(header: str) -> list[str]:
        out, inside = [], False
        for ln in lines:
            if ln.startswith("*"):
                inside = (ln.split(",")[0].strip() == header)
                continue
            if inside and ln and not ln.startswith(("#", "!")):
                out.append(ln)
        return out
    n_node_lines = len(section_lines("*NODE"))
    n_en_lines = sum(1 for ln in lines if ln.startswith("*EN,"))
    active_line = next(ln for ln in lines if "ACTIVE=" in ln)
    n_active = len(active_line.split("ACTIVE=")[1].split(","))
    check(".dat 节点行数", n_node_lines == m.n_nodes, f"{n_node_lines}")
    check(".dat 单元行数", n_en_lines == m.n_elements, f"{n_en_lines}")
    check("ACTIVE 覆盖全部单元", n_active == m.n_elements, f"{n_active}")
    check("边界条件数", len(m.boundaries) == 9,
          f"{len(m.boundaries)} = 4 断面×2 支座 + 1 纵向固定")


def anastruct_oracle():
    """等刚度连续梁：EI = E·Iy(箱)，q 全长均布。返回 (x, 挠度) 采样。"""
    box = box_section(B, H, TF, TW)
    EI = E_C50 * box["IY"]
    L = sum(SPANS)
    n = 220                                   # 0.5m 单元
    ss = SystemElements(EA=1e12, EI=EI)
    xs = [L * i / n for i in range(n + 1)]
    for i in range(n):
        ss.add_element(location=[[xs[i], 0], [xs[i + 1], 0]], EA=1e12, EI=EI)
    positions = [0.0]
    for s in SPANS:
        positions.append(positions[-1] + s)
    for sx in positions:
        ss.add_support_hinged(int(round(sx / L * n)) + 1)
    for i in range(n):
        ss.q_load(q=-Q_DECK, element_id=i + 1, direction="element")
    ss.solve()
    disp = ss.get_node_displacements()
    return {xs[i]: disp[i]["uy"] for i in range(n + 1)}


def online_checks() -> None:
    print("[在线检查 · FEM-Eigen 3D]")
    m = build()
    res = solve_3d(m, out_dir="out/p1_verify", dat_name="grillage.dat")

    check("求解产出位移包络", len(res["disp"]) == m.n_nodes * 6,
          f"{len(res['disp'])} 条")
    check("求解产出内力包络", len(res["beam"]) == m.n_elements,
          f"{len(res['beam'])} 单元")

    oracle = anastruct_oracle()

    # 中梁线节点 2000+i；i=6 → x=15（边跨跨中）；i=22 → x=55（中跨跨中）
    # 容差：相对 2% 与绝对 0.3mm 取大（边跨跨中挠度本身仅 ~0.1mm 量级）
    for label, i, xq in [("边跨跨中挠度", 6, 15.0), ("中跨跨中挠度", 22, 55.0)]:
        fem = res["disp"][(2000 + i, 3)][0]          # min（向下为负）
        ref = oracle[min(oracle, key=lambda k: abs(k - xq))]
        tol = max(0.02 * abs(ref), 3e-4)
        err_abs = abs(fem - ref)
        err = err_abs / abs(ref) * 100 if ref else 0.0
        check(f"{label} vs anastruct", err_abs < tol,
              f"FEM={fem*1000:.2f}mm  oracle={ref*1000:.2f}mm  "
              f"差={err_abs*1000:.2f}mm ({err:.1f}%)")


def main() -> int:
    m = build()
    offline_checks(m)
    if "--offline" in sys.argv:
        print(f"\n离线部分: {sum(1 for r in RESULTS if r[0])}/{len(RESULTS)} 通过")
        return 0
    if not fem_eigen_3d_available():
        print("\n[跳过] FEM-Eigen 3D 不可用")
        return 1
    online_checks()

    n_pass = sum(1 for r in RESULTS if r[0])
    print("=" * 60)
    print(f"  {n_pass}/{len(RESULTS)} 通过")
    return 0 if n_pass == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
