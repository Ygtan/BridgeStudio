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


# ---------------------------------------------------------------- 紧凑输出
# 内核 readModel3D 支持两条压缩语法（见 fem_3d.hpp 的 crEvalToken）：
#
#     *FILL <首节点> <末节点>        等间距节点只写首末两点，中间点由内核插值
#     *DO I a b ... *ENDDO           规则排列的单元用索引表达式展开
#
# 压缩的目的**不是**把文件弄小，而是让编号规律可见：一座斜拉桥 80 根索、
# 上百个梁段时，逐行 dat 有几千行，人工核对锚点与单元号几乎不可能；压缩后
# "第 k 号梁段连接 k 与 k+1 号节点" 是一眼能看出来的。
#
# 但压缩必须**可逆**：内核读回来的模型要与逐行写法逐字段相同。因此
#   · 只在"严格等距 / 严格等差"时才压缩，判据宁紧勿松——压错了会静默
#     移动节点（坐标差几微米），力学上看不出来，事后无法追查；
#   · 带单元级附加项（W=/L0=）的单元一律单行：*DO 的循环体每轮是同一行
#     文本，表达不了"只有某一根索的 W 不同"。
_FILL_TOL = 1e-9


def _consecutive_runs(ids: list[int]) -> list[tuple[int, int]]:
    """把升序整数序列切成连续段（闭区间）。"""
    out: list[tuple[int, int]] = []
    if not ids:
        return out
    start = prev = ids[0]
    for x in ids[1:]:
        if x == prev + 1:
            prev = x
            continue
        out.append((start, prev))
        start = prev = x
    out.append((start, prev))
    return out


def _compress_node_lines(nodes) -> list[str]:
    """节点段压缩：连续 id 且 x/y/z 等步长（≥3 点）时写首末两点 + *FILL。"""
    ns = sorted(nodes, key=lambda n: n.id)
    lines: list[str] = []

    def one(n) -> str:
        return f"{n.id} {fmt(n.x)} {fmt(n.y)} {fmt(n.z)}"

    def step_close(a, b, c) -> bool:
        """a→b 与 b→c 的两步是否同长（容差随坐标量级放大）。"""
        scale = max(1.0, abs(a.x), abs(b.x), abs(c.x), abs(a.y), abs(b.y),
                    abs(c.y), abs(a.z), abs(b.z), abs(c.z))
        tol = _FILL_TOL * scale
        return (abs((b.x - a.x) - (c.x - b.x)) <= tol
                and abs((b.y - a.y) - (c.y - b.y)) <= tol
                and abs((b.z - a.z) - (c.z - b.z)) <= tol)

    i = 0
    while i < len(ns):
        group = [ns[i]]
        j = i + 1
        while j < len(ns) and ns[j].id == group[-1].id + 1:
            group.append(ns[j])
            j += 1

        # 连续 id 段内再按"步长相同"切分。边跨 16 段、主跨 32 段时，步长会
        # 在跨界处从 5 m 跳到 6.25 m；若整段判等距，主梁一段都压不动
        # （实测压缩率只有 80%，且大模型上几乎没有收益）。
        cuts = [0]
        for k in range(1, len(group) - 1):
            if not step_close(group[k - 1], group[k], group[k + 1]):
                cuts.append(k)
        cuts.append(len(group) - 1)

        # 相邻子段共用转折点，只写一次：*FILL 只要求首末点已定义，
        # 而转折点在上一个子段已经写过。
        prev_id: int | None = None
        for a, b in zip(cuts, cuts[1:]):
            seg = group[a:b + 1]
            if len(seg) >= 3:
                if seg[0].id != prev_id:
                    lines.append(one(seg[0]))
                lines.append(one(seg[-1]))
                lines.append(f"*FILL {seg[0].id} {seg[-1].id}")
                prev_id = seg[-1].id
            else:
                for g in seg:
                    if g.id != prev_id:
                        lines.append(one(g))
                    prev_id = g.id
        i = j
    return lines


