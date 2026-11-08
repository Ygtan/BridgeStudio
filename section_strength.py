"""section_strength.py —— 截面应力验算：给内力配一个"分母"。

解决什么问题
------------
在这之前，程序能算出"自锚式悬索桥加劲梁轴力 39,149 kN"，但答不出"这根
梁压不压得住"。内力没有好坏，只有跟材料强度比才有意义。本模块把
`combination.py` 的组合内力（N, My, Vz）折算成截面应力，再跟
`code_material.py` 的强度设计值相比，给出利用率与结论。

分两种构件，判据不同
--------------------
**钢构件**（悬索桥 / 钢斜拉桥的加劲梁）—— JTG D64-2015 §5.3.1：

    1 款  翼缘弯曲正应力    γ₀·σx ≤ fd
    2 款  腹板剪应力        γ₀·τ  ≤ fvd
    4 款  折算应力          γ₀·√((σx/fd)² + (τ/fvd)²) ≤ 1

第 4 款就是第四强度理论（von Mises）的规范写法：因为 fvd = fd/√3，
把它两边乘 fd 就回到 √(σ² + 3τ²) ≤ fd。两种写法等价，程序按规范的
分式写，因为**利用率**（左边那个数）是设计人真正要看的量，而它只有
在这种写法下才直接等于"用掉了几成"。

**混凝土构件**（连续梁 / 混凝土斜拉桥）—— JTG 3362-2018 §7.1.5：

    σcc ≤ 0.5·fck        （受压区混凝土最大压应力）

注意这条的适用边界：7.1.5 属于**预应力混凝土构件**的应力验算。普通
钢筋混凝土构件规范不要求做使用阶段应力验算，靠的是承载力 + 裂缝宽度
控制。本程序不建钢筋，做不了那两项，所以这里把 0.5·fck 当**量级判据**
用：超了说明截面偏小或预应力不足，需要回到设计；没超也不能当成"验算
通过"。`level` 因此标 `warn` 而不是 `error`。

符号约定（为什么不做绝对值叠加）
--------------------------------
自锚式悬索桥的加劲梁同时承受 39,000 kN 轴压和弯曲。跨中下缘由弯矩产生
拉应力，被轴压**抵消**——这是自锚式最本质的受力特征，也是它比地锚式省
材料的原因。若图省事写成 |N/A| + |M/W|，等于假设两者同号叠加，会把
真实的应力状态高估一倍以上，从而掩盖真正的控制部位。

所以保留符号：

    σ(y) = N/A − My·y/IY        （y 向上为正，上缘 y=+h/2）

对上下缘分别算出 σ_top、σ_bot，两个值都写进结果表。**控制值对符号翻转
不敏感**：即使把 My 的正负理解反了，只是上下缘互换，max(|σ_top|,
|σ_bot|) 不变。这是刻意的——本程序拿不到梁单元局部坐标系的朝向保证，
不能赌符号。

不做什么
--------
不做承载力（Mu / Vu）。那需要钢筋面积、保护层、箍筋间距，模型里没有。
不做稳定（压弯构件整体稳定、腹板局部屈曲），需要计算长度与加劲肋布置。
这两条是路线图上的下一步，不是本模块的疏漏。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import code_material as cm


# ---------------------------------------------------------------- 输入


@dataclass(frozen=True)
class SectionForCheck:
    """验算所需的截面与材料。全部从 `RealConst3D` + `Material3D` 来。"""

    A: float                    # 截面积 m²
    WT: float                   # 上缘截面模量 m³
    WB: float                   # 下缘截面模量 m³
    AW: float                   # 腹板总面积 m²（剪应力用；0 = 不做剪应力）
    grade: str                  # 材料牌号 / 等级
    kind: str                   # "steel" | "concrete"
    t_plate: float = 0.0        # 控制板厚 mm（仅钢，查强度档用）

    def describe(self) -> str:
        if self.kind == "steel":
            t = f"，t={self.t_plate:g}mm" if self.t_plate else ""
            return f"{self.grade}{t}"
        return self.grade


@dataclass
class StressResult:
    """一个截面在一种内力下的应力状态与利用率。单位一律 MPa。"""

    sigma_top: float = 0.0      # 上缘正应力（+拉 −压）
    sigma_bot: float = 0.0      # 下缘正应力
    tau: float = 0.0            # 腹板剪应力
    sigma_eq: float = 0.0       # 折算应力（von Mises）；混凝土不起控制时留 0

    fd: float = 0.0             # 正应力控制强度（钢 fd / 混凝土 0.5fck）
    fvd: float = 0.0            # 剪应力控制强度（钢 fvd / 混凝土 0.167fck*）
    limit_desc: str = ""        # 限值的条文出处，写进计算书

    util_normal: float = 0.0    # 正应力利用率
    util_shear: float = 0.0     # 剪应力利用率
    util_eq: float = 0.0        # 折算应力利用率
    util: float = 0.0           # 三者的最大值
    control: str = ""           # 控制项名称（"上缘正应力" 等）

    def to_row(self) -> list[str]:
        """表行：两个正应力 + 剪应力 + 折算 + 利用率 + 控制项。"""
        return [f"{self.sigma_top:+.1f}", f"{self.sigma_bot:+.1f}",
                f"{self.tau:.1f}", f"{self.sigma_eq:.1f}",
                f"{self.util:.3f}", self.control]


# ---------------------------------------------------------------- 主函数


def check(section: SectionForCheck, N: float, My: float, Vz: float,
          gamma0: float = 1.0) -> StressResult:
    """给定组合内力（N、My 为 N·m、Vz 为 N）算应力与利用率。

    `gamma0` 是结构重要性系数（JTG D60-2015 §4.1.5）：一级 1.1、二级 1.0、
    三级 0.9。**乘在效应侧**，与 JTG D64-2015 §5.3.1 的 γ₀ 位置一致。
    """
    if section.kind == "steel":
        return _check_steel(section, N, My, Vz, gamma0)
    if section.kind == "concrete":
        return _check_concrete(section, N, My, Vz, gamma0)
    raise ValueError(
        f"未知构件类型 {section.kind!r}，只能是 'steel' 或 'concrete'")


def _edge_stresses(section: SectionForCheck, N: float, My: float,
                   gamma0: float) -> tuple[float, float]:
    """上下缘正应力（MPa，+拉 −压）。缺截面模量时只给轴应力。"""
    sig_a = gamma0 * N / section.A / 1e6                     # Pa → MPa
    if section.WT <= 0.0 and section.WB <= 0.0:
        # 没有截面模量（索、桁架杆）时退化为轴应力，不编一个 W 出来
        return sig_a, sig_a
    wt = section.WT if section.WT > 0.0 else section.WB
    wb = section.WB if section.WB > 0.0 else section.WT
    # y 向上为正：上缘 σ = σa − M/WT，下缘 σ = σa + M/WB
    return sig_a - gamma0 * My / wt / 1e6, sig_a + gamma0 * My / wb / 1e6


def _shear(section: SectionForCheck, Vz: float, gamma0: float) -> float:
    """腹板平均剪应力 τ = γ0·V/AW（MPa）。缺 AW 时返回 0 并靠调用方标注。"""
    if section.AW <= 0.0:
        return 0.0
    return abs(gamma0 * Vz / section.AW) / 1e6


def _check_steel(section: SectionForCheck, N: float, My: float, Vz: float,
                 gamma0: float) -> StressResult:
    st = cm.steel(section.grade, section.t_plate)
    r = StressResult(fd=st.fd, fvd=st.fvd,
                     limit_desc=f"fd={st.fd:g} MPa、fvd={st.fvd:g} MPa"
                                f"（JTG D64-2015 表 3.2.1，{st.label}）")

    r.sigma_top, r.sigma_bot = _edge_stresses(section, N, My, gamma0)
    r.tau = _shear(section, Vz, gamma0)

    # 上、下缘分别算利用率：弯曲正应力在两侧反号，控制侧不一定同一边
    cand: list[tuple[float, str]] = [
        (abs(r.sigma_top) / st.fd, "上缘正应力"),
        (abs(r.sigma_bot) / st.fd, "下缘正应力"),
    ]
    if st.fvd > 0:
        # 折算应力逐个边缘算：σ 与 τ 在同一块腹板上同时存在，取该边缘的
        # 正应力配同一个腹板剪应力（D64 §5.3.1 第 4 款）
        cand.append((math.hypot(r.sigma_top / st.fd, r.tau / st.fvd),
                     "上缘折算应力"))
        cand.append((math.hypot(r.sigma_bot / st.fd, r.tau / st.fvd),
                     "下缘折算应力"))


    r.util_normal = max(abs(r.sigma_top), abs(r.sigma_bot)) / st.fd
    r.util_shear = abs(r.tau) / st.fvd if st.fvd > 0 else 0.0
    r.util_eq = math.hypot(max(abs(r.sigma_top), abs(r.sigma_bot)) / st.fd,
                           r.tau / st.fvd) if st.fvd > 0 else 0.0

    r.util, r.control = max(cand, key=lambda kv: kv[0])
    # 表里给"折算应力"这一列时用最不利侧的值；控制项名已标明是哪侧
    r.sigma_eq = math.sqrt(
        max(abs(r.sigma_top), abs(r.sigma_bot)) ** 2 + 3.0 * r.tau ** 2)
    return r


def _check_concrete(section: SectionForCheck, N: float, My: float, Vz: float,
                    gamma0: float) -> StressResult:
    co = cm.concrete(section.grade)
    # 7.1.5：受压区混凝土最大压应力 ≤ 0.5·fck
    f_lim = 0.5 * co.fck
    # 混凝土抗剪不是靠"腹板剪应力 ≤ 某个强度"，而是配箍后的斜截面承载力。
    # 这里只给量级参考：ftd 是第一主拉应力限值的量级，不构成判定。
    fv_ref = co.ftd
    r = StressResult(fd=f_lim, fvd=fv_ref,
                     limit_desc=f"0.5·fck={f_lim:g} MPa（JTG 3362-2018 §7.1.5，"
                                f"{co.grade}）")

    r.sigma_top, r.sigma_bot = _edge_stresses(section, N, My, gamma0)
    r.tau = _shear(section, Vz, gamma0)

    # 压应力取两侧中更大的压（即更负的那个）
    sig_c = min(r.sigma_top, r.sigma_bot)
    sig_t = max(r.sigma_top, r.sigma_bot)
    r.util = abs(sig_c) / f_lim if f_lim > 0 else 0.0
    r.util_normal = r.util
    r.util_shear = abs(r.tau) / fv_ref if fv_ref > 0 else 0.0
    r.util_eq = 0.0                     # 混凝土不做折算应力
    r.sigma_eq = 0.0
    r.control = "受压区混凝土压应力"
    if sig_t > co.ftk:
        r.control += "（受拉边缘已超 ftk，截面开裂）"
    return r


# ---------------------------------------------------------------- 报告物料


def result_table(rows: list[tuple[str, StressResult]]) -> dict:
    """计算书用的应力验算表。`rows` = [(截面/部位说明, 结果)]。"""
    return {
        "title": "控制截面应力验算",
        "note": "正应力以受拉为正、受压为负，单位 MPa；"
                "利用率为「效应/限值」，>1.0 即不满足。"
                "钢构件按 JTG D64-2015 §5.3.1 三款分别验算正应力、"
                "剪应力与折算应力；混凝土构件按 JTG 3362-2018 §7.1.5 "
                "验算受压区最大压应力。",
        "headers": ["部位", "上缘 σ (MPa)", "下缘 σ (MPa)", "τ (MPa)",
                    "折算应力 (MPa)", "利用率", "控制项"],
        "rows": [[name] + r.to_row() for name, r in rows],
    }


# ---------------------------------------------------------------- 承载力验算
#
# 应力验算回答"截面边缘应力超不超强度"，承载力验算回答"配筋够不够扛住
# 设计内力"。前者对钢构件是硬判据、对混凝土只是量级参考；后者才是钢筋
# 混凝土受弯构件的正牌验算（JTG 3362-2018 第 5 章 持久状况承载能力极限
# 状态）。本模块只做普通钢筋混凝土（无预应力、无弯起钢筋）：
#
#   正截面抗弯  §5.2.2 矩形 / §5.2.3 工形（受压翼缘）
#   斜截面抗剪  §5.2.9 仅配箍筋
#
# 不做：压弯构件 N-M 相关（轴力较大时需用 §5.3 偏心受压公式，纯弯公式
# 会偏不安全）、受扭、抗冲切。这几条留待下一步，接口先按"受弯为主"设计。


@dataclass(frozen=True)
class CapacitySection:
    """正截面抗弯 + 斜截面抗剪承载力验算所需的截面与配筋。

    尺寸单位一律 mm，面积 mm²，强度 MPa；钢筋牌号/混凝土等级照
    `code_material`。

    - 矩形截面：``bf == b`` 且 ``hf == 0``（``bf`` 留 0 即退化为矩形）。
    - 工形 / T 形截面：``bf > b``（受压翼缘宽）且 ``hf > 0``（翼缘厚）。
      箱梁顶板受压时近似为工形（bf=箱宽、hf=顶板厚、b=各腹板宽之和）。
    - 斜截面抗剪的 ``b`` 始终取**腹板宽度**（矩形=全宽；箱梁=各腹板宽之和）。
    """

    h: float                          # 截面全高 mm
    b: float                          # 腹板宽度 mm
    As: float                         # 受拉钢筋面积 mm²
    bf: float = 0.0                   # 受压翼缘宽度 mm（0=取 b，即矩形）
    hf: float = 0.0                   # 受压翼缘厚度 mm（0=矩形）
    as_: float = 50.0                 # 受拉钢筋合力点至受拉边缘距离 mm
    Asp: float = 0.0                  # 受压钢筋面积 mm²（0=单筋）
    asp: float = 40.0                 # 受压钢筋合力点至受压边缘距离 mm
    grade_rebar: str = "HRB400"       # 纵向钢筋牌号
    Asv: float = 0.0                  # 同一截面内箍筋各肢总面积 mm²
    sv: float = 150.0                 # 箍筋间距 mm
    grade_stirrup: str = "HRB400"     # 箍筋牌号
    concrete_grade: str = "C50"
    l0: float = 0.0                  # 构件计算长度 mm（表5.3.1/§5.3.9 用；0=未提供，按短柱 φ=1/η=1）

    @property
    def h0(self) -> float:
        """有效高度 = h − as（mm）。"""
        return self.h - self.as_

    @property
    def b_eff(self) -> float:
        """受压区有效宽度：工形/T 形取翼缘宽，矩形取 b。"""
        return self.bf if self.bf > self.b else self.b


#: JTG 3362-2018 表 5.2.1 相对界限受压区高度 ξb。
#: 三档：C50 及以下 / C55~C60 / C65~C70（C75/C80 该表不适用）。
_XI_B: dict[str, tuple[float, float, float]] = {
    "HPB300": (0.58, 0.56, 0.54),
    "HRB400": (0.53, 0.51, 0.49),
    "HRB500": (0.49, 0.47, 0.46),
}
_XI_B_ALIAS = {"HRBF400": "HRB400", "RRB400": "HRB400"}


def _xi_b(rebar_grade: str, concrete_grade: str) -> float:
    """按钢筋种类 + 混凝土等级查 ξb（表 5.2.1）。"""
    key = (rebar_grade or "HRB400").strip().upper().replace(" ", "")
    key = _XI_B_ALIAS.get(key, key)
    if key not in _XI_B:
        raise KeyError(f"未登记 ξb 的钢筋牌号 {rebar_grade!r}")
    fcu = cm.concrete(concrete_grade).fcu_k
    if fcu <= 50:
        return _XI_B[key][0]
    if fcu <= 60:
        return _XI_B[key][1]
    if fcu <= 70:
        return _XI_B[key][2]
    raise ValueError(
        f"{concrete_grade} 超出 ξb 表范围（C75/C80 不适用），应做专项验算。")


#: JTG 3362-2018 §9.3.12 最小箍筋配筋率 ρsv,min。
_RHO_SV_MIN: dict[str, float] = {
    "HPB300": 0.0012,
    "HRB400": 0.0010,
    "HRB500": 0.0010,
}


def _rho_sv_min(grade_stirrup: str) -> float:
    key = (grade_stirrup or "HRB400").strip().upper().replace(" ", "")
    key = _XI_B_ALIAS.get(key, key)
    return _RHO_SV_MIN.get(key, 0.0010)


def flexural_capacity(sec: CapacitySection) -> dict:
    """正截面抗弯承载力 Mu（JTG 3362-2018 §5.2.2 矩形 / §5.2.3 工形）。

    只算纵向体内普通钢筋（无预应力、无弯起）。返回 dict：
    ``Mu``(N·m)、``x``(mm)、``xi``、``xi_b``、``over_reinforced``。
    """
    co = cm.concrete(sec.concrete_grade)
    rb = cm.rebar(sec.grade_rebar)
    fcd, fsd = co.fcd, rb.fsd            # MPa
    h0, b = sec.h0, sec.b
    xi_b = _xi_b(sec.grade_rebar, sec.concrete_grade)

    if sec.As <= 0.0:
        return {"Mu": 0.0, "x": 0.0, "xi": 0.0, "xi_b": xi_b,
                "over_reinforced": False, "fcd": fcd, "fsd": fsd}

    if sec.bf > b and sec.hf > 0.0:
        # 工形 / T 形（受压翼缘在受压区）
        if fsd * sec.As <= fcd * sec.bf * sec.hf:
            # 第一类 T 形：中和轴在翼缘内，按 bf 宽矩形
            x = fsd * sec.As / (fcd * sec.bf)
            Mu = fcd * sec.bf * x * (h0 - x / 2)
        else:
            # 第二类 T 形：中和轴在腹板内
            x = (fsd * sec.As - fcd * (sec.bf - b) * sec.hf) / (fcd * b)
            Mu = (fcd * b * x * (h0 - x / 2)
                  + fcd * (sec.bf - b) * sec.hf * (h0 - sec.hf / 2))
    else:
        # 矩形
        x = fsd * sec.As / (fcd * b)
        Mu = fcd * b * x * (h0 - x / 2)

    return {"Mu": Mu / 1e3,                 # N·mm → N·m
            "x": x, "xi": x / h0, "xi_b": xi_b,
            "over_reinforced": x > xi_b * h0,
            "fcd": fcd, "fsd": fsd}


def shear_capacity(sec: CapacitySection, alpha1: float = 1.0,
                   alpha2: float = 1.0, alpha3: float = 1.0) -> dict:
    """斜截面抗剪承载力 Vu（JTG 3362-2018 §5.2.9，仅配箍筋、无弯起筋）。

    ``alpha1`` 异号弯矩影响系数（简支/连续近边支点 1.0，连续/悬臂近中间
    支点 0.9）；``alpha2`` 预应力提高系数（普通钢筋混凝土 1.0）；
    ``alpha3`` 受压翼缘影响系数（矩形 1.0，T/I 形 1.1）。

    返回 dict：``Vu``(N)、``rho_sv``、``rho_sv_min``、``P``、``fsv``。
    """
    co = cm.concrete(sec.concrete_grade)
    fcu_k = min(co.fcu_k, 50.0)            # 公式取值上限 50 MPa
    b, h0 = sec.b, sec.h0
    P = min(100.0 * sec.As / (b * h0), 2.5)   # 纵向配筋百分率，>2.5 取 2.5
    rho_sv = sec.Asv / (sec.sv * b) if sec.Asv > 0.0 and sec.sv > 0.0 else 0.0
    fsv = cm.rebar_fsd_tension(sec.grade_stirrup)   # 箍筋抗拉设计值（≤330）

    if sec.Asv <= 0.0 or rho_sv <= 0.0:
        return {"Vu": 0.0, "rho_sv": rho_sv,
                "rho_sv_min": _rho_sv_min(sec.grade_stirrup),
                "P": P, "fsv": fsv, "fcu_k": fcu_k, "alpha1": alpha1}

    # 半经验公式：b、h0 取 mm、强度取 MPa 时结果即为 kN，×1e3 得 N。
    Vu = (0.45e-3 * alpha1 * alpha2 * alpha3 * b * h0
          * math.sqrt((2 + 0.6 * P) * math.sqrt(fcu_k) * rho_sv * fsv))
    return {"Vu": Vu * 1e3, "rho_sv": rho_sv,
            "rho_sv_min": _rho_sv_min(sec.grade_stirrup),
            "P": P, "fsv": fsv, "fcu_k": fcu_k, "alpha1": alpha1}


@dataclass
class CapacityResult:
    """一个截面在组合设计内力下的承载力利用率与结论。"""

    Mu: float = 0.0            # 抗弯承载力 N·m
    Vu: float = 0.0            # 抗剪承载力 N
    util_M: float = 0.0        # γ0·|Md| / Mu
    util_V: float = 0.0        # γ0·|Vd| / Vu
    util: float = 0.0          # 两者取大
    control: str = ""          # 控制项名
    over_reinforced: bool = False
    no_rebar: bool = False     # 未配纵向钢筋
    no_stirrup: bool = False   # 未配箍筋
    note: str = ""             # 超筋 / 少筋 / 缺箍的说明

    def to_row(self) -> list[str]:
        mu = f"{self.Mu / 1e3:.0f}" if self.Mu > 0 else "—"
        vu = f"{self.Vu / 1e3:.0f}" if self.Vu > 0 else "—"
        return [mu, vu, f"{self.util_M:.3f}", f"{self.util_V:.3f}",
                f"{self.util:.3f}", self.control]


def check_capacity(sec: CapacitySection, Md: float, Vd: float,
                   gamma0: float = 1.0, alpha1: float = 1.0,
                   alpha3: float = 1.0) -> CapacityResult:
    """组合承载力验算：给定弯矩/剪力设计值（N·m / N），返回利用率与结论。

    ``Md``、``Vd`` 应为**基本组合（承载力）**的设计内力（含 γ0 之前）。
    ``gamma0`` 结构重要性系数乘在效应侧（JTG D60 §4.1.5）。
    ``alpha3`` 受压翼缘影响系数（工形/T 形截面取 1.1）。
    """
    fm = flexural_capacity(sec)
    sh = shear_capacity(sec, alpha1=alpha1, alpha3=alpha3)
    r = CapacityResult(Mu=fm["Mu"], Vu=sh["Vu"],
                       over_reinforced=fm["over_reinforced"])

    if sec.As <= 0.0:
        r.no_rebar = True
        r.note = "未配纵向钢筋，无法验算正截面抗弯承载力。"
    if sh["rho_sv"] <= 0.0:
        r.no_stirrup = True
        r.note += "未配箍筋，无法验算斜截面抗剪承载力。" if r.note else \
            "未配箍筋，无法验算斜截面抗剪承载力。"

    r.util_M = gamma0 * abs(Md) / r.Mu if r.Mu > 0 else 0.0
    r.util_V = gamma0 * abs(Vd) / r.Vu if r.Vu > 0 else 0.0

    if r.Mu <= 0 and r.Vu <= 0:
        r.util = 0.0
        r.control = "未配筋，承载力不可判定"
        return r

    cand = []
    if r.Mu > 0:
        cand.append((r.util_M, "正截面抗弯"))
    if r.Vu > 0:
        cand.append((r.util_V, "斜截面抗剪"))
    r.util, r.control = max(cand, key=lambda kv: kv[0])

    if fm["over_reinforced"]:
        r.control += "（超筋）"
        r.note += (f"受压区高度 x={fm['x']:.0f}mm > ξb·h0="
                   f"{fm['xi_b'] * sec.h0:.0f}mm，超筋，应加受压筋或加大截面。")
    return r


def capacity_table(rows: list[tuple[str, CapacityResult]]) -> dict:
    """计算书用的承载力验算表。`rows` = [(截面/部位说明, 结果)]。"""
    return {
        "title": "正截面 / 斜截面承载力验算",
        "note": "按 JTG 3362-2018 §5.2.2 / §5.2.3 / §5.2.9，"
                "仅普通钢筋混凝土（无预应力、无弯起钢筋）。"
                "Mu 为抗弯承载力、Vu 为抗剪承载力；利用率为 γ0·|效应|/承载力，"
                ">1.0 即不满足。",
        "headers": ["部位", "Mu (kN·m)", "Vu (kN)",
                    "利用率 M", "利用率 V", "利用率", "控制项"],
        "rows": [[name] + r.to_row() for name, r in rows],
    }


# ---------------------------------------------------------------- 偏心受压（压弯）承载力
#
# 受弯承载力（Mu/Vu）回答"配筋够不够扛弯矩/剪力"，但桥塔、拱肋这类构件
# 承受的是**轴压 + 弯矩**（压弯）。轴力较大时用纯弯公式会偏不安全——受压
# 区高度 x 被轴力抬高、甚至进入小偏心，受拉钢筋应力 σs 不再等于 fsd。故
# 按 JTG 3362-2018 §5.3.4（矩形截面偏心受压）单独验算：
#
#   力平衡     N ≤ fcd·b·x + f'sd·A's − σs·As            （5.3.4-1）
#   力矩平衡   N·e ≤ fcd·b·x·(h0−x/2) + f'sd·A's·(h0−a's) （5.3.4-2）
#
# 其中 e = η·e0 + h/2 − as、e0 = M/N。大偏心（ξ≤ξb）取 σs = fsd；小偏心
# （ξ>ξb）σs 按 §5.1.5 平截面公式 σs = εcu·Es·(β·h0/x − 1)，受拉为正、
# 受压为负，且 −f'sd ≤ σs ≤ fsd。长细比较大时按 §5.3.9 用偏心距增大系数
# η 计入二阶效应。
#
# 只做矩形截面、对称配筋（A's=As，桥塔/拱肋的标准做法）；工形/T 形压弯、
# 非对称配筋留待下一步，接口按矩形对称设计。


def _beta(concrete_grade: str) -> float:
    """§5.1.4 受压区矩形应力图高度与实际受压区高度之比 β。

    C50 及以下 0.80，C80 0.74，中间每 5 MPa 降 0.01（线性）。
    """
    fcu = cm.concrete(concrete_grade).fcu_k
    return 0.80 - max(0.0, fcu - 50.0) * 0.002


def _eps_cu(concrete_grade: str) -> float:
    """§5.1.5 截面非均匀受压时混凝土极限压应变 εcu。

    C50 及以下 0.0033，C80 0.003，中间线性插入。
    """
    fcu = cm.concrete(concrete_grade).fcu_k
    return 0.0033 - max(0.0, fcu - 50.0) * 0.00001


def _sigma_s(sec: CapacitySection, x: float, beta: float, eps_cu: float,
             Es: float, fsd: float, fsd_c: float) -> float:
    """§5.1.5 受拉（或受压较小）边纵向钢筋应力，受拉为正，限 −f'sd≤σs≤fsd。"""
    if x <= 1e-6:
        return fsd
    sig = eps_cu * Es * (beta * sec.h0 / x - 1.0)
    return min(fsd, max(-fsd_c, sig))


