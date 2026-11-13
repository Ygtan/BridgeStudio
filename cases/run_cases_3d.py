#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""3D 连续箱梁算例编排：同源导出 -> 双求解器 -> 三方对照 -> 归档。

与 2D 的 ``cases/run_cases.py`` 平行、完全独立（X20_接入约定 §3.4 两链正交）。

每个算例产出：
    <stem>.dat            FEM-Eigen 3D 前处理输出（ModelSpec3D.to_dat）
    <stem>.model.json     模型快照
    <stem>.inp            ANSYS BEAM188 APDL（SECTYPE/SECDATA）
    <stem>_b4.inp         ANSYS BEAM4 APDL（R/RMORE，Euler-Bernoulli 严格基准）
    <stem>.report.txt     FEM-Eigen 3D example_3d_report.exe 结果
    <stem>_disp/_reac/_elem.csv          BEAM188 机器可读结果
    <stem>_b4_disp/_b4_reac/_b4_elem.csv BEAM4 机器可读结果
    <stem>_ansys.out / <stem>_b4_ansys.out   ANSYS 作业日志
    compare_report.txt / manifest.txt / run_log.txt

用法：
    C:\\Users\\user\\anaconda3\\python.exe cases\\run_cases_3d.py --case all
    ... --case all --generate-only
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRIDGESTUDIO = HERE.parent
sys.path.insert(0, str(BRIDGESTUDIO))

from ansys_bridge_3d import (  # noqa: E402
    build_box_beam_3d, spec3d_to_apdl, parse_fem_report_3d,
    parse_ansys_csv_3d, compare_3d,
)

FEM_EIGEN = Path(r"D:\AI-work\FEM-Eigen")
FEM_EXE_3D = FEM_EIGEN / "build" / "example_3d_report.exe"
ANSYS_EXE = Path(r"C:\Program Files\ANSYS Inc\v180\ANSYS\bin\winx64\ANSYS180.exe")
MSYS_BIN = r"C:\msys64\mingw64\bin"
CASES_DIR = HERE

CASES_3D = {
    "CB3D01": dict(
        folder="CB3D01_three_span_box_beam_3d",
        stem="cb3d01_3span",
        title="CB3D01 three-span box beam 3D 3x30m nodalized UDL",
        desc="三跨连续箱梁 3D 单梁线：3x30 m，箱形截面 8.0x2.0 m / "
             "tw=0.02 tf=0.025，30 个 3D BEAM 单元，均布荷载 20 kN/m 节点化",
        kwargs=dict(spans=(30.0, 30.0, 30.0), n_elem_per_span=10,
                    B=8.0, h=2.0, tw=0.02, tf=0.025,
                    E=2.06e11, nu=0.2, udl_kN=20.0,
                    title="三跨连续箱梁 3D 单梁线 均布荷载"),
    ),
    "CB3D02": dict(
        folder="CB3D02_space_effects",
        stem="cb3d02_space",
        title="CB3D02 three-span box beam 3D lateral force + torsion",
        desc="同 CB3D01 几何，(UDL 10 kN/m 节点化) + 中跨跨中 node16 "
             "横向力 FY=-100 kN + 扭矩 MX=+50 kN·m，检验 UY/ROTX/Vy/Mz/T",
        kwargs=dict(spans=(30.0, 30.0, 30.0), n_elem_per_span=10,
                    B=8.0, h=2.0, tw=0.02, tf=0.025,
                    E=2.06e11, nu=0.2, udl_kN=10.0,
                    extra_loads=((16, 2, -100000.0), (16, 4, 50000.0)),
                    title="三跨连续箱梁 3D 空间效应（横向力 + 扭矩）"),
    ),
}


def _env() -> dict:
    env = os.environ.copy()
    env["PATH"] = MSYS_BIN + os.pathsep + env.get("PATH", "")
    return env


def _run(cmd: list[str], cwd: Path, log: list[str], tag: str) -> int:
    log.append("$ " + " ".join(str(c) for c in cmd))
    try:
        p = subprocess.run([str(c) for c in cmd], cwd=str(cwd), env=_env(),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600)
        out = (p.stdout or "") + (p.stderr or "")
        log.append("[{}] exit={}".format(tag, p.returncode))
        if out.strip():
            log.extend("    " + s for s in out.strip().splitlines()[-12:])
        return p.returncode
    except Exception as exc:                                # noqa: BLE001
        log.append("[{}] EXCEPTION {}".format(tag, exc))
        return -1


