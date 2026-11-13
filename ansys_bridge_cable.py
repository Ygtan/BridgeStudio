#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BridgeStudio 3D 斜拉桥 / 拱桥：混合单元（BEAM + CABLE/TRUSS）同源导出与校核。

与 ``ansys_bridge_3d.py``（纯 BEAM 三跨箱梁）平行、**完全独立**，不改动
C 阶段已验证的任何文件。本模块专门处理「同一 ModelSpec3D 里同时存在
梁单元与轴向杆单元」的情形：

    ModelSpec3D -> to_dat(compact=False)          FEM-Eigen 3D ``example_3d_report.exe``
               -> spec_mixed_to_apdl()           ANSYS 18.0 ``BEAM4 + LINK180``
               -> parse/compare                 三方对照报告

单元语义（本机 ANSYS 18.0 实测，见 ansys_probe/link_probe.py、arch_probe.py）：

  * FEM-Eigen 线性 3D 内核 ``fem_3d.hpp`` 按 ``BEAM / BEAM7 / 其它`` 分派，
    ``CABLE`` 与 ``TRUSS`` 都走 ``truss3DStiffness``，即 ``(EA/L)·c⊗c``，
    **不区分只拉不压、不读 L0、无垂度**。只受拉 / 悬链线 / Ernst 模量 /
    索力迭代属于 ``fem_3d_nl.hpp`` + ``cable_solver.exe`` 非线性链。
  * ANSYS ``LINK180`` 同为 3D 轴向杆（可拉可压），与该线性口径严格对应。
    实测最小混合模型：位移、梁端力、杆轴力全部逐位一致，0 warning。
  * ANSYS ``BEAM4`` 与 FEM-Eigen 3D 同为 12x12 Euler-Bernoulli 空间梁；
    ``BEAM188`` 是 Timoshenko 一阶剪切，差异已在 C 阶段单独量化。

局部坐标陷阱（实测）：``beam3DTransformation`` 用 ``ez_ref = global Z``
（若单元轴平行 Z 则改用 global Y），``ey = ez_ref × ex``。ANSYS BEAM4
默认规则与之一致，因此 **X-Z 平面内的斜梁逐位一致**；但**纯竖直 BEAM**
两边局部 y 轴会差 90°（IY/IZ 互换）导致不一致。本模块的两个构造器都
避免竖直梁：竖向构件一律用轴向杆（LINK180 / TRUSS），拱肋/塔柱用斜梁。

