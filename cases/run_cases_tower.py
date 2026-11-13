#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CB3D09 — 真实竖直塔柱斜拉桥（F 阶段）算例编排。

同源导出 -> FEM-Eigen 3D -> ANSYS BEAM4+LINK180 -> 对照 -> 归档。
与 run_cases_cable.py 完全独立，不改动 CB3D03/CB3D04 的任何产物。

用法：
    C:\\Users\\user\\anaconda3\\python.exe cases\\run_cases_tower.py
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

from ansys_bridge_tower import (  # noqa: E402
    build_cable_stayed_tower_3d, spec_mixed_to_apdl,
    parse_fem_report_mixed, parse_ansys_csv_3d, compare_mixed,
)

FEM_EXE_3D = Path(r"D:\AI-work\FEM-Eigen\build\example_3d_report.exe")
ANSYS_EXE = Path(r"C:\Program Files\ANSYS Inc\v180\ANSYS\bin\winx64\ANSYS180.exe")
MSYS_BIN = r"C:\msys64\mingw64\bin"
OUT = HERE / "CB3D09_real_tower_3d"
STEM = "cb3d09_real_tower"

CFG = dict(span=60.0, n_elem=12, n_tower_seg=6, tower_z=18.0,
           B=8.0, h=2.0, tw=0.02, tf=0.025,
           E_deck=3.45e10, nu=0.2,
           tower_b=3.0, tower_h=3.0,
           cable_area=0.004, E_cable=1.95e11, udl_kN=20.0,
           title="独塔斜拉桥 3D 真实塔柱（BEAM 主梁/塔柱 + CABLE 斜拉索）")
TITLE = "CB3D09 single-pylon cable-stayed bridge 3D with real vertical tower (BEAM + CABLE)"


def _env() -> dict:
    env = os.environ.copy()
    env["PATH"] = MSYS_BIN + os.pathsep + env.get("PATH", "")
    return env


