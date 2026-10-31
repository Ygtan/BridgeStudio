"""code_material.py —— 规范材料强度设计值：所有验算的"分母"。

为什么单独一层
--------------
内力本身没有好坏：39,000 kN 的轴力是不是问题，取决于它压在什么材料上。
把强度值抄在 `checks()` 里、抄在报告里、抄在界面上，三处必然不一致——
这类不一致在计算书里最难被发现，因为每一处单独看都"有依据"。

所以：**强度值只在这一个文件里出现**，其余地方一律通过 `concrete()` /
`steel()` / `rebar()` 查表。查到就返回，查不到就抛 `KeyError`——绝不
静默退回一个默认值。一个没登记的材料等级被当成 C50 算过去，比程序崩掉
危险得多。

数值来源（逐条可查）
--------------------
混凝土、普通钢筋、预应力钢绞线：
    JTG 3362-2018《公路钢筋混凝土及预应力混凝土桥涵设计规范》
    表 3.1.3（强度标准值）、表 3.1.4（设计值）、表 3.1.5（弹性模量）、
    表 3.1.6（预应力钢筋）

钢材（按板厚分档）：
    JTG D64-2015《公路钢结构桥梁设计规范》表 3.2.1
    fd  = 抗拉、抗压和抗弯强度设计值
    fvd = 抗剪强度设计值（= fd/√3 ≈ 0.577·fd）
    fcd = 端面承压（刨平顶紧）强度设计值

单位一律 **MPa**（= N/mm²），与规范表格一致。程序内部内力用 N、m，
换算在 `section_strength.py` 里一次完成，不在本文件里做单位魔法。
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------- 混凝土


@dataclass(frozen=True)
class Concrete:
    """一个混凝土强度等级。应力单位 MPa。"""

    grade: str
    fck: float      # 轴心抗压强度标准值（JTG 3362-2018 表 3.1.3）
    ftk: float      # 轴心抗拉强度标准值（同表）
    fcd: float      # 轴心抗压强度设计值（表 3.1.4）
    ftd: float      # 轴心抗拉强度设计值（同表）
    Ec: float       # 弹性模量，×10⁴ MPa（表 3.1.5）

    @property
    def Ec_pa(self) -> float:
        """弹性模量，Pa。"""
        return self.Ec * 1e4 * 1e6


#: JTG 3362-2018 表 3.1.3 / 3.1.4 / 3.1.5，C25~C80 逐级照抄。
#: 注意 C50~C80 的 Ec 在"掺引气剂及高砂率泵送且无实测"时应乘 0.95，
#: 规范注明是**构造取值**问题，不是强度问题，故不在此处折算。
CONCRETE: dict[str, Concrete] = {
    "C25": Concrete("C25", 16.7, 1.78, 11.5, 1.23, 2.80),
    "C30": Concrete("C30", 20.1, 2.01, 13.8, 1.39, 3.00),
    "C35": Concrete("C35", 23.4, 2.20, 16.1, 1.52, 3.15),
    "C40": Concrete("C40", 26.8, 2.40, 18.4, 1.65, 3.25),
    "C45": Concrete("C45", 29.6, 2.51, 20.5, 1.74, 3.35),
    "C50": Concrete("C50", 32.4, 2.65, 22.4, 1.83, 3.45),
    "C55": Concrete("C55", 35.5, 2.74, 24.4, 1.89, 3.55),
    "C60": Concrete("C60", 38.5, 2.85, 26.5, 1.96, 3.60),
    "C65": Concrete("C65", 41.5, 2.93, 28.5, 2.02, 3.65),
    "C70": Concrete("C70", 44.5, 3.00, 30.5, 2.07, 3.70),
    "C75": Concrete("C75", 47.5, 3.05, 32.4, 2.10, 3.75),
    "C80": Concrete("C80", 50.2, 3.10, 34.6, 2.14, 3.80),
}


def concrete(grade: str) -> Concrete:
    """按等级查混凝土。"c50" / "C50" 都认，大小写与空格不敏感。"""
    key = (grade or "").strip().upper().replace(" ", "")
    if key not in CONCRETE:
        raise KeyError(
            f"未登记的混凝土强度等级 {grade!r}；"
            f"已登记：{', '.join(sorted(CONCRETE))}。"
            "新增等级请补 JTG 3362-2018 表 3.1.3/3.1.4 的数值。")
    return CONCRETE[key]


# ---------------------------------------------------------------- 钢材


@dataclass(frozen=True)
class Steel:
    """一个钢材牌号在某个板厚档的强度设计值。应力单位 MPa。"""

    grade: str
    t_min: float    # 该档厚度下限（mm，不含；首档取 0）
    t_max: float    # 该档厚度上限（mm，含）
    fd: float       # 抗拉、抗压、抗弯
    fvd: float      # 抗剪
    fcd: float      # 端面承压（刨平顶紧）

    @property
    def label(self) -> str:
        if self.t_min <= 0:
            return f"{self.grade} t≤{self.t_max:g}mm"
        return f"{self.grade} {self.t_min:g}<t≤{self.t_max:g}mm"


#: JTG D64-2015 表 3.2.1 逐行照抄：(厚度上限 mm, fd, fvd, fcd)。
#: 表中厚度指**计算点的钢材厚度**；轴心受力构件取截面中最厚板件。
_STEEL_TABLE: dict[str, tuple[tuple[float, float, float, float], ...]] = {
    "Q235": ((16, 190, 110, 280), (40, 180, 105, 280), (100, 170, 100, 280)),
    "Q345": ((16, 275, 160, 355), (40, 270, 155, 355), (63, 260, 150, 355),
             (80, 250, 145, 355), (100, 245, 140, 355)),
    "Q390": ((16, 310, 180, 370), (40, 295, 170, 370), (63, 280, 160, 370),
             (100, 265, 150, 370)),
    "Q420": ((16, 335, 195, 390), (40, 320, 185, 390), (63, 305, 175, 390),
             (100, 290, 165, 390)),
}

#: 桥梁用结构钢（Q345q / Q370q 等，GB/T 714）在 D64 表 3.2.1 中没有单列，
#: 其设计值与同强度等级的一般结构钢相同。此处只做牌号归一，不另设数值——
#: 免得"q"系列和普通系列各留一份表，日后改一处漏一处。
_STEEL_ALIAS: dict[str, str] = {
    "Q235": "Q235", "Q235B": "Q235", "Q235C": "Q235", "Q235D": "Q235",
    "Q345": "Q345", "Q345B": "Q345", "Q345C": "Q345", "Q345D": "Q345",
    "Q345Q": "Q345", "Q345QB": "Q345", "Q345QC": "Q345", "Q345QD": "Q345",
    "Q370Q": "Q345", "Q370QD": "Q345",   # 桥梁钢 Q370q ≈ Q345 强度档
    "Q390": "Q390", "Q390D": "Q390",
    "Q420": "Q420", "Q420Q": "Q420", "Q420QD": "Q420",
}


def steel(grade: str, thickness_mm: float) -> Steel:
    """按牌号 + 板厚查钢材。`thickness_mm` 是计算点的板厚。

    查不到牌号抛 KeyError；厚度超出表的上限（>100 mm）也抛，因为规范只给
    到 100 mm，再厚的板该走专项研究，不该拿 245 MPa 硬顶。
    """
    key = (grade or "").strip().upper().replace(" ", "").replace("-", "")
    base = _STEEL_ALIAS.get(key)
    if base is None:
        raise KeyError(
            f"未登记的钢材牌号 {grade!r}；"
            f"已登记：{', '.join(sorted(_STEEL_ALIAS))}。"
            "新增牌号请补 JTG D64-2015 表 3.2.1 的数值。")

    t = max(0.0, float(thickness_mm))
    prev = 0.0
    for t_max, fd, fvd, fcd in _STEEL_TABLE[base]:
        if t <= t_max + 1e-9:
            return Steel(base, prev, t_max, fd, fvd, fcd)
        prev = t_max
    raise KeyError(
        f"{base} 的板厚 {t:g} mm 超出 JTG D64-2015 表 3.2.1 的范围"
        f"（上限 100 mm）；厚板应做专项验算，不能外推。")


# ---------------------------------------------------------------- 钢筋


@dataclass(frozen=True)
class Rebar:
    """普通钢筋（JTG 3362-2018 表 3.1.4）。"""

    grade: str
    fsk: float      # 抗拉强度标准值
    fsd: float      # 抗拉强度设计值
    fsd_c: float    # 抗压强度设计值
    Es: float       # 弹性模量，×10⁵ MPa


REBAR: dict[str, Rebar] = {
    "HPB300": Rebar("HPB300", 300, 250, 250, 2.1),
    "HRB400": Rebar("HRB400", 400, 330, 330, 2.0),
    "HRBF400": Rebar("HRBF400", 400, 330, 330, 2.0),
    "RRB400": Rebar("RRB400", 400, 330, 330, 2.0),
    "HRB500": Rebar("HRB500", 500, 415, 400, 2.0),
}

#: 表 3.1.4 注 1：轴心受拉和小偏心受拉构件的钢筋抗拉强度设计值大于
#: 330 MPa 时仍按 330 取用。这个截断是**构件受力状态相关**的，不能塞进
#: Rebar 里当常量，故单列成函数。
REBAR_TENSION_CAP = 330.0


def rebar(grade: str) -> Rebar:
    key = (grade or "").strip().upper().replace(" ", "")
    if key not in REBAR:
        raise KeyError(
            f"未登记的钢筋牌号 {grade!r}；"
            f"已登记：{', '.join(sorted(REBAR))}。")
    return REBAR[key]


def rebar_fsd_tension(grade: str) -> float:
    """轴心受拉 / 小偏心受拉构件的 fsd（表 3.1.4 注 1 的 330 截断）。"""
    return min(rebar(grade).fsd, REBAR_TENSION_CAP)


# ---------------------------------------------------------------- 预应力钢绞线


@dataclass(frozen=True)
class Strand:
    """预应力钢绞线（JTG 3362-2018 表 3.1.6，1×7 标准型）。"""

    fpk: float      # 抗拉强度标准值
    fpd: float      # 抗拉强度设计值
    fpd_c: float    # 抗压强度设计值
    Ep: float       # 弹性模量，×10⁵ MPa


STRAND: dict[int, Strand] = {
    1720: Strand(1720, 1170, 390, 1.95),
    1860: Strand(1860, 1260, 390, 1.95),
}


def strand(fpk: int) -> Strand:
    if fpk not in STRAND:
        raise KeyError(
            f"未登记的钢绞线强度级别 {fpk}；已登记：{sorted(STRAND)}。")
    return STRAND[fpk]


# ---------------------------------------------------------------- 报告物料


def concrete_table(grades: tuple[str, ...] | None = None) -> dict:
    """计算书用的混凝土强度表。默认只列程序实际用到的等级。"""
    keys = grades or ("C40", "C50", "C60")
    return {
        "title": "混凝土强度标准值与设计值（MPa）",
        "note": "引自 JTG 3362-2018 表 3.1.3、表 3.1.4、表 3.1.5；"
                "Ec 为弹性模量（×10⁴ MPa）。",
        "headers": ["等级", "fck", "ftk", "fcd", "ftd", "Ec"],
        "rows": [[c.grade, f"{c.fck:g}", f"{c.ftk:g}", f"{c.fcd:g}",
                  f"{c.ftd:g}", f"{c.Ec:g}"] for c in map(concrete, keys)],
    }


def steel_table(grades: tuple[str, ...] = ("Q345",)) -> dict:
    """计算书用的钢材强度表（按板厚分档逐行列出）。

    `grades` 可以给完整牌号（"Q345qD"），内部按表 3.2.1 的牌号归一。
    未登记的牌号抛 KeyError——计算书里宁可少一张表，不能印一张错表。
    """
    rows: list[list[str]] = []
    for g in grades:
        key = (g or "").strip().upper().replace(" ", "").replace("-", "")
        base = _STEEL_ALIAS.get(key, key)
        if base not in _STEEL_TABLE:
            raise KeyError(
                f"未登记的钢材牌号 {g!r}；已登记："
                f"{', '.join(sorted(_STEEL_ALIAS))}。")
        prev = 0.0
        for t_max, fd, fvd, fcd in _STEEL_TABLE[base]:
            rng = f"≤{t_max:g}" if prev <= 0.0 else f"{prev:g}~{t_max:g}"
            rows.append([base, rng, f"{fd:g}", f"{fvd:g}", f"{fcd:g}"])
            prev = t_max
    return {
        "title": "钢材强度设计值（MPa，JTG D64-2015 表 3.2.1）",
        "note": "厚度指计算点的钢材厚度；轴心受力构件取截面中最厚板件。"
                "fvd 为抗剪强度设计值（= fd/√3），fcd 为端面承压强度设计值。",
        "headers": ["牌号", "板厚 (mm)", "fd", "fvd", "fcd"],
        "rows": rows,
    }
