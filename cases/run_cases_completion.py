#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CB3D21 算例编排：真实斜拉桥「成桥态起点全过程稳定」vs ANSYS 两段求解。

    C:\\Users\\user\\anaconda3\\python.exe cases\\run_cases_completion.py
        [--generate-only] [--sync-to <FEM-Eigen benchmark cases dir>]
        [--sync-only]

产出（BridgeStudio/cases/CB3D21_completion_stability_vs_ansys/）：
    cb3d21_completion_stability.dat / .model.json / .cfg.json   同源输入与快照
    cb3d21_completion_stability_cable.txt                      FEM 主链路原始结果
    cs_default.dat / cs_default_cable.txt                      默认零污染负例
    cs_load.inp / cs_load_curve.csv / cs_load_ansys.out         ANSYS 荷载控制主 job
    cs_arc.inp  / cs_arc_curve.csv  / cs_arc_ansys.out          ANSYS ARCLEN 交叉验证
    *_compare_report.txt / *_manifest.txt / *_run_log.txt

需要 ANSYS 18.0 许可（2 个 job）；--generate-only 只写输入文件。

判据口径（2026-11-15 ② 阶段物理定标，见
FEM-Eigen/tests/benchmark/completion_stability_3d/probes/README.md）：
  * FEM `BASIS=completion_state`：λ 只缩放 `*STABILITY_LOAD` 增量荷载，
    起点 `u0` 是成桥态收敛位移，`F0` 不缩放。`MODE=STATIC` + 显式 L0
    保证 `RES0` 与 `[NEWTON] RESLAST` 同量级（起点严格闭合）。
  * ANSYS step1 `TIME=1` 求成桥态；step2 把跨中节点荷载从 `F_orig`
    ramp 到 `F_orig - LAMMAX*PINC`。荷载控制主 job 的
    `lambda_FEM = (TIME-1)*LAMMAX`，`DeltaUZ = UZ - UZ_step1`
    与 FEM `[STABILITY]` 的 `(lambda, CTRL)` 逐点对拍。
  * **不用反力换算 λ**：完整斜拉桥 50 个 `F` / 34 个约束节点上
    `ΣRF,FZ` 在 step2 随加载反而下降，反力求和不可用。
  * ARCLEN job 的 `TIME` 不是 λ，只做终点位移交叉验证。
  * 该算例路径单调硬化、无极限点，ANSYS 荷载控制即可覆盖全程；
    越过极限点的能力由 L/N 阶段独立基准题与 FEM 弧长内核证明。

如实边界：`MODE=COMPLETION` 的索长迭代会改写 L0 导致 `u0` 与 `m`
不同源（CB3D10 实测 `RES0=3.15 MN`），本算例用 `STATIC` 绕开并登记为
独立后续项；② 完成后 B3 仍标 `P` 不标 `Y`。
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

from ansys_bridge_completion import (  # noqa: E402
    CASE_ID, COMPLETION_CASE_NAME, COMPLETION_CFG, EPS0, PINC, LAMMAX,
    CTRL_NODE, CTRL_DOF, VMAX, STAB_KEYS, ANSYS_CASES,
    build_model, design_coords, cable_l0, cable_elements, node_load,
    fem_dat, ansys_curve_apdl, read_curve_csv, read_fem_path,
    interp_ctrl, model_json,
)

ANSYS_EXE = Path(r"C:\Program Files\ANSYS Inc\v180\ANSYS\bin\winx64\ANSYS180.exe")
CABLE_EXE = Path(r"D:\AI-work\FEM-Eigen\build\cable_solver.exe")

OUT = HERE / COMPLETION_CASE_NAME
STEM = CASE_ID
DEFAULT_ID = "cs_default"

