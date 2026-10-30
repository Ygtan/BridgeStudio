"""view_3d.py —— 三维交互视图：把 SolveResult 变成**可旋转的场景**。

为什么要它
----------
`report_3d.figures()` 出的是 PNG：梁格 222 个单元压成一张平面图，斜拉桥的
两排索在立面里叠成一片。更要紧的是**横向分配**——梁格建模的唯一理由是
"荷载在 5 条纵梁之间怎么分"，而平面图恰好把横向压掉了，只剩每条纵梁各自的
曲线，看不出哪条梁吃得多。

这里把「场景」（几何 + 标量场 + 变形）与「渲染器」分开：

    SolveResult + model
        │
        └─ build_scene() → Scene3D ─┬─ to_plotly()    界面：可旋转、悬停读数
                                    └─ figure_axon()  报告：一张轴测 PNG

同一份几何、同一个值、同一个放大倍数，两个出口。界面上转到某个角度看到的
东西，与报告里印出来的那张讲的是同一件事——不是"两套图尽量画得像"。

三条硬规矩
----------
1. **变形是放大后的**。16.8 mm 挠度摊在 110 m 的图上不到一个像素；不放大
   根本看不出来。所以放大倍数写在标题与说明里，不藏在参数里。
2. **云图值取单元值，不在单元内插值**。FEM 只给两端内力，中间值按形函数
   插出来，画成沿单元的渐变会显得比实际更精确。
3. **有正负的场让 0 居中**（蓝负 / 灰白零 / 红正），量值场从 0 起。位移包络
   按「向下极值 / 向上极值」分成两个场，不取 |w|——绝对值会把上拱与下挠
   画成同一种颜色。

为什么悬停点画在中点而不是整条单元上
------------------------------------
plotly 的三维悬停按"最近的点"命中，不按线段。222 条单元共 444 个端点，密集
处相邻单元的端点只差 2.5 m，鼠标指着一根梁会读到旁边那根的内力——这是那种
"看起来有读数、其实是错的"的坑。所以线只画形态（不参与悬停），每个单元在
中点放一个标记，一个单元恰好一个读数。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field as dc_field
from typing import Any, Iterable, Sequence

Vec3 = tuple[float, float, float]

# ---------------------------------------------------------------- 配色

#: 有正负的场：蓝（负）— 灰白（零）— 红（正）。与报告、界面同一套色。
_C_DIV = [[0.0, "#185FA5"], [0.30, "#6E9AC4"], [0.50, "#F2F2F2"],
          [0.70, "#C97B62"], [1.0, "#A32D2D"]]
#: 量值场：浅灰（小）→ 深蓝（大）。单调递增，中间不出现"白色=零"的误读。
_C_SEQ = [[0.0, "#EDEDED"], [0.35, "#B8CCE0"], [0.65, "#5A8FBF"],
          [1.0, "#123E68"]]
_C_SUP = "#A32D2D"
_C_CTRL = "#A32D2D"

#: 构件组 → (线宽, 线色, 标记大小)。线色只表达"这是什么构件"，不表达结果；
#: 结果由标记的颜色表达。
_GROUP_STYLE: dict[str, tuple[float, str, float]] = {
    "纵梁": (5.4, "#3A3A3A", 3.4),
    "主梁": (5.4, "#3A3A3A", 3.4),
    "横梁": (2.6, "#9A9A9A", 2.8),
    "竖构件": (4.4, "#5F5F5F", 3.2),
    "索": (1.5, "#9A9A9A", 2.6),
    "桁架": (2.4, "#7A7A7A", 3.0),
}


# ================================================================ 场


@dataclass(frozen=True)
class Field:
    """一个可着色的标量场。

    `symmetric=True` 表示有正负（0 居中）；`False` 表示量值（从 0 起）。
    `factor` 是**显示单位**的换算系数：内核给 N / N·m / m，读的人要 kN /
    kN·m / mm。换算只在这里做一次，云图、读数、控制值标签就不会三处各错一遍
    （实测过一次：色标写着 kN·m，数值是 7,408,900）。
    """

    key: str
    label: str
    unit: str = ""
    symmetric: bool = True
    note: str = ""
    factor: float = 1.0


def _peak_beam(res: Any, comp: str) -> float:
    lo, hi = f"{comp}Min", f"{comp}Max"
    return max((max(abs(v.get(lo, 0.0)), abs(v.get(hi, 0.0)))
                for v in res.beam.values()), default=0.0)


def _w_pairs(res: Any) -> dict[int, tuple[float, float]]:
    """节点竖向（dof=3）位移 → {节点: (min, max)}，单位 m。"""
    return {n: (lo, hi) for (n, dof), (lo, hi) in res.disp.items() if dof == 3}


def _w_peak(res: Any, side: str = "abs") -> float:
    got = _w_pairs(res).values()
    if side == "abs":
        return max((max(abs(lo), abs(hi)) for lo, hi in got), default=0.0)
    if side == "up":
        return max((hi for _lo, hi in got), default=0.0)
    return min((lo for lo, _hi in got), default=0.0)


def fields(res: Any, model: Any) -> list[Field]:
    """这个结果**能画哪些场**。按结果有的量出清单，不按模型类型硬编码。

    梁格有阶段包络 → 出 My / Vz / N 与位移；成桥态内核不出梁内力 → 这些场
    自动消失，不留一个"全零的云图"让人以为内力为零。
    """
    out: list[Field] = [Field(
        "geometry", "结构几何（不着色）", "",
        note="只画构件与支承，不表示任何结果量。用于核对几何与支承位置。")]

    # ---- 位移 ----
    wsp = _w_pairs(res)
    if wsp:
        two_sided = any(abs(hi - lo) > 1e-12 for lo, hi in wsp.values())
        if res.kind == "cable_stayed":
            lab = "成桥态竖向位移（相对制造线形）"
        else:
            lab = "竖向位移包络（施工阶段）"
        if two_sided:
            out.append(Field("w_min", f"{lab} · 向下极值", "mm", symmetric=False,
                             factor=1000.0,
                             note="每一节点取各阶段的**最小**竖向位移。"
                                  "下挠为负，色标是量值。"))
            if _w_peak(res, "up") > 1e-9:
                out.append(Field("w_max", f"{lab} · 向上极值", "mm",
                                 symmetric=False, factor=1000.0,
                                 note="每一节点取各阶段的**最大**竖向位移"
                                      "（上拱）。与向下极值分开看，"
                                      "取 |w| 会把上拱画成下挠。"))
        else:
            out.append(Field("w", lab, "mm", symmetric=True, factor=1000.0,
                             note="蓝为下挠、红为上拱。"))

    # ---- 梁内力（阶段包络）----
    if res.beam:
        tag = "阶段包络"
        out.append(Field(
            "My", f"梁 My {tag}（取 |·| 大的一端）", "kN·m", True, factor=1e-3,
            note="纵向梁沿 x 走、竖向弯曲绕局部 y，主弯矩在 My 不在 Mz。"
                 "两端异号，故取绝对值大的一端着色，两端值都写在读数里。"))
        if _peak_beam(res, "Vz") > 1e-3:
            out.append(Field("Vz", f"梁 Vz {tag}（取 |·| 大的一端）", "kN", True,
                             factor=1e-3,
                             note="竖向剪力，抗剪配筋与箍筋用。"))
        if _peak_beam(res, "N") > 1e-3:
            out.append(Field("N", f"梁 N {tag}（取 |·| 大的一端）", "kN", True,
                             factor=1e-3,
                             note="轴力。梁格无纵向约束时此项恒为 0，"
                                  "该场不会出现在清单里。"))

    # ---- 基本组合 ----
    try:
        combos = res.combinations() if hasattr(res, "combinations") else []
    except Exception:                                   # noqa: BLE001
        combos = []
    ult = next((c for c in combos if c.spec.key == "ultimate"), None)
    if ult is not None and ult.rows:
        tail = "" if ult.has_live else "（无车道荷载，只有恒载项）"
        out.append(Field(
            "combo_My", "基本组合 My（1.2G + 1.4Q）", "kN·m", True, factor=1e-3,
            note=f"配筋用的**设计内力**，按单元 × i/j 端组合{tail}。"
                 "不是承载力验算结论。"))

    # ---- 索 ----
    if res.cables:
        out.append(Field("T", "成桥索力 T", "kN", symmetric=False, factor=1e-3,
                         note="量值场：越深越大。索的安全系数见校核清单。"))

    # ---- 预抬高 ----
    if res.camber:
        out.append(Field("camber", "制造线形预抬高", "mm", True, factor=1000.0,
                         note="负值表示制造时压低（成桥会上抬）。"))
    return out


def field_by_key(res: Any, model: Any, key: str) -> Field:
    fs = fields(res, model)
    for f in fs:
        if f.key == key:
            return f
    return fs[0]


def default_field(res: Any, model: Any) -> str:
    """报告轴测图默认画哪个场：取"最能说明这次计算在算什么"的那个。"""
    keys = {f.key for f in fields(res, model)}
    for k in ("combo_My", "My", "T", "w_min", "w"):
        if k in keys:
            return k
    return "geometry"


# ================================================================ 几何


def _group_of(a: Any, b: Any, kind: str, res_kind: str) -> str:
    """构件分组。只按几何走向与单元类型判，不猜模型语义。

    - CABLE → 索；TRUSS → 桁架
    - 竖着走的梁（Δz 远大于 Δx/Δy）→ 竖构件（桥塔、墩）
    - Δy 大于 Δx → 横梁；否则 → 纵梁（斜拉桥叫主梁）
    """
    if kind == "CABLE":
        return "索"
    if kind == "TRUSS":
        return "桁架"
    dx, dy, dz = abs(b.x - a.x), abs(b.y - a.y), abs(b.z - a.z)
    if dz > 3.0 * max(dx, dy):
        return "竖构件"
    if dy > dx:
        return "横梁"
    return "主梁" if res_kind == "cable_stayed" else "纵梁"


def support_points(model: Any) -> list[tuple[float, float, float]]:
    """受竖向/横向约束的节点坐标 → [(x, y, z)]。

    与 `report_3d._support_node_positions` 同源（都看 dof 2/3）；梁格的支座
    是横向一排，逐个画，不是一个代表符号。`verify_view_3d` 会断言两者一致。
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


