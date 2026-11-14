#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CB3D14 / CB3D15 算例编排：索垂度 Ernst 对标 + 带索自重悬索桥空缆态。

    C:\\Users\\user\\anaconda3\\python.exe cases\\run_cases_sag.py
        [--generate-only] [--sync-to <FEM-Eigen benchmark cases dir>]

产出（BridgeStudio/cases/）：
    CB3D14_cable_sag_ernst/      .sag / .model.json / 7 份 APDL + ANSYS CSV/OUT
                                 *_fem.csv（内核 CLI）/ 对比报告 / manifest / run_log
    CB3D15_suspension_cable_selfweight/
                                 .dat / .model.json / _cable.txt / 空缆态报告

需要 ANSYS 18.0 许可（7 个 job）；--generate-only 只写输入文件。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRIDGESTUDIO = HERE.parent
sys.path.insert(0, str(BRIDGESTUDIO))

os.environ["PATH"] = (r"C:\msys64\mingw64\bin" + os.pathsep
                      + os.environ.get("PATH", ""))

from ansys_bridge_sag import (  # noqa: E402
    BRIDGE_CFG, SAG_CFG, bridge_report, build_cases, model_snapshot,
    read_csv_table, sag_apdl, sag_from_ansys, sag_param_text,
)
from fem_eigen_cable import parse_cable_result, solve_cable_bridge  # noqa: E402
from suspension_3d import build_suspension_3d  # noqa: E402

ANSYS_EXE = Path(r"C:\Program Files\ANSYS Inc\v180\ANSYS\bin\winx64\ANSYS180.exe")
PROBE_EXE = Path(r"D:\AI-work\FEM-Eigen\build\cable_sag_probe.exe")

CASE = "CB3D14_cable_sag_ernst"
STEM = "cb3d14_cable_sag"
OUT = HERE / CASE
BRIDGE_CASE = "CB3D15_suspension_cable_selfweight"
BRIDGE_STEM = "cb3d15_cable_selfweight"
BRIDGE_OUT = HERE / BRIDGE_CASE
#: W=0 对照（CB3D13 冻结结果），只用于报告中的量化对比。
W0_REF = HERE / "CB3D13_suspension_balanced_erection" / (
    "cb3d13_balanced_erection_cable.txt")


def _run(cmd: list, cwd: Path, log: list[str], tag: str) -> int:
    log.append("$ " + " ".join(str(c) for c in cmd))
    try:
        p = subprocess.run([str(c) for c in cmd], cwd=str(cwd),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=900)
        log.append("[{}] exit={}".format(tag, p.returncode))
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
    log.append("[{}] errors={} warnings={}".format(tag, nerr, nwarn))
    return nerr, nwarn


def read_fem_csv(path: Path) -> dict[str, dict[str, float]]:
    rows: dict[str, dict[str, float]] = {}
    keys: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        p = line.split(",")
        if p[0] == "case":
            keys = p[1:]
            continue
        rows[p[0]] = {k: float(v) for k, v in zip(keys, p[1:])}
    return rows


