"""matplotlib 可视化：截面图、原始模型图、变形图、弯矩图。

每个函数返回一个 matplotlib Figure，供 Streamlit 用 st.pyplot 展示。
"""

from __future__ import annotations

import os

import matplotlib
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Polygon

from model_spec import ModelSpec
from section_lib import draw_section, section_properties

# ---------- 中文字体 ----------
# matplotlib 默认 DejaVu Sans 不含中文，图表标签会出现方框。
# 优先注册系统黑体（SimHei）；找不到时降级，仅警告一次。
_CN_FONT_PATHS = [
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/msyh.ttc",
]
_CN_FONT = next((p for p in _CN_FONT_PATHS if os.path.exists(p)), None)
if _CN_FONT:
    try:
        font_manager.fontManager.addfont(_CN_FONT)
        _cn_name = font_manager.FontProperties(fname=_CN_FONT).get_name()
        matplotlib.rcParams["font.sans-serif"] = [_cn_name, "Microsoft YaHei", "DejaVu Sans"]
        matplotlib.rcParams["font.family"] = "sans-serif"
    except Exception as e:  # pragma: no cover
        print(f"[warn] 中文字体注册失败，图表中文可能显示为方框：{e}")
matplotlib.rcParams["axes.unicode_minus"] = False  # 正常显示负号


def draw_model(spec: ModelSpec) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(9, 3.2))
    L = spec.length
    ax.plot([0, L], [0, 0], color="#185fa5", lw=4, label="梁轴线")
    # 支座
    for s in spec.supports:
        _draw_support(ax, s.position, 0, s.type)
    # 荷载
    for ld in spec.loads:
        if ld.type == "udl":
            start = ld.start if ld.start is not None else 0.0
            end = ld.end if ld.end is not None else L
            _draw_udl(ax, start, end)
        else:
            pos = ld.position if ld.position is not None else L / 2.0
            _draw_point(ax, pos, ld.value)
    ax.set_xlim(-0.5, L + 0.5)
    ax.set_ylim(-1.6, 2.2)
    ax.set_xlabel("x (m)")
    ax.set_yticks([])
    ax.set_title("有限元模型（前处理）", fontsize=12)
    ax.grid(True, axis="x", alpha=0.2)
    return fig


def _draw_support(ax, x, y, stype):
    if stype == "pinned":
        ax.add_patch(Polygon([(x - 0.18, y - 0.35), (x + 0.18, y - 0.35),
                              (x, y)], closed=True, facecolor="#374151", edgecolor="#111827"))
        ax.plot([x - 0.3, x + 0.3], [y - 0.35, y - 0.35], color="#111827", lw=2)
    elif stype == "fixed":
        ax.add_patch(Polygon([(x - 0.12, y + 0.4), (x - 0.12, y - 0.5),
                              (x - 0.32, y - 0.5), (x - 0.32, y + 0.4)],
                             closed=True, facecolor="#374151", edgecolor="#111827"))
        for yy in range(-4, 4):
            ax.plot([x - 0.32, x - 0.12], [yy * 0.12 + 0.02, yy * 0.12 - 0.02],
                    color="#111827", lw=0.8)
    else:  # roller
        ax.add_patch(Polygon([(x - 0.18, y - 0.25), (x + 0.18, y - 0.25),
                              (x, y)], closed=True, facecolor="#374151", edgecolor="#111827"))
        ax.add_patch(plt.Circle((x, y - 0.4), 0.1, facecolor="white", edgecolor="#111827"))
        ax.plot([x - 0.32, x + 0.32], [y - 0.55, y - 0.55], color="#111827", lw=2)


def _draw_udl(ax, start, end):
    y0 = 1.6
    ax.plot([start, end], [y0, y0], color="#b91c1c", lw=1.5)
    n = max(3, int((end - start) / max(0.5, (end - start) / 12)))
    for i in range(n + 1):
        x = start + (end - start) * i / n
        ax.annotate("", xy=(x, 0.05), xytext=(x, y0),
                    arrowprops=dict(arrowstyle="->", color="#b91c1c", lw=1.2))
    ax.text((start + end) / 2, y0 + 0.15, "均布荷载", color="#b91c1c", fontsize=9, ha="center")


