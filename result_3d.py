"""result_3d.py —— 三维求解结果的**唯一**返回类型 SolveResult。

为什么要这一层
--------------
在接入之前，两个三维内核各吐各的字典：

- `solve_3d()` 返回 `{"disp": {(n,dof): (min,max)}, "beam": {...}, "live": {...}}`
- `solve_cable_bridge()` 返回 `{"meta", "cables", "camber", "erected", "u", ...}`

于是界面上每个消费方都得先判断"这是哪种结果"，再挑字段读——斜拉桥的
`camber` 对着梁格的 `disp`，梁格的 `beam` 对着斜拉桥的空洞。计算书、CSV、
HTML 每加一个出口就复制一遍这个判断。这正是"接入"要消灭的东西。

现在的契约
----------
**一律是包络。** 梁格的阶段包络本来就是 (min, max)；成桥态只是"一个状态的
包络"，于是 `disp[(n, dof)] = (v, v)`。这样任何消费方只认一种形状：

    for (nid, dof), (lo, hi) in res.disp.items(): ...

三件派生能力由结果自己提供，界面/Word/CSV 三处共用同一份：

- `metrics()`  → 指标卡（标签、数值、单位、说明），界面直接铺
- `checks()`   → 工程校核（含限值与结论），不是"看起来对"而是可判定的通过/不通过
- `tables()`   → 明细表（表头 + 行），CSV / Word / HTML 同一份数据

诚实标注
--------
内核没给的东西就留空，不猜：梁格内核不输出反力与梁内力包络以外的量，
所以 `beam` 里没有索；成桥态内核不输出梁内力，`beam` 就是 `{}`，并在
`diagnostics` 里写一条"本内核不输出梁内力包络"。宁可少一格，不要假一格。
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import combination

#: 内核键 → 中文名。界面标题、计算书章节名都取这里，不各写一份。
KIND_LABELS: dict[str, str] = {
    "grillage3d": "三维梁格",
    "cable_stayed": "斜拉桥成桥态",
}

#: 索的最小安全系数下限（JTG/T 3365-01-2020 拉索安全系数 ≥ 2.5）
CABLE_SAFETY_MIN = 2.5
#: 索破断强度默认值（1860 级钢绞线，GB/T 5224）
CABLE_FPU_DEFAULT = 1860e6


# ---------------------------------------------------------------- 校核


@dataclass
class Check:
    """一条工程校核。

    `level="error"` 表示不满足即**不合格**（必须解决）；
    `level="warn"`  表示超出建议值，需人工判断；
    `level="info"`  仅陈述量级，不构成判定（例如"预抬高 L/1137"）。
    """

    name: str
    ok: bool
    detail: str = ""
    value: float | None = None
    limit: float | None = None
    unit: str = ""
    level: str = "error"

    @property
    def passed(self) -> bool:
        """只有 error/warn 级才参与"通过/不通过"。"""
        return self.ok or self.level == "info"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "ok": self.ok, "detail": self.detail,
            "value": self.value, "limit": self.limit, "unit": self.unit,
            "level": self.level, "passed": self.passed,
        }


@dataclass
class Metric:
    """一张指标卡。`hint` 会显示在数值下方，别写"暂无"。"""

    label: str
    value: str
    unit: str = ""
    hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "value": self.value,
                "unit": self.unit, "hint": self.hint}


# ---------------------------------------------------------------- 结果


@dataclass
class SolveResult:
    """一次三维求解的全部产出。**所有出口只吃这一个类型。**"""

    kind: str                                   # "grillage3d" | "cable_stayed"
    title: str = ""
    solver: str = ""                            # 可执行文件名
    dat_text: str = ""                          # 求解器实际读到的文本（从盘上回读）
    dat_path: str = ""
    result_path: str = ""
    stdout: str = ""

    ok: bool = True
    converged: bool | None = None
    iterations: int | None = None
    elapsed_ms: float | None = None

    stats: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    # 位移包络：{(node, dof): (min, max)}。成桥态为 (v, v)。
    disp: dict[tuple[int, int], tuple[float, float]] = field(default_factory=dict)
    # 梁内力包络：{elem: {Nmin,Nmax,MzMin,MzMax}}。本内核没有则为空。
    beam: dict[int, dict[str, float]] = field(default_factory=dict)
    # 活载包络：{stage: (max, min)}
    live: dict[int, tuple[float, float]] = field(default_factory=dict)

    # ---- 荷载组合用的成桥态效应 ----------------------------------------
    # 上面 `disp` / `beam` 是**跨施工阶段的包络**，里面混着"某跨还没浇完"
    # 的临时构型内力；而活载效应只可能发生在成桥构型上。两者不是同一状态，
    # 不能一个乘 1.2、另一个乘 1.4 再相加（两个不同构型的极值相加没有物理
    # 意义）。所以组合另取这一组成桥态单值。
    #
    # 键是 (单元号, "i"/"j") 而不是单元号：同一单元两端弯矩通常异号，
    # 先外包成一对再组合，会把 i 端的恒载与 j 端的活载加到一起——那既不是
    # i 端也不是 j 端的截面。
    final_beam: dict[tuple[int, str], dict[str, float]] = field(default_factory=dict)
    final_disp: dict[tuple[int, int], float] = field(default_factory=dict)
    # 成桥态活载内力包络：{(单元, 端): {MyMin, MyMax, VzMin, VzMax}}
    live_beam: dict[tuple[int, str], dict[str, float]] = field(default_factory=dict)
    # 成桥态专用
    cables: list[dict[str, Any]] = field(default_factory=list)
    camber: dict[int, tuple[float, float, float]] = field(default_factory=dict)
    erected: dict[int, tuple[float, float, float]] = field(default_factory=dict)

    diagnostics: list[str] = field(default_factory=list)

    # ------------------------------------------------------------ 指纹

    @property
    def fingerprint(self) -> str:
        """`.dat` 文本指纹：判断"结果是不是上一版模型的"。"""
        return hashlib.sha1(self.dat_text.encode("utf-8")).hexdigest()[:12]

    def is_stale(self, dat_text: str) -> bool:
        """当前模型导出的 .dat 是否已与本次求解时不同。"""
        if not self.dat_text:
            return False
        return hashlib.sha1(dat_text.encode("utf-8")).hexdigest()[:12] != self.fingerprint

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    # ------------------------------------------------------------ 派生量

    def has_beam_force(self) -> bool:
        return bool(self.beam)

    def max_vertical_deflection(self) -> tuple[int, float] | None:
        """竖向（dof=3）挠度绝对值最大者 → (节点号, 值 m)。负号保留原向。"""
        best: tuple[int, float] | None = None
        for (nid, dof), (lo, hi) in self.disp.items():
            if dof != 3:
                continue
            v = lo if abs(lo) >= abs(hi) else hi
            if best is None or abs(v) > abs(best[1]):
                best = (nid, v)
        return best

    def live_worst(self) -> tuple[int, float] | None:
        """活载包络最不利值 → (阶段号, 位移 m)。取绝对值大的一侧。

        内核给的是**影响点**（中跨跨中）处各施工阶段的活载位移包络，一个
        阶段一对 (max, min)；包络的 min 才是向下挠的控制值，但上拱同样要
        看得见，所以按绝对值挑。
        """
        best: tuple[int, float] | None = None
        for stage, (hi, lo) in self.live.items():
            v = lo if abs(lo) >= abs(hi) else hi
            if best is None or abs(v) > abs(best[1]):
                best = (stage, v)
        return best

    def cable_safety(self, model: Any = None) -> tuple[int, float] | None:
        """最不利索的安全系数 → (索单元号, κ)。κ = A·fpu / T。

        面积取模型实常数、fpu 取 1860 级默认值；任一缺失就返回 None 而不是
        假设一个面积——用假面积算出来的安全系数比不给更糟。
        """
        area = _cable_area_m2(model) if model is not None else None
        if not area or not self.cables:
            return None
        worst: tuple[int, float] | None = None
        for c in self.cables:
            t = float(c.get("T") or 0.0)
            if t <= 0:
                continue
            k = area * CABLE_FPU_DEFAULT / t
            if worst is None or k < worst[1]:
                worst = (int(c["elem"]), k)
        return worst

    def control_sections(self, model: Any = None) -> list[tuple[str, int, float]]:
        """控制断面 → [(标记, 节点号, x)]：支座断面 + 各跨跨中。

        施工控制只关心这几处（支座看支承是否落实，跨中看线形），逐个节点
        列表没有意义：91 个节点里有 80 个既不是支座也不是跨中。
        """
        if model is None or not self.disp:
            return []
        x_of = {n.id: n.x for n in model.nodes}
        sup = support_x(model)
        if len(sup) < 1:
            return []
        targets = [(f"支座 {x:.0f} m", x) for x in sup]
        targets += [(f"跨中 {(a + b) / 2:.0f} m", (a + b) / 2)
                    for a, b in zip(sup[:-1], sup[1:])]
        out: list[tuple[str, int, float]] = []
        for label, x in targets:
            if not x_of:
                continue
            nid = min(x_of, key=lambda n: abs(x_of[n] - x))
            if any(nid == o[1] for o in out):      # 同一节点只列一次
                continue
            out.append((label, nid, x_of[nid]))
        return out

    def total_length(self, model: Any = None) -> float | None:
        """模型纵向长度（用于挠度限值 L/xxx）。由节点坐标推出，不信参数。"""
        if model is None:
            return None
        try:
            xs = [n.x for n in model.nodes]
        except AttributeError:
            return None
        if not xs:
            return None
        return max(xs) - min(xs)

    # ------------------------------------------------------------ 指标卡

    def metrics(self, model: Any = None) -> list[Metric]:
        st = self.stats
        out: list[Metric] = [
            Metric("节点 / 单元", f"{st.get('nodes', 0)} / {st.get('elements', 0)}",
                   hint=f"{st.get('dofs', '—')} 自由度"),
        ]
        if self.kind == "cable_stayed":
            m = self.meta
            out += [
                Metric("收敛", "是" if self.converged else "否",
                       hint=f"{self.iterations or 0} 轮 / {(self.elapsed_ms or 0):.0f} ms"),
                Metric("几何闭合误差", f"{(m.get('closure_err') or 0) * 1000:.2e}", "mm",
                       hint="目标 < 1e-6 m"),
                Metric("成桥索力",
                       f"{(m.get('tension_min_kN') or 0):.0f} ~ {(m.get('tension_max_kN') or 0):.0f}",
                       "kN", hint=f"{len(self.cables)} 根索"),
                Metric("索承担桥面恒载", f"{(m.get('deck_share_pct') or 0):.1f}", "%",
                       hint=f"支座承担 {(m.get('support_share_kN') or 0):.0f} kN"),
            ]
            sf = self.cable_safety(model)
            if sf is not None:
                out.append(Metric(
                    "索最小安全系数", f"{sf[1]:.2f}",
                    hint=f"索 {sf[0]}，限值 ≥ {CABLE_SAFETY_MIN:.1f}"))
        else:
            mvd = self.max_vertical_deflection()
            out.append(Metric(
                "最大竖向挠度",
                f"{mvd[1] * 1000:.2f}" if mvd else "—", "mm",
                hint=f"节点 {mvd[0]}" if mvd else "无竖向结果"))
            mmax = self.max_bending()
            out.append(Metric(
                "最大竖向弯矩 My",
                f"{mmax[1] / 1e3:.1f}" if mmax else "—", "kN·m",
                hint=f"单元 {mmax[0]}" if mmax else "无梁内力"))
            # 活载包络是这本软件唯一"活载相关"的结果，之前算完就没出口了：
            # 数据在 res.live 里躺着，界面上只显示"活载包络 3 阶段"这条
            # 说明文字。旧版界面有这一格，重构时必须恢复。
            lw = self.live_worst()
            if lw is not None:
                out.append(Metric(
                    "活载包络（影响点）", f"{lw[1] * 1000:.2f}", "mm",
                    hint=f"阶段 {lw[0]}，JTG D60 车道荷载"))
            out.append(Metric("施工阶段", f"{st.get('stages', 0)}",
                              hint=f"活载包络 {len(self.live)} 阶段"))
            out.append(Metric("梁内力包络单元", f"{len(self.beam)}",
                              hint="六分量：N/Vy/Vz/T/My/Mz"))
            ult = next((c for c in self.combinations()
                        if c.spec.key == "ultimate"), None)
            w = ult.worst("My") if ult else None
            if w is not None:
                out.append(Metric(
                    "基本组合控制弯矩", f"{w[1] / 1e3:,.0f}", "kN·m",
                    hint=f"1.2G+1.4Q，单元 {w[0][0]}·{w[0][1]} 端"))
        return out

    def max_bending(self) -> tuple[int, float] | None:
        """竖向弯曲弯矩绝对值最大者 → (单元号, My)。梁格的主弯矩在 My。

        纵向梁沿 X 走，竖向弯曲绕局部 y（水平轴）→ My；Mz 是横向平面内弯曲。
        只盯 Mz 会得出"内力全 0"的结论（这是内核原本只输出 Mz 时的真实症状）。
        """
        best: tuple[int, float] | None = None
        for eid, v in self.beam.items():
            my = max(abs(v.get("MyMin", 0.0)), abs(v.get("MyMax", 0.0)))
            if my == 0.0:
                my = max(abs(v.get("MzMin", 0.0)), abs(v.get("MzMax", 0.0)))
            if best is None or my > abs(best[1]):
                best = (eid, my)
        return best

    # ------------------------------------------------------------ 荷载组合

    def combinations(self) -> list[combination.BeamCombo]:
        """JTG D60-2015 三种组合下的梁端内力（基本/频遇/准永久）。

        只对梁格内核有意义：斜拉桥内核不输出梁内力，也不输出活载效应。
        """
        return combination.beam_combinations(self)

    def disp_combinations(self) -> list[combination.DispCombo]:
        """影响点处的位移组合。"""
        return combination.disp_combinations(self)

    def combination_checks(self, model: Any = None) -> list[Check]:
        """组合是否可用、量级是否合理。不做承载力判定——那要配筋。"""
        if self.kind != "grillage3d":
            return [Check(
                "运营期荷载组合", True,
                f"{self.kind_label}内核不输出梁内力与活载效应，不做运营期组合；"
                "索力按标准值 + 安全系数验算", level="info")]
        if not self.final_beam:
            return [Check("荷载组合", False,
                          "结果里没有成桥态梁内力，无法形成组合值", level="error")]
        out: list[Check] = []
        if not self.live_beam:
            out.append(Check(
                "活载效应", False,
                "模型未定义车道荷载（LANE_3D）：组合里只有恒载项乘系数，"
                "这不是组合值", level="warn"))
        else:
            g = max((abs(v.get("My", 0.0)) for v in self.final_beam.values()),
                    default=0.0)
            q = max((max(abs(v.get("MyMin", 0.0)), abs(v.get("MyMax", 0.0)))
                     for v in self.live_beam.values()), default=0.0)
            if g > 0:
                ratio = q / g
                # 经验判据，不是条文：常规梁桥活载弯矩约为恒载的 0.2~0.6，
                # 超过 2 倍多半是车道荷载或跨径填错（这也是唯一能自动发现的
                # "活载异常"信号）。
                out.append(Check(
                    "活载/恒载效应比", ratio <= 2.0,
                    f"|S_Q| / |S_G| = {ratio:.2f}（活载 {q / 1e3:,.0f} / "
                    f"恒载 {g / 1e3:,.0f} kN·m）"
                    + ("" if ratio <= 2.0
                       else "：活载效应超过恒载两倍，请核对车道荷载与跨径"),
                    value=ratio, limit=2.0, level="warn"))
        return out

    # ------------------------------------------------------------ 校核

    def checks(self, model: Any = None) -> list[Check]:
        """工程校核。给了 `model` 才能做几何相关的判定（限值、同跨单调）。"""
        out: list[Check] = []
        st = self.stats
        if not st.get("nodes"):
            out.append(Check("模型规模", False, "结果里没有节点——求解可能没真正执行",
                             level="error"))
        if self.converged is False:
            out.append(Check("非线性收敛", False,
                             f"迭代 {self.iterations} 轮未达容差，结果仅供参考",
                             level="error"))
        if self.kind == "cable_stayed":
            out += self._checks_cable(model)
        else:
            out += self._checks_grillage(model)
        out += self.combination_checks(model)
        return out

    def _checks_cable(self, model: Any) -> list[Check]:
        m = self.meta
        out: list[Check] = []

        cerr = m.get("closure_err")
        if cerr is not None:
            out.append(Check(
                "几何闭合误差", float(cerr) < 1e-6,
                f"{float(cerr):.2e} m（容差 1e-6 m）",
                value=float(cerr), limit=1e-6, unit="m"))

        # 竖向自洽：ΣT·ez + 支座分担 = 桥面恒载。
        # 不用内核的 balance_pct——那是拿"索竖向分力"对"全部恒载(含塔自重)"，
        # 塔自重由塔底直接传走、永远不经过索，于是这个残差天生就是 −50% 左右。
        # 拿它做"平衡校核"会把正常模型判成不合格。
        deck = float(m.get("deck_load_kN") or 0.0)
        vs = float(m.get("vertical_sum_kN") or 0.0)
        sup = float(m.get("support_share_kN") or 0.0)
        if deck > 0:
            resid = abs(vs + sup - deck) / deck
            out.append(Check(
                "竖向平衡（ΣT·ez + 支座 = 桥面恒载）", resid < 1e-5,
                f"索 {vs:.0f} kN + 支座 {sup:.0f} kN = {deck:.0f} kN"
                f"（残差 {resid:.1e}）",
                value=resid, limit=1e-5, unit="1"))

        share = m.get("deck_share_pct")
        if share is not None:
            out.append(Check(
                # 不是规范条文，是体系判据：索承体系若索只担一半恒载，
                # 剩下的一半必然靠主梁自己跨越主跨——线形会失控。
                "索承担桥面恒载", float(share) > 90.0,
                f"{float(share):.1f}%（要求 > 90%，否则主梁被迫独立跨越主跨）",
                value=float(share), limit=90.0, unit="%", level="warn"))

        tmin = m.get("tension_min_kN")
        if tmin is not None:
            out.append(Check(
                "索无受压", float(tmin) > 0.0,
                f"最小索力 {float(tmin):.1f} kN",
                value=float(tmin), limit=0.0, unit="kN"))

        # 安全系数：面积从模型实常数取，破断强度用 1860 级默认值
        sf = self.cable_safety(model)
        area = _cable_area_m2(model)
        if sf is not None and area:
            out.append(Check(
                f"索最小安全系数（≥ {CABLE_SAFETY_MIN:.1f}）", sf[1] >= CABLE_SAFETY_MIN,
                f"破断力 {area * CABLE_FPU_DEFAULT / 1e3:.0f} kN / 最大索力 "
                f"{area * CABLE_FPU_DEFAULT / sf[1] / 1e3:.0f} kN = {sf[1]:.2f}"
                f"（索 {sf[0]}，A={area * 1e6:.0f} mm²，fpu=1860 MPa）",
                value=sf[1], limit=CABLE_SAFETY_MIN))

        out += _same_span_smoothness(self.cables, model)
        out.append(self._camber_check(model))
        return out

    def _camber_check(self, model: Any) -> Check:
        vals = [v[2] for n, v in self.camber.items() if 1000 < n < 2000]
        if not vals:
            vals = [v[2] for v in self.camber.values()]
        if not vals:
            return Check("预抬高量级", True, "结果中无制造线形数据", level="info")
        zmax = max(abs(v) for v in vals)
        if zmax <= 0:
            return Check("预抬高量级", True, "预抬高全为 0（制造线形等于设计线形）",
                         value=0.0, unit="m", level="info")
        # 分母用**最大跨径**而不是总长：工程上"L/1137"指跨径，总长会让
        # 这个比值虚高近一倍（360 m 对 200 m），看起来比实际更安全。
        sup = support_x(model)
        span = max((b - a for a, b in zip(sup[:-1], sup[1:])), default=0.0) if len(sup) >= 2 else 0.0
        L = span or self.total_length(model)
        if L:
            return Check("预抬高量级", True,
                         f"最大 |预抬高| {zmax * 1000:.1f} mm ≈ 最大跨径/{L / zmax:.0f}"
                         f"（负值表示成桥会上抬，制造时压低）",
                         value=zmax, unit="m", level="info")
        return Check("预抬高量级", True, f"最大 |预抬高| {zmax * 1000:.1f} mm",
                     value=zmax, unit="m", level="info")

    def _checks_grillage(self, model: Any) -> list[Check]:
        out: list[Check] = []
        mvd = self.max_vertical_deflection()
        L = self.total_length(model)
        if mvd and L:
            # JTG D60-2015 表 4.3.2：梁式桥主梁竖向挠度（不计冲击）≤ 计算跨径/600。
            # 这里的分母用结构总长（三跨连续），偏保守，只作量级判据。
            lim = L / 600.0
            out.append(Check(
                "竖向挠度 ≤ L/600（JTG D60）", abs(mvd[1]) <= lim,
                f"|{mvd[1] * 1000:.2f}| mm ≤ {lim * 1000:.1f} mm"
                f"（L={L:.0f} m，节点 {mvd[0]}）",
                value=abs(mvd[1]), limit=lim, unit="m", level="warn"))

        # 活载挠度单独校核：规范限值针对的是**车道荷载产生的挠度**，
        # 不是恒载 + 活载的总量。上面那条拿全量对 L/600，只能当量级判据；
        # 这条才是条文对应的量，分母用最大跨径（不是总长）。
        lw = self.live_worst()
        if lw is not None:
            sup = support_x(model)
            span = (max((b - a for a, b in zip(sup[:-1], sup[1:])), default=0.0)
                    if len(sup) >= 2 else 0.0)
            if span > 0:
                lim = span / 600.0
                out.append(Check(
                    "活载挠度 ≤ L₁/600（JTG D60 4.3.2）", abs(lw[1]) <= lim,
                    f"阶段 {lw[0]} 影响点 |{lw[1] * 1000:.2f}| mm ≤ "
                    f"{lim * 1000:.1f} mm（最大跨径 {span:.0f} m）",
                    value=abs(lw[1]), limit=lim, unit="m"))
        if self.beam:
            mmax = self.max_bending()
            out.append(Check(
                "梁单元竖向弯矩非零", bool(mmax and mmax[1] > 1e-3),
                (f"最大 |My| {mmax[1] / 1e3:.1f} kN·m（单元 {mmax[0]}）"
                 if mmax else "所有单元弯矩为 0"),
                level="warn"))
            # 轴力为 0 是**结果**而不是缺失：梁格只在支座处约束竖向/横向，
            # 竖向荷载找不到传力路径去产生轴力。写出来，读者才不会追问
            # "轴力图去哪了"。
            npeak = max((max(abs(v.get("Nmin", 0.0)), abs(v.get("Nmax", 0.0)))
                         for v in self.beam.values()), default=0.0)
            if npeak < 1.0:            # < 1 N 视为零（浮点残差量级）
                out.append(Check(
                    "轴力水平", True,
                    f"全模型 |N| 最大 {npeak:.2e} N（浮点残差量级）：梁格无纵向"
                    "约束，竖向荷载不产生轴力（故不绘轴力图）", level="info"))
            else:
                out.append(Check(
                    "轴力水平", True,
                    f"最大 |N| {npeak / 1e3:.1f} kN（由纵向约束/温度等引起）",
                    value=npeak / 1e3, unit="kN", level="info"))
        if not self.disp:
            out.append(Check("位移包络非空", False, "summary 里没有任何位移条目"))
        return out

    # ------------------------------------------------------------ 明细表

    def tables(self, model: Any = None) -> dict[str, dict[str, Any]]:
        """CSV / Word / HTML 共用的明细表。键名固定，缺数据就不给这张表。"""
        out: dict[str, dict[str, Any]] = {}
        x_of = {n.id: n.x for n in model.nodes} if model is not None else {}

        if self.cables:
            area = _cable_area_m2(model) if model is not None else None
            rows = []
            for c in sorted(self.cables, key=lambda c: x_of.get(c["anchor"], 0.0)):
                t = float(c.get("T") or 0.0)
                # 逐根安全系数：只有最不利值进指标卡，但施工方要核对的是
                # "哪几根索偏紧"，所以每根都得列出来。
                k = (area * CABLE_FPU_DEFAULT / t) if (area and t > 0) else None
                rows.append([
                    c["elem"], c["anchor"],
                    _r(x_of.get(c["anchor"], float("nan")), 2),
                    _r(c["Ltarget"], 3), _r(c["L0"], 3),
                    _r(c["T"] / 1e3, 1),
                    _r(k, 2) if k is not None else "—",
                    _r(c["dL"] * 1000, 1),
                ])
            out["cable_table"] = {
                "title": "成桥索力、下料索长与安全系数",
                # dL 是「成桥弦长 − 目标弦长」的闭合残差，不是设计调整量。
                # 不写这一句，读者会以为"换算出来的索长调整量全是 0"。
                "note": "弦长残差 = 成桥弦长 − 设计弦长（成桥弦长 = 设计弦长 + "
                        "残差/1000），反映索长闭环精度，量级应在 0.1 mm 以下；"
                        "安全系数 = A·fpu / T"
                        f"（A 取实常数，fpu = 1860 MPa，限值 ≥ "
                        f"{CABLE_SAFETY_MIN:.1f}）。",
                # 表头刻意短：8 列塞进 15.5 cm 时，"索单元"会被折成「索单/元」
                # 这种带孤字的竖排。信息量由表题承担（成桥索力与下料索长）。
                "headers": ["索号", "锚点", "锚点 x (m)", "设计弦长 (m)",
                            "下料 L0 (m)", "索力 (kN)", "安全系数",
                            "弦长残差 (mm)"],
                "rows": rows,
            }

        if self.live:
            out["live_envelope"] = {
                "title": "活载包络（JTG D60，影响点 = 中跨跨中）",
                "note": "取自内核 compute3DStageLiveEnvelope：对各施工阶段的"
                        "激活体系逐车道求影响面后取包络，max 为向上、min 为向下。",
                "headers": ["施工阶段", "竖向 max (mm)", "竖向 min (mm)"],
                "rows": [[k, _r(hi * 1000, 2), _r(lo * 1000, 2)]
                         for k, (hi, lo) in sorted(self.live.items())],
            }

        if self.disp and model is not None:
            rows2 = []
            for label, nid, x in self.control_sections(model):
                v = self.disp.get((nid, 3), (0.0, 0.0))
                h = self.disp.get((nid, 2), (0.0, 0.0))
                rows2.append([label, nid, _r(x, 2),
                              _r(v[0] * 1000, 2), _r(v[1] * 1000, 2),
                              _r(max(abs(h[0]), abs(h[1])) * 1000, 2)])
            if rows2:
                out["control_disp"] = {
                    "title": "控制断面位移包络",
                    "note": "支座与跨中节点；竖向向下为负，横向取绝对值最大者。",
                    "headers": ["断面", "节点", "x (m)", "竖向 min (mm)",
                                "竖向 max (mm)", "横向 max (mm)"],
                    "rows": rows2,
                }

        if self.camber:
            deck = sorted((n for n in self.camber if 1000 < n < 2000),
                          key=lambda n: x_of.get(n, 0.0)) or sorted(self.camber)
            out["camber_table"] = {
                "title": "制造线形（预抬高）",
                "headers": ["节点", "x (m)", "预抬高 (mm)", "制造标高 (m)"],
                "rows": [[n, _r(x_of.get(n, float("nan")), 2),
                          _r(self.camber[n][2] * 1000, 1),
                          _r(self.erected.get(n, (0, 0, 0))[2], 4)] for n in deck],
            }

        if self.beam:
            out["beam_envelope"] = {
                "title": "梁内力包络（六分量）",
                "headers": ["单元", "Nmin (kN)", "Nmax (kN)", "VzMax (kN)",
                            "MyMin (kN·m)", "MyMax (kN·m)",
                            "MzMin (kN·m)", "MzMax (kN·m)", "Tmax (kN·m)"],
                "rows": [[e,
                          _r(v.get("Nmin", 0) / 1e3, 2),
                          _r(v.get("Nmax", 0) / 1e3, 2),
                          _r(max(abs(v.get("VzMin", 0)), abs(v.get("VzMax", 0))) / 1e3, 1),
                          _r(v.get("MyMin", 0) / 1e3, 1),
                          _r(v.get("MyMax", 0) / 1e3, 1),
                          _r(v.get("MzMin", 0) / 1e3, 1),
                          _r(v.get("MzMax", 0) / 1e3, 1),
                          _r(max(abs(v.get("Tmin", 0)), abs(v.get("Tmax", 0))) / 1e3, 1)]
                         for e, v in sorted(self.beam.items())],
            }
        combos = self.combinations()
        if combos:
            out["combo_coef"] = combination.coefficient_table()
            out["combo_summary"] = combination.summary_table(
                combos, self.disp_combinations())
            ult = next((c for c in combos if c.spec.key == "ultimate"), None)
            if ult is not None and ult.rows:
                out["combo_ultimate"] = combination.beam_table(ult)
        return out

    # ------------------------------------------------------------ 兼容旧字典

    def as_legacy_dict(self) -> dict[str, Any]:
        """拼成 `solve_cable_bridge()` 当年的字典形状。

        为什么留着：计算书（report_docx，1500 行）与若干验证脚本是按那个
        形状写的。**形状转换只在这里做一次**，而不是让每个消费方各自拼
        `res["u"].get((n,3))`——那种重复正是"接入"要消灭的东西。
        后续新写的消费方只该用 SolveResult 的字段与方法。
        """
        return {
            "meta": self.meta,
            "cables": self.cables,
            "camber": self.camber,
            "erected": self.erected,
            "u": {k: v[0] for k, v in self.disp.items()},
            "converged": bool(self.converged),
            "dat": Path(self.dat_path),
            "result": Path(self.result_path),
            "stdout": self.stdout,
        }

    # ------------------------------------------------------------ 序列化

    def to_dict(self) -> dict[str, Any]:
        """只序列化能被 JSON 吃下的部分（键统一成字符串）。"""
        return {
            "kind": self.kind, "title": self.title, "solver": self.solver,
            "dat_path": self.dat_path, "result_path": self.result_path,
            "fingerprint": self.fingerprint, "ok": self.ok,
            "converged": self.converged, "iterations": self.iterations,
            "elapsed_ms": self.elapsed_ms,
            "stats": dict(self.stats), "meta": dict(self.meta),
            "n_disp": len(self.disp), "n_beam": len(self.beam),
            "n_cables": len(self.cables),
            "diagnostics": list(self.diagnostics),
            "metrics": [m.to_dict() for m in self.metrics()],
            "checks": [c.to_dict() for c in self.checks()],
        }


# ---------------------------------------------------------------- 内部工具


def _r(v: Any, nd: int) -> Any:
    """安全取整：NaN/None 原样给出，避免表里出现 `nan` 字样。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    if not math.isfinite(f):
        return "—"
    v = round(f, nd)
    # 取整之后才可能出现 -0.0（如 -0.04 保留 1 位）：符号位在 float 里是独立的，
    # 不显式归一，表里就会出现 "-0.0" 这种只说明格式没处理干净的东西。
    return 0.0 if v == 0 else v