def compare_report(cases, fem, ansys, cfg) -> tuple[str, bool]:
    lines: list[str] = []
    a = lines.append
    ok = True
    a("=" * 100)
    a("CB3D14  cable sag / Ernst modulus: FEM kernel closed-form vs ANSYS segmented LINK180")
    a("=" * 100)
    a("FEM  : cable_sag_probe.exe -> fem_3d.hpp::cableTensionFromChord +")
    a("       fem_3d_nl.hpp::cableErnstModulus (same code as cable_solver.exe)")
    a("ANSYS: {} LINK180 segments, NLGEOM,ON, horizontal-uniform nodal self weight,".format(
        cfg["NSEG"]))
    a("       INISTATE,EPEL per segment, initial geometry = straight + %.2f m sine" % cfg["amp"])
    a("Tolerance: rtol=2e-3 on each group scale (same as H/I stages)")
    a("")
    a("%-10s %8s %14s %14s %10s %11s %11s %8s" % (
        "case", "W", "L0 (m)", "T_fem (N)", "Eeq/E", "H_ansys", "dT/T", "state"))
    for cs in cases:
        cid = cs["id"]
        f = fem[cid]
        r = ansys.get(cid)
        if r is None:
            a("%-10s  MISSING ANSYS RESULT" % cid)
            ok = False
            continue
        rel = abs(r["H"] - f["T"]) / max(abs(f["T"]), 1.0)
        state = "PASS" if rel <= 2e-3 else "FAIL"
        ok &= (state == "PASS")
        a("%-10s %8.3f %14.9f %14.3f %10.6f %11.3f %11.3e %8s" % (
            cid, cs["W"], cs["L0"], f["T"], f["Eeq_ratio"], r["H"], rel, state))
    a("")
    a("%-10s %12s %12s %10s %12s %12s %8s" % (
        "case", "f_fem (m)", "f_ansys (m)", "df/f", "T_min (N)", "T_max (N)", "state"))
    for cs in cases:
        cid = cs["id"]
        f = fem[cid]
        r = ansys.get(cid)
        if r is None or cs["W"] <= 0.0:
            continue
        rel = abs(r["f"] - f["f"]) / max(abs(f["f"]), 1e-12)
        state = "PASS" if rel <= 2e-3 else "FAIL"
        ok &= (state == "PASS")
        a("%-10s %12.9f %12.9f %10.3e %12.3f %12.3f %8s" % (
            cid, f["f"], r["f"], rel, r["T_min"], r["T_max"], state))
    # Ernst tangent: ANSYS finite difference on the 5-point L0 sweep
    sweep = [c for c in cases if c["group"] == "sag" and
             c["W"] == cfg["dens"] * cfg["A"] * 9.81]
    if len(sweep) >= 3 and all(c["id"] in ansys for c in sweep):
        i = len(sweep) // 2
        p, m, n = sweep[i - 1], sweep[i], sweep[i + 1]
        h = n["L0"] - p["L0"]
        fd = (ansys[n["id"]]["H"] - ansys[p["id"]]["H"]) / h
        ana = fem[m["id"]]["dTdL0"]
        rel = abs(fd - ana) / max(abs(ana), 1.0)
        state = "PASS" if rel <= 2e-3 else "FAIL"
        ok &= (state == "PASS")
        a("")
        a("Ernst tangent dT/dL0 (finite difference of ANSYS H over L0 sweep):")
        a("  ANSYS_fd = %.6e N/m" % fd)
        a("  FEM_Ernst= %.6e N/m   (Eeq*A/L0 with Eeq/E=%.6f)" % (
            ana, fem[m["id"]]["Eeq_ratio"]))
        a("  relative = %.3e   [%s]" % (rel, state))
    rod = next((c for c in cases if c["group"] == "rod"), None)
    if rod and rod["id"] in fem:
        a("")
        a("W=0 straight-rod degeneration: Eeq/E = %.9f (must be 1)  [%s]" % (
            fem[rod["id"]]["Eeq_ratio"],
            "PASS" if abs(fem[rod["id"]]["Eeq_ratio"] - 1.0) < 1e-12 else "FAIL"))
        ok &= abs(fem[rod["id"]]["Eeq_ratio"] - 1.0) < 1e-12
    a("")
    a("Known difference: FEM closed form is a shallow-sag first-order model")
    a("(single T instead of tension varying along the cable); the ANSYS chain")
    a("is a finer discrete solution.  The gap scales with (V/H)^2 and is")
    a("~2e-4 here, three orders below the 17%-68% Ernst correction.")
    a("")
    a("CB3D14 CABLE SAG CASE " + ("PASSED" if ok else "FAILED"))
    return "\n".join(lines) + "\n", bool(ok)


