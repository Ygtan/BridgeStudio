#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""3D 斜拉桥 / 拱桥混合单元算例编排：同源导出 -> 双求解器 -> 对照 -> 归档。

与 ``cases/run_cases_3d.py``（纯 BEAM 三跨箱梁）平行、完全独立，不改动
C 阶段已验证的任何文件。本脚本校核「同一 ModelSpec3D 里混合 BEAM +
CABLE/TRUSS」的建模 / 求解 / 商业软件对标链路。

每个算例产出：
    <stem>.dat            FEM-Eigen 3D 前处理输出（ModelSpec3D.to_dat）
    <stem>.model.json     模型快照
    <stem>.inp            ANSYS 18.0 BEAM4 + LINK180 APDL
    <stem>.report.txt     FEM-Eigen 3D example_3d_report.exe 结果
    <stem>_disp/_reac.csv         ANSYS 节点位移 / 反力
    <stem>_beam/_link.csv         ANSYS 梁端力 / 轴向杆轴力
    <stem>_ansys.out      ANSYS 作业日志
    compare_report.txt / manifest.txt / run_log.txt

用法：
    C:\\Users\\user\\anaconda3\\python.exe cases\\run_cases_cable.py --case all
    ... --case all --generate-only
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

from ansys_bridge_cable import (  # noqa: E402
    build_cable_stayed_3d, build_arch_bridge_3d,
    spec_mixed_to_apdl, parse_fem_report_mixed, compare_mixed,
)
from ansys_bridge_3d import parse_ansys_csv_3d  # noqa: E402

FEM_EIGEN = Path(r"D:\AI-work\FEM-Eigen")
FEM_EXE_3D = FEM_EIGEN / "build" / "example_3d_report.exe"
ANSYS_EXE = Path(r"C:\Program Files\ANSYS Inc\v180\ANSYS\bin\winx64\ANSYS180.exe")
MSYS_BIN = r"C:\msys64\mingw64\bin"
CASES_DIR = HERE

BUILDERS = {
    "build_cable_stayed_3d": build_cable_stayed_3d,
    "build_arch_bridge_3d": build_arch_bridge_3d,
}

CASES_CABLE = {
    "CB3D03": dict(
        folder="CB3D03_cable_stayed_3d",
        stem="cb3d03_cable_stayed",
        builder="build_cable_stayed_3d",
        title="CB3D03 single-pylon cable-stayed bridge 3D (BEAM + CABLE)",
        desc="独塔双跨斜拉桥 3D 单梁线：L=60 m / 12 个 BEAM 主梁单元，"
             "塔顶 z=18 m 刚性锚点，6 根 CABLE 斜拉索（A=4000 mm2），"
             "q=20 kN/m 节点化；ANSYS BEAM4 + LINK180",
        kwargs=dict(span=60.0, n_elem=12, B=8.0, h=2.0, tw=0.02, tf=0.025,
                    E_deck=3.45e10, nu=0.2, tower_z=18.0,
                    cable_area=0.004, E_cable=1.95e11, udl_kN=20.0,
                    title="独塔斜拉桥 3D 混合单元（BEAM 主梁 + CABLE 斜拉索）"),
    ),
    "CB3D04": dict(
        folder="CB3D04_arch_bridge_3d",
        stem="cb3d04_arch_bridge",
        builder="build_arch_bridge_3d",
        title="CB3D04 parabolic arch bridge 3D (BEAM rib/deck + TRUSS hangers)",
        desc="下承式抛物线拱桥 3D：L=60 m / 矢高 12 m，12 根主梁 BEAM + "
             "12 段拱肋 BEAM（X-Z 斜梁）+ 11 根竖向吊杆 TRUSS，"
             "q=20 kN/m 节点化；ANSYS BEAM4 + LINK180",
        kwargs=dict(span=60.0, n_elem=12, rise=12.0,
                    B=8.0, h=2.0, tw=0.02, tf=0.025,
                    E_deck=3.45e10, nu=0.2, hanger_area=0.002,
                    E_hanger=1.95e11, udl_kN=20.0,
                    title="抛物线拱桥 3D 混合单元（BEAM 拱肋/主梁 + TRUSS 吊杆）"),
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
        "3D 混合单元算例归档: " + cfg["title"],
        "=" * 78,
        "",
        "模型: " + cfg["desc"],
        "唯一数据源: ModelSpec3D (BridgeStudio/model_spec_3d.py)",
        "  " + cfg["builder"] + "(**" + repr(cfg["kwargs"]) + ")",
        "",
        "链路:",
        "  ModelSpec3D.to_dat(compact=False)   -> " + stem + ".dat   (FEM-Eigen 3D)",
        "  spec_mixed_to_apdl()                 -> " + stem + ".inp  (ANSYS 18.0)",
        "  example_3d_report.exe <stem>.dat    -> " + stem + ".report.txt",
        "  ANSYS180.exe -b -j <job> -i *.inp   -> _disp/_reac/_beam/_link.csv",
        "  compare_mixed()                      -> compare_report.txt",
        "",
        "文件清单:",
        "  {:<34s} 前处理输出（FEM-Eigen 3D .dat）".format(stem + ".dat"),
        "  {:<34s} 模型快照（ModelSpec3D JSON）".format(stem + ".model.json"),
        "  {:<34s} ANSYS BEAM4 + LINK180 APDL".format(stem + ".inp"),
        "  {:<34s} FEM-Eigen 3D 结果（位移 + 混合单元内力）".format(
            stem + ".report.txt"),
        "  {:<34s} ANSYS 节点位移 / 反力".format(stem + "_disp/_reac.csv"),
        "  {:<34s} ANSYS BEAM4 i 端 6 分量 / LINK180 轴力".format(
            stem + "_beam/_link.csv"),
        "  {:<34s} ANSYS 作业日志".format(stem + "_ansys.out"),
        "  compare_report.txt / manifest.txt / run_log.txt",
        "",
        "关键约定（本机 ANSYS v180 实测，见 ansys_probe/link_probe.py, arch_probe.py）:",
        "  X 纵向 / Y 横向 / Z 竖向；竖向荷载 = DOF3(UZ) = ANSYS FZ",
        "  FEM-Eigen 线性 3D 内核把 CABLE/TRUSS 都按 (EA/L)·c⊗c 轴向杆装配,",
        "    不区分只拉不压、不读 L0、无垂度 -> ANSYS LINK180 口径一致",
        "  只受拉 / 悬链线 / Ernst / 索力迭代属 cable_solver.exe 非线性链",
        "  BEAM4: ET,1,BEAM4 + R,1,A,IZZ,IYY,TKZ,TKY,0 + RMORE,0,J,0,0,0,0",
        "         SHEARZ/SHEARY=0 -> Euler-Bernoulli，与 FEM-Eigen 3D 严格一致",
        "  LINK180: ET,2,LINK180 + R,2,A,ISTRN ; 轴力 = SMISC 1",
        "  斜梁局部坐标: 两侧均 ez_ref=global Z -> ey=Z×ex，X-Z 平面斜梁逐位一致",
        "                纯竖直 BEAM 会差 90°，竖向构件一律改用轴向杆",
        "  ETABLE 需按 ESEL,S,TYPE 分组，避免对 LINK180 请求梁的 SMISC 分量",
        "  *CFOPEN + *VWRITE 一次写出整个数组，外面不能再套 *DO",
        "  APDL 命令流必须纯 ASCII（中文标题需转义）",
        "",
        "复现:",
        "  C:\\Users\\user\\anaconda3\\python.exe "
        "D:\\AI-work\\BridgeStudio\\cases\\run_cases_cable.py --case all",
        "  (需 C:\\msys64\\mingw64\\bin 在 PATH；ANSYS 许可仅校核脚本需要)",
        "=" * 78,
    ]) + "\n"


