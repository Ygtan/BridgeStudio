"""beam_arch_3d.py
==================
梁拱 / 拱梁组合建造器（线性共同受力），X20 第 5 类桥型「组合体系」。
单位：m / N / Pa。

范围与边界
----------
- **线性共同受力**：拱肋与主梁都建 BEAM 单元、吊杆建 TRUSS 单元，一起组装
  线性刚度矩阵；不做成桥态找形、不设目标索力、不加内核键。斜拉-悬索的
  ``[COOPERATIVE]`` 四类残差全是 SUSP+CABLE 专用，不可套用到拱肋。
- **两种体系型式**（``arch_type``）：
  - ``TIED``（系杆拱 / 自锚）：拱脚节点与主梁端节点**合并为同一节点**，基础
    只约束竖向 / 横向，水平推力沿主梁（系梁）自锚传递；系梁轴力 ≈ q·L²/(8f)。
  - ``EXTERNAL``（拱梁组合 / 外部推力）：拱脚为独立节点并固结（无铰拱），
    水平推力由桥墩 / 基础承担；主梁不作为推力路径。

编号规范（B1，X20_接入约定.md §5）
----------------------------------
紧凑分块编号（§5.1）：节点 / 单元各自独立按组件组顺序分配，第一组从 1 起；
≥50 的组按百位跳转，<50 的组按十位跳转。
- 节点组顺序：主梁（n+1）、拱肋（TIED 时中间 n-1 个，端点共用主梁端）
- 单元组顺序：拱肋、主梁、吊杆
- 实常数：1=主梁，2=拱肋，3=吊杆(TRUSS)
- 材料：1=钢材

口径与验收见《组合体系接入规格.md》§4.7。
"""

from __future__ import annotations

import math

from model_spec_3d import (
    Boundary3D,
    Element3D,
    ElemTypeDef3D,
    Influence3D,
    Lane3D,
    Material3D,
    ModelSpec3D,
    Node3D,
    NodalLoad3D,
    RealConst3D,
    Stage3D,
)
from grillage_3d import box_section
from arch_3d import _arch_z
from block_numbering import BlockNumbering

G = 9.81

TIED_ARCH = "TIED"          # 系杆拱（自锚）
EXTERNAL_ARCH = "EXTERNAL"  # 拱梁组合（外部推力）


