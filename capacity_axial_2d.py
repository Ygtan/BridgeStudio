"""capacity_axial_2d.py —— 2D 偏心受压（压弯）/ 轴心受压的唯一口径层。

第十六次接入：把第十二次「2D 承载力 Mu / Vu 半部」剩下的
**压弯 / 轴压半部**按 opt-in 接通。纯 BS 侧，FE 仓零改动。

一条调研纠偏（必须先讲清楚）
----------------------------
第十二次以「2D 组合轴力 N 只有恒载单值、没有活载轴力包络」为由拒绝
压弯 / 轴压。这句话来自 `combination.py` 的一行过时注释，与两仓源码矛盾：

* FE `fem_2d.cpp::defaultEnvelopeSpecs()` 为每单元每端登记 `FORCE/N`
  （`E<id>_N1` / `E<id>_N2`），`example_influence.exe` 把活载轴力包络写进
  `<stem>_envelope_lane1.txt`；
* BS `solve_2d.py::_parse_lane_envelope()` 把它解析成
  `live_beam[(单元,端)] = {NMin, NMax, MyMin, MyMax, VzMin, VzMax}`；
* 只是 `combination.py::beam_combinations()` 按历史口径取 `q.get("N", 0.0)`
  （键名不匹配，恒取 0），`BeamCombo.rows["N"]` 才退化成恒载单值。

本层**不修** `beam_combinations()`：那是 `strength_2d` 12 角点应力验算的
效应源，改它就等于改默认计算书的数值。本层独立读 `final_beam` +
`live_beam`，按 `combination.ULTIMATE` 的系数自行组合，默认路径一字不动。

四条口径
--------
1. **唯一效应源 + 同端配对**：恒载 `final_beam[(单元,端)]` 的 `N` / `My`
   单值，活载 `live_beam[(单元,端)]` 的 `NMin/NMax` / `MyMin/MyMax` 包络；
   按 `combination.ULTIMATE.gamma_g / gamma_q` 组合。N 与 M 必须来自
   **同一个 (单元, 端)**，不跨端、不跨分量拼全局最大值。每个断面枚举
   2(N 侧) × 2(M 侧) 角点。**逐分量独立极值不冒充同一车辆位置的联合
   最不利工况**，这是影响线包络法固有的近似，表注与边界说明里如实写出。
2. **三链分派与 3D 同源**：`Nc = max(-N_comb, 0)`（受压为正）；
   `n_axial = fcd·b·h`；`Nc > 5%·n_axial` 才进本章，再按
   `e0 = Md/Nc < 1%·截面高` 分轴心受压（§5.3.1）/ 偏心受压（§5.3.4）；
   `Nc ≤ 5%` 的角点归 flex 链（由第九章 `capacity_2d` 负责），本章不出表、
   只记数。负弯矩角点按 `mirror_section_for_negative` 取实际验算面，
   与 `result_3d._capacity_cases` 一致。
3. **`l0` 必填、不静默降级**：φ（表 5.3.1）/ η（§5.3.9）/ λ 都靠计算长度。
   `RebarSpec2D.l0 > 0`（mm）才做，缺任一个配筋截面**整章 `refused`**，
   绝不静默取短柱 φ=1 / η=1。缺活载轴力包络则整章 `no_axial_live` 拒绝，
   不拿恒载单值冒充完整判定。配筋转换与 `capacity_2d._capacity_section`
   共用同一份，不复制、不猜牌号、不从 A/I 反推。
4. **只复用纯函数、结论与计算书同源**：公式与表契约唯一来源
   `section_strength.check_eccentric` / `check_axial` / `eccentric_table` /
   `axial_table` / `capacity_calculation_sheet`；`tables` 非空当且仅当
   `status == "ok"`，`Solve2DResult.axial_capacity_tables()` 是 HTML /
   四出口 / 界面的唯一取数口。不重写规范公式、不重算 K·ΔU=R。

本层是**纯 Python 校核**，不调 FE 内核、不改 `.dat`、不改默认 HTML、
不写回 `BeamCombo.rows`。能力矩阵「截面应力 / 承载力验算」2D 仍标 **P**
（2D 逐分量包络近似、无 GUI 入口、不等于 3D 全套承载力），`P ≠ Y`。
"""

from __future__ import annotations

from typing import Any

import code_material as cm
import combination
import section_strength
from capacity_2d import _capacity_section, _enum_value

SOURCE = ("Solve2DResult.final_beam + live_beam(NMin/NMax) + "
          "ElemTypeDef.rebar → section_strength.check_eccentric / check_axial")
BASIS = "2d_beam_axial_ultimate"

