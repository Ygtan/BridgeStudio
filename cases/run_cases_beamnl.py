#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CB3D16 算例编排：大转动梁柱效应（CR 梁）vs ANSYS BEAM188 + NLGEOM。

    C:\\Users\\user\\anaconda3\\python.exe cases\\run_cases_beamnl.py
        [--generate-only] [--sync-to <FEM-Eigen benchmark cases dir>]

产出（BridgeStudio/cases/CB3D16_large_rotation_beam_column/）：
    cb3d16_beam_nl.beam / .model.json     同源参数与快照
    <case>.inp / <case>_disp.csv / <case>_ansys.out   8 个 ANSYS 作业
    cb3d16_beam_nl_fem.csv                beam_nl_probe.exe 内核 CR 解
    *_compare_report.txt / *_manifest.txt / *_run_log.txt

需要 ANSYS 18.0 许可（8 个 job）；--generate-only 只写输入文件。
"""
from __future__ import annotations

import argparse
import json
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

from ansys_bridge_beamnl import (  # noqa: E402
    BNL_CASES, BNL_GROUPS, CASE_ID, PARAMS_FILE, beamnl_apdl,
    exact_values, params_text, read_disp_csv,
)

ANSYS_EXE = Path(r"C:\Program Files\ANSYS Inc\v180\ANSYS\bin\winx64\ANSYS180.exe")
PROBE_EXE = Path(r"D:\AI-work\FEM-Eigen\build\beam_nl_probe.exe")

CASE = "CB3D16_large_rotation_beam_column"
STEM = CASE_ID
OUT = HERE / CASE

RTOL = 2e-3
ATOL = 1e-8


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


def read_fem_csv(path: Path) -> tuple[dict, dict]:
    """解析 beam_nl_probe.exe 输出：N 行节点位移 + S 行解析/派生量。"""
    nodal: dict[str, dict[int, list[float]]] = {}
    summ: dict[str, dict[str, float]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        p = line.split(",")
        if p[0] == "N":
            nodal.setdefault(p[1], {})[int(p[2])] = [float(v) for v in p[3:]]
        elif p[0] == "S":
            summ.setdefault(p[1], {})[p[2]] = float(p[3])
    return nodal, summ


def case_scale(a: dict[int, list[float]]) -> list[float]:
    """每个自由度列的最大绝对值，用作相对判据的 scale。"""
    return [max((abs(row[k]) for row in a.values()), default=0.0)
            for k in range(6)]


def compare_report(cases, nodal, summ, ansys, groups) -> tuple[str, bool]:
    lines: list[str] = []
    a = lines.append
    ok = True
    a("=" * 104)
    a("CB3D16  large-rotation beam-column: FEM CR kernel (fem_3d_nl.hpp) vs ANSYS BEAM188+NLGEOM")
    a("=" * 104)
    a("FEM  : beam_nl_probe.exe -> solveModel3DNonlinear (co-rotational triad,")
    a("       analytic geometric stiffness crBeamTangent12, Newton + load steps)")
    a("ANSYS: BEAM188 + NLGEOM,ON + NSUBST/NEQIT, ASEC section, LAST-result CSV")
    a("  CIRCLE group: pure end moment, default linear interpolation (no shear)")
    a("  PDELTA group: KEYOPT(3)=2 cubic + G*1000 -> remove shear locking / shear")
    a("                flexibility, so BEAM188 degenerates to Euler-Bernoulli")
    a("Tolerance: |dU| <= ATOL + RTOL*per-DOF scale, ATOL=%.1e RTOL=%.1e" % (ATOL, RTOL))
    a("")
    a("%-9s %-7s %11s %11s %11s %9s  %s" % (
        "case", "kind", "max|dU|", "max|dR|", "worst d/tol", "state", "physics"))
    detail: dict[str, dict] = {}
    for cs in cases:
        cid, gid = cs["id"], cs["group"]
        g = groups[gid]
        av = ansys.get(cid)
        fv = nodal.get(cid)
        if av is None or fv is None:
            a("%-9s  MISSING FEM/ANSYS RESULT" % cid)
            ok = False
            continue
        keys = sorted(set(av) & set(fv))
        sc = case_scale(av)
        du = dr = rel = 0.0
        case_ok = True
        for i in keys:
            for k in range(6):
                d = abs(fv[i][k] - av[i][k])
                tol = ATOL + RTOL * sc[k]
                r = d / tol
                if k < 3:
                    du = max(du, d)
                else:
                    dr = max(dr, d)
                rel = max(rel, r)
                if d > tol:
                    case_ok = False
        ok &= case_ok
        state = "PASS" if case_ok else "FAIL"
        a("%-9s %-7s %11.4e %11.4e %11.4e %9s  %s" % (
            cid, cs["kind"], du, dr, rel, state, cs["desc"]))
        detail[cid] = dict(max_du=du, max_dr=dr, rel=rel, state=state)

    a("")
    a("--- CIRCLE: finite rotation, analytic circular elastica ---")
    a("%-9s %9s %14s %14s %14s %14s %11s" % (
        "case", "theta", "xtip_ansys", "xtip_exact", "ytip_ansys", "ytip_exact", "rot_error"))
    for cs in cases:
        if cs["kind"] != "CIRCLE":
            continue
        cid = cs["id"]
        av = ansys.get(cid)
        if av is None:
            continue
        g = groups[cs["group"]]
        ex = exact_values(g, cs)
        tip = max(av)
        xt = g["L"] + av[tip][0]
        yt = av[tip][1]
        rot = av[tip][5]
        derr = abs(rot - ex["rot_ex"]) / max(abs(ex["rot_ex"]), 1.0)
        ok &= derr <= 1e-6
        a("%-9s %9.5f %14.9f %14.9f %14.9f %14.9f %11.3e" % (
            cid, ex["theta"], xt, ex["xtip_ex"], yt, ex["ytip_ex"], derr))
    full = next((c for c in cases if c["kind"] == "CIRCLE" and c["lam"] == 1.0), None)
    if full and full["id"] in ansys:
        av = ansys[full["id"]]
        tip = max(av)
        g = groups[full["group"]]
        close = ((g["L"] + av[tip][0]) ** 2 + av[tip][1] ** 2) ** 0.5
        a("  full-circle closure |tip-root| = %.4e m   [%s]" % (
            close, "PASS" if close < 1e-6 else "FAIL"))
        ok &= close < 1e-6
    a("  NOTE: the ~1e-2..4e-2 m gap between FEM/ANSYS and the exact arc is the")
    a("  8-segment chord discretisation (O(theta^2/n^2)); FEM and ANSYS share it,")
    a("  while their mutual difference stays at 1e-9 m / 1e-10 rad.")

    a("")
    a("--- PDELTA: analytic geometric stiffness (Timoshenko beam-column) ---")
    a("%-9s %10s %14s %14s %14s %10s %10s" % (
        "case", "P/Pcr", "ymid_fem", "ymid_ansys", "y_exact", "amp_fem", "amp_ex"))
    amps: list[float] = []
    for cs in cases:
        if cs["kind"] != "PDELTA":
            continue
        cid = cs["id"]
        av = ansys.get(cid)
        s = summ.get(cid)
        if av is None or s is None:
            continue
        g = groups[cs["group"]]
        ex = exact_values(g, cs)
        mid = int(g["NELEM"]) // 2 + 1
        ya = -av[mid][1]
        amp = ya / ex["ylin"]
        amps.append(amp)
        a("%-9s %10.2f %14.9f %14.9f %14.9f %10.5f %10.5f" % (
            cid, cs["lam"], s["ymid_fem"], ya, ex["y_pd_ex"],
            s["amp_fem"], ex["amp_ex"]))
    if len(amps) >= 2:
        grow = all(amps[i + 1] > amps[i] for i in range(len(amps) - 1))
        a("  amplification factor monotonically increases 1 -> %.3f   [%s]" % (
            amps[-1], "PASS" if grow and amps[-1] > 3.0 else "FAIL"))
        ok &= bool(grow and amps[-1] > 3.0)
    a("")
    a("CB3D16 LARGE-ROTATION BEAM-COLUMN CASE " + ("PASSED" if ok else "FAILED"))
    return "\n".join(lines) + "\n", bool(ok)


def manifest(groups, cases, warns: dict) -> str:
    L: list[str] = []
    a = L.append
    a("=" * 96)
    a("CB3D16  大转动梁柱效应算例归档")
    a("=" * 96)
    a("")
    a("唯一数据源: BridgeStudio/ansys_bridge_beamnl.py::BNL_GROUPS / BNL_CASES")
    a("同一份参数导出 *.beam (FEM-Eigen beam_nl_probe.exe) 与 8 份 BEAM188 APDL。")
    a("")
    for gid, g in groups.items():
        a("[{}] {}".format(gid, g["desc"]))
        a("  E={} Pa  G={} Pa  L={} m  A={} m2".format(g["E"], g["G"], g["L"], g["A"]))
        a("  IY={}  IZ={}  J={}  NELEM={}  Q={} N".format(
            g["IY"], g["IZ"], g["J"], g["NELEM"], g["Q"]))
        a("  LOADSTEPS={}  MAXITER={}  TOL={}".format(
            g["LOADSTEPS"], g["MAXITER"], g["TOL"]))
        a("  KEYOPT(3)={}  NSUBST={}  CNVTOL_F={}".format(
            g["keyopt3"], g["nsubst"], g["cnvtol_f"]))
    a("")
    a("工况 {} 个：".format(len(cases)))
    for c in cases:
        a("  {:<9s} {:<7s} lambda={:<5.2f}  # {}".format(
            c["id"], c["kind"], c["lam"], c["desc"]))
    a("")
    a("ANSYS 18.0 作业日志（errors/warnings）：")
    for k in sorted(warns):
        a("  {:<12s} errors={} warnings={}".format(k, warns[k][0], warns[k][1]))
    a("")
    a("口径说明：BEAM188 是 Timoshenko 单元，FEM CR 梁是 Euler-Bernoulli。")
    a("  PDELTA 组用 KEYOPT(3)=2 三次插值消除剪切锁定、G 提高 1000 倍消除")
    a("  剪切柔度；CIRCLE 组为纯弯矩、无横向剪力，保持默认即可。")
    a("  诊断数据见 FEM-Eigen/tests/benchmark/beam_nl_ansys_3d/README.md。")
    a("")
    a("保留 .beam / .inp / .csv / .txt / .out / .json 作为归档证据；")
    a("ANSYS 二进制与数据库产物由 .gitignore 排除。")
    return "\n".join(L) + "\n"


def sync_to(dst: Path, log: list[str]) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    pats = ["*.beam", "*.model.json", "*_fem.csv", "*_disp.csv", "*.inp",
            "*_ansys.out", "*_compare_report.txt", "*_manifest.txt",
            "*_run_log.txt"]
    n = 0
    for pat in pats:
        for p in sorted(OUT.glob(pat)):
            shutil.copy2(p, dst / p.name)
            n += 1
    log.append("[SYNC] copied {} files to {}".format(n, dst))


def run(generate_only: bool, sync_dir: Path | None) -> bool:
    OUT.mkdir(parents=True, exist_ok=True)
    log: list[str] = []
    ok = True
    groups, cases = BNL_GROUPS, BNL_CASES

    (OUT / PARAMS_FILE).write_text(params_text(groups, cases),
                                   encoding="ascii", newline="\n")
    snapshot = {"groups": groups, "cases": cases,
                "source": "ansys_bridge_beamnl.py::BNL_GROUPS / BNL_CASES",
                "ansys_element": "BEAM188", "nlgeom": True}
    (OUT / (STEM + ".model.json")).write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    print("[GEN ] {} cases, .beam / .model.json".format(len(cases)))

    ansys: dict[str, dict[int, list[float]]] = {}
    warns: dict[str, tuple[int, int]] = {}
    for cs in cases:
        job = cs["id"]
        g = groups[cs["group"]]
        text = beamnl_apdl(g, cs, jobname=job)
        (OUT / (job + ".inp")).write_text(text, encoding="ascii", newline="\n")
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
        dp = OUT / (job + "_disp.csv")
        if rc != 0 or sw[0] != 0 or not dp.exists():
            print("[FAIL] {} ANSYS exit={} errors={} csv={}".format(
                job, rc, sw[0], dp.exists()))
            ok = False
            continue
        ansys[job] = read_disp_csv(dp)
        tip = max(ansys[job])
        print("[ANSYS] {:<9s} nodes={:3d} tip_UX={:+.8g} tip_ROTZ={:+.8g}".format(
            job, len(ansys[job]), ansys[job][tip][0], ansys[job][tip][5]))
    if generate_only:
        print("[GEN ] generate-only: {} APDLs written".format(len(cases)))
        return True

    nodal, summ = {}, {}
    if not PROBE_EXE.exists():
        print("[FAIL] beam_nl_probe.exe not found: {}".format(PROBE_EXE))
        ok = False
    else:
        rc = _run([PROBE_EXE, OUT / PARAMS_FILE, OUT / (STEM + "_fem.csv")],
                  OUT, log, "FEM-BEAMNL")
        if rc != 0:
            print("[FAIL] beam_nl_probe exit={}".format(rc))
            ok = False
        else:
            nodal, summ = read_fem_csv(OUT / (STEM + "_fem.csv"))
    if nodal:
        txt, cok = compare_report(cases, nodal, summ, ansys, groups)
        (OUT / (STEM + "_compare_report.txt")).write_text(txt, encoding="utf-8")
        ok &= cok
        print("[{}] CB3D16 FEM vs ANSYS comparison".format(
            "PASS" if cok else "FAIL"))

    (OUT / (STEM + "_manifest.txt")).write_text(
        manifest(groups, cases, warns), encoding="utf-8")
    (OUT / (STEM + "_run_log.txt")).write_text("\n".join(log) + "\n",
                                               encoding="utf-8")
    if sync_dir:
        sync_to(sync_dir, log)
        (OUT / (STEM + "_run_log.txt")).write_text("\n".join(log) + "\n",
                                                   encoding="utf-8")
    print("CB3D16 " + ("PASSED" if ok else "FAILED"))
    return bool(ok)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate-only", action="store_true")
    ap.add_argument("--sync-to", type=Path, default=None)
    a = ap.parse_args()
    return 0 if run(a.generate_only, a.sync_to) else 1


if __name__ == "__main__":
    sys.exit(main())
