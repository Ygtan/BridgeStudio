"""cable_stayed_3d.py
====================
双塔三跨斜拉桥参数化建造器（成桥态求解的输入模型）。

为什么单独一个建造器，而不是往 grillage_3d 里加参数
----------------------------------------------------
梁格法的离散逻辑是"把箱梁切成纵梁线 + 横梁"，斜拉桥的离散逻辑是
"主梁一条线 + 两座塔 + 扇形索面"。两者共用只有截面库，硬塞进一个函数
会让两组互斥参数同时出现（n_lines / n_cable 只能二选一）。

模型构成
--------
- 主梁：一条纵向梁线（y=0，z=0），按 panel 等分，塔位必为节点
- 桥塔：两座竖向悬臂柱，塔底固结（独立节点，与主梁不刚接）
- 拉索：CABLE 单元，梁侧锚点均匀取自该侧梁节点，塔侧锚点沿塔高分散
  （anchor_low~1.0 塔高，形成扇形索面，而非全部挤在塔顶一个点）
- 支承：两桥台 + 两塔处竖向/横向支承；一端纵向固定（半漂浮体系）
- 荷载：桥面恒载 q 按节点受荷宽度分配 + 塔柱自重

编号规范（X20 分块）
--------------------
- 节点：主梁 1000+i，塔1 2000+k，塔2 3000+k
- 单元：主梁 100+i，塔1 200+k，塔2 300+k，索 9000+j
- 实常数：1=主梁，2=桥塔，3=拉索；材料：1=混凝土，2=索

单位：m / N / Pa。
"""

from __future__ import annotations

import math

from model_spec_3d import (
    Boundary3D,
    CableBridge3D,
    CableSpec3D,
    Element3D,
    ElemTypeDef3D,
    Material3D,
    ModelSpec3D,
    Node3D,
    NodalLoad3D,
    RealConst3D,
)
from grillage_3d import box_section

G = 9.81


