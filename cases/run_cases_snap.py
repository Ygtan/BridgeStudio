#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CB3D17 算例编排：后屈曲 / snap-through 球面弧长法 vs ANSYS ARCLEN。

    C:\\Users\\user\\anaconda3\\python.exe cases\\run_cases_snap.py
        [--generate-only] [--sync-to <FEM-Eigen benchmark cases dir>]

产出（BridgeStudio/cases/CB3D17_snap_through_arc_length/）：
    cb3d17_snap_through_arc_length.snap / .model.json   同源参数与快照
    <case>.inp / <case>_curve.csv / <case>_ansys.out    3 个 ANSYS LINK180+ARCLEN 作业
    cb3d17_snap_through_arc_length_fem.csv              snap_probe.exe 弧长法路径
    *_compare_report.txt / *_manifest.txt / *_run_log.txt

需要 ANSYS 18.0 许可（3 个 job）；--generate-only 只写输入文件。
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

from ansys_bridge_snap import (  # noqa: E402
    CASE_ID, PARAMS_FILE, SNAP_CASE_NAME, SNAP_CFG, SNAP_ANSYS_CASES,
    SNAP_FEM_CASES, analytic_limit, analytic_p, interp_lambda,
    params_text, quadratic_extremum, read_curve_csv, snap_apdl,
)

ANSYS_EXE = Path(r"C:\Program Files\ANSYS Inc\v180\ANSYS\bin\winx64\ANSYS180.exe")
PROBE_EXE = Path(r"D:\AI-work\FEM-Eigen\build\snap_probe.exe")

OUT = HERE / SNAP_CASE_NAME
STEM = CASE_ID

# 判据（量级与实测分离：ANSYS/解析 ~5e-6 N，FEM/解析 ~2e-8 N）
TOL_DP_ANSYS = 1.0e-3      # N
TOL_DP_FEM = 1.0e-3        # N
TOL_LAMBDA_TIME = 1.0e-5   # lambda_reaction vs ANSYS TIME
TOL_EXTREMUM = 1.0e-3      # 二次插值极值与解析 / FEM-ANSYS 差
TOL_CURVE = 5.0e-5         # FEM 二次插值 vs ANSYS 逐点 lambda（实测 ~3.3e-6）