def build_beam_arch_3d(
    span: float = 500.0,
    rise: float = 100.0,
    n_seg_half: int = 8,
    arch_type: str = TIED_ARCH,
    arch_h: float = 7.0, arch_b: float = 5.0,
    arch_tf: float = 0.05, arch_tw: float = 0.045,
    deck_h: float = 3.2, deck_b: float = 24.0,
    deck_tf: float = 0.03, deck_tw: float = 0.022,
    hanger_area_mm2: float = 30000.0,
    q_deck: float = 200e3,
    q2_deck: float = 60e3,
    E_steel: float = 206e9,
    dens_steel: float = 7850.0,
    lane_code: str = "",              # "JTG_D60"=车道荷载+跨中竖向影响线（opt-in）
    title: str = "",
) -> tuple[ModelSpec3D, dict]:
    """生成梁拱 / 拱梁组合的线性共同受力模型与派生量（不进 pydantic 契约）。"""
    if n_seg_half < 3:
        raise ValueError("半跨段数 n_seg_half 至少 3")
    if not (2.0 <= span / rise <= 8.0):
        raise ValueError(f"矢跨比 span/rise = {span / rise:.2f} 超出 2~8")
    if arch_type not in (TIED_ARCH, EXTERNAL_ARCH):
        raise ValueError(f"arch_type 必须为 {TIED_ARCH} / {EXTERNAL_ARCH}，收到 {arch_type!r}")
    if q_deck <= 0.0:
        raise ValueError("q_deck 必须为正")

    L = float(span)
    f = float(rise)
    N = int(n_seg_half)
    n_arch = 2 * N
    n_hanger = n_arch - 1

    xs = [L * j / n_arch for j in range(n_arch + 1)]
    zs = [_arch_z(x, L, f) for x in xs]

    # ---------------------------------------------------------- 编号
    nn = BlockNumbering()
    deck_nids = nn.allocate(n_arch + 1)
    if arch_type == TIED_ARCH:
        # 拱脚与主梁端共用节点：只分配中间 n-1 个拱肋节点
        mid = nn.allocate(n_arch - 1)
        arch_nids = [deck_nids[0]] + list(mid) + [deck_nids[n_arch]]
    else:
        arch_nids = list(nn.allocate(n_arch + 1))

    en = BlockNumbering()
    arch_eids = en.allocate(n_arch)
    deck_eids = en.allocate(n_arch)
    hanger_eids = en.allocate(n_hanger)
    arch_eid = lambda j: arch_eids[j - 1]
    deck_eid = lambda i: deck_eids[i - 1]
    hanger_eid = lambda j: hanger_eids[j - 1]

    # ---------------------------------------------------------- 节点
    nodes: list[Node3D] = []
    for i in range(n_arch + 1):
        nodes.append(Node3D(id=deck_nids[i], x=xs[i], y=0.0, z=0.0))
    if arch_type == EXTERNAL_ARCH:
        for j in range(n_arch + 1):
            nodes.append(Node3D(id=arch_nids[j], x=xs[j], y=0.0, z=zs[j]))
    else:
        for j in range(1, n_arch):
            nodes.append(Node3D(id=arch_nids[j], x=xs[j], y=0.0, z=zs[j]))

    # ---------------------------------------------------------- 截面/材料
    G_shear = E_steel / (2 * (1 + 0.3))
    arch = box_section(arch_b, arch_h, arch_tf, arch_tw)
    deck = box_section(deck_b, deck_h, deck_tf, deck_tw)
    hanger_A = hanger_area_mm2 * 1e-6

    materials = [Material3D(id=1, E=E_steel, dens=dens_steel, grade="Q345qD")]
    reals = [
        RealConst3D(id=1, A=deck["A"], IY=deck["IY"], IZ=deck["IY"] * 0.5,
                    J=deck["J"], G=G_shear, WT=deck["WT"], WB=deck["WB"],
                    AW=deck["AW"]),
        RealConst3D(id=2, A=arch["A"], IY=arch["IY"], IZ=arch["IY"] * 0.5,
                    J=arch["J"], G=G_shear, WT=arch["WT"], WB=arch["WB"],
                    AW=arch["AW"]),
        RealConst3D(id=3, A=hanger_A),                              # 吊杆 TRUSS
    ]
    etypes = [
        ElemTypeDef3D(id=1, kind="BEAM", mat=1, real=1),   # 主梁
        ElemTypeDef3D(id=2, kind="BEAM", mat=1, real=2),   # 拱肋
        ElemTypeDef3D(id=3, kind="TRUSS", mat=1, real=3),  # 吊杆
    ]

    # ---------------------------------------------------------- 单元
    elements: list[Element3D] = []
    for j in range(1, n_arch + 1):
        elements.append(Element3D(id=arch_eid(j), etype=2,
                                  n1=arch_nids[j - 1], n2=arch_nids[j]))
    for i in range(1, n_arch + 1):
        elements.append(Element3D(id=deck_eid(i), etype=1,
                                  n1=deck_nids[i - 1], n2=deck_nids[i]))
    for j in range(1, n_hanger + 1):
        elements.append(Element3D(id=hanger_eid(j), etype=3,
                                  n1=arch_nids[j], n2=deck_nids[j]))

    # ---------------------------------------------------------- 约束
    boundaries: list[Boundary3D] = []
    if arch_type == TIED_ARCH:
        # 系杆拱：拱脚与主梁端同节点，基础不承受水平推力
        boundaries.append(Boundary3D(node=deck_nids[0], dofs=(2, 3)))
        boundaries.append(Boundary3D(node=deck_nids[n_arch], dofs=(2, 3)))
        boundaries.append(Boundary3D(node=deck_nids[0], dofs=(1, 1)))
    else:
        # 拱梁组合：拱脚固结（无铰拱），水平推力入基础
        boundaries.append(Boundary3D(node=arch_nids[0], dofs=(1, 6)))
        boundaries.append(Boundary3D(node=arch_nids[n_arch], dofs=(1, 6)))
        # 主梁端只给竖向 / 横向，纵向一端固定
        boundaries.append(Boundary3D(node=deck_nids[0], dofs=(2, 3)))
        boundaries.append(Boundary3D(node=deck_nids[n_arch], dofs=(2, 3)))
        boundaries.append(Boundary3D(node=deck_nids[0], dofs=(1, 1)))
    # 平面外：主梁节点绕 x 扭转定位（单梁线）
    for i in range(n_arch + 1):
        boundaries.append(Boundary3D(node=deck_nids[i], dofs=(4, 4)))
    # 平面外：拱肋中间节点横向约束（单肋平面模型）
    for j in range(1, n_arch):
        if arch_nids[j] in set(deck_nids):
            continue
        boundaries.append(Boundary3D(node=arch_nids[j], dofs=(2, 2)))

    # ---------------------------------------------------------- 荷载（线性，单阶段）
    w_arch = arch["A"] * dens_steel * G
    q_total = q_deck + q2_deck
    loads: list[NodalLoad3D] = []
    for j in range(1, n_arch + 1):
        dx = xs[j] - xs[j - 1]
        dz = zs[j] - zs[j - 1]
        w = w_arch * math.hypot(dx, dz)
        loads.append(NodalLoad3D(node=arch_nids[j - 1], dof=3, value=-w / 2))
        loads.append(NodalLoad3D(node=arch_nids[j], dof=3, value=-w / 2))
    for i in range(1, n_arch + 1):
        w = q_total * (xs[i] - xs[i - 1])
        loads.append(NodalLoad3D(node=deck_nids[i - 1], dof=3, value=-w / 2))
        loads.append(NodalLoad3D(node=deck_nids[i], dof=3, value=-w / 2))

    all_eids = [arch_eid(j) for j in range(1, n_arch + 1)] + \
               [deck_eid(i) for i in range(1, n_arch + 1)] + \
               [hanger_eid(j) for j in range(1, n_hanger + 1)]
    stages = [Stage3D(id=1, activate=all_eids, loads=loads)]

    # ---- 车道（opt-in）----
    # 默认 lane_code="" 不写任何 *LANE_3D / *INFLUENCE_3D，第 44 项冻结合约不变。
    # 沿主梁纵列布一条规范车道，影响点取跨中主梁节点竖向自由度。
    lanes: list[Lane3D] = []
    influences: list[Influence3D] = []
    if lane_code:
        mid_i = n_arch // 2
        influences.append(Influence3D(node=deck_nids[mid_i], dof=3))
        lanes.append(Lane3D(
            nodes=[deck_nids[i] for i in range(n_arch + 1)],
            dof=3, code=lane_code,
        ))

    model = ModelSpec3D(
        title=title or f"{L:.0f}m 梁拱组合（{arch_type}，线性共同受力）",
        nodes=nodes,
        materials=materials,
        reals=reals,
        etypes=etypes,
        elements=elements,
        boundaries=boundaries,
        loads=[],
        stages=stages,
        cable_bridge=None,
        influences=influences,
        lanes=lanes,
    )

    # 三铰拱闭式水平推力（benchmark 锚点）：拱肋自重 w_arch 经拱轴传入拱脚，
    # 必须计入总恒载；只算桥面恒载会低估约 25%。线弹性组合体系（刚拱 +
    # 刚性吊杆 + 系梁）实测系梁拉力约 1.01·H_theory。
    q_all = q_total + w_arch
    H_theory = q_all * L * L / (8.0 * f)
    meta = {
        "arch_type": arch_type,
        "span": L,
        "rise": f,
        "sag_ratio": L / f,
        "n_arch_seg": n_arch,
        "n_hanger": n_hanger,
        "n_seg_half": N,
        "arch_node_ids": [arch_nids[j] for j in range(n_arch + 1)],
        "deck_node_ids": [deck_nids[i] for i in range(n_arch + 1)],
        "hanger_ids": [hanger_eid(j) for j in range(1, n_hanger + 1)],
        "arch_elems": [arch_eid(j) for j in range(1, n_arch + 1)],
        "deck_elems": [deck_eid(i) for i in range(1, n_arch + 1)],
        "w_arch": w_arch,
        "q_deck": q_deck,
        "q2_deck": q2_deck,
        "q_all_N_per_m": q_all,
        "H_theory_kN": H_theory / 1000.0,
        "n_stages": len(stages),
        "lane_code": lane_code,
        "lane_nodes": [deck_nids[i] for i in range(n_arch + 1)] if lane_code else [],
        "influence_node": deck_nids[n_arch // 2] if lane_code else 0,
    }
    return model, meta
