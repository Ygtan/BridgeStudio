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
