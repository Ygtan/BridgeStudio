#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""I 阶段：自锚式悬索桥「自动平衡边跨 + 施工阶段找形」ANSYS 对标。

唯一数据源仍是 ``ModelSpec3D``；本模块只消费 ``cable_solver.exe`` 输出的
``[ERECTION_*]`` 段，不重演内核内部的吊索吊装规则。

数据链路
--------
    suspension_3d.build_suspension_3d(system="SELF", side_span=0,
                                       q2_deck=..., erection=True)
        -> cable_solver.exe  -> <stem>_cable.txt
             [ERECTION]        每阶段 H / 矢高 / 跨中高程 / 吊索力范围
             [ERECTION_SHAPE]  每阶段主跨主缆 13 个节点的高程
             [ERECTION_CHAIN]  主跨主缆节点 id / x（各阶段相同）
             [ERECTION_LOAD]   每阶段吊索搬运到主缆节点的竖向荷载 N
             [ERECTION_FORCE]  每阶段主跨 12 段主缆轴力 N
        -> stage_apdl()      -> 每阶段一份 ANSYS LINK180 + NLGEOM APDL
        -> stage_from_ansys()-> 阶段构形 / 索力 / 水平力
        -> compare_erection()  逐阶段对标报告

后 4 段由 I 阶段在 ``cable_solver.cpp`` 中新增；只在 ``*ERECTION`` 启用时
输出，A~H 阶段全部冻结 ``*_cable.txt`` 逐字节不变（17 个算例已对拍）。

对标口径：给定阶段下料长度 + 阶段荷载，从扰动几何出发独立求解
------------------------------------------------------------------
FEM 的 ``findErectionState`` 采用「主缆**总**无应力长度守恒、塔顶鞍座可滑」
的施工阶段口径：二分水平力 H 使三跨总 L0(H) 等于成桥下料总长，各段 L0
随 H 重新分配。ANSYS LINK180 则是「各段下料长度各自固定」。二者在**部分
荷载阶段不是同一个力学问题**——实测从成桥线形直接卸载时：

    铺装 stage5  线形差 3.4 mm（PASS）
    合拢 stage4  线形差 15 mm
    吊装 stage3  线形差 2.18 m
    吊装 stage1/2 低张力，ANSYS 不收敛

因此本模块采用工程上可严格闭环的口径：对每一阶段，把 FEM 找形给出的
**阶段构形 z_stage 与阶段轴力 T_stage** 换算成该阶段的等效下料长度

    L0_stage[k] = L_stage[k] / (1 + T_stage[k] / EA)

作为 ANSYS 的 ``INISTATE`` 初应变输入，施加该阶段的吊索节点荷载，并把
ANSYS 初始几何加一个 0.20 m 的正弦扰动（两端不动）。ANSYS 必须靠
NLGEOM 自己把索链收敛回平衡构形——不是从答案出发。对比收敛后的线形、
逐段索力与水平力，即验证「同一组下料长度 + 荷载，两套求解器给出同一
平衡态」。

空缆态（stage 0）不参与对标：内核把 H 二分到数值下界 100 N、矢高恒 0，
是退化直线态，ANSYS 无横向荷载索链存在多解/不稳定。报告如实列出。

自动平衡边跨
-------------
自锚式主缆锚于加劲梁端，梁端支座能承压不能承拉。令「主缆在锚点竖向
分力 V=0」解得边跨 ``L_bal = sqrt(2*H*dz_top/q)``。生成器在
``side_span=0`` 时自动取该值；``balance_report()`` 用支座反力给出量化
证据，并可与手动边跨的冻结结果对照。