def _expr(coeff: int, const: int) -> str:
    """生成 *DO 体内可用的索引表达式（内核支持 + - * / 与括号）。"""
    if coeff == 0:
        return str(const)
    body = "I" if coeff == 1 else f"{coeff}*I"
    if const == 0:
        return body
    return f"{body}+{const}" if const > 0 else f"{body}-{abs(const)}"


def _fit_linear(values: list[int], ids: list[int]) -> tuple[int, int] | None:
    """拟合 values = a*id + b（a ≥ 0）。"""
    if len(values) < 2:
        return None
    a = values[1] - values[0]
    if a < 0:
        return None
    b = values[0] - a * ids[0]
    if all(v == a * i + b for v, i in zip(values, ids)):
        return a, b
    return None


def _compress_element_lines(items: list[tuple[int, int, int, str]]) -> list[str]:
    """单元段压缩：items = [(id, n1, n2, extra)]。

    extra 非空（单元级 W=/L0= 覆盖）的单元不参与循环，单独成行。
    每段取"最长可拟合"的连续 id 段，与 X20 导出器的策略一致：贪心取最长，
    因为一段 *DO 的表达力只取决于首末，越长越省。
    """
    singles = [t for t in items if t[3]]
    loops = sorted((t[:3] for t in items if not t[3]), key=lambda t: t[0])
    body: list[str] = []

    k = 0
    while k < len(loops):
        best: tuple[int, int, tuple[int, int], tuple[int, int]] | None = None
        best_end = k
        for m in range(k + 3, len(loops) + 1):
            seg = loops[k:m]
            ids = [t[0] for t in seg]
            if any(ids[p + 1] != ids[p] + 1 for p in range(len(ids) - 1)):
                break                      # 再往后也不可能连续
            f1 = _fit_linear([t[1] for t in seg], ids)
            if f1 is None:
                continue
            f2 = _fit_linear([t[2] for t in seg], ids)
            if f2 is None:
                continue
            best = (ids[0], ids[-1], f1, f2)
            best_end = m
        if best is not None:
            first, last, f1, f2 = best
            body.append(f"*DO I {first} {last}")
            body.append(f"*EN,I,{_expr(f1[0], f1[1])},{_expr(f2[0], f2[1])}")
            body.append("*ENDDO")
            k = best_end
        else:
            t = loops[k]
            body.append(f"*EN,{t[0]},{t[1]},{t[2]}")
            k += 1

    # 带附加项的单元按 id 插回（数量通常极少，直接追加在末尾会打乱编号顺序，
    # 而编号顺序本身是核对的线索，所以按 id 归并）
    out = list(body)
    for t in sorted(singles, key=lambda t: t[0]):
        out.append(f"*EN,{t[0]},{t[1]},{t[2]} {t[3]}")
    return out


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
    W: float = 0.0                  # 索单位长度重量 N/m（CABLE 垂度/Ernst 用）
    L0: float = 0.0                 # 无应力索长 m（0 = 取几何长度）


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


class Influence3D(BaseModel):
    """影响线自由度（*INFLUENCE_3D <node> <dof>）。"""
    node: int
    dof: int = Field(ge=1, le=6)


class Lane3D(BaseModel):
    """车道荷载（*LANE_3D）。

    CODE="JTG_D60" 时由内核按车道首末节点距离 L 自动取
    qk=10.5 kN/m、Pk(L)（L≤5m:270kN，L≥50m:360kN，线性内插）。
    """
    nodes: list[int] = Field(min_length=2)   # 车道纵列节点（顺序即行车方向）
    q: float = 0.0                           # N/m（CODE 模式下可缺省）
    p: float = 0.0                           # N
    dof: int = 3                             # 加载方向自由度（3=竖向）
    impact: float = 1.0                      # 冲击系数
    reduction: float = 1.0                   # 多车道横向折减
    code: str = ""                           # "JTG_D60" 或空


