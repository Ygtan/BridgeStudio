"""combination.py —— JTG D60-2015 荷载组合：把两个工况合成设计值。

为什么单独一层
--------------
内核只回答"某个工况单独作用时效应是多少"：恒载（成桥态）S_G、活载（影响
线包络）S_Q。规范要的是**组合**，而且不同验算用不同组合——承载力看基本
组合，裂缝与短期挠度看频遇组合，长期变形看准永久组合。这三个式子不该
散落在报告、界面、CSV 各处各写一遍。

    S_ud = γ_G·S_G + γ_Q·S_Q          基本组合（承载力）
    S_fd = S_G + ψ1·S_Q               频遇组合（ψ1 = 0.7）
    S_qd = S_G + ψ2·S_Q               准永久组合（ψ2 = 0.4）

三条铁律
--------
1. **同一构型**。S_G 取成桥态单值（`res.final_beam`，由各施工阶段的内力
   **累加**得到），不取阶段包络：
   逐跨施工的包络里含"某跨还没浇完"的悬臂临时内力，与运营期活载相加
   没有物理意义。施工阶段验算继续用包络（`res.beam`），那是另一个问题。
2. **同一截面**。按 (单元, 端) 组合，不按单元：两端弯矩异号，先外包再
   相加会把 i 端的恒载和 j 端的活载凑到一起。
3. **缺项就说缺**。模型没定义车道荷载时只出恒载项，并标 `has_live=False`；
   界面与报告必须把这一条显式说出来，不能让读者以为看到的是组合值。

不做什么
--------
不算承载力。截面抗弯/抗剪承载力需要配筋，本程序不建钢筋，也没有材料
强度设计值。这里给的是**设计内力包络**——设计院拿去配筋的输入，不是
"验算通过"的结论。想要结论，得把 Mu 接进来。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------- 组合定义


@dataclass(frozen=True)
class ComboSpec:
    """一种组合。系数是数据不是逻辑：报告要逐项列出时直接从这取。"""

    key: str
    name: str
    gamma_g: float
    gamma_q: float
    expr: str
    use: str
    basis: str
    level: str = "warn"


#: 基本组合。γ_G=1.2 是"永久作用对结构不利"时的值；γ_Q1=1.4 对应汽车荷载
#: （含冲击力）。若某工况永久作用对结构有利，γ_G 应取 1.0——本程序只有
#: 一个恒载工况且总是向下，故不区分。
ULTIMATE = ComboSpec(
    key="ultimate", name="基本组合（承载力）",
    gamma_g=1.2, gamma_q=1.4,
    expr="1.2·S_G + 1.4·S_Q",
    use="正截面、斜截面承载力验算（配筋设计的控制内力）",
    basis="JTG D60-2015 表 4.1.5-1（γ_G=1.2）、表 4.1.5-2（γ_Q1=1.4）",
    level="error")

FREQUENT = ComboSpec(
    key="frequent", name="频遇组合（正常使用）",
    gamma_g=1.0, gamma_q=0.7,
    expr="S_G + 0.7·S_Q",
    use="裂缝宽度、短期挠度",
    basis="JTG D60-2015 表 4.1.7（汽车荷载频遇值系数 ψ1=0.7）",
    level="warn")

QUASI = ComboSpec(
    key="quasi", name="准永久组合",
    gamma_g=1.0, gamma_q=0.4,
    expr="S_G + 0.4·S_Q",
    use="长期变形、持续荷载下的应力状态",
    basis="JTG D60-2015 表 4.1.7（汽车荷载准永久值系数 ψ2=0.4）",
    level="info")

ALL: tuple[ComboSpec, ...] = (ULTIMATE, FREQUENT, QUASI)


# ---------------------------------------------------------------- 结果


@dataclass
class BeamCombo:
    """一种组合下的梁端内力。

    `rows[(单元, 端)] = {"MyMin","MyMax","VzMin","VzMax"}`（N·m / N）。
    弯矩保留 min/max 两侧：活载本身是包络，两端极值对应不同的车辆位置。
    """

    spec: ComboSpec
    rows: dict[tuple[int, str], dict[str, float]] = field(default_factory=dict)
    has_live: bool = True

    def worst(self, comp: str = "My") -> tuple[tuple[int, str], float] | None:
        """|分量| 最大的截面 → ((单元, 端), 值)。"""
        lo, hi = f"{comp}Min", f"{comp}Max"
        best: tuple[tuple[int, str], float] | None = None
        for key, v in self.rows.items():
            for k in (lo, hi):
                val = v.get(k)
                if val is None:
                    continue
                if best is None or abs(val) > abs(best[1]):
                    best = (key, val)
        return best

    def unit_table(self, limit: int = 0) -> list[list[Any]]:
        """每单元一行：取两端中更不利的一侧。

        表里给 222×2 行没人看，控制信息是"这个单元的最大正/负弯矩"，所以
        把两端收成一行，并写出控制端。`limit` 为 0 表示全给。
        """
        by_elem: dict[int, dict[str, Any]] = {}
        for (eid, end), v in self.rows.items():
            slot = by_elem.setdefault(eid, {"MyMin": 0.0, "MyMax": 0.0,
                                            "VzMin": 0.0, "VzMax": 0.0,
                                            "MyMin_end": "i", "MyMax_end": "i"})
            if v.get("MyMin", 0.0) < slot["MyMin"]:
                slot["MyMin"] = v["MyMin"]
                slot["MyMin_end"] = end
            if v.get("MyMax", 0.0) > slot["MyMax"]:
                slot["MyMax"] = v["MyMax"]
                slot["MyMax_end"] = end
            if v.get("VzMin", 0.0) < slot["VzMin"]:
                slot["VzMin"] = v["VzMin"]
            if v.get("VzMax", 0.0) > slot["VzMax"]:
                slot["VzMax"] = v["VzMax"]
        items = sorted(by_elem.items())
        if limit and len(items) > limit:
            # 按 |My| 取前 limit 个，但顺序仍按单元号——施工方是按单元号找的
            keep = {e for e, _ in sorted(
                items, key=lambda kv: -max(abs(kv[1]["MyMin"]), abs(kv[1]["MyMax"])))[:limit]}
            items = [(e, v) for e, v in items if e in keep]
        return [[e, v["MyMin_end"], v["MyMin"], v["MyMax_end"], v["MyMax"],
                 v["VzMin"], v["VzMax"]] for e, v in items]


@dataclass
class DispCombo:
    """位移组合（影响点处的一个标量）。

    荷载组合对位移的意义有限：JTG D60 4.3.2 的挠度限值判的是**汽车荷载
    （不计冲击）本身**产生的挠度 ≤ L/600，不是组合值——恒载挠度由预拱度
    消除，不参与那条判定。所以这里给出组合值只作参考，判定仍用单项活载。
    """

    spec: ComboSpec
    value: float                      # m，向下为负
    sg: float                         # 恒载分量
    sq: float                         # 活载分量


# ---------------------------------------------------------------- 组合计算


def beam_combinations(res: Any) -> list[BeamCombo]:
    """按 (单元, 端) 逐截面组合。内核没有成桥态内力时返回空表。"""
    final = getattr(res, "final_beam", None) or {}
    live = getattr(res, "live_beam", None) or {}
    if not final:
        return []
    has_live = bool(live)
    out: list[BeamCombo] = []
    for spec in ALL:
        rows: dict[tuple[int, str], dict[str, float]] = {}
        for key, g in final.items():
            q = live.get(key) or {}
            # G 是单值；Q 是影响线包络，两侧分别叠加。同截面同分量相加，
            # 不跨端、不跨分量。
            rows[key] = {
                "MyMin": spec.gamma_g * g.get("My", 0.0) + spec.gamma_q * q.get("MyMin", 0.0),
                "MyMax": spec.gamma_g * g.get("My", 0.0) + spec.gamma_q * q.get("MyMax", 0.0),
                "VzMin": spec.gamma_g * g.get("Vz", 0.0) + spec.gamma_q * q.get("VzMin", 0.0),
                "VzMax": spec.gamma_g * g.get("Vz", 0.0) + spec.gamma_q * q.get("VzMax", 0.0),
            }
        out.append(BeamCombo(spec=spec, rows=rows, has_live=has_live))
    return out


def live_disp_at_final(res: Any) -> float | None:
    """成桥态（最后施工阶段）影响点活载位移 → m，取绝对值大的一侧。

    `res.live` 是各阶段的包络；运营期对应最后一个阶段。前几阶段的模型是
    残缺的（未激活跨的节点被约束），那里的活载位移恒为 0，混进包络会把
    最不利值抹平。
    """
    live = getattr(res, "live", None) or {}
    if not live:
        return None
    hi, lo = live[max(live)]
    return lo if abs(lo) >= abs(hi) else hi


def disp_combinations(res: Any) -> list[DispCombo]:
    """影响点处的位移组合。缺成桥态位移或活载时返回空表。"""
    final_disp = getattr(res, "final_disp", None) or {}
    if not final_disp:
        return []
    # 影响点 = 竖向位移绝对值最大的节点（与 max_vertical_deflection 同判据）
    best: tuple[float, float] | None = None       # (|u|, u)
    for (nid, dof), u in final_disp.items():
        if dof != 3:
            continue
        if best is None or abs(u) > best[0]:
            best = (abs(u), u)
    if best is None:
        return []
    sg = best[1]
    sq = live_disp_at_final(res) or 0.0
    return [DispCombo(spec=s, value=s.gamma_g * sg + s.gamma_q * sq, sg=sg, sq=sq)
            for s in ALL]


# ---------------------------------------------------------------- 报告物料


def coefficient_table() -> dict[str, Any]:
    """系数表：报告第一章就要有，读者才能自己复核。"""
    return {
        "title": "荷载组合系数（JTG D60-2015）",
        "note": "γ_G 为永久作用分项系数，γ_Q 为汽车荷载（含冲击力）分项系数；"
                "频遇、准永久组合中的 0.7 / 0.4 是汽车荷载的频遇值系数与"
                "准永久值系数。",
        "headers": ["组合", "γ_G", "γ_Q", "表达式", "用途", "依据"],
        "rows": [[s.name, f"{s.gamma_g:.1f}", f"{s.gamma_q:.1f}",
                  s.expr, s.use, s.basis] for s in ALL],
    }


def beam_table(combo: BeamCombo, limit: int = 0) -> dict[str, Any]:
    """某一组合的梁端内力表（每单元一行，控制端写在列里）。"""
    rows = combo.unit_table(limit=limit)
    return {
        "title": f"{combo.spec.name}：梁端弯矩与剪力",
        "note": ("弯矩以 kN·m、剪力以 kN 计；「端」列指出该极值出现在单元的 "
                 "i 端还是 j 端。活载侧取自 JTG D60 车道荷载影响线包络"
                 "（一辆车队沿桥逐位置布载后取最不利）。"
                 + ("模型未定义车道荷载，表中只有恒载项。" if not combo.has_live else "")),
        "headers": ["单元", "MyMin 端", "MyMin (kN·m)", "MyMax 端",
                    "MyMax (kN·m)", "VzMin (kN)", "VzMax (kN)"],
        "rows": [[r[0], r[1], round(r[2] / 1e3, 1), r[3], round(r[4] / 1e3, 1),
                  round(r[5] / 1e3, 1), round(r[6] / 1e3, 1)] for r in rows],
    }


def summary_table(combos: list[BeamCombo], disps: list[DispCombo]) -> dict[str, Any]:
    """一页看清三种组合的控制值——报告里先给这张，再给明细。"""
    rows: list[list[Any]] = []
    by_key = {c.spec.key: c for c in combos}
    dby_key = {d.spec.key: d for d in disps}
    for spec in ALL:
        c = by_key.get(spec.key)
        d = dby_key.get(spec.key)
        if c is None:
            rows.append([spec.name, spec.expr, "—", "—", "—"])
            continue
        w = c.worst("My")
        cell = "—" if w is None else f"{w[1] / 1e3:,.0f}（单元 {w[0][0]}·{w[0][1]} 端）"
        dv = "—" if d is None else f"{d.value * 1000:.2f}"
        rows.append([spec.name, spec.expr, cell, dv, spec.use])
    note = ("弯矩取全桥 |My| 最大者；位移为影响点（竖向位移最大节点）处的值，"
            "向下为负。位移组合值仅供参考——JTG D60 4.3.2 的挠度限值判的是"
            "汽车荷载本身产生的挠度，不是组合值。")
    # 缺活载时这张表最容易被误读成"组合已经做完了"：它看起来和正常时
    # 一模一样，只是数值小一圈。必须在表注里说死。
    if combos and not combos[0].has_live:
        note += ("本模型未定义车道荷载（*LANE_3D），表中组合值只有恒载项，"
                 "不是组合值。")
    return {
        "title": "荷载组合控制值汇总",
        "note": note,
        "headers": ["组合", "表达式", "控制弯矩 My (kN·m)", "影响点位移 (mm)", "用途"],
        "rows": rows,
    }
