#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BridgeStudio <-> FEM-Eigen 3D <-> ANSYS 三维梁同源导出与校核。

与 2D 的 ``ansys_bridge.py`` 平行、完全独立，不改动任何 2D 主干（项目的
X20_接入约定 §3.4 明确两条链正交，且 2D 有「默认 .dat 逐位不变」的黄金哈希
与 ``verify_2d_export_import.py`` 的「ANSYS 不含 BEAM4/BEAM188」负例守卫）。

数据契约用 BridgeStudio 已有的 ``ModelSpec3D``（model_spec_3d.py），
``to_dat(compact=False)`` 直接产出 FEM-Eigen 3D 内核（fem_3d.hpp /
readModel3D）可读的 .dat。本模块从**同一个 ModelSpec3D** 再导出两份 APDL：

  - BEAM188  ``SECTYPE,BEAM,ASEC`` + ``SECDATA``  （本轮目标，现代商业单元）
  - BEAM4    ``R`` + ``RMORE``，SHEARZ/SHEARY=0  （Euler-Bernoulli 严格基准）

为什么需要两份（本机 ANSYS 18.0 实测，见 FEM-Eigen/tests/benchmark/nonlinear_ansys_3d/probes/beam188_probe*.py）：

  * FEM-Eigen 3D 内核是 12x12 **Euler-Bernoulli** 空间梁（三次 Hermitian 形函数）。
  * ANSYS BEAM188 基于**一阶剪切变形 Timoshenko 理论 + 线性形函数**：
      - 单单元悬臂梁端部挠度只有 EB 的 0.859（剪切锁定）；
      - 细分后收敛到 EB + PL/(GA)（1.0678，标准 Timoshenko）；
      - 官方明确「每根构件要用多个单元」。
    ``SECCONTROL`` / 放大 ``MP,GXY`` 能抑制剪切，但只能部分抑制、且端内力
    与节点平衡值仍差约 1.7%，强行数值退化并不干净。
  * ANSYS BEAM4 同为 6DOF 三次 Euler-Bernoulli 梁，实测 UZ/EB=1.000000000、
    ROTX/EB=1.000000000、端内力精确平衡，与 FEM-Eigen 3D 逐位一致。

因此校核分两层：FEM-Eigen 3D vs BEAM4 是**严格同源校核**（rtol=1e-3）；
BEAM188 单独记录，差异明确归因于 Timoshenko 剪切 + 线性形函数离散。

坐标系约定（与 model_spec_3d.py 一致，也与 ANSYS 常规桥梁坐标一致）：
    X = 纵桥向, Y = 横向, Z = 竖向（向上为正）
    竖向荷载 -> DOF 3 (UZ) / ANSYS ``FZ``
    竖向弯曲惯性矩 -> ``IY`` (ANSYS ``IYY``)；横向弯曲 -> ``IZ`` (``IZZ``)
    竖向剪力 -> ``Vz`` / ``SFz``；竖向弯矩 -> ``My`` / ``MY``
    扭转 -> ``J`` (ANSYS ``Torsion Constant`` / ``IXX``)

FEM-Eigen 3D 内核的既有缺口（不改 C++ 内核，见 README）：
    不认识 ``*ELEMENT_LOAD`` / ``*GRAVITY`` / ``*SELF_WEIGHT``（静默忽略），
    ``DENS`` 只进一致质量矩阵。所以均布荷载/自重必须**节点化**为 ``*CLOAD``，
    ANSYS 侧用同一组 ``F,node,FZ`` 施加，保证严格同源。