def _run(cmd: list, cwd: Path, log: list[str], tag: str) -> int:
    log.append("$ " + " ".join(str(c) for c in cmd))
    try:
        p = subprocess.run([str(c) for c in cmd], cwd=str(cwd),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=1800)
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
    """snap_probe.exe 输出：P 行逐平衡点、S 行解析/派生量。"""
    pts: dict[str, list[list[float]]] = {}
    summ: dict[str, dict[str, float]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        q = line.split(",")
        if q[0] == "P" and len(q) >= 9:
            pts.setdefault(q[1], []).append([float(x) for x in q[2:]])
        elif q[0] == "S" and len(q) >= 4:
            summ.setdefault(q[1], {})[q[2]] = float(q[3])
    return pts, summ


def fem_curve(rows: list[list[float]]) -> list[tuple[float, float]]:
    """P 行 = [step, lambda, v, residual, iterations, ds, pivot]。"""
    return [(r[2], r[1]) for r in rows]


def compare_report(ansys, warns, fem_pts, fem_sum) -> tuple[str, bool]:
    L: list[str] = []
    a = L.append
    ok = True
    cfg = SNAP_CFG
    vcr_e, pcr_e = analytic_limit(cfg, 0)
    vcr_l, pcr_l = analytic_limit(cfg, 1)

    a("=" * 108)
    a("CB3D17  post-buckling / snap-through: FEM spherical arc-length vs ANSYS LINK180 + ARCLEN")
    a("=" * 108)
    a("FEM  : snap_probe.exe -> stability_path_3d.hpp (Crisfield spherical constraint,")
    a("       lambda as unknown, consistent tangent from fem_3d_nl.hpp CR elements)")
    a("ANSYS: LINK180 + NLGEOM,ON + ARCLEN,ON + ARCTRM,U ; per-substep reaction CSV")
    a("")
    a("von Mises two-bar shallow truss: b=%.3f m  h=%.3f m  A=%.3e m2  E=%.3e Pa"
      % (cfg["B"], cfg["H"], cfg["A"], cfg["E"]))
    a("reference load P_ref=%.4e N (> analytic limit, otherwise ANSYS stops at lambda=1)"
      % cfg["PREF"])
    a("independent analytic anchor P(v) = -2*N*(h-v)/L, L=sqrt(b^2+(h-v)^2)")
    a("  engineering strain N=EA(L-L0)/L0 :  v_cr=%.9f m  P_cr=%.4f N  lambda_cr=%.9f"
      % (vcr_e, pcr_e, pcr_e / cfg["PREF"]))
    a("  logarithmic  strain N=EA*ln(L/L0):  v_cr=%.9f m  P_cr=%.4f N  lambda_cr=%.9f"
      % (vcr_l, pcr_l, pcr_l / cfg["PREF"]))
    a("  P(h)=%.1f N  P(2h)=%.1f N  (flat / fully inverted configurations)"
      % (analytic_p(cfg["H"], cfg, 1), analytic_p(2 * cfg["H"], cfg, 1)))
    a("ANSYS LINK180 KEYOPT(2)=1 is exactly the logarithmic-strain measure;")
    a("KEYOPT(2)=0 differs by ~2.3% and is archived as a strain-measure contrast only.")
    a("")

    # ---------------- ANSYS ----------------
    a("--- ANSYS per-substep equilibrium path vs independent analytic anchor ---")
    a("%-16s %6s %13s %13s %13s %11s %10s %9s" % (
        "job", "nset", "worst|dP|N", "lambda_max", "lambda_min", "v_max",
        "|lamR-T|", "state"))
    an_detail: dict[str, dict] = {}
    for cs in SNAP_ANSYS_CASES:
        job = cs["id"]
        rows = ansys.get(job)
        if not rows:
            a("%-16s  MISSING ANSYS RESULT" % job)
            ok = False
            continue
        strain = 1 if cs["keyopt2"] == 1 else 0
        # 对照作业用工程应变解析只是量级参照；主对标严格用对数应变解析
        ref_strain = 1
        worst = max(abs(lam * cfg["PREF"] - analytic_p(v, cfg, ref_strain))
                    for v, lam, _, _ in rows if 0.0 <= v <= 2 * cfg["H"])
        lmax = max(lam for _, lam, _, _ in rows)
        lmin = min(lam for _, lam, _, _ in rows)
        vmax = max(v for v, _, _, _ in rows)
        dlt = max(abs(lam - lt) for _, lam, lt, _ in rows)
        passed = (cs["keyopt2"] != 1 or
                  (worst <= TOL_DP_ANSYS and dlt <= TOL_LAMBDA_TIME))
        ok &= passed
        a("%-16s %6d %13.4e %13.7f %13.7f %11.6f %10.3e %9s" % (
            job, len(rows), worst, lmax, lmin, vmax, dlt,
            "REF" if cs["keyopt2"] == 0 else ("PASS" if passed else "FAIL")))
        an_detail[job] = dict(rows=rows, worst=worst, lmax=lmax, lmin=lmin,
                              vmax=vmax, dlt=dlt, strain=strain)
    a("  TOL: worst|dP|<=%.1e N, |lambda_reaction-TIME|<=%.1e" %
      (TOL_DP_ANSYS, TOL_LAMBDA_TIME))
    main = an_detail.get("sn_log_fine")
    if main:
        rows = main["rows"]
        curve = [(v, lam) for v, lam, _, _ in rows]
        vmaxfit, lmaxfit = quadratic_extremum(curve, True)
        vminfit, lminfit = quadratic_extremum(curve, False)
        dfit = abs(lmaxfit - pcr_l / cfg["PREF"])
        ok &= dfit <= TOL_EXTREMUM
        a("  ANSYS limit point (quadratic fit): v*=%.9f  lambda*=%.9f  "
          "analytic=%.9f  d=%.3e  [%s]" % (
              vmaxfit, lmaxfit, pcr_l / cfg["PREF"], dfit,
              "PASS" if dfit <= TOL_EXTREMUM else "FAIL"))
        a("  descending branch: lambda_min_fit=%+.7f at v=%.6f ; "
          "v_max=%.6f (>=2h=%.1f)  [%s]" % (
              lminfit, vminfit, main["vmax"], 2 * cfg["H"],
              "PASS" if lminfit < -0.5 and main["vmax"] >= 0.99 else "FAIL"))
        ok &= bool(lminfit < -0.5 and main["vmax"] >= 0.99)
    a("")

    # ---------------- FEM ----------------
    a("--- FEM arc-length path vs independent analytic anchor ---")
    a("%-14s %7s %7s %13s %13s %13s %13s %9s" % (
        "case", "npts", "STRAIN", "worst|dP|N", "lam_max_fit", "analytic",
        "v_cr_fit", "state"))
    fem_detail: dict[str, dict] = {}
    for cs in SNAP_FEM_CASES:
        cid = cs["id"]
        s = fem_sum.get(cid)
        rows = fem_pts.get(cid)
        if not s or not rows:
            a("%-14s  MISSING FEM RESULT" % cid)
            ok = False
            continue
        worst = s["worst_dP"]
        dfit = abs(s["lambda_max_fit"] - s["analytic_lambda_cr"])
        passed = bool(worst <= TOL_DP_FEM and dfit <= TOL_EXTREMUM
                      and s["converged"] > 0.5 and s["error_empty"] > 0.5)
        ok &= passed
        a("%-14s %7d %7d %13.4e %13.8f %13.8f %13.8f %9s" % (
            cid, int(s["npoints"]), int(s["STRAIN"]), worst,
            s["lambda_max_fit"], s["analytic_lambda_cr"],
            s["v_at_lambda_max_fit"], "PASS" if passed else "FAIL"))
        fem_detail[cid] = dict(s=s, curve=fem_curve(rows))
    a("  TOL: worst|dP|<=%.1e N, |lambda_max_fit-analytic|<=%.1e" %
      (TOL_DP_FEM, TOL_EXTREMUM))
    a("")

    # ---------------- FEM vs ANSYS ----------------
    a("--- FEM spherical arc-length path vs ANSYS ARCLEN path (same log-strain measure) ---")
    a("%-14s %-16s %13s %13s %13s %11s %9s" % (
        "fem", "ansys", "lam*_fem", "lam*_ansys", "d(lambda*)", "curve_max", "state"))
    for cs in SNAP_ANSYS_CASES:
        job, cid = cs["id"], cs["fem"]
        if cid is None or job not in an_detail or cid not in fem_detail:
            continue
        fcur = fem_detail[cid]["curve"]
        acur = [(v, lam) for v, lam, _, _ in an_detail[job]["rows"]]
        _, fl = quadratic_extremum(fcur, True)
        _, al = quadratic_extremum(acur, True)
        # 在 ANSYS 每个子步的 v 上二次插值 FEM 曲线；只在 FEM 路径覆盖
        # 的 v 区间内比较，避免端点外推。
        fvmax = max(v for v, _ in fcur)
        diffs = [abs(lam - interp_lambda(fcur, v))
                 for v, lam, _, _ in an_detail[job]["rows"]
                 if 0.0 <= v <= min(2 * cfg["H"], fvmax)]
        cworst = max(diffs) if diffs else float("nan")
        passed = (abs(fl - al) <= TOL_EXTREMUM and cworst <= TOL_CURVE)
        ok &= passed
        a("%-14s %-16s %13.8f %13.8f %13.4e %11.3e %9s" % (
            cid, job, fl, al, fl - al, cworst, "PASS" if passed else "FAIL"))
    a("  TOL: |d(lambda*)|<=%.1e, per-substep |lambda_ansys-lambda_fem(v)|<=%.1e"
      % (TOL_EXTREMUM, TOL_CURVE))
    a("")

    # ---------------- strain contrast ----------------
    if "sn_log_fine" in an_detail and "sn_eng" in an_detail:
        l1 = an_detail["sn_log_fine"]["lmax"]
        l0 = an_detail["sn_eng"]["lmax"]
        a("--- strain-measure contrast (documented, not a failure) ---")
        a("  KEYOPT(2)=1 lambda_max=%.7f  vs  KEYOPT(2)=0 lambda_max=%.7f  "
          "relative difference=%+.3f%%" % (l1, l0, 100 * (l0 - l1) / l1))
        a("  analytic: P_cr_log/P_cr_eng-1=%+.3f%%  -> KEYOPT(2)=1 is the log-strain"
          % (100 * (pcr_l / pcr_e - 1)))
        a("  measure; the ~2% offset of KEYOPT(2)=0 is constitutive, not algorithmic.")
        a("")
    a("NOTE: the ANSYS 'indefinite tangent matrix' warning at the limit point is the")
    a("physical signature of snap-through (tangent crosses zero); the run terminates")
    a("by ARCTRM at v=2h after lambda has decreased through zero into negative loads.")
    a("")
    a("CB3D17 SNAP-THROUGH ARC-LENGTH CASE " + ("PASSED" if ok else "FAILED"))
    return "\n".join(L) + "\n", bool(ok)


def manifest(warns: dict) -> str:
    L: list[str] = []
    a = L.append
    cfg = SNAP_CFG
    a("=" * 96)
    a("CB3D17  后屈曲 / snap-through 全过程算例归档")
    a("=" * 96)
    a("")
    a("唯一数据源: BridgeStudio/ansys_bridge_snap.py::SNAP_CFG /")
    a("             SNAP_FEM_CASES / SNAP_ANSYS_CASES")
    a("同一份参数导出 *.snap (FEM-Eigen snap_probe.exe) 与 3 份 LINK180+ARCLEN APDL。")
    a("")
    a("[公共参数]")
    for k in ("E", "A", "B", "H", "PREF", "VMAX", "TOL", "MAXSTEPS",
              "MAXITER", "DSMAX", "DSMIN", "TIME_END", "MINARC", "NEQIT"):
        a("  %-10s = %s" % (k, cfg[k]))
    a("")
    a("FEM 弧长法算例 %d 个：" % len(SNAP_FEM_CASES))
    for c in SNAP_FEM_CASES:
        a("  {:<14s} DS={:<7g} STRAIN={}  # {}".format(
            c["id"], c["DS"], c["STRAIN"], c["desc"]))
    a("")
    a("ANSYS 作业 %d 个：" % len(SNAP_ANSYS_CASES))
    for c in SNAP_ANSYS_CASES:
        a("  {:<16s} KEYOPT(2)={}  maxarc={:<7g} nsub={:<5d} # {}".format(
            c["id"], c["keyopt2"], c["maxarc"], c["nsub"], c["desc"]))
    a("")
    a("ANSYS 18.0 作业日志（errors/warnings）：")
    for k in sorted(warns):
        a("  {:<18s} errors={} warnings={}".format(k, warns[k][0], warns[k][1]))
    a("")
    a("口径说明：")
    a("  - ANSYS LINK180 KEYOPT(2)=1 为对数应变，与 FEM STRAIN=1 严格同口径；")
    a("    KEYOPT(2)=0 的作业只用于量化应变度量差异，不参与严格对标。")
    a("  - 极限点处 ANSYS 会提示 indefinite tangent matrix，属物理标志。")
    a("  - FEM 弧长求解器是独立新文件 stability_path_3d.hpp，未改主求解器；")
    a("    B 类规格「主链路未接入非线性稳定全过程」的如实边界仍然成立。")
    a("")
    a("保留 .snap / .inp / .csv / .txt / .out / .json 作为归档证据；")
    a("ANSYS 二进制与数据库产物由 .gitignore 排除。")
    return "\n".join(L) + "\n"


def sync_to(dst: Path, log: list[str]) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    pats = ["*.snap", "*.model.json", "*_fem.csv", "*_curve.csv", "*.inp",
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

    (OUT / PARAMS_FILE).write_text(
        params_text(SNAP_CFG, SNAP_FEM_CASES), encoding="ascii", newline="\n")
    snapshot = {"config": SNAP_CFG, "fem_cases": SNAP_FEM_CASES,
                "ansys_cases": SNAP_ANSYS_CASES,
                "source": "ansys_bridge_snap.py::SNAP_CFG / SNAP_*_CASES",
                "ansys_element": "LINK180", "arclen": True, "nlgeom": True,
                "path_method": "Crisfield spherical arc-length"}
    (OUT / (STEM + ".model.json")).write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    print("[GEN ] {} FEM cases, {} ANSYS jobs, .snap / .model.json".format(
        len(SNAP_FEM_CASES), len(SNAP_ANSYS_CASES)))

    ansys: dict[str, list] = {}
    warns: dict[str, tuple[int, int]] = {}
    for cs in SNAP_ANSYS_CASES:
        job = cs["id"]
        (OUT / (job + ".inp")).write_text(
            snap_apdl(SNAP_CFG, cs, job), encoding="ascii", newline="\n")
        if generate_only:
            continue
        for old in OUT.glob(job + "_*.csv"):
            old.unlink()
        rc = _run([ANSYS_EXE, "-b", "-j", job, "-i", job + ".inp",
                   "-o", job + "_ansys.out"], OUT, log, "ANSYS-" + job)
        op = OUT / (job + "_ansys.out")
        sw = _log_summary(op, log, "ANSYS-" + job) if op.exists() else (-1, -1)
        warns[job] = sw
        cp = OUT / (job + "_curve.csv")
        if rc != 0 or sw[0] != 0 or not cp.exists():
            print("[FAIL] {} ANSYS exit={} errors={} csv={}".format(
                job, rc, sw[0], cp.exists()))
            ok = False
            continue
        ansys[job] = read_curve_csv(cp)
        rows = ansys[job]
        lm = max(r[1] for r in rows)
        vm = max(r[0] for r in rows)
        print("[ANSYS] {:<16s} nset={:4d} lambda_max={:+.7f} v_max={:.6f}".format(
            job, len(rows), lm, vm))
    if generate_only:
        print("[GEN ] generate-only: {} APDLs written".format(len(SNAP_ANSYS_CASES)))
        return True

    fem_pts, fem_sum = {}, {}
    if not PROBE_EXE.exists():
        print("[FAIL] snap_probe.exe not found: {}".format(PROBE_EXE))
        ok = False
    else:
        rc = _run([PROBE_EXE, OUT / PARAMS_FILE, OUT / (STEM + "_fem.csv")],
                  OUT, log, "FEM-SNAP")
        if rc != 0:
            print("[FAIL] snap_probe exit={}".format(rc))
            ok = False
        else:
            fem_pts, fem_sum = read_fem_csv(OUT / (STEM + "_fem.csv"))
            for cid, s in fem_sum.items():
                print("[FEM  ] {:<14s} npts={:4d} worst_dP={:.3e} N  "
                      "lam*_fit={:.8f}".format(
                          cid, int(s["npoints"]), s["worst_dP"],
                          s["lambda_max_fit"]))

    if ansys and fem_pts:
        txt, cok = compare_report(ansys, warns, fem_pts, fem_sum)
        (OUT / (STEM + "_compare_report.txt")).write_text(txt, encoding="utf-8")
        ok &= cok
        print("[{}] CB3D17 FEM vs ANSYS comparison".format(
            "PASS" if cok else "FAIL"))

    (OUT / (STEM + "_manifest.txt")).write_text(
        manifest(warns), encoding="utf-8")
    (OUT / (STEM + "_run_log.txt")).write_text("\n".join(log) + "\n",
                                               encoding="utf-8")
    if sync_dir:
        sync_to(sync_dir, log)
        (OUT / (STEM + "_run_log.txt")).write_text("\n".join(log) + "\n",
                                                   encoding="utf-8")
    print("CB3D17 " + ("PASSED" if ok else "FAILED"))
    return bool(ok)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate-only", action="store_true")
    ap.add_argument("--sync-to", type=Path, default=None)
    a = ap.parse_args()
    return 0 if run(a.generate_only, a.sync_to) else 1


if __name__ == "__main__":
    sys.exit(main())
