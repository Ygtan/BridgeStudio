#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CB3D11 / CB3D12 — 地锚式与自锚式悬索桥 vs ANSYS 18.0 NLGEOM 对标算例。

两个算例使用**同一组几何/材料/荷载参数**，只改 SYSTEM 与由此派生的
锚固、纵向约束方式，因此差异可以直接归因于体系：

    CB3D11  EARTH 地锚式：主缆锚于桥台外侧锚碇，H 传地基，梁端纵向固定
    CB3D12  SELF  自锚式：主缆锚于加劲梁端，H 压进梁，纵向约束放跨中

链路与口径见 ansys_bridge_susp.py 模块头。每个算例产出：
    <stem>.dat                    FEM 前处理输出（含 *SUSP_BRIDGE）
    <stem>.model.json             模型快照
    <stem>_cable.txt              cable_solver.exe 找形结果（冻结）
    <stem>_nl.inp                 ANSYS BEAM4+LINK180+NLGEOM+INISTATE APDL
    <stem>_nl_ansys_disp/_reac/_link.csv   ANSYS 机器可读结果
    <stem>_nl_ansys.out           ANSYS 作业日志
    <stem>_nl_compare_report.txt / _manifest.txt / _run_log.txt

用法：
    C:\\Users\\user\\anaconda3\\python.exe cases\\run_cases_susp.py
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

from suspension_3d import build_suspension_3d  # noqa: E402
from fem_eigen_cable import solve_cable_bridge  # noqa: E402
from ansys_bridge_nl import spec_nl_to_apdl  # noqa: E402
from ansys_bridge_3d import parse_ansys_csv_3d  # noqa: E402
from ansys_bridge_susp import (  # noqa: E402
    SYSTEM_LABEL, compare_susp, design_geometry, strain_range,
    unstrained_lengths,
)

ANSYS_EXE = Path(r"C:\Program Files\ANSYS Inc\v180\ANSYS\bin\winx64\ANSYS180.exe")

#: 两个体系共用的参数。索自重 cable_dens=0 -> W=0，关闭垂度/Ernst，
#: 与 ANSYS LINK180 的无垂度轴向杆一致；索面积取较大值使初应变落在
#: 1e-3 量级，压住 Almansi / Lagrangian 应变度量的 O(eps^2) 差异。
CFG = dict(
    main_span=120.0, side_span=40.0, sag_ratio=0.10, panel=10.0,
    clearance=5.0, tower_below=6.0, n_tower_seg=4,
    B=12.0, h=3.0, tf=0.028, tw=0.020,
    q_deck=20e3, cable_dens=0.0, tower_dens=2500.0,
    side_hangers=True, anchor_offset=8.0,
    main_area_mm2=30000.0, hanger_area_mm2=5000.0,
    tower_b=2.0, tower_h=2.0,
    lane_count=1,
)

CASES = [
    dict(system="EARTH", case="CB3D11_suspension_earth_anchored",
         stem="cb3d11_susp_earth",
         title="CB3D11 Earth-anchored suspension bridge vs ANSYS NLGEOM"),
    dict(system="SELF", case="CB3D12_suspension_self_anchored",
         stem="cb3d12_susp_self",
         title="CB3D12 Self-anchored suspension bridge vs ANSYS NLGEOM"),
]


def _run(cmd: list[str], cwd: Path, log: list[str], tag: str) -> int:
    log.append("$ " + " ".join(str(c) for c in cmd))
    try:
        p = subprocess.run([str(c) for c in cmd], cwd=str(cwd),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=900)
        out = (p.stdout or "") + (p.stderr or "")
        log.append("[{}] exit={}".format(tag, p.returncode))
        if out.strip():
            log.extend("    " + s for s in out.strip().splitlines()[-12:])
        return p.returncode
    except Exception as exc:                                # noqa: BLE001
        log.append("[{}] EXCEPTION {}".format(tag, exc))
        return -1


def _log_summary(path: Path, log: list[str]) -> tuple[int, int]:
    txt = path.read_text(encoding="utf-8", errors="replace")
    import re
    nerr = nwarn = 0
    m = re.search(r"NUMBER OF ERROR\s+MESSAGES ENCOUNTERED=\s*(\d+)", txt)
    if m:
        nerr = int(m.group(1))
    m = re.search(r"NUMBER OF WARNING\s+MESSAGES ENCOUNTERED=\s*(\d+)", txt)
    if m:
        nwarn = int(m.group(1))
    log.append("[ANSYS-LOG] errors={} warnings={}".format(nerr, nwarn))
    log.append("[ANSYS-LOG] warning signature KEYOPT(3)=1 undocumented: {}".format(
        "present" if "KEYOPT(3) = 1 is an undocumented" in txt else "MISSING"))
    return nerr, nwarn


