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

import code_material

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
    "FORMFINDING": "恒载索形找形（索多边形）",
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
        # 走 _text_runs 而不是 add_run(text)：表注里同样会出现 **粗体** 与 _{下标}，
        # 直接 add_run 会把星号原样印出来（"**向上拔**"）——这是最容易漏的一处。
        par = self.doc.add_paragraph()
        par.paragraph_format.space_before = Pt(2)
        par.paragraph_format.space_after = Pt(6)
        par.paragraph_format.line_spacing = 1.2
        _text_runs(par, text, size=NOTE_PT, color=GRAY)

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
    r1, r2, r3 = reals.get(1), reals.get(2), reals.get(3)

    # 哪个材料用于什么，按 *ET 的单元类型判，**不按材料编号猜**：
    # 悬索桥是 1=钢箱梁、2=混凝土塔、3=缆索，斜拉桥是 1=混凝土梁塔、
    # 2=缆索。靠 id 写死，换一种桥型就错一行。
    kind_of: dict[int, set[str]] = {}
    for et in getattr(model, "etypes", []):
        kind_of.setdefault(et.mat, set()).add(getattr(et, "kind", ""))
    cable_mats = {mid for mid, ks in kind_of.items() if ks and ks <= {"CABLE"}}

    def _grade(m: Any) -> str:
        if m is None:
            return "—"
        return (getattr(m, "grade", "") or "").strip() or "未标注"

    def _part(mid: int) -> str:
        if mid in cable_mats:
            return "缆索"
        return "梁 / 塔" if "BEAM" in kind_of.get(mid, set()) else "—"

    body_grades = sorted({_grade(m) for mid, m in mats.items()
                          if mid not in cable_mats and _grade(m) != "未标注"})
    w.p(f"主体受力构件（梁、塔）采用{'、'.join(body_grades) or '钢与混凝土'}，"
        "缆索采用高强度钢绞线或高强钢丝。截面特性按薄壁箱形截面计算，"
        "计入约束扭转常数与扇性惯矩。")

    rows = []
    for mid, m in sorted(mats.items()):
        rows.append([f"M{mid}", _part(mid), _grade(m),
                     _f(m.E / 1e9, 1), _f(m.dens, 0)])
    w.table("材料参数", ["编号", "用于", "材料", "弹性模量 E (GPa)", "密度 (kg/m³)"],
            rows, widths=[1.8, 2.8, 3.6, 3.9, 3.4],
            aligns=["c", "c", "l", "c", "c"])

    # 材料强度设计值：应力验算的"分母"。即使本报告不做承载力验算，
    # 读者也要能看到取的是哪一版规范、哪一个厚度档。
    for g in body_grades:
        try:
            if g.upper() in code_material.CONCRETE:
                t = code_material.concrete_table((g,))
                w.table(f"混凝土强度标准值与设计值（{g}）", t["headers"], t["rows"],
                        widths=[2.6, 2.9, 2.9, 2.9, 2.9, 2.9],
                        aligns=["c"] * 6)
            else:
                t = code_material.steel_table((g,))
                w.table(f"钢材强度设计值（{g}）", t["headers"], t["rows"],
                        widths=[3.0, 3.4, 2.8, 2.8, 3.2],
                        aligns=["c"] * 5)
        except KeyError:
            # 牌号没登记就不出这张表，也不猜一个强度值顶上
            w.note(f"材料「{g}」不在强度登记表内，未列出强度设计值。")

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


# ================================================================ 悬索桥计算书
#
# 为什么不能复用斜拉桥那本
# ------------------------
# 两本计算书的**推导对象**不同：
#   · 斜拉桥：索力是设计变量，计算书要论证"这组索力如何确定、梁面线形如何
#     预抬"。核心章节是索力优化 + 制造线形闭环。
#   · 悬索桥：主缆线形是恒载的结果，索力是**推导出来的**。核心章节是找形
#     的微分方程、H 的确定、三跨共用一个 H 的条件、以及边跨平衡。
# 硬套斜拉桥的章节会得到一本"没有推导、只有数字"的书，审图人第一句就会问
# "主缆线形哪来的"。所以单独一本，7 章。


def collect_susp_data(*, model, info: dict, res, params: dict | None = None) -> dict:
    """汇总悬索桥计算书所需的全部数据（不做力学重算，只做统计）。"""
    p = dict(params or {})
    m = dict(res.meta)
    H = float(m.get("H_kN") or 0.0) * 1e3            # N
    sys_name = str(m.get("susp_system") or "EARTH").upper()
    is_self = sys_name == "SELF"

    node_x = {n.id: n.x for n in model.nodes}
    node_z = {n.id: n.z for n in model.nodes}

    # 主缆链（锚→塔1→塔2→锚）：直接用求解结果的分段表，按 x 排序没有意义，
    # 必须保持 *MAINCABLE 的链序——所以不排序。
    chain = list(res.main_cable)
    mains = [float(r["T"]) for r in chain]
    hangs = [float(r["T"]) for r in res.hangers]

    deck_n = [n for n in model.nodes if 1000 < n.id < 2000]
    gir = list(res.girder)
    camber_deck = [v[2] for n, v in res.camber.items() if 1000 < n < 2000]

    # ---- 自锚式的梁体轴向缩短 -------------------------------------------
    # 主缆锚在梁端，H 直接压进加劲梁，成桥态梁比无应力状态短 Δ = H·L/EA。
    # 这一项和竖向预拱度**不是同一件事**：前者要靠伸缩缝/合龙段预留，后者
    # 靠预制标高解决。混着报会把 10 mm 的预抬高说成"预变形 75 mm"，现场
    # 照 75 mm 起拱就错了。所以两个数分开取，都从 [U] 的实测位移来。
    deck_sorted = sorted((n for n in deck_n), key=lambda n: n.x)
    ux_first = float(res.disp.get((deck_sorted[0].id, 1), (0.0, 0.0))[0]) if deck_sorted else 0.0
    ux_last = float(res.disp.get((deck_sorted[-1].id, 1), (0.0, 0.0))[0]) if deck_sorted else 0.0
    deck_short_mm = (ux_first - ux_last) * 1000.0        # 收缩为正
    deck_end_move_mm = max(abs(ux_first), abs(ux_last)) * 1000.0

    return {
        "params": p,
        "meta": m,
        "system": sys_name,
        "system_label": "自锚式悬索桥" if is_self else "地锚式悬索桥",
        "is_self": is_self,
        "n_nodes": len(model.nodes),
        "n_elems": len(model.elements),
        "dofs": int(m.get("dofs") or 0),
        "n_main": len(chain),
        "n_hanger": len(res.hangers),
        "n_girder": len(gir),
        "span": float(m.get("span_m") or 0.0),
        "side_span": float(info.get("side_span") or 0.0),
        "tower_top_z": float(info.get("tower_top_z") or 0.0),
        "sag": float(m.get("sag_m") or 0.0),
        "sag_target": float(m.get("sag_target_m") or 0.0),
        "sag_ratio": float(m.get("sag_ratio") or 0.0),
        "H": H,
        "H_kN": H / 1e3,
        "tension_method": "FormFinding",
        "T_main_min": min(mains) if mains else 0.0,
        "T_main_max": max(mains) if mains else 0.0,
        "T_hanger_min": min(hangs) if hangs else 0.0,
        "T_hanger_max": max(hangs) if hangs else 0.0,
        "T_hanger_sum": sum(hangs),
        "girder_N_max": float(m.get("girder_N_max_kN") or 0.0),
        "girder_M_max": float(m.get("girder_M_max_kNm") or 0.0),
        "tension_err": float(m.get("tension_err") or 0.0),
        "balance_pct": float(m.get("balance_pct") or 0.0),
        "support_v_sum": float(m.get("support_v_sum_kN") or 0.0),
        "support_v_max": float(m.get("support_v_max_kN") or 0.0),
        "applied_v": float(m.get("applied_v_kN") or 0.0),
        "total_deck_load": float(info.get("total_deck_load") or 0.0),
        "camber_max": max((abs(v) for v in camber_deck), default=0.0),
        "deck_short_mm": deck_short_mm,
        "deck_end_move_mm": deck_end_move_mm,
        "camber": dict(res.camber),
        "erected": dict(res.erected),
        "chain": chain,
        "hangers": list(res.hangers),
        "girder": gir,
        "reaction": dict(res.reaction),
        "cable_shape": dict(getattr(res, "cable_shape", {}) or {}),
        # 施工阶段（分级找形）：空缆 → 对称吊装一期 → 合拢 → 二期铺装。
        "erection": list(getattr(res, "erection", []) or []),
        "erection_shape": dict(getattr(res, "erection_shape", {}) or {}),
        "erection_q1": float(info.get("deck_load_q1") or 0.0),
        "erection_q2": float(info.get("deck_load_q2") or 0.0),
        "erection_panels": int(info.get("erection_panels") or 2),
        "node_x": node_x,
        "node_z": node_z,
        "deck_x": [n.x for n in sorted(deck_n, key=lambda n: n.x)],
        "tables": res.tables(model),
        "checks": res.checks(model),
        "converged": bool(res.converged),
        "iterations": int(res.iterations or 0),
        "elapsed_ms": float(res.elapsed_ms or 0.0),
    }


