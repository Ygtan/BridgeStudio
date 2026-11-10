"""envelope_3d.py —— 3D 活载包络视图的**唯一口径层**。

为什么单独一层
--------------
两套内核给活载包络的字段名、端标签、分量集合都不一样，但界面云图、
计算书明细表、峰值表、口径说明必须讲同一件事。散在三处各写一遍，迟早
出现「图上有 N、表里没有 N」或「一处说规范最不利、一处说仅本模型车道」。

这里只**读** ``SolveResult`` 已有的字段，不重算力学、不新增 ``.dat`` 段、
不补零（与 `开发使用手册.md`「解析器只做解析 + 派生展示量 + 自检」一致）::

    线性阶段内核  res.live_beam   {(单元, "i"/"j"): {MyMin,MyMax,VzMin,VzMax}}
        ← ``*_summary.txt`` 的 ``live beam envelope``
        ← fem_3d.hpp::compute3DLiveBeamEnvelope（成桥态 + 线弹性影响线）
    索承体系内核  res.girder_live {(单元, 1/2): {NMin,NMax,MyMin,...,VzMax}}
        ← ``[GIRDER_LIVE]``（成桥态切线刚度影响线）

三条口径（图注、表注、章节说明都不许省）
----------------------------------------
1. **不是规范全工况最不利包络**：只覆盖本模型给定的车道定义与一个成桥态
   构型，未枚举温度、支座沉降、风、人群及多可变作用组合；横向多车道靠
   横向分布系数法（车道数 × 折减 × 偏载），不是横向影响面。
2. **施工阶段过程量不与之相加**：``beam`` / ``disp`` 是跨阶段 min/max，
   里面混着未成桥的临时构型；荷载组合只用成桥态恒载 ``final_beam`` /
   ``girder_ends`` 加本层给的出活载包络。
3. **min/max 来自不同加载位置**：角点组合偏保守。本视图是展示量，
   不是设计值，也不是承载力验算结论。

缺数据不摆空表、不画全零云图：见 ``status()`` 的五种分流。
"""

from __future__ import annotations

from typing import Any

#: 本层的「非零」判据：内力给的是 N / N·m，1e-3 以下视为数值零。
#: 与 report_3d._component_peak(..., 1e3) > 1e-3 是同一量级口径。
ZERO_TOL = 1e-3

LINEAR_COMPS = ("My", "Vz")
CABLE_COMPS = ("N", "My", "Vz")
COMP_UNIT = {"N": "kN", "My": "kN·m", "Vz": "kN"}
COMP_SCALE = {"N": 1e3, "My": 1e3, "Vz": 1e3}
COMP_LABEL = {"N": "轴力 N", "My": "竖向弯矩 My", "Vz": "竖向剪力 Vz"}
_SOURCES = (("live_beam", LINEAR_COMPS), ("girder_live", CABLE_COMPS))

SOURCE_LABEL = {
    "live_beam": "`live beam envelope`（成桥态线弹性影响线）",
    "girder_live": "`[GIRDER_LIVE]`（成桥态切线刚度影响线）",
}

SCOPE_NOTE = (
    "本视图是内核按**本模型给定车道定义**做影响线最不利布载得到的 min/max "
    "聚合，**不是规范全工况最不利包络**：未枚举温度、支座沉降、风、人群及"
    "多可变作用组合；横向多车道按横向分布系数法（车道数 × 多车道折减 × "
    "偏载增大）计入，不是横向影响面。min 与 max 来自不同加载位置，"
    "取角点组合偏保守。它是展示量，不是设计值或承载力验算结论。"
)

PROCESS_NOTE = (
    "施工阶段内力/位移包络（`beam force envelope`）是**未成桥临时构型的过程量**，"
    "不与本章活载包络相加；荷载组合只用成桥态恒载 `final_beam` / "
    "`girder_ends` 与本章活载包络。活载包络只覆盖内核实际输出的分量："
    "线性梁格只有 My / Vz，索承体系另有 N，**缺的分量不补零**。"
)

FIELD_NOTE = (
    "活载最不利包络着色：每单元取两端中 |·| 大的一端，悬停读数列出两端各自"
    "的 min/max。只覆盖本模型给定车道定义，**不是规范全工况最不利包络**；"
    "不含温度、支座沉降、风与人群；不是设计值。"
)


def _pairs(res: Any, src: str) -> dict:
    return getattr(res, src, None) or {}


def _ends(src: str) -> tuple:
    return ("i", "j") if src == "live_beam" else (1, 2)


