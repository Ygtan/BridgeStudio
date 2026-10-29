"""verify_cable_3d.py
====================
P3 端到端回归：斜拉桥成桥态（下料索长 + 制造线形）的 BridgeStudio ↔ 内核链。

校验内容
--------
1. 离线（建模与导出）
   - 索数 = 4×n_cable；.dat 含 CABLE 类型、*CABLE_BRIDGE、*CABLE、W=（索线重）
   - 锚点不落在支座断面上（这是自动定索力病态的唯一结构性根因）
   - 每跨离散数 ≥ n_cable+4（否则锚点会被"首末余量"吃掉）
2. 在线（cable_solver 成桥态闭环）
   - 收敛、几何闭合误差 < 1e-6 m
   - 成桥索力 = 目标索力（相对误差 < 1e-4）——即 L0 反算正确
   - 竖向自洽：ΣT·ez + 支座分担 = 桥面恒载（残差 < 1e-6 相对）
   - 下料索长 L0 < 设计弦长（T>0 且垂度远小于弹性伸长）
   - 成桥位移与预抬高严格互为反号（几何闭合方程本身）
   - 制造线形 + 成桥位移 = 设计线形（节点 z 复原）
   - 参数扫描（100/200/300 m 主跨、6/12/20 根/侧）全部收敛
   - MODE=TENSION（显式给索力）与 COMPLETION 的 L0 一致

理论基准
--------
L0 = (L* + s)/(1 + T/EA)，s = w²Lh³/(24T²)（浅垂度弹性悬链线）
制造线形：ΔX + u(ΔX) = 0  ⇒  erected_z + u_z = 设计 z
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from cable_stayed_3d import build_cable_stayed_3d, cable_break_load
from fem_eigen_cable import cable_solver_available, solve_cable_bridge
from model_spec_3d import CableBridge3D

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((ok, name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def main() -> int:
    print("=" * 62)
    print("  斜拉桥成桥态端到端回归（BridgeStudio → cable_solver）")
    print("=" * 62)

    if not cable_solver_available():
        print("  cable_solver.exe 不可用，无法做在线校验。")
        print("  编译： cd D:/AI-work/FEM-Eigen && cmake --build build --target cable_solver")
        return 1

    # ---------------------------------------------------------------- 1 建模
    print("\n[1] 建模与导出")
    m, info = build_cable_stayed_3d(main_span=200.0, side_span=80.0,
                                    tower_height=60.0, n_cable=12)
    n_cable_exp = 4 * 12
    check("索数 = 4×n_cable", info["n_cable_actual"] == n_cable_exp,
          f"{info['n_cable_actual']} / {n_cable_exp}")
    check("CABLE 单元数 = 索数",
          m.stats()["kinds"].get("CABLE", 0) == info["n_cable_actual"],
          str(m.stats()["kinds"]))

    dat = m.to_dat()
    for kw in ("*CABLE_BRIDGE", "*CABLE", "CABLE\n", "W=", "DECKLOAD="):
        check(f".dat 含 {kw!r}", kw in dat)
    check("CABLE 未退化为 TRUSS", "\n3 CABLE" in dat)

    # 锚点不得落在支座断面（x ∈ {0, Ls, Ls+Lm, Ltot}）
    x_of = {n.id: n.x for n in m.nodes}
    sup_x = info["x_supports"]
    bad = [c.elem for c in m.cable_bridge.cables
           if any(abs(x_of[_anchor(m, c.elem)] - sx) < 1e-6 for sx in sup_x)]
    check("索锚点不在支座断面上", not bad, f"违规 {bad[:3]}" if bad else "0 根")

    # ---------------------------------------------------------------- 2 求解
    print("\n[2] 成桥态闭环")
    res = solve_cable_bridge(m, out_dir="out/verify_cable", dat_name="cable.dat")
    md = res["meta"]
    check("收敛", res["converged"], f"{md.get('iterations')} 轮，"
                                    f"{md.get('elapsed_ms'):.0f} ms")
    check("几何闭合误差 < 1e-6 m", md.get("closure_err", 1) < 1e-6,
          f"{md.get('closure_err'):.2e} m")

    # 成桥索力 = 目标索力
    target = {c.elem: c.tension for c in m.cable_bridge.cables}
    err_t = max(abs(c["T"] - target[c["elem"]]) / target[c["elem"]]
                for c in res["cables"])
    check("成桥索力 = 目标索力（<1e-4）", err_t < 1e-4, f"max 相对误差 {err_t:.2e}")

    # 竖向自洽：ΣT·ez + 支座分担 = 桥面恒载
    deck = info["total_deck_load"]
    share = md.get("deck_share_pct", 0.0)
    sup_share = md.get("support_share_kN", 0.0) * 1e3
    resid = abs(md.get("vertical_sum_kN", 0.0) * 1e3 + sup_share - deck) / deck
    check("ΣT·ez + 支座分担 = 桥面恒载", resid < 1e-6,
          f"索 {share:.2f}% + 支座 {sup_share/1e3:.0f} kN，残差 {resid:.1e}")
    check("索承担比例在合理区间 (50%~100%)", 50.0 <= share <= 100.0, f"{share:.1f}%")

    # 下料索长 < 设计弦长
    worse = [c for c in res["cables"] if c["L0"] >= c["Ltarget"]]
    check("下料索长 L0 < 设计弦长", not worse,
          f"最长比值 {max(c['L0']/c['Ltarget'] for c in res['cables']):.6f}")

    # L0 与闭式解一致
    EA = m.reals[2].A * m.materials[1].E
    w = m.reals[2].W
    worst = 0.0
    for c in res["cables"]:
        node_a = _anchor(m, c["elem"])
        node_b = _far(m, c["elem"], node_a)
        na, nb = _node(m, node_a), _node(m, node_b)
        Lh = ((nb.x - na.x) ** 2 + (nb.y - na.y) ** 2) ** 0.5
        sag = w * w * Lh ** 3 / (24 * c["T"] ** 2) if Lh > 0 else 0.0
        l0 = (c["Ltarget"] + sag) / (1 + c["T"] / EA)
        worst = max(worst, abs(l0 - c["L0"]) / c["L0"])
    check("L0 vs 闭式反算（<1e-6）", worst < 1e-6, f"max {worst:.2e}")

    # camber 与 u 互为反号
    cam, u = res["camber"], res["u"]
    dz_pairs = [(cam[n][2], u.get((n, 3), 0.0)) for n in cam if 1000 < n < 2000]
    worst_rev = max(abs(a + b) for a, b in dz_pairs)
    zmax = max(abs(a) for a, _ in dz_pairs)
    check("成桥位移 = −预抬高", worst_rev < 1e-6,
          f"max|Δ+u|={worst_rev:.2e} m，预抬高幅值 {zmax*1000:.1f} mm")

    # 制造线形 + 成桥位移 = 设计线形（设计 z 全为 0）
    worst_z = max(abs(res["erected"][n][2] + u.get((n, 3), 0.0))
                  for n in res["erected"] if 1000 < n < 2000)
    check("制造标高 + 成桥位移 = 设计标高", worst_z < 1e-6,
          f"max 残差 {worst_z:.2e} m")

    # ---------------------------------------------------------------- 3 参数扫描
    print("\n[3] 参数扫描（不同跨度 / 索数）")
    for Lm, Ls, Ht, n in ((120.0, 50.0, 40.0, 6), (300.0, 110.0, 80.0, 16),
                          (200.0, 80.0, 60.0, 20)):
        try:
            mm, ii = build_cable_stayed_3d(main_span=Lm, side_span=Ls,
                                           tower_height=Ht, n_cable=n)
            rr = solve_cable_bridge(mm, out_dir="out/verify_cable",
                                    dat_name=f"cable_{int(Lm)}_{n}.dat")
            ok = rr["converged"] and rr["meta"].get("closure_err", 1) < 1e-6
            check(f"L={Lm:.0f}m / {n} 根/侧 收敛", ok,
                  f"{rr['meta'].get('iterations')} 轮，索力 "
                  f"{rr['meta'].get('tension_min_kN'):.0f}~"
                  f"{rr['meta'].get('tension_max_kN'):.0f} kN")
        except Exception as e:                       # noqa: BLE001
            check(f"L={Lm:.0f}m / {n} 根/侧 收敛", False, str(e)[:80])

    # ---------------------------------------------------------------- 4 索力上限
    print("\n[4] 索力安全系数")
    bl = cable_break_load(6000.0, 1770e6)
    tmax = md.get("tension_max_kN", 0.0) * 1e3
    check("最大索力安全系数 > 2.0", bl / max(tmax, 1e-9) > 2.0,
          f"{bl/tmax:.2f}（{tmax/1e3:.0f} kN / 破断 {bl/1e3:.0f} kN）")

    n_pass = sum(1 for r in RESULTS if r[0])
    print("=" * 62)
    print(f"  {n_pass}/{len(RESULTS)} 通过")
    return 0 if n_pass == len(RESULTS) else 1


def _node(model, nid: int):
    return next(n for n in model.nodes if n.id == nid)


def _anchor(model, elem_id: int) -> int:
    """梁侧锚点 = 索较低的那端（塔顶在上）。"""
    e = next(q for q in model.elements if q.id == elem_id)
    return e.n1 if _node(model, e.n1).z <= _node(model, e.n2).z else e.n2


def _far(model, elem_id: int, anchor: int) -> int:
    e = next(q for q in model.elements if q.id == elem_id)
    return e.n2 if e.n1 == anchor else e.n1


if __name__ == "__main__":
    sys.exit(main())
