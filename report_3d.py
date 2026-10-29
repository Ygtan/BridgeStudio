"""report_3d.py —— 三维结果的四个出口（界面 / Word / HTML / CSV）共用的报告层。

问题
----
"接入"完成后，求解只返回一种结果（`SolveResult`），但**出口**还是三份：

- 界面自己画图（`ui_3d._plot_grillage` / `_plot_cable`），图注、颜色、坐标轴
  各写一遍；
- CSV 由界面另拼一遍表头；
- Word 只有斜拉桥有（`report_docx` 的 7 章 16 页），梁格没有计算书。

于是同一个数字在两处可能出现两种写法（`175.9` 与 `175.90`、`kN` 与 `kN·m`），
改一处限值要改三处。这正是"后处理"要收口的地方。

做法
----
**先把"内容"拼成一份与排版无关的中间结构，再各写一个渲染器。**

    SolveResult + model
        │
        ├─ figures()      图（PNG）—— 界面、Word、HTML 共用同一张
        ├─ tables()       表（来自 SolveResult，本就唯一）
        └─ blocks()       正文/标题/校核/结论（纯数据，不含字号与颜色）
                │
                ├─ write_html()   单文件 HTML（图片内嵌 base64）
                ├─ write_docx()   复用 report_docx 的版式积木
                └─ write_csv()    zip 包（指标 / 校核 / 每张明细表一个 csv）

界面不再画图，只负责把 `figures()` 的 PNG 摆出来。这样"界面看到的"
与"下载到的"是同一张图、同一张表、同一句结论——不是"尽量一致"。

为什么中间结构用 dict 而不是类
------------------------------
块类型只有七八种（h1/h2/p/bullets/metrics/checks/table/figure/note），
用 dict 让三个渲染器各写一个 `if t == ...` 分支就能穷举完；换成类层次
反而要在每个渲染器里 import 一遍所有块类型。
"""

from __future__ import annotations

import base64
import csv
import datetime as _dt
import io
import math
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from result_3d import (CABLE_FPU_DEFAULT, CABLE_SAFETY_MIN, Check, Metric,
                       SolveResult, _cable_area_m2, support_x)

# ---------------------------------------------------------------- 配色
# 与界面、Word 计算书同一套，避免"屏幕上蓝、报告里灰"
C_BLUE = "#185FA5"
C_LIGHT = "#8FA8C8"
C_RED = "#A32D2D"
C_GREEN = "#2E7D32"
C_GRAY = "#666666"
C_INK = "#1A1A1A"


# ================================================================ 图


def _plt():
    """matplotlib 句柄 + 中文字体。不改 backend——Streamlit 里已经有自己的。

    只设 rcParams：`matplotlib.use("Agg")` 在 pyplot 已导入后调用会静默失效，
    而在某些后端上又会抛异常，不值得为了省一行去赌。
    """
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "SimSun"]
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["font.size"] = 9
    plt.rcParams["axes.linewidth"] = 0.8
    return plt


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=200, bbox_inches="tight")
    import matplotlib.pyplot as plt

    plt.close(fig)
    return path


def _deck_x(model: Any) -> dict[int, float]:
    return {n.id: n.x for n in model.nodes}


def _elem_mid_x(model: Any) -> dict[int, float]:
    nodes = {n.id: n for n in model.nodes}
    out: dict[int, float] = {}
    for e in model.elements:
        a, b = nodes.get(e.n1), nodes.get(e.n2)
        if a is not None and b is not None:
            out[e.id] = (a.x + b.x) / 2.0
    return out


def _is_plan_view(model: Any) -> bool:
    """横向（y）铺开的模型画平面图，否则画立面图。

    梁格由多条纵梁 + 横梁组成，投影到立面会重叠成一坨；斜拉桥主梁在 y=0
    一条线上，投影到平面则退化成一条直线。用模型自己的几何判定，不问类型。
    """
    ys = [n.y for n in model.nodes] if model.nodes else [0.0]
    return (max(ys) - min(ys)) > 0.5


def figure_layout(res: SolveResult, model: Any, path: Path) -> Path:
    """结构计算简图：单元按类型着色 + 支承断面符号。"""
    plt = _plt()
    nodes = {n.id: n for n in model.nodes}
    kind_of = {t.id: t.kind for t in model.etypes}
    plan = _is_plan_view(model)

    fig, ax = plt.subplots(figsize=(7.6, 3.3))
    style = {"BEAM": (C_INK, 1.6), "CABLE": ("#6F6F6F", 0.9),
             "TRUSS": ("#7A7A7A", 1.2)}
    for e in model.elements:
        a, b = nodes.get(e.n1), nodes.get(e.n2)
        if a is None or b is None:
            continue
        col, lw = style.get(kind_of.get(e.etype, "BEAM"), ("#9A9A9A", 1.0))
        # 横梁必须画细画浅：45 道横梁在 15.5 cm 宽的版面上会连成一片黑栅栏，
        # 把纵梁全盖住（图上只剩"斑马线"，看不出是三跨连续体系）。
        if plan and abs(b.y - a.y) > 0.5:
            col, lw = "#9A9A9A", 0.45
        if plan:
            ax.plot([a.x, b.x], [a.y, b.y], color=col, lw=lw, solid_capstyle="butt",
                    zorder=3 if lw > 1.0 else 2)
        else:
            ax.plot([a.x, b.x], [a.z, b.z], color=col, lw=lw, solid_capstyle="butt")

    # 支座按**实际受约束的节点**画，不在 y=0 画一个代表符号：梁格的支座
    # 是横向一排（每条纵梁下一个），只画中间那一个会让人以为是独柱支承。
    sup_nodes = _support_node_positions(model)
    if plan:
        for (x, y, _z) in sup_nodes:
            ax.plot([x], [y], marker="^", ms=6, mfc="white", mec=C_RED,
                    mew=1.0, zorder=6)
        ax.set_xlabel("顺桥向 x (m)　（红三角 = 支承节点）")
        ax.set_ylabel("横桥向 y (m)")
    else:
        zs = [n.z for n in model.nodes] or [0.0]
        H = max(zs) - min(zs) or 1.0
        for (x, _y, z) in sup_nodes:
            ax.plot([x], [z], marker="^", ms=6, mfc="white", mec=C_RED,
                    mew=1.0, zorder=6)
        ax.set_ylim(-0.16 * H, 1.10 * max(H, 1e-9))
        ax.set_xlabel("顺桥向 x (m)　（红三角 = 支承节点）")
        ax.set_ylabel("竖向 z (m)")

    ax.grid(alpha=0.25, lw=0.5)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    return _save(fig, path)


