#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""G 阶段：FEM-Eigen 非线性成桥态 -> ANSYS 18.0 NLGEOM + LINK180 同源对标。

数据链路（唯一数据源仍是 BridgeStudio 的 ModelSpec3D）：

    build_cable_stayed_3d(mode=COMPLETION, cable_dens=0)
        -> to_dat() -> cable_solver.exe -> <stem>_cable.txt
             [ERECTED] 制造线形（ANSYS 初始几何）
             [CABLE]   每根索的下料长度 L0 与成桥索力 T
             [U]       相对制造线形的成桥位移（= -CAMBER）
             [REACTION] 支座反力
        -> spec_nl_to_apdl() -> ANSYS BEAM4 + LINK180 + NLGEOM
             INISTATE,SET,DTYP,EPEL / INISTATE,DEFINE,<e>,,EPEL,X,eps
             eps = (L_chord(ERECTED) - L0) / L0
        -> compare_nl() 位移 / 索力 / 反力逐项对比

本机 ANSYS 18.0 实测语义（ansys_probe/link180_nl_probe.py、
link180_inistrain_probe.py、vertical_beam_probe.py）：
  * LINK180 的 ``R,id,A,ISTRN`` 第 3 参数被静默忽略；初应变必须用
    ``INISTATE,SET,DTYP,EPEL`` + ``INISTATE,DEFINE,elem,,EPEL,X,eps``，
    零位移时给出 N0 = E*A*eps（NLGEOM 下同样成立）。
  * ``KEYOPT(3)=1`` = tension-only（压缩时轴力归零），2 = compression-only。
  * NLGEOM,ON 时 ``KEYOPT(2)=0`` 用 Almansi 应变、``KEYOPT(2)=1`` 用对数
    应变；FEM 共转列式索用 Lagrangian 工程应变 N=EA/L0*(Ln-L0)。
    三者在一阶一致，差异是 O(eps^2)，故本算例把索应变控制在 ~1e-3。
  * 竖直/近竖直 BEAM4 需方向节点 K(+Y) 才能与 FEM 的 ey=+X/ez=+Y 对齐。

