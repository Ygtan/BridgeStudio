#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CB3D19 算例编排：CR 梁刚接框架后屈曲 vs ANSYS BEAM188 + ARCLEN。

    C:\\Users\\user\\anaconda3\\python.exe cases\\run_cases_frame.py
        [--generate-only] [--sync-to <FEM-Eigen benchmark cases dir>]

产出（BridgeStudio/cases/CB3D19_frame_postbuckling_arc_length/）：
    cb3d19_frame_postbuckling.frame / .model.json     同源参数与快照
    <case>.inp / <case>_curve.csv / <case>_ansys.out  2 个 ANSYS BEAM188+ARCLEN 作业
    cb3d19_frame_postbuckling_fem.csv                frame_probe.exe 弧长法路径
    *_compare_report.txt / *_manifest.txt / *_run_log.txt

需要 ANSYS 18.0 许可（2 个 job）；--generate-only 只写输入文件。
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

from ansys_bridge_frame import (  # noqa: E402
    CASE_ID, PARAMS_FILE, FRAME_CASE_NAME, FRAME_CFG, FRAME_ANSYS_CASES,
    FRAME_FEM_CASES, apex_node, first_limit_point, frame_apdl, frame_nodes,
    interp_lambda, linear_frame_stiffness, params_text, read_curve_csv,
    right_node,
)

ANSYS_EXE = Path(r"C:\Program Files\ANSYS Inc\v180\ANSYS\bin\winx64\ANSYS180.exe")
PROBE_EXE = Path(r"D:\AI-work\FEM-Eigen\build\frame_probe.exe")

OUT = HERE / FRAME_CASE_NAME
STEM = CASE_ID

# 判据：数值按实测标定，量级与「两套求解器一致性」分开表述
TOL_K0_REL = 1.0e-8        # 独立线性刚架锚点 vs FEM 内核精确切线
TOL_SLOPE_REL = 1.0e-2     # ANSYS 极小位移子步 λ/v vs k0/PREF
TOL_LAMBDA_TIME = 1.0e-4   # ANSYS 反力反算 λ vs TIME（极限点附近最大）
TOL_CURVE = 1.0e-3         # FEM 三点二次插值 vs ANSYS 逐子步 λ
TOL_EXTREMUM = 2.0e-3      # 第一极限点二次拟合 FEM vs ANSYS
TOL_RES_REL = 1.0e-8       # FEM 路径最大残差相对 |Fref|


def _run(cmd: list, cwd: Path, log: list[str], tag: str) -> int:
    log.append("$ " + " ".join(str(c) for c in cmd))
    r = subprocess.run([str(c) for c in cmd], cwd=str(cwd), timeout=1800,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, errors="replace")
    log.append("[%s] exit=%d" % (tag, r.returncode))
    if r.stdout:
        log.extend(r.stdout.splitlines()[-8:])
    return r.returncode


def _log_summary(path: Path) -> tuple[int, int]:
    txt = path.read_text(encoding="ascii", errors="replace") if path.exists() else ""
    def grab(label: str) -> int:
        # ANSYS 排版里 "ERROR   MESSAGES" / "WARNING MESSAGES" 的空格数不固定
        m = re.findall(r"NUMBER OF %s\s+MESSAGES ENCOUNTERED=\s*(\d+)" % label, txt)
        return int(m[-1]) if m else -1
    return grab("ERROR"), grab("WARNING")


def read_fem_csv(path: Path):
    """返回 (P 行按 case 分组, S 行汇总, E 行错误码)。"""
    pts: dict[str, list[tuple]] = {}
    summ: dict[str, dict[str, float]] = {}
    errors: dict[str, str] = {}
    for line in path.read_text(encoding="ascii", errors="replace").splitlines():
        p = line.split(",")
        if not p:
            continue
        try:
            if p[0] == "P" and len(p) >= 9:
                pts.setdefault(p[1], []).append((
                    int(p[2]), float(p[3]), float(p[4]), float(p[5]),
                    int(p[6]), float(p[7]), float(p[8])))
            elif p[0] == "S" and len(p) >= 4:
                summ.setdefault(p[1], {})[p[2]] = float(p[3])
            elif p[0] == "E" and len(p) >= 3:
                errors[p[1]] = p[2]
        except (ValueError, IndexError):
            continue
    return pts, summ, errors


def fem_curve(pts: dict[str, list[tuple]], cid: str) -> list[tuple[float, float]]:
    return [(x[2], x[1]) for x in pts.get(cid, [])]