def run_case(key: str, generate_only: bool = False) -> bool:
    cfg = CASES_CABLE[key]
    case_dir = CASES_DIR / cfg["folder"]
    case_dir.mkdir(parents=True, exist_ok=True)
    stem = cfg["stem"]

    model = BUILDERS[cfg["builder"]](**cfg["kwargs"])
    dat = case_dir / (stem + ".dat")
    inp = case_dir / (stem + ".inp")
    dat.write_text(model.to_dat(compact=False), encoding="utf-8")
    (case_dir / (stem + ".model.json")).write_text(
        model.model_dump_json(indent=2), encoding="utf-8")
    inp.write_text(spec_mixed_to_apdl(model, jobname=stem,
                                      title=cfg["title"]), encoding="ascii")
    (case_dir / "manifest.txt").write_text(_manifest(cfg, stem), encoding="utf-8")
    print("[GEN ] {}  {} nodes / {} elems".format(
        key, len(model.nodes), len(model.elements)))
    if generate_only:
        (case_dir / "run_log.txt").write_text("[generate-only]\n", encoding="utf-8")
        return True

    for pat in ("*_disp.csv", "*_reac.csv", "*_beam.csv", "*_link.csv"):
        for p in case_dir.glob(stem + pat):
            p.unlink()

    log: list[str] = []
    rc_fem = _run([FEM_EXE_3D, dat.name], case_dir, log, "FEM-Eigen-3D")
    rc_ansys = _run([ANSYS_EXE, "-b", "-j", stem, "-i", inp.name,
                     "-o", stem + "_ansys.out"], case_dir, log, "ANSYS-BEAM4-LINK180")
    (case_dir / "run_log.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
    if rc_fem != 0 or rc_ansys != 0:
        print("[FAIL] {} solver exit codes: fem={} ansys={}".format(
            key, rc_fem, rc_ansys))
        return False

    files = {
        "fem": case_dir / (stem + ".report.txt"),
        "disp": case_dir / (stem + "_disp.csv"),
        "reac": case_dir / (stem + "_reac.csv"),
        "beam": case_dir / (stem + "_beam.csv"),
        "link": case_dir / (stem + "_link.csv"),
    }
    missing = [str(p) for p in files.values() if not p.exists()]
    if missing:
        print("[FAIL] {} missing outputs: {}".format(key, missing))
        return False

    fem = parse_fem_report_mixed(files["fem"])
    adisp = parse_ansys_csv_3d(files["disp"])
    areac = parse_ansys_csv_3d(files["reac"])
    abeam = parse_ansys_csv_3d(files["beam"])
    alink = parse_ansys_csv_3d(files["link"])
    text, ok = compare_mixed(model, fem, adisp, areac, abeam, alink, stem=stem)
    (case_dir / "compare_report.txt").write_text(text, encoding="utf-8")
    print("[{}] {}  (disp {} nodes / force {} elems / beam {} / link {})".format(
        "PASS" if ok else "FAIL", key, len(fem["disp"]), len(fem["force"]),
        len(abeam), len(alink)))
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="all", choices=["all", *CASES_CABLE])
    ap.add_argument("--generate-only", action="store_true")
    a = ap.parse_args()
    keys = list(CASES_CABLE) if a.case == "all" else [a.case]
    ok = True
    for k in keys:
        try:
            ok &= run_case(k, a.generate_only)
        except Exception as exc:                            # noqa: BLE001
            import traceback
            traceback.print_exc()
            print("[FAIL] {} exception: {}".format(k, exc))
            ok = False
    print("ALL 3D MIXED CASES PASSED" if ok else "SOME 3D MIXED CASES FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
