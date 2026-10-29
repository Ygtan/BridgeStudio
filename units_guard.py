"""units_guard.py —— 通用单位哨兵（LLM 路径与规则路径共用）
================================================================
为什么要把这段从 llm_parser 里搬出来
------------------------------------
单位哨兵原先写在 `llm_parser._unit_guard()`，只作用于 LLM 返回的 JSON。
后果是：**规则解析完全不经过它**——没配 key、或 LLM 失败回退时，
这类错误会被静默放过。实测过的三个例子：

    "梁高1200宽600"      → h = 1200 m（本意 1200 mm）
    "跨度120000的拱桥"    → span = 120000 m
    "桁高6000"           → depth = 6000 m

它们都不会报错，会一路送进 FEM-Eigen 照算不误，是最危险的静默错误。
现在两边共用同一张阈值表，规则路径也在出口过一遍。

阈值怎么定的
------------
原则：**只在"这个数在工程上不可能"时才动手**，避免把真实大跨结构改成小模型。

- 梁跨度 1000 m：梁单元路径用于常规结构，超过必然是 mm。
- 桥主跨 3000 m：世界最大悬索桥主跨约 2000 m，所以 1200 m 的斜拉桥
  必须原样保留，只有到 3000 m 以上才判定为 mm 误写。
- 截面尺寸 20 m：最大的箱梁梁高也在 15 m 上下。
- 桁高 60 m：500 m 级桁架桥的桁高约 40 m，留出余量。
- 矢高 500 m：1000 m 级拱桥矢高可达 200 m，阈值必须够宽。
- 均布荷载 1e4 kN/m、集中力 1e5 kN：超过就是 N 误写成 kN。

调用方式
--------
    notes: list[str] = []
    guard_units(params, "arch", notes)      # 就地修正，返回 notes

    spec2 = guard_spec(spec, notes)         # 梁侧：ModelSpec 进 ModelSpec 出
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------- 阈值
BEAM_MAX_LENGTH = 1000.0     # 梁单元路径的跨度上限
BEAM_MAX_SECTION = 20.0      # 梁截面尺寸上限（h/b/tw/tf 共用）
BEAM_MAX_UDL = 1e4           # kN/m，超过即 N/m 误写
BEAM_MAX_POINT = 1e5         # kN，超过即 N 误写
BRIDGE_MAX_SPAN = 3000.0     # 桥主跨上限（世界最大悬索桥约 2000 m，留余量）
BRIDGE_MAX_RISE = 500.0      # 1000 m 级拱桥矢高可达 200 m，阈值必须够宽
BRIDGE_MAX_DEPTH = 60.0      # 500 m 级桁架桥桁高约 40 m
BRIDGE_MAX_WIDTH = 60.0      # 宽幅桥可达 50 m
BRIDGE_MAX_NODE = 1e5        # 节点荷载 kN

# ---------------------------------------------------------------- 阈值表
# 字段 → (上限, 换算系数, 疑似单位, 中文名, 换算后单位)
_SCALARS: dict[str, dict[str, tuple[float, float, str, str, str]]] = {
    "arch": {
        "span": (BRIDGE_MAX_SPAN, 1000.0, "mm", "跨度", "m"),
        "rise": (BRIDGE_MAX_RISE, 1000.0, "mm", "矢高", "m"),
        "udl": (BEAM_MAX_UDL, 1000.0, "N/m", "均布荷载", "kN/m"),
        "point": (BEAM_MAX_POINT, 1000.0, "N", "集中力", "kN"),
    },
    "truss": {
        "span": (BRIDGE_MAX_SPAN, 1000.0, "mm", "跨度", "m"),
        "depth": (BRIDGE_MAX_DEPTH, 1000.0, "mm", "桁高", "m"),
        "node_load": (BRIDGE_MAX_NODE, 1000.0, "N", "节点荷载", "kN"),
    },
    "beam_bridge": {
        "span": (BRIDGE_MAX_SPAN, 1000.0, "mm", "跨度", "m"),
        "h": (BEAM_MAX_SECTION, 1000.0, "mm", "梁高", "m"),
        "b": (BRIDGE_MAX_WIDTH, 1000.0, "mm", "桥宽", "m"),
        "udl": (BEAM_MAX_UDL, 1000.0, "N/m", "均布荷载", "kN/m"),
        "point": (BEAM_MAX_POINT, 1000.0, "N", "集中力", "kN"),
    },
    # 三维：参数名与二维完全不同（sp1/sp2、Lm/Ls、Ht），必须单独登记。
    # 漏登记的后果是静默的——`_SCALARS.get(kind, {})` 对未知 kind 是空操作，
    # 于是 "主跨200000" 会一路建成 200 公里的斜拉桥并照算不误。
    "grillage3d": {
        "sp1": (BRIDGE_MAX_SPAN, 1000.0, "mm", "边跨", "m"),
        "sp2": (BRIDGE_MAX_SPAN, 1000.0, "mm", "中跨", "m"),
        "B": (BRIDGE_MAX_WIDTH, 1000.0, "mm", "箱宽", "m"),
        "h": (BEAM_MAX_SECTION, 1000.0, "mm", "梁高", "m"),
        "tf": (BEAM_MAX_SECTION, 1000.0, "mm", "顶/底板厚", "m"),
        "tw": (BEAM_MAX_SECTION, 1000.0, "mm", "腹板厚", "m"),
        "panel": (BEAM_MAX_SECTION, 1000.0, "mm", "横梁间距", "m"),
        "q": (BEAM_MAX_UDL, 1000.0, "N/m", "桥面均布荷载", "kN/m"),
    },
    "cable_stayed": {
        "Lm": (BRIDGE_MAX_SPAN, 1000.0, "mm", "主跨", "m"),
        "Ls": (BRIDGE_MAX_SPAN, 1000.0, "mm", "边跨", "m"),
        "Ht": (BRIDGE_MAX_SPAN, 1000.0, "mm", "塔高", "m"),
        "h": (BEAM_MAX_SECTION, 1000.0, "mm", "主梁高", "m"),
        "B": (BRIDGE_MAX_WIDTH, 1000.0, "mm", "主梁宽", "m"),
        "q": (BEAM_MAX_UDL, 1000.0, "N/m", "桥面恒载", "kN/m"),
    },
}


def _num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def guard_bridge_span(span: float, notes: list[str]) -> float:
    """单独纠正跨度。

    必须在派生默认值**之前**调用：矢高 span/5、桁高 span/8、梁高 span/20
    都由跨度推导，若跨度还是 120000，会先推出"矢高 24000 m"这种荒谬默认值，
    再被哨兵二次修正——提示信息会自相矛盾（先说 24000 m，又说已按 24 m）。
    """
    if _num(span) and abs(span) > BRIDGE_MAX_SPAN:
        fixed = span / 1000.0
        notes.append(f"跨度 {span:g} 疑为 mm，已按 {fixed:g} m 处理")
        return fixed
    return span


def _convert(holder: dict, key: str, limit: float, scale: float,
             unit: str, label: str, target: str, notes: list[str]) -> None:
    v = holder.get(key)
    if not _num(v) or abs(v) <= limit:
        return
    holder[key] = v / scale
    notes.append(f"{label} {v:g} 疑为 {unit}，已按 {v / scale:g} {target} 处理")


def _guard_beam(data: dict, notes: list[str]) -> None:
    """梁侧：length + section(h/b/tw/tf) + loads[].value。

    与迁移前 `llm_parser._unit_guard()` 的行为逐字一致，
    保证 llm_parser --selftest ⑤ 仍然通过。
    """
    _convert(data, "length", BEAM_MAX_LENGTH, 1000.0, "mm", "跨度", "m", notes)

    sec = data.get("section")
    if isinstance(sec, dict):
        for k in ("h", "b", "tw", "tf"):
            _convert(sec, k, BEAM_MAX_SECTION, 1000.0, "mm", f"截面 {k}", "m", notes)

    for ld in data.get("loads", []) or []:
        if not isinstance(ld, dict):
            continue
        typ = ld.get("type")
        if typ == "udl":
            _convert(ld, "value", BEAM_MAX_UDL, 1000.0, "N/m", "均布荷载", "kN/m", notes)
        elif typ == "point":
            _convert(ld, "value", BEAM_MAX_POINT, 1000.0, "N", "集中力", "kN", notes)


def _guard_scalars(data: dict, kind: str, notes: list[str]) -> None:
    """桥侧：按 kind 的阈值表逐字段检查（扁平参数，无嵌套）。"""
    for key, (limit, scale, unit, label, target) in _SCALARS.get(kind, {}).items():
        _convert(data, key, limit, scale, unit, label, target, notes)


def guard_units(data: dict, kind: str, notes: list[str] | None = None) -> list[str]:
    """就地修正 `data` 里的单位误写，返回提示列表。

    kind ∈ {"beam", "arch", "truss", "beam_bridge", "grillage3d", "cable_stayed"}。
    只做"量级明显不可能"的换算，不做业务范围校验（那是 validate() 的事）。
    """
    notes = notes if notes is not None else []
    if kind == "beam":
        _guard_beam(data, notes)
    else:
        _guard_scalars(data, kind, notes)
    return notes


def guard_spec(spec, notes: list[str]) -> Any:
    """梁侧便捷入口：ModelSpec 进、ModelSpec 出（无修正时原样返回）。

    规则解析（parse_full / modify）拿到的是对象而不是 dict，
    所以需要这层转换。没有触发修正时不重建对象，避免无谓开销。
    """
    if spec is None:
        return spec
    data = spec.model_dump()
    before = len(notes)
    guard_units(data, "beam", notes)
    if len(notes) == before:
        return spec
    return type(spec)(**data)


if __name__ == "__main__":            # 快速自检
    cases = [
        ("beam", {"length": 30000, "section": {"h": 1200, "b": 600},
                  "loads": [{"type": "udl", "value": 20000}]}),
        ("arch", {"span": 120000, "rise": 20000, "udl": 30}),
        ("truss", {"span": 60, "depth": 6000, "node_load": 100}),
        ("beam_bridge", {"span": 1200, "h": 2.0, "b": 8000}),
    ]
    for kind, data in cases:
        ns: list[str] = []
        guard_units(data, kind, ns)
        print(f"[{kind}] {data}")
        for n in ns:
            print(f"         · {n}")
