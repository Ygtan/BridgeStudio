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
    """实常数（`*R`）。BEAM 必须有 I；TRUSS/CABLE/STRUT 必须有 A。"""

    id: int
    kind: ElemKind = ElemKind.BEAM
    A: float                    # 截面积 m²
    I: float | None = None      # 惯性矩 m⁴（BEAM 必填）
    WT: float | None = None     # 上翼缘抗弯模量 m³（应力图用，缺则跳过应力图）
    WB: float | None = None     # 下翼缘抗弯模量 m³
    H: float | None = None      # 截面高度 m（温度梯度用）
    addmass: float | None = None  # 附加线质量 kg/m
    istrn: float | None = None  # 初应变，正值预拉（索/杆用）


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
    """解析自由度写法："1-2"→{1,2}，"1-3"→{1,2,3}，"2"→{2}，"1,3"→{1,3}。"""
    out: set[int] = set()
    for part in re.split(r"[,\s]+", dofs.strip()):
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                out.update(range(int(a), int(b) + 1))
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
    gravity: float = -9.81       # 0.0 表示不计自重
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
            out.append("*MAT,1")
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

    lanes: list[Lane2D] = []
    if lane:
        lanes.append(Lane2D(nodes=[n.id for n in nodes]))

    return BridgeSpec(
        title=title,
        nodes=nodes, elements=elements,
        elem_types=[_elem_type_from_section(1, ElemKind.BEAM, shape, h, b, tw, tf)],
        materials=[_mat(material)],
        boundaries=boundaries,
        nodal_loads=nloads, elem_loads=loads,
        stages=stages, lanes=lanes,
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
    return _finish("beam_bridge", dict(
        span=span, n_spans=ns, h=h, b=b, material=mat,
        udl=udl or 0.0, point=point or 0.0,
        staged=staged, q2_kN=q2, lane=lane,
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
