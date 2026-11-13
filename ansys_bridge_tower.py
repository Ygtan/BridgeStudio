#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F 阶段：带**真实竖直塔柱**的斜拉桥 3D 同源导出。

与 ``ansys_bridge_cable.build_cable_stayed_3d``（塔顶刚性锚点简化）平行、
完全独立。本模块把塔建成真实的竖直 BEAM 柱，塔底固结，索锚在塔身不同
高度，塔梁分离（塔底节点不与主梁共节点）。

竖直 BEAM 的局部坐标问题由 ``ansys_bridge_cable.spec_mixed_to_apdl`` 中
新增的**自动方向节点 K** 解决：检测到纯竖直 BEAM 时，ANSYS 侧自动在
I 点全局 +Y 方向生成 K 节点并用 ``E,I,J,K`` 定义单元，使 BEAM4 的
局部 y/z 与 FEM-Eigen 3D 一致（实测 IY/IZ 不再互换）。FEM-Eigen 内核
零改动，既有 CB3D03/CB3D04 的 APDL 逐字节不变。

    build_cable_stayed_tower_3d() -> ModelSpec3D
        -> to_dat(compact=False)       FEM-Eigen 3D ``example_3d_report.exe``
        -> spec_mixed_to_apdl()        ANSYS 18.0 ``BEAM4 + LINK180``
        -> parse/compare_mixed()       三方对照报告

