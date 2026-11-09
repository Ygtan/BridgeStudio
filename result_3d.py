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

import buckling_3d
import cable_length_3d
import combination
import code_material
import modal_3d
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
#: 钢绞线抗拉强度标准值默认，以及张拉控制应力上限系数。
#: JTG 3362 对钢丝、钢绞线的规定是 σcon ≤ 0.75 fpk。
#: 这里用有效预应力 σpe = Pe/Ap 去比这个上限：超了，控制应力只会更高。
STRAND_FPK_DEFAULT = 1860e6
STRAND_CONTROL_RATIO = 0.75
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


def stage_time_effect_note(model: Any, solver: str) -> str:
    """施工阶段龄期在这次求解里到底有没有进入徐变和收缩。"""
    times = [float(getattr(s, "time", 0.0) or 0.0)
             for s in getattr(model, "stages", []) or []]
    times = [t for t in times if t > 0.0]
    uniform = (len(times) >= 2 and all(
        abs((b - a) - (times[1] - times[0])) < 1e-6
        for a, b in zip(times, times[1:])))
    if uniform:
        note = (f"第一段末龄期 {times[0]:g} 天，之后每段加 {times[1] - times[0]:g} 天，"
                "含合拢和二期。这不是现场进度，也不是规范规定的工期。")
    else:
        note = ("龄期是程序拟定的施工周期：第一段为加载龄期，之后每段加一个周期。"
                "这不是现场进度，也不是规范规定的工期。")
    rh = float(getattr(model, "creep_rh", 0.0) or 0.0)
    h = float(getattr(model, "creep_h", 0.0) or 0.0)
    fcm = float(getattr(model, "creep_fcm", 0.0) or 0.0)
    phi0_in = float(getattr(model, "creep_phi0", 0.0) or 0.0)
    beta_in = float(getattr(model, "creep_beta_h", 700.0) or 700.0)
    t0 = float(getattr(model, "creep_t0", 7.0) or 7.0)
    member_h = [float(getattr(r, "HTH", 0.0) or 0.0)
                for r in getattr(model, "reals", []) or []]
    member_h = [x for x in member_h if x > 0.0]
    code = rh > 0.0 and fcm > 0.0 and (h > 0.0 or bool(member_h))
    if code and h <= 0.0:
        note += ("未写统一理论厚度。各混凝土截面按外轮廓 h=2A/u："
                 + "、".join(f"{x:.1f} mm" for x in member_h)
                 + "。拉索不取。")
    elif code:
        from model_doc import jtg3362_nominal_creep, jtg3362_shrinkage_nominal
        phi0, beta = jtg3362_nominal_creep(rh, h, fcm, t0)
        eps = jtg3362_shrinkage_nominal(rh, fcm)
        note += (f"按 JTG 3362 附录 C，相对湿度 {rh:g}%、理论厚度 {h:g} mm、"
                 f"fcm={fcm:g} MPa，名义徐变系数 φ0={phi0:.3f}，βH={beta:.1f}，"
                 f"名义收缩系数 {eps:.3e}。")
        ts = float(getattr(model, "shrinkage_ts", 0.0) or 0.0)
        if ts > 0.0 and abs(ts - t0) > 1e-6:
            note += f"干燥从第 {ts:g} 天起，参与受力之前的收缩不进入平衡。"
    elif phi0_in > 0.0:
        note += (f"徐变曲线是 φ0={phi0_in:g}、βH={beta_in:g} 的简化曲线，"
                 "不是按湿度、理论厚度取值。")
    nonlinear = "stage_nl" in (solver or "")
    if nonlinear and (code or phi0_in > 0.0):
        note += ("本次是非线性阶段：徐变取相邻龄期的系数差，作用在已有混凝土应力上；"
                 "收缩只计构件参与受力之后新发展的部分，索不收缩。")
    elif code or phi0_in > 0.0:
        note += "本次是线性阶段，不读龄期，没有按这条曲线计算徐变和收缩。"
    else:
        note += "非线性阶段才按相邻龄期计算徐变。线性阶段不读龄期。"
    return note


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
    # 施工阶段各预应力束的有效预加力：{(阶段号, 束号): {"Pe": N}}。
    # 与索力同性质：**状态量**，只在张拉那一阶段出现，不累加。Pe = P·loss，
    # 校核"有效预加力 vs 张拉控制应力"用它（张拉控制应力 = P / 束面积）。
    stage_prestress: dict[tuple[int, int], dict[str, float]] = field(default_factory=dict)
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
    # 活载梁内力包络（成桥态切线刚度影响线）：min/max 成对，与 girder_ends
    # 恒载标准值配对做荷载组合。缺 = 模型未定义车道或旧内核。
    girder_live: dict[tuple[int, int], dict[str, float]] = field(default_factory=dict)
    reaction: dict[tuple[int, int], float] = field(default_factory=dict)
    # 找形后的主缆节点坐标 {node: (x, z)}。**这张图不能拿模型坐标画**：
    # .dat 里的主缆坐标只是建造器给的初始抛物线，边跨那一段甚至是按
    # "锚点到塔顶的直线"插的，与找形结果能差几米（锚点处主缆必须水平，
    # V=0，线形是顶点在锚点的抛物线）。画错不报错，但设计线形全错。
    cable_shape: dict[int, tuple[float, float]] = field(default_factory=dict)
    # 施工阶段（分级找形）：每阶段一行 {stage, name, H, sag, midz, hmin, hmax}。
    # 空缆 → 从跨中对称吊装一期恒载 Q1 → 合拢 → 二期铺装 Q2（成桥态）。
    # 只有模型开了 erection（*ERECTION 段）才非空。
    erection: list[dict[str, Any]] = field(default_factory=list)
    # 每阶段主跨主缆高程序列 {stage: [z…]}，画"主缆随吊装下沉"曲线用。
    erection_shape: dict[int, list[float]] = field(default_factory=dict)

    # ---- 模态与自振特性 ------------------------------------------------
    # K/M 装配与广义特征求解都在 FEM-Eigen 内核里完成（线性内核用成桥态
    # 构型 + 线弹性刚度，索承体系用成桥态一致切线刚度）。这里只存内核
    # 输出的解析结果，形状见 modal_3d.parse_modal_section：
    #   {"nmodes", "ndof", "mass": {X,Y,Z}, "modes": [{order,f,T,mx,my,mz,
    #    mnorm,shape}], "error"}。
    # 空 dict = 本次结果根本没有模态数据（手工构造/旧序列化结果），
    # 此时不产生模态校核，也不假装"验过了"；真实求解总有这一段，
    # 内核算不出时会带 error 并给出 warn。
    modal: dict[str, Any] = field(default_factory=dict)

    # ---- 线性屈曲荷载因子 ----------------------------------------------
    # (K + λ·Kg)φ = 0 由 FEM-Eigen 线性内核求解；形状见
    # buckling_3d.parse_buckling_section：
    #   {"nmodes", "ndof", "basis", "ref": {...}, "status",
    #    "modes": [{order, lambda, pcr_kN}], "error"}。
    # 索承体系成桥态是几何非线性，内核只给 ERROR=nonlinear_state_not_supported；
    # 空 dict = 手工构造/旧序列化结果，此时不产生屈曲校核，也不假装验过。
    buckling: dict[str, Any] = field(default_factory=dict)

    # ---- 索长迭代 / 成桥态几何闭合过程量 --------------------------------
    # 内核 fem_3d_nl.hpp 的 IterationTrace3D → [LENGTH] 段，形状见
    # cable_length_3d.parse_cable_length_section：
    #   {"kind", "status", "err_kind", "niter", "tol", "converged",
    #    "sens", "steps", "cables", "nj", "J", "error"}。
    # 线性梁格 / 旧序列化结果为空 dict，此时不产生校核，也不假装收敛过。
    cable_length: dict[str, Any] = field(default_factory=dict)

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
        看得见，所以按绝对值挑。位移已不计冲击（冲击只放大内力包络）。
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
                    hint=f"阶段 {lw[0]}，JTG D60 车道荷载（不计冲击）"))
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
        # 模态卡片对三种内核都给：频率来自内核的 Kφ=λMφ，不是这里估的。
        md = self.modal or {}
        modes = md.get("modes") or []
        if modes:
            f1 = float(modes[0]["f"])
            vf = modal_3d.vertical_fundamental(md)
            ratios = modal_3d.cumulative_ratio(md, "Z")
            hint = f"T = {modes[0]['T']:.3f} s；共 {len(modes)} 阶"
            if vf:
                hint += f"；竖向基频 {vf:.3f} Hz"
            if ratios:
                hint += f"；前 {len(modes)} 阶累计竖向参与质量 {ratios[-1] * 100:.0f}%"
            out.append(Metric("一阶自振频率", f"{f1:.3f}", "Hz", hint=hint))
        elif md.get("error"):
            out.append(Metric(
                "一阶自振频率", "未产出",
                hint=modal_3d.ERROR_TEXT.get(md["error"], md["error"])))
        # 屈曲：λ 是荷载放大倍数，不是规范安全系数。没有 λ 时按内核
        # STATUS / ERROR 写明是"物理上无压杆"还是"本内核不适用"。
        bk = self.buckling or {}
        lam1 = buckling_3d.first_factor(bk)
        if lam1 is not None:
            out.append(Metric(
                "一阶屈曲因子 λ₁", f"{lam1:.2f}",
                hint=f"Pcr = {bk['modes'][0]['pcr_kN']:,.0f} kN；"
                     f"参考荷载 {bk['ref']['pref_kN']:,.0f} kN；"
                     f"共 {len(bk['modes'])} 阶"))
        elif bk:
            out.append(Metric(
                "一阶屈曲因子 λ₁", "未产出",
                hint=buckling_3d.status_note(bk)))
        # 成桥态迭代：只陈述"几轮、末轮误差多少、哪种口径"的事实，
        # 不把"收敛了"包装成设计合格判定。
        cl = self.cable_length or {}
        if cl.get("steps") and not cl.get("error"):
            fe = cable_length_3d.final_error(cl)
            unit = " m" if cl.get("err_kind") == "closure_m" else ""
            out.append(Metric(
                "成桥态迭代", f"{cl.get('niter', 0)} 轮",
                hint=f"末轮控制误差 {fe:.3g}{unit}"
                     f"（{cable_length_3d.err_unit(cl)}）；"
                     + cable_length_3d.status_note(cl)))
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
        """组合是否可用、量级是否合理。承载力判定不在这里——由
        `_checks_capacity` 按模型里的配筋信息做。"""
        if self.kind != "grillage3d":
            # 索承体系：活载组合已接入。组合内力由 combination.cable_combinations
            # 生成（恒载 [GIRDEREND] + 活载包络 [GIRDER_LIVE]），组合应力验算
            # 在 _checks_strength_cable 里做，这里只给一句状态说明。
            if self.girder_live:
                return [Check(
                    "运营期荷载组合", True,
                    f"{self.kind_label}已接入活载组合：恒载（[GIRDEREND]）与"
                    "活载包络（[GIRDER_LIVE]）按 JTG D60 系数组合（基本/频遇/"
                    "准永久），组合应力验算见「正应力 ≤ fd（基本组合）」等条目。",
                    level="info")]
            return [Check(
                "运营期荷载组合", True,
                f"{self.kind_label}结果里没有活载内力包络（[GIRDER_LIVE] 缺失）："
                "模型未定义车道荷载或旧内核，组合退化为恒载单项。",
                level="info")]
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
            note = _impact_note(self.meta)
            if note:
                out.append(Check(
                    "汽车冲击系数", True,
                    note.lstrip("，") + "。未手写冲击系数时，由竖向基频按 JTG D60 计算。",
                    value=float(self.meta["impact_mu"]), level="info"))
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
        out += self._checks_girder_live(model)
        out += self._checks_stage_cable(model)
        out += self._checks_stage_prestress(model)
        out += self._checks_modal()
        out += self._checks_buckling()
        out += self._checks_cable_length()
        return out

    def _checks_cable_length(self) -> list[Check]:
        """索长迭代 / 几何闭合过程量的自检与工程提示。

        判的是"逐轮数据对不对、有没有收敛、两种误差口径有没有混用"，
        **不是**"成桥态设计合不合格"——规范没给迭代轮数或收敛率的
        合格限值。只有真的未收敛 / 数据自相矛盾才给 warn。
        """
        cl = self.cable_length or {}
        if not cl:
            return []                     # 线性梁格 / 旧结果：没有就不假装验过
        # 无外层迭代（STATIC / 悬索桥找形）是设计边界，不是求解失败；
        # 标 warn 会让校核汇总凭空多一条"不通过"。
        if cl.get("error") in ("no_iteration_static_mode",
                               "no_iteration_suspension_formfinding",
                               "no_length_section"):
            return [Check("索长迭代与成桥态几何闭合", True,
                          cable_length_3d.status_note(cl)
                          + " 本次报告不给逐轮收敛表。", level="info")]
        issues = cable_length_3d.validate(cl)
        if issues:
            return [Check("索长迭代与成桥态几何闭合", False,
                          "；".join(issues), level="warn")]
        fe = cable_length_3d.final_error(cl)
        rate = cable_length_3d.mean_convergence_rate(cl)
        out = [Check(
            "成桥态外层迭代收敛", True,
            f"{cl.get('niter', 0)} 轮（上限 {cl.get('maxiter', 0)}）收敛，"
            f"末轮控制误差 {fe:.3g}，容差 {cl.get('tol', 0):.3g}；"
            + (f"相邻轮误差比几何均值 {rate:.3g}。" if rate is not None else "")
            + " 该结论只说明数值收敛，不构成设计规范意义上的合格判定。",
            level="info")]
        out.append(Check(
            "迭代口径", True, cable_length_3d.mode_note(cl), level="info"))
        sens = cl.get("sens") or {}
        if sens.get("j_available") or sens.get("j_rebuild"):
            out.append(Check(
                "影响矩阵 / 敏感度", True,
                cable_length_3d.sens_note(cl), level="info"))
        # TENSION 模式索力命中但线形未闭合：明确写出来，别让读者把
        # "收敛"误读成"制造线形已给出"。
        if cl.get("kind") == "cable_length" and cl.get("steps"):
            geom = float(cl["steps"][-1]["geom_err"])
            out.append(Check(
                "索力命中与线形闭合的区别", True,
                f"末轮成桥弦长相对设计弦长最大偏差 {geom:.4g} m；索长迭代"
                "只命中索力，制造线形（预抬高）需 MODE=COMPLETION 的"
                "几何闭合迭代，两者不是同一件事。",
                level="info"))
        return out

    def _checks_modal(self) -> list[Check]:
        """模态结果的自检与工程提示。

        这里判的是"数据对不对、和冲击系数是不是同一次算的、截断够不够"，
        不是"频率合不合格"——规范没给自振频率的通过限值，硬编一个就是
        假校核。所以三条都是 info 级陈述；只有内核报错/频率非正/与
        f1_vertical 不同源才算问题，给 warn。
        """
        md = self.modal or {}
        if not md:
            return []                     # 手工构造/旧结果：没有就不假装验过
        try:
            ref = float(self.meta.get("f1_vertical") or 0.0) or None
        except (TypeError, ValueError):
            ref = None
        issues = modal_3d.validate(md, ref)
        if issues:
            return [Check("模态与自振特性", False,
                          "；".join(issues), level="warn")]
        modes = md["modes"]
        out = [Check(
            "一阶自振频率", modes[0]["f"] > 0,
            f"f₁ = {modes[0]['f']:.4f} Hz（T = {modes[0]['T']:.4f} s），"
            f"共 {len(modes)} 阶；Kφ = λMφ 由 FEM-Eigen 内核求解",
            level="info")]
        if ref:
            fs = [float(m["f"]) for m in modes]
            hit = min(fs, key=lambda f: abs(f - ref))
            out.append(Check(
                "模态与冲击系数同源", True,
                f"f1_vertical = {ref:.5f} Hz 对应第 {fs.index(hit) + 1} 阶 "
                f"{hit:.5f} Hz；JTG D60 冲击系数 μ 与各阶频率出自同一次特征求解",
                level="info"))
        ratios = modal_3d.cumulative_ratio(md, "Z")
        if ratios:
            out.append(Check(
                "竖向参与质量覆盖率", True,
                modal_3d.participation_note(md),
                value=ratios[-1], unit="1", level="info"))
        return out

    def _checks_buckling(self) -> list[Check]:
        """线性屈曲结果的自检与工程提示。

        这里判的是"λ 与 P_ref 同不同源、内核状态是什么、参考荷载有没有
        超过临界荷载"，**不是**"稳定验算合不合格"——规范没有 λ 的合格
        限值，硬编一个就是假校核。唯一够格判"不通过"的是数学事实
        λ₁ ≤ 1（参考荷载已达到/超过线性临界荷载）。
        """
        bk = self.buckling or {}
        if not bk:
            return []                     # 手工构造/旧结果：没有就不假装验过
        issues = buckling_3d.validate(bk)
        lam = buckling_3d.first_factor(bk)
        # 索承体系"线弹性屈曲不适用"是**设计边界**，不是这次求解算砸了。
        # 标成 warn/不通过会让工程校核汇总凭空多一条"不通过"，读者以为
        # 结果有问题；用 info 级把边界写清楚，报告章与 diagnostics 照样
        # 明确写"本次未产出"，不静默。
        if bk.get("error") == "nonlinear_state_not_supported":
            return [Check(
                "线性屈曲与稳定", True,
                buckling_3d.status_note(bk)
                + " 构件稳定仍按 JTG 3362-2018 §5.3 在「工程校核」验算，"
                  "整体稳定需另做非线性稳定全过程分析。",
                level="info")]
        hard = bool(bk.get("error")) or bk.get("status") in (
            "no_modes", "no_free_dof", "cholesky_failed")
        if issues and hard:
            return [Check("线性屈曲与稳定", False,
                          "；".join(issues), level="warn")]
        if lam is None:
            # STATUS=no_compression：参考荷载下没有受压杆件，本来就没有
            # 压屈模态。写清楚，别让用户以为"安全系数无限大"。
            return [Check(
                "线性屈曲荷载因子", True,
                buckling_3d.status_note(bk)
                + " 本次未产出 λ，不构成稳定验算结论。",
                level="info")]
        out = [Check(
            "一阶线性屈曲荷载因子", True,
            buckling_3d.load_note(bk),
            value=lam, unit="1", level="info")]
        out.append(Check(
            "线性屈曲适用范围", True,
            "λ 是参考荷载整体放大倍数，不是 JTG 3362-2018 §5.3 的构件稳定"
            "系数 φ；规范没有 λ 的合格限值。线性屈曲不含初始缺陷、材料非线性"
            "与几何非线性，是稳定承载力的上限。构件稳定仍按 §5.3 验算，整体"
            "稳定需另做非线性全过程（荷载–位移极值点）分析。",
            level="info"))
        if lam <= 1.0:
            out.append(Check(
                "参考荷载与临界荷载", False,
                f"λ₁ = {lam:.4f} ≤ 1：按 (K+λ·Kg)φ=0，参考荷载已达到或超过"
                "线性临界荷载。这里的 1 是数学分界，不是规范限值；须复核"
                "荷载/约束/构型并改做非线性稳定分析。",
                value=lam, limit=1.0, unit="1", level="warn"))
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

        # 临时索（被 DEACTIVE 拆掉的，如拱肋吊装的斜拉扣索）：合拢后就退出工作，
        # 其「末阶段 vs 设计目标」的偏差是"结构闭合后卸载"的施工物理，不是
        # 调索失败——那条判据只对永久索（成桥后仍在工作）有意义。拆索前只要
        # 全程受拉、不超上限即可。
        temp_elems: set[int] = set()
        if model is not None:
            for s in getattr(model, "stages", []) or []:
                for e in getattr(s, "deactivate", []) or []:
                    try:
                        temp_elems.add(int(e))
                    except (TypeError, ValueError):
                        continue

        # ---- 1. 全程不出现受压 -------------------------------------------
        # 永久索受压 = 施工顺序不成立（error）；临时索（合拢后 DEACTIVE 拆除）
        # 受压 = 拆除前已退出工作、被闭合后的结构接管，属正常施工物理，降级为
        # 提示（warn）——不是"这顺序错了"，而是"这几根临时索在拆除前已松弛"。
        neg = [r for r in rows if r["Tmin"] <= 0.0]
        neg_perm = [r for r in neg if r["elem"] not in temp_elems]
        neg_temp = [r for r in neg if r["elem"] in temp_elems]
        if neg_perm:
            worst = min(neg_perm, key=lambda r: r["Tmin"])
            detail = (f"{len(neg_perm)} 根索全过程出现过轴力 ≤ 0，最小 "
                      f"{worst['Tmin']/1e3:.0f} kN（单元 {worst['elem']}，"
                      f"阶段 {worst['TminStage']}）；线性阶段把索当桁架，允许受压"
                      "——真实索受压即退出工作，该施工顺序在这几根索上不成立")
            out.append(Check("施工阶段索不出现受压（全过程）", False, detail,
                             level="error"))
        elif neg_temp:
            worst = min(neg_temp, key=lambda r: r["Tmin"])
            detail = (f"{len(neg_temp)} 根临时索在拆除前轴力松弛到 ≤ 0（最小 "
                      f"{worst['Tmin']/1e3:.0f} kN，单元 {worst['elem']}，阶段 "
                      f"{worst['TminStage']}）；临时索合拢后被闭合结构接管，"
                      "先张卸载到松弛属正常施工物理，不构成受压破坏")
            out.append(Check("施工阶段索不出现受压（全过程）", True, detail,
                             level="warn"))
        else:
            detail = (f"{len(rows)} 根索在 {len(all_stages)} 个阶段"
                      f"（{'、'.join(map(str, all_stages))}）内全部受拉，"
                      f"最小 {min(r['Tmin'] for r in rows)/1e3:.0f} kN")
            out.append(Check("施工阶段索不出现受压（全过程）", True, detail,
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
        # 只对永久索判定：临时索在末阶段已被（或即将被）拆除，末阶段索力偏离
        # 目标是"先张卸载"的正常施工物理，不构成调索失败。
        perm = [r for r in rows if r["Tdes"] > 0.0 and r["elem"] not in temp_elems]
        n_temp = sum(1 for r in rows if r["Tdes"] > 0.0 and r["elem"] in temp_elems)
        if perm:
            r = max(perm, key=lambda x: x["devLast"])
            dev = r["devLast"]
            detail = (f"最大偏差 {dev*100:.2f}%（单元 {r['elem']}："
                      f"{r['Tlast']/1e3:.0f} / {r['Tdes']/1e3:.0f} kN）。"
                      + ("" if dev <= 0.02 else
                         "分批张拉时先张的索会被后张的带跑，属正常现象——"
                         "按目标再补调一轮（加一个只给 TENSION= 的阶段）即可拉回。"))
            if n_temp:
                detail += f"（另有 {n_temp} 根临时索在合拢后拆除，不参与本判据）"
            out.append(Check(
                f"末阶段索力 vs 设计目标（{len(perm)} 根）",
                dev <= 0.02, detail,
                value=dev, limit=0.02,
                level="warn" if dev <= 0.02 else "error"))
        elif n_temp:
            out.append(Check(
                f"末阶段索力 vs 设计目标（0 根永久索）", True,
                f"全部 {n_temp} 根为临时索（合拢后 DEACTIVE 拆除），"
                "末阶段卸载是正常施工物理，无永久索需要命中设计目标",
                level="info"))

        # ---- 4. 全过程峰值 vs 设计目标 ------------------------------------
        # 临时索也参与这条：张拉目标就是"吊住该段"，峰值偏离目标意味着张拉量
        # 给错了（超张）或被后续吊装带偏（先张卸载），两者都要在过程中暴露。
        all_tgt = [r for r in rows if r["Tdes"] > 0.0]
        peak = [r for r in all_tgt if r["ratio"] is not None]
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

    def _checks_stage_prestress(self, model: Any = None) -> list[Check]:
        """施工阶段预应力的校核。

        与索力不同，预应力没有"目标力"要命中——它就是给定张拉力、扣损失后
        施加。所以这里只回答三个问题：

        1. 异常：内核报的 Pe 必须为正（P>0、loss∈(0,1] 时不可能为负，出现
           非正说明解析或配置错了）。
        2. 张拉了多少、有效预加力多少——让"这一阶段到底干了什么"有数。
        3. 模型声明了预应力、结果里却没有 → 旧内核没编译，明说"没做"。

        张拉控制应力：只有本束给了面积 Ap 才比较 σpe = Pe/Ap 与 0.75 fpk。
        没给面积就明确写成「没做」，不用一个缺省面积把校核做成通过。
        """
        out: list[Check] = []
        declared = 0
        if model is not None:
            for s in getattr(model, "stages", []) or []:
                declared += len(getattr(s, "prestress", []) or [])
        if not self.stage_prestress:
            if declared:
                out.append(Check(
                    "施工阶段预应力结果", False,
                    f"模型声明了 {declared} 束预应力，但结果里没有 stage prestress "
                    "段——多半是旧版 example_3d_grillage.exe，重新编译内核。"
                    "本次没做预应力校核，这不是「通过」，是「没做」。",
                    level="info"))
            return out

        neg = [(s, p, v["Pe"]) for (s, p), v in self.stage_prestress.items()
               if v.get("Pe", 0.0) <= 0.0]
        out.append(Check(
            f"有效预加力为正（{len(self.stage_prestress)} 束·阶段）",
            not neg,
            (f"{len(neg)} 条记录 Pe ≤ 0：阶段 {neg[0][0]} 束 {neg[0][1]} "
             f"Pe={neg[0][2]:.0f} N，P 或 loss 配置有误" if neg else
             f"{len(self.stage_prestress)} 条记录全部为正"),
            level="error" if neg else "info"))

        stages = sorted({s for s, _ in self.stage_prestress})
        last = stages[-1]
        last_pe = [v["Pe"] for (s, _), v in self.stage_prestress.items() if s == last]
        out.append(Check(
            f"阶段 {last} 有效预加力合计", True,
            f"{len(last_pe)} 束，ΣPe = {sum(last_pe)/1e3:,.0f} kN（损失后）",
            value=sum(last_pe), unit="N", level="info"))
        out.extend(self._checks_strand_stress(model))
        return out

    def _checks_strand_stress(self, model: Any = None) -> list[Check]:
        """σpe = Pe/Ap 与 0.75 fpk 比较。没给 Ap 的束不参与，也不算通过。"""
        tendons = []
        if model is not None:
            for s in getattr(model, "stages", []) or []:
                for i, ps in enumerate(getattr(s, "prestress", []) or [], 1):
                    tendons.append((getattr(s, "id", 0), i, ps))
        given = [(sid, i, ps) for sid, i, ps in tendons if float(getattr(ps, "Ap", 0) or 0) > 0]
        if not given:
            if tendons:
                return [Check(
                    "张拉控制应力", True,
                    "未给钢束面积 Ap，不做 σpe ≤ 0.75 fpk 校核。"
                    "这不是「通过」，是「没做」。",
                    level="info")]
            return []
        rows = []
        for sid, i, ps in given:
            pe = self.stage_prestress.get((sid, i), {}).get("Pe")
            if pe is None:
                continue
            ap = float(ps.Ap)
            fpk = float(getattr(ps, "fpk", 0) or 0) or STRAND_FPK_DEFAULT
            cap = STRAND_CONTROL_RATIO * fpk
            rows.append((sid, i, pe / ap, cap, ap, fpk))
        if not rows:
            return [Check(
                "张拉控制应力", True,
                "给了钢束面积，但结果里没有对应的有效预加力，本次没做应力校核。",
                level="info")]
        worst = max(rows, key=lambda r: r[2] / r[3])
        ok = all(sig <= cap * (1.0 + 1e-9) for _, _, sig, cap, _, _ in rows)
        sid, i, sig, cap, ap, fpk = worst
        return [Check(
            "有效预应力未超过 0.75 fpk",
            ok,
            f"阶段 {sid} 束 {i}：σpe = Pe/Ap = {sig/1e6:.0f} MPa，"
            f"上限 {STRAND_CONTROL_RATIO:g}×{fpk/1e6:.0f} = {cap/1e6:.0f} MPa"
            f"（Ap = {ap*1e6:.0f} mm²）。"
            "这是有效应力与钢绞线张拉控制应力上限的比较，不是分项损失验算。",
            value=sig, limit=cap, unit="Pa",
            level="error" if not ok else "info")]

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
                        "混凝土梁的抗剪由斜截面承载力控制（JTG 3362 §5.2.9），"
                        "已配筋时见下方「斜截面抗剪 Vu」条目；此处 τ 只作量级。",
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
                "（规范靠承载力与裂缝宽度控制），故 0.5fck 只作量级判据、"
                "不构成「验算通过」；正截面/斜截面承载力验算见下方条目。",
                level="info"))
        if len(ranked) > len(shown):
            out.append(Check(
                "应力验算覆盖范围", True,
                f"模型含 {len(ranked)} 个梁截面，上列只给利用率最高的 "
                f"{len(shown)} 个；其余 {len(ranked) - len(shown)} 个截面的"
                "利用率更低（横梁、隔板在纵向弯曲中基本不参与）。",
                level="info"))
        out.extend(self._checks_capacity(model, ult))
        return out

    def _checks_capacity(self, model: Any, ult: Any) -> list[Check]:
        """承载力验算（混凝土配筋截面）：按受力状态分派三条链。

        用基本组合的设计内力（N / My / Vz 包络，取绝对值大者）。受压轴力
        显著（>5% 截面轴压承载力，如桥塔）时按偏心距再分：偏心距 <1% 截面
        高走轴心受压 §5.3.1，否则走偏心受压 §5.3.4；轴力≈0（如主梁）走
        正截面抗弯 §5.2.2/5.2.3 + 斜截面 §5.2.9。只覆盖填了配筋的混凝土
        截面；钢截面走应力验算，未配筋的混凝土截面不判定。

        兼容两种组合行结构：梁格 ``beam_combinations`` 的轴力是单值 ``N``，
        索承体系 ``cable_combinations`` 的轴力是包络 ``NMin/NMax``。
        """
        # 分派只做一次：工程校核（这里）与计算书明细表（tables）共用同一份
        # `_capacity_cases`，避免两条链路各自判断"受弯还是压弯"而漂移。
        cases = _capacity_cases(model, ult)
        if not cases:
            return []

        ranked_axial = [(c.result.util, c.label, (c.wn,), c.result)
                        for c in cases if c.chain == "axial"]
        ranked_ecc = [(c.result.util, c.label, (c.wn, c.wm_ctrl), c.result)
                      for c in cases if c.chain == "eccentric"]
        ranked_flex = [(c.result.util, c.label, (c.wm_ctrl, c.wv), c.result)
                       for c in cases if c.chain == "flex"]

        out: list[Check] = []

        # ---- 受弯验算（主梁等轴力≈0 的构件）----
        if ranked_flex:
            ranked_flex.sort(key=lambda t: -t[0])
            for _, label, (wm, wv), r in ranked_flex[:3]:
                if r.Mu > 0 or math.isinf(r.util_M):
                    src = f"单元 {wm[1]}·{wm[2]} 端" if wm else "—"
                    util_s = "∞" if math.isinf(r.util_M) else f"{r.util_M:.3f}"
                    mu_s = "—" if r.Mu <= 0 else f"{r.Mu / 1e3:,.0f}"
                    out.append(Check(
                        f"{label} 正截面抗弯 Mu",
                        r.util_M <= 1.0,
                        f"{src} γ0·|Md| = {abs(wm[0]) / 1e3:,.0f} kN·m ≤ "
                        f"Mu = {mu_s} kN·m，利用率 {util_s}"
                        f"{'（超筋）' if r.over_reinforced else ''}；"
                        f"控制区 {r.zone}（正弯矩区 Mu="
                        f"{r.Mu_pos / 1e3:,.0f} kN·m / 利用率 "
                        f"{section_strength._fmt_util(r.util_M_pos)}，"
                        f"负弯矩区 Mu={r.Mu_neg / 1e3:,.0f} kN·m / 利用率 "
                        f"{section_strength._fmt_util(r.util_M_neg)}）；"
                        f"JTG 3362-2018 §5.2.2/5.2.3",
                        value=r.util_M, limit=1.0, level="error"))
                if r.Vu > 0:
                    src = f"单元 {wv[1]}·{wv[2]} 端" if wv else "—"
                    out.append(Check(
                        f"{label} 斜截面抗剪 Vu",
                        r.util_V <= 1.0,
                        f"{src} γ0·|Vd| = {abs(wv[0]) / 1e3:,.0f} kN ≤ "
                        f"Vu = {r.Vu / 1e3:,.0f} kN，利用率 {r.util_V:.3f}；"
                        f"JTG 3362-2018 §5.2.9（仅配箍筋，α1=1.0、α3=1.1）",
                        value=r.util_V, limit=1.0, level="error"))
                if r.note:
                    out.append(Check(
                        f"{label} 承载力验算说明", True, r.note, level="info"))

        # ---- 轴心受压验算（桥塔等弯矩≈0 的构件）----
        if ranked_axial:
            ranked_axial.sort(key=lambda t: -t[0])
            for _, label, (wn,), r in ranked_axial[:3]:
                lam_s = (f"λ={r.lam_kind}={r.lam:.0f}" if r.lam > 0
                         else "λ 未计（未给计算长度，按短柱 φ=1）")
                out.append(Check(
                    f"{label} 轴心受压正截面",
                    r.util <= 1.0,
                    f"单元 {wn[1]}·{wn[2]} 端 N = {r.N / 1e3:,.0f} kN ≤ "
                    f"Nu = {r.Nu / 1e3:,.0f} kN（0.9·φ·(fcd·A+f'sd·A's)，"
                    f"φ={r.phi:.3f}，{lam_s}），利用率 {r.util:.3f}；"
                    f"JTG 3362-2018 §5.3.1",
                    value=r.util, limit=1.0, level="error"))

        # ---- 偏心受压验算（压弯构件，弯矩显著）----
        if ranked_ecc:
            ranked_ecc.sort(key=lambda t: -t[0])
            for _, label, (wn, wm), r in ranked_ecc[:3]:
                if r.Mu > 0 or math.isinf(r.util):
                    util_s = "∞" if math.isinf(r.util) else f"{r.util:.3f}"
                    src = f"单元 {wn[1]}·{wn[2]} 端" if wn else "—"
                    out.append(Check(
                        f"{label} 偏心受压（压弯）正截面",
                        r.util <= 1.0 and not math.isinf(r.util),
                        f"{src} N = {r.N / 1e3:,.0f} kN、M = {r.M / 1e3:,.0f} "
                        f"kN·m，{r.mode}，x = {r.x:.0f} mm，σs = "
                        f"{r.sigma_s:+.0f} MPa；该轴力下 Mu = "
                        f"{r.Mu / 1e3:,.0f} kN·m，利用率 {util_s}；"
                        f"JTG 3362-2018 §5.3.4/5.3.5"
                        f"{'（非对称配筋，含 §5.3.4-4/5 反向破坏验算）' if r.asymmetric else '（对称配筋）'}",
                        value=r.util, limit=1.0, level="error"))
                if r.note:
                    out.append(Check(
                        f"{label} 压弯验算说明", True, r.note, level="info"))

        out.append(Check(
            "承载力验算适用范围", True,
            "受弯按矩形/工形（受压翼缘在受压区）单筋 §5.2.2/5.2.3、斜截面"
            "按仅配箍筋 §5.2.9；正、负弯矩分别按顶板/底板受压面各算一次 Mu，"
            "取利用率大者控制（不赌梁单元局部坐标朝向）。未登记负弯矩面的"
            "截面按正弯矩面对称近似，并在验算说明里提示——底板较窄或顶板"
            "纵筋较少时该近似偏不安全。轴力显著（>5% 轴压承载力）时按偏心距"
            "自动分派轴心受压 §5.3.1（偏心距 <1% 截面高）或偏心受压 "
            "§5.3.4/5.3.5；压弯支持非对称配筋，小偏心另按 §5.3.4-4/5 验算"
            "反向破坏，工形/箱形非对称配筋的反向破坏（含受压较小边翼缘项）"
            "未覆盖、会在结果里标明。均为普通钢筋混凝土（无预应力、无弯起"
            "钢筋）。稳定系数 φ 按长细比查表 5.3.1（矩形用 l0/b、箱形用 "
            "l0/i）、二阶效应 η 按 §5.3.9；未提供计算长度 l0 时按短柱 "
            "φ=1、η=1（不静默，λ 标注为「未计」）。",
            level="info"))
        return out

    def _checks_strength_cable(self, model: Any) -> list[Check]:
        """索承体系（斜拉桥 / 悬索桥）的组合应力验算。

        内力来自两段：恒载成桥态单值（`[GIRDEREND]`）+ 活载包络
        （`[GIRDER_LIVE]`），组合由 `combination.cable_combinations` 生成
        （基本 / 频遇 / 准永久三种）。与梁格那条链路的差别是：活载轴力 N
        也有包络（自锚式梁轴力 ≈ 主缆水平力 H，活载会改变 H），所以正应力
        要枚举 N×My 的 4 个角点取最不利——这是包络法固有近似，写进口径条目。

        基本组合（承载力）按钢 fd / 混凝土 0.5fck 判：钢超限标 error（硬规范，
        JTG D64 §5.3.1），混凝土超限标 warn（0.5fck 是量级判据）。频遇 /
        准永久组合属正常使用，只给控制截面的利用率，不做逐项判定。

        塔柱也在验算范围内：它同样是 BEAM、同样被压弯，实常数里没有截面
        模量（W=0），应力验算只算轴应力；其承载力另按 §5.3.1 轴心受压 /
        §5.3.4 偏心受压验算（见 `_checks_capacity`），不静默。
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
        combos = combination.cable_combinations(self)
        if not combos:
            return []

        # 对某种组合，把每个截面的最不利角点应力排名。
        # 元素 = (util, label, sec, (eid, end, StressResult))
        def _rank(combo: Any) -> list[tuple[float, str, Any, tuple[int, int, Any]]]:
            ranked: list[tuple[float, str, Any, tuple[int, int, Any]]] = []
            for label, sec, eids in secs:
                worst: tuple[int, int, Any] | None = None
                for (eid, end), row in combo.rows.items():
                    if eid not in eids:
                        continue
                    r = _combo_stress(sec, row)
                    if worst is None or r.util > worst[2].util:
                        worst = (eid, end, r)
                if worst is not None:
                    ranked.append((worst[2].util, label, sec, worst))
            ranked.sort(key=lambda t: -t[0])
            return ranked

        ult = _rank(combos[0])          # 基本组合（承载力）
        if not ult:
            return [Check(
                "截面应力验算", True,
                "梁端内力与模型的梁截面没对上（单元号或截面定义变了？），"
                "本次不做应力验算", level="info")]

        out: list[Check] = []
        # 基本组合详列：利用率最高的截面，正应力 / 剪应力 / 折算。
        shown = [t for t in ult if t[0] >= 0.02][:3] or ult[:1]
        # 主梁总要出现。索承体系的恒载分配常常让主梁利用率低到 2% 以下
        # （地锚式悬索桥的加劲梁在恒载下本该几乎不受力），只按利用率筛选
        # 会把它筛掉——而它恰恰是设计人要看的构件，塔不是。
        main = next((t for t in ult if t[1].startswith("主梁")), None)
        if main is not None and not any(t[1] == main[1] for t in shown):
            shown = (shown[:2] + [main]) if len(shown) >= 3 else shown + [main]
        for top, label, sec, (eid, end, r) in shown:
            steel = sec.kind == "steel"
            sym = "fd" if steel else "0.5fck"
            no_w = sec.WT <= 0.0 and sec.WB <= 0.0
            out.append(Check(
                f"{label} 正应力 ≤ {sym}（基本组合 1.2G+1.4Q）",
                r.util_normal <= 1.0,
                f"单元 {eid}·{end} 端 σ = {r.sigma_top:+.1f} / "
                f"{r.sigma_bot:+.1f} MPa（上/下缘，拉正压负），"
                f"利用率 {r.util_normal:.3f}；{r.limit_desc}。"
                + ("**该截面未给截面模量（WT/WB=0），应力验算只算轴应力、"
                   "弯曲应力未计；承载力按轴心/偏心受压另验（见下方条目）。**"
                   if no_w else ""),
                value=r.util_normal, limit=1.0,
                level="error" if steel else "warn"))

            if sec.AW > 0.0:
                if steel:
                    out.append(Check(
                        f"{label} 剪应力 ≤ fvd（基本组合）",
                        r.util_shear <= 1.0,
                        f"单元 {eid}·{end} 端 τ = V/AW = {r.tau:.1f} MPa，"
                        f"利用率 {r.util_shear:.3f}；{r.limit_desc}",
                        value=r.util_shear, limit=1.0, level="error"))
                else:
                    out.append(Check(
                        f"{label} 腹板剪应力（量级）", True,
                        f"单元 {eid}·{end} 端 τ = V/AW = {r.tau:.1f} MPa。"
                        "混凝土梁的抗剪须按斜截面承载力验算（JTG 3362 "
                        "§5.2.9，箍筋 + 混凝土贡献），本条只是应力量级、"
                        "不给利用率也不下结论；填了箍筋的截面另见"
                        "「斜截面抗剪 Vu」校核条目。",
                        value=r.tau, unit="MPa", level="info"))

            if steel:
                out.append(Check(
                    f"{label} 折算应力利用率 ≤ 1.0（基本组合）",
                    r.util_eq <= 1.0,
                    f"单元 {eid}·{end} 端 √((σx/fd)²+(τ/fvd)²) = "
                    f"{r.util_eq:.3f}（σx={r.sigma_top:+.1f} 或 "
                    f"{r.sigma_bot:+.1f} MPa，τ={r.tau:.1f} MPa）；"
                    "JTG D64-2015 §5.3.1 第 4 款",
                    value=r.util_eq, limit=1.0, level="error"))

        # 频遇 / 准永久组合：只给控制截面的利用率，不做逐项判定。
        for combo in combos[1:]:
            fr = _rank(combo)
            if not fr:
                continue
            top, label, sec, (eid, end, r) = fr[0]
            out.append(Check(
                f"{label} 正应力利用率（{combo.spec.name}）",
                r.util_normal <= 1.0,
                f"单元 {eid}·{end} 端 σ = {r.sigma_top:+.1f} / "
                f"{r.sigma_bot:+.1f} MPa，利用率 {r.util_normal:.3f}；"
                f"{r.limit_desc}。{combo.spec.expr}，正常使用组合，"
                "仅作量级参考。",
                value=r.util_normal, limit=1.0,
                level="warn"))

        # 口径与边界只写一次。这段话比上面的利用率更重要：它是唯一能让人
        # 不把"组合应力合格"误读成"一切验算都通过"的地方。
        out.append(Check(
            "索承体系组合应力验算的口径", True,
            f"内力取自成桥态恒载（{len(self.girder_ends) // 2} 根梁两端）与"
            "活载包络（[GIRDER_LIVE]，成桥态切线刚度影响线），按 JTG D60 "
            "系数组合。正应力枚举 N×My 角点取最不利——N/My 活载极值来自"
            "不同加载位置，角点组合偏保守；梁几何刚度（P-Δ）按一阶略去。"
            "活载横向布载按横向分布系数法计入（车道数 × 多车道横向折减 × "
            "偏载增大，见模型 *LANE_3D 的 LANE_COUNT/REDUCTION/ECCENTRICITY；"
            "单梁模型算不出严格横向影响面，偏载系数为经验值）"
            f"{_impact_note(self.meta)}。",
            level="info"))
        if len(ult) > len(shown):
            out.append(Check(
                "应力验算覆盖范围", True,
                f"模型含 {len(ult)} 个梁截面，上列只给利用率最高的 "
                f"{len(shown)} 个；其余 {len(ult) - len(shown)} 个更低。",
                level="info"))
        # 承载力验算（混凝土配筋截面：桥塔轴心受压 / 偏心受压，加劲梁受弯）。
        # 塔柱是混凝土 C50、已配筋，这里按 §5.3.1/§5.3.4 补上承载力结论，
        # 替代此前"塔柱只算轴应力、无承载力判定"的空缺。
        out.extend(self._checks_capacity(model, combos[0]))
        return out

    def _checks_girder_live(self, model: Any = None) -> list[Check]:
        """活载梁内力包络（成桥态切线刚度影响线）的校核。

        只回答两件事：
        1. 有没有活载包络——缺 = 模型没定义车道或旧内核，如实说「没做」。
        2. 活载弯矩峰值在哪、多大——这是量级提示，不是判定：索承体系的
           恒载弯矩（尤其斜拉桥）本就接近零（最小弯曲能法的目标），所以
           「活载/恒载弯矩比」没有意义，不能拿它当判据。真正的判定是组合
           应力（基本/频遇/准永久），见「组合应力验算」校核项。
        """
        if self.kind not in ("cable_stayed", "suspension"):
            return []
        if not self.girder_live:
            return [Check(
                "活载梁内力包络", True,
                "结果里没有活载内力包络（[GIRDER_LIVE] 段缺失）：模型未定义"
                "车道荷载（LANE_3D）或旧版 cable_solver.exe。本次只算恒载，"
                "不做活载内力——这不是「通过」，是「没做」。",
                level="info")]
        out: list[Check] = []
        # 活载弯矩峰值：量级提示（不设限值、不做承载力判定）。
        worst = None
        for (eid, end), lv in self.girder_live.items():
            my = max(abs(float(lv.get("MyMax", 0.0))),
                     abs(float(lv.get("MyMin", 0.0))))
            if worst is None or my > worst[0]:
                worst = (my, eid, end)
        if worst is not None:
            out.append(Check(
                "活载弯矩峰值（量级提示）",
                True,
                f"主梁活载弯矩包络峰值 {worst[0]/1e3:.0f} kN·m，"
                f"位于单元 {worst[1]}·{worst[2]} 端。此为量级提示，"
                "承载力判定须做组合应力验算。",
                level="info"))
        out.append(Check(
            "活载内力包络口径", True,
            f"活载内力取自 {len(self.girder_live) // 2} 根主梁两端的包络，"
            "基于成桥态切线刚度影响线（JTG D60 车道荷载，横向布载按横向分布"
            "系数法计入——车道数 × 多车道横向折减 × 偏载增大"
            f"{_impact_note(self.meta)}），梁几何刚度（P-Δ）按一阶略去。"
            "该包络已按 JTG D60 系数接入组合应力验算（基本/频遇/准永久，见"
            "「组合应力验算」）。",
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
        # 表 4.3.2 的挠度不计冲击：内核位移包络已不乘 1+μ（冲击只放大内力），
        # 这里直接用包络值比对，无需再除。
        lw = self.live_worst()
        if lw is not None:
            sup = support_x(model)
            span = (max((b - a for a, b in zip(sup[:-1], sup[1:])), default=0.0)
                    if len(sup) >= 2 else 0.0)
            if span > 0:
                lim = span / 600.0
                bare = lw[1]
                out.append(Check(
                    "活载挠度 ≤ L₁/600（JTG D60 4.3.2）", abs(bare) <= lim,
                    f"阶段 {lw[0]} 影响点不计冲击 |{lw[1] * 1000:.2f}| mm ≤ "
                    f"{lim * 1000:.1f} mm（最大跨径 {span:.0f} m）",
                    value=abs(bare), limit=lim, unit="m"))
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

    @staticmethod
    def _capacity_tables(out: dict[str, dict[str, Any]], model: Any,
                         ult: Any) -> None:
        """承载力汇总表 + 逐项复核计算书，写进 `tables()` 的产出字典。

        梁格（组合内力来自 `beam_combinations`）与索承体系（来自
        `cable_combinations`）都调这一处；分派结果与 `_checks_capacity`
        共用 `_capacity_cases`，不会一边判轴压、一边出抗弯表。
        """
        if ult is None or not getattr(ult, "rows", None) or model is None:
            return
        cases = _capacity_cases(model, ult)
        if not cases:
            return
        cap_pairs = [(c.label, c.result) for c in cases
                     if c.chain == "flex"]
        if cap_pairs:
            out["capacity_table"] = \
                section_strength.capacity_table(cap_pairs)
            out["capacity_faces_table"] = \
                section_strength.capacity_faces_table(cap_pairs)
        ecc_pairs = [(c.label, c.result) for c in cases
                     if c.chain == "eccentric"]
        if ecc_pairs:
            out["eccentric_table"] = \
                section_strength.eccentric_table(ecc_pairs)
        ax_pairs = [(c.label, c.result) for c in cases
                    if c.chain == "axial"]
        if ax_pairs:
            out["axial_table"] = section_strength.axial_table(ax_pairs)
        calc_cases = [
            section_strength.CalcCase(
                label=c.label, chain=c.chain, sec=c.sec, result=c.result,
                N=c.N, M=c.M, Md=c.Md, Md_neg=c.Md_neg, Vd=c.Vd,
                gamma0=c.gamma0, alpha1=c.alpha1, alpha3=c.alpha3, l0=c.l0)
            for c in cases]
        out["capacity_calculation_sheet"] = \
            section_strength.capacity_calculation_sheet(calc_cases)

    def tables(self, model: Any = None) -> dict[str, dict[str, Any]]:
        """CSV / Word / HTML 共用的明细表。键名固定，缺数据就不给这张表。"""
        out: dict[str, dict[str, Any]] = {}
        x_of = {n.id: n.x for n in model.nodes} if model is not None else {}

        # 模态表与桥型无关：只要内核产出了模态就给。缺数据（旧内核 /
        # 未收敛 / 无质量）不给表，由 checks() 的 warn 写明原因，不摆一张
        # 空表或一行"—"冒充"验过了"。
        md = self.modal or {}
        if md.get("modes") and not md.get("error"):
            out["modal_table"] = modal_3d.modal_table(md)
        # 屈曲表：只在内核确实给出 λ 时给。no_compression / ERROR 段不给表，
        # 由 checks() 的 info/warn 写明原因，不摆一张只有表头的空表。
        bk = self.buckling or {}
        if bk.get("status") == "ok" and bk.get("modes") and not bk.get("error"):
            out["buckling_table"] = buckling_3d.buckling_table(bk)
        # 索长迭代 / 几何闭合：只在内核确实记录了逐轮过程时给表。
        # STATIC / 悬索桥找形走 ERROR 段，不给一张只有表头的空表。
        cl = self.cable_length or {}
        if cl.get("steps") and not cl.get("error"):
            out["cable_length_table"] = cable_length_3d.iteration_table(cl)
            if cl.get("cables"):
                out["cable_length_cables_table"] = cable_length_3d.cable_table(cl)

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

        # ---- 索承体系：截面应力（基本组合 1.2G+1.4Q）--------------------
        # 每个梁截面一行，取该截面下利用率最高的那一端。梁格那边没有这张表：
        # 它的应力是组合内力算的，逐截面组合表已经把内力给了，应力由校核条目
        # 承担；索承体系没有逐截面组合表，若再不给应力表，报告里就只剩几条校核文字。
        if (self.girder_ends and model is not None
                and self.kind in ("cable_stayed", "suspension")):
            combos = combination.cable_combinations(self)
            ult = next((c for c in combos if c.spec.key == "ultimate"), None)
            rows = []
            if ult is not None:
                for label, sec, eids in _strength_sections(model):
                    best: tuple[float, int, int, Any] | None = None
                    for (eid, end), row in ult.rows.items():
                        if eid not in eids:
                            continue
                        rr = _combo_stress(sec, row)
                        if best is None or rr.util > best[0]:
                            best = (rr.util, eid, end, rr)
                    if best is None:
                        continue
                    rows.append([label, f"{best[1]}·{best[2]}"] + best[3].to_row())
            if rows:
                out["stress_table"] = {
                    "title": "控制截面应力（基本组合 1.2G+1.4Q）",
                    "note": "正应力以受拉为正、受压为负，单位 MPa；利用率为"
                            "「效应/限值」，>1.0 即不满足。内力取成桥态恒载 + "
                            "活载包络的基本组合（1.2G+1.4Q），N 与 My 来自"
                            "**同一端**（同一截面），正应力枚举 N×My 角点取"
                            "最不利（包络法偏保守）。钢按 JTG D64-2015 §5.3.1 "
                            "三款；混凝土按 JTG 3362-2018 §7.1.5 的 0.5·fck。",
                    "headers": ["部位", "单元·端", "上缘 σ (MPa)", "下缘 σ (MPa)",
                                "τ (MPa)", "折算应力 (MPa)", "利用率", "控制项"],
                    "rows": rows,
                }
            # 承载力计算书：索承体系没有 beam_combinations，但同样有基本组合
            # 内力；桥塔的 φ、λ、σs、η 此前只出现在校核文字里，现在也逐项
            # 列出条文号，供复核。与 _checks_strength_cable 用同一个 ult。
            self._capacity_tables(out, model, ult)

        # ---- 施工阶段龄期 ------------------------------------------------
        # 悬臂序列把每段末龄期写在 Stage3D.time。计算书要列出来，否则
        # 徐变只发生在内核里，读报告的人不知道各段是第几天。
        if model is not None and any(float(getattr(s, "time", 0.0) or 0.0) > 0.0
                                     for s in getattr(model, "stages", []) or []):
            rows = []
            for s in model.stages:
                rows.append([
                    s.id,
                    _r(float(s.time), 1) if s.time else "—",
                    len(s.activate),
                    len(s.tensions),
                ])
            out["stage_schedule"] = {
                "title": "施工阶段龄期",
                "note": stage_time_effect_note(model, self.solver),
                "headers": ["阶段", "末龄期 (天)", "新激活单元", "本段张拉索"],
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
                        "激活体系逐车道求影响面后取包络，max 为向上、min 为向下。"
                        "位移不计冲击（表 4.3.2 挠度限值；冲击只放大内力包络）。",
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

        if self.erection:
            rows = []
            for r in self.erection:
                hmn = r.get("hmin", 0.0)
                hmx = r.get("hmax", 0.0)
                rows.append([
                    r["stage"], r.get("name", ""),
                    _r(float(r.get("H") or 0.0) / 1e3, 0),
                    _r(float(r.get("sag") or 0.0), 3),
                    _r(float(r.get("midz") or 0.0), 3),
                    _r(hmn / 1e3, 0) if hmn > 0 else "—",
                    _r(hmx / 1e3, 0) if hmx > 0 else "—",
                ])
            out["erection_table"] = {
                "title": "施工阶段主缆线形与索力",
                "note": "主缆下料长度 L0 一旦固定，各阶段的 H 与矢高由二分 L0 命中反算"
                        "（矢高是结果不是输入）。空缆矢高最小；吊装初期荷载集中在"
                        "跨中，矢高出现峰值；随吊装向塔侧推进、荷载摊开，矢高回落；"
                        "合拢后加二期铺装，矢高回升至成桥值。铺装态 H、矢高须与成桥态"
                        "一致（本表的自洽判据）。hmin/hmax 为当前阶段吊索力范围。",
                "headers": ["阶段", "名称", "H (kN)", "矢高 (m)", "跨中高程 (m)",
                            "吊索力 min (kN)", "吊索力 max (kN)"],
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
                # 承载力汇总表 + 逐项复核计算书（与工程校核共用一份分派结果）
                if self.final_beam:
                    self._capacity_tables(out, model, ult)
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
            "n_modes": len(self.modal.get("modes") or []),
            "modal_error": self.modal.get("error", ""),
            "n_buckling_modes": len(self.buckling.get("modes") or []),
            "buckling_status": self.buckling.get("status", ""),
            "buckling_error": self.buckling.get("error", ""),
            "cable_length_kind": self.cable_length.get("kind", ""),
            "cable_length_status": self.cable_length.get("status", ""),
            "cable_length_niter": len(self.cable_length.get("steps") or []),
            "cable_length_error": self.cable_length.get("error", ""),
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


def _capacity_sections(model: Any) -> list[tuple[str, Any, set[int]]]:
    """从模型取出可做承载力验算的配筋截面。

    返回 `[(标签, CapacitySection, 该截面下的单元号集合)]`。只含混凝土
    截面且填了 `RealConst3D.rebar` 的；钢截面走应力验算，索/桁架杆没有
    配筋概念。未配筋的混凝土截面不在此列——由调用方说明原因。
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
        if mat is None or rc is None or getattr(rc, "rebar", None) is None:
            continue
        grade = (getattr(mat, "grade", "") or "").strip()
        if grade.upper() not in code_material.CONCRETE:
            continue        # 钢截面不该带 rebar；带了也不做混凝土承载力
        rb = rc.rebar
        # 负弯矩面（底板受压 / 顶板受拉）：未登记时为 None，验算会给
        # "按正弯矩面对称近似"的提示，不静默。None 字段表示沿用正弯矩面。
        neg = None
        if bool(getattr(rb, "negative_given", False)):
            def _opt(name: str) -> float | None:
                v = getattr(rb, name, None)
                return None if v is None else float(v)
            neg = section_strength.FaceSpec(
                bf=_opt("bf_neg"), hf=_opt("hf_neg"),
                As=_opt("As_neg"), Asp=_opt("Asp_neg"),
                as_=_opt("as_neg"), asp=_opt("asp_neg"))
        sec = section_strength.CapacitySection(
            h=float(rb.h), b=float(rb.b), As=float(rb.As),
            bf=float(rb.bf), hf=float(rb.hf),
            as_=float(rb.as_), Asp=float(rb.Asp),
            asp=float(getattr(rb, "asp", 40.0) or 40.0),
            Asp_given=bool(getattr(rb, "Asp_given", False)),
            grade_rebar=rb.grade_rebar,
            Asv=float(rb.Asv), sv=float(rb.sv),
            grade_stirrup=rb.grade_stirrup,
            concrete_grade=grade,
            l0=float(getattr(rb, "l0", 0.0) or 0.0),
            hollow=bool(getattr(rb, "hollow", False)),
            negative=neg,
        )
        eids = {e.id for e in model.elements if e.etype == t.id}
        head = "主梁" if t.id == main_et else f"截面#{t.real}"
        out.append((f"{head}（{grade}）", sec, eids))
    return out