def manifest(cfg, cases, warns: dict) -> str:
    L: list[str] = []
    a = L.append
    a("=" * 90)
    a("CB3D14 / CB3D15  算例归档")
    a("=" * 90)
    a("")
    a("唯一数据源: BridgeStudio/ansys_bridge_sag.py::SAG_CFG 与 BRIDGE_CFG")
    a("  SAG_CFG = " + repr(SAG_CFG))
    a("  BRIDGE_CFG = " + repr(BRIDGE_CFG))
    a("")
    a("CB3D14 单索本构：")
    a("  E={} Pa  A={} m2  dens={} kg/m3  W={:.6f} N/m".format(
        cfg["E"], cfg["A"], cfg["dens"],
        cfg["dens"] * cfg["A"] * 9.81))
    a("  LH={} m  NSEG={}  T_target={} N  perturb={} m".format(
        cfg["LH"], cfg["NSEG"], cfg["T_target"], cfg["amp"]))
    a("  工况 {} 个：".format(len(cases)))
    for c in cases:
        a("    {:<10s} W={:10.4f}  L0={:16.9f}  amp={:.2f}  # {}".format(
            c["id"], c["W"], c["L0"], c["amp"], c["note"]))
    a("")
    a("ANSYS 18.0 作业日志（errors/warnings）：")
    for k in sorted(warns):
        a("  {:<12s} errors={} warnings={}".format(k, warns[k][0], warns[k][1]))
    a("")
    a("CB3D15 带索自重悬索桥：与 CB3D13 同参数，仅 cable_dens: 0 -> 7850")
    a("  验证 W>0 时空缆 stage 0 不再命中 H 二分下界 100 N、矢高 > 0。")
    a("")
    a("保留 .sag / .inp / .dat / .csv / .txt / .out / .json 作为归档证据；")
    a("ANSYS 二进制与数据库产物由 .gitignore 排除。")
    return "\n".join(L) + "\n"


def sync_to(dst: Path, log: list[str]) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    pats = ["*.sag", "*.model.json", "*_fem.csv", "*_disp.csv", "*_link.csv",
            "*.inp", "*_ansys.out", "*_compare_report.txt", "*_manifest.txt",
            "*_run_log.txt"]
    n = 0
    for src_dir in (OUT, BRIDGE_OUT):
        for pat in pats:
            for p in sorted(src_dir.glob(pat)):
                shutil.copy2(p, dst / p.name)
                n += 1
    for pat in ("*.dat", "*_cable.txt", "*_report.txt"):
        for p in sorted(BRIDGE_OUT.glob(pat)):
            shutil.copy2(p, dst / p.name)
            n += 1
    log.append("[SYNC] copied {} files to {}".format(n, dst))