def eta_second_order(sec: CapacitySection, N: float, M: float,
                     l0: float | None) -> float:
    """§5.3.9 偏心距增大系数 η（二阶效应）。l0 计算长度 mm；N 受压为正（N）。

    仅当长细比 l0/i > 17.5（矩形回转半径 i=h/√12）时二阶效应才不可忽略，
    否则 η=1。ζ1=min(0.2+2.7e0/h0, 0.5fcdA/N, 1.0)、ζ2=min(1.15−0.01l0/h,1)。
    """
    if l0 is None or l0 <= 0.0 or N <= 0.0 or abs(M) <= 0.0:
        return 1.0
    h, h0 = sec.h, sec.h0
    if l0 / (h / math.sqrt(12.0)) <= 17.5:
        return 1.0
    e0 = abs(M) / N * 1000.0          # M 为 N·m、N 为 N → e0 是 m，转 mm 与 h0 对齐
    zeta1 = min(0.2 + 2.7 * e0 / h0, 1.0)
    zeta1 = min(zeta1, 0.5 * cm.concrete(sec.concrete_grade).fcd
                * (sec.b * sec.h) / N)
    zeta1 = max(zeta1, 0.0)
    zeta2 = min(1.15 - 0.01 * l0 / h, 1.0)
    return 1.0 + zeta1 * zeta2 * (l0 / h) ** 2 / (1300.0 * e0 / h0)