def _comps(src: str) -> tuple[str, ...]:
    return LINEAR_COMPS if src == "live_beam" else CABLE_COMPS


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _extremes(d: dict, comps: tuple[str, ...]) -> list[float]:
    out: list[float] = []
    for v in d.values():
        for c in comps:
            out.append(abs(_f(v.get(c + "Min"))))
            out.append(abs(_f(v.get(c + "Max"))))
    return out


def has_section(res: Any) -> bool:
    """内核是否**回过**活载包络段（哪怕全零）。"""
    return any(bool(_pairs(res, s)) for s, _ in _SOURCES)


def invalid_ranges(res: Any) -> list[str]:
    """min > max 的行：内核数据不自洽，报告要明说而不是照画。"""
    bad: list[str] = []
    for src, comps in _SOURCES:
        for key, v in _pairs(res, src).items():
            for c in comps:
                lo, hi = _f(v.get(c + "Min")), _f(v.get(c + "Max"))
                if lo > hi + 1e-9:
                    bad.append(f"{src}[{key}].{c}=({lo:g},{hi:g})")
    return bad


def source(res: Any) -> str:
    """可用包络的数据源；全零段不算可用，返回空串。"""
    for name, comps in _SOURCES:
        vals = _extremes(_pairs(res, name), comps)
        if any(v > ZERO_TOL for v in vals):
            return name
    return ""


def components(res: Any, src: str | None = None) -> tuple[str, ...]:
    """实际有量的分量。全零分量不出现在云图清单和明细表里。"""
    src = src or source(res)
    if not src:
        return ()
    d = _pairs(res, src)
    return tuple(c for c in _comps(src)
                 if any(abs(_f(v.get(c + "Min"))) > ZERO_TOL
                        or abs(_f(v.get(c + "Max"))) > ZERO_TOL
                        for v in d.values()))


def band_rows(res: Any, src: str | None = None):
    """展平成 ``(单元, 端, dict)``，供表格与图形共用。"""
    src = src or source(res)
    if not src:
        return []
    ends = _ends(src)
    return [(elem, end, _pairs(res, src)[(elem, end)])
            for elem, end in sorted(_pairs(res, src),
                                    key=lambda k: (k[0], ends.index(k[1])
                                                   if k[1] in ends else 9))]


def status(res: Any, model: Any = None) -> tuple[str, str]:
    """``(状态码, 中文说明)``。五条分流，报告章据此决定出不出图/表。

    ``ok`` 才有云图与明细表；其余状态**章节照出**、只给说明，避免整章
    消失让人以为这项根本没做（与模态/屈曲章的 ERROR 段处理同一纪律）。
    """
    bad = invalid_ranges(res)
    if bad:
        return ("invalid_range",
                f"内核回的活载包络存在 min > max（{bad[0]} 等 {len(bad)} 处）："
                "本次不出云图、不给明细表，不把不自洽的数据画成结论。")
    src = source(res)
    if src:
        return ("ok",
                f"活载包络取自 {SOURCE_LABEL[src]}，可用分量："
                f"{'、'.join(components(res, src))}；"
                "已按单元 × 两端出结构云图与明细表。")
    if has_section(res):
        return ("zero_section",
                "内核回了活载包络段，但各分量恒为 0：不出结构云图、不给明细表，"
                "不用一张全零的图冒充「算过了」。请核对车道荷载、影响线与"
                "影响点定义（施工中间态下影响点可能尚未与结构连通）。")
    lanes = getattr(model, "lanes", None) if model is not None else None
    if lanes:
        return ("missing_section",
                "本模型定义了车道荷载（`*LANE_3D`），但内核未回活载包络段："
                "不补零、不猜测，请核对求解器版本与结果段登记。")
    return ("no_lane",
            "本模型未定义车道荷载（`*LANE_3D`），本次没有活载内力包络；"
            "这是**未请求**，不是漏算。定义车道后重算即可出包络视图。")


def default_field(res: Any) -> str:
    """结构云图默认着哪个活载分量；无可用分量返回空串。"""
    cs = components(res)
    return "live_" + cs[0] if cs else ""


def field_specs(res: Any) -> list[dict[str, Any]]:
    """供 ``view_3d.fields()`` 追加的场规格（不 import view_3d，避免环）。"""
    out: list[dict[str, Any]] = []
    for c in components(res):
        out.append({
            "key": "live_" + c,
            "label": f"活载 {c} 最不利包络（本模型车道）",
            "unit": COMP_UNIT[c],
            "symmetric": True,
            "factor": 1e-3,
            "note": FIELD_NOTE + f" 分量：{COMP_LABEL[c]}。",
        })
    return out


