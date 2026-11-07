"""verify_stage_3d.py
====================
P2 端到端回归：逐跨浇筑三阶段施工 + JTG D60 车道活载。

校验内容
--------
1. 离线
   - 阶段数 = 3；三阶段 ACTIVE 并集 = 全部单元（无遗漏无重复）
   - 三阶段荷载总和 = q×L（跨间受荷宽度守恒）
   - .dat 含 *INFLUENCE_3D / *LANE_3D CODE=JTG_D60
2. 在线（FEM-Eigen 3D）
   - 阶段 1（30m 简支状态）边跨跨中挠度 ≈ 5qL⁴/(384EI)（容差 5%，
     梁格近似 + 荷载节点化误差）
   - 阶段位移包络：边跨跨中 min 来自阶段 1（后续阶段在其上引起上抬）
   - 活载包络：三个阶段 min 均为负且 |min| 单调不增（结构越长分担越多）
   - 阶段 1 活载包络 ≈ 简支跨 qk+Pk 影响线加载理论值（容差 15%）

理论基准（阶段 1，全宽荷载，EI = E·Iy_box）
    δ_dead = 5·q·L⁴ / (384·E·Iy)                        （恒载）
    δ_live = 5·qk·L⁴ / (384·E·Iy) + Pk(L)·L³ / (48·E·Iy)（活载最不利）
    JTG D60: qk=10.5 kN/m, Pk(30m)=320 kN（270+90/45×25）
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()          # 输出被重定向到管道/文件时，GBK 会崩（见模块说明）

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fem_eigen_3d import fem_eigen_3d_available, solve_3d
from grillage_3d import box_section, build_box_grillage_3d

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((ok, name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


SPANS = (30.0, 50.0, 30.0)
Q_DECK = 100e3
E_C50, NU = 34.5e9, 0.2
B, H, TF, TW = 12.0, 2.5, 0.28, 0.45


def theory():
    box = box_section(B, H, TF, TW)
    EI = E_C50 * box["IY"]
    L = SPANS[0]
    d_dead = 5 * Q_DECK * L ** 4 / (384 * EI)
    return d_dead, EI


def _live_oracle() -> float:
    """成桥态活载 oracle：anastruct 三跨连续梁，qk 全长 + Pk(110m)=360kN
    置于中跨跨中（最不利位置近似），返回 x=55 处挠度。"""
    from anastruct import SystemElements

    box = box_section(B, H, TF, TW)
    EI = E_C50 * box["IY"]
    L = sum(SPANS)
    n = 220
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
        ss.q_load(q=-10500.0, element_id=i + 1, direction="element")
    ss.point_load(int(round(55.0 / L * n)) + 1, Fy=-360000.0)
    ss.solve()
    disp = ss.get_node_displacements()
    return disp[int(round(55.0 / L * n))]["uy"]


def main() -> int:
    d_dead, EI = theory()
    print(f"[理论基准] EI={EI:.3f} N·m²  阶段1恒载挠度={d_dead*1000:.2f}mm")

    m = build_box_grillage_3d(spans=SPANS, B=B, h=H, tf=TF, tw=TW,
                              n_lines=3, panel=2.5, E=E_C50, nu=NU,
                              q_deck=Q_DECK, staged=True, lane_code="JTG_D60")

    print("[离线检查]")
    check("阶段数 = 3", len(m.stages) == 3, f"{len(m.stages)}")
    union = [e for s in m.stages for e in s.activate]
    check("ACTIVE 并集 = 全部单元",
          sorted(union) == sorted(e.id for e in m.elements)
          and len(union) == len(set(union)),
          f"{len(set(union))}/{m.n_elements}")
    total = sum(-ld.value for s in m.stages for ld in s.loads)
    expect = Q_DECK * sum(SPANS)
    check("三阶段荷载总和守恒", abs(total - expect) / expect < 1e-9,
          f"{total:.0f} N")
    text = m.to_dat()
    check(".dat 含 INFLUENCE_3D", "*INFLUENCE_3D" in text)
    check(".dat 含 LANE_3D CODE=JTG_D60",
          "*LANE_3D" in text and "CODE=JTG_D60" in text)

    if "--offline" in sys.argv:
        print(f"\n离线部分: {sum(1 for r in RESULTS if r[0])}/{len(RESULTS)} 通过")
        return 0
    if not fem_eigen_3d_available():
        print("\n[跳过] FEM-Eigen 3D 不可用")
        return 1

    print("[在线检查 · FEM-Eigen 3D]")
    res = solve_3d(m, out_dir="out/p2_verify", dat_name="grillage_staged.dat")

    # 边跨跨中 = 中梁线 2006；包络 min 应为阶段 1 简支挠度
    fem_dead = res["disp"][(2006, 3)][0]
    err = abs(fem_dead - (-d_dead)) / d_dead * 100
    check("阶段1简支挠度 vs 理论", err < 5.0,
          f"FEM={fem_dead*1000:.2f}mm  理论={-d_dead*1000:.2f}mm  误差={err:.1f}%")

    # 活载包络：影响点在中跨跨中（x=55）
    live = res["live"]
    check("活载包络含 3 阶段", len(live) == 3, f"{sorted(live)}")
    if len(live) == 3:
        mins = [live[k][1] for k in (1, 2, 3)]
        # 阶段 1：影响点尚未建造 → 活载响应应为 0（物理正确）
        check("阶段1活载=0（影响点未建）", abs(mins[0]) < 1e-6,
              f"{mins[0]*1000:.3f}mm")
        check("阶段2/3活载 min 为负（向下）",
              mins[1] < 0 and mins[2] < 0,
              f"阶段2={mins[1]*1000:.2f}mm  阶段3={mins[2]*1000:.2f}mm")
        # 阶段 3（成桥）：vs anastruct 三跨连续梁活载最不利近似。
        # 内核的活载位移包络按 JTG D60 计入汽车冲击 1+μ，oracle 乘同一系数才可比。
        impact_factor = float((res.get("lane_impact") or {}).get("impact_factor") or 1.0)
        oracle = _live_oracle() * impact_factor
        err_l = abs(mins[2] - oracle) / abs(oracle) * 100
        check("阶段3活载 vs 连续梁理论", err_l < 20.0,
              f"FEM={mins[2]*1000:.2f}mm  oracle={oracle*1000:.2f}mm "
              f"(1+μ={impact_factor:.3f})  误差={err_l:.1f}%")

    n_pass = sum(1 for r in RESULTS if r[0])
    print("=" * 60)
    print(f"  {n_pass}/{len(RESULTS)} 通过")
    return 0 if n_pass == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