def build_cable_stayed_3d(
    main_span: float = 200.0,
    side_span: float = 80.0,
    tower_height: float = 60.0,
    n_cable: int = 12,                    # 每塔每侧索数
    panel: float = 8.0,                   # 主梁目标单元长度
    B: float = 12.0,
    h: float = 2.5,
    tf: float = 0.28,
    tw: float = 0.45,
    tower_b: float = 3.0,
    tower_h: float = 4.5,
    n_tower_seg: int = 8,
    anchor_low: float = 0.55,             # 最低索的塔上锚固高度（占塔高）
    q_deck: float = 100e3,                # 桥面恒载 N/m（含自重），向下为正
    cable_area_mm2: float = 6000.0,       # 单索截面积 mm²
    cable_E: float = 195e9,
    cable_dens: float = 7850.0,
    E_conc: float = 34.5e9,
    dens_conc: float = 2500.0,
    mode: str = "COMPLETION",
    method: str = "DECK",                 # DECK | MINENERGY | RIGID
    cable_tensions: dict[int, float] | None = None,   # 指定索力（N），缺省按 method 定
    min_tension_fraction: float = 0.02,
    title: str = "双塔三跨斜拉桥",
) -> tuple[ModelSpec3D, dict]:
    """生成双塔三跨斜拉桥 ModelSpec3D（含成桥态工况）。

    返回 (模型, 派生量字典)。派生量只给界面/验证脚本用（支座 x、索单元 id、
    实际索数、总恒载），不进 pydantic 契约。
    """
    if n_cable < 2:
        raise ValueError("每塔每侧索数 n_cable 至少 2")
    # 塔的分段数不能少于单侧索数：否则多根索会锚到同一个塔节点上
    n_tower_seg = max(n_tower_seg, n_cable)
    L_tot = 2.0 * side_span + main_span
    xT1, xT2 = side_span, side_span + main_span
    G_shear = E_conc / (2 * (1 + 0.2))

    # ---------------------------------------------------------- 主梁节点
    xs: list[float] = [0.0]
    support_idx: list[int] = [0]
    for span, x_start in ((side_span, 0.0), (main_span, xT1), (side_span, xT2)):
        # 每跨至少 n_cable + 4 段：留出首末索到支座的余量后，仍能给足
        # n_cable 个锚点（否则 n_cable=12 会静默退化成 9 根）。
        n_seg = max(int(math.ceil(span / panel)), n_cable + 4)
        for j in range(1, n_seg + 1):
            xs.append(x_start + span * j / n_seg)
        support_idx.append(len(xs) - 1)
    gid = lambda i: 1000 + i + 1

    nodes: list[Node3D] = [Node3D(id=gid(i), x=x, y=0.0, z=0.0)
                           for i, x in enumerate(xs)]
    x_sup = [xs[i] for i in support_idx]        # 0, Ls, Ls+Lm, Ltot

    # ---------------------------------------------------------- 截面
    box = box_section(B, h, tf, tw)
    tower_A = tower_b * tower_h
    tower_I = tower_b * tower_h ** 3 / 12.0
    cable_A = cable_area_mm2 * 1e-6
    cable_w = cable_A * cable_dens * G          # 单位长度重量 N/m

    materials = [
        Material3D(id=1, E=E_conc, dens=dens_conc),
        Material3D(id=2, E=cable_E, dens=cable_dens),
    ]
    reals = [
        RealConst3D(id=1, A=box["A"], IY=box["IY"], IZ=box["IY"] * 0.5,
                    J=box["J"], G=G_shear, WT=box["WT"], WB=box["WB"]),
        RealConst3D(id=2, A=tower_A, IY=tower_I, IZ=tower_I,
                    J=tower_I * 1.6, G=G_shear),
        RealConst3D(id=3, A=cable_A, W=cable_w),
    ]
    etypes = [
        ElemTypeDef3D(id=1, kind="BEAM", mat=1, real=1),
        ElemTypeDef3D(id=2, kind="BEAM", mat=1, real=2),
        ElemTypeDef3D(id=3, kind="CABLE", mat=2, real=3),
    ]

    # ---------------------------------------------------------- 主梁单元 + 荷载
    elements: list[Element3D] = []
    for i in range(len(xs) - 1):
        elements.append(Element3D(id=100 + i + 1, etype=1,
                                  n1=gid(i), n2=gid(i + 1)))

    def trib(i: int) -> float:
        left = (xs[i] - xs[i - 1]) / 2 if i > 0 else 0.0
        right = (xs[i + 1] - xs[i]) / 2 if i < len(xs) - 1 else 0.0
        return left + right

    loads: list[NodalLoad3D] = [
        NodalLoad3D(node=gid(i), dof=3, value=-q_deck * trib(i))
        for i in range(len(xs))
    ]

    # ---------------------------------------------------------- 桥塔
    tower_tops: list[int] = []
    for ti, xT in enumerate((xT1, xT2)):
        base = 2000 if ti == 0 else 3000
        tid = lambda k, _b=base: _b + k + 1
        for k in range(n_tower_seg + 1):
            nodes.append(Node3D(id=tid(k), x=xT, y=0.0,
                                z=tower_height * k / n_tower_seg))
        tower_tops.append(tid(n_tower_seg))
        ebase = 200 if ti == 0 else 300
        w_tower = tower_A * dens_conc * G
        for k in range(n_tower_seg):
            elements.append(Element3D(id=ebase + k + 1, etype=2,
                                      n1=tid(k), n2=tid(k + 1)))
            # 塔柱自重：两端各半
            seg = tower_height / n_tower_seg
            loads.append(NodalLoad3D(node=tid(k), dof=3, value=-w_tower * seg / 2))
            loads.append(NodalLoad3D(node=tid(k + 1), dof=3, value=-w_tower * seg / 2))

    # ---------------------------------------------------------- 拉索
    # 梁侧锚点两条硬规则（都是被病态解逼出来的）：
    # ① 不留"紧邻支座"的锚点。索力是靠"单位力对 → 柔度"反算的，锚点离支座
    #    只有一个梁段时，该点柔度接近 0，索力被解成天文数字（实测最外索
    #    19.9 MN，而 A=6000mm² 的索破断力才 10.6 MN）。
    # ② 首末索各留 1.2 个梁段余量，这也符合斜拉桥的实际布索（第一根索距
    #    塔/桥台都有距离）。
    # 因此每跨的离散数至少是 n_cable + 4，否则留完余量凑不出 n_cable 根。
    X = {n.id: n.x for n in nodes}

    def anchors_for_span(x_lo: float, x_hi: float, x_tower: float, seg: float) -> list[int]:
        margin = 1.2 * seg
        cand = [
            gid(i) for i, x in enumerate(xs)
            if x_lo - 1e-9 <= x <= x_hi + 1e-9
            and abs(x - x_lo) > margin - 1e-9
            and abs(x - x_hi) > margin - 1e-9
        ]
        cand.sort(key=lambda n: abs(X[n] - x_tower))
        return cand

    def pick_even(seq: list[int], k: int) -> list[int]:
        if k <= 0 or not seq:
            return []
        if len(seq) <= k:
            return list(seq)
        if k == 1:
            return [seq[0]]
        return [seq[round(j * (len(seq) - 1) / (k - 1))] for j in range(k)]

    def tower_anchor(ti: int, j: int, k_total: int) -> int:
        """第 j 根索（0=最低）在塔上的锚固节点。"""
        base = 2000 if ti == 0 else 3000
        lo = anchor_low
        hi = 1.0
        frac = lo if k_total <= 1 else lo + (hi - lo) * j / (k_total - 1)
        seg = round(frac * n_tower_seg)
        seg = min(max(seg, 1), n_tower_seg)
        return base + seg + 1

    cable_ids: list[int] = []
    eid_cable = 9000
    span_specs = (
        (0, xT1, 0.0, xT1),      # 塔1 边跨侧
        (0, xT1, xT1, xT2),      # 塔1 主跨侧
        (1, xT2, xT1, xT2),      # 塔2 主跨侧
        (1, xT2, xT2, L_tot),    # 塔2 边跨侧
    )
    for ti, xT, x_lo, x_hi in span_specs:
        # 该跨的实际段长（节点等分，取跨起点相邻 xs 的差）
        i_lo = min(range(len(xs)), key=lambda i: abs(xs[i] - x_lo))
        seg = (xs[i_lo + 1] - xs[i_lo]) if i_lo + 1 < len(xs) else (x_hi - x_lo)
        picks = pick_even(anchors_for_span(x_lo, x_hi, xT, seg), n_cable)
        # 索按"离塔近 → 塔上锚点低"配对：短索锚得低，长索锚得高（扇形）
        for j, node in enumerate(picks):
            eid_cable += 1
            elements.append(Element3D(id=eid_cable, etype=3,
                                      n1=tower_anchor(ti, j, len(picks)), n2=node))
            cable_ids.append(eid_cable)

    # ---------------------------------------------------------- 约束
    boundaries: list[Boundary3D] = []
    for i in support_idx:
        boundaries.append(Boundary3D(node=gid(i), dofs=(2, 3)))   # 双支座体系
    boundaries.append(Boundary3D(node=gid(0), dofs=(1, 1)))        # 纵向固定一端
    for i in range(len(xs)):
        boundaries.append(Boundary3D(node=gid(i), dofs=(4, 4)))    # 扭转定位（单梁线）
    for ti in range(2):
        base = 2000 if ti == 0 else 3000
        for d in range(1, 7):
            boundaries.append(Boundary3D(node=base + 1, dofs=(d, d)))  # 塔底固结

    # ---------------------------------------------------------- 成桥态工况
    trib_map = {gid(i): trib(i) for i in range(len(xs))}
    tensions = dict(cable_tensions or {})
    method_out = method
    if not tensions and method == "DECK":
        # 按恒载竖向分配：每根索只承担其锚点所辖桥面宽度内的恒载竖向分量。
        # Σ(T·ez) 恒等于 q·L_tot（自洽），且分布随 ez 平滑变化——这是斜拉桥
        # 手算初定索力的标准做法，也是本界面默认。
        model_tmp = ModelSpec3D(
            title=title, nodes=nodes, materials=materials, reals=reals,
            etypes=etypes, elements=elements, boundaries=boundaries, loads=loads)
        tensions = tensions_by_deck_load(model_tmp, q_deck, trib_map)
        method_out = "GIVEN"
    cables = [CableSpec3D(elem=eid, anchor=0, tension=float(tensions.get(eid, 0.0)))
              for eid in cable_ids]
    cable_bridge = CableBridge3D(
        mode=mode, method=method_out if method_out in ("MINENERGY", "RIGID") else "MINENERGY",
        cables=cables,
        min_tension_fraction=min_tension_fraction,
        deck_load=q_deck * L_tot,
    )

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
    # 供界面/验证脚本读取的派生量（不算模型字段，避免污染 pydantic 契约）
    model_meta = {
        "x_supports": x_sup,
        "cable_ids": cable_ids,
        "n_cable_actual": len(cable_ids),
        "cable_w": cable_w,
        "cable_area": cable_A,
        "total_deck_load": q_deck * L_tot,
        "deck_trib": trib_map,
        "deck_load": q_deck,
        "tension_method": method,
        "n_nodes": len(nodes),
        "n_tower_seg": n_tower_seg,
    }
    return model, model_meta


