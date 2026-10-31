"""suspension_3d.py
==================
单跨双塔悬索桥参数化建造器（自锚式 / 地锚式）。

为什么单独一个建造器
--------------------
悬索桥的拓扑与斜拉桥差别不在"有没有索"，而在**主缆是一条连续索**：
锚碇 → 塔顶 → 跨中 → 塔顶 → 锚碇，一根到底；吊索从主缆竖直下到加劲梁。
斜拉桥是"一根索一个塔上锚点"的扇形，两者只有截面库和梁单元是共用的。

更关键的一条：**主缆的设计线形由恒载决定**，不能像斜拉桥那样"梁面高程
由人给定、索力去适应"。所以这个建造器只给出一个抛物线的**初值**，
真正的线形由内核找形算出来（见 cable_solver 的 *SUSP_BRIDGE 段）。
若把初值当成设计线形直接用，成桥态闭环会无解——残差卡在数厘米上，
看起来像"没收敛"，其实是设计构形根本不是平衡态。

自锚式与地锚式的区别，全部体现在两处，没有第三处
------------------------------------------------
1. **主缆锚在哪里**：地锚式锚在桥台外侧的锚碇（全约束，H 传给地基）；
   自锚式锚在加劲梁端节点（H 压进加劲梁——这正是自锚式的定义）。
2. **加劲梁纵向怎么约束**：地锚式梁端纵向固定，H 不经梁；
   自锚式必须让梁能自由缩短，否则 H 会被支座吃掉。这里把纵向约束放在
   **跨中节点**：对称荷载下该约束的支反力恒为零，只起"消除刚体位移"的作用，
   梁仍可绕跨中对称缩短。若改成约束梁端纵向，主缆水平力会直接进支座，
   算出来的梁轴力变成 0——一个看不出错的错。

编号规范（沿用 X20 分块）
------------------------
- 节点：加劲梁 1000+i，主缆 2000+k，塔1 3000+m，塔2 4000+m，锚碇 5001/5002
- 单元：加劲梁 100+i，塔1 800+m，塔2 900+m，主缆 3000+k，吊索 6000+j
- 实常数：1=加劲梁，2=桥塔，3=主缆，4=吊索
- 材料：1=加劲梁（钢），2=桥塔（混凝土），3=主缆与吊索（高强钢丝）

单位：m / N / Pa。
"""

from __future__ import annotations

import math

from grillage_3d import box_section
from model_spec_3d import (
    Boundary3D,
    CableBridge3D,
    Element3D,
    ElemTypeDef3D,
    Material3D,
    ModelSpec3D,
    Node3D,
    NodalLoad3D,
    RealConst3D,
)

G = 9.81

#: 体系名 → 中文（界面与报告共用同一套词）
SYSTEM_LABELS = {"EARTH": "地锚式悬索桥", "SELF": "自锚式悬索桥"}


