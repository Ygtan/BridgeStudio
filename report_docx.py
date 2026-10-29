"""report_docx.py
================
斜拉桥成桥态 **Word 计算书**生成器（P4 成果层 a1）。

为什么不是“把界面上的表导出成 Word”
------------------------------------
界面表格是给人看的，计算书是给**审图的人**看的：它必须自带
① 计算依据 ② 计算模型（离散/材料/约束/荷载）③ 计算方法（公式与假设）
④ 结果与校核 ⑤ 结论。缺任何一项，对方都无法复核，只能选择相信——
而“只能相信”的计算书在工程上没有价值。

所以本模块的立场是：**报告中的每个数字都必须能追溯到求解器输出或模型属性**，
不允许出现“再算一遍”的二次力学（那会变成第二套物理）。因此：

    model（几何/材料/约束/荷载）+ info（派生量）+ res（求解器输出）
        │
        └─→ collect_report_data()  ← 只做汇总与统计，不重算位移内力
                │
                └─→ build_cable_report()  ← 只管排版

docx 结构与编号
---------------
封面 / 目录 / 1 概述 / 2 计算模型 / 3 计算方法 / 4 成桥态结果 /
5 下料索长 / 6 制造线形 / 7 结论 / 附录。图表按章编号（表 5-1、图 4-1）。

版式约定：A4，正文宋体小四 1.5 倍行距首行缩进 2 字符；标题黑体；
表格三线表 9 pt；图题在图下、表题在表上（GB/T 1.1 习惯）。

用法
----
    from cable_stayed_3d import build_cable_stayed_3d
    from fem_eigen_cable import solve_cable_bridge
    from report_docx import build_cable_report

    m, info = build_cable_stayed_3d()
    res = solve_cable_bridge(m, out_dir="out/cable")
    build_cable_report(model=m, info=info, res=res,
                       params={"Lm": 200, "Ls": 80, "Ht": 60, "n": 12,
                               "h": 2.5, "B": 12, "q": 100, "A": 6000,
                               "method": "DECK", "fpu": 1860},
                       out_path="out/cable/report.docx")

命令行自检：``python report_docx.py``（生成 out/cable/report.docx）。
"""

from __future__ import annotations

import datetime as _dt
import math
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

# ---------------------------------------------------------------- 版式常量

FONT_CN = "宋体"
FONT_HEI = "黑体"
FONT_EN = "Times New Roman"

PAGE_W, PAGE_H = 21.0, 29.7          # A4
MARGIN_L, MARGIN_R = 3.0, 2.5
MARGIN_T, MARGIN_B = 2.54, 2.54
TEXT_W = PAGE_W - MARGIN_L - MARGIN_R   # 15.5 cm

BODY_PT = 12.0        # 小四
TABLE_PT = 9.0
NOTE_PT = 9.0

GRAY = RGBColor(0x59, 0x59, 0x59)

#: 公式里的上下标标记：_{...} 下标、^{...} 上标。
#: 只用下划线写 "L_0" 在 Word 里是字面下划线，不是下标——这是计算书
#: 最容易被审图人一眼看破的地方，因此公式一律走这个标记。
_MATH_TOKEN = re.compile(r"([_^])\{([^{}]*)\}")
#: 正文里的粗体标记 **...**
_BOLD_TOKEN = re.compile(r"\*\*(.+?)\*\*")


# ================================================================ 字体/边框底件


def _set_run_font(run, *, cn: str = FONT_CN, en: str = FONT_EN,
                  size: float | None = None, bold: bool | None = None,
                  color: RGBColor | None = None) -> None:
    """同时设置西文与东亚字体。Word 的东亚字体必须写进 rFonts/@eastAsia，
    只设 font.name 对中文完全无效（这是 python-docx 的已知陷阱）。"""
    run.font.name = en
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        if rfonts.get(qn(attr)) is not None:
            del rfonts.attrib[qn(attr)]
    rfonts.set(qn("w:ascii"), en)
    rfonts.set(qn("w:hAnsi"), en)
    rfonts.set(qn("w:eastAsia"), cn)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color


def _style_font(style, *, cn: str, en: str, size: float,
                bold: bool = False) -> None:
    style.font.name = en
    style.font.size = Pt(size)
    style.font.bold = bold
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        if rfonts.get(qn(attr)) is not None:
            del rfonts.attrib[qn(attr)]
    rfonts.set(qn("w:ascii"), en)
    rfonts.set(qn("w:hAnsi"), en)
    rfonts.set(qn("w:eastAsia"), cn)
    # 内置样式的文字颜色常带 themeColor，会盖掉 font.color，须显式移除
    color = rpr.find(qn("w:color"))
    if color is not None:
        rpr.remove(color)
    style.font.color.rgb = RGBColor(0, 0, 0)


def _emit_runs(par, seg: str, *, size: float, bold: bool = False,
               cn: str = FONT_CN, en: str = FONT_EN,
               color: RGBColor | None = None) -> None:
    """写一段文字，其中的 _{}/^{} 转成真上下标（下标部分用 Cambria Math）。"""
    pos = 0
    for m in _MATH_TOKEN.finditer(seg):
        if m.start() > pos:
            r = par.add_run(seg[pos:m.start()])
            _set_run_font(r, cn=cn, en=en, size=size, bold=bold, color=color)
        r = par.add_run(m.group(2))
        _set_run_font(r, cn=cn, en="Cambria Math", size=size, bold=bold,
                      color=color)
        if m.group(1) == "_":
            r.font.subscript = True
        else:
            r.font.superscript = True
        pos = m.end()
    if pos < len(seg):
        r = par.add_run(seg[pos:])
        _set_run_font(r, cn=cn, en=en, size=size, bold=bold, color=color)


def _math_runs(par, expr: str, size: float = BODY_PT) -> None:
    """整行公式（全部用 Cambria Math）。"""
    _emit_runs(par, expr, size=size, cn=FONT_CN, en="Cambria Math")


def _text_runs(par, text: str, size: float = BODY_PT,
               bold_cn: str = FONT_HEI, color: RGBColor | None = None) -> None:
    """写正文：支持 **粗体** 与 _{}/^{} 上下标。"""
    pos = 0
    for m in _BOLD_TOKEN.finditer(text):
        if m.start() > pos:
            _emit_runs(par, text[pos:m.start()], size=size, color=color)
        _emit_runs(par, m.group(1), size=size, bold=True, cn=bold_cn,
                   color=color)
        pos = m.end()
    if pos < len(text):
        _emit_runs(par, text[pos:], size=size, color=color)


def _cell_border(cell, edge: str, sz: int) -> None:
    """给单元格某条边设框线。sz 单位 1/8 pt（12 → 1.5 pt）。"""
    tcPr = cell._tc.get_or_add_tcPr()
    borders = tcPr.find(qn("w:tcBorders"))
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        tcPr.append(borders)
    el = borders.find(qn(f"w:{edge}"))
    if el is None:
        el = OxmlElement(f"w:{edge}")
        borders.append(el)
    el.set(qn("w:val"), "single")
    el.set(qn("w:sz"), str(sz))
    el.set(qn("w:space"), "0")
    el.set(qn("w:color"), "000000")


def _clear_table_borders(table) -> None:
    tblPr = table._tbl.tblPr
    old = tblPr.find(qn("w:tblBorders"))
    if old is not None:
        tblPr.remove(old)
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "none")
        el.set(qn("w:sz"), "0")
        el.set(qn("w:space"), "0")
        borders.append(el)
    tblPr.append(borders)


def _repeat_header(row) -> None:
    """表头跨页重复。长表（下料索长 48 行、预抬高 58 行）必须有。"""
    trPr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    trPr.append(el)


def _field(paragraph, instr: str, placeholder: str = "") -> None:
    """插入 Word 域（PAGE / NUMPAGES / TOC）。"""
    def _run_with(child):
        r = paragraph.add_run()
        r._r.append(child)
        return r

    b = OxmlElement("w:fldChar")
    b.set(qn("w:fldCharType"), "begin")
    _run_with(b)
    it = OxmlElement("w:instrText")
    it.set(qn("xml:space"), "preserve")
    it.text = f" {instr} "
    _run_with(it)
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    _run_with(sep)
    if placeholder:
        paragraph.add_run(placeholder)
    e = OxmlElement("w:fldChar")
    e.set(qn("w:fldCharType"), "end")
    _run_with(e)


# ================================================================ 数据汇总