def compare_report(ansys: dict, warns: dict, fem_pts: dict,
                   fem_sum: dict, errors: dict, k0: float) -> tuple[str, bool]:
    cfg = FRAME_CFG
    lines: list[str] = []
    ok = True

    def judge(name: str, passed: bool, detail: str) -> None:
        nonlocal ok
        lines.append("[%s] %s : %s" % ("PASS" if passed else "FAIL", name, detail))
        if not passed:
            ok = False

    lines.append("CB3D19 CR beam frame post-buckling: FEM arc-length vs ANSYS ARCLEN")
    lines.append("=" * 78)
    lines.append("E=%.6g Pa  G=%.6g Pa  A=%.6g m2  IY=IZ=%.6g m4  J=%.6g m4" % (
        cfg["E"], cfg["G"], cfg["A"], cfg["IY"], cfg["J"]))
    lines.append("B=%.6g m  H=%.6g m  NELEM=%d/slope  PREF=%.6g N  VMAX=%.6g m" % (
        cfg["B"], cfg["H"], cfg["NELEM"], cfg["PREF"], cfg["VMAX"]))
    lines.append("BEAM188 KEYOPT(3)=2 + Gx1000 (EB退化); IY=IZ 消除局部轴约定差异")
    lines.append("")

    # ---- 独立线性刚架刚度锚点 ----
    lines.append("--- 独立解析锚点：2D 线性刚架刚度矩阵（纯 Python，非 FEM 内核）---")
    lines.append("k0_analytic = %.12e N/m ; k0/PREF = %.12f" % (k0, k0 / cfg["PREF"]))
    for c in FRAME_FEM_CASES:
        if c.get("negative"):
            continue
        kf = fem_sum.get(c["id"], {}).get("k0_fem", 0.0)
        rel = abs(kf - k0) / k0 if k0 else 9.9
        judge("k0 FEM vs 独立锚点 [%s]" % c["id"], rel <= TOL_K0_REL,
              "k0_fem=%.12e  rel=%.3e (tol %.1e)" % (kf, rel, TOL_K0_REL))
    lines.append("")

    # ---- ANSYS 逐子步自检 ----
    lines.append("--- ANSYS 逐子步：反力反算 λ 与 TIME、初始线性斜率、极限点 ---")
    for ac in FRAME_ANSYS_CASES:
        cur = ansys.get(ac["id"], [])
        if not cur:
            judge("ANSYS 曲线存在 [%s]" % ac["id"], False, "no curve data")
            continue
        dlt = max(abs(x[1] - x[2]) for x in cur)
        judge("lambda_reaction vs TIME [%s]" % ac["id"], dlt <= TOL_LAMBDA_TIME,
              "worst=%.6e (tol %.1e), n=%d" % (dlt, TOL_LAMBDA_TIME, len(cur)))
        small = [x for x in cur if 0.0 < x[0] <= 0.002 * cfg["H"]]
        if small:
            slope = small[-1][1] / small[-1][0]
            rel = abs(slope - k0 / cfg["PREF"]) / (k0 / cfg["PREF"])
            judge("ANSYS 初始斜率 vs k0/PREF [%s]" % ac["id"],
                  rel <= TOL_SLOPE_REL,
                  "slope=%.9f  analytic=%.9f  rel=%.3e (tol %.1e)" % (
                      slope, k0 / cfg["PREF"], rel, TOL_SLOPE_REL))
        vf, lf, vr, lr = first_limit_point([(x[0], x[1]) for x in cur])
        lines.append("  first limit point [%s]: v*=%.7f  lambda*=%.7f "
                     "(raw v=%.7f lambda=%.7f)" % (ac["id"], vf, lf, vr, lr))
        lmin = min(x[1] for x in cur)
        judge("下降段反向荷载 [%s]" % ac["id"], lmin < 0.0,
              "lambda_min=%.7f" % lmin)
        lines.append("  v_final=%.6f ; ANSYS log errors=%d warnings=%d" % (
            cur[-1][0], warns.get(ac["id"], (-1, -1))[0],
            warns.get(ac["id"], (-1, -1))[1]))
    lines.append("")

    # ---- FEM 路径自洽与负例 ----
    lines.append("--- FEM 弧长路径自洽 ---")
    for c in FRAME_FEM_CASES:
        cid = c["id"]
        s = fem_sum.get(cid, {})
        if c.get("negative"):
            got = errors.get(cid, "")
            judge("负例错误码 [%s]" % cid, got == c["negative"],
                  "expect %s got %s" % (c["negative"], got or "<none>"))
            continue
        rel_res = s.get("max_residual_rel", 9.9)
        judge("收敛与残差 [%s]" % cid,
              s.get("converged", 0) > 0.5 and s.get("error_empty", 0) > 0.5
              and rel_res <= TOL_RES_REL,
              "converged=%.0f error_empty=%.0f max_res_rel=%.3e npoints=%.0f" % (
                  s.get("converged", 0), s.get("error_empty", 0), rel_res,
                  s.get("npoints", 0)))
        judge("下降段与终止位移 [%s]" % cid,
              s.get("lambda_min", 0.0) < 0.0
              and s.get("v_final", 0.0) >= 0.99 * cfg["VMAX"],
              "lambda_min=%.7f v_final=%.6f" % (
                  s.get("lambda_min", 0.0), s.get("v_final", 0.0)))
    lines.append("")

    # ---- FEM vs ANSYS 完整曲线 ----
    lines.append("--- FEM 弧长路径 vs ANSYS ARCLEN 曲线（三点二次插值）---")
    for ac in FRAME_ANSYS_CASES:
        if not ac.get("fem"):
            continue
        fc = fem_curve(fem_pts, ac["fem"])
        cur = ansys.get(ac["id"], [])
        if not fc or not cur:
            judge("曲线数据齐备 [%s]" % ac["id"], False, "missing data")
            continue
        worst, wv = 0.0, 0.0
        for v, lr, lt, iset in cur:
            if 0.0 <= v <= cfg["VMAX"]:
                d = abs(lr - interp_lambda(fc, v))
                if d > worst:
                    worst, wv = d, v
        judge("曲线一致性 [%s] vs FEM[%s]" % (ac["id"], ac["fem"]),
              worst <= TOL_CURVE,
              "worst|dlambda|=%.6e @ v=%.5f (tol %.1e)" % (
                  worst, wv, TOL_CURVE))
        vf, lf, _, _ = first_limit_point([(x[0], x[1]) for x in cur])
        fs = fem_sum.get(ac["fem"], {})
        judge("第一极限点 [%s]" % ac["id"],
              abs(lf - fs.get("lambda_max_fit", -9.9)) <= TOL_EXTREMUM
              and abs(vf - fs.get("v_at_lambda_max_fit", -9.9)) <= TOL_EXTREMUM,
              "ANSYS=(%.7f,%.7f) FEM=(%.7f,%.7f) tol=%.1e" % (
                  vf, lf, fs.get("v_at_lambda_max_fit", -9.9),
                  fs.get("lambda_max_fit", -9.9), TOL_EXTREMUM))
    lines.append("")

    for ac in FRAME_ANSYS_CASES:
        e, w = warns.get(ac["id"], (-1, -1))
        judge("ANSYS 日志 0 error [%s]" % ac["id"], e == 0,
              "errors=%d warnings=%d" % (e, w))
    lines.append("")
    lines.append("RESULT: %s" % ("ALL CHECKS PASSED" if ok else "FAILED"))
    return "\n".join(lines) + "\n", ok