@dataclass
class _CapacityCaseRow:
    """一个截面的承载力验算记录：工程校核与计算书共用这一份分派结果。

    以前 ``_checks_capacity`` 和 ``tables()`` 各扫一遍组合内力、各自决定
    "受弯还是压弯"，口径迟早会漂。现在只在这里算一次：``chain`` 是分派
    结果、``sec`` 是**实际送进验算函数的截面**（压弯负弯矩控制时是镜像面）、
    ``result`` 是对应的结果对象，利用率与明细表都从它取。
    """

    label: str
    sec: Any
    chain: str                    # "flex" | "eccentric" | "axial"
    result: Any
    N: float = 0.0               # 轴压设计值（N，受压为正，未乘 γ0）
    M: float = 0.0               # 压弯弯矩设计值（N·m，未乘 γ0）
    Md: float = 0.0              # 正弯矩设计值（N·m）
    Md_neg: float = 0.0          # 负弯矩设计值绝对值（N·m）
    Vd: float = 0.0              # 剪力设计值（N）
    gamma0: float = 1.0
    alpha1: float = 1.0
    alpha3: float = 1.0
    l0: Any = None
    wn: Any = None               # 最不利轴力 (值, 单元, 端)
    wm_ctrl: Any = None          # 控制弯矩 (值, 单元, 端)
    wv: Any = None               # 最不利剪力 (值, 单元, 端)