class CableSpec3D(BaseModel):
    """一根待定索：单元 id + 梁侧锚点 + 目标成桥索力（0 = 自动定）。"""
    elem: int
    anchor: int = 0                 # 0 = 由几何自动判定（z 较低的那端）
    tension: float = 0.0            # N，拉为正；<=0 表示自动


class CableBridge3D(BaseModel):
    """成桥态求解工况（对应内核 *CABLE_BRIDGE 段）。

    MODE：
      - COMPLETION：成桥态闭环。由设计线形反算下料索长，并迭代制造线形
        （= 预抬高）。这是"施工控制"真正要的东西。
      - TENSION：已给全目标索力，只做无应力索长迭代找形。
      - STATIC：按 .dat 里给的 L0（无应力索长）直接做几何非线性静力。
    """
    mode: Literal["COMPLETION", "TENSION", "STATIC"] = "COMPLETION"
    method: Literal["MINENERGY", "RIGID"] = "MINENERGY"   # 自动定索力方法
    nl_steps: int = 3
    newton: int = 30
    max_iter: int = 20
    tol: float = 1e-8
    tol_tension: float = 1e-7
    min_tension_fraction: float = 0.02
    deck_load: float = 0.0          # 桥面恒载合计 N（仅用于竖向平衡校核）
    verbose: bool = False
    cables: list[CableSpec3D] = Field(default_factory=list)


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
    influences: list[Influence3D] = Field(default_factory=list)
    lanes: list[Lane3D] = Field(default_factory=list)
    cable_bridge: "CableBridge3D | None" = None

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
        for inf in self.influences:
            if inf.node not in node_ids:
                raise ValueError(f"影响线引用了不存在的节点 {inf.node}")
        for lane in self.lanes:
            for nd in lane.nodes:
                if nd not in node_ids:
                    raise ValueError(f"车道引用了不存在的节点 {nd}")

        if self.cable_bridge is not None:
            kind_of_et = {t.id: t.kind for t in self.etypes}
            elem_by_id = {e.id: e for e in self.elements}
            if not self.cable_bridge.cables:
                raise ValueError("成桥态工况未指定任何索（cable_bridge.cables 为空）")
            for c in self.cable_bridge.cables:
                if c.elem not in elem_by_id:
                    raise ValueError(f"成桥态工况引用了不存在的单元 {c.elem}")
                e = elem_by_id[c.elem]
                if kind_of_et[e.etype] != "CABLE":
                    raise ValueError(f"成桥态工况的单元 {c.elem} 不是 CABLE 类型")
                if c.anchor and c.anchor not in (e.n1, e.n2):
                    raise ValueError(
                        f"索 {c.elem} 的锚点 {c.anchor} 不是其端点（{e.n1}/{e.n2}）")
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
    def to_dat(self, compact: bool = True) -> str:
        """导出 FEM-Eigen 3D new-format .dat（fem_3d.hpp readModel3D）。

        CABLE 的两种导出行为（重要）：
        - 无 cable_bridge（走 3D **线性** 内核 example_3d_grillage）时，
          CABLE 映射为 TRUSS —— 线性内核没有只受拉单元，索会被当成杆。
        - 有 cable_bridge（走 **非线性** cable_solver）时，CABLE 原样导出，
          并附带 *CABLE_BRIDGE / *CABLE 工况段：内核按只受拉 + 垂度处理。

        compact（默认 True）：节点走 *FILL、单元走 *DO 循环，见模块顶部
        "紧凑输出"一节。`compact=False` 逐行输出，用于排查——两种写法必须
        解析出同一个模型，这是 test_3d [18] 的对拍内容。
        """
        L: list[str] = []
        nonlinear = self.cable_bridge is not None
        L.append(f"# {self.title}")
        L.append("# 由 BridgeStudio 导出（FEM-Eigen 3D new format，单位 m/N/Pa）")
        if compact:
            L.append("# 紧凑写法：*FILL 补等间距节点、*DO 展开规则单元（内核可读回）")

        L.append("*NODE")
        if compact:
            L.extend(_compress_node_lines(self.nodes))
        else:
            for n in sorted(self.nodes, key=lambda n: n.id):
                L.append(f"{n.id} {fmt(n.x)} {fmt(n.y)} {fmt(n.z)}")

        L.append("*ET")
        cable_mapped = False
        for t in sorted(self.etypes, key=lambda t: t.id):
            out_kind = t.kind
            if t.kind == "CABLE" and not nonlinear:
                out_kind = "TRUSS"
                cable_mapped = True
            L.append(f"{t.id} {out_kind}")
        if cable_mapped:
            L.append("# 注：CABLE 在 3D 线性内核中映射为 TRUSS（只受拉/垂度需非线性内核）")

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
                        "IW", "ADDMASS", "ISTRN", "W", "L0"):
                v = getattr(r, key)
                if v:
                    kv.append(f"{key}={fmt(v)}")
            L.append(" ".join(kv))

        # 单元：按 (type, mat, real) 分组，每组只激活一次三元组。
        # 用分组而不是"状态机逐行切换"，是因为交错编号（如梁段 1001 与索
        # 9001 交替）会让状态机反复重写 *TYPE/*MAT/*REAL，既啰嗦又打断
        # 后面 *DO 的连续性——连续性是压缩的唯一前提。
        groups: dict[tuple[int, int, int], list[tuple[int, int, int, str]]] = {}
        for e in sorted(self.elements, key=lambda e: e.id):
            t = self._etype(e.etype)
            key = (t.id, t.mat, t.real)
            groups.setdefault(key, []).append((e.id, e.n1, e.n2, ""))

        for (tid, mat, real), items in groups.items():
            L.append(f"*TYPE,{tid}")
            L.append(f"*MAT,{mat}")
            L.append(f"*REAL,{real}")
            if compact:
                L.extend(_compress_element_lines(items))
            else:
                for eid, n1, n2, extra in items:
                    L.append(f"*EN,{eid},{n1},{n2}" + (f" {extra}" if extra else ""))

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

        for inf in self.influences:
            L.append("*INFLUENCE_3D")
            L.append(f"{inf.node} {inf.dof}")

        for lane in self.lanes:
            L.append("*LANE_3D")
            parts = ["NODES=" + ",".join(str(i) for i in lane.nodes)]
            if lane.code:
                parts.append(f"CODE={lane.code}")
            else:
                parts.append(f"Q={fmt(lane.q)}")
                parts.append(f"P={fmt(lane.p)}")
            parts.append(f"DOF={lane.dof}")
            if lane.impact != 1.0:
                parts.append(f"IMPACT={fmt(lane.impact)}")
            if lane.reduction != 1.0:
                parts.append(f"REDUCTION={fmt(lane.reduction)}")
            L.append(" ".join(parts))

        if self.cable_bridge is not None:
            cb = self.cable_bridge
            L.append("*CABLE_BRIDGE")
            L.append(f"MODE={cb.mode}")
            L.append(f"METHOD={cb.method}")
            L.append(f"NLSTEPS={cb.nl_steps}")
            L.append(f"NEWTON={cb.newton}")
            L.append(f"MAXITER={cb.max_iter}")
            L.append(f"TOL={fmt(cb.tol)}")
            L.append(f"TOLT={fmt(cb.tol_tension)}")
            L.append(f"MINT={fmt(cb.min_tension_fraction)}")
            if cb.deck_load > 0:
                L.append(f"DECKLOAD={fmt(cb.deck_load)}")
            L.append(f"VERBOSE={1 if cb.verbose else 0}")
            L.append("# <elemId> <anchorNode> <targetTension_N>  （索力 0 = 自动）")
            L.append("*CABLE")
            for c in cb.cables:
                L.append(f"{c.elem} {c.anchor} {fmt(c.tension)}")

        L.append("*END")
        return "\n".join(L) + "\n"