def _nice(v: float) -> float:
    if not math.isfinite(v) or v <= 0:
        return 1.0
    e = 10.0 ** math.floor(math.log10(v))
    for k in (1.0, 1.5, 2.0, 3.0, 5.0, 10.0):
        if v <= k * e:
            return k * e
    return 10.0 * e


def deform_scale_suggest(res: Any, model: Any, target: float = 0.04) -> float:
    """变形放大倍数：让最大位移占模型长度的 `target`，再取整到 1/2/5×10ⁿ。"""
    peak = _w_peak(res)
    xs = [n.x for n in model.nodes] or [0.0]
    span = max(xs) - min(xs)
    if peak <= 1e-12 or span <= 0:
        return 1.0
    return _nice(target * span / peak)


# ================================================================ 场景


@dataclass
class Member:
    elem: int
    group: str
    p0: Vec3
    p1: Vec3
    value: float | None = None
    hover: str = ""

    @property
    def mid(self) -> Vec3:
        a, b = self.p0, self.p1
        return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2)


@dataclass
class Scene3D:
    """一份与渲染器无关的场景：几何、标量场、变形、比例。"""

    field: Field
    members: list[Member] = dc_field(default_factory=list)
    supports: list[Vec3] = dc_field(default_factory=list)
    vmin: float = 0.0
    vmax: float = 0.0
    deform_scale: float = 1.0
    deformed: bool = False
    aspect: dict[str, float] = dc_field(
        default_factory=lambda: {"x": 1.0, "y": 0.2, "z": 0.2})
    control: tuple[int, str, Vec3] | None = None
    notes: list[str] = dc_field(default_factory=list)
    proportion: str = "auto"

    @property
    def has_values(self) -> bool:
        return any(m.value is not None for m in self.members)

    def groups(self) -> dict[str, list[Member]]:
        out: dict[str, list[Member]] = {}
        for m in self.members:
            out.setdefault(m.group, []).append(m)
        return out

    def bbox(self) -> tuple[float, float, float]:
        pts = [p for m in self.members for p in (m.p0, m.p1)]
        pts += list(self.supports)
        if not pts:
            return (1.0, 1.0, 1.0)
        return tuple(max(1e-9, max(p[i] for p in pts) - min(p[i] for p in pts))
                     for i in range(3))  # type: ignore[return-value]