def _manifest(case: dict, model, res: dict, info: dict, apdl_info: dict,
              eps_range: tuple[float, float], nerr: int, nwarn: int) -> str:
    md = res["meta"]
    stem, system = case["stem"], case["system"]
    job = stem + "_nl_ansys"
    return "\n".join([
        "=" * 80,
        "{}  {} 算例归档".format(case["case"], SYSTEM_LABEL[system]),
        "=" * 80,
        "",
        "唯一数据源: BridgeStudio.suspension_3d.build_suspension_3d(",
        "  SYSTEM={}, ".format(system) + repr(CFG) + ")  -> ModelSpec3D",
        "  注: 生成后清空 model.lanes（本算例只做静力成桥态对标，",
        "      不引入车道活载 / 影响线 / 模态；ANSYS 侧无对应荷载）。",
        "",
        "体系差异:",
        "  EARTH 地锚式：主缆锚于桥台外侧锚碇（全约束），H 传地基；",
        "                加劲梁端纵向固定，梁不承受 H。",
        "  SELF  自锚式：主缆锚于加劲梁端节点，H 以轴压进入加劲梁；",
        "                纵向约束置于跨中（对称荷载下支反力恒为零）。",
        "",
        "FEM 找形: cable_solver.exe FormFinding（*SUSP_BRIDGE SYSTEM={}）".format(system),
        "  converged={}, iterations={}, H={:.1f} kN, 主跨矢高={:.6g} m".format(
            res.get("converged"), md.get("iterations"),
            float(md.get("H_kN", 0)), float(md.get("sag_m", 0))),
        "  closure_err={:.6e} m (= max|[U]|, 位移量级而非迭代残差)".format(
            float(md.get("closure_err", 0))),
        "  tension_err={:.6e}".format(float(md.get("tension_err", 0))),
        "  主缆 {} 段 + 吊索 {} 根 = {} 根 CABLE；索力 {:.1f} .. {:.1f} kN".format(
            len(res["main_cable"]), len(res["hangers"]), len(res["cables"]),
            float(md.get("tension_min_kN", 0)), float(md.get("tension_max_kN", 0))),
        "  边跨 {:.1f} m / 全桥 {:.1f} m / 塔顶 z={:.1f} m".format(
            info["side_span"], info["total_length"], info["tower_top_z"]),
        "",
        "链路:",
        "  ModelSpec3D.to_dat()                 -> " + stem + ".dat",
        "  cable_solver.exe <stem>.dat          -> " + stem + "_cable.txt",
        "    [ERECTED] + [U] -> 设计几何 D（ANSYS 初始几何）",
        "    [CABLE] L0      -> 绝对下料长度 -> INISTATE 初应变 eps=(L_D-L0)/L0",
        "    [U] / [REACTION] / [CABLE] T -> FEM 参考值",
        "  ansys_bridge_susp.design_geometry() + spec_nl_to_apdl()",
        "                                      -> " + stem + "_nl.inp",
        "  ANSYS180.exe -b -j {} -i {}     -> {}_*.csv".format(job, stem + "_nl.inp", job),
        "  compare_susp()                       -> " + stem + "_nl_compare_report.txt",
        "",
        "ANSYS 语义（本机 ANSYS 18.0 实测，探测脚本见",
        "  FEM-Eigen/tests/benchmark/nonlinear_ansys_3d/probes/）:",
        "  BEAM4 + LINK180(KEYOPT(3)=1 tension only, KEYOPT(2)=0) + NLGEOM,ON",
        "  NSUBST,20,100,10 / NEQIT,50；索轴力 = SMISC 1",
        "  R,id,A,ISTRN 的 ISTRN 对 LINK180 无效；初应变必须用",
        "    INISTATE,SET,DTYP,EPEL + INISTATE,DEFINE,<e>,,EPEL,X,eps",
        "  竖直塔柱 BEAM4 用方向节点 K(+Y) 对齐 FEM 局部轴（{} 个）".format(
            apdl_info["orient_nodes"]),
        "  eps 范围 {:.6g} .. {:.6g}".format(eps_range[0], eps_range[1]),
        "  ANSYS 日志: {} error / {} warning（KEYOPT(3)=1 undocumented，".format(nerr, nwarn),
        "    两个 CABLE 单元类型各一条，如实保留不隐藏）",
        "",
        "口径边界:",
        "  悬索桥 FormFinding 不是斜拉桥的制造线形闭环。[CAMBER]/[ERECTED]",
        "  只是预拱度建议值，不参与 L0 反算；若误把 [ERECTED] 当作 ANSYS",
        "  初始几何，实测位移相对差 0.25（FAIL）。",
        "  cable_dens=0 -> W=0 关闭垂度/Ernst（ANSYS LINK180 无垂度模型）。",
        "  FEM 共转列式索用 Lagrangian 工程应变；ANSYS NLGEOM 用 Almansi",
        "  应变，两者一阶一致、差异 O(eps^2)。BEAM4 不含大转动梁柱效应，",
        "  成桥位移为毫米量级，影响为二阶。",
        "  未验证：带索垂度 / Ernst 的成桥态对标、大转动下的梁柱效应、",
        "  ANSYS 版本升级后 KEYOPT(3)=1 的行为复测。",
        "",
        "文件清单:",
        "  {:<44s} FEM 前处理输出".format(stem + ".dat"),
        "  {:<44s} 模型快照".format(stem + ".model.json"),
        "  {:<44s} cable_solver 找形结果（冻结）".format(stem + "_cable.txt"),
        "  {:<44s} ANSYS APDL".format(stem + "_nl.inp"),
        "  {:<44s} ANSYS 位移/反力/索力 CSV".format(job + "_*.csv"),
        "  {:<44s} ANSYS 作业日志".format(job + ".out"),
        "  " + stem + "_nl_compare_report.txt / _manifest.txt / _run_log.txt",
        "",
        "复现:",
        "  C:\\Users\\user\\anaconda3\\python.exe "
        "D:\\AI-work\\BridgeStudio\\cases\\run_cases_susp.py",
        "=" * 80,
    ]) + "\n"