#: 与 3D `result_3d._capacity_cases` 同源，不新增模型控件。
GAMMA0 = 1.0

#: 分派阈值（与 3D 同源，只在本层用一次；不写进模型、不设界面开关）。
AXIAL_FRACTION = 0.05
ECCENTRICITY_FRACTION = 0.01

BOUNDARY_NOTE = (
    "本章只做普通钢筋混凝土偏心受压（JTG 3362-2018 §5.3.4 / §5.3.5）与"
    "轴心受压（§5.3.1）两条链，是第十二次 Mu / Vu 一条 flex 链之外的"
    "独立 opt-in 半部，不替代也不修改第九章。效应直接取内核已算出的"
    "成桥态恒载 N / My 与车道活载 NMin/NMax / MyMin/MyMax 包络，按"
    "JTG D60-2015 基本组合（γ_G=1.2、γ_Q=1.4）在同一个 (单元, 端) 上"
    "配对并枚举 2×2 角点；不跨端、不跨分量，也不修改 beam_combinations 的"
    "12 角点应力口径。**逐分量独立极值不保证来自同一车辆位置，属影响线"
    "包络法固有近似，不等于同一工况的联合最不利 (N, M)**。轴压显著"
    "（>5% 截面轴压承载力）才进本章，再按偏心距 <1% 截面高分轴压 / 压弯；"
    "不显著的角点归 flex 链，本章不出表。计算长度 l0 必须由 RebarSpec2D "
    "显式给出（mm），缺失整章拒绝，不静默取短柱 φ=1 / η=1；缺活载轴力"
    "包络同样整章拒绝，不拿恒载单值冒充完整判定。γ₀=1.0 与 3D 分派同源。"
    "预应力、受扭、抗冲切、腹板局部屈曲、Mz / Vy、工形/箱形非对称小偏心"
    "反向破坏（§5.3.4-4/5 含翼缘项）与 3D 全套承载力仍未覆盖。"
    "能力边界矩阵「截面应力 / 承载力验算」2D 仍标 P，不标 Y。"
)

TABLE_NOTE_EXTRA = (
    "2D 压弯 / 轴压口径：效应取成桥态恒载单值 + 车道活载包络按 JTG D60-2015 "
    "基本组合（1.2·S_G + 1.4·S_Q）在**同一 (单元, 端)** 配对，每断面枚举 "
    "2(N 侧)×2(M 侧) 角点；不跨端、不跨分量，逐分量独立极值不冒充同一"
    "车辆位置的联合最不利工况。轴压分派阈值 5%·fcd·b·h、偏心距阈值 "
    "1%·截面高，与 3D `result_3d._capacity_cases` 同源；γ₀=1.0。"
    "计算长度 l0 取自 opt-in 的 `RebarSpec2D`（mm，必填），表中「模型输入」"
    "行的 2D 来源是 `RebarSpec2D`，不是 3D 的 `RebarSpec3D`；公式与表头"
    "仍由 `section_strength.py` 唯一给出。2D 仍标 P 不标 Y。来源：" + SOURCE + "。"
)

STATUS_TEXT = {
    "not_requested": "本次未请求 2D 压弯 / 轴压承载力验算（默认关闭，不写进 .dat）。",
    "unavailable": "本次没有可用的成桥态内力 / 模型 / 组合结果，2D 压弯 / 轴压做不了；静力与活载结果不受影响。",
    "no_ultimate": "三种 JTG D60 组合里没有基本组合（key=ultimate），2D 压弯 / 轴压没有效应源，本次不做。",
    "no_axial_live": "本次未做 2D 压弯 / 轴压验算：内核没有产出活载轴力包络（NMin/NMax），不拿恒载单值轴力冒充完整判定。",
    "no_beam_section": "本次未做 2D 压弯 / 轴压验算：成桥态内力里没有可验算的梁单元（TRUSS / CABLE / STRUT 等轴向杆没有配筋概念）。",
    "no_rebar_section": "本次未做 2D 压弯 / 轴压验算：梁单元类型都没有 opt-in 的 RebarSpec2D 配筋契约，不编一个配筋出来。",
    "no_compression": "本次未做 2D 压弯 / 轴压验算：全部角点的轴压都不超过 5% 截面轴压承载力，按三链分派归 flex 链，本章不出表、不摆空行。",
    "no_effect": "基本组合在这些配筋截面上没有非零的轴压 / 弯矩效应，本次不出表、不补零。",
}