def _support_node_positions(model: Any) -> list[tuple[float, float, float]]:
    """受竖向/横向约束的节点坐标 → [(x, y, z)]。

    与 `support_x()` 同源（都看 dof 2/3），但保留 y：梁格的支座是横向一排，
    画到图上是 3 个三角并排，而不是一个代表符号。
    """
    by = {n.id: n for n in model.nodes}
    out: list[tuple[float, float, float]] = []
    seen: set[int] = set()
    for b in getattr(model, "boundaries", []):
        if (2 in b.dofs or 3 in b.dofs) and b.node in by and b.node not in seen:
            n = by[b.node]
            out.append((n.x, n.y, n.z))
            seen.add(b.node)
    return out


def _bucketed(pairs: Iterable[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    """把 (x, lo, hi) 按 x 归并成包络。

    梁格同一 x 上有 5 条纵梁的节点，逐点画会让曲线在上下两条之间来回锯齿，
    看着像数值振荡——其实是横向不同位置。按 x 取包络才是"这张图想说的东西"。
    """
    bucket: dict[float, tuple[float, float]] = {}
    for x, lo, hi in pairs:
        k = round(x, 3)
        if k in bucket:
            l0, h0 = bucket[k]
            bucket[k] = (min(l0, lo), max(h0, hi))
        else:
            bucket[k] = (lo, hi)
    return [(k, v[0], v[1]) for k, v in sorted(bucket.items())]


def figure_deflection(res: SolveResult, model: Any, path: Path) -> Path:
    """竖向位移包络沿桥分布。两个内核共用（成桥态是单状态包络）。"""
    plt = _plt()
    x_of = _deck_x(model)
    pts = [(x_of[nid], lo, hi) for (nid, dof), (lo, hi) in res.disp.items()
           if dof == 3 and nid in x_of]
    rows = _bucketed(pts)
    fig, ax = plt.subplots(figsize=(7.4, 2.9))
    if rows:
        ax.plot([r[0] for r in rows], [r[1] * 1000 for r in rows], "-",
                lw=1.3, color=C_BLUE, label="包络 min")
        ax.plot([r[0] for r in rows], [r[2] * 1000 for r in rows], "--",
                lw=0.9, color=C_LIGHT, label="包络 max")
        ax.axhline(0.0, color=C_GRAY, lw=0.8)
    for sx in support_x(model):
        ax.axvline(x=sx, color=C_RED, ls="--", lw=0.7, alpha=0.55)
    ax.set_xlabel("顺桥向 x (m)　（红虚线 = 支承断面）")
    ax.set_ylabel("竖向位移 (mm)")
    ax.grid(alpha=0.25, lw=0.5)
    if rows:
        ax.legend(frameon=False, fontsize=8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    return _save(fig, path)


def figure_moment(res: SolveResult, model: Any,
                  path: Path) -> tuple[Path, str]:
    """梁单元竖向弯矩包络（My），**按纵梁线分组**。返回 (图路径, 图注补充)。

    不分组的坑：梁格里既有纵梁也有横梁，横梁的 My 是横向弯曲，和纵梁的纵向
    弯矩混在一张图上，同一 x 处两个量交替出现 —— 画出来是"每隔 2.5 m 一个
    尖峰"的锯齿，看图的人第一反应是数值振荡。按横向位置 y 分组后每条纵梁
    一条曲线，横梁单独排除并在图注里说明。
    """
    plt = _plt()
    nodes = {n.id: n for n in model.nodes}
    lines: dict[float, list[tuple[float, float, float]]] = {}
    n_trans = 0
    for e in model.elements:
        a, b = nodes.get(e.n1), nodes.get(e.n2)
        v = res.beam.get(e.id)
        if a is None or b is None or v is None:
            continue
        if abs(b.y - a.y) > 0.5:            # 横梁：本次不画
            n_trans += 1
            continue
        y = round((a.y + b.y) / 2.0, 2)
        lines.setdefault(y, []).append(
            ((a.x + b.x) / 2.0, v.get("MyMin", 0.0), v.get("MyMax", 0.0)))

    fig, ax = plt.subplots(figsize=(7.4, 3.0))
    colors = [C_BLUE, C_GREEN, "#8A5A00", "#5B4B8A", "#0F6E6E"]
    for i, (y, rows) in enumerate(sorted(lines.items())):
        rows.sort(key=lambda t: t[0])
        c = colors[i % len(colors)]
        ax.plot([r[0] for r in rows], [r[1] / 1e3 for r in rows], "-", lw=1.2,
                color=c, label=f"纵梁 y={y:+.1f} m  min")
        ax.plot([r[0] for r in rows], [r[2] / 1e3 for r in rows], "--", lw=0.8,
                color=c, alpha=0.55)
    ax.axhline(0.0, color=C_GRAY, lw=0.8)
    # 顶部留 30% 给图例：3 条曲线 6 个图例项，放右上必然压住支座负弯矩峰
    if lines:
        # 单位是 N·m，图上是 kN·m：忘了 /1e3 会把纵轴放大一千倍（实测直接
        # 变成 1e7 量级、曲线压成一条直线）
        hi = max(max(r[2] for r in grp) for grp in lines.values()) / 1e3
        lo = min(min(r[1] for r in grp) for grp in lines.values()) / 1e3
        ax.set_ylim(lo * 1.12, hi * 1.45 if hi > 0 else -lo * 1.45)
    for sx in support_x(model):
        ax.axvline(x=sx, color=C_RED, ls="--", lw=0.7, alpha=0.55)
    ax.set_xlabel("顺桥向 x (m)　（红虚线 = 支承断面）")
    ax.set_ylabel("My (kN·m)")
    ax.grid(alpha=0.25, lw=0.5)
    if lines:
        ax.legend(frameon=False, fontsize=7.5, ncol=3, loc="upper center")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    p = _save(fig, path)
    # 把"排除了什么"写进图注：一张只有纵梁弯矩的图，若不说，读者会以为
    # 横梁没有弯矩。
    note = (f"本图只含纵梁；{n_trans} 个横梁单元（横向弯曲）未绘入，"
            "其数值见明细表。" if n_trans else "")
    return p, note


def _component_peak(res: SolveResult, kmin: str, kmax: str,
                    scale: float = 1.0) -> float:
    """某内力分量的包络峰值（已按 scale 换算单位）。"""
    return max((max(abs(v.get(kmin, 0.0)), abs(v.get(kmax, 0.0)))
                for v in res.beam.values()), default=0.0) / scale


def _line_groups(res: SolveResult, model: Any, kmin: str, kmax: str
                 ) -> tuple[dict[float, list[tuple[float, float, float]]], int]:
    """按纵梁线（横向 y）分组收集单元包络 → ({y: [(x, min, max)]}, 横梁数)。

    为什么必须分组：梁格里横梁与纵梁的同一分量语义不同（纵梁 My 是竖向弯曲，
    横梁 My 是横向弯曲）。混在一起画，同一个 x 上两个量交替出现，看起来就是
    数值振荡。这个坑已经踩过一次（弯矩图锯齿）。
    """
    nodes = {n.id: n for n in model.nodes}
    groups: dict[float, list[tuple[float, float, float]]] = {}
    n_trans = 0
    for e in model.elements:
        a, b = nodes.get(e.n1), nodes.get(e.n2)
        v = res.beam.get(e.id)
        if a is None or b is None or v is None:
            continue
        if abs(b.y - a.y) > 0.5:            # 横梁
            n_trans += 1
            continue
        y = round((a.y + b.y) / 2.0, 2)
        groups.setdefault(y, []).append(
            ((a.x + b.x) / 2.0, v.get(kmin, 0.0), v.get(kmax, 0.0)))
    for g in groups.values():
        g.sort(key=lambda t: t[0])
    return groups, n_trans


def _envelope_figure(groups: dict[float, list[tuple[float, float, float]]],
                     model: Any, path: Path, *, ylabel: str, scale: float,
                     legend_fmt: str = "纵梁 y={:+.1f} m") -> Path:
    """一组纵梁线的 min/max 包络（实线 min、虚线 max）。"""
    plt = _plt()
    fig, ax = plt.subplots(figsize=(7.4, 3.0))
    colors = [C_BLUE, C_GREEN, "#8A5A00", "#5B4B8A", "#0F6E6E"]
    for i, (y, rows) in enumerate(sorted(groups.items())):
        c = colors[i % len(colors)]
        ax.plot([r[0] for r in rows], [r[1] / scale for r in rows], "-", lw=1.2,
                color=c, label=legend_fmt.format(y) + " min")
        ax.plot([r[0] for r in rows], [r[2] / scale for r in rows], "--", lw=0.95,
                color=c, alpha=0.85)
    ax.axhline(0.0, color=C_GRAY, lw=0.8)
    if groups:
        hi = max(max(r[2] for r in g) for g in groups.values()) / scale
        lo = min(min(r[1] for r in g) for g in groups.values()) / scale
        top = hi * 1.45 if hi > 0 else 0.0
        bot = lo * 1.12 if lo < 0 else 0.0
        ax.set_ylim(bot, top if top > bot else bot + 1.0)
    for sx in support_x(model):
        ax.axvline(x=sx, color=C_RED, ls="--", lw=0.7, alpha=0.55)
    ax.set_xlabel("顺桥向 x (m)　（红虚线 = 支承断面）")
    ax.set_ylabel(ylabel)
    ax.grid(alpha=0.25, lw=0.5)
    if groups:
        ax.legend(frameon=False, fontsize=7.5, ncol=3, loc="upper center")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    return _save(fig, path)


def figure_axial(res: SolveResult, model: Any,
                 path: Path) -> tuple[Path, str]:
    """轴力包络 N（纵梁）。轴力是预应力/体系转换的核对量，不是弯矩的附属。"""
    groups, n_trans = _line_groups(res, model, "Nmin", "Nmax")
    p = _envelope_figure(groups, model, path, ylabel="N (kN)", scale=1e3)
    note = (f"只含纵梁；{n_trans} 个横梁单元未绘入。" if n_trans else "")
    return p, note


def figure_shear(res: SolveResult, model: Any,
                 path: Path) -> tuple[Path, str]:
    """剪力包络 Vz（纵梁）。"""
    groups, n_trans = _line_groups(res, model, "VzMin", "VzMax")
    p = _envelope_figure(groups, model, path, ylabel="Vz (kN)", scale=1e3)
    note = (f"只含纵梁；{n_trans} 个横梁单元未绘入。" if n_trans else "")
    return p, note


def figure_live(res: SolveResult, model: Any, path: Path) -> Path:
    """活载包络：各施工阶段影响点处车道荷载产生的竖向位移。

    内核只给标量（每个阶段一对 max/min），没有沿桥分布——所以是成对柱状图，
    不是曲线。画成曲线会是伪造的：三个点连成的"曲线"没有物理含义。
    """
    plt = _plt()
    stages = sorted(res.live)
    lo = [res.live[s][1] * 1000 for s in stages]
    hi = [res.live[s][0] * 1000 for s in stages]
    fig, ax = plt.subplots(figsize=(6.4, 2.9))
    idx = list(range(len(stages)))
    # 柱宽按组数自适应：只有 3 个阶段时 0.34 的柱宽在版面上细得像针
    w = min(0.42, 2.6 / max(len(stages), 1) / 2)
    ax.bar([i - w / 2 for i in idx], lo, w, color=C_BLUE, label="向下（min）")
    ax.bar([i + w / 2 for i in idx], hi, w, color=C_LIGHT, label="向上（max）")
    ax.axhline(0.0, color=C_GRAY, lw=0.8)
    ax.set_xticks(idx)
    ax.set_xticklabels([f"阶段 {s}" for s in stages])
    ax.set_ylabel("竖向位移 (mm)")
    ax.grid(axis="y", alpha=0.25, lw=0.5)
    ax.legend(frameon=False, fontsize=8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    return _save(fig, path)


def figure_safety(res: SolveResult, model: Any, path: Path) -> Path:
    """逐根索的安全系数，附规范下限线。"""
    plt = _plt()
    area = _cable_area_m2(model)
    x_of = _deck_x(model)
    cs = sorted(res.cables, key=lambda c: x_of.get(c["anchor"], 0.0))
    ks = [(x_of.get(c["anchor"], 0.0), area * CABLE_FPU_DEFAULT / c["T"]
           if (area and c["T"] > 0) else float("nan")) for c in cs]
    fig, ax = plt.subplots(figsize=(7.4, 2.7))
    ax.plot([k[0] for k in ks], [k[1] for k in ks], "o-", ms=3.4, lw=1.1,
            color=C_BLUE, mfc=C_BLUE, label="安全系数 κ = A·fpu / T")
    ax.axhline(CABLE_SAFETY_MIN, color=C_RED, ls="--", lw=0.9,
               label=f"限值 {CABLE_SAFETY_MIN:.1f}（JTG/T 3365-01）")
    for sx in support_x(model):
        ax.axvline(x=sx, color=C_RED, ls="--", lw=0.7, alpha=0.35)
    ax.set_xlabel("顺桥向 x (m)　（红虚线 = 支承断面）")
    ax.set_ylabel("安全系数 κ")
    ax.grid(alpha=0.25, lw=0.5)
    ax.legend(frameon=False, fontsize=8, loc="upper center", ncol=2)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    return _save(fig, path)


def figure_tension(res: SolveResult, model: Any, path: Path) -> Path:
    """成桥索力沿桥纵向分布。"""
    plt = _plt()
    x_of = _deck_x(model)
    cs = sorted(res.cables, key=lambda c: x_of.get(c["anchor"], 0.0))
    fig, ax = plt.subplots(figsize=(7.4, 2.9))
    ax.plot([x_of.get(c["anchor"], 0.0) for c in cs],
            [c["T"] / 1e3 for c in cs], "o-", ms=3.4, lw=1.1,
            color=C_BLUE, mfc=C_BLUE, label="成桥索力")
    # 平均线自己算：内核 meta 里没有这个字段，不去假设它存在
    if cs:
        ax.axhline(y=sum(c["T"] for c in cs) / len(cs) / 1e3, color=C_GREEN,
                   lw=0.8, ls=":", label="平均索力")
    for sx in support_x(model):
        ax.axvline(x=sx, color=C_RED, ls="--", lw=0.7, alpha=0.55)
    ax.set_xlabel("顺桥向 x (m)　（红虚线 = 支承断面）")
    ax.set_ylabel("索力 (kN)")
    ax.grid(alpha=0.25, lw=0.5)
    ax.legend(frameon=False, fontsize=8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    return _save(fig, path)


def figure_camber(res: SolveResult, model: Any, path: Path) -> Path:
    """主梁预抬高（制造线形相对设计线形）。"""
    plt = _plt()
    x_of = _deck_x(model)
    deck = sorted((n for n in res.camber if 1000 < n < 2000),
                  key=lambda n: x_of.get(n, 0.0)) or sorted(res.camber)
    fig, ax = plt.subplots(figsize=(7.2, 2.8))
    if deck:
        ax.plot([x_of.get(n, 0.0) for n in deck],
                [res.camber[n][2] * 1000 for n in deck], "o-", ms=3.2, lw=1.1,
                color=C_GREEN, mfc=C_GREEN)
        ax.axhline(0.0, color=C_GRAY, lw=0.8)
        ymax = max((abs(res.camber[n][2]) * 1000 for n in deck), default=1.0) or 1.0
        ax.set_ylim(-1.35 * ymax, 1.35 * ymax)
    for sx in support_x(model):
        ax.axvline(x=sx, color=C_RED, ls="--", lw=0.7, alpha=0.55)
    ax.set_xlabel("顺桥向 x (m)")
    ax.set_ylabel("预抬高 (mm)")
    ax.grid(alpha=0.25, lw=0.5)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    return _save(fig, path)


#: 报告正文里图的排列顺序。与 figures() 的生成顺序解耦：报告要有稳定的
#: 叙事顺序（先简图、再变形、再内力、再索），而生成顺序由判据决定。
_FIGURE_ORDER = ("layout", "deflection", "moment", "axial", "shear",
                 "live", "tension", "safety", "camber")


@dataclass
class Figure:
    key: str
    caption: str
    path: Path
    note: str = ""


def figures(res: SolveResult, model: Any, out_dir: str | Path,
            *, dpi_scale: float = 1.0) -> dict[str, Figure]:
    """生成全部图。**哪些图存在由结果决定**，不是由类型硬编码。

    梁格没有索 → 不出索力图；成桥态内核不输出梁内力 → 不出弯矩图。
    宁可少一张图，不要一张空坐标轴（那会让看图的人以为内力为零）。
    """
    d = Path(out_dir) / "figures"
    out: dict[str, Figure] = {}

    out["layout"] = Figure(
        "layout",
        f"结构计算简图（{len(model.nodes)} 节点 / {len(model.elements)} 单元）",
        figure_layout(res, model, d / "layout.png"),
        note=("平面图：横向为便于阅读已放大，横纵比例不相等。"
              if _is_plan_view(model) else ""))
    if any(dof == 3 for (_n, dof) in res.disp):
        # 成桥态的 u 是"相对制造线形"的位移（内核 CompletionStateResult3D::u），
        # 不是"相对设计标高"的下挠：桥面按制造线形架好、在恒载下落回设计标高，
        # 所以它必然与预抬高互为镜像。标成"竖向位移包络"会被读成"桥面挠了 180 mm"。
        if res.kind == "cable_stayed":
            cap = "成桥位移（相对制造线形，向下为负）"
            note = ("该曲线是桥面从制造线形落回设计标高的过程量，与制造线形预抬高"
                    "互为镜像；设计标高是否闭合由几何闭合误差判定（当前 "
                    f"{(res.meta.get('closure_err') or 0):.1e} m）。")
        else:
            cap = "竖向位移包络（施工阶段）"
            note = ""
        out["deflection"] = Figure(
            "deflection", cap, figure_deflection(res, model, d / "deflection.png"),
            note=note)
    if res.beam:
        mp, mnote = figure_moment(res, model, d / "moment.png")
        out["moment"] = Figure(
            "moment", "梁单元竖向弯矩包络（My）", mp,
            note="弯矩符号随单元 i→j 方向：实线为 min、虚线为 max，"
                 "比较量级时取绝对值。" + mnote)
        # 轴力与剪力：弯矩之外的两个主控内力，内核已输出就不该只画弯矩。
        # 但"有字段"不等于"有量"：三跨连续梁格没有纵向约束，竖向荷载不产生
        # 轴力（N 全为 -0.0）。画出来是一张 y 轴 0~1 的空图，比不画更糟——
        # 读者会以为轴力曲线被裁掉了。这里按峰值判，1e-3 kN 以下视为零。
        if _component_peak(res, "Nmin", "Nmax", 1e3) > 1e-3:
            ap, anote = figure_axial(res, model, d / "axial.png")
            out["axial"] = Figure(
                "axial", "梁单元轴力包络（N）", ap,
                note="实线为 min（压）、虚线为 max（拉）。" + anote)
        if _component_peak(res, "VzMin", "VzMax", 1e3) > 1e-3:
            sp, snote = figure_shear(res, model, d / "shear.png")
            out["shear"] = Figure(
                "shear", "梁单元剪力包络（Vz）", sp,
                note="实线为 min、虚线为 max。" + snote)
    if res.live:
        # 阶段包络为 0 不是漏算：施工中间态下影响点还没与任何单元连通
        # （阶段 1 只建边跨，中跨跨中尚无单元）。不说清楚，读者会以为
        # "阶段 1 的活载算丢了"。
        zeros = [s for s, (hi, lo) in sorted(res.live.items())
                 if abs(hi) < 1e-12 and abs(lo) < 1e-12]
        lnote = ("内核按施工阶段逐车道求影响面后取包络；每阶段只有一对极值，"
                 "故为柱状而非沿桥曲线。")
        if zeros:
            lnote += (f"阶段 {'、'.join(map(str, zeros))} 包络为 0：该阶段"
                      "影响点尚未与任何单元连通（施工中间态），不是漏算。")
        out["live"] = Figure(
            "live", "活载包络（JTG D60 车道荷载，影响点 = 中跨跨中）",
            figure_live(res, model, d / "live.png"), note=lnote)
    if res.cables:
        out["tension"] = Figure("tension", "成桥索力分布",
                                figure_tension(res, model, d / "tension.png"))
        if _cable_area_m2(model):
            out["safety"] = Figure(
                "safety", "索安全系数分布",
                figure_safety(res, model, d / "safety.png"),
                note=f"κ = A·fpu / T，A 取实常数、fpu = 1860 MPa；"
                     f"限值 {CABLE_SAFETY_MIN:.1f}。")
    if res.camber:
        out["camber"] = Figure("camber", "制造线形预抬高",
                               figure_camber(res, model, d / "camber.png"))
    return out


# ================================================================ 文档中间结构


@dataclass
class Report:
    """一份报告的**全部内容**，不含任何排版参数。

    三个渲染器（HTML / DOCX / CSV）都只读这个结构；界面的指标卡与校核清单
    也读同一份 `blocks` 里的 items，因此"界面看到的"和"下载到的"不会分叉。
    """

    title: str
    subtitle: str
    kind: str
    meta: list[tuple[str, str]] = field(default_factory=list)
    blocks: list[dict[str, Any]] = field(default_factory=list)
    figures: dict[str, Figure] = field(default_factory=dict)
    tables: dict[str, dict[str, Any]] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)
    fingerprint: str = ""
    generated_at: str = ""

    def checks(self) -> list[Check]:
        for b in self.blocks:
            if b["type"] == "checks":
                return b["items"]
        return []

    def metrics(self) -> list[Metric]:
        for b in self.blocks:
            if b["type"] == "metrics":
                return b["items"]
        return []


def _fmt(v: Any, nd: int = 2) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if not math.isfinite(f):
        return "—"
    return f"{f:.{nd}f}"


def _overview_paragraph(res: SolveResult, model: Any) -> str:
    st = res.stats
    conv = {True: "几何非线性迭代收敛",
            False: "**迭代未收敛，结果仅作参考**", None: "线性求解一次完成"}[
                res.converged if res.converged is not None else None]
    txt = (f"本次计算在 {st.get('nodes', 0)} 个节点、{st.get('elements', 0)} 个单元、"
           f"{st.get('dofs', '—')} 个自由度的模型上直接求解，{conv}"
           f"（{res.iterations or 0} 轮 / {(res.elapsed_ms or 0):.0f} ms）。")
    if res.diagnostics:
        txt += ("全部数值取自求解器输出文件，未做二次力学计算；"
                "本内核不输出的量在第五节逐条说明。")
    else:
        txt += "全部数值取自求解器输出文件，未做二次力学计算。"
    return txt


def _conclusion_bullets(res: SolveResult, model: Any,
                        checks: Sequence[Check]) -> list[str]:
    """结论从校核结果**生成**，不手写——手写的结论会与数据脱节。"""
    bad = [c for c in checks if not c.passed]
    out: list[str] = []
    if not bad:
        out.append(f"本次共 {len(checks)} 项工程校核全部通过，"
                   "结果可作为本阶段设计依据。")
    else:
        out.append(f"本次共 {len(checks)} 项校核，{len(checks) - len(bad)} 项通过，"
                   f"**{len(bad)} 项未通过**，需在下结论前处理：")
        out += [f"未通过 · {c.name}：{c.detail}" for c in bad]
    mvd = res.max_vertical_deflection()
    if mvd:
        L = res.total_length(model)
        tail = f"（约 L/{L / abs(mvd[1]):.0f}，L={L:.0f} m）" if L and mvd[1] else ""
        out.append(f"最大竖向位移 {mvd[1] * 1000:.2f} mm，出现在节点 {mvd[0]}{tail}。")
    mb = res.max_bending()
    if mb:
        out.append(f"最大竖向弯矩 |My| = {mb[1] / 1e3:.1f} kN·m，位于单元 {mb[0]}。")
    if res.cables:
        tmax = max(c["T"] for c in res.cables)
        tmin = min(c["T"] for c in res.cables)
        out.append(f"成桥索力 {tmin / 1e3:.0f} ~ {tmax / 1e3:.0f} kN，"
                   f"共 {len(res.cables)} 根；索承担桥面恒载 "
                   f"{_fmt(res.meta.get('deck_share_pct'), 1)}%。")
    if res.camber:
        zmax = max((v[2] for v in res.camber.values()), key=abs, default=0.0)
        out.append(f"制造线形最大预抬高 {zmax * 1000:.1f} mm"
                   "（负值表示成桥会上抬，制造时压低）。")
    out.append("本报告为设计阶段计算复核，不含施工阶段验算、疲劳验算与"
               "抗震验算；荷载组合按单一恒载工况给出。")
    return out


def build_document(res: SolveResult, model: Any, *, project: str = "",
                   out_dir: str | Path | None = None,
                   params: dict[str, Any] | None = None) -> Report:
    """把 SolveResult 拼成与排版无关的报告结构。"""
    out_dir = Path(out_dir or "out") / f"report_{res.kind}"
    figs = figures(res, model, out_dir)
    tabs = res.tables(model)
    metrics = res.metrics(model)
    checks = res.checks(model)
    title = project or res.title or res.kind_label

    meta: list[tuple[str, str]] = [
        ("桥型", res.kind_label),
        ("结构规模", f"{res.stats.get('nodes', 0)} 节点 / "
                     f"{res.stats.get('elements', 0)} 单元 / "
                     f"{res.stats.get('dofs', '—')} 自由度"),
        ("求解内核", f"{res.solver}（结果指纹 {res.fingerprint}）"),
        ("数据来源", res.result_path or "—"),
        ("生成时间", _dt.datetime.now().strftime("%Y-%m-%d %H:%M")),
    ]

    blocks: list[dict[str, Any]] = []

    blocks.append({"type": "h1", "text": "计算概要"})
    blocks.append({"type": "p", "text": _overview_paragraph(res, model)})
    blocks.append({"type": "metrics", "items": metrics})

    blocks.append({"type": "h1", "text": "工程校核"})
    nbad = sum(1 for c in checks if not c.passed)
    blocks.append({"type": "p", "text": (
        f"共 {len(checks)} 项校核，{len(checks) - nbad} 项通过。"
        "判据、限值与实测值同列一表，便于逐条复核；"
        "标注「提示」的条目只陈述量级，不构成合格判定。" if checks else
        "本次求解没有产出可判定的校核项。")})
    blocks.append({"type": "checks", "items": checks})

    blocks.append({"type": "h1", "text": "计算结果"})
    # 按**实际生成的图**出章节，而不是写死一份清单。写死的清单在加图时
    # 会静默漏掉：图生成了、报告里没有，而"报告生成成功"看不出来
    # （这正是 verify_report_3d 的 base64 对拍断言抓到的问题）。
    for key in _FIGURE_ORDER:
        if key in figs:
            blocks.append({"type": "figure", "key": key})
    for key in figs:                     # 清单外的新图兜底，排在最后
        if key not in _FIGURE_ORDER:
            blocks.append({"type": "figure", "key": key})

    blocks.append({"type": "h1", "text": "结论与说明"})
    blocks.append({"type": "bullets", "items": _conclusion_bullets(res, model, checks)})
    if res.diagnostics:
        blocks.append({"type": "h2", "text": "本内核不输出的量"})
        blocks.append({"type": "bullets", "items": list(res.diagnostics)})

    # 明细表放最后：它是被查的，不是被读的；夹在结果与结论之间会让结论
    # 翻过几十页表格才出现（梁格 222 个单元 = 15 页）。
    if tabs:
        blocks.append({"type": "h1", "text": "明细表"})
        for key in tabs:
            blocks.append({"type": "table", "key": key})

    return Report(title=title, subtitle=res.kind_label, kind=res.kind, meta=meta,
                  blocks=blocks, figures=figs, tables=tabs,
                  diagnostics=list(res.diagnostics), fingerprint=res.fingerprint,
                  generated_at=_dt.datetime.now().strftime("%Y-%m-%d %H:%M"))


# ================================================================ 渲染器：HTML


_HTML_CSS = """
:root{--ink:#1f2328;--mut:#59636e;--line:#d8dee4;--bg:#fff;--soft:#f6f8fa;
      --blue:#185fa5;--green:#2e7d32;--red:#a32d2d;--amber:#9a6700;}
*{box-sizing:border-box}
body{margin:0;background:var(--soft);color:var(--ink);
     font:15px/1.75 "Microsoft YaHei","PingFang SC","Hiragino Sans GB",sans-serif;}
.page{max-width:900px;margin:24px auto 60px;background:var(--bg);padding:52px 60px 44px;
      box-shadow:0 1px 3px rgba(0,0,0,.08),0 8px 24px rgba(0,0,0,.06);border-radius:4px;}
h1{font-size:15px;margin:34px 0 10px;padding-left:9px;border-left:4px solid var(--blue);
   font-weight:700;letter-spacing:.02em;}
h2{font-size:14px;margin:22px 0 8px;font-weight:700;}
p{margin:0 0 10px;text-align:justify;}
.title{font-size:26px;font-weight:700;margin:0 0 6px;letter-spacing:.01em;}
.subtitle{color:var(--mut);font-size:13px;margin:0 0 22px;}
.meta{display:grid;grid-template-columns:88px 1fr;gap:2px 14px;font-size:12.5px;
      padding:14px 16px;background:var(--soft);border:1px solid var(--line);border-radius:4px;
      margin-bottom:6px;}
.meta dt{color:var(--mut);} .meta dd{margin:0;word-break:break-all;}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:10px;
       margin:12px 0 6px;}
.card{border:1px solid var(--line);border-radius:4px;padding:10px 12px;}
.card .k{font-size:11.5px;color:var(--mut);} .card .v{font-size:19px;font-weight:700;
       font-variant-numeric:tabular-nums;} .card .u{font-size:12px;color:var(--mut);margin-left:3px;}
.card .h{font-size:11.5px;color:var(--mut);margin-top:2px;}
table{border-collapse:collapse;width:100%;font-size:12.5px;margin:8px 0 4px;
      font-variant-numeric:tabular-nums;}
caption{caption-side:top;text-align:left;font-size:12.5px;font-weight:700;color:var(--mut);
        padding-bottom:6px;}
th,td{padding:5px 8px;border-bottom:1px solid var(--line);text-align:center;}
th{font-weight:700;border-top:1.6px solid var(--ink);border-bottom:1px solid var(--ink);}
th{white-space:nowrap;}
th.nw,td.nw{white-space:nowrap;}
.tw{overflow-x:auto;margin:8px 0 4px;}
table.wide{font-size:11.5px;} table.wide th,table.wide td{padding:4px 6px;}
tbody tr:last-child td{border-bottom:1.6px solid var(--ink);}
td.l,th.l{text-align:left;}
figure{margin:16px 0 10px;} figure img{width:100%;height:auto;display:block;}
.tnote{margin:2px 0 12px;color:var(--mut);font-size:11.5px;}
figcaption{font-size:12.5px;font-weight:700;color:var(--mut);text-align:center;margin-top:6px;}
.ok{color:var(--green);font-weight:700;} .ng{color:var(--red);font-weight:700;}
.warn{color:var(--amber);font-weight:700;}
ul{margin:6px 0 12px;padding-left:22px;} li{margin:3px 0;}
.foot{margin-top:36px;padding-top:12px;border-top:1px solid var(--line);color:var(--mut);
      font-size:11.5px;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;}
@media print{body{background:#fff}.page{box-shadow:none;margin:0;max-width:none;padding:0}
  h1{page-break-after:avoid}figure,table{page-break-inside:avoid}
  .tw{overflow:visible} table.wide{font-size:9.5px}}
"""


def _unit(u: Any) -> str:
    """单位的展示形式。`"1"` 是内部表示无量纲的标记，不该露在表格里。"""
    u = "" if u is None else str(u)
    return "" if u in ("1", "—", "None") else u


def _h(s: Any) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _table_html(title: str, headers: Sequence[str], rows: Sequence[Sequence[Any]],
                left_cols: Sequence[int] = ()) -> str:
    th = "".join(
        f'<th class="l">{_h(x)}</th>' if i in left_cols else f"<th>{_h(x)}</th>"
        for i, x in enumerate(headers))
    body = []
    for r in rows:
        tds = "".join(
            f'<td class="l">{_h(v)}</td>' if i in left_cols else f"<td>{_h(v)}</td>"
            for i, v in enumerate(r))
        body.append(f"<tr>{tds}</tr>")
    cap = f"<caption>{_h(title)}</caption>" if title else ""
    cls = ' class="wide"' if len(headers) >= 8 else ""
    return (f'<div class="tw"><table{cls}>{cap}<thead><tr>{th}</tr></thead>'
            f"<tbody>{''.join(body)}</tbody></table></div>")


def _img_b64(path: Path) -> str:
    data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    mt = "image/png" if str(path).lower().endswith(".png") else "image/jpeg"
    return f"data:{mt};base64,{data}"


def render_html(rep: Report) -> str:
    """把 Report 渲染成**单个自包含** HTML（图片内嵌，无外链）。"""
    # 字符串里的 **粗体** 标记转成 <strong>，与 Word 共用同一份正文
    def rich(s: str) -> str:
        out = _h(s)
        while "**" in out:
            out = out.replace("**", "<strong>", 1)
            if "**" in out:
                out = out.replace("**", "</strong>", 1)
        return out

    body: list[str] = []
    fig_no = tab_no = 0
    for b in rep.blocks:
        t = b["type"]
        if t == "h1":
            body.append(f"<h1>{_h(b['text'])}</h1>")
        elif t == "h2":
            body.append(f"<h2>{_h(b['text'])}</h2>")
        elif t == "p":
            body.append(f"<p>{rich(b['text'])}</p>")
        elif t == "bullets":
            items = "".join(f"<li>{rich(x)}</li>" for x in b["items"])
            body.append(f"<ul>{items}</ul>")
        elif t == "note":
            body.append(f'<p style="color:#59636e;font-size:12.5px">{_h(b["text"])}</p>')
        elif t == "metrics":
            cards = "".join(
                f'<div class="card"><div class="k">{_h(m.label)}</div>'
                f'<div class="v">{_h(m.value)}'
                + (f'<span class="u">{_h(m.unit)}</span>' if m.unit else "")
                + "</div>"
                + (f'<div class="h">{_h(m.hint)}</div>' if m.hint else "")
                + "</div>" for m in b["items"])
            body.append(f'<div class="cards">{cards}</div>')
        elif t == "checks":
            rows, lefts = [], ()
            for c in b["items"]:
                mark = ("<span class='ok'>通过</span>" if c.passed
                        else ("<span class='warn'>提示</span>" if c.level == "info"
                              else "<span class='ng'>不通过</span>"))
                u = _unit(c.unit)
                val = ("—" if c.value is None else
                       f"{c.value:.4g}" + (f" {u}" if u else ""))
                lim = ("—" if c.limit is None else
                       f"{c.limit:.4g}" + (f" {u}" if u else ""))
                rows.append([c.name, mark, val, lim, c.detail])
            rows_html = "".join(
                "<tr>" + "".join(
                    f'<td class="l">{x}</td>' if i in (0, 4)
                    else f'<td class="nw">{x}</td>'
                    for i, x in enumerate(r)) + "</tr>" for r in rows)
            body.append(
                '<div class="tw"><table><thead><tr><th class="l">校核项</th>'
                '<th class="nw">结论</th><th class="nw">实测值</th>'
                '<th class="nw">限值</th><th class="l">说明</th></tr></thead>'
                f"<tbody>{rows_html}</tbody></table></div>")
        elif t == "table":
            tb = rep.tables[b["key"]]
            tab_no += 1
            body.append(_table_html(f"表 {tab_no}　{tb['title']}",
                                    tb["headers"], tb["rows"], left_cols=(0,)))
            if tb.get("note"):
                body.append(f'<p class="tnote">{_h(tb["note"])}</p>')
        elif t == "figure":
            f = rep.figures.get(b["key"])
            if f is None:
                continue
            fig_no += 1
            body.append(
                f'<figure><img alt="{_h(f.caption)}" src="{_img_b64(f.path)}">'
                f"<figcaption>图 {fig_no}　{_h(f.caption)}</figcaption></figure>")
            if f.note:
                body.append(f'<p style="color:#59636e;font-size:12px">{_h(f.note)}</p>')

    meta = "".join(f"<dt>{_h(k)}</dt><dd>{_h(v)}</dd>" for k, v in rep.meta)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_h(rep.title)} · {_h(rep.subtitle)}</title>
<style>{_HTML_CSS}</style></head><body><div class="page">
<div class="title">{_h(rep.title)}</div>
<div class="subtitle">{_h(rep.subtitle)}　计算报告</div>
<dl class="meta">{meta}</dl>
{''.join(body)}
<div class="foot"><span>BridgeStudio · FEM-Eigen　结果指纹 {_h(rep.fingerprint)}</span>
<span>生成于 {_h(rep.generated_at)}</span></div>
</div></body></html>"""


def write_html(rep: Report, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_html(rep), encoding="utf-8")
    return p


# ================================================================ 渲染器：DOCX


def _display_w(s: Any) -> int:
    """显示宽度：中文按 2、其余按 1。列宽按它分配，不能按 len()。"""
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in str(s))


def _col_widths(headers: Sequence[str], rows: Sequence[Sequence[Any]],
                total: float, n_sample: int = 60) -> list[float]:
    """按内容宽度分列宽（单位 cm）。

    为什么不是"按比例分摊"：比例分摊会让窄列吃掉刚好不够的宽度——「单元」列
    内容只有 3 个字符（0.45 cm），按比例分到 0.88 cm，扣掉单元格左右边距
    （各 0.19 cm）只剩 0.5 cm，于是 101 被折成竖排的 1/0/1。宽度必须按
    "内容 + 边距"算绝对量，再整体压缩。
    """
    UNIT = 0.162      # 9 pt 下一个半角字符约 1.6 mm
    PAD = 0.44        # 单元格左右边距合计 + 余量
    FLOOR = 1.10      # 再挤也要容得下两位数
    rows = list(rows)
    n = len(headers)

    want: list[float] = []
    for i in range(n):
        wid = max([_display_w(headers[i])] +
                  [_display_w(r[i]) for r in rows[:n_sample] if i < len(r)])
        want.append(max(FLOOR, min(30 * UNIT + PAD, wid * UNIT + PAD)))

    s_all = sum(want)
    if s_all <= total:                       # 还有富余：按内容比例摊掉
        out = [w + (total - s_all) * (w / s_all) for w in want]
    else:                                    # 超宽：等比压缩后再从最宽列扣
        out = [max(FLOOR, w * (total / s_all)) for w in want]
        over = sum(out) - total
        for i in sorted(range(n), key=lambda k: -out[k]):
            if over <= 1e-9:
                break
            take = min(over, out[i] - FLOOR)
            out[i] -= take
            over -= take
    return [round(x, 2) for x in out]


def write_docx(rep: Report, path: str | Path) -> Path:
    """用 report_docx 的版式积木排版。**复用而非另写一套**：三线表、
    图表编号、分页控制的坑已经在那 1500 行里踩完了。"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    from report_docx import (TEXT_W, _Writer, _add_header_footer,
                             _set_run_font, _setup_document)

    doc = _setup_document()
    _add_header_footer(doc.sections[0], f"{rep.title} · 计算报告")
    w = _Writer(doc)

    # 标题区（不占章号，所以不调 w.h1）
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(2)
    _set_run_font(p.add_run(rep.title), cn="黑体", size=20, bold=True)
    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p2.paragraph_format.space_after = Pt(14)
    _set_run_font(p2.add_run(f"{rep.subtitle}　计算报告"), size=11.5, color=None)

    # number=False：这张表属于标题区，若编号会变成"表 0-1"，还会把后续表号推一位
    w.table("计算基本信息", ["项目", "内容"], rep.meta,
            _col_widths(["项目", "内容"], rep.meta, TEXT_W),
            aligns=["l", "l"], number=False)

    fig_no = tab_no = 0
    for b in rep.blocks:
        t = b["type"]
        if t == "h1":
            w.h1(b["text"])
        elif t == "h2":
            w.h2(b["text"])
        elif t == "p":
            w.p(b["text"])
        elif t == "bullets":
            for x in b["items"]:
                w.bullet(x)
        elif t == "note":
            w.note(b["text"])
        elif t == "metrics":
            w.table("主要指标", ["指标", "数值", "单位", "说明"],
                    [[m.label, m.value, m.unit or "—", m.hint or "—"]
                     for m in b["items"]],
                    _col_widths(["指标", "数值", "单位", "说明"],
                                [[m.label, m.value, m.unit or "—", m.hint or "—"]
                                 for m in b["items"]], TEXT_W),
                    aligns=["l", "c", "c", "l"])
        elif t == "checks":
            rows = []
            for c in b["items"]:
                mark = "通过" if c.passed else ("提示" if c.level == "info" else "不通过")
                val = "—" if c.value is None else f"{c.value:.4g}"
                lim = "—" if c.limit is None else f"{c.limit:.4g}"
                rows.append([c.name, mark, val, lim, c.detail])
            w.table("工程校核", ["校核项", "结论", "实测", "限值", "说明"], rows,
                    _col_widths(["校核项", "结论", "实测", "限值", "说明"],
                                rows, TEXT_W),
                    aligns=["l", "c", "c", "c", "l"])
        elif t == "table":
            tb = rep.tables[b["key"]]
            tab_no += 1
            w.table(tb["title"], tb["headers"], tb["rows"],
                    _col_widths(tb["headers"], tb["rows"], TEXT_W),
                    aligns=["l"] + ["c"] * (len(tb["headers"]) - 1),
                    font_size=8.0 if len(tb["headers"]) >= 8 else 9.0)
            if tb.get("note"):
                w.note(tb["note"])
        elif t == "figure":
            f = rep.figures.get(b["key"])
            if f is None:
                continue
            fig_no += 1
            w.figure(f.path, f.caption)
            if f.note:
                w.note(f.note)

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(p))
    return p


# ================================================================ 渲染器：CSV


def _csv_text(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(list(headers))
    wr.writerows(rows)
    # Excel 认 UTF-8 需要 BOM，否则中文列名是乱码
    return "\ufeff" + buf.getvalue()


def write_csv(rep: Report, path: str | Path) -> Path:
    """一个 zip：指标 + 校核 + 每张明细表，各一个 csv。

    不做"一张大表"：明细表列数不同，拼成一张要么补空列要么截断。
    Excel 里逐个打开也比在一张 700 行的表里翻找省事。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("00_指标.csv", _csv_text(
            ["指标", "数值", "单位", "说明"],
            [[m.label, m.value, m.unit, m.hint] for m in rep.metrics()]))
        z.writestr("01_校核.csv", _csv_text(
            ["校核项", "结论", "实测值", "限值", "单位", "说明"],
            [[c.name, "通过" if c.passed else ("提示" if c.level == "info" else "不通过"),
              "" if c.value is None else f"{c.value:.6g}",
              "" if c.limit is None else f"{c.limit:.6g}",
              c.unit, c.detail] for c in rep.checks()]))
        for i, (key, tb) in enumerate(rep.tables.items(), start=2):
            z.writestr(f"{i:02d}_{key}.csv", _csv_text(tb["headers"], tb["rows"]))
        z.writestr("README.txt",
                   f"{rep.title} · {rep.subtitle}\n"
                   f"结果指纹：{rep.fingerprint}\n生成时间：{rep.generated_at}\n\n"
                   "全部数值取自求解器输出，未做二次力学计算。\n"
                   "指标/校核与界面、Word、HTML 报告完全同源。\n")
    return p


# ================================================================ 一次性出口


def write_all(rep: Report, out_dir: str | Path, stem: str = "report") -> dict[str, Path]:
    """三种文件一次写全，返回 {格式: 路径}。界面按钮直接用这个。

    目标文件被 Word / Excel 打开着时，Windows 直接拒写（PermissionError）。
    "生成失败：Permission denied" 对用户毫无指导意义——他不会想到是自己开着
    那份文档。这里退一步改名写到同目录，并把实际路径返回给界面，用户仍能
    下载到正确内容，且不会覆盖他正在看的那一份。
    """
    d = Path(out_dir)

    def _put(fn: Any, path: Path) -> Path:
        try:
            return fn(rep, path)
        except PermissionError:
            alt = path.with_name(f"{path.stem}_{rep.fingerprint}{path.suffix}")
            return fn(rep, alt)

    return {
        "html": _put(write_html, d / f"{stem}.html"),
        "docx": _put(write_docx, d / f"{stem}.docx"),
        "zip": _put(write_csv, d / f"{stem}_数据表.zip"),
    }


# ---------------------------------------------------------------- 自检


if __name__ == "__main__":
    from cable_stayed_3d import build_cable_stayed_3d
    from grillage_3d import build_box_grillage_3d
    from solver_3d import solve

    cases = []
    cases.append(("grillage3d", build_box_grillage_3d()))
    mc, _info = build_cable_stayed_3d()
    cases.append(("cable_stayed", mc))

    for kind, model in cases:
        res = solve(model, kind=kind, out_dir=f"out/p4_{kind}")
        rep = build_document(res, model, out_dir=f"out/p4_{kind}")
        print(f"=== {kind}: {len(rep.blocks)} 块 / {len(rep.figures)} 图 / "
              f"{len(rep.tables)} 表 / {len(rep.checks())} 校核")
        paths = write_all(rep, f"out/p4_{kind}", stem=f"{kind}_report")
        for k, v in paths.items():
            print(f"    {k:5s} {v}  {v.stat().st_size / 1024:.0f} KB")
