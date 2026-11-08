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
- CABLE：斜拉索/吊杆。线性内核里刚度按轴向杆装配（不作只受拉判断），
  类型原样导出——施工阶段调索（*STAGE 的 TENSION=）要求内核认得它是索。
  垂度与只受拉的几何非线性走 cable_solver。

依赖：pydantic。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

ElemKind3D = Literal["BEAM", "BEAM7", "TRUSS", "CABLE"]


def fmt(v: float) -> str:
    """数值格式化：10 位有效数字，与 fem_eigen_export 风格一致。"""
    return f"{v:.10g}"


def _int_list_ranges(vals: list[int]) -> str:
    """整数列表压缩成 `a-b,c,d-e`：连续段用 a-b，孤立值原样。

    预应力束的 ELEM 列表通常是连续的梁段（1,2,...,10），写成 1-10 比十个
    逗号分隔可读得多，也和 *STAGE 其他键的"编号规律可见"原则一致。
    """
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
    #: 材料牌号（"Q345qD" / "C50"），只在强度验算时用。
    #: 空串 = 未标注 → 应力验算跳过并说明原因，绝不猜一个等级顶上。
    #: 不写进 *MAT（内核只认 E/DENS），所以留空不影响求解。
    grade: str = ""


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
    HTH: float = 0.0                # 理论厚度 mm。0 表示没有。模型上的统一厚度优先。
    #: 腹板总面积 m²（所有腹板之和）。剪应力 τ=V/AW 用。0 = 未提供，跳过剪应力验算。
    AW: float = 0.0
    #: 控制板厚 mm（腹板/翼缘中较厚者）。查 JTG D64 表 3.2.1 的厚度档用。
    #: 0 = 未提供，按 0 处理（落在 ≤16mm 档，偏不保守，故建造器都应填）。
    T_PLATE: float = 0.0
    #: 混凝土梁截面的配筋（承载力验算用）。None = 无配筋信息（钢截面/索/
    #: 桁架杆不填），承载力验算跳过并说明。不写进 .dat、不参与力学求解。
    rebar: "RebarSpec3D | None" = None


class RebarSpec3D(BaseModel):
    """混凝土梁截面配筋（JTG 3362-2018 承载力验算用，验算侧信息）。

    尺寸单位 mm、面积 mm²、牌号照 `code_material`。**不写进 .dat、不参与
    求解**——它只给 `section_strength.check_capacity` 提供截面几何与配筋。

    - 矩形截面：``bf == b``（``bf`` 留 0 即退化为矩形）且 ``hf == 0``。
    - 工形 / T 形截面：``bf > b``（受压翼缘宽，箱梁顶板受压取箱宽）且
      ``hf > 0``（翼缘厚）。
    - 斜截面抗剪的 ``b`` 始终取**腹板宽度**（矩形=全宽；箱梁=各腹板宽之和）。
    - ``Asp_given=False``（默认）表示未显式给受压钢筋、按对称配筋 A's=As；
      置 True 后 ``Asp`` 按字面值取（可为 0，即单筋非对称配筋）。
    - ``negative_given=True`` 时启用**负弯矩面**（受压在底板、受拉在顶板）：
      底板宽/厚、顶板纵筋可与正弯矩面不同，``*_neg`` 为 None 表示沿用对应
      的正弯矩面值。不启用时负弯矩按正弯矩面对称近似，验算会给提示。
    """

    h: float = Field(gt=0)          # 截面全高 mm
    b: float = Field(gt=0)          # 腹板宽度 mm
    As: float = Field(gt=0)         # 受拉钢筋面积 mm²
    bf: float = 0.0                 # 受压翼缘宽度 mm（0=取 b，即矩形）
    hf: float = 0.0                 # 受压翼缘厚度 mm（0=矩形）
    as_: float = 50.0               # 受拉钢筋合力点至受拉边缘距离 mm
    asp: float = 40.0               # 受压钢筋合力点至受压边缘距离 mm
    Asp: float = 0.0                # 受压钢筋面积 mm²（Asp_given=True 时按字面值）
    Asp_given: bool = False         # False=未提供受压筋、按对称配筋；True=非对称/单筋
    Asv: float = 0.0                # 同一截面内箍筋各肢总面积 mm²（0=未配箍）
    sv: float = 150.0               # 箍筋间距 mm
    grade_rebar: str = "HRB400"     # 纵向钢筋牌号
    grade_stirrup: str = "HRB400"   # 箍筋牌号
    l0: float = 0.0                 # 构件计算长度 mm（表5.3.1稳定/§5.3.9二阶效应用；0=未提供）
    hollow: bool = False            # True=空心箱形截面（外 bf×h、腹板 2×(b/2)、翼缘 hf）
    negative_given: bool = False    # True=登记了负弯矩面（底板受压）
    bf_neg: float | None = None     # 底板（受压翼缘）宽 mm；None=沿用 bf
    hf_neg: float | None = None     # 底板厚 mm；None=沿用 hf
    As_neg: float | None = None     # 顶板受拉纵筋 mm²；None=沿用 As
    Asp_neg: float | None = None    # 底板受压纵筋 mm²；None=沿用 Asp
    as_neg: float | None = None     # 顶板筋合力点至顶缘 mm；None=沿用 as_
    asp_neg: float | None = None    # 底板筋合力点至底缘 mm；None=沿用 asp


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