# 判据：按 ② 阶段端到端实测标定
TOL_RES0_REL = 1.0e-6       # RES0 / F0_NORM（起点闭合残差）
TOL_RES0_NEWTON = 10.0      # RES0 / [NEWTON] RESLAST（同量级）
TOL_START_UZ = 1.0e-4       # FEM CTRL0 vs ANSYS step1 UZ，m
TOL_CURVE = 1.0e-4          # 逐子步 |DeltaUZ - FEM CTRL|，m（实测 1.85e-5）
TOL_SLOPE_REL = 2.0e-2      # 初始线性段斜率 FEM vs ANSYS
TOL_ENDPOINT = 1.0e-4       # ARCLEN vs 荷载控制终点 UZ，m（实测 6.9e-8）
TOL_LAMBDA = 1.0e-8         # ANSYS 子步 λ 必须落在 FEM 路径内


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
        m = re.findall(r"NUMBER OF %s\s+MESSAGES ENCOUNTERED=\s*(\d+)" % label, txt)
        return int(m[-1]) if m else -1
    return grab("ERROR"), grab("WARNING")


def read_fem_header(path: Path) -> dict:
    """解析 FEM [STABILITY] 段头 + [NEWTON] RESLAST。"""
    out: dict = {"has_stability": False}
    if not path.exists():
        return out
    txt = path.read_text(encoding="utf-8", errors="replace")
    stab = txt[txt.find("[STABILITY]"):] if "[STABILITY]" in txt else ""
    out["has_stability"] = "[STABILITY]" in txt
    for key, pat in (
        ("basis", r"BASIS=(\S+)"),
        ("status", r"STATUS=(\S+)"),
        ("converged", r"CONVERGED=(\d+)"),
        ("npoint", r"NPOINT=(\d+)"),
        ("nstep", r"NSTEP=(\d+)"),
        ("lambda_final", r"LAMBDA_FINAL=(\S+) ERROR=(\S+)"),
        ("ctrl0", r"STATE CTRL0=(\S+) F0_NORM=(\S+) FINC_NORM=(\S+) RES0=(\S+)"),
    ):
        m = re.search(pat, stab)
        if not m:
            continue
        g = m.groups()
        if key == "basis" or key == "status":
            out[key] = g[0]
        elif key == "lambda_final":
            out["lambda_final"] = float(g[0])
            out["error"] = g[1]
        elif key == "ctrl0":
            out["ctrl0"] = float(g[0])
            out["f0_norm"] = float(g[1])
            out["finc_norm"] = float(g[2])
            out["res0"] = float(g[3])
        else:
            out[key] = int(g[0])
    m = re.search(r"\[NEWTON\][\s\S]*?RESLAST=(\S+)", txt)
    if m:
        out["reslast"] = float(m.group(1))
    m = re.search(r"\[U\][\s\S]*?^%d\s+%d\s+(\S+)" % (CTRL_NODE, CTRL_DOF),
                  txt, re.M)
    if m:
        out["u_ctrl"] = float(m.group(1))
    return out


def without_stability(dat: str) -> str:
    """删掉 opt-in 键与 *STABILITY_LOAD 段，得到默认零污染对照 .dat。"""
    keys = {s.strip() for s in STAB_KEYS.strip().splitlines()}
    out: list[str] = []
    i = 0
    lines = dat.splitlines()
    while i < len(lines):
        s = lines[i].strip()
        if s in keys:
            i += 1
            continue
        if s == "*STABILITY_LOAD":
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("*"):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out) + "\n"


def split_curve(curve: list[tuple[float, float, float]]):
    """返回 (step1 终点 UZ, step2 [(iset, TIME, UZ)])。"""
    s1 = [p for p in curve if p[1] <= 1.0 + 1e-9]
    s2 = [p for p in curve if p[1] > 1.0 + 1e-9]
    return (s1[-1][2] if s1 else None), s2


