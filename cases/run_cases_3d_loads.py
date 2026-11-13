#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E-stage case runner: 3D REAL distributed loads / selfweight.

    C:/Users/user/anaconda3/python.exe run_cases_3d_loads.py --case all

Each case goes through:
    ModelSpec3D -> *.dat (FEM-Eigen 3D) + *.inp (ANSYS BEAM4)
    -> example_3d_report.exe -> *.report.txt
    -> ANSYS180 -b          -> *_disp/_reac/_elem.csv
    -> compare_3d_loads()   -> compare_report.txt

Kept separate from run_cases_3d.py / run_cases_cable.py on purpose: those
chains are already frozen in the FEM-Eigen CTest benchmarks.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ansys_bridge_3d import parse_ansys_csv_3d, parse_fem_report_3d  # noqa: E402
from ansys_bridge_3d_loads import (build_box_beam_3d_loads,  # noqa: E402
                                   compare_3d_loads, spec3d_loads_to_apdl)

FEM_EIGEN = Path(r"D:\AI-work\FEM-Eigen")
FEM_EXE = FEM_EIGEN / "build" / "example_3d_report.exe"
ANSYS_EXE = Path(r"C:\Program Files\ANSYS Inc\v180\ANSYS\bin\winx64\ANSYS180.exe")
MSYS_BIN = r"C:\msys64\mingw64\bin"

CASES_LOADS = {
    "CB3D06": dict(
        folder="CB3D06_distributed_udl",
        stem="cb3d06_udl",
        title="CB3D06 three-span box beam 3D REAL *ELEMENT_LOAD q=20kN/m",
        desc=("三跨等截面连续箱梁 3D：3x30 m，30 个 BEAM 单元，"
              "真实 *ELEMENT_LOAD 均布荷载 20 kN/m（不再节点化）"),
        kwargs=dict(q_kN=20.0),
    ),
    "CB3D07": dict(
        folder="CB3D07_distributed_selfweight",
        stem="cb3d07_selfweight",
        title="CB3D07 3D distributed UDL + DENS selfweight + SELF_WEIGHT + point load",
        desc=("同 CB3D06 几何 + *ELEMENT_LOAD 20 kN/m + DENS=2500 自动自重 "
              + "(g=-9.81) + *SELF_WEIGHT ALL MASS 100 + 跨中节点力 -100 kN"),
        kwargs=dict(q_kN=20.0, dens=2500.0, gravity=(0.0, 0.0, -9.81),
                    self_weights=(dict(who="ALL", rule="MASS", value=100.0),),
                    extra_loads=((16, 3, -100000.0),)),
    ),
    "CB3D08": dict(
        folder="CB3D08_trapezoidal_loads",
        stem="cb3d08_trapezoid",
        title="CB3D08 3D trapezoidal *ELEMENT_LOAD q1=20 q2=40 kN/m",
        desc=("同几何 + 每单元梯形分布荷载 q1=20 / q2=40 kN/m，"
              "验证 3D 内核 q1/q2 不一致时的固端力系数"),
        kwargs=dict(q_kN=20.0, q2_kN=40.0),
    ),
}


def _env() -> dict:
    e = os.environ.copy()
    e["PATH"] = MSYS_BIN + os.pathsep + e.get("PATH", "")
    return e


def _run(cmd: list[str], cwd: Path, log: list[str], tag: str) -> int:
    log.append("$ " + " ".join(str(c) for c in cmd))
    try:
        r = subprocess.run([str(c) for c in cmd], cwd=str(cwd), env=_env(),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600)
    except Exception as exc:                                  # noqa: BLE001
        log.append("  [{}] exception: {}".format(tag, exc))
        return -1
    log.append("  [{}] rc={}".format(tag, r.returncode))
    if r.stdout.strip():
        log.append("  stdout: " + r.stdout.strip().splitlines()[-1][:200])
    if r.returncode != 0 and r.stderr.strip():
        log.append("  stderr: " + r.stderr.strip()[-400:])
    return r.returncode


