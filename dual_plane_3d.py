"""dual_plane_3d.py — 单索面 3D 模型 → 双索面 3D 模型的通用镜像。

为什么是镜像而不是改三个建造器
------------------------------
三个 3D 建造器（斜拉 / 悬索 / 拱）当前都是单索面：所有节点 y=0，主梁一条线。
若直接在建造器里铺开双索面，三处都要重复「双梁线 + 横梁 + 双塔/双拱肋 +
双索面 + 荷载分半 + 阶段映射」，且默认路径会被迫改签名。这里改为**在建造器
返回之后**做一次镜像：建造器保持原样，默认逐字节不变；只有显式 opt-in
（`cable_planes=2`）才调用本模块。

几何假设
--------
- 输入模型所有节点位于 y=0（单索面）；输出把原节点移到 y=-B/2，镜像节点
  放到 y=+B/2。若输入节点本就有 y 偏移，按 y±B/2 处理，同样成立。
- **主梁截面分半**：两条梁线各承担一半截面与一半恒载，总量守恒。
- 索 / 吊杆 / 拱肋**截面不分半**，数量翻倍（双索面本来就是两套索）。
- 单索面为消除平面外机构而加的纯横向约束 ``dofs=(2,2)`` 在双索面时取消：
  两片索面 + 横梁本身提供横向刚度；支座处的 (2,3) 约束保留。
- 横梁在梁线节点 x 处连接左右两侧，截面为主梁的一半、两向弯曲刚度均给出。

单位：m / N / Pa。
"""

from __future__ import annotations

import copy
import math
from typing import Any

from model_spec_3d import (
    Boundary3D,
    CableBridge3D,
    CableSpec3D,
    ElemLoad3D,
    Element3D,
    ElemTypeDef3D,
    Influence3D,
    Lane3D,
    ModelSpec3D,
    Node3D,
    NodalLoad3D,
    SelfWeight3D,
    SuspPlane3D,
)

#: 半截面字段：A / 惯性矩 / 截面模量 / 线质量。ISTRN（应变）、W（索重）、
#: HTH（理论厚度）不是"按条数分半"的量，保持不变。
_HALVE_FIELDS = (
    "A", "IY", "IZ", "J", "WT", "WB", "WY", "WTORS", "IW", "AW", "ADDMASS",
)


def _max_id(items: list[Any], default: int = 0) -> int:
    return max((int(getattr(x, "id")) for x in items), default=default)


def _planeout_nodes(boundaries: list[Boundary3D]) -> set[int]:
    """只挑出「节点上**仅有一个**纯 dof2 约束」的节点。

    锚碇节点通常有 (1,1)~(6,6) 六个独立约束，其中也含 (2,2)——那个不能删，
    否则主缆端节点不再全约束，内核会按「主缆没挂上锚碇」报错。支座 (2,3)
    也不是。只有孤立的 (2,2) 才是单索面为消除平面外机构加的人为约束。
    """
    by_node: dict[int, list[Boundary3D]] = {}
    for b in boundaries:
        by_node.setdefault(b.node, []).append(b)
    out: set[int] = set()
    for nid, bs in by_node.items():
        if len(bs) != 1:
            continue
        b = bs[0]
        if (tuple(b.dofs) == (2, 2)
                and abs(b.value) < 1e-12 and b.k == 0.0):
            out.add(nid)
    return out