def compare_report(fem_hdr: dict, fem_path: list, ansys: dict,
                   warns: dict, default_hdr: dict, cfg: dict,
                   apdl_info: dict) -> tuple[str, bool]:
    lines: list[str] = []
    ok = True

    def judge(name: str, passed: bool, detail: str) -> None:
        nonlocal ok
        lines.append("[%s] %s : %s" % ("PASS" if passed else "FAIL", name, detail))
        if not passed:
            ok = False

    lines.append("CB3D21 completion-state stability path: FEM vs ANSYS two-step")
    lines.append("=" * 78)
    lines.append("双塔三跨斜拉桥（与 CB3D10 同几何/材料/恒载）；"
                 "MODE=STATIC + 设计线形 + 16 根索显式 L0")
    lines.append("main_span=%.1f m side_span=%.1f m tower_height=%.1f m "
                 "n_cable=%d panel=%.1f m" % (
                     COMPLETION_CFG["main_span"], COMPLETION_CFG["side_span"],
                     COMPLETION_CFG["tower_height"], COMPLETION_CFG["n_cable"],
                     COMPLETION_CFG["panel"]))
    lines.append("EPS0=%.3g  PINC=%.6g N  LAMMAX=%.6g  CTRL=(%d,%d)  "
                 "VMAX=%.6g m" % (EPS0, PINC, LAMMAX, CTRL_NODE, CTRL_DOF, VMAX))
    lines.append("FEM  R = F0 + lambda*Finc - Fint(u),  u(0)=u0 (成桥态)")
    lines.append("ANSYS step1 TIME=1 恒载成桥态；step2 F_orig=%.9g N -> "
                 "F_end=%.9g N" % (apdl_info.get("f_orig", 0.0),
                                    apdl_info.get("f_end", 0.0)))
    lines.append("lambda_FEM=(TIME-1)*LAMMAX（荷载控制）；不用反力求和（多荷载模型不可靠）")
    lines.append("")

    # ---- ① 起点自洽（独立锚点 1）----
    lines.append("--- 起点自洽：成桥态 u0 / F0 与 [NEWTON] 收敛残差 ---")
    res0 = fem_hdr.get("res0", float("nan"))
    f0n = fem_hdr.get("f0_norm", 0.0)
    rl = fem_hdr.get("reslast", 0.0)
    rel = res0 / f0n if f0n else 9.9
    judge("BASIS=completion_state / CONVERGED",
          fem_hdr.get("basis") == "completion_state"
          and fem_hdr.get("converged") == 1
          and fem_hdr.get("error") == "none",
          "basis=%s converged=%s error=%s NPOINT=%d" % (
              fem_hdr.get("basis"), fem_hdr.get("converged"),
              fem_hdr.get("error"), fem_hdr.get("npoint", 0)))
    judge("RES0 / F0_NORM <= %.1e" % TOL_RES0_REL, rel <= TOL_RES0_REL,
          "RES0=%.6g N  F0_NORM=%.6g N  rel=%.3e" % (res0, f0n, rel))
    judge("RES0 与 [NEWTON] RESLAST 同量级", res0 <= TOL_RES0_NEWTON * rl,
          "RES0=%.6g N  RESLAST=%.6g N  ratio=%.3f (tol %.1f)" % (
              res0, rl, res0 / rl if rl else 9.9, TOL_RES0_NEWTON))
    ctrl0 = fem_hdr.get("ctrl0", float("nan"))
    u_ctrl = fem_hdr.get("u_ctrl", float("nan"))
    judge("STATE CTRL0 == [U](1016,3)", abs(ctrl0 - u_ctrl) <= 1e-10,
          "CTRL0=%.12g  [U]=%.12g  |d|=%.3e" % (
              ctrl0, u_ctrl, abs(ctrl0 - u_ctrl)))
    lines.append("  FINC_NORM=%.9g N ; LAMBDA_FINAL=%.9g" % (
        fem_hdr.get("finc_norm", 0.0), fem_hdr.get("lambda_final", 0.0)))
    lines.append("")

    # ---- FEM 路径自洽 ----
    lines.append("--- FEM 弧长路径自洽 ---")
    lams = [p[0] for p in fem_path]
    mono = len(lams) > 2 and all(lams[i] >= lams[i - 1]
                                 for i in range(1, len(lams)))
    judge("路径单调硬化、无极限点（本算例口径）", mono,
          "NPOINT=%d  lambda 0 -> %.9g" % (
              len(lams), lams[-1] if lams else float("nan")))
    judge("FEM 路径覆盖 ANSYS 全部子步",
          (lams[-1] if lams else 0.0) >= LAMMAX - TOL_LAMBDA,
          "lambda_final=%.9g >= LAMMAX=%.9g" % (
              lams[-1] if lams else 0.0, LAMMAX))
    judge("默认零污染负例无 [STABILITY] 段",
          not default_hdr.get("has_stability", True),
          "cs_default has_stability=%s" % default_hdr.get("has_stability"))
    lines.append("")

    # ---- ANSYS 逐子步对拍 ----
    lines.append("--- ANSYS 荷载控制两段 vs FEM [STABILITY] 逐点对拍 ---")
    primary = "cs_load"
    curve = ansys.get(primary, [])
    uz0, s2 = split_curve(curve)
    if uz0 is None or not s2:
        judge("ANSYS 曲线数据齐备 [%s]" % primary, False,
              "n=%d step2=%d" % (len(curve), len(s2)))
    else:
        judge("FEM CTRL0 vs ANSYS step1 UZ", abs(ctrl0 - uz0) <= TOL_START_UZ,
              "FEM=%.9g m  ANSYS=%.9g m  |d|=%.3e m (tol %.1e)" % (
                  ctrl0, uz0, abs(ctrl0 - uz0), TOL_START_UZ))
        worst, wlam, cnt = 0.0, 0.0, 0
        for iset, tnow, uz in s2:
            lam = (tnow - 1.0) * LAMMAX
            if lam < -TOL_LAMBDA or lam > lams[-1] + TOL_LAMBDA:
                continue
            d = abs((uz - uz0) - interp_ctrl(fem_path, lam))
            cnt += 1
            if d > worst:
                worst, wlam = d, lam
        judge("逐子步 |DeltaUZ - FEM CTRL| <= %.1e m" % TOL_CURVE,
              cnt > 10 and worst <= TOL_CURVE,
              "worst=%.6e m @ lambda=%.6g, n=%d (tol %.1e)" % (
                  worst, wlam, cnt, TOL_CURVE))
        # 初始线性段斜率（独立锚点 2）：k = lambda*PINC / |DeltaUZ|
        small = [p for p in s2 if 0.0 < (p[1] - 1.0) * LAMMAX <= 0.05]
        if small and len(fem_path) > 1:
            t0 = small[0]
            lam0 = (t0[1] - 1.0) * LAMMAX
            k_ansys = lam0 * PINC / abs(t0[2] - uz0) if t0[2] != uz0 else 0.0
            k_fem = (fem_path[1][0] - fem_path[0][0]) * PINC / abs(
                fem_path[1][1] - fem_path[0][1])
            srel = abs(k_ansys - k_fem) / k_fem if k_fem else 9.9
            judge("初始线性段斜率 FEM vs ANSYS", srel <= TOL_SLOPE_REL,
                  "k_ansys=%.6g N/m  k_fem=%.6g N/m  rel=%.3e (tol %.1e)" % (
                      k_ansys, k_fem, srel, TOL_SLOPE_REL))
        lines.append("  step1 UZ=%.9g m ; step2 %d 子步 ; "
                     "终点 UZ=%.9g m (DeltaUZ=%.9g m)" % (
                         uz0, len(s2), s2[-1][2], s2[-1][2] - uz0))
    lines.append("")

    # ---- ARCLEN 交叉验证（独立锚点 3）----
    lines.append("--- ARCLEN 交叉验证（TIME 不是 lambda，只比终点）---")
    arc = ansys.get("cs_arc", [])
    if not arc:
        judge("ANSYS ARCLEN 曲线存在", False, "no curve data")
    else:
        judge("ARCLEN job 终点 vs 荷载控制终点",
              abs(arc[-1][2] - curve[-1][2]) <= TOL_ENDPOINT if curve else False,
              "ARCLEN UZ=%.9g m  load UZ=%.9g m  |d|=%.3e m (tol %.1e)" % (
                  arc[-1][2], curve[-1][2] if curve else float("nan"),
                  abs(arc[-1][2] - curve[-1][2]) if curve else 9.9,
                  TOL_ENDPOINT))
        lines.append("  ARCLEN substeps=%d  TIME_final=%.9g（不作 lambda 换算）" % (
            len(arc), arc[-1][1]))
    lines.append("")

    for ac in ANSYS_CASES:
        e, w = warns.get(ac["id"], (-1, -1))
        judge("ANSYS 日志 0 error [%s]" % ac["id"], e == 0,
              "errors=%d warnings=%d" % (e, w))
    lines.append("")
    lines.append("口径边界：BASIS=completion_state 的 lambda 只缩放增量荷载，")
    lines.append("不是规范稳定安全系数；本算例路径单调硬化无极限点，")
    lines.append("越过极限点能力由 L/N 阶段独立基准题与 FEM 弧长内核证明。")
    lines.append("MODE=COMPLETION 索长迭代改写 L0 导致 u0 与 m 不同源，")
    lines.append("本算例用 MODE=STATIC + 显式 L0 绕开，该缺陷登记为独立后续项。")
    lines.append("② 完成后 B3 能力矩阵仍标 P 不标 Y。")
    lines.append("")
    lines.append("RESULT: %s" % ("ALL CHECKS PASSED" if ok else "FAILED"))
    return "\n".join(lines) + "\n", ok