def _fmt(v: float, nd: int = 1) -> str:
    if not math.isfinite(v):
        return "—"
    return f"{v:,.{nd}f}"


def _member_value(res: Any, fld: Field, elem: int,
                  ult: Any = None) -> tuple[float | None, str]:
    """单元在当前场下的标量 + 读数说明。返回 (值, 说明)，**值已是显示单位**。

    说明里写**两端**的值：梁单元两端弯矩通常异号，只报一个数会让读数的人
    以为整根梁都是这个弯矩。
    """
    key, k = fld.key, fld.factor
    if key == "geometry":
        return None, ""
    if key in ("My", "Vz", "N"):
        v = res.beam.get(elem)
        if not v:
            return None, ""
        lo, hi = v.get(f"{key}Min", 0.0), v.get(f"{key}Max", 0.0)
        val = lo if abs(lo) >= abs(hi) else hi
        unit = "kN·m" if key == "My" else "kN"
        return val * k, f"{key} 包络 {_fmt(lo * k)} ~ {_fmt(hi * k)} {unit}"
    if key == "combo_My":
        if ult is None:
            return None, ""
        best: tuple[float, str] | None = None
        for end in ("i", "j"):
            row = ult.rows.get((elem, end))
            if not row:
                continue
            lo, hi = row.get("MyMin", 0.0), row.get("MyMax", 0.0)
            val = lo if abs(lo) >= abs(hi) else hi
            if best is None or abs(val) > abs(best[0]):
                best = (val, end)
        if best is None:
            return None, ""
        return best[0] * k, f"基本组合 My（{best[1]} 端）"
    if key == "T":
        for c in res.cables:
            if int(c.get("elem", -1)) == elem:
                t = float(c.get("T") or 0.0)
                return t * k, "成桥索力 T"
        return None, ""
    if key in ("w", "w_min", "w_max"):
        return None, ""            # 位移是节点量，在 build_scene 里按节点填
    if key == "camber":
        v = res.camber.get(elem)
        if v is None:
            return None, ""
        return v[2] * k, "预抬高（竖向）"
    return None, ""