def _solve_x_small(sec: CapacitySection, N: float, fcd: float, fsd: float,
                   fsd_c: float, Es: float, beta: float, eps_cu: float,
                   b: float, As: float, Asp: float, xi_b: float,
                   h0: float, h: float) -> float:
    """小偏心受压求受压区高度 x（二分法解力平衡 §5.3.4-1）。

    力平衡 N = fcd·b·x + f'sd·A's − σs(x)·As，σs(x) 随 x 单调降，左式随 x
    单调升，故在 [ξb·h0, h] 上二分。若 f(h)<0（N 超全截面受压承载力）返回
    h，由调用方判"轴压不足"。
    """
    def f(x: float) -> float:
        sig = _sigma_s(sec, x, beta, eps_cu, Es, fsd, fsd_c)
        return fcd * b * x + fsd_c * Asp - sig * As - N

    lo, hi = xi_b * h0, h
    if f(hi) < 0.0:
        return hi
    for _ in range(120):
        mid = 0.5 * (lo + hi)
        if f(mid) < 0.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-3:
            break
    return 0.5 * (lo + hi)


def eccentric_capacity(sec: CapacitySection, N: float) -> dict:
    """偏心受压正截面抗压承载力（§5.3.4 矩形、对称配筋）。

    `N` 轴力设计值（受压为正，N，应已含结构重要性系数 γ0）。返回该轴力
    水平下截面能承受的**一阶**极限弯矩 ``Mu``（N·m，不含 η）；效应侧乘 η
    后再比较。只支持矩形（bf≤b 或 hf=0），工形/T 形压弯暂不支持。

    返回 dict：``Mu``(N·m)、``x``(mm)、``xi``、``xi_b``、``mode``、
    ``sigma_s``(MPa)、``N``、``over_reinforced``。
    """
    co = cm.concrete(sec.concrete_grade)
    rb = cm.rebar(sec.grade_rebar)
    fcd, fsd, fsd_c = co.fcd, rb.fsd, rb.fsd_c
    Es = rb.Es * 1e5
    beta = _beta(sec.concrete_grade)
    eps_cu = _eps_cu(sec.concrete_grade)
    h, h0, b = sec.h, sec.h0, sec.b
    As = sec.As
    Asp = sec.Asp if sec.Asp > 0.0 else sec.As   # 默认对称配筋 A's=As
    a_sp = sec.asp
    xi_b = _xi_b(sec.grade_rebar, sec.concrete_grade)

    base = {"x": 0.0, "xi": 0.0, "xi_b": xi_b, "N": N,
            "over_reinforced": False}

    if sec.bf > b and sec.hf > 0.0:
        return {**base, "Mu": 0.0, "mode": "工形截面压弯暂不支持",
                "sigma_s": 0.0}
    if As <= 0.0 or N <= 0.0:
        return {**base, "Mu": 0.0,
                "mode": "未配筋" if As <= 0.0 else "非受压",
                "sigma_s": 0.0}

    # 大偏心假设：σs=fsd，力平衡求 x
    #   N = fcd·b·x + fsd_c·Asp − fsd·As  →  x = (N − fsd_c·Asp + fsd·As)/(fcd·b)
    x_big = (N - fsd_c * Asp + fsd * As) / (fcd * b)

    if x_big <= xi_b * h0:
        mode = "大偏心受压"
        x = max(x_big, 2.0 * a_sp)   # x ≥ 2a's（受压钢筋屈服的构造下限）
        sigma_s = fsd
    else:
        mode = "小偏心受压"
        x = _solve_x_small(sec, N, fcd, fsd, fsd_c, Es, beta, eps_cu,
                           b, As, Asp, xi_b, h0, h)
        sigma_s = _sigma_s(sec, x, beta, eps_cu, Es, fsd, fsd_c)

    # 力矩平衡（对 As 合力点取矩）求一阶极限弯矩：
    #   N·(η·e0 + h/2 − as) ≤ fcd·b·x·(h0−x/2) + fsd_c·Asp·(h0−a_sp)
    #   → M_lim = RHS − N·(h/2 − as)   （η 由调用方处理，此处 η=1）
    rhs = fcd * b * x * (h0 - x / 2.0) + fsd_c * Asp * (h0 - a_sp)
    m_lim = rhs - N * (h / 2.0 - sec.as_)
    if x_big < 2.0 * a_sp and x_big <= xi_b * h0:
        # 受压钢筋应力达不到 f'sd，保守忽略受压钢筋贡献
        m_lim = min(m_lim, fcd * b * x * (h0 - x / 2.0)
                    - N * (h / 2.0 - sec.as_))

    return {**base, "Mu": m_lim / 1e3, "x": x, "xi": x / h0,
            "mode": mode, "sigma_s": sigma_s,
            "over_reinforced": x > xi_b * h0}