坐标系：X 纵向 / Y 横向 / Z 竖向；向下荷载对应 ANSYS ``FZ < 0``。
"""
from __future__ import annotations

import math
from typing import Any

from model_spec_3d import ModelSpec3D  # noqa: E402

RTOL = 2e-3
#: ANSYS 初始几何扰动幅值（m），只加在中间主缆节点、两端保持不动。
PERTURB_AMP = 0.20
#: 空缆态的内核标记：H 命中二分下界、矢高为 0，不参与 ANSYS 对标。
SKIP_STAGE_NAMES = ("空缆",)


def _num(v: float, fmt: str = "{:.12g}") -> str:
    s = fmt.format(float(v))
    return "0" if s in ("-0", "-0.0") else s


def main_cable_EA(model: ModelSpec3D, res: dict) -> tuple[float, float, float]:
    """从 ``ModelSpec3D`` 解析主缆 E / A / EA，不写死常数。

    取 ``[MAINCABLE]`` 第一段对应的单元，经 ``etype -> (mat, real)`` 查材料
    与实常数。全桥主缆同材质同截面，取一段即可。
    """
    if not res.get("main_cable"):
        raise ValueError("结果中没有 [MAINCABLE] 段")
    eid = res["main_cable"][0]["elem"]
    elem = next((e for e in model.elements if e.id == eid), None)
    if elem is None:
        raise ValueError("模型里找不到主缆单元 {}".format(eid))
    tdef = next((t for t in model.etypes if t.id == elem.etype), None)
    mat = next((m for m in model.materials if m.id == tdef.mat), None)
    real = next((r for r in model.reals if r.id == tdef.real), None)
    if tdef is None or mat is None or real is None:
        raise ValueError("主缆单元的 etype / mat / real 绑定不完整")
    return float(mat.E), float(real.A), float(mat.E) * float(real.A)


def stage_input(res: dict, stage: int) -> dict[str, Any]:
    """取出一个阶段的同源输入与 FEM 参考值。"""
    for key in ("erection", "erection_shape", "erection_chain",
                "erection_load", "erection_force"):
        if not res.get(key):
            raise ValueError(
                "结果缺少 [{}] 段：请用 I 阶段重新编译的 cable_solver.exe".format(key))
    row = next((r for r in res["erection"] if r["stage"] == stage), None)
    if row is None:
        raise ValueError("没有 stage {}".format(stage))
    xs = [x for _, x in res["erection_chain"]]
    z = list(res["erection_shape"][stage])
    q = list(res["erection_load"][stage])
    T = list(res["erection_force"][stage])
    n = len(xs)
    if len(z) != n or len(q) != n or len(T) != n - 1:
        raise ValueError("stage {} 的 CHAIN/SHAPE/LOAD/FORCE 长度不一致".format(stage))
    return {"stage": stage, "name": row["name"], "row": row,
            "xs": xs, "z": z, "q": q, "T": T, "n": n}


def stage_unstrained(xs: list[float], z: list[float], T: list[float],
                     EA: float) -> list[float]:
    """阶段等效下料长度：``L0 = L_stage / (1 + T/EA)``（Lagrangian 工程应变）。"""
    out = []
    for k in range(len(xs) - 1):
        L = math.hypot(xs[k + 1] - xs[k], z[k + 1] - z[k])
        out.append(L / (1.0 + T[k] / EA))
    return out


def stage_apdl(inp: dict[str, Any], *, E: float, A: float,
                jobname: str, amp: float = PERTURB_AMP,
                title: str | None = None) -> tuple[str, dict[str, Any]]:
    """生成单阶段主跨主缆索链的 ANSYS ``LINK180 + NLGEOM`` 命令流。

    初始几何 = FEM 阶段线形 + ``amp*sin(pi*k/(n-1))`` 扰动；初应变由
    ``L_init / L0_stage - 1`` 逐段注入；荷载取 ``[ERECTION_LOAD]``。
    返回 ``(apdl_text, info)``，info 含 z_init / eps / L0 供报告与复核。
    """
    xs, z, q, T, n = inp["xs"], inp["z"], inp["q"], inp["T"], inp["n"]
    EA = E * A
    L0 = stage_unstrained(xs, z, T, EA)
    zi = [z[k] + amp * math.sin(math.pi * k / (n - 1)) for k in range(n)]
    eps = []
    for k in range(n - 1):
        Li = math.hypot(xs[k + 1] - xs[k], zi[k + 1] - zi[k])
        eps.append(Li / L0[k] - 1.0)

    lines: list[str] = []
    add = lines.append
    add("/TITLE,{}".format(title or ("Erection stage %d" % inp["stage"])))
    add("/PREP7")
    add("ET,1,LINK180")
    add("KEYOPT,1,2,0")
    add("! KEYOPT(3) left at 0: every stage keeps positive tension,")
    add("! tension-only would add needless on/off switching in NLGEOM.")
    add("R,1,{}".format(_num(A)))
    add("MP,EX,1,{}".format(_num(E)))
    for i, (x, zz) in enumerate(zip(xs, zi), 1):
        add("N,{}, {},0,{}".format(i, _num(x), _num(zz)))
    for k in range(n - 1):
        add("E,{},{}".format(k + 1, k + 2))
    add("! Tower-top chain ends fixed; out-of-plane UY fixed everywhere.")
    add("D,ALL,UY,0")
    add("D,1,UX,0")
    add("D,1,UZ,0")
    add("D,{},UX,0".format(n))
    add("D,{},UZ,0".format(n))
    add("INISTATE,SET,DTYP,EPEL")
    for k, ep in enumerate(eps, 1):
        add("INISTATE,DEFINE,{},,EPEL,X,{}".format(k, _num(ep, "{:.15g}")))
    add("/SOLU")
    add("ANTYPE,STATIC")
    add("NLGEOM,ON")
    add("NSUBST,60,300,10")
    add("NEQIT,100")
    add("OUTRES,ALL,LAST")
    add("! ---- hanger loads transferred to main-cable nodes (downward = -FZ) ----")
    for i, v in enumerate(q, 1):
        if abs(v) > 1e-12:
            add("F,{},FZ,{}".format(i, _num(-v, "{:.10g}")))
    add("SOLVE")
    add("/POST1")
    add("SET,LAST")
    add("*DIM,ADSP,ARRAY,{},4".format(n))
    for i in range(1, n + 1):
        add(" ADSP({},1)={}".format(i, i))
        add(" *GET,ADSP({},2),NODE,{},U,X".format(i, i))
        add(" *GET,ADSP({},3),NODE,{},U,Y".format(i, i))
        add(" *GET,ADSP({},4),NODE,{},U,Z".format(i, i))
    add("*CFOPEN,{}_disp,csv".format(jobname))
    add("*VWRITE,ADSP(1,1),ADSP(1,2),ADSP(1,3),ADSP(1,4)")
    add(" (1X,F8.0,3(1X,E18.10))")
    add("*CFCLOS")
    add("ETABLE,LKN,SMISC,1")
    add("*DIM,ALK,ARRAY,{},2".format(n - 1))
    for i in range(1, n):
        add(" ALK({},1)={}".format(i, i))
        add(" *GET,ALK({},2),ELEM,{},ETAB,LKN".format(i, i))
    add("*CFOPEN,{}_link,csv".format(jobname))
    add("*VWRITE,ALK(1,1),ALK(1,2)")
    add(" (1X,F8.0,1(1X,E18.10))")
    add("*CFCLOS")
    add("FINISH")
    info = {"z_init": zi, "eps": eps, "L0": L0, "EA": EA,
            "q_total": sum(q), "amp": amp, "n": n}
    return "\n".join(lines) + "\n", info


def stage_from_ansys(adisp: dict, alink: dict, *, xs: list[float],
                     z_init: list[float]) -> dict[str, Any]:
    """从 ANSYS CSV 还原阶段构形、逐段索力、水平力与派生几何量。"""
    n = len(xs)
    missing = [i for i in range(1, n + 1) if i not in adisp or len(adisp[i]) < 3]
    if missing or any(j not in alink for j in range(1, n)):
        raise ValueError("ANSYS CSV 缺少节点 {} / 单元数据".format(missing))
    x = [xs[i - 1] + adisp[i][0] for i in range(1, n + 1)]
    z = [z_init[i - 1] + adisp[i][2] for i in range(1, n + 1)]
    T = [alink[j][0] for j in range(1, n)]
    Hs = []
    for k in range(n - 1):
        dx, dz = x[k + 1] - x[k], z[k + 1] - z[k]
        Lk = math.hypot(dx, dz)
        Hs.append(T[k] * abs(dx) / Lk if Lk > 0 else 0.0)
    H = sum(Hs) / len(Hs)
    mid = n // 2
    return {"x": x, "z": z, "T": T, "H": H, "H_seg": Hs,
            "midz": z[mid], "sag": z[0] - min(z),
            "H_spread": max(Hs) - min(Hs),
            "max_uz": max(abs(adisp[i][2]) for i in range(1, n + 1))}


def compare_erection(res: dict, stage_results: dict[int, dict[str, Any]],
                     *, stem: str, amp: float = PERTURB_AMP,
                     tol: float = RTOL) -> tuple[str, bool]:
    """逐阶段对标报告：线形 / 主缆段索力 / 水平力，分组量级判据。"""
    lines: list[str] = []
    ok_all = True
    add = lines.append
    add("=" * 98)
    add("施工阶段找形逐阶段 ANSYS 对标: {}".format(stem))
    add("=" * 98)
    add("输入: FEM [ERECTION_SHAPE / CHAIN / LOAD / FORCE]（cable_solver.exe）")
    add("ANSYS: 主跨主缆 LINK180 索链（{} 节点 / {} 段）+ NLGEOM,ON".format(
        len(res["erection_chain"]), len(res["erection_chain"]) - 1))
    add("  初始几何 = FEM 阶段线形 + {:.2f} m 正弦扰动；两端塔顶固定".format(amp))
    add("  阶段下料长度 L0 = L_stage/(1+T_stage/EA)，逐段由 INISTATE 注入初应变")
    add("  荷载 = 该阶段吊索搬运到主缆节点的竖向力（同源，不重演吊装规则）")
    add("  ANSYS 从扰动几何独立收敛；不是从 FEM 答案出发")
    add("判据: 每组 scale=max(|FEM|,|ANSYS|)，逐项 |got-ref| <= scale*{:.1e}".format(tol))
    add("")
    skipped = [r["stage"] for r in res["erection"]
               if r["stage"] not in stage_results]
    if skipped:
        add("不参与对标: stage {} 空缆态（内核 H 二分命中下界 100 N、矢高 0，".format(
            ",".join(str(s) for s in skipped)))
        add("  为退化直线态；ANSYS 无横向荷载索链存在多解/不稳定，如实列出不隐藏）")
        add("")

    groups: dict[str, list[tuple[str, float, float]]] = {
        "shape": [], "force": [], "H": []}
    for st in sorted(stage_results):
        inp = stage_input(res, st)
        got = stage_results[st]
        for k, (fz, az) in enumerate(zip(inp["z"], got["z"])):
            groups["shape"].append(("s{}_z{}".format(st, k), fz, az))
        for k, (ft, at) in enumerate(zip(inp["T"], got["T"])):
            groups["force"].append(("s{}_T{}".format(st, k), abs(ft), abs(at)))
        groups["H"].append(("s{}_H".format(st),
                            abs(inp["row"]["H"]), abs(got["H"])))

    labels = {"shape": "主跨主缆线形 z", "force": "主缆段轴力 T",
              "H": "主缆水平力 H"}
    for gi, (key, pairs) in enumerate(groups.items(), 1):
        if not pairs:
            ok_all = False
            add("[{}] {} 无可比数据  FAIL".format(gi, labels[key]))
            continue
        dmax = max(abs(f - a) for _, f, a in pairs)
        scale = max([max(abs(f), abs(a)) for _, f, a in pairs] + [1e-30])
        rel = dmax / scale
        ok = rel <= tol
        ok_all &= ok
        add("[{}] {:<18s} {} 项; 最大绝对差 {:.6e}, 量级 {:.6e}, "
            "相对 {:.3e}  {}".format(gi, labels[key], len(pairs), dmax,
                                    scale, rel, "PASS" if ok else "FAIL"))
        worst = max(pairs, key=lambda p: abs(p[1] - p[2]))
        add("    最大差项: {}  FEM={:.8g}  ANSYS={:.8g}".format(
            worst[0], worst[1], worst[2]))

    add("")
    add("逐阶段摘要（FEM / ANSYS）")
    add("  {:<4s} {:<9s} {:<14s} {:<14s} {:<12s} {:<12s} {:<12s}".format(
        "st", "名称", "qsum(kN)", "H(kN)", "midz(m)", "sag(m)", "max|dz|(m)"))
    for st in sorted(stage_results):
        inp = stage_input(res, st)
        got = stage_results[st]
        zf = inp["z"]
        dz = max(abs(a - b) for a, b in zip(zf, got["z"]))
        add("  {:<4d} {:<9s} {:<14.1f} {:<14.1f} {:<12.5f} {:<12.5f} {:<12.3e}".format(
            st, inp["name"], sum(inp["q"]) / 1000.0,
            got["H"] / 1000.0, got["midz"], got["sag"], dz))
    add("")
    add("FEM 阶段序列（[ERECTION]）：")
    for r in res["erection"]:
        add("  stage {} {:<8s} H={:12.1f} kN  sag={:9.5f} m  midz={:9.5f} m".format(
            r["stage"], r["name"], r["H"] / 1000.0,
            r["sag"], r["midz"]))
    add("")
    add("口径边界: FEM 施工阶段采用「主缆总无应力长度守恒、塔顶鞍座可滑」")
    add("  二分 H；ANSYS LINK180 是「各段下料长度固定」。从成桥线形直接")
    add("  卸载到部分荷载阶段时两者不是同一问题，实测 stage5/4/3 线形差")
    add("  3.4 mm / 15 mm / 2.18 m，stage1/2 低张力不收敛。本算例对标的是")
    add("  「给定阶段下料长度 + 阶段荷载，两套软件给出同一平衡态」，不是")
    add("  ANSYS 独立预测 FEM 的鞍座滑移口径。")
    add("")
    add("=" * 98)
    add("总判定: " + ("PASS —— 施工阶段逐阶段 ANSYS 对标通过"
                      if ok_all else "FAIL —— 存在超差项，需排查"))
    add("=" * 98)
    return "\n".join(lines) + "\n", bool(ok_all)


def balance_report(res: dict, info: dict, *, stem: str,
                    unbalanced: dict | None = None,
                    tol: float = 1e-3) -> tuple[str, bool]:
    """自动平衡边跨量化报告：锚点主缆竖向分力 V≈0 的支座反力证据。

    ``unbalanced`` 可传手动边跨算例的 ``parse_cable_result()`` 结果做对照。
    """
    lines: list[str] = []
    add = lines.append
    add("=" * 98)
    add("自锚式自动平衡边跨量化验证: {}".format(stem))
    add("=" * 98)
    L_main = float(info["main_span"])
    q = float(info["deck_load"])
    total = q * L_main
    L_side = float(info["side_span"])
    L_bal = float(info["side_span_balanced"])
    anchors = [int(n) for n in info["anchor_nodes"]]
    rz = {k[0]: v for k, v in res["reaction"].items() if k[1] == 3}
    rx = {k[0]: v for k, v in res["reaction"].items() if k[1] == 1}
    end_rz = [rz.get(n, 0.0) for n in anchors]
    ratio = max(abs(v) for v in end_rz) / total if total > 0 else float("inf")
    auto_ok = bool(info["side_span_auto"])
    geom_ok = abs(L_side - L_bal) <= 1e-9
    balance_ok = ratio <= tol
    ok = auto_ok and geom_ok and balance_ok

    add("平衡边跨条件 V=0:  L_bal = sqrt(2*H*dz_top/q) = {:.6f} m".format(L_bal))
    add("生成器 side_span=0 -> 自动取 L_bal: {}；实际边跨 {:.6f} m".format(
        "是" if auto_ok else "否", L_side))
    add("边跨/主跨 = {:.4f}（地锚式常规约 1/3；自锚式因 V=0 显著偏长）".format(
        L_side / L_main))
    add("几何一致性 |L_side - L_bal| = {:.3e} m  {}".format(
        abs(L_side - L_bal), "PASS" if geom_ok else "FAIL"))
    add("")
    add("梁端（主缆锚点）竖向反力 Rz，负号 = 支座受拔：")
    for n, v in zip(anchors, end_rz):
        add("  节点 {:<6d} Rz = {:12.3f} kN".format(n, v / 1000.0))
    add("  max|Rz| / (q_deck * L_main) = {:.6e}  判据 <= {:.1e}  {}".format(
        ratio, tol, "PASS" if balance_ok else "FAIL"))
    add("")
    add("塔顶纵向反力 Rx（两侧主缆水平力自平衡后 residual）：")
    for n, v in sorted(rx.items()):
        add("  节点 {:<6d} Rx = {:12.3f} kN".format(n, v / 1000.0))
    if rx:
        add("  max|Rx| / H = {:.6e}（FEM H = {:.1f} kN）".format(
            max(abs(v) for v in rx.values()) /
            max(abs(float(res["meta"].get("H_kN", 0)) * 1000.0), 1.0),
            float(res["meta"].get("H_kN", 0))))
    add("")
    if unbalanced is not None:
        uinfo_anchors = [int(n) for n in anchors]
        urz = {k[0]: v for k, v in unbalanced["reaction"].items() if k[1] == 3}
        uv = [urz.get(n, 0.0) for n in uinfo_anchors]
        add("对照：手动 40 m 边跨（CB3D12，同一主跨/荷载/索面积）")
        for n, v in zip(uinfo_anchors, uv):
            add("  节点 {:<6d} Rz = {:12.3f} kN".format(n, v / 1000.0))
        add("  max|Rz| / (q_deck*L_main) = {:.6e}".format(
            max(abs(v) for v in uv) / total))
        add("  -> 边跨不足使主缆在锚点产生上拔，需要抗拔支座或压重；")
        add("     自动平衡边跨把该力压到近零，这正是 V=0 条件的工程意义。")
        add("")
    add("=" * 98)
    add("总判定: " + ("PASS —— 自动平衡边跨成立（梁端无抗拔需求）"
                      if ok else "FAIL —— 自动平衡边跨判据不满足"))
    add("=" * 98)
    return "\n".join(lines) + "\n", ok
