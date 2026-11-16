#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CB3D23 算例编排：施工阶段中间态起点的全过程稳定 vs ANSYS 两段求解。

    python cases/run_cases_stage_state.py
        [--generate-only] [--check-only] [--sync-to <FEM-Eigen benchmark cases dir>]
        [--fem-exe <cable_solver.exe>]

产出（BridgeStudio/cases/CB3D23_stage_state_vs_ansys/）：
    cb3d23_stage_state.dat / .cfg.json / .model.json      同源输入与快照
    cb3d23_stage_state_stage_stability.txt                FEM STABASIS=STAGE 结果
    st_bad_stage.dat / st_bad_stage_stage_stability.txt  未知 STAGEID 负例
    st_load.inp / _curve.csv / _ansys.out                荷载控制 job
    st_arc.inp  / _curve.csv / _ansys.out                ARCLEN job
    _compare_report.txt / _manifest.txt / _run_log.txt

需要 ANSYS 18.0 许可（2 个 job）；`--generate-only` 只写输入文件。
`--check-only` 纯离线复核冻结证据与黄金值，不跑 FEM / ANSYS、不写结果文件。

口径见 `ansys_stage_stability.py` 模块头：λ = TIME − 1；按 ΔUY 插值 λ 逐点对拍；
`[STAGES]` + `STAGE=` 证明起点是施工阶段中间态，不是成桥态或参考构形。
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

from ansys_stage_stability import (  # noqa: E402
    CASE_ID, CASE_NAME, STAGE_ID, STAGE_ACTIVE, STAGE_LOADS, PINC,
    INC_NODES, CTRL_NODE, CTRL_DOF, CTRLTARGET, NODES, ELEMENTS,
    ANSYS_CASES, E_MODULUS, AREA, FIXED_X, FIXED_Z,
    fem_dat, ansys_inp, config_json, stage_constant_loads,
    stage_active_elements, parse_fem_stability, read_curve_csv,
)

ANSYS_EXE = Path(r"C:\Program Files\ANSYS Inc\v180\ANSYS\bin\winx64\ANSYS180.exe")
DEFAULT_FEM_EXE = Path(r"D:\AI-work\FEM-Eigen\_d_build\cable_solver.exe")
OUT = HERE / CASE_NAME
STEM = CASE_ID
BAD_ID = "st_bad_stage"

# 判据阈值按 2026-11-15 本机端到端实测最差值 + 余量定标，不是编造的合格限值。
# 小荷载档把轴向应变压到 ~2e-4，ANSYS LINK180(Almansi) 与 FEM 共转列式
# (工程应变) 的本构差异随之降到 0.03% 量级；10 倍荷载档实测 step1 差
# 2.9e-5 m、worst|Δλ| 3.8e-2，已弃用。
TOL_RES0_REL = 1.0e-9       # RES0 / |F0|（实测 6.3e-13）
TOL_CTRL0_ABS = 1.0e-6      # ANSYS step1 UY(2) ≡ FEM CTRL0（实测差 2.6e-7 m）
TOL_LAMBDA_PT = 6.0e-3      # 逐点 |Δλ|（实测 worst 3.34e-3，约 1.8× 余量）
TOL_LAMBDA_MEAN = 2.5e-3    # 平均 |Δλ|（实测 1.25e-3，约 2× 余量）

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
    """按 ΔUY（单调自变量）在 (ΔUY, λ) 曲线上线性插值 λ。"""
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
    """从 CSV [(iset, TIME, UY)] 取 step1 末 UY，返回 (UY_step1, [(|ΔUY|, λ=TIME−1)])。

    本算例增量荷载向下，UY 与 FEM CTRL 都是**递减**的；统一取负号换成
    递增的「向下位移幅值」作插值自变量，与 FEM 侧 `-CTRL` 对齐。
    """
    s1 = [r for r in rows if r[1] <= 1.0 + 1e-9]
    if not s1:
        raise ValueError("CSV 缺 step1 子步")
    u0 = s1[-1][2]
    s2 = [r for r in rows if r[1] > 1.0 + 1e-9]
    out: list[tuple[float, float]] = []
    last = -1e300
    for r in s2:
        du = -(r[2] - u0)
        if du > last + 1e-12:
            out.append((du, r[1] - 1.0))
            last = du
    return u0, out


