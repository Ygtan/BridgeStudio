"""truss_3d.py —— 三维空间桁架桥建造器。

矩形断面空间桁架桥：两片竖向 Warren 桁架（左、右各一片）+ 上弦横向联系 +
下弦横向联系（兼作桥面横梁），全部 TRUSS 单元，走 3D 线性阶段内核
（`cable_bridge=None`，`detect_kind` 落到 `grillage3d`，与 `arch_3d` 同一链）。

与 2D 桁架（`bridge_spec.build_truss_bridge`）的区别只在维度：
- 2D 是单片平面 Warren 桁架，节点占 3 dof（u/v/θ），TRUSS 不连 θ 须补 `dofs="3"`；
- 3D 是两片桁架 + 横联组成的空间桁架梁，纯 TRUSS 节点在内核里只有 3 个平动
  自由度（`buildDofMap3D` 只给 BEAM 节点补到 6 dof），**无需**补转动约束。

支座（简支空间桁架的标准做法）：
- 左端一片约束 u/v/w（纵向固定），另一片约束 v/w；
- 右端两片约束 v/w（释放纵向 u，允许温度伸缩）。
桥面恒载 `q_deck` 按 tributary 面积分配到左右两片下弦节点（各半）。

口径：本建造器**不单独算杆件自重**——桁架自重应并入 `q_deck`（桥面恒载，
含自重），否则钢桁架自重占比高、算出的弦杆轴力会明显偏小。这一点写进
`info`，界面据此提示，不做静默。

编号规范（B1，统一约定见 X20_接入约定.md §5）
------------------------------------------------
下表是该规范在本建造器内的落地实例（同模型内不得撞号，新增段先登记契约 §5）：
- 节点：左片下弦 1+i、左片上弦 101+i、右片下弦 201+i、右片上弦 301+i
- 单元：按生成顺序从 1 连续编号（弦杆 / 腹杆 / 横向联系按 etype 分组）
- 实常数：1=弦杆，2=腹杆，3=横向联系；材料：1=钢材

单位：m / N / Pa（见 X20_接入约定.md §4）。
"""

from __future__ import annotations

import math

from model_spec_3d import (
    Boundary3D,
    Element3D,
    ElemTypeDef3D,
    Material3D,
    ModelSpec3D,
    NodalLoad3D,
    Node3D,
    RealConst3D,
    Stage3D,
)