def manifest(cfg: dict, apdl_info: dict) -> str:
    L: list[str] = [
        "CB3D21 manifest - completion-state stability path vs ANSYS two-step",
        "case_id = %s" % CASE_ID,
        "fem_exe = cable_solver.exe",
        "ansys_exe = ANSYS180.exe (v180)",
        "",
        "唯一数据源: BridgeStudio.ansys_bridge_completion.COMPLETION_CFG "
        "+ EPS0 + PINC + LAMMAX",
        "  ModelSpec3D(mode=STATIC, 设计线形) -> *.dat / *.inp 同源导出",
        "",
        "COMPLETION_CFG:",
    ]
    for k in sorted(COMPLETION_CFG):
        L.append("  %s = %s" % (k, COMPLETION_CFG[k]))
    L += [
        "EPS0 = %.12g" % EPS0,
        "PINC = %.12g" % PINC,
        "LAMMAX = %.12g" % LAMMAX,
        "CTRL_NODE = %d ; CTRL_DOF = %d ; VMAX = %.12g" % (
            CTRL_NODE, CTRL_DOF, VMAX),
        "STAB_KEYS = %s" % " ".join(STAB_KEYS.split()),
        "F_orig(%d,FZ) = %.12g N" % (CTRL_NODE, apdl_info.get("f_orig", 0.0)),
        "F_end(%d,FZ)  = %.12g N" % (CTRL_NODE, apdl_info.get("f_end", 0.0)),
        "INISTATE eps_uniform = %.12g" % apdl_info.get("eps_uniform", 0.0),
        "",
        "ANSYS cases:",
    ]
    for c in ANSYS_CASES:
        L.append("  %-10s arclen=%-5s time2=%-5g %s" % (
            c["id"], c["arclen"], c["time2"], c["desc"]))
    L += ["", "Files (frozen as benchmark evidence):"]
    for name in sorted(p.name for p in OUT.iterdir() if p.is_file()):
        L.append("  " + name)
    return "\n".join(L) + "\n"