def _nodal_values(res: Any, fld: Field) -> dict[int, float]:
    """节点量 → {节点: 显示值}。

    位移与预抬高都是**节点**给的（`camber` 的键就是节点号，不是单元号——
    实测过一次：按单元号去查，91 个节点全查空，云图整片灰、却没有报错）。
    """
    k = fld.factor
    if fld.key in ("w", "w_min", "w_max"):
        out: dict[int, float] = {}
        for n, (lo, hi) in _w_pairs(res).items():
            if fld.key == "w_min":
                v = lo
            elif fld.key == "w_max":
                v = hi
            else:
                v = lo if abs(lo) >= abs(hi) else hi
            out[n] = v * k
        return out
    if fld.key == "camber":
        return {n: v[2] * k for n, v in res.camber.items() if v}
    return {}


def _ultimate_combo(res: Any) -> Any:
    """基本组合（1.2G+1.4Q）。只算一次——逐单元各算一遍是 222 次重复计算。"""
    try:
        combos = res.combinations() if hasattr(res, "combinations") else []
    except Exception:                                   # noqa: BLE001
        return None
    return next((c for c in combos if c.spec.key == "ultimate"), None)


def build_scene(res: Any, model: Any, field_key: str = "geometry", *,
                deform: bool = False, scale: float | None = None,
                true_proportion: bool = False) -> Scene3D:
    """把结果装配成场景。纯数据，不 import 任何绘图库。"""
    fld = field_by_key(res, model, field_key)
    nodes = {n.id: n for n in model.nodes}
    kind_of = {t.id: t.kind for t in getattr(model, "etypes", [])}
    wsp = _w_pairs(res)

    # 变形：每个节点取 |w| 大的那一侧。这样跨中下沉、支座上抬就是同一个
    # 形状，而不是"包络上沿/下沿两条曲线"——后者看着像两根梁。
    amp = 0.0
    dz: dict[int, float] = {}
    if deform and wsp:
        amp = float(scale) if scale else deform_scale_suggest(res, model)
        dz = {n: amp * (lo if abs(lo) >= abs(hi) else hi)
              for n, (lo, hi) in wsp.items()}

    def _p(n: Any) -> Vec3:
        return (n.x, n.y, n.z + dz.get(n.id, 0.0))

    nodal = _nodal_values(res, fld)
    ult = _ultimate_combo(res) if fld.key == "combo_My" else None
    nodal_label = {"w": "竖向位移", "w_min": "竖向位移（向下极值）",
                   "w_max": "竖向位移（向上极值）",
                   "camber": "预抬高"}.get(fld.key, "节点值")

    members: list[Member] = []
    for e in model.elements:
        a, b = nodes.get(e.n1), nodes.get(e.n2)
        if a is None or b is None:
            continue
        grp = _group_of(a, b, kind_of.get(e.etype, "BEAM"), res.kind)
        val, extra = _member_value(res, fld, e.id, ult)
        if nodal:
            # 节点量落到单元：取两端中 |·| 大的一侧，与梁内力的取法一致。
            pick = [(nodal[n], n) for n in (e.n1, e.n2) if n in nodal]
            if pick:
                val, node_id = max(pick, key=lambda t: abs(t[0]))
                extra = f"节点 {node_id} {nodal_label}"
        hover = f"单元 {e.id}（{grp}）"
        if val is not None:
            hover += (f"<br>{extra}" if extra else "") + \
                     f"<br>着色值 {_fmt(val)} {fld.unit}"
        hover += f"<br>x {min(a.x, b.x):.1f}–{max(a.x, b.x):.1f} m"
        members.append(Member(e.id, grp, _p(a), _p(b), val, hover))

    vals = [m.value for m in members if m.value is not None]
    vmin = min(vals) if vals else 0.0
    vmax = max(vals) if vals else 0.0
    if fld.symmetric and vals:
        m = max(abs(vmin), abs(vmax))
        vmin, vmax = -m, m
    if vmax - vmin < 1e-12:
        vmax = vmin + 1.0                  # 全零也要有一个可用的色标

    # 控制构件：|值| 最大的那一个。看云图的人第一个想知道的就是它在哪。
    control = None
    if vals:
        cm = max((m for m in members if m.value is not None),
                 key=lambda m: abs(m.value or 0.0))
        control = (cm.elem, f"控制构件 {cm.elem}：{_fmt(cm.value or 0.0)} "
                            f"{fld.unit}", cm.mid)

    sup = [(x, y, z + dz.get(_nearest_node(nodes, (x, y, z)), 0.0))
           for (x, y, z) in support_points(model)]

    scene = Scene3D(field=fld, members=members, supports=sup,
                    vmin=vmin, vmax=vmax,
                    deform_scale=amp if deform else 1.0, deformed=bool(deform),
                    control=control)

    # ---- 比例与说明 ----
    ex, ey, ez = scene.bbox()
    mx = max(ex, ey, ez)
    if true_proportion:
        scene.proportion = "true"
        scene.aspect = {"x": max(ex / mx, 1e-3), "y": max(ey / mx, 1e-3),
                        "z": max(ez / mx, 1e-3)}
        scene.notes.append(
            f"按真实比例显示：纵向 {ex:.0f} m、横向 {ey:.0f} m、竖向 {ez:.0f} m。")
    else:
        scene.proportion = "auto"
        scene.aspect = {"x": 1.0, "y": max(ey / ex, 0.16),
                        "z": max(ez / ex, 0.10)}
        # 只提**真的被压缩**的方向：斜拉桥模型 y 方向没有尺寸，写一句
        # "横向已放大"会让人以为模型有宽度。
        comp = []
        if 1e-6 < ey / ex < 0.16:
            comp.append("横向")
        if 1e-6 < ez / ex < 0.10:
            comp.append("竖向")
        if comp:
            scene.notes.append(
                f"{'/'.join(comp)}已按最小比例放大显示（真实 "
                f"{ex:.0f}×{ey:.0f}×{ez:.0f} m）：{ex:.0f} m 长的桥按真实比例"
                "在屏幕上是一条线。坐标轴刻度仍是真实值。")
    if scene.deformed:
        peak = _w_peak(res)
        scene.notes.append(
            f"变形按 ×{amp:,.0f} 放大绘制，真实最大竖向位移 {peak * 1000:.1f} mm。")
    elif wsp:
        scene.notes.append("未放大变形（勾选「显示变形」可看挠曲形态）。")
    if fld.note:
        scene.notes.append(fld.note)
    return scene