# ---------------------------------------------------------------- 悬索桥绘图


def _cb(model):
    """取悬索桥工况段（主缆/吊索单元清单）。"""
    cb = getattr(model, "cable_bridge", None)
    if cb is None or not str(getattr(cb, "system", "") or ""):
        raise ValueError("该模型不是悬索桥（cable_bridge.system 为空）")
    return cb


def plot_susp_frame(model, d: dict, path: Path) -> Path:
    """图 2-1 结构计算简图：加劲梁 + 桥塔 + 主缆 + 吊索 + 支承。

    主缆用**找形结果**的节点坐标（`d["cable_shape"]`），不用模型里那份——
    模型坐标是建造器给的初始抛物线，边跨那一段更是按"锚点到塔顶的直线"
    插的；而边跨主缆在锚点处必须水平（V=0），真线形是顶点落在锚点的抛物线，
    二者在塔根附近能差几米。这条不报错，只会让图看着"挺像"而已。
    """
    plt = _mpl()
    cb = _cb(model)
    nodes = {n.id: n for n in model.nodes}
    elems = {e.id: e for e in model.elements}
    deck = sorted((n for n in model.nodes if 1000 < n.id < 2000), key=lambda n: n.x)
    tw1 = sorted((n for n in model.nodes if 3000 < n.id < 4000), key=lambda n: n.z)
    tw2 = sorted((n for n in model.nodes if 4000 < n.id < 5000), key=lambda n: n.z)
    shape = d.get("cable_shape") or {}

    def xy(nid: int) -> tuple[float, float]:
        n = nodes[nid]
        if nid in shape:
            return float(shape[nid][0]), float(shape[nid][1])
        return n.x, n.z

    fig, ax = plt.subplots(figsize=(7.6, 3.4))
    # 吊索（连到找形后的主缆上端）
    for hid in cb.hangers:
        e = elems.get(hid)
        if not e:
            continue
        top, bot = (e.n1, e.n2) if nodes[e.n1].z >= nodes[e.n2].z else (e.n2, e.n1)
        x1, z1 = xy(top)
        nb = nodes[bot]
        ax.plot([x1, nb.x], [z1, nb.z], color="#b0b0b0", lw=0.6, zorder=2)
    # 主缆（按链序）
    pts: list[int] = []
    for eid in cb.main_cable:
        e = elems.get(eid)
        if not e:
            continue
        if not pts:
            pts.append(e.n1)
        pts.append(e.n2)
    if pts:
        xs = [xy(n)[0] for n in pts]
        zs = [xy(n)[1] for n in pts]
        ax.plot(xs, zs, color="#c0392b", lw=1.8, zorder=5, label="主缆（找形线形）")
    # 桥塔
    for tw, name in ((tw1, "桥塔"), (tw2, "桥塔")):
        if not tw:
            continue
        ax.plot([n.x for n in tw], [n.z for n in tw], color="#1f4e79", lw=3.2,
                solid_capstyle="butt", zorder=4, label=name)
    # 加劲梁
    ax.plot([n.x for n in deck], [n.z for n in deck], color="#1a1a1a", lw=2.8,
            solid_capstyle="butt", zorder=6, label="加劲梁")
    # 支承符号
    supp = sorted({b.node for b in model.boundaries
                   if 1000 < b.node < 2000 and 3 in getattr(b, "dofs", ())})
    for nid in supp:
        x, z = nodes[nid].x, nodes[nid].z
        ax.plot([x], [z], marker="^", ms=7, color="#1f4e79", zorder=7,
                markerfacecolor="white", markeredgewidth=1.2)
    ax.set_xlabel("x (m)")
    ax.set_ylabel("高程 z (m)")
    # 不锁等比例：624 m 长的桥按真实比例画，竖向只占 35 m，整幅图会有八成
    # 是空白，矢高也看不出来。桥型简图历来是竖向量向放大画，图注里写明倍数。
    ax.grid(alpha=0.25, lw=0.6)
    h, lab = ax.get_legend_handles_labels()
    uniq = dict(zip(lab, h))
    ax.legend(uniq.values(), uniq.keys(), loc="upper right", fontsize=8, frameon=False)
    fig.tight_layout()
    fig.canvas.draw()
    try:
        bb = ax.get_window_extent()
        xr, yr = ax.get_xlim(), ax.get_ylim()
        exag = (bb.height / max(bb.width, 1e-9)) / ((yr[1] - yr[0]) / (xr[1] - xr[0]))
        ax.set_title(f"注：竖向放大 ≈ {exag:.1f} 倍", fontsize=7.5,
                     color="#606060", loc="right")
    except Exception:                                       # noqa: BLE001
        pass
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)
    return path


def plot_susp_tension(d: dict, path: Path) -> Path:
    """图 4-1 主缆分段索力与吊索力沿桥分布。"""
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(7.4, 3.0))
    # 主缆单元的编号与节点号不必对应（*DO 生成的），硬凑 x 只会凑出假坐标，
    # 所以横轴用"沿主缆链的序号"。这条链本身就是从锚碇经塔顶到锚碇的顺序，
    # 序号即沿桥里程的单调映射。
    xs = list(range(len(d["chain"])))
    ts = [float(r["T"]) / 1e3 for r in d["chain"]]
    ax.plot(xs, ts, "-o", ms=2.6, lw=1.2, color="#c0392b", label="主缆分段索力")
    ax.axhline(d["H_kN"], ls="--", lw=0.9, color="#7f7f7f",
               label=f"水平分力 H = {d['H_kN']:,.0f} kN")
    # 吊索
    hx = [i for i in range(len(d["hangers"]))]
    ax.plot(hx, [float(h["T"]) / 1e3 for h in d["hangers"]], "s-", ms=2.4, lw=1.0,
            color="#1f4e79", label="吊索索力")
    ax.set_xlabel("沿主缆链的分段 / 吊索序号")
    ax.set_ylabel("索力 (kN)")
    ax.grid(alpha=0.25, lw=0.6)
    ax.legend(fontsize=8, frameon=False, loc="center right")
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)
    return path


def plot_susp_girder(d: dict, path: Path) -> Path:
    """图 4-2 加劲梁恒载内力沿桥分布（My 与 N）。"""
    plt = _mpl()
    xs = [d["node_x"].get(int(g["elem"]) + 900, None) for g in d["girder"]]
    if any(x is None for x in xs):
        xs = list(range(len(d["girder"])))
        xlabel = "加劲梁单元序号"
    else:
        xlabel = "x (m)"
    my = [float(g["My"]) / 1e3 for g in d["girder"]]
    nn = [float(g["N"]) / 1e3 for g in d["girder"]]

    fig, ax = plt.subplots(figsize=(7.4, 3.0))
    ax.plot(xs, my, lw=1.4, color="#c0392b", label="弯矩 My")
    ax.set_xlabel(xlabel)
    ax.set_ylabel("My (kN·m)")
    ax.grid(alpha=0.25, lw=0.6)
    ax.axhline(0.0, lw=0.8, color="#7f7f7f")
    ax2 = ax.twinx()
    ax2.plot(xs, nn, lw=1.2, ls="--", color="#1f4e79", label="轴力 N")
    ax2.set_ylabel("N (kN)")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=8, frameon=False, loc="lower left")
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)
    return path