def build_suspension_3d(
    system: str = "EARTH",
    main_span: float = 300.0,
    side_span: float = 0.0,               # 0 = 自动（自锚式按"锚点主缆水平"取）
    sag_ratio: float = 0.10,
    tower_height: float = 0.0,            # 0 = 矢高 + 净空
    clearance: float = 5.0,               # 跨中主缆至桥面的净空
    tower_below: float = 8.0,             # 塔底在桥面以下（基础）
    panel: float = 10.0,                  # 吊索间距 / 加劲梁目标单元长
    side_hangers: bool = True,            # 边跨是否挂吊索
    anchor_offset: float = 12.0,          # 仅地锚式：锚碇在桥台外侧的距离
    B: float = 12.0,
    h: float = 3.0,
    tf: float = 0.028,
    tw: float = 0.020,
    tower_b: float = 3.0,
    tower_h: float = 4.0,
    n_tower_seg: int = 6,
    main_area_mm2: float = 60000.0,       # 单根主缆截面积（全桥两根之一）
    hanger_area_mm2: float = 3000.0,
    cable_E: float = 195e9,
    cable_dens: float = 7850.0,
    deck_E: float = 206e9,
    deck_dens: float = 7850.0,
    tower_E: float = 34.5e9,
    tower_dens: float = 2500.0,
    q_deck: float = 100e3,                # 桥面恒载 N/m（含桥面系与二期）
    title: str = "",
) -> tuple[ModelSpec3D, dict]:
    """生成悬索桥 ModelSpec3D（成桥态工况）与派生量字典。"""
    system = (system or "EARTH").upper()
    if system not in SYSTEM_LABELS:
        raise ValueError(f"system 只能是 EARTH / SELF，收到 {system!r}")
    if main_span <= 0 or side_span < 0:
        raise ValueError("主跨必须为正、边跨不能为负")
    if not 0.02 <= sag_ratio <= 0.30:
        # 矢跨比不是自由参数：太小则主缆接近直线、H 巨大且塔顶竖向反力畸高，
        # 太大则吊索过长、梁的竖向刚度靠不上主缆。公路悬索桥实际在 1/12~1/8。
        raise ValueError(f"矢跨比应在 0.02~0.30 之间，收到 {sag_ratio}")

    L_main = float(main_span)
    f_sag = sag_ratio * L_main                     # 主跨矢高
    z_top = f_sag + clearance                      # 塔顶高程（桥面为 0）
    z_base = -abs(tower_below)
    z_anchor = 0.0
    if tower_height <= 0:
        tower_height = z_top - z_base
    else:
        z_top = z_base + tower_height

    # ---------------------------------------------------------- 边跨长度
    #
    # 边跨不是自由参数，尤其对自锚式。
    #
    # 主缆水平力 H 由主跨矢高定了（H ≈ qL²/8f），边跨必须用同一个 H（否则塔顶
    # 受不平衡水平力）。于是边跨主缆在锚点处的竖向分力为
    #       V = H·(Δz/L) − qL/2
    # 其中 Δz 是塔顶到锚点的高差。**自锚式的锚点就在加劲梁端**，这个 V 只能由
    # 梁端的竖向支座承担，而支座能承压、不能承拉：
    #   · V < 0（主缆在锚点朝上走）→ 梁端要被"拔起来"，必须设抗拔支座或压重；
    #   · V = 0 → 主缆在锚点恰好水平，梁端只受水平力，这才是自锚式该有的样子。
    # 令 V=0 解出 L = √(2HΔz/q)，这就是"平衡边跨"。现实里的自锚式悬索桥正好
    # 落在这一带（如佛山平胜大桥 350 m 主跨配 2×165 m 边跨，比值 0.47，远大于
    # 地锚式的 1/3）——不是设计者偏爱长边跨，是 V=0 那个条件要求的。
    #
    # 地锚式不受这条约束：锚碇本来就是靠自重与摩阻抗拔的（本算例锚碇反力
    # −7,980 kN 就是抗拔力），所以边跨按常规取 1/3 主跨。
    cable_A0 = main_area_mm2 * 1e-6
    q_side0 = q_deck + cable_A0 * cable_dens * G
    H0 = q_side0 * L_main ** 2 / (8.0 * f_sag)          # 均布近似，够用来定边跨
    dz_top = max(z_top - z_anchor, 0.0)
    L_bal = math.sqrt(2.0 * H0 * dz_top / q_side0) if q_side0 > 0 and dz_top > 0 else 0.0
    if side_span and float(side_span) > 0:
        L_side = float(side_span)
    else:
        L_side = L_bal if system == "SELF" else L_main / 3.0
    L_tot = 2 * L_side + L_main

    # ---------------------------------------------------------- 加劲梁节点
    n_side = max(2, int(round(L_side / panel))) if L_side > 0 else 0
    n_main = max(4, int(round(L_main / panel)))
    xs: list[float] = []
    if n_side:
        xs += [L_side * i / n_side for i in range(n_side + 1)]           # 0 … Ls
    else:
        xs.append(0.0)
    xs += [L_side + L_main * j / n_main for j in range(1, n_main + 1)]   # … Ls+Lm
    if n_side:
        xs += [L_side + L_main + L_side * k / n_side
               for k in range(1, n_side + 1)]                            # … L
    i_t1 = n_side                       # 塔1 对应的梁节点下标
    i_t2 = n_side + n_main              # 塔2
    gid = lambda i: 1000 + i + 1
    deck_ids = [gid(i) for i in range(len(xs))]
    x_to_deck = {round(x, 9): gid(i) for i, x in enumerate(xs)}

    nodes: list[Node3D] = [Node3D(id=gid(i), x=x, y=0.0, z=0.0)
                           for i, x in enumerate(xs)]

    # ---------------------------------------------------------- 截面与材料
    box = box_section(B, h, tf, tw)
    tower_A = tower_b * tower_h
    tower_I = tower_b * tower_h ** 3 / 12.0
    cable_A = main_area_mm2 * 1e-6
    hanger_A = hanger_area_mm2 * 1e-6
    cable_w = cable_A * cable_dens * G
    hanger_w = hanger_A * cable_dens * G
    G_steel = deck_E / (2 * (1 + 0.3))
    G_conc = tower_E / (2 * (1 + 0.2))

    materials = [
        Material3D(id=1, E=deck_E, dens=deck_dens),
        Material3D(id=2, E=tower_E, dens=tower_dens),
        Material3D(id=3, E=cable_E, dens=cable_dens),
    ]
    reals = [
        RealConst3D(id=1, A=box["A"], IY=box["IY"], IZ=box["IY"] * 0.5,
                    J=box["J"], G=G_steel, WT=box["WT"], WB=box["WB"]),
        RealConst3D(id=2, A=tower_A, IY=tower_I, IZ=tower_I,
                    J=tower_I * 1.6, G=G_conc),
        RealConst3D(id=3, A=cable_A, W=cable_w),
        RealConst3D(id=4, A=hanger_A, W=hanger_w),
    ]
    etypes = [
        ElemTypeDef3D(id=1, kind="BEAM", mat=1, real=1),
        ElemTypeDef3D(id=2, kind="BEAM", mat=2, real=2),
        ElemTypeDef3D(id=3, kind="CABLE", mat=3, real=3),
        ElemTypeDef3D(id=4, kind="CABLE", mat=3, real=4),
    ]

    # ---------------------------------------------------------- 加劲梁单元
    elements: list[Element3D] = [
        Element3D(id=100 + i + 1, etype=1, n1=gid(i), n2=gid(i + 1))
        for i in range(len(xs) - 1)
    ]
    deck_e0, deck_e1 = 101, 100 + len(xs) - 1

    # ---------------------------------------------------------- 桥塔
    tower_tops: list[int] = []
    for ti, xt in enumerate((L_side, L_side + L_main)):
        base = 3000 if ti == 0 else 4000
        tid = lambda k, _b=base: _b + k + 1
        for k in range(n_tower_seg + 1):
            nodes.append(Node3D(id=tid(k), x=xt, y=0.0,
                                z=z_base + (z_top - z_base) * k / n_tower_seg))
        tower_tops.append(tid(n_tower_seg))
        ebase = 800 if ti == 0 else 900
        seg = (z_top - z_base) / n_tower_seg
        w_tower = tower_A * tower_dens * G
        for k in range(n_tower_seg):
            elements.append(Element3D(id=ebase + k + 1, etype=2,
                                      n1=tid(k), n2=tid(k + 1)))

    # ---------------------------------------------------------- 主缆链
    # 链上的水平位置：锚 → 边跨各吊点 → 塔1 → 主跨各吊点 → 塔2 → 边跨吊点 → 锚
    # 吊索必须竖直，所以主缆节点与梁节点同 x——这也是悬索桥离散的自然做法。
    interior = [x for x in xs if 1e-9 < x < L_tot - 1e-9]
    main_only = [x for x in interior if L_side + 1e-9 < x < L_side + L_main - 1e-9]
    side1_only = [x for x in interior if x < L_side - 1e-9]
    side2_only = [x for x in interior if x > L_side + L_main + 1e-9]

    if system == "EARTH":
        chain_x = [-anchor_offset] + (side1_only if side_hangers else []) \
                  + [L_side] + main_only + [L_side + L_main] \
                  + (side2_only if side_hangers else []) + [L_tot + anchor_offset]
    else:
        # 自锚式：主缆直接锚在加劲梁两端（就是梁端节点本身）
        chain_x = [0.0] + (side1_only if side_hangers else []) \
                  + [L_side] + main_only + [L_side + L_main] \
                  + (side2_only if side_hangers else []) + [L_tot]
    chain_x = _dedup_sorted(chain_x)

    idx_t1 = _index_of(chain_x, L_side)
    idx_t2 = _index_of(chain_x, L_side + L_main)
    i_top1, i_top2 = tower_tops[0], tower_tops[1]

    # 主缆链的节点号：**塔位必须用塔顶节点本身，自锚式端部必须用梁端节点本身**。
    #
    # 不能"另建一个坐标相同的节点"。坐标重合而节点不共用，主缆就是一根从锚碇
    # 到锚碇自由悬挂的索——塔不参与受力、梁也不参与，而各处位移、倾角看起来
    # 都还"像座桥"，只有索力掉到设计值的百分之一、吊索全部松弛、桥面被迫自己
    # 受弯（实测梁弯矩虚高 10 倍）。这是悬索桥建模里最隐蔽的一处错，因为它不
    # 报错、不发散，只是答案错。内核另有一条校验专门查这件事。
    cable_ids: list[int] = []
    cable_own: list[int] = []          # 由本建造器新建的主缆节点（需加横向约束）
    last_k = len(chain_x) - 1
    for k, xc in enumerate(chain_x):
        if k == idx_t1:
            cable_ids.append(i_top1)
            continue
        if k == idx_t2:
            cable_ids.append(i_top2)
            continue
        if system == "SELF" and k == 0:
            cable_ids.append(gid(0))       # 主缆锚进加劲梁端（自锚式的定义）
            continue
        if system == "SELF" and k == last_k:
            cable_ids.append(gid(len(xs) - 1))
            continue
        nid = 2000 + k + 1
        nodes.append(Node3D(id=nid, x=xc, y=0.0,
                            z=_initial_cable_z(xc, L_side, L_main, z_top, f_sag,
                                               z_anchor, system, anchor_offset)))
        cable_ids.append(nid)
        cable_own.append(nid)

    main_cable_elems: list[int] = []
    for k in range(len(cable_ids) - 1):
        eid = 3000 + k + 1
        elements.append(Element3D(id=eid, etype=3,
                                  n1=cable_ids[k], n2=cable_ids[k + 1]))
        main_cable_elems.append(eid)

    # ---------------------------------------------------------- 吊索
    # 塔顶与两端锚点不挂吊索：塔顶是主缆的转折点，锚点本身就是支承。
    hangers: list[int] = []
    eid_h = 6000
    for k, xc in enumerate(chain_x):
        if k in (0, len(chain_x) - 1, idx_t1, idx_t2):
            continue
        key = round(xc, 9)
        if key not in x_to_deck:
            continue                      # 锚跨（地锚式的桥台外侧）没有梁节点
        eid_h += 1
        elements.append(Element3D(id=eid_h, etype=4,
                                  n1=cable_ids[k], n2=x_to_deck[key]))
        hangers.append(eid_h)

    # ---------------------------------------------------------- 荷载
    def trib(i: int) -> float:
        left = (xs[i] - xs[i - 1]) / 2 if i > 0 else 0.0
        right = (xs[i + 1] - xs[i]) / 2 if i < len(xs) - 1 else 0.0
        return left + right

    loads: list[NodalLoad3D] = [
        NodalLoad3D(node=gid(i), dof=3, value=-q_deck * trib(i))
        for i in range(len(xs))
    ]
    # 塔柱自重（两端各半，与斜拉桥建造器一致）
    for ti in range(2):
        base = 3000 if ti == 0 else 4000
        tid = lambda k, _b=base: _b + k + 1
        seg = (z_top - z_base) / n_tower_seg
        w_tower = tower_A * tower_dens * G
        for k in range(n_tower_seg):
            loads.append(NodalLoad3D(node=tid(k), dof=3, value=-w_tower * seg / 2))
            loads.append(NodalLoad3D(node=tid(k + 1), dof=3, value=-w_tower * seg / 2))

    # ---------------------------------------------------------- 约束
    boundaries: list[Boundary3D] = []
    for i in (0, len(xs) - 1):
        boundaries.append(Boundary3D(node=gid(i), dofs=(2, 3)))    # 梁端：横向+竖向
    # 塔处加劲梁竖向支座。
    #
    # 塔位那个梁节点上不挂吊索（塔顶是主缆的转折点），若再不给支承，它承担的
    # 那一片桥面荷载就只能靠相邻两跨的加劲梁"扛"过去——实测这会凭空造出
    # 1.2 万 kN·m 的塔处梁弯矩（正常值只有 400 量级）和不相称的梁轴力，吊索力
    # 也会偏出 10%。真实结构里这里就是塔上的竖向支座，荷载经塔身下传。
    # 本版把该支座反力直接记为支座反力（不细分塔梁传力路径），报告里单独列出
    # 它的量值，塔柱轴力需要时再加上这一项。
    for i in (i_t1, i_t2):
        boundaries.append(Boundary3D(node=gid(i), dofs=(2, 3)))
    for i in range(len(xs)):
        boundaries.append(Boundary3D(node=gid(i), dofs=(4, 4)))    # 扭转定位（单梁线）

    # 单索面平面模型：主缆与吊索全部落在 y=0 平面内，而索/杆单元只提供轴向
    # 刚度、没有平面外刚度，主缆节点的横向自由度因此是**零刚度的机构**——
    # 内核会直接报"零刚度自由度：节点 xxxx 的第 2 个自由度未约束且未被任何
    # 单元连接"（若装配时没查出来，Newton 会以 |du|~1 m 振荡发散）。
    # 真实结构里平面外刚度来自另一根主缆（两索面）与横撑；本模型按平面问题
    # 处理，故把主缆节点的横向自由度取约束。加索面间距是另一个量级的改动
    # （要建双索面 + 横梁），不在本版范围。
    #
    # 只约束**主缆自己新建的**节点：塔顶与梁端节点由梁单元提供横向刚度，
    # 再去约束它反而把塔的平面外弯曲锁死了。
    #
    # 地锚式的两个锚碇节点也在 cable_own 里（它们是新建的），但后面会整体
    # 全约束；这里跳过它们，否则同一自由度被约束两次，模型校验会报"重复
    # 约束"——数值上无害，但会在界面上挂一条永远消不掉的提示。
    anchored = ({cable_ids[0], cable_ids[-1]} if system == "EARTH" else set())
    for cid in cable_own:
        if cid in anchored:
            continue
        boundaries.append(Boundary3D(node=cid, dofs=(2, 2)))

    if system == "EARTH":
        boundaries.append(Boundary3D(node=gid(0), dofs=(1, 1)))    # 梁端纵向固定
        for cid in (cable_ids[0], cable_ids[-1]):
            for d in range(1, 7):                                  # 锚碇全约束
                boundaries.append(Boundary3D(node=cid, dofs=(d, d)))
    else:
        # 自锚式：纵向约束必须放在跨中（对称荷载下支反力恒为零），
        # 不能放在梁端——否则主缆水平力被支座直接吃掉，梁轴力算成 0。
        i_mid = min(range(len(xs)), key=lambda i: abs(xs[i] - L_tot / 2))
        boundaries.append(Boundary3D(node=gid(i_mid), dofs=(1, 1)))

    for ti in range(2):
        base = 3000 if ti == 0 else 4000
        for d in range(1, 7):
            boundaries.append(Boundary3D(node=base + 1, dofs=(d, d)))  # 塔底固结

    # ---------------------------------------------------------- 成桥态工况
    cable_bridge = CableBridge3D(
        mode="COMPLETION",
        method="MINENERGY",
        deck_load=q_deck * L_tot,
        system=system,
        sag=f_sag,
        tower1=i_top1,
        tower2=i_top2,
        main_cable=main_cable_elems,
        hangers=hangers,
        deck_e0=deck_e0,
        deck_e1=deck_e1,
    )

    if not title:
        title = f"双塔三跨{SYSTEM_LABELS[system]}　主跨 {L_main:.0f} m"
    model = ModelSpec3D(
        title=title,
        nodes=nodes,
        materials=materials,
        reals=reals,
        etypes=etypes,
        elements=elements,
        boundaries=boundaries,
        loads=loads,
        cable_bridge=cable_bridge,
    )

    # 解析手算水平力：均布近似 H = qL²/(8f)，仅作对照（真正值由内核找形给出）
    q_main = q_deck + cable_w
    meta = {
        "system": system,
        "system_label": SYSTEM_LABELS[system],
        "x_supports": [0.0, L_side, L_side + L_main, L_tot],
        "tower_nodes": [i_top1, i_top2],
        "anchor_nodes": [cable_ids[0], cable_ids[-1]],
        "main_cable_elems": main_cable_elems,
        "hangers": hangers,
        "n_hanger": len(hangers),
        "sag": f_sag,
        "sag_ratio": sag_ratio,
        "tower_top_z": z_top,
        "tower_base_z": z_base,
        "main_span": L_main,
        "side_span": L_side,
        "side_span_auto": not (side_span and float(side_span) > 0),
        # 平衡边跨：使主缆在锚点竖向分力为 0 的边跨长度（见 build 里的推导）。
        # 自锚式的锚点就在梁端，梁端支座不能承拉，所以这条对它是有约束力的。
        "side_span_balanced": L_bal,
        "total_length": L_tot,
        "deck_load": q_deck,
        "total_deck_load": q_deck * L_tot,
        "cable_area": cable_A,
        "cable_w": cable_w,
        "H_estimate": q_main * L_main ** 2 / (8 * f_sag),
        "n_nodes": len(nodes),
        "n_elements": len(elements),
    }
    return model, meta


