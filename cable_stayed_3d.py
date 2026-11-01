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
    Lane3D,
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
    main_cover: float = 0.46,             # 单侧索在主跨上的覆盖比例（留中部无索区）
    q_deck: float = 100e3,                # 桥面恒载 N/m（含自重），向下为正
    cable_area_mm2: float = 6000.0,       # 单索截面积 mm²
    cable_E: float = 195e9,
    cable_dens: float = 7850.0,
    E_conc: float = 34.5e9,
    dens_conc: float = 2500.0,
    deck_grade: str = "C50",              # 主梁混凝土等级（应力验算用，不参与求解）
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
    # 离散数由"布索覆盖比例"反推，而不是拍一个 panel：
    #   主跨每侧只覆盖 main_cover 倍主跨（中部留无索区，这是实际斜拉桥的做法），
    #   要在 0.42L 内放下 n_cable 个锚点且首末各留 1.2 段余量，需要
    #       (0.42·L − 2·1.2·seg) / seg + 1 ≥ n_cable
    #   ⇒  n_seg ≥ (n_cable + 2.4) / 0.42　（取 2.6 留一格余量）
    # 曾用固定 n_cable+4：主跨 12 根/侧只有 27 段，覆盖半跨时锚点不够，
    # 于是两侧索都铺满全跨、锚点交错，索力沿桥呈锯齿（实测 520→2580→880 kN）。
    n_seg_by_cover = {
        "side": int(math.ceil((n_cable + 2.6) / 1.0)),
        "main": int(math.ceil((n_cable + 2.6) / main_cover)),
    }
    xs: list[float] = [0.0]
    support_idx: list[int] = [0]
    for span, x_start, kind in ((side_span, 0.0, "side"),
                                (main_span, xT1, "main"),
                                (side_span, xT2, "side")):
        n_seg = max(int(math.ceil(span / panel)), n_cable + 4,
                    n_seg_by_cover[kind])
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
        Material3D(id=1, E=E_conc, dens=dens_conc, grade=deck_grade),
        # 斜拉索按安全系数判，不标牌号（见 result_3d.cable_table）
        Material3D(id=2, E=cable_E, dens=cable_dens),
    ]
    reals = [
        RealConst3D(id=1, A=box["A"], IY=box["IY"], IZ=box["IY"] * 0.5,
                    J=box["J"], G=G_shear, WT=box["WT"], WB=box["WB"],
                    AW=box["AW"]),
        # 塔是实心矩形：最大剪应力 1.5V/A，等效腹板面积取 A/1.5
        RealConst3D(id=2, A=tower_A, IY=tower_I, IZ=tower_I,
                    J=tower_I * 1.6, G=G_shear, AW=tower_A / 1.5),
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
        # 塔单元从 800 起：主梁单元按 100+i 递增，主跨加密后可能越过 200，
        # 原先塔从 200 起会与主梁撞号（n_cable=20 时主梁到 206，实测报
        # "单元编号重复: 201"）。索仍从 9000 起，三者互不重叠。
        ebase = 800 if ti == 0 else 900
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
        if len(seq) < k:
            # 静默少索是这里最危险的失败模式：模型照样收敛、报告照样出，
            # 只是"每塔每侧 12 根"变成了 11 根。宁可当场报错。
            raise ValueError(
                f"锚点候选不足：需要 {k} 个，候选只有 {len(seq)} 个。"
                f"请增大主梁离散或减小 main_cover。")
        if len(seq) == k:
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
    # 主跨每侧只覆盖 main_cover×Lm：两侧索在跨中留出无索区（实际斜拉桥
    # 的无索区约占主跨 1/5~1/3）。若两侧都铺满全跨，锚点会交错，
    # 同一 x 附近同时有短索与长索，索力沿桥呈锯齿——那不是索力分布，
    # 是布索错误在结果上的投影。
    span_specs = (
        (0, xT1, 0.0, xT1),                                  # 塔1 边跨侧
        (0, xT1, xT1, xT1 + main_cover * main_span),         # 塔1 主跨侧
        (1, xT2, xT2 - main_cover * main_span, xT2),         # 塔2 主跨侧
        (1, xT2, xT2, L_tot),                                # 塔2 边跨侧
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
        tensions = tensions_by_deck_load(model_tmp, q_deck, trib_map, x_sup)
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
        # 车道：主梁纵向节点序列，按 JTG D60 自动生成 qk/Pk。
        # 索承体系内核在成桥态切线刚度上算活载梁内力包络（见 cable_solver）。
        lanes=[Lane3D(nodes=[gid(i) for i in range(len(xs))], code="JTG_D60")],
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
    trib_map: dict[int, float] | None = None,
    x_supports: list[float] | None = None,
) -> dict[int, float]:
    """按恒载竖向分配法初定索力：每跨的恒载按索数均分，再除以各索的竖向余弦。

    为什么必须"按跨"而不是"按锚点受荷宽度"
    ----------------------------------------
    早期实现用 T_i = q·trib(锚点)/ez_i，只在**有锚点**的节点上分配。主跨中部
    留出无索区后，那几段梁的 trib 无处分摊，等于宣称"这部分恒载由支座承担"——
    可主跨中部根本没有支座。于是成桥态里 28% 的桥面荷载必须靠主梁自己跨越
    200 m 传到两端，实测预抬高被放大到 992 mm（正常应为数十毫米）。

    现在的做法：每个跨内的恒载（扣掉两端支座断面直接承担的那一半受荷宽度）
    均分给该跨全部拉索，于是 ΣT·ez 精确等于"该跨由索承担的恒载"，荷载有了
    着落，主梁不再被迫跨越整跨。索力仍随 ez 单调变化，符合扇形索面的规律。
    """
    node = {n.id: n for n in model.nodes}
    kind_of = {t.id: t.kind for t in model.etypes}

    cables: list[tuple[int, int, float]] = []      # (elem, 梁侧锚点, ez)
    for e in model.elements:
        if kind_of.get(e.etype) != "CABLE":
            continue
        a, b = (e.n1, e.n2) if node[e.n1].z <= node[e.n2].z else (e.n2, e.n1)
        dz = node[b].z - node[a].z
        if abs(dz) < 1e-9:
            raise ValueError(f"索 {e.id} 两端等高，竖向分量退化")
        na, nb = node[a], node[b]
        L = ((nb.x - na.x) ** 2 + (nb.y - na.y) ** 2 + (nb.z - na.z) ** 2) ** 0.5
        cables.append((e.id, a, dz / L))

    # 退化路径：没给支座位置时按锚点受荷宽度分配（保留给单元测试/旧调用）
    if not x_supports or len(x_supports) < 2:
        out: dict[int, float] = {}
        for eid, a, ez in cables:
            out[eid] = q_deck * (trib_map or {}).get(a, 0.0) / ez
        return out

    node_at_x = {round(n.x, 9): n.id for n in model.nodes}
    out = {}
    spans = list(zip(x_supports[:-1], x_supports[1:]))
    for lo, hi in spans:
        group = [(eid, ez) for eid, a, ez in cables
                 if lo - 1e-9 <= node[a].x <= hi + 1e-9]
        if not group:
            continue
        # 支座断面处的荷载由支座直接承担：该节点 trib 有一半落在本跨
        w_span = q_deck * (hi - lo)
        if trib_map:
            for x_sup in (lo, hi):
                nid = node_at_x.get(round(x_sup, 9))
                if nid is not None:
                    w_span -= q_deck * trib_map.get(nid, 0.0) / 2.0
        each = max(w_span, 0.0) / len(group)
        for eid, ez in group:
            out[eid] = each / ez
    return out


def cable_break_load(cable_area_mm2: float, fpu: float = 1860e6) -> float:
    """单索破断力 N（fpu 默认 1860 MPa 级钢绞线，GB/T 5224）。"""
    return cable_area_mm2 * 1e-6 * fpu
