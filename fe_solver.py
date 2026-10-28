"""有限元求解封装：基于 anastruct 的 2D 框架/梁单元刚度法求解。

输入 ModelSpec，输出节点坐标、竖向位移(挠度)、弯矩、支座反力与关键极值。
单位：长度 m，力 N，弯矩 N·m。弯矩按工程惯例翻转符号（下挠受拉为正）。
"""

from __future__ import annotations

from anastruct import SystemElements

from model_spec import ModelSpec
from section_lib import section_properties


def solve(spec: ModelSpec) -> dict:
    props = section_properties(spec.section)
    A, I = props["A"], props["I"]
    E = spec.E
    EA, EI = E * A, E * I
    L = spec.length
    n_elem = max(40, min(300, int(round(L * 8))))

    ss = SystemElements(EA=EA, EI=EI)
    xs = [L * i / n_elem for i in range(n_elem + 1)]
    elem_ids = []
    for i in range(n_elem):
        elem_ids.append(ss.add_element(location=[[xs[i], 0], [xs[i + 1], 0]], EA=EA, EI=EI))

    def node_at(pos):  # anastruct 节点 id 从 1 起
        nid = int(round(pos / L * n_elem)) if L > 0 else 0
        return min(max(nid, 0), n_elem) + 1

    for sup in spec.supports:
        nid = node_at(sup.position)
        if sup.type == "pinned":
            ss.add_support_hinged(nid)
        elif sup.type == "fixed":
            ss.add_support_fixed(nid)
        else:  # roller
            ss.add_support_roll(nid)

    for ld in spec.loads:
        if ld.type == "point":
            pos = ld.position if ld.position is not None else L / 2.0
            ss.point_load(node_at(pos), Fy=-ld.value * 1000.0)  # kN→N，向下为负
        else:  # udl
            start = ld.start if ld.start is not None else 0.0
            end = ld.end if ld.end is not None else L
            q = -ld.value * 1000.0  # N/m，向下为负
            for i, eid in enumerate(elem_ids):
                x0, x1 = xs[i], xs[i + 1]
                if min(end, x1) > max(start, x0):  # 区段有重叠
                    ss.q_load(q=q, element_id=eid, direction="element")

    ss.solve()

    disp = ss.get_node_displacements()
    deflections = [disp[i]["uy"] for i in range(n_elem + 1)]  # 列表 0 索引对应节点 i+1

    # 弯矩：沿各单元采样并拼接（翻转符号 → 工程惯例：下挠受拉为正）
    Xm, Mm = [], []
    for i, eid in enumerate(elem_ids):
        arr = [-v for v in ss.element_map[eid].bending_moment]
        m = len(arr)
        xa = [xs[i] + (xs[i + 1] - xs[i]) * k / (m - 1) for k in range(m)]
        if i < n_elem - 1:
            Xm += xa[:-1]
            Mm += arr[:-1]
        else:
            Xm += xa
            Mm += arr

    reactions = {}
    for sup in spec.supports:
        node = ss.reaction_forces.get(node_at(sup.position))
        if node is not None:
            reactions[node_at(sup.position)] = {"Fx": node.Fx, "Fy": node.Fy, "Ty": node.Tz}

    max_def = max(deflections, key=abs) if deflections else 0.0
    max_mom = max(Mm, key=abs) if Mm else 0.0

    return {
        "xs": xs,
        "deflections": deflections,     # m
        "xs_moment": Xm,
        "moments": Mm,                  # N·m（工程符号）
        "reactions": reactions,         # {node_id: {Fx,Fy,Ty}}
        "max_deflection": max_def,      # m
        "max_moment": max_mom,          # N·m
        "props": props,
        "EA": EA,
        "EI": EI,
        "n_elem": n_elem,
    }