def _cable_area_m2(model: Any) -> float | None:
    """从模型实常数里取索的截面积（m²）。取不到就返回 None，不猜。"""
    try:
        kinds = {t.id: t.kind for t in model.etypes}
        cable_etype = {t.id: t for t in model.etypes if t.kind == "CABLE"}
        if not cable_etype:
            return None
        real_id = next(iter(cable_etype.values())).real
        for rc in model.reals:
            if rc.id == real_id and rc.A:
                return float(rc.A)
    except (AttributeError, StopIteration):
        return None
    return None


def support_x(model: Any) -> list[float]:
    """从边界条件推支承断面 x。索承体系的"跨"由此确定，不信建造器的 meta。

    判据：某节点被约束了 dof=2 或 dof=3（竖向/横向），就是支座断面。
    塔底固结（1..6 全约束）也会命中，但塔底不在梁线上，用"梁节点"卡住。
    """
    if model is None:
        return []
    try:
        deck_ids = {n.id for n in model.nodes if n.id < 2000}
        constrained = {b.node for b in model.boundaries
                       if b.node in deck_ids and (2 in b.dofs or 3 in b.dofs)}
        x_of = {n.id: n.x for n in model.nodes}
        return sorted({round(x_of[i], 6) for i in constrained})
    except (AttributeError, KeyError):
        return []