def manifest() -> str:
    cfg = FRAME_CFG
    L: list[str] = []
    L.append("CB3D19 manifest - CR beam frame post-buckling")
    L.append("case_id = %s" % CASE_ID)
    L.append("params  = %s" % PARAMS_FILE)
    L.append("fem_exe = frame_probe.exe")
    for k in ("E", "G", "A", "IY", "IZ", "J", "B", "H", "NELEM", "PREF",
              "VMAX", "TOL", "MAXSTEPS", "MAXITER", "DSMAX", "DSMIN",
              "TIME_END", "MINARC", "NEQIT", "PRXY"):
        L.append("%s = %s" % (k, cfg[k]))
    L.append("apex_node = %d ; right_node = %d ; nodes = %d" % (
        apex_node(cfg), right_node(cfg), len(frame_nodes(cfg))))
    L.append("k0_analytic = %.12e" % linear_frame_stiffness(cfg))
    L.append("")
    L.append("FEM cases:")
    for c in FRAME_FEM_CASES:
        L.append("  %-12s group=%-5s DS=%g PSI=%g %s" % (
            c["id"], c["group"], c["DS"], c["PSI"],
            "NEG:" + c["negative"] if c.get("negative") else c["desc"]))
    L.append("ANSYS cases:")
    for c in FRAME_ANSYS_CASES:
        L.append("  %-12s maxarc=%g nsub=%d fem=%s" % (
            c["id"], c["maxarc"], c["nsub"], c["fem"]))
    L.append("")
    L.append("Files (frozen as benchmark evidence):")
    for name in sorted(p.name for p in OUT.iterdir() if p.is_file()):
        L.append("  " + name)
    return "\n".join(L) + "\n"


