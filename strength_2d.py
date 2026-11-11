"""strength_2d.py —— 二维截面应力验算的唯一口径层。

能力边界（先说清楚只做了一半）
------------------------------
`能力边界矩阵.md` §2 的「截面应力 / 承载力验算」在 2D 侧标 **P**：

- **做**：截面**应力**验算。效应侧只取 `Solve2DResult.final_beam` 经
  `combination.beam_combinations` 得到的三种组合内力，截面与材料取同一份
  `BridgeSpec`，交给 `section_strength.check` / `result_table` 出表。
- **本表不做**：**承载力** Mu / Vu / 压弯 / 轴压。本表只是截面边缘应力
  与强度设计值之比，不是承载力利用率。承载力 Mu / Vu 由第十二次新增的
  **独立 opt-in 口径层** `capacity_2d.py` 单独出章（需 `ElemTypeDef.rebar`
  的 `RebarSpec2D` 配筋契约）；未请求或模型无配筋时该章整章拒绝，
  不编一个配筋出来。压弯 / 轴压在 2D 仍不做（组合轴力 N 只有恒载单值、
  无活载轴力包络）。两章的边界措辞由 `capacity_note_for()` 按实际状态
  选择，不得新旧章自相矛盾；能力矩阵这一格仍标 P，不标 Y。

三条铁律
--------
1. **不解析内核「单元应力 (Pa)」段**。那段是内核用 `*R` 的 WT/WB 从
   `单元内力 (N, N*m)` 换算出来的第二套 σ；解析它会让计算书出现两套
   应力来源。更关键的是 `example_stage.cpp` 的 `_stages.txt` 根本没有
   应力段 —— 按段解析会让「有施工阶段的模型反而没结论」。本模块的
   唯一效应源是 `final_beam`（静态 / 阶段两条路径统一）。
2. **不静默兜底**。`SectionForCheck` 的 A / WT / WB / AW / grade / kind /
   板厚必须由模型**显式**给出，缺任一项整章 `refused` 并逐条列原因。
   特别是：缺 AW 会让 `τ` 静默取 0；缺板厚会让 `steel(grade, 0)` 落到
   **最薄板档（fd 最高、偏不安全）**；缺 WT/WB 时 Python 侧会退化成
   轴应力而内核是直接 throw —— 本模块按内核的严格口径走。
3. **结论与计算书同源**。表字典由 `section_strength.result_table` 构造，
   `Solve2DResult.strength_tables()` 是界面与 `write_2d_html` 的唯一取数口。

2D 组合的口径差异（不能照抄 3D）
--------------------------------
`BeamCombo.rows` 的轴力只有单值 `N` 且仅含恒载（活载包络不给轴力），
不能像 `result_3d._combo_stress` 那样去读 `NMin/NMax`（那样会把 2D 的
轴力悄悄丢成 0）。这里按「N 单值 × My min/max × Vz min/max × 三种组合」
共 12 个角点枚举，取利用率最大者，并在表注写明「N 不含活载」。
"""

from __future__ import annotations

from typing import Any

import code_material as cm
import section_strength

SOURCE = ("Solve2DResult.final_beam + combination.beam_combinations → "
          "section_strength.check")

CAPACITY_REFUSED_NOTE = (
    "承载力验算（Mu / Vu / 压弯 / 轴压）本次未做：本次未请求 2D 承载力章，"
    "或模型没有 opt-in 的 `ElemTypeDef.rebar`（`RebarSpec2D`）配筋契约，"
    "section_strength.py 明确要求「模型里没有配筋信息时明确跳过并说明，"
    "不编一个配筋出来」。本表只是截面边缘应力与强度设计值之比，"
    "不是正截面 / 斜截面承载力利用率，不得拿它冒充配筋设计的验算结论；"
    "本应力表不出 Mu / Vu 表、不补零。压弯 / 轴压在 2D 仍不做"
    "（组合轴力 N 只有恒载单值、无活载轴力包络）。"
    "能力矩阵这一格只能标 P，不标 Y。"
)

