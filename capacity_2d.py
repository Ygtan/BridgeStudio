"""capacity_2d.py —— 二维正截面抗弯 Mu / 斜截面抗剪 Vu 的唯一口径层。

能力边界（只闭环 Mu / Vu 一条 flex 链）
--------------------------------------
`能力边界矩阵.md` §2 的「截面应力 / 承载力验算」2D 侧仍标 **P**。本次只把
**Mu / Vu 半部**按 opt-in 接通，不把 `P` 涂成 `Y`：

- **做**：普通钢筋混凝土的正截面抗弯 Mu（JTG 3362-2018 §5.2.2 矩形 /
  §5.2.3 工形）与斜截面抗剪 Vu（§5.2.9，仅配箍筋、无弯起筋），
  My 正 / 负两个弯矩方向各自按受压面算 Mu、缺筋 `util=∞`。
- **不做**：偏心受压 / 轴心受压（§5.3.4 / §5.3.1）及其「反向破坏」验算。
  2D 的 `BeamCombo.rows` 轴力 N 只有恒载单值、没有活载轴力包络，
  照搬 3D `result_3d._capacity_cases` 的 5% 轴压 / 1% 偏心距分派阈值
  会把一个不完整效应源包装成完整判定。本层不设这两个阈值、不写占位链。
  预应力、受扭、抗冲切、腹板局部屈曲、Mz / Vy 亦不在范围内。

四条口径
--------
1. **唯一效应源**：`Solve2DResult.combinations()` 里 `key == "ultimate"` 的
   基本组合 `BeamCombo.rows`，按 `ElemTypeDef` 分组取**逐端最不利**的
   `M_pos`（My≥0 最大）、`M_neg`（My<0 的绝对值最大）、`Vd`（|Vz| 最大），
   与 3D `_capacity_cases` 同源。**不用** `strength_2d` 的 12 角点相乘：
   那是应力验算的枚举口径，M 与 V 混搭角点在承载力里没有力学意义。
2. **唯一配筋源**：`ElemTypeDef.rebar`（`RebarSpec2D`，opt-in Python-only，
   不写进 `.dat`）+ `MaterialDef.grade`（混凝土等级，与 3D 同源）。
   全部几何与配筋必须由模型显式给出；缺任一项**整章 `refused`**，
   不编配筋、不从 A/I/WT/WB 反推、不从材料 name 猜牌号、不补零。
3. **只复用纯函数**：`section_strength.check_capacity` /
   `capacity_table` / `capacity_faces_table` / `capacity_calculation_sheet`
   是唯一公式与表契约来源，本层不重写任何规范公式，不重算 K·ΔU=R。
   `γ0=1.0`、`α1=1.0`、`α3=1.1` 与 3D `_capacity_cases` 同源硬编码
   （`α2` 普通钢筋混凝土恒 1.0，由 `shear_capacity` 内部固定），
   不新增控件、不猜参数；出处在本模块与计算书表注里写明。
4. **结论与计算书同源**：`tables` 非空当且仅当 `status == "ok"`；
   `Solve2DResult.capacity_tables()` 是 HTML / 四出口 / 界面的唯一取数口。

本层是**纯 Python 校核**，不调 FE 内核、不改 `.dat`、不改默认 HTML。
"""

from __future__ import annotations

from typing import Any

import code_material as cm
import section_strength

SOURCE = ("Solve2DResult.combinations()[ultimate] + ElemTypeDef.rebar "
          "→ section_strength.check_capacity")
BASIS = "2d_beam_ultimate"

#: 与 3D `result_3d._capacity_cases` 的分派硬编码同源，不新增模型控件。
#: α2（预应力提高系数）普通钢筋混凝土恒 1.0，由 shear_capacity 内部固定。
GAMMA0 = 1.0
ALPHA1 = 1.0
ALPHA3 = 1.1