def _run(cmd: list[str], log: list[str], tag: str) -> int:
    log.append("$ " + " ".join(str(c) for c in cmd))
    try:
        p = subprocess.run([str(c) for c in cmd], cwd=str(OUT), env=_env(),
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


def _manifest(model) -> str:
    from ansys_bridge_cable import _vertical_beam_orientations
    kinds = {t.id: t.kind for t in model.etypes}
    orient = _vertical_beam_orientations(model, kinds)
    return "\n".join([
        "=" * 78,
        "3D 真实塔柱斜拉桥算例归档: " + TITLE,
        "=" * 78,
        "",
        "模型: 单塔双跨 L=60 m / 12 个主梁 BEAM 单元；竖直塔柱 6 段 BEAM",
        "      （塔高 18 m，塔底固结，塔梁分离）；6 根 CABLE 扇形索；q=20 kN/m 节点化",
        "唯一数据源: ModelSpec3D (ansys_bridge_tower.build_cable_stayed_tower_3d)",
        "  " + repr(CFG),
        "",
        "链路:",
        "  ModelSpec3D.to_dat(compact=False)   -> " + STEM + ".dat   (FEM-Eigen 3D)",
        "  spec_mixed_to_apdl()                 -> " + STEM + ".inp  (ANSYS 18.0)",
        "  example_3d_report.exe <stem>.dat    -> " + STEM + ".report.txt",
        "  ANSYS180.exe -b -j <job> -i *.inp   -> _disp/_reac/_beam/_link.csv",
        "  compare_mixed(tower=True)            -> compare_report.txt",
        "",
        "关键约定:",
        "  X 纵向 / Y 横向 / Z 竖向；竖向荷载 = DOF3(UZ) = ANSYS FZ",
        "  FEM-Eigen 3D 对竖直梁取 ey=+X / ez=+Y；ANSYS BEAM4 默认 IY/IZ 互换。",
        "  APDL 自动为 %d 个纯竖直 BEAM 单元生成方向节点 K(+Y)，" % len(orient),
        "  用 E,I,J,K 定义单元，实测与 FEM 逐位一致、0 warning；K 节点不参与求解。",
        "  FEM-Eigen 内核零改动；无竖直 BEAM 的既有算例 APDL 逐字节不变。",
        "  BEAM4: SHEARZ/SHEARY=0 -> Euler-Bernoulli；LINK180 轴力 = SMISC 1",
        "  线性 3D 内核把 CABLE 按 (EA/L)·c⊗c 装配（不区分只拉不压、不读 L0）",
        "  *CFOPEN + *VWRITE 一次写出整个数组，外面不能再套 *DO",
        "  APDL 命令流必须纯 ASCII",
        "",
        "文件清单:",
        "  {:<38s} 前处理输出（FEM-Eigen 3D .dat）".format(STEM + ".dat"),
        "  {:<38s} 模型快照（ModelSpec3D JSON）".format(STEM + ".model.json"),
        "  {:<38s} ANSYS BEAM4 + LINK180 APDL".format(STEM + ".inp"),
        "  {:<38s} FEM-Eigen 3D 结果".format(STEM + ".report.txt"),
        "  {:<38s} ANSYS 节点位移 / 反力".format(STEM + "_disp/_reac.csv"),
        "  {:<38s} ANSYS BEAM4 i 端 6 分量 / LINK180 轴力".format(
            STEM + "_beam/_link.csv"),
        "  {:<38s} ANSYS 作业日志".format(STEM + "_ansys.out"),
        "  compare_report.txt / manifest.txt / run_log.txt",
        "",
        "复现:",
        "  C:\\Users\\user\\anaconda3\\python.exe "
        "D:\\AI-work\\BridgeStudio\\cases\\run_cases_tower.py",
        "=" * 78,
    ]) + "\n"


def run_case(generate_only: bool = False) -> bool:
    OUT.mkdir(parents=True, exist_ok=True)
    model = build_cable_stayed_tower_3d(**CFG)
    dat = OUT / (STEM + ".dat")
    inp = OUT / (STEM + ".inp")
    dat.write_text(model.to_dat(compact=False), encoding="utf-8")
    (OUT / (STEM + ".model.json")).write_text(
        model.model_dump_json(indent=2), encoding="utf-8")
    inp.write_text(spec_mixed_to_apdl(model, jobname=STEM, title=TITLE),
                   encoding="ascii")
    (OUT / "manifest.txt").write_text(_manifest(model), encoding="utf-8")
    print("[GEN ] CB3D09  {} nodes / {} elems".format(
        len(model.nodes), len(model.elements)))
    if generate_only:
        (OUT / "run_log.txt").write_text("[generate-only]\n", encoding="utf-8")
        return True

    for pat in ("*_disp.csv", "*_reac.csv", "*_beam.csv", "*_link.csv"):
        for p in OUT.glob(STEM + pat):
            p.unlink()

    log: list[str] = []
    rc_fem = _run([FEM_EXE_3D, dat.name], log, "FEM-Eigen-3D")
    rc_ansys = _run([ANSYS_EXE, "-b", "-j", STEM, "-i", inp.name,
                     "-o", STEM + "_ansys.out"], log, "ANSYS-BEAM4-LINK180")
    (OUT / "run_log.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
    if rc_fem != 0 or rc_ansys != 0:
        print("[FAIL] solver exit codes: fem={} ansys={}".format(
            rc_fem, rc_ansys))
        return False

    files = {k: OUT / (STEM + s) for k, s in (
        ("fem", ".report.txt"), ("disp", "_disp.csv"),
        ("reac", "_reac.csv"), ("beam", "_beam.csv"),
        ("link", "_link.csv"))}
    missing = [str(p) for p in files.values() if not p.exists()]
    if missing:
        print("[FAIL] missing outputs: {}".format(missing))
        return False

    fem = parse_fem_report_mixed(files["fem"])
    adisp = parse_ansys_csv_3d(files["disp"])
    areac = parse_ansys_csv_3d(files["reac"])
    abeam = parse_ansys_csv_3d(files["beam"])
    alink = parse_ansys_csv_3d(files["link"])
    text, ok = compare_mixed(model, fem, adisp, areac, abeam, alink,
                             stem=STEM, tower=True)
    (OUT / "compare_report.txt").write_text(text, encoding="utf-8")
    print("[{}] CB3D09  (disp {} nodes / force {} elems / beam {} / link {})".format(
        "PASS" if ok else "FAIL", len(fem["disp"]), len(fem["force"]),
        len(abeam), len(alink)))
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate-only", action="store_true")
    a = ap.parse_args()
    ok = run_case(a.generate_only)
    print("CB3D09 REAL TOWER CASE PASSED" if ok
          else "CB3D09 REAL TOWER CASE FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
