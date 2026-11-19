"""combo_3d.py
==============
斜拉-悬索协作体系（组合体系）三维建造器。

为什么复用悬索建造器而不是从头重写
------------------------------------
内核侧阶段一（``cable_solver.cpp`` 的 ``SYSTEM=COOP``）验证的协作口径是：

    **完整悬索主体（主缆全主跨 + 吊索） + 塔顶附近追加的少量斜拉索**

冻结算例 ``FEM-Eigen/tests/benchmark/combo_system_3d/cases/combo_case.dat``
里斜拉索只有 4 根（每塔 1 主跨侧 + 1 边跨侧），主缆 104 段、吊索 101 根。
所以本建造器**不重写那 600 行悬索主体**，而是：

    1. 调 :func:`build_suspension_3d` 得到悬索 ``ModelSpec3D``；
    2. 从既有节点中定位梁节点序列与两塔塔顶节点（不新建节点）；
    3. 按 §3.7 口径参数把斜拉索锚点离散到梁节点，分配新单元号；
    4. 追加 ``CableSpec3D`` 目标索力，置 ``cable_bridge.cooperative = True``。

这样悬索主体的几何、编号、边界与既有算例完全一致，协作能力是**纯追加**，
缺省不触发时（本建造器不被调用）零污染。

§3.7 口径参数（定性词一律参数化，不硬编码）
--------------------------------------------
``susp_fraction``      主跨悬索段占比（默认 0.5，居中布置）；两端各留
                       ``(1-susp_fraction)/2`` 作斜拉段。
``side_stay_ratio``    边跨斜拉段 / 主跨单侧斜拉段（默认 0.5，体现
                       「边跨约为主跨斜拉段一半略短」）。与 1.5× 的边跨
                       目标索力配合，使塔两侧水平分量基本平衡（§3.7
                       ``r_tower`` 判据）；默认几何与阶段一冻结算例
                       （主跨锚点 129 m / 边跨 63.5 m）同一量级。
``tower_height_ratio`` 塔高 / 主跨；``None`` = 沿用悬索自动塔高（= 矢高 +
                       净空），显式给值时按 0.10~0.25 校验并覆盖。
``plane_count``        索面数。当前仅 ``1`` 放行——与阶段一内核冻结算例
                       ``susp_planes=1`` 一致；``2`` 需先扩 mirror 与内核
                       benchmark，显式拒绝而非静默降级成单索面。
``coop_boundary_node`` 协作分界梁节点；0 = 自动取主跨斜拉段末端节点。
                       非 0 时必须是实际梁节点，且落在主跨内——分界必须与
                       索锚点离散对齐，不得静默取整到别的节点。

编号（X20 接入约定 §5.1 / §5.2）
---------------------------------
节点、材料、实常数、单元主体全部来自 ``build_suspension_3d``；斜拉索作为
新组件组，按 §5.1 非主要构件的十位跳转规则在既有最大单元号之后分配，
不与主缆 / 吊索 / 梁单元共用号段。详见 ``X20_接入约定.md`` §5.2。

单位：m / N / Pa。
"""

from __future__ import annotations

from model_spec_3d import CableSpec3D, Element3D, ModelSpec3D
from suspension_3d import build_suspension_3d

#: 体系名 → 中文（界面与报告共用，与悬索建造器同一套词）
COMBO_LABEL = "斜拉-悬索协作体系桥"