@dataclass
class EccentricResult:
    """压弯构件（偏心受压）正截面承载力验算结果。"""

    N: float = 0.0            # 轴力设计值 γ0·N（受压为正，N）
    M: float = 0.0            # 弯矩设计值 γ0·|M|（N·m）
    Mu: float = 0.0           # 该轴力下的一阶极限弯矩（N·m，不含 η）
    eta: float = 1.0          # 偏心距增大系数
    util: float = 0.0         # η·M / Mu（轴压不足时 = inf）
    mode: str = ""            # 大偏心受压 / 小偏心受压 / 轴压不足 / 非受压 / 未配筋
    x: float = 0.0            # 受压区高度 mm
    xi: float = 0.0
    xi_b: float = 0.0
    sigma_s: float = 0.0      # 受拉钢筋应力 MPa（受压为负）
    note: str = ""

    def to_row(self) -> list[str]:
        mu = f"{self.Mu / 1e3:.0f}" if self.Mu > 0 else "—"
        util = "∞" if math.isinf(self.util) else f"{self.util:.3f}"
        return [f"{self.N / 1e3:.0f}", f"{self.M / 1e3:.0f}",
                f"{self.eta:.3f}", mu, util, self.mode]


def check_eccentric(sec: CapacitySection, N: float, M: float,
                    gamma0: float = 1.0, l0: float | None = None) -> EccentricResult:
    """压弯构件（偏心受压）验算。N 受压为正（N）、M 为弯矩（N·m），不含 γ0。

    `gamma0` 结构重要性系数乘在效应侧（JTG D60 §4.1.5）；`l0` 构件计算长度
    （mm），提供时按 §5.3.9 计入二阶效应；显式传入优先，否则回退到
    ``sec.l0``，都没有时 η=1。只覆盖矩形对称配筋。
    """
    r = EccentricResult()
    Nd = gamma0 * N
    Md = gamma0 * abs(M)
    r.N, r.M = Nd, Md

    if sec.As <= 0.0:
        r.mode = "未配筋"
        r.note = "未配纵向钢筋，无法验算偏心受压正截面承载力。"
        return r
    if Nd <= 0.0:
        r.mode = "非受压"
        r.note = "截面轴力为拉（N≤0），偏心受压公式不适用，应按受弯/偏心受拉验算。"
        return r

    l0 = l0 if l0 is not None else sec.l0
    r.eta = eta_second_order(sec, N, M, l0)
    cap = eccentric_capacity(sec, Nd)
    r.Mu = cap["Mu"]
    r.x, r.xi, r.xi_b = cap["x"], cap["xi"], cap["xi_b"]
    r.mode = cap["mode"]
    r.sigma_s = cap["sigma_s"]

    if r.Mu > 0.0:
        r.util = r.eta * Md / r.Mu
    else:
        # 轴压承载力不足（N 超全截面受压承载力）：效应必不满足
        r.util = float("inf")
        r.note = ("轴力 γ0·N 已超过截面全截面受压承载力，"
                  "即使弯矩为零也不满足。")
    return r