CAPACITY_LINK_NOTE = (
    "本表只是截面边缘应力与强度设计值之比，不是正截面 / 斜截面承载力利用率，"
    "不得拿它冒充配筋设计的验算结论；本应力表本身不出 Mu / Vu 表、不补零。"
    "承载力 Mu / Vu 见同源计算书里 opt-in 的「2D 正截面抗弯 / 斜截面抗剪"
    "承载力」章（`capacity_2d.py`，复用 `section_strength.check_capacity`）；"
    "压弯 / 轴压在 2D 仍不做，能力矩阵这一格只能标 P，不标 Y。"
)

#: 兼容旧调用方：默认是「未请求 / 无配筋」的拒绝口径。报告、界面与表注
#: 一律改用 `capacity_note_for(res.capacity)` 按实际状态选择，别直接读它。
CAPACITY_NOTE = CAPACITY_REFUSED_NOTE


def capacity_note_for(cap: dict[str, Any] | None) -> str:
    """应力章唯一的承载力边界措辞选择器。

    `Solve2DResult.capacity.status == "ok"` 时指向新章；否则（默认
    `not_requested` / 缺配筋 `refused` / 任何非 ok 状态）维持显式拒绝。
    """
    return (CAPACITY_LINK_NOTE
            if (cap or {}).get("status") == "ok"
            else CAPACITY_REFUSED_NOTE)

STRESS_NOTE_EXTRA = (
    "效应侧只取成桥态内力经 JTG D60 三种组合后的设计值，不解析内核"
    "「单元应力 (Pa)」段（那是同一批内力经 WT/WB 换算的第二套 σ，"
    "且施工阶段结果文件没有该段）。轴力 N 只有单值、仅含恒载（活载包络"
    "不给轴力）；My / Vz 按 min/max 两侧与三种组合共 12 个角点枚举，"
    "取利用率最大者并保留原符号，不按绝对值叠加。只列利用率最大的前 "
    "{limit} 个控制截面。γ₀={gamma0:g}（JTG D60-2015 §4.1.5，"
    "二级 1.0 / 一级 1.1 / 三级 0.9，乘在效应侧）。混凝土按 "
    "JTG 3362-2018 §7.1.5 的 0.5·fck 只是量级判据，不构成「验算通过」。"
    "轴向杆单元（TRUSS / CABLE / STRUT）不做截面应力验算。"
    "本表不构成规范合格判定。"
)


# ---------------------------------------------------------------- 前置校验


def _enum_value(v: Any) -> str:
    return str(getattr(v, "value", v) or "").upper()


def _section_of(t: Any, m: Any) -> tuple[section_strength.SectionForCheck | None, str]:
    """把一个 (ElemTypeDef, MaterialDef) 变成验算截面；缺项返回中文原因。

    字段名刻意与 `SectionForCheck` 一致。全部是**显式声明**的 opt-in 字段，
    不从 `MaterialDef.name`（自由文本，如「钢材 Q345」）里猜牌号或构件类型。
    """
    A = float(getattr(t, "A", 0.0) or 0.0)
    WT = float(getattr(t, "WT", 0.0) or 0.0)
    WB = float(getattr(t, "WB", 0.0) or 0.0)
    AW = float(getattr(t, "AW", 0.0) or 0.0)
    t_plate = float(getattr(t, "t_plate", 0.0) or 0.0)
    grade = str(getattr(m, "grade", "") or "").strip()
    kind = str(getattr(m, "kind", "") or "").strip().lower()

    if A <= 0.0:
        return None, "截面积 A 必须 > 0"
    if WT <= 0.0 or WB <= 0.0:
        return None, ("上/下缘截面模量 WT、WB 必须都 > 0（内核 BEAM 缺 WT/WB "
                      "直接报错，Python 侧不退化成轴应力）")
    if AW <= 0.0:
        return None, ("腹板总面积 AW 必须 > 0（缺值会让剪应力 τ 静默取 0，"
                      "等于跳过剪应力与折算应力校核）")
    if not grade:
        return None, "材料未显式给出强度等级 grade（不从材料 name 猜）"
    if kind not in ("steel", "concrete"):
        return None, ("材料未显式给出构件类型 kind，须为 'steel' 或 'concrete'"
                      f"（当前 {kind!r}）")
    if kind == "steel":
        if t_plate <= 0.0:
            return None, ("钢材未显式给出控制板厚 t_plate > 0 mm；"
                          "给 0 会落到 JTG D64-2015 表 3.2.1 最薄板档"
                          "（fd 最高、偏不安全）")
        try:
            cm.steel(grade, t_plate)
        except (KeyError, ValueError) as e:
            return None, f"钢材牌号 / 板厚查 JTG D64-2015 表 3.2.1 失败：{e}"
    else:
        try:
            cm.concrete(grade)
        except (KeyError, ValueError) as e:
            return None, f"混凝土强度等级查 JTG 3362-2018 表 3.1.3 失败：{e}"
    return section_strength.SectionForCheck(
        A=A, WT=WT, WB=WB, AW=AW, grade=grade, kind=kind,
        t_plate=t_plate if kind == "steel" else 0.0), ""


