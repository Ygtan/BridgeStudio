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
所以 `beam` 里没有索；成桥态内核只给**梁端**内力（`girder_ends`），不给
包络，所以 `beam`（包络）就是 `{}`，并在 `diagnostics` 里写清楚。宁可少一格，
不要假一格——应力验算用 `girder_ends`，包络图仍然没有。
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import combination
import code_material
import section_strength

#: 内核键 → 中文名。界面标题、计算书章节名都取这里，不各写一份。
KIND_LABELS: dict[str, str] = {
    "grillage3d": "三维梁格",
    "cable_stayed": "斜拉桥成桥态",
    "suspension": "悬索桥成桥态",
}

#: 索的最小安全系数下限（JTG/T 3365-01-2020 拉索安全系数 ≥ 2.5）
CABLE_SAFETY_MIN = 2.5
#: 悬索桥主缆安全系数下限（JTG/T D65-05-2015：主缆 ≥ 2.0、吊索 ≥ 2.5）。
#: 主缆不可更换，限值反而比可换的吊索低——这是规范里的既有取舍，不是笔误。
MAIN_CABLE_SAFETY_MIN = 2.0
#: 索破断强度默认值（1860 级钢绞线，GB/T 5224）
CABLE_FPU_DEFAULT = 1860e6
#: 施工期索力上限对应的临时安全系数：T ≤ A·fpu / 2.0。
#:
#: 比运营期的 2.5 低——施工阶段是短暂工况、荷载组合系数更小，规范允许更低的
#: 临时安全系数。但这个上限必须有：施工调索的目标是"到达设计索力"，不设上限
#: 的话，一次超张拉到 1.5 倍设计索力的错误顺序也会显示成"调索成功"。
#: 这是本程序自定的判定线，不是规范条文；条目里如实写明出处。
CABLE_SAFETY_MIN_STAGE = 2.0
#: 主缆破断强度（1770 级镀锌高强钢丝，GB/T 17101）
MAIN_CABLE_FPU = 1770e6


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
    # 意义）。所以组合另取这一组成桥态单值——它是各施工阶段内力的**累加**
    # （内核 compute3DFinalStageBeamForces 逐阶段求和，每阶段用自己的自由度
    # 映射）：分阶段施工的假定是「已硬化构件内力冻结」，成桥态 = Σ 各阶段
    # 增量，而不是「最后一个阶段那一批荷载单独的解」。
    #
    # 键是 (单元号, "i"/"j") 而不是单元号：同一单元两端弯矩通常异号，
    # 先外包成一对再组合，会把 i 端的恒载与 j 端的活载加到一起——那既不是
    # i 端也不是 j 端的截面。
    final_beam: dict[tuple[int, str], dict[str, float]] = field(default_factory=dict)
    final_disp: dict[tuple[int, int], float] = field(default_factory=dict)
    # 成桥态活载内力包络：{(单元, 端): {MyMin, MyMax, VzMin, VzMax}}
    live_beam: dict[tuple[int, str], dict[str, float]] = field(default_factory=dict)
    # 施工阶段各索状态：{(阶段号, 单元号): {T, L0, chord, target, dev, Tdes, devDes}}。
    #
    # 与上面的位移/内力包络性质不同：那些是**增量**（可逐阶段累加），索力是
    # **状态量**（累加没有意义）。所以这里按 (阶段, 索) 平铺，不提供包络，
    # 也不参与荷载组合。devDes 是"相对该索设计目标的偏差"——分批张拉时先张
    # 的索会被后张的带跑，这个数就是必须二次调索的理由。
    stage_cables: dict[tuple[int, int], dict[str, float]] = field(default_factory=dict)
    # 成桥态专用
    cables: list[dict[str, Any]] = field(default_factory=list)
    camber: dict[int, tuple[float, float, float]] = field(default_factory=dict)
    erected: dict[int, tuple[float, float, float]] = field(default_factory=dict)

    # ---- 悬索桥专用（其他内核留空，不填假数据）--------------------------
    # 主缆分段 / 吊索 / 加劲梁内力 / 支座反力。
    # 悬索桥和斜拉桥的差别不只是"多了个 H"：主缆是一条连续索，它的分段
    # 索力由找形而非优化给出；加劲梁在恒载下本该几乎不受力（地锚式），
    # 或只受 H 的轴压（自锚式）。这四张表就是把这两条讲清楚的地方。
    main_cable: list[dict[str, Any]] = field(default_factory=list)
    hangers: list[dict[str, Any]] = field(default_factory=list)
    girder: list[dict[str, Any]] = field(default_factory=list)
    # 梁端内力（两种索承体系都有）：{(单元, 端): {N, My, Vz}}，端为 1=i、2=j。
    #
    # 为什么不能直接用上面那个 `girder`：它是"每根梁一行、N/My/Vz 各取两端
    # 绝对值较大者"的摘要，max|N| 与 max|My| 可能来自不同端，拼出来的
    # (N, My) 在梁上并不存在。应力 σ = N/A ± My/W 必须**同一端**配对。
    girder_ends: dict[tuple[int, int], dict[str, float]] = field(default_factory=dict)
    reaction: dict[tuple[int, int], float] = field(default_factory=dict)
    # 找形后的主缆节点坐标 {node: (x, z)}。**这张图不能拿模型坐标画**：
    # .dat 里的主缆坐标只是建造器给的初始抛物线，边跨那一段甚至是按
    # "锚点到塔顶的直线"插的，与找形结果能差几米（锚点处主缆必须水平，
    # V=0，线形是顶点在锚点的抛物线）。画错不报错，但设计线形全错。
    cable_shape: dict[int, tuple[float, float]] = field(default_factory=dict)

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

    def stage_cable_summary(self, model: Any = None) -> list[dict[str, Any]]:
        """把逐阶段索状态压成「每根索一条全过程记录」，供表、图、校核共用。

        为什么要另做一层汇总：`stage_cables` 是 {(阶段, 索): {...}} 的平铺字典，
        回答"第 3 阶段第 7 根索多少力"很直接，回答"这根索施工期最大多少、出现
        在哪一阶段、离目标差多少"要遍历两遍。表、图、校核三处各遍历一遍就等于
        三份口径，改一处漏两处。

        返回的每条记录：
            elem / stages（有记录的阶段号，升序）/ n
            Tmin / TminStage、Tmax / TmaxStage
            Tfirst / Tlast（首、末阶段索力）
            Tdes / ratio（设计目标、峰值/目标；无目标时 ratio 为 None）
            devLast（末阶段相对**设计目标**的偏差，不是相对本阶段目标）
            missing（全程出现过、但这根索没被激活的阶段号）

        `missing` 不是装饰：索在中途被拆掉（换索、合龙前临时索）时，Python 侧
        的包络会把"没有记录"读成"索力不变"，得到一个假的平稳过程。这里把它
        显式列出来，由调用方决定怎么处理。
        """
        all_stages = sorted({s for s, _ in self.stage_cables})
        if not all_stages:
            return []
        by_elem: dict[int, list[tuple[int, dict[str, float]]]] = {}
        for (s, e), v in self.stage_cables.items():
            by_elem.setdefault(e, []).append((s, v))

        out: list[dict[str, Any]] = []
        for e, series in by_elem.items():
            series.sort(key=lambda kv: kv[0])
            seen = [s for s, _ in series]
            tmax_s, tmax_v = max(series, key=lambda kv: float(kv[1].get("T", 0.0)))
            tmin_s, tmin_v = min(series, key=lambda kv: float(kv[1].get("T", 0.0)))
            Tdes = max((float(v.get("Tdes") or 0.0) for _, v in series), default=0.0)
            Tmax = float(tmax_v.get("T", 0.0))
            Tmin = float(tmin_v.get("T", 0.0))
            # 缺失阶段分两种，含义完全相反，不能混成一个 `missing`：
            #   notYet  出现在首记录之前 —— 该索还没张拉，本来就没有（正常）
            #   dropped 出现在首记录之后 —— 记录中途消失，多半是被 DEACTIVE
            #           拆掉了；也有可能是内核漏了。后者必须报出来。
            not_yet = [s for s in all_stages if s < seen[0]]
            dropped = [s for s in all_stages
                       if s > seen[0] and s not in set(seen)]
            out.append({
                "elem": int(e),
                "stages": seen,
                "n": len(seen),
                "Tmin": Tmin, "TminStage": int(tmin_s),
                "Tmax": Tmax, "TmaxStage": int(tmax_s),
                "Tfirst": float(series[0][1].get("T", 0.0)),
                "Tlast": float(series[-1][1].get("T", 0.0)),
                "Tdes": Tdes,
                "ratio": (Tmax / Tdes) if Tdes > 0 else None,
                # 全过程偏离目标：峰值往上、谷值往下都算，取绝对值大者。
                # 只比峰值会漏掉"被后来的索带成负值"的那一类——它的峰值
                # 恰好等于目标（因为张拉时就停在目标上），比值是漂亮的 1.00。
                "devRange": (max(abs(Tmax - Tdes), abs(Tmin - Tdes)) / Tdes
                             if Tdes > 0 else None),
                "devLast": abs(float(series[-1][1].get("devDes") or 0.0)),
                "L0": float(series[-1][1].get("L0") or 0.0),
                "notYet": not_yet,
                "dropped": dropped,
                "missing": not_yet + dropped,
            })
        out.sort(key=lambda r: r["elem"])
        return out

    def stage_cable_series(self, model: Any = None) -> dict[int, dict[str, Any]]:
        """索力-阶段曲线用：{索单元: {"label": 图例名, "pts": [(阶段, 索力 kN)]}}。

        图例名带锚点 x（能从 model 取到的话）：施工方认的是"哪根索"，
        而单元号在图纸上不一定标。取不到 model 就退回纯单元号——不编位置。
        """
        x_of: dict[int, float] = {}
        el: dict[int, Any] = {}
        if model is not None:
            try:
                x_of = {n.id: n.x for n in model.nodes}
                el = {e.id: e for e in model.elements}
            except AttributeError:
                pass
        out: dict[int, dict[str, Any]] = {}
        for r in self.stage_cable_summary(model):
            e = r["elem"]
            label = f"索{e}"
            if e in el:
                x = x_of.get(el[e].n1)
                if x is not None:
                    label += f" @{x:.0f}m"
            out[e] = {
                "label": label,
                "pts": sorted((s, float(v.get("T", 0.0)) / 1e3)
                              for (s, ee), v in self.stage_cables.items() if ee == e),
            }
        return out

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
        elif self.kind == "suspension":
            m = self.meta
            sys_name = str(m.get("susp_system") or "EARTH").upper()
            out += [
                Metric("体系", "地锚式" if sys_name == "EARTH" else "自锚式",
                       hint="主缆锚在锚碇" if sys_name == "EARTH"
                            else "主缆锚在加劲梁端，H 压进梁"),
                Metric("收敛", "是" if self.converged else "否",
                       hint=f"{self.iterations or 0} 轮 / {(self.elapsed_ms or 0):.0f} ms"),
                Metric("主缆水平力 H", f"{(m.get('H_kN') or 0):,.0f}", "kN",
                       hint="找形结果，非人为给定"),
                Metric("成桥矢高", f"{(m.get('sag_m') or 0):.2f}", "m",
                       hint=f"目标 {(m.get('sag_target_m') or 0):.2f} m，"
                            f"矢跨比 1/{(m.get('sag_ratio') or 0):.1f}"),
                Metric("主缆索力",
                       f"{(m.get('tension_max_kN') or 0):,.0f}", "kN",
                       hint=f"全长分段最大；吊索 "
                            f"{(m.get('hanger_T_min_kN') or 0):.0f}~"
                            f"{(m.get('hanger_T_max_kN') or 0):.0f} kN"),
                Metric("加劲梁轴力 N", f"{(m.get('girder_N_max_kN') or 0):,.0f}", "kN",
                       hint=f"弯矩 My {(m.get('girder_M_max_kNm') or 0):,.0f} kN·m"),
                Metric("竖向平衡偏差", f"{(m.get('balance_pct') or 0):+.3f}", "%",
                       hint=f"支座 ΣV {(m.get('support_v_sum_kN') or 0):,.0f} kN / "
                            f"荷载 {(m.get('applied_v_kN') or m.get('total_load_kN') or 0):,.0f} kN"),
            ]
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
            if self.stage_cables:
                # 索承体系走线性梁格时才有：与"成桥索力"不同，这是**过程**
                # 峰值，施工控制关心的是它有没有把索（和塔、梁）顶过头。
                sr = self.stage_cable_summary(model)
                hi = max(sr, key=lambda r: r["Tmax"])
                low = min(sr, key=lambda r: r["Tmin"])
                out.append(Metric(
                    "施工期索力峰值", f"{hi['Tmax'] / 1e3:.0f}", "kN",
                    hint=f"索 {hi['elem']}，阶段 {hi['TmaxStage']}；"
                         f"全过程最小 {low['Tmin'] / 1e3:.0f} kN"
                         f"（阶段 {low['TminStage']}）"))
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

        只对梁格内核有意义：索承体系内核只给恒载标准值，没有活载效应，
        系数乘上去得到的是一个不存在的组合值。
        """
        return combination.beam_combinations(self)

    def disp_combinations(self) -> list[combination.DispCombo]:
        """影响点处的位移组合。"""
        return combination.disp_combinations(self)

    def combination_checks(self, model: Any = None) -> list[Check]:
        """组合是否可用、量级是否合理。不做承载力判定——那要配筋。"""
        if self.kind != "grillage3d":
            if self.kind == "suspension":
                g = max((abs(float(x.get("My") or 0.0)) for x in self.girder),
                        default=0.0)
                return [Check(
                    "运营期荷载组合", True,
                    f"悬索桥成桥态内核给恒载内力（加劲梁 My 最大 {g / 1e3:,.0f} kN·m）"
                    "与索力，但不含活载效应，故只做标准值验算：索按安全系数、"
                    "加劲梁按恒载内力。运营期组合需另接活载模块。", level="info")]
            return [Check(
                "运营期荷载组合", True,
                f"{self.kind_label}给的是恒载标准值（含梁端内力：索力与截面"
                "应力都按标准值验），不含活载效应，故不做运营期组合；"
                "索按安全系数、加劲梁与塔按恒载应力。运营期组合需另接活载模块。", level="info")]
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
        elif self.kind == "suspension":
            out += self._checks_suspension(model)
        else:
            out += self._checks_grillage(model)
        out += self.combination_checks(model)
        out += self._checks_strength(model)
        out += self._checks_stage_cable(model)
        return out

    def _checks_stage_cable(self, model: Any) -> list[Check]:
        """施工阶段索力的校核。

        四条常驻，前两条管**过程**、后两条管**终态**。只查末阶段是不够的：
        一根索在阶段 2 被压成负值、阶段 4 又拉回目标，末阶段的检查会给出
        "全部通过"，而实际施工中那根索在阶段 2 就已经退出工作了。

        1. 全程不出现受压/松索——线性阶段内核把 CABLE 当桁架，允许负轴力，
           而真实索一受压就退出工作；这个结果本身不可信，不能当"索力偏小"糊过去。
        2. 全程峰值不超过 A·fpu/2.0（施工期临时安全系数，见常量说明）——
           调索的判据是"到达目标"，不设上限的话，超张拉到 1.5 倍的错误顺序
           也会显示成"调索成功"。
        3. 末阶段各索还差设计目标多少——分批张拉时先张的必然被后张的带跑，
           这个差额就是二次调索的依据。
        4. 全过程峰值偏离设计目标多少——超张拉与欠张拉都在这里暴露。

        第 5 条只在中途真有索失去记录时出现（见末尾），它不属于力学判定，
        而是"这张过程表有没有洞"的提醒。
        """
        rows = self.stage_cable_summary(model)
        if not rows:
            return []
        out: list[Check] = []
        all_stages = sorted({s for s, _ in self.stage_cables})

        # ---- 1. 全程不出现受压 -------------------------------------------
        neg = [r for r in rows if r["Tmin"] <= 0.0]
        if neg:
            worst = min(neg, key=lambda r: r["Tmin"])
            detail = (f"{len(neg)} 根索全过程出现过轴力 ≤ 0，最小 "
                      f"{worst['Tmin']/1e3:.0f} kN（单元 {worst['elem']}，"
                      f"阶段 {worst['TminStage']}）；线性阶段把索当桁架，允许受压"
                      "——真实索受压即退出工作，该施工顺序在这几根索上不成立")
        else:
            detail = (f"{len(rows)} 根索在 {len(all_stages)} 个阶段"
                      f"（{'、'.join(map(str, all_stages))}）内全部受拉，"
                      f"最小 {min(r['Tmin'] for r in rows)/1e3:.0f} kN")
        out.append(Check("施工阶段索不出现受压（全过程）", not neg, detail,
                         level="error"))

        # ---- 2. 全程峰值 ≤ A·fpu / 2.0 -----------------------------------
        # 逐根取面积：主缆与吊索的 A 差几十倍，用同一个面积会把吊索判重。
        cap_rows: list[tuple[float, dict[str, Any], float]] = []
        no_area: list[int] = []
        if model is not None:
            for r in rows:
                area = _elem_area_m2(model, r["elem"])
                if not area:
                    no_area.append(r["elem"])
                    continue
                cap = area * CABLE_FPU_DEFAULT / CABLE_SAFETY_MIN_STAGE
                if r["Tmax"] > 0.0:
                    cap_rows.append((r["Tmax"] / cap, r, cap))
        if not cap_rows:
            out.append(Check(
                "施工期索力上限（A·fpu / "
                f"{CABLE_SAFETY_MIN_STAGE:.1f}）", True,
                "模型没有给出索的实常数截面积，施工期索力上限无法判定"
                "——这不是「通过」，是「没做」", level="info"))
        else:
            ratio, r, cap = max(cap_rows, key=lambda t: t[0])
            msg = (f"峰值索力 {r['Tmax']/1e3:.0f} kN（单元 {r['elem']}，阶段 "
                   f"{r['TmaxStage']}）对上限 {cap/1e3:.0f} kN = {ratio*100:.1f}%；"
                   f"上限取 A·fpu/{CABLE_SAFETY_MIN_STAGE:.1f}"
                   "（施工期临时安全系数，低于运营期 "
                   f"{CABLE_SAFETY_MIN:.1f}），是本程序自定的判定线")
            if no_area:
                msg += f"；{len(no_area)} 根索无截面积、未参与判定"
            out.append(Check(
                "施工期索力不超过上限", ratio <= 1.0, msg,
                value=ratio, limit=1.0,
                level="warn" if ratio <= 1.0 else "error"))

        # ---- 3. 末阶段 vs 设计目标 ---------------------------------------
        tgt = [r for r in rows if r["Tdes"] > 0.0]
        if tgt:
            r = max(tgt, key=lambda x: x["devLast"])
            dev = r["devLast"]
            out.append(Check(
                f"末阶段索力 vs 设计目标（{len(tgt)} 根）",
                dev <= 0.02,
                f"最大偏差 {dev*100:.2f}%（单元 {r['elem']}："
                f"{r['Tlast']/1e3:.0f} / {r['Tdes']/1e3:.0f} kN）。"
                + ("" if dev <= 0.02 else
                   "分批张拉时先张的索会被后张的带跑，属正常现象——"
                   "按目标再补调一轮（加一个只给 TENSION= 的阶段）即可拉回。"),
                value=dev, limit=0.02,
                level="warn" if dev <= 0.02 else "error"))

        # ---- 4. 全过程峰值 vs 设计目标 ------------------------------------
        peak = [r for r in tgt if r["ratio"] is not None]
        if peak:
            r = max(peak, key=lambda x: abs(x["ratio"] - 1.0))
            dev = abs(r["ratio"] - 1.0)
            out.append(Check(
                f"施工期索力峰值 vs 设计目标（{len(peak)} 根）",
                dev <= 0.10,
                f"最大偏离 {dev*100:.2f}%（单元 {r['elem']}：峰值 "
                f"{r['Tmax']/1e3:.0f} kN 出现在阶段 {r['TmaxStage']}，"
                f"目标 {r['Tdes']/1e3:.0f} kN）。超 10% 有两种常见原因："
                "该阶段张拉量给大了，或者它被后来张拉的索带跑（先张的被顶起来）。"
                "前者要改张拉量，后者要改顺序或补一轮调索——两者都不能靠"
                "「最后一轮调平」掩盖过程中的超张",
                value=dev, limit=0.10,
                level="warn"))

        # ---- 5. 记录连续性（只在真出现时提）-------------------------------
        # 「还没张拉」不算：那根索本来就不存在。首记录之后又消失的才算，
        # 它多半是被 DEACTIVE 拆掉了——这本身可能是设计意图，但必须让人看到，
        # 因为过程表里那一格显示为「—」，与「索力为 0」是两件事。
        gone = [r for r in rows if r["dropped"]]
        if gone:
            eg = gone[0]
            out.append(Check(
                f"施工阶段索记录连续（{len(gone)} 根中断）", False,
                f"单元 {eg['elem']} 在阶段 "
                f"{'、'.join(map(str, eg['dropped']))} 没有记录；"
                "若是 DEACTIVE 拆索属正常，但过程表里那几格是空值而不是 0，"
                "不能当索力归零参与比较",
                level="warn"))
        return out

    def _checks_strength(self, model: Any) -> list[Check]:
        """截面应力验算：把组合内力换成应力，跟材料强度比。

        与 `combination_checks` 的分工要分清：那条只说"我给了组合值"，
        这条才回答"这个组合值扛不扛得住"。前者是算术，后者是判定。

        梁格走组合内力（`final_beam`）；索承体系没有活载、没有组合，
        走成桥态梁端内力（`girder_ends`），口径另算——见
        `_checks_strength_cable`。
        """
        if not self.final_beam:
            if self.kind in ("cable_stayed", "suspension"):
                return self._checks_strength_cable(model)
            return []
        secs = _strength_sections(model)
        if not secs:
            return [Check(
                "截面应力验算", True,
                "模型未标注材料牌号（*MAT 的 grade 为空），无法确定强度"
                "设计值，本次不做应力验算——这不是「通过」，是「没做」",
                level="info")]

        combos = combination.beam_combinations(self)
        ult = next((c for c in combos if c.spec.key == "ultimate"), None)
        if ult is None:
            return []

        # 先算每个截面，再按利用率排序取前 3。模型有 3 个梁截面时逐截面
        # 出三条就是 9 条，其中大半是"利用率 0.00"——那不是校核，是噪音。
        # 读者要的是"哪里最危险"，不是"处处多安全"。
        ranked: list[tuple[float, str, Any, list[tuple[int, str, Any]]]] = []
        for label, sec, eids in secs:
            cases: list[tuple[int, str, Any]] = []
            for (eid, end), v in ult.rows.items():
                if eid not in eids:
                    continue
                n = float(v.get("N", 0.0))
                # 包络配对：MyMin 配 VzMin、MyMax 配 VzMax。两者不一定同时
                # 达到，但同号侧通常同现——包络法固有的近似，写在这里而不是
                # 藏起来。
                for my, vz in ((v.get("MyMin", 0.0), v.get("VzMin", 0.0)),
                               (v.get("MyMax", 0.0), v.get("VzMax", 0.0))):
                    cases.append((eid, end, section_strength.check(
                        sec, n, float(my), float(vz))))
            if not cases:
                continue
            top = max(t[2].util for t in cases)
            ranked.append((top, label, sec, cases))

        if not ranked:
            return []
        ranked.sort(key=lambda t: -t[0])

        out: list[Check] = []
        # 利用率 < 2% 的截面不单列：横梁、隔板在纵向弯曲里基本不参与，
        # 把它们写成"利用率 0.000"的校核条目，等于用零信息占三行版面。
        shown = [t for t in ranked if t[0] >= 0.02][:3] or ranked[:1]
        for top, label, sec, cases in shown:
            steel = sec.kind == "steel"
            # 三项各找自己的控制截面：轴力极值与弯矩极值不在同一处，
            # 拿弯矩最大的截面去代表剪应力控制部位会漏判。
            wn = max(cases, key=lambda t: t[2].util_normal)
            sym = "fd" if steel else "0.5fck"
            out.append(Check(
                f"{label} 正应力 ≤ {sym}",
                wn[2].util_normal <= 1.0,
                f"单元 {wn[0]}·{wn[1]} 端 σ = {wn[2].sigma_top:+.1f} / "
                f"{wn[2].sigma_bot:+.1f} MPa（上/下缘，拉正压负），"
                f"利用率 {wn[2].util_normal:.3f}；{wn[2].limit_desc}",
                value=wn[2].util_normal, limit=1.0,
                level="error" if steel else "warn"))

            if sec.AW > 0.0:
                ws = max(cases, key=lambda t: t[2].util_shear)
                if steel:
                    out.append(Check(
                        f"{label} 剪应力 ≤ fvd", ws[2].util_shear <= 1.0,
                        f"单元 {ws[0]}·{ws[1]} 端 τ = V/AW = {ws[2].tau:.1f} MPa，"
                        f"利用率 {ws[2].util_shear:.3f}；{ws[2].limit_desc}",
                        value=ws[2].util_shear, limit=1.0, level="error"))
                else:
                    # 混凝土的抗剪由配箍后的斜截面承载力控制（JTG 3362-2018
                    # §5.2.9），不是"τ ≤ 某个强度"。硬把 τ 除以 ftd 得到
                    # 1.57 这种数，是在制造一个没有物理意义的"不合格"。
                    out.append(Check(
                        f"{label} 腹板剪应力（量级）", True,
                        f"单元 {ws[0]}·{ws[1]} 端 τ = V/AW = {ws[2].tau:.1f} MPa。"
                        "混凝土梁的抗剪须按斜截面承载力验算（箍筋 + 混凝土"
                        "贡献），本程序不建钢筋，故不给利用率、也不下结论。",
                        value=ws[2].tau, unit="MPa", level="info"))

            if steel:
                we = max(cases, key=lambda t: t[2].util_eq)
                out.append(Check(
                    f"{label} 折算应力利用率 ≤ 1.0", we[2].util_eq <= 1.0,
                    f"单元 {we[0]}·{we[1]} 端 √((σx/fd)²+(τ/fvd)²) = "
                    f"{we[2].util_eq:.3f}（σx={we[2].sigma_top:+.1f} 或 "
                    f"{we[2].sigma_bot:+.1f} MPa，τ={we[2].tau:.1f} MPa）；"
                    "JTG D64-2015 §5.3.1 第 4 款",
                    value=we[2].util_eq, limit=1.0, level="error"))

        # 适用范围只写一次，不跟着每个截面重复。
        if any(sec.kind == "concrete" for _, _, sec, _ in shown):
            out.append(Check(
                "混凝土应力验算的适用范围", True,
                "钢筋混凝土构件的使用阶段应力验算不是 JTG 3362 的强制项"
                "（规范靠承载力与裂缝宽度控制）；本程序不建钢筋，那两条"
                "都做不了，故 0.5fck 只作量级判据，不构成「验算通过」。",
                level="info"))
        if len(ranked) > len(shown):
            out.append(Check(
                "应力验算覆盖范围", True,
                f"模型含 {len(ranked)} 个梁截面，上列只给利用率最高的 "
                f"{len(shown)} 个；其余 {len(ranked) - len(shown)} 个截面的"
                "利用率更低（横梁、隔板在纵向弯曲中基本不参与）。",
                level="info"))
        return out

    def _checks_strength_cable(self, model: Any) -> list[Check]:
        """索承体系（斜拉桥 / 悬索桥）的截面应力验算。

        内力来自 `cable_solver` 的 `[GIRDEREND]`——成桥态、**恒载标准值**、
        每端一行。与梁格那条链路的差别只有两条，但两条都影响结论的用法：

        1. **没有活载，也没有组合系数**。这里算的是恒载单项应力，不是组合
           设计值，所以「利用率 < 1」不能当成设计通过——条目一律标 warn，
           并在说明里写明口径。恒载单项就超过设计强度（利用率 > 1）反而
           是硬结论：无论活载多大都不可能满足，标 error。
        2. **用的是端内力，不是包络**。成桥态是一个确定状态，没有"最不利"，
           所以不存在包络配对带来的近似（梁格那边 MyMax 配 VzMax 是一种
           近似，这里不需要）。

        塔柱也在验算范围内：它同样是 BEAM，同样被压弯。但塔的实常数里没有
        截面模量（W=0），所以只算轴应力——这一点写在条目说明里，不静默。
        """
        if not self.girder_ends:
            return [Check(
                "截面应力验算", True,
                "结果里没有梁端内力（[GIRDEREND] 段缺失）：多半是旧版 "
                "cable_solver.exe，重新编译内核即可。本次没做应力验算——"
                "这不是「通过」，是「没做」。",
                level="info")]
        secs = _strength_sections(model)
        if not secs:
            return [Check(
                "截面应力验算", True,
                "模型未标注材料牌号（*MAT 的 grade 为空），无法确定强度"
                "设计值，本次不做应力验算——这不是「通过」，是「没做」",
                level="info")]

        ranked: list[tuple[float, str, Any, list[tuple[int, int, Any]]]] = []
        for label, sec, eids in secs:
            cases: list[tuple[int, int, Any]] = []
            for (eid, end), v in self.girder_ends.items():
                if eid not in eids:
                    continue
                cases.append((eid, end, section_strength.check(
                    sec, float(v.get("N", 0.0)), float(v.get("My", 0.0)),
                    float(v.get("Vz", 0.0)))))
            if not cases:
                continue
            ranked.append((max(t[2].util for t in cases), label, sec, cases))
        if not ranked:
            return [Check(
                "截面应力验算", True,
                "梁端内力与模型的梁截面没对上（单元号或截面定义变了？），"
                "本次不做应力验算", level="info")]
        ranked.sort(key=lambda t: -t[0])

        out: list[Check] = []
        shown = [t for t in ranked if t[0] >= 0.02][:3] or ranked[:1]
        # 主梁总要出现。索承体系的恒载分配常常让主梁利用率低到 2% 以下
        # （地锚式悬索桥的加劲梁在恒载下本该几乎不受力），只按利用率筛选
        # 会把它筛掉——而它恰恰是设计人要看的构件，塔不是。
        main = next((t for t in ranked if t[1].startswith("主梁")), None)
        if main is not None and not any(t[1] == main[1] for t in shown):
            shown = (shown[:2] + [main]) if len(shown) >= 3 else shown + [main]
        for top, label, sec, cases in shown:
            steel = sec.kind == "steel"
            wn = max(cases, key=lambda t: t[2].util_normal)
            sym = "fd" if steel else "0.5fck"
            no_w = sec.WT <= 0.0 and sec.WB <= 0.0
            out.append(Check(
                f"{label} 正应力 ≤ {sym}（恒载标准值）",
                wn[2].util_normal <= 1.0,
                f"单元 {wn[0]}·{wn[1]} 端 σ = {wn[2].sigma_top:+.1f} / "
                f"{wn[2].sigma_bot:+.1f} MPa（上/下缘，拉正压负），"
                f"利用率 {wn[2].util_normal:.3f}；{wn[2].limit_desc}。"
                + ("**该截面未给截面模量（WT/WB=0），只算了轴应力，"
                   "弯曲应力未计。**" if no_w else "")
                + "口径：成桥态恒载标准值，未含活载与组合系数——"
                  "利用率 < 1 不等于设计通过。",
                value=wn[2].util_normal, limit=1.0,
                level="error" if wn[2].util_normal > 1.0 else "warn"))

            if sec.AW > 0.0:
                ws = max(cases, key=lambda t: t[2].util_shear)
                if steel:
                    out.append(Check(
                        f"{label} 剪应力 ≤ fvd（恒载标准值）",
                        ws[2].util_shear <= 1.0,
                        f"单元 {ws[0]}·{ws[1]} 端 τ = V/AW = {ws[2].tau:.1f} MPa，"
                        f"利用率 {ws[2].util_shear:.3f}；{ws[2].limit_desc}",
                        value=ws[2].util_shear, limit=1.0,
                        level="error" if ws[2].util_shear > 1.0 else "warn"))
                else:
                    out.append(Check(
                        f"{label} 腹板剪应力（量级）", True,
                        f"单元 {ws[0]}·{ws[1]} 端 τ = V/AW = {ws[2].tau:.1f} MPa。"
                        "混凝土梁的抗剪须按斜截面承载力验算（箍筋 + 混凝土"
                        "贡献），本程序不建钢筋，故不给利用率、也不下结论。",
                        value=ws[2].tau, unit="MPa", level="info"))

            if steel:
                we = max(cases, key=lambda t: t[2].util_eq)
                out.append(Check(
                    f"{label} 折算应力利用率 ≤ 1.0（恒载标准值）",
                    we[2].util_eq <= 1.0,
                    f"单元 {we[0]}·{we[1]} 端 √((σx/fd)²+(τ/fvd)²) = "
                    f"{we[2].util_eq:.3f}（σx={we[2].sigma_top:+.1f} 或 "
                    f"{we[2].sigma_bot:+.1f} MPa，τ={we[2].tau:.1f} MPa）；"
                    "JTG D64-2015 §5.3.1 第 4 款",
                    value=we[2].util_eq, limit=1.0,
                    level="error" if we[2].util_eq > 1.0 else "warn"))

        # 口径与边界只写一次。这段话比上面的利用率更重要：它是唯一能让人
        # 不把"恒载应力合格"误读成"设计通过"的地方。
        out.append(Check(
            "索承体系应力验算的口径", True,
            f"内力取自成桥态（{len(self.girder_ends) // 2} 根梁的两端），"
            "为恒载标准值：本内核不含活载、不形成 JTG D60 组合，所以这些"
            "利用率的含义是「恒载单项占设计强度的比例」，不是设计验算结论。"
            "真正的运营期验算还缺活载效应与组合系数。",
            level="info"))
        if len(ranked) > len(shown):
            out.append(Check(
                "应力验算覆盖范围", True,
                f"模型含 {len(ranked)} 个梁截面，上列只给利用率最高的 "
                f"{len(shown)} 个；其余 {len(ranked) - len(shown)} 个更低。",
                level="info"))
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

    def _checks_suspension(self, model: Any) -> list[Check]:
        """悬索桥校核。

        与斜拉桥的校核**不能共用**，因为要判的东西不同：

        - 斜拉桥校核"索力有没有达到人为给定的目标"；悬索桥的索力是找形的
          结果，该判的是"找形出来的线形对不对"（矢高命中）和"由它反算的
          下料长度能不能把设计线形做成"（索力达到目标）。
        - 斜拉桥没有"体系自洽"这一项；悬索桥有，而且它是最能拦住错的一项：
          自锚式的梁轴力**必须**≈ −H，地锚式的**必须**≈ 0。若纵向约束放错
          位置，H 会被支座吃掉，两条判据同时不过，而位移、索力全都正常。
        """
        m = self.meta
        out: list[Check] = []
        sys_name = str(m.get("susp_system") or "EARTH").upper()
        is_self = sys_name == "SELF"

        # ---- 找形：矢高是否命中目标 ----
        sag = float(m.get("sag_m") or 0.0)
        sag_t = float(m.get("sag_target_m") or 0.0)
        if sag_t > 0:
            err = abs(sag - sag_t) / sag_t
            out.append(Check(
                "找形矢高命中", err < 1e-3,
                f"成桥矢高 {sag:.4f} m / 目标 {sag_t:.4f} m（偏差 {err:.1e}）",
                value=err, limit=1e-3, unit="1"))

        ratio = float(m.get("sag_ratio") or 0.0)
        if ratio > 0:
            out.append(Check(
                "矢跨比", 1 / 14.0 <= 1 / ratio <= 1 / 7.0,
                f"1/{ratio:.1f}（公路悬索桥常在 1/12 ~ 1/8；过小则 H 与塔顶"
                f"竖向反力畸高，过大则吊索过长、梁的刚度靠不上主缆）",
                value=ratio, limit=1.0, unit="1", level="warn"))

        # ---- 竖向平衡：Σ 支座反力 = Σ 竖向荷载 ----
        sup = float(m.get("support_v_sum_kN") or 0.0)
        app = float(m.get("applied_v_kN") or 0.0)
        if app > 0:
            resid = abs(sup - app) / app
            out.append(Check(
                "竖向平衡（Σ支座反力 = Σ竖向荷载）", resid < 1e-4,
                f"{sup:,.0f} kN / {app:,.0f} kN（残差 {resid:.1e}）",
                value=resid, limit=1e-4, unit="1"))

        # ---- 达成索力 vs 目标索力 ----
        terr = m.get("tension_err")
        if terr is not None:
            out.append(Check(
                "索力达到找形目标（偏差 ≤ 10%）", float(terr) < 0.10,
                f"最大相对偏差 {float(terr) * 100:.2f}%（含主缆与吊索；"
                f"偏差来自加劲梁的弹性压缩改变了索的弦长）",
                value=float(terr), limit=0.10, unit="1", level="warn"))

        # ---- 无受压 ----
        tmin = m.get("tension_min_kN")
        if tmin is not None:
            out.append(Check(
                "主缆与吊索无受压", float(tmin) > 0.0,
                f"最小索力 {float(tmin):.1f} kN", value=float(tmin),
                limit=0.0, unit="kN"))

        # ---- 安全系数：主缆与吊索的截面积差几十倍，必须逐根取实常数 ----
        for tag, rows, lim, fpu in (
            ("主缆", self.main_cable, MAIN_CABLE_SAFETY_MIN, MAIN_CABLE_FPU),
            ("吊索", self.hangers, CABLE_SAFETY_MIN, CABLE_FPU_DEFAULT),
        ):
            worst: tuple[int, float] | None = None
            for r in rows:
                a = _elem_area_m2(model, r["elem"])
                t = float(r.get("T") or 0.0)
                if not a or t <= 0:
                    continue
                k = a * fpu / t
                if worst is None or k < worst[1]:
                    worst = (r["elem"], k)
            if worst is not None:
                out.append(Check(
                    f"{tag}最小安全系数（≥ {lim:.1f}）", worst[1] >= lim,
                    f"单元 {worst[0]}：{worst[1]:.2f}"
                    f"（A·fpu/T，fpu={fpu / 1e6:.0f} MPa）",
                    value=worst[1], limit=lim))

        # ---- 体系自洽：加劲梁轴力 ----
        #
        # 这一条是自锚式悬索桥唯一"能一把抓住模型错"的判据。自锚式的定义就是
        # 主缆水平力 H 压进加劲梁，所以恒载下梁内轴力必然 ≈ −H。若把纵向约束
        # 放在梁端，H 直接进支座，梁轴力算成 0，而位移、索力、平衡全都正常。
        H = float(m.get("H_kN") or 0.0)
        Nmax = float(m.get("girder_N_max_kN") or 0.0)
        if H > 0 and abs(Nmax) > 0:
            if is_self:
                rel = abs(abs(Nmax) - H) / H
                out.append(Check(
                    "自锚体系自洽（梁轴力 ≈ −H）", rel < 0.10,
                    f"梁轴力 {Nmax:,.0f} kN / H {H:,.0f} kN（偏差 {rel * 100:.1f}%）"
                    f"；梁端纵向必须自由，纵向约束应放跨中",
                    value=rel, limit=0.10, unit="1"))
            else:
                rel = abs(Nmax) / H
                out.append(Check(
                    "地锚体系自洽（梁轴力 ≈ 0）", rel < 0.05,
                    f"梁轴力 {Nmax:,.0f} kN，H {H:,.0f} kN 由锚碇直接承受"
                    f"（{rel * 100:.1f}% H）",
                    value=rel, limit=0.05, unit="1"))

        # ---- 吊索力均匀性 ----
        ht = [float(h.get("T") or 0.0) for h in self.hangers if (h.get("T") or 0) > 0]
        if len(ht) >= 2:
            spread = max(ht) / min(ht)
            out.append(Check(
                "吊索力均匀（最大/最小 ≤ 1.30）", spread <= 1.30,
                f"{max(ht) / 1e3:.1f} / {min(ht) / 1e3:.1f} kN = {spread:.2f}",
                value=spread, limit=1.30, level="warn"))

        # ---- 锚点竖向分力：地锚式靠抗拔，自锚式靠梁端支座 ----
        #
        # 这条是本工具最能给出"设计结论"的地方，而不只是校核算术：
        # 主缆水平力 H 由主跨矢高定，边跨必须共用同一个 H。于是主缆在锚点的
        # 竖向分力 V = H·(Δz/L) − qL/2 是几何决定量，不是可调参数。
        #   · 地锚式：锚碇靠自重 + 摩阻抗拔，V 为负（向上拔）是常态；
        #   · 自锚式：锚点就在加劲梁端，竖向支座不能承拉，|V|/H 必须很小，
        #     否则梁端要被拔起来。满足 V≈0 的边跨长度 L=√(2HΔz/q)，明显长于
        #     地锚式惯用的 1/3 主跨——这是几何要求，不是偏好。
        if self.reaction and H > 0:
            neg = [(n, v) for (n, d), v in self.reaction.items()
                   if d == 3 and v < -1.0]
            if neg:
                n0, v0 = min(neg, key=lambda kv: kv[1])
                rel = abs(v0) / (H * 1e3)
                if is_self:
                    out.append(Check(
                        "自锚式梁端不被拔起（|V锚|/H ≤ 0.15）", rel <= 0.15,
                        f"节点 {n0} 竖向反力 {v0 / 1e3:,.0f} kN（|V|/H = {rel:.2f}）。"
                        f"主缆在锚点仍有竖向分力，梁端需抗拔支座或压重；"
                        f"把边跨调到 L≈√(2HΔz/q) 附近可使其趋于 0",
                        value=rel, limit=0.15, unit="1"))
                else:
                    out.append(Check(
                        "锚碇抗拔", True,
                        f"节点 {n0} 竖向反力 {v0 / 1e3:,.0f} kN（负 = 锚碇需下拉主缆，"
                        f"|V|/H = {rel:.2f}；锚碇设计须计抗拔与抗滑）",
                        value=v0 / 1e3, unit="kN", level="info"))

        # ---- 自锚式的梁体轴向缩短：这一项不是预拱度 ----
        #
        # 主缆 H 压进梁，成桥态梁体缩短 Δ = H·L/(EA)。它要靠梁端伸缩缝或
        # 合龙段预留，和竖向预抬高**不是同一件事**：把两者混成一个"预变形"
        # 数字，现场就会照大数起拱，反而错。实测值直接取 [U] 里两端节点
        # 的纵向位移，不靠 H·L/EA 反推（反推要猜 EA，模型里的实常数才是准的）。
        if is_self and model is not None:
            ds = sorted((n for n in model.nodes if 1000 < n.id < 2000),
                        key=lambda n: n.x)
            if len(ds) >= 2:
                ua = float(self.disp.get((ds[0].id, 1), (0.0, 0.0))[0])
                ub = float(self.disp.get((ds[-1].id, 1), (0.0, 0.0))[0])
                short = abs((ua - ub) * 1000.0)
                out.append(Check(
                    "梁体轴向缩短（制造长度补偿）", True,
                    f"成桥态梁体缩短 {short:.1f} mm，两端各内移 "
                    f"{max(abs(ua), abs(ub)) * 1000:.1f} mm；须由梁端伸缩缝 / "
                    f"合龙段预留，与竖向预抬高分开处理",
                    value=short, unit="mm", level="info"))

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
            rows = []
            for c in sorted(self.cables, key=lambda c: x_of.get(c["anchor"], 0.0)):
                t = float(c.get("T") or 0.0)
                # 逐根取实常数截面：悬索桥主缆与吊索的 A 差 20 倍，用同一个
                # 面积算安全系数会把吊索判重 20 倍（看起来"吊索很安全"）。
                if model is not None:
                    area = _elem_area_m2(model, c["elem"])
                else:
                    area = _cable_area_m2(model)
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

        # ---- 索承体系：截面应力（成桥态恒载标准值）------------------------
        # 每个梁截面一行，取该截面下利用率最高的那一端。梁格那边没有这张表：
        # 它的应力是组合内力算的，逐截面组合表已经把内力给了，应力由校核条目
        # 承担；索承体系没有组合表，若再不给应力表，报告里就只剩几条校核文字。
        if (self.girder_ends and model is not None
                and self.kind in ("cable_stayed", "suspension")):
            rows = []
            for label, sec, eids in _strength_sections(model):
                best: tuple[float, int, int, Any] | None = None
                for (eid, end), v in self.girder_ends.items():
                    if eid not in eids:
                        continue
                    rr = section_strength.check(
                        sec, float(v.get("N", 0.0)), float(v.get("My", 0.0)),
                        float(v.get("Vz", 0.0)))
                    if best is None or rr.util > best[0]:
                        best = (rr.util, eid, end, rr)
                if best is None:
                    continue
                rows.append([label, f"{best[1]}·{best[2]}"] + best[3].to_row())
            if rows:
                out["stress_table"] = {
                    "title": "控制截面应力（成桥态恒载标准值）",
                    "note": "正应力以受拉为正、受压为负，单位 MPa；利用率为"
                            "「效应/限值」，>1.0 即不满足。内力取自成桥态梁端，"
                            "N 与 M 来自**同一端**（同一截面）。"
                            "**口径：恒载标准值，未含活载与组合系数**——"
                            "利用率 <1 只说明恒载单项占设计强度的比例，"
                            "不等于设计验算通过。钢按 JTG D64-2015 §5.3.1 "
                            "三款；混凝土按 JTG 3362-2018 §7.1.5 的 0.5·fck。",
                    "headers": ["部位", "单元·端", "上缘 σ (MPa)", "下缘 σ (MPa)",
                                "τ (MPa)", "折算应力 (MPa)", "利用率", "控制项"],
                    "rows": rows,
                }

        # ---- 施工阶段：索力全过程 ----------------------------------------
        # 行 = 索，列 = 各阶段末索力。这个形状是刻意的：施工控制问的是
        # "这根索从张拉到现在怎么变的"，行是索才读得出来；反过来按阶段排，
        # 得在几十行里横着找同一根索。
        if self.stage_cables:
            summ = self.stage_cable_summary(model)
            stages = sorted({s for s, _ in self.stage_cables})
            rows = []
            for r in summ:
                series = {s: float(v.get("T", 0.0))
                          for (s, e), v in self.stage_cables.items()
                          if e == r["elem"]}
                rows.append([
                    r["elem"],
                    _r(r["Tdes"] / 1e3, 1) if r["Tdes"] > 0 else "—",
                    *[(_r(series[s] / 1e3, 1) if s in series else "—")
                      for s in stages],
                    f"{_r(r['Tmax'] / 1e3, 1)}（{r['TmaxStage']}）",
                    f"{_r(r['Tmin'] / 1e3, 1)}（{r['TminStage']}）",
                ])
            out["stage_cable_table"] = {
                "title": "施工阶段索力全过程",
                "note": "各阶段末的索力（kN），不是增量：索力是状态量，不能逐阶段"
                        "累加（累加出来是「张拉动作的总和」，没有物理意义）。"
                        "「—」表示该阶段这根索未激活。峰值/谷值取全过程，"
                        "括号内为出现阶段；它才是施工期验算要看的两列——"
                        "只盯最后一阶段，会漏掉中途被压成负值和一时超张的情况。",
                "headers": (["索单元", "设计目标 (kN)"]
                            + [f"阶段 {s} (kN)" for s in stages]
                            + ["峰值 (阶段)", "谷值 (阶段)"]),
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

        # ---- 悬索桥四张专项表 --------------------------------------------
        if self.main_cable:
            H = float(self.meta.get("H_kN") or 0.0)
            rows = []
            for r in self.main_cable:
                t = float(r.get("T") or 0.0)
                a = _elem_area_m2(model, r["elem"])
                k = (a * MAIN_CABLE_FPU / t) if (a and t > 0) else None
                ch = float(r.get("chord") or 0.0)
                rows.append([r["elem"], _r(r.get("z"), 3),
                             _r(t / 1e3, 1), _r(r.get("L0"), 4),
                             _r(ch, 4) if ch > 0 else "—",
                             _r(k, 2) if k is not None else "—",
                             _r(100.0 * t / H, 1) if H > 0 else "—"])
            out["main_cable_table"] = {
                "title": "主缆分段索力、下料长度与安全系数",
                "note": "主缆是一条连续索，各段索力 = √(H² + V²)，水平分力 H 全长"
                        "不变（这正是塔顶不受不平衡水平力的条件）。下料长度 L0 "
                        "按浅垂度弹性悬链线由**平衡态弦长**与目标索力反算——不是"
                        "设计弦长，两者相差一个弹性缩短量，自锚式可达 150 mm。"
                        f"安全系数 = A·fpu/T，fpu = {MAIN_CABLE_FPU / 1e6:.0f} MPa，"
                        f"限值 ≥ {MAIN_CABLE_SAFETY_MIN:.1f}。",
                "headers": ["单元", "低端高程 (m)", "索力 (kN)", "下料 L0 (m)",
                            "平衡弦长 (m)", "安全系数", "T/H (%)"],
                "rows": rows,
            }

        if self.hangers:
            rows = []
            for r in self.hangers:
                t = float(r.get("T") or 0.0)
                a = _elem_area_m2(model, r["elem"])
                k = (a * CABLE_FPU_DEFAULT / t) if (a and t > 0) else None
                chord = float(r.get("chord") or 0.0)
                L0 = float(r.get("L0") or 0.0)
                rows.append([r["elem"], r.get("deck_node"),
                             _r(chord, 3), _r(L0, 3),
                             _r((chord - L0) * 1000, 1) if chord and L0 else "—",
                             _r(t / 1e3, 1),
                             _r(k, 2) if k is not None else "—"])
            out["hanger_table"] = {
                "title": "吊索索力、下料长度与安全系数",
                "note": "吊索目标索力 = 该梁节点承担的竖向恒载（找形直接从荷载取，"
                        "不做迭代）。弦长 − 下料长 = 弹性伸长量，是正常量而非误差。"
                        f"安全系数限值 ≥ {CABLE_SAFETY_MIN:.1f}（JTG/T D65-05-2015）。",
                "headers": ["单元", "梁节点", "成桥弦长 (m)", "下料 L0 (m)",
                            "伸长 (mm)", "索力 (kN)", "安全系数"],
                "rows": rows,
            }

        if self.girder:
            H = float(self.meta.get("H_kN") or 0.0)
            out["girder_table"] = {
                "title": "加劲梁恒载内力",
                "note": "成桥态（恒载）下加劲梁几乎不受力——这正是悬索桥与斜拉桥"
                        "的根本差别：恒载全部由主缆承担。地锚式梁轴力应 ≈ 0；"
                        "自锚式梁轴力应 ≈ −H（H 压进梁是自锚式的定义）。"
                        "若两者都不成立，说明模型的纵向约束位置错了。",
                "headers": ["单元", "N (kN)", "My (kN·m)", "Vz (kN)"],
                "rows": [[g["elem"], _r(g["N"] / 1e3, 1), _r(g["My"] / 1e3, 1),
                          _r(g["Vz"] / 1e3, 1)] for g in self.girder],
            }

        if self.reaction:
            rows = []
            x_of2 = x_of
            for (nid, dof), v in sorted(self.reaction.items(),
                                        key=lambda kv: (kv[0][0], kv[0][1])):
                rows.append([nid, _r(x_of2.get(nid, float("nan")), 2),
                             f"U{'XYZ'[dof - 1]}" if 1 <= dof <= 3 else str(dof),
                             _r(v / 1e3, 1)])
            out["reaction_table"] = {
                "title": "支座反力",
                "note": "向上为正。锚碇节点出现负值表示主缆在该处把锚碇**向上拔**，"
                        "锚碇设计须按抗拔与抗滑验算，不能只按承压考虑。",
                "headers": ["节点", "x (m)", "方向", "反力 (kN)"],
                "rows": rows,
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
            "n_main_cable": len(self.main_cable),
            "n_hangers": len(self.hangers),
            "n_girder": len(self.girder),
            "n_reaction": len(self.reaction),
            "diagnostics": list(self.diagnostics),
            "metrics": [m.to_dict() for m in self.metrics()],
            "checks": [c.to_dict() for c in self.checks()],
        }


# ---------------------------------------------------------------- 内部工具


def _strength_sections(model: Any) -> list[tuple[str, Any, set[int]]]:
    """从模型里取出所有可做应力验算的梁截面。

    返回 `[(标签, SectionForCheck, 该截面下的单元号集合)]`。

    为什么按 (mat, real) 分组而不是"只验主梁"：桥塔同样是 BEAM、同样被
    压弯，漏掉它就等于默认塔不会坏。模型里有几个梁截面就验几个。

    标签里"主梁"给单元数最多的那一组（三个建造器的建模顺序都是主梁在
    前），其余用实常数号标识——程序不知道"横隔板"叫什么，就不装作知道。
    """
    out: list[tuple[str, Any, set[int]]] = []
    try:
        ets = [t for t in model.etypes if getattr(t, "kind", "") == "BEAM"]
        mats = {m.id: m for m in model.materials}
        reals = {r.id: r for r in model.reals}
    except AttributeError:
        return out
    if not ets:
        return out

    counts: dict[int, int] = {}
    for e in model.elements:
        counts[e.etype] = counts.get(e.etype, 0) + 1
    main_et = max(ets, key=lambda t: counts.get(t.id, 0)).id

    for t in ets:
        mat = mats.get(t.mat)
        rc = reals.get(t.real)
        if mat is None or rc is None or not (getattr(mat, "grade", "") or "").strip():
            continue          # 未标牌号：跳过，由调用方统一说明原因
        kind = "concrete" if mat.grade.strip().upper() in code_material.CONCRETE \
            else "steel"
        sec = section_strength.SectionForCheck(
            A=float(rc.A),
            WT=float(rc.WT), WB=float(rc.WB),
            AW=float(getattr(rc, "AW", 0.0) or 0.0),
            grade=mat.grade, kind=kind,
            t_plate=float(getattr(rc, "T_PLATE", 0.0) or 0.0),
        )
        # 牌号预检：查不到强度值就在这里跳过，不让 KeyError 冒到 checks()
        # 外面去把整份报告带崩。程序宁可"这一项没做"，不能"整页打不开"。
        try:
            if kind == "steel":
                code_material.steel(sec.grade, sec.t_plate)
            else:
                code_material.concrete(sec.grade)
        except KeyError:
            continue
        eids = {e.id for e in model.elements if e.etype == t.id}
        head = "主梁" if t.id == main_et else f"截面#{t.real}"
        out.append((f"{head}（{sec.describe()}）", sec, eids))
    return out


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


def _elem_area_m2(model: Any, elem_id: int) -> float | None:
    """按**单元**取实常数截面积（m²）。

    悬索桥的主缆与吊索截面积差几十倍（0.06 m² 对 0.003 m²）。用"取第一根索
    的面积"去统一算安全系数，会把吊索判轻、把主缆判重，两张表一起错。
    """
    try:
        et = {t.id: t for t in model.etypes}
        el = {e.id: e for e in model.elements}
        e = el.get(int(elem_id))
        if e is None:
            return None
        rid = et[e.etype].real
        for rc in model.reals:
            if rc.id == rid and rc.A:
                return float(rc.A)
    except (AttributeError, KeyError):
        return None
    return None


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