def eccentric_table(rows: list[tuple[str, EccentricResult]]) -> dict:
    """计算书用的偏心受压（压弯）承载力验算表。"""
    return {
        "title": "偏心受压（压弯）正截面承载力验算",
        "note": "按 JTG 3362-2018 §5.3.4 矩形截面对称配筋公式；"
                "N 受压为正，Mu 为该轴力下的一阶极限弯矩（不含 η），"
                "利用率为 η·γ0·|M|/Mu，>1.0 即不满足。",
        "headers": ["部位", "N (kN)", "M (kN·m)", "η", "Mu (kN·m)",
                    "利用率", "破坏形态"],
        "rows": [[name] + r.to_row() for name, r in rows],
    }


# ---------------------------------------------------------------- 轴心受压
#
# 桥塔、立柱等以轴压为主的构件，弯矩很小（偏心距 e0→0）时偏心受压公式退化，
# 应按 §5.3.1 轴心受压构件正截面抗压承载力验算：
#
#   γ0·Nd ≤ 0.9·φ·(fcd·A + f'sd·A's)            （5.3.1-1）
#
# φ 为稳定系数（表 5.3.1，随长细比 l0/i 减小）。l0 未提供时按短柱 φ=1，
# 不静默降级——由调用方在口径里说明「未提供计算长度，稳定未计」。