def _manifest(cfg: dict, stem: str) -> str:
    return "\n".join([
        "=" * 78,
        "3D 算例归档: " + cfg["title"],
        "=" * 78,
        "",
        "模型: " + cfg["desc"],
        "唯一数据源: ModelSpec3D (BridgeStudio/model_spec_3d.py)",
        "  build_box_beam_3d(**" + repr(cfg["kwargs"]) + ")",
        "",
        "链路:",
        "  ModelSpec3D.to_dat(compact=False)   -> " + stem + ".dat   (FEM-Eigen 3D)",
        "  spec3d_to_apdl(BEAM188)            -> " + stem + ".inp   (ANSYS 18.0)",
        "  spec3d_to_apdl(BEAM4)              -> " + stem + "_b4.inp (EB 严格基准)",
        "  example_3d_report.exe <stem>.dat   -> " + stem + ".report.txt",
        "  ANSYS180.exe -b -j <job> -i *.inp  -> *_disp/_reac/_elem.csv",
        "  compare_3d()                       -> compare_report.txt",
        "",
        "文件清单:",
        "  {:<34s} 前处理输出（FEM-Eigen 3D .dat）".format(stem + ".dat"),
        "  {:<34s} 模型快照（ModelSpec3D JSON）".format(stem + ".model.json"),
        "  {:<34s} ANSYS BEAM188 APDL（SECTYPE/SECDATA）".format(stem + ".inp"),
        "  {:<34s} ANSYS BEAM4 APDL（R/RMORE, 无剪切）".format(stem + "_b4.inp"),
        "  {:<34s} FEM-Eigen 3D 结果（6DOF 位移 + i 端 6 分量内力）".format(
            stem + ".report.txt"),
        "  {:<34s} BEAM188 节点位移/反力/单元端力".format(
            stem + "_disp/_reac/_elem.csv"),
        "  {:<34s} BEAM4 节点位移/反力/单元端力".format(
            stem + "_b4_disp/_b4_reac/_b4_elem.csv"),
        "  {:<34s} ANSYS 作业日志".format(stem + "_ansys.out / _b4_ansys.out"),
        "  {:<34s} 三方对比报告".format("compare_report.txt"),
        "  {:<34s} 本清单 / 命令与退出码".format("manifest.txt / run_log.txt"),
        "",
        "3D 关键约定（本机 v180 实测）:",
        "  X 纵向 / Y 横向 / Z 竖向；竖向荷载 = DOF3(UZ) = ANSYS FZ",
        "  竖向弯曲 IY <-> ANSYS IYY；横向 IZ <-> IZZ；扭转 J <-> Torsion Constant",
        "  BEAM188: ET,1,BEAM188 + SECTYPE,1,BEAM,ASEC + SECDATA,A,IYY,0,IZZ,0,J,...",
        "  BEAM4:   ET,1,BEAM4 + R,1,A,IZZ,IYY,TKZ,TKY,0 + RMORE,0,J,0,0,0,0",
        "           SHEARZ/SHEARY=0 -> Euler-Bernoulli，与 FEM-Eigen 3D 严格一致",
        "  BEAM188 SMISC 1/2/3/4/5/6 = Fx/My/Mz/TQ/SFz/SFy(I), 14..19 = J 端",
        "  BEAM4   SMISC 1..6 = N/Vy/Vz/T/My/Mz(I), 7..12 = J 端",
        "  *CFOPEN + *VWRITE 一次写出整个数组，外面不能再套 *DO",
        "",
        "已知内核差异（不判 FAIL，报告第 [4] 节量化）:",
        "  FEM-Eigen 3D / BEAM4 = 12x12 Euler-Bernoulli 三次梁；",
        "  BEAM188 = 一阶剪切变形 Timoshenko + 线性形函数，含剪切挠度 PL/(GA)。",
        "",
        "已知内核缺口（未改 C++ 源码）:",
        "  FEM-Eigen 3D 不认识 *ELEMENT_LOAD / *GRAVITY / *SELF_WEIGHT（静默忽略），",
        "  DENS 只进一致质量矩阵。故均布荷载节点化为 *CLOAD，ANSYS 用同一组 F 施加。",
        "",
        "复现:",
        "  C:\\Users\\user\\anaconda3\\python.exe "
        "D:\\AI-work\\BridgeStudio\\cases\\run_cases_3d.py --case all",
        "  (需 C:\\msys64\\mingw64\\bin 在 PATH；ANSYS 许可仅本校核脚本需要)",
        "=" * 78,
    ]) + "\n"


