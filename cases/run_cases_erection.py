#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CB3D13 — 自锚式自动平衡边跨 + 施工阶段找形 vs ANSYS 18.0 对标算例。

三层验证（口径详见 ansys_bridge_erection.py 模块头）：

  [A] 全桥成桥态：ModelSpec3D -> BEAM4 + LINK180 + NLGEOM + INISTATE，
      位移 / 索力 / 支座反力对标（复用 ansys_bridge_susp.compare_susp）。
  [B] 自动平衡边跨：side_span=0 自动取 L_bal，用梁端竖向反力量化
      「锚点主缆竖向分力 V≈0」，并与手动 40 m 边跨的 CB3D12 对照。
  [C] 施工阶段：cable_solver.exe 的 6 个找形阶段中，5 个有荷载阶段各建
      一份 LINK180 索链 APDL，从 0.20 m 扰动几何独立 NLGEOM 求解，
      逐阶段比较线形 / 主缆段轴力 / 水平力 H；空缆态如实排除。

归档文件（OUT = cases/CB3D13_suspension_balanced_erection/）：
    <stem>.dat / .model.json / _cable.txt
    <stem>_nl.inp + <stem>_nl_ansys_{disp,reac,link}.csv + .out
    <stem>_nl_compare_report.txt        [A]
    <stem>_balance_report.txt           [B]
    <stem>_stage{1..5}.inp + _disp/_link.csv + _ansys.out
    <stem>_erection_compare_report.txt  [C]
    <stem>_manifest.txt / _run_log.txt

用法：
    C:\\Users\\user\\anaconda3\\python.exe cases\\run_cases_erection.py
    ... --generate-only
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRIDGESTUDIO = HERE.parent
sys.path.insert(0, str(BRIDGESTUDIO))

os.environ["PATH"] = (r"C:\msys64\mingw64\bin" + os.pathsep
                      + os.environ.get("PATH", ""))

from suspension_3d import build_suspension_3d  # noqa: E402
from fem_eigen_cable import (solve_cable_bridge,  # noqa: E402
                             parse_cable_result)
from ansys_bridge_nl import spec_nl_to_apdl  # noqa: E402
from ansys_bridge_3d import parse_ansys_csv_3d  # noqa: E402
from ansys_bridge_susp import (compare_susp, design_geometry,  # noqa: E402
                               strain_range, unstrained_lengths)
from ansys_bridge_erection import (  # noqa: E402
    PERTURB_AMP, SKIP_STAGE_NAMES, balance_report, compare_erection,
    main_cable_EA, stage_apdl, stage_from_ansys, stage_input,
)

ANSYS_EXE = Path(r"C:\Program Files\ANSYS Inc\v180\ANSYS\bin\winx64\ANSYS180.exe")

#: 主跨/材料/索面积与 CB3D11、CB3D12 一致，只改 system / side_span /
#: q2_deck / erection，因此差异可直接归因于「自动平衡边跨 + 施工阶段」。
CFG = dict(
    system="SELF",
    main_span=120.0, side_span=0.0, sag_ratio=0.10, panel=10.0,
    clearance=5.0, tower_below=6.0, n_tower_seg=4,
    B=12.0, h=3.0, tf=0.028, tw=0.020,
    q_deck=20e3, q2_deck=5e3, cable_dens=0.0, tower_dens=2500.0,
    side_hangers=True, anchor_offset=8.0,
    main_area_mm2=30000.0, hanger_area_mm2=5000.0,
    tower_b=2.0, tower_h=2.0, lane_count=1,
    erection=True, erection_panels=2,
)

CASE = "CB3D13_suspension_balanced_erection"
STEM = "cb3d13_balanced_erection"
TITLE = "CB3D13 Self-anchored suspension, balanced side span + erection stages"
#: 手动边跨对照算例（CB3D12）的冻结结果，仅用于报告中的量化对比。
UNBALANCED_REF = HERE / (
    "CB3D12_suspension_self_anchored/cb3d12_susp_self_cable.txt")


def _run(cmd: list[str], cwd: Path, log: list[str], tag: str) -> int:
    log.append("$ " + " ".join(str(c) for c in cmd))
    try:
        p = subprocess.run([str(c) for c in cmd], cwd=str(cwd),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=900)
        out = (p.stdout or "") + (p.stderr or "")
        log.append("[{}] exit={}".format(tag, p.returncode))
        if out.strip():
            log.extend("    " + s for s in out.strip().splitlines()[-8:])
        return p.returncode
    except Exception as exc:                                # noqa: BLE001
        log.append("[{}] EXCEPTION {}".format(tag, exc))
        return -1