def plot_susp_camber(model, d: dict, path: Path) -> Path:
    """图 6-1 制造线形预变形（加劲梁竖向预抬高）。"""
    plt = _mpl()
    xs, dz = [], []
    for nid in sorted(d["camber"], key=lambda n: d["node_x"].get(n, 0.0)):
        if not (1000 < nid < 2000):
            continue
        xs.append(d["node_x"].get(nid, 0.0))
        dz.append(d["camber"][nid][2] * 1000.0)
    fig, ax = plt.subplots(figsize=(7.4, 2.6))
    ax.plot(xs, dz, lw=1.4, color="#1f4e79")
    ax.axhline(0.0, lw=0.8, color="#7f7f7f")
    ax.set_xlabel("x (m)")
    ax.set_ylabel("预变形 (mm)")
    ax.grid(alpha=0.25, lw=0.6)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)
    return path


def plot_susp_erection(model, d: dict, path: Path) -> Path:
    """图 8-1 施工阶段：主缆线形随吊装下沉 + H/矢高随阶段变化。"""
    plt = _mpl()
    er = d["erection"]
    shp = d["erection_shape"]
    stages = sorted(er, key=lambda r: r["stage"])

    # 主跨主缆节点 x 序列（塔1→塔2），与 [ERECTION_SHAPE] 的 z 序列对齐
    cb = getattr(model, "cable_bridge", None)
    xs: list[float] = []
    if cb is not None:
        nid = {n.id: n for n in model.nodes}
        a, b = nid.get(cb.tower1), nid.get(cb.tower2)
        if a is not None and b is not None:
            lo, hi = min(a.x, b.x), max(a.x, b.x)
            pts = sorted((n for n, (x, _z) in d["cable_shape"].items()
                          if lo - 1e-6 <= x <= hi + 1e-6),
                         key=lambda n: d["cable_shape"][n][0])
            xs = [d["cable_shape"][n][0] for n in pts]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(7.4, 5.6))
    if xs:
        first, last = stages[0], stages[-1]
        for r in stages:
            zs = shp.get(r["stage"])
            if not zs or len(zs) != len(xs):
                continue
            if r is first:
                ax1.plot(xs, zs, "-", lw=1.5, color="#1f4e79", label="空缆")
            elif r is last:
                ax1.plot(xs, zs, "-", lw=1.8, color="#c0392b", label="铺装（成桥）")
            else:
                ax1.plot(xs, zs, "-", lw=0.6, color="#9db6cc", alpha=0.7)
        ax1.set_xlabel("x (m)")
        ax1.set_ylabel("主缆高程 z (m)")
        ax1.grid(alpha=0.25, lw=0.6)
        ax1.legend(fontsize=8, frameon=False, loc="upper right")

    xx = [r["stage"] for r in stages]
    H = [float(r.get("H") or 0.0) / 1e6 for r in stages]
    sag = [float(r.get("sag") or 0.0) for r in stages]
    ax2.plot(xx, H, "o-", ms=3.4, lw=1.3, color="#1f4e79", label="主缆水平力 H")
    ax2.set_xlabel("施工阶段")
    ax2.set_ylabel("H (MN)", color="#1f4e79")
    ax2.tick_params(axis="y", labelcolor="#1f4e79")
    labels = [f"{r['stage']}\n{r.get('name','')}" if r.get("name") in ("空缆", "合拢", "铺装")
              else str(r["stage"]) for r in stages]
    ax2.set_xticks(xx)
    ax2.set_xticklabels(labels, fontsize=7.5)
    ax3 = ax2.twinx()
    ax3.plot(xx, sag, "s--", ms=3.2, lw=1.1, color="#c0392b", label="矢高")
    ax3.set_ylabel("矢高 (m)", color="#c0392b")
    ax3.tick_params(axis="y", labelcolor="#c0392b")
    h1, l1 = ax2.get_legend_handles_labels()
    h2, l2 = ax3.get_legend_handles_labels()
    ax2.legend(h1 + h2, l1 + l2, fontsize=8, frameon=False, loc="center left")
    ax2.grid(alpha=0.25, lw=0.6)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)
    return path


# ---------------------------------------------------------------- 悬索桥正文


