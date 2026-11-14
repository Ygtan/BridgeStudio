#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""H 阶段：悬索桥（地锚式 / 自锚式）FEM-Eigen 找形 -> ANSYS 18.0 NLGEOM 对标。

数据链路（唯一数据源仍是 BridgeStudio 的 ModelSpec3D）：

    suspension_3d.build_suspension_3d(system=EARTH|SELF)
        -> to_dat() -> cable_solver.exe -> <stem>_cable.txt
             [CABLE]     每根主缆段/吊索的 T / L0 / chord（平衡态弦长）
             [MAINCABLE] 主缆分段；[HANGER] 吊索；[U]、[REACTION]
             [CAMBER]    预拱度建议值 = -[U]
             [ERECTED]   制造线形 = 设计几何 - [U]
        -> design_geometry() 重建 **设计几何** D = [ERECTED] + [U]
        -> ansys_bridge_nl.spec_nl_to_apdl(coords=D, l0=[CABLE].L0)
             ANSYS BEAM4 + LINK180 + NLGEOM + INISTATE
             eps = (L_D - L0) / L0
        -> compare_susp() 位移 / 索力 / 反力逐项对比

**为什么初始几何不是 [ERECTED]（与 G 阶段斜拉桥不同）**
------------------------------------------------------------
G 阶段的斜拉桥走 ``solveCompletionState3D``：制造线形是闭环迭代的未知量，
L0 相对 [ERECTED] 反算，所以 ANSYS 必须从 [ERECTED] 出发。

悬索桥走 ``cable_solver.cpp`` 的 FormFinding 分支（内核源码注释明确）：
主缆节点坐标不是可制造量，可制造的是**无应力索长**；把节点坐标当未知量
会让方程数少于未知量、迭代漂移到松弛构型。因此该分支是
「找形给出设计几何与索力 -> 由变形后弦长闭式反算 L0 -> 解一次几何非线性」。
其中：

  * ``[U]`` 是**从设计几何出发**的一次 Newton 位移（毫米级弹性压缩量），
    不是「从制造线形出发的成桥位移」；
  * ``[CAMBER] = -[U]``、``[ERECTED] = D - [U]`` 只是**预拱度建议值**，
    不参与 L0 反算，二者不构成 G 阶段那种闭环；
  * ``closure_err`` 定义为 max|[U]|，是位移量级而非迭代残差。

所以 ANSYS 必须与设计几何 D 同起点、使用同一批绝对 L0。实测若误用
[ERECTED] 作初始几何，位移相对差达 0.25（FAIL）；改用 D 后降到 1e-3
量级（PASS）。D 由 [ERECTED] + [U] 逐节点重建，不依赖 .dat 里那条
只作初值用的抛物线，也不依赖内核是否把找形线形写回 m.nodes。

两种体系的差别（除建模外不改变对标口径）
------------------------------------------
  * EARTH 地锚式：主缆锚在桥台外侧锚碇（全约束），水平力 H 传给地基；
    加劲梁端纵向固定，梁不承受 H。
  * SELF 自锚式：主缆锚在加劲梁端节点，H 压进加劲梁；纵向约束必须放在
    跨中（对称荷载下该支反力恒为零），否则 H 被支座吃掉、梁轴力算成 0。

ANSYS 语义与 G 阶段相同（探测脚本归档在
FEM-Eigen/tests/benchmark/nonlinear_ansys_3d/probes/）：
LINK180 的 ``R,id,A,ISTRN`` 无效，初应变必须走 INISTATE；
``KEYOPT(3)=1`` = tension-only；NLGEOM 下 Almansi 应变与 FEM 共转列式
Lagrangian 工程应变一阶一致，差异 O(eps^2)，故索初应变控制在 1e-3 量级；
竖直塔柱 BEAM4 用方向节点 K(+Y) 对齐 FEM 局部轴。

