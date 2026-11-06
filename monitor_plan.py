"""monitor_plan.py —— 把已有求解结果写成施工监控细则。

不新增单元，也不再算一遍力学。测点取自模型几何，理论值取自
SolveResult 里已经有的位移、索力和架设矢高。允许偏差是本程序拟定的
施工控制带宽，条目里写明，不写成规范限值。
"""

from __future__ import annotations

from typing import Any

# 主梁标高：绝对 20 mm 与跨径的 1/5000 取较大者。
ELEV_ABS_MM = 20.0
ELEV_SPAN_DENOM = 5000.0
# 塔顶顺桥向偏位：塔高的 1/3000，且不小于 20 mm。
TOWER_ABS_MM = 20.0
TOWER_HEIGHT_DENOM = 3000.0
# 索力相对偏差。
CABLE_TOL = 0.05


def monitoring_table(res: Any, model: Any,
                     params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """返回报告表。没有位移也没有索力时返回 None。"""
    del params  # 细则只读结果和几何，不读界面上可能过期的参数
    rows = _rows(res, model)
    if not rows:
        return None
    return {
        "title": "施工监控测点与允许偏差",
        "note": "允许偏差是本程序拟定的施工控制带宽，不是规范限值。"
                "标高或索力超限时，只回算条目里写的那一个参数，然后重算相邻量；"
                "不要用一个标高同时反推全部索力。",
        "headers": ["测点", "理论值", "允许偏差", "读取阶段", "超限时回算"],
        "rows": rows,
    }


def _rows(res: Any, model: Any) -> list[list[str]]:
    nodes = {n.id: n for n in getattr(model, "nodes", []) or []}
    if not nodes:
        return []
    when = _read_stage(res)
    out: list[list[str]] = []
    deck = _deck_nodes(model, nodes)
    has_cable = bool(getattr(res, "cables", None) or getattr(res, "stage_cables", None)
                     or getattr(res, "hangers", None) or getattr(res, "main_cable", None))
    if len(deck) >= 2:
        xs = [nodes[i].x for i in deck]
        span = max(xs) - min(xs)
        band = max(ELEV_ABS_MM, span * 1000.0 / ELEV_SPAN_DENOM)
        elev_action = ("回算邻近索的张拉索力或无应力长度 L0"
                       if has_cable else "回算该断面的支架标高或预应力张拉")
        for label, nid in _stations(deck, nodes):
            uz = _mm(res, nid, 3)
            if uz is None:
                continue
            out.append([
                f"{label}标高（节点 {nid}）",
                f"{uz:.1f} mm",
                f"±{band:.0f} mm",
                when,
                elev_action,
            ])
    for label, nid, height in _tower_tops(model, nodes):
        ux = _mm(res, nid, 1)
        if ux is None:
            continue
        band = max(TOWER_ABS_MM, height * 1000.0 / TOWER_HEIGHT_DENOM)
        out.append([
            f"{label}顺桥向偏位（节点 {nid}）",
            f"{ux:.1f} mm",
            f"±{band:.0f} mm",
            when,
            "回算该塔两侧索力差，不要同时改全部索",
        ])
    out.extend(_cable_rows(res, when))
    out.extend(_erection_rows(res, deck, nodes))
    return out


def _read_stage(res: Any) -> str:
    if getattr(res, "erection", None):
        return "各架设阶段及铺装后"
    if getattr(res, "stage_cables", None):
        return "各施工阶段末及末阶段"
    return "成桥态"


def _mm(res: Any, nid: int, dof: int) -> float | None:
    final = getattr(res, "final_disp", None) or {}
    if (nid, dof) in final:
        return float(final[(nid, dof)]) * 1000.0
    pair = (getattr(res, "disp", None) or {}).get((nid, dof))
    if not pair:
        return None
    lo, hi = float(pair[0]), float(pair[1])
    return (lo if abs(lo) >= abs(hi) else hi) * 1000.0


def _kind_of(model: Any) -> dict[int, str]:
    return {t.id: str(t.kind) for t in getattr(model, "etypes", []) or []}


def _deck_nodes(model: Any, nodes: dict[int, Any]) -> list[int]:
    """顺桥向梁上的节点，同一 x 只留最靠近桥轴的那一个。"""
    kinds = _kind_of(model)
    picked: dict[float, int] = {}
    for e in getattr(model, "elements", []) or []:
        if kinds.get(e.etype) not in ("BEAM", "BEAM7"):
            continue
        a, b = nodes.get(e.n1), nodes.get(e.n2)
        if a is None or b is None:
            continue
        dx, dy, dz = b.x - a.x, b.y - a.y, b.z - a.z
        if abs(dx) + 1e-9 < max(abs(dy), abs(dz)):
            continue
        for n in (a, b):
            key = round(n.x, 3)
            old = picked.get(key)
            if old is None or abs(n.y) < abs(nodes[old].y):
                picked[key] = n.id
    return [picked[k] for k in sorted(picked)]


def _stations(deck: list[int], nodes: dict[int, Any]) -> list[tuple[str, int]]:
    xs = [nodes[i].x for i in deck]
    lo, hi = xs[0], xs[-1]
    span = hi - lo
    if span <= 1e-9:
        return [("跨中", deck[len(deck) // 2])]
    targets = (
        ("梁端", 0.0),
        ("L/4", 0.25),
        ("跨中", 0.5),
        ("3L/4", 0.75),
        ("另一梁端", 1.0),
    )
    out: list[tuple[str, int]] = []
    used: set[int] = set()
    for label, frac in targets:
        x = lo + frac * span
        nid = min(deck, key=lambda i: abs(nodes[i].x - x))
        if nid in used:
            continue
        if used and min(abs(nodes[nid].x - nodes[j].x) for j in used) < max(1.0, 0.02 * span):
            continue
        used.add(nid)
        out.append((label, nid))
    return out


def _tower_tops(model: Any, nodes: dict[int, Any]) -> list[tuple[str, int, float]]:
    kinds = _kind_of(model)
    groups: dict[int, list[int]] = {}
    for e in getattr(model, "elements", []) or []:
        if kinds.get(e.etype) not in ("BEAM", "BEAM7"):
            continue
        a, b = nodes.get(e.n1), nodes.get(e.n2)
        if a is None or b is None:
            continue
        dx, dy, dz = b.x - a.x, b.y - a.y, b.z - a.z
        if abs(dz) + 1e-9 < max(abs(dx), abs(dy)):
            continue
        key = int(round((a.x + b.x) / 2.0))
        groups.setdefault(key, []).extend((e.n1, e.n2))
    out: list[tuple[str, int, float]] = []
    for i, key in enumerate(sorted(groups), start=1):
        ids = list(dict.fromkeys(groups[key]))
        top = max(ids, key=lambda n: nodes[n].z)
        base_z = min(nodes[n].z for n in ids)
        height = nodes[top].z - base_z
        if height < 5.0:
            continue
        name = "塔顶" if len(groups) == 1 else f"{i} 号塔顶"
        out.append((name, top, height))
    return out


def _cable_rows(res: Any, when: str) -> list[list[str]]:
    rows: list[list[str]] = []
    pairs = _tension_pairs(res)
    if not pairs:
        return rows
    lo = min(pairs, key=lambda it: it[1])
    hi = max(pairs, key=lambda it: it[1])
    for tag, eid, force in (("最大索力", hi[0], hi[1]), ("最小索力", lo[0], lo[1])):
        if force <= 0:
            continue
        rows.append([
            f"{tag}（索 {eid}）",
            f"{force / 1e3:.1f} kN",
            f"±{CABLE_TOL * 100:.0f}%",
            when,
            "只调整该索的张拉指令或无应力长度 L0，再重算相邻索",
        ])
    return rows


def _tension_pairs(res: Any) -> list[tuple[int, float]]:
    cables = list(getattr(res, "cables", None) or [])
    if not cables:
        cables = list(getattr(res, "hangers", None) or [])
    if not cables:
        cables = list(getattr(res, "main_cable", None) or [])
    pairs = [(int(c["elem"]), float(c.get("T") or 0.0))
             for c in cables if c.get("elem") is not None and float(c.get("T") or 0.0) > 0]
    if pairs:
        return pairs
    stage = getattr(res, "stage_cables", None) or {}
    if not stage:
        return []
    last = max(s for s, _ in stage)
    out = []
    for (s, eid), st in stage.items():
        if s == last and float(st.get("T") or 0.0) > 0:
            out.append((int(eid), float(st["T"])))
    return out


def _erection_rows(res: Any, deck: list[int], nodes: dict[int, Any]) -> list[list[str]]:
    stages = [r for r in (getattr(res, "erection", None) or []) if "sag" in r]
    if len(stages) < 2 or len(deck) < 2:
        return []
    span = max(nodes[i].x for i in deck) - min(nodes[i].x for i in deck)
    band = max(0.05, span / ELEV_SPAN_DENOM)
    first, last = stages[0], stages[-1]
    return [[
        f"主缆矢高（{first.get('name', '空缆')} → {last.get('name', '铺装')}）",
        f"{float(first['sag']):.2f} m → {float(last['sag']):.2f} m",
        f"±{band:.2f} m",
        "各架设阶段",
        "回算主缆无应力长度，不单独改一根吊索来凑矢高",
    ]]
