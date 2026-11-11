"""二维桥梁计算书的 Report 中间结构（旁路，不改 `write_2d_html`）。

2026-11-11 第八次（B2「同源计算书四出口」）：2D 以前只有
`export_html.write_2d_html` 一个自拼 HTML 的出口。本模块把同一份
`Solve2DResult` 装配成 `report_3d.Report`，然后**原样复用** 3D 的四个
写出器（`write_html` / `write_docx` / `write_csv` / `write_pdf`，统一入口
`write_all`）。那些写出器只读 `Report`，零 3D 字段硬编码，因此这里
不另写渲染器、不改 CSS、不改 `BKSEC<i>Z` 书签机制。

口径边界（不许越线）
--------------------
- 只消费 `Solve2DResult` 里已有的字段与 `BridgeSpec`，**不重算力学**、
  不解析内核「单元应力 (Pa)」段、不补 0、不猜参数。
- 数值与 `write_2d_html` 取同一批字段、用同一套换算表达式
  （N / N·m → kN / kN·m 除 1e3，位移 m → mm 乘 1e3，均保留 1 位小数），
  由 `verify_report_2d.py` 逐项对拍；这里不另立第二套口径。
- opt-in 的模态 / 截面应力章按 `status != "not_requested"` 出现，与旧
  HTML 完全同一判据；承载力 Mu / Vu 仍按 2D 无配筋契约显式写「做不了」。
- `write_2d_html` 一个字节不改，默认 `.dat` 一个字节不改。
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

import report_3d as R
from result_3d import Check, Metric

SOURCE = "Solve2DResult + BridgeSpec -> report_3d.Report（四出口同源）"

#: 四出口口径与能力边界，写在结论章，不摆成校核结论。
FOUR_EXPORTS_NOTE = (
    "本计算书由同一份 Report 中间结构输出 HTML / DOCX / CSV / PDF "
    "四种格式，数值与二维 HTML 计算书取同一份 Solve2DResult；"
    "PDF 由 HTML 打印并做页码 / 章节书签归档，打印失败显式报错、"
    "绝不生成占位 PDF。"
)
BOUNDARY_NOTE = (
    "2D 报告只列内核已给出的成桥态内力、挠度、施工阶段与车道活载包络、"
    "JTG D60 三种组合，以及 opt-in 的 2D 频率 / 线性屈曲因子与截面应力；"
    "除 opt-in 的 [MODAL2D] 明细段（BASIS=xy，只给 X/Y 两向参与质量与"
    "节点振型）、第十次 opt-in 的 [PRESTRESSLOSS2D] 明细段（2D 非阶段"
    "LOSSREF 束的 σl1~σl6 / σpe）外，2D 报告没有振型 / 参与质量 / "
    "承载力 Mu / Vu / 结构云图，缺的字段不补零，不拿应力利用率冒充"
    "承载力利用率。2D 没有出平面自由度，Z 向参与质量不存在（不是 0）；"
    "2D 没有阶段维度，分项损失明细不等于 3D 阶段分项损失；"
    "四出口机制对齐不等于 2D 力学能力与 3D 对齐。"
)
DETAIL_BOUNDARY_NOTE = (
    "本次 opt-in 的 [MODAL2D] 明细段由 2D 内核写出周期 T、X/Y 两向有效"
    "参与质量与节点振型；这是 2D 的 BASIS=xy 口径，没有 3D 的 Z 向参与质量，"
    "也没有结构云图与振型动画。参与质量是模态截断是否足够的判据，"
    "不是规范合格判定。"
)
LOSS_BOUNDARY_NOTE = (
    "本次 opt-in 的 [PRESTRESSLOSS2D] 明细段由 2D 内核写出非阶段 LOSSREF "
    "束的 σl1~σl6、σpe 与 σpe/σcon；字段名与单位同 3D，但 2D 没有阶段"
    "维度，不设 STAGE 占位。σl3 / σl5 / σl6 是后期损失，不进 σpe 与 Pe；"
    "σl2 只按直线筋；εcs / φ 需按附录 C 查表输入。分项损失是复核明细，"
    "不是规范合格判定，也不是 3D 阶段分项损失。"
)
NO_LIVE_NOTE = (
    "没有车道活载包络，组合里只有恒载项。这不是完整的正常使用验算。"
)
NO_COMBO_NOTE = (
    "成桥态内力为空，本次不出规范组合表；不补零、不摆空表。"
)
NO_CHECK_NOTE = (
    "本次没有车道活载竖向位移结果，不生成 L/600 校核结论；"
    "CSV 数据包里的校核表只有表头，不补零、不摆空结论。"
)
STAGE_AGE_NOTE = (
    "本表只列施工阶段末龄期。徐变 / 收缩的规范曲线说明见二维 HTML "
    "计算书，本 Report 不重算规范曲线、不另立第二套徐变口径。"
)
LIVE_BEAM_NOTE = (
    "这是车道影响线最不利布载的内力，已含 1+μ。下面的规范组合已经用过"
    "这份包络，不要再加一次。"
)
STAGE_ENV_NOTE = (
    "包络含未成桥的临时构型，例如边跨还是简支时的跨中弯矩。"
    "不能把这份极值与运营期活载相加。"
)
FINAL_BEAM_NOTE = (
    "成桥态 = Σ阶段累计；索力不累加、梁内力累加。N 只有单值且仅含恒载，"
    "与二维 HTML 计算书同源。"
)


# ---------------------------------------------------------------- 表装配


def _final_beam_table(res: Any) -> dict[str, Any] | None:
    """与 write_2d_html 的「成桥态梁端内力」表逐格同式。"""
    rows: list[list[Any]] = []
    for (eid, end), f in sorted((getattr(res, "final_beam", None) or {}).items()):
        rows.append([
            str(eid), str(end),
            f"{f.get('N', 0.0) / 1e3:.1f}",
            f"{f.get('My', 0.0) / 1e3:.1f}",
            f"{f.get('Vz', 0.0) / 1e3:.1f}",
        ])
    if not rows:
        return None
    return {"title": "成桥态梁端内力",
            "headers": ["单元", "端", "N (kN)", "My (kN·m)", "Vz (kN)"],
            "rows": rows, "note": FINAL_BEAM_NOTE}


def _combo_table(res: Any) -> tuple[dict[str, Any] | None, bool]:
    """返回 (控制弯矩表, 是否只有恒载项)。"""
    rows: list[list[Any]] = []
    no_live = False
    for c in res.combinations():
        worst = c.worst("My")
        if worst is None:
            continue
        (eid, end), val = worst
        rows.append([
            str(c.spec.name), str(c.spec.expr),
            f"{eid}·{end}", f"{val / 1e3:.1f}",
        ])
        if not c.has_live:
            no_live = True
    if not rows:
        return None, no_live
    tb: dict[str, Any] = {
        "title": "规范组合（控制弯矩）",
        "headers": ["组合", "表达式", "截面", "My (kN·m)"],
        "rows": rows,
    }
    if no_live:
        tb["note"] = NO_LIVE_NOTE
    return tb, no_live


def _stage_age_table(spec: Any) -> dict[str, Any] | None:
    rows: list[list[Any]] = []
    for s in getattr(spec, "stages", []) or []:
        t = float(getattr(s, "time", 0.0) or 0.0)
        if t > 0.0:
            rows.append([str(s.id), f"{t:g}"])
    if not rows:
        return None
    return {"title": "施工阶段龄期",
            "headers": ["阶段", "末龄期 (天)"],
            "rows": rows, "note": STAGE_AGE_NOTE}


def _stage_envelope_table(res: Any) -> dict[str, Any] | None:
    rows: list[list[Any]] = []
    for eid, f in sorted((getattr(res, "beam", None) or {}).items()):
        rows.append([
            str(eid),
            f"{f.get('Mmin', 0.0) / 1e3:.1f}",
            f"{f.get('Mmax', 0.0) / 1e3:.1f}",
            f"{f.get('Vmin', 0.0) / 1e3:.1f}",
            f"{f.get('Vmax', 0.0) / 1e3:.1f}",
        ])
    if not rows:
        return None
    return {"title": "施工阶段内力包络",
            "headers": ["单元", "My min (kN·m)", "My max (kN·m)",
                        "Vz min (kN)", "Vz max (kN)"],
            "rows": rows, "note": STAGE_ENV_NOTE}


def _live_beam_table(res: Any) -> dict[str, Any] | None:
    rows: list[list[Any]] = []
    live = getattr(res, "live_beam", None) or {}
    for (eid, end), f in sorted(live.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        rows.append([
            str(eid), str(end),
            f"{f.get('NMin', 0.0) / 1e3:.1f}",
            f"{f.get('NMax', 0.0) / 1e3:.1f}",
            f"{f.get('MyMin', 0.0) / 1e3:.1f}",
            f"{f.get('MyMax', 0.0) / 1e3:.1f}",
            f"{f.get('VzMin', 0.0) / 1e3:.1f}",
            f"{f.get('VzMax', 0.0) / 1e3:.1f}",
        ])
    if not rows:
        return None
    return {"title": "车道活载内力包络",
            "headers": ["单元", "端", "N min (kN)", "N max (kN)",
                        "My min (kN·m)", "My max (kN·m)",
                        "Vz min (kN)", "Vz max (kN)"],
            "rows": rows, "note": LIVE_BEAM_NOTE}


def _defl_note(res: Any) -> str:
    defl = res.max_vertical_deflection()
    if defl is None:
        return "本次没有竖向位移结果，不出挠度数值（展示量，不构成规范判定）。"
    return (f"最大竖向位移：节点 {defl[0]}　{defl[1] * 1000:.2f} mm。"
            "这是展示量，不构成规范判定。")


def _live_disp_check(res: Any, spec: Any) -> Check | None:
    """JTG D60 表 4.3.2 的 L/600，口径与二维 HTML 计算书逐字同源。"""
    disp = getattr(res, "live_disp", None) or {}
    if not disp:
        return None
    from solve_2d import _max_vertical_span

    span = _max_vertical_span(spec)
    if span <= 0.0:
        return None
    node, pair = min(disp.items(), key=lambda kv: kv[1][1])
    bare = pair[1]  # 位移包络已不计冲击（冲击只放大内力）
    lim = span / 600.0
    ok = abs(bare) <= lim
    detail = (
        f"节点 {node}，向下最大 {bare * 1000:.2f} mm；"
        f"表 4.3.2 限值 L/600 = {lim * 1000:.1f} mm"
        f"（最大跨径 {span:.0f} m），{'满足' if ok else '超出'}。"
        "与二维 HTML 计算书同一口径，不另立第二套判定。"
    )
    return Check("车道活载竖向位移 L/600", ok, detail,
                 value=abs(bare) * 1000.0, limit=lim * 1000.0,
                 unit="mm", level="warn")


def _metrics(res: Any) -> list[Metric]:
    defl = res.max_vertical_deflection()
    return [
        Metric("成桥态最大竖向位移",
               "—" if defl is None else f"{defl[1] * 1000:.2f}", "mm",
               "—" if defl is None else f"节点 {defl[0]}，dof=2 取绝对值最大（展示量）"),
        Metric("成桥态梁端内力条目",
               f"{len(getattr(res, 'final_beam', None) or {})}", "条",
               "与二维 HTML 计算书同源"),
        Metric("车道冲击系数 1+μ",
               f"{float(getattr(res, 'impact_factor', 1.0)):.3f}", "",
               "只放大内力，不放大位移包络"),
        Metric("结果指纹", str(res.fingerprint), "", "sha1(.dat)[:12]"),
    ]


# ---------------------------------------------------------------- 中间结构


def build_2d_document(res: Any, spec: Any) -> R.Report:
    """把 2D 求解结果装配成 `report_3d.Report`；四出口只读这一份。

    本函数**不写文件、不改 res / spec、不调内核**。章节完全由 `blocks`
    决定：缺数据的表不给、opt-in 未请求时整章不出现，不摆空表。
    """
    title = str(getattr(spec, "title", "")
                or getattr(res, "title", "") or "二维桥梁")
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    tabs: dict[str, dict[str, Any]] = {}
    blocks: list[dict[str, Any]] = []

    def add(key: str, tb: dict[str, Any] | None) -> None:
        if not tb:
            return
        tabs[key] = tb
        blocks.append({"type": "table", "key": key})

    # ① 结论与说明：指标卡、校核清单、四出口与能力边界
    blocks.append({"type": "h1", "text": "结论与说明"})
    blocks.append({"type": "metrics", "items": _metrics(res)})
    chk = _live_disp_check(res, spec)
    if chk is None:
        blocks.append({"type": "note", "text": NO_CHECK_NOTE})
    else:
        blocks.append({"type": "checks", "items": [chk]})
    blocks.append({"type": "bullets",
                   "items": [FOUR_EXPORTS_NOTE, BOUNDARY_NOTE]})

    # ② 成桥态内力与挠度
    blocks.append({"type": "h1", "text": "成桥态内力与挠度"})
    add("final_beam_table", _final_beam_table(res))
    blocks.append({"type": "note", "text": _defl_note(res)})

    # ③ 施工阶段与车道活载（三张表都没有就不出这一章）
    age = _stage_age_table(spec)
    env = _stage_envelope_table(res)
    live = _live_beam_table(res)
    if age or env or live:
        blocks.append({"type": "h1", "text": "施工阶段与车道活载"})
        add("stage_age_table", age)
        add("stage_envelope_table", env)
        add("live_beam_table", live)

    # ④ 规范组合
    combo, no_live = _combo_table(res)
    blocks.append({"type": "h1", "text": "规范组合（控制弯矩）"})
    if combo:
        add("combo_table", combo)
    else:
        blocks.append({"type": "note", "text": NO_COMBO_NOTE})
    if no_live and not combo:
        blocks.append({"type": "note", "text": NO_LIVE_NOTE})

    # ⑤ 2D 自振 / 线性屈曲（opt-in，判据与 write_2d_html 一致）
    m = getattr(res, "modal", None) or {}
    if m.get("status") != "not_requested":
        blocks.append({"type": "h1", "text": "2D 自振特性与线性屈曲"})
        mt = res.modal_tables() if hasattr(res, "modal_tables") else {}
        for key in ("modal_table", "buckling_table",
                    "modal_detail_table", "modal_shape_table"):
            add(key, mt.get(key))
        if not mt or "buckling_table" not in mt:
            from modal_2d import status_text

            blocks.append({"type": "note", "text": str(status_text(m))})
        if "modal_detail_table" in mt:
            blocks.append({"type": "note", "text": DETAIL_BOUNDARY_NOTE})
        elif (m.get("detail") or {}).get("status") not in (None, "not_requested"):
            from modal_2d import detail_status_text

            blocks.append({"type": "note",
                           "text": str(detail_status_text(m.get("detail")))})
        cons = m.get("consistency") or {}
        if cons.get("note"):
            blocks.append({"type": "note", "text": str(cons["note"])})

    # ⑥ 2D 截面应力验算（opt-in）：承载力永远单独写「做不了」
    s = getattr(res, "strength", None) or {}
    if s.get("status") != "not_requested":
        blocks.append({"type": "h1", "text": "2D 截面应力验算"})
        st = res.strength_tables() if hasattr(res, "strength_tables") else {}
        for key, tb in st.items():
            add(key, tb)
        if not st:
            from strength_2d import status_text as strength_status_text

            blocks.append({"type": "note",
                           "text": str(strength_status_text(s))})
        from strength_2d import CAPACITY_NOTE

        blocks.append({"type": "note", "text": str(CAPACITY_NOTE)})

    # ⑦ 2D 分项预应力损失明细（第十次 opt-in）：2D 非阶段束，
    #    不冒充 3D 的阶段分项损失口径。
    pl = getattr(res, "prestress_loss", None) or {}
    if pl.get("status") != "not_requested":
        blocks.append({"type": "h1", "text": "2D 分项预应力损失明细"})
        pt = (res.prestress_loss_tables()
              if hasattr(res, "prestress_loss_tables") else {})
        for key, tb in pt.items():
            add(key, tb)
        if not pt:
            from prestress_loss_2d import status_text as loss_status_text

            blocks.append({"type": "note",
                           "text": str(loss_status_text(pl))})
        blocks.append({"type": "note", "text": LOSS_BOUNDARY_NOTE})

    # ⑧ 诊断与能力边界
    blocks.append({"type": "h1", "text": "诊断与能力边界"})
    diags = [str(d) for d in (getattr(res, "diagnostics", None) or [])]
    if diags:
        blocks.append({"type": "bullets", "items": diags})
    else:
        blocks.append({"type": "note", "text": "本次求解内核未给出诊断信息。"})
    blocks.append({"type": "note", "text": NO_CHECK_NOTE if chk is None
                   else "L/600 只用于车道活载竖向位移，过程量不构成规范判定。"})

    meta = [
        ("桥型", title),
        ("维度", "二维（2D）"),
        ("求解器", str(getattr(res, "solver", "") or "FEM-Eigen")),
        ("结果指纹", str(res.fingerprint)),
        ("计算书", "Report 中间结构 → HTML / DOCX / CSV / PDF"),
        ("生成时间", now),
    ]
    return R.Report(
        title=title, subtitle="二维桥梁",
        kind=str(getattr(res, "kind", "2d") or "2d"),
        meta=meta, blocks=blocks, figures={}, tables=tabs,
        diagnostics=list(getattr(res, "diagnostics", None) or []),
        fingerprint=str(res.fingerprint), generated_at=now,
    )


# ---------------------------------------------------------------- 四出口


def write_2d_all(rep: R.Report, out_dir: str | Path,
                 stem: str = "report2d", *, pdf: bool = True) -> dict[str, Path]:
    """同一份 Report 交给 `report_3d.write_all`，不另写渲染器。

    `pdf=False` 用于界面分步降级 / 故障注入；PDF 失败由
    `report_3d.PDFExportError` 显式抛出，绝不假装四种格式都好了。
    """
    return R.write_all(rep, out_dir, stem=stem, pdf=pdf)


def build_and_write(res: Any, spec: Any, out_dir: str | Path,
                    stem: str = "report2d", *, pdf: bool = True
                    ) -> tuple[R.Report, dict[str, Path]]:
    """构造 + 落盘一步到位，返回 (Report, {格式: 路径})。"""
    rep = build_2d_document(res, spec)
    return rep, write_2d_all(rep, out_dir, stem=stem, pdf=pdf)