class Prestress3D(BaseModel):
    """一条预应力筋（施工阶段 *STAGE 的 PRESTRESS=，等效荷载法）。

    - ``elems``：连续梁单元 id（首尾即锚固端，必须首尾相接成一条开链）
    - ``P``：张拉控制力 N（正）；``e``：**端部**偏心 m（形心上方为正）
    - ``s``：矢高 = 跨中偏心 − 端部偏心（m，向上为正）。``s=0`` 是直线筋；
      ``s<0`` 跨中筋更低 → 跨内向上均布力 → **上拱**。
    - ``loss``：有效/张拉比（0 < loss ≤ 1）

    直线筋（s=0）的等效荷载只有「两端一对轴压 + 两端一对偏心弯矩」，**不产生
    上拱**；上拱来自曲线筋（s≠0）的跨内反向均布力（q = −8Pe·s/L²）+ 两端锚固
    切线竖向力（4Pe·s/L）。曲线筋要求单元链**共线**（抛物线定义在首尾弦上），
    且上拱精度依赖跨内单元细分——单元越细越接近抛物线的二次弯矩。
    """
    elems: list[int] = Field(min_length=1)
    P: float = Field(gt=0)
    e: float = 0.0
    s: float = 0.0
    loss: float = Field(default=1.0, gt=0.0, le=1.0)
    # 束面积 m²。0 表示没给：校核不得用缺省面积去判 σpe ≤ 0.75 fpk。
    Ap: float = 0.0
    # 抗拉强度标准值 Pa。0 表示校核时用 1860 MPa。
    fpk: float = 0.0