def _nearest_node(nodes: dict[int, Any], p: Vec3) -> int:
    best, bd = -1, float("inf")
    for nid, n in nodes.items():
        d = (n.x - p[0]) ** 2 + (n.y - p[1]) ** 2 + (n.z - p[2]) ** 2
        if d < bd:
            best, bd = nid, d
    return best


# ================================================================ 渲染器：plotly


def to_plotly(scene: Scene3D, *, height: int = 620):
    """场景 → plotly Figure（界面用；plotly 自带 plotly.js，可离线）。"""
    import plotly.graph_objects as go

    fig = go.Figure()
    groups = scene.groups()

    # 线：只画形态，不参与悬停。**一个单元一条 trace**，不用"多点折线 +
    # None 分隔"——三维 scatter 对 None 是否断线各版本行为不一致，赌错的
    # 结果是相邻单元被连成一条斜线（把简支画成连续梁）。200 多条 trace 对
    # plotly 不算什么，JSON 也就几十 KB。
    for grp, ms in groups.items():
        lw, col, _ms = _GROUP_STYLE.get(grp, (2.0, "#888888", 3.0))
        for m in ms:
            fig.add_trace(go.Scatter3d(
                x=[m.p0[0], m.p1[0]], y=[m.p0[1], m.p1[1]],
                z=[m.p0[2], m.p1[2]], mode="lines", name=grp,
                legendgroup=grp, showlegend=False,
                line=dict(color=col, width=lw), hoverinfo="skip"))

    # 点：每个单元一个标记，颜色 = 标量。一个单元恰好一个悬停点。
    if scene.has_values:
        first = True
        for grp, ms in groups.items():
            pts = [m for m in ms if m.value is not None]
            if not pts:
                continue
            _lw, _col, msize = _GROUP_STYLE.get(grp, (2.0, "#888888", 3.0))
            mid = [m.mid for m in pts]
            fig.add_trace(go.Scatter3d(
                x=[p[0] for p in mid], y=[p[1] for p in mid], z=[p[2] for p in mid],
                mode="markers", name=grp, legendgroup=grp, showlegend=False,
                marker=dict(
                    size=msize, opacity=0.95,
                    color=[m.value for m in pts],
                    colorscale=_C_DIV if scene.field.symmetric else _C_SEQ,
                    cmin=scene.vmin, cmax=scene.vmax,
                    showscale=first,
                    colorbar=dict(
                        title=dict(text=f"{scene.field.label}<br>"
                                        f"<span style='font-size:11px'>"
                                        f"{scene.field.unit}</span>", side="top"),
                        thickness=12, len=0.55, x=1.0, xpad=4,
                        tickfont=dict(size=10))),
                text=[m.hover for m in pts],
                hovertemplate="%{text}<extra></extra>"))
            first = False

    # 支座
    if scene.supports:
        fig.add_trace(go.Scatter3d(
            x=[p[0] for p in scene.supports], y=[p[1] for p in scene.supports],
            z=[p[2] for p in scene.supports], mode="markers", name="支承节点",
            marker=dict(symbol="diamond", size=5, color="white",
                        line=dict(color=_C_SUP, width=1)),
            hovertemplate="支承节点<extra></extra>", showlegend=False))

    # 控制构件
    if scene.control is not None:
        eid, label, pos = scene.control
        fig.add_trace(go.Scatter3d(
            x=[pos[0]], y=[pos[1]], z=[pos[2]], mode="markers+text",
            text=[label], textposition="top center",
            textfont=dict(size=10, color=_C_CTRL),
            marker=dict(symbol="circle-open", size=11, color=_C_CTRL,
                        line=dict(color=_C_CTRL, width=2)),
            hovertemplate=label + "<extra></extra>", showlegend=False))

    scene_cfg: dict[str, Any] = dict(
        xaxis=dict(title="顺桥向 x (m)", backgroundcolor="rgba(0,0,0,0)",
                   gridcolor="rgba(140,140,140,0.25)", zeroline=False),
        yaxis=dict(title="横桥向 y (m)", backgroundcolor="rgba(0,0,0,0)",
                   gridcolor="rgba(140,140,140,0.25)", zeroline=False),
        zaxis=dict(title="竖向 z (m)", backgroundcolor="rgba(0,0,0,0)",
                   gridcolor="rgba(140,140,140,0.25)", zeroline=False),
        aspectmode="manual", aspectratio=scene.aspect,
        camera=dict(eye=dict(x=1.5, y=-1.45, z=0.85),
                    center=dict(x=0, y=0, z=0)),
        dragmode="orbit")
    fig.update_layout(
        height=height, margin=dict(l=0, r=0, t=6, b=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False, hovermode="closest", scene=scene_cfg)
    return fig


def to_html_div(scene: Scene3D, *, height: int = 560,
                include_plotlyjs: bool | str = True) -> str:
    """场景 → 一段可直接嵌进 HTML 的 div（报告用）。

    `include_plotlyjs=True` 把 plotly.js 内联进页面（约 4 MB）：离线环境里
    报告要能单独打开，外链 CDN 是不行的。
    """
    return to_plotly(scene, height=height).to_html(
        full_html=False, include_plotlyjs=include_plotlyjs,
        config={"displaylogo": False, "displayModeBar": False, "responsive": True})


def write_html(scene: Scene3D, path: Any, *, title: str = "",
               height: int = 620) -> Any:
    """写一个**能单独发出去**的 HTML：内联 plotly.js，双击就能转。

    这条出口存在的理由很实际：审图的人不一定装了这个程序。给他一个
    4 MB 的 html，比自己截图发过去强——截图只能看一个角度。
    """
    from pathlib import Path as _P

    p = path if isinstance(path, _P) else _P(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    head = f"<h3 style='font-family:sans-serif'>{title}</h3>" if title else ""
    notes = "".join(
        f"<p style='font-family:sans-serif;font-size:13px;color:#59636e;"
        f"margin:2px 0'>{n}</p>" for n in scene.notes)
    body = to_html_div(scene, height=height)
    p.write_text(
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<title>{title or '三维视图'}</title></head><body>"
        f"<div style='max-width:1000px;margin:0 auto'>{head}{body}"
        f"<div style='padding:0 6px 12px'>{notes}</div></div></body></html>",
        encoding="utf-8")
    return p


# ================================================================ 渲染器：matplotlib


def _project(p: Vec3, az: float, el: float) -> tuple[float, float, float]:
    """正交投影：先绕 z 轴转 az，再抬高 el。返回 (屏 x, 屏 y, 深度)。"""
    ca, sa = math.cos(az), math.sin(az)
    ce, se = math.cos(el), math.sin(el)
    xr = p[0] * ca - p[1] * sa
    yr = p[0] * sa + p[1] * ca
    return xr, p[2] * ce - yr * se, yr * ce + p[2] * se


def figure_axon(scene: Scene3D, path: Any, *, title: str = "",
                az_deg: float = -55.0, el_deg: float = 22.0) -> Any:
    """场景 → 轴测 PNG（报告用：Word/PDF 里没法旋转，但要看得出横向分配）。

    画家算法：按深度从远到近画，近的盖住远的。单元只有 200 来个，直接排序
    够用；用不到 z-buffer。

    figsize 按**投影后的实际宽高比**定：一个 110×12 m 的梁格投到 7.6×3.6
    的画幅里，上下会各留一条空白，Word 里就成了"图很小、上下很多白"。
    """
    import matplotlib.pyplot as plt
    from matplotlib.cm import ScalarMappable
    from matplotlib.colors import LinearSegmentedColormap, Normalize
    from matplotlib.lines import Line2D

    plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "SimSun"]
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["font.size"] = 9

    az, el = math.radians(az_deg), math.radians(el_deg)
    # 色标直接由 plotly 那套十六进制色阶生成：同一份色阶，屏幕上和纸上
    # 不会一个偏紫一个偏褐（"RdBu_r" 与本项目的蓝红不是同一个蓝红）。
    cmap = LinearSegmentedColormap.from_list(
        "bridge_div" if scene.field.symmetric else "bridge_seq",
        [c for _p, c in (_C_DIV if scene.field.symmetric else _C_SEQ)],
        N=256)
    norm = Normalize(vmin=scene.vmin, vmax=scene.vmax)

    items = []
    for m in scene.members:
        a, b = _project(m.p0, az, el), _project(m.p1, az, el)
        items.append(((a[2] + b[2]) / 2, m, a, b))
    items.sort(key=lambda t: t[0])            # 远的先画

    xs = [q[0] for _d, _m, a, b in items for q in (a, b)]
    ys = [q[1] for _d, _m, a, b in items for q in (a, b)]
    span_x = (max(xs) - min(xs)) if xs else 1.0
    span_y = (max(ys) - min(ys)) if ys else 1.0
    w_in = 7.6
    h_in = min(4.8, max(1.9, w_in * (span_y / span_x if span_x > 0 else 0.35) + 0.75))

    fig, ax = plt.subplots(figsize=(w_in, h_in))
    for _depth, m, a, b in items:
        lw, col, _ms = _GROUP_STYLE.get(m.group, (2.0, "#888888", 3.0))
        if m.value is None:
            c, w = col, lw * 0.9
        else:
            c, w = cmap(norm(m.value)), lw * 1.25
        ax.plot([a[0], b[0]], [a[1], b[1]], color=c, lw=w,
                solid_capstyle="butt")

    for sp in scene.supports:
        px = _project(sp, az, el)
        ax.plot([px[0]], [px[1]], marker="^", ms=5, mfc="white", mec=_C_SUP,
                mew=0.9, zorder=8)

    if scene.control is not None:
        _eid, label, pos = scene.control
        px = _project(pos, az, el)
        ax.plot([px[0]], [px[1]], marker="o", ms=9, mfc="none", mec=_C_CTRL,
                mew=1.2, zorder=9)
        ax.annotate(label.split("：")[0], (px[0], px[1]),
                    textcoords="offset points", xytext=(6, 6),
                    fontsize=8, color=_C_CTRL, zorder=10)

    if scene.has_values:
        cb = fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=ax,
                          fraction=0.022, pad=0.01)
        cb.set_label(f"{scene.field.label} ({scene.field.unit})", fontsize=8)
        cb.ax.tick_params(labelsize=7.5)

    handles = [Line2D([0], [0], color=_GROUP_STYLE.get(g, (0, "#888"))[1],
                      lw=max(1.2, _GROUP_STYLE.get(g, (1.5, "#888"))[0] * 0.55),
                      label=f"{g}（{len(ms)}）")
               for g, ms in scene.groups().items()]
    if handles:
        # 左上角是这块斜带唯一稳定空着的位置（左下/右上都被桥占着）
        ax.legend(handles=handles, frameon=False, fontsize=7.5, loc="upper left",
                  ncol=min(4, len(handles)),
                  handlelength=1.6, columnspacing=1.0, borderaxespad=0.2)
    ax.set_aspect("equal")
    ax.axis("off")
    if title:
        ax.set_title(title, fontsize=9.5, pad=2)
    fig.tight_layout(pad=0.2)
    path = path if hasattr(path, "parent") else __import__("pathlib").Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path
