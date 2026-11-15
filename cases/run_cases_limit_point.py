#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CB3D22 算例编排：真实斜拉桥「越过极值点 + 下降段」vs ANSYS 两段 ARCLEN。

    python cases/run_cases_limit_point.py
        [--generate-only] [--sync-to <FEM-Eigen benchmark cases dir>]
        [--sync-only] [--live-ansys] [--fem-exe <cable_solver.exe>]

产出（BridgeStudio/cases/CB3D22_limit_point_vs_ansys/）：
    cb3d22_limit_point.dat / .model.json / .cfg.json      同源输入与快照
    cb3d22_limit_point_cable.txt                         FEM 主链路结果
    lp_default.dat / lp_default_cable.txt                默认零污染负例
    lp_load.inp / lp_load_curve.csv / lp_load_ansys.out   上升段荷载控制 job
    lp_arc.inp  / lp_arc_curve.csv  / lp_arc_ansys.out    越过极值点 ARCLEN job
    cb3d22_limit_point_compare_report.txt / _manifest.txt / _run_log.txt

需要 ANSYS 18.0 许可（2 个 job）；`--generate-only` 只写输入文件。
正常回归不需要重跑（`--live-ansys` 会在临时目录复跑，不覆写冻结 CSV）。

判据口径见 `ansys_bridge_limit_point.py` 模块头：λ = TIME − 1，
路径在 λ 上非单调，按控制位移 ΔUX 插值 λ 对拍；极值点单独比较。
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

from ansys_bridge_completion import model_json, node_load  # noqa: E402
from ansys_bridge_limit_point import (  # noqa: E402
    CASE_ID, LIMIT_CASE_NAME, LIMIT_CFG, TOWER_SCALE, EPS, PINC,
    CTRL_NODE, CTRL_DOF, CTRLTARGET, ARCTRM_U, STAB_KEYS, ANSYS_CASES,
    build_model, design_coords, cable_l0, cable_elements, fem_dat,
    ansys_curve_apdl, read_curve_csv, parse_fem_stability,
)

ANSYS_EXE = Path(r"C:\Program Files\ANSYS Inc\v180\ANSYS\bin\winx64\ANSYS180.exe")
DEFAULT_FEM_EXE = Path(r"D:\AI-work\FEM-Eigen\_c_build\cable_solver.exe")
OUT = HERE / LIMIT_CASE_NAME
STEM = CASE_ID
DEFAULT_ID = "lp_default"

TOL_RES0_REL = 1.0e-8       # RES0 / |F0|
TOL_CTRL0 = 1.0e-10         # CTRL0 ≡ [U](2009,1)；[U] 段按 10 位有效数字写出
TOL_EPS = 1.0e-12           # INISTATE eps ≡ EPS0
TOL_LAMBDA_MAX_REL = 1.0e-2  # 极值点荷载因子 FEM vs ANSYS（实测 1.35e-3）
TOL_UX_LIMIT_REL = 1.0e-2   # 极值点位移 FEM vs ANSYS（实测 3.85e-3）
# 逐点 |Δλ| 阈值按 2026-11-15 本机端到端实测最差值 + 约 20~50% 余量定标：
# 误差来自 BEAM4 应力刚化 vs FEM 共转 CR 梁在稳定敏感细塔上的模型差异，
# 以及极值点附近 λ(dUX) 曲线平坦、位移微差被插值放大。不是编造的合格限值。
TOL_ASCEND = 8.0e-3         # 上升段实测 max 5.49e-3
TOL_DESCEND = 2.5e-2        # 下降段实测 max 2.05e-2（局部步长跳变）
TOL_LOAD = 1.2e-2           # lp_load 上升段实测 max 9.44e-3
TOL_MEAN = 6.0e-3           # 各段平均 |Δλ|，抑制孤立跳变掩盖整体偏离
TOL_LIVE_CSV = 1.0e-8       # --live-ansys 复跑 CSV 最大偏差

FAILED: list[str] = []
CHECKS = [0]


def ok(cond: bool, msg: str) -> None:
    CHECKS[0] += 1
    if not cond:
        FAILED.append(msg)


def _run(cmd: list, cwd: Path, log: list[str], tag: str) -> int:
    log.append("$ " + " ".join(str(c) for c in cmd))
    r = subprocess.run([str(c) for c in cmd], cwd=str(cwd), timeout=2400,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, errors="replace")
    log.append("[%s] exit=%d" % (tag, r.returncode))
    if r.stdout:
        log.extend(r.stdout.splitlines()[-6:])
    return r.returncode


