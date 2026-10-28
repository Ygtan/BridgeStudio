"""model_spec_3d.py
==================
三维有限元模型规范（ModelSpec3D）—— BridgeStudio 与 FEM-Eigen 3D 内核
（fem_3d.hpp）之间的唯一数据契约。

坐标约定（与 BridgeStudio 全线统一）
------------------------------------
- x：桥梁纵向（顺桥向）
- y：横桥向
- z：竖向（向上为正）
- 节点 6 自由度：1=u(x) 2=v(y) 3=w(z) 4=θx 5=θy 6=θz

竖向弯曲说明（重要，源自 fem.hpp BeamElement::transformation）
--------------------------------------------------------------
梁单元局部 y/z 轴由全局 z 轴投影确定：对沿 x（或 y）方向的梁，
局部 z = 全局 z（竖向），因此**竖向弯曲（绕横桥向水平轴）用 IY**，
横桥向弯曲（绕竖轴）用 IZ。J 为圣维南扭转常数。

单元类型
--------
- BEAM ：6 自由度空间梁
- BEAM7：7 自由度薄壁梁（翘曲，需 IW）
- TRUSS：三维桁架（仅轴力）
- CABLE：斜拉索/吊杆——3D 线性内核中退化为 TRUSS 导出（只受拉的
  几何非线性属 P3 阶段二内核），导出时自动映射并记录提示。

依赖：pydantic。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

ElemKind3D = Literal["BEAM", "BEAM7", "TRUSS", "CABLE"]


def fmt(v: float) -> str:
    """数值格式化：10 位有效数字，与 fem_eigen_export 风格一致。"""
    return f"{v:.10g}"


# ---------------------------------------------------------------- 基础构件
class Node3D(BaseModel):
    id: int
    x: float
    y: float
    z: float


class Material3D(BaseModel):
    id: int
    E: float = Field(gt=0)          # 弹性模量 Pa
    dens: float = 0.0               # 密度 kg/m³（模态/自振用；静力显式加载不依赖）


class RealConst3D(BaseModel):
    """实常数（*R）。竖向弯曲用 IY；IZ 为横向弯曲；J 扭转。"""
    id: int
    A: float = Field(gt=0)          # 截面积 m²
    IY: float = 0.0                 # 竖向弯曲惯性矩 m⁴
    IZ: float = 0.0                 # 横向弯曲惯性矩 m⁴
    J: float = 0.0                  # 扭转常数 m⁴
    G: float = 0.0                  # 剪切模量 Pa
    WT: float = 0.0                 # 上缘截面模量 m³（应力输出用）
    WB: float = 0.0                 # 下缘截面模量 m³
    WY: float = 0.0                 # 侧弯截面模量 m³
    WTORS: float = 0.0              # 扭转截面模量 m³
    IW: float = 0.0                 # 翘曲常数（仅 BEAM7）m⁶
    ADDMASS: float = 0.0            # 附加线质量 kg/m
    ISTRN: float = 0.0              # 初应变（可作初拉力：F=E·A·ISTRN）


class ElemTypeDef3D(BaseModel):
    """单元类型（*ET + *TYPE/*MAT/*REAL 三元组）。"""
    id: int
    kind: ElemKind3D
    mat: int = 1
    real: int = 1


class Element3D(BaseModel):
    id: int
    etype: int                      # 指向 ElemTypeDef3D.id
    n1: int
    n2: int


class Boundary3D(BaseModel):
    """约束（*BOUNDARY）。dofs 为 1..6 的闭区间 [a, b]。"""
    node: int
    dofs: tuple[int, int]
    value: float = 0.0              # 支座沉降/强制位移
    k: float = 0.0                  # >0 即弹性支承刚度 N/m


class NodalLoad3D(BaseModel):
    node: int
    dof: int                        # 1..6
    value: float                    # N 或 N·m


class Stage3D(BaseModel):
    """施工阶段（*STAGE + *STAGE_LOAD）。

    注意：FEM-Eigen 3D 内核的 solveModel3DStages 只使用阶段荷载
    （m.loads 被忽略），因此所有荷载必须挂在某个阶段下。
    """
    id: int
    activate: list[int] = Field(default_factory=list)    # 激活单元 id
    deactivate: list[int] = Field(default_factory=list)  # 钝化单元 id
    loads: list[NodalLoad3D] = Field(default_factory=list)


class ModelSpec3D(BaseModel):
    """三维模型完整规范。to_dat() 输出 fem_3d.hpp 可直接读取的 .dat。"""
    title: str = "untitled_3d"
    nodes: list[Node3D] = Field(default_factory=list)
    materials: list[Material3D] = Field(default_factory=list)
    reals: list[RealConst3D] = Field(default_factory=list)
    etypes: list[ElemTypeDef3D] = Field(default_factory=list)
    elements: list[Element3D] = Field(default_factory=list)
    boundaries: list[Boundary3D] = Field(default_factory=list)
    loads: list[NodalLoad3D] = Field(default_factory=list)  # 仅静力求解用
    stages: list[Stage3D] = Field(default_factory=list)

    # -------------------------------------------------- 校验
    @model_validator(mode="after")
    def _check_refs(self) -> "ModelSpec3D":
        node_ids = {n.id for n in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError("节点编号重复")
        et_ids = {t.id for t in self.etypes}
        if len(et_ids) != len(self.etypes):
            raise ValueError("单元类型编号重复")
        mat_ids = {m.id for m in self.materials}
        real_ids = {r.id for r in self.reals}
        if len(real_ids) != len(self.reals):
            raise ValueError("实常数编号重复")

        for t in self.etypes:
            if t.mat not in mat_ids:
                raise ValueError(f"单元类型 {t.id} 引用了不存在的材料 {t.mat}")
            if t.real not in real_ids:
                raise ValueError(f"单元类型 {t.id} 引用了不存在的实常数 {t.real}")

        elem_ids = set()
        for e in self.elements:
            if e.id in elem_ids:
                raise ValueError(f"单元编号重复: {e.id}")
            elem_ids.add(e.id)
            if e.etype not in et_ids:
                raise ValueError(f"单元 {e.id} 引用了不存在的单元类型 {e.etype}")
            for nd in (e.n1, e.n2):
                if nd not in node_ids:
                    raise ValueError(f"单元 {e.id} 引用了不存在的节点 {nd}")

        kind_of = {t.id: t.kind for t in self.etypes}
        real_of = {r.id: r for r in self.reals}
        for e in self.elements:
            kind = kind_of[e.etype]
            r = real_of[self._etype(e.etype).real]
            if kind in ("BEAM", "BEAM7") and (r.IY <= 0 or r.J <= 0):
                raise ValueError(
                    f"BEAM/BEAM7 单元 {e.id} 的实常数缺少 IY 或 J（竖向弯曲/扭转刚度）")
            if kind == "BEAM7" and r.IW <= 0:
                raise ValueError(f"BEAM7 单元 {e.id} 缺少翘曲常数 IW")

        for b in self.boundaries:
            if b.node not in node_ids:
                raise ValueError(f"约束引用了不存在的节点 {b.node}")
            if not (1 <= b.dofs[0] <= b.dofs[1] <= 6):
                raise ValueError(f"约束自由度区间非法: {b.node} {b.dofs}")

        for ld in self.loads:
            if ld.node not in node_ids:
                raise ValueError(f"节点荷载引用了不存在的节点 {ld.node}")
            if not (1 <= ld.dof <= 6):
                raise ValueError(f"节点荷载自由度非法: {ld.dof}")

        stage_ids = [s.id for s in self.stages]
        if len(set(stage_ids)) != len(stage_ids):
            raise ValueError("阶段编号重复")
        if self.stages:
            if sorted(stage_ids) != list(range(1, len(self.stages) + 1)):
                raise ValueError("阶段编号必须为从 1 开始的连续整数")
        for s in self.stages:
            for eid in s.activate + s.deactivate:
                if eid not in elem_ids:
                    raise ValueError(f"阶段 {s.id} 激活/钝化了不存在的单元 {eid}")
            for ld in s.loads:
                if ld.node not in node_ids:
                    raise ValueError(f"阶段 {s.id} 荷载引用了不存在的节点 {ld.node}")
        return self

    def _etype(self, etype_id: int) -> ElemTypeDef3D:
        for t in self.etypes:
            if t.id == etype_id:
                return t
        raise KeyError(etype_id)

    # -------------------------------------------------- 统计
    @property
    def n_nodes(self) -> int:
        return len(self.nodes)

    @property
    def n_elements(self) -> int:
        return len(self.elements)

    def stats(self) -> dict:
        kind_count: dict[str, int] = {}
        for e in self.elements:
            k = self._etype(e.etype).kind
            kind_count[k] = kind_count.get(k, 0) + 1
        return {
            "nodes": self.n_nodes,
            "elements": self.n_elements,
            "kinds": kind_count,
            "stages": len(self.stages),
            "materials": len(self.materials),
            "reals": len(self.reals),
        }

    # -------------------------------------------------- 导出
    def to_dat(self) -> str:
        """导出 FEM-Eigen 3D new-format .dat（fem_3d.hpp readModel3D）。

        CABLE → TRUSS 映射说明：3D 线性内核无只受拉单元；索的
        几何非线性（受压退出/垂度效应）属 P3 阶段二内容。
        """
        L: list[str] = []
        L.append(f"# {self.title}")
        L.append("# 由 BridgeStudio 导出（FEM-Eigen 3D new format，单位 m/N/Pa）")

        L.append("*NODE")
        for n in sorted(self.nodes, key=lambda n: n.id):
            L.append(f"{n.id} {fmt(n.x)} {fmt(n.y)} {fmt(n.z)}")

        L.append("*ET")
        cable_mapped = False
        for t in sorted(self.etypes, key=lambda t: t.id):
            out_kind = t.kind
            if t.kind == "CABLE":
                out_kind = "TRUSS"
                cable_mapped = True
            L.append(f"{t.id} {out_kind}")
        if cable_mapped:
            L.append("# 注：CABLE 在 3D 线性内核中映射为 TRUSS（只受拉非线性属 P3）")

        L.append("*MP")
        for m in sorted(self.materials, key=lambda m: m.id):
            parts = [f"{m.id}", f"E={fmt(m.E)}"]
            if m.dens:
                parts.append(f"DENS={fmt(m.dens)}")
            L.append(" ".join(parts))

        L.append("*R")
        for r in sorted(self.reals, key=lambda r: r.id):
            kv = [f"{r.id}", f"A={fmt(r.A)}"]
            for key in ("IY", "IZ", "J", "G", "WT", "WB", "WY", "WTORS",
                        "IW", "ADDMASS", "ISTRN"):
                v = getattr(r, key)
                if v:
                    kv.append(f"{key}={fmt(v)}")
            L.append(" ".join(kv))

        # 单元：按 TYPE/MAT/REAL 状态机输出（内核按激活三元组装单元）
        state = (None, None, None)
        for e in sorted(self.elements, key=lambda e: e.id):
            t = self._etype(e.etype)
            cur = (t.id, t.mat, t.real)
            if cur != state:
                L.append(f"*TYPE,{t.id}")
                L.append(f"*MAT,{t.mat}")
                L.append(f"*REAL,{t.real}")
                state = cur
            L.append(f"*EN,{e.id},{e.n1},{e.n2}")

        if self.boundaries:
            L.append("*BOUNDARY")
            for b in self.boundaries:
                a, c = b.dofs
                ds = f"{a}-{c}" if a != c else f"{a}"
                parts = [f"{b.node}", ds]
                if b.value or b.k:
                    parts.append(fmt(b.value))
                if b.k:
                    parts.append(f"K={fmt(b.k)}")
                L.append(" ".join(parts))

        if self.loads:
            L.append("*CLOAD")
            for ld in self.loads:
                L.append(f"{ld.node} {ld.dof} {fmt(ld.value)}")

        for s in self.stages:
            L.append("*STAGE")
            parts = [f"{s.id}"]
            if s.activate:
                parts.append("ACTIVE=" + ",".join(str(i) for i in s.activate))
            if s.deactivate:
                parts.append("DEACTIVE=" + ",".join(str(i) for i in s.deactivate))
            L.append(" ".join(parts))
            for ld in s.loads:
                L.append(f"*STAGE_LOAD")
                L.append(f"{s.id} NODE={ld.node} DOF={ld.dof} VALUE={fmt(ld.value)}")

        L.append("*END")
        return "\n".join(L) + "\n"