def run_case(case: dict, generate_only: bool = False) -> bool:
    OUT = HERE / case["case"]
    OUT.mkdir(parents=True, exist_ok=True)
    stem, system = case["stem"], case["system"]
    job = stem + "_nl_ansys"
    inp_name = stem + "_nl.inp"

    model, info = build_suspension_3d(system=system, **CFG)
    model.lanes = []                     # 本算例只做静力成桥态对标
    (OUT / (stem + ".model.json")).write_text(
        model.model_dump_json(indent=2), encoding="utf-8")

    res = solve_cable_bridge(model, out_dir=OUT,
                             dat_name=stem + ".dat", timeout=600)
    if not res["converged"]:
        print("[FAIL] {} cable_solver not converged".format(stem))
        return False
    coords = design_geometry(res)
    l0 = unstrained_lengths(res)
    eps_range = strain_range(model, coords, l0)

    apdl, apdl_info = spec_nl_to_apdl(
        model, jobname=job, coords=coords, l0=l0, title=case["title"])
    (OUT / inp_name).write_text(apdl, encoding="ascii")
    print("[FEM ] {}  {} nodes / {} elems / {} cables ({} main + {} hangers)  "
          "H={:.0f} kN closure={:.3e}".format(
              stem, len(model.nodes), len(model.elements), len(res["cables"]),
              len(res["main_cable"]), len(res["hangers"]),
              float(res["meta"].get("H_kN", 0)),
              float(res["meta"].get("closure_err", float("nan")))))
    print("[GEN ] {}  APDL {} bytes, eps {:.6g}..{:.6g}, {} orient nodes".format(
        stem, len(apdl), eps_range[0], eps_range[1],
        apdl_info["orient_nodes"]))

    log: list[str] = []
    if generate_only:
        (OUT / (stem + "_nl_run_log.txt")).write_text(
            "[generate-only]\n", encoding="utf-8")
        return True

    for p in OUT.glob(job + "_*.csv"):
        p.unlink()
    rc = _run([ANSYS_EXE, "-b", "-j", job, "-i", inp_name,
               "-o", job + ".out"], OUT, log, "ANSYS-NLGEOM-SUSP")
    out_path = OUT / (job + ".out")
    nerr = nwarn = -1
    if out_path.exists():
        nerr, nwarn = _log_summary(out_path, log)
    (OUT / (stem + "_nl_run_log.txt")).write_text(
        "\n".join(log) + "\n", encoding="utf-8")
    if rc != 0 or nerr != 0:
        print("[FAIL] {} ANSYS exit={} errors={}".format(stem, rc, nerr))
        return False

    files = {k: OUT / (job + s) for k, s in (
        ("disp", "_disp.csv"), ("reac", "_reac.csv"), ("link", "_link.csv"))}
    missing = [str(p) for p in files.values() if not p.exists()]
    if missing:
        print("[FAIL] {} missing ANSYS outputs: {}".format(stem, missing))
        return False
    adisp = parse_ansys_csv_3d(files["disp"])
    areac = parse_ansys_csv_3d(files["reac"])
    alink = parse_ansys_csv_3d(files["link"])
    text, ok = compare_susp(res, adisp, areac, alink, stem=stem,
                            system=system, model=model, coords=coords,
                            eps_range=eps_range)
    (OUT / (stem + "_nl_compare_report.txt")).write_text(text, encoding="utf-8")
    (OUT / (stem + "_nl_manifest.txt")).write_text(
        _manifest(case, model, res, info, apdl_info, eps_range, nerr, nwarn),
        encoding="utf-8")
    print("[{}] {}  disp {} nodes / link {} cables / {} ANSYS warnings".format(
        "PASS" if ok else "FAIL", stem, len(adisp), len(alink), nwarn))
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate-only", action="store_true")
    a = ap.parse_args()
    all_ok = True
    for case in CASES:
        all_ok &= run_case(case, a.generate_only)
    print("CB3D11/CB3D12 SUSPENSION ANSYS CASES PASSED" if all_ok
          else "CB3D11/CB3D12 SUSPENSION ANSYS CASES FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
