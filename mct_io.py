"""mct_io.py —— MIDAS Civil ``.mct`` 双向互导（导出 + 导入）。

为什么需要这个模块
------------------
``export_formats.py`` 里已经有 ``write_midas_file``，但它只做了半边，而且是
按「文件生成了」验收的。实测三个缺陷，都属于「打开不报错、结果不对」这一类：

1. **荷载一条都没写出去**。梁格的 141 条阶段荷载全丢，导出的 MIDAS 模型是
   「有刚度、有支座、零荷载」——求解得到全零位移，而文件完全合法。
   根因是代码只读 ``model.loads``，而三维内核的荷载**只挂在阶段上**
   （``solveModel3DStages`` 忽略 ``m.loads``，见 ``model_spec_3d`` 文档）。
2. **材料行错位**。MIDAS 的 ``*MATERIAL`` 头是 10 个字段
   ``iMAT, TYPE, MNAME, SPHEAT, HEATCO, PLAST, TUNIT, bMASS, DAMPRATIO, [DATA1]``，
   用户材料的 ``[DATA1]`` 再从 ``2, ELAST, POISN, THERMAL, DEN, MASS`` 接着写。
   旧代码把 ``2`` 放进了 PLAST 位、把 E 放进了 TUNIT 位——于是 E 变成「温度
   单位」、泊松比变成「是否用质量密度」。文件仍能读进去，E 取默认值。
3. **荷载工况段整个缺失**（``*STLDCASE`` / ``*USE-STLD`` / ``*CONLOAD``）。
   MIDAS 没有工况就等于没有荷载，即使 ``*CONLOAD`` 写了也不生效。

导入侧要小心的四件事（都是"合法但算错"）
------------------------------------------
1. **单位**。``*UNIT`` 可以是 ``N, MM`` / ``KN, M`` / ``KGF, CM``。坐标差 1000 倍
   的桥，几何一看就知道不对；但 ``I`` 差 1e12、``E`` 差 1e6，算出来的位移可能
   只差几倍，看着"像那么回事"。全部换算集中在本模块的 ``_UNITS`` 一处。
2. **重度和质量密度**。MIDAS 的 ``DEN`` 是**容重**（N/m³），``ModelSpec3D`` 存的是
   **质量密度**（kg/m³）。差一个 g，模态与自重全错。反算用文件里
   ``*STRUCTYPE`` 给的重力加速度，不是硬编码 9.8。
3. **截面来源**。``*SECTION`` 的 ``DBUSER``（截面库）只给「H 形、2 号、0.7 m」
   这类参数，拿不到 ``A / Iyy / Izz / Ixx``，必须查数据库。本程序不查库，
   所以这类截面**报错并跳过**，而不是猜一个——猜出来的刚度没人能发现。
4. **β 角**。MIDAS 梁单元第 7 个字段 ``ANGLE`` 是截面绕单元轴的转角。本程序的
   局部轴由全局 z 投影唯一确定，表示不了 β：β≠0 的单元，横向弯曲方向可能与
   MIDAS 不同。逐条告警，不静默。

映射表
------
    单元   BEAM→BEAM    TRUSS→TRUSS   TENSTR→CABLE
    惯矩   Iyy→IY       Izz→IZ        Ixx→J        （按「绕哪根轴」，同名）
    材料   ELAST→E      POISN→G=E/2(1+ν)            DEN/g→dens
    约束   "111100" → 自由度 1..4 的闭区间

与 ``export_formats`` 的分工：那边管「三种格式共用的字段搬运」，这边管
「MCT 特有的格式与语义」——``write_midas_file`` 现在只是一层转发。
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from model_spec_3d import (Boundary3D, Element3D, ElemTypeDef3D, Material3D,
                           ModelSpec3D, NodalLoad3D, Node3D, RealConst3D,
                           Stage3D, validate_model_3d)

__all__ = [
    "GRAVITY", "MctImport", "write_mct", "write_mct_from_canonical",
    "read_mct", "read_mct_file", "read_mct_text",
]

#: 默认重力加速度。写文件与读文件都用这一个，往返才严格可逆。
GRAVITY = 9.806

#: ``*UNIT`` 第一字段（力的单位）→ 牛。
_FORCE_TO_N = {"N": 1.0, "KN": 1e3, "KGF": 9.80665, "TONF": 9.80665e3,
               "LBF": 4.4482216153, "KIP": 4.4482216153e3}
#: ``*UNIT`` 第二字段（长度单位）→ 米。
_LEN_TO_M = {"MM": 1e-3, "CM": 1e-2, "M": 1.0, "IN": 0.0254, "FT": 0.3048}

#: MIDAS 单元类型 → ModelSpec3D 的 kind。其余（PLATE/SOLID/RIGID…）不支持。
_KIND_FROM_MIDAS = {"BEAM": "BEAM", "TRUSS": "TRUSS", "TENSTR": "CABLE"}
#: canonical 的 kind → MIDAS 单元类型。键是 ``to_canonical`` 的词表
#: （BEAM3D/TRUSS3D/CABLE3D），不是 ModelSpec3D 的 kind——两套词表混用会让
#: 索被写成梁：文件仍是合法 MCT，只是整座斜拉桥的索变成了可压可弯的梁。
_KIND_TO_MIDAS = {"BEAM3D": "BEAM", "TRUSS3D": "TRUSS", "CABLE3D": "TENSTR",
                  # 兼容直接传 ModelSpec3D 词表的情形
                  "BEAM": "BEAM", "BEAM7": "BEAM", "TRUSS": "TRUSS",
                  "CABLE": "TENSTR"}

#: ``*SECTION`` 的已知类型。遇到不认识的类型说明跳行跳错了，要停下。
_SEC_TYPES = {"VALUE", "DBUSER", "DB", "PSC", "SRC", "COMBINED", "TAPERED",
              "COMPOSITE-B", "COMPOSITE-T", "CONSTRUCT", "USER"}

#: 读到不需要搬运、也不值得告警的段。
_BENIGN = {
    "VERSION", "UNIT", "STRUCTYPE", "ENDDATA", "STLDCASE", "USE-STLD",
    "GROUP", "MATL-COLOR", "SECT-COLOR", "VIEW", "POST-SETTING",
    "NODE", "ELEMENT", "MATERIAL", "SECTION", "CONSTRAINT", "CONLOAD",
    "SELFWEIGHT",
}

#: 能识别但**搬不动**的段 → 一句话说清丢了什么。
_LOSSY = {
    "BEAMLOAD": "梁单元分布荷载未还原（本程序的荷载只有节点力）",
    "BEAMLOAD-COM": "梁单元组合荷载未还原",
    "STAGE": "施工阶段未还原：所有阶段被合并为单一荷载工况",
    "STAGEANAL": "施工阶段分析设置未还原",
    "MOVLOAD": "移动荷载（车道）未还原：本程序的车道需按 LANE_3D 重建",
    "MVLOAD": "移动荷载（车道）未还原：本程序的车道需按 LANE_3D 重建",
    "PRESTRESS": "预应力钢束未还原",
    "TDN-PROFILE": "钢束形状未还原",
    "ELASTICLINK": "弹性连接未还原（本程序只有刚性约束与节点弹性支承）",
    "RIGIDLINK": "刚性连接未还原",
    "NODALELASTIC": "节点弹性支承未还原",
    "SPRING": "弹簧单元未还原",
    "TEMP": "温度荷载未还原",
    "TEMPERATURE": "温度荷载未还原",
    "STLDCASE-STAGE": "施工阶段荷载工况未还原",
}


def _num(v: Any) -> str:
    """MCT 数值：10 位有效数字，-0.0 洗成 0。"""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "0"
    if x == 0.0:
        return "0"
    if not math.isfinite(x):
        raise ValueError(f"数值非法（nan/inf）：{v!r}")
    return f"{x:.10g}"


def _f(tok: str, default: float = 0.0) -> float:
    """宽松解析一个数值字段（MCT 里空格/空字段很常见）。"""
    t = (tok or "").strip()
    if not t:
        return default
    try:
        return float(t)
    except ValueError:
        return default


# ================================================================ 导出
def write_mct(model: ModelSpec3D, path: str | os.PathLike[str], *,
              load_case: str = "DL", include_selfweight: bool = False,
              ) -> list[str]:
    """``ModelSpec3D`` → MIDAS ``.mct``。返回「导出成功但有损」的清单。

    ``include_selfweight`` 默认**关**：本程序的恒载是以节点力形式给的（桥面板
    荷载已换算到节点），若同时打开 ``*SELFWEIGHT`` 会**双计**恒载——这是
    最容易发生、又最难在结果里看出来的一类错。

    荷载汇总（阶段荷载求和）在 ``to_canonical`` 里做，三种格式共用一份；
    这里只管 MCT 特有的几件事：工况段、自重开关、以及"带不走的那些"。
    """
    # 唯一转换点（X20_接入约定.md 铁律 2）：本模块只消费 canonical dict，不自建。
    from export_formats import to_canonical          # 局部导入：避免环引用

    canonical, warns = to_canonical(model)
    warns = list(warns)

    # 车道荷载/影响线/成桥态工况在 MCT 里没有对应物（MIDAS 的移动荷载是
    # *MOVLOAD + 车道定义，格式与结构数据完全不同）。说清楚，不然用户会以为
    # 导出的模型能直接算活载。
    if model.lanes:
        warns.append(
            f"{len(model.lanes)} 条车道荷载（LANE_3D）未导出：MIDAS 的移动荷载"
            f"要另建车道 + *MOVLOAD，与本文件的结构数据不是同一类。"
            f"导出模型只能算恒载工况。")
    if model.influences:
        warns.append(f"{len(model.influences)} 个影响线自由度未导出")
    if model.cable_bridge is not None:
        warns.append(
            "成桥态工况（*CABLE_BRIDGE）未导出：MIDAS 里的索力初张拉要用"
            "*TENSION 或初拉力荷载，且需按施工阶段激活。本文件只给几何与截面。")
    if include_selfweight:
        warns.append("已按材料密度施加自重（*SELFWEIGHT）：若节点力里已含自重，"
                     "MIDAS 侧会双计。")
    return write_mct_from_canonical(
        canonical, path, load_case=load_case,
        include_selfweight=include_selfweight, warnings=warns)


def write_mct_from_canonical(canonical: dict[str, Any],
                             path: str | os.PathLike[str], *,
                             load_case: str = "DL",
                             include_selfweight: bool = False,
                             warnings: Iterable[str] = ()) -> list[str]:
    """canonical dict → MCT 文本。``export_formats.write_midas_file`` 走这里。"""
    warns = list(warnings)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    mats = canonical["materials"]
    secs = canonical["real_constants"]
    nodes = canonical["nodes"]
    elems = canonical["elements"]
    ets = {t["id"]: t for t in canonical["element_types"]}
    cl = canonical["loads"]["cloads"]

    L: list[str] = []
    A = L.append
    A("*VERSION")
    A("8.8.5")
    A("*UNIT")
    A("N, M")
    A("*STRUCTYPE")
    A(f"0, 0, {_num(GRAVITY)}, 0, NO, NO")

    # ---- 材料：头 10 字段 + DATA1（2, ELAST, POISN, THERMAL, DEN, MASS）
    # DEN 是**容重**（N/m³），不是质量密度；ModelSpec3D 存的是 kg/m³。
    A("*MATERIAL")
    A("; iMAT, TYPE, MNAME, SPHEAT, HEATCO, PLAST, TUNIT, bMASS, DAMPRATIO,"
      " [DATA1]")
    A("; [DATA1] = 2, ELAST, POISN, THERMAL, DEN(容重 N/m^3), MASS")
    for m in mats:
        A(f"{m['id']}, USER, MAT{m['id']}, 0, 0, 0, 0, NO, 0, 2, "
          f"{_num(m['elastic_modulus'])}, {_num(m['poisson_ratio'])}, "
          f"{_num(m['thermal_expansion'])}, "
          f"{_num(m['density'] * GRAVITY)}, 0")

    # ---- 截面：VALUE 型四行块。第 2 行是全部刚度数据，跨版本最稳。
    # Asy/Asz 填 A 是**有意的**：本内核是 Euler-Bernoulli（不计剪切变形），
    # 而 MIDAS BEAM 是 Timoshenko。剪切面积给成全面积，MIDAS 侧才不会被剪切
    # 变形软化，两边才可比。
    A("*SECTION")
    A("; iSEC, TYPE, SNAME, OFFSET, bSD, bWE, SHAPE, BLT, D1..D8")
    A("; 2nd line: AREA, ASy, ASz, Ixx(扭转), Iyy(竖向弯曲), Izz(横向弯曲)")
    A("; 3rd line: CyP, CyM, CzP, CzM, QyB, QzB, PERI_OUT, PERI_IN, Cy, Cz")
    A("; 4th line: Y1..Y4, Z1..Z4, Zyy, Zzz")
    for r in secs:
        v = r["fields"]
        sid = r["id"]
        if r["kind"] == "BEAM3D":
            A(f"{sid}, VALUE, SEC{sid}, CC, 0, 0, 0, 0, 0, 0")
            A(f"{_num(v['AREA'])}, {_num(v['AREA'])}, {_num(v['AREA'])}, "
              f"{_num(v['IXX'])}, {_num(v['IYY'])}, {_num(v['IZZ'])}")
            # 上/下缘距离由截面模量反算（WT = Iyy/CzP、WY = Izz/CyP）。截面模量
            # 只用于应力输出，给 0 不影响刚度，但会让 MIDAS 侧的应力没意义。
            cyp = (v["IZZ"] / v["WY"]) if v.get("WY") else 0.0
            czp = (v["IYY"] / v["WT"]) if v.get("WT") else 0.0
            czm = (v["IYY"] / v["WB"]) if v.get("WB") else czp
            A(f"{_num(cyp)}, {_num(cyp)}, {_num(czp)}, {_num(czm)}, "
              "0, 0, 0, 0, 0, 0")
            A("0, 0, 0, 0, 0, 0, 0, 0, 0, 0")
        else:
            A(f"{sid}, VALUE, SEC{sid}, CC, 0, 0, 0, 0, 0, 0")
            A(f"{_num(v['A_TRUSS'])}, 0, 0, 0, 0, 0")
            A("0, 0, 0, 0, 0, 0, 0, 0, 0, 0")
            A("0, 0, 0, 0, 0, 0, 0, 0, 0, 0")

    A("*NODE")
    for n in nodes:
        A(f"{n['id']}, {_num(n['x'])}, {_num(n['y'])}, {_num(n['z'])}")

    # ---- 单元：iEL, TYPE, iMAT, iPRO, iN1, iN2, ANGLE, iSUB, EXVAL
    A("*ELEMENT")
    for e in elems:
        et = ets[e["element_type_id"]]
        kind = _KIND_TO_MIDAS.get(et["kind"], "BEAM")
        A(f"{e['id']}, {kind}, {et['material_id']}, {et['real_id']}, "
          f"{e['start_node']}, {e['end_node']}, 0, 0, 0")

    if canonical["boundary_conditions"]:
        A("*CONSTRAINT")
        for bc in canonical["boundary_conditions"]:
            code = "".join("1" if d in bc["dofs"] else "0"
                           for d in ("UX", "UY", "UZ", "ROTX", "ROTY", "ROTZ"))
            A(f"{bc['node_id']}, {code}, ")
            if abs(bc.get("value") or 0.0) > 0:
                warns.append(
                    f"节点 {bc['node_id']} 的支座沉降 {bc['value']:g} m 未导出："
                    f"MIDAS 的强制位移走 *SPECIFIED-DISPLACEMENT，与本文件的"
                    f"约束段不是同一类数据")

    # ---- 荷载：没有 *STLDCASE + *USE-STLD，*CONLOAD 会被 MIDAS 静默忽略
    if cl or include_selfweight:
        A("*STLDCASE")
        A("; LCNAME, LCTYPE, DESC")
        A(f"{load_case}, D, BridgeStudio 导出的荷载工况")
        A(f"*USE-STLD, {load_case}")
    if cl:
        warns.append(
            f"节点力已写入荷载工况 {load_case}（{len(cl)} 条）。"
            "MIDAS 侧需确认该工况参与分析。")
        A("*CONLOAD")
        A("; NODE, FX, FY, FZ, MX, MY, MZ, GROUP")
        for ld in cl:
            comps = ["0", "0", "0", "0", "0", "0"]
            d = int(ld["dof"])
            if 1 <= d <= 6:
                comps[d - 1] = _num(ld["value"])
            A(f"{ld['node_id']}, " + ", ".join(comps) + ", ")
    if include_selfweight:
        A("*SELFWEIGHT")
        A("; X, Y, Z, GROUP")
        A("0, 0, -1, ")
    elif cl:
        warns.append(
            "未写 *SELFWEIGHT：本程序的恒载已换算成节点力，再加重力会双计。"
            "若要在 MIDAS 里用自重，请删掉 *CONLOAD 的竖向分量。")

    A("*ENDDATA")
    p.write_text("\n".join(L) + "\n", encoding="utf-8", newline="\n")
    return warns


# ================================================================ 导入
@dataclass
class _Sec:
    """一个 ``*段``：段名 + 段头参数 + 数据行（已去注释、去空行）。"""

    name: str
    arg: str
    lines: list[str]
    line_no: int


@dataclass
class MctImport:
    """一次导入的全部结果。

    ``errors`` 非空表示**模型不可用**（缺截面特性、单元悬空…）；``warnings``
    是"能算但与原模型不同"；``losses`` 是"文件里有、本程序没有"。
    """

    model: ModelSpec3D
    unit: tuple[str, str] = ("N", "M")
    gravity: float = GRAVITY
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    losses: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    sections: dict[str, int] = field(default_factory=dict)
    source: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> dict[str, Any]:
        m = self.model
        return {
            "title": m.title,
            "nodes": len(m.nodes),
            "elements": len(m.elements),
            "materials": len(m.materials),
            "reals": len(m.reals),
            "boundaries": len(m.boundaries),
            "loads": len(m.loads),
            "load_sum_z": sum(ld.value for ld in m.loads if ld.dof == 3),
            "unit": list(self.unit),
            "kinds": m.stats().get("kinds", {}),
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "losses": len(self.losses),
        }

    def lint(self) -> tuple[list[str], list[str]]:
        """再跑一遍模型校验（引用完整性、几何退化、可解性）。"""
        if not self.ok:
            return [], []
        return validate_model_3d(self.model)


def _decode(data: bytes | str) -> str:
    if isinstance(data, str):
        return data
    for enc in ("utf-8-sig", "utf-8", "gbk", "cp949"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def _split(text: str) -> list[_Sec]:
    """按 ``*段名`` 切块。数据行（不以 ``*`` 开头）归上一段。"""
    out: list[_Sec] = []
    cur: _Sec | None = None
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.split(";", 1)[0].strip()          # 行内注释一律切掉
        if not line:
            continue
        if line.startswith("*"):
            name, _, arg = line[1:].partition(",")
            cur = _Sec(name.strip().upper(), arg.strip(), [], i)
            out.append(cur)
        elif cur is not None:
            cur.lines.append(line)
    return out


def _parse_unit(secs: list[_Sec]) -> tuple[tuple[str, str], float, float, list[str]]:
    """``*UNIT`` → (原样单位, 力→N, 长度→m)。

    ``*UNIT`` 段可以是两行（力、长度各一行），也可以是一行 ``N, M``。都认。
    """
    warns: list[str] = []
    fu, lu = "N", "M"
    for s in secs:
        if s.name != "UNIT":
            continue
        toks: list[str] = []
        if s.arg:
            toks += [t.strip().upper() for t in s.arg.split(",") if t.strip()]
        for ln in s.lines:
            toks += [t.strip().upper() for t in ln.split(",") if t.strip()]
        if toks:
            fu = toks[0]
        if len(toks) > 1:
            lu = toks[1]
        break
    if fu not in _FORCE_TO_N:
        warns.append(f"未识别的力单位 {fu}，按 N 处理"
                     f"（若原文件是 kN，结果会差 1000 倍）")
        fu = "N"
    if lu not in _LEN_TO_M:
        warns.append(f"未识别的长度单位 {lu}，按 m 处理"
                     f"（若原文件是 mm，几何会差 1000 倍）")
        lu = "M"
    return (fu, lu), _FORCE_TO_N[fu], _LEN_TO_M[lu]


def _parse_gravity(secs: list[_Sec], len_to_m: float
                   ) -> tuple[float, list[str]]:
    """``*STRUCTYPE`` 第 3 个字段是重力加速度。

    **不能硬编码 9.806**：用它去除容重，质量密度会错 1000 倍——模态与自重全错，
    静力结果照样"正常"。同一个字段在不同文件里可能是 m/s²（9.806）也可能是
    文件长度单位每秒平方（mm 下是 9806），按量级判一次。
    """
    for s in secs:
        if s.name != "STRUCTYPE":
            continue
        for ln in s.lines:
            f = [t.strip() for t in ln.split(",")]
            if len(f) >= 3:
                g = _f(f[2])
                if g > 0:
                    # 9.8~10 是 m/s²；几百以上是 mm/s² 之类，换算回 SI
                    return (g if g < 100.0 else g * len_to_m), []
        break
    return GRAVITY, [f"*STRUCTYPE 未给重力加速度，按 {GRAVITY} m/s² 处理"]


def _parse_materials(secs: list[_Sec], force_to_n: float, len_to_m: float,
                     gravity: float
                     ) -> tuple[list[Material3D], dict[int, float], list[str]]:
    """``*MATERIAL`` → (材料, 泊松比旁路表, 告警)。

    ``Material3D`` 没有 ν 字段（内核只用 G），所以 ν 单独返回，由 ``_apply_g``
    反算 G。两种写法都认：

      · 标准 USER：10 个头字段后接 ``2, ELAST, POISN, THERMAL, DEN, MASS``；
      · 旧版本项目导出的单行式（``id, USER, NAME, 0, 0, 2, E, ν, α, DEN``，
        PLAST 位被 ``2`` 占、E 落在 TUNIT 位）——认出来并告警，否则会静默
        把 E 读成 0。
    """
    mats: list[Material3D] = []
    poisson: dict[int, float] = {}
    warns: list[str] = []
    for s in secs:
        if s.name != "MATERIAL":
            continue
        for ln in s.lines:
            f = [t.strip() for t in ln.split(",")]
            if len(f) < 3:
                continue
            try:
                mid = int(f[0])
            except ValueError:
                continue
            typ = f[1].upper()
            name = f[2]

            E = nu = alpha = den_w = 0.0
            if len(f) >= 15 and f[9] == "2":                  # 标准 USER
                E, nu, alpha, den_w = (_f(f[10]), _f(f[11]),
                                       _f(f[12]), _f(f[13]))
            elif typ in ("STEEL", "CONC", "SRC", "COMP", "DB"):
                # 规范库材料：字段数随版本变，取最后一个「大数」当 ELAST
                cand = [(_f(t), i) for i, t in enumerate(f) if i >= 10]
                big = [(v, i) for v, i in cand if v > 1e3]
                if big:
                    E = big[-1][0]
                    warns.append(
                        f"材料 {mid}（{typ}，{name}）用规范库定义：E 取该行最后一个"
                        f"大于 1e3 的字段（{E:.4g}），泊松比按 0.3。本程序不查"
                        f"材料库，请核对这两个值")
                else:
                    warns.append(
                        f"材料 {mid}（{typ}，{name}）用规范库定义且未给弹性模量，"
                        f"该材料被跳过，引用它的单元不可用")
                    continue
            elif len(f) >= 10 and _f(f[6]) > 1e3:             # 旧版单行式
                E, nu, alpha, den_w = (_f(f[6]), _f(f[7]), _f(f[8]), _f(f[9]))
                warns.append(
                    f"材料 {mid} 用的是旧版单行写法（PLAST/TUNIT 位错位），"
                    f"已按旧版解析：E={E:.4g}")
            else:
                warns.append(f"材料 {mid} 的数据行无法识别，已跳过：{ln[:60]}")
                continue

            E_si = E * force_to_n / (len_to_m ** 2)
            # DEN 是容重（力/长度³）→ 质量密度 = 容重 / g
            dens = (den_w * force_to_n / (len_to_m ** 3)) / gravity if gravity else 0.0
            if E_si > 0:
                mats.append(Material3D(id=mid, E=E_si, dens=dens))
            if 0.0 < nu < 0.5:
                poisson[mid] = nu
        break
    return mats, poisson, warns


def _parse_sections(secs: list[_Sec], len_to_m: float
                    ) -> tuple[list[RealConst3D], list[str], list[str], list[int]]:
    """``*SECTION`` → RealConst3D。

    VALUE 型：第 2 行 ``AREA, ASy, ASz, Ixx, Iyy, Izz``，第 3 行给纤维距离。
    DBUSER/PSC/SRC 型：只给截面库参数，拿不到 A/I，**报错并跳过**。
    """
    reals: list[RealConst3D] = []
    errors: list[str] = []
    warns: list[str] = []
    zero_i: list[int] = []          # Iyy/Izz 为 0 的截面号（索的截面本来就如此）
    kA, kI = len_to_m ** 2, len_to_m ** 4

    for s in secs:
        if s.name != "SECTION":
            continue
        lines = s.lines
        i = 0
        while i < len(lines):
            if not _is_sec_header(lines[i]):
                # 不是标题行 → 上一截面的残余数据行（纤维距离、Y/Z 曲线）
                i += 1
                continue
            head = [t.strip() for t in lines[i].split(",")]
            i += 1
            sid = int(head[0])
            typ = head[1].upper()
            name = head[2]
            if typ != "VALUE":
                i = _skip_to_header(lines, i)
                errors.append(
                    f"截面 {sid}（{name}）的类型是 {typ}：截面库引用，"
                    f"A / Iyy / Izz / Ixx 要查库才能得到。本程序不查库，该截面"
                    f"被跳过——请在 MIDAS 里改用 VALUE 型数值截面重新导出")
                continue
            if i >= len(lines):
                errors.append(f"截面 {sid} 只有标题行，缺截面特性数据")
                continue
            vals = [_f(t) for t in lines[i].split(",")]
            i += 1
            if len(vals) < 6:
                errors.append(
                    f"截面 {sid} 的特性行只有 {len(vals)} 个数，至少要 6 个"
                    f"（AREA, ASy, ASz, Ixx, Iyy, Izz）")
                i = _skip_to_header(lines, i)
                continue
            A, _asy, _asz, ixx, iyy, izz = vals[:6]
            cyp = czp = czm = 0.0
            if i < len(lines):
                fl = [_f(t) for t in lines[i].split(",")]
                # 第 3 行是纤维距离；若上一行已被吃掉，这里可能是第 4 行
                # （Y1..Zzz，前四位通常为 0），用"前四位不全为零"区分。
                if len(fl) >= 10 and any(abs(x) > 0 for x in fl[:4]):
                    cyp, _cym, czp, czm = fl[0], fl[1], fl[2], fl[3]
                    i += 1
            i = _skip_to_header(lines, i)
            if A <= 0:
                errors.append(f"截面 {sid} 的面积 {A:g} ≤ 0，不可用")
                continue
            if iyy <= 0 or izz <= 0:
                # 索截面本来就是 A + 0，不为它报警；只对真正用于梁的截面报，
                # 否则一座斜拉桥导入后会被几十条"零惯矩"淹没（判断在调用方）。
                zero_i.append(sid)
            reals.append(RealConst3D(
                id=sid, A=A * kA, IY=iyy * kI, IZ=izz * kI, J=ixx * kI,
                G=0.0,                          # 由材料反算，见 _apply_g
                WT=(iyy * kI / (czp * len_to_m)) if czp > 0 else 0.0,
                WB=(iyy * kI / (czm * len_to_m)) if czm > 0 else 0.0,
                WY=(izz * kI / (cyp * len_to_m)) if cyp > 0 else 0.0,
            ))
        break
    return reals, errors, warns, zero_i


def _is_sec_header(line: str) -> bool:
    """这一行是不是 ``iSEC, TYPE, ...``？

    判据是「首字段是整数 **且** 第二字段是已知截面类型」。只看首字段会把
    ``0, 0, 0, 0`` 这种全零数据行当成 0 号截面——实测就是这么误报的。
    """
    f = [t.strip() for t in line.split(",")]
    return len(f) >= 3 and _is_int(f[0]) and f[1].upper() in _SEC_TYPES


def _skip_to_header(lines: list[str], i: int) -> int:
    while i < len(lines) and not _is_sec_header(lines[i]):
        i += 1
    return i


def _is_int(tok: str) -> bool:
    try:
        int(tok)
        return True
    except (TypeError, ValueError):
        return False


def _parse_nodes(secs: list[_Sec], len_to_m: float
                 ) -> tuple[list[Node3D], list[str]]:
    nodes: list[Node3D] = []
    errors: list[str] = []
    seen: set[int] = set()
    for s in secs:
        if s.name != "NODE":
            continue
        for ln in s.lines:
            f = [t.strip() for t in ln.split(",")]
            if len(f) < 4:
                continue
            try:
                nid = int(f[0])
            except ValueError:
                continue
            if nid in seen:
                errors.append(f"节点 {nid} 重复定义")
                continue
            seen.add(nid)
            nodes.append(Node3D(id=nid, x=_f(f[1]) * len_to_m,
                                y=_f(f[2]) * len_to_m, z=_f(f[3]) * len_to_m))
        break
    return nodes, errors


def _parse_elements(secs: list[_Sec]
                    ) -> tuple[list[tuple[int, str, int, int, int, int]],
                               list[str], list[str]]:
    """``*ELEMENT`` → (id, kind, mat, sec, n1, n2)。

    行格式：``iEL, TYPE, iMAT, iPRO, iN1, iN2, ANGLE, iSUB, EXVAL``。
    ANGLE（β 角）与 iSUB 本程序表示不了，各给一条告警。
    """
    rows: list[tuple[int, str, int, int, int, int]] = []
    errors: list[str] = []
    warns: list[str] = []
    bad_kind: set[str] = set()
    angled: list[int] = []
    subdiv: list[int] = []

    for s in secs:
        if s.name != "ELEMENT":
            continue
        for ln in s.lines:
            f = [t.strip() for t in ln.split(",")]
            if len(f) < 6:
                continue
            try:
                eid = int(f[0])
                mat, sec = int(f[2]), int(f[3])
                n1, n2 = int(f[4]), int(f[5])
            except ValueError:
                continue
            typ = f[1].upper()
            kind = _KIND_FROM_MIDAS.get(typ)
            if kind is None:
                bad_kind.add(typ)
                continue
            if len(f) > 6 and abs(_f(f[6])) > 1e-9:
                angled.append(eid)
            if len(f) > 7 and _f(f[7]) != 0:
                subdiv.append(eid)
            rows.append((eid, kind, mat, sec, n1, n2))
        break

    if bad_kind:
        errors.append(f"单元类型 {'、'.join(sorted(bad_kind))} 不支持（本程序只有"
                      f"梁/桁架/索），相关单元已跳过")
    if angled:
        warns.append(
            f"{len(angled)} 个单元的 β 角（截面转角）≠ 0：本程序的梁局部轴由全局"
            f" z 轴投影唯一确定，表示不了 β，这些单元的横向弯曲方向可能与 MIDAS "
            f"不同（单元 {angled[:6]}{'…' if len(angled) > 6 else ''}）")
    if subdiv:
        warns.append(f"{len(subdiv)} 个单元带细分标志（iSUB≠0），已按单段梁处理")
    return rows, errors, warns


def _parse_constraints(secs: list[_Sec]) -> tuple[list[Boundary3D], list[str]]:
    """``*CONSTRAINT`` 的六位标志 → 自由度闭区间。

    先把连续的 1 收成区段**再**建对象：``Boundary3D.dofs`` 是不可变元组，
    边建边改会让每段都退化成单自由度（第一版就是这么错的）。
    """
    out: list[Boundary3D] = []
    warns: list[str] = []
    odd: set[str] = set()
    moved: list[str] = []
    for s in secs:
        if s.name != "CONSTRAINT":
            continue
        for ln in s.lines:
            f = [t.strip() for t in ln.split(",")]
            if len(f) < 2:
                continue
            try:
                nid = int(f[0])
            except ValueError:
                continue
            code = f[1].replace(" ", "")
            if not code:
                continue
            code = code.ljust(6, "0")[:6]
            flags = [ch == "1" for ch in code]
            odd |= {ch for ch in code if ch not in "012"}
            run_start: int | None = None
            for d in range(1, 7):
                if flags[d - 1]:
                    if run_start is None:
                        run_start = d
                    if d == 6:
                        out.append(Boundary3D(node=nid, dofs=(run_start, 6)))
                        run_start = None
                elif run_start is not None:
                    out.append(Boundary3D(node=nid, dofs=(run_start, d - 1)))
                    run_start = None
            if len(f) >= 3:
                vals = [_f(t) for t in f[2:8]]
                if any(abs(v) > 0 for v in vals):
                    moved.append(f"节点 {nid} {vals}")
        break
    if odd:
        warns.append(f"约束标志里出现了非 0/1/2 的字符 {'、'.join(sorted(odd))}，"
                     f"已按「未约束」处理")
    if moved:
        warns.append(
            f"{len(moved)} 个节点的强制位移无法完整表达（``Boundary3D.value`` "
            f"只能取单个标量）：{'；'.join(moved[:4])}")
    return out, warns


def _expand_nodes(token: str) -> list[int]:
    """``*CONLOAD`` 的节点列表：支持 ``1``、``1 2 5`` 与 MIDAS 的 ``1to5``。"""
    out: list[int] = []
    for part in token.replace(",", " ").split():
        if "to" in part:
            a, _, b = part.partition("to")
            try:
                out.extend(range(int(a), int(b) + 1))
            except ValueError:
                continue
        elif _is_int(part):
            out.append(int(part))
    return out


def _parse_loads(secs: list[_Sec], force_to_n: float
                 ) -> tuple[list[NodalLoad3D], list[str], list[str]]:
    """``*CONLOAD`` → 节点力。``*SELFWEIGHT`` 只告警。

    自重**不**转成节点力：内核没有重力对象，把自重摊成节点力需要一致的
    质量分布与形函数，摊出来的力与 MIDAS 的重力积分不相等（差一组梁端
    等效节点力）。宁可说"没做"，也不给一个看着合理的错数。
    """
    loads: list[NodalLoad3D] = []
    warns: list[str] = []
    losses: list[str] = []
    has_self = False
    case = used = ""
    has_use = False

    for s in secs:
        if s.name == "STLDCASE":
            nm = (s.lines[0].split(",")[0].strip() if s.lines else
                  s.arg.split(",")[0].strip())
            case = case or nm
        elif s.name == "USE-STLD":
            has_use = True
            used = s.arg.split(",")[0].strip() or (
                s.lines[0].split(",")[0].strip() if s.lines else "")
        elif s.name == "CONLOAD":
            for ln in s.lines:
                f = [t.strip() for t in ln.split(",")]
                if len(f) < 2:
                    continue
                for nid in _expand_nodes(f[0]):
                    for d in range(1, 7):
                        v = _f(f[d]) if d < len(f) else 0.0
                        if v:
                            loads.append(NodalLoad3D(node=nid, dof=d,
                                                     value=v * force_to_n))
        elif s.name == "SELFWEIGHT":
            has_self = True

    if has_self:
        losses.append(
            "文件里有 *SELFWEIGHT（自重）：本程序没有重力对象，自重**未**转为"
            "节点力。若原模型的恒载只由自重施加，导入后模型将没有任何荷载，"
            "求解得到全零结果——这不叫「算对了」，叫「没加载」。")
    if loads and not has_use:
        warns.append(
            "文件有 *CONLOAD 但缺 *USE-STLD（未指定当前荷载工况）：在 MIDAS 里"
            "这些荷载默认不参与分析。已按「全部节点力都要」导入。")
    if case and used and case != used:
        warns.append(f"定义的荷载工况 {case} 与当前工况 {used} 不同，已一并导入")
    return loads, warns, losses


def _wire_types(nodes: list[Node3D], rows: list[tuple[int, str, int, int, int, int]],
                reals: list[RealConst3D], mats: list[Material3D]
                ) -> tuple[list[ElemTypeDef3D], list[Element3D], list[str]]:
    """(kind, mat, sec) 三元组 → etype；单元 id 原样保留。"""
    errors: list[str] = []
    mat_ids = {m.id for m in mats}
    real_ids = {r.id for r in reals}
    node_ids = {n.id for n in nodes}

    types: dict[tuple[str, int, int], int] = {}
    ets: list[ElemTypeDef3D] = []
    els: list[Element3D] = []
    seen: set[int] = set()
    for eid, kind, mat, sec, n1, n2 in sorted(rows, key=lambda t: t[0]):
        if mat not in mat_ids:
            errors.append(f"单元 {eid} 引用了不可用的材料 {mat}")
            continue
        if sec not in real_ids:
            errors.append(f"单元 {eid} 引用了不可用的截面 {sec}")
            continue
        if n1 not in node_ids or n2 not in node_ids:
            errors.append(f"单元 {eid} 引用了不存在的节点 {n1}/{n2}")
            continue
        if eid in seen:
            errors.append(f"单元 {eid} 重复定义")
            continue
        seen.add(eid)
        key = (kind, mat, sec)
        if key not in types:
            types[key] = len(ets) + 1
            ets.append(ElemTypeDef3D(id=types[key], kind=kind,  # type: ignore[arg-type]
                                     mat=mat, real=sec))
        els.append(Element3D(id=eid, etype=types[key], n1=n1, n2=n2))
    return ets, els, errors


def _apply_g(reals: list[RealConst3D], ets: list[ElemTypeDef3D],
             mats: list[Material3D], poisson: dict[int, float]
             ) -> tuple[list[RealConst3D], list[str]]:
    """G = E / (2(1+ν)) 写回实常数。

    ``RealConst3D`` 把 G 挂在**实常数**上（几何与材料混在一处，这是既有契约
    的简化）。同一截面被两种材料共用且 E/ν 不同时，只能取一个 G——会告警，
    因为另一份材料的剪切刚度就错了。
    """
    mat_by_id = {m.id: m for m in mats}
    warns: list[str] = []
    g_by_sec: dict[int, tuple[float, int]] = {}
    for t in ets:
        m = mat_by_id.get(t.mat)
        if m is None or m.E <= 0:
            continue
        g = m.E / (2.0 * (1.0 + poisson.get(m.id, 0.3)))
        if t.real in g_by_sec:
            g0, m0 = g_by_sec[t.real]
            if abs(g0 - g) > 1e-6 * max(1.0, g0):
                warns.append(
                    f"截面 {t.real} 被材料 {m0} 与 {t.mat} 共用，但两者 E/ν 不同"
                    f"（G 相差 {abs(g0 - g):.3g} Pa）。本程序把 G 放在实常数里，"
                    f"只能取一个：已取材料 {m0} 的 G，材料 {t.mat} 的剪切刚度会有偏差")
        else:
            g_by_sec[t.real] = (g, t.mat)
    out = [r.model_copy(update={"G": g_by_sec[r.id][0]}) if r.id in g_by_sec else r
           for r in reals]
    return out, warns


def read_mct_text(text: str, *, title: str = "", source: str = "") -> MctImport:
    """解析 MCT 文本。**不抛异常**：问题都进 errors / warnings / losses。"""
    secs = _split(_decode(text))
    if not secs:
        return MctImport(model=ModelSpec3D(title=title or "empty_mct"),
                         errors=["文件里没有找到任何 *段（不是 MCT 格式？）"])

    unit, force_to_n, len_to_m = _parse_unit(secs)
    gravity, w_g = _parse_gravity(secs, len_to_m)
    mats, poisson, w_mat = _parse_materials(secs, force_to_n, len_to_m, gravity)
    reals, e_sec, w_sec, zero_i = _parse_sections(secs, len_to_m)
    nodes, e_node = _parse_nodes(secs, len_to_m)
    rows, e_el, w_el = _parse_elements(secs)
    bounds, w_bc = _parse_constraints(secs)
    loads, w_ld, l_ld = _parse_loads(secs, force_to_n)

    ets, els, e_wire = _wire_types(nodes, rows, reals, mats)
    reals, w_apply = _apply_g(reals, ets, mats, poisson)

    bad_sec = sorted({t.real for t in ets
                      if t.kind in ("BEAM", "BEAM7") and t.real in zero_i})
    if bad_sec:
        w_sec = list(w_sec) + [
            f"截面 {i} 的 Iyy 或 Izz 为 0，却被梁单元引用：该方向的弯曲刚度退化，"
            f"模型可能奇异" for i in bad_sec]

    counts: dict[str, int] = {}
    losses: list[str] = []
    for s in secs:
        counts[s.name] = counts.get(s.name, 0) + 1
        if s.name not in _BENIGN and s.name not in _LOSSY:
            losses.append(f"未识别的段 *{s.name}（{len(s.lines)} 行）已跳过")
    for name, msg in _LOSSY.items():
        if counts.get(name):
            losses.append(f"*{name}：{msg}")

    errors = list(e_node) + list(e_sec) + list(e_el) + list(e_wire)
    notes: list[str] = []
    if (unit[0], unit[1]) != ("N", "M"):
        notes.append(f"文件单位是 {unit[0]}, {unit[1]}，已换算到 m / N / Pa"
                     f"（长度 ×{len_to_m:g}、力 ×{force_to_n:g}）")
    if not nodes:
        errors.append("没有读到任何节点（*NODE 段缺失或格式不符）")
    if not els:
        errors.append("没有读到任何可用的梁/桁架/索单元")

    if errors:
        model = ModelSpec3D(title=title or "imported_mct")
    else:
        try:
            model = ModelSpec3D(
                title=title or "imported_mct",
                nodes=nodes, materials=mats, reals=reals, etypes=ets,
                elements=els, boundaries=bounds, loads=loads)
        except Exception as exc:                        # noqa: BLE001
            # pydantic 的校验失败（梁缺 J、索缺 IW…）也当错误返回，不往外抛
            return MctImport(
                model=ModelSpec3D(title=title or "imported_mct"), unit=unit,
                gravity=gravity,
                errors=errors + [f"模型校验失败：{exc}"],
                warnings=w_mat + w_sec + w_el + w_bc + w_ld + w_apply + w_g,
                losses=losses + l_ld, notes=notes, sections=counts,
                source=source)

    # MCT 是"单工况"模型：一条荷载工况、全部单元同时激活。本内核的成桥态与
    # 活载段只在**有施工阶段**时输出（fem_3d.hpp：stages.empty() 直接返回），
    # 所以补一个"全激活"阶段，而不是让结果里那几段凭空消失。
    # 荷载同时留在 model.loads（忠实于文件），内核只读阶段荷载，不会双计。
    if loads and not model.stages:
        model.stages = [Stage3D(id=1, activate=[e.id for e in model.elements],
                                loads=list(loads))]
        notes.append(f"已补一个「全激活」施工阶段（{len(model.elements)} 个单元、"
                     f"{len(loads)} 条节点力）：MCT 没有施工阶段，而本内核的"
                     f"成桥态/活载段只在有阶段时输出。结果等价于一次静力求解。")

    warnings = (w_g + w_mat + w_sec + w_el + w_bc + w_ld + w_apply)
    if any(t.kind == "CABLE" for t in ets):
        warnings.append("TENSTR（只受拉索）→ CABLE。线性内核仍按 TRUSS 处理"
                        "（可压、不计垂度）；要按只受拉算请走斜拉桥内核")
    return MctImport(model=model, unit=unit, gravity=gravity, errors=errors,
                     warnings=warnings, losses=losses + l_ld, notes=notes,
                     sections=counts, source=source)


def read_mct_file(path: str | os.PathLike[str], *, title: str = "") -> MctImport:
    p = Path(path)
    return read_mct_text(_decode(p.read_bytes()), title=title or p.stem,
                         source=str(p))


def read_mct(source: str | bytes | os.PathLike[str], *, title: str = "") -> MctImport:
    """从文本 / 字节 / 路径导入。文本里出现 ``*`` 就按文本处理。"""
    if isinstance(source, bytes):
        return read_mct_text(_decode(source), title=title, source="<bytes>")
    if isinstance(source, (Path, os.PathLike)):
        return read_mct_file(source, title=title)
    s = str(source)
    if "*" in s and "\n" in s:
        return read_mct_text(s, title=title, source="<text>")
    return read_mct_file(s, title=title)


# ================================================================ CLI
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("mct_io —— MIDAS Civil .mct 双向互导")
        print("用法：")
        print("  python mct_io.py import <in.mct> [out.dat]   导入，可选写出 .dat")
        print("  python mct_io.py export grillage|cable [out.mct]")
        return 0

    cmd = argv[0]
    if cmd == "import":
        if len(argv) < 2:
            print("缺少 .mct 路径")
            return 2
        imp = read_mct_file(argv[1])
        sm = imp.summary()
        print(f"节点 {sm['nodes']}  单元 {sm['elements']}  材料 {sm['materials']}  "
              f"截面 {sm['reals']}  约束 {sm['boundaries']}  节点力 {sm['loads']}")
        print(f"单位 {sm['unit']}  竖向合力 {sm['load_sum_z'] / 1e3:,.1f} kN")
        for tag, items in (("错误", imp.errors), ("告警", imp.warnings),
                           ("未还原", imp.losses)):
            for one in items:
                print(f"  [{tag}] {one}")
        if not imp.ok:
            return 1
        if len(argv) >= 3:
            Path(argv[2]).parent.mkdir(parents=True, exist_ok=True)
            Path(argv[2]).write_text(imp.model.to_dat(), encoding="utf-8")
            print(f"已写出 {argv[2]}")
        return 0

    if cmd == "export":
        kind = argv[1] if len(argv) > 1 else "grillage"
        out = Path(argv[2] if len(argv) > 2 else f"out/{kind}.mct")
        if kind.startswith("cable"):
            from cable_stayed_3d import build_cable_stayed_3d
            model, _info = build_cable_stayed_3d()
        else:
            from grillage_3d import build_box_grillage_3d
            model = build_box_grillage_3d(staged=True, lane_code="JTG_D60")
        warns = write_mct(model, out)
        print(f"已写出 {out}（{out.stat().st_size / 1024:.0f} KB）")
        for w in warns:
            print(f"  · {w}")
        return 0

    print(f"未知子命令 {cmd!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