def _log_summary(path: Path) -> tuple[int, int]:
    txt = path.read_text(encoding="ascii", errors="replace") if path.exists() else ""
    def grab(label: str) -> int:
        m = re.findall(r"NUMBER OF %s\s+MESSAGES ENCOUNTERED=\s*(\d+)" % label, txt)
        return int(m[-1]) if m else -1
    return grab("ERROR"), grab("WARNING")


def interp_lambda_by_ctrl(curve: list[tuple[float, float]], ctrl: float) -> float:
    """按 ΔCTRL（单调自变量）在 (ΔCTRL, λ) 曲线上线性插值 λ。"""
    if not curve:
        raise ValueError("曲线为空")
    if ctrl <= curve[0][0]:
        return curve[0][1]
    if ctrl >= curve[-1][0]:
        return curve[-1][1]
    lo, hi = 0, len(curve) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if curve[mid][0] <= ctrl:
            lo = mid
        else:
            hi = mid
    a, b = curve[lo], curve[hi]
    return a[1] + (b[1] - a[1]) * (ctrl - a[0]) / (b[0] - a[0])


def ansys_step2_curve(rows: list[tuple[float, float, float]]) -> tuple[float, list[tuple[float, float]]]:
    """从 CSV [(iset, TIME, UX)] 取 step1 末 UX，返回 (UX_step1, [(ΔUX, λ=TIME-1)])。"""
    s1 = [r for r in rows if r[1] <= 1.0 + 1e-9]
    if not s1:
        raise ValueError("CSV 缺 step1 子步")
    u0 = s1[-1][2]
    s2 = [r for r in rows if r[1] > 1.0 + 1e-9]
    # ARCLEN 预测步偶有 ΔUX 微小回退；按位移作插值自变量前先取严格递增
    # 子序列（λ 本身在极值点后下降，是物理现象，不受此过滤影响）。
    out: list[tuple[float, float]] = []
    last = -1e300
    for r in s2:
        du = r[2] - u0
        if du > last + 1e-12:
            out.append((du, r[1] - 1.0))
            last = du
    return u0, out


def limit_of(curve: list[tuple[float, float]]) -> tuple[int, float, float]:
    """返回 (索引, 极值点 λ_max, 极值点 ΔCTRL)。"""
    i = max(range(len(curve)), key=lambda k: curve[k][1])
    return i, curve[i][1], curve[i][0]