def run(generate_only: bool, sync_dir: Path | None) -> bool:
    OUT.mkdir(parents=True, exist_ok=True)
    BRIDGE_OUT.mkdir(parents=True, exist_ok=True)
    log: list[str] = []
    ok = True
    cfg = SAG_CFG
    cases = build_cases(cfg)

    (OUT / (STEM + ".sag")).write_text(
        sag_param_text(cfg, cases), encoding="ascii", newline="\n")
    (OUT / (STEM + ".model.json")).write_text(
        json.dumps(model_snapshot(cfg, cases), indent=2, ensure_ascii=False),
        encoding="utf-8")
    print("[GEN ] {} cases, .sag / .model.json".format(len(cases)))

    infos = {}
    ansys = {}
    warns = {}
    for cs in cases:
        job = cs["id"]
        text, info = sag_apdl(cs, cfg, jobname=job)
        (OUT / (job + ".inp")).write_text(text, encoding="ascii", newline="\n")
        infos[job] = info
        if generate_only:
            continue
        for old in OUT.glob(job + "_*.csv"):
            old.unlink()
        rc = _run([ANSYS_EXE, "-b", "-j", job, "-i", job + ".inp",
                   "-o", job + "_ansys.out"], OUT, log, "ANSYS-" + job)
        op = OUT / (job + "_ansys.out")
        sw = (-1, -1)
        if op.exists():
            sw = _log_summary(op, log, "ANSYS-" + job)
        warns[job] = sw
        dp, lp = OUT / (job + "_disp.csv"), OUT / (job + "_link.csv")
        if rc != 0 or sw[0] != 0 or not (dp.exists() and lp.exists()):
            print("[FAIL] {} ANSYS exit={} errors={} csv={}/{}".format(
                job, rc, sw[0], dp.exists(), lp.exists()))
            ok = False
            continue
        ansys[job] = sag_from_ansys(read_csv_table(dp), read_csv_table(lp), info)
        print("[ANSYS] {:<10s} H={:12.3f} N  f={:.9f} m".format(
            job, ansys[job]["H"], ansys[job]["f"]))
    if generate_only:
        print("[GEN ] generate-only: {} APDLs written".format(len(cases)))
        return True

    # FEM 内核 CLI
    if not PROBE_EXE.exists():
        print("[FAIL] cable_sag_probe.exe not found: {}".format(PROBE_EXE))
        ok = False
    else:
        rc = _run([PROBE_EXE, OUT / (STEM + ".sag"),
                   OUT / (STEM + "_fem.csv")], OUT, log, "FEM-PROBE")
        if rc != 0:
            print("[FAIL] cable_sag_probe exit={}".format(rc))
            ok = False
        else:
            fem = read_fem_csv(OUT / (STEM + "_fem.csv"))
            txt, cok = compare_report(cases, fem, ansys, cfg)
            (OUT / (STEM + "_compare_report.txt")).write_text(txt, encoding="utf-8")
            ok &= cok
            print("[{}] CB3D14 Ernst comparison".format("PASS" if cok else "FAIL"))

    # CB3D15：W>0 悬索桥，跑 cable_solver 并检查空缆 stage 0
    model, _ = build_suspension_3d(**BRIDGE_CFG)
    model.lanes = []
    (BRIDGE_OUT / (BRIDGE_STEM + ".model.json")).write_text(
        model.model_dump_json(indent=2), encoding="utf-8")
    res = solve_cable_bridge(model, out_dir=BRIDGE_OUT,
                             dat_name=BRIDGE_STEM + ".dat", timeout=600)
    res_w0 = parse_cable_result(W0_REF) if W0_REF.exists() else None
    if not res.get("converged"):
        print("[FAIL] CB3D15 cable_solver not converged")
        ok = False
    report = bridge_report(res, res_w0)
    (BRIDGE_OUT / (BRIDGE_STEM + "_report.txt")).write_text(report, encoding="utf-8")
    s0 = (res.get("erection") or [{}])[0]
    bok = bool(s0.get("H", 0.0) > 1e3 and s0.get("sag", 0.0) > 1e-6)
    ok &= bok
    print("[{}] CB3D15 empty-cable stage H={:.3f} kN sag={:.6f} m".format(
        "PASS" if bok else "FAIL", s0.get("H", 0.0) / 1e3, s0.get("sag", 0.0)))

    (OUT / (STEM + "_manifest.txt")).write_text(
        manifest(cfg, cases, warns), encoding="utf-8")
    (OUT / (STEM + "_run_log.txt")).write_text("\n".join(log) + "\n",
                                               encoding="utf-8")
    if sync_dir:
        sync_to(sync_dir, log)
        (OUT / (STEM + "_run_log.txt")).write_text("\n".join(log) + "\n",
                                                   encoding="utf-8")
    print("CB3D14 + CB3D15 " + ("PASSED" if ok else "FAILED"))
    return bool(ok)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate-only", action="store_true")
    ap.add_argument("--sync-to", type=Path, default=None)
    a = ap.parse_args()
    return 0 if run(a.generate_only, a.sync_to) else 1


if __name__ == "__main__":
    sys.exit(main())