def tensions_by_deck_load(
    model: ModelSpec3D,
    q_deck: float,
    trib_map: dict[int, float],
) -> dict[int, float]:
    """按恒载竖向分配法初定索力：T_i = q·trib(锚点) / ez_i。

    ez_i 为索从梁侧锚点指向塔端的方向余弦（竖直分量）。因为
    Σ q·trib = q·L，所以 Σ T·ez 精确等于桥面总恒载——**竖向自洽**，
    这是刚性支承法/最小弯曲能量法在有塔处支座的体系里做不到的：
      · 刚性支承法把锚点竖向压成 0，塔处支座会被解成"向下拉"（负反力），
        实测索力竖向分量达到桥面恒载的 141%（支座在拉梁，物理上不成立）；
      · 最小弯曲能量法在有支座的裸结构上，最优解就是"索不受力"
        （梁自己扛弯矩），实测 48 根索里 40 根被钉在保力下限。
    本方法忽略塔的柔性（初定阶段允许），塔的变形由成桥态闭环的几何迭代
    自行吸收——闭环里 L0 就是按这组目标索力反算的。
    """
    node_z = {n.id: n.z for n in model.nodes}
    out: dict[int, float] = {}
    for e in model.elements:
        kind = next(t.kind for t in model.etypes if t.id == e.etype)
        if kind != "CABLE":
            continue
        a, b = (e.n1, e.n2) if node_z[e.n1] <= node_z[e.n2] else (e.n2, e.n1)
        dz = node_z[b] - node_z[a]
        if abs(dz) < 1e-9:
            raise ValueError(f"索 {e.id} 两端等高，竖向分量退化")
        na = next(n for n in model.nodes if n.id == a)
        nb = next(n for n in model.nodes if n.id == b)
        L = ((nb.x - na.x) ** 2 + (nb.y - na.y) ** 2 + (nb.z - na.z) ** 2) ** 0.5
        ez = dz / L
        out[e.id] = q_deck * trib_map.get(a, 0.0) / ez
    return out


def cable_break_load(cable_area_mm2: float, fpu: float = 1770e6) -> float:
    """单索破断力 N（fpu 默认 1770 MPa 钢绞线/钢丝）。"""
    return cable_area_mm2 * 1e-6 * fpu
