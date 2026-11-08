"""verify_truss_3d.py
====================
三维空间钢桁架桥（两片竖向 Warren 桁架 + 上下弦横向联系）回归。

这是 6 桥型 × 2 维度正交矩阵里最后补上的一格 ``("truss","3d")``：
此前只有平面桁架（2d），缺空间桁架（3d）。纯 TRUSS 单元，节点只有 3 个
平动自由度，`cable_bridge=None` 让 `detect_kind` 落到 `grillage3d` 线性
阶段内核（与 `arch_3d` 同一条链）。

校验内容
--------
1. 离线（建模）
   - 节点/单元规模与拓扑（弦杆 30 / 腹杆 32 / 横向联系 47）
   - 单元构成全部 TRUSS
   - 支座：左端两片纵向固定、右端释放纵向（温度伸缩）；上弦两端锁横向
2. 自然语言识别（意图分类 + 参数解析）
   - 「空间桁架桥」→ truss×3d；普通「桁架桥」仍走 2d
   - 跨度/桁高/桥宽/节间数解析；mm 误写被单位哨兵纠正
3. 在线（线性阶段内核）
   - 收敛；跨中挠度 ≈ 弦杆力偶理想化闭式解（5qL⁴/384EI），且 ≤ L/400
   - 弦杆轴力由位移反算（内核不输出 TRUSS 轴力，N = EA·ΔL/L）
   - 下弦受拉、上弦受压；左右两片严格镜像对称；跨中弦力 ≈ M/h
"""

from __future__ import annotations

import math

from console_utf8 import setup_console