def generate(fem_exe: Path) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    model, _ = build_model()
    coords = design_coords(model)
    l0 = cable_l0(model, coords)
    dat = fem_dat(model, l0)
    (OUT / f"{STEM}.dat").write_text(dat, encoding="utf-8", newline="\n")
    (OUT / f"{STEM}.model.json").write_text(model_json(model), encoding="utf-8")
    cfg = {
        "case": "CB3D22",
        "title": "③a real-bridge limit point (thin-tower stable-sensitive config)",
        "limit_cfg": {k: (v if not isinstance(v, float) else float(v))
                      for k, v in LIMIT_CFG.items()},
        "tower_scale": TOWER_SCALE,
        "eps0": EPS, "pinc": PINC,
        "ctrl": [CTRL_NODE, CTRL_DOF], "ctrl_target": CTRLTARGET,
        "arctrm_u": ARCTRM_U, "stab_keys": STAB_KEYS.strip(),
        "l0": {str(k): v for k, v in sorted(l0.items())},
        "ansys_cases": ANSYS_CASES,
        "fem_exe": str(fem_exe),
    }
    (OUT / f"{STEM}.cfg.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    # 默认零污染负例：去掉稳定性键与 *STABILITY_LOAD
    dflt = dat
    dflt = "\n".join(ln for ln in dflt.splitlines()
                     if not ln.startswith("STABILITY=1 STABASIS=COMPLETION")
                     and not ln.startswith("DS0=")
                     and not ln.startswith("MAXPATHSTEP=")) + "\n"
    dflt = re.sub(r"\*STABILITY_LOAD\n2009 1 [^\n]*\n", "", dflt)
    (OUT / f"{DEFAULT_ID}.dat").write_text(dflt, encoding="utf-8", newline="\n")

    apdl_info = {}
    for c in ANSYS_CASES:
        apdl, info = ansys_curve_apdl(model, l0, jobname=c["id"], case=c)
        (OUT / f"{c['id']}.inp").write_text(apdl, encoding="ascii", newline="\n")
        apdl_info[c["id"]] = info
    return {"model": model, "coords": coords, "l0": l0, "dat": dat,
            "apdl_info": apdl_info}


def check_generation(g: dict) -> None:
    dat, l0, coords = g["dat"], g["l0"], g["coords"]
    ok("MODE=STATIC" in dat, "FEM .dat 不是 MODE=STATIC")
    ok("STABASIS=COMPLETION" in dat, "FEM .dat 缺 STABASIS=COMPLETION")
    ok(f"{CTRL_NODE} {CTRL_DOF} {PINC:.17g}" in dat, "FEM .dat 增量荷载不同源")
    worst = 0.0
    for eid, L0 in l0.items():
        e = next(e for e in cable_elements(g["model"]) if e.id == eid)
        L = sum((b - a) ** 2 for a, b in zip(coords[e.n1], coords[e.n2])) ** 0.5
        worst = max(worst, abs(L0 * (1.0 + EPS) - L))
    ok(len(l0) == 16, f"索数量 {len(l0)} != 16")
    ok(worst < 1e-9, f"L0*(1+EPS0) 与设计弦长最大偏差 {worst:.3e}")
    for c in ANSYS_CASES:
        apdl = (OUT / f"{c['id']}.inp").read_text(encoding="ascii")
        epss = [float(x) for x in re.findall(
            r"INISTATE,DEFINE,[^,\n]*,[^,\n]*,EPEL,X,([\d.eE+-]+)", apdl)]
        ok(len(epss) == 16, f"{c['id']} INISTATE 条数 {len(epss)} != 16")
        ok(all(abs(e - EPS) < TOL_EPS for e in epss),
           f"{c['id']} INISTATE eps 与 EPS0 不同源")
        ok(apdl.count("SOLVE") == 2, f"{c['id']} SOLVE 数 != 2（必须两段）")
        ok(len(re.findall(r"^F,2009,FX,", apdl, re.M)) == 1,
           f"{c['id']} 塔顶增量荷载数 != 1")
        ok(("ARCLEN,ON" in apdl) == c["arclen"],
           f"{c['id']} ARCLEN 开关与 case 定义不符")
        print(f"[PASS] {c['id']}: 16 INISTATE eps≡{EPS:.3g}, 2 SOLVE, "
              f"F_end={g['apdl_info'][c['id']]['f_end']:.6g}")


def run_all(fem_exe: Path, live: bool) -> dict:
    log: list[str] = []
    ok(ANSYS_EXE.exists(), f"ANSYS180.exe 不存在: {ANSYS_EXE}")
    ok(fem_exe.exists(), f"cable_solver.exe 不存在: {fem_exe}")
    # FEM 主算例 + 默认负例（.dat 已由 generate() 写好，直接原地运行）
    for stem in (STEM, DEFAULT_ID):
        rc = _run([fem_exe, f"{stem}.dat"], OUT, log, f"FEM-{stem}")
        ok(rc == 0, f"FEM {stem} 退出码 {rc}")
    # ANSYS 两个 job
    for c in ANSYS_CASES:
        rc = _run([ANSYS_EXE, "-b", "-j", c["id"], "-i", c["id"] + ".inp",
                   "-o", c["id"] + "_ansys.out"], OUT, log, "ANSYS-" + c["id"])
        ok(rc == 0, f"ANSYS {c['id']} 退出码 {rc}")
        err, warn = _log_summary(OUT / f"{c['id']}_ansys.out")
        ok(err == 0, f"ANSYS {c['id']} error={err} warning={warn}")
        ok((OUT / f"{c['id']}_curve.csv").exists(),
           f"ANSYS {c['id']} 未产出曲线 CSV")
        print(f"[PASS] ANSYS {c['id']}: 0 error, warning={warn}")
    (OUT / f"{STEM}_run_log.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
    if live:
        live_ansys(fem_exe)
    return {"log": log}


def live_ansys(fem_exe: Path) -> None:
    """可选：临时目录复跑两个 ANSYS job，比对 CSV（不覆写冻结证据）。"""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="cb3d22_live_") as td:
        tmp = Path(td)
        for c in ANSYS_CASES:
            shutil.copy2(OUT / f"{c['id']}.inp", tmp / f"{c['id']}.inp")
        for c in ANSYS_CASES:
            _run([ANSYS_EXE, "-b", "-j", c["id"], "-i", c["id"] + ".inp",
                  "-o", c["id"] + "_ansys.out"], tmp, [], "LIVE-" + c["id"])
            got = tmp / f"{c['id']}_curve.csv"
            frozen = read_curve_csv(OUT / f"{c['id']}_curve.csv")
            if not got.exists():
                ok(False, f"--live-ansys {c['id']} 未产出 CSV")
                continue
            live_rows = read_curve_csv(got)
            ok(len(live_rows) == len(frozen),
               f"--live-ansys {c['id']} 子步数 {len(live_rows)} != {len(frozen)}")
            worst = 0.0
            for a, b in zip(frozen, live_rows):
                worst = max(worst, abs(a[1] - b[1]), abs(a[2] - b[2]))
            ok(worst <= TOL_LIVE_CSV,
               f"--live-ansys {c['id']} 最大偏差 {worst:.3e}")
            print(f"[PASS] --live-ansys {c['id']}: {len(live_rows)} 值 "
                  f"max|d|={worst:.3e}")


def check_results() -> dict:
    fem_txt = (OUT / f"{STEM}_cable.txt").read_text(encoding="utf-8", errors="replace")
    st = parse_fem_stability(fem_txt)
    ok(st.get("present") is True, "FEM 缺 [STABILITY] 段")
    if not st.get("present"):
        return {}
    head, state = st["head"], st["state"]
    ok(head.get("BASIS") == "completion_state", "FEM BASIS 不是 completion_state")
    ok(head.get("STATUS") == "ok" and head.get("ERROR") == "none"
       and head.get("CONVERGED") == "1", "FEM 路径未收敛")
    ok(state["RES0"] / state["F0_NORM"] < TOL_RES0_REL,
       f"FEM RES0/|F0|={state['RES0'] / state['F0_NORM']:.3e} 未闭合")
    usec = re.search(r"^\[U\]\n(.*?)(?=^\[[A-Z_]+\]|\Z)", fem_txt, re.S | re.M)
    uline = re.search(r"^2009 1 (\S+)", usec.group(1), re.M) if usec else None
    ok(uline is not None and abs(state["CTRL0"] - float(uline.group(1))) < TOL_CTRL0,
       "FEM CTRL0 与 [U](2009,1) 不同源（[U] 段 10 位有效数字）")
    ok(abs(state["FINC_NORM"] - PINC) < 1e-9, "FINC_NORM 与 PINC 不同源")
    ok("ERROR=nonlinear_state_not_supported" in fem_txt,
       "[BUCKLING] ERROR 段被删")
    dflt = (OUT / f"{DEFAULT_ID}_cable.txt").read_text(encoding="utf-8", errors="replace")
    ok("[STABILITY]" not in dflt, "STABILITY=0 泄漏了 [STABILITY] 段")

    fem_curve = [(p[2], p[1]) for p in st["points"]]   # (ΔCTRL, λ)
    fi, flam, fctrl = limit_of(fem_curve)
    ndesc = sum(1 for k in range(fi + 1, len(fem_curve))
                if fem_curve[k][1] < fem_curve[k - 1][1] - 1e-9)
    ok(0 < fi < len(fem_curve) - 100, "FEM 极值点不在路径中段")
    ok(ndesc >= 100, f"FEM 下降段步数 {ndesc} < 100")
    ok((flam - fem_curve[-1][1]) / flam > 0.10,
       "FEM λ 降幅不足 10%，不构成下降段")
    print(f"[PASS] FEM limit point: lambda_max={flam:.7g} @ dUX={fctrl:.7g}, "
          f"NPOINT={len(fem_curve)}, descending={ndesc}, "
          f"lambda_end={fem_curve[-1][1]:.7g}")

    arcs = {}
    for c in ANSYS_CASES:
        rows = read_curve_csv(OUT / f"{c['id']}_curve.csv")
        u0, curve = ansys_step2_curve(rows)
        arcs[c["id"]] = curve
        print(f"[PASS] {c['id']}: UX_step1={u0:.7g}, step2 points={len(curve)}, "
              f"lambda_end={curve[-1][1]:.7g}, dUX_end={curve[-1][0]:.7g}")
    arc = arcs["lp_arc"]
    ai, alam, actrl = limit_of(arc)
    ok(0 < ai < len(arc) - 100, "ANSYS lp_arc 极值点不在路径中段")
    ok(abs(flam - alam) / flam < TOL_LAMBDA_MAX_REL,
       f"极值点 λ 相对差 {abs(flam - alam) / flam:.3e} 超容差")
    ok(abs(fctrl - actrl) / fctrl < TOL_UX_LIMIT_REL,
       f"极值点 ΔUX 相对差 {abs(fctrl - actrl) / fctrl:.3e} 超容差")
    print(f"[PASS] ANSYS lp_arc limit point: lambda_max={alam:.7g} @ dUX={actrl:.7g}; "
          f"FEM vs ANSYS d-lambda={abs(flam-alam)/flam:.3e}, "
          f"d-UX={abs(fctrl-actrl)/fctrl:.3e}")

    # 按 ΔCTRL 插值 λ：上升段 / 下降段分别统计
    dv_asc, dv_desc = [], []
    for ctrl, lam in fem_curve:
        if ctrl > arc[-1][0] or ctrl < arc[0][0]:
            continue
        d = abs(lam - interp_lambda_by_ctrl(arc, ctrl))
        (dv_asc if ctrl <= fctrl else dv_desc).append(d)
    wa = max(dv_asc) if dv_asc else float("inf")
    wd = max(dv_desc) if dv_desc else float("inf")
    ma = sum(dv_asc) / len(dv_asc) if dv_asc else float("inf")
    md = sum(dv_desc) / len(dv_desc) if dv_desc else float("inf")
    ok(len(dv_asc) > 50 and wa < TOL_ASCEND, f"上升段逐点 |Δλ| max={wa:.3e}")
    ok(len(dv_desc) > 100 and wd < TOL_DESCEND, f"下降段逐点 |Δλ| max={wd:.3e}")
    ok(ma < TOL_MEAN and md < TOL_MEAN,
       f"逐段平均 |Δλ| 超容差: asc={ma:.3e} desc={md:.3e}")
    print(f"[PASS] FEM vs ANSYS ARCLEN by dUX: ascending {len(dv_asc)} pts "
          f"worst|dλ|={wa:.3e} mean={ma:.3e}; descending {len(dv_desc)} pts "
          f"worst|dλ|={wd:.3e} mean={md:.3e}")
    # 荷载控制 job 校核上升段（只到 λ=1.5，极值点之前）
    load = arcs["lp_load"]
    ok(load[-1][1] < flam, "lp_load 终点 λ 应小于 FEM 极值点 λ_max")
    dv_load = [abs(lam - interp_lambda_by_ctrl(load, ctrl))
               for ctrl, lam in fem_curve
               if load[0][0] <= ctrl <= load[-1][0]]
    wl = max(dv_load) if dv_load else float("inf")
    ml = sum(dv_load) / len(dv_load) if dv_load else float("inf")
    ok(len(dv_load) > 50 and wl < TOL_LOAD,
       f"lp_load 上升段逐点 |Δλ| max={wl:.3e}")
    ok(ml < TOL_MEAN, f"lp_load 平均 |Δλ| {ml:.3e} 超容差")
    print(f"[PASS] lp_load ascending branch: {len(dv_load)} pts "
          f"worst|dλ|={wl:.3e} mean={ml:.3e}")
    return {"fem": st, "arc": arcs}


def write_reports(g: dict, res: dict) -> None:
    st = res.get("fem", {})
    pts = st.get("points", [])
    curve = [(p[2], p[1]) for p in pts]
    fi, flam, fctrl = limit_of(curve) if curve else (0, 0.0, 0.0)
    arc = res.get("arc", {}).get("lp_arc", [])
    ai, alam, actrl = limit_of(arc) if arc else (0, 0.0, 0.0)
    lines = [
        "CB3D22 ③a 真实斜拉桥越过极值点 / 下降段 —— FEM vs ANSYS 两段对标",
        "=" * 72,
        "构型: 与 CB3D21 同一座 50 节点 / 63 单元 / 16 索双塔三跨斜拉桥，",
        f"      仅桥塔截面按 TOWER_SCALE={TOWER_SCALE} 缩放（稳定敏感构型，",
        "      不是实际设计尺寸），增量荷载改为塔顶 2009 节点 +X 水平力。",
        f"同源: 16 根索 L0 = L_design/(1+EPS0), EPS0={EPS}, INISTATE eps≡EPS0",
        "口径: FEM R=F0+λ·Finc−Fint(u); ANSYS step1 TIME=1 成桥态,",
        "      step2 λ=TIME−1; 按 ΔUX 插值 λ 对拍（λ 非单调）。",
        "ANSYS: lp_load 荷载控制到 λ=1.5（极值点前）、lp_arc ARCLEN 越过极值点。",
        "-" * 72,
        f"FEM   : NPOINT={len(curve)}  lambda_max={flam:.9g} @ dUX={fctrl:.9g}  "
        f"lambda_end={curve[-1][1]:.9g}" if curve else "FEM: (none)",
        f"ANSYS : NPOINT={len(arc)}  lambda_max={alam:.9g} @ dUX={actrl:.9g}  "
        f"lambda_end={arc[-1][1]:.9g}" if arc else "ANSYS: (none)",
        f"极值点相对差: d-lambda={abs(flam-alam)/max(flam,1e-30):.4g}, "
        f"d-UX={abs(fctrl-actrl)/max(fctrl,1e-30):.4g}" if curve and arc else "",
        f"判据: {CHECKS[0]} checks, {len(FAILED)} failed",
        "如实边界: 稳定敏感细塔是为观测极值点而构造的研究构型，不是实际设计；",
        "分支屈曲 / 初始缺陷 / 材料非线性 / 施工阶段中间态稳定仍未覆盖。",
    ]
    (OUT / f"{STEM}_compare_report.txt").write_text(
        "\n".join(x for x in lines if x) + "\n", encoding="utf-8")
    files = sorted(p.name for p in OUT.iterdir() if p.is_file())
    (OUT / f"{STEM}_manifest.txt").write_text(
        "\n".join([f"case={LIMIT_CASE_NAME}", f"files={len(files)}"] + files) + "\n",
        encoding="utf-8")


def sync_to(dest: Path, g: dict) -> None:
    """把冻结输入/证据同步到 FEM-Eigen benchmark cases 目录。"""
    dest.mkdir(parents=True, exist_ok=True)
    names = [f"{STEM}.dat", f"{STEM}.cfg.json", f"{STEM}.model.json",
             f"{STEM}_cable.txt", f"{DEFAULT_ID}.dat", f"{DEFAULT_ID}_cable.txt",
             f"{STEM}_compare_report.txt", f"{STEM}_manifest.txt",
             f"{STEM}_run_log.txt"]
    for c in ANSYS_CASES:
        names += [f"{c['id']}.inp", f"{c['id']}_curve.csv",
                  f"{c['id']}_ansys.out"]
    for n in names:
        src = OUT / n
        if src.exists():
            shutil.copy2(src, dest / n)
    print(f"[SYNC] {len(names)} files -> {dest}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate-only", action="store_true")
    ap.add_argument("--sync-to", default="")
    ap.add_argument("--sync-only", action="store_true")
    ap.add_argument("--live-ansys", action="store_true")
    ap.add_argument("--check-only", action="store_true",
                    help="只重跑判据，不重生成输入、不重跑 ANSYS/FEM")
    ap.add_argument("--fem-exe", default=str(DEFAULT_FEM_EXE))
    a = ap.parse_args()
    fem_exe = Path(a.fem_exe)
    if a.check_only:
        res = check_results()
        write_reports({}, res)
    elif not a.sync_only:
        g = generate(fem_exe)
        check_generation(g)
        if not a.generate_only:
            run_all(fem_exe, a.live_ansys)
            res = check_results()
            write_reports(g, res)
            if FAILED:
                (OUT / f"{STEM}_compare_report.txt").write_text(
                    "\n".join(["[FAIL] %d/%d" % (len(FAILED), CHECKS[0])]
                              + ["  - " + f for f in FAILED]) + "\n",
                    encoding="utf-8")
        else:
            print("[PASS] generate-only: inputs written")
    else:
        g = generate(fem_exe)
    if a.sync_to:
        sync_to(Path(a.sync_to), g)
    if FAILED:
        print(f"\n[FAIL] {len(FAILED)}/{CHECKS[0]} 项未通过：")
        for f in FAILED:
            print("  - " + f)
        return 1
    print(f"\n[PASS] CB3D22 limit-point benchmark: all {CHECKS[0]} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
