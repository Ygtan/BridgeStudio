"""桥梁结构规范模型（BridgeSpec）——把前处理从「单根梁」扩展到 CABLE / TRUSS / 拱桥。

为什么需要这一层
----------------
FEM-Eigen 求解内核已经支持 BEAM / TRUSS / CABLE / STRUT 四类单元，以及预应力、
收缩徐变、施工阶段、影响线、非线性索等能力。但 NL-FE 的 ModelSpec 只能表达
「一根 2D 梁 + 支座 + UDL/集中力」，这些能力完全没有入口。

本模块定义与 `.dat`(new format) **一一对应**的规范模型（canonical model），
并提供三个参数化建造器（梁桥 / 拱桥 / 桁架桥），把内核已有的能力暴露到前处理层。

设计原则
--------
1. BridgeSpec 是纯数据（节点 + 单元 + 类型 + 材料 + 边界 + 荷载），
   与「怎么画」「怎么算」解耦，可同时喂给导出器、可视化、未来的 3D 预览。
2. 建造器负责把工程参数（跨度、矢高、节间数…）翻译成节点-单元拓扑，
   翻译过程中保证：支座、吊杆、集中力位置**精确落在节点上**（不就近取整）。
3. `to_dat()` 之前必须过 `validate_model()`——引用缺失、重复 id、
   缺实常数这类问题在本地报错，而不是让 C++ 求解器给出奇异矩阵。
"""

from __future__ import annotations

import math
import re
from enum import Enum

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- 基础定义


class ElemKind(str, Enum):
    """FEM-Eigen 支持的单元类型，见 input_format.md 3.1。"""

    BEAM = "BEAM"    # 梁，3 自由度/节点
    TRUSS = "TRUSS"  # 桁架，2 自由度/节点，可拉可压
    CABLE = "CABLE"  # 只受拉索，2 自由度/节点，受压退出工作
    STRUT = "STRUT"  # 只受压杆，2 自由度/节点，受拉退出工作


class MaterialDef(BaseModel):
    id: int = 1
    name: str = "钢材 Q345"
    E: float = 2.06e11          # Pa
    alpx: float = 1.2e-5        # 1/℃
    dens: float = 7850.0        # kg/m³（给 DENS 就会自动算自重）


class ElemTypeDef(BaseModel):
    """实常数（`*R`）。BEAM 必须有 I；TRUSS/CABLE/STRUT 必须有 A。

    `mat` 是本类型引用的材料 id（`*MAT,<mat>`），默认 1。斜拉/悬索桥等
    混合材料模型（混凝土主梁/塔 + 钢索）需要为不同类型指定不同材料。
    """

    id: int
    kind: ElemKind = ElemKind.BEAM
    A: float                    # 截面积 m²
    I: float | None = None      # 惯性矩 m⁴（BEAM 必填）
    WT: float | None = None     # 上翼缘抗弯模量 m³（应力图用，缺则跳过应力图）
    WB: float | None = None     # 下翼缘抗弯模量 m³
    H: float | None = None      # 截面高度 m（温度梯度用）
    addmass: float | None = None  # 附加线质量 kg/m
    istrn: float | None = None  # 初应变，正值预拉（索/杆用）
    mat: int = 1                # 材料 id（*MP 表里的编号）
    h_th_mm: float = 0.0        # 理论厚度 mm。0 表示没有。统一厚度优先。


class NodeDef(BaseModel):
    id: int
    x: float
    y: float = 0.0


class ElemDef(BaseModel):
    id: int
    type_id: int
    n1: int
    n2: int


class BoundaryDef(BaseModel):
    """`*BOUNDARY`。dofs 写法："1-2" 铰、"2" 竖向辊、"1-3" 固结。"""

    node: int
    dofs: str = "1-2"
    value: float = 0.0


class NodalLoadDef(BaseModel):
    """`*CLOAD`。value 单位 N，向下为负。"""

    node: int
    dof: int = 2
    value: float = 0.0


class ElemLoadDef(BaseModel):
    """`*ELEMENT_LOAD` 均布荷载。q 单位 N/m，向下为负。

    单元编号区间 [e_from, e_to]（含端点）；e_to 为空表示单个单元。
    """

    e_from: int
    e_to: int | None = None
    q: float = 0.0


class StageLoadDef(BaseModel):
    """施工阶段节点荷载（`*STAGE_LOAD`）。"""

    node: int
    dof: int = 2
    value: float = 0.0


class StageElemLoadDef(BaseModel):
    """施工阶段单元均布荷载（`*STAGE_ELEMENT_LOAD`）。"""

    elems: list[int] = Field(default_factory=list)
    dir: str = "GY"
    q1: float = 0.0
    q2: float = 0.0


class Stage2D(BaseModel):
    """施工阶段（`*STAGE`）。2D 内核用 ACTIVE=/DEACTIVE= 激活/钝化单元。

    `time` 为阶段龄期（天），0 表示不参与时变（徐变/收缩）插值。
    """

    id: int
    activate: list[int] = Field(default_factory=list)
    deactivate: list[int] = Field(default_factory=list)
    loads: list[StageLoadDef] = Field(default_factory=list)
    elem_loads: list[StageElemLoadDef] = Field(default_factory=list)
    time: float = 0.0


class Lane2D(BaseModel):
    """车道荷载（`*LANE_LOAD`）。按 JTG D60 影响线最不利布载取包络。

    `code="JTG_D60"` 时不写 Q/P，交给内核按规范自动填充（qk=10.5 kN/m、
    Pk 按 L 内插 270~360 kN）；`code=""` 时用显式 q/p（自定义荷载）。
    2D 内核的 `applyJtgD60LaneLoads2D` 会覆盖 Q/P，故两者互斥、不重复写。
    """

    nodes: list[int] = Field(default_factory=list)   # 车道纵列节点（按行车方向）
    q: float = -10.5e3           # 均布车道荷载 N/m（向下为负，code 为空时用）
    p: float = -360.0e3          # 集中荷载 N（向下为负，code 为空时用）
    dir: str = "GY"
    method: str = "LINEAR"
    code: str = "JTG_D60"        # "JTG_D60" = 内核自动车道荷载；"" = 用 q/p


def _int_ranges(vals: list[int]) -> str:
    """整数列表压缩成 `a-b,c-d`：连续段用 a-b，孤立值原样（内核 parseIntList 兼容）。"""
    vals = sorted(set(vals))
    if not vals:
        return ""
    parts: list[str] = []
    a = b = vals[0]
    for v in vals[1:]:
        if v == b + 1:
            b = v
        else:
            parts.append(str(a) if a == b else f"{a}-{b}")
            a = b = v
    parts.append(str(a) if a == b else f"{a}-{b}")
    return ",".join(parts)


def _dof_set(dofs: str) -> set[int]:
    """解析自由度写法："1-2"→{1,2}，"1-3"→{1,2,3}，"2"→{2}，"1,3"→{1,3}。

    用连字符表示**连续区间**，因此 "1-3" 与 "1-2-3" 等价（都取 1..3）。
    """
    out: set[int] = set()
    for part in re.split(r"[,\s]+", dofs.strip()):
        if not part:
            continue
        if "-" in part:
            nums = part.split("-")
            try:
                a, b = int(nums[0]), int(nums[-1])
                out.update(range(a, b + 1))
            except ValueError:
                pass
        else:
            try:
                out.add(int(part))
            except ValueError:
                pass
    return out