class Stage3D(BaseModel):
    """施工阶段（*STAGE + *STAGE_LOAD + *STAGE 行内的 TENSION= / L0= / PRESTRESS=）。

    注意：FEM-Eigen 3D 内核的 solveModel3DStages 只使用阶段荷载
    （m.loads 被忽略），因此所有荷载必须挂在某个阶段下。

    张拉两个键的区别（写错会得到完全不同的结果，见 input_format_3d.md）：
    - `tensions`：给**目标索力**，内核迭代无应力索长去命中它。这是"调索"，
      也是唯一能表达"这一阶段把索拉到 3200 kN"的写法。
    - `unstrained`：直接给**下料长度**，不迭代。用于"我知道这根索下多长"。
    同一根索同时出现在两处会被内核判为语义冲突并报错。

    预应力束（`prestress`）独立于索张拉：等效荷载是状态量（张拉一次、
    永久存在），阶段内只施加一次，效应通过累计位移延续。直线筋（s=0）与
    曲线筋（s≠0）都走这个字段，区别只在矢高。
    """

    id: int
    activate: list[int] = Field(default_factory=list)    # 激活单元 id
    deactivate: list[int] = Field(default_factory=list)  # 钝化单元 id
    loads: list[NodalLoad3D] = Field(default_factory=list)
    tensions: dict[int, float] = Field(default_factory=dict)    # 单元号 → 目标索力 N
    unstrained: dict[int, float] = Field(default_factory=dict)  # 单元号 → 无应力索长 m
    prestress: list[Prestress3D] = Field(default_factory=list)  # 本阶段张拉的预应力束
    # 相对已积累内力的徐变增量。静定梁最终位移约为瞬时值的 (1+Δφ) 倍。
    creep_dphi: float = 0.0
    # 阶段末龄期（天）。>0 且模型写了徐变曲线时，内核用 φ(t)−φ(上一阶段) 代替 creep_dphi。
    time: float = 0.0


class Influence3D(BaseModel):
    """影响线自由度（*INFLUENCE_3D <node> <dof>）。"""
    node: int
    dof: int = Field(ge=1, le=6)