SYNC_PATTERNS = ("*.frame", "*.model.json", "*_fem.csv", "*_curve.csv",
                 "*.inp", "*_ansys.out", "*_compare_report.txt",
                 "*_manifest.txt", "*_run_log.txt")


def sync_to(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for pat in SYNC_PATTERNS:
        for src in OUT.glob(pat):
            shutil.copy2(src, dest / src.name)
            n += 1
    print("[SYNC] %d files -> %s" % (n, dest))


def run(generate_only: bool, sync: str | None) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    log: list[str] = ["CB3D19 run log", "=" * 60]
    cfg = FRAME_CFG

    (OUT / PARAMS_FILE).write_text(
        params_text(cfg, FRAME_FEM_CASES), encoding="ascii")
    model = {
        "case_id": CASE_ID,
        "cfg": cfg,
        "fem_cases": FRAME_FEM_CASES,
        "ansys_cases": FRAME_ANSYS_CASES,
        "nodes": frame_nodes(cfg),
        "apex_node": apex_node(cfg),
        "right_node": right_node(cfg),
        "k0_analytic": linear_frame_stiffness(cfg),
    }
    (OUT / (STEM + ".model.json")).write_text(
        json.dumps(model, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for ac in FRAME_ANSYS_CASES:
        (OUT / (ac["id"] + ".inp")).write_text(
            frame_apdl(cfg, ac, ac["id"]), encoding="ascii")
    print("[GEN ] %s, %s.model.json, %d ANSYS .inp" % (
        PARAMS_FILE, STEM, len(FRAME_ANSYS_CASES)))
    if generate_only:
        if sync:
            sync_to(Path(sync))
        return 0

    if not ANSYS_EXE.exists():
        print("[FAIL] ANSYS180.exe not found:", ANSYS_EXE)
        return 1
    if not PROBE_EXE.exists():
        print("[FAIL] frame_probe.exe not found:", PROBE_EXE)
        return 1

    for ac in FRAME_ANSYS_CASES:
        job = ac["id"]
        rc = _run([ANSYS_EXE, "-b", "-j", job, "-i", job + ".inp",
                   "-o", job + "_ansys.out"], OUT, log, job)
        print("[ANSYS] %-12s exit=%d" % (job, rc))
        if rc != 0:
            log.append("[FATAL] ANSYS job %s failed" % job)

    fem_csv = OUT / (STEM + "_fem.csv")
    rc = _run([PROBE_EXE, PARAMS_FILE, fem_csv.name], OUT, log, "frame_probe")
    print("[FEM  ] frame_probe exit=%d" % rc)

    ansys: dict = {}
    warns: dict = {}
    for ac in FRAME_ANSYS_CASES:
        cid = ac["id"]
        p = OUT / (cid + "_curve.csv")
        ansys[cid] = read_curve_csv(p, cfg) if p.exists() else []
        warns[cid] = _log_summary(OUT / (cid + "_ansys.out"))
    fem_pts, fem_sum, errors = read_fem_csv(fem_csv) if fem_csv.exists() else ({}, {}, {})
    k0 = linear_frame_stiffness(cfg)

    report, ok = compare_report(ansys, warns, fem_pts, fem_sum, errors, k0)
    (OUT / (STEM + "_compare_report.txt")).write_text(report, encoding="utf-8")
    (OUT / (STEM + "_manifest.txt")).write_text(manifest(), encoding="utf-8")
    (OUT / (STEM + "_run_log.txt")).write_text("\n".join(log) + "\n",
                                               encoding="ascii", errors="replace")
    print(report)
    if sync:
        sync_to(Path(sync))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="CB3D19 frame post-buckling case")
    ap.add_argument("--generate-only", action="store_true",
                    help="only write .frame/.model.json/.inp, do not run ANSYS")
    ap.add_argument("--sync-to", default=None,
                    help="copy frozen evidence into a FEM-Eigen benchmark cases dir")
    ap.add_argument("--sync-only", action="store_true",
                    help="only copy already-frozen evidence (no generate / no ANSYS)")
    a = ap.parse_args()
    if a.sync_only:
        if not a.sync_to:
            print("[FAIL] --sync-only requires --sync-to")
            return 2
        sync_to(Path(a.sync_to))
        return 0
    return run(a.generate_only, a.sync_to)


if __name__ == "__main__":
    sys.exit(main())
