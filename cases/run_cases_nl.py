#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CB3D10 — 非线性成桥态 vs ANSYS 18.0 NLGEOM + LINK180 对标算例编排。

流程见 ansys_bridge_nl.py 模块头。产出：
    <stem>.dat             COMPLETION 前处理输出（ModelSpec3D.to_dat）
    <stem>.model.json      模型快照
    <stem>_cable.txt       cable_solver.exe 原始结果（冻结）
    <stem>_nl.inp          ANSYS BEAM4+LINK180+NLGEOM+INISTATE APDL
    <stem>_nl_disp/_reac/_link.csv   ANSYS 机器可读结果
    <stem>_nl_ansys.out    ANSYS 作业日志
    compare_report.txt / manifest.txt / run_log.txt

用法：
    C:\\Users\\user\\anaconda3\\python.exe cases\\run_cases_nl.py
    ... --generate-only
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRIDGESTUDIO = HERE.parent
sys.path.insert(0, str(BRIDGESTUDIO))

os.environ["PATH"] = (r"C:\msys64\mingw64\bin" + os.pathsep
                      + os.environ.get("PATH", ""))

from cable_stayed_3d import build_cable_stayed_3d  # noqa: E402
from fem_eigen_cable import solve_cable_bridge  # noqa: E402
from ansys_bridge_nl import (  # noqa: E402
    spec_nl_to_apdl, initial_cable_strain, compare_nl,
)
from ansys_bridge_3d import parse_ansys_csv_3d  # noqa: E402

ANSYS_EXE = Path(r"C:\Program Files\ANSYS Inc\v180\ANSYS\bin\winx64\ANSYS180.exe")
OUT = HERE / "CB3D10_completion_vs_ansys_nl"
STEM = "cb3d10_completion"
NL_STEM = STEM + "_nl"

CFG = dict(
    main_span=80.0, side_span=30.0, tower_height=25.0,
    n_cable=4, panel=10.0,
    B=12.0, h=2.5, tf=0.28, tw=0.45,
    tower_b=3.0, tower_h=3.0,
    q_deck=50e3, cable_area_mm2=3000.0,
    cable_dens=0.0,                 # W=0 -> 关闭垂度/Ernst
    mode="COMPLETION", method="DECK",
)
TITLE = ("CB3D10 FEM-Eigen nonlinear completion state vs "
         "ANSYS NLGEOM + LINK180")