def status_text(s: dict[str, Any] | None) -> str:
    """缺结果 / 拒绝时的唯一中文文案；HTML、四出口与界面不得另写一套。"""
    s = s or {}
    st = str(s.get("status", "not_requested"))
    if st == "ok":
        return (f"已按 JTG D60 基本组合在同端配对枚举角点，对 "
                f"{s.get('rebar_sections', 0)} 个显式配筋截面给出压弯 / 轴压"
                f"利用率（γ₀={s.get('gamma0', GAMMA0):g}），"
                f"其中压弯 {s.get('eccentric_angles', 0)} 个角点、"
                f"轴压 {s.get('axial_angles', 0)} 个角点、"
                f"归 flex 链 {s.get('flex_angles', 0)} 个角点。"
                + ("组合含活载轴力包络。" if s.get("has_axial_live") else
                   "本次没有活载轴力包络，不是完整的承载力验算。")
                + BOUNDARY_NOTE)
    if st == "refused":
        reasons = list(s.get("reasons") or [])
        head = ("本次未做 2D 压弯 / 轴压验算：模型缺少显式声明的配筋 / "
                "材料 / 计算长度参数，整章拒绝，不补零、不回落、不静默取短柱。")
        shown = "；".join(reasons[:3])
        more = f"；另有 {len(reasons) - 3} 条同类问题" if len(reasons) > 3 else ""
        return head + shown + more + "。" + BOUNDARY_NOTE
    return STATUS_TEXT.get(st, f"2D 压弯 / 轴压状态 {st}，本次不出表。") \
        + BOUNDARY_NOTE