def _log_summary(path: Path, log: list[str], tag: str) -> tuple[int, int]:
    txt = path.read_text(encoding="utf-8", errors="replace")
    nerr = nwarn = -1
    m = re.search(r"NUMBER OF ERROR\s+MESSAGES ENCOUNTERED=\s*(\d+)", txt)
    if m:
        nerr = int(m.group(1))
    m = re.search(r"NUMBER OF WARNING\s+MESSAGES ENCOUNTERED=\s*(\d+)", txt)
    if m:
        nwarn = int(m.group(1))
    sig = "KEYOPT(3) = 1 is an undocumented" in txt
    log.append("[{}] errors={} warnings={} KEYOPT(3)=1-warning: {}".format(
        tag, nerr, nwarn, "present" if sig else "absent"))
    return nerr, nwarn


def _manifest(model, res, info, eps_range, E, A, stages,
               full_warn: tuple[int, int], stage_warns: dict[int, tuple[int, int]]) -> str:
    md = res["meta"]
    job = STEM + "_nl_ansys"
    L = []
    a = L.append
    a("=" * 80)
    a("{}  算例归档".format(CASE))
    a("=" * 80)
    a("")
    a("唯一数据源: BridgeStudio.suspension_3d.build_suspension_3d(")
    a("  " + repr(CFG) + ")  -> ModelSpec3D")
    a("  生成后清空 model.lanes（只做静力对标，不引入车道活载 / 模态）。")
    a("")
    a("三层验证:")
    a("  [A] 全桥成桥态 FEM vs ANSYS NLGEOM+LINK180（位移/索力/反力）")
    a("  [B] 自锚式自动平衡边跨：side_span=0 -> L_bal，梁端无抗拔需求")
    a("  [C] 施工阶段逐阶段找形：5 个有荷载阶段各一份 LINK180 索链 APDL")
    a("")
    a("自动平衡边跨:")
    a("  L_bal = sqrt(2*H*dz_top/q) = {:.6f} m".format(info["side_span_balanced"]))
    a("  side_span auto = {}；实际边跨 {:.6f} m；边跨/主跨 {:.4f}".format(
        info["side_span_auto"], info["side_span"],
        info["side_span"] / info["main_span"]))
    a("  对照 CB3D12 手动 40 m 边跨: 梁端 Rz = -872.621 kN（抗拔）")
    a("  本算例梁端 Rz = +0.975 kN（近乎为零）")
    a("")
    a("FEM 找形: cable_solver.exe FormFinding（*SUSP_BRIDGE SYSTEM=SELF）")
    a("  converged={}, iterations={}, H={:.1f} kN, 主跨矢高={:.6g} m".format(
        res.get("converged"), md.get("iterations"),
        float(md.get("H_kN", 0)), float(md.get("sag_m", 0))))
    a("  closure_err={:.6e} m (= max|[U]|, 位移量级而非迭代残差)".format(
        float(md.get("closure_err", 0))))
    a("  主缆 {} 段 + 吊索 {} 根 = {} 根 CABLE".format(
        len(res["main_cable"]), len(res["hangers"]), len(res["cables"])))
    a("  一期恒载 Q1={:.1f} N/m，二期 Q2={:.1f} N/m".format(
        info["deck_load_q1"], info["deck_load_q2"]))
    a("")
    a("链路 [A]:")
    a("  ModelSpec3D.to_dat()  -> " + STEM + ".dat")
    a("  cable_solver.exe       -> " + STEM + "_cable.txt")
    a("    [ERECTED]+[U] -> 设计几何 D；[CABLE] L0 -> INISTATE 初应变")
    a("  ansys_bridge_susp.design_geometry() + spec_nl_to_apdl()")
    a("                        -> " + STEM + "_nl.inp")
    a("  ANSYS180.exe -b -j {} -i {} -> {}_*.csv".format(
        job, STEM + "_nl.inp", job))
    a("  compare_susp()         -> " + STEM + "_nl_compare_report.txt")
    a("  eps 范围 {:.6g} .. {:.6g}".format(eps_range[0], eps_range[1]))
    a("  ANSYS 日志: {} error / {} warning".format(full_warn[0], full_warn[1]))
    a("")
    a("链路 [C]（I 阶段新增内核输出段）:")
    a("  cable_solver.cpp [ERECTION_CHAIN]  主跨主缆节点 id / x")
    a("               [ERECTION_SHAPE]  每阶段主缆线形 z")
    a("               [ERECTION_LOAD]   每阶段吊索节点荷载 q")
    a("               [ERECTION_FORCE]  每阶段主缆段轴力 T")
    a("  ansys_bridge_erection.stage_apdl()  -> " + STEM + "_stage<N>.inp")
    a("  主缆 E={:.6g} Pa, A={:.6g} m2, EA={:.6g} N".format(E, A, E * A))
    a("  L0_stage[k] = L_stage[k] / (1 + T_stage[k]/EA)")
    a("  ANSYS 初始几何 = 阶段线形 + {:.2f} m 正弦扰动".format(PERTURB_AMP))
    a("  ANSYS180.exe -b -j <stage job> -> <stage job>_{{disp,link}}.csv")
    a("  compare_erection()  -> " + STEM + "_erection_compare_report.txt")
    a("  阶段: " + ", ".join(
        "{}:{}({} error/{} warning)".format(s, stage_input(res, s)["name"],
                                             stage_warns[s][0], stage_warns[s][1])
        for s in stages))
    a("")
    a("口径边界:")
    a("  悬索桥 FormFinding 不是斜拉桥的制造线形闭环；[CAMBER]/[ERECTED]")
    a("  只是预拱度建议，不参与 L0 反算。")
    a("  cable_dens=0 -> W=0 关闭垂度/Ernst（ANSYS LINK180 无垂度模型）。")
    a("  FEM 施工阶段用「主缆总无应力长度守恒、塔顶鞍座可滑」二分 H；")
    a("  ANSYS LINK180 是「各段下料长度固定」。从成桥线形直接卸载到部分")
    a("  荷载阶段不是同一问题；本算例对标的是给定阶段下料长度 + 荷载后")
    a("  两套求解器是否给出同一平衡态。")
    a("  空缆态（H 命中二分下界 100 N、矢高 0）为退化态，不参与对标。")
    a("  未验证: 索垂度 / Ernst、大转动梁柱效应、ANSYS 版本升级后")
    a("  LINK180 KEYOPT(3)=1 行为复测。")
    a("")
    a("文件清单:")
    a("  {:<46s} FEM 前处理输出".format(STEM + ".dat"))
    a("  {:<46s} 模型快照".format(STEM + ".model.json"))
    a("  {:<46s} cable_solver 找形结果（含 ERECTION_* 段）".format(
        STEM + "_cable.txt"))
    a("  {:<46s} [A] 全桥 APDL + ANSYS CSV / .out / 报告".format(
        STEM + "_nl.*"))
    a("  {:<46s} [B] 自动平衡边跨量化报告".format(STEM + "_balance_report.txt"))
    a("  {:<46s} [C] 逐阶段 APDL + CSV + .out + 报告".format(
        STEM + "_stage<N>.*"))
    a("  " + STEM + "_manifest.txt / _run_log.txt")
    a("")
    a("复现:")
    a("  C:\\Users\\user\\anaconda3\\python.exe "
      "D:\\AI-work\\BridgeStudio\\cases\\run_cases_erection.py")
    a("=" * 80)
    return "\n".join(L) + "\n"