def run_case(key: str, generate_only: bool = False) -> bool:
    cfg = CASES_3D[key]
    case_dir = CASES_DIR / cfg["folder"]
    case_dir.mkdir(parents=True, exist_ok=True)
    stem = cfg["stem"]
    log: list[str] = ["CASE {}  {}".format(key, cfg["title"])]

    model = build_box_beam_3d(**cfg["kwargs"])
    dat = case_dir / (stem + ".dat")
    inp188 = case_dir / (stem + ".inp")
    inp4 = case_dir / (stem + "_b4.inp")
    dat.write_text(model.to_dat(compact=False), encoding="utf-8")
    (case_dir / (stem + ".model.json")).write_text(
        model.model_dump_json(indent=2), encoding="utf-8")
    inp188.write_text(spec3d_to_apdl(model, jobname=stem,
                                    element="BEAM188", title=cfg["title"]),
                      encoding="ascii")
    inp4.write_text(spec3d_to_apdl(model, jobname=stem + "_b4",
                                   element="BEAM4", title=cfg["title"]),
                    encoding="ascii")
    (case_dir / "manifest.txt").write_text(_manifest(cfg, stem), encoding="utf-8")
    print("[GEN ] {} .dat/.inp/_b4.inp/model.json OK ({} nodes, {} elems)".format(
        key, len(model.nodes), len(model.elements)))
    if generate_only:
        (case_dir / "run_log.txt").write_text(
            "\n".join(log + ["[generate-only]"]) + "\n", encoding="utf-8")
        return True

    # 清理旧结果，避免上次运行残留被误读
    for p in case_dir.glob(stem + "*_disp.csv"):
        p.unlink()
    for p in case_dir.glob(stem + "*_reac.csv"):
        p.unlink()
    for p in case_dir.glob(stem + "*_elem.csv"):
        p.unlink()

    rc_fem = _run([FEM_EXE_3D, dat.name], case_dir, log, "FEM-Eigen-3D")
    apdl_runs = [
        (inp188, stem, stem + "_ansys.out"),
        (inp4, stem + "_b4", stem + "_b4_ansys.out"),
    ]
    rc_ansys = []
    for inp, job, out in apdl_runs:
        rc_ansys.append(_run([ANSYS_EXE, "-b", "-j", job, "-i", inp.name,
                              "-o", out], case_dir, log, "ANSYS-" + job))
    (case_dir / "run_log.txt").write_text("\n".join(log) + "\n", encoding="utf-8")

    if rc_fem != 0 or any(r != 0 for r in rc_ansys):
        print("[FAIL] {} solver exit codes: fem={} ansys={}".format(
            key, rc_fem, rc_ansys))
        return False

    report = case_dir / (stem + ".report.txt")
    files = {
        "fem": report,
        "b4disp": case_dir / (stem + "_b4_disp.csv"),
        "b4reac": case_dir / (stem + "_b4_reac.csv"),
        "b4elem": case_dir / (stem + "_b4_elem.csv"),
        "b188disp": case_dir / (stem + "_disp.csv"),
        "b188reac": case_dir / (stem + "_reac.csv"),
        "b188elem": case_dir / (stem + "_elem.csv"),
    }
    missing = [str(p) for p in files.values() if not p.exists()]
    if missing:
        print("[FAIL] {} missing outputs: {}".format(key, missing))
        return False

    fem = parse_fem_report_3d(files["fem"])
    b4disp = parse_ansys_csv_3d(files["b4disp"])
    b4reac = parse_ansys_csv_3d(files["b4reac"])
    b4elem = parse_ansys_csv_3d(files["b4elem"])
    b188disp = parse_ansys_csv_3d(files["b188disp"])
    b188reac = parse_ansys_csv_3d(files["b188reac"])
    b188elem = parse_ansys_csv_3d(files["b188elem"])
    text, ok = compare_3d(model, fem, b4disp, b4reac, b4elem,
                          b188disp, b188reac, b188elem, stem=stem)
    (case_dir / "compare_report.txt").write_text(text, encoding="utf-8")
    print("[{}] {}  (disp {} nodes / force {} elems / ANSYS rows {}/{}/{})".format(
        "PASS" if ok else "FAIL", key, len(fem["disp"]), len(fem["force"]),
        len(b4disp), len(b4reac), len(b4elem)))
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="all", choices=["all", *CASES_3D])
    ap.add_argument("--generate-only", action="store_true")
    a = ap.parse_args()
    keys = list(CASES_3D) if a.case == "all" else [a.case]
    ok = True
    for k in keys:
        try:
            ok &= run_case(k, a.generate_only)
        except Exception as exc:                            # noqa: BLE001
            import traceback
            traceback.print_exc()
            print("[FAIL] {} exception: {}".format(k, exc))
            ok = False
    print("ALL 3D CASES PASSED" if ok else "SOME 3D CASES FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