class Lane3D(BaseModel):
    """车道荷载（*LANE_3D）。

    CODE="JTG_D60" 时由内核按车道首末节点距离 L 自动取
    qk=10.5 kN/m、Pk(L)（L≤5m:270kN，L≥50m:360kN，线性内插）。

    横向布载（横向分布系数法）：单梁模型横向只有一个节点，算不出严格
    横向影响面，规范上用「横向分布系数」把多车道并排布载折进纵向影响线
    包络：m = lane_count · reduction · eccentricity。三个量缺省（1, 1.0, 1.0）
    时退化为旧的单车道近似。

    汽车冲击（JTG D60）：`impact` 是**放大倍数**（1+μ），不是纯冲击系数 μ。
    索承体系下若 `impact` 缺省（1.0），内核按成桥态切线刚度求竖向一阶弯曲
    基频 f1 自动填 1+μ；显式写非 1.0 则尊重手动值。
    """
    nodes: list[int] = Field(min_length=2)   # 车道纵列节点（顺序即行车方向）
    q: float = 0.0                           # N/m（CODE 模式下可缺省）
    p: float = 0.0                           # N
    dof: int = 3                             # 加载方向自由度（3=竖向）
    impact: float = 1.0                      # 冲击放大倍数（1+μ；缺省 1=不计冲击）
    reduction: float = 1.0                   # 多车道横向折减系数（JTG D60 表 4.3.1-5）
    lane_count: int = 1                      # 横向设计车道数
    eccentricity: float = 1.0                # 偏载增大系数（横向偏载放大）
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

    悬索桥额外用 ``system`` / ``sag`` / ``tower1`` / ``tower2`` /
    ``main_cable`` / ``hangers`` 描述主缆体系（内核 *SUSP_BRIDGE 段）。
    ``system`` 非空即走悬索桥找形：主缆线形由恒载决定，不能由几何给定。
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

    # ---- 悬索桥（*SUSP_BRIDGE / *MAINCABLE / *HANGER）----
    system: Literal["", "EARTH", "SELF"] = ""   # 地锚式 / 自锚式；空 = 斜拉桥
    sag: float = 0.0                # 主跨矢高 m（>0 时优先于矢跨比）
    tower1: int = 0                 # 塔顶节点（必须在主缆链上）
    tower2: int = 0
    main_cable: list[int] = Field(default_factory=list)   # 主缆单元，按链序
    hangers: list[int] = Field(default_factory=list)      # 吊索单元
    deck_e0: float = 0.0            # 加劲梁单元号区间（输出梁轴力用）
    deck_e1: float = 1e12

    # ---- 悬索桥施工阶段（内核 *ERECTION 段）----
    # 给定成桥主缆下料长度 L0 的逐阶段找形：空缆 → 从跨中对称吊装一期恒载
    # （钢箱梁自重 Q1）→ 合拢 → 二期铺装 Q2（成桥态）。矢高是结果不是输入。
    erection: bool = False          # 是否生成施工阶段模拟方案
    erection_q1: float = 0.0        # 一期恒载 N/m（钢箱梁自重，吊装阶段施加）
    erection_q2: float = 0.0        # 二期恒载 N/m（铺装+护栏，合拢后施加）
    erection_panels: int = 1        # 每次对称吊装的吊点对数（每次对称吊 2 段）


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
    # 全模型一个徐变系数。刚度用 E/(1+χφ)。0 表示不折减。
    # 这不是按龄期分阶段的 AEMM。
    creep_phi: float = 0.0
    creep_chi: float = 0.8
    # 徐变曲线 φ=φ0·[(t−t0)/(βH+t−t0)]^power。φ0=0 表示不按龄期。
    # 阶段 TIME 给出后，增量是相邻龄期的 φ 差，后加的荷载不从第一天起徐变。
    creep_phi0: float = 0.0
    creep_t0: float = 7.0
    creep_beta_h: float = 700.0
    creep_power: float = 0.3
    # 三者都大于 0 时，名义徐变系数和 βH 由内核按 JTG 3362 附录 C.2.1 计算。
    # 不写 PHI0。相对湿度为百分数，理论厚度为 mm，fcm 为圆柱体平均强度 MPa。
    creep_rh: float = 0.0
    creep_h: float = 0.0
    creep_fcm: float = 0.0
    # 干燥开始龄期（天）。0 表示与加载龄期相同。只在规范收缩里使用。
    shrinkage_ts: float = 0.0
    # 自由收缩应变，正值为缩短。0 表示不计。全模型一个值，不按龄期。
    shrinkage_ecs: float = 0.0

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
            cb = self.cable_bridge
            if cb.system:
                # 悬索桥的索力由内核找形推出（荷载 + 矢高定 H），不需要在
                # 模型里预先给目标索力；此时 cables 允许为空，改为校验主缆链
                # 与吊索的引用是否成立。
                if not cb.main_cable:
                    raise ValueError("悬索桥工况未指定主缆单元（main_cable 为空）")
                if not cb.hangers:
                    raise ValueError("悬索桥工况未指定吊索单元（hangers 为空）")
                if cb.sag <= 0:
                    raise ValueError("悬索桥工况必须给定主跨矢高 sag")
                for eid in list(cb.main_cable) + list(cb.hangers):
                    if eid not in elem_by_id:
                        raise ValueError(f"悬索桥工况引用了不存在的单元 {eid}")
                    if kind_of_et[elem_by_id[eid].etype] != "CABLE":
                        raise ValueError(f"悬索桥工况的单元 {eid} 不是 CABLE 类型")
                for nid in (cb.tower1, cb.tower2):
                    if nid not in node_ids:
                        raise ValueError(f"悬索桥工况引用了不存在的塔顶节点 {nid}")
            else:
                if not cb.cables:
                    raise ValueError("成桥态工况未指定任何索（cable_bridge.cables 为空）")
            for c in cb.cables:
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

        CABLE 的类型导出（重要）：
        - 有 cable_bridge（走 **非线性** cable_solver）：CABLE 原样导出，
          并附带 *CABLE_BRIDGE / *CABLE 工况段：内核按只受拉 + 垂度处理。
        - 无 cable_bridge（走 3D **线性** 内核 example_3d_grillage）：**仍导出
          CABLE**。早期这里映射成 TRUSS，理由是"线性内核不区分二者"——但施工
          阶段调索（*STAGE 的 TENSION=）要求内核认得这根是索，映射成 TRUSS
          会让调索直接报错。线性装配只按 BEAM/non-BEAM 分支，两种写法的刚度
          完全相同，所以改成原样导出不影响任何一个既有算例的数值。

        compact（默认 True）：节点走 *FILL、单元走 *DO 循环，见模块顶部
        "紧凑输出"一节。`compact=False` 逐行输出，用于排查——两种写法必须
        解析出同一个模型，这是 test_3d [18] 的对拍内容。
        """
        L: list[str] = []
        nonlinear = self.cable_bridge is not None
        L.append(f"# {self.title}")
        L.append("# 由 BridgeStudio 导出（FEM-Eigen 3D new format，单位 m/N/Pa）")
        has_member_h = any(r.HTH > 0.0 for r in self.reals)
        code_creep = (self.creep_rh > 0.0 and self.creep_fcm > 0.0
                      and (self.creep_h > 0.0 or has_member_h))
        if self.creep_phi or self.creep_phi0 or code_creep:
            bits = ["*CREEP"]
            if self.creep_phi:
                bits.append(f"PHI={fmt(self.creep_phi)}")
            if code_creep:
                bits.append(f"RH={fmt(self.creep_rh)}")
                if self.creep_h > 0.0:
                    bits.append(f"H={fmt(self.creep_h)}")
                bits.append(f"FCM={fmt(self.creep_fcm)}")
                bits.append(f"T0={fmt(self.creep_t0)}")
                if self.shrinkage_ts > 0.0:
                    bits.append(f"TS={fmt(self.shrinkage_ts)}")
            elif self.creep_phi0:
                bits.append(f"PHI0={fmt(self.creep_phi0)}")
                bits.append(f"T0={fmt(self.creep_t0)}")
                bits.append(f"BETAH={fmt(self.creep_beta_h)}")
                bits.append(f"POWER={fmt(self.creep_power)}")
            bits.append(f"CHI={fmt(self.creep_chi)}")
            L.append(" ".join(bits))
        if self.shrinkage_ecs:
            L.append(f"*SHRINKAGE ECS={fmt(self.shrinkage_ecs)}")
        if compact:
            L.append("# 紧凑写法：*FILL 补等间距节点、*DO 展开规则单元（内核可读回）")

        L.append("*NODE")
        if compact:
            L.extend(_compress_node_lines(self.nodes))
        else:
            for n in sorted(self.nodes, key=lambda n: n.id):
                L.append(f"{n.id} {fmt(n.x)} {fmt(n.y)} {fmt(n.z)}")

        L.append("*ET")
        for t in sorted(self.etypes, key=lambda t: t.id):
            L.append(f"{t.id} {t.kind}")
        if any(t.kind == "CABLE" for t in self.etypes):
            L.append("# 注：线性内核里 CABLE 的刚度按轴向杆装配（不作只受拉判断），"
                     "垂度与只受拉须走非线性 cable_solver")

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
                        "IW", "ADDMASS", "ISTRN", "W", "L0", "HTH"):
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
            if s.creep_dphi:
                parts.append(f"DPHI={fmt(s.creep_dphi)}")
            if s.time:
                parts.append(f"TIME={fmt(s.time)}")
            # 张拉规格按单元号排序输出：dict 的插入顺序取决于调用方怎么建的，
            # 同一份模型两次导出得到不同文本会让 .dat 对拍失去意义。
            if s.tensions:
                parts.append("TENSION=" + ",".join(
                    f"{k}:{fmt(s.tensions[k])}" for k in sorted(s.tensions)))
            if s.unstrained:
                parts.append("L0=" + ",".join(
                    f"{k}:{fmt(s.unstrained[k])}" for k in sorted(s.unstrained)))
            for ps in s.prestress:
                if ps.s == 0.0:
                    # 直线筋：位置模式（向后兼容，旧 .dat 与测试都认这个）
                    body = (_int_list_ranges(sorted(ps.elems))
                            + f":{fmt(ps.P)}:{fmt(ps.e)}"
                            + (f":{fmt(ps.loss)}" if ps.loss != 1.0 else ""))
                else:
                    # 曲线筋：关键字模式（矢高 S 必须命名，否则与 loss 位置冲突）
                    body = (_int_list_ranges(sorted(ps.elems))
                            + f":P={fmt(ps.P)}:E={fmt(ps.e)}:S={fmt(ps.s)}"
                            + (f":LOSS={fmt(ps.loss)}" if ps.loss != 1.0 else ""))
                parts.append("PRESTRESS=" + body)
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
            if lane.lane_count != 1:
                parts.append(f"LANE_COUNT={lane.lane_count}")
            if lane.eccentricity != 1.0:
                parts.append(f"ECCENTRICITY={fmt(lane.eccentricity)}")
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
            if cb.system:
                # 悬索桥：主缆线形由找形确定，必须告诉内核哪条链是主缆、
                # 哪些是吊索、塔顶在哪里——它无法从几何自行分辨
                # （斜拉索与吊索都是 CABLE 单元，形状也都能斜能竖）。
                L.append("# 悬索桥：主缆找形的定位信息（地锚式/自锚式仅边界不同）")
                L.append("*SUSP_BRIDGE")
                L.append(f"SYSTEM={cb.system}")
                L.append(f"TOWER1={cb.tower1}")
                L.append(f"TOWER2={cb.tower2}")
                if cb.sag > 0:
                    L.append(f"SAG={fmt(cb.sag)}")
                L.append(f"DECKE0={fmt(cb.deck_e0)}")
                L.append(f"DECKE1={fmt(cb.deck_e1)}")
                L.append("# 主缆单元，按链序排列（锚 → 塔1 → 塔2 → 锚）")
                L.append("*MAINCABLE")
                for eid in cb.main_cable:
                    L.append(str(eid))
                L.append("# 吊索单元")
                L.append("*HANGER")
                for eid in cb.hangers:
                    L.append(str(eid))
                if cb.erection:
                    L.append("# 施工阶段：给定主缆下料长度 L0 的逐阶段找形")
                    L.append("# 空缆 → 从跨中对称吊装一期恒载 Q1 → 合拢 → 二期铺装 Q2（成桥态）")
                    L.append("*ERECTION")
                    L.append(f"Q1={fmt(cb.erection_q1)}")
                    L.append(f"Q2={fmt(cb.erection_q2)}")
                    L.append(f"PANELS={cb.erection_panels}")
            L.append("# <elemId> <anchorNode> <targetTension_N>  （索力 0 = 自动）")
            L.append("*CABLE")
            for c in cb.cables:
                L.append(f"{c.elem} {c.anchor} {fmt(c.tension)}")

        L.append("*END")
        return "\n".join(L) + "\n"


# ---------------------------------------------------------------- 模型校验
def validate_model_3d(model: ModelSpec3D) -> tuple[list[str], list[str]]:
    """检查模型的引用完整性与可解性，返回 ``(errors, warnings)``。

    为什么 pydantic 不够：``ModelSpec3D`` 只能保证「字段类型对、A>0」，
    它管不了**引用**——单元指向不存在的节点、etype 指向不存在的实常数、
    索两端等高。这些错误在 pydantic 眼里完全合法，却会在内核里变成
    「节点缺失」「局部坐标退化」这类难以回溯的崩溃，或者更糟：静默算错。

    约定与手册 §7 一致：**返回中文列表，不抛异常**。界面上直接把 errors
    显示出来即可，不需要 try/except 包一层——校验本身就是给用户看的。

    检查项分两级：
      errors   不可求解（引用断了、几何退化、无约束）
      warnings 可求解但很可能是笔误（孤立节点、重复单元、索等高差、W=0）

    后面三项 warnings 是本项目里真实踩过的坑，不是教科书清单：
      · 孤立节点会被内核的「悬空自由度」处理逻辑硬约束住，塔顶变刚体；
      · 索两端等高会让竖向分量 ez 退化为 0，目标索力反算直接除零；
      · 索 W=0 时 Ernst 模量退化成无垂度，成桥态索力偏高而没有任何报错。
    """
    errors: list[str] = []
    warnings: list[str] = []

    # ---------------------------------------------------------- 编号唯一性
    def duplicates(kind: str, ids: list[int]) -> None:
        seen: set[int] = set()
        dup: set[int] = set()
        for i in ids:
            if i in seen:
                dup.add(i)
            seen.add(i)
        if dup:
            errors.append(f"{kind}编号重复：{sorted(dup)}")

    duplicates("节点", [n.id for n in model.nodes])
    duplicates("材料", [m.id for m in model.materials])
    duplicates("实常数", [r.id for r in model.reals])
    duplicates("单元类型", [t.id for t in model.etypes])
    duplicates("单元", [e.id for e in model.elements])

    if not model.nodes:
        errors.append("模型没有节点")
    if not model.elements:
        errors.append("模型没有单元")

    node_ids = {n.id for n in model.nodes}
    mat_ids = {m.id for m in model.materials}
    real_ids = {r.id for r in model.reals}
    et_ids = {t.id for t in model.etypes}
    et_by_id = {t.id: t for t in model.etypes}
    real_by_id = {r.id: r for r in model.reals}
    node_xyz = {n.id: n for n in model.nodes}

    # ---------------------------------------------------------- 引用完整性
    for t in model.etypes:
        if t.mat not in mat_ids:
            errors.append(f"单元类型 {t.id} 引用了不存在的材料 {t.mat}")
        if t.real not in real_ids:
            errors.append(f"单元类型 {t.id} 引用了不存在的实常数 {t.real}")

    zero_len: list[int] = []
    for e in model.elements:
        if e.etype not in et_ids:
            errors.append(f"单元 {e.id} 引用了不存在的单元类型 {e.etype}")
        missing = [n for n in (e.n1, e.n2) if n not in node_ids]
        if missing:
            errors.append(f"单元 {e.id} 引用了不存在的节点 {missing}")
            continue
        if e.n1 == e.n2:
            errors.append(f"单元 {e.id} 的起止节点相同（{e.n1}）")
            continue
        a, b = node_xyz[e.n1], node_xyz[e.n2]
        L = ((b.x - a.x) ** 2 + (b.y - a.y) ** 2 + (b.z - a.z) ** 2) ** 0.5
        if L <= 1e-9:
            zero_len.append(e.id)

    if zero_len:
        errors.append(
            f"单元长度为 0（两节点坐标重合，局部坐标退化）：{sorted(zero_len)[:8]}"
            + ("…" if len(zero_len) > 8 else ""))

    # ---------------------------------------------------------- 边界与荷载
    for b in model.boundaries:
        if b.node not in node_ids:
            errors.append(f"边界条件引用了不存在的节点 {b.node}")
        lo, hi = b.dofs
        if not (1 <= lo <= hi <= 6):
            errors.append(f"节点 {b.node} 的自由度区间 {lo}-{hi} 越界（应为 1~6）")
        if b.k < 0:
            errors.append(f"节点 {b.node} 的弹性支承刚度不能为负（k={b.k:g}）")

    for ld in model.loads:
        if ld.node not in node_ids:
            errors.append(f"节点荷载引用了不存在的节点 {ld.node}")
        if not (1 <= ld.dof <= 6):
            errors.append(f"节点 {ld.node} 的荷载自由度 {ld.dof} 越界（应为 1~6）")

    for s in model.stages:
        for eid in list(s.activate) + list(s.deactivate):
            if eid not in {e.id for e in model.elements}:
                errors.append(f"阶段 {s.id} 激活/去激活了不存在的单元 {eid}")

    # ---------------------------------------------------------- 索专用检查
    # 索的两端若等高，竖向分量 ez = Δz/L = 0，目标索力 T = q·t/ez 会除零。
    # 这个退化在 pydantic 看来完全正常，进内核才变成 inf/NaN。
    cable_elems: dict[int, "Element3D"] = {}
    for e in model.elements:
        t = et_by_id.get(e.etype)
        if t is not None and t.kind == "CABLE":
            cable_elems[e.id] = e

    for eid, e in cable_elems.items():
        if e.n1 not in node_ids or e.n2 not in node_ids:
            continue
        a, b = node_xyz[e.n1], node_xyz[e.n2]
        if abs(b.z - a.z) <= 1e-9:
            errors.append(f"索单元 {eid} 两端等高（z 相同），竖向分量退化")
        rc = real_by_id.get(et_by_id[e.etype].real)
        if rc is not None:
            if rc.W <= 0:
                warnings.append(
                    f"索单元 {eid} 所在实常数 W=0：垂度与 Ernst 模量退化为无垂度，"
                    f"成桥态索力会偏高（且不会有任何报错）")
            if rc.L0 > 0:
                L = ((b.x - a.x) ** 2 + (b.y - a.y) ** 2 + (b.z - a.z) ** 2) ** 0.5
                if abs(rc.L0 - L) / L > 0.05:
                    warnings.append(
                        f"索单元 {eid} 的无应力索长 L0={rc.L0:.4f} m 与几何长度 "
                        f"{L:.4f} m 相差 {abs(rc.L0 - L) / L * 100:.1f}%")

    if model.cable_bridge is not None:
        for c in model.cable_bridge.cables:
            if c.elem not in cable_elems:
                errors.append(f"成桥态工况引用的单元 {c.elem} 不是索（或不存在）")

    # ---------------------------------------------------------- 结构可解性
    # 完全没有约束 → 6 个刚体位移，K 奇异。这是最常见也最难看出的一类错。
    #
    # 这里只判"一条 boundary 都没有"。别再往里套 `any(b.k > 0 ...)`——
    # 进了这个分支，boundaries 一定是空的，那个判断恒为 False，纯属误导
    # （曾经真这么写过）。弹簧支承（k>0）与刚性约束在这个判断上等价：
    # 有一条就够，刚度不为零即可解。
    if not model.boundaries:
        errors.append("模型没有任何边界约束：刚度矩阵奇异（6 个刚体位移）")

    # ---------------------------------------------------------- 警告级
    used_nodes: set[int] = set()
    for e in model.elements:
        used_nodes.add(e.n1)
        used_nodes.add(e.n2)
    lonely = sorted(node_ids - used_nodes)
    if lonely:
        warnings.append(
            f"{len(lonely)} 个节点未被任何单元引用：{lonely[:8]}"
            + ("…" if len(lonely) > 8 else "")
            + "。内核会把它们的自由度硬约束住（塔顶因此变刚体）")

    seen_pairs: dict[tuple[int, int, int], int] = {}
    for e in model.elements:
        key = (min(e.n1, e.n2), max(e.n1, e.n2), e.etype)
        if key in seen_pairs:
            warnings.append(
                f"单元 {e.id} 与 {seen_pairs[key]} 连接同一对节点且类型相同（重复单元）")
        else:
            seen_pairs[key] = e.id

    seen_dof: dict[tuple[int, int], int] = {}
    for i, b in enumerate(model.boundaries):
        for d in range(b.dofs[0], b.dofs[1] + 1):
            key = (b.node, d)
            if key in seen_dof:
                warnings.append(
                    f"节点 {b.node} 的自由度 {d} 被重复约束（多条约束取其一，易生歧义）")
            seen_dof[key] = i

    for r in model.reals:
        if r.G > 0 and any(t.real == r.id and t.mat in mat_ids for t in model.etypes):
            mats = [m for m in model.materials
                    if any(t.real == r.id and t.mat == m.id for t in model.etypes)]
            for m in mats:
                nu = m.E / (2.0 * r.G) - 1.0
                if not (0.0 < nu < 0.5):
                    warnings.append(
                        f"实常数 {r.id} 的 G={r.G:g} 与材料 {m.id} 的 E={m.E:g} "
                        f"不自洽（反算泊松比 {nu:.3f}，超出 0~0.5）")

    return errors, warnings