# ---------------------------------------------------------------- 小工具


def _dedup_sorted(xs: list[float]) -> list[float]:
    out: list[float] = []
    for x in sorted(xs):
        if not out or abs(x - out[-1]) > 1e-6:
            out.append(float(x))
    return out


def _index_of(xs: list[float], x: float) -> int:
    for i, v in enumerate(xs):
        if abs(v - x) < 1e-6:
            return i
    raise ValueError(f"主缆链上没有 x={x} 的节点：塔位必须是主缆链上的节点")


def _initial_cable_z(x: float, Ls: float, Lm: float, z_top: float, f: float,
                     z_anchor: float, system: str, offset: float) -> float:
    """主缆高程初值（抛物线）。**这只是初值**——真正线形由内核找形给出。

    为什么还要给初值：内核的找形是以现有几何为起点做索多边形积分，
    初值只影响可读性（.dat 打开时像座桥）；给成一条水平直线也能算，
    但排查 .dat 的人会以为模型坏了。
    """
    if Ls - 1e-9 <= x <= Ls + Lm + 1e-9:
        t = (x - Ls) / Lm                       # 0…1
        return z_top - f * 4.0 * t * (1.0 - t)
    if x < Ls:
        x0 = 0.0 if system == "SELF" else -offset
        t = (x - x0) / max(Ls - x0, 1e-9)
        return z_anchor + (z_top - z_anchor) * t
    x1 = Ls + Lm + Ls if system == "SELF" else Ls + Lm + Ls + offset
    t = (x - (Ls + Lm)) / max(x1 - (Ls + Lm), 1e-9)
    return z_top + (z_anchor - z_top) * t


def cable_break_load(area_m2: float, fpu: float = 1770e6) -> float:
    """主缆/吊索破断力 N（1770 MPa 级镀锌高强钢丝，GB/T 17101）。"""
    return area_m2 * fpu