class BridgeSpec(BaseModel):
    title: str = "bridge"
    nodes: list[NodeDef] = Field(default_factory=list)
    elements: list[ElemDef] = Field(default_factory=list)
    elem_types: list[ElemTypeDef] = Field(default_factory=list)
    materials: list[MaterialDef] = Field(default_factory=list)
    boundaries: list[BoundaryDef] = Field(default_factory=list)
    nodal_loads: list[NodalLoadDef] = Field(default_factory=list)
    elem_loads: list[ElemLoadDef] = Field(default_factory=list)
    stages: list[Stage2D] = Field(default_factory=list)
    lanes: list[Lane2D] = Field(default_factory=list)
    # 三者都大于 0 且阶段写了 TIME 时，二维施工阶段按 JTG 3362 附录 C.2.1 徐变。
    creep_rh: float = 0.0
    creep_h: float = 0.0
    creep_fcm: float = 0.0
    creep_t0: float = 7.0
    shrinkage_ts: float = 0.0    # 干燥开始龄期；0 表示与加载龄期相同
    gravity: float = -9.81       # 0.0 表示不计自重
    analysis: str = "LINEAR"     # LINEAR | NONLINEAR（轴向单元几何非线性 + 索开关）
    note: str = ""

    # ------------------------------------------------------------ 校验

    def validate_model(self) -> list[str]:
        """返回问题列表；空列表表示可以安全导出。

        刻意放在 Python 侧：让「引用了不存在的节点」「BEAM 忘了给 I」这类错误
        在这里以中文报出来，而不是变成 C++ 端的一句奇异矩阵。
        """
        errs: list[str] = []
        node_ids = {n.id for n in self.nodes}
        type_ids = {t.id for t in self.elem_types}
        mat_ids = {m.id for m in self.materials}

        if len(node_ids) != len(self.nodes):
            errs.append("存在重复节点 id")
        if len({e.id for e in self.elements}) != len(self.elements):
            errs.append("存在重复单元 id")
        if len(type_ids) != len(self.elem_types):
            errs.append("存在重复单元类型 id")
        if not self.nodes:
            errs.append("没有节点")
        if not self.elements:
            errs.append("没有单元")
        if not self.boundaries:
            errs.append("没有边界约束（结构会是机构，刚度矩阵奇异）")

        for t in self.elem_types:
            if t.kind == ElemKind.BEAM and not t.I:
                errs.append(f"单元类型 {t.id}（BEAM）缺少惯性矩 I —— 梁单元必填")
            if t.kind in (ElemKind.TRUSS, ElemKind.CABLE, ElemKind.STRUT) and not t.A:
                errs.append(f"单元类型 {t.id}（{t.kind.value}）缺少截面积 A")

        for e in self.elements:
            if e.type_id not in type_ids:
                errs.append(f"单元 {e.id} 引用了不存在的类型 {e.type_id}")
            if e.n1 not in node_ids or e.n2 not in node_ids:
                errs.append(f"单元 {e.id} 引用了不存在的节点 ({e.n1},{e.n2})")
            if e.n1 == e.n2:
                errs.append(f"单元 {e.id} 两端节点相同")

        for b in self.boundaries:
            if b.node not in node_ids:
                errs.append(f"边界约束引用了不存在的节点 {b.node}")

        # 转角自由度：TRUSS/CABLE/STRUT 只连 u、v，若某节点没有任何 BEAM 相连
        # 且 dof 3 未被约束，求解器会报"零刚度自由度"。在这里提前拦下来。
        beam_nodes: set[int] = set()
        for e in self.elements:
            t = next((t for t in self.elem_types if t.id == e.type_id), None)
            if t and t.kind == ElemKind.BEAM:
                beam_nodes.update((e.n1, e.n2))
        rot_fixed = {b.node for b in self.boundaries if 3 in _dof_set(b.dofs)}
        loose = [n.id for n in self.nodes
                 if n.id not in beam_nodes and n.id not in rot_fixed]
        if loose:
            preview = "、".join(str(i) for i in loose[:8])
            errs.append(
                f"有 {len(loose)} 个节点只连着桁架/索单元，转角自由度(dof 3)无刚度"
                f"（节点 {preview}{'…' if len(loose) > 8 else ''}）——"
                "纯桁架/索模型需给所有节点加 dofs='3' 约束"
            )
        for ld in self.nodal_loads:
            if ld.node not in node_ids:
                errs.append(f"节点荷载引用了不存在的节点 {ld.node}")
        for ld in self.elem_loads:
            hi = ld.e_to if ld.e_to is not None else ld.e_from
            if not any(ld.e_from <= e.id <= hi for e in self.elements):
                errs.append(f"单元荷载区间 {ld.e_from}-{hi} 内没有任何单元")
        if not mat_ids:
            errs.append("没有材料定义")
        return errs

    # ------------------------------------------------------------ 摘要

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for e in self.elements:
            t = next((t for t in self.elem_types if t.id == e.type_id), None)
            k = t.kind.value if t else f"?{e.type_id}"
            counts[k] = counts.get(k, 0) + 1
        kinds = "、".join(f"{k}×{v}" for k, v in counts.items()) or "无"
        xmin = min((n.x for n in self.nodes), default=0.0)
        xmax = max((n.x for n in self.nodes), default=0.0)
        ymax = max((n.y for n in self.nodes), default=0.0)
        return (
            f"{self.title} ｜ 节点 {len(self.nodes)}，单元 {len(self.elements)}（{kinds}）\n"
            f"跨度 {xmax - xmin:.2f} m，最大高度 {ymax:.2f} m ｜ "
            f"约束 {len(self.boundaries)}，节点荷载 {len(self.nodal_loads)}，"
            f"单元荷载 {len(self.elem_loads)}，自重 {'计' if self.gravity else '不计'}"
        )

    # ------------------------------------------------------------ 导出

    def to_dat(self) -> str:
        """生成 FEM-Eigen `.dat`（new format）文本。

        与 fem_eigen_export.spec_to_dat 共用同一套关键字约定，注意两点：
        - `*EN,id,n1,n2` 里 id 是单元自身的编号；
        - `*GRAVITY` 必须显式写出，因为求解器 gy 默认就是 -9.81。
        """
        out: list[str] = []
        out.append("# 由 fe_nl_preprocessor 自动生成（桥梁规范模型 BridgeSpec）")
        out.append("# 目标格式：FEM-Eigen new format，见 FEM-Eigen/input_format.md")
        if self.note:
            out.append(f"# 模型描述：{self.note}")
        out.append("*TITLE")
        out.append(self.title)
        out.append("")

        if self.analysis != "LINEAR":
            out.append("! 分析类型（NONLINEAR = 轴向单元几何非线性 + 索/杆开关）")
            out.append(f"*ANALYSIS TYPE={self.analysis}")
            out.append("")

        out.append("! 单元类型")
        out.append("*ET")
        for t in self.elem_types:
            out.append(f"{t.id} {t.kind.value}")
        out.append("")

        out.append("! 材料（E:Pa  DENS:kg/m3  给 DENS 即自动计自重）")
        out.append("*MP")
        for m in self.materials:
            out.append(f"{m.id} E={m.E:.6g} ALPX={m.alpx:.6g} DENS={m.dens:.6g}")
        out.append("")

        out.append("! 实常数")
        out.append("*R")
        for t in self.elem_types:
            f = [f"A={t.A:.6g}"]
            if t.I is not None:
                f.append(f"I={t.I:.6g}")
            if t.WT is not None:
                f.append(f"WT={t.WT:.6g}")
            if t.WB is not None:
                f.append(f"WB={t.WB:.6g}")
            if t.H is not None:
                f.append(f"H={t.H:.6g}")
            if t.addmass is not None:
                f.append(f"ADDMASS={t.addmass:.6g}")
            if t.istrn is not None:
                f.append(f"ISTRN={t.istrn:.6g}")
            if t.h_th_mm > 0.0:
                f.append(f"HTH={t.h_th_mm:.6g}")
            out.append(f"{t.id} " + " ".join(f))
        out.append("")

        out.append("! 节点：id x y")
        out.append("*NODE")
        for n in self.nodes:
            out.append(f"{n.id} {n.x:.6f} {n.y:.6f}")
        out.append("")

        out.append("! 单元：按类型分组，组内 *EN,id,n1,n2")
        for t in self.elem_types:
            group = [e for e in self.elements if e.type_id == t.id]
            if not group:
                continue
            out.append(f"*TYPE,{t.id}")
            out.append(f"*MAT,{t.mat}")
            out.append(f"*REAL,{t.id}")
            for e in group:
                out.append(f"*EN,{e.id},{e.n1},{e.n2}")
        out.append("")

        out.append("! 边界约束（1=u, 2=v, 3=θ）")
        out.append("*BOUNDARY")
        for b in self.boundaries:
            out.append(f"{b.node} {b.dofs} {b.value:.6g}")
        out.append("")

        if self.nodal_loads:
            out.append("! 节点集中荷载（N，向下为负）")
            out.append("*CLOAD")
            for ld in self.nodal_loads:
                out.append(f"{ld.node} {ld.dof} {ld.value:.6g}")
            out.append("")

        if self.elem_loads:
            out.append("! 单元均布荷载（N/m，向下为负）")
            out.append("*ELEMENT_LOAD")
            for ld in self.elem_loads:
                hi = ld.e_to if ld.e_to is not None else ld.e_from
                rng = str(ld.e_from) if hi == ld.e_from else f"{ld.e_from}-{hi}"
                out.append(f"{rng} GY {ld.q:.6g} {ld.q:.6g}")
            out.append("")

        member_h = any(t.h_th_mm > 0.0 for t in self.elem_types)
        if self.creep_rh > 0.0 and self.creep_fcm > 0.0 and (self.creep_h > 0.0 or member_h):
            out.append("! 徐变：JTG 3362 附录 C.2.1。没写湿度时二维阶段不徐变。")
            h_bit = f" H={self.creep_h:.6g}" if self.creep_h > 0.0 else ""
            out.append(
                "*CREEP_MODEL MODEL=JTG3362 "
                f"RH={self.creep_rh:.6g}{h_bit} "
                f"FCM={self.creep_fcm:.6g} T0={self.creep_t0:.6g}"
                + (f" TS={self.shrinkage_ts:.6g}" if self.shrinkage_ts > 0.0 else ""))
            out.append("")

        out.append("! 自重开关（必须显式给出：求解器 gy 默认 -9.81）")
        out.append("*GRAVITY")
        out.append(f"0 {self.gravity:.6g}")
        out.append("")

        if self.stages:
            out.append("! 施工阶段：ACTIVE=/DEACTIVE= 激活/钝化单元")
            out.append("*STAGE")
            for s in self.stages:
                parts = [str(s.id)]
                if s.activate:
                    parts.append("ACTIVE=" + _int_ranges(s.activate))
                if s.deactivate:
                    parts.append("DEACTIVE=" + _int_ranges(s.deactivate))
                if s.time:
                    parts.append(f"TIME={s.time:.6g}")
                out.append(" ".join(parts))
            out.append("")
            if any(s.loads for s in self.stages):
                out.append("*STAGE_LOAD")
                for s in self.stages:
                    for ld in s.loads:
                        out.append(f"{s.id} NODE={ld.node} DOF={ld.dof} "
                                   f"VALUE={ld.value:.6g}")
                out.append("")
            if any(s.elem_loads for s in self.stages):
                out.append("*STAGE_ELEMENT_LOAD")
                for s in self.stages:
                    for ld in s.elem_loads:
                        out.append(f"{s.id} ELEM={_int_ranges(ld.elems)} "
                                   f"DIR={ld.dir} Q1={ld.q1:.6g} Q2={ld.q2:.6g}")
                out.append("")

        if self.lanes:
            out.append("! 车道荷载（JTG D60 影响线最不利布载包络）")
            out.append("*LANE_LOAD")
            for i, ln in enumerate(self.lanes, 1):
                parts = [f"{i}", f"NODES={_int_ranges(ln.nodes)}"]
                if ln.code == "JTG_D60":
                    # 交给内核按规范自动填充 qk/Pk（含 Pk 随跨径内插），
                    # 不再手写 Q/P 以免两处打架。
                    parts.append("CODE=JTG_D60")
                else:
                    if ln.q:
                        parts.append(f"Q={ln.q:.6g}")
                    if ln.p:
                        parts.append(f"P={ln.p:.6g}")
                parts.append(f"DIR={ln.dir}")
                if ln.method != "LINEAR":
                    parts.append(f"METHOD={ln.method}")
                out.append(" ".join(parts))
            out.append("")

        out.append("*END")
        return "\n".join(out) + "\n"


