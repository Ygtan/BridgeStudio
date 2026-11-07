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
    import re
    bL = build_beam_bridge(span=Lspan, n_spans=1, udl_kN=0.0, gravity=0.0, lane=True)
    rL = solve_2d(bL, out_dir=OUT / "beam30_lane", dat_name="beam30_lane.dat")
    env = (OUT / "beam30_lane" / "beam30_lane_envelope_lane1.txt").read_text(encoding="utf-8")
    fac_m = re.search(r"impact_factor=(\S+)", env)
    fac = float(fac_m.group(1)) if fac_m else 1.0
    pk = 270000.0 + 2000.0 * (Lspan - 5.0)
    bare_m = 10500.0 * Lspan * Lspan / 8.0 + pk * Lspan / 4.0
    mid_node = len(bL.elements) // 2 + 1
    got_m = 0.0
    for eid, end, n_end in (
        (mid_node - 1, "j", mid_node),
        (mid_node, "i", mid_node),
    ):
        slot = rL.live_beam.get((eid, end), {})
        got_m = max(got_m, abs(slot.get("MyMin", 0.0)), abs(slot.get("MyMax", 0.0)))
    want_m = bare_m * fac
    dm_imp = abs(got_m - want_m) / want_m
    print(f"    车道跨中 |My| {got_m/1e3:.1f} kN·m（不计冲击 {bare_m/1e3:.1f} × {fac:.3f}）"
          f"误差 {dm_imp*100:.2f}%")
    verdicts.append(("简支梁车道活载含自动冲击",
                     PASS if fac > 1.0 and dm_imp < 0.03 else FAIL,
                     f"1+μ={fac:.3f} 误差{dm_imp*100:.2f}%"))
    qk = 10500.0
    want_v = -(5.0 * qk * Lspan ** 4 / (384.0 * EI)
               + pk * Lspan ** 3 / (48.0 * EI))  # 不计冲击（表 4.3.2 挠度限值）
    got_v = rL.live_disp.get(mid_node, (0.0, 0.0))[1]
    dv_imp = abs(got_v - want_v) / abs(want_v)
    print(f"    车道跨中挠度 {got_v*1000:.3f} mm（理论不计冲击 {want_v*1000:.3f}）"
          f"误差 {dv_imp*100:.2f}%")
    verdicts.append(("简支梁车道活载挠度不计冲击",
                     PASS if mid_node in rL.live_disp and dv_imp < 0.02 else FAIL,
                     f"{got_v*1000:.2f} mm 误差{dv_imp*100:.2f}%"))
    note = " ".join(rL.diagnostics)
    limit_ok = "不计冲击" in note and "L/600" in note and "满足" in note
    verdicts.append(("活载挠度按不计冲击对 L/600",
                     PASS if limit_ok else FAIL, "表 4.3.2"))
    from export_html import write_2d_html
    html_path = write_2d_html(r1, b1, OUT / "beam30.html")
    html = html_path.read_text(encoding="utf-8")
    html_ok = "2250.0" in html and "成桥态梁端内力" in html
    verdicts.append(("二维梁桥 HTML 写出跨中弯矩",
                     PASS if html_ok else FAIL, html_path.name))
    html_lane = write_2d_html(rL, bL, OUT / "beam30_lane.html")
    lane_html = html_lane.read_text(encoding="utf-8")
    lane_html_ok = ("车道活载内力包络" in lane_html
                    and f"{got_m / 1e3:.1f}" in lane_html
                    and "不要再加一次" in lane_html)
    verdicts.append(("HTML 写出车道活载弯矩包络",
                     PASS if lane_html_ok else FAIL, html_lane.name))

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
    html_staged = write_2d_html(r3, b3, OUT / "beam3x30_staged.html")
    staged_html = html_staged.read_text(encoding="utf-8")
    staged_ok = ("施工阶段内力包络" in staged_html and "2250.0" in staged_html
                 and "不能把这份极值与运营期活载相加" in staged_html)
    verdicts.append(("HTML 写出临时简支弯矩",
                     PASS if staged_ok else FAIL, html_staged.name))
    verdicts.append(("二维施工阶段写明不算龄期徐变",
                     PASS if "不按龄期计算徐变" in " ".join(r3.diagnostics) else FAIL,
                     ""))

    # 混凝土梁写了湿度和计算龄期：两阶段，位移 ×(1+χφ)，弯矩不变。
    from model_doc import ModelDoc, creep_phi_jtg3362
    bc = build_beam_bridge(
        span=10.0, n_spans=1, n_elem=2, udl_kN=10.0, gravity=0.0,
        creep_rh=55.0, creep_h=100.0, creep_fcm=40.0,
        creep_t0=7.0, creep_t_end=10000.0)
    rc = solve_2d(bc, out_dir=OUT / "beam10_creep", dat_name="beam10_creep.dat")
    phi = creep_phi_jtg3362(10000.0, 7.0, 55.0, 100.0, 40.0)
    E = bc.materials[0].E
    I = bc.elem_types[0].I
    w = 10000.0
    Lc = 10.0
    v0 = -5.0 * w * Lc ** 4 / (384.0 * E * I)
    want_v = v0 * (1.0 + 0.8 * phi)
    mid = len(bc.elements) // 2 + 1
    got_v = rc.final_disp.get((mid, 2), 0.0)
    want_m = w * Lc * Lc / 8.0
    got_m = rc.final_beam.get((mid - 1, "j"), {}).get("My", 0.0)
    dv = abs(got_v - want_v) / abs(want_v)
    dm = abs(got_m - want_m) / want_m
    print(f"\n[5] 10m 混凝土梁徐变到 10000 天")
    print(f"    v {got_v*1000:.4f} mm（弹性 {v0*1000:.4f} × (1+0.8×{phi:.3f})）"
          f"误差 {dv*100:.2f}%")
    print(f"    M {got_m/1e3:.2f} kN·m（加载时 {want_m/1e3:.2f}）误差 {dm*100:.3f}%")
    from model_doc import jtg3362_shrinkage
    eps = jtg3362_shrinkage(10000.0, 7.0, 7.0, 55.0, 100.0, 40.0)
    free = len(bc.elements) + 1
    got_ux = rc.final_disp.get((free, 1), 0.0)
    want_ux = -eps * Lc
    got_n = rc.final_beam.get((1, "i"), {}).get("N", 0.0)
    du = abs(got_ux - want_ux) / abs(want_ux)
    print(f"    ux {got_ux*1000:.3f} mm（收缩 {want_ux*1000:.3f}）误差 {du*100:.2f}%  N={got_n:.3e}")
    verdicts.append(("二维混凝土梁徐变放大位移且弯矩不变",
                     PASS if dv < 0.01 and dm < 0.01 and du < 0.02
                     and abs(got_n) < 1.0e3
                     and "1+χφ" in " ".join(rc.diagnostics)
                     else FAIL,
                     f"位移{dv*100:.2f}% 弯矩{dm*100:.3f}% 缩短{du*100:.2f}%"))
    early = build_beam_bridge(
        span=10.0, n_spans=1, n_elem=2, udl_kN=10.0, gravity=0.0,
        creep_rh=55.0, creep_h=100.0, creep_fcm=40.0,
        creep_t0=7.0, creep_t_end=10000.0, shrinkage_ts=3.0)
    re_ = solve_2d(early, out_dir=OUT / "beam10_dry3", dat_name="beam10_dry3.dat")
    eps3 = jtg3362_shrinkage(10000.0, 7.0, 3.0, 55.0, 100.0, 40.0)
    ux3 = re_.final_disp.get((len(early.elements) + 1, 1), 0.0)
    want3 = -eps3 * Lc
    du3 = abs(ux3 - want3) / abs(want3)
    v3 = re_.final_disp.get((len(early.elements) // 2 + 1, 2), 0.0)
    print(f"    干燥从第3天 ux {ux3*1000:.3f} mm（{want3*1000:.3f}）误差 {du3*100:.2f}%")
    verdicts.append(("干燥早于加载时，二维梁不把更早的收缩算进平衡",
                     PASS if du3 < 0.02 and abs(want3) < abs(want_ux) * 0.95
                     and abs(v3 - want_v) / abs(want_v) < 0.01 else FAIL,
                     f"缩短{du3*100:.2f}%"))
    from bridge_spec import (BoundaryDef, BridgeSpec, ElemDef, ElemKind,
                             ElemTypeDef, MaterialDef, NodeDef, Stage2D, StageLoadDef)
    Eb, Ab, Lb, Pb = 2.0e11, 0.01, 10.0, 1.0e6
    bar = BridgeSpec(
        title="钢拉杆",
        nodes=[NodeDef(id=1, x=0.0), NodeDef(id=2, x=Lb)],
        elements=[ElemDef(id=1, type_id=1, n1=1, n2=2)],
        elem_types=[ElemTypeDef(id=1, kind=ElemKind.TRUSS, A=Ab)],
        materials=[MaterialDef(id=1, E=Eb, dens=0.0)],
        boundaries=[BoundaryDef(node=1, dofs="1-3"), BoundaryDef(node=2, dofs="2-3")],
        stages=[
            Stage2D(id=1, activate=[1], time=7.0,
                    loads=[StageLoadDef(node=2, dof=1, value=Pb)]),
            Stage2D(id=2, time=10000.0),
        ],
        creep_rh=55.0, creep_h=100.0, creep_fcm=40.0, creep_t0=7.0,
        gravity=0.0,
    )
    rb = solve_2d(bar, out_dir=OUT / "truss_creep", dat_name="truss_creep.dat")
    ux_bar = rb.final_disp.get((2, 1), 0.0)
    want_bar = Pb * Lb / (Eb * Ab)
    crept = want_bar * (1.0 + 0.8 * phi)
    dbar = abs(ux_bar - want_bar) / want_bar
    print(f"    钢拉杆伸长 {ux_bar*1000:.3f} mm（弹性 {want_bar*1000:.3f}，"
          f"若按混凝土徐变 {crept*1000:.3f}）误差 {dbar*100:.2f}%")
    verdicts.append(("钢拉杆不按混凝土徐变曲线伸长",
                     PASS if dbar < 0.01 and abs(ux_bar - crept) / crept > 0.2 else FAIL,
                     f"{ux_bar*1000:.3f} mm"))
    from bridge_spec import StageElemLoadDef
    Ec, Ac, Lcable = 1.95e11, 0.002, 8.0
    Eb2, Ib, Ab2 = 3.45e10, 0.01, 0.5

    def _stay_beam(with_creep: bool) -> BridgeSpec:
        return BridgeSpec(
            title="梁加竖索",
            nodes=[NodeDef(id=1, x=0.0), NodeDef(id=2, x=5.0),
                   NodeDef(id=3, x=10.0), NodeDef(id=4, x=5.0, y=Lcable)],
            elements=[ElemDef(id=1, type_id=1, n1=1, n2=2),
                      ElemDef(id=2, type_id=1, n1=2, n2=3),
                      ElemDef(id=3, type_id=2, n1=2, n2=4)],
            elem_types=[
                ElemTypeDef(id=1, kind=ElemKind.BEAM, A=Ab2, I=Ib, WT=0.01, WB=0.01, mat=1),
                ElemTypeDef(id=2, kind=ElemKind.TRUSS, A=Ac, mat=2),
            ],
            materials=[MaterialDef(id=1, E=Eb2, dens=0.0),
                       MaterialDef(id=2, E=Ec, dens=0.0)],
            boundaries=[BoundaryDef(node=1, dofs="1-2"),
                        BoundaryDef(node=3, dofs="2"),
                        BoundaryDef(node=4, dofs="1-3")],
            stages=[
                Stage2D(id=1, activate=[1, 2, 3], time=7.0,
                        elem_loads=[StageElemLoadDef(elems=[1, 2], q1=-20000.0, q2=-20000.0)]),
                Stage2D(id=2, time=10000.0),
            ],
            creep_rh=55.0 if with_creep else 0.0,
            creep_h=100.0 if with_creep else 0.0,
            creep_fcm=40.0 if with_creep else 0.0,
            creep_t0=7.0,
            gravity=0.0,
        )

    r0 = solve_2d(_stay_beam(False), out_dir=OUT / "stay_elastic", dat_name="stay_elastic.dat")
    rC = solve_2d(_stay_beam(True), out_dir=OUT / "stay_creep", dat_name="stay_creep.dat")
    v0s = r0.final_disp.get((2, 2), 0.0)
    vCs = rC.final_disp.get((2, 2), 0.0)
    ux = rC.final_disp.get((2, 1), 0.0)
    uy = vCs
    Lnew = ((ux) ** 2 + (uy - Lcable) ** 2) ** 0.5
    dL = Lnew - Lcable
    n_ends = [abs(rC.final_beam.get((3, end), {}).get("N", 0.0)) for end in ("i", "j")]
    Nstay = max(n_ends)
    Ngeom = Ec * Ac / Lcable * dL
    ratio = Nstay / Ngeom if Ngeom else 0.0
    soft = 1.0 / (1.0 + 0.8 * phi)
    print(f"    竖索：挠度 {v0s*1000:.3f} → {vCs*1000:.3f} mm，"
          f"索力/几何弹性 {ratio:.3f}（若索也折减模量则约 {soft:.3f}）")
    verdicts.append(("混凝土梁徐变后竖索仍按原弹性模量受力",
                     PASS if abs(vCs) > abs(v0s) * 1.02 and abs(ratio - 1.0) < 0.05
                     and abs(ratio - soft) > 0.2 else FAIL,
                     f"挠度比 {abs(vCs)/max(abs(v0s),1e-12):.3f} 索力比 {ratio:.3f}"))
    doc, _, notes = ModelDoc.from_text(
        "混凝土梁桥，跨度10米，均布荷载10kN/m，相对湿度55%，理论厚度100毫米，"
        "混凝土C40，计算龄期10000天")
    doc.build()
    ages = [s.time for s in doc.model.stages]
    from model_doc import closed_contour_perimeter_m, theoretical_thickness_mm
    from bridge_spec import build_cable_stayed_2d
    bauto = build_beam_bridge(
        span=10.0, n_spans=1, n_elem=2, udl_kN=10.0, gravity=0.0,
        creep_rh=55.0, creep_fcm=40.0, creep_t0=7.0, creep_t_end=10000.0)
    h_auto = theoretical_thickness_mm(
        bauto.elem_types[0].A, closed_contour_perimeter_m(8.0, 2.0))
    rauto = solve_2d(bauto, out_dir=OUT / "beam10_hauto", dat_name="beam10_hauto.dat")
    phi_auto = creep_phi_jtg3362(10000.0, 7.0, 55.0, h_auto, 40.0)
    v_auto = rauto.final_disp.get((len(bauto.elements) // 2 + 1, 2), 0.0)
    v0_auto = -5.0 * 10000.0 * 10.0 ** 4 / (384.0 * bauto.materials[0].E * bauto.elem_types[0].I)
    want_auto = v0_auto * (1.0 + 0.8 * phi_auto)
    d_auto = abs(v_auto - want_auto) / abs(want_auto)
    dat_auto = bauto.to_dat()
    print(f"    未写厚度 h=2A/u={h_auto:.1f} mm  挠度误差 {d_auto*100:.2f}%")
    verdicts.append(("未写理论厚度时按截面外轮廓 h=2A/u",
                     PASS if abs(bauto.elem_types[0].h_th_mm - h_auto) < 1e-6
                     and "HTH=" in dat_auto and d_auto < 0.01 else FAIL,
                     f"{h_auto:.1f} mm"))
    stayed = build_cable_stayed_2d(
        main_span=40.0, side_span=16.0, tower_height=12.0, n_cable=2,
        staged=True, creep_rh=55.0, creep_fcm=48.0, creep_h=0.0, creep_t_end=10000.0)
    hd, ht = stayed.elem_types[0].h_th_mm, stayed.elem_types[1].h_th_mm
    verdicts.append(("斜拉主梁和桥塔理论厚度分开算",
                     PASS if hd > 0.0 and ht > 0.0 and abs(hd - ht) > 1.0
                     and stayed.stages[-1].time == 10000.0
                     and stayed.elem_types[2].h_th_mm == 0.0 else FAIL,
                     f"梁 {hd:.1f} mm  塔 {ht:.1f} mm"))
    verdicts.append(("句子里的湿度让二维梁桥分出加载和计算龄期",
                     PASS if ages[:1] == [7.0] and ages[-1:] == [10000.0]
                     and doc.model.creep_fcm == 40.0
                     and any("1+χφ" in n for n in notes.notes) else FAIL,
                     " ".join(f"{a:g}" for a in ages)))

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