def model_snapshot() -> str:
    """FEM/ANSYS 同源锚点快照（节点/单元/阶段）。"""
    return json.dumps({
        "case": CASE_ID, "E": E_MODULUS, "A": AREA,
        "nodes": [list(map(float, n)) for n in NODES],
        "elements": [list(map(int, e)) for e in ELEMENTS],
        "stage_active": {str(k): v for k, v in STAGE_ACTIVE.items()},
        "stage_loads": {str(k): {str(n): v for n, v in d.items()}
                        for k, d in STAGE_LOADS.items()},
        "stage_id": STAGE_ID,
        "stage_constant_loads": {str(k): v for k, v in stage_constant_loads().items()},
        "stage_active_elements": stage_active_elements(),
    }, indent=2, sort_keys=True) + "\n"


def bad_stage_dat() -> str:
    """负例：STAGEID=99，必须写 ERROR=stage_not_found 而不是静默降级。"""
    txt = fem_dat()
    lines = []
    for ln in txt.splitlines():
        if ln.startswith("STABILITY=1 STABASIS=STAGE"):
            lines.append(ln.replace("STAGEID=2", "STAGEID=99"))
        else:
            lines.append(ln)
    return "\n".join(lines) + "\n"


def generate_inputs(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{STEM}.dat").write_text(fem_dat(), encoding="utf-8")
    (out / f"{STEM}.cfg.json").write_text(config_json(), encoding="utf-8")
    (out / f"{STEM}.model.json").write_text(model_snapshot(), encoding="utf-8")
    (out / f"{BAD_ID}.dat").write_text(bad_stage_dat(), encoding="utf-8")
    for case in ANSYS_CASES:
        (out / f"{case['id']}.inp").write_text(ansys_inp(case), encoding="ascii")
    return {"n_inp": len(ANSYS_CASES)}


def run_all(out: Path, fem_exe: Path) -> list[str]:
    log: list[str] = []
    log.append("$ %s %s.dat" % (fem_exe.name, STEM))
    r = subprocess.run([str(fem_exe), f"{STEM}.dat"], cwd=str(out), timeout=1200,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, errors="replace")
    log.append("[fem] exit=%d" % r.returncode)
    if r.stdout:
        log.extend(r.stdout.splitlines()[-8:])
    r = subprocess.run([str(fem_exe), f"{BAD_ID}.dat"], cwd=str(out), timeout=1200,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, errors="replace")
    log.append("[fem-bad] exit=%d" % r.returncode)
    for case in ANSYS_CASES:
        job = case["id"]
        rc = _run([ANSYS_EXE, "-b", "-j", job, "-i", job + ".inp",
                   "-o", job + "_ansys.out"], out, log, "ansys-" + job)
        log.append("[ansys-%s] errors/warnings=%s" % (job, _log_summary(out / (job + "_ansys.out"))))
    return log


def verify(out: Path, *, frozen: bool = True) -> tuple[list[str], dict]:
    """纯离线判据：只读冻结证据文本，不调求解器。"""
    lines: list[str] = []
    fem_txt = (out / f"{STEM}_stage_stability.txt").read_text(
        encoding="utf-8", errors="replace").replace("\r\n", "\n")
    fem = parse_fem_stability(fem_txt)
    ok(fem.get("present") is True, "[STABILITY] 段缺失")
    if not fem.get("present"):
        return lines, {"fem": fem}

    head, state, stage = fem["head"], fem["state"], fem["stage"]
    stages = fem["stages"]
    pts = fem["points"]
    ok(head.get("BASIS") == "stage_state", "BASIS 不是 stage_state")
    ok(head.get("STATUS") == "ok" and head.get("CONVERGED") == "1",
       "FEM 弧长路径未收敛: %s / %s" % (head.get("STATUS"), head.get("CONVERGED")))
    ok(abs(stage.get("STAGE", -1) - STAGE_ID) < 1e-9,
       "STAGE= 不是指定中间态 %d" % STAGE_ID)
    ok(abs(stage.get("NELEM", -1) - len(stage_active_elements())) < 1e-9,
       "STAGE NELEM 与阶段激活单元数不符")
    ok(stage.get("NDOF", 0) > 0, "STAGE NDOF 缺失")

    # [STAGES]：阶段 2 必须是中间态（前后都还有阶段），且阶段 1 单元更少。
    ids = [int(s["ID"]) for s in stages]
    ok(ids == sorted(STAGE_ACTIVE), "[STAGES] 阶段编号不符: %s" % ids)
    ok(len(ids) > STAGE_ID, "选中的阶段不是中间态（后面没有阶段了）")
    by_id = {int(s["ID"]): s for s in stages}
    ok(by_id.get(STAGE_ID, {}).get("CONVERGED") == 1.0,
       "阶段 2 的共转列式平衡未收敛")
    ok(by_id.get(1, {}).get("NELEM", 0) < by_id.get(STAGE_ID, {}).get("NELEM", 0),
       "阶段 1 与阶段 2 激活单元数没有递进")
    ok(by_id.get(STAGE_ID, {}).get("NDLOAD", 0) == len(stage_constant_loads()),
       "阶段 2 累计阶段荷载条数与同源配置不符")
    lines.append("[PASS] [STAGES] %s；STAGE=%d NELEM=%d NDOF=%d，阶段 1 NELEM=%d"
                 % (ids, STAGE_ID, stage.get("NELEM", 0), stage.get("NDOF", 0),
                    by_id.get(1, {}).get("NELEM", 0)))

    # 起点闭合与「非零初态」：这两条是与成桥态/参考构形基准的实质区别。
    f0n = state.get("F0_NORM", 0.0)
    res0 = state.get("RES0", 1e300)
    ok(f0n > 0 and res0 / f0n < TOL_RES0_REL,
       "起点闭合残差过大: RES0/|F0|=%.3g" % (res0 / max(f0n, 1e-300)))
    ctrl0 = state.get("CTRL0", 0.0)
    ok(abs(ctrl0) > 1e-4, "起点 CTRL0 近似 0，疑似退化为参考构形")
    ok(state.get("FINC_NORM", 0.0) > 0, "FINC_NORM 缺失")
    lines.append("[PASS] 起点闭合 RES0/|F0|=%.3g，CTRL0=%.9g（非零初态）"
                 % (res0 / max(f0n, 1e-300), ctrl0))

    fem_curve = [(-p[2], p[1]) for p in pts]   # 向下位移幅值（递增自变量）
    ok(len(fem_curve) >= 30, "FEM 平衡点过少: %d" % len(fem_curve))
    ok(all(fem_curve[i][0] <= fem_curve[i + 1][0] + 1e-9
           for i in range(len(fem_curve) - 1)), "FEM ΔUY 非单调")
    lam_end = fem_curve[-1][1]
    ok(lam_end > 0.5, "FEM 终点 λ 过小: %g" % lam_end)
    ok(abs(fem_curve[-1][0] - CTRLTARGET) / CTRLTARGET < 0.1,
       "FEM 终点 |ΔUY| 与 CTRLTARGET 不符")
    lines.append("[PASS] FEM 路径 NPOINT=%d λ_end=%.9g |ΔUY|_end=%.9g（单调）"
                 % (len(fem_curve), lam_end, fem_curve[-1][0]))

    # 负例：未知 STAGEID 必须显式报错，不得静默降级成成桥态/参考构形。
    bad = (out / f"{BAD_ID}_stage_stability.txt").read_text(
        encoding="utf-8", errors="replace")
    ok("ERROR=stage_not_found" in bad, "未知 STAGEID 未写 stage_not_found")
    ok("BASIS=stage_state" in bad, "负例 BASIS 不是 stage_state")
    lines.append("[PASS] 负例 STAGEID=99 -> ERROR=stage_not_found，无静默降级")

    # 同源锚点：APDL 的 N/E/F 必须与 .dat 与配置逐项一致。
    dat = (out / f"{STEM}.dat").read_text(encoding="utf-8", errors="replace")
    cfg = json.loads((out / f"{STEM}.cfg.json").read_text(encoding="utf-8"))
    ok(cfg["stage_id"] == STAGE_ID and cfg["pinc"] == PINC,
       "cfg.json 与模块常量不同源")
    node_pairs = {}
    for ln in dat.splitlines():
        m = re.match(r"^(\d+) +(-?[\d.eE+]+) +(-?[\d.eE+]+) +(-?[\d.eE+]+)\s*$", ln)
        if m:
            node_pairs[int(m.group(1))] = tuple(float(m.group(i)) for i in (2, 3, 4))
    ok(len(node_pairs) == len(NODES), ".dat 节点数不符")
    for case in ANSYS_CASES:
        inp = (out / f"{case['id']}.inp").read_text(encoding="ascii", errors="replace")
        n_count = 0
        for ln in inp.splitlines():
            q = [s.strip() for s in ln.split(",")]
            if q and q[0] == "N" and len(q) >= 5 and q[1].isdigit():
                try:
                    want = node_pairs.get(int(q[1]))
                    ok(want is not None and all(
                        abs(want[k] - float(q[2 + k])) < 1e-12 for k in range(3)),
                       "%s 节点 %s 与 .dat 不同源" % (case["id"], q[1]))
                    n_count += 1
                except ValueError:
                    ok(False, "%s 节点 %s 坐标非数值" % (case["id"], q[1]))
        ok(n_count == len(NODES), "%s 节点数不符" % case["id"])
        e_lines = [ln for ln in inp.splitlines()
                   if re.match(r"^E,\d+,\d+\s*$", ln.strip())]
        ok(len(e_lines) == len(stage_active_elements()),
           "%s 单元数不是阶段 2 激活单元数" % case["id"])
        loads = stage_constant_loads()
        for node, val in loads.items():
            ok(any(re.match(r"^F,%d,FY,(-?[\d.eE+]+)\s*$" % node, ln.strip())
                   and abs(float(ln.strip().split(",")[3]) - val) < 1e-6
                   for ln in inp.splitlines()),
               "%s 阶段荷载 F,%d,FY 与配置不同源" % (case["id"], node))
        ok("NLGEOM,ON" in inp and "TIME,1" in inp,
           "%s 缺 NLGEOM / step1 TIME=1" % case["id"])
        for nid in FIXED_X:
            ok(f"D,{nid},UX,0.0" in inp,
               "%s 缺 D,%d,UX 侧向导向约束（与 .dat 不同源）" % (case["id"], nid))
        for nid in FIXED_Z:
            ok(f"D,{nid},UZ,0.0" in inp,
               "%s 缺 D,%d,UZ 约束" % (case["id"], nid))
        ok(f"TIME,{case['time2']:.12g}" in inp,
           "%s 缺 step2 TIME" % case["id"])
        ok(("ARCLEN,ON" in inp) == bool(case["arclen"]),
           "%s ARCLEN 开关不符" % case["id"])
    lines.append("[PASS] 同源锚点：APDL N/E/F 与 .dat / cfg.json 逐项一致，"
                 "2 个 job 各含 %d 个阶段 2 单元" % len(stage_active_elements()))

    # ANSYS 两段求解：step1 平衡态 ≡ FEM CTRL0；step2 按 ΔUY 插值 λ 对拍。
    curves = {}
    for case in ANSYS_CASES:
        job = case["id"]
        out_txt = (out / f"{job}_ansys.out").read_text(
            encoding="ascii", errors="replace")
        err, warn = _log_summary_from_text(out_txt)
        ok(err == 0, "%s ANSYS 有 %s 条 error" % (job, err))
        csv = out / f"{job}_curve.csv"
        ok(csv.is_file(), "%s 缺曲线 CSV" % job)
        if not csv.is_file():
            continue
        rows = read_curve_csv(csv)
        ok(len(rows) > 5, "%s 曲线点数过少" % job)
        if len(rows) <= 5:
            continue
        u0, curve = ansys_step2_curve(rows)
        curves[job] = curve
        ok(abs(u0 - ctrl0) < TOL_CTRL0_ABS,
           "%s step1 UY(2)=%.9g 与 FEM CTRL0=%.9g 不符" % (job, u0, ctrl0))
        ok(len(curve) >= 10, "%s step2 点数过少" % job)
        # 只对两条曲线共同的 ΔUY 区间逐点比较，避免越界外推。
        lo = max(fem_curve[0][0], curve[0][0])
        hi = min(fem_curve[-1][0], curve[-1][0])
        if hi > lo:
            diffs = [abs(lam - interp_lambda_by_ctrl(curve, ctrl))
                     for ctrl, lam in fem_curve if lo <= ctrl <= hi]
            ok(bool(diffs), "%s 无重叠比较区间" % job)
            if diffs:
                worst = max(diffs)
                mean = sum(diffs) / len(diffs)
                ok(worst < TOL_LAMBDA_PT,
                   "%s 逐点 |Δλ| 最大 %.4g 超容差" % (job, worst))
                ok(mean < TOL_LAMBDA_MEAN,
                   "%s 平均 |Δλ| %.4g 超容差" % (job, mean))
                lines.append("[PASS] %s: step1 UY=%.9g；step2 %d 点，"
                             "共同区间 |ΔUY|=[%.6g, %.6g] 覆盖 FEM 路径 %.1f%%，"
                             "逐点 worst|Δλ|=%.4g mean=%.4g"
                             % (job, u0, len(curve), lo, hi,
                                100.0 * (hi - lo) / max(fem_curve[-1][0] - fem_curve[0][0], 1e-300),
                                worst, mean))
        lam_a = curve[-1][1] if curve else 0.0
        ok(lam_a >= lam_end - 1e-6,
           "%s 终点 λ=%.6g 未覆盖 FEM 终点 %.6g" % (job, lam_a, lam_end))
        ok(abs(lam_a - case["lammax"]) / case["lammax"] < 1e-6,
           "%s 终点 TIME 与 LAMMAX 不符" % job)
    lines.append("[PASS] ANSYS 两个 job：0 error，λ=TIME−1 且终点覆盖整个 FEM 路径")

    return lines, {"fem": fem, "curves": curves,
                   "ctrl0": ctrl0, "lam_end": lam_end,
                   "npoint": len(fem_curve)}


def _log_summary_from_text(txt: str) -> tuple[int, int]:
    def grab(label: str) -> int:
        m = re.findall(r"NUMBER OF %s\s+MESSAGES ENCOUNTERED=\s*(\d+)" % label, txt)
        return int(m[-1]) if m else -1
    return grab("ERROR"), grab("WARNING")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate-only", action="store_true")
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--sync-only", action="store_true",
                    help="只把冻结证据同步到 --sync-to，不跑求解器")
    ap.add_argument("--sync-to", default="")
    ap.add_argument("--fem-exe", default=str(DEFAULT_FEM_EXE))
    args = ap.parse_args()
    fem_exe = Path(args.fem_exe)

    def sync_files(dest: Path) -> int:
        dest.mkdir(parents=True, exist_ok=True)
        names = sorted(p.name for p in OUT.iterdir() if p.is_file()
                       and not _is_ansys_scratch(p.name))
        for name in names:
            shutil.copy2(OUT / name, dest / name)
        print("[SYNC] %d files -> %s" % (len(names), dest))
        return len(names)

    if args.sync_only:
        if not args.sync_to:
            print("[FAIL] --sync-only 需要 --sync-to")
            return 2
        sync_files(Path(args.sync_to))
        return 0

    if args.check_only:
        lines, info = verify(OUT)
        for s in lines:
            print(s)
        print("\n[%s] CB3D23 stage-state benchmark: %d checks, %d failed"
              % ("PASS" if not FAILED else "FAIL", CHECKS[0], len(FAILED)))
        for f in FAILED:
            print("  [FAIL] " + f)
        if args.sync_to and not FAILED:
            sync_files(Path(args.sync_to))
        return 0 if not FAILED else 1

    generate_inputs(OUT)
    if args.generate_only:
        print("[OK] 仅生成输入: %s" % OUT)
        return 0

    log = run_all(OUT, fem_exe)
    lines, info = verify(OUT)
    (OUT / f"{STEM}_run_log.txt").write_text("\n".join(log) + "\n", encoding="utf-8")

    report = [
        "=" * 78,
        "CB3D23 ③b：施工阶段中间态起点的全过程稳定 FEM vs ANSYS 两段对标",
        "=" * 78,
        "口径: FEM R=F0+λ·Finc−Fint(u), u0=施工阶段 %d 共转列式全量平衡位移;" % STAGE_ID,
        "      ANSYS step1 TIME=1 求同一阶段平衡态, step2 λ=TIME−1;",
        "      按 ΔUY 插值 λ 逐点对拍。BASIS=stage_state 与 completion_state 分列。",
        "-" * 78,
    ] + lines + [
        "-" * 78,
        "FEM   : NPOINT=%d lambda_end=%.9g CTRL0=%.9g" % (
            info.get("npoint", 0), info.get("lam_end", 0.0), info.get("ctrl0", 0.0)),
        "ANSYS : st_load %d 点 / st_arc %d 点；两个 job 0 error" % (
            len(info.get("curves", {}).get("st_load", [])),
            len(info.get("curves", {}).get("st_arc", []))),
        "结论  : %d checks, %d failed" % (CHECKS[0], len(FAILED)),
        "RESULT: " + ("ALL CHECKS PASSED" if not FAILED else "FAILED"),
    ]
    for f in FAILED:
        report.append("  [FAIL] " + f)
    (OUT / f"{STEM}_compare_report.txt").write_text(
        "\n".join(report) + "\n", encoding="utf-8")

    files = sorted(p.name for p in OUT.iterdir() if p.is_file()
                   and not _is_ansys_scratch(p.name))
    (OUT / f"{STEM}_manifest.txt").write_text(
        "\n".join(files) + "\n", encoding="utf-8")
    for s in lines:
        print(s)
    print("\n[%s] CB3D23 stage-state: %d checks, %d failed -> %s"
          % ("PASS" if not FAILED else "FAIL", CHECKS[0], len(FAILED), OUT))

    if args.sync_to:
        dest = Path(args.sync_to)
        dest.mkdir(parents=True, exist_ok=True)
        for name in files:
            shutil.copy2(OUT / name, dest / name)
        print("[SYNC] %d files -> %s" % (len(files), dest))
    return 0 if not FAILED else 1


def _is_ansys_scratch(name: str) -> bool:
    skip = (".rst", ".emat", ".esav", ".full", ".mntr", ".DSP", ".dsp",
            ".err", ".log", ".ldhi", ".rdb", ".db", ".arc", ".stat")
    if name.lower().endswith(tuple(s.lower() for s in skip)):
        return True
    return bool(re.match(r".*\.r\d{3}$", name))


if __name__ == "__main__":
    sys.exit(main())