def mirror_to_dual_plane(
    model: ModelSpec3D,
    info: dict[str, Any] | None = None,
    B: float = 12.0,
    *,
    kind: str = "cable_stayed",
    half_real_ids: tuple[int, ...] | None = None,
) -> tuple[ModelSpec3D, dict[str, Any]]:
    """把单索面模型镜像为双索面模型。

    Parameters
    ----------
    model
        建造器生成的单索面 ``ModelSpec3D``。**本函数不修改入参**。
    info
        建造器派生量字典；会被复制并追加双索面字段。
    B
        桥面全宽 m，两索面位于 ``y = ±B/2``。
    kind
        ``cable_stayed`` / ``suspension`` / ``arch_3d``，只用于 info 标注。
    half_real_ids
        需要分半的实常数编号。默认 ``(1,)``（主梁）；斜拉 / 悬索另传
        ``(1, 2)`` 把塔柱也分半。拱桥保持 ``(1,)``，拱肋不分半。

    Returns
    -------
    (new_model, new_info)
    """
    if not math.isfinite(B) or B <= 0.0:
        raise ValueError(f"桥面宽度 B 必须为正数，收到 {B!r}")
    half_ids = set(half_real_ids if half_real_ids is not None else (1,))

    out = copy.deepcopy(model)
    meta: dict[str, Any] = dict(info or {})

    node_off = _max_id(out.nodes) + 1
    elem_off = _max_id(out.elements) + 1
    orig_nodes = list(out.nodes)
    orig_elems = list(out.elements)
    node_by_id = {n.id: n for n in orig_nodes}

    # ---- 1. 实常数分半 + 横梁截面 ----
    for r in out.reals:
        if r.id in half_ids:
            for f in _HALVE_FIELDS:
                setattr(r, f, float(getattr(r, f, 0.0)) * 0.5)

    base_real = next((r for r in out.reals if r.id == 1), None)
    if base_real is None:
        raise ValueError("双索面镜像要求模型至少有一个编号为 1 的主梁实常数")
    cross_real = copy.deepcopy(base_real)
    cross_real.id = _max_id(out.reals) + 1
    for f in _HALVE_FIELDS:
        setattr(cross_real, f, float(getattr(cross_real, f, 0.0)) * 0.5)
    out.reals.append(cross_real)

    base_etype = next((t for t in out.etypes
                       if t.real == 1 and t.kind in ("BEAM", "BEAM7")), None)
    if base_etype is None:
        raise ValueError("双索面镜像找不到主梁单元类型（real=1 的 BEAM）")
    cross_etype_id = _max_id(out.etypes) + 1
    out.etypes.append(ElemTypeDef3D(
        id=cross_etype_id, kind=base_etype.kind,
        mat=base_etype.mat, real=cross_real.id,
    ))
    deck_etypes = {t.id for t in out.etypes if t.real == 1}

    # ---- 2. 节点镜像：原侧 y=-B/2，镜像侧 y=+B/2 ----
    half_b = 0.5 * B
    for n in orig_nodes:
        n.y = float(n.y) - half_b
    mirrored_nodes = [
        Node3D(id=n.id + node_off, x=n.x, y=n.y + B, z=n.z)
        for n in orig_nodes
    ]
    out.nodes = orig_nodes + mirrored_nodes

    # ---- 3. 单元镜像 ----
    mirrored_elems = [
        Element3D(id=e.id + elem_off, etype=e.etype,
                  n1=e.n1 + node_off, n2=e.n2 + node_off)
        for e in orig_elems
    ]
    out.elements = orig_elems + mirrored_elems

    # ---- 4. 横梁：沿每个梁线节点 x 连接左右索面 ----
    deck_nids = sorted(
        {nd for e in orig_elems if e.etype in deck_etypes for nd in (e.n1, e.n2)},
        key=lambda i: node_by_id[i].x,
    )
    cross_start = elem_off + _max_id(orig_elems) + 1
    cross_ids = [cross_start + i for i in range(len(deck_nids))]
    for cid, nid in zip(cross_ids, deck_nids):
        out.elements.append(Element3D(
            id=cid, etype=cross_etype_id, n1=nid, n2=nid + node_off,
        ))

    # ---- 5. 节点荷载：两侧各一半，总荷载守恒 ----
    out.loads = (
        [NodalLoad3D(node=ld.node, dof=ld.dof, value=ld.value * 0.5)
         for ld in model.loads]
        + [NodalLoad3D(node=ld.node + node_off, dof=ld.dof, value=ld.value * 0.5)
           for ld in model.loads]
    )

    # ---- 6. 边界：镜像；取消纯横向人为约束 ----
    # 悬索/斜拉的孤立 (2,2) 是单索面平面外机构约束，双索面可删；
    # 拱桥保留：本镜像只在**主梁**节点之间加横梁，拱肋横撑尚未建模，
    # 施工早期两片拱肋之间还没有横向联系，删掉 (2,2) 会直接矩阵奇异。
    drop_nodes = (set() if kind == "arch_3d"
                  else _planeout_nodes(model.boundaries))
    base_bnd = [b for b in model.boundaries if b.node not in drop_nodes]
    out.boundaries = base_bnd + [
        Boundary3D(node=b.node + node_off, dofs=b.dofs,
                   value=b.value, k=b.k)
        for b in base_bnd
    ]

    # ---- 7. 单元分布荷载 / 自重：两侧各一半 ----
    out.elem_loads = []
    for el in model.elem_loads:
        out.elem_loads.append(ElemLoad3D(
            e_from=el.e_from, e_to=el.e_to, dir=el.dir,
            q1=el.q1 * 0.5, q2=el.q2 * 0.5,
        ))
        out.elem_loads.append(ElemLoad3D(
            e_from=el.e_from + elem_off,
            e_to=None if el.e_to is None else el.e_to + elem_off,
            dir=el.dir, q1=el.q1 * 0.5, q2=el.q2 * 0.5,
        ))
    out.self_weights = []
    for sw in model.self_weights:
        out.self_weights.append(copy.deepcopy(sw))
        sw2 = copy.deepcopy(sw)
        if sw2.who.isdigit():
            sw2.who = str(int(sw2.who) + elem_off)
        out.self_weights.append(sw2)

    # ---- 8. 车道：两侧各承担一半横向布载（规范值不变，折减系数折半）----
    out.lanes = []
    for ln in model.lanes:
        left = copy.deepcopy(ln)
        left.reduction = float(ln.reduction) * 0.5
        right = copy.deepcopy(ln)
        right.nodes = [nd + node_off for nd in ln.nodes]
        right.reduction = float(ln.reduction) * 0.5
        out.lanes.extend((left, right))

    # ---- 9. 影响线测点镜像 ----
    out.influences = list(model.influences) + [
        Influence3D(node=inf.node + node_off, dof=inf.dof)
        for inf in model.influences
    ]

    # ---- 10. 施工阶段：激活/钝化、荷载、索力映射 ----
    # 横梁必须随其对应的主梁段逐根激活：若在首个主梁阶段一次性全部激活，
    # 尚未安装的主梁节点只连一根 y 向横梁，x/z 自由度无刚度 → 矩阵奇异。
    deck_elem_by_node: dict[int, list[int]] = {}
    for e in orig_elems:
        if e.etype in deck_etypes:
            deck_elem_by_node.setdefault(e.n1, []).append(e.id)
            deck_elem_by_node.setdefault(e.n2, []).append(e.id)
    first_stage_of_elem: dict[int, int] = {}
    for st in model.stages:
        for eid in st.activate:
            first_stage_of_elem.setdefault(eid, st.id)
    cross_by_stage: dict[int, list[int]] = {}
    for cid, nid in zip(cross_ids, deck_nids):
        cand = [first_stage_of_elem[d] for d in deck_elem_by_node.get(nid, [])
                if d in first_stage_of_elem]
        if cand:
            cross_by_stage.setdefault(min(cand), []).append(cid)

    out.stages = []
    for st in model.stages:
        s2 = copy.deepcopy(st)
        s2.activate = list(st.activate) + [e + elem_off for e in st.activate]
        s2.deactivate = list(st.deactivate) + [e + elem_off for e in st.deactivate]
        s2.activate = list(s2.activate) + cross_by_stage.get(st.id, [])
        s2.loads = (
            [NodalLoad3D(node=ld.node, dof=ld.dof, value=ld.value * 0.5)
             for ld in st.loads]
            + [NodalLoad3D(node=ld.node + node_off, dof=ld.dof,
                           value=ld.value * 0.5)
               for ld in st.loads]
        )
        s2.tensions = {
            **{e: t * 0.5 for e, t in st.tensions.items()},
            **{e + elem_off: t * 0.5 for e, t in st.tensions.items()},
        }
        s2.unstrained = {
            **dict(st.unstrained),
            **{e + elem_off: L for e, L in st.unstrained.items()},
        }
        for ps in s2.prestress:
            ps.elems = list(ps.elems) + [e + elem_off for e in ps.elems]
        out.stages.append(s2)
    # 无施工阶段时横梁默认全部存在（已在 out.elements 中），无需额外动作。

    # ---- 11. 成桥态工况：索力 + 悬索附加索面 ----
    if model.cable_bridge is not None:
        cb = copy.deepcopy(model.cable_bridge)
        orig_cables = list(model.cable_bridge.cables)
        cb.cables = (
            [CableSpec3D(elem=c.elem, anchor=c.anchor,
                         tension=(c.tension * 0.5 if c.tension > 0 else 0.0))
             for c in orig_cables]
            + [CableSpec3D(elem=c.elem + elem_off,
                           # 0 是「由几何自动判定」哨兵，不能加偏移，否则
                           # 会变成指向非端点的假锚点而被契约校验拦下。
                           anchor=(c.anchor + node_off if c.anchor > 0 else 0),
                           tension=(c.tension * 0.5 if c.tension > 0 else 0.0))
               for c in orig_cables]
        )
        if cb.system:
            cb.planes = [SuspPlane3D(
                tower1=cb.tower1 + node_off,
                tower2=cb.tower2 + node_off,
                main_cable=[e + elem_off for e in cb.main_cable],
                hangers=[e + elem_off for e in cb.hangers],
            )]
            if cb.deck_e1 < 1e11:
                cb.deck_e1 = elem_off + cb.deck_e1
        out.cable_bridge = cb

    # ---- 12. 汇总派生量 ----
    meta.update({
        "cable_planes": 2,
        "bridge_width": float(B),
        "dual_plane_kind": kind,
        "node_offset": node_off,
        "elem_offset": elem_off,
        "cross_beam_ids": cross_ids,
        "deck_node_ids": [nid for nid in deck_nids]
                         + [nid + node_off for nid in deck_nids],
        "n_nodes": len(out.nodes),
        "n_elements": len(out.elements),
        "n_stages": len(out.stages),
    })
    # 让 pydantic 校验引用一致性（节点/单元/阶段/工况不得指向不存在的 id）。
    validated = ModelSpec3D.model_validate(out.model_dump())
    return validated, meta