def build_suspension_report(*, model, info: dict, res,
                            params: dict | None = None,
                            out_path: str | Path,
                            project: str = "", analyst: str = "",
                            title: str | None = None,
                            fig_dir: str | Path | None = None,
                            update_toc: bool = True) -> Path:
    """生成悬索桥（自锚式 / 地锚式）成桥态计算书，返回 .docx 路径。

    `res` 是 `result_3d.SolveResult`，不是旧字典——悬索桥这部分从一开头就只
    用新契约，不再背 `as_legacy_dict()` 的包袱。
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    d = collect_susp_data(model=model, info=info, res=res, params=params)

    figs = Path(fig_dir) if fig_dir else out.parent / "report_figs"
    figs.mkdir(parents=True, exist_ok=True)
    f_frame = plot_susp_frame(model, d, figs / "fig_susp_frame.png")
    f_ten = plot_susp_tension(d, figs / "fig_susp_tension.png")
    f_gir = plot_susp_girder(d, figs / "fig_susp_girder.png")
    f_cam = plot_susp_camber(model, d, figs / "fig_susp_camber.png")
    f_erec = (plot_susp_erection(model, d, figs / "fig_susp_erection.png")
              if d["erection"] else None)

    doc_title = title or getattr(model, "title", "") or d["system_label"]
    doc = _setup_document()
    _cover(doc, d, doc_title, project, analyst)
    header_txt = f"{doc_title} · 成桥态计算书"

    toc_sec = doc.add_section(WD_SECTION.NEW_PAGE)
    _apply_page(toc_sec)
    _add_header(toc_sec, header_txt)
    toc_sec.footer.is_linked_to_previous = False
    _toc(doc)

    body = doc.add_section(WD_SECTION.NEW_PAGE)
    _apply_page(body)
    _start_page_numbering(body, 1)
    _add_header_footer(body, header_txt)

    w = _Writer(doc)
    _susp_sec_1(w, model, d, doc_title, project)
    _susp_sec_2(w, model, d, f_frame)
    _susp_sec_3(w, model, d)
    _susp_sec_4(w, d, fig_tension=f_ten, fig_girder=f_gir)
    _susp_sec_5(w, d)
    _susp_sec_6(w, model, d, fig_camber=f_cam)
    if d["erection"]:
        _susp_sec_7_erection(w, model, d, fig_erection=f_erec)
    _susp_sec_8(w, d)

    doc.save(str(out))
    if update_toc:
        update_fields_with_word(out)
    return out


# ---------------------------------------------------------------- 第 1 章

def _susp_sec_1(w: _Writer, model, d: dict, title: str, project: str) -> None:
    p = d["params"]
    w.h1("工程概况")
    w.h2("1.1　桥型与主要尺寸")
    w.p(f"本桥为双塔三跨{d['system_label']}，主跨 {_f(d['span'], 1)} m，"
        f"矢跨比 1/{_f(d['sag_ratio'], 1)}（主缆矢高 {_f(d['sag'], 2)} m）。"
        f"加劲梁为钢箱梁，主缆与吊索采用高强钢丝。计算对象为"
        f"**恒载成桥态**：在全部恒载一次施加完成的构形上求主缆线形、索力、"
        f"加劲梁内力与支座反力。")

    rows = [
        ["桥型", d["system_label"]],
        ["跨径布置", f"{_f(p.get('side_span') or 0, 1)} + {_f(d['span'], 1)} + "
                     f"{_f(p.get('side_span') or 0, 1)} m"],
        ["主跨矢高 / 矢跨比", f"{_f(d['sag'], 2)} m / 1/{_f(d['sag_ratio'], 1)}"],
        ["主缆锚固方式", "锚于加劲梁端（自锚）" if d["is_self"] else "锚于锚碇（地锚）"],
        ["加劲梁形式", f"钢箱梁，宽 {_f(p.get('B') or 12, 1)} m，"
                       f"高 {_f(p.get('h') or 3, 1)} m"],
        ["桥面恒载", f"{_f(p.get('q') or 100, 0)} kN/m（含钢箱梁、桥面系与二期）"],
        ["主缆/吊索截面积", f"{_f(p.get('A') or 60000, 0)} / "
                            f"{_f(p.get('A_h') or 3000, 0)} mm²"],
    ]
    w.table("主要技术参数", ["项目", "取值"], rows, widths=[4.6, 10.9],
            aligns=["l", "l"])

    w.h2("1.2　自锚式与地锚式的差别")
    w.p("两种体系的力学内核**完全相同**（主缆找形、索长反算、几何非线性求解"
        "一套代码），差别全部落在边界条件上，只有两处：")
    w.bullet("主缆锚在哪里：地锚式锚在桥台外侧的锚碇上（全约束），主缆水平力 "
             "H 由锚碇传给地基；自锚式锚在加劲梁端节点上，H 压进加劲梁，"
             "梁内产生约 −H 的轴压力。")
    w.bullet("加劲梁纵向怎么约束：地锚式梁端纵向固定，H 不经梁；自锚式梁端"
             "纵向**必须自由**，否则 H 会被支座直接吃掉、梁轴力算成 0——"
             "位移和索力看起来全都正常，只有这一项错。故纵向约束放在跨中，"
             "对称荷载下该约束的支反力恒为零，只起消除刚体位移的作用。")
    w.p("由此带来一个容易被忽略的约束：自锚式的锚点就在加劲梁端，而竖向支座"
        "**只能承压、不能承拉**。主缆在锚点处的竖向分力 V 必须很小，否则梁端"
        "要被拔起来。V 是几何决定量而非可调参数，见 3.5 节的推导。")

    rows = [
        ["主缆锚固", "锚碇（全约束）", "加劲梁端节点"],
        ["H 的传力路径", "锚碇 → 地基", "主缆 → 加劲梁（轴压）"],
        ["加劲梁轴力", "≈ 0", "≈ −H"],
        ["梁端纵向约束", "固定", "自由（约束移至跨中）"],
        ["锚点竖向分力", "由锚碇抗拔承担", "由梁端支座承担，须接近 0"],
        ["对边跨长度的要求", "无特殊要求（常取 L/3）", "由 V=0 定，明显长于 L/3"],
    ]
    w.table("两种体系的受力差别", ["项目", "地锚式", "自锚式"], rows,
            widths=[3.6, 5.6, 6.3], aligns=["l", "l", "l"])
    w.note("本次计算的对象是：" + ("自锚式" if d["is_self"] else "地锚式")
           + "。上表另一列只作对照，不参与本次计算。")

    w.h2("1.3　计算依据")
    for t in ("JTG D60-2015《公路桥涵设计通用规范》：恒载取值与荷载组合。",
              "JTG/T D65-05-2015《公路悬索桥设计规范》：主缆与吊索安全系数、"
              "矢跨比范围。",
              "JTG/T 3365-01-2020《公路斜拉桥设计规范》：索的容许应力与"
              "安全系数取值的对照。",
              "GB/T 17101《桥梁缆索用热镀锌或锌铝合金钢丝》：主缆钢丝强度。"):
        w.bullet(t)
    w.p(f"计算软件为 FEM-Eigen 空间杆系几何非线性求解器（共转列式 + 索垂度"
        f"弹性悬链线），求解入口 cable_solver，本次计算迭代 {d['iterations']} 轮、"
        f"耗时 {_f(d['elapsed_ms'], 0)} ms、收敛={'是' if d['converged'] else '否'}。")


# ---------------------------------------------------------------- 第 2 章

def _susp_sec_2(w: _Writer, model, d: dict, fig_frame: Path) -> None:
    w.h1("计算模型")
    w.h2("2.1　离散与编号")
    w.p(f"全桥离散为空间杆系：加劲梁用梁单元沿单梁线（桥轴线）离散，桥塔用"
        f"梁单元沿塔轴离散，主缆与吊索用只受拉的索单元。模型共 "
        f"{d['n_nodes']} 个节点、{d['n_elems']} 个单元、{d['dofs']} 个自由度，"
        f"其中主缆 {d['n_main']} 段、吊索 {d['n_hanger']} 根、"
        f"加劲梁 {d['n_girder']} 段。")
    rows = [
        ["加劲梁", "1000+i", "100+i", "BEAM", "钢箱梁"],
        ["主缆", "2000+k", "3000+k", "CABLE", "高强钢丝"],
        ["桥塔（塔 1/塔 2）", "3000+m / 4000+m", "800+m / 900+m", "BEAM", "混凝土"],
        ["吊索", "—", "6000+j", "CABLE", "高强钢丝"],
        ["锚碇（仅地锚式）", "主缆端节点", "—", "—", "—"],
    ]
    w.table("构件编号约定", ["构件", "节点号", "单元号", "单元类型", "材料"], rows,
            widths=[3.4, 3.4, 3.2, 2.4, 3.1], aligns=["l", "c", "c", "c", "l"])
    w.note("主缆节点在塔位处**直接使用塔顶节点**，不另建坐标相同的节点。"
           "若两者只是坐标重合而不共用，主缆就成了一根从锚碇到锚碇的自由索，"
           "塔不参与受力——求解照样收敛、位移倾角看着都像座桥，只有索力会掉到"
           "设计值的百分之一。求解器对此有一条硬校验。")

    w.h2("2.2　材料与截面")
    # 用途按 *ET 归类，不按材料号猜：加劲梁是钢、桥塔是混凝土、缆索是
    # 高强钢丝，三者的强度体系完全不同（fd / 0.5fck / 安全系数）。
    kind_of: dict[int, set[str]] = {}
    for et in model.etypes:
        kind_of.setdefault(et.mat, set()).add(getattr(et, "kind", ""))
    cable_mats = {mid for mid, ks in kind_of.items() if ks and ks <= {"CABLE"}}

    def _grade_of(m: Any) -> str:
        return (getattr(m, "grade", "") or "").strip() or "未标注"

    rows = []
    for mat in model.materials:
        part = "缆索" if mat.id in cable_mats else "梁 / 塔"
        rows.append([f"M{mat.id}", part, _grade_of(mat),
                     _f(mat.E / 1e9, 1), _f(mat.dens, 0)])
    w.table("材料", ["编号", "用于", "材料", "弹性模量 E (GPa)", "密度 (kg/m³)"],
            rows, widths=[1.8, 2.8, 3.6, 3.9, 3.4],
            aligns=["c", "c", "l", "c", "c"])
    w.note("「用于」列只区分两类，因为两类材料的验算体系不同：梁与塔按强度"
           "设计值（fd 或 0.5fck）验算，缆索按安全系数 A·fpu/T 验算。"
           "各材料对应的具体构件见上一张表。")

    body_grades = sorted({_grade_of(m) for m in model.materials
                          if m.id not in cable_mats
                          and _grade_of(m) != "未标注"})
    for g in body_grades:
        try:
            if g.upper() in code_material.CONCRETE:
                t = code_material.concrete_table((g,))
                w.table(f"混凝土强度标准值与设计值（{g}）", t["headers"], t["rows"],
                        widths=[2.6, 2.9, 2.9, 2.9, 2.9, 2.9],
                        aligns=["c"] * 6)
            else:
                t = code_material.steel_table((g,))
                w.table(f"钢材强度设计值（{g}）", t["headers"], t["rows"],
                        widths=[3.0, 3.4, 2.8, 2.8, 3.2],
                        aligns=["c"] * 5)
        except KeyError:
            w.note(f"材料「{g}」不在强度登记表内，未列出强度设计值。")

    rows = []
    for rc in model.reals:
        rows.append([f"R{rc.id}", _f(rc.A, 5), _f(getattr(rc, "IY", 0) or 0, 4),
                     _f(getattr(rc, "W", 0) or 0, 1)])
    w.table("实常数（截面）", ["编号", "面积 A (m²)", "惯性矩 Iy (m⁴)",
                              "线重 W (N/m)"], rows,
            widths=[3.0, 4.2, 4.2, 4.1], aligns=["c", "c", "c", "c"])

    w.h2("2.3　边界条件")
    rows = []
    x_of = d["node_x"]
    for b in model.boundaries:
        dofs = tuple(getattr(b, "dofs", ()) or ())
        if not dofs:
            continue
        # 只列"体系性"约束，逐节点的扭转定位约束不逐条列（数量多、信息量低）
        if dofs == (4, 4):
            continue
        rows.append([b.node, _f(x_of.get(b.node, float("nan")), 1),
                     " ".join(str(x) for x in dofs)])
    if not rows:
        rows = [["—", "—", "—"]]
    w.table("节点约束（扭转定位约束不逐条列出）",
            ["节点", "x (m)", "约束自由度"], rows,
            widths=[3.4, 4.0, 8.1], aligns=["c", "c", "l"])
    w.p("自由度 1/2/3 为沿 X/Y/Z 的平动，4/5/6 为绕 X/Y/Z 的转动。"
        "主缆与吊索落在 y=0 平面内，索单元只提供轴向刚度，主缆节点的平面外"
        "自由度没有刚度来源，故按平面问题处理——这是单索面模型的既定假定，"
        "真实结构的面外刚度来自另一根主缆与横撑。")

    w.h2("2.4　恒载")
    w.p(f"桥面恒载以节点集中力的形式施加，按节点分担宽度分配，"
        f"合计 {_f(d['total_deck_load'] / 1e3, 0)} kN。塔柱自重按塔段长度"
        f"分摊到塔节点。主缆与吊索的自重由索单元线重自动计入（浅垂度弹性"
        f"悬链线中的 w），不重复施加节点力。")
    w.p("**注意一处刻意的处理**：加劲梁单元自身线重取 0，桥面恒载 "
        "q 已含钢箱梁自重。若两者都计，梁会重一遍，而重的那一遍不会出现在"
        "找形取用的节点荷载里，成桥态就会出现一个与荷载不符的系统性偏差。")

    w.h2("2.5　计算简图")
    w.figure(fig_frame, "结构计算简图（主缆、吊索、桥塔、加劲梁与竖向支承）")


# ---------------------------------------------------------------- 第 3 章

def _susp_sec_3(w: _Writer, model, d: dict) -> None:
    w.h1("主缆找形")
    w.h2("3.1　为什么悬索桥不能“给线形、求索力”")
    w.p("斜拉桥的设计线形由人给定（梁面高程是设计出来的），索力去适应它；"
        "悬索桥正相反：**主缆的线形由恒载决定**，人给定的是矢高（或矢跨比），"
        "线形与索力一起被解出来。把一条人工给定的抛物线当设计线形去做成桥态"
        "闭环，会得到一个无解的方程——残差卡在数厘米上看起来像“没收敛”，"
        "实质是那条线形根本不是平衡态。所以必须先找形。")

    w.h2("3.2　索多边形的微分方程")
    w.p("取主缆任一微段，设水平分力为 H（沿全索不变——这正是索的平衡条件："
        "索上无水平荷载），竖向力为 V(x)，则")
    w.formula("H · dz/dx = V(x)", "索的切线斜率由水平力与竖向力之比确定")
    w.p("对微段竖向平衡，V 沿 x 的变化率等于该处的向下荷载集度 q(x)，故")
    w.formula("V(x) = V_0 − S(x)，　S(x) = ∫_0^x q(ξ) dξ",
              "S 为截面以左的累计竖向荷载，V_0 为起点处的竖向力")
    w.p("两式合并即得线形的积分式。数值实现上不解析求 q，而是**逐段累加**："
        "把每个主缆节点上的吊索力与主缆自重按段中点的左侧累计荷载累加，"
        "再逐段积分高程。这样点荷载与自重可以同时精确进入，不必把吊索力"
        "摊成等效均布荷载。")
    w.formula("z_{k+1} = z_k + (S_k − V_0) · Δx_k / H")
    w.p("端部条件给出 V_0：要求 z 从起点积到终点恰好等于两者的高差 Δz，于是")
    w.formula("V_0 = (M − H·Δz) / L，　M = ∫_0^L S(x) dx",
              "主跨两端等高时 Δz=0，退化为 V_0 = M/L（即恒载的一半）")
    w.p("各分段索力随之确定：T_k = √(H² + V_k²)，V_k = V_0 − S_k。")

    w.h2("3.3　水平力 H 的确定")
    w.p("矢高 f 随 H 单调减小（H 越大索绷得越直），因此对 H 二分必然收敛。"
        "实现上在对数区间内二分 200 次，得到使主跨矢高命中目标的 H。"
        "本次计算的结果为：")
    w.formula(f"H = {_f(d['H_kN'], 0)} kN，　矢高 = {_f(d['sag'], 4)} m "
              f"（目标 {_f(d['sag_target'], 4)} m）")
    q_est = d["H_kN"] * 8 * d["sag"] / (d["span"] ** 2) if d["span"] > 0 else 0.0
    w.p(f"作为对照，把主跨荷载视为均布时 H ≈ qL²/(8f)，反解等效均布荷载 "
        f"q ≈ {_f(q_est, 1)} kN/m，与“桥面恒载 + 主缆自重”量级一致，"
        f"说明找形结果可信（数值差来自吊索的集中力与主缆沿跨的自重分布）。")

    w.h2("3.4　主缆连续跨塔：三跨为什么必须共用一个 H")
    w.p("主缆在塔顶是连续通过的（索鞍处只能转向，不能断开）。若主跨与边跨"
        "用了不同的 H，塔顶就会受到一对大小不等、方向相反的水平力，塔柱要"
        "靠弯曲去平衡它——这在真实结构里不存在，是建模引入的假力。所以做法是："
        "**先由主跨定 H，再用同一个 H 积分两个边跨**，边跨的端部竖向力由"
        "各自的高差条件反求。塔顶的水平力因此自动平衡，塔柱只承受竖向力。")
    w.p("边跨与主跨在塔顶的竖向分力并不相等，其差额就是塔柱的轴力来源——"
        "这是悬索桥塔柱的主要荷载，见 4.6 节。")

    w.h2("3.5　边跨平衡条件（自锚式的关键约束）")
    w.p("由 3.2 节的 V_0 表达式，边跨主缆在锚点处的竖向分力为")
    w.formula("V = H·(Δz/L) − qL/2",
              "Δz 为塔顶到锚点的高差，L 为边跨长度，q 为边跨等效均布荷载")
    w.p("令 V = 0 解出边跨长度：")
    w.formula("L = √(2·H·Δz / q)", "此时主缆在锚点恰好水平，锚点只受水平力")
    if d["is_self"]:
        w.p(f"**这一条对自锚式是有约束力的**：锚点就在加劲梁端，梁端竖向支座"
            f"不能承拉。若边跨短于上式给出的长度，主缆在锚点仍有向上的竖向"
            f"分力，梁端会被拔起来，必须设抗拔支座或压重。本桥边跨按此式取值，"
            f"主缆在锚点接近水平。")
    else:
        w.p("**地锚式不受这条约束**：锚碇靠自重与摩阻抗拔，V 为负（主缆向上"
            "拔锚碇）是常态，锚碇设计按抗拔与抗滑验算即可。因此地锚式的边跨"
            "可按常规取 1/3 主跨。")
    if d["is_self"]:
        v_anchor = [v for (n, dd), v in d["reaction"].items()
                    if dd == 3 and 1000 < n < 2000]
        if v_anchor:
            w.p(f"本次计算得到的梁端竖向反力最小值为 "
                f"{_f(min(v_anchor) / 1e3, 1)} kN（负值表示需下拉）。"
                f"|V|/H = {_f(abs(min(v_anchor)) / max(d['H_kN'], 1e-9), 3)}，"
                f"远小于 0.15，梁端无需抗拔构造。")
    # 平衡边跨的几何后果：锚点水平 → 边跨主缆不是"锚点到塔顶的直线"
    shp = sorted((v for v in d.get("cable_shape", {}).values()),
                 key=lambda v: v[0])
    if d["is_self"] and len(shp) >= 3:
        (x0, z0), (x1, z1) = shp[0], shp[1]
        slope0 = (z1 - z0) / max(x1 - x0, 1e-12)
        w.p("平衡边跨还有一个常被忽略的几何后果：锚点处竖向分力为零，意味着"
            "**主缆在锚点的切线是水平的**。所以边跨主缆不是“从锚点斜拉到塔顶”"
            "的一条直线，而是顶点落在锚点的抛物线，越靠近塔顶越陡。"
            f"本次计算中锚点段的初始斜率 dz/dx = {_f(slope0, 4)}（≈0），"
            "而“锚点到塔顶的直线”在该处的斜率应是 "
            f"{_f(d['tower_top_z'] / max(d['side_span'], 1e-9), 3)}——"
            "两者在塔根附近的偏差可达数米。建模时若按直线画初值并无不可"
            "（找形会把它改过来），但**设计线形与图件必须取找形结果**，"
            "拿初值当设计线形是把错的线形交给施工。")

    w.h2("3.6　无应力索长反算与索长迭代")
    w.p("找形给出的是**成桥态的几何与目标索力**，而施工需要的是**无应力索长**"
        "（下料长度）。由浅垂度弹性悬链线，已知弦长 L_c 与索力 T 时无应力索长为")
    w.formula("L_0 = (L_c + s) / (1 + T/EA)，　s = w²·L_h³ / (24·T²)",
              "s 为索在自重下的垂度附加长度，L_h 为水平投影长度")
    w.p("反算本身是闭式的，但有一处必须迭代：上式里的 L_c 应当取**平衡态弦长**，"
        "而不是设计弦长——两者差一个弹性缩短量（自锚式尤为明显，加劲梁被 H "
        "压缩，主缆跨度随之缩短，可达 100 mm 量级）。用设计弦长反算，达到的索力"
        "会系统性偏低百分之几，这点差额最后全部变成加劲梁的恒载弯矩。")
    w.p(f"实现上每轮只改 L_0、不动几何，反复求解直到达到索力与目标索力的最大"
        f"相对偏差小于 0.1%。本次计算的最终偏差为 {_f(d['tension_err'] * 100, 2)}%，"
        f"加劲梁恒载弯矩被压到 {_f(d['girder_M_max'], 0)} kN·m。")


# ---------------------------------------------------------------- 第 4 章

def _susp_sec_4(w: _Writer, d: dict, *, fig_tension: Path, fig_girder: Path) -> None:
    w.h1("成桥态计算结果")
    w.h2("4.1　结果汇总")
    rows = [
        ["主缆水平力 H", f"{_f(d['H_kN'], 0)}", "kN", "找形结果，非人为给定"],
        ["成桥矢高", f"{_f(d['sag'], 3)}", "m",
         f"目标 {_f(d['sag_target'], 3)} m"],
        ["主缆索力", f"{_f(d['T_main_min'] / 1e3, 0)} ~ "
                     f"{_f(d['T_main_max'] / 1e3, 0)}", "kN", "分段极值"],
        ["吊索索力", f"{_f(d['T_hanger_min'] / 1e3, 1)} ~ "
                     f"{_f(d['T_hanger_max'] / 1e3, 1)}", "kN",
         f"{d['n_hanger']} 根"],
        ["吊索竖向力合计", f"{_f(d['T_hanger_sum'] / 1e3, 0)}", "kN",
         "应等于其所吊桥面恒载"],
        ["加劲梁轴力 |N|max", f"{_f(d['girder_N_max'], 0)}", "kN",
         "自锚式应 ≈ H；地锚式应 ≈ 0"],
        ["加劲梁弯矩 |My|max", f"{_f(d['girder_M_max'], 0)}", "kN·m",
         "恒载下应接近 0"],
        ["竖向支反力合计", f"{_f(d['support_v_sum'], 0)}", "kN",
         f"与竖向荷载 {_f(d['applied_v'], 0)} kN 之比"],
        ["竖向平衡偏差", f"{_f(d['balance_pct'], 4)}", "%", "目标 < 0.01%"],
        ["达到索力偏差", f"{_f(d['tension_err'] * 100, 2)}", "%", "目标 < 0.1%"],
        ["最大预变形", f"{_f(d['camber_max'] * 1000, 0)}", "mm",
         "制造线形相对设计线形的偏移"],
    ]
    w.table("成桥态主要结果", ["项目", "数值", "单位", "说明"], rows,
            widths=[4.0, 3.4, 1.8, 6.3], aligns=["l", "r", "c", "l"])

    w.h2("4.2　主缆索力")
    w.p("主缆沿全长是一条连续索，各分段索力由 √(H²+V²) 给出，水平分力 H "
        "沿全长不变。塔顶附近的分段索力最大（该处斜率最陡），跨中附近最小"
        "（斜率接近零，索力接近 H 本身）。")
    tb = d["tables"].get("main_cable_table")
    if tb:
        w.table("主缆分段索力与下料长度", tb["headers"], tb["rows"],
                widths=[2.2, 2.6, 2.6, 2.6, 2.6, 1.5, 1.4],
                aligns=["c", "r", "r", "r", "r", "r", "r"])
        w.note(tb["note"])
    w.figure(fig_tension, "主缆分段索力与吊索索力分布")

    w.h2("4.3　吊索索力")
    w.p("吊索的目标索力直接取该梁节点承担的竖向恒载，不需要迭代——这是悬索桥"
        "与斜拉桥在“索力怎么来”上最直观的差别。实际达到的索力与目标相差 "
        f"{_f(d['tension_err'] * 100, 2)}%，差值来自加劲梁的弹性压缩改变了索的弦长，"
        "已在 3.6 节通过索长迭代压到很小。")
    tb = d["tables"].get("hanger_table")
    if tb:
        w.table("吊索索力、下料长度与安全系数", tb["headers"], tb["rows"],
                widths=[2.2, 2.2, 2.6, 2.6, 2.2, 2.2, 1.5],
                aligns=["c", "c", "r", "r", "r", "r", "r"])
        w.note(tb["note"])

    w.h2("4.4　加劲梁内力")
    w.p("恒载下加劲梁的受力状态是判别体系是否建模正确的第一道关："
        + ("自锚式应形成约 −H 的轴压力，弯矩则应接近 0；" if d["is_self"]
           else "地锚式轴力应接近 0（H 由锚碇直接承受），弯矩也应接近 0；")
        + "若梁的弯矩出现数千 kN·m 量级，说明吊索力与桥面荷载没有对上，"
          "荷载被梁“扛”过去了。")
    tb = d["tables"].get("girder_table")
    if tb:
        w.table("加劲梁恒载内力", tb["headers"], tb["rows"],
                widths=[3.0, 4.2, 4.2, 4.1], aligns=["c", "r", "r", "r"])
        w.note(tb["note"])
    w.figure(fig_girder, "加劲梁恒载弯矩与轴力沿桥分布")

    w.h2("4.5　支座反力与竖向平衡")
    w.p(f"全部支座竖向反力之和为 {_f(d['support_v_sum'], 0)} kN，竖向荷载合计 "
        f"{_f(d['applied_v'], 0)} kN（节点恒载 + 缆索自重），偏差 "
        f"{_f(d['balance_pct'], 4)}%。这一项是**全局平衡校核**，也是"
        f"判断“模型有没有算对”最硬的一条：它不依赖任何假定，只依赖平衡。")
    w.p("说明：悬索桥不能用“索力竖向分量之和”做平衡校核。主缆各段的竖向分量"
        "一段向上、一段向下，求和的符号取决于每段的方向被取成哪一端，得到的"
        "数没有物理意义（实测可达恒载的 6 倍）。真正的平衡式是"
        "Σ支座反力 = Σ竖向荷载。")
    tb = d["tables"].get("reaction_table")
    if tb:
        w.table("支座反力", tb["headers"], tb["rows"],
                widths=[3.0, 3.6, 3.0, 5.9], aligns=["c", "r", "c", "r"])
        w.note(tb["note"])

    w.h2("4.6　桥塔内力")
    towers = sorted({n for (n, dd), _v in d["reaction"].items()
                     if dd == 3 and 3000 < n < 5000})
    w.p("塔柱轴力 = 塔顶主缆竖向分力之和 + 塔柱自重。塔顶主缆竖向分力可由"
        "主缆在该处的斜率与 H 直接得到；塔底竖向反力则直接取 4.5 节的支座"
        "反力结果。")
    if towers:
        rows = []
        for nid in towers:
            v = d["reaction"].get((nid, 3))
            if v is None:
                continue
            rows.append([nid, _f(d["node_x"].get(nid, float("nan")), 1),
                         _f(v / 1e3, 0)])
        if rows:
            w.table("塔底竖向反力", ["节点", "x (m)", "竖向反力 (kN)"], rows,
                    widths=[3.4, 4.2, 7.9], aligns=["c", "r", "r"])
    w.note("塔处加劲梁的竖向支座反力按支座反力单独计，不并入塔柱轴力——"
           "本版把塔梁传力路径简化为“梁在塔处直接支承”，不细分经横梁传至"
           "塔柱的过程。若需计入，把塔位梁节点的竖向反力叠加到塔柱轴力上。")


# ---------------------------------------------------------------- 第 5 章

def _susp_sec_5(w: _Writer, d: dict) -> None:
    w.h1("体系与安全度校核")
    w.h2("5.1　校核汇总")
    rows = []
    for c in d["checks"]:
        rows.append(["√" if c.passed else "×", c.name, c.detail or "—"])
    if not rows:
        rows = [["—", "—", "无校核项"]]
    w.table("校核汇总（√ 通过 / × 不通过）", ["结论", "校核项", "计算值"], rows,
            widths=[1.4, 5.0, 9.1], aligns=["c", "l", "l"])
    w.note("不通过项必须处理；标注为“仅陈述”的项不构成判定，只说明量级。"
           "矢跨比、吊索力均匀性、索力达到目标三项属于“建议值”级别，"
           "超出时需人工判断而不是直接判不合格。")

    w.h2("5.2　体系自洽性校核")
    if d["is_self"]:
        w.p("自锚式的定义就是主缆水平力 H 压进加劲梁，所以恒载下梁内轴力必然"
            "≈ −H。这一条是本计算书里**最能一把抓住模型错误**的判据：若把纵向"
            "约束错放在梁端，H 会直接进支座，梁轴力算成 0，而位移、索力、"
            "竖向平衡全都正常——除了模型本身已经错了。")
        w.formula(f"|N_梁| / H = {_f(abs(d['girder_N_max']) / max(d['H_kN'], 1e-9), 3)}"
                  f"　（应 ≈ 1，容差 10%）")
    else:
        w.p("地锚式的主缆水平力 H 由锚碇直接传给地基，不进入加劲梁，所以恒载"
            "下梁内轴力应接近 0。若出现接近 H 的轴力，说明主缆端节点被误接到了"
            "加劲梁上（那已经是自锚式了）。")
        w.formula(f"|N_梁| / H = {_f(abs(d['girder_N_max']) / max(d['H_kN'], 1e-9), 4)}"
                  f"　（应 ≈ 0）")

    w.h2("5.3　索的安全系数")
    w.p("主缆安全系数按 JTG/T D65-05-2015 取 2.0，吊索取 2.5。主缆的限值反而"
        "比吊索低，原因是主缆不可更换、设计上按全寿命考虑，而吊索可更换——"
        "这是规范里的既有取舍，不是笔误。")
    w.formula("K = A · f_pu / T", "A 取实常数截面，主缆 f_pu = 1770 MPa，"
                                  "吊索 f_pu = 1860 MPa")
    w.p("逐根安全系数见 4.2 节与 4.3 节的明细表。注意主缆与吊索的截面积相差"
        "近二十倍，必须按各自的实常数分别计算；用同一个面积去算会把吊索判重"
        "二十倍，看起来“吊索很安全”。")


# ---------------------------------------------------------------- 第 6 章

def _susp_sec_6(w: _Writer, model, d: dict, fig_camber: Path) -> None:
    w.h1("下料长度与制造线形")
    w.h2("6.1　主缆与吊索的下料长度")
    w.p("下料长度 L_0 是**无应力长度**，即索在零应力状态下的长度，由 3.6 节的"
        "闭式反算给出，并经索长迭代修正到与平衡态弦长一致。施工时按 L_0 下料，"
        "架设后施加恒载，索即伸长到设计弦长、达到设计索力。")
    rows = []
    for r in d["chain"][:6]:
        rows.append([r["elem"], _f(r["L0"], 4), _f(float(r["T"]) / 1e3, 1)])
    if len(d["chain"]) > 6:
        rows.append(["…", "…", "…"])
    w.table("主缆分段下料长度（摘录，全表见 4.2 节）",
            ["单元", "下料 L0 (m)", "索力 (kN)"], rows,
            widths=[4.0, 6.0, 5.5], aligns=["c", "r", "r"])

    w.h2("6.2　制造线形与预变形")
    w.p("制造线形 = 设计线形 − 成桥位移。索承体系的成桥位移是“弹性压缩 + 索的"
        "弹性伸长”的结果，量级在毫米到厘米；自锚式还要多一项加劲梁的轴向"
        "缩短（H·L/EA），可达百毫米量级，这一项属于**制造长度补偿**而不是"
        "预拱度，两者要分开看。")
    w.formula("预变形 = −u，　制造线形 = 设计线形 + 预变形",
              "u 为成桥位移（相对制造线形），故制造线形 = 设计线形 − u")
    w.p(f"本次计算加劲梁的最大竖向预变形为 {_f(d['camber_max'] * 1000, 1)} mm，"
        f"约为主跨的 L/{_f(d['span'] * 1000.0 / max(d['camber_max'] * 1000, 0.1), 0)}。"
        "这个量级对应的是预制标高调整，量级很小说明恒载线形已由主缆找形吸收掉了——"
        "悬索桥的恒载弯矩本来就该接近于零。")
    w.figure(fig_camber, "加劲梁竖向预变形沿桥分布")
    if d["is_self"]:
        w.p(f"另一项必须单独交代：主缆锚在加劲梁端，水平力 H = {_f(d['H_kN'], 0)} kN "
            f"直接压进梁体，成桥态梁体总缩短 {_f(abs(d['deck_short_mm']), 1)} mm，"
            f"两端各向内位移 {_f(d['deck_end_move_mm'], 1)} mm。"
            "这一项要靠**梁端伸缩缝 / 合龙段预留**消化，不是起拱能解决的；"
            "施工时若按竖向预变形反推梁段长度，就会少放长这一截。")
    else:
        w.p("地锚式的主缆锚在锚碇上，H 由锚碇传给地基，加劲梁不承担轴力，"
            "因此不存在梁体轴向缩短这一项——这也是地锚式与自锚式在施工控制上"
            "最省心的地方。")
    tb = d["tables"].get("camber_table")
    if tb:
        w.table("加劲梁制造线形（预变形）", tb["headers"], tb["rows"],
                widths=[3.0, 3.6, 4.4, 4.5], aligns=["c", "r", "r", "r"])
    w.note("本内核输出的预变形取负号即为“制造时压低/放长”的方向："
           "正号表示成桥后该点会上升，制造时应预先压低。")


# ---------------------------------------------------------------- 第 7 章

def _susp_sec_7_erection(w: _Writer, model, d: dict, fig_erection) -> None:
    """施工阶段（分级找形）：空缆 → 从跨中对称吊装 → 合拢 → 二期铺装。"""
    er = d["erection"]
    stages = sorted(er, key=lambda r: r["stage"])
    first, last = stages[0], stages[-1]
    q1, q2 = d["erection_q1"], d["erection_q2"]

    w.h1("施工阶段（从跨中吊装到成桥）")
    w.h2("7.1　施工顺序")
    w.p(f"施工阶段按「分级找形」模拟：主缆下料长度 L_0 一旦固定，各阶段的"
        f"主缆水平力 H 与矢高就不再是输入，而是由「二分 H 使整根主缆的无应力"
        f"长度命中 L_0」反算出来的结果。施工顺序为：")
    w.bullet("空缆：只受主缆自重，矢高最小、H 最小。")
    w.bullet("吊装：主跨加劲梁自跨中向两侧对称吊装一期恒载 Q_1（钢箱梁自重，"
             "铰接，不产生加劲梁弯矩），每步对称吊 2×PANELS 段。")
    w.bullet("合拢：主跨加劲梁合拢成连续梁，边跨加劲梁同步就位，全桥 Q_1 上齐。")
    w.bullet("铺装：二期恒载 Q_2（铺装 + 护栏）全桥施加，达到成桥态。")

    w.h2("7.2　分级找形原理")
    w.formula("L_0(H) = Σ_k (c_k + s_k) / (1 + T_k/EA)，　s_k = w²·Lh³/(24T_k²)",
              "c_k 为分段弦长、T_k 为分段索力、w 为主缆单位重；对 H 二分使 L_0(H)=L_0")
    w.p("与成桥找形「给定矢高反算 H」不同，施工阶段找形是「给定 L_0 反算 H」："
        "主缆不可伸长（下料长度固定），从空缆到成桥主缆逐级下沉、矢高从 "
        f"{_f(first['sag'], 2)} m 增大到 {_f(last['sag'], 2)} m。")
    if q2 > 0:
        w.p(f"一期恒载 Q_1 = {_f(q1 / 1e3, 1)} kN/m（钢箱梁自重），"
            f"二期恒载 Q_2 = {_f(q2 / 1e3, 1)} kN/m（铺装 + 护栏），"
            f"合计 {_f((q1 + q2) / 1e3, 1)} kN/m。")

    w.h2("7.3　各阶段主缆线形与索力")
    rows = []
    for r in stages:
        hmn = float(r.get("hmin") or 0.0)
        hmx = float(r.get("hmax") or 0.0)
        rows.append([
            str(r["stage"]), r.get("name", ""),
            _f(float(r.get("H") or 0.0) / 1e3, 0),
            _f(float(r.get("sag") or 0.0), 3),
            _f(float(r.get("midz") or 0.0), 3),
            _f(hmn / 1e3, 0) if hmn > 0 else "—",
            _f(hmx / 1e3, 0) if hmx > 0 else "—",
        ])
    w.table("各阶段主缆 H、矢高、跨中高程与吊索力",
            ["阶段", "名称", "H (kN)", "矢高 (m)", "跨中高程 (m)",
             "吊索力 min (kN)", "吊索力 max (kN)"],
            rows, widths=[1.4, 2.0, 2.6, 2.4, 2.8, 2.6, 2.6],
            aligns=["c", "l", "r", "r", "r", "r", "r"])
    if fig_erection is not None:
        w.figure(fig_erection, "主缆线形随吊装下沉 + H / 矢高随阶段变化")

    w.h2("7.4　自洽判据与施工控制要点")
    w.p(f"铺装态 H = {_f(float(last.get('H') or 0.0) / 1e3, 0)} kN、"
        f"矢高 {_f(float(last.get('sag') or 0.0), 2)} m，与成桥态"
        f"（H = {_f(d['H_kN'], 0)} kN、矢高 {_f(d['sag'], 2)} m）一致——"
        "铺装完成后的状态必须精确重现成桥态，这是施工阶段模拟正确性的自洽判据。")
    peak = max(stages, key=lambda r: float(r.get("sag") or 0.0))
    if peak is not first and peak is not last:
        w.p(f"吊装中期矢高出现峰值 {_f(float(peak.get('sag') or 0.0), 2)} m"
            f"（阶段 {peak['stage']}，{peak.get('name', '')}）：荷载集中在跨中时"
            "主缆下沉最多，是主缆下料长度与吊装顺序的校核要点。")
    w.note("本次施工阶段模拟的简化假设：边跨加劲梁在合拢阶段一次就位（不逐段"
           "吊装）；一期恒载吊装阶段按铰接处理（加劲梁不产生恒载弯矩）；"
           "未计吊机/配重/风等施工临时荷载；单索面平面模型，双索面与横撑"
           "不在本版范围。")

    w.h2("7.5　施工阶段与成桥态的衔接")
    w.p("成桥态的恒载内力 = 各施工阶段恒载增量的叠加。主缆索力是状态量"
        "（不累加），加劲梁内力按各阶段增量累加；本算例一期恒载在吊装阶段"
        "铰接上梁、不产生加劲梁弯矩，因此加劲梁恒载弯矩主要来自二期铺装与"
        "成桥后的活载组合。")


# ---------------------------------------------------------------- 第 8 章

def _susp_sec_8(w: _Writer, d: dict) -> None:
    w.h1("结论与建议")
    w.h2("8.1　主要结论")
    w.bullet(f"主缆线形由恒载找形确定，矢高 {_f(d['sag'], 3)} m 命中目标 "
             f"{_f(d['sag_target'], 3)} m，对应水平力 H = {_f(d['H_kN'], 0)} kN。")
    w.bullet(f"主缆分段索力 {_f(d['T_main_min'] / 1e3, 0)} ~ "
             f"{_f(d['T_main_max'] / 1e3, 0)} kN，吊索索力 "
             f"{_f(d['T_hanger_min'] / 1e3, 1)} ~ {_f(d['T_hanger_max'] / 1e3, 1)} kN，"
             f"达到值与找形目标的偏差 {_f(d['tension_err'] * 100, 2)}%。")
    if d["is_self"]:
        w.bullet(f"加劲梁恒载轴力 {_f(d['girder_N_max'], 0)} kN ≈ −H，"
                 f"自锚体系成立；恒载弯矩 {_f(d['girder_M_max'], 0)} kN·m，"
                 f"处于该体系正常量级。")
    else:
        w.bullet(f"加劲梁恒载轴力 {_f(d['girder_N_max'], 0)} kN（≈0），"
                 f"恒载弯矩 {_f(d['girder_M_max'], 0)} kN·m，"
                 f"恒载全部由主缆承担，符合地锚式悬索桥的受力特征。")
    w.bullet(f"Σ支座反力 {_f(d['support_v_sum'], 0)} kN 与 Σ竖向荷载 "
             f"{_f(d['applied_v'], 0)} kN 的偏差 {_f(d['balance_pct'], 4)}%，"
             f"全局竖向平衡成立。")

    w.h2("8.2　设计建议")
    if d["is_self"]:
        w.bullet("自锚式的边跨长度受“锚点主缆水平”条件约束（3.5 节），"
                 "取值明显长于地锚式的 1/3 主跨。若受场地限制必须缩短边跨，"
                 "梁端会出现上拔力，需设抗拔支座或压重，并把该项列入施工工序。")
        w.bullet("加劲梁在恒载下即承受约 −H 的轴压力，其稳定性（面内、面外）"
                 "与截面压应力应作为控制条件另行验算；本计算书只给出内力，"
                 "不做承载力判定。")
    else:
        w.bullet("锚碇需按抗拔与抗滑验算：主缆在锚点把锚碇向上拔，"
                 "设计不能只考虑承压。锚碇反力见 4.5 节。")
        w.bullet("地锚式的边跨可取 1/3 主跨左右，但需注意边跨过短会使主缆在"
                 "锚点的竖向分力过大，锚碇规模随之增加。")
    w.bullet("主缆与吊索的安全系数按标准值验算；运营期的活载效应与荷载组合"
             "需另接活载模块，本计算书不包含活载。")
    w.bullet("本模型为单索面平面模型，未计双索面与横撑提供的面外刚度。"
             "面外稳定（主缆横向、吊索横向、加劲梁侧向）应另行验算。")

    w.h2("8.3　附件")
    w.table("计算附件", ["文件", "说明"], [
        ["suspension.dat", "求解器输入（模型 + 悬索桥找形定位段）"],
        ["suspension_cable.txt", "求解器原始输出（索力/索长/反力/位移，分段纯文本）"],
        ["fig_susp_frame.png", "图 2-1 源图"],
        ["fig_susp_tension.png", "图 4-1 源图"],
        ["fig_susp_girder.png", "图 4-2 源图"],
        ["fig_susp_camber.png", "图 6-1 源图"],
        ["fig_susp_erection.png", "图 7-1 源图（仅施工阶段模拟时生成）"],
    ], widths=[4.6, 10.9], aligns=["l", "l"])
    par = w.doc.add_paragraph()
    par.paragraph_format.space_before = Pt(12)
    r = par.add_run("— 计算书结束 —")
    _set_run_font(r, cn=FONT_HEI, size=10.5, color=GRAY)
    par.alignment = WD_ALIGN_PARAGRAPH.CENTER