BOUNDARY_NOTE = (
    "本章只做普通钢筋混凝土正截面抗弯 Mu（JTG 3362-2018 §5.2.2 / §5.2.3）"
    "与斜截面抗剪 Vu（§5.2.9，仅配箍筋、无弯起筋）一条 flex 链。"
    "效应只取 JTG D60-2015 基本组合（key=ultimate）按单元类型分组的逐端最不利"
    "正弯矩、负弯矩、剪力，与 3D `result_3d._capacity_cases` 同源；"
    "不用 2D 截面应力的 12 角点相乘。2D 组合的轴力 N 只有恒载单值、"
    "没有活载轴力包络，故不做偏心受压 / 轴心受压分派（压弯 / 轴压仍不做），"
    "也不照搬 3D 的 5% / 1% 阈值；预应力、受扭、抗冲切、腹板局部屈曲、"
    "Mz / Vy 与 3D 全套承载力仍未覆盖。"
    "γ₀=1.0 / α1=1.0 / α3=1.1 与 3D 分派同源硬编码"
    "（α3 为受压翼缘影响系数，T/I 形取 1.1；α2 由 shear_capacity 内部固定 1.0），"
    "不新增控件、不猜参数。配筋来自 opt-in 的 `ElemTypeDef.rebar`"
    "（`RebarSpec2D`，Python-only，不写进 .dat）；几何与配筋未显式给出时整章拒绝，"
    "不补零、不编配筋。能力边界矩阵「截面应力 / 承载力验算」2D 仍标 P，不标 Y。"
)

TABLE_NOTE_EXTRA = (
    "2D 口径：效应取 JTG D60-2015 基本组合（1.2·S_G + 1.4·S_Q）按实常数类型"
    "分组的逐端最不利 M_pos / M_neg / Vd，不使用 12 角点；γ₀=1.0、α1=1.0、"
    "α3=1.1 与 3D 分派同源（JTG D60-2015 §4.1.5 / JTG 3362-2018 §5.2.9）。"
    "表中「模型输入」行的 2D 来源是 `RebarSpec2D`，不是 3D 的 `RebarSpec3D`；"
    "公式与表头仍由 `section_strength.py` 唯一给出。2D 不做压弯 / 轴压分派"
    "（组合轴力 N 只有恒载单值、无活载轴力包络），能力矩阵该格仍标 P。"
    "来源：" + SOURCE + "。"
)

STATUS_TEXT = {
    "not_requested": "本次未请求 2D 承载力 Mu / Vu 验算（默认关闭，不写进 .dat）。",
    "unavailable": "本次没有可用的成桥态内力 / 模型 / 规范组合结果，2D 承载力做不了；静力与活载结果不受影响。",
    "no_ultimate": "三种 JTG D60 组合里没有基本组合（key=ultimate），2D 承载力验算没有效应源，本次不做。",
    "no_beam_section": "本次未做 2D 承载力验算：成桥态内力里没有可验算的梁单元（TRUSS / CABLE / STRUT 等轴向杆没有配筋概念）。",
    "no_rebar_section": "本次未做 2D 承载力验算：梁单元类型都没有 opt-in 的 RebarSpec2D 配筋契约，不编一个配筋出来。",
    "no_effect": "基本组合在这些配筋截面上没有非零的弯矩 / 剪力效应，本次不出表、不补零。",
}


def status_text(s: dict[str, Any] | None) -> str:
    """缺结果 / 拒绝时的唯一中文文案；HTML、四出口与界面不得另写一套。"""
    s = s or {}
    st = str(s.get("status", "not_requested"))
    if st == "ok":
        return (f"已按 JTG D60 基本组合逐端取最不利效应，对 "
                f"{s.get('rebar_sections', 0)} 个显式配筋截面给出 Mu / Vu 利用率"
                f"（γ₀={s.get('gamma0', GAMMA0):g}、α1={s.get('alpha1', ALPHA1):g}、"
                f"α3={s.get('alpha3', ALPHA3):g}）。"
                + ("组合含活载。" if s.get("has_live") else
                   "模型无活载包络，组合只有恒载项，不是完整的承载力验算。")
                + BOUNDARY_NOTE)
    if st == "refused":
        reasons = list(s.get("reasons") or [])
        head = ("本次未做 2D 承载力 Mu / Vu 验算：模型缺少显式声明的配筋 / "
                "材料参数，整章拒绝，不补零、不回落。")
        shown = "；".join(reasons[:3])
        more = f"；另有 {len(reasons) - 3} 条同类问题" if len(reasons) > 3 else ""
        return head + shown + more + "。" + BOUNDARY_NOTE
    return STATUS_TEXT.get(st, f"2D 承载力 Mu / Vu 状态 {st}，本次不出表。") \
        + BOUNDARY_NOTE


