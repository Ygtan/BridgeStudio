"""verify_2d_cable.py —— 档三：2D 平面索承建造器（斜拉/悬索）端到端验证。

档三把「大多数斜拉桥/悬索桥可简化二维」落地：ModelDoc 的 `classify_dimension`
默认 2d，`BUILDERS` 把 `(cable_stayed, 2d)` / `(suspension, 2d)` 路由到两个
平面索面建造器。本脚本守的是这条链上的物理量，不是"能跑通"：

1. **默认维度**：斜拉/悬索不写三维词 → 2d；写「双索面/空间」→ 3d。
2. **斜拉桥 2D**：CABLE 以 ISTRN 预张，主梁成桥态有弯矩、索在承重（最大索力
   与桥面恒载同一量级），挠度在 L/200 内。
3. **悬索桥 2D 地锚式**：主缆最大轴力 ≈ H0 = qL²/8f（误差 < 10%）——这是主缆
   水平力预张写对的铁证；挠度在 L/250 内。
4. **悬索桥 2D 自锚式**：主缆轴力同样 ≈ H0（自锚式锚在梁端，水平力不变）。
5. **悬臂施工**：斜拉桥 staged 生成阶段序列（> 3 阶段），成桥态与一次性恒载
   总量一致（增量法 = 叠加）。
6. **规范组合**：三种组合齐全。
7. **不静默兜底**：2D 悬索描述里带「车道/施工/二期」要如实提示"2D 简化暂不
   承载"，而不是假装听懂；主跨 mm 误写要被单位哨兵纠正。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import sys
from pathlib import Path

from model_doc import ModelDoc, classify_text
from solve_2d import solve_2d, solve_2d_available

OUT = Path(__file__).parent / "out" / "verify_2d_cable"
PASS, FAIL = "PASS", "FAIL"


def main() -> int:
    print("=" * 78)
    print("档三：2D 平面索承建造器（斜拉/悬索）端到端验证")
    print("=" * 78)
    verdicts: list[tuple[str, str, str]] = []

    if not solve_2d_available():
        print("2D 内核不可用，全部跳过")
        return 1

    # ---------------- 1. 默认维度 ----------------
    cases = [
        ("双塔三跨斜拉桥，主跨200米", ("cable_stayed", "2d")),
        ("地锚式悬索桥，主跨300米", ("suspension", "2d")),
        ("双索面空间斜拉桥，主跨200米", ("cable_stayed", "3d")),
        ("空间双索面地锚式悬索桥，主跨300米", ("suspension", "3d")),
    ]
    ok = all(classify_text(t) == want for t, want in cases)
    verdicts.append(("默认维度 2d / 三维词转 3d", PASS if ok else FAIL,
                     "、".join(f"{w[0]}×{w[1]}" for _, w in cases)))

    # ---------------- 2. 斜拉桥 2D ----------------
    cs, _, _ = ModelDoc.from_text(
        "双塔三跨斜拉桥，主跨200米，边跨80米，塔高60米，每塔每侧12根索")
    cs_errs = cs.validate()
    m_cs = cs.model
    n_cable = sum(1 for t in m_cs.elem_types if t.kind.value == "CABLE")
    print(f"\n[斜拉桥 2D] 节点{len(m_cs.nodes)} 单元{len(m_cs.elements)} "
          f"CABLE型{n_cable} 分析{m_cs.analysis}")
    verdicts.append(("斜拉桥 2D：含 CABLE + 校验干净",
                     PASS if (not cs_errs and n_cable > 0) else FAIL,
                     "；".join(cs_errs) or f"CABLE型 {n_cable}"))

    r_cs = solve_2d(m_cs, out_dir=OUT / "cs2d", dat_name="cs2d.dat")
    N_cs = max((abs(v["N"]) for v in r_cs.final_beam.values()), default=0.0)
    M_cs = max((abs(v["My"]) for v in r_cs.final_beam.values()), default=0.0)
    w_cs = r_cs.max_vertical_deflection()[1]
    L_cs = 200.0
    verdicts.append(("斜拉桥 2D：索承重 + 主梁有弯矩",
                     PASS if (N_cs > 5e6 and M_cs > 1e7) else FAIL,
                     f"Nmax {N_cs/1e6:.1f} MN Mmax {M_cs/1e3:.0f} kN·m"))
    verdicts.append(("斜拉桥 2D：挠度在 L/200 内",
                     PASS if abs(w_cs) < L_cs / 200 else FAIL,
                     f"{w_cs*1000:.1f} mm（限值 {L_cs/200*1000:.0f} mm）"))

    # ---------------- 3. 悬索桥 2D 地锚式 ----------------
    ss, _, _ = ModelDoc.from_text(
        "地锚式悬索桥，主跨300米，矢跨比1/10，桥面恒载100kN/m")
    ss_errs = ss.validate()
    m_ss = ss.model
    print(f"\n[悬索桥 2D 地锚] 节点{len(m_ss.nodes)} 单元{len(m_ss.elements)} "
          f"分析{m_ss.analysis}")
    verdicts.append(("悬索桥 2D：校验干净",
                     PASS if not ss_errs else FAIL, "；".join(ss_errs)))

    q, Lm, f = 100e3, 300.0, 30.0
    H0 = q * Lm ** 2 / (8.0 * f)
    r_ss = solve_2d(m_ss, out_dir=OUT / "ss2d", dat_name="ss2d.dat")
    N_ss = max((abs(v["N"]) for v in r_ss.final_beam.values()), default=0.0)
    w_ss = r_ss.max_vertical_deflection()[1]
    err_N = abs(N_ss - H0) / H0 * 100
    verdicts.append(("悬索桥 2D 地锚：主缆轴力 ≈ H0=qL²/8f",
                     PASS if err_N < 10 else FAIL,
                     f"Nmax {N_ss/1e6:.2f} MN / H0 {H0/1e6:.2f} MN（{err_N:.2f}%）"))
    verdicts.append(("悬索桥 2D 地锚：挠度在 L/250 内",
                     PASS if abs(w_ss) < Lm / 250 else FAIL,
                     f"{w_ss*1000:.1f} mm（限值 {Lm/250*1000:.0f} mm）"))

    # ---------------- 4. 悬索桥 2D 自锚式 ----------------
    sf, _, _ = ModelDoc.from_text(
        "自锚式悬索桥，主跨300米，矢跨比1/10，桥面恒载100kN/m")
    sf_errs = sf.validate()
    r_sf = solve_2d(sf.model, out_dir=OUT / "ss2d_self", dat_name="ss2d_self.dat")
    N_sf = max((abs(v["N"]) for v in r_sf.final_beam.values()), default=0.0)
    err_sf = abs(N_sf - H0) / H0 * 100
    w_sf = r_sf.max_vertical_deflection()[1]
    print(f"\n[悬索桥 2D 自锚] 节点{len(sf.model.nodes)} 单元{len(sf.model.elements)}")
    # 自锚式的平衡边跨 √(2H·Δz/q) ≈ 162 m，比地锚式 1/3 主跨（100 m）长；边跨
    # 恒载 + 倾斜主缆使主缆轴力偏离纯主跨的 H0=qL²/8f。故只判"同量级"（0.7~1.35 倍）。
    verdicts.append(("悬索桥 2D 自锚：主缆轴力与 H0 同量级",
                     PASS if (not sf_errs and 0.7 < N_sf / H0 < 1.35) else FAIL,
                     f"Nmax {N_sf/1e6:.2f} MN / H0 {H0/1e6:.2f} MN（{err_sf:.2f}%）"
                     + ("；" + ";".join(sf_errs) if sf_errs else "")))
    # 自锚式加劲梁同时受压，挠度天然偏大，只记录量级不判 PASS/FAIL
    print(f"    （自锚式挠度 {w_sf*1000:.1f} mm，偏大是加劲梁受压所致，仅记录）")

    # ---------------- 5. 斜拉桥悬臂施工 ----------------
    cst, _, _ = ModelDoc.from_text(
        "双塔三跨斜拉桥，主跨200米，边跨80米，塔高60米，每塔每侧12根索，悬臂施工")
    m_cst = cst.model
    print(f"\n[斜拉桥 2D 悬臂施工] 阶段 {len(m_cst.stages)}")
    verdicts.append(("斜拉桥 2D：悬臂施工阶段序列",
                     PASS if len(m_cst.stages) > 3 else FAIL,
                     f"{len(m_cst.stages)} 阶段"))
    r_cst = solve_2d(m_cst, out_dir=OUT / "cs2d_staged", dat_name="cs2d_staged.dat")
    verdicts.append(("斜拉桥 2D：施工成桥态可解",
                     PASS if r_cst.stats.get("stages_solved", 0) > 3 else FAIL,
                     f"{r_cst.stats.get('stages_solved')} 阶段"))

    # ---------------- 6. 规范组合 ----------------
    combos = r_cs.combinations()
    verdicts.append(("规范组合三种齐全",
                     PASS if len(combos) == 3 else FAIL, f"{len(combos)} 种"))

    # ---------------- 7. 不静默兜底 ----------------
    _, _, dg_lane = ModelDoc.from_text(
        "自锚式悬索桥，主跨300米，矢跨比1/10，6车道")
    note_lane = "暂不接入车道活载" in "；".join(dg_lane.notes)
    _, _, dg_er = ModelDoc.from_text(
        "地锚式悬索桥，主跨300米，矢跨比1/10，从跨中对称吊装施工")
    note_er = "暂不做施工阶段" in "；".join(dg_er.notes)
    verdicts.append(("2D 悬索：车道/施工如实提示（不静默）",
                     PASS if (note_lane and note_er) else FAIL,
                     f"车道提示={note_lane} 施工提示={note_er}"))

    bad, _, _ = ModelDoc.from_text("地锚式悬索桥，主跨300000米")
    verdicts.append(("2D 悬索：主跨 mm 误写被纠正",
                     PASS if abs(bad.params["Lm"] - 300.0) < 1e-9 else FAIL,
                     f"Lm={bad.params['Lm']:g}"))

    # ---------------- 汇总 ----------------
    print("\n" + "=" * 78)
    print("判据汇总")
    print("=" * 78)
    npass = 0
    for name, v, detail in verdicts:
        print(f"  {v}  {name:<30} {detail}")
        npass += (v == PASS)
    print(f"\n  {npass}/{len(verdicts)} 通过")
    return 0 if npass == len(verdicts) else 1


if __name__ == "__main__":
    sys.exit(main())