setup_console()

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from model_doc import ModelDoc, classify_text
from solver_3d import solve
from truss_3d import build_truss_3d

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((ok, name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def back_calc_axial(model, final_disp) -> dict[int, float]:
    """由节点位移反算 TRUSS 轴力 N = EA·ΔL/L（拉为正）。

    线性内核（example_3d_grillage）只输出 BEAM 梁端内力，TRUSS 轴力是
    空间桁架的核心结果却缺失。这里从成桥态位移 `final_disp` 反算：
    相对位移投影到单元轴向得到伸长 ΔL，再乘 EA/L。
    """
    node = {n.id: n for n in model.nodes}
    etype = {t.id: t for t in model.etypes}
    real = {r.id: r for r in model.reals}
    mat = {mm.id: mm for mm in model.materials}
    out: dict[int, float] = {}
    for e in model.elements:
        t = etype[e.etype]
        A = real[t.real].A
        E = mat[t.mat].E
        n1, n2 = node[e.n1], node[e.n2]
        dx, dy, dz = n2.x - n1.x, n2.y - n1.y, n2.z - n1.z
        L = math.hypot(dx, dy, dz)
        ux, uy, uz = dx / L, dy / L, dz / L
        d1 = [final_disp.get((e.n1, k), 0.0) for k in (1, 2, 3)]
        d2 = [final_disp.get((e.n2, k), 0.0) for k in (1, 2, 3)]
        dL = (d2[0] - d1[0]) * ux + (d2[1] - d1[1]) * uy + (d2[2] - d1[2]) * uz
        out[e.id] = E * A * dL / L
    return out


def main() -> int:
    print("=" * 66)
    print("  三维空间钢桁架桥端到端回归（BridgeStudio → 线性阶段内核）")
    print("=" * 66)

    # ---------------------------------------------------------------- 1 建模
    print("\n[1] 建模与拓扑")
    m, info = build_truss_3d()
    st = m.stats()
    check("节点/单元规模", st["nodes"] == 34 and st["elements"] == 109,
          f"{st['nodes']} 节点 / {st['elements']} 单元")
    check("单元构成全部 TRUSS", st["kinds"] == {"TRUSS": 109}, str(st["kinds"]))
    check("弦杆 30 / 腹杆 32 / 横向联系 47",
          len(info["chord_elems"]) == 30 and len(info["diag_elems"]) == 32
          and len(info["lat_elems"]) == 47,
          f"{len(info['chord_elems'])}/{len(info['diag_elems'])}/"
          f"{len(info['lat_elems'])}")

    # 支座：左端两片纵向固定、右端释放纵向 u；上弦两端各锁一个横向 y。
    bs = {b.node: b.dofs for b in m.boundaries}
    check("左端左片 u/v/w + 左端右片 u/v/w",
          bs.get(1) == (1, 3) and bs.get(201) == (1, 3),
          f"{bs.get(1)} / {bs.get(201)}")
    check("右端两片释放纵向（v/w）",
          bs.get(9) == (2, 3) and bs.get(209) == (2, 3),
          f"{bs.get(9)} / {bs.get(209)}")
    check("上弦两端各锁横向 y（门架/端横联）",
          bs.get(101) == (2, 2) and bs.get(301) == (2, 2)
          and bs.get(108) == (2, 2) and bs.get(308) == (2, 2),
          f"{bs.get(101)} / {bs.get(108)}")

    # ---------------------------------------------------------------- 2 意图
    print("\n[2] 自然语言识别")
    check("「空间桁架桥」→ truss×3d",
          classify_text("空间桁架桥，跨度60米，8个节间，桁高6米，桥宽10米")
          == ("truss", "3d"))
    check("普通「桁架桥」仍走 2d",
          classify_text("跨度60米的桁架桥，8个节间，桁高6米") == ("truss", "2d"))

    doc, src, diag = ModelDoc.from_text(
        "空间桁架桥，跨度60米，8个节间，桁高6米，桥宽10米，桥面恒载100kN/m")
    check("from_text → truss×3d", doc.kind == "truss" and doc.dimension == "3d", src)
    check("跨度/桁高/桥宽/节间解析",
          doc.params["span"] == 60.0 and doc.params["depth"] == 6.0
          and doc.params["width"] == 10.0 and doc.params["n_panel"] == 8,
          f"span={doc.params['span']} depth={doc.params['depth']} "
          f"width={doc.params['width']} n={doc.params['n_panel']}")
    check("校验通过", not doc.validate(), "; ".join(doc.validate()))

    # mm 误写：跨度 60000mm、桁高 6000、桥宽 10000mm 都该被纠正为 m。
    doc2, _, diag2 = ModelDoc.from_text(
        "空间桁架桥跨度60000mm，桁高6000，桥宽10000毫米，8节间")
    check("mm 误写纠正",
          doc2.params["span"] == 60.0 and doc2.params["depth"] == 6.0
          and doc2.params["width"] == 10.0,
          f"span={doc2.params['span']} depth={doc2.params['depth']} "
          f"width={doc2.params['width']}")

    # ---------------------------------------------------------------- 3 求解
    print("\n[3] 求解与物理校核")
    res = solve(m, out_dir="out/verify_truss_3d", dat_name="truss.dat")
    check("线性求解收敛", res.converged, res.solver)

    fd = res.final_disp
    # 跨中挠度：弦杆力偶理想化 I_eff = 2·A_chord·(h/2)²（每片），全桥 ×2。
    L, h, q = info["span"], info["depth"], info["q_deck"]
    A_chord = info["A_chord"]
    I_eff = 2.0 * (2.0 * A_chord * (h / 2.0) ** 2)
    E = 206e9
    d_beam = 5.0 * q * L ** 4 / (384.0 * E * I_eff)
    d_max = max(abs(fd[(n, 3)]) for n in info["lower_node_ids"])
    check("跨中挠度 ≈ 闭式解（±30%）", 0.70 * d_beam <= d_max <= 1.30 * d_beam,
          f"{d_max * 1e3:.0f} mm vs 闭式 {d_beam * 1e3:.0f} mm")
    check("跨中挠度 ≤ L/400", d_max <= L / 400.0, f"{d_max * 1e3:.0f} mm")

    # 弦杆轴力反算 + 符号/对称/量级校核
    N = back_calc_axial(m, fd)
    node = {n.id: n for n in m.nodes}
    by_id = {e.id: e for e in m.elements}
    chord = [by_id[i] for i in info["chord_elems"]]
    bottom = [e for e in chord if node[e.n1].z < h / 2.0]
    top = [e for e in chord if node[e.n1].z > h / 2.0]
    Nb = [N[e.id] for e in bottom]
    Nt = [N[e.id] for e in top]
    check("下弦全部受拉（N > 0）", all(x > 0 for x in Nb),
          f"max {max(Nb) / 1e3:.0f} kN")
    check("上弦全部受压（N < 0）", all(x < 0 for x in Nt),
          f"min {min(Nt) / 1e3:.0f} kN")

    left = [e for e in bottom if node[e.n1].y < 0]
    right = [e for e in bottom if node[e.n1].y > 0]
    Nl = [N[e.id] for e in left]
    Nr = [N[e.id] for e in right]
    sym = max(abs(a - b) for a, b in zip(Nl, Nr))
    check("左右两片弦力镜像对称", sym <= 1.0,
          f"max 差 {sym / 1e3:.2f} kN")

    N_ref = (q * L ** 2 / 8.0) / h / 2.0       # Mmax/h 分摊到每片
    check("跨中下弦力 ≈ M/h（±15%）",
          abs(max(Nb) - N_ref) / N_ref <= 0.15,
          f"{max(Nb) / 1e3:.0f} vs {N_ref / 1e3:.0f} kN")

    print("\n" + "=" * 66)
    n_fail = sum(1 for ok, _, _ in RESULTS if not ok)
    print(f"  {len(RESULTS)} 项校验，{len(RESULTS) - n_fail} 通过，{n_fail} 失败")
    print("=" * 66)
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