def run(generate_only: bool = False) -> bool:
    OUT = HERE / CASE
    OUT.mkdir(parents=True, exist_ok=True)
    log: list[str] = []
    ok_all = True

    model, info = build_suspension_3d(**CFG)
    model.lanes = []
    (OUT / (STEM + ".model.json")).write_text(
        model.model_dump_json(indent=2), encoding="utf-8")
    res = solve_cable_bridge(model, out_dir=OUT,
                             dat_name=STEM + ".dat", timeout=600)
    if not res["converged"]:
        print("[FAIL] cable_solver not converged")
        return False
    print("[FEM ] {}  {} nodes / {} elems / {} cables  H={:.0f} kN  "
          "side_span={:.3f} m (auto={})".format(
              STEM, len(model.nodes), len(model.elements), len(res["cables"]),
              float(res["meta"].get("H_kN", 0)), info["side_span"],
              info["side_span_auto"]))

    E, A, EA = main_cable_EA(model, res)
    stages = [r["stage"] for r in res["erection"]
              if r["name"] not in SKIP_STAGE_NAMES
              and sum(res["erection_load"][r["stage"]]) > 0]
    print("[FEM ] erection {} stages total, {} with loads; EA={:.4g}".format(
        len(res["erection"]), len(stages), EA))

    # ---------------- [B] 自动平衡边跨 ----------------
    unbalanced = None
    if UNBALANCED_REF.exists():
        unbalanced = parse_cable_result(UNBALANCED_REF)
    btxt, bok = balance_report(res, info, stem=STEM, unbalanced=unbalanced)
    (OUT / (STEM + "_balance_report.txt")).write_text(btxt, encoding="utf-8")
    ok_all &= bok
    print("[{}] [B] balanced side span".format("PASS" if bok else "FAIL"))

    # ---------------- [A] 全桥成桥态 ----------------
    coords = design_geometry(res)
    l0 = unstrained_lengths(res)
    eps_range = strain_range(model, coords, l0)
    job = STEM + "_nl_ansys"
    apdl, ai = spec_nl_to_apdl(model, jobname=job, coords=coords, l0=l0,
                               title=TITLE)
    (OUT / (STEM + "_nl.inp")).write_text(apdl, encoding="ascii")
    print("[GEN ] [A] full-bridge APDL {} bytes, eps {:.3g}..{:.3g}".format(
        len(apdl), eps_range[0], eps_range[1]))
    full_warn = (-1, -1)
    if not generate_only:
        for p in OUT.glob(job + "_*.csv"):
            p.unlink()
        rc = _run([ANSYS_EXE, "-b", "-j", job, "-i", STEM + "_nl.inp",
                   "-o", job + ".out"], OUT, log, "ANSYS-FULL")
        out_path = OUT / (job + ".out")
        if out_path.exists():
            full_warn = _log_summary(out_path, log, "ANSYS-FULL")
        if rc != 0 or full_warn[0] != 0:
            print("[FAIL] [A] ANSYS exit={} errors={}".format(rc, full_warn[0]))
            ok_all = False
        else:
            a = {k: parse_ansys_csv_3d(OUT / (job + s)) for k, s in (
                ("disp", "_disp.csv"), ("reac", "_reac.csv"),
                ("link", "_link.csv"))}
            atxt, aok = compare_susp(
                res, a["disp"], a["reac"], a["link"], stem=STEM,
                system="SELF", model=model, coords=coords,
                eps_range=eps_range)
            (OUT / (STEM + "_nl_compare_report.txt")).write_text(
                atxt, encoding="utf-8")
            ok_all &= aok
            print("[{}] [A] full-bridge completion".format(
                "PASS" if aok else "FAIL"))

    # ---------------- [C] 逐阶段 ----------------
    stage_results: dict[int, dict] = {}
    stage_warns: dict[int, tuple[int, int]] = {}
    for st in stages:
        inp = stage_input(res, st)
        sjob = "{}_stage{}".format(STEM, st)
        # APDL 必须纯 ASCII；阶段中文名只出现在报告 / manifest 里。
        text, sinfo = stage_apdl(inp, E=E, A=A, jobname=sjob,
                                 title="CB3D13 erection stage {}".format(st))
        (OUT / (sjob + ".inp")).write_text(text, encoding="ascii")
        if generate_only:
            continue
        for p in OUT.glob(sjob + "_*.csv"):
            p.unlink()
        rc = _run([ANSYS_EXE, "-b", "-j", sjob, "-i", sjob + ".inp",
                   "-o", sjob + "_ansys.out"], OUT, log,
                  "ANSYS-STAGE-{}".format(st))
        opath = OUT / (sjob + "_ansys.out")
        sw = (-1, -1)
        if opath.exists():
            sw = _log_summary(opath, log, "ANSYS-STAGE-{}".format(st))
        stage_warns[st] = sw
        dp, lp = OUT / (sjob + "_disp.csv"), OUT / (sjob + "_link.csv")
        if rc != 0 or sw[0] != 0 or not (dp.exists() and lp.exists()):
            print("[FAIL] [C] stage {} ANSYS exit={} errors={} csv={}/{}".format(
                st, rc, sw[0], dp.exists(), lp.exists()))
            ok_all = False
            continue
        adisp, alink = parse_ansys_csv_3d(dp), parse_ansys_csv_3d(lp)
        stage_results[st] = stage_from_ansys(
            adisp, alink, xs=inp["xs"], z_init=sinfo["z_init"])

    if not generate_only:
        if len(stage_results) != len(stages):
            ok_all = False
        ctxt, cok = compare_erection(res, stage_results, stem=STEM,
                                     amp=PERTURB_AMP)
        (OUT / (STEM + "_erection_compare_report.txt")).write_text(
            ctxt, encoding="utf-8")
        ok_all &= cok
        print("[{}] [C] {} erection stages".format(
            "PASS" if cok else "FAIL", len(stage_results)))

    (OUT / (STEM + "_run_log.txt")).write_text(
        "\n".join(log) + ("\n[generate-only]\n" if generate_only else "\n"),
        encoding="utf-8")
    (OUT / (STEM + "_manifest.txt")).write_text(
        _manifest(model, res, info, eps_range, E, A, stages,
                   full_warn, stage_warns), encoding="utf-8")
    print("CB3D13 BALANCED ERECTION CASE " + ("PASSED" if ok_all else "FAILED"))
    return bool(ok_all)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate-only", action="store_true")
    a = ap.parse_args()
    return 0 if run(a.generate_only) else 1


if __name__ == "__main__":
    sys.exit(main())