def member_value(res: Any, comp: str, elem: int,
                 factor: float = 1.0) -> tuple[float | None, str]:
    """单元在活载包络场下的着色值 + 两端读数说明。值已是显示单位。"""
    src = source(res)
    if not src or comp not in _comps(src):
        return None, ""
    d, k, unit = _pairs(res, src), factor, COMP_UNIT[comp]
    found: list[tuple[float, Any, float, float]] = []
    for end in _ends(src):
        v = d.get((elem, end))
        if not v:
            continue
        lo, hi = _f(v.get(comp + "Min")), _f(v.get(comp + "Max"))
        found.append((lo if abs(lo) >= abs(hi) else hi, end, lo, hi))
    if not found:
        return None, ""
    best = max(found, key=lambda t: abs(t[0]))
    detail = "；".join(
        f"{end} 端 {comp} {lo * k:,.1f} ~ {hi * k:,.1f} {unit}"
        for _val, end, lo, hi in found)
    return best[0] * k, f"活载 {comp} 包络（{detail}）"


def _elem_x(model: Any) -> dict[int, float]:
    nodes = {n.id: n.x for n in getattr(model, "nodes", [])}
    out = {}
    for e in getattr(model, "elements", []):
        a, b = nodes.get(e.n1), nodes.get(e.n2)
        if a is not None and b is not None:
            out[e.id] = (a + b) / 2.0
    return out


def peak_table(res: Any) -> dict[str, Any] | None:
    """跟「3D 包络视图」章走的结论表：每个分量一行，一页看得完。"""
    src = source(res)
    comps = components(res, src)
    if not src or not comps:
        return None
    d = _pairs(res, src)
    rows = []
    for c in comps:
        los = [(v.get(c + "Min", 0.0), k) for k, v in d.items()]
        his = [(v.get(c + "Max", 0.0), k) for k, v in d.items()]
        lo = min(los, key=lambda t: _f(t[0]))[0]
        hi = max(his, key=lambda t: _f(t[0]))[0]
        # 显式逐行取 |·| 最大者，避免 max() 在 tuple 上比较到 dict 键。
        ctrl_val, ctrl_key, ctrl_side = 0.0, None, ""
        for k, v in d.items():
            for side in ("Min", "Max"):
                val = _f(v.get(c + side))
                if abs(val) > abs(ctrl_val):
                    ctrl_val, ctrl_key, ctrl_side = val, k, side.lower()
        s = COMP_SCALE[c]
        rows.append([COMP_LABEL[c], f"{_f(lo) / s:,.2f}", f"{_f(hi) / s:,.2f}",
                     f"{ctrl_val / s:,.2f}",
                     f"单元 {ctrl_key[0]} · {ctrl_key[1]} 端 · {ctrl_side}"
                     if ctrl_key else "—", COMP_UNIT[c]])
    return {
        "title": "活载包络峰值（按分量）",
        "headers": ["分量", "包络 min", "包络 max", "控制 |·|", "出现位置", "单位"],
        "rows": rows,
        # 控制列按 |·| 选取但保留原符号：min / max 来自不同加载位置，
        # 写成不带符号的绝对值会丢掉「是下缘还是上缘控制」这个信息。
        "note": (f"数据源：{SOURCE_LABEL[src]}。"
                 "「控制 |·|」列按绝对值选取控制值，保留 min/max 原符号；"
                 "它不是设计值，也不与云图色标上限（取绝对值）相减。"
                 + SCOPE_NOTE),
    }


def detail_table(res: Any, model: Any) -> dict[str, Any] | None:
    """归文末「明细表」章的逐单元 · 逐端依据表；缺数据返回 None。"""
    src = source(res)
    comps = components(res, src)
    if not src or not comps:
        return None
    x_of = _elem_x(model)
    headers = ["单元", "端", "x (m)"]
    for c in comps:
        headers += [f"{c}Min ({COMP_UNIT[c]})", f"{c}Max ({COMP_UNIT[c]})"]
    rows = []
    for elem, end, v in band_rows(res, src):
        row = [elem, end, f"{x_of.get(elem, float('nan')):.2f}"]
        for c in comps:
            row += [f"{_f(v.get(c + 'Min')) / COMP_SCALE[c]:,.2f}",
                    f"{_f(v.get(c + 'Max')) / COMP_SCALE[c]:,.2f}"]
        rows.append(row)
    return {
        "title": "活载内力包络明细（逐单元 · 逐端）",
        "headers": headers,
        "rows": rows,
        "note": (f"数据源：{SOURCE_LABEL[src]}；只列内核实际输出的分量"
                 f"（{'、'.join(comps)}），缺的不补零。" + PROCESS_NOTE + SCOPE_NOTE),
    }