def build_combo_3d(
    main_span: float = 1000.0,
    susp_fraction: float = 0.5,
    side_stay_ratio: float = 0.5,
    tower_height_ratio: float | None = None,
    plane_count: int = 1,
    coop_boundary_node: int = 0,
    stay_per_side: int = 1,
    stay_tension_main: float = 2.0e6,
    stay_tension_side: float = 3.0e6,
    title: str = "",
    **susp_kwargs,
) -> tuple[ModelSpec3D, dict]:
    """生成协作体系 ``ModelSpec3D`` 与派生量字典。

    ``susp_kwargs`` 原样透传给 :func:`build_suspension_3d`（``system`` /
    ``sag_ratio`` / ``side_span`` / ``panel`` / ``q_deck`` / ``jack_stages`` …），
    使分级顶推、双塔三跨等既有悬索能力在协作体系里继续可用。
    """
    # ---------------------------------------------------------- 口径校验
    if not 0.05 <= susp_fraction <= 0.95:
        raise ValueError(
            f"susp_fraction 应在 0.05~0.95 之间（主跨悬索段居中，两端留斜拉段），"
            f"收到 {susp_fraction}")
    if not 0.0 < side_stay_ratio <= 1.0:
        raise ValueError(
            f"side_stay_ratio 应在 (0,1] 之间（边跨略短于主跨斜拉合计），"
            f"收到 {side_stay_ratio}")
    if tower_height_ratio is not None and not 0.08 <= tower_height_ratio <= 0.30:
        raise ValueError(
            f"tower_height_ratio 应介于 0.08~0.30（约 1/10~1/4），"
            f"收到 {tower_height_ratio}")
    if plane_count != 1:
        raise NotImplementedError(
            f"plane_count={plane_count} 尚未放行：阶段一内核冻结算例为单索面"
            "（susp_planes=1）；双索面需先扩展镜像建造与内核 benchmark，"
            "不静默降级成单索面")
    if stay_per_side < 1:
        raise ValueError(f"stay_per_side 至少为 1，收到 {stay_per_side}")
    if stay_tension_main <= 0.0 or stay_tension_side <= 0.0:
        raise ValueError("协作斜拉索目标索力必须为正（内核要求显式给目标力）")
    if main_span <= 0.0:
        raise ValueError("主跨必须为正")

    # ---------------------------------------------------------- 悬索主体
    sk = dict(susp_kwargs)
    if tower_height_ratio is not None:
        # z_top = z_base + tower_height；z_base = -tower_below。
        # 显式给 ratio 时按主跨换算成塔高透传；None 时完全走悬索自动塔高。
        below = abs(float(sk.get("tower_below", 8.0)))
        sk["tower_height"] = float(tower_height_ratio) * main_span + below
    model, smeta = build_suspension_3d(main_span=main_span, **sk)

    L_main = float(smeta["main_span"])
    L_side = float(smeta["side_span"])
    L_tot = float(smeta["total_length"])
    cb = model.cable_bridge
    if cb is None or not cb.system:
        raise RuntimeError("悬索主体未生成 *CABLE_BRIDGE 悬索工况，无法建协作体系")

    # ---------------------------------------------------------- 梁节点序列
    # 梁节点：y≈0、z≈0、0<=x<=L_tot。锚碇在 x<0 / x>L_tot，塔底 z<0，
    # 塔顶与主缆自建节点 z>0，均被过滤掉。
    deck = sorted(
        (n for n in model.nodes
         if abs(n.y) < 1e-9 and abs(n.z) < 1e-9
         and -1e-9 <= n.x <= L_tot + 1e-9),
        key=lambda n: n.x,
    )
    if len(deck) < 5:
        raise RuntimeError("悬索主体的梁节点不足，无法离散斜拉索锚点")
    deck_ids = [n.id for n in deck]
    deck_x = [n.x for n in deck]

    x_t1 = L_side                 # 塔1 在梁上的 x
    x_t2 = L_side + L_main        # 塔2
    tower_nodes = list(smeta["tower_nodes"])   # 主缆端塔顶节点（斜拉索塔上端）

    def nearest_deck(x: float) -> tuple[int, float]:
        idx = min(range(len(deck_x)), key=lambda i: abs(deck_x[i] - x))
        return deck_ids[idx], deck_x[idx]

    # ---------------------------------------------------------- 分界节点
    # 主跨单侧斜拉段长度、边跨斜拉段长度（§3.7 口径）
    L_stay_main_one = (1.0 - susp_fraction) * L_main / 2.0
    L_stay_side = side_stay_ratio * L_stay_main_one
    if L_stay_main_one <= 0.0:
        raise ValueError("susp_fraction=1 时主跨无斜拉段，不是协作体系")

    def anchor_offset(span: float, k: int) -> float:
        """第 k 根索锚点在斜拉段内的位置（段内均布；1 根时落在段中点）。"""
        return span * (k - 0.5) / stay_per_side

    if coop_boundary_node:
        if coop_boundary_node not in deck_ids:
            raise ValueError(
                f"coop_boundary_node={coop_boundary_node} 不是梁节点（须与索锚点离散对齐）")
        bx = deck_x[deck_ids.index(coop_boundary_node)]
        if not (x_t1 + 1e-9 < bx < x_t2 - 1e-9):
            raise ValueError(
                f"coop_boundary_node={coop_boundary_node} (x={bx:.3f}) 不在主跨内"
                f" ({x_t1:.3f}, {x_t2:.3f})")
    else:
        # 自动分界 = 最远的主跨斜拉锚点（保证分界与索锚点离散对齐）
        boundary_node, bx = nearest_deck(
            x_t1 + anchor_offset(L_stay_main_one, stay_per_side))
        coop_boundary_node = boundary_node

    # ---------------------------------------------------------- 斜拉索布设
    # 每塔每侧 stay_per_side 根：主跨侧朝跨中、边跨侧朝桥台。
    # 锚点按斜拉段长度等分，离散到最近梁节点；同一侧不重复用节点。
    # 4 组：塔1主跨侧、塔1边跨侧、塔2主跨侧、塔2边跨侧；每组 stay_per_side 根。
    # 斜拉索数少（<50），按 §5.1 非主要构件十位跳转到既有最大单元号之后。
    max_eid = max((e.id for e in model.elements), default=0)
    next_start = (max_eid // 10 + 1) * 10 + 1
    stay_eids = list(range(next_start, next_start + 4 * stay_per_side))

    stay_cables: list[CableSpec3D] = []
    stay_main_elems: list[int] = []
    stay_side_elems: list[int] = []
    stay_detail: list[dict] = []
    eid_iter = iter(stay_eids)

    for ti, x_t in enumerate((x_t1, x_t2)):
        tower_node = tower_nodes[ti]
        sign_main = 1.0 if ti == 0 else -1.0     # 主跨侧朝向跨中
        # 主跨侧锚点：距塔 (k/stay_per_side)·L_stay_main_one
        for k in range(1, stay_per_side + 1):
            x_anchor = x_t + sign_main * anchor_offset(L_stay_main_one, k)
            nid, ax = nearest_deck(x_anchor)
            eid = next(eid_iter)
            model.elements.append(
                Element3D(id=eid, etype=4, n1=tower_node, n2=nid))
            stay_cables.append(CableSpec3D(
                elem=eid, anchor=nid, tension=float(stay_tension_main)))
            stay_main_elems.append(eid)
            stay_detail.append({"tower": ti + 1, "side": "main", "elem": eid,
                                "anchor": nid, "x": round(ax, 3),
                                "tension_N": float(stay_tension_main)})
        # 边跨侧锚点：距塔 (k/stay_per_side)·L_stay_side，朝桥台
        sign_side = -1.0 if ti == 0 else 1.0
        for k in range(1, stay_per_side + 1):
            x_anchor = x_t + sign_side * anchor_offset(L_stay_side, k)
            nid, ax = nearest_deck(x_anchor)
            eid = next(eid_iter)
            model.elements.append(
                Element3D(id=eid, etype=4, n1=tower_node, n2=nid))
            stay_cables.append(CableSpec3D(
                elem=eid, anchor=nid, tension=float(stay_tension_side)))
            stay_side_elems.append(eid)
            stay_detail.append({"tower": ti + 1, "side": "side", "elem": eid,
                                "anchor": nid, "x": round(ax, 3),
                                "tension_N": float(stay_tension_side)})

    # 分界节点必须与实际斜拉锚点离散对齐（不能静默取整到别的节点）
    stay_anchors = {d["anchor"] for d in stay_detail if d["side"] == "main"}
    if coop_boundary_node not in stay_anchors and stay_per_side > 1:
        raise ValueError(
            f"coop_boundary_node={coop_boundary_node} 未命中主跨斜拉锚点集合")

    # ---------------------------------------------------------- 协作标记
    cb.cables = list(cb.cables) + stay_cables
    cb.cooperative = True

    if not title:
        title = (f"{COMBO_LABEL}　主跨 {L_main:.0f} m　"
                 f"悬索段 {100 * susp_fraction:.0f}%")

    meta = dict(smeta)
    meta.update({
        "title": title,
        "combo_system": True,
        "combo_label": COMBO_LABEL,
        # §3.7 口径参数
        "susp_fraction": susp_fraction,
        "side_stay_ratio": side_stay_ratio,
        "tower_height_ratio": (
            tower_height_ratio if tower_height_ratio is not None
            else (smeta["tower_top_z"] - smeta["tower_base_z"]) / L_main),
        "plane_count": plane_count,
        "coop_boundary_node": coop_boundary_node,
        "coop_boundary_x": round(bx, 3),
        # 斜拉索派生量
        "stay_per_side": stay_per_side,
        "stay_count": len(stay_cables),
        "stay_elems": stay_eids,
        "stay_main_elems": stay_main_elems,
        "stay_side_elems": stay_side_elems,
        "stay_tension_main": float(stay_tension_main),
        "stay_tension_side": float(stay_tension_side),
        "stay_detail": stay_detail,
        "L_stay_main_one": L_stay_main_one,
        "L_stay_side": L_stay_side,
        "n_elements": len(model.elements),
    })
    return model, meta


__all__ = ["COMBO_LABEL", "build_combo_3d"]