def _enum_value(v: Any) -> str:
    return str(getattr(v, "value", v) or "").upper()


def _capacity_section(t: Any, m: Any) -> tuple[section_strength.CapacitySection | None, str]:
    """把 (ElemTypeDef.rebar, MaterialDef) 转成唯一的 CapacitySection。

    与 3D `result_3d._capacity_sections` 同构；缺项返回中文原因，不猜值。
    """
    rb = getattr(t, "rebar", None)
    if rb is None:
        return None, ("没有 opt-in 的 RebarSpec2D 配筋契约（不编一个配筋出来）")
    grade = str(getattr(m, "grade", "") or "").strip()
    if not grade:
        return None, "材料未显式给出强度等级 grade（不从材料 name 猜）"
    if grade.upper() not in cm.CONCRETE:
        return None, (f"材料等级 {grade!r} 不是混凝土强度等级，不做普通钢筋"
                      "混凝土承载力验算（钢截面走应力验算）")
    try:
        cm.concrete(grade)
        cm.rebar(str(rb.grade_rebar))
        cm.rebar_fsd_tension(str(rb.grade_stirrup))
    except (KeyError, ValueError) as e:
        return None, f"混凝土等级 / 钢筋牌号查 code_material 失败：{e}"
    neg = None
    if bool(getattr(rb, "negative_given", False)):
        def _opt(name: str) -> float | None:
            v = getattr(rb, name, None)
            return None if v is None else float(v)
        neg = section_strength.FaceSpec(
            bf=_opt("bf_neg"), hf=_opt("hf_neg"),
            As=_opt("As_neg"), Asp=_opt("Asp_neg"),
            as_=_opt("as_neg"), asp=_opt("asp_neg"))
    sec = section_strength.CapacitySection(
        h=float(rb.h), b=float(rb.b), As=float(rb.As),
        bf=float(rb.bf), hf=float(rb.hf),
        as_=float(rb.as_), Asp=float(rb.Asp), asp=float(rb.asp),
        Asp_given=bool(getattr(rb, "Asp_given", False)),
        grade_rebar=str(rb.grade_rebar),
        Asv=float(rb.Asv), sv=float(rb.sv),
        grade_stirrup=str(rb.grade_stirrup),
        concrete_grade=grade,
        l0=float(getattr(rb, "l0", 0.0) or 0.0),
        hollow=bool(getattr(rb, "hollow", False)),
        outer_bf=float(getattr(rb, "outer_bf", 0.0) or 0.0),
        negative=neg,
    )
    return sec, ""