# ---------------------------------------------------------------- 材料库

MATERIAL_LIB: dict[str, MaterialDef] = {
    "钢材 Q345": MaterialDef(id=1, name="钢材 Q345", E=2.06e11, alpx=1.2e-5, dens=7850.0),
    "混凝土 C50": MaterialDef(id=1, name="混凝土 C50", E=3.45e10, alpx=1.0e-5, dens=2500.0),
}


def _mat(name: str) -> MaterialDef:
    return MATERIAL_LIB.get(name, MATERIAL_LIB["钢材 Q345"]).model_copy(deep=True)


def _section_props(shape: str, h: float, b: float,
                   tw: float | None = None, tf: float | None = None) -> dict:
    """复用梁单元那套参数化截面库，保证两条路径的截面属性算法一致。"""
    from model_spec import Section
    from section_lib import section_properties

    return section_properties(Section(shape=shape, h=h, b=b, tw=tw, tf=tf))


def _elem_type_from_section(tid: int, kind: ElemKind, shape: str, h: float, b: float,
                            tw: float | None = None, tf: float | None = None,
                            addmass: float | None = None) -> ElemTypeDef:
    p = _section_props(shape, h, b, tw, tf)
    if kind == ElemKind.BEAM:
        return ElemTypeDef(id=tid, kind=kind, A=p["A"], I=p["I"],
                           WT=p.get("W"), WB=p.get("W"), H=h, addmass=addmass)
    return ElemTypeDef(id=tid, kind=kind, A=p["A"], addmass=addmass)


def _nearest_node(nodes: list[NodeDef], x: float, y: float = 0.0) -> int:
    return min(nodes, key=lambda n: (n.x - x) ** 2 + (n.y - y) ** 2).id


# ---------------------------------------------------------------- 建造器 1：梁桥


