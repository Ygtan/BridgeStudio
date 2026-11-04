"""arch_3d.py
============
大跨度钢箱拱桥参数化建造器（含施工阶段），用于 3D 线性阶段内核。

为什么单独一个建造器
--------------------
现有 ``bridge_spec.build_arch_bridge`` 是 2D 下承式拱桥（主梁+拱肋+吊杆），
没有施工阶段能力；而「拱肋分段吊装 + 斜拉扣索辅助 + 合拢 + 吊杆主梁 + 铺装」
这一整条施工链必须落在 3D 内核的 ``*STAGE`` 机制上（逐步激活单元 + 扣索
张拉 + 钝化临时扣索）。本建造器把工程参数翻译成节点-单元拓扑 + 施工序列。

桥型与模型假设
--------------
- **下承式单线平面模型**（y=0 平面）：拱肋、主梁、吊杆、扣索全部落在 xz
  平面内。真实结构的平面外刚度来自另一片拱肋与横撑，本模型按平面问题处理，
  拱肋节点横向自由度取约束（与悬索桥单索面模型同一处理）。
- **有推力拱（地锚式）**：拱脚固结（无铰拱），水平推力由拱座地基承担，
  主梁只是桥面系，通过吊杆挂在拱下，两端支座可纵向滑动（释放温度）。
- 拱轴线为二次抛物线 z=4f·x(L-x)/L²。
- **扣索体系（斜拉扣挂法简化）**：扣索从高处吊塔锚固点（塔顶节点，固定
  边界）斜向下拉到拱肋节段自由端，临时吊住悬臂拼装的拱肋。真实工艺的吊塔
  是刚架/缆索吊机，刚度远大于扣索，故塔顶简化为固定边界（DOF 1-3），不建
  塔柱——塔柱只是把锚点抬高的传力构件，去掉它不影响施工序列的力学本质。

施工顺序（Stage3D 序列）
------------------------
1. 拱脚段（左右各 1 段）就位（直接座在拱座）；
2. 从拱脚向跨中对称吊装拱肋节段，每段配一根扣索张拉（CABLE，目标张力 =
   该段自重 / 竖向方向余弦）；
3. 拱顶合拢（最后对称一对段在跨中会合，拱圈闭合、自稳）；
4. 拆除全部扣索（DEACTIVE）；
5. 从两端向跨中安装吊杆 + 主梁段（一期恒载）；
6. 主梁跨中合拢；
7. 二期铺装（成桥态）。

编号规范（X20 分块）
--------------------
- 节点：主梁 1000+i，拱肋 3000+j，塔顶锚点 4000（左）/ 5000（右）
- 单元：拱肋 100+j，主梁 200+i，吊杆 500+j，扣索 9000+t（左）/ 9100+t（右）
- 实常数：1=主梁，2=拱肋，3=吊杆(TRUSS)，4=扣索(CABLE)
- 材料：1=钢材

单位：m / N / Pa。
"""

from __future__ import annotations

import math

from model_spec_3d import (
    Boundary3D,
    Element3D,
    ElemTypeDef3D,
    Material3D,
    ModelSpec3D,
    Node3D,
    NodalLoad3D,
    RealConst3D,
    Stage3D,
)
from grillage_3d import box_section

G = 9.81


def _arch_z(x: float, span: float, rise: float) -> float:
    """拱轴线（二次抛物线）高程。"""
    return 4.0 * rise * x * (span - x) / (span * span)