def _same_span_smoothness(cables: list[dict[str, Any]], model: Any) -> list[Check]:
    """同跨索力不得跳变。

    这是本项目用真实故障换来的判据：主跨两侧各铺满全跨时，短索与长索
    锚在同一点附近，索力沿桥呈锯齿（实测 520 → 2580 → 880 kN）。模型照样
    收敛、报告照样出图，只有这条检查能把它拦住。相邻索力比取 1.5。
    """
    if model is None or not cables:
        return []
    x_of = {n.id: n.x for n in model.nodes}
    sup = support_x(model)
    if len(sup) < 2:
        return []
    spans = list(zip(sup[:-1], sup[1:]))
    worst = 0.0
    worst_at = ""
    for lo, hi in spans:
        grp = [c for c in cables
               if lo - 1e-9 <= x_of.get(c["anchor"], -1e18) <= hi + 1e-9]
        grp.sort(key=lambda c: x_of.get(c["anchor"], 0.0))
        for a, b in zip(grp[:-1], grp[1:]):
            ta, tb = a["T"], b["T"]
            if ta <= 0 or tb <= 0:
                continue
            ratio = max(ta, tb) / min(ta, tb)
            if ratio > worst:
                worst = ratio
                worst_at = (f"[{lo:.0f}, {hi:.0f}] m 处 "
                            f"x={x_of.get(a['anchor'], 0):.0f}→"
                            f"{x_of.get(b['anchor'], 0):.0f} m")
    return [Check(
        "同跨索力无跳变（相邻比 ≤ 1.5）", worst <= 1.5,
        f"实测最大相邻比 {worst:.2f}" + (f"（{worst_at}）" if worst_at else ""),
        value=worst, limit=1.5, level="warn")]