# ---------------------------------------------------------------- 状态文案


def status_text(s: dict[str, Any] | None) -> str:
    """缺结果 / 拒绝时的唯一中文文案；HTML 与界面不得另写一套。"""
    s = s or {}
    st = s.get("status", "not_requested")
    if st == "ok":
        return (f"已按 JTG D60 三种组合 × My/Vz 两侧角点枚举，"
                f"给出利用率最大的前 {s.get('limit', 0)} 个控制截面"
                f"（共枚举 {s.get('cases', 0)} 个角点，γ₀={s.get('gamma0', 1.0):g}）。"
                + ("组合含活载。" if s.get("has_live") else
                   "模型无活载包络，组合只有恒载项，不是完整的正常使用验算。")
                + capacity_note_for(s.get("capacity")))
    if st == "refused":
        reasons = list(s.get("reasons") or [])
        head = "本次未做 2D 截面应力验算：模型缺少显式声明的验算参数，整章拒绝，不补零、不回落。"
        shown = "；".join(reasons[:3])
        more = f"；另有 {len(reasons) - 3} 条同类问题" if len(reasons) > 3 else ""
        return head + shown + more + "。" + capacity_note_for(s.get("capacity"))
    if st == "no_beam_section":
        return ("本次未做 2D 截面应力验算：成桥态内力里没有可验算的梁单元"
                "（TRUSS / CABLE / STRUT 等轴向杆不做截面应力验算）。"
                + capacity_note_for(s.get("capacity")))
    return {
        "not_requested": ("本次未请求 2D 截面应力验算（默认关闭，勾选后由同一份"
                          "成桥态内力与 BridgeSpec 出表，不写进 .dat）。"),
        "unavailable": ("本次没有可用的成桥态内力 / 模型 / 规范组合结果，"
                        "2D 截面应力验算做不了；静力与活载结果不受影响。"),
    }.get(st, f"2D 截面应力验算状态 {st}，本次不出表。")


# ---------------------------------------------------------------- 主入口