def build_arch_3d(
    span: float = 500.0,            # 主跨 (m)
    rise: float = 100.0,            # 矢高 (m)，矢跨比 span/rise
    n_seg_half: int = 8,            # 半跨拱肋吊装段数（全跨 2·n_seg_half 段）
    tower_top: float | None = None,  # 吊塔锚点高程 (m)；None = 2×矢高（保证扣索倾角）
    # 拱肋钢箱截面（500m 大跨需足够大的钢箱：7m 高 × 5m 宽，50/45 板厚，
    #   使恒载组合应力控制在 Q345 屈服 70% 左右，见参数扫描）
    arch_h: float = 7.0, arch_b: float = 5.0,
    arch_tf: float = 0.05, arch_tw: float = 0.045,
    # 主梁钢箱截面
    deck_h: float = 3.2, deck_b: float = 24.0,
    deck_tf: float = 0.03, deck_tw: float = 0.022,
    hanger_area_mm2: float = 30000.0,  # 单根吊杆面积（大跨拱桥用刚性吊杆）
    cable_area_mm2: float = 8000.0,   # 单根临时扣索面积（满足施工期 A·fpu/2 上限）
    cable_fpu: float = 1860e6,      # 扣索破断强度 (Pa)
    q_deck: float = 200e3,          # 一期恒载 N/m（主梁自重+桥面系）
    q2_deck: float = 60e3,          # 二期恒载 N/m（铺装+护栏）
    E_steel: float = 206e9,
    dens_steel: float = 7850.0,
    staged: bool = True,            # 生成施工阶段序列（本建造器仅支持 staged）
    title: str = "500m 钢箱拱桥（拱肋分段吊装 + 斜拉扣索辅助）",
) -> tuple[ModelSpec3D, dict]:
    """生成下承式钢箱拱桥 ModelSpec3D + 施工阶段序列。

    返回 (模型, 派生量字典)。派生量只给界面/验证脚本用，不进 pydantic 契约。
    """
    if n_seg_half < 3:
        raise ValueError("半跨吊装段数 n_seg_half 至少 3（拱脚段 + 吊装段 + 合拢）")
    if not (2.0 <= span / rise <= 8.0):
        raise ValueError(f"矢跨比 span/rise = {span / rise:.2f} 超出合理范围 2~8")

    L = span
    f = rise
    N = int(n_seg_half)
    n_arch = 2 * N                       # 拱肋段数
    n_hanger = 2 * N - 1                 # 吊杆根数
    tower_top = 2.0 * rise if tower_top is None else float(tower_top)

    # ---------------------------------------------------------- 几何
    xs = [L * j / n_arch for j in range(n_arch + 1)]
    zs = [_arch_z(x, L, f) for x in xs]

    deck_nid = lambda i: 1000 + i               # 主梁节点 i=0..2N
    arch_nid = lambda j: 3000 + j               # 拱肋节点 j=0..2N
    topL = 4000                                  # 左塔顶锚点
    topR = 5000                                  # 右塔顶锚点

    nodes: list[Node3D] = []
    for i in range(n_arch + 1):
        nodes.append(Node3D(id=deck_nid(i), x=xs[i], y=0.0, z=0.0))
    for j in range(n_arch + 1):
        nodes.append(Node3D(id=arch_nid(j), x=xs[j], y=0.0, z=zs[j]))
    nodes.append(Node3D(id=topL, x=0.0, y=0.0, z=tower_top))
    nodes.append(Node3D(id=topR, x=L, y=0.0, z=tower_top))

    # ---------------------------------------------------------- 截面/材料
    G_shear = E_steel / (2 * (1 + 0.3))
    arch = box_section(arch_b, arch_h, arch_tf, arch_tw)
    deck = box_section(deck_b, deck_h, deck_tf, deck_tw)
    hanger_A = hanger_area_mm2 * 1e-6
    cable_A = cable_area_mm2 * 1e-6

    materials = [Material3D(id=1, E=E_steel, dens=dens_steel, grade="Q345qD")]
    reals = [
        RealConst3D(id=1, A=deck["A"], IY=deck["IY"], IZ=deck["IY"] * 0.5,
                    J=deck["J"], G=G_shear, WT=deck["WT"], WB=deck["WB"],
                    AW=deck["AW"]),
        RealConst3D(id=2, A=arch["A"], IY=arch["IY"], IZ=arch["IY"] * 0.5,
                    J=arch["J"], G=G_shear, WT=arch["WT"], WB=arch["WB"],
                    AW=arch["AW"]),
        RealConst3D(id=3, A=hanger_A),                              # 吊杆 TRUSS
        RealConst3D(id=4, A=cable_A),                               # 扣索 CABLE
    ]
    etypes = [
        ElemTypeDef3D(id=1, kind="BEAM", mat=1, real=1),   # 主梁
        ElemTypeDef3D(id=2, kind="BEAM", mat=1, real=2),   # 拱肋
        ElemTypeDef3D(id=3, kind="TRUSS", mat=1, real=3),  # 吊杆
        ElemTypeDef3D(id=4, kind="CABLE", mat=1, real=4),  # 扣索
    ]

    # ---------------------------------------------------------- 单元
    elements: list[Element3D] = []
    for j in range(1, n_arch + 1):
        elements.append(Element3D(id=100 + j, etype=2,
                                  n1=arch_nid(j - 1), n2=arch_nid(j)))
    for i in range(1, n_arch + 1):
        elements.append(Element3D(id=200 + i, etype=1,
                                  n1=deck_nid(i - 1), n2=deck_nid(i)))
    for j in range(1, n_hanger + 1):
        elements.append(Element3D(id=500 + j, etype=3,
                                  n1=arch_nid(j), n2=deck_nid(j)))
    # 扣索：左扣索 t=1..N-2 锚到左段 (1+t) 自由端（节点 2..N-1，朝跨中端），
    #       右扣索 t=1..N-2 锚到对称右段自由端（节点 2N-1-t，即 14..9，朝跨中端）。
    #       对称关系：左段 j 的对称段是 2N+1-j，其朝跨中端是节点 2N-j。
    #       拱顶合拢段（段 N、N+1）不设扣索：拱圈闭合后自稳。
    cable_left: list[int] = []
    cable_right: list[int] = []
    cable_anchor: dict[int, int] = {}   # 扣索 id → 拱肋节点 id
    for t in range(1, N - 1):
        cl = 9000 + t
        cr = 9100 + t
        elements.append(Element3D(id=cl, etype=4,
                                  n1=topL, n2=arch_nid(1 + t)))
        elements.append(Element3D(id=cr, etype=4,
                                  n1=topR, n2=arch_nid(2 * N - 1 - t)))
        cable_left.append(cl)
        cable_right.append(cr)
        cable_anchor[cl] = 1 + t
        cable_anchor[cr] = 2 * N - 1 - t
    cable_ids = cable_left + cable_right

    # ---------------------------------------------------------- 约束
    boundaries: list[Boundary3D] = []
    # 拱脚固结（无铰拱，拱座承受水平推力）
    boundaries.append(Boundary3D(node=arch_nid(0), dofs=(1, 6)))
    boundaries.append(Boundary3D(node=arch_nid(n_arch), dofs=(1, 6)))
    # 塔顶锚点固定（吊塔/缆索吊机刚度远大于扣索，简化为固定边界）
    boundaries.append(Boundary3D(node=topL, dofs=(1, 3)))
    boundaries.append(Boundary3D(node=topR, dofs=(1, 3)))
    # 主梁端点：横向+竖向；一端纵向固定
    boundaries.append(Boundary3D(node=deck_nid(0), dofs=(2, 3)))
    boundaries.append(Boundary3D(node=deck_nid(n_arch), dofs=(2, 3)))
    boundaries.append(Boundary3D(node=deck_nid(0), dofs=(1, 1)))
    # 平面外：主梁节点绕 x 扭转定位（单梁线，同斜拉桥/悬索桥）
    for i in range(n_arch + 1):
        boundaries.append(Boundary3D(node=deck_nid(i), dofs=(4, 4)))
    # 平面外：拱肋中间节点横向约束（单肋平面模型，见模块说明）
    for j in range(1, n_arch):
        boundaries.append(Boundary3D(node=arch_nid(j), dofs=(2, 2)))

    # ---------------------------------------------------------- 自重
    w_arch = arch["A"] * dens_steel * G        # 拱肋 N/m
    w_deck1 = q_deck                           # 主梁一期 N/m（含自重+桥面系）

    def arch_loads(seg_idxs: list[int]) -> list[NodalLoad3D]:
        out: list[NodalLoad3D] = []
        for j in seg_idxs:
            dx = xs[j] - xs[j - 1]
            dz = zs[j] - zs[j - 1]
            w = w_arch * math.hypot(dx, dz)
            out.append(NodalLoad3D(node=arch_nid(j - 1), dof=3, value=-w / 2))
            out.append(NodalLoad3D(node=arch_nid(j), dof=3, value=-w / 2))
        return out

    def deck_loads(seg_idxs: list[int]) -> list[NodalLoad3D]:
        out: list[NodalLoad3D] = []
        for i in seg_idxs:
            w = w_deck1 * (xs[i] - xs[i - 1])
            out.append(NodalLoad3D(node=deck_nid(i - 1), dof=3, value=-w / 2))
            out.append(NodalLoad3D(node=deck_nid(i), dof=3, value=-w / 2))
        return out

    # 扣索张拉目标：平衡该段拱肋自重（竖向方向余弦），截断到 0.7 倍破断力。
    def cable_target(cid: int) -> float:
        j = cable_anchor[cid]
        # 扣索平衡的段：左半跨段 j 的自由端是节点 j；右半跨段 j+1 的自由端是节点 j
        # （右半跨段编号仍从左到右递增，其朝跨中端是编号较小的节点 j）。
        s = j if cid in cable_left else j + 1
        dx = xs[s] - xs[s - 1]
        dz = zs[s] - zs[s - 1]
        seg_w = w_arch * math.hypot(dx, dz)            # 段 s 自重
        # 扣索从塔顶（高处）斜向下到节点 j，竖向投影 = tower_top - z_j
        h = tower_top - zs[j]
        if h <= 0.0:
            return 0.7 * cable_area_mm2 * 1e-6 * cable_fpu
        xa = xs[j] if cid in cable_left else (L - xs[j])   # 塔顶在 x=0 或 x=L
        L_cable = math.hypot(xa, h)
        T = seg_w * L_cable / h                          # 竖向分量 = 段自重
        return min(T, 0.7 * cable_area_mm2 * 1e-6 * cable_fpu)

    # ---------------------------------------------------------- 施工阶段
    stages: list[Stage3D] = []
    if not staged:
        all_struct = ([100 + j for j in range(1, n_arch + 1)] +
                      [200 + i for i in range(1, n_arch + 1)] +
                      [500 + j for j in range(1, n_hanger + 1)])
        loads = arch_loads(list(range(1, n_arch + 1))) + \
            deck_loads(list(range(1, n_arch + 1)))
        stages = [Stage3D(id=1, activate=all_struct, loads=loads)]
    else:
        # ---- 阶段 1：拱脚段（左段1 + 右段2N）----
        stages.append(Stage3D(
            id=1,
            activate=[100 + 1, 100 + n_arch],
            loads=arch_loads([1, n_arch]),
        ))
        # ---- 阶段 2..N-1：对称吊装 + 扣索张拉 ----
        for t in range(2, N):
            seg_left = t
            seg_right = n_arch + 1 - t
            cl = 9000 + (t - 1)
            cr = 9100 + (t - 1)
            stages.append(Stage3D(
                id=t,
                activate=[100 + seg_left, 100 + seg_right, cl, cr],
                loads=arch_loads([seg_left, seg_right]),
                tensions={cl: cable_target(cl), cr: cable_target(cr)},
            ))
        # ---- 阶段 N：拱顶合拢（段 N + 段 N+1 在跨中会合，拱圈闭合）----
        stages.append(Stage3D(
            id=N,
            activate=[100 + N, 100 + (N + 1)],
            loads=arch_loads([N, N + 1]),
        ))
        # ---- 阶段 N+1：拆除全部扣索 ----
        stages.append(Stage3D(id=N + 1, deactivate=cable_ids))
        # ---- 阶段 N+2 .. 2N+1：安装吊杆 + 主梁段（从两端向跨中）----
        for t in range(1, N + 1):
            s_deck = [200 + t, 200 + (n_arch + 1 - t)]
            s_hang = [500 + t]
            if t < N:
                s_hang.append(500 + (n_arch - t))
            s_hang = sorted(set(s_hang))
            stages.append(Stage3D(
                id=N + 1 + t,
                activate=s_deck + s_hang,
                loads=deck_loads([t, n_arch + 1 - t]),
            ))
        # ---- 阶段 2N+2：二期铺装（成桥态）----
        q2_loads: list[NodalLoad3D] = []
        if q2_deck > 0:
            for i in range(1, n_arch + 1):
                w = q2_deck * (xs[i] - xs[i - 1])
                q2_loads.append(NodalLoad3D(node=deck_nid(i - 1), dof=3, value=-w / 2))
                q2_loads.append(NodalLoad3D(node=deck_nid(i), dof=3, value=-w / 2))
        stages.append(Stage3D(id=2 * N + 2, loads=q2_loads))

    model = ModelSpec3D(
        title=title,
        nodes=nodes,
        materials=materials,
        reals=reals,
        etypes=etypes,
        elements=elements,
        boundaries=boundaries,
        loads=[],
        stages=stages,
        cable_bridge=None,
    )

    model_meta = {
        "span": L,
        "rise": f,
        "sag_ratio": L / f,
        "tower_top": tower_top,
        "n_arch_seg": n_arch,
        "n_hanger": n_hanger,
        "n_seg_half": N,
        "arch_node_ids": [arch_nid(j) for j in range(n_arch + 1)],
        "deck_node_ids": [deck_nid(i) for i in range(n_arch + 1)],
        "hanger_ids": [500 + j for j in range(1, n_hanger + 1)],
        "cable_ids": cable_ids,
        "cable_left": cable_left,
        "cable_right": cable_right,
        "cable_anchor": cable_anchor,
        "arch_elems": [100 + j for j in range(1, n_arch + 1)],
        "deck_elems": [200 + i for i in range(1, n_arch + 1)],
        "w_arch": w_arch,
        "q_deck": q_deck,
        "q2_deck": q2_deck,
        "n_stages": len(stages),
        "staged": staged,
        "cable_targets": {cid: cable_target(cid) for cid in cable_ids},
    }
    return model, model_meta