def evaluate(res: Any, spec: Any, gamma0: float = GAMMA0) -> dict[str, Any]:
    """从一次 2D 求解结果产出压弯 / 轴压结论与表字典。

    只由 `solve_2d(..., axial_capacity=True)` 调用；返回 ``{status, source,
    note, reasons, tables, boundary_note, gamma0, has_live, has_axial_live,
    skipped_axial, rebar_sections, angle_count, flex_angles,
    eccentric_angles, axial_angles, controls}``。
    `tables` 非空当且仅当 `status == "ok"`。
    """
    out: dict[str, Any] = {
        "status": "unavailable", "source": SOURCE, "note": "",
        "basis": BASIS, "reasons": [], "tables": {},
        "boundary_note": BOUNDARY_NOTE,
        "gamma0": float(gamma0),
        "has_live": False, "has_axial_live": False,
        "skipped_axial": 0, "rebar_sections": 0,
        "angle_count": 0, "flex_angles": 0,
        "eccentric_angles": 0, "axial_angles": 0,
        "controls": [],
    }
    final = getattr(res, "final_beam", None) or {}
    live = getattr(res, "live_beam", None) or {}
    combos = res.combinations() if hasattr(res, "combinations") else []
    if not final or spec is None or not combos:
        out["note"] = status_text(out)
        return out
    ult = next((c for c in combos
                if str(getattr(getattr(c, "spec", None), "key", "")) == "ultimate"),
               None)
    if ult is None:
        out["status"] = "no_ultimate"
        out["note"] = status_text(out)
        return out
    out["has_live"] = bool(getattr(ult, "has_live", False))
    # 活载轴力包络是本章的硬前提：内核已产出 NMin/NMax，缺了就如实拒绝。
    out["has_axial_live"] = any(
        ("NMin" in (v or {}) or "NMax" in (v or {})) for v in live.values())
    if not out["has_axial_live"]:
        out["status"] = "no_axial_live"
        out["note"] = status_text(out)
        return out

    elem_by_id = {int(e.id): e for e in (getattr(spec, "elements", []) or [])}
    type_by_id = {int(t.id): t for t in (getattr(spec, "elem_types", []) or [])}
    mat_by_id = {int(m.id): m for m in (getattr(spec, "materials", []) or [])}

    # 按实常数类型分组：同一截面的所有 (单元, 端) 共用一份配筋。
    groups: dict[int, list[tuple[int, str]]] = {}
    reasons: list[str] = []
    axial = 0
    for key in sorted(final):
        e = elem_by_id.get(int(key[0]))
        if e is None:
            reasons.append(f"单元 {key[0]} 在 spec.elements 里找不到，无法取其截面")
            continue
        t = type_by_id.get(int(e.type_id))
        if t is None:
            reasons.append(
                f"单元 {key[0]} 引用的类型 {e.type_id} 在 spec.elem_types 里找不到")
            continue
        if _enum_value(getattr(t, "kind", "")) != "BEAM":
            axial += 1
            continue
        groups.setdefault(int(t.id), []).append(key)
    out["skipped_axial"] = axial
    if not groups and not reasons:
        out["status"] = "no_beam_section"
        out["note"] = status_text(out)
        return out

    caps: list[tuple[str, section_strength.CapacitySection,
                     list[tuple[int, str]]]] = []
    seen: set[int] = set()
    no_rebar = 0
    for tid in sorted(groups):
        t = type_by_id[tid]
        if getattr(t, "rebar", None) is None:
            no_rebar += 1
            continue
        m = mat_by_id.get(int(getattr(t, "mat", 1)))
        sec, why = _capacity_section(t, m)
        if sec is None:
            if tid not in seen:
                reasons.append(f"单元类型 {tid}：{why}")
                seen.add(tid)
            continue
        # l0 是 φ / η / λ 的唯一输入，缺失绝不静默按短柱降级。
        if float(getattr(sec, "l0", 0.0) or 0.0) <= 0.0:
            if tid not in seen:
                reasons.append(
                    f"单元类型 {tid}：RebarSpec2D.l0 未显式给出计算长度（mm），"
                    "φ / η / λ 无法定解，不静默取短柱 φ=1")
                seen.add(tid)
            continue
        caps.append((f"实常数类型 {tid}（{sec.concrete_grade}）", sec,
                     groups[tid]))
    out["rebar_sections"] = len(caps)
    out["no_rebar_sections"] = no_rebar
    if not caps and not reasons:
        out["status"] = ("no_rebar_section" if no_rebar
                         else "no_beam_section")
        out["note"] = status_text(out)
        return out
    if reasons:
        out["status"] = "refused"
        out["reasons"] = reasons
        out["note"] = status_text(out)
        return out

    combo_spec = combination.ULTIMATE
    gg, gq = float(combo_spec.gamma_g), float(combo_spec.gamma_q)
    ecc_pairs: list[tuple[str, section_strength.EccentricResult]] = []
    ax_pairs: list[tuple[str, section_strength.AxialResult]] = []
    calc_cases: list[section_strength.CalcCase] = []
    controls: list[dict[str, Any]] = []
    angle_rows: list[list[str]] = []
    flex_angles = 0
    any_effect = False
    notes: list[str] = []

    for label, sec, keys in caps:
        try:
            fcd = cm.concrete(sec.concrete_grade).fcd
            n_axial = fcd * sec.b * sec.h       # N（mm²·N/mm²）
        except Exception:
            reasons.append(f"{label}：混凝土 fcd 查 code_material 失败，"
                           "5% 轴压分派阈值无法定解")
            continue
        h_m = float(sec.h) / 1000.0
        for key in keys:
            g = final.get(key) or {}
            q = live.get(key) or {}
            ng = float(g.get("N", 0.0))
            mg = float(g.get("My", 0.0))
            # 缺活载包络的一侧不补零冒充：只取实际存在的键。
            n_sides = [("NMin", float(q["NMin"]))] if "NMin" in q else []
            if "NMax" in q:
                n_sides.append(("NMax", float(q["NMax"])))
            m_sides = [(mk, float(q[mk])) for mk in ("MyMin", "MyMax")
                       if mk in q]
            if not n_sides or not m_sides:
                continue
            for n_lab, nq in n_sides:
                n_comb = gg * ng + gq * nq
                nc = max(-n_comb, 0.0)          # 受拉为正 → 受压为正
                for m_lab, mq in m_sides:
                    m_comb = gg * mg + gq * mq
                    md = abs(m_comb)
                    if nc == 0.0 and md == 0.0:
                        continue
                    any_effect = True
                    out["angle_count"] += 1
                    tag = f"{n_lab}·{m_lab}"
                    where = f"单元 {key[0]}·{key[1]} / {tag}"
                    if nc <= AXIAL_FRACTION * n_axial:
                        flex_angles += 1
                        angle_rows.append([
                            where, "flex", f"{n_comb / 1e3:.3f}",
                            f"{m_comb / 1e3:.3f}", f"{nc / 1e3:.3f}",
                            "—", "—", "归 flex 链（轴压 ≤5%·Nu）"])
                        continue
                    e0 = md / nc if nc > 0.0 else float("inf")
                    if e0 < ECCENTRICITY_FRACTION * h_m:
                        r_ax = section_strength.check_axial(
                            sec, nc, gamma0=out["gamma0"], l0=sec.l0)
                        chain = "axial"
                        ax_pairs.append((where, r_ax))
                        calc_cases.append(section_strength.CalcCase(
                            label=where, chain=chain, sec=sec, result=r_ax,
                            N=nc, gamma0=out["gamma0"], l0=sec.l0))
                        util = float(r_ax.util)
                        cap_txt = f"{r_ax.Nu / 1e3:.3f} kN"
                        note = "轴心受压（e0 <1% 截面高）"
                        out["axial_angles"] += 1
                    else:
                        # 负弯矩角点用实际送进验算函数的镜像截面，与 3D 同源。
                        sec_used = sec
                        m_used = m_comb
                        if m_comb < 0.0:
                            sec_neg, given = \
                                section_strength.mirror_section_for_negative(sec)
                            if given:
                                sec_used = sec_neg
                                m_used = -m_comb
                        r_ec = section_strength.check_eccentric(
                            sec_used, nc, m_used, gamma0=out["gamma0"],
                            l0=sec_used.l0)
                        chain = "eccentric"
                        ecc_pairs.append((where, r_ec))
                        calc_cases.append(section_strength.CalcCase(
                            label=where, chain=chain, sec=sec_used,
                            result=r_ec, N=nc, M=md, gamma0=out["gamma0"],
                            l0=sec_used.l0))
                        util = float(r_ec.util)
                        cap_txt = (f"{r_ec.Mu / 1e3:.3f} kN·m"
                                   if getattr(r_ec, "Mu", 0.0) > 0 else "—")
                        note = str(getattr(r_ec, "mode", "") or "偏心受压")
                        if str(getattr(r_ec, "note", "") or "").strip():
                            rn = str(r_ec.note).strip()
                            note += "；" + rn
                            if rn not in notes:
                                notes.append(rn)
                        if m_comb < 0.0:
                            note += "；负弯矩面（底板受压）"
                        out["eccentric_angles"] += 1
                    angle_rows.append([
                        where, chain, f"{n_comb / 1e3:.3f}",
                        f"{m_comb / 1e3:.3f}", f"{nc / 1e3:.3f}",
                        f"{e0:.6g}",
                        ("∞" if util == float("inf") else f"{util:.4f}"),
                        note])
                    controls.append({
                        "label": where, "chain": chain, "key": list(key),
                        "angle": tag, "N": n_comb, "M": m_comb,
                        "Nc": nc, "e0": e0, "util": util,
                        "capacity": cap_txt,
                    })

    if reasons:
        out["status"] = "refused"
        out["reasons"] = reasons
        out["note"] = status_text(out)
        return out
    out["flex_angles"] = flex_angles
    if not any_effect:
        out["status"] = "no_effect"
        out["note"] = status_text(out)
        return out
    if not ecc_pairs and not ax_pairs:
        out["status"] = "no_compression"
        out["note"] = status_text(out)
        return out

    tables: dict[str, dict[str, Any]] = {}
    if ecc_pairs:
        tables["capacity_axial_2d_eccentric_table"] = \
            section_strength.eccentric_table(ecc_pairs)
    if ax_pairs:
        tables["capacity_axial_2d_axial_table"] = \
            section_strength.axial_table(ax_pairs)
    tables["capacity_axial_2d_angles_table"] = {
        "title": "2D 压弯 / 轴压角点分派明细（同端配对）",
        "note": ("每个 (单元, 端) 枚举 2(N 侧)×2(M 侧) 角点；N 与 M 取自"
                 "同一端、不跨端不跨分量。逐分量独立极值不保证来自同一"
                 "车辆位置，属影响线包络法固有近似。轴压 ≤5%·fcd·b·h 的"
                 "角点归 flex 链，本章不出承载力结论。e0=Md/Nc（m）。"),
        "headers": ["断面 / 角点", "验算链", "N (kN)", "M (kN·m)",
                    "Nc (kN)", "e0 (m)", "利用率", "备注"],
        "rows": angle_rows,
    }
    # 与 3D `_fill_capacity_tables` 同源：每条链按利用率取最不利 3 个展开，
    # 全部角点留在角点明细表里，不靠放大计算书冒充「逐项复核都做了」。
    tables["capacity_axial_2d_sheet"] = \
        section_strength.capacity_calculation_sheet(calc_cases)
    # 压弯链中文结论（缺筋 / 非受压 / 反向破坏未覆盖）留痕到结论表。
    if notes and ecc_pairs:
        tables["capacity_axial_2d_eccentric_table"]["note"] += (
            " 结论附注：" + "；".join(notes) + "。")
    for tb in tables.values():
        tb["note"] = str(tb.get("note", "")) + " " + TABLE_NOTE_EXTRA
    out.update(status="ok", tables=tables, controls=controls)
    out["note"] = status_text(out)
    return out


def tables(d: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """报告 / 界面唯一取数口：状态非 ok 时为空 dict，不摆空表。"""
    d = d or {}
    if str(d.get("status", "not_requested")) != "ok":
        return {}
    return dict(d.get("tables") or {})