def _manifest(cfg: dict, stem: str) -> str:
    return "\n".join([
        "=" * 78,
        cfg["title"],
        cfg["desc"],
        "=" * 78,
        "唯一数据源: ModelSpec3D（ansys_bridge_3d_loads.build_box_beam_3d_loads）",
        "  kwargs = {!r}".format(cfg["kwargs"]),
        "",
        "链路:",
        "  BridgeSpec3D -> to_dat()               " + stem + ".dat",
        "               -> spec3d_loads_to_apdl() " + stem + ".inp  (ANSYS BEAM4)",
        "  FEM-Eigen example_3d_report.exe        " + stem + ".report.txt",
        "  ANSYS180 -b                           " + stem + "_disp/_reac/_elem.csv",
        "",
        "文件清单:",
        "  {:<30s} FEM-Eigen 前处理输出（含 *ELEMENT_LOAD / *GRAVITY）".format(stem + ".dat"),
        "  {:<30s} 模型快照".format(stem + ".model.json"),
        "  {:<30s} ANSYS BEAM4 命令流（SFBEAM / MP,DENS / ACEL）".format(stem + ".inp"),
        "  {:<30s} FEM-Eigen 3D 位移 + i 端内力".format(stem + ".report.txt"),
        "  {:<30s} ANSYS 节点位移 UX..ROTZ".format(stem + "_disp.csv"),
        "  {:<30s} ANSYS 节点反力 FX..MZ".format(stem + "_reac.csv"),
        "  {:<30s} ANSYS 单元 i/j 端力".format(stem + "_elem.csv"),
        "  {:<30s} ANSYS 作业日志（校核证据）".format(stem + "_ansys.out"),
        "  {:<30s} 三方对比报告".format("compare_report.txt"),
        "  {:<30s} 运行命令与退出码".format("run_log.txt"),
        "",
        "同源约定:",
        "  *ELEMENT_LOAD 1-30 GZ -q1 -q2  <->  SFBEAM,ELEM,1,PRES,+q1,+q2",
        "      （BEAM4 LKEY=1 实测对应全局 -Z；PRES 为正面号向下）",
        "  *GRAVITY 0 0 -9.81 + DENS      <->  MP,DENS + ACEL,0,0,9.81",
        "      （ACEL 是惯性加速度，与重力向量反号）",
        "  *SELF_WEIGHT ALL MASS m        <->  并入同一条 SFBEAM 的 PRES 幅值",
        "      （ANSYS 同面重复 SFBEAM 会覆盖，故先求和再写一条）",
        "  SMISC 1/2/3/4/5/6 = i 端 N/Vy/Vz/T/My/Mz；7..12 = j 端",
        "  *CFOPEN + *VWRITE 一次写整列，外层不可再套 *DO",
        "",
        "边界:",
        "  3D 内核 g 默认 0（与 2D 的 gy=-9.81 不同），必须显式写 *GRAVITY；",
        "  分布荷载/自重与 *STAGE 不能混用（readModel3D 会显式拒绝，不静默）；",
        "  APDL 对标只支持 GZ 方向与纯 -Z 重力，其它方向由闭式解套件验证。",
        "",
        "复现:",
        "  C:\\Users\\user\\anaconda3\\python.exe "
        "D:\\AI-work\\BridgeStudio\\cases\\run_cases_3d_loads.py --case all",
        "=" * 78,
        "",
    ])


def run_case(key: str, generate_only: bool = False) -> bool:
    cfg = CASES_LOADS[key]
    case_dir = HERE / cfg["folder"]
    case_dir.mkdir(parents=True, exist_ok=True)
    stem = cfg["stem"]

    try:
        model = build_box_beam_3d_loads(**cfg["kwargs"])
    except Exception as exc:                                   # noqa: BLE001
        print("[FAIL] {}: 建模失败: {}".format(key, exc))
        return False

    dat = case_dir / (stem + ".dat")
    inp = case_dir / (stem + ".inp")
    dat.write_text(model.to_dat(compact=False), encoding="utf-8")
    (case_dir / (stem + ".model.json")).write_text(
        model.model_dump_json(indent=2), encoding="utf-8")
    try:
        inp.write_text(spec3d_loads_to_apdl(model, jobname=stem, title=cfg["title"]),
                       encoding="ascii")
    except Exception as exc:                                   # noqa: BLE001
        print("[FAIL] {}: APDL 生成失败: {}".format(key, exc))
        return False
    (case_dir / "manifest.txt").write_text(_manifest(cfg, stem), encoding="utf-8")
    print("[{}] 前处理输出已生成: {} / {}".format(key, dat.name, inp.name))
    if generate_only:
        return True

    log: list[str] = []
    for p in case_dir.glob(stem + "_*.csv"):
        p.unlink()
    rpt = case_dir / (stem + ".report.txt")
    if rpt.exists():
        rpt.unlink()

    rc_fem = _run([FEM_EXE, dat.name], case_dir, log, "FEM-Eigen-3D")
    rc_ansys = _run([ANSYS_EXE, "-b", "-j", stem, "-i", inp.name,
                     "-o", stem + "_ansys.out"], case_dir, log, "ANSYS-BEAM4")
    (case_dir / "run_log.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
    if rc_fem != 0 or rc_ansys != 0:
        print("[FAIL] {} 求解失败 rc_fem={} rc_ansys={}".format(key, rc_fem, rc_ansys))
        return False

    needed = [stem + s for s in (".report.txt", "_disp.csv", "_reac.csv", "_elem.csv")]
    missing = [n for n in needed if not (case_dir / n).exists()]
    if missing:
        print("[FAIL] {} 缺少结果文件: {}".format(key, missing))
        return False

    fem = parse_fem_report_3d(case_dir / (stem + ".report.txt"))
    adisp = parse_ansys_csv_3d(case_dir / (stem + "_disp.csv"))
    areac = parse_ansys_csv_3d(case_dir / (stem + "_reac.csv"))
    aelem = parse_ansys_csv_3d(case_dir / (stem + "_elem.csv"))
    report, ok = compare_3d_loads(model, fem, adisp, areac, aelem, stem=stem)
    (case_dir / "compare_report.txt").write_text(report, encoding="utf-8")
    print("[{}] {}".format(key, "PASS" if ok else "FAIL"))
    return bool(ok)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="all", choices=["all", *CASES_LOADS])
    ap.add_argument("--generate-only", action="store_true")
    a = ap.parse_args()
    keys = list(CASES_LOADS) if a.case == "all" else [a.case]
    ok = True
    for k in keys:
        ok &= run_case(k, a.generate_only)
    print("=" * 60)
    print("ALL CASES PASS" if ok else "SOME CASES FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