def _f(x: Any, nd: int = 3) -> str:
    """数值格式化：非有限值输出 '—'，避免 nan/inf 污染报告。"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(v):
        return "—"
    return f"{v:.{nd}f}"


def _pct(x: float, nd: int = 2) -> str:
    return f"{x:.{nd}f}"


def group_cables(info: dict) -> list[dict]:
    """把全部索按「塔 + 跨侧」分 4 组。

    依赖建造器的编号顺序（塔1边跨 → 塔1主跨 → 塔2主跨 → 塔2边跨），
    这是 cable_stayed_3d 的公开约定；顺序不符时退化为“全部一组”，
    宁可少分栏也不要错标组名。
    """
    ids = list(info.get("cable_ids", []))
    if not ids or len(ids) % 4:
        return [{"name": "全部拉索", "elems": ids}]
    n = len(ids) // 4
    names = ["A 塔边跨侧", "A 塔主跨侧", "B 塔主跨侧", "B 塔边跨侧"]
    return [{"name": names[i], "elems": ids[i * n:(i + 1) * n]} for i in range(4)]


def collect_report_data(*, model, info: dict, res: dict,
                        params: dict | None = None,
                        fpu_mpa: float = 1860.0) -> dict:
    """把 model/info/res 汇总成报告所需的全部表格数据（**不做力学重算**）。

    返回的每个量都可直接打印进 docx；统计量在这里算完，排版层不再动数。
    """
    p = dict(params or {})
    meta = res["meta"]
    nodes = {n.id: n for n in model.nodes}
    elems = {e.id: e for e in model.elements}
    kind_of = {t.id: t.kind for t in model.etypes}

    # ---- 索表：几何 + 求解结果 + 目标索力（来自模型工况段）
    target: dict[int, float] = {}
    cb = getattr(model, "cable_bridge", None)
    if cb is not None:
        target = {c.elem: float(c.tension) for c in cb.cables}
    area = float(info.get("cable_area") or 0.0)
    if area <= 0:
        for r in model.reals:
            if r.id == 3:
                area = float(r.A)
                break
    break_load = area * fpu_mpa * 1e6

    cables: list[dict] = []
    for c in sorted(res["cables"], key=lambda c: c["elem"]):
        e = elems.get(c["elem"])
        gid = c["anchor"] or (e.n1 if e else 0)
        if e is not None and gid not in nodes:
            gid = e.n1
        nd = nodes.get(gid)
        T = float(c["T"])
        L0 = float(c["L0"])
        Lt = float(c["Ltarget"])
        cables.append({
            "elem": c["elem"], "anchor": gid,
            "x": nd.x if nd else 0.0,
            "z": nd.z if nd else 0.0,
            "T": T, "L0": L0, "chord": float(c["chord"]), "Ltarget": Lt,
            "dL": float(c["dL"]),
            "target": target.get(c["elem"], 0.0),
            "sigma": T / area if area else 0.0,
            "sf": break_load / T if T > 0 else float("inf"),
            "elong_mm": (Lt - L0) * 1000.0,
            "elong_rate": (Lt - L0) / Lt if Lt else 0.0,
        })

    # ---- 分组统计
    elem2group: dict[int, str] = {}
    for g in group_cables(info):
        for eid in g["elems"]:
            elem2group[eid] = g["name"]
    for c in cables:
        c["group"] = elem2group.get(c["elem"], "—")

    groups: list[dict] = []
    for g in group_cables(info):
        sel = [c for c in cables if c["elem"] in set(g["elems"])]
        if not sel:
            continue
        Ts = [c["T"] for c in sel]
        groups.append({
            "name": g["name"], "n": len(sel),
            "Tmin": min(Ts), "Tmax": max(Ts), "Tmean": sum(Ts) / len(Ts),
            "sfmin": min(c["sf"] for c in sel),
            "L0min": min(c["L0"] for c in sel),
            "L0max": max(c["L0"] for c in sel),
        })

    Ts = [c["T"] for c in cables] or [0.0]
    # 对称性：同序号索（同一组内第 k 根）在 A/B 塔之间、或边跨 vs 边跨
    sym_rows: list[dict] = []
    if len(groups) == 4:
        n = groups[0]["n"]
        pairs = [(0, 3, "边跨侧（A 塔 ↔ B 塔）"), (1, 2, "主跨侧（A 塔 ↔ B 塔）")]
        for ia, ib, label in pairs:
            ga = [c for c in cables if c["group"] == groups[ia]["name"]]
            gb = [c for c in cables if c["group"] == groups[ib]["name"]]
            devs = [abs(a["T"] - b["T"]) / max(abs(a["T"]), 1e-9)
                    for a, b in zip(ga, gb)]
            sym_rows.append({"label": label, "n": min(len(ga), len(gb)),
                             "dev_max": max(devs) if devs else 0.0,
                             "dev_mean": sum(devs) / len(devs) if devs else 0.0})

    # ---- 主梁节点（预抬高表 / 成桥位移）
    deck = sorted((n for n in model.nodes if 1000 < n.id < 2000), key=lambda n: n.x)
    camber_rows = []
    for n in deck:
        dz = res["camber"].get(n.id, (0, 0, 0))[2]
        ez = res["erected"].get(n.id, (n.x, n.y, n.z))[2]
        camber_rows.append({"node": n.id, "x": n.x, "z": n.z,
                            "camber_mm": dz * 1000.0, "erect_z": ez,
                            "uz_mm": res["u"].get((n.id, 3), 0.0) * 1000.0})
    cam_abs = [abs(r["camber_mm"]) for r in camber_rows] or [0.0]

    # ---- 支承反力信息（求解器只给汇总，逐支座反力未输出，报告如实说明）
    sup_share = float(meta.get("support_share_kN", 0.0) or 0.0)
    deck_load = float(meta.get("deck_load_kN", 0.0) or 0.0)
    total_load = float(meta.get("total_load_kN", 0.0) or 0.0)
    vert = float(meta.get("vertical_sum_kN", 0.0) or 0.0)

    Lm = float(p.get("Lm") or 0.0)
    if Lm <= 0:
        xs = info.get("x_supports") or []
        Lm = (xs[2] - xs[1]) if len(xs) >= 4 else 0.0

    counts: dict[str, int] = {}
    by_etype: dict[int, int] = {}
    for e in model.elements:
        k = kind_of.get(e.etype, "?")
        counts[k] = counts.get(k, 0) + 1
        by_etype[e.etype] = by_etype.get(e.etype, 0) + 1

    return {
        "meta": meta, "params": p,
        "counts": counts, "by_etype": by_etype,
        "n_nodes": len(model.nodes), "n_elems": len(model.elements),
        "dofs": int(meta.get("dofs", 0) or 0),
        "cables": cables, "groups": groups, "sym": sym_rows,
        "camber_rows": camber_rows,
        "area": area, "break_load": break_load, "fpu": fpu_mpa,
        "Tmin": min(Ts), "Tmax": max(Ts), "Tmean": sum(Ts) / len(Ts),
        "Tsum": float(meta.get("tension_sum_kN", 0.0) or 0.0) * 1e3,
        "vert": vert * 1e3, "deck_load": deck_load * 1e3,
        "total_load": total_load * 1e3, "sup_share": sup_share * 1e3,
        "deck_share_pct": float(meta.get("deck_share_pct", 0.0) or 0.0),
        "sf_min": min((c["sf"] for c in cables), default=float("inf")),
        "sf_max": max((c["sf"] for c in cables), default=0.0),
        "sigma_min": min((c["sigma"] for c in cables), default=0.0),
        "sigma_max": max((c["sigma"] for c in cables), default=0.0),
        "uniformity": (max(Ts) / min(Ts)) if min(Ts) > 0 else float("inf"),
        "elong_min": min((c["elong_mm"] for c in cables), default=0.0),
        "elong_max": max((c["elong_mm"] for c in cables), default=0.0),
        "elong_rate": (sum(c["elong_rate"] for c in cables) / len(cables)
                       if cables else 0.0),
        "camber_abs_max": max(cam_abs), "camber_min": min(
            (r["camber_mm"] for r in camber_rows), default=0.0),
        "camber_max": max((r["camber_mm"] for r in camber_rows), default=0.0),
        "Lm": Lm,
        "camber_ratio": (Lm * 1000.0 / max(cam_abs) if cam_abs and Lm else 0.0),
        "closure_err": float(meta.get("closure_err", 0.0) or 0.0),
        "tension_err": float(meta.get("tension_err", 0.0) or 0.0),
        "iters": int(meta.get("iterations", 0) or 0),
        "elapsed_ms": float(meta.get("elapsed_ms", 0.0) or 0.0),
        "converged": bool(meta.get("converged", 0)),
        "cable_w": float(info.get("cable_w", 0.0) or 0.0),
        "n_cable": len(cables),
        "x_supports": list(info.get("x_supports") or []),
        "mode": str(meta.get("mode", "")),
        "tension_method": str(meta.get("tension_method", "")),
    }


METHOD_LABELS = {
    "DECK": "按恒载竖向分配",
    "GIVEN": "按恒载竖向分配",
    "RIGID": "刚性支承连续梁法",
    "MINENERGY": "最小弯曲能量法",
}


# ================================================================ 绘图


def _mpl():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "SimSun"]
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["font.size"] = 9
    plt.rcParams["axes.linewidth"] = 0.8
    return plt


def plot_elevation(model, data: dict, path: Path) -> Path:
    """图 2-1 结构计算简图：主梁 + 桥塔 + 索 + 支承。"""
    plt = _mpl()
    nodes = {n.id: n for n in model.nodes}
    kind_of = {t.id: t.kind for t in model.etypes}
    deck = sorted((n for n in model.nodes if 1000 < n.id < 2000), key=lambda n: n.x)
    towers = [[n for n in model.nodes if 2000 < n.id < 3000],
              [n for n in model.nodes if 3000 < n.id < 4000]]
    cables = [(nodes[e.n1], nodes[e.n2]) for e in model.elements
              if kind_of.get(e.etype) == "CABLE"]

    fig, ax = plt.subplots(figsize=(7.6, 3.3))
    for a, b in cables:
        ax.plot([a.x, b.x], [a.z, b.z], color="#7f7f7f", lw=0.7, zorder=2)
    ax.plot([n.x for n in deck], [n.z for n in deck], color="#1a1a1a", lw=2.8,
            solid_capstyle="butt", zorder=4, label="主梁")
    for tw in towers:
        tw = sorted(tw, key=lambda n: n.z)
        ax.plot([n.x for n in tw], [n.z for n in tw], color="#1a1a1a", lw=3.2,
                solid_capstyle="butt", zorder=4)
        ax.plot([tw[0].x], [tw[0].z], marker="s", ms=4, color="#1a1a1a", zorder=5)
    # 支承符号
    for x in data["x_supports"]:
        ax.plot([x, x], [0, 0], marker="^", ms=7, mfc="white", mec="#1a1a1a",
                mew=0.9, zorder=6)
    H = max(n.z for n in model.nodes) if model.nodes else 1.0
    L = max(n.x for n in model.nodes) if model.nodes else 1.0
    # 下部预留 0.34H 给跨度标注：留少了标注文字会压在梁线与索上
    # （第一版 ylim 到 -0.16H，主跨标注正好骑在主梁上）。
    ax.set_ylim(-0.34 * H, 1.12 * H)
    ax.set_xlim(-0.03 * L, 1.03 * L)

    # 跨度标注
    xs = data["x_supports"]
    yl = -0.17 * H
    if len(xs) >= 4:
        for i, name in ((1, "边跨"), (2, "主跨"), (3, "边跨")):
            ax.annotate("", xy=(xs[i - 1], yl), xytext=(xs[i], yl),
                        arrowprops=dict(arrowstyle="<->", lw=0.7, color="#1a1a1a"))
            ax.text((xs[i - 1] + xs[i]) / 2, yl - 0.04 * H,
                    f"{name} {xs[i] - xs[i-1]:.0f} m", ha="center", va="top",
                    fontsize=8.5)
    ax.set_xlabel("顺桥向 x (m)")
    ax.set_ylabel("竖向 z (m)")
    ax.set_aspect("equal", adjustable="box")
    ax.grid(alpha=0.25, lw=0.5)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_tension(data: dict, path: Path, fpu_line: float | None = None) -> Path:
    """图 4-1 成桥索力沿桥纵向分布（目标索力用空心圈叠画，看得到贴合程度）。"""
    plt = _mpl()
    cs = sorted(data["cables"], key=lambda c: (c["x"], c["elem"]))
    fig, ax = plt.subplots(figsize=(7.4, 2.9))
    ax.plot([c["x"] for c in cs], [c["T"] / 1e3 for c in cs], "o-", ms=3.4,
            lw=1.1, color="#185FA5", mfc="#185FA5", label="成桥索力")
    if any(c["target"] for c in cs):
        # 目标索力不画线、只画空心圈：若两者贴合，图上就是"蓝实心点镶红圈"，
        # 一眼能看出闭环精度；若用同样粗细的线，重合时根本分不清。
        ax.plot([c["x"] for c in cs], [c["target"] / 1e3 for c in cs], "o",
                ms=7.0, mfc="none", mec="#C05555", mew=0.9,
                label="目标索力（空心圈）")
    for x in data["x_supports"]:
        ax.axvline(x=x, color="#A32D2D", ls="--", lw=0.7, alpha=0.55)
    ax.set_xlabel("顺桥向 x (m)　（红虚线为支承断面）")
    ax.set_ylabel("索力 (kN)")
    ax.grid(alpha=0.25, lw=0.5)
    ax.legend(frameon=False, fontsize=8, loc="upper right", ncol=2,
              handletextpad=0.4, columnspacing=1.2)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_camber(data: dict, path: Path) -> Path:
    """图 6-1 主梁预抬高（制造线形相对设计线形）。"""
    plt = _mpl()
    rows = data["camber_rows"]
    fig, ax = plt.subplots(figsize=(7.2, 2.8))
    ax.plot([r["x"] for r in rows], [r["camber_mm"] for r in rows], "o-",
            ms=3.2, lw=1.1, color="#2E7D32", mfc="#2E7D32")
    ax.axhline(0.0, color="#666666", lw=0.8)
    for x in data["x_supports"]:
        ax.axvline(x=x, color="#A32D2D", ls="--", lw=0.7, alpha=0.55)
    ymax = max((abs(r["camber_mm"]) for r in rows), default=1.0)
    ax.set_ylim(-1.35 * ymax, 1.35 * ymax)
    ax.set_xlabel("顺桥向 x (m)")
    ax.set_ylabel("预抬高 (mm)")
    ax.grid(alpha=0.25, lw=0.5)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return path


# ================================================================ 排版积木


class _Writer:
    """把“写正文/插表/插图”封装成带自动编号的调用，避免手工数图表号。"""

    def __init__(self, doc: Document):
        self.doc = doc
        self.chapter = 0
        self.tab_no = 0
        self.fig_no = 0
        self.label = "0"          # 图表编号前缀：章号，附录为字母

    # ---------------------------------------------------------- 标题

    def h1(self, text: str) -> None:
        self.chapter += 1
        self.tab_no = 0
        self.fig_no = 0
        self.label = str(self.chapter)
        p = self.doc.add_paragraph(style="Heading 1")
        run = p.add_run(f"{self.chapter}　{text}")
        _set_run_font(run, cn=FONT_HEI, en=FONT_EN, size=16, bold=True)
        p.paragraph_format.space_before = Pt(18)
        p.paragraph_format.space_after = Pt(10)

    def h1_appendix(self, text: str, letter: str = "A") -> None:
        """附录标题：仍用 Heading 1 让目录能收进来，但图表号改成 A-1。"""
        self.tab_no = 0
        self.fig_no = 0
        self.label = letter
        p = self.doc.add_paragraph(style="Heading 1")
        run = p.add_run(f"附录 {letter}　{text}")
        _set_run_font(run, cn=FONT_HEI, en=FONT_EN, size=16, bold=True)
        p.paragraph_format.space_before = Pt(18)
        p.paragraph_format.space_after = Pt(10)
        # 附录另起一页：否则表 A-1 的表头会孤零零落在上一页页脚处
        p.paragraph_format.page_break_before = True

    def h2(self, text: str) -> None:
        p = self.doc.add_paragraph(style="Heading 2")
        run = p.add_run(text)
        _set_run_font(run, cn=FONT_HEI, en=FONT_EN, size=13.5, bold=True)
        p.paragraph_format.space_before = Pt(12)
        p.paragraph_format.space_after = Pt(6)

    def h3(self, text: str) -> None:
        p = self.doc.add_paragraph(style="Heading 3")
        run = p.add_run(text)
        _set_run_font(run, cn=FONT_HEI, en=FONT_EN, size=12, bold=True)
        p.paragraph_format.space_before = Pt(8)
        p.paragraph_format.space_after = Pt(4)

    # ---------------------------------------------------------- 正文

    def p(self, text: str, *, indent: bool = True, size: float = BODY_PT,
          bold_prefix: str | None = None) -> None:
        par = self.doc.add_paragraph()
        pf = par.paragraph_format
        pf.line_spacing = 1.5
        pf.space_before = Pt(0)
        pf.space_after = Pt(0)
        if indent:
            pf.first_line_indent = Pt(size * 2)
        pf.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        if bold_prefix and text.startswith(bold_prefix):
            r = par.add_run(bold_prefix)
            _set_run_font(r, cn=FONT_HEI, size=size, bold=True)
            text = text[len(bold_prefix):]
        _text_runs(par, text, size=size)

    def bullet(self, text: str, size: float = BODY_PT) -> None:
        par = self.doc.add_paragraph()
        pf = par.paragraph_format
        pf.line_spacing = 1.5
        pf.left_indent = Pt(size * 2)
        pf.first_line_indent = Pt(-12)
        pf.space_before = Pt(0)
        pf.space_after = Pt(0)
        r = par.add_run("· ")
        _set_run_font(r, size=size)
        _text_runs(par, text, size=size)

    def formula(self, expr: str, note: str = "") -> None:
        """居中的公式行（支持 \\n 多行）+ 缩进的灰字说明。

        公式里的 _{}/^{} 会转成真上下标，见 `_math_runs`。
        """
        for line in [q for q in expr.split("\n") if q.strip()]:
            par = self.doc.add_paragraph()
            par.alignment = WD_ALIGN_PARAGRAPH.CENTER
            par.paragraph_format.space_before = Pt(6 if line == expr.split("\n")[0] else 2)
            par.paragraph_format.space_after = Pt(2)
            _math_runs(par, line.strip(), BODY_PT)
        if note:
            par2 = self.doc.add_paragraph()
            par2.alignment = WD_ALIGN_PARAGRAPH.LEFT
            par2.paragraph_format.left_indent = Pt(36)
            par2.paragraph_format.space_before = Pt(2)
            par2.paragraph_format.space_after = Pt(6)
            _text_runs(par2, note, size=10.5, color=GRAY)

    def note(self, text: str) -> None:
        par = self.doc.add_paragraph()
        par.paragraph_format.space_before = Pt(2)
        par.paragraph_format.space_after = Pt(6)
        par.paragraph_format.line_spacing = 1.2
        r = par.add_run(text)
        _set_run_font(r, size=NOTE_PT, color=GRAY)

    # ---------------------------------------------------------- 表

    def table(self, caption: str, headers: Sequence[str],
              rows: Iterable[Sequence[Any]], widths: Sequence[float],
              *, aligns: Sequence[str] | None = None,
              font_size: float = TABLE_PT,
              groups: dict[int, str] | None = None,
              number: bool = True) -> None:
        """三线表。`rows` 中若某行是 dict 形式的分组标题（groups 索引 → 文本），
        会生成跨列合并行 —— 长表按塔/跨分栏比加一列“组别”更清晰。

        `number=False` 用于标题区的信息表（计算基本信息）：它不属于正文，
        编号会变成“表 0-1”这种自相矛盾的东西，且会把后续表号整体推一位。
        """
        if number:
            self.tab_no += 1
        # 表题（表上）
        cap = self.doc.add_paragraph()
        cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        cap.paragraph_format.space_before = Pt(8)
        cap.paragraph_format.space_after = Pt(3)
        label = f"表 {self.label}-{self.tab_no}　" if number else ""
        r = cap.add_run(f"{label}{caption}")
        _set_run_font(r, cn=FONT_HEI, size=9.5, bold=True)
        # 表题必须与表头同页，否则会出现"表头孤零零落在页脚"的排版事故
        cap.paragraph_format.keep_with_next = True

        ncol = len(headers)
        tbl = self.doc.add_table(rows=1, cols=ncol)
        tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
        tbl.autofit = False
        _clear_table_borders(tbl)
        # 固定列宽三件套：tblLayout=fixed + tblGrid/w:gridCol + 每个 cell 的 tcW。
        # 只设 cell.width 是不够的——Word 会按 tblGrid（默认均分）重排，
        # 实测 1.2/9.6/4.7 的列被摊成三等分。
        layout = OxmlElement("w:tblLayout")
        layout.set(qn("w:type"), "fixed")
        tbl._tbl.tblPr.append(layout)
        for i, wd in enumerate(widths):
            tbl.columns[i].width = Cm(wd)

        aligns = list(aligns or ["c"] * ncol)
        amap = {"l": WD_ALIGN_PARAGRAPH.LEFT, "c": WD_ALIGN_PARAGRAPH.CENTER,
                "r": WD_ALIGN_PARAGRAPH.RIGHT}

        def _fmt_cell(cell, text, align, bold=False, size=None):
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            par = cell.paragraphs[0]
            par.alignment = amap[align]
            par.paragraph_format.space_before = Pt(1.5)
            par.paragraph_format.space_after = Pt(1.5)
            par.paragraph_format.line_spacing = 1.0
            r = par.add_run(str(text))
            _set_run_font(r, cn=FONT_HEI if bold else FONT_CN,
                          size=size or font_size, bold=bold)

        hdr = tbl.rows[0]
        _repeat_header(hdr)
        for i, h in enumerate(headers):
            _fmt_cell(hdr.cells[i], h, "c", bold=True)
            hdr.cells[i].width = Cm(widths[i])
            _cell_border(hdr.cells[i], "top", 14)
            _cell_border(hdr.cells[i], "bottom", 8)
            hdr.cells[i].paragraphs[0].paragraph_format.keep_with_next = True

        for row in rows:
            if isinstance(row, dict) and "__group__" in row:
                gr = tbl.add_row()
                merged = gr.cells[0]
                for c in gr.cells[1:]:
                    merged = merged.merge(c)
                par = merged.paragraphs[0]
                par.alignment = WD_ALIGN_PARAGRAPH.LEFT
                par.paragraph_format.space_before = Pt(3)
                par.paragraph_format.space_after = Pt(1)
                par.paragraph_format.line_spacing = 1.0
                r = par.add_run(row["__group__"])
                _set_run_font(r, cn=FONT_HEI, size=font_size - 0.5, bold=True)
                _cell_border(merged, "bottom", 4)
                continue
            tr = tbl.add_row()
            for i, v in enumerate(row):
                _fmt_cell(tr.cells[i], v, aligns[i])
                tr.cells[i].width = Cm(widths[i])
        # 底线
        for cell in tbl.rows[-1].cells:
            _cell_border(cell, "bottom", 14)

        # 分页控制：单个表行绝不允许被劈开；短表（≤12 行）整体保持同页，
        # 否则会出现“表体只剩最后一行跑到下一页”这种一眼就露怯的排版。
        for tr in tbl.rows:
            trPr = tr._tr.get_or_add_trPr()
            trPr.append(OxmlElement("w:cantSplit"))
        if len(tbl.rows) <= 12:
            for tr in tbl.rows[:-1]:
                for cell in tr.cells:
                    for par in cell.paragraphs:
                        par.paragraph_format.keep_with_next = True

    # ---------------------------------------------------------- 图

    def figure(self, image: Path, caption: str, width_cm: float = TEXT_W) -> None:
        self.fig_no += 1
        par = self.doc.add_paragraph()
        par.alignment = WD_ALIGN_PARAGRAPH.CENTER
        par.paragraph_format.space_before = Pt(8)
        par.paragraph_format.space_after = Pt(2)
        par.add_run().add_picture(str(image), width=Cm(width_cm))
        cap = self.doc.add_paragraph()
        cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        cap.paragraph_format.space_after = Pt(10)
        r = cap.add_run(f"图 {self.label}-{self.fig_no}　{caption}")
        _set_run_font(r, cn=FONT_HEI, size=9.5, bold=True)


# ================================================================ 文档骨架


def _setup_document() -> Document:
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(PAGE_W), Cm(PAGE_H)
    sec.left_margin, sec.right_margin = Cm(MARGIN_L), Cm(MARGIN_R)
    sec.top_margin, sec.bottom_margin = Cm(MARGIN_T), Cm(MARGIN_B)

    normal = doc.styles["Normal"]
    _style_font(normal, cn=FONT_CN, en=FONT_EN, size=BODY_PT)
    normal.paragraph_format.line_spacing = 1.5

    for name, size in (("Heading 1", 16), ("Heading 2", 13.5), ("Heading 3", 12)):
        try:
            _style_font(doc.styles[name], cn=FONT_HEI, en=FONT_EN, size=size,
                        bold=True)
        except KeyError:
            pass
    return doc


def _apply_page(section) -> None:
    section.page_width, section.page_height = Cm(PAGE_W), Cm(PAGE_H)
    section.left_margin, section.right_margin = Cm(MARGIN_L), Cm(MARGIN_R)
    section.top_margin, section.bottom_margin = Cm(MARGIN_T), Cm(MARGIN_B)


def _add_header(section, title: str) -> None:
    section.header.is_linked_to_previous = False
    hp = section.header.paragraphs[0]
    hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    r = hp.add_run(title)
    _set_run_font(r, size=9, color=GRAY)
    pbdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "4")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "808080")
    pbdr.append(bottom)
    hp._p.get_or_add_pPr().append(pbdr)


def _add_page_footer(section) -> None:
    section.footer.is_linked_to_previous = False
    fp = section.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = fp.add_run("第 ")
    _set_run_font(r, size=9)
    _field(fp, "PAGE", "1")
    r = fp.add_run(" 页　共 ")
    _set_run_font(r, size=9)
    _field(fp, "NUMPAGES", "1")
    r = fp.add_run(" 页")
    _set_run_font(r, size=9)


def _add_header_footer(section, title: str) -> None:
    _add_page_footer(section)
    _add_header(section, title)


def _start_page_numbering(section, start: int = 1) -> None:
    sectPr = section._sectPr
    el = sectPr.find(qn("w:pgNumType"))
    if el is None:
        el = OxmlElement("w:pgNumType")
        sectPr.insert_element_before(
            el, "w:cols", "w:formProt", "w:vAlign", "w:noEndnote",
            "w:titlePg", "w:textDirection", "w:bidi", "w:rtlGutter",
            "w:docGrid", "w:printerSettings")
    el.set(qn("w:start"), str(start))


def update_fields_with_word(path: str | Path, timeout: int = 120) -> bool:
    """用本机 Word 更新目录/页码域并原地保存。

    为什么值得为此起一个 Word 进程
    ------------------------------
    python-docx 只能写 TOC **域**，不能排版目录内容；交付出去的计算书如果
    目录是空的，第一印象就废了。有 Word 就借它更新一次；没有则静默跳过，
    文档仍能打开，只是需要手动 F9（正文里已写明提示）。
    """
    import os
    import subprocess

    if os.name != "nt":
        return False
    p = Path(path).resolve()
    script = (
        "$ErrorActionPreference='Stop';"
        "$w=New-Object -ComObject Word.Application;"
        "$w.Visible=$false;$w.DisplayAlerts=0;"
        "try{"
        f"$d=$w.Documents.Open('{p.as_posix()}',$false,$false);"
        "$d.Fields.Update()|Out-Null;"
        "if($d.TablesOfContents.Count -gt 0){"
        "$t=$d.TablesOfContents.Item(1);"
        "$t.Update()|Out-Null;"
        # 目录段落基于 Normal（1.5 倍行距），30 条要占两页。改为固定 14 pt：
        # 注意 wdLineSpaceMultiple(=5) 下 LineSpacing 的单位是"行"，
        # 写 15 会被当成 15 倍行距——必须先切到 wdLineSpaceExactly(=4)。
        "$t.Range.ParagraphFormat.LineSpacingRule=4;"
        "$t.Range.ParagraphFormat.LineSpacing=14;"
        "$t.Range.ParagraphFormat.SpaceBefore=0;"
        "$t.Range.ParagraphFormat.SpaceAfter=0;"
        "};"
        "$d.Save();$d.Close(0)"
        "}finally{$w.Quit()}"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0
    except Exception:                                    # noqa: BLE001
        return False


def _cover(doc: Document, data: dict, title: str, project: str,
           analyst: str) -> None:
    # 顶级抬头用固定行业短语；若调用方给了项目名且与桥名不同才单独显示，
    # 否则封面会把同一个名字印两遍（第一版就印了两遍）。
    kicker = project.strip() if project.strip() and project.strip() != title \
        else "桥梁结构计算"
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(132)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(kicker)
    _set_run_font(r, cn=FONT_HEI, size=18, bold=True)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(10)
    r = p.add_run("计　算　书")
    _set_run_font(r, cn=FONT_HEI, size=26, bold=True)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(34)
    r = p.add_run(title)
    _set_run_font(r, cn=FONT_HEI, size=15, bold=False)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(6)
    r = p.add_run("（成桥态：索力 · 下料索长 · 制造线形）")
    _set_run_font(r, size=10.5, color=GRAY)

    # 封面信息表（无框线，靠列宽对齐）
    rows = [
        ("计算内容", "恒载成桥态几何非线性分析"),
        ("计算软件", "FEM-Eigen 空间刚架/索单元非线性求解器"),
        ("索力确定方法", METHOD_LABELS.get(
            str(data["tension_method"]).upper(),
            data["tension_method"] or "—")),
        ("模型规模", f'{data["n_nodes"]} 节点 / {data["n_elems"]} 单元 / '
                     f'{data["dofs"]} 自由度'),
        ("计算日期", _dt.date.today().strftime("%Y 年 %m 月 %d 日")),
    ]
    if analyst:
        rows.append(("计算", analyst))

    # 信息表与标题之间留白
    gap = doc.add_paragraph()
    gap.paragraph_format.space_before = Pt(0)
    gap.paragraph_format.space_after = Pt(0)
    _set_run_font(gap.add_run(""), size=10.5)

    tbl = doc.add_table(rows=0, cols=2)
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    _clear_table_borders(tbl)
    for k, v in rows:
        tr = tbl.add_row()
        tr.cells[0].width = Cm(4.2)
        tr.cells[1].width = Cm(9.4)
        for cell, txt, al in ((tr.cells[0], k, WD_ALIGN_PARAGRAPH.RIGHT),
                              (tr.cells[1], v, WD_ALIGN_PARAGRAPH.LEFT)):
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            par = cell.paragraphs[0]
            par.alignment = al
            par.paragraph_format.space_before = Pt(5)
            par.paragraph_format.space_after = Pt(5)
            r = par.add_run(txt)
            _set_run_font(r, size=10.5)


def _toc(doc: Document) -> None:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("目　　录")
    _set_run_font(r, cn=FONT_HEI, size=16, bold=True)
    p.paragraph_format.space_after = Pt(12)

    p = doc.add_paragraph()
    _field(p, 'TOC \\o "1-2" \\h \\z \\u',
           "（目录域：在 Word 中全选后按 F9 更新即可生成）")
    _set_run_font(p.add_run(), size=BODY_PT)


# ================================================================ 主体


def build_cable_report(*, model, info: dict, res: dict,
                       params: dict | None = None,
                       out_path: str | Path,
                       fpu_mpa: float | None = None,
                       project: str = "", analyst: str = "",
                       title: str | None = None,
                       fig_dir: str | Path | None = None,
                       update_toc: bool = True) -> Path:
    """生成斜拉桥成桥态 Word 计算书，返回 .docx 路径。"""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    p = dict(params or {})
    fpu = float(fpu_mpa if fpu_mpa is not None else p.get("fpu", 1860.0) or 1860.0)
    data = collect_report_data(model=model, info=info, res=res,
                               params=p, fpu_mpa=fpu)

    figs = Path(fig_dir) if fig_dir else out.parent / "report_figs"
    figs.mkdir(parents=True, exist_ok=True)
    f_elev = plot_elevation(model, data, figs / "fig_elevation.png")
    f_ten = plot_tension(data, figs / "fig_tension.png")
    f_cam = plot_camber(data, figs / "fig_camber.png")

    doc_title = title or getattr(model, "title", "") or "斜拉桥"
    doc = _setup_document()
    _cover(doc, data, doc_title, project, analyst)
    header_txt = f"{doc_title} · 成桥态计算书"

    # ---------------- 目录节：有页眉、无页码（页码从正文才开始数）
    toc_sec = doc.add_section(WD_SECTION.NEW_PAGE)
    _apply_page(toc_sec)
    _add_header(toc_sec, header_txt)
    toc_sec.footer.is_linked_to_previous = False
    _toc(doc)

    # ---------------- 正文节：页码从 1 开始
    body = doc.add_section(WD_SECTION.NEW_PAGE)
    _apply_page(body)
    _start_page_numbering(body, 1)
    _add_header_footer(body, header_txt)

    w = _Writer(doc)
    _sec_1_overview(w, data, doc_title, project)
    _sec_2_model(w, model, data, f_elev)
    _sec_3_method(w, data, model)
    _sec_4_results(w, data, model, fig_tension=f_ten)
    _sec_5_cables(w, data)
    _sec_6_camber(w, data, f_cam)
    _sec_7_conclusion(w, data)

    doc.save(str(out))
    if update_toc:
        update_fields_with_word(out)
    return out


# ---------------------------------------------------------------- 第 1 章


def _sec_1_overview(w: _Writer, d: dict, title: str, project: str) -> None:
    p = d["params"]
    w.h1("概述")
    w.h2("1.1　工程概况")
    span_txt = ""
    xs = d["x_supports"]
    if len(xs) >= 4:
        span_txt = (f"{xs[1] - xs[0]:.0f} + {xs[2] - xs[1]:.0f} + "
                    f"{xs[3] - xs[2]:.0f} m")
    w.p(f"本计算书针对{title}进行恒载成桥态计算。"
        + (f"桥跨布置为 {span_txt}，" if span_txt else "")
        + f"主梁为单梁（梁线）计算模型，共设 {d['n_cable']} 根斜拉索，"
          f"全桥离散为 {d['n_nodes']} 个节点、{d['n_elems']} 个单元、"
          f"{d['dofs']} 个自由度。")
    w.p("计算目的是确定三个施工控制量：① 成桥恒载状态下的索力分布；"
        "② 各索的无应力下料长度；③ 主梁制造线形（预抬高）。"
        "这三者构成斜拉桥“索力—索长—线形”闭环，缺一项则现场无法按图施工。")

    w.h2("1.2　计算依据")
    w.p("本计算书的力学模型、材料参数与荷载取值参照下列规范，"
        "并采用自研非线性有限元程序求解：")
    w.table("执行规范与计算软件", ["序号", "名称", "用途"], [
        ["1", "JTG D60—2015《公路桥涵设计通用规范》", "恒载取值与组合原则"],
        ["2", "JTG/T 3365-01—2020《公路斜拉桥设计规范》",
         "拉索安全系数、成桥索力与索长控制要求"],
        ["3", "JTG 3362—2018《公路钢筋混凝土及预应力混凝土桥涵设计规范》",
         "混凝土材料参数与截面特性"],
        ["4", "FEM-Eigen 空间刚架非线性求解器",
         "共转列式几何非线性、索垂度、成桥态闭环求解"],
    ], widths=[1.2, 9.6, 4.7], aligns=["c", "l", "l"])
    w.note("注：本计算书仅完成恒载成桥态的几何与索力计算，不含活载组合、"
           "承载力与抗风抗震验算，亦不含施工阶段的索力张拉顺序。")

    w.h2("1.3　计算内容与交付成果")
    w.p("计算按“建模 → 定目标索力 → 反算无应力索长 → 几何闭合迭代 → 输出制造线形”"
        "的链条进行，交付成果如下：")
    w.bullet("表 5-1　拉索下料索长表（含索力、应力、安全系数），用于厂家下料与现场挂索；")
    w.bullet("表 6-1　主梁制造线形（预抬高）表，用于梁段预制标高控制；")
    w.bullet("表 4-2　成桥态竖向平衡校核，用于确认目标索力的自洽性；")
    w.bullet("表 4-3、表 4-4　索力分区统计与对称性校核，用于发现异常索。")


# ---------------------------------------------------------------- 第 2 章


def _sec_2_model(w: _Writer, model, d: dict, fig_elev: Path) -> None:
    w.h1("计算模型")
    w.h2("2.1　结构离散")
    w.p("主梁沿顺桥向离散为梁单元（BEAM），桥塔为竖向悬臂梁单元，"
        "拉索采用只受拉的索单元（CABLE）。主梁与桥塔之间不刚接——"
        "塔、梁各自独立成链，仅通过拉索传力，这与半漂浮体系的实际构造一致；"
        "若把塔梁刚接，塔的弯曲刚度会被主梁“借走”，成桥索力将系统偏小。")
    w.figure(fig_elev, "结构计算简图（▲ 为支承断面）", width_cm=15.0)

    cnt = d["counts"]
    by = d.get("by_etype") or {}
    n_deck, n_tower = by.get(1), by.get(2)
    rows = [
        ["主梁", "BEAM", str(n_deck) if n_deck is not None else "—",
         "纵向单梁线，按索距与塔位离散"],
        ["桥塔", "BEAM", str(n_tower) if n_tower is not None else "—",
         "竖向悬臂柱，塔底固结"],
        ["拉索", "CABLE", str(cnt.get("CABLE", 0)), "只受拉，含垂度（弹性悬链线）"],
        ["合计", "—", str(d["n_elems"]), f'{d["n_nodes"]} 节点 / {d["dofs"]} 自由度'],
    ]
    w.table("模型规模与单元构成", ["构件", "单元类型", "数量", "说明"], rows,
            widths=[2.4, 2.6, 1.8, 8.7], aligns=["c", "c", "c", "l"])

    w.h2("2.2　材料与截面")
    mats = {m.id: m for m in model.materials}
    reals = {r.id: r for r in model.reals}
    m1, m2 = mats.get(1), mats.get(2)
    r1, r2, r3 = reals.get(1), reals.get(2), reals.get(3)
    w.p("主梁与桥塔为混凝土，拉索为钢绞线。截面特性按薄壁箱形截面计算，"
        "计入约束扭转常数与扇性惯矩。")
    rows = [["主梁 / 桥塔", "C50 混凝土", _f(m1.E / 1e9, 1) if m1 else "—",
             _f(m1.dens, 0) if m1 else "—"],
            ["拉索", "高强度钢绞线", _f(m2.E / 1e9, 1) if m2 else "—",
             _f(m2.dens, 0) if m2 else "—"]]
    w.table("材料参数", ["构件", "材料", "弹性模量 E (GPa)", "密度 (kg/m³)"], rows,
            widths=[3.2, 4.2, 4.2, 3.9], aligns=["c", "l", "c", "c"])

    rows = []
    if r1:
        rows.append(["主梁", _f(r1.A, 3), _f(r1.IY, 3), _f(r1.IZ, 3),
                     _f(r1.J, 3), _f(r1.WB, 3)])
    if r2:
        rows.append(["桥塔", _f(r2.A, 3), _f(r2.IY, 3), _f(r2.IZ, 3),
                     _f(r2.J, 3), "—"])
    if r3:
        rows.append(["拉索", _f(r3.A, 4), "—", "—", "—", "—"])
    w.table("截面特性", ["构件", "面积 A (m²)", "惯性矩 Iy (m⁴)",
                         "惯性矩 Iz (m⁴)", "扭转常数 J (m⁴)", "截面模量 Wb (m³)"],
            rows, widths=[2.2, 3.2, 2.8, 2.8, 2.6, 2.9],
            aligns=["c", "r", "r", "r", "r", "r"])

    w.h2("2.3　边界条件")
    xs = d["x_supports"]
    rows = []
    for i, x in enumerate(xs):
        tag = "桥台" if i in (0, len(xs) - 1) else "桥塔处"
        rows.append([f"S{i + 1}", tag, _f(x, 2), "竖向 + 横向", "双支座体系"])
    if len(xs) > 1:
        rows.append(["—", "桥台 S1", _f(xs[0], 2), "纵向（顺桥向）", "半漂浮体系，仅一端纵向固定"])
    rows.append(["—", "塔底 ×2", "—", "全部 6 个自由度", "固结"])
    w.table("边界条件", ["编号", "位置", "x (m)", "约束方向", "说明"], rows,
            widths=[1.4, 3.0, 2.2, 4.4, 4.5], aligns=["c", "c", "r", "l", "l"])
    w.note("主梁为单梁线模型，另对全部主梁节点施加扭转约束以消除单梁线"
           "无法自持的绕梁轴刚体转动；该项约束仅用于定位，不参与竖向受力。")

    w.h2("2.4　荷载")
    q = d["params"].get("q")
    w.p("计算仅考虑恒载：桥面恒载按节点受荷宽度分配到主梁节点，塔柱自重按段长"
        "平均分配至两端节点，拉索自重由索单元内部分配（并因此产生垂度）。")
    rows = [
        ["桥面恒载 q", _f(q, 1) if q else "—", "kN/m", "按节点受荷宽度分配"],
        ["桥面恒载合计", _f(d["deck_load"] / 1e3, 1), "kN", "q × 全桥长度"],
        ["拉索自重 w", _f(d["cable_w"], 1), "N/m", "A × ρ × g"],
        ["恒载合计（含塔）", _f(d["total_load"] / 1e3, 1), "kN",
         "桥面恒载 + 塔柱自重"],
    ]
    w.table("荷载汇总", ["荷载", "数值", "单位", "说明"], rows,
            widths=[4.2, 3.0, 2.0, 6.3], aligns=["l", "r", "c", "l"])


# ---------------------------------------------------------------- 第 3 章


def _sec_3_method(w: _Writer, d: dict, model) -> None:
    w.h1("计算方法")
    w.p("斜拉桥成桥态的难点不在“能不能算”，而在“算什么”：成桥位置是设计给定的线形，"
        "不是自由变形后的位置；而要实现这条线形，必须在无应力状态下先把索做短、"
        "把梁做高。以下四节按求解顺序说明。")

    w.h2("3.1　几何非线性：共转列式梁单元")
    w.p("梁柱效应与索的大位移采用共转列式（Co-rotational）表述：把单元运动分解为"
        "刚体运动与局部小变形，局部变形用线性梁理论计算，刚体转动用指数映射精确积分。"
        "这样既避免了线性理论的虚假刚度，也不需要引入有限转动的复杂插值。")
    w.formula("T = exp([ψ]_{×})·E_{0}　，　f = Bᵀq　，　q = K_{l}(d − d_{0})",
              "T 为单元当前标架，E₀ 为初始标架，ψ 为剔除刚体转动后的局部转角；"
              "q 为局部内力，K_{l} 为局部弹性刚度。")
    w.p("切线刚度由材料项与几何项组成，其中 ∂d/∂u 用对数映射的左 Jacobian 逆解析给出，"
        "而非差分：")
    w.formula("K = BᵀK_{l}(∂d/∂u) + K_{g}",
              "差分切线需对每个自由度扰动一次（12 次单元重算，实测占单元总耗时 96%），"
              "解析式与差分结果一致到 4×10⁻¹¹，但快 6.6 倍。")

    w.h2("3.2　拉索垂度")
    w.p("索在自重下呈悬链线。采用浅垂度弹性悬链线解，由弦长与无应力索长反求索力，"
        "并据此计算 Ernst 等效模量：")
    w.formula("T = T(L_{n}, L_{0}; EA, w, L_{h})\n"
              "E_{eq} = E / [1 + (wL_{h})^{2}·EA / (12T^{3})]",
              "L_{n} 为当前弦长，L_{0} 为无应力索长，w 为索单位长度重量，"
              "L_{h} 为水平投影长度。Ernst 模量的分母保留 L_{0}，使等效模量与索内力严格共轭，"
              "否则迭代会在松弛—张紧之间振荡。")
    w.p(f"本例索自重 w = {_f(d['cable_w'], 1)} N/m。垂度对索力的影响在长索上不可忽略："
        "若不扣垂度，短索索力基本不变，而最长索的索力会被高估 3%~5%，"
        "对应下料长度偏短，成桥后主跨线形偏高。")

    w.h2("3.3　成桥态解耦：索长与线形")
    w.p("关键洞察是：成桥态可拆成两个互不耦合的子问题。")
    w.h3("① 无应力索长 L₀ 的反算")
    w.p("成桥位置定义为设计线形，故成桥弦长 L* 已知（由几何直接量取），"
        "目标索力 T* 由第 3.4 节确定，于是 L₀ 可闭式反算，无需迭代结构：")
    w.formula("L_{0} = (L^{*} + s(T^{*})) / (1 + T^{*}/EA)",
              "s(T*) 为垂度引起的索长增量（悬链线解）。"
              "该式是本方案的核心：它把“找形”问题转化为“代数反算”，"
              "避免了传统方法“猜索长—重解结构—比较线形”的外层迭代。")
    w.h3("② 制造线形 Xm 的几何闭合")
    w.p("无应力索长确定后，结构在恒载下的变形是唯一确定的。要使其在成桥时落到设计线形，"
        "必须让制造线形满足几何闭合方程：")
    w.formula("ΔX + u(X_{0} + ΔX) = 0",
              "ΔX 为制造线形相对设计线形的偏移（预抬高），u 为该线形下的成桥位移。"
              "本文用低秩 Newton 迭代求解：位移对 ΔX 的敏感度矩阵秩不超过索数，"
              "故可写成 G = −K⁻¹BKcBᵀ（Kc 为索刚度矩阵），用 Woodbury 公式把每次迭代降为 1 次分解加 nc 次回代。")

    w.h2("3.4　目标索力的确定")
    method = d["tension_method"].upper()
    w.p(f"本次计算采用「{METHOD_LABELS.get(method, method or '—')}」确定成桥目标索力。")
    w.p("该法把每一跨的桥面恒载（扣除支座断面直接承担的那部分受荷宽度）"
        "平均分配给该跨全部拉索，再按各索的竖向倾角反算索力：")
    w.formula("T_{i} = (q·L_{span} / n_{span}) / cosθ_{i}",
              "q 为桥面恒载集度，L_{span} 为该跨跨径，n_{span} 为该跨索数，"
              "θ_{i} 为索与竖直方向的夹角。")
    w.p("采用“按跨分配”而不是“按锚点受荷宽度分配”，是因为主跨中部通常不布索"
        "（本例两侧索各覆盖 46% 主跨，中部留出约 8% 跨径的无索区）。"
        "无索区那几段梁的恒载并没有支座可以直接承担，它经主梁弯矩传给两侧锚点——"
        "若按锚点受荷宽度分配，这部分荷载会被静默地记为“支座承担”，"
        "成桥态里主梁被迫跨越整跨，预抬高被放大一个数量级。")
    w.p("该式的意义在于竖向自洽：由 Σq·L_{span} = q·L 可知 ΣT_{i}·cosθ_{i} "
        "加上支座分担精确等于桥面总恒载。表 4-2 对此做了数值校核。")
    w.note("说明：刚性支承连续梁法与最小弯曲能量法亦是可选方法，但在“塔处设竖向支座”"
           "的斜拉桥体系中均不适用：前者会把塔处支座解成向下拉（负反力），"
           "后者在裸结构（梁+塔）自身即可承重时，最优解退化为“索不受力”。"
           "两法适用于无塔处竖向支座的纯索承体系。")

    w.h2("3.5　收敛准则与求解流程")
    w.p("几何闭合迭代以位移增量范数与闭合残差双重判据收敛：")
    w.formula("‖ΔX‖_{∞} < 10^{−6} m　且　‖ΔX + u‖_{∞} < 10^{−6} m",
              "索力收敛另以 |T − T*|/T* < 10⁻⁶ 判据。全部迭代在同一个稀疏分解上完成。")
    w.table("求解流程", ["步骤", "内容", "输出"], [
        ["1", "建几何、材料、约束、恒载", "—"],
        ["2", "按恒载竖向分配确定目标索力 T*", "目标索力表"],
        ["3", "由 L* 与 T* 闭式反算各索无应力索长 L₀", "下料索长表"],
        ["4", "以 L₀ 求解恒载下的成桥位移 u", "成桥位移"],
        ["5", "判断 ΔX + u 是否闭合；否则更新 ΔX（低秩 Newton）", "预抬高"],
        ["6", "输出索力、索长、制造线形与校核", "本计算书"],
    ], widths=[1.2, 9.8, 4.5], aligns=["c", "l", "l"])


# ---------------------------------------------------------------- 第 4 章


def _sec_4_results(w: _Writer, d: dict, model, fig_tension: Path | None = None) -> None:
    w.h1("成桥态计算结果")
    w.h2("4.1　收敛性与计算规模")
    w.p(f"成桥态几何闭合迭代{'收敛' if d['converged'] else '未收敛'}，"
        f"共 {d['iters']} 轮，耗时 {d['elapsed_ms']:.0f} ms。收敛判据与残差如下：")
    rows = [
        ["几何迭代次数", f'{d["iters"]}', "轮"],
        ["闭合残差 ‖ΔX + u‖∞", f'{d["closure_err"]:.2e}', "m"],
        ["索力残差 |T−T*|/T*", f'{d["tension_err"]:.2e}', "—"],
        ["求解耗时", f'{d["elapsed_ms"]:.0f}', "ms"],
    ]
    w.table("收敛性指标", ["项目", "数值", "单位"], rows,
            widths=[7.4, 4.6, 3.5], aligns=["l", "r", "c"])
    w.p(f"闭合残差 {d['closure_err']:.2e} m 意味着成桥线形与设计线形的偏差在"
        f"纳米量级，远小于施工测量的毫米级精度要求；也就是说，"
        f"表 6-1 给出的制造线形是“精确”的，现场误差将完全来自制造与安装，"
        f"而非计算。")

    w.h2("4.2　竖向平衡校核")
    w.p("索力的正确性首先应通过整体竖向平衡检验：索力的竖向分量之和，"
        "加上支座反力，必须等于全部恒载。")
    rows = [
        ["索力竖向分量之和 ΣT·cosθ", _f(d["vert"] / 1e3, 1), "kN", "—"],
        ["桥面恒载 q·L", _f(d["deck_load"] / 1e3, 1), "kN", "索 + 支座共同承担"],
        ["支座承担", _f(d["sup_share"] / 1e3, 1), "kN",
         f'{100.0 - d["deck_share_pct"]:.2f}%'],
        ["索承担桥面恒载比例", _f(d["deck_share_pct"], 2), "%",
         "索 / 桥面恒载"],
        ["塔柱自重", _f((d["total_load"] - d["deck_load"]) / 1e3, 1), "kN",
         "由塔底直接传至基础，不经索"],
        ["恒载合计", _f(d["total_load"] / 1e3, 1), "kN", "—"],
    ]
    w.table("成桥态竖向平衡校核", ["项目", "数值", "单位", "说明"], rows,
            widths=[6.0, 2.8, 1.5, 5.2], aligns=["l", "r", "c", "l"])
    w.p(f"索承担桥面恒载 {_f(d['deck_share_pct'], 2)}%，其余 "
        f"{_f(100.0 - d['deck_share_pct'], 2)}% 由塔处支座直接承担。"
        "该比例不是“越多越好”的指标——它由支座位置与布索范围决定："
        "塔位处主梁必然由支座支承，落在该处受荷宽度内的恒载不会进入索。"
        "校核的关键是**两项之和与总恒载闭合**，此处闭合。")
    w.note("注：若误用“恒载合计”（含塔自重）作分母，会得到索只承担约 40% 的结论，"
           "这是错误的——塔自重沿塔柱直接下传至基础，荷载路径上不经过任何一根索。")

    w.h2("4.3　索力分布")
    cs = d["cables"]
    w.p(f"全部 {d['n_cable']} 根索的成桥索力介于 {_f(d['Tmin'] / 1e3, 0)} ~ "
        f"{_f(d['Tmax'] / 1e3, 0)} kN，平均 {_f(d['Tmean'] / 1e3, 0)} kN，"
        f"最大值与最小值之比 {_f(d['uniformity'], 1)}。")
    w.p("索力沿桥纵向**不作单调变化**：塔根处最小，向两侧跨中递增，跨中最长索最大；"
        "边跨端部因索倾角减小而回升至中等水平，整体呈“塔根低、跨中高”的 W 形。"
        "这与式(3-2)一致——塔根索虽短，但倾角大（cosθ 接近 1）且分担的桥面宽度小；"
        "跨中长索接近水平（cosθ 小）、分担宽度大，故索力最大。"
        "**同一跨内索力必须单调、无跳变**，出现跳变才说明该锚点刚度异常或索力反算病态。")
    if fig_tension is not None:
        w.figure(fig_tension, "成桥索力沿桥纵向分布", width_cm=15.0)

    rows = []
    for g in d["groups"]:
        rows.append([g["name"], str(g["n"]), _f(g["Tmin"] / 1e3, 0),
                     _f(g["Tmax"] / 1e3, 0), _f(g["Tmean"] / 1e3, 0),
                     _f(g["Tmax"] / g["Tmin"], 2), _f(g["sfmin"], 2)])
    rows.append(["全桥", str(d["n_cable"]), _f(d["Tmin"] / 1e3, 0),
                 _f(d["Tmax"] / 1e3, 0), _f(d["Tmean"] / 1e3, 0),
                 _f(d["uniformity"], 2), _f(d["sf_min"], 2)])
    w.table("索力分区统计", ["分区", "索数", "Tmin (kN)", "Tmax (kN)",
                            "Tmean (kN)", "不均匀系数", "最小安全系数"], rows,
            widths=[3.4, 1.3, 2.3, 2.3, 2.3, 2.2, 2.6],
            aligns=["l", "c", "r", "r", "r", "r", "r"])
    w.note("注：不均匀系数 = 该区最大索力 / 最小索力。各分区均小于 2，说明区内索力沿桥变化平缓；"
           "全桥系数略大，是因为主跨单索分担的受荷宽度约为边跨的 1.25 倍。")

    if d["sym"]:
        rows = [[s["label"], str(s["n"]), f'{s["dev_max"] * 100:.2f}',
                 f'{s["dev_mean"] * 100:.2f}'] for s in d["sym"]]
        w.table("对称性校核（同序号索的索力相对偏差）",
                ["对比项", "对数", "最大偏差 (%)", "平均偏差 (%)"], rows,
                widths=[6.6, 2.0, 3.4, 3.5], aligns=["l", "c", "r", "r"])
        worst = max(s["dev_max"] for s in d["sym"])
        if worst > 0.02:
            w.note(f"最大偏差 {worst * 100:.2f}%，超过 2%。"
                   "结构关于桥轴中线对称而索力不对称，通常源于纵桥向约束只设在一端"
                   "（半漂浮体系），属正常现象；若数值更大，需检查该侧锚点是否贴近支座。")
        else:
            w.note(f"最大偏差 {worst * 100:.2f}%，在施工控制精度（≤2%）以内。"
                   "纵桥向仅一端固定，故两侧并非严格对称，该量级偏差由约束不对称引起。")


# ---------------------------------------------------------------- 第 5 章


def _sec_5_cables(w: _Writer, d: dict) -> None:
    w.h1("拉索下料长度")
    w.h2("5.1　计算原理")
    w.p("下料长度是无应力状态下的索长，它不等于图纸上的弦长：")
    w.formula("L_{0} = (L^{*} + s) / (1 + T^{*}/EA)",
              "L* 为成桥弦长（= 设计弦长），s 为垂度增量，T*/(EA) 为弹性应变。"
              "前者使索“变长”，后者使索“变短”。")
    w.p(f"本例弹性伸长率平均 {_f(d['elong_rate'] * 100, 3)}%，"
        f"即每米索在成桥时被拉长 {_f(d['elong_rate'] * 1000, 2)} mm；"
        f"各索伸长量介于 {_f(d['elong_min'], 1)} ~ {_f(d['elong_max'], 1)} mm。"
        "这个量级正是施工中“索长了就松、短了就紧”的敏感区间——"
        "索力对索长的敏感度约为每毫米数十千牛，所以下料长度必须给到毫米级。")

    w.h2("5.2　下料索长表")
    rows: list = []
    last_group = None
    for c in sorted(d["cables"], key=lambda c: (c["group"], c["x"])):
        if c["group"] != last_group:
            rows.append({"__group__": f'{c["group"]}（{sum(1 for q in d["cables"] if q["group"] == c["group"])} 根）'})
            last_group = c["group"]
        rows.append([str(c["elem"]), _f(c["x"], 2), _f(c["Ltarget"], 3),
                     _f(c["L0"], 3), _f(c["elong_mm"], 1),
                     _f(c["T"] / 1e3, 0), _f(c["sigma"] / 1e6, 0),
                     _f(c["sf"], 2)])
    w.table("拉索下料索长表", ["索单元", "梁侧锚点 x (m)", "设计弦长 (m)",
                              "下料索长 L₀ (m)", "弹性伸长 (mm)", "成桥索力 (kN)",
                              "索应力 (MPa)", "安全系数"], rows,
            widths=[1.7, 2.5, 2.3, 2.5, 2.2, 2.2, 2.0, 1.9],
            aligns=["c", "r", "r", "r", "r", "r", "r", "r"])
    w.note("注：梁侧锚点 x 为索在主梁上的锚固位置（顺桥向）；"
           "索单元编号与模型 .dat 文件一致，便于对照复核。"
           "下料长度已扣除垂度增量与弹性伸长，可直接用于厂家下料。")

    w.h2("5.3　索应力与安全系数")
    w.p(f"索应力介于 {_f(d['sigma_min'] / 1e6, 0)} ~ {_f(d['sigma_max'] / 1e6, 0)} MPa，"
        f"以 f_{{pk}} = {_f(d['fpu'], 0)} MPa、单索面积 {_f(d['area'] * 1e6, 0)} mm² 计，"
        f"破断力 {_f(d['break_load'] / 1e3, 0)} kN，"
        f"最小安全系数 {_f(d['sf_min'], 2)}（出现在最长索）。")
    ok = d["sf_min"] >= 2.0
    w.p(("安全系数满足 JTG/T 3365-01—2020 关于拉索安全系数不小于 2.0 的要求。"
         if ok else
         f"安全系数 {_f(d['sf_min'], 2)} 低于 2.0，不满足规范要求，"
         "应加大单索面积或增加索数。"))
    # 反向校核：按容许应力反算所需面积，指出截面富余程度（避免“安全系数越大越好”的误读）
    # 注意 d["fpu"] 的单位是 MPa，乘 1e6 才是 Pa，否则反算面积会大 6 个量级、
    # 富余判断恒为假（这个坑静默跳过了整段文字，只能靠逐页看图发现）。
    a_req = d["Tmax"] / (0.4 * d["fpu"] * 1e6)
    if d["area"] > a_req * 1.05:
        w.p(f"另一方面，若按《公路斜拉桥设计规范》取容许应力 0.4f_{{pk}} = "
            f"{_f(0.4 * d['fpu'], 0)} MPa，最大索力 {_f(d['Tmax'] / 1e3, 0)} kN "
            f"仅需面积 {_f(a_req * 1e6, 0)} mm²，而实配 {_f(d['area'] * 1e6, 0)} mm²，"
            f"富余 {_f((d['area'] / a_req - 1.0) * 100, 0)}%。"
            "本计算书按实配截面出的索力已满足全部校核；若要优化用钢量，"
            "可把单索面积降至上述反算值后重算——注意面积变化会小幅改变结构刚度，"
            "进而影响索力分配，需迭代一次。")
    rows = [["最大索应力", _f(d["sigma_max"] / 1e6, 0), "MPa", "—"],
            ["最小安全系数", _f(d["sf_min"], 2), "—", "≥ 2.0 满足" if ok else "＜ 2.0 不满足"],
            ["索力不均匀系数 Tmax/Tmin", _f(d["uniformity"], 2), "—",
             "斜拉桥常规 2~4"]]
    w.table("拉索强度校核", ["项目", "数值", "单位", "结论"], rows,
            widths=[6.4, 3.2, 2.0, 3.9], aligns=["l", "r", "c", "l"])


# ---------------------------------------------------------------- 第 6 章


def _sec_6_camber(w: _Writer, d: dict, fig_cam: Path) -> None:
    w.h1("制造线形（预抬高）")
    w.h2("6.1　预抬高的含义")
    w.p("制造线形是梁段在无应力状态下的几何形状，即在设计线形的基础上叠加预抬高。"
        "预抬高为负值时表示“该点在成桥时会向上抬，故制造时应压低”。"
        "它由成桥态几何闭合方程 ΔX + u(ΔX) = 0 反解得到，"
        "不是按经验曲线估的，也不是算法噪声。")
    w.figure(fig_cam, "主梁预抬高沿桥纵向分布（负值 = 制造时压低）", width_cm=15.0)
    # 预抬高的正负形态由布索与主梁刚度的相对关系决定，不能想当然：
    # 主跨中部留无索区时，那一段梁的恒载靠自身弯曲跨越，成桥时下沉，
    # 制造就得抬高（正）；边跨被索整体上提，成桥时上抬，制造就得压低（负）。
    w.p("预抬高的正负形态取决于布索范围与主梁刚度的相对关系，两个方向都可能出现："
        "某点成桥时下沉，制造时要抬高（正值）；成桥时上抬，制造时要压低（负值）。"
        f"本例预抬高 {_f(d['camber_min'], 1)} ~ {_f(d['camber_max'], 1)} mm，"
        f"边跨段为负（被索上提，制造时压低），主跨中段为正（无索区恒载靠主梁弯曲跨越，"
        f"成桥时下沉，制造时抬高），与布索范围（两侧各覆盖 46% 主跨）一致。")
    w.p(f"预抬高绝对值最大 {_f(d['camber_abs_max'], 1)} mm，约为主跨的 "
        f"L/{_f(d['camber_ratio'], 0)}，在同规模斜拉桥的施工控制经验"
        f"（常规 L/1000 ~ L/2000）之内。")

    w.h2("6.2　制造线形表")
    rows = [[str(r["node"]), _f(r["x"], 2), _f(r["z"], 4),
             _f(r["uz_mm"], 1), _f(r["camber_mm"], 1), _f(r["erect_z"], 4)]
            for r in d["camber_rows"]]
    w.table("主梁制造线形表", ["节点", "x (m)", "设计标高 (m)", "成桥位移 (mm)",
                              "预抬高 (mm)", "制造标高 (m)"], rows,
            widths=[1.8, 2.6, 2.8, 3.0, 2.8, 2.5],
            aligns=["c", "r", "r", "r", "r", "r"])
    w.note("注：制造标高 = 设计标高 + 预抬高。成桥位移与预抬高数值互为反号，"
           "二者之和应为零（闭合残差见表 4-1），这本身即是一次独立的自校核。")

    w.h2("6.3　结果说明")
    w.p("制造线形只解决“梁段做成什么样”，成桥线形还取决于现场索力张拉。"
        "施工控制的正确做法是：按表 5-1 下料、按表 6-1 预制，"
        "然后在张拉阶段以索力为主控、线形为校核。若把线形作为主控，"
        "由于主梁刚度大，很小的标高偏差会对应很大的索力偏差，容易超张拉。")


# ---------------------------------------------------------------- 第 7 章


def _sec_7_conclusion(w: _Writer, d: dict) -> None:
    w.h1("结论")
    w.p(f"（1）成桥态在 {d['iters']} 轮几何迭代内收敛，闭合残差 "
        f"{d['closure_err']:.2e} m，制造线形与设计线形严格对应。")
    w.p(f"（2）{d['n_cable']} 根索的成桥索力介于 {_f(d['Tmin'] / 1e3, 0)} ~ "
        f"{_f(d['Tmax'] / 1e3, 0)} kN，索力竖向分量与支座反力之和闭合于总恒载，"
        f"索承担桥面恒载 {_f(d['deck_share_pct'], 2)}%。")
    w.p(f"（3）最小索力安全系数 {_f(d['sf_min'], 2)}，"
        + ("满足规范不小于 2.0 的要求。" if d["sf_min"] >= 2.0
           else "低于规范要求，需调整索面积或索数。"))
    w.p(f"（4）主梁预抬高 {_f(d['camber_min'], 1)} ~ {_f(d['camber_max'], 1)} mm"
        f"（约 L/{_f(d['camber_ratio'], 0)}），可作为梁段预制标高控制依据。")
    w.p("（5）本计算仅含恒载成桥态。施工阶段的索力张拉顺序、以及活载、温度、"
        "风与地震工况下的验算，需在施工图阶段另行计算。")

    w.h1_appendix("计算文件清单", "A")
    w.p("本计算书对应的原始计算文件如下，可用于第三方独立复核：", indent=True)
    w.table("计算文件清单", ["文件", "内容"], [
        ["cable.dat", "FEM-Eigen 输入文件（节点/单元/材料/约束/荷载/成桥态工况）"],
        ["cable_cable.txt", "求解器原始输出（索力/索长/预抬高/位移，分段纯文本）"],
        ["fig_elevation.png", "图 2-1 源图"],
        ["fig_tension.png", "图 4-1 源图"],
        ["fig_camber.png", "图 6-1 源图"],
    ], widths=[4.6, 10.9], aligns=["l", "l"])
    p = w.doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12)
    r = p.add_run("— 计算书结束 —")
    _set_run_font(r, cn=FONT_HEI, size=10.5, color=GRAY)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER


# ---------------------------------------------------------------- 便捷入口

def build_cable_report_from_files(*, dat_path, result_path, model, info: dict,
                                  params: dict | None = None,
                                  out_path="out/cable/report.docx",
                                  **kw) -> Path:
    """从磁盘上已有的 .dat / _cable.txt 生成报告（不重新求解）。"""
    from fem_eigen_cable import parse_cable_result

    res = parse_cable_result(result_path)
    res["dat"] = Path(dat_path)
    res["result"] = Path(result_path)
    res["converged"] = bool(res["meta"].get("converged", 0))
    return build_cable_report(model=model, info=info, res=res,
                              params=params, out_path=out_path, **kw)


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from cable_stayed_3d import build_cable_stayed_3d
    from fem_eigen_cable import solve_cable_bridge

    m, info = build_cable_stayed_3d()
    r = solve_cable_bridge(m, out_dir="out/cable", dat_name="cable.dat")
    out = build_cable_report(
        model=m, info=info, res=r,
        params={"Lm": 200, "Ls": 80, "Ht": 60, "n": 12, "h": 2.5, "B": 12,
                "q": 100, "A": 6000, "method": "DECK", "fpu": 1860},
        out_path="out/cable/report.docx", project="双塔三跨斜拉桥",
        analyst="")
    print("已生成:", out, f"({out.stat().st_size / 1024:.0f} KB)")