def _run(cmd: list[str], log: list[str], tag: str) -> int:
    log.append("$ " + " ".join(str(c) for c in cmd))
    try:
        p = subprocess.run([str(c) for c in cmd], cwd=str(OUT),
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


def _manifest(res: dict, info: dict, eps: dict) -> str:
    md = res["meta"]
    return "\n".join([
        "=" * 78,
        "CB3D10 非线性成桥态 vs ANSYS NLGEOM + LINK180 算例归档",
        "=" * 78,
        "",
        "唯一数据源: BridgeStudio.cable_stayed_3d.build_cable_stayed_3d(",
        "  " + repr(CFG) + ")  -> ModelSpec3D",
        "",
        "FEM 成桥态: MODE=COMPLETION METHOD=DECK, converged={}, iterations={},".format(
            res.get("converged"), md.get("iterations")),
        "  closure_err={:.6e} m, tension_err={:.6e}".format(
            float(md.get("closure_err", 0)), float(md.get("tension_err", 0))),
        "  索数 {}，索力 {:.1f} .. {:.1f} kN".format(
            len(res["cables"]),
            float(md.get("tension_min_kN", 0)),
            float(md.get("tension_max_kN", 0))),
        "",
        "链路:",
        "  ModelSpec3D.to_dat()              -> " + STEM + ".dat",
        "  cable_solver.exe <stem>.dat       -> " + STEM + "_cable.txt",
        "    [ERECTED]  -> ANSYS 初始几何（制造线形）",
        "    [CABLE] L0 -> INISTATE 初应变 eps=(L_ERECTED-L0)/L0",
        "    [U] / [REACTION] -> FEM 参考值",
        "  spec_nl_to_apdl()                 -> " + NL_STEM + ".inp",
        "  ANSYS180.exe -b -i *.inp          -> _nl_disp/_reac/_link.csv",
        "  compare_nl()                      -> compare_report.txt",
        "",
        "ANSYS 语义（本机 v180 实测）:",
        "  LINK180: KEYOPT(3)=1 tension-only, KEYOPT(2)=0；轴力 = SMISC 1",
        "  R,id,A,ISTRN 的 ISTRN 对 LINK180 无效；初应变必须用",
        "    INISTATE,SET,DTYP,EPEL + INISTATE,DEFINE,<e>,,EPEL,X,eps",
        "  竖直/近竖直 BEAM4 用方向节点 K(+Y) 对齐 FEM 局部轴（%d 个）" % info["orient_nodes"],
        "  NLGEOM,ON + NSUBST,20,100,10 + NEQIT,50",
        "  APDL 命令流纯 ASCII；*CFOPEN+*VWRITE 一次写出整个数组",
        "",
        "口径边界:",
        "  索自重 W=0 关闭垂度/Ernst（ANSYS LINK180 无垂度模型）。",
        "  FEM 共转列式索用 Lagrangian 工程应变 N=EA/L0*(Ln-L0)；",
        "  ANSYS NLGEOM 用 Almansi 应变，两者一阶一致，差异 O(eps^2)。",
        "  BEAM4 不参与大转动更新，成桥位移为 mm 量级，影响为二阶。",
        "",
        "文件清单:",
        "  {:<40s} COMPLETION 前处理输出".format(STEM + ".dat"),
        "  {:<40s} 模型快照".format(STEM + ".model.json"),
        "  {:<40s} cable_solver 原始结果（冻结）".format(STEM + "_cable.txt"),
        "  {:<40s} ANSYS APDL".format(NL_STEM + ".inp"),
        "  {:<40s} ANSYS 位移/反力/索力 CSV".format(NL_STEM + "_*.csv"),
        "  {:<40s} ANSYS 作业日志".format(NL_STEM + "_ansys.out"),
        "  compare_report.txt / manifest.txt / run_log.txt",
        "",
        "复现:",
        "  C:\\Users\\user\\anaconda3\\python.exe "
        "D:\\AI-work\\BridgeStudio\\cases\\run_cases_nl.py",
        "=" * 78,
    ]) + "\n"


def run_case(generate_only: bool = False) -> bool:
    OUT.mkdir(parents=True, exist_ok=True)
    model, info_model = build_cable_stayed_3d(**CFG)
    (OUT / (STEM + ".model.json")).write_text(
        model.model_dump_json(indent=2), encoding="utf-8")

    res = solve_cable_bridge(model, out_dir=OUT, dat_name=STEM + ".dat",
                             timeout=600)
    if not res["converged"]:
        print("[FAIL] cable_solver 未收敛")
        return False
    coords = dict(res["erected"])
    l0 = {c["elem"]: c["L0"] for c in res["cables"]}
    eps = initial_cable_strain(model, coords=coords, l0=l0)
    print("[FEM ] CB3D10  {} nodes / {} elems / {} cables  "
          "closure={:.3e}".format(
              len(model.nodes), len(model.elements), len(res["cables"]),
              float(res["meta"].get("closure_err", float("nan")))))

    apdl, apdl_info = spec_nl_to_apdl(
        model, jobname=NL_STEM, coords=coords, l0=l0, title=TITLE)
    (OUT / (NL_STEM + ".inp")).write_text(apdl, encoding="ascii")
    (OUT / "manifest.txt").write_text(
        _manifest(res, apdl_info, eps), encoding="utf-8")
    print("[GEN ] CB3D10  APDL {} bytes, {} cables, {} orientation nodes, "
          "eps {:.6g}..{:.6g}".format(
              len(apdl), apdl_info["cable_count"],
              apdl_info["orient_nodes"], min(eps.values()), max(eps.values())))
    if generate_only:
        (OUT / "run_log.txt").write_text("[generate-only]\n", encoding="utf-8")
        return True

    for p in OUT.glob(NL_STEM + "_*.csv"):
        p.unlink()
    log: list[str] = []
    rc = _run([ANSYS_EXE, "-b", "-j", NL_STEM, "-i", NL_STEM + ".inp",
               "-o", NL_STEM + "_ansys.out"], log, "ANSYS-NLGEOM-LINK180")
    (OUT / "run_log.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
    if rc != 0:
        print("[FAIL] ANSYS exit={}".format(rc))
        return False

    files = {k: OUT / (NL_STEM + s) for k, s in (
        ("disp", "_disp.csv"), ("reac", "_reac.csv"), ("link", "_link.csv"))}
    missing = [str(p) for p in files.values() if not p.exists()]
    if missing:
        print("[FAIL] missing ANSYS outputs: {}".format(missing))
        return False
    adisp = parse_ansys_csv_3d(files["disp"])
    areac = parse_ansys_csv_3d(files["reac"])
    alink = parse_ansys_csv_3d(files["link"])
    text, ok = compare_nl(res, adisp, areac, alink, stem=STEM)
    (OUT / "compare_report.txt").write_text(text, encoding="utf-8")
    print("[{}] CB3D10  disp {} nodes / link {} cables".format(
        "PASS" if ok else "FAIL", len(adisp), len(alink)))
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate-only", action="store_true")
    a = ap.parse_args()
    ok = run_case(a.generate_only)
    print("CB3D10 NONLINEAR ANSYS CASE PASSED" if ok
          else "CB3D10 NONLINEAR ANSYS CASE FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