#: JTG 3362-2018 表 5.3.1 钢筋混凝土轴心受压构件稳定系数 φ。
#: 坐标用「l0/b」（b 为矩形截面短边，表注明）；线性插值，两端截断。
#: 塔柱为矩形实心截面，按 l0/b 精确查表（表的 l0/i 行是任意截面取整近似，
#: 用回转半径去插反而引入取整误差，故统一用短边）。
_STABILITY_L0_B: list[float] = [
    8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28,
    30, 32, 34, 36, 38, 40, 42, 44, 46, 48, 50,
]
_STABILITY_PHI: list[float] = [
    1.00, 0.98, 0.95, 0.92, 0.87, 0.81, 0.75, 0.70, 0.65, 0.60, 0.56,
    0.52, 0.48, 0.44, 0.40, 0.36, 0.32, 0.29, 0.26, 0.23, 0.21, 0.19,
]


def stability_factor(sec: CapacitySection,
                     l0: float | None) -> tuple[float, float]:
    """JTG 3362-2018 表 5.3.1 稳定系数 φ。

    返回 ``(φ, l0/b)``，l0/b 为长细比（b 取矩形短边 min(b, h)）。``l0`` 为
    None/≤0 时按短柱 φ=1（l0/b 记 0，表示未计）。l0/b > 表尾 50 时取表末值
    0.19，靠调用方提示超范围。
    """
    if l0 is None or l0 <= 0.0:
        return 1.0, 0.0
    b = min(sec.b, sec.h)
    if b <= 0.0:
        return 1.0, 0.0
    l0b = l0 / b
    xs, ys = _STABILITY_L0_B, _STABILITY_PHI
    if l0b <= xs[0]:
        return ys[0], l0b
    if l0b >= xs[-1]:
        return ys[-1], l0b
    for j in range(len(xs) - 1):
        if xs[j] <= l0b <= xs[j + 1]:
            t = (l0b - xs[j]) / (xs[j + 1] - xs[j])
            return ys[j] + t * (ys[j + 1] - ys[j]), l0b
    return ys[-1], l0b          # 不可达，仅作兜底


