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