def _capacity_cases(model: Any, ult: Any) -> list[_CapacityCaseRow]:
    """按受力状态分派三条承载力验算链，返回全部记录。

    受压轴力显著（>5% 截面轴压承载力）时按偏心距再分：偏心距 <1% 截面高
    走轴心受压 §5.3.1，否则走偏心受压 §5.3.4；轴力≈0 走正截面抗弯
    §5.2.2/5.2.3 + 斜截面 §5.2.9。兼容梁格轴力单值 ``N`` 与索承体系
    轴力包络 ``NMin/NMax``。
    """
    cap_secs = _capacity_sections(model)
    rows: list[_CapacityCaseRow] = []
    for label, sec, eids in cap_secs:
        worst_N: tuple[float, int, str] | None = None
        worst_M_pos: tuple[float, int, str] | None = None
        worst_M_neg: tuple[float, int, str] | None = None
        worst_V: tuple[float, int, str] | None = None
        for (eid, end), v in ult.rows.items():
            if eid not in eids:
                continue
            if "NMin" in v or "NMax" in v:
                nc = max(-v.get("NMin", 0.0), -v.get("NMax", 0.0), 0.0)
            else:
                nc = max(-v.get("N", 0.0), 0.0)
            if worst_N is None or nc > worst_N[0]:
                worst_N = (nc, eid, end)
            # 保留弯矩符号：正/负弯矩的受压面与配筋不同，不能只取 |M|
            for my in (v.get("MyMin", 0.0), v.get("MyMax", 0.0)):
                if my >= 0.0:
                    if worst_M_pos is None or my > worst_M_pos[0]:
                        worst_M_pos = (my, eid, end)
                elif worst_M_neg is None or -my > worst_M_neg[0]:
                    worst_M_neg = (-my, eid, end)
            for vz in (v.get("VzMin", 0.0), v.get("VzMax", 0.0)):
                if worst_V is None or abs(vz) > worst_V[0]:
                    worst_V = (abs(vz), eid, end)
        if (worst_N is None and worst_M_pos is None
                and worst_M_neg is None and worst_V is None):
            continue
        Nc = worst_N[0] if worst_N else 0.0
        M_pos = worst_M_pos[0] if worst_M_pos else 0.0
        M_neg = worst_M_neg[0] if worst_M_neg else 0.0
        Md = max(M_pos, M_neg)
        wm_ctrl = worst_M_pos if M_pos >= M_neg else worst_M_neg
        Vd = worst_V[0] if worst_V else 0.0
        try:
            fcd = code_material.concrete(sec.concrete_grade).fcd
            n_axial = fcd * sec.b * sec.h
        except Exception:
            n_axial = 0.0
        common = dict(label=label, wn=worst_N, wm_ctrl=wm_ctrl, wv=worst_V)
        if Nc > 0.05 * n_axial:
            e0 = Md / Nc if Nc > 0 else float("inf")
            if e0 < 0.01 * (sec.h / 1000.0):     # 偏心距 <1% 截面高 → 轴心
                r = section_strength.check_axial(sec, Nc, gamma0=1.0)
                rows.append(_CapacityCaseRow(
                    sec=sec, chain="axial", result=r, N=Nc, **common))
            else:
                r = section_strength.check_eccentric(
                    sec, Nc, M_pos, gamma0=1.0)
                sec_used, M_used = sec, M_pos
                # 非对称配筋压弯构件两个弯矩方向承载力不同，取不利面
                sec_neg, given = \
                    section_strength.mirror_section_for_negative(sec)
                if given and M_neg > 0.0:
                    r_neg = section_strength.check_eccentric(
                        sec_neg, Nc, M_neg, gamma0=1.0)
                    if r_neg.util > r.util:
                        r = r_neg
                        sec_used, M_used = sec_neg, M_neg
                        r.note = ((r.note + "；") if r.note else "") + \
                            "负弯矩面（底板受压）控制。"
                rows.append(_CapacityCaseRow(
                    sec=sec_used, chain="eccentric", result=r,
                    N=Nc, M=M_used, **common))
        else:
            r = section_strength.check_capacity(
                sec, M_pos, Vd, gamma0=1.0, alpha1=1.0, alpha3=1.1,
                Md_neg=M_neg)
            rows.append(_CapacityCaseRow(
                sec=sec, chain="flex", result=r,
                Md=M_pos, Md_neg=M_neg, Vd=Vd, alpha3=1.1, **common))
    return rows