def evaluate(res: Any, spec: Any, gamma0: float = GAMMA0,
             alpha1: float = ALPHA1, alpha3: float = ALPHA3) -> dict[str, Any]:
    """从一次 2D 求解结果产出 Mu / Vu 承载力结论与表字典。

    只由 `solve_2d(..., capacity=True)` 调用；返回 ``{status, source, note,
    reasons, tables, boundary_note, gamma0, alpha1, alpha3, has_live,
    skipped_axial, rebar_sections, controls}``。
    `tables` 非空当且仅当 `status == "ok"`。
    """
    out: dict[str, Any] = {
        "status": "unavailable", "source": SOURCE, "note": "",
        "basis": BASIS, "reasons": [], "tables": {},
        "boundary_note": BOUNDARY_NOTE,
        "gamma0": float(gamma0), "alpha1": float(alpha1),
        "alpha3": float(alpha3), "has_live": False,
        "skipped_axial": 0, "rebar_sections": 0, "controls": [],
    }
    final = getattr(res, "final_beam", None) or {}
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
        # 「完全没挂配筋契约」与「参数给错了」分开：前者是没启用本章，
        # 后者是模型声明有误。都整章拒绝，但状态与中文文案不同，不混。
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

    # 逐端最不利（与 3D _capacity_cases 同源）：保留弯矩符号分槽，剪力取 |·|。
    pairs: list[tuple[str, section_strength.CapacityResult]] = []
    calc_cases: list[section_strength.CalcCase] = []
    controls: list[dict[str, Any]] = []
    for label, sec, keys in caps:
        kset = set(keys)
        m_pos, m_neg, vd = 0.0, 0.0, 0.0
        k_pos: tuple[int, str] | None = None
        k_neg: tuple[int, str] | None = None
        k_v: tuple[int, str] | None = None
        for key, row in ult.rows.items():
            if key not in kset:
                continue
            for my in (row.get("MyMin", 0.0), row.get("MyMax", 0.0)):
                my = float(my)
                if my >= 0.0:
                    if k_pos is None or my > m_pos:
                        m_pos, k_pos = my, key
                elif k_neg is None or -my > m_neg:
                    m_neg, k_neg = -my, key
            for vz in (row.get("VzMin", 0.0), row.get("VzMax", 0.0)):
                av = abs(float(vz))
                if k_v is None or av > vd:
                    vd, k_v = av, key
        if m_pos == 0.0 and m_neg == 0.0 and vd == 0.0:
            continue                      # 无效应不摆空行、不补零
        r = section_strength.check_capacity(
            sec, m_pos, vd, gamma0=out["gamma0"], alpha1=out["alpha1"],
            alpha3=out["alpha3"], Md_neg=m_neg)
        pairs.append((label, r))
        calc_cases.append(section_strength.CalcCase(
            label=label, chain="flex", sec=sec, result=r,
            Md=m_pos, Md_neg=m_neg, Vd=vd,
            gamma0=out["gamma0"], alpha1=out["alpha1"],
            alpha3=out["alpha3"], l0=None))
        controls.append({
            "label": label, "M_pos": m_pos, "M_neg": m_neg, "Vd": vd,
            "m_pos_key": list(k_pos) if k_pos else None,
            "m_neg_key": list(k_neg) if k_neg else None,
            "v_key": list(k_v) if k_v else None,
            "util": r.util, "control": r.control,
        })
    if not pairs:
        out["status"] = "no_effect"
        out["note"] = status_text(out)
        return out

    tables: dict[str, dict[str, Any]] = {
        "capacity_2d_table": section_strength.capacity_table(pairs),
        "capacity_2d_faces_table": section_strength.capacity_faces_table(pairs),
        "capacity_2d_sheet": section_strength.capacity_calculation_sheet(
            calc_cases, max_per_chain=len(calc_cases)),
    }
    # 缺筋 / 缺箍 / 超筋 / 未登记负弯矩面的中文结论由 check_capacity 给出，
    # 结论表要把它留痕（同源、不重写公式、不补零），否则只看数字会漏判。
    notes: list[str] = []
    for _, r in pairs:
        n = str(getattr(r, "note", "") or "").strip()
        if n and n not in notes:
            notes.append(n)
    if notes:
        tables["capacity_2d_table"]["note"] += (
            " 结论附注：" + "；".join(notes) + "。")
    for tb in tables.values():
        tb["note"] = str(tb.get("note", "")) + " " + TABLE_NOTE_EXTRA
    out.update(status="ok", tables=tables, controls=controls)
    out["note"] = status_text(out)
    return out


def tables(d: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """报告 / 界面唯一取数口：状态非 ok 时为空 dict，不摆空表。"""
    s = d or {}
    return dict(s.get("tables") or {}) if s.get("status") == "ok" else {}