坐标系：X 纵向 / Y 横向 / Z 竖向（向上为正）；竖向荷载 = DOF3 = ANSYS FZ。
"""
from __future__ import annotations

from pathlib import Path

from model_spec_3d import (  # noqa: E402
    Boundary3D, Element3D, ElemTypeDef3D, Material3D, ModelSpec3D,
    Node3D, NodalLoad3D, RealConst3D, validate_model_3d,
)
from ansys_bridge_3d import box_section_3d, parse_ansys_csv_3d  # noqa: E402

DOF3_NAME = {1: "UX", 2: "UY", 3: "UZ",
             4: "ROTX", 5: "ROTY", 6: "ROTZ"}
DOF3_LOAD = {1: "FX", 2: "FY", 3: "FZ",
             4: "MX", 5: "MY", 6: "MZ"}
DOF3_RF = {1: "FX", 2: "FY", 3: "FZ",
           4: "MX", 5: "MY", 6: "MZ"}

#: BEAM4 i 端 6 分量对应的 SMISC 编号（实测标定）
BEAM4_SMISC = (1, 2, 3, 4, 5, 6)
FORCE_KEYS = ("N", "Vy", "Vz", "T", "My", "Mz")
AXIAL_KINDS = ("TRUSS", "CABLE")


# ----------------------------------------------------------------- 构造器
def build_cable_stayed_3d(
    *,
    span: float = 60.0,
    n_elem: int = 12,
    B: float = 8.0, h: float = 2.0,
    tw: float = 0.02, tf: float = 0.025,
    E_deck: float = 3.45e10, nu: float = 0.2,
    tower_z: float = 18.0,
    cable_area: float = 0.004,
    E_cable: float = 1.95e11,
    cable_dens: float = 7850.0,
    udl_kN: float = 20.0,
    title: str = "独塔斜拉桥 3D 混合单元（BEAM 主梁 + CABLE 斜拉索）",
) -> ModelSpec3D:
    """独塔双跨斜拉桥：水平主梁 BEAM + 扇形斜拉索 CABLE。

    塔按**刚性锚点**简化（塔顶节点全约束），不建塔柱梁单元——本轮校核
    对象是「同一模型里混合 BEAM + 轴向杆」的建模/求解/对标链路，不是塔梁
    相互作用。索两端 z 不等高（塔顶 / 梁面），满足 CABLE 的几何校验。

    节点/单元号从 1 连续：主梁节点 1..n+1、塔顶 n+2；
    主梁单元 1..n、索单元 n+1..n+6。
    """
    if n_elem < 6 or n_elem % 2:
        raise ValueError("n_elem 需为 >=6 的偶数（保证塔位落在节点上）")
    sec = box_section_3d(B, h, tw, tf, E_deck, nu)
    le = span / n_elem
    mid = n_elem // 2 + 1                       # 塔位对应的主梁节点号
    tower_node = n_elem + 2

    nodes = [Node3D(id=i + 1, x=le * i, y=0.0, z=0.0)
             for i in range(n_elem + 1)]
    nodes.append(Node3D(id=tower_node, x=le * (mid - 1),
                        y=0.0, z=tower_z))

    elements = [Element3D(id=i + 1, etype=1, n1=i + 1, n2=i + 2)
                for i in range(n_elem)]
    # 每侧 3 根索，锚点对称分布：左 mid-4, mid-2, mid-1 / 右 mid+1, mid+2, mid+4
    anchors = [mid - 4, mid - 2, mid - 1, mid + 1, mid + 2, mid + 4]
    anchors = [a for a in anchors if 1 <= a <= n_elem + 1]
    for j, a in enumerate(anchors):
        elements.append(Element3D(id=n_elem + 1 + j, etype=2,
                                  n1=tower_node, n2=a))

    boundaries = [
        Boundary3D(node=1, dofs=(1, 4), value=0.0),          # 左端：UX UY UZ ROTX
        Boundary3D(node=n_elem + 1, dofs=(2, 3), value=0.0),  # 右端：UY UZ
        Boundary3D(node=tower_node, dofs=(1, 3), value=0.0),  # 刚性塔顶：UX UY UZ
    ]

    q = udl_kN * 1000.0
    loads = [NodalLoad3D(node=i + 1, dof=3,
                        value=-q * le * (0.5 if i in (0, n_elem) else 1.0))
             for i in range(n_elem + 1)]

    cable_w = cable_area * cable_dens * 9.81     # N/m，仅非线性内核垂度用
    model = ModelSpec3D(
        title=title,
        nodes=nodes,
        materials=[Material3D(id=1, E=E_deck),
                   Material3D(id=2, E=E_cable)],
        reals=[RealConst3D(id=1, A=sec["A"], IY=sec["IY"], IZ=sec["IZ"],
                           J=sec["J"], G=sec["G"], WT=sec["WT"],
                           WB=sec["WB"], WY=sec["WY"], WTORS=sec["WTORS"]),
               RealConst3D(id=2, A=cable_area, W=cable_w)],
        etypes=[ElemTypeDef3D(id=1, kind="BEAM", mat=1, real=1),
                ElemTypeDef3D(id=2, kind="CABLE", mat=2, real=2)],
        elements=elements,
        boundaries=boundaries,
        loads=loads,
    )
    errs, _ = validate_model_3d(model)
    if errs:
        raise ValueError("ModelSpec3D 校验未通过: " + "; ".join(errs))
    return model


def build_arch_bridge_3d(
    *,
    span: float = 60.0,
    n_elem: int = 12,
    rise: float = 12.0,
    B: float = 8.0, h: float = 2.0,
    tw: float = 0.02, tf: float = 0.025,
    E_deck: float = 3.45e10, nu: float = 0.2,
    hanger_area: float = 0.002,
    E_hanger: float = 1.95e11,
    udl_kN: float = 20.0,
    title: str = "抛物线拱桥 3D 混合单元（BEAM 拱肋/主梁 + TRUSS 吊杆）",
) -> ModelSpec3D:
    """下承式抛物线拱桥：水平主梁 BEAM + 拱肋 BEAM + 竖向吊杆 TRUSS。

    拱轴 ``z(x) = 4·rise·x·(L-x)/L²``。拱肋为 X-Z 平面折线斜梁（每段
    dx=le>0，绝不竖直），与 ANSYS BEAM4 的默认局部坐标严格一致。
    拱脚与主梁端点共用节点（水平推力由主梁承担，即系杆拱体系）。

    节点/单元号从 1 连续：主梁 1..n+1、拱肋内部 n+2..2n；
    主梁单元 1..n、拱肋单元 n+1..2n、吊杆 2n+1..3n-1。
    """
    if n_elem < 6:
        raise ValueError("n_elem 需 >= 6")
    sec = box_section_3d(B, h, tw, tf, E_deck, nu)
    le = span / n_elem

    def arch_z(x: float) -> float:
        return 4.0 * rise * x * (span - x) / (span * span)

    nodes = [Node3D(id=i + 1, x=le * i, y=0.0, z=0.0)
             for i in range(n_elem + 1)]
    # 拱肋内部节点：x = le*j (j=1..n-1)，编号 n+2 .. 2n
    for j in range(1, n_elem):
        nodes.append(Node3D(id=n_elem + 1 + j, x=le * j, y=0.0,
                            z=arch_z(le * j)))

    elements = [Element3D(id=i + 1, etype=1, n1=i + 1, n2=i + 2)
                for i in range(n_elem)]                       # 主梁
    # 拱肋：node1 -> arch(1) -> ... -> arch(n-1) -> node(n+1)
    arch_chain = [1] + list(range(n_elem + 2, 2 * n_elem + 1)) + [n_elem + 1]
    for k in range(len(arch_chain) - 1):
        elements.append(Element3D(id=n_elem + 1 + k, etype=1,
                                  n1=arch_chain[k], n2=arch_chain[k + 1]))
    # 吊杆：arch(j) -> deck(j+1)，j=1..n-1
    for j in range(1, n_elem):
        elements.append(Element3D(id=2 * n_elem + j, etype=2,
                                  n1=n_elem + 1 + j, n2=j + 1))

    boundaries = [
        Boundary3D(node=1, dofs=(1, 4), value=0.0),
        Boundary3D(node=n_elem + 1, dofs=(2, 3), value=0.0),
    ]
    q = udl_kN * 1000.0
    loads = [NodalLoad3D(node=i + 1, dof=3,
                        value=-q * le * (0.5 if i in (0, n_elem) else 1.0))
             for i in range(n_elem + 1)]

    model = ModelSpec3D(
        title=title,
        nodes=nodes,
        materials=[Material3D(id=1, E=E_deck),
                   Material3D(id=2, E=E_hanger)],
        reals=[RealConst3D(id=1, A=sec["A"], IY=sec["IY"], IZ=sec["IZ"],
                           J=sec["J"], G=sec["G"], WT=sec["WT"],
                           WB=sec["WB"], WY=sec["WY"], WTORS=sec["WTORS"]),
               RealConst3D(id=2, A=hanger_area)],
        etypes=[ElemTypeDef3D(id=1, kind="BEAM", mat=1, real=1),
                ElemTypeDef3D(id=2, kind="TRUSS", mat=2, real=2)],
        elements=elements,
        boundaries=boundaries,
        loads=loads,
    )
    errs, _ = validate_model_3d(model)
    if errs:
        raise ValueError("ModelSpec3D 校验未通过: " + "; ".join(errs))
    return model


# ----------------------------------------------------------------- APDL
def _num(x: float, fmt: str = "{:.12g}") -> str:
    return fmt.format(float(x))


def _dofs(a: int, b: int) -> list[int]:
    return list(range(a, b + 1))


def _axial_only_nodes(model: ModelSpec3D) -> set[int]:
    """只被轴向杆（TRUSS/CABLE）连接的节点：FEM-Eigen 只给 3 个平动 DOF。"""
    kinds = {t.id: t.kind for t in model.etypes}
    beam_nodes: set[int] = set()
    axial_nodes: set[int] = set()
    for e in model.elements:
        tgt = beam_nodes if kinds[e.etype] not in AXIAL_KINDS else axial_nodes
        tgt.add(e.n1)
        tgt.add(e.n2)
    return axial_nodes - beam_nodes


def spec_mixed_to_apdl(model: ModelSpec3D, *, jobname: str,
                       title: str | None = None) -> str:
    """把混合单元 ``ModelSpec3D`` 转成 ANSYS 18.0 ``BEAM4 + LINK180`` 命令流。

    输出 4 张机器可读 CSV：``_disp`` / ``_reac``（节点 6 自由度，轴向-only
    节点的转动分量补 0）、``_beam``（梁 i 端 6 分量）、``_link``（杆轴力）。
    """
    errs, _ = validate_model_3d(model)
    if errs:
        raise ValueError("ModelSpec3D 校验未通过: " + "; ".join(errs))
    node_ids = sorted(n.id for n in model.nodes)
    elem_ids = sorted(e.id for e in model.elements)
    if node_ids != list(range(1, len(node_ids) + 1)):
        raise ValueError("要求节点号从 1 连续编号")
    if elem_ids != list(range(1, len(elem_ids) + 1)):
        raise ValueError("要求单元号从 1 连续编号")

    kinds = {t.id: t.kind for t in model.etypes}
    reals = {r.id: r for r in model.reals}
    mats = {m.id: m for m in model.materials}
    beam_types = sorted(t.id for t in model.etypes if t.kind not in AXIAL_KINDS)
    link_types = sorted(t.id for t in model.etypes if t.kind in AXIAL_KINDS)
    if not beam_types or not link_types:
        raise ValueError("混合单元导出要求同时存在 BEAM 与 TRUSS/CABLE 单元类型")
    if any(k not in ("BEAM", "BEAM7", "TRUSS", "CABLE") for k in kinds.values()):
        raise ValueError("仅支持 BEAM / BEAM7 / TRUSS / CABLE 单元类型")

    # 每个 real 只允许被同一类单元引用（BEAM 与 LINK 的实常数格式不同）
    for t in model.etypes:
        rc = reals.get(t.real)
        if rc is None:
            raise ValueError("etype {} 引用了不存在的实常数 {}".format(t.id, t.real))
        if t.kind not in AXIAL_KINDS and min(rc.A, rc.IY, rc.IZ, rc.J, rc.G) <= 0:
            raise ValueError("BEAM 实常数 {} 的 A/IY/IZ/J/G 必须全非零".format(rc.id))
        if t.kind in AXIAL_KINDS and rc.A <= 0:
            raise ValueError("轴向杆实常数 {} 的 A 必须为正".format(rc.id))

    etype_by_id = {t.id: t for t in model.etypes}
    beam_real = reals[etype_by_id[beam_types[0]].real]
    beam_mat = mats[etype_by_id[beam_types[0]].mat]
    tkz = 2.0 * beam_real.IY / beam_real.WT if beam_real.WT else 1.0
    tky = 2.0 * beam_real.IZ / beam_real.WY if beam_real.WY else 1.0

    nnode, nelem = len(node_ids), len(elem_ids)
    axial_only = _axial_only_nodes(model)
    beam_elems = sorted(e.id for e in model.elements
                        if kinds[e.etype] not in AXIAL_KINDS)
    link_elems = sorted(e.id for e in model.elements
                        if kinds[e.etype] in AXIAL_KINDS)

    L: list[str] = []
    add = L.append
    apdl_title = (title or model.title).encode("ascii", "backslashreplace").decode("ascii")
    add("/BATCH")
    add("/TITLE,{} - BEAM4+LINK180".format(apdl_title))
    add("/PREP7")
    # ---- element types ----
    for t in sorted(model.etypes, key=lambda t: t.id):
        add("ET,{},{}".format(t.id, "BEAM4" if t.kind not in AXIAL_KINDS
                             else "LINK180"))
    # ---- real constants (per real id; RMORE must immediately follow its R) ----
    real_owner = {}
    for t in model.etypes:
        prev = real_owner.get(t.real)
        cur = "LINK" if t.kind in AXIAL_KINDS else "BEAM"
        if prev and prev != cur:
            raise ValueError("实常数 {} 同时被梁与杆引用，格式冲突".format(t.real))
        real_owner[t.real] = cur
    for rid in sorted(real_owner):
        rc = reals[rid]
        if real_owner[rid] == "BEAM":
            add("R,{},{},{},{},{},{},0.0".format(
                rid, _num(rc.A), _num(rc.IZ), _num(rc.IY),
                _num(tkz), _num(tky)))
            add("RMORE,0.0,{},0.0,0.0,0.0,0.0".format(_num(rc.J)))
        else:
            add("R,{},{},{}".format(rid, _num(rc.A), _num(rc.ISTRN)))
    # ---- materials ----
    for mid in sorted(mats):
        add("MP,EX,{},{}".format(mid, _num(mats[mid].E)))
        add("MP,PRXY,{},0.3".format(mid))
    add("! GXY drives BEAM4 torsional stiffness (matches FEM-Eigen *R G=)")
    add("MP,GXY,{},{}".format(beam_mat.id, _num(beam_real.G)))
    # ---- nodes ----
    add("! ---- Nodes (X longitudinal, Y transverse, Z vertical) ----")
    for nd in sorted(model.nodes, key=lambda n: n.id):
        add("N,{},{},{},{}".format(nd.id, _num(nd.x), _num(nd.y), _num(nd.z)))
    # ---- elements: keep FEM element id == ANSYS element number ----
    add("! ---- Elements (created in ascending FEM id order) ----")
    for e in sorted(model.elements, key=lambda e: e.id):
        t = next(t for t in model.etypes if t.id == e.etype)
        add("TYPE,{}".format(t.id))
        add("MAT,{}".format(t.mat))
        add("REAL,{}".format(t.real))
        add("E,{},{}".format(e.n1, e.n2))
    # ---- boundary conditions: clip to available DOF on axial-only nodes ----
    add("! ---- Boundary conditions ----")
    for b in model.boundaries:
        if b.k:
            raise ValueError("混合单元导出暂不支持弹性支承")
        mx = 3 if b.node in axial_only else 6
        for d in _dofs(*b.dofs):
            if d <= mx:
                add("D,{},{},{}".format(b.node, DOF3_NAME[d], _num(b.value)))
    add("! ---- Nodal loads (same *CLOAD as FEM-Eigen 3D) ----")
    for ld in model.loads:
        add("F,{},{},{}".format(ld.node, DOF3_LOAD[ld.dof], _num(ld.value)))
    add("FINISH")
    add("/SOLU")
    add("ANTYPE,STATIC")
    add("SOLVE")
    add("FINISH")

    # ---------------------------------------------------------- post-processing
    add("! ---- Post-processing: machine-readable CSV ----")
    add("/POST1")
    add("SET,LAST")
    add("*DIM,ADSP,ARRAY,{},7".format(nnode))
    for nd in node_ids:
        add(" ADSP({},1)={}".format(nd, nd))
        for k, comp in enumerate(("X", "Y", "Z"), start=2):
            add(" *GET,TMP,NODE,{},U,{}".format(nd, comp))
            add(" ADSP({},{})=TMP".format(nd, k))
        for k in range(5, 8):
            if nd in axial_only:
                add(" ADSP({},{})=0.0".format(nd, k))
            else:
                comp = ("X", "Y", "Z")[k - 5]
                add(" *GET,TMP,NODE,{},ROT,{}".format(nd, comp))
                add(" ADSP({},{})=TMP".format(nd, k))
    add("*CFOPEN,{}_disp,csv".format(jobname))
    add("*VWRITE," + ",".join("ADSP(1,{})".format(k) for k in range(1, 8)))
    add(" (1X,F8.0,6(1X,E18.10))")
    add("*CFCLOS")

    add("*DIM,ARCT,ARRAY,{},7".format(nnode))
    for nd in node_ids:
        add(" ARCT({},1)={}".format(nd, nd))
        for k, comp in enumerate(("FX", "FY", "FZ", "MX", "MY", "MZ"), start=2):
            if nd in axial_only and k >= 5:
                add(" ARCT({},{})=0.0".format(nd, k))
            else:
                add(" *GET,TMP,NODE,{},RF,{}".format(nd, comp))
                add(" ARCT({},{})=TMP".format(nd, k))
    add("*CFOPEN,{}_reac,csv".format(jobname))
    add("*VWRITE," + ",".join("ARCT(1,{})".format(k) for k in range(1, 8)))
    add(" (1X,F8.0,6(1X,E18.10))")
    add("*CFCLOS")

    # BEAM i-end forces: select only BEAM element types, one ETABLE per SMISC
    add("! ---- BEAM4 i-end forces (SMISC 1..6 = N/Vy/Vz/T/My/Mz) ----")
    add("ESEL,S,TYPE,,{}".format(beam_types[0]))
    for tid in beam_types[1:]:
        add("ESEL,A,TYPE,,{}".format(tid))
    for k, s in enumerate(BEAM4_SMISC, start=1):
        add("ETABLE,BI{},SMISC,{}".format(k, s))
    add("*DIM,ABM,ARRAY,{},7".format(len(beam_elems)))
    for row, eid in enumerate(beam_elems, start=1):
        add(" ABM({},1)={}".format(row, eid))
        for k in range(1, 7):
            add(" *GET,TMP,ELEM,{},ETAB,BI{}".format(eid, k))
            add(" ABM({},{})=TMP".format(row, k + 1))
    add("*CFOPEN,{}_beam,csv".format(jobname))
    add("*VWRITE," + ",".join("ABM(1,{})".format(k) for k in range(1, 8)))
    add(" (1X,F8.0,6(1X,E18.10))")
    add("*CFCLOS")

    # LINK axial force: SMISC 1 only (avoids ETABLE warnings on LINK180)
    add("! ---- LINK180 axial force (SMISC 1) ----")
    add("ESEL,S,TYPE,,{}".format(link_types[0]))
    for tid in link_types[1:]:
        add("ESEL,A,TYPE,,{}".format(tid))
    add("ETABLE,LKN,SMISC,1")
    add("*DIM,ALK,ARRAY,{},2".format(len(link_elems)))
    for row, eid in enumerate(link_elems, start=1):
        add(" ALK({},1)={}".format(row, eid))
        add(" *GET,TMP,ELEM,{},ETAB,LKN".format(eid))
        add(" ALK({},2)=TMP".format(row))
    add("*CFOPEN,{}_link,csv".format(jobname))
    add("*VWRITE,ALK(1,1),ALK(1,2)")
    add(" (1X,F8.0,1(1X,E18.10))")
    add("*CFCLOS")
    add("ESEL,ALL")
    add("FINISH")
    add("/EXIT,NOSAVE")
    return "\n".join(L) + "\n"


# ----------------------------------------------------------------- 解析
def parse_fem_report_mixed(path: Path) -> dict:
    """解析混合单元 ``<stem>.report.txt``。

    返回 ``{"disp": {node: [6 floats]}, "force": {elem: {6 keys}},
            "kind": {elem: str}}``。轴向-only 节点在 FEM 报告中只有 3 个
    平动值，这里补 0 到 6 位；轴向杆单元只有 ``N=``，其余分量补 0。
    """
    disp: dict[int, list[float]] = {}
    force: dict[int, dict[str, float]] = {}
    kind: dict[int, str] = {}
    section = ""
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "节点位移" in line:
            section = "disp"
            continue
        if "单元内力" in line:
            section = "force"
            continue
        if "单元应力" in line or "支座反力" in line:
            section = ""
            continue
        toks = line.replace(",", " ").split()
        try:
            key = int(toks[0])
        except (ValueError, IndexError):
            continue
        if section == "disp":
            try:
                vals = [float(v) for v in toks[1:7]]
            except ValueError:
                continue
            if vals:
                disp[key] = (vals + [0.0] * 6)[:6]
        elif section == "force":
            kv: dict[str, float] = {}
            for t in toks[2:]:
                if "=" in t:
                    k, _, v = t.partition("=")
                    try:
                        kv[k] = float(v)
                    except ValueError:
                        pass
            if any(k in kv for k in FORCE_KEYS):
                force[key] = {k: kv.get(k, 0.0) for k in FORCE_KEYS}
                kind[key] = toks[1] if len(toks) > 1 else ""
    return {"disp": disp, "force": force, "kind": kind}


# ----------------------------------------------------------------- 对比
def _fmt(x: float, width: int = 16) -> str:
    return "{:>{}.8g}".format(x, width)


def compare_mixed(model: ModelSpec3D, fem: dict,
                   adisp: dict, areac: dict, abeam: dict, alink: dict,
                   *, stem: str, tol: float = 1e-3) -> tuple[str, bool]:
    """混合单元校核：位移 / 梁端力 / 杆轴力 / 竖向力平衡 + 特征量汇总。"""
    lines: list[str] = []
    ok_all = True
    add = lines.append
    kinds = {t.id: t.kind for t in model.etypes}
    elem_kind = {e.id: kinds[e.etype] for e in model.elements}
    axial = {eid for eid, k in elem_kind.items() if k in AXIAL_KINDS}
    beams = {eid for eid, k in elem_kind.items() if k not in AXIAL_KINDS}

    add("=" * 94)
    add("3D 混合单元校核: {}  (FEM-Eigen 3D / ANSYS BEAM4+LINK180)".format(stem))
    add("=" * 94)
    add("模型: {} 节点 / {} 单元 = {} BEAM + {} {} / Z 竖向".format(
        len(model.nodes), len(model.elements), len(beams), len(axial),
        "/".join(sorted({elem_kind[e] for e in axial})) or "轴向杆"))
    for t in model.etypes:
        rc = next(r for r in model.reals if r.id == t.real)
        add("  etype {} {:<6s} A={:.6g} m2  IY={:.6g}  IZ={:.6g}  J={:.6g}  G={:.6g}".format(
            t.id, t.kind, rc.A, rc.IY, rc.IZ, rc.J, rc.G))
    add("单元语义: FEM-Eigen 线性 3D 内核把 CABLE/TRUSS 都按 (EA/L)·c⊗c 轴向杆装配,")
    add("          不区分只拉不压、不读 L0、无垂度；ANSYS LINK180 同为轴向杆，口径一致。")
    add("          只受拉 / 悬链线 / Ernst / 索力迭代属于 cable_solver.exe 非线性链。")
    add("局部坐标: X-Z 平面斜梁两侧均用 ez_ref=global Z，实测逐位一致；纯竖直 BEAM 会")
    add("          差 90°，本算例竖向构件一律用轴向杆规避。")
    add("")

    # [1] 节点位移
    add("[1] 严格校核 — 节点位移 6 自由度 (FEM-Eigen 3D vs ANSYS BEAM4+LINK180)")
    allv: list[tuple[int, int, float, float]] = []
    for nd in sorted(fem["disp"]):
        f = fem["disp"][nd]
        a = adisp.get(nd)
        if a is None or len(a) < 6:
            ok_all = False
            add("  缺少 ANSYS 节点 {} 位移".format(nd))
            continue
        for k in range(6):
            allv.append((nd, k + 1, f[k], a[k]))
    if allv:
        dmax = max(abs(f - a) for _, _, f, a in allv)
        scale = max(max(abs(f), abs(a)) for _, _, f, a in allv) or 1.0
        rel = dmax / scale
        ok = rel <= tol
        ok_all &= ok
        add("  {} 节点 x 6 DOF = {} 项; 最大绝对差 {:.6e}, 量级 {:.6e}, "
            "相对 {:.3e}  {}".format(len(fem["disp"]), len(allv), dmax, scale,
                                    rel, "PASS" if ok else "FAIL"))
        for lbl, k in (("UX", 0), ("UY", 1), ("UZ", 2), ("ROTY", 4)):
            sub = [(f, a) for _, d, f, a in allv if d == k + 1]
            if sub:
                add("    {:<5s} 最大幅值 {:<16.8g} 最大差 {:<12.4g}".format(
                    lbl, max(abs(f) for f, _ in sub),
                    max(abs(f - a) for f, a in sub)))
    else:
        ok_all = False
        add("  无可比位移数据  FAIL")

    # [2] 梁 i 端 6 分量（幅值）
    add("")
    add("[2] 严格校核 — BEAM 单元 i 端内力幅值 (FEM-Eigen 3D vs ANSYS BEAM4)")
    pairs: list[tuple[int, str, float, float]] = []
    for e in sorted(beams):
        f = fem["force"].get(e)
        a = abeam.get(e)
        if f is None or a is None or len(a) < 6:
            ok_all = False
            add("  缺少单元 {} 梁端力 (fem={}, ansys={})".format(
                e, f is not None, a is not None))
            continue
        for k, key in enumerate(FORCE_KEYS):
            pairs.append((e, key, abs(f[key]), abs(a[k])))
    if pairs:
        dmax = max(abs(f - a) for _, _, f, a in pairs)
        scale = max(max(f, a) for _, _, f, a in pairs) or 1.0
        rel = dmax / scale
        ok = rel <= tol
        ok_all &= ok
        add("  {} BEAM x 6 分量 = {} 项; 最大绝对差 {:.6e}, 量级 {:.6e}, "
            "相对 {:.3e}  {}".format(len(beams), len(pairs), dmax, scale,
                                    rel, "PASS" if ok else "FAIL"))
        for key in FORCE_KEYS:
            sub = [(f, a) for _, k, f, a in pairs if k == key]
            if sub:
                add("    {:<3s} 最大幅值 {:<16.8g} 最大差 {:<12.4g}".format(
                    key, max(f for f, _ in sub),
                    max(abs(f - a) for f, a in sub)))
    else:
        ok_all = False
        add("  无可比梁端力数据  FAIL")

    # [3] 轴向杆轴力
    add("")
    add("[3] 严格校核 — 轴向杆轴力幅值 (FEM-Eigen 3D vs ANSYS LINK180 SMISC 1)")
    ax: list[tuple[int, float, float]] = []
    for e in sorted(axial):
        f = fem["force"].get(e)
        a = alink.get(e)
        if f is None or a is None or not a:
            ok_all = False
            add("  缺少单元 {} 轴力 (fem={}, ansys={})".format(
                e, f is not None, a is not None))
            continue
        ax.append((e, abs(f["N"]), abs(a[0])))
    if ax:
        dmax = max(abs(f - a) for _, f, a in ax)
        scale = max(max(f, a) for _, f, a in ax) or 1.0
        rel = dmax / scale
        ok = rel <= tol
        ok_all &= ok
        add("  {} 根轴向杆; 最大绝对差 {:.6e}, 量级 {:.6e}, 相对 {:.3e}  {}".format(
            len(ax), dmax, scale, rel, "PASS" if ok else "FAIL"))
        add("  杆轴力 (FEM / ANSYS, N):")
        for e, f, a in ax:
            add("    elem {:<4d} {:<18.8g} {:<18.8g}  {}".format(e, f, a,
                elem_kind[e]))
        add("  最大杆力 {:.8g} N (elem {})".format(
            max(ax, key=lambda t: t[1])[1], max(ax, key=lambda t: t[1])[0]))
    else:
        ok_all = False
        add("  无可比轴力数据  FAIL")

    # [4] 竖向力平衡
    add("")
    add("[4] ANSYS 支座反力 / 外荷载 竖向力平衡自查")
    add("    注: 只统计竖向支座节点；ANSYS 在无约束但有外荷载的节点上")
    add("        *GET,NODE,RF,FZ 返回节点荷载残差，不能计入反力。")
    fz_load = sum(ld.value for ld in model.loads if ld.dof == 3)
    sup = [b.node for b in model.boundaries if 3 in _dofs(*b.dofs)]
    fz_reac = sum(areac.get(nd, [0.0] * 6)[2] for nd in sup)
    bal = abs(fz_load + fz_reac)
    scale = max(abs(fz_load), 1.0)
    ok = bal / scale <= tol
    ok_all &= ok
    add("  外荷载 ΣFZ = {:.8g} N, 支座反力 ΣFZ = {:.8g} N, 残差 {:.6e} "
        "({:.3e})  {}".format(fz_load, fz_reac, bal, bal / scale,
                             "PASS" if ok else "FAIL"))
    add("  竖向支座 {} 个: {}".format(len(sup), sup))
    for nd in sup:
        add("    node {:<4d} RF_FZ = {:>16.8g} N".format(
            nd, areac.get(nd, [0.0] * 6)[2]))

    add("")
    add("=" * 94)
    add("总判定: " + ("PASS —— 3D 混合单元建模 -> FEM-Eigen 3D 计算 -> "
                    "ANSYS BEAM4+LINK180 校核 流程打通，结果严格一致"
                    if ok_all else "FAIL —— 存在超差项，需排查"))
    add("=" * 94)
    return "\n".join(lines) + "\n", bool(ok_all)