坐标系：X 纵向 / Y 横向 / Z 竖向；竖向荷载 = DOF3 = ANSYS FZ。
"""
from __future__ import annotations

from pathlib import Path

from model_spec_3d import ModelSpec3D  # noqa: E402
from ansys_bridge_3d import parse_ansys_csv_3d  # noqa: E402

DOF3_NAME = {1: "UX", 2: "UY", 3: "UZ",
             4: "ROTX", 5: "ROTY", 6: "ROTZ"}
DOF3_LOAD = {1: "FX", 2: "FY", 3: "FZ",
             4: "MX", 5: "MY", 6: "MZ"}
AXIAL_KINDS = ("TRUSS", "CABLE")


def _num(x: float, fmt: str = "{:.12g}") -> str:
    return fmt.format(float(x))


def _dofs(a: int, b: int) -> list[int]:
    return list(range(a, b + 1))


def _axial_only_nodes(model: ModelSpec3D) -> set[int]:
    kinds = {t.id: t.kind for t in model.etypes}
    beam_nodes: set[int] = set()
    axial_nodes: set[int] = set()
    for e in model.elements:
        tgt = beam_nodes if kinds[e.etype] not in AXIAL_KINDS else axial_nodes
        tgt.add(e.n1)
        tgt.add(e.n2)
    return axial_nodes - beam_nodes


def initial_cable_strain(model: ModelSpec3D, *,
                         coords: dict[int, tuple[float, float, float]],
                         l0: dict[int, float]) -> dict[int, float]:
    """每根 CABLE 单元 -> ANSYS INISTATE 初应变 eps = (L-L0)/L0。"""
    kinds = {t.id: t.kind for t in model.etypes}
    out: dict[int, float] = {}
    for e in model.elements:
        if kinds[e.etype] != "CABLE" or e.id not in l0:
            continue
        p1, p2 = coords[e.n1], coords[e.n2]
        L = sum((b - a) ** 2 for a, b in zip(p1, p2)) ** 0.5
        if L <= 0.0 or l0[e.id] <= 0.0:
            raise ValueError("索单元 {} 的弦长/L0 非法".format(e.id))
        out[e.id] = (L - l0[e.id]) / l0[e.id]
    return out


def spec_nl_to_apdl(model: ModelSpec3D, *, jobname: str,
                    coords: dict[int, tuple[float, float, float]],
                    l0: dict[int, float],
                    title: str | None = None) -> tuple[str, dict]:
    """生成 ANSYS ``BEAM4 + LINK180 + NLGEOM + INISTATE`` 命令流。

    返回 ``(apdl_text, info)``；``info["elem_map"]`` 是
    ``ANSYS 自动单元号 -> FEM 单元号``，供结果解析与报告使用。
    节点直接用 FEM 原 id（ANSYS 允许任意正整数节点号），因此 CSV 第一列
    就是 FEM 节点/单元 id，无需重编号。
    """
    kinds = {t.id: t.kind for t in model.etypes}
    reals = {r.id: r for r in model.reals}
    mats = {m.id: m for m in model.materials}
    node_ids = [n.id for n in sorted(model.nodes, key=lambda n: n.id)]
    if len(set(node_ids)) != len(node_ids):
        raise ValueError("节点 id 重复")
    missing = [i for i in node_ids if i not in coords]
    if missing:
        raise ValueError("ERECTED 坐标缺少节点: {}".format(missing[:5]))

    elems = sorted(model.elements, key=lambda e: e.id)
    elem_map = {idx + 1: e.id for idx, e in enumerate(elems)}
    axial_only = _axial_only_nodes(model)
    eps = initial_cable_strain(model, coords=coords, l0=l0)

    beam_types = sorted(t.id for t in model.etypes
                        if t.kind not in AXIAL_KINDS)
    link_types = sorted(t.id for t in model.etypes
                        if t.kind in AXIAL_KINDS)
    if not beam_types or not link_types:
        raise ValueError("要求模型同时含 BEAM 与 CABLE/TRUSS 单元")

    # 近竖直 BEAM -> 方向节点 K(+Y)；用 ERECTED 几何判定
    orient: dict[int, tuple[float, float, float]] = {}
    for e in elems:
        if kinds[e.etype] in AXIAL_KINDS:
            continue
        p1, p2 = coords[e.n1], coords[e.n2]
        dx, dy, dz = p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]
        L = (dx * dx + dy * dy + dz * dz) ** 0.5
        if L > 0.0 and abs(dz) / L > 0.999:
            orient[e.id] = (p1[0], p1[1] + 1.0, p1[2])
    orient_node: dict[int, int] = {}
    if orient:
        nid = max(node_ids) + 1
        for eid in sorted(orient):
            orient_node[eid] = nid
            nid += 1

    L: list[str] = []
    add = L.append
    apdl_title = (title or model.title).encode(
        "ascii", "backslashreplace").decode("ascii")
    add("/BATCH")
    add("/TITLE,{} - BEAM4+LINK180 NLGEOM".format(apdl_title))
    add("/PREP7")
    for t in sorted(model.etypes, key=lambda t: t.id):
        if t.kind in AXIAL_KINDS:
            add("ET,{},LINK180".format(t.id))
            # tension-only 仅对 CABLE；TRUSS 保持可拉可压
            add("KEYOPT,{},2,0".format(t.id))
            add("KEYOPT,{},3,{}".format(
                t.id, 1 if t.kind == "CABLE" else 0))
        else:
            add("ET,{},BEAM4".format(t.id))
    # 实常数：每个 real 只被同一类单元引用
    real_owner: dict[int, str] = {}
    for t in model.etypes:
        cur = "LINK" if t.kind in AXIAL_KINDS else "BEAM"
        if real_owner.get(t.real, cur) != cur:
            raise ValueError("实常数 {} 同时被梁与杆引用".format(t.real))
        real_owner[t.real] = cur
    for rid in sorted(real_owner):
        rc = reals[rid]
        if real_owner[rid] == "BEAM":
            tkz = 2.0 * rc.IY / rc.WT if rc.WT else 1.0
            tky = 2.0 * rc.IZ / rc.WY if rc.WY else 1.0
            add("R,{},{},{},{},{},{},0.0".format(
                rid, _num(rc.A), _num(rc.IZ), _num(rc.IY),
                _num(tkz), _num(tky)))
            add("RMORE,0.0,{},0.0,0.0,0.0,0.0".format(_num(rc.J)))
        else:
            # LINK180 的 ISTRN 实常数无效；初应变统一走 INISTATE
            add("R,{},{}".format(rid, _num(rc.A)))
    for mid in sorted(mats):
        add("MP,EX,{},{}".format(mid, _num(mats[mid].E)))
        add("MP,PRXY,{},0.3".format(mid))
    for rid in sorted(real_owner):
        if real_owner[rid] != "BEAM":
            continue
        owner = next(t for t in model.etypes if t.real == rid)
        add("MP,GXY,{},{}".format(owner.mat, _num(reals[rid].G)))

    add("! ---- Nodes: FEM [ERECTED] manufactured geometry ----")
    for nd in sorted(model.nodes, key=lambda n: n.id):
        x, y, z = coords[nd.id]
        add("N,{},{},{},{}".format(nd.id, _num(x), _num(y), _num(z)))
    if orient_node:
        add("! ---- Orientation nodes K (near-vertical BEAM4) ----")
        for eid in sorted(orient_node):
            add("N,{},{},{},{}".format(
                orient_node[eid], *[_num(v) for v in orient[eid]]))

    add("! ---- Elements: ANSYS elem no. = creation order ----")
    etype_by_id = {t.id: t for t in model.etypes}
    for idx, e in enumerate(elems, start=1):
        t = etype_by_id[e.etype]
        add("TYPE,{}".format(t.id))
        add("MAT,{}".format(t.mat))
        add("REAL,{}".format(t.real))
        add("E,{},{}{}".format(
            e.n1, e.n2,
            ",{}".format(orient_node[e.id]) if e.id in orient_node else ""))

    add("! ---- Cable initial strain from FEM unstrained length L0 ----")
    add("INISTATE,SET,DTYP,EPEL")
    for ansys_eid in sorted(elem_map):
        fem_eid = elem_map[ansys_eid]
        if fem_eid in eps:
            add("INISTATE,DEFINE,{},,EPEL,X,{}".format(
                ansys_eid, _num(eps[fem_eid], "{:.15g}")))

    add("! ---- Boundary conditions ----")
    for b in model.boundaries:
        mx = 3 if b.node in axial_only else 6
        for d in _dofs(*b.dofs):
            if d <= mx:
                add("D,{},{},{}".format(
                    b.node, DOF3_NAME[d], _num(b.value)))
    # FEM-Eigen 的 *CLOAD 对同一 (node, dof) 是**累加**语义；ANSYS 的
    # F,node,label,value 则是**覆盖**语义。塔柱自重按「每段两端各半」写两遍，
    # 塔身内部节点会收到两条同 dof 的荷载，若不合并就会丢掉一半
    # （实测两塔共丢 4.83 MN，反力总和从 18.03 MN 掉到 13.21 MN）。
    merged: dict[tuple[int, int], float] = {}
    for ld in model.loads:
        key = (ld.node, ld.dof)
        merged[key] = merged.get(key, 0.0) + ld.value
    add("! ---- Nodal loads (merged *CLOAD; ANSYS F overwrites duplicates) ----")
    for (nd, dof), val in sorted(merged.items()):
        add("F,{},{},{}".format(nd, DOF3_LOAD[dof], _num(val)))
    add("FINISH")
    add("/SOLU")
    add("ANTYPE,STATIC")
    add("NLGEOM,ON")
    add("NSUBST,20,100,10")
    add("NEQIT,50")
    add("OUTRES,ALL,LAST")
    add("SOLVE")
    add("FINISH")

    # ------------------------------------------------------------ post
    add("! ---- Machine-readable results (first column = FEM id) ----")
    add("/POST1")
    add("SET,LAST")
    nnode = len(node_ids)
    add("*DIM,ADSP,ARRAY,{},7".format(nnode))
    add("*DIM,ARCT,ARRAY,{},7".format(nnode))
    for row, nd in enumerate(node_ids, start=1):
        add(" ADSP({},1)={}".format(row, nd))
        add(" ARCT({},1)={}".format(row, nd))
        for k, comp in enumerate(("X", "Y", "Z"), start=2):
            add(" *GET,TMP,NODE,{},U,{}".format(nd, comp))
            add(" ADSP({},{})=TMP".format(row, k))
        for k in range(5, 8):
            comp = ("X", "Y", "Z")[k - 5]
            if nd in axial_only:
                add(" ADSP({},{})=0.0".format(row, k))
            else:
                add(" *GET,TMP,NODE,{},ROT,{}".format(nd, comp))
                add(" ADSP({},{})=TMP".format(row, k))
        for k, comp in enumerate(("FX", "FY", "FZ", "MX", "MY", "MZ"),
                                start=2):
            if nd in axial_only and k >= 5:
                add(" ARCT({},{})=0.0".format(row, k))
            else:
                add(" *GET,TMP,NODE,{},RF,{}".format(nd, comp))
                add(" ARCT({},{})=TMP".format(row, k))
    add("*CFOPEN,{}_disp,csv".format(jobname))
    add("*VWRITE," + ",".join("ADSP(1,{})".format(k) for k in range(1, 8)))
    add(" (1X,F8.0,6(1X,E18.10))")
    add("*CFCLOS")
    add("*CFOPEN,{}_reac,csv".format(jobname))
    add("*VWRITE," + ",".join("ARCT(1,{})".format(k) for k in range(1, 8)))
    add(" (1X,F8.0,6(1X,E18.10))")
    add("*CFCLOS")

    elem_kind = {e.id: kinds[e.etype] for e in elems}
    cable_ansys = sorted(ae for ae, fe in elem_map.items()
                         if elem_kind[fe] == "CABLE")
    add("! ---- LINK180 cable axial force (SMISC 1) ----")
    add("ESEL,S,TYPE,,{}".format(link_types[0]))
    for tid in link_types[1:]:
        add("ESEL,A,TYPE,,{}".format(tid))
    add("ETABLE,LKN,SMISC,1")
    add("*DIM,ALK,ARRAY,{},2".format(len(cable_ansys)))
    for row, ae in enumerate(cable_ansys, start=1):
        add(" ALK({},1)={}".format(row, elem_map[ae]))
        add(" *GET,TMP,ELEM,{},ETAB,LKN".format(ae))
        add(" ALK({},2)=TMP".format(row))
    add("*CFOPEN,{}_link,csv".format(jobname))
    add("*VWRITE,ALK(1,1),ALK(1,2)")
    add(" (1X,F8.0,1(1X,E18.10))")
    add("*CFCLOS")
    add("ESEL,ALL")
    add("FINISH")
    add("/EXIT,NOSAVE")

    info = {
        "elem_map": elem_map,
        "eps": eps,
        "orient_nodes": len(orient_node),
        "cable_count": len(cable_ansys),
        "node_count": nnode,
    }
    return "\n".join(L) + "\n", info


def compare_nl(result: dict, adisp: dict, areac: dict, alink: dict,
               *, stem: str, tol: float = 2e-3) -> tuple[str, bool]:
    """成桥态三项对标：节点位移 / 索力 / 支座反力。"""
    lines: list[str] = []
    ok_all = True
    add = lines.append
    cables = result["cables"]
    u = result["u"]
    reaction = result["reaction"]
    md = result["meta"]

    def check(label: str, pairs: list[tuple[str, float, float]],
              scale_hint: float = 0.0) -> None:
        nonlocal ok_all
        if not pairs:
            ok_all = False
            add("  {} 无可比数据  FAIL".format(label))
            return
        dmax = max(abs(f - a) for _, f, a in pairs)
        scale = max([max(abs(f), abs(a)) for _, f, a in pairs]
                    + [scale_hint, 1e-30])
        rel = dmax / scale
        ok = rel <= tol
        ok_all &= ok
        add("  {:<22s} {} 项; 最大绝对差 {:.6e}, 量级 {:.6e}, "
            "相对 {:.3e}  {}".format(label, len(pairs), dmax, scale,
                                    rel, "PASS" if ok else "FAIL"))
        worst = max(pairs, key=lambda p: abs(p[1] - p[2]))
        add("    最大差项: {}  FEM={:.8g}  ANSYS={:.8g}".format(
            worst[0], worst[1], worst[2]))

    add("=" * 94)
    add("非线性成桥态对标: {}  (FEM-Eigen cable_solver / ANSYS NLGEOM+LINK180)".format(stem))
    add("=" * 94)
    add("FEM: MODE=COMPLETION, converged={}, iterations={}, "
        "closure_err={:.3e}, tension_err={:.3e}".format(
            result.get("converged"), md.get("iterations"),
            float(md.get("closure_err", float("nan"))),
            float(md.get("tension_err", float("nan")))))
    add("ANSYS: BEAM4 + LINK180(KEYOPT(3)=1 tension only), NLGEOM,ON;")
    add("       初始几何 = FEM [ERECTED] 制造线形；索初应变 = (L_ERECTED-L0)/L0")
    add("       由 INISTATE,DEFINE 逐单元注入（LINK180 的 R,ISTRN 无效）。")
    add("本构口径: FEM 共转列式索 N=EA/L0*(Ln-L0)（Lagrangian 工程应变）；")
    add("          ANSYS NLGEOM 用 Almansi/对数应变，差异为 O(eps^2)。")
    add("          索自重 W=0 -> 关闭垂度/Ernst，避免 ANSYS 无垂度模型造成差异。")
    add("判据: 逐项相对量级 rtol={:.1e}（实测最大差异约 8e-4）".format(tol))
    add("ANSYS 提示 KEYOPT(3)=1 为 undocumented option：实测压缩时轴力归零，")
    add("       行为确为 tension-only；该 warning 如实保留，不做隐藏。")
    add("")

    add("[1] 节点位移（相对制造线形，6 DOF）")
    dp: list[tuple[str, float, float]] = []
    for (nd, dof), fv in sorted(u.items()):
        row = adisp.get(nd)
        if row is None or len(row) < dof:
            ok_all = False
            add("  缺少 ANSYS 节点 {} 位移".format(nd))
            continue
        dp.append(("n{}_d{}".format(nd, dof), fv, row[dof - 1]))
    check("位移", dp, scale_hint=max((abs(v) for v in u.values()),
                                    default=0.0))

    add("")
    add("[2] 成桥索力（LINK180 SMISC 1）")
    cp: list[tuple[str, float, float]] = []
    for c in cables:
        row = alink.get(c["elem"])
        if row is None or not row:
            ok_all = False
            add("  缺少 ANSYS 索单元 {} 轴力".format(c["elem"]))
            continue
        cp.append(("e{}".format(c["elem"]), abs(c["T"]), abs(row[0])))
    check("索力", cp)
    if cp:
        add("  FEM 索力范围 {:.1f} .. {:.1f} kN".format(
            min(f for _, f, _ in cp) / 1000.0,
            max(f for _, f, _ in cp) / 1000.0))
        add("  ANSYS 索力范围 {:.1f} .. {:.1f} kN".format(
            min(a for _, _, a in cp) / 1000.0,
            max(a for _, _, a in cp) / 1000.0))

    add("")
    add("[3] 支座反力（[REACTION] 段）")
    rp: list[tuple[str, float, float]] = []
    for (nd, dof), fv in sorted(reaction.items()):
        row = areac.get(nd)
        if row is None or len(row) < dof:
            ok_all = False
            add("  缺少 ANSYS 节点 {} 反力".format(nd))
            continue
        rp.append(("n{}_d{}".format(nd, dof), fv, row[dof - 1]))
    check("反力", rp)

    add("")
    add("=" * 94)
    add("总判定: " + ("PASS —— 非线性成桥态与 ANSYS NLGEOM+LINK180 对标通过"
                    if ok_all else "FAIL —— 存在超差项，需排查"))
    add("=" * 94)
    return "\n".join(lines) + "\n", bool(ok_all)