@dataclass
class AxialResult:
    """轴心受压构件正截面承载力验算结果。"""

    N: float = 0.0            # 轴力设计值 γ0·N（受压为正，N）
    Nu: float = 0.0           # 抗压承载力 0.9·φ·(fcd·A + f'sd·A's)（N）
    phi: float = 1.0          # 稳定系数
    lam: float = 0.0          # 长细比 l0/b（b 矩形短边；0=未提供 l0，按短柱）
    util: float = 0.0         # N / Nu

    def to_row(self) -> list[str]:
        nu = f"{self.Nu / 1e3:.0f}" if self.Nu > 0 else "—"
        lam = f"{self.lam:.0f}" if self.lam > 0 else "—"
        return [f"{self.N / 1e3:.0f}", nu, f"{self.phi:.3f}", lam,
                f"{self.util:.3f}"]


def axial_capacity(sec: CapacitySection, phi: float = 1.0) -> float:
    """§5.3.1 轴心受压抗压承载力 Nu = 0.9·φ·(fcd·A + f'sd·A's)（N）。

    ``phi`` 稳定系数（长细比 l0/b 相关，短柱=1）；A=b·h（矩形），A's 为
    全部纵向钢筋面积（对称配筋取 As+Asp，Asp=0 时取 2·As）。
    """
    co = cm.concrete(sec.concrete_grade)
    rb = cm.rebar(sec.grade_rebar)
    A = sec.b * sec.h
    As_total = sec.As + (sec.Asp if sec.Asp > 0.0 else sec.As)
    return 0.9 * phi * (co.fcd * A + rb.fsd_c * As_total)


