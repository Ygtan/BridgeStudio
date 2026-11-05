"""verify_solve_2d.py —— 二维桥梁求解链（solve_2d）的端到端验证。

不是"能跑通"就算过，每条判据都对到物理量。注意 `build_beam_bridge` 的
`span` 是**全桥总长**（`n_spans` 把它等分），所以每跨 = span / n_spans。

1. 简支梁成桥态 vs 理论解：M=wL²/8、v=5wL⁴/(384EI)，误差应 ~0（验证解析
   与 final_beam 映射正确）；
2. 三跨连续梁（一次性）中间支座弯矩 ≈ -wL²/10（三弯矩定理，L 为单跨）；
3. 三跨连续梁（逐跨施工）成桥态**竖向平衡**：支座反力合计 = 外荷载——这是
   增量法正装不产生"凭空荷载"的铁证；
4. **增量法正装 == 线性叠加**：线性、无时变效应的梁桥，逐跨施工成桥态应等于
   一次性浇筑（叠加原理）。两者不一致说明增量法把某阶段的临时构型漏算/多算
   进了成桥态；
5. **施工阶段包络捕获临时状态**：阶段 1 的边跨是简支态，跨中弯矩 = wL²/8，
   远大于成桥态的连续梁跨中弯矩。阶段包络里应能看到这个 2250 kN·m 的峰值——
   这正是施工阶段分析区别于"只算成桥态"的价值；
6. 规范组合：三种组合齐全、活载项在场（has_live=True）、控制弯矩有物理量级。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import sys
from pathlib import Path

from bridge_spec import build_beam_bridge, _section_props
from solve_2d import solve_2d, solve_2d_available

OUT = Path(__file__).parent / "out" / "verify_solve_2d"
PASS, FAIL = "PASS", "FAIL"


def _reactions(final_beam: dict, n_elem: int, support_nodes: set[int]) -> dict[int, float]:
    """由端剪力跳差反推支座反力。单元 e 连节点 e 与 e+1（build_beam_bridge 拓扑）。"""
    r: dict[int, float] = {}
    for k in sorted(support_nodes):
        if k == 1:
            r[k] = final_beam.get((1, "i"), {}).get("Vz", 0.0)
        elif k == n_elem + 1:
            r[k] = -final_beam.get((n_elem, "j"), {}).get("Vz", 0.0)
        else:
            r[k] = (final_beam.get((k, "i"), {}).get("Vz", 0.0)
                    - final_beam.get((k - 1, "j"), {}).get("Vz", 0.0))
    return r


def main() -> int:
    print("=" * 78)
    print("二维桥梁求解链（solve_2d）端到端验证")
    print("=" * 78)
    verdicts: list[tuple[str, str, str]] = []

    if not solve_2d_available():
        print("2D 内核不可用，全部跳过")
        return 1

    E = 2.06e11
    Lspan = 30.0                                   # 单跨跨径 (m)

    # ---------------- 1. 简支梁 vs 理论解 ----------------
    w = 20.0e3                                     # N/m
    p = _section_props("box", 2.0, 8.0, 0.02, 0.025)
    EI = E * p["I"]
    b1 = build_beam_bridge(span=Lspan, n_spans=1, udl_kN=20.0, gravity=0.0)
    r1 = solve_2d(b1, out_dir=OUT / "beam30", dat_name="beam30.dat")
    print(f"\n[1] {b1.title}")
    m_num = max(abs(v["My"]) for v in r1.final_beam.values())
    v_num = min(v for (n, dof), v in r1.final_disp.items() if dof == 2)
    m_th, v_th = w * Lspan ** 2 / 8.0, -5.0 * w * Lspan ** 4 / (384.0 * EI)
    dm = abs(m_num - m_th) / m_th * 100
    dv = abs(v_num - v_th) / abs(v_th) * 100
    print(f"    Mmax {m_num/1e3:.2f} kN·m（理论 {m_th/1e3:.2f}）误差 {dm:.4f}%")
    print(f"    vmax {v_num*1000:.4f} mm（理论 {v_th*1000:.4f}）误差 {dv:.4f}%")
    ok = dm < 0.5 and dv < 0.5
    verdicts.append(("简支梁 vs 理论解", PASS if ok else FAIL, f"弯矩{dm:.3f}% 挠度{dv:.3f}%"))

    # ---------------- 2. 三跨连续梁（一次性）中间支座弯矩 ----------------
    total2 = Lspan * 3
    b2 = build_beam_bridge(span=total2, n_spans=3, udl_kN=20.0, gravity=0.0)
    r2 = solve_2d(b2, out_dir=OUT / "beam3x30", dat_name="beam3x30.dat")
    n_elem2 = len(b2.elements)
    sup_node = n_elem2 // 3 + 1                     # 第一中间支座节点号
    m_sup = r2.final_beam.get((sup_node - 1, "j"), {}).get("My", 0.0)
    m_th2 = -w * Lspan ** 2 / 10.0                  # 三弯矩定理：中间支座 M = -wL²/10
    d2 = abs(m_sup - m_th2) / abs(m_th2) * 100
    print(f"\n[2] {b2.title}")
    print(f"    中间支座弯矩 {m_sup/1e3:.2f} kN·m（理论 -wL²/10 = {m_th2/1e3:.2f}）"
          f"误差 {d2:.3f}%")
    ok2 = d2 < 1.0
    verdicts.append(("三跨连续梁中间支座弯矩", PASS if ok2 else FAIL, f"误差{d2:.3f}%"))

    # ---------------- 3. 逐跨施工：平衡 + 增量法在起作用 ----------------
    total3 = Lspan * 3
    b3 = build_beam_bridge(span=total3, n_spans=3, udl_kN=20.0, q2_kN=10.0,
                           staged=True, lane=True, gravity=0.0)
    r3 = solve_2d(b3, out_dir=OUT / "beam3x30_staged", dat_name="beam3x30_staged.dat")
    n_elem3 = len(b3.elements)
    support_nodes = {1, n_elem3 // 3 + 1, 2 * n_elem3 // 3 + 1, n_elem3 + 1}
    reac = _reactions(r3.final_beam, n_elem3, support_nodes)
    total_load = (20.0 + 10.0) * total3 * 1000.0     # N
    sum_r = sum(reac.values())
    print(f"\n[3] {b3.title}（逐跨施工 + 二期铺装 + 车道活载）")
    print(f"    支座反力 { {k: round(v/1e3, 1) for k, v in reac.items()} } kN")
    print(f"    反力合计 {sum_r/1e3:.2f} kN（外荷载 {total_load/1e3:.2f} kN）"
          f"差 {abs(sum_r-total_load)/1e3:.3f} kN")
    bal_ok = abs(sum_r - total_load) < 0.01 * total_load

    # 线性叠加：同恒载总量（30 kN/m）下，逐跨施工成桥态 == 一次性浇筑。
    b3b = build_beam_bridge(span=total3, n_spans=3, udl_kN=30.0, gravity=0.0)
    r3b = solve_2d(b3b, out_dir=OUT / "beam3x30_oneshot30", dat_name="beam3x30_oneshot30.dat")
    diff = max(
        abs(r3.final_beam[k].get("My", 0.0) - r3b.final_beam[k].get("My", 0.0))
        for k in r3b.final_beam
    )
    rel = diff / (abs(m_th2) * 1.5 + 1e-9)         # 30 kN/m 对应的支座弯矩基准
    print(f"    成桥态 vs 一次性浇筑（同 30 kN/m）：最大弯矩差 {diff/1e3:.3f} kN·m "
          f"（相对基准 {rel*100:.3f}%）")
    lin_ok = diff < 0.01 * abs(m_th2) * 1.5         # 叠加原理：差异应≈0（<1%）

    # 阶段包络捕获临时状态：阶段 1 边跨为简支态，跨中弯矩 = wL²/8 = 2250 kN·m，
    # 应出现在阶段包络里、且大于成桥态同位置的跨中弯矩。
    env_mmax = max(v["Mmax"] for v in r3.beam.values())
    m_simply = w * Lspan ** 2 / 8.0                 # 20 kN/m 简支态跨中弯矩
    print(f"    阶段包络全局 Mmax = {env_mmax/1e3:.1f} kN·m "
          f"（简支态理论 wL²/8 = {m_simply/1e3:.1f} kN·m）")
    env_ok = abs(env_mmax - m_simply) < 0.02 * m_simply

    verdicts.append(("逐跨施工成桥态平衡", PASS if bal_ok else FAIL,
                     f"差{abs(sum_r-total_load)/1e3:.3f}kN"))
    verdicts.append(("增量法正装==线性叠加", PASS if lin_ok else FAIL,
                     f"差{diff/1e3:.3f}kN·m"))
    verdicts.append(("阶段包络捕获临时简支态", PASS if env_ok else FAIL,
                     f"Mmax={env_mmax/1e3:.1f}kN·m"))

    # ---------------- 4. 规范组合 ----------------
    combos = r3.combinations()
    print(f"\n[4] 规范组合（{len(combos)} 种）")
    n_ok = 0
    for c in combos:
        wc = c.worst("My")
        cell = "—" if wc is None else f"{wc[1]/1e3:.1f} kN·m（单元 {wc[0][0]}·{wc[0][1]}）"
        print(f"    {c.spec.name}: 控制弯矩 {cell}  has_live={c.has_live}")
        if c.has_live and wc is not None and abs(wc[1]) > 0:
            n_ok += 1
    combo_ok = len(combos) == 3 and n_ok == 3
    verdicts.append(("三种组合齐全且含活载项", PASS if combo_ok else FAIL,
                     f"{n_ok}/3 组合通过"))

    # ---------------- 汇总 ----------------
    print("\n" + "=" * 78)
    print("判据汇总")
    print("=" * 78)
    npass = 0
    for name, v, detail in verdicts:
        print(f"  {v}  {name:<28} {detail}")
        npass += (v == PASS)
    print(f"\n  {npass}/{len(verdicts)} 通过")
    return 0 if npass == len(verdicts) else 1


if __name__ == "__main__":
    sys.exit(main())