坐标系：X 纵向 / Y 横向 / Z 竖向；竖向荷载 = DOF3 = ANSYS FZ。
"""
from __future__ import annotations

from model_spec_3d import ModelSpec3D  # noqa: E402

RTOL = 2e-3
SYSTEM_LABEL = {
    "EARTH": "地锚式悬索桥（主缆锚于锚碇，H 传地基）",
    "SELF": "自锚式悬索桥（主缆锚于加劲梁端，H 压进加劲梁）",
}


def design_geometry(res: dict) -> dict[int, tuple[float, float, float]]:
    """由 ``[ERECTED] + [U]`` 重建设计几何 D。

    内核 FormFinding 分支写 ``camber = -u``、``erected = D + camber``，
    且 ``[U]`` 段输出的就是同一个 u（相对 D）。因此 D = ERECTED + U 是
    恒等式，不依赖 .dat 初值线形。缺失的自由度按 0 处理（转动不参与）。
    """
    out: dict[int, tuple[float, float, float]] = {}
    for nd, (x, y, z) in res["erected"].items():
        out[nd] = (
            x + float(res["u"].get((nd, 1), 0.0)),
            y + float(res["u"].get((nd, 2), 0.0)),
            z + float(res["u"].get((nd, 3), 0.0)),
        )
    return out


def unstrained_lengths(res: dict) -> dict[int, float]:
    """FEM 找形反算的逐索绝对下料长度 L0（ANSYS INISTATE 的唯一输入）。"""
    return {c["elem"]: c["L0"] for c in res["cables"]}


def strain_range(model: ModelSpec3D, coords: dict,
                 l0: dict[int, float]) -> tuple[float, float]:
    """返回 ANSYS 初应变 eps=(L_D-L0)/L0 的最小/最大值，用于报告与体检。"""
    kinds = {t.id: t.kind for t in model.etypes}
    vals: list[float] = []
    for e in model.elements:
        if kinds[e.etype] != "CABLE" or e.id not in l0:
            continue
        p1, p2 = coords[e.n1], coords[e.n2]
        L = sum((b - a) ** 2 for a, b in zip(p1, p2)) ** 0.5
        vals.append(abs((L - l0[e.id]) / l0[e.id]))
    if not vals:
        raise ValueError("模型中没有可计算初应变的 CABLE 单元")
    return min(vals), max(vals)


def compare_susp(res: dict, adisp: dict, areac: dict, alink: dict, *,
                 stem: str, system: str, model: ModelSpec3D,
                 coords: dict, eps_range: tuple[float, float],
                 tol: float = RTOL) -> tuple[str, bool]:
    """悬索桥三项对标：设计几何起点的位移 / 索力 / 支座反力。"""
    lines: list[str] = []
    ok_all = True
    add = lines.append
    md = res["meta"]

    def check(label: str, pairs: list[tuple[str, float, float]]) -> None:
        nonlocal ok_all
        if not pairs:
            ok_all = False
            add("  {} 无可比数据  FAIL".format(label))
            return
        dmax = max(abs(f - a) for _, f, a in pairs)
        scale = max([max(abs(f), abs(a)) for _, f, a in pairs] + [1e-30])
        rel = dmax / scale
        ok = rel <= tol
        ok_all &= ok
        add("  {:<22s} {} 项; 最大绝对差 {:.6e}, 量级 {:.6e}, "
            "相对 {:.3e}  {}".format(label, len(pairs), dmax, scale,
                                    rel, "PASS" if ok else "FAIL"))
        worst = max(pairs, key=lambda p: abs(p[1] - p[2]))
        add("    最大差项: {}  FEM={:.8g}  ANSYS={:.8g}".format(
            worst[0], worst[1], worst[2]))

    add("=" * 96)
    add("悬索桥成桥态对标: {}  ({})".format(stem, SYSTEM_LABEL.get(system, system)))
    add("=" * 96)
    add("模型: {} 节点 / {} 单元；主缆 {} 段 + 吊索 {} 根 = {} 根 CABLE".format(
        len(model.nodes), len(model.elements), len(res["main_cable"]),
        len(res["hangers"]), len(res["cables"])))
    add("FEM: cable_solver.exe FormFinding 分支（*SUSP_BRIDGE SYSTEM={}）".format(
        system))
    add("  converged={}, iterations={}, H={:.1f} kN, 主跨矢高={:.6g} m, "
        "目标矢高={:.6g} m".format(
            res.get("converged"), md.get("iterations"),
            float(md.get("H_kN", 0)), float(md.get("sag_m", 0)),
            float(md.get("sag_target_m", 0))))
    add("  closure_err={:.3e} m（= max|[U]|，位移量级而非迭代残差）, "
        "tension_err={:.3e}".format(
            float(md.get("closure_err", float("nan"))),
            float(md.get("tension_err", float("nan")))))
    add("")
    add("ANSYS: BEAM4 + LINK180(KEYOPT(3)=1 tension only) + NLGEOM,ON；")
    add("  初始几何 = 设计几何 D = [ERECTED] + [U]（**不是** [ERECTED]）；")
    add("  索下料长度 = FEM [CABLE] 的绝对 L0，初应变 eps=(L_D-L0)/L0")
    add("  由 INISTATE,SET,DTYP,EPEL + INISTATE,DEFINE,<e>,,EPEL,X,eps 逐索注入。")
    add("  eps 范围 {:.6g} .. {:.6g}（控制在 1e-3 量级以压住应变度量二阶差）".format(
        eps_range[0], eps_range[1]))
    add("  索自重 W=0 -> 关闭垂度/Ernst（ANSYS LINK180 无垂度模型）；")
    add("  竖直塔柱 BEAM4 由方向节点 K(+Y) 对齐 FEM 局部轴 ey=+X/ez=+Y。")
    add("本构口径: FEM 共转列式索 N=EA/L0*(Ln-L0)（Lagrangian 工程应变）；")
    add("          ANSYS NLGEOM 用 Almansi 应变，两者一阶一致、差异 O(eps^2)。")
    add("判据: 每组取 scale=max(|FEM|,|ANSYS|)，逐项 |got-ref| <= scale*{:.1e}".format(tol))
    add("ANSYS 提示 KEYOPT(3)=1 为 undocumented option：实测压缩时轴力归零，")
    add("       行为确为 tension-only；两处 CABLE 单元类型各一条 warning，如实保留。")
    add("")
    add("口径提示: 悬索桥 FormFinding 不是斜拉桥的制造线形闭环；[CAMBER]/")
    add("          [ERECTED] 只是预拱度建议，不参与 L0 反算。若误把 [ERECTED]")
    add("          当作 ANSYS 初始几何，实测位移相对差 0.25（FAIL）。")
    add("")

    add("[1] 节点位移（相对设计几何 D，FEM [U] vs ANSYS U）")
    dp: list[tuple[str, float, float]] = []
    for (nd, dof), fv in sorted(res["u"].items()):
        row = adisp.get(nd)
        if row is None or len(row) < dof:
            ok_all = False
            add("  缺少 ANSYS 节点 {} 位移".format(nd))
            continue
        dp.append(("n{}_d{}".format(nd, dof), fv, row[dof - 1]))
    check("位移", dp)

    add("")
    add("[2] 成桥索力（主缆分段 + 吊索，LINK180 SMISC 1）")
    cp: list[tuple[str, float, float]] = []
    for c in res["cables"]:
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
    if res["hangers"]:
        ht = [h["T"] for h in res["hangers"]]
        add("  吊索力 {:.1f} .. {:.1f} kN（FEM [HANGER]）".format(
            min(ht) / 1000.0, max(ht) / 1000.0))

    add("")
    add("[3] 支座反力（FEM [REACTION]）")
    rp: list[tuple[str, float, float]] = []
    for (nd, dof), fv in sorted(res["reaction"].items()):
        row = areac.get(nd)
        if row is None or len(row) < dof:
            ok_all = False
            add("  缺少 ANSYS 节点 {} 反力".format(nd))
            continue
        rp.append(("n{}_d{}".format(nd, dof), fv, row[dof - 1]))
    check("反力", rp)
    if system == "EARTH":
        add("  地锚式：锚碇节点全约束，主缆水平力 H 由锚碇传给地基。")
    else:
        add("  自锚式：主缆锚于加劲梁端，H 以轴压形式进入加劲梁；")
        add("          纵向约束置于跨中，对称荷载下其支反力恒为零。")

    add("")
    add("=" * 96)
    add("总判定: " + ("PASS —— {}成桥态与 ANSYS NLGEOM+LINK180 对标通过".format(
        SYSTEM_LABEL.get(system, system).split("（")[0])
        if ok_all else "FAIL —— 存在超差项，需排查"))
    add("=" * 96)
    return "\n".join(lines) + "\n", bool(ok_all)