SYNC_PATTERNS = ("*.dat", "*.model.json", "*.cfg.json", "*_cable.txt",
                 "*.inp", "*_curve.csv", "*_ansys.out",
                 "*_compare_report.txt", "*_manifest.txt", "*_run_log.txt")


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
    log: list[str] = ["CB3D21 run log", "=" * 60]

    m, _info = build_model()
    coords = design_coords(m)
    l0 = cable_l0(m, coords)
    dat = fem_dat(m, l0)
    dat_default = without_stability(dat)

    (OUT / (STEM + ".dat")).write_text(dat, encoding="utf-8")
    (OUT / (DEFAULT_ID + ".dat")).write_text(dat_default, encoding="utf-8")
    (OUT / (STEM + ".model.json")).write_text(model_json(m) + "\n",
                                              encoding="utf-8")
    cfg = {
        "case_id": CASE_ID,
        "COMPLETION_CFG": COMPLETION_CFG,
        "EPS0": EPS0, "PINC": PINC, "LAMMAX": LAMMAX,
        "CTRL_NODE": CTRL_NODE, "CTRL_DOF": CTRL_DOF, "VMAX": VMAX,
        "STAB_KEYS": STAB_KEYS,
        "ANSYS_CASES": ANSYS_CASES,
        "cable_L0": {str(k): v for k, v in l0.items()},
        "nodes": len(m.nodes), "elements": len(m.elements),
        "cables": len(cable_elements(m)),
        "F_orig": node_load(m, CTRL_NODE, CTRL_DOF),
    }
    (OUT / (STEM + ".cfg.json")).write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    apdl_info: dict = {}
    for ac in ANSYS_CASES:
        apdl, ai = ansys_curve_apdl(m, l0, jobname=ac["id"], case=ac)
        (OUT / (ac["id"] + ".inp")).write_text(apdl, encoding="ascii")
        apdl_info = ai
    print("[GEN ] %s.dat / %s.dat / %s.model.json / %s.cfg.json / %d ANSYS .inp" % (
        STEM, DEFAULT_ID, STEM, STEM, len(ANSYS_CASES)))
    if generate_only:
        if sync:
            sync_to(Path(sync))
        return 0

    if not ANSYS_EXE.exists():
        print("[FAIL] ANSYS180.exe not found:", ANSYS_EXE)
        return 1
    if not CABLE_EXE.exists():
        print("[FAIL] cable_solver.exe not found:", CABLE_EXE)
        return 1

    for ac in ANSYS_CASES:
        job = ac["id"]
        rc = _run([ANSYS_EXE, "-b", "-j", job, "-i", job + ".inp",
                   "-o", job + "_ansys.out"], OUT, log, job)
        print("[ANSYS] %-10s exit=%d" % (job, rc))
        if rc != 0:
            log.append("[FATAL] ANSYS job %s failed" % job)

    for stem in (STEM, DEFAULT_ID):
        rc = _run([CABLE_EXE, stem + ".dat"], OUT, log, "cable_solver/" + stem)
        print("[FEM  ] %-28s exit=%d" % (stem, rc))
        if rc != 0:
            log.append("[FATAL] cable_solver %s failed" % stem)

    ansys: dict = {}
    warns: dict = {}
    for ac in ANSYS_CASES:
        cid = ac["id"]
        p = OUT / (cid + "_curve.csv")
        ansys[cid] = read_curve_csv(p) if p.exists() else []
        warns[cid] = _log_summary(OUT / (cid + "_ansys.out"))

    fem_txt = OUT / (STEM + "_cable.txt")
    fem_hdr = read_fem_header(fem_txt)
    fem_path = read_fem_path(fem_txt) if fem_txt.exists() else []
    default_hdr = read_fem_header(OUT / (DEFAULT_ID + "_cable.txt"))

    report, ok = compare_report(fem_hdr, fem_path, ansys, warns,
                                default_hdr, cfg, apdl_info)
    (OUT / (STEM + "_compare_report.txt")).write_text(report, encoding="utf-8")
    (OUT / (STEM + "_manifest.txt")).write_text(
        manifest(cfg, apdl_info), encoding="utf-8")
    (OUT / (STEM + "_run_log.txt")).write_text("\n".join(log) + "\n",
                                               encoding="ascii", errors="replace")
    print(report)
    if sync:
        sync_to(Path(sync))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="CB3D21 completion stability case")
    ap.add_argument("--generate-only", action="store_true",
                    help="only write .dat/.json/.inp, do not run ANSYS / FEM")
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