坐标系：X 纵向 / Y 横向 / Z 竖向（向上为正）；竖向荷载 = DOF3 = ANSYS FZ。
"""
from __future__ import annotations

from ansys_bridge_cable import (  # noqa: E402
    parse_fem_report_mixed, compare_mixed, spec_mixed_to_apdl,
)
from ansys_bridge_3d import box_section_3d, parse_ansys_csv_3d  # noqa: E402
from model_spec_3d import (  # noqa: E402
    Boundary3D, Element3D, ElemTypeDef3D, Material3D, ModelSpec3D,
    Node3D, NodalLoad3D, RealConst3D, validate_model_3d,
)

__all__ = [
    "build_cable_stayed_tower_3d", "spec_mixed_to_apdl",
    "parse_fem_report_mixed", "parse_ansys_csv_3d", "compare_mixed",
]


def _tower_section(b: float, h: float, E: float, nu: float) -> dict:
    """实心矩形塔柱截面 -> BEAM 实常数字段。"""
    A = b * h
    IY = b * h ** 3 / 12.0          # 绕局部 y（全局 X）弯曲
    IZ = h * b ** 3 / 12.0          # 绕局部 z（全局 Y）弯曲
    return dict(
        A=A, IY=IY, IZ=IZ,
        J=0.229 * b * h ** 3 if b <= h else 0.229 * h * b ** 3,
        G=E / (2.0 * (1.0 + nu)),
        WT=IY / (h / 2.0), WY=IZ / (b / 2.0),
        WB=IY / (h / 2.0), AW=A, WTORS=0.0,
    )


def build_cable_stayed_tower_3d(
    *,
    span: float = 60.0,
    n_elem: int = 12,
    n_tower_seg: int = 6,
    tower_z: float = 18.0,
    B: float = 8.0, h: float = 2.0,
    tw: float = 0.02, tf: float = 0.025,
    E_deck: float = 3.45e10, nu: float = 0.2,
    tower_b: float = 3.0, tower_h: float = 3.0,
    cable_area: float = 0.004,
    E_cable: float = 1.95e11,
    udl_kN: float = 20.0,
    title: str = "独塔斜拉桥 3D 真实塔柱（BEAM 主梁/塔柱 + CABLE 斜拉索）",
) -> ModelSpec3D:
    """单塔双跨斜拉桥：水平主梁 BEAM + **竖直塔柱 BEAM** + 扇形斜拉索 CABLE。

    节点编号：主梁 1..n+1；塔底 n+2；塔身 n+3..n+2+n_tower_seg。
    单元编号：主梁 1..n；塔柱 n+1..n+n_tower_seg；索其后 6 根。
    塔底节点全约束（塔梁分离，基础固结）；主梁左右端按 C 阶段 3D 稳定
    约束方式锁自由度；塔不占用主梁支座。
    """
    if n_elem < 6 or n_elem % 2:
        raise ValueError("n_elem 需为 >=6 的偶数（保证塔位落在节点上）")
    if n_tower_seg < 3:
        raise ValueError("n_tower_seg 需 >= 3（塔上要有多个锚固高度）")
    sec = box_section_3d(B, h, tw, tf, E_deck, nu)
    tsec = _tower_section(tower_b, tower_h, E_deck, nu)
    le = span / n_elem
    mid = n_elem // 2 + 1                        # 塔 x 位置对应的主梁节点
    x_tower = le * (mid - 1)

    nodes = [Node3D(id=i + 1, x=le * i, y=0.0, z=0.0)
             for i in range(n_elem + 1)]
    tower_base = n_elem + 2
    nodes.append(Node3D(id=tower_base, x=x_tower, y=0.0, z=0.0))
    tower_ids = [tower_base]
    for k in range(1, n_tower_seg + 1):
        nid = tower_base + k
        nodes.append(Node3D(id=nid, x=x_tower, y=0.0,
                            z=tower_z * k / n_tower_seg))
        tower_ids.append(nid)

    elements = [Element3D(id=i + 1, etype=1, n1=i + 1, n2=i + 2)
                for i in range(n_elem)]
    for k in range(n_tower_seg):
        elements.append(Element3D(id=n_elem + 1 + k, etype=2,
                                  n1=tower_ids[k], n2=tower_ids[k + 1]))

    # 塔上三个锚固高度（取 1/2、2/3、1 塔高附近的塔身节点），左右对称布索
    hi = [tower_ids[max(1, round(n_tower_seg * f))]
          for f in (0.5, 2.0 / 3.0, 1.0)]
    left_anchor = [mid - 4, mid - 2, mid - 1]
    right_anchor = [mid + 1, mid + 2, mid + 4]
    left_anchor = [a for a in left_anchor if 1 <= a <= n_elem + 1]
    right_anchor = [a for a in right_anchor if 1 <= a <= n_elem + 1]
    # 外侧索锚在高处、内侧索锚在低处
    cable_pairs = [(left_anchor[j], hi[2 - j]) for j in range(len(left_anchor))]
    cable_pairs += [(right_anchor[j], hi[j]) for j in range(len(right_anchor))]
    eid = n_elem + n_tower_seg + 1
    for deck_node, tower_node in cable_pairs:
        elements.append(Element3D(id=eid, etype=3, n1=tower_node, n2=deck_node))
        eid += 1

    boundaries = [
        Boundary3D(node=1, dofs=(1, 4), value=0.0),           # UX UY UZ ROTX
        Boundary3D(node=n_elem + 1, dofs=(2, 3), value=0.0),   # UY UZ
        Boundary3D(node=tower_base, dofs=(1, 6), value=0.0),   # 塔底固结 6DOF
    ]
    q = udl_kN * 1000.0
    loads = [NodalLoad3D(node=i + 1, dof=3,
                         value=-q * le * (0.5 if i in (0, n_elem) else 1.0))
             for i in range(n_elem + 1)]

    model = ModelSpec3D(
        title=title,
        nodes=nodes,
        materials=[Material3D(id=1, E=E_deck),
                   Material3D(id=2, E=E_cable)],
        reals=[
            RealConst3D(id=1, A=sec["A"], IY=sec["IY"], IZ=sec["IZ"],
                        J=sec["J"], G=sec["G"], WT=sec["WT"],
                        WB=sec["WB"], WY=sec["WY"], WTORS=sec["WTORS"]),
            RealConst3D(id=2, A=tsec["A"], IY=tsec["IY"], IZ=tsec["IZ"],
                        J=tsec["J"], G=tsec["G"], WT=tsec["WT"],
                        WB=tsec["WB"], WY=tsec["WY"], WTORS=tsec["WTORS"]),
            RealConst3D(id=3, A=cable_area),
        ],
        etypes=[ElemTypeDef3D(id=1, kind="BEAM", mat=1, real=1),
                ElemTypeDef3D(id=2, kind="BEAM", mat=1, real=2),
                ElemTypeDef3D(id=3, kind="CABLE", mat=2, real=3)],
        elements=elements,
        boundaries=boundaries,
        loads=loads,
    )
    errs, _ = validate_model_3d(model)
    if errs:
        raise ValueError("ModelSpec3D 校验未通过: " + "; ".join(errs))
    return model