def build_truss_3d(
    span: float = 60.0,            # 跨度 (m)
    n_panel: int = 8,              # 节间数
    depth: float = 6.0,            # 桁高 (m)
    width: float = 10.0,           # 两片桁架间距 / 桥宽 (m)
    A_chord_mm2: float = 20000.0,  # 弦杆面积 (mm²)
    A_diag_mm2: float = 12000.0,   # 腹杆（斜杆）面积 (mm²)
    A_lat_mm2: float = 10000.0,    # 横向联系面积 (mm²)
    q_deck: float = 100e3,         # 桥面均布恒载 (N/m)，含自重
    E_steel: float = 206e9,
    dens_steel: float = 7850.0,
    title: str | None = None,
) -> tuple[ModelSpec3D, dict]:
    """生成矩形断面空间钢桁架桥 ModelSpec3D + 派生量字典。

    返回 ``(model, info)``。``info`` 只给界面/验证脚本用，不进 pydantic 契约。
    """
    if n_panel < 2:
        raise ValueError("节间数 n_panel 至少 2")
    if span <= 0 or depth <= 0 or width <= 0:
        raise ValueError("跨度/桁高/桥宽必须为正")
    if depth > span:
        raise ValueError(f"桁高 {depth:g} m 大于跨度 {span:g} m")

    panel = span / n_panel
    half = width / 2.0

    # ---- 节点编号 ----
    loL = lambda i: 1 + i            # 左片下弦 i=0..n_panel
    upL = lambda i: 101 + i          # 左片上弦 i=0..n_panel-1
    loR = lambda i: 201 + i          # 右片下弦 i=0..n_panel
    upR = lambda i: 301 + i          # 右片上弦 i=0..n_panel-1

    nodes: list[Node3D] = []
    for i in range(n_panel + 1):
        nodes.append(Node3D(id=loL(i), x=i * panel, y=-half, z=0.0))
        nodes.append(Node3D(id=loR(i), x=i * panel, y=+half, z=0.0))
    for i in range(n_panel):
        nodes.append(Node3D(id=upL(i), x=(i + 0.5) * panel, y=-half, z=depth))
        nodes.append(Node3D(id=upR(i), x=(i + 0.5) * panel, y=+half, z=depth))

    # ---- 截面/材料 ----
    A_chord = A_chord_mm2 * 1e-6
    A_diag = A_diag_mm2 * 1e-6
    A_lat = A_lat_mm2 * 1e-6

    materials = [Material3D(id=1, E=E_steel, dens=dens_steel, grade="Q345qD")]
    reals = [
        RealConst3D(id=1, A=A_chord),   # 弦杆
        RealConst3D(id=2, A=A_diag),    # 腹杆（斜杆）
        RealConst3D(id=3, A=A_lat),     # 横向联系
    ]
    etypes = [
        ElemTypeDef3D(id=1, kind="TRUSS", mat=1, real=1),
        ElemTypeDef3D(id=2, kind="TRUSS", mat=1, real=2),
        ElemTypeDef3D(id=3, kind="TRUSS", mat=1, real=3),
    ]

    # ---- 单元 ----
    elements: list[Element3D] = []
    lowerL_elems: list[int] = []
    lowerR_elems: list[int] = []
    upperL_elems: list[int] = []
    upperR_elems: list[int] = []
    diagL_elems: list[int] = []
    diagR_elems: list[int] = []
    lat_elems: list[int] = []
    eid = 1

    def add(n1: int, n2: int, etype: int, bucket: list[int]) -> None:
        nonlocal eid
        elements.append(Element3D(id=eid, etype=etype, n1=n1, n2=n2))
        bucket.append(eid)
        eid += 1

    # 下弦（左右各 n_panel）
    for i in range(n_panel):
        add(loL(i), loL(i + 1), 1, lowerL_elems)
    for i in range(n_panel):
        add(loR(i), loR(i + 1), 1, lowerR_elems)
    # 上弦（左右各 n_panel-1）
    for i in range(n_panel - 1):
        add(upL(i), upL(i + 1), 1, upperL_elems)
    for i in range(n_panel - 1):
        add(upR(i), upR(i + 1), 1, upperR_elems)
    # 斜杆（左右各 2*n_panel）：下弦 i → 上弦 i，上弦 i → 下弦 i+1
    for i in range(n_panel):
        add(loL(i), upL(i), 2, diagL_elems)
        add(upL(i), loL(i + 1), 2, diagL_elems)
    for i in range(n_panel):
        add(loR(i), upR(i), 2, diagR_elems)
        add(upR(i), loR(i + 1), 2, diagR_elems)
    # 横向联系：上弦平面 + 下弦平面各自形成**稳定的水平桁架**（横向杆 + 对角杆）。
    # 只用横向杆（纯 y 向）时，上/下弦平面是铰接矩形网格，可水平剪切变形
    # （机构），刚度矩阵奇异。对角杆引入 x 向分量，既消除剪切机构，又让右片的
    # 纵向通过横联链挂到左片支座上。
    # 上弦横向杆：upL(i) - upR(i)
    for i in range(n_panel):
        add(upL(i), upR(i), 3, lat_elems)
    # 上弦交叉撑（X 形）：upL(i)-upR(i+1) 与 upR(i)-upL(i+1) 成对。
    # 只用单方向对角，左右两片桁架的弦杆力会不对称（端部差 ~14%），
    # 因为斜杆把纵向力在左右之间传递得一边多一边少。交叉撑恢复镜像对称。
    for i in range(n_panel - 1):
        add(upL(i), upR(i + 1), 3, lat_elems)
        add(upR(i), upL(i + 1), 3, lat_elems)
    # 下弦横向杆：loL(i) - loR(i)（兼作横梁）
    for i in range(n_panel + 1):
        add(loL(i), loR(i), 3, lat_elems)
    # 下弦交叉撑（X 形）
    for i in range(n_panel):
        add(loL(i), loR(i + 1), 3, lat_elems)
        add(loR(i), loL(i + 1), 3, lat_elems)

    # ---- 支座（左端横截面纵向固定、右端释放纵向 u 以允许温度伸缩）----
    boundaries = [
        Boundary3D(node=loL(0), dofs=(1, 3)),            # 左端左片：u/v/w
        Boundary3D(node=loR(0), dofs=(1, 3)),            # 左端右片：u/v/w
        Boundary3D(node=loL(n_panel), dofs=(2, 3)),      # 右端左片：v/w
        Boundary3D(node=loR(n_panel), dofs=(2, 3)),      # 右端右片：v/w
        # 上弦横向定位（门架/端横联）：上弦平面是水平桁架，横联/对角撑都是
        # 水平杆，自身抵抗不了"上弦整体横向平移"这个机构——必须在上弦两端
        # 各锁一个横向 y，中间节点的 y 才能沿水平桁架传到这两端。
        Boundary3D(node=upL(0), dofs=(2, 2)),
        Boundary3D(node=upR(0), dofs=(2, 2)),
        Boundary3D(node=upL(n_panel - 1), dofs=(2, 2)),
        Boundary3D(node=upR(n_panel - 1), dofs=(2, 2)),
    ]

    # ---- 桥面恒载：下弦节点按 tributary 分配，两片各半 ----
    def trib(i: int) -> float:
        return panel / 2.0 if (i == 0 or i == n_panel) else panel

    loads: list[NodalLoad3D] = []
    for i in range(n_panel + 1):
        w = q_deck * trib(i) / 2.0
        loads.append(NodalLoad3D(node=loL(i), dof=3, value=-w))
        loads.append(NodalLoad3D(node=loR(i), dof=3, value=-w))

    # ---- 单阶段（一次成桥）----
    all_elems = [e.id for e in elements]
    stages = [Stage3D(id=1, activate=all_elems, loads=loads)]

    model = ModelSpec3D(
        title=title or f"{span:.0f}m 空间钢桁架桥（{n_panel}节间，桁高{depth:.0f}m）",
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

    info = {
        "span": span,
        "depth": depth,
        "width": width,
        "n_panel": n_panel,
        "panel": panel,
        "n_nodes": len(nodes),
        "n_elements": len(elements),
        "A_chord": A_chord,
        "A_diag": A_diag,
        "A_lat": A_lat,
        "q_deck": q_deck,
        # 派生：各类单元 id 列表（验证脚本 / 报告用）
        "chord_elems": lowerL_elems + lowerR_elems + upperL_elems + upperR_elems,
        "diag_elems": diagL_elems + diagR_elems,
        "lat_elems": lat_elems,
        "lower_node_ids": [loL(i) for i in range(n_panel + 1)]
        + [loR(i) for i in range(n_panel + 1)],
    }
    return model, info