"""
from __future__ import annotations

import math
from pathlib import Path

from model_spec_3d import (  # noqa: E402
    Boundary3D, Element3D, ElemTypeDef3D, Material3D, ModelSpec3D,
    Node3D, NodalLoad3D, RealConst3D, validate_model_3d,
)

DOF3_NAME = {1: "UX", 2: "UY", 3: "UZ",
             4: "ROTX", 5: "ROTY", 6: "ROTZ"}
DOF3_LOAD = {1: "FX", 2: "FY", 3: "FZ",
             4: "MX", 5: "MY", 6: "MZ"}
DOF3_RF = {1: "FX", 2: "FY", 3: "FZ",
           4: "MX", 5: "MY", 6: "MZ"}

#: 统一输出列 [N, Vy, Vz, T, My, Mz] 在两套单元的 SMISC 编号
#: BEAM188 Table 188.2: 1=Fx 2=My 3=Mz 4=TQ 5=SFz 6=SFy; J 端 14..19
SMISC_BEAM188 = ((1, 6, 5, 4, 2, 3), (14, 19, 18, 17, 15, 16))
#: BEAM4 (实测标定): 1=N 2=Vy 3=Vz 4=T 5=My 6=Mz; J 端 7..12
SMISC_BEAM4 = ((1, 2, 3, 4, 5, 6), (7, 8, 9, 10, 11, 12))

FORCE_KEYS = ("N", "Vy", "Vz", "T", "My", "Mz")


# ----------------------------------------------------------------- 截面
def box_section_3d(B: float, h: float, tw: float, tf: float,
                   E: float, nu: float = 0.2) -> dict:
    """单箱单室矩形箱梁的完整 3D 截面属性（外减内 + Bredt 扭转）。

    与 2D ``section_lib.section_properties`` 的 A/I 口径一致（同一个外减内
    矩形），另外补出横向弯曲 IZ、Bredt 扭转常数 J、截面模量。
    """
    bw, hw = B - 2.0 * tw, h - 2.0 * tf
    if bw <= 0 or hw <= 0:
        raise ValueError("箱梁壁厚过大，内腔尺寸非正")
    A = B * h - bw * hw
    IY = (B * h ** 3 - bw * hw ** 3) / 12.0        # 绕 y 轴：竖向(z)弯曲
    IZ = (h * B ** 3 - hw * bw ** 3) / 12.0        # 绕 z 轴：横向(y)弯曲
    # Bredt 薄壁闭口截面扭转常数 J = 4*A0^2 / ∮(ds/t)，A0 为壁厚中线包围面积
    A0 = (B - tw) * (h - tf)
    loop = 2.0 * (B - tw) / tf + 2.0 * (h - tf) / tw
    J = 4.0 * A0 * A0 / loop
    G = E / (2.0 * (1.0 + nu))
    return {
        "A": A, "IY": IY, "IZ": IZ, "J": J, "G": G, "E": E, "nu": nu,
        "WT": IY / (h / 2.0), "WB": IY / (h / 2.0),
        "WY": IZ / (B / 2.0), "WTORS": J / max(tf, tw),
        "B": B, "H": h, "TW": tw, "TF": tf,
        "TKZ": h, "TKY": B,
    }


# ----------------------------------------------------------------- 建模器
def build_box_beam_3d(
    *,
    spans: tuple[float, ...] = (30.0, 30.0, 30.0),
    n_elem_per_span: int = 10,
    B: float = 8.0, h: float = 2.0,
    tw: float = 0.02, tf: float = 0.025,
    E: float = 2.06e11, nu: float = 0.2,
    udl_kN: float = 20.0,
    extra_loads: tuple[tuple[int, int, float], ...] = (),
    title: str = "三跨连续箱梁 3D 单梁线",
) -> ModelSpec3D:
    """三跨连续箱梁 3D 单梁线模型（X 纵向 / Z 竖向），产出 ``ModelSpec3D``。

    ``udl_kN`` 均布荷载被**节点化**为一致节点力（梯形分配：端节点 q·Le/2，
    内节点 q·Le），因为 FEM-Eigen 3D 内核不支持 ``*ELEMENT_LOAD``。
    ``extra_loads`` = ((node, dof, value_N_or_Nm), ...) 用于加横向力/扭矩。
    """
    sec = box_section_3d(B, h, tw, tf, E, nu)
    total = sum(spans)
    n_elem = n_elem_per_span * len(spans)
    le = total / n_elem
    # 节点：从 0 起等间距；所有支座恰落在单元边界上
    nodes = [Node3D(id=i + 1, x=le * i, y=0.0, z=0.0)
             for i in range(n_elem + 1)]
    elements = [Element3D(id=i + 1, etype=1, n1=i + 1, n2=i + 2)
                for i in range(n_elem)]

    # 支座节点：0 跨起点 + 各跨终点
    support_nodes = [1]
    acc = 0
    for s in spans:
        acc += s
        support_nodes.append(int(round(acc / le)) + 1)

    # 6 个刚体模式必须全部消除：
    #   node1 锁 UX/UY/UZ/ROTX；末端锁 UY/UZ（消除绕 Z、绕 Y 的刚体转动）
    #   各中间支座仅锁 UZ，形成多跨连续梁的竖向支承
    boundaries = [Boundary3D(node=1, dofs=(1, 4), value=0.0)]
    for nd in support_nodes[1:-1]:
        boundaries.append(Boundary3D(node=nd, dofs=(3, 3), value=0.0))
    boundaries.append(Boundary3D(node=support_nodes[-1],
                                 dofs=(2, 3), value=0.0))

    q = udl_kN * 1000.0
    loads = []
    for i in range(n_elem + 1):
        w = 0.5 if i in (0, n_elem) else 1.0
        p = -q * le * w
        if p:
            loads.append(NodalLoad3D(node=i + 1, dof=3, value=p))
    for nd, dof, val in extra_loads:
        loads.append(NodalLoad3D(node=nd, dof=dof, value=val))

    model = ModelSpec3D(
        title=title,
        nodes=nodes,
        materials=[Material3D(id=1, E=E)],
        reals=[RealConst3D(id=1, A=sec["A"], IY=sec["IY"], IZ=sec["IZ"],
                           J=sec["J"], G=sec["G"], WT=sec["WT"],
                           WB=sec["WB"], WY=sec["WY"], WTORS=sec["WTORS"])],
        etypes=[ElemTypeDef3D(id=1, kind="BEAM", mat=1, real=1)],
        elements=elements,
        boundaries=boundaries,
        loads=loads,
    )
    errs, _warns = validate_model_3d(model)
    if errs:
        raise ValueError("ModelSpec3D 校验未通过: " + "; ".join(errs))
    return model


# ----------------------------------------------------------------- APDL
def _num(x: float, fmt: str = "{:.12g}") -> str:
    return fmt.format(float(x))


def _dofs(a: int, b: int) -> list[int]:
    return list(range(a, b + 1))


def spec3d_to_apdl(model: ModelSpec3D, *, jobname: str,
                   element: str = "BEAM188",
                   title: str | None = None) -> str:
    """把 ``ModelSpec3D`` 转成可直接由 ANSYS180.exe -b 运行的 APDL 命令流。

    ``element="BEAM188"`` 用 ``SECTYPE/SECDATA``（Timoshenko 商业单元）；
    ``element="BEAM4"`` 用 ``R/RMORE`` 且 SHEARZ/SHEARY=0（Euler-Bernoulli
    严格基准）。两份命令流的节点/单元/材料/截面/约束/荷载完全同源。
    """
    element = element.upper()
    if element not in ("BEAM188", "BEAM4"):
        raise ValueError("只支持 BEAM188 / BEAM4，收到 " + element)
    errs, _ = validate_model_3d(model)
    if errs:
        raise ValueError("ModelSpec3D 校验未通过: " + "; ".join(errs))
    if len(model.etypes) != 1 or model.etypes[0].kind != "BEAM":
        raise ValueError("当前 3D APDL 导出只支持单一 BEAM 单元类型")
    if len(model.reals) != 1 or len(model.materials) != 1:
        raise ValueError("当前 3D APDL 导出只支持单一材料与单一实常数")
    mat, rc = model.materials[0], model.reals[0]
    node_ids = sorted(n.id for n in model.nodes)
    elem_ids = sorted(e.id for e in model.elements)
    if node_ids != list(range(1, len(node_ids) + 1)):
        raise ValueError("要求节点号从 1 连续编号")
    if elem_ids != list(range(1, len(elem_ids) + 1)):
        raise ValueError("要求单元号从 1 连续编号")
    if any(min(r.A, r.IY, r.IZ, r.J, r.G) <= 0 for r in model.reals):
        raise ValueError("3D 内核要求 A/IY/IZ/J/G 全部非零（否则零刚度自由度）")

    nnode, nelem = len(node_ids), len(elem_ids)
    # 显式给出 GXY（G 由 GXY 唯一决定，与 FEM-Eigen *R 的 G= 同源）；
    # PRXY 只用于后处理/应力输出，不参与刚度。
    E = mat.E
    prxy = 0.3
    # BEAM4 的 TKZ/TKY 是截面外廓尺寸（仅影响应力/自重输出，不影响刚度），
    # 由截面模量反推：WT=IY/(h/2) -> h=2IY/WT，WY=IZ/(B/2) -> B=2IZ/WY
    tkz = 2.0 * rc.IY / rc.WT if rc.WT else 1.0
    tky = 2.0 * rc.IZ / rc.WY if rc.WY else 1.0

    L: list[str] = []
    add = L.append
    add("/BATCH")
    # ANSYS 命令流保持纯 ASCII：中文标题转义，避免非 ASCII 注释引发编码问题
    apdl_title = (title or model.title).encode("ascii", "backslashreplace").decode("ascii")
    add("/TITLE,{} - {}".format(apdl_title, element))
    add("/PREP7")
    add("ET,1,{}".format(element))
    if element == "BEAM188":
        add("KEYOPT,1,1,0")     # 不引入第 7 个翘曲自由度
        add("! ASEC: integrated section properties given directly (no ANSYS I/J recompute)")
        add("SECTYPE,1,BEAM,ASEC")
        # SECDATA: A, IYY, IYZ, IZZ, IW, J, CGY, CGZ, SHY, SHZ
        add("SECDATA,{},{},0,{},0,{},0,0,0,0".format(
            _num(rc.A), _num(rc.IY), _num(rc.IZ), _num(rc.J)))
        add("SECNUM,1")
    else:
        add("! BEAM4 real constants: A, IZZ, IYY, TKZ, TKY, THETA")
        add("R,1,{},{},{},{},{},0.0".format(
            _num(rc.A), _num(rc.IZ), _num(rc.IY),
            _num(tkz), _num(tky)))
        add("! RMORE: ISTRN, IXX(=J torsion constant), SHEARZ, SHEARY, SPIN, ADDMAS")
        add("! SHEARZ/SHEARY=0 -> no transverse shear, pure Euler-Bernoulli")
        add("RMORE,0.0,{},0.0,0.0,0.0,0.0".format(_num(rc.J)))
        add("REAL,1")
    add("MP,EX,1,{}".format(_num(E)))
    add("MP,PRXY,1,{}".format(_num(prxy)))
    add("MP,GXY,1,{}".format(_num(rc.G)))
    add("! ---- Nodes (X longitudinal, Y transverse, Z vertical) ----")
    for n in sorted(model.nodes, key=lambda n: n.id):
        add("N,{},{},{},{}".format(n.id, _num(n.x), _num(n.y), _num(n.z)))
    add("TYPE,1")
    add("MAT,1")
    add("! ---- Elements ----")
    for e in sorted(model.elements, key=lambda e: e.id):
        if e.etype != model.etypes[0].id:
            raise ValueError("单元 {} 的 etype={} 不一致".format(e.id, e.etype))
        add("E,{},{}".format(e.n1, e.n2))
    add("! ---- Boundary conditions (6 DOF) ----")
    for b in model.boundaries:
        if b.k:
            raise ValueError("当前 3D APDL 导出不支持弹性支承（请用刚性约束）")
        for d in _dofs(*b.dofs):
            add("D,{},{},{}".format(b.node, DOF3_NAME[d], _num(b.value)))
    add("! ---- Nodal loads (same *CLOAD as FEM-Eigen 3D) ----")
    for ld in model.loads:
        add("F,{},{},{}".format(ld.node, DOF3_LOAD[ld.dof], _num(ld.value)))
    add("FINISH")
    add("/SOLU")
    add("ANTYPE,STATIC")
    add("SOLVE")
    add("FINISH")

    add("! ---- Post-processing: machine-readable CSV ----")
    add("/POST1")
    add("SET,LAST")
    # 节点位移：id UX UY UZ ROTX ROTY ROTZ
    add("*DIM,ADSP,ARRAY,{},7".format(nnode))
    add("*DO,I,1,{}".format(nnode))
    add(" ADSP(I,1)=I")
    for k, comp in enumerate(("X", "Y", "Z"), start=2):
        add(" *GET,TMP,NODE,I,U,{}".format(comp))
        add(" ADSP(I,{})=TMP".format(k))
    for k, comp in enumerate(("X", "Y", "Z"), start=5):
        add(" *GET,TMP,NODE,I,ROT,{}".format(comp))
        add(" ADSP(I,{})=TMP".format(k))
    add("*ENDDO")
    add("*CFOPEN,{}_disp,csv".format(jobname))
    add("*VWRITE," + ",".join("ADSP(1,{})".format(k) for k in range(1, 8)))
    add(" (1X,F8.0,6(1X,E18.10))")
    add("*CFCLOS")
    # 节点反力：id FX FY FZ MX MY MZ
    add("*DIM,ARCT,ARRAY,{},7".format(nnode))
    add("*DO,I,1,{}".format(nnode))
    add(" ARCT(I,1)=I")
    for k, comp in enumerate(("FX", "FY", "FZ", "MX", "MY", "MZ"), start=2):
        add(" *GET,TMP,NODE,I,RF,{}".format(comp))
        add(" ARCT(I,{})=TMP".format(k))
    add("*ENDDO")
    add("*CFOPEN,{}_reac,csv".format(jobname))
    add("*VWRITE," + ",".join("ARCT(1,{})".format(k) for k in range(1, 8)))
    add(" (1X,F8.0,6(1X,E18.10))")
    add("*CFCLOS")
    # 单元端内力：12 个 ETABLE，输出统一列 [N Vy Vz T My Mz]_I + _J
    smisc = SMISC_BEAM188 if element == "BEAM188" else SMISC_BEAM4
    labels = []
    for end, cols in zip(("I", "J"), smisc):
        for key, s in zip(FORCE_KEYS, cols):
            lab = "E{}{}".format(key.upper(), end)
            labels.append(lab)
            add("ETABLE,{},SMISC,{}".format(lab, s))
    add("*DIM,AELM,ARRAY,{},13".format(nelem))
    add("*DO,I,1,{}".format(nelem))
    add(" AELM(I,1)=I")
    for k, lab in enumerate(labels, start=2):
        add(" *GET,TMP,ELEM,I,ETAB,{}".format(lab))
        add(" AELM(I,{})=TMP".format(k))
    add("*ENDDO")
    add("*CFOPEN,{}_elem,csv".format(jobname))
    add("*VWRITE," + ",".join("AELM(1,{})".format(k) for k in range(1, 14)))
    add(" (1X,F8.0,12(1X,E18.10))")
    add("*CFCLOS")
    add("FINISH")
    add("/EXIT,NOSAVE")
    return "\n".join(L) + "\n"


# ----------------------------------------------------------------- 解析
def parse_fem_report_3d(path: Path) -> dict:
    """解析 ``example_3d_report.exe`` 产出的 ``<stem>.report.txt``。

    返回 ``{"disp": {node: [6 floats]}, "force": {elem: {6 keys: float}}}``。
    3D 内核的 .report.txt 只有单元 i 端内力，没有支座反力段。
    """
    disp: dict[int, list[float]] = {}
    force: dict[int, dict[str, float]] = {}
    section = ""
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if "节点位移" in line:
            section = "disp"
            continue
        if "单元内力" in line or "单元应力" in line or "支座反力" in line:
            section = "force" if "单元内力" in line else ""
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
            if len(vals) == 6:
                disp[key] = vals
        elif section == "force":
            kv = {}
            for t in toks[2:]:
                if "=" in t:
                    k, _, v = t.partition("=")
                    try:
                        kv[k] = float(v)
                    except ValueError:
                        pass
            if any(k in kv for k in FORCE_KEYS):
                force[key] = {k: kv.get(k, 0.0) for k in FORCE_KEYS}
    return {"disp": disp, "force": force}


def parse_ansys_csv_3d(path: Path) -> dict[int, list[float]]:
    """读 ANSYS ``*VWRITE`` 导出的表：首列 = 编号，其余为数值。"""
    out: dict[int, list[float]] = {}
    for raw in Path(path).read_text(encoding="ascii", errors="replace").splitlines():
        toks = raw.replace(",", " ").split()
        if len(toks) < 2:
            continue
        try:
            vals = [float(t) for t in toks]
        except ValueError:
            continue
        out[int(round(vals[0]))] = vals[1:]
    return out


# ----------------------------------------------------------------- 对比
def _fmt(x: float, width: int = 16) -> str:
    return "{:>{}.8g}".format(x, width)


def compare_3d(model: ModelSpec3D, fem: dict,
               b4disp: dict, b4reac: dict, b4elem: dict,
               b188disp: dict | None = None,
               b188reac: dict | None = None,
               b188elem: dict | None = None,
               *, stem: str, tol: float = 1e-3) -> tuple[str, bool]:
    """三层对比：严格校核 (FEM-Eigen 3D vs BEAM4) + BEAM188 差异量化。"""
    lines: list[str] = []
    ok_all = True
    add = lines.append
    add("=" * 92)
    add("3D 连续箱梁校核: {}  (FEM-Eigen 3D / ANSYS BEAM4 / ANSYS BEAM188)".format(stem))
    add("=" * 92)
    add("模型: {} 节点 / {} 单元 / 单一 BEAM / Z 竖向".format(
        len(model.nodes), len(model.elements)))
    rc = model.reals[0]
    add("截面: A={:.6g} m2  IY={:.6g} m4 (竖向)  IZ={:.6g} m4 (横向)  "
        "J={:.6g} m4  G={:.6g} Pa".format(rc.A, rc.IY, rc.IZ, rc.J, rc.G))
    add("说明: FEM-Eigen 3D 内核无 *ELEMENT_LOAD/*GRAVITY，均布荷载已节点化为")
    add("      *CLOAD(DOF=3)，ANSYS 侧用同一组 F,node,FZ，严格同源。")
    add("")

    # --- 第 1 层：节点位移 6 自由度，全量对比 ---
    add("[1] 严格校核 — 节点位移 (FEM-Eigen 3D vs ANSYS BEAM4, EB 对 EB)")
    allv: list[tuple[int, int, float, float]] = []
    for nd in sorted(fem["disp"]):
        f = fem["disp"][nd]
        a = b4disp.get(nd)
        if a is None or len(a) < 6:
            ok_all = False
            add("  缺少 BEAM4 节点 {} 位移".format(nd))
            continue
        for k in range(6):
            allv.append((nd, k + 1, f[k], a[k]))
    if allv:
        dmax = max(abs(f - a) for _, _, f, a in allv)
        scale = max(max(abs(f), abs(a)) for _, _, f, a in allv) or 1.0
        rel = dmax / scale
        ok = rel <= tol
        ok_all &= ok
        add("  {} 个节点 x 6 自由度 = {} 项; 最大绝对差 {:.6e}, 量级 {:.6e}, "
            "相对 {:.3e}  {}".format(len(fem["disp"]), len(allv), dmax, scale,
                                    rel, "PASS" if ok else "FAIL"))
        for lbl, k in (("UZ", 2), ("UY", 1), ("ROTX", 3), ("ROTY", 4)):
            pairs = [(f, a) for nd, d, f, a in allv if d == k + 1]
            if pairs:
                mx = max(abs(f) for f, _ in pairs)
                df = max(abs(f - a) for f, a in pairs)
                add("    {:<5s} 最大幅值 {:<16.8g} 最大差 {:<12.4g}".format(
                    lbl, mx, df))
    else:
        ok_all = False
        add("  无可比位移数据  FAIL")

    # --- 第 2 层：单元 i 端 6 分量内力（幅值，符号约定不同） ---
    add("")
    add("[2] 严格校核 — 单元 i 端内力幅值 (FEM-Eigen 3D vs ANSYS BEAM4)")
    add("    注: 两套内核端力符号约定不同，按幅值比较（与 2D 剪力处理一致）")
    pairs: list[tuple[int, str, float, float]] = []
    for e in sorted(fem["force"]):
        f = fem["force"][e]
        a = b4elem.get(e)
        if a is None or len(a) < 6:
            ok_all = False
            add("  缺少 BEAM4 单元 {} 端力".format(e))
            continue
        for k, key in enumerate(FORCE_KEYS):
            pairs.append((e, key, abs(f[key]), abs(a[k])))
    if pairs:
        dmax = max(abs(f - a) for _, _, f, a in pairs)
        scale = max(max(f, a) for _, _, f, a in pairs) or 1.0
        rel = dmax / scale
        ok = rel <= tol
        ok_all &= ok
        add("  {} 单元 x 6 分量 = {} 项; 最大绝对差 {:.6e}, 量级 {:.6e}, "
            "相对 {:.3e}  {}".format(len(fem["force"]), len(pairs), dmax, scale,
                                    rel, "PASS" if ok else "FAIL"))
        for key in FORCE_KEYS:
            sub = [(f, a) for _, k, f, a in pairs if k == key]
            add("    {:<3s} 最大幅值 {:<16.8g} 最大差 {:<12.4g}".format(
                key, max(f for f, _ in sub), max(abs(f - a) for f, a in sub)))
    else:
        ok_all = False
        add("  无可比端力数据  FAIL")

    # --- 第 3 层：ANSYS 反力与外荷载平衡自查 ---
    add("")
    add("[3] ANSYS BEAM4 反力 / 外荷载 竖向力平衡自查")
    add("    注: 只统计竖向支座节点；ANSYS 在无约束但有外荷载的节点上")
    add("        *GET,NODE,RF,FZ 返回的是节点荷载残差，不能计入反力。")
    fz_load = sum(ld.value for ld in model.loads if ld.dof == 3)
    sup = [b.node for b in model.boundaries if 3 in _dofs(*b.dofs)]
    fz_reac = sum(b4reac.get(nd, [0.0] * 6)[2] for nd in sup)
    bal = abs(fz_load + fz_reac)
    scale = max(abs(fz_load), 1.0)
    ok = bal / scale <= tol
    ok_all &= ok
    add("  外荷载 ΣFZ = {:.8g} N, 支座反力 ΣFZ = {:.8g} N, 残差 {:.6e} "
        "({:.3e})  {}".format(fz_load, fz_reac, bal, bal / scale,
                             "PASS" if ok else "FAIL"))
    add("  竖向支座 {} 个: {}".format(len(sup), sup))
    for nd in sup:
        v = b4reac.get(nd, [0.0] * 6)
        add("    node {:<4d} RF_FZ = {:>16.8g} N".format(nd, v[2]))

    # --- 第 4 层：BEAM188 差异量化（不参与 PASS/FAIL） ---
    add("")
    add("[4] 商业软件对照 — ANSYS BEAM188 (Timoshenko) 与 EB 基准的差异")
    if b188disp:
        # 近零位移处相对差会虚高（跨中反弯点），改用「最大绝对差 / 峰值」
        diffs = []
        for nd in sorted(b4disp):
            if nd not in b188disp:
                continue
            for k in range(6):
                f, a = b4disp[nd][k], b188disp[nd][k]
                diffs.append((abs(f - a), nd, k + 1, f, a))
        if diffs:
            dmax = max(diffs, key=lambda t: t[0])
            peak = max(max(abs(t[3]), abs(t[4])) for t in diffs) or 1.0
            add("  节点位移: 最大绝对差 {:.6g} / 位移量级 {:.6g} = {:.4%} "
                "(node {} DOF{}, BEAM4={:.8g}, BEAM188={:.8g})".format(
                    dmax[0], peak, dmax[0] / peak, dmax[1], dmax[2],
                    dmax[3], dmax[4]))
        # 只看竖向挠度峰值
        uz4 = {nd: v[2] for nd, v in b4disp.items() if len(v) >= 3}
        uz8 = {nd: v[2] for nd, v in b188disp.items() if len(v) >= 3}
        if uz4 and uz8:
            nd = max(uz4, key=lambda n: abs(uz4[n]))
            add("  竖向挠度峰值 node {}: BEAM4(EB)={:.8g} m, "
                "BEAM188={:.8g} m, 差 {:.4%}".format(
                    nd, uz4[nd], uz8[nd], (uz8[nd] - uz4[nd]) / uz4[nd]))
        add("  差异来源: BEAM188 基于一阶剪切变形(Timoshenko) + 线性形函数;")
        add("             FEM-Eigen 3D / BEAM4 为 Euler-Bernoulli 三次梁。")
        add("             实测单单元悬臂梁 BEAM188 挠度 = 0.859 EB (剪切锁定),")
        add("             细分后收敛到 1.0678 EB = EB + PL/(GA)。")
    else:
        add("  (未提供 BEAM188 位移结果)")

    add("")
    add("=" * 92)
    verdict = ("PASS —— 3D 建模 -> FEM-Eigen 3D 计算 -> ANSYS 校核 "
               "流程打通，与 BEAM4(EB) 严格一致；BEAM188 差异已量化"
               if ok_all else "FAIL —— 存在超差项，需排查")
    add("总判定: " + verdict)
    add("=" * 92)
    return "\n".join(lines) + "\n", bool(ok_all)