def build_beam_bridge(
    span: float = 30.0,
    n_spans: int = 1,
    n_elem: int | None = None,
    shape: str = "box",
    h: float = 2.0,
    b: float = 8.0,
    tw: float | None = 0.02,
    tf: float | None = 0.025,
    material: str = "钢材 Q345",
    udl_kN: float = 0.0,
    point_kN: float = 0.0,
    gravity: float = -9.81,
    staged: bool = False,
    q2_kN: float = 0.0,
    lane: bool = False,
    creep_rh: float = 0.0,
    creep_h: float = 0.0,
    creep_fcm: float = 0.0,
    creep_t0: float = 7.0,
    creep_t_end: float = 0.0,
    shrinkage_ts: float = 0.0,
) -> BridgeSpec:
    """简支 / 连续梁桥。用于与拱桥、桁架桥在同一内核上做对比。

    `staged=True`：逐跨施工（每跨一个阶段，激活该跨单元 + 施加该跨一期
    恒载 udl_kN，自重按激活单元自动计入）；`q2_kN` 为二期铺装，合拢后
    最后一个阶段全桥施加。`lane=True`：生成 JTG D60 车道荷载影响线包络。
    """
    n_elem = n_elem or max(20, int(round(span / 1.0)))
    n_elem = max(4, n_elem)
    # 连续梁要保证中间支座落在节点上：单元数取跨数的整数倍
    if n_spans > 1 and n_elem % n_spans:
        n_elem += n_spans - n_elem % n_spans

    nodes = [NodeDef(id=i + 1, x=span * i / n_elem, y=0.0) for i in range(n_elem + 1)]
    elements = [ElemDef(id=i + 1, type_id=1, n1=i + 1, n2=i + 2) for i in range(n_elem)]

    bnd = [BoundaryDef(node=1, dofs="1-2")]
    for k in range(1, n_spans):
        bnd.append(BoundaryDef(node=n_elem * k // n_spans + 1, dofs="2"))
    bnd.append(BoundaryDef(node=n_elem + 1, dofs="2"))
    # 去重（n_spans=1 时末端支座会与上面重复）
    seen: set[int] = set()
    boundaries = []
    for x in bnd:
        if x.node not in seen:
            seen.add(x.node)
            boundaries.append(x)

    loads: list[ElemLoadDef] = []
    if udl_kN:
        loads.append(ElemLoadDef(e_from=1, e_to=n_elem, q=-abs(udl_kN) * 1000.0))
    nloads: list[NodalLoadDef] = []
    if point_kN:
        nid = _nearest_node(nodes, span / 2, 0.0)
        nloads.append(NodalLoadDef(node=nid, dof=2, value=-abs(point_kN) * 1000.0))

    title = f"{span:.0f}m 简支梁桥" if n_spans == 1 else f"{span:.0f}m {n_spans}跨连续梁桥"

    # 逐跨施工：每跨激活单元 + 施加该跨一期恒载；自重按激活单元自动计入。
    stages: list[Stage2D] = []
    if staged and n_spans > 1:
        per = n_elem // n_spans
        for k in range(n_spans):
            seg = list(range(k * per + 1, (k + 1) * per + 1))
            s = Stage2D(id=k + 1, activate=seg)
            if udl_kN:
                s.elem_loads.append(StageElemLoadDef(
                    elems=seg, q1=-abs(udl_kN) * 1000.0, q2=-abs(udl_kN) * 1000.0))
            stages.append(s)
        if q2_kN:
            stages.append(Stage2D(
                id=n_spans + 1,
                elem_loads=[StageElemLoadDef(
                    elems=list(range(1, n_elem + 1)),
                    q1=-abs(q2_kN) * 1000.0, q2=-abs(q2_kN) * 1000.0)]))
        # 一期恒载已折进阶段荷载，不再全局施加
        loads = []

    # 写了湿度和计算龄期才把加载与读数拆成两个阶段。理论厚度可以手写，
    # 没写就用截面外轮廓的 h=2A/u。静定梁位移约放大到 1+χφ（χ=0.8）。
    if (creep_rh > 0.0 and creep_fcm > 0.0 and creep_t_end > creep_t0):
        if not stages:
            seg = list(range(1, n_elem + 1))
            first = Stage2D(id=1, activate=seg, time=creep_t0)
            if udl_kN:
                first.elem_loads.append(StageElemLoadDef(
                    elems=seg, q1=-abs(udl_kN) * 1000.0, q2=-abs(udl_kN) * 1000.0))
            for ld in nloads:
                first.loads.append(StageLoadDef(
                    node=ld.node, dof=ld.dof, value=ld.value))
            stages = [first, Stage2D(id=2, time=creep_t_end)]
            loads = []
            nloads = []
        else:
            for i, s in enumerate(stages):
                if not s.time:
                    s.time = creep_t0 + i * 7.0
            if stages[-1].time + 1e-9 < creep_t_end:
                stages.append(Stage2D(id=stages[-1].id + 1, time=creep_t_end))

    lanes: list[Lane2D] = []
    if lane:
        lanes.append(Lane2D(nodes=[n.id for n in nodes]))

    beam_type = _elem_type_from_section(1, ElemKind.BEAM, shape, h, b, tw, tf)
    if creep_rh > 0.0 and creep_fcm > 0.0 and creep_h <= 0.0:
        from model_doc import closed_contour_perimeter_m, theoretical_thickness_mm
        beam_type.h_th_mm = theoretical_thickness_mm(
            beam_type.A, closed_contour_perimeter_m(b, h))
    return BridgeSpec(
        title=title,
        nodes=nodes, elements=elements,
        elem_types=[beam_type],
        materials=[_mat(material)],
        boundaries=boundaries,
        nodal_loads=nloads, elem_loads=loads,
        stages=stages, lanes=lanes,
        creep_rh=creep_rh, creep_h=creep_h, creep_fcm=creep_fcm, creep_t0=creep_t0,
        shrinkage_ts=shrinkage_ts,
        gravity=gravity,
        note=f"跨度{span}m 跨数{n_spans} {shape} 截面 h={h}m b={b}m UDL={udl_kN}kN/m",
    )


# ---------------------------------------------------------------- 建造器 2：拱桥


def build_arch_bridge(
    span: float = 100.0,
    rise: float = 20.0,
    n_hanger: int = 9,
    n_deck_elem: int = 20,
    shape_deck: str = "box",
    h_deck: float = 2.0,
    b_deck: float = 8.0,
    tw_deck: float | None = 0.02,
    tf_deck: float | None = 0.025,
    shape_arch: str = "box",
    h_arch: float = 1.6,
    b_arch: float = 6.0,
    tw_arch: float | None = 0.025,
    tf_arch: float | None = 0.03,
    hanger_A: float = 0.001,
    hanger_kind: ElemKind = ElemKind.TRUSS,
    material: str = "钢材 Q345",
    udl_kN: float = 20.0,
    point_kN: float = 0.0,
    gravity: float = -9.81,
) -> BridgeSpec:
    """下承式拱桥：主梁(BEAM) + 拱肋(BEAM) + 吊杆(TRUSS 或 CABLE)。

    拓扑与 FEM-Eigen 自带的 arch_bridge_100m.dat 一致：
    拱脚直接接在主梁两端节点上，吊杆竖直连接主梁与拱肋。
    吊杆用 CABLE 时即为「只受拉索」，受压自动退出工作（可做索松弛验算）。
    """
    # --- 主梁节点：均匀网格 ∪ 吊杆位置，保证吊杆下端点精确落在节点上 ---
    grid = [span * i / n_deck_elem for i in range(n_deck_elem + 1)]
    hx = [span * (i + 1) / (n_hanger + 1) for i in range(n_hanger)]
    xs = sorted({round(x, 6) for x in grid} | {round(x, 6) for x in hx})
    nodes = [NodeDef(id=i + 1, x=x, y=0.0) for i, x in enumerate(xs)]
    x_to_deck = {round(n.x, 6): n.id for n in nodes}

    deck_elems = [ElemDef(id=i + 1, type_id=1, n1=i + 1, n2=i + 2)
                  for i in range(len(xs) - 1)]
    n_deck = len(deck_elems)

    # --- 拱肋节点：二次抛物线 ---
    arch_ids: list[int] = []
    for i, x in enumerate(hx):
        y = 4.0 * rise * x * (span - x) / (span * span)
        nid = 101 + i
        arch_ids.append(nid)
        nodes.append(NodeDef(id=nid, x=x, y=y))

    # 拱肋链：主梁左端 → 拱脚 → … → 拱顶 → … → 拱脚 → 主梁右端
    arch_elems: list[ElemDef] = []
    eid = 101
    arch_elems.append(ElemDef(id=eid, type_id=2, n1=1, n2=arch_ids[0]))
    eid += 1
    for a, bnode in zip(arch_ids, arch_ids[1:]):
        arch_elems.append(ElemDef(id=eid, type_id=2, n1=a, n2=bnode))
        eid += 1
    arch_elems.append(ElemDef(id=eid, type_id=2, n1=arch_ids[-1], n2=len(xs)))

    # --- 吊杆：竖直，主梁节点 → 拱肋节点 ---
    hang_elems: list[ElemDef] = []
    for i, x in enumerate(hx):
        hang_elems.append(ElemDef(id=201 + i, type_id=3,
                                  n1=x_to_deck[round(x, 6)], n2=arch_ids[i]))

    # --- 支座：主梁两端，左铰右辊 ---
    boundaries = [BoundaryDef(node=1, dofs="1-2"),
                  BoundaryDef(node=len(xs), dofs="2")]

    loads = [ElemLoadDef(e_from=1, e_to=n_deck, q=-abs(udl_kN) * 1000.0)] if udl_kN else []
    nloads: list[NodalLoadDef] = []
    if point_kN:
        nloads.append(NodalLoadDef(node=_nearest_node(nodes, span / 2), dof=2,
                                   value=-abs(point_kN) * 1000.0))

    return BridgeSpec(
        title=f"{span:.0f}m 拱桥（矢高{rise:.0f}m，{n_hanger}对吊杆·{hanger_kind.value}）",
        nodes=nodes,
        elements=deck_elems + arch_elems + hang_elems,
        elem_types=[
            _elem_type_from_section(1, ElemKind.BEAM, shape_deck, h_deck, b_deck,
                                    tw_deck, tf_deck),
            _elem_type_from_section(2, ElemKind.BEAM, shape_arch, h_arch, b_arch,
                                    tw_arch, tf_arch),
            ElemTypeDef(id=3, kind=hanger_kind, A=hanger_A),
        ],
        materials=[_mat(material)],
        boundaries=boundaries,
        nodal_loads=nloads, elem_loads=loads,
        gravity=gravity,
        note=(f"跨度{span}m 矢高{rise}m（矢跨比 1/{span/rise:.1f}）"
              f" 主梁{shape_deck} h={h_deck}m 拱肋{shape_arch} h={h_arch}m "
              f"吊杆{hanger_kind.value} A={hanger_A} UDL={udl_kN}kN/m"),
    )


# ---------------------------------------------------------------- 建造器 3：桁架桥


def build_truss_bridge(
    span: float = 60.0,
    n_panel: int = 8,
    depth: float = 6.0,
    A_chord: float = 0.02,
    A_diag: float = 0.012,
    material: str = "钢材 Q345",
    node_load_kN: float = 100.0,
    gravity: float = -9.81,
) -> BridgeSpec:
    """简支 Warren 桁架：下弦、上弦、斜杆均为 TRUSS（只受轴力）。

    下弦节点 i=0..N，上弦节点位于半节间处 ((i+0.5)·L/N, depth)。
    """
    n_panel = max(2, int(n_panel))
    panel = span / n_panel

    lower = list(range(1, n_panel + 2))
    nodes: list[NodeDef] = [NodeDef(id=i + 1, x=i * panel, y=0.0)
                            for i in range(n_panel + 1)]
    upper: list[int] = []
    for i in range(n_panel):
        nid = 101 + i
        upper.append(nid)
        nodes.append(NodeDef(id=nid, x=(i + 0.5) * panel, y=depth))

    elems: list[ElemDef] = []
    eid = 1
    # 下弦（A_chord）
    for a, b in zip(lower, lower[1:]):
        elems.append(ElemDef(id=eid, type_id=1, n1=a, n2=b)); eid += 1
    # 上弦（A_chord）
    for a, b in zip(upper, upper[1:]):
        elems.append(ElemDef(id=eid, type_id=1, n1=a, n2=b)); eid += 1
    # 斜杆 + 端斜杆（A_diag）
    for i in range(n_panel):
        elems.append(ElemDef(id=eid, type_id=2, n1=lower[i], n2=upper[i])); eid += 1
        elems.append(ElemDef(id=eid, type_id=2, n1=upper[i], n2=lower[i + 1])); eid += 1

    # 支座：左端铰（u、v），右端竖向辊
    boundaries = [BoundaryDef(node=lower[0], dofs="1-2"),
                  BoundaryDef(node=lower[-1], dofs="2")]
    # 纯桁架模型必须约束**所有节点**的转角自由度（dof 3）：
    # TRUSS 单元只连接 u、v，转角没有任何单元提供刚度，求解器会直接报
    # "存在零刚度自由度：节点 x dof 3"。约束它不影响桁架内力（转角不是桁架自由度）。
    boundaries += [BoundaryDef(node=n.id, dofs="3") for n in nodes]

    # 荷载施加在下弦中间各节点（桥面荷载通过横梁传到下弦）
    nloads = [NodalLoadDef(node=nid, dof=2, value=-abs(node_load_kN) * 1000.0)
              for nid in lower[1:-1]] if node_load_kN else []

    return BridgeSpec(
        title=f"{span:.0f}m 简支桁架（{n_panel}节间，桁高{depth:.0f}m）",
        nodes=nodes,
        elements=elems,
        elem_types=[
            ElemTypeDef(id=1, kind=ElemKind.TRUSS, A=A_chord),
            ElemTypeDef(id=2, kind=ElemKind.TRUSS, A=A_diag),
        ],
        materials=[_mat(material)],
        boundaries=boundaries,
        nodal_loads=nloads,
        gravity=gravity,
        note=f"跨度{span}m {n_panel}节间 桁高{depth}m 弦杆A={A_chord} 腹杆A={A_diag} "
             f"节点荷载={node_load_kN}kN",
    )


# ---------------------------------------------------------------- 建造器 4：2D 斜拉桥


def build_cable_stayed_2d(
    main_span: float = 200.0,
    side_span: float = 80.0,
    tower_height: float = 60.0,
    n_cable: int = 12,
    h: float = 2.5,
    B: float = 12.0,
    tf: float = 0.28,
    tw: float = 0.45,
    tower_b: float = 3.0,
    tower_h: float = 4.5,
    tower_below: float = 8.0,
    q_kN: float = 100.0,
    q2_kN: float = 0.0,
    cable_area_mm2: float = 6000.0,
    anchor_low: float = 0.55,
    cable_E: float = 195e9,
    deck_E: float = 34.5e9,
    deck_dens: float = 2500.0,
    tower_E: float = 34.5e9,
    tower_dens: float = 2500.0,
    lane: bool = False,
    staged: bool = False,
    creep_rh: float = 0.0,
    creep_h: float = 0.0,
    creep_fcm: float = 0.0,
    creep_t0: float = 7.0,
    creep_t_end: float = 0.0,
    shrinkage_ts: float = 0.0,
    load_age_days: float = 7.0,
    cycle_days: float = 7.0,
) -> BridgeSpec:
    """双塔三跨平面斜拉桥（2D 简化，成桥态一次静力 / 可选悬臂施工）。

    与三维 `build_cable_stayed_3d` 共用同一套工程参数（Lm/Ls/Ht/n/q/A…），
    只是把主梁降成一条平面梁线、桥塔降成竖直悬臂、扇形索面压进 x-y 平面。
    索力用「按锚点受荷宽度竖向分配」初定（与 3D 的 tensions_by_deck_load 同源），
    以 ISTRN 预应变写进 CABLE 单元：N = EA·ΔL/L + EA·ISTRN。

    与 3D 的差别（刻意）：
    - 不做几何非线性找形：2D 内核走 LINEAR（线弹性 + 索单向开关迭代）。
    - 体系用**塔梁固结**（塔基在桥面以下 tower_below，桥面处塔梁共节点）——
      这是悬臂施工稳定性的前提，也是钢斜拉桥常用成桥体系；3D 成桥态默认的
      半漂浮体系在 2D 简化里不单列，避免两套拓扑。
    - 主跨每侧索覆盖半跨（跨中为合拢点），不留无索区。
    """
    if n_cable < 2:
        raise ValueError("每塔每侧索数 n_cable 至少 2")
    q = abs(q_kN) * 1000.0
    q2 = abs(q2_kN) * 1000.0
    if q2 > q:
        raise ValueError(f"二期恒载 {q2_kN:g} kN/m 超过桥面恒载 {q_kN:g} kN/m")
    q1 = q - q2
    Ltot = 2.0 * side_span + main_span
    xT1, xT2 = side_span, side_span + main_span
    x_mid = xT1 + main_span / 2.0
    Ht = float(tower_height)

    # ---- 主梁锚点 x（每塔两侧各 n_cable 根，共 4 组；j=0 最靠塔） ----
    side1 = [round(xT1 - side_span * (j + 1) / (n_cable + 1), 9) for j in range(n_cable)]
    main1 = [round(xT1 + (x_mid - xT1) * (j + 1) / (n_cable + 1), 9) for j in range(n_cable)]
    main2 = [round(xT2 - (x_mid - xT1) * (j + 1) / (n_cable + 1), 9) for j in range(n_cable)]
    side2 = [round(xT2 + side_span * (j + 1) / (n_cable + 1), 9) for j in range(n_cable)]
    zones = [side1, main1, main2, side2]          # t1s, t1m, t2m, t2s
    zone_tower = [xT1, xT1, xT2, xT2]

    # ---- 主梁节点 x：支座/塔位/合拢点/锚点并集 ----
    xs = sorted({0.0, xT1, xT2, Ltot, x_mid} | {x for g in zones for x in g})
    xs = [round(x, 9) for x in xs]
    m = len(xs)
    iT1 = xs.index(round(xT1, 9))
    iT2 = xs.index(round(xT2, 9))

    # ---- 塔（竖直悬臂，塔基在桥面以下；桥面处塔梁共节点） ----
    f_j = [anchor_low + (1.0 - anchor_low) * j / (n_cable - 1) for j in range(n_cable)]
    th = [-tower_below, 0.0] + [f_j[k] * Ht for k in range(n_cable)]  # n+2 个塔节点高度
    cable_A = cable_area_mm2 * 1e-6

    nodes: list[NodeDef] = []
    for ti, (xT, base) in enumerate(((xT1, 2000), (xT2, 3000))):
        for k, yv in enumerate(th):
            nodes.append(NodeDef(id=base + k, x=xT, y=yv))

    # 主梁节点：塔位复用塔节点（y=0），其余新建
    node_at: dict[float, int] = {}
    di = 0
    for x in xs:
        if abs(x - xT1) < 1e-9:
            node_at[x] = 2001
        elif abs(x - xT2) < 1e-9:
            node_at[x] = 3001
        else:
            nid = 1000 + di
            nodes.append(NodeDef(id=nid, x=x, y=0.0))
            node_at[x] = nid
            di += 1

    # ---- 材料 / 截面 ----
    mat_conc = MaterialDef(id=1, name="混凝土 C50", E=deck_E, alpx=1.0e-5, dens=deck_dens)
    mat_cable = MaterialDef(id=2, name="高强钢丝", E=cable_E, alpx=1.2e-5, dens=7850.0)
    deck_type = _elem_type_from_section(1, ElemKind.BEAM, "box", h, B, tw, tf)
    deck_type.mat = 1
    tower_A = tower_b * tower_h
    tower_I = tower_b * tower_h ** 3 / 12.0
    tower_type = ElemTypeDef(id=2, kind=ElemKind.BEAM, A=tower_A, I=tower_I,
                             WT=tower_b * tower_h ** 2 / 6.0,
                             WB=tower_b * tower_h ** 2 / 6.0,
                             H=tower_h, mat=1)
    if creep_rh > 0.0 and creep_fcm > 0.0 and creep_h <= 0.0:
        from model_doc import closed_contour_perimeter_m, theoretical_thickness_mm
        deck_type.h_th_mm = theoretical_thickness_mm(
            deck_type.A, closed_contour_perimeter_m(B, h))
        tower_type.h_th_mm = theoretical_thickness_mm(
            tower_A, closed_contour_perimeter_m(tower_b, tower_h))

    # ---- 单元：主梁 + 塔 ----
    elements = [ElemDef(id=101 + i, type_id=1, n1=node_at[xs[i]], n2=node_at[xs[i + 1]])
                for i in range(m - 1)]
    tower_elem_ids: dict[int, list[int]] = {}
    for ti, base in ((0, 2000), (1, 3000)):
        eids = [200 + ti * 100 + k + 1 for k in range(n_cable + 1)]
        tower_elem_ids[ti] = eids
        for k, eid in enumerate(eids):
            elements.append(ElemDef(id=eid, type_id=2, n1=base + k, n2=base + k + 1))

    # ---- 拉索（CABLE，ISTRN = 锚点受荷宽度竖向力 / 竖向余弦 / EA） ----
    cable_types: list[ElemTypeDef] = []
    cables_by_zone: list[list[int]] = []
    cidx = 0
    for zi, (xT, anchors) in enumerate(zip(zone_tower, zones)):
        base = 2000 if zi < 2 else 3000
        zcables: list[int] = []
        for j, xa in enumerate(anchors):
            i = xs.index(xa)
            left = (xs[i] - xs[i - 1]) / 2 if i > 0 else 0.0
            right = (xs[i + 1] - xs[i]) / 2 if i < m - 1 else 0.0
            trib = left + right
            yt = f_j[j] * Ht
            dx = xa - xT
            L = math.hypot(dx, yt)
            sin_theta = yt / L
            T = q * trib / sin_theta
            istrn = T / (cable_E * cable_A)
            eid = 9000 + cidx + 1
            elements.append(ElemDef(id=eid, type_id=3 + cidx,
                                    n1=base + 2 + j, n2=node_at[xa]))
            cable_types.append(ElemTypeDef(id=3 + cidx, kind=ElemKind.CABLE,
                                           A=cable_A, istrn=istrn, mat=2))
            zcables.append(eid)
            cidx += 1
        cables_by_zone.append(zcables)

    # ---- 支座 ----
    boundaries = [
        BoundaryDef(node=node_at[0.0], dofs="1-2"),
        BoundaryDef(node=node_at[round(Ltot, 9)], dofs="2"),
        BoundaryDef(node=2000, dofs="1-3"),
        BoundaryDef(node=3000, dofs="1-3"),
    ]

    # ---- 荷载：桥面恒载 + 塔自重（自重已含在 q 里，塔自重量手动施加） ----
    elem_loads: list[ElemLoadDef] = [] if staged else \
        [ElemLoadDef(e_from=101, e_to=100 + m - 1, q=-q)]
    nodal_loads: list[NodalLoadDef] = []
    if tower_dens:
        w_tower = tower_A * tower_dens * 9.81
        for ti, base in ((0, 2000), (1, 3000)):
            for k in range(n_cable + 1):
                seg = th[k + 1] - th[k]
                nodal_loads.append(NodalLoadDef(node=base + k, dof=2, value=-w_tower * seg / 2))
                nodal_loads.append(NodalLoadDef(node=base + k + 1, dof=2, value=-w_tower * seg / 2))

    # ---- 施工阶段（悬臂施工 + 张拉） ----
    stages: list[Stage2D] = []
    if staged:
        tower_elems = [e for ti in (0, 1) for e in tower_elem_ids[ti]]
        active: set[int] = set()
        zero = {iT1 - 1, iT1, iT2 - 1, iT2}
        stages.append(Stage2D(
            id=1, activate=tower_elems + [101 + i for i in sorted(zero)],
            elem_loads=[StageElemLoadDef(elems=[101 + i for i in sorted(zero)],
                                         q1=-q1, q2=-q1)]))
        active |= zero
        for j in range(n_cable):
            new_elems: set[int] = set()
            new_cables: list[int] = []
            for zi, xT in enumerate(zone_tower):
                xa = zones[zi][j]
                lo, hi = sorted((xT, xa))
                for i in range(m - 1):
                    mid = (xs[i] + xs[i + 1]) / 2
                    if lo < mid < hi and i not in active:
                        new_elems.add(i)
                new_cables.append(cables_by_zone[zi][j])
            active |= new_elems
            stages.append(Stage2D(
                id=2 + j,
                activate=[101 + i for i in sorted(new_elems)] + new_cables,
                elem_loads=[StageElemLoadDef(elems=[101 + i for i in sorted(new_elems)],
                                             q1=-q1, q2=-q1)]))
        rest = [i for i in range(m - 1) if i not in active]
        if rest:
            stages.append(Stage2D(
                id=2 + n_cable, activate=[101 + i for i in rest],
                elem_loads=[StageElemLoadDef(elems=[101 + i for i in rest],
                                             q1=-q1, q2=-q1)]))
        if q2 > 0:
            stages.append(Stage2D(
                id=3 + n_cable,
                elem_loads=[StageElemLoadDef(elems=list(range(101, 100 + m)),
                                             q1=-q2, q2=-q2)]))
        if creep_rh > 0.0 and creep_fcm > 0.0 and (creep_h > 0.0 or deck_type.h_th_mm > 0.0):
            for i, s in enumerate(stages):
                s.time = load_age_days + i * cycle_days
            if creep_t_end > stages[-1].time + 1e-9:
                stages.append(Stage2D(id=stages[-1].id + 1, time=creep_t_end))

    lanes: list[Lane2D] = [Lane2D(nodes=[node_at[x] for x in xs])] if lane else []

    return BridgeSpec(
        title=f"{main_span:.0f}m 双塔三跨斜拉桥（2D，{n_cable} 对索/塔）",
        nodes=nodes,
        elements=elements,
        elem_types=[deck_type, tower_type] + cable_types,
        materials=[mat_conc, mat_cable],
        boundaries=boundaries,
        nodal_loads=nodal_loads,
        elem_loads=elem_loads,
        stages=stages,
        lanes=lanes,
        creep_rh=creep_rh, creep_h=creep_h, creep_fcm=creep_fcm,
        creep_t0=creep_t0, shrinkage_ts=shrinkage_ts,
        gravity=0.0,
        analysis="NONLINEAR",
        note=(f"主跨{main_span}m 边跨{side_span}m 塔高{Ht}m 每塔每侧{n_cable}索 "
              f"桥面恒载{q_kN}kN/m 单索{cable_area_mm2}mm² "
              f"{'悬臂施工' if staged else '成桥态'}"),
    )


# ---------------------------------------------------------------- 建造器 5：2D 悬索桥


def build_suspension_2d(
    system: str = "EARTH",
    main_span: float = 300.0,
    side_span: float = 0.0,
    sag_ratio: float = 0.10,
    clearance: float = 5.0,
    tower_below: float = 8.0,
    panel: float = 10.0,
    side_hangers: bool = True,
    h: float = 3.0,
    B: float = 12.0,
    tf: float = 0.028,
    tw: float = 0.020,
    tower_b: float = 3.0,
    tower_h: float = 4.0,
    n_tower_seg: int = 6,
    q_kN: float = 100.0,
    main_area_mm2: float = 60000.0,
    hanger_area_mm2: float = 3000.0,
    cable_E: float = 195e9,
    deck_E: float = 206e9,
    deck_dens: float = 7850.0,
    tower_E: float = 34.5e9,
    tower_dens: float = 2500.0,
    lane: bool = False,
) -> BridgeSpec:
    """单跨双塔平面悬索桥（2D 简化，成桥态一次静力）。

    与三维 `build_suspension_3d` 共用同一套工程参数（system/Lm/Ls/sag/clear/
    q/A/Ah…），主缆按抛物线初值、恒载水平力 H=qL²/8f 以 ISTRN 预张写进 CABLE
    单元。2D 内核 LINEAR：线弹性 + 索单向开关迭代，不做几何非线性找形。

    与 3D 的差别（刻意）：主缆用常数水平力 H 的 ISTRN 预张（各段张力 T=H/cosθ
    差异 <5% 略去），不做逐段找形；锚碇/锚点、塔顶共节点、塔处加劲梁竖向支座
    等拓扑与 3D 保持一致，避免"坐标重合而节点不共用"那类隐蔽错。
    """
    system = (system or "EARTH").upper()
    if system not in ("EARTH", "SELF"):
        raise ValueError("system 只能是 EARTH / SELF")
    Lm = float(main_span)
    f_sag = sag_ratio * Lm
    q = abs(q_kN) * 1000.0
    cable_A = main_area_mm2 * 1e-6
    hanger_A = hanger_area_mm2 * 1e-6
    z_top = f_sag + clearance
    H0 = q * Lm ** 2 / (8.0 * f_sag)

    # ---- 边跨：自锚式取平衡边跨 √(2HΔz/q)，地锚式取 1/3 主跨 ----
    if side_span and side_span > 0:
        Ls = float(side_span)
    else:
        Ls = math.sqrt(2.0 * H0 * z_top / q) if (system == "SELF" and q > 0) else Lm / 3.0
    Ltot = 2.0 * Ls + Lm
    xT1, xT2 = Ls, Ls + Lm

    # ---- 加劲梁节点：panel 均匀，塔位/跨中为节点 ----
    n_main = max(4, int(round(Lm / panel)))
    n_side = max(2, int(round(Ls / panel)))
    xs: list[float] = []
    if n_side:
        xs += [Ls * i / n_side for i in range(n_side + 1)]
    else:
        xs.append(0.0)
    xs += [Ls + Lm * j / n_main for j in range(1, n_main + 1)]
    if n_side:
        xs += [Ls + Lm + Ls * k / n_side for k in range(1, n_side + 1)]
    xs = [round(x, 9) for x in xs]
    m = len(xs)
    iT1, iT2 = n_side, n_side + n_main
    deck_ids = [1000 + i for i in range(m)]

    nodes: list[NodeDef] = [NodeDef(id=deck_ids[i], x=xs[i], y=0.0) for i in range(m)]

    # ---- 材料 / 截面 ----
    mat_deck = MaterialDef(id=1, name="钢材 Q345qD", E=deck_E, alpx=1.2e-5, dens=deck_dens)
    mat_tower = MaterialDef(id=2, name="混凝土 C50", E=tower_E, alpx=1.0e-5, dens=tower_dens)
    mat_cable = MaterialDef(id=3, name="高强钢丝", E=cable_E, alpx=1.2e-5, dens=7850.0)
    deck_type = _elem_type_from_section(1, ElemKind.BEAM, "box", h, B, tw, tf)
    deck_type.mat = 1
    tower_A = tower_b * tower_h
    tower_I = tower_b * tower_h ** 3 / 12.0
    tower_type = ElemTypeDef(id=2, kind=ElemKind.BEAM, A=tower_A, I=tower_I,
                             WT=tower_b * tower_h ** 2 / 6.0,
                             WB=tower_b * tower_h ** 2 / 6.0,
                             H=tower_h, mat=2)
    main_type = ElemTypeDef(id=3, kind=ElemKind.CABLE, A=cable_A,
                            istrn=H0 / (cable_E * cable_A), mat=3)
    hanger_type = ElemTypeDef(id=4, kind=ElemKind.CABLE, A=hanger_A, mat=3)

    # ---- 加劲梁单元 + 荷载 ----
    elements = [ElemDef(id=101 + i, type_id=1, n1=deck_ids[i], n2=deck_ids[i + 1])
                for i in range(m - 1)]
    elem_loads = [ElemLoadDef(e_from=101, e_to=100 + m - 1, q=-q)]

    # ---- 桥塔（竖直悬臂，塔基在桥面以下；塔顶与主缆共节点） ----
    tower_tops: dict[int, int] = {}
    for ti, (xT, base) in enumerate(((xT1, 3000), (xT2, 4000))):
        yb = -tower_below
        for k in range(n_tower_seg + 1):
            nodes.append(NodeDef(id=base + k, x=xT,
                                 y=yb + (z_top - yb) * k / n_tower_seg))
        for k in range(n_tower_seg):
            elements.append(ElemDef(id=800 + ti * 100 + k + 1, type_id=2,
                                    n1=base + k, n2=base + k + 1))
        tower_tops[ti] = base + n_tower_seg

    # ---- 主缆链（锚 → 塔顶 → 跨中 → 塔顶 → 锚，抛物线/直线） ----
    anchor_offset = 12.0      # 地锚式锚碇在桥台外侧的距离

    def cable_z(x: float) -> float:
        if Ls - 1e-9 <= x <= Ls + Lm + 1e-9:
            t = (x - Ls) / Lm
            return z_top - f_sag * 4.0 * t * (1.0 - t)
        if x < Ls:
            x0 = 0.0 if system == "SELF" else -anchor_offset
            return 0.0 + (z_top - 0.0) * (x - x0) / max(Ls - x0, 1e-9)
        x1 = Ltot if system == "SELF" else Ltot + anchor_offset
        return z_top + (0.0 - z_top) * (x - (Ls + Lm)) / max(x1 - (Ls + Lm), 1e-9)

    anchor_offset = 12.0      # 地锚式锚碇在桥台外侧的距离

    chain_x: list[float] = []
    if system == "EARTH":
        chain_x.append(-anchor_offset)
    else:
        chain_x.append(0.0)
    # 中间节点**含塔位**（塔位在下面的循环里映射到塔顶节点，主缆必须过塔顶）
    chain_x += [x for x in xs if 1e-9 < x < Ltot - 1e-9]
    if system == "EARTH":
        chain_x.append(Ltot + anchor_offset)
    else:
        chain_x.append(Ltot)
    # 塔位插入塔顶节点，其余节点新建
    chain_node_ids: list[int] = []
    cable_own: list[int] = []
    cidx = 0
    for x in chain_x:
        if abs(x - xT1) < 1e-9:
            chain_node_ids.append(tower_tops[0]); continue
        if abs(x - xT2) < 1e-9:
            chain_node_ids.append(tower_tops[1]); continue
        if system == "SELF" and abs(x - 0.0) < 1e-9:
            chain_node_ids.append(deck_ids[0]); continue
        if system == "SELF" and abs(x - Ltot) < 1e-9:
            chain_node_ids.append(deck_ids[m - 1]); continue
        nid = 5000 + cidx
        cidx += 1
        nodes.append(NodeDef(id=nid, x=x, y=cable_z(x)))
        chain_node_ids.append(nid)
        # 地锚式锚碇节点会单独全约束（1-2-3），不再进 cable_own 补转角约束
        is_anchor = system == "EARTH" and (
            abs(x - chain_x[0]) < 1e-9 or abs(x - chain_x[-1]) < 1e-9)
        if not is_anchor:
            cable_own.append(nid)

    main_cable_elems: list[int] = []
    for k in range(len(chain_node_ids) - 1):
        eid = 6000 + k + 1
        elements.append(ElemDef(id=eid, type_id=3,
                                n1=chain_node_ids[k], n2=chain_node_ids[k + 1]))
        main_cable_elems.append(eid)

    # ---- 吊索（竖直，主缆节点 → 加劲梁节点；塔顶/锚点不挂） ----
    hangers: list[int] = []
    x_to_deck = {xs[i]: deck_ids[i] for i in range(m)}
    eid_h = 7000
    for x, nid in zip(chain_x, chain_node_ids):
        if abs(x - xT1) < 1e-9 or abs(x - xT2) < 1e-9:
            continue
        if x <= 1e-9 or x >= Ltot - 1e-9:
            continue
        key = round(x, 9)
        if key not in x_to_deck:
            continue
        eid_h += 1
        elements.append(ElemDef(id=eid_h, type_id=4, n1=nid, n2=x_to_deck[key]))
        hangers.append(eid_h)

    # ---- 支座 ----
    boundaries: list[BoundaryDef] = []
    if system == "EARTH":
        boundaries.append(BoundaryDef(node=deck_ids[0], dofs="1-2"))
        boundaries.append(BoundaryDef(node=deck_ids[m - 1], dofs="2"))
        for nid in (chain_node_ids[0], chain_node_ids[-1]):
            boundaries.append(BoundaryDef(node=nid, dofs="1-3"))   # 锚碇全约束
    else:
        boundaries.append(BoundaryDef(node=deck_ids[0], dofs="2"))
        boundaries.append(BoundaryDef(node=deck_ids[m - 1], dofs="2"))
        i_mid = min(range(m), key=lambda i: abs(xs[i] - Ltot / 2))
        boundaries.append(BoundaryDef(node=deck_ids[i_mid], dofs="1"))
    boundaries.append(BoundaryDef(node=deck_ids[iT1], dofs="2"))
    boundaries.append(BoundaryDef(node=deck_ids[iT2], dofs="2"))
    for ti, base in ((0, 3000), (1, 4000)):
        boundaries.append(BoundaryDef(node=base, dofs="1-3"))     # 塔底固结
    # 主缆内部节点只连索/吊杆（2 自由度），转角自由度无刚度，须约束
    for nid in cable_own:
        boundaries.append(BoundaryDef(node=nid, dofs="3"))

    # ---- 塔自重 ----
    nodal_loads: list[NodalLoadDef] = []
    if tower_dens:
        w_tower = tower_A * tower_dens * 9.81
        for ti, base in ((0, 3000), (1, 4000)):
            seg = (z_top + tower_below) / n_tower_seg
            for k in range(n_tower_seg):
                nodal_loads.append(NodalLoadDef(node=base + k, dof=2, value=-w_tower * seg / 2))
                nodal_loads.append(NodalLoadDef(node=base + k + 1, dof=2, value=-w_tower * seg / 2))

    lanes: list[Lane2D] = [Lane2D(nodes=deck_ids)] if lane else []

    title = f"{main_span:.0f}m {'地锚式' if system == 'EARTH' else '自锚式'}悬索桥（2D）"
    return BridgeSpec(
        title=title,
        nodes=nodes,
        elements=elements,
        elem_types=[deck_type, tower_type, main_type, hanger_type],
        materials=[mat_deck, mat_tower, mat_cable],
        boundaries=boundaries,
        nodal_loads=nodal_loads,
        elem_loads=elem_loads,
        stages=[],
        lanes=lanes,
        gravity=0.0,
        analysis="NONLINEAR",
        note=(f"主跨{Lm}m 边跨{Ls:.1f}m 矢跨比1/{1.0 / sag_ratio:.1f} "
              f"主缆{main_area_mm2}mm² 吊索{hanger_area_mm2}mm² "
              f"桥面恒载{q_kN}kN/m H≈{H0 / 1e6:.1f}MN"),
    )


# ---------------------------------------------------------------- 自然语言入口

_NUM = r"([0-9]+(?:\.[0-9]+)?)"
_UNIT = r"(毫米|mm|厘米|cm|米|m|M)?"


def _to_m(val: float, unit: str | None) -> float:
    u = (unit or "").lower()
    if "毫米" in u or u == "mm":
        return val / 1000.0
    if "厘米" in u or u == "cm":
        return val / 100.0
    return val


def _grab(text: str, *keys: str) -> float | None:
    for k in keys:
        m = re.search(k + r"[\s:：为约]*?" + _NUM + r"\s*" + _UNIT, text)
        if m:
            return _to_m(float(m.group(1)), m.group(2))
    return None


def _finish(kind: str, params: dict, notes: list[str]) -> tuple[str, dict, list[str]]:
    """统一出口：过一遍单位哨兵再返回。

    裸数字（"跨度120000"、"桁高6000"）在正则层面无法判断单位，
    规则路径原先直接放行 → 建出 120 公里的桥并照算不误。
    阈值与换算见 `units_guard.py`。
    """
    from units_guard import guard_units

    guard_units(params, kind, notes)
    return kind, params, notes


def extract_bridge_params(text: str) -> tuple[str, dict, list[str]]:
    """把一句中文描述抽成「桥型 + 建造参数 + 默认值说明」。

    返回 `(kind, params, notes)`，其中 kind ∈ {"arch", "truss", "beam_bridge"}，
    params 可直接作为对应 `build_*()` 的关键字参数。

    刻意**只抽参数、不建造**：C 方案要求「参数是唯一真源，模型由 build() 展开」，
    否则解析一次就把拓扑冻住，改跨度时节点表不会跟着更新。
    抓不到的用工程默认值并显式列出，绝不假装听懂了。
    """
    t = text.strip()
    notes: list[str] = []
    from units_guard import guard_bridge_span

    span = _grab(t, "跨度", "跨径", "总长", "全长", "桥长")
    if span is None:
        span = 60.0
        notes.append("未说明跨度，默认 60 m")
    # 先纠正跨度：后面的 rise/depth/h 默认值都由它推导
    span = guard_bridge_span(max(5.0, span), notes)

    udl = None
    m = re.search(_NUM + r"\s*(?:kN/m|kn/m|kN·m-1|千牛/米|kN每米)", t, re.I)
    if m:
        udl = abs(float(m.group(1)))
    if udl is None and re.search(r"均布|分布荷载|满布", t):
        udl = 20.0
        notes.append("说了均布荷载但没给值，默认 20 kN/m")

    point = None
    m = re.search(_NUM + r"\s*kN(?!/?\s*m)", t, re.I)
    if m and not re.search(r"均布", t):
        point = abs(float(m.group(1)))

    is_concrete = "混凝土" in t
    mat = "混凝土 C50" if is_concrete else "钢材 Q345"

    # ---- 拱桥 ----
    if re.search(r"拱|拱桥|拱肋", t):
        rise = _grab(t, "矢高", "拱高", "净矢高")
        if rise is None:
            m = re.search(r"矢跨比[\s:：为]*?1\s*/\s*([0-9]+(?:\.[0-9]+)?)", t)
            if m:
                rise = span / float(m.group(1))
            else:
                rise = span / 5.0
                notes.append(f"未说明矢高，按常用矢跨比 1/5 取 {rise:.1f} m")
        nh = 9
        m = re.search(r"([0-9]+)\s*(?:对|根)?\s*吊杆", t)
        if m:
            nh = int(m.group(1))
        else:
            notes.append(f"未说明吊杆数，默认 {nh} 对")
        kind = ElemKind.CABLE if re.search(r"索|拉索|缆", t) else ElemKind.TRUSS
        return _finish("arch", dict(
            span=span, rise=rise, n_hanger=nh, material=mat,
            udl=udl or 0.0, point=point or 0.0, hanger_kind=kind.value,
        ), notes)

    # ---- 桁架 ----
    if re.search(r"桁架|桁架桥|truss", t, re.I):
        depth = _grab(t, "桁高", "梁高", "高度")
        if depth is None:
            depth = span / 8.0
            notes.append(f"未说明桁高，按 1/8 跨度取 {depth:.1f} m")
        np_ = None
        m = re.search(r"([0-9]+)\s*(?:个)?\s*节间", t)
        if m:
            np_ = int(m.group(1))
        else:
            np_ = 8
            notes.append(f"未说明节间数，默认 {np_} 个")
        # 把「节点荷载」从 UDL 折算：每延米荷载 × 节间长度
        q = udl or 20.0
        node_load = q * (span / np_)
        return _finish("truss", dict(
            span=span, n_panel=np_, depth=depth, material=mat,
            node_load=node_load,
        ), notes)

    # ---- 梁桥 ----
    ns = 1
    m = re.search(r"([0-9]+)\s*跨", t)
    if m:
        ns = int(m.group(1))
    elif re.search(r"连续梁|连续", t):
        ns = 3
        notes.append("说是连续梁但没给跨数，默认 3 跨")
    h = _grab(t, "梁高", "截面高", "高度") or span / 20.0
    if not _grab(t, "梁高", "截面高", "高度"):
        notes.append(f"未说明梁高，按 1/20 跨度取 {h:.2f} m")
    b = _grab(t, "宽", "桥宽") or 6.0
    # 档二：2D 施工阶段 + 车道活载。施工词 → 逐跨施工；车道/活载词 → JTG D60
    # 影响线最不利布载包络；二期铺装 → 合拢后全桥施加（自动开启逐跨施工）。
    staged = bool(re.search(r"逐跨施工|分段施工|施工阶段|正装|悬臂|挂篮|从一侧|向另一侧", t))
    lane = bool(re.search(r"车道|活载|影响线|汽车荷载", t))
    q2 = 0.0
    m2 = re.search(r"二期(?:恒载|铺装|荷载|桥面)?[\s:：为约]*"
                   r"([0-9]+(?:\.[0-9]+)?)\s*(?:kN/m|kn/m|千牛/米)", t)
    if m2:
        q2 = float(m2.group(1))
        if not staged:
            staged = True
            notes.append("识别到二期铺装，自动开启逐跨施工（二期在合拢后全桥施加）")
    creep: dict = {}
    m_rh = re.search(r"相对湿度\s*([0-9.]+)\s*%?", t)
    m_th = re.search(r"理论厚度\s*([0-9.]+)\s*(毫米|mm|米|m)?", t, re.I)
    m_age = re.search(r"(?:计算龄期|目标龄期)\s*([0-9.]+)\s*(?:天|日)?", t)
    if m_rh:
        if not is_concrete:
            notes.append("写了湿度，但这根梁不是混凝土，不按 JTG 3362 徐变。")
        elif not m_age:
            notes.append("写了湿度，但没有计算龄期，二维梁桥不按龄期徐变。")
        elif not m_th:
            m_grade = re.search(r"混凝土\s*C\s*([0-9.]+)", t, re.I)
            fcm = 0.8 * float(m_grade.group(1)) + 8.0 if m_grade else 48.0
            creep = {
                "creep_rh": float(m_rh.group(1)), "creep_h": 0.0, "creep_fcm": fcm,
                "creep_t0": 7.0, "creep_t_end": float(m_age.group(1)),
            }
            notes.append("未写理论厚度，按截面外轮廓取 h=2A/u。")
        else:
            rh = float(m_rh.group(1))
            h_mm = float(m_th.group(1))
            unit = (m_th.group(2) or "").lower()
            if unit in ("米", "m") or (not unit and h_mm < 5.0):
                h_mm *= 1000.0
                notes.append(f"理论厚度按米换算为 {h_mm:g} mm。")
            m_grade = re.search(r"混凝土\s*C\s*([0-9.]+)", t, re.I)
            fcm = 0.8 * float(m_grade.group(1)) + 8.0 if m_grade else 48.0
            m_ts = re.search(r"(?:收缩开始龄期|干燥开始龄期|养护结束)\s*([0-9.]+)", t)
            creep = {
                "creep_rh": rh, "creep_h": h_mm, "creep_fcm": fcm,
                "creep_t0": 7.0, "creep_t_end": float(m_age.group(1)),
                "shrinkage_ts": float(m_ts.group(1)) if m_ts else 0.0,
            }
            notes.append(
                f"二维梁桥按 JTG 3362 徐变到 {float(m_age.group(1)):g} 天："
                f"相对湿度 {rh:g}%、理论厚度 {h_mm:g} mm、fcm={fcm:g} MPa。"
                "静定梁位移约放大到 1+χφ，χ=0.8，弯矩不随徐变放大。")
    return _finish("beam_bridge", dict(
        span=span, n_spans=ns, h=h, b=b, material=mat,
        udl=udl or 0.0, point=point or 0.0,
        staged=staged, q2_kN=q2, lane=lane,
        **creep,
    ), notes)


def parse_bridge_nl(text: str) -> tuple[BridgeSpec, list[str]]:
    """一句话 → BridgeSpec（保留的便捷入口，内部走 extract_bridge_params）。

    新代码请优先用 `ModelDoc.from_text()`：它把参数与展开模型分开，
    改参数时不会冻住拓扑。本函数只做「抽参数 + 立刻建造」的合并动作。
    """
    kind, p, notes = extract_bridge_params(text)
    if kind == "arch":
        spec = build_arch_bridge(
            span=p["span"], rise=p["rise"], n_hanger=p["n_hanger"],
            material=p["material"], udl_kN=p["udl"], point_kN=p["point"],
            hanger_kind=ElemKind(p["hanger_kind"]),
        )
    elif kind == "truss":
        spec = build_truss_bridge(
            span=p["span"], n_panel=p["n_panel"], depth=p["depth"],
            material=p["material"], node_load_kN=p["node_load"],
        )
    else:
        spec = build_beam_bridge(
            span=p["span"], n_spans=p["n_spans"], h=p["h"], b=p["b"],
            material=p["material"], udl_kN=p["udl"], point_kN=p["point"],
        )
    return spec, notes


# ---------------------------------------------------------------- 自检

if __name__ == "__main__":
    import sys

    specs = {
        "beam": build_beam_bridge(),
        "arch": build_arch_bridge(),
        "arch_cable": build_arch_bridge(hanger_kind=ElemKind.CABLE),
        "truss": build_truss_bridge(),
    }
    for k, s in specs.items():
        errs = s.validate_model()
        print(f"[{k}] {s.summary()}")
        print(f"       校验：{'OK' if not errs else errs}")
        if "--dat" in sys.argv:
            print(s.to_dat())