def _impact_note(meta: dict) -> str:
    """汽车冲击系数（JTG D60）的口径描述；无基频/μ 时返回空串。"""
    f1 = meta.get("f1_vertical")
    mu = meta.get("impact_mu")
    try:
        f1v, muv = float(f1), float(mu)
    except (TypeError, ValueError):
        return ""
    if f1v <= 0:
        return ""
    return (f"，汽车冲击按 JTG D60 计入：竖向一阶弯曲基频 f1={f1v:.2f} Hz、"
            f"冲击系数 μ={muv:.3f}（活载放大 1+μ={1+muv:.3f}）")


def _combo_stress(sec: Any, row: dict[str, float]) -> section_strength.StressResult:
    """组合内力包络 `row` 下的最不利截面应力。

    正应力 σ = N/A ± My/W 是 N、My 的线性函数，极值只出现在 N∈{NMin,NMax} ×
    My∈{MyMin,MyMax} 的 4 个角点；剪应力 τ = |Vz/AW| 只依赖 |Vz|，取绝对值
    大的一侧。枚举 4 个角点、取 util 最大者。这是包络法固有近似——N、My 的
    极值来自不同加载位置，角点组合偏保守，由调用方在口径条目里写明，不藏。
    """
    nmin, nmax = row.get("NMin", 0.0), row.get("NMax", 0.0)
    mymin, mymax = row.get("MyMin", 0.0), row.get("MyMax", 0.0)
    vmin, vmax = row.get("VzMin", 0.0), row.get("VzMax", 0.0)
    vz = vmin if abs(vmin) >= abs(vmax) else vmax
    best: section_strength.StressResult | None = None
    for n in (nmin, nmax):
        for my in (mymin, mymax):
            r = section_strength.check(sec, n, my, vz)
            if best is None or r.util > best.util:
                best = r
    assert best is not None
    return best


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

        def covers(dofs, target: int) -> bool:
            # dofs 是闭区间 [a, b]，不是自由度列表。(1, 4) 含竖向 3。
            if not dofs:
                return False
            lo, hi = int(dofs[0]), int(dofs[-1])
            if lo > hi:
                lo, hi = hi, lo
            return lo <= target <= hi

        constrained = {b.node for b in model.boundaries
                       if b.node in deck_ids and (covers(b.dofs, 2) or covers(b.dofs, 3))}
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