def _draw_point(ax, pos, val):
    y0 = 1.9
    ax.annotate("", xy=(pos, 0.05), xytext=(pos, y0),
                arrowprops=dict(arrowstyle="->", color="#b91c1c", lw=1.6))
    ax.text(pos, y0 + 0.05, f"{val:g}kN", color="#b91c1c", fontsize=9, ha="center")


def draw_deformed(spec: ModelSpec, result: dict) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(9, 3.2))
    L = spec.length
    xs = result["xs"]
    defl = result["deflections"]
    max_d = max(abs(d) for d in defl) if defl else 0.0
    scale = (0.18 * L / max_d) if max_d > 1e-9 else 0.0
    y_def = [d * scale for d in defl]
    ax.plot(xs, [0] * len(xs), "--", color="#9ca3af", lw=1.5, label="原始轴线")
    ax.plot(xs, y_def, color="#0f6e56", lw=2.5, label="变形后 (放大显示)")
    for s in spec.supports:
        ax.plot([s.position, s.position], [0, -0.4], color="#374151", lw=2)
    ax.set_xlim(-0.5, L + 0.5)
    ax.set_xlabel("x (m)")
    ax.set_ylabel("挠度 (放大)")
    ax.set_title(f"变形图  最大挠度 = {max_d*1000:.2f} mm", fontsize=12)
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(True, alpha=0.2)
    return fig


def draw_moment(spec: ModelSpec, result: dict) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(9, 3.2))
    L = spec.length
    xs = result["xs_moment"]
    mom = result["moments"]
    max_m = max(abs(m) for m in mom) if mom else 0.0
    ax.fill_between(xs, mom, 0, color="#1d9e75", alpha=0.35)
    ax.plot(xs, mom, color="#0f6e56", lw=2)
    ax.axhline(0, color="#6b7280", lw=0.8)
    ax.set_xlim(-0.5, L + 0.5)
    ax.set_xlabel("x (m)")
    ax.set_ylabel("弯矩 (kN·m)")
    ax.set_title(f"弯矩图  极值 = {max_m/1000:.1f} kN·m", fontsize=12)
    ax.grid(True, alpha=0.2)
    return fig


def draw_section_fig(spec: ModelSpec) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(3.2, 4))
    draw_section(spec.section, ax=ax)
    return fig


# ---------------------------------------------------------------- 桥梁简图
# 桥梁层没有 ModelSpec 那种"一根梁"，画的是节点/单元网络。
# 按单元类型着色，一眼能看出哪部分是梁、哪部分是索或桁架杆。
_BRIDGE_COLORS = {
    "BEAM": "#185fa5",
    "TRUSS": "#1d9e75",
    "CABLE": "#d85a30",
    "STRUT": "#854f0b",
}
_BRIDGE_LW = {"BEAM": 3.2, "TRUSS": 1.6, "CABLE": 1.4, "STRUT": 1.6}


def draw_bridge_geometry(spec) -> plt.Figure:
    """桥梁结构简图（未变形）。spec 需具备 nodes/elements/elem_types/boundaries。"""
    fig, ax = plt.subplots(figsize=(9, 3.8))
    kinds = {t.id: t.kind.value for t in spec.elem_types}
    pos = {n.id: (n.x, n.y) for n in spec.nodes}

    seen: set[str] = set()
    for e in spec.elements:
        k = kinds.get(e.type_id, "BEAM")
        x1, y1 = pos[e.n1]
        x2, y2 = pos[e.n2]
        ax.plot([x1, x2], [y1, y2],
                color=_BRIDGE_COLORS.get(k, "#5f5e5a"),
                lw=_BRIDGE_LW.get(k, 1.6),
                solid_capstyle="round",
                label=f"{k} ×{sum(1 for x in spec.elements if kinds.get(x.type_id) == k)}"
                      if k not in seen else None,
                zorder=2)
        seen.add(k)

    ax.scatter([p[0] for p in pos.values()], [p[1] for p in pos.values()],
               s=5, color="#444441", zorder=3)
    for b in spec.boundaries:
        x, y = pos[b.node]
        ax.plot([x], [y], marker="^", ms=9, mfc="white", mec="#a32d2d",
                mew=1.4, zorder=4)

    ax.set_aspect("equal", adjustable="datalim")
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.set_title(f"{spec.title}　{len(spec.nodes)} 节点 / {len(spec.elements)} 单元",
                 fontsize=12)
    ax.grid(True, alpha=0.2)
    if seen:
        ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
    return fig