def evaluate(res: Any, spec: Any, gamma0: float = 1.0,
             limit: int = 8) -> dict[str, Any]:
    """从一次 2D 求解结果产出应力验算结论与表字典。

    只由 `solve_2d(..., strength=True)` 调用；返回 ``{status, source, note,
    reasons, tables, capacity, capacity_note, gamma0, limit, cases, has_live,
    skipped_axial, beam_sections}``。`capacity` 只记 2D 承载力章的状态，
    供 `capacity_note_for()` 挑措辞；`tables` 非空当且仅当 `status == "ok"`。
    """
    # capacity 先于 strength 计算（见 solve_2d）：按实际状态挑措辞，避免
    # 应力章与承载力章一个说「做了」、一个说「没做」。
    cap = getattr(res, "capacity", None) or {}
    cap_state = {"status": str(cap.get("status", "not_requested"))}
    out: dict[str, Any] = {
        "status": "unavailable", "source": SOURCE, "note": "",
        "reasons": [], "tables": {},
        "capacity": cap_state,
        "capacity_note": capacity_note_for(cap_state),
        "gamma0": float(gamma0), "limit": int(limit), "cases": 0,
        "has_live": False, "skipped_axial": 0, "beam_sections": 0,
    }
    final = getattr(res, "final_beam", None) or {}
    combos = res.combinations() if hasattr(res, "combinations") else []
    if not final or spec is None or not combos:
        out["note"] = status_text(out)
        return out
    out["has_live"] = bool(getattr(combos[0], "has_live", False))

    elem_by_id = {int(e.id): e for e in (getattr(spec, "elements", []) or [])}
    type_by_id = {int(t.id): t for t in (getattr(spec, "elem_types", []) or [])}
    mat_by_id = {int(m.id): m for m in (getattr(spec, "materials", []) or [])}

    sections: dict[tuple[int, str], section_strength.SectionForCheck] = {}
    reasons: list[str] = []
    axial = 0
    seen_type: set[int] = set()
    for key in sorted(final):
        eid = key[0]
        e = elem_by_id.get(int(eid))
        if e is None:
            reasons.append(f"单元 {eid} 在 spec.elements 里找不到，无法取其截面")
            continue
        t = type_by_id.get(int(e.type_id))
        if t is None:
            reasons.append(f"单元 {eid} 引用的类型 {e.type_id} 在 spec.elem_types 里找不到")
            continue
        if _enum_value(getattr(t, "kind", "")) != "BEAM":
            axial += 1
            continue
        m = mat_by_id.get(int(getattr(t, "mat", 1)))
        sec, why = _section_of(t, m)
        if sec is None:
            if int(t.id) not in seen_type:
                reasons.append(f"单元 {eid}（实常数类型 {t.id}）：{why}")
                seen_type.add(int(t.id))
            continue
        sections[key] = sec
    out["skipped_axial"] = axial
    out["beam_sections"] = len(sections)
    if not sections and not reasons:
        out["status"] = "no_beam_section"
        out["note"] = status_text(out)
        return out
    if reasons:
        out["status"] = "refused"
        out["reasons"] = reasons
        out["note"] = status_text(out)
        return out

    cases: list[tuple[float, tuple[int, str], str, str, str,
                      section_strength.StressResult]] = []
    for combo in combos:
        cname = str(combo.spec.name)
        for key, row in combo.rows.items():
            sec = sections.get(key)
            if sec is None:
                continue
            N = float(row.get("N", 0.0))
            for mk in ("MyMin", "MyMax"):
                for vk in ("VzMin", "VzMax"):
                    r = section_strength.check(
                        sec, N, float(row.get(mk, 0.0)),
                        float(row.get(vk, 0.0)), out["gamma0"])
                    cases.append((r.util, key, cname, mk, vk, r))
    if not cases:
        out["note"] = status_text(out)
        return out
    cases.sort(key=lambda c: (-c[0], c[1][0], c[1][1]))
    top = cases[:max(1, out["limit"])]
    rows = [(f"单元 {key[0]}·{key[1]} / {cname} / {mk}·{vk}", r)
            for _, key, cname, mk, vk, r in top]
    table = section_strength.result_table(rows)
    table["title"] = "2D 控制截面应力验算（组合最不利角点）"
    table["note"] = (table["note"] + " "
                     + STRESS_NOTE_EXTRA.format(limit=out["limit"],
                                                gamma0=out["gamma0"])
                     + " " + capacity_note_for(out["capacity"])
                     + " 来源：" + SOURCE + "。")
    out.update(status="ok", tables={"stress_table": table},
               cases=len(cases))
    out["note"] = status_text(out)
    return out