def check_axial(sec: CapacitySection, N: float, gamma0: float = 1.0,
                l0: float | None = None) -> AxialResult:
    """轴心受压验算。N 受压为正（N），不含 γ0。

    ``l0`` 计算长度（mm）：显式传入优先，否则回退到 ``sec.l0``，再没有则
    按短柱 φ=1（λ 记 0，调用方在口径里说明未计）。
    """
    l0 = l0 if l0 is not None else sec.l0
    phi, lam = stability_factor(sec, l0)
    r = AxialResult(N=gamma0 * N, phi=phi, lam=lam)
    r.Nu = axial_capacity(sec, phi)
    r.util = r.N / r.Nu if r.Nu > 0 else 0.0
    return r


def axial_table(rows: list[tuple[str, AxialResult]]) -> dict:
    """计算书用的轴心受压承载力验算表。"""
    return {
        "title": "轴心受压正截面承载力验算",
        "note": "按 JTG 3362-2018 §5.3.1，Nu = 0.9·φ·(fcd·A + f'sd·A's)；"
                "N 受压为正，利用率为 γ0·N/Nu，>1.0 即不满足。"
                "稳定系数 φ 按长细比 l0/b 查表 5.3.1（线性插值），"
                "λ 为 — 表示未提供计算长度 l0、按短柱 φ=1。",
        "headers": ["部位", "N (kN)", "Nu (kN)", "φ", "λ", "利用率"],
        "rows": [[name] + r.to_row() for name, r in rows],
    }
