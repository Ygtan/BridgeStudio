# -*- coding: utf-8 -*-
"""连续梁算例：BridgeStudio 建模 -> FEM-Eigen 计算 -> ANSYS 18.0 校核 一键打通。

用法（在任意目录，需 anaconda python，含 pydantic / matplotlib）::

    C:\\Users\\user\\anaconda3\\python.exe D:\\AI-work\\BridgeStudio\\cases\\run_cases.py --case all
    ... --case CB01
    ... --case CB02 --generate-only      # 只产出 .dat / .inp / .model.json，不求解

每个算例目录下会保留：
    <stem>.dat              BridgeStudio 前处理输出（FEM-Eigen 输入）
    <stem>.model.json       BridgeSpec 模型快照（可精确复现建模）
    <stem>.inp              ANSYS MAPDL 命令流（与 .dat 同源）
    <stem>.result           FEM-Eigen 求解结果
    <stem>_ansys.out        ANSYS 批处理输出（含许可/求解日志）
    <stem>_disp.csv          ANSYS 节点位移
    <stem>_reac.csv          ANSYS 节点反力
    <stem>_elem.csv          ANSYS 单元端力
    <stem>_spring.csv        ANSYS 接地弹簧力（仅 CB05 弹性支承算例）
    <stem>.log/.err/.rst    ANSYS 作业文件（校核证据）
    compare_report.txt      三方对比报告
    run_log.txt             本次运行的命令与退出码
    manifest.txt            文件清单与复现步骤
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BRIDGESTUDIO = Path(r"D:\AI-work\BridgeStudio")
FEM_EIGEN = Path(r"D:\AI-work\FEM-Eigen")
FEM_EXE = FEM_EIGEN / "build" / "example_mixed.exe"
ANSYS_EXE = Path(r"C:\Program Files\ANSYS Inc\v180\ANSYS\bin\winx64\ANSYS180.exe")
MSYS_BIN = r"C:\msys64\mingw64\bin"
MSYS_USR = r"C:\msys64\usr\bin"
CASES_DIR = Path(__file__).resolve().parent

sys.path.insert(0, str(BRIDGESTUDIO))

from bridge_spec import (BoundaryDef, NodalLoadDef,  # noqa: E402
                         build_beam_bridge)
from ansys_bridge import (spec_to_apdl, parse_fem_result, parse_ansys_csv,  # noqa: E402
                          compare, compare_generic)


def _as_spring(spec, node: int, k: float) -> None:
    """把某个刚性竖向支座替换成接地弹簧（同源改 BridgeSpec）。"""
    for i, b in enumerate(spec.boundaries):
        if int(b.node) == node:
            spec.boundaries[i] = BoundaryDef(node=node, dofs=b.dofs, value=0.0, k=k)
            return
    raise ValueError("节点 {} 上没有可替换的支座".format(node))


def _two_point_loads(spec) -> None:
    """左右两跨跨中各加 200 kN 向下节点力（CB01 网格：节点 6 / 16）。"""
    spec.nodal_loads.extend([
        NodalLoadDef(node=6, dof=2, value=-200000.0),
        NodalLoadDef(node=16, dof=2, value=-200000.0),
    ])

CASES = {
    "CB01": dict(
        folder="CB01_two_span_continuous_beam",
        stem="cb01_2span",
        n_spans=2,
        title="CB01 two-span continuous beam 2x20m UDL 20kN/m",
        desc="两跨等截面连续梁：2x20 m，矩形截面 0.6x1.2 m，20 个梁单元，均布荷载 20 kN/m",
        kwargs=dict(span=40.0, n_spans=2, n_elem=20, shape="rectangular",
                    h=1.2, b=0.6, udl_kN=20.0, gravity=0.0),
    ),
    "CB02": dict(
        folder="CB02_three_span_continuous_beam",
        stem="cb02_3span",
        n_spans=3,
        title="CB02 three-span continuous box beam 3x30m UDL 20kN/m",
        desc="三跨等截面连续箱梁：3x30 m，箱形截面 h=2.0 m / b=8.0 m，30 个梁单元，均布荷载 20 kN/m",
        kwargs=dict(span=90.0, n_spans=3, n_elem=30, shape="box",
                    h=2.0, b=8.0, udl_kN=20.0, gravity=0.0),
    ),
    # ---- 扩展工况：自重 / 节点集中力 / 弹性支承（无现成闭式解，用通用对比） ----
    "CB03": dict(
        folder="CB03_selfweight",
        stem="cb03_selfweight",
        n_spans=2,
        mode="generic",
        title="CB03 two-span beam UDL 20kN/m + self weight (DENS + ACEL)",
        desc="两跨连续梁（CB01 几何）叠加材料自重：DENS=7850 kg/m3, g=9.81",
        note="均布 20 kN/m + 自重（FEM *GRAVITY 0 -9.81 / ANSYS ACEL,0,9.81,0）",
        kwargs=dict(span=40.0, n_spans=2, n_elem=20, shape="rectangular",
                    h=1.2, b=0.6, udl_kN=20.0, gravity=-9.81),
    ),
    "CB04": dict(
        folder="CB04_point_loads",
        stem="cb04_point_loads",
        n_spans=2,
        mode="generic",
        title="CB04 two-span beam with two 200kN point loads at span midpoints",
        desc="两跨连续梁：无均布荷载，左右两跨跨中各 200 kN 节点集中力",
        note="节点 6 / 16 各 -200 kN（FEM *CLOAD / ANSYS F,FY）",
        kwargs=dict(span=40.0, n_spans=2, n_elem=20, shape="rectangular",
                    h=1.2, b=0.6, udl_kN=0.0, gravity=0.0),
        setup=_two_point_loads,
    ),
    "CB05": dict(
        folder="CB05_elastic_support",
        stem="cb05_elastic_support",
        n_spans=2,
        mode="generic",
        spring=True,
        title="CB05 two-span beam with elastic mid support K=2e6 N/m",
        desc="两跨连续梁：中支座改为竖向弹性支承 K=2e6 N/m，均布 20 kN/m",
        note="中支座 node 11 弹性支承（FEM *BOUNDARY K= / ANSYS COMBIN14）",
        kwargs=dict(span=40.0, n_spans=2, n_elem=20, shape="rectangular",
                    h=1.2, b=0.6, udl_kN=20.0, gravity=0.0),
        setup=lambda s: _as_spring(s, 11, 2.0e6),
    ),
}


def _run(cmd: list[str], cwd: Path, env: dict, timeout: int, log: list[str], tag: str):
    t0 = time.time()
    log.append("[{}] cwd = {}".format(tag, cwd))
    log.append("[{}] cmd = {}".format(tag, " ".join('"{}"'.format(c) if " " in c else c for c in map(str, cmd))))
    try:
        r = subprocess.run(list(map(str, cmd)), cwd=str(cwd), env=env,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        log.append("[{}] exit = {}  elapsed = {:.2f}s".format(tag, r.returncode, time.time() - t0))
        if r.stdout.strip():
            log.append("[{}] stdout:\n{}".format(tag, r.stdout.strip()[:4000]))
        if r.stderr.strip():
            log.append("[{}] stderr:\n{}".format(tag, r.stderr.strip()[:2000]))
        return r.returncode
    except subprocess.TimeoutExpired:
        log.append("[{}] TIMEOUT after {}s".format(tag, timeout))
        return -99


def run_case(name: str, generate_only: bool = False) -> bool:
    cfg = CASES[name]
    case_dir = CASES_DIR / cfg["folder"]
    case_dir.mkdir(parents=True, exist_ok=True)
    stem = cfg["stem"]
    log: list[str] = ["运行时间: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                      "算例: {} -- {}".format(name, cfg["desc"])]
    log.append("Python: {} ({})".format(sys.executable, sys.version.split()[0]))
    print("\n" + "=" * 78)
    print("{}  {}".format(name, cfg["desc"]))
    print("=" * 78)

    # ---- 1. BridgeStudio 建模（唯一数据源） ----
    spec = build_beam_bridge(**cfg["kwargs"])
    if cfg.get("setup"):
        cfg["setup"](spec)
    errs = list(spec.validate_model() or [])
    if errs:
        raise RuntimeError("{} 模型校验失败: {}".format(name, errs))

    dat = case_dir / (stem + ".dat")
    model_json = case_dir / (stem + ".model.json")
    inp = case_dir / (stem + ".inp")
    dat.write_text(spec.to_dat(), encoding="utf-8")
    model_json.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    inp.write_text(spec_to_apdl(spec, jobname=stem, title=cfg["title"]), encoding="utf-8")
    print("  前处理输出 : {}  ({} 行)".format(dat.name, len(dat.read_text(encoding='utf-8').splitlines())))
    print("  模型快照   : {}".format(model_json.name))
    print("  ANSYS APDL : {}  ({} 行)".format(inp.name, len(inp.read_text(encoding='utf-8').splitlines())))

    # ANSYS 结果文件先清掉，避免读到上一次的残留
    csvs = [case_dir / (stem + s) for s in ("_disp.csv", "_reac.csv", "_elem.csv")]
    spring_csv = case_dir / (stem + "_spring.csv")
    if cfg.get("spring"):
        csvs.append(spring_csv)
    for p in csvs:
        if p.exists():
            p.unlink()

    if generate_only:
        (case_dir / "run_log.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
        print("  (--generate-only：跳过求解)")
        return True

    # ---- 2. FEM-Eigen 求解 ----
    env = os.environ.copy()
    env["PATH"] = MSYS_BIN + ";" + MSYS_USR + ";" + env.get("PATH", "")
    rc_fem = _run([FEM_EXE, dat.name], case_dir, env, 180, log, "FEM-Eigen")
    result = case_dir / (stem + ".result")
    if rc_fem != 0 or not result.exists():
        (case_dir / "run_log.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
        print("  [FAIL] FEM-Eigen 求解未产出 .result (rc={})".format(rc_fem))
        return False
    print("  FEM 结果   : {}  ({} 行)".format(result.name, len(result.read_text(encoding='utf-8').splitlines())))

    # ---- 3. ANSYS MAPDL 批处理求解 ----
    out_name = stem + "_ansys.out"
    rc_ansys = _run([ANSYS_EXE, "-b", "-j", stem, "-i", inp.name, "-o", out_name],
                    case_dir, env, 600, log, "ANSYS")
    missing = [p.name for p in csvs if not p.exists()]
    if rc_ansys != 0 or missing:
        ans_out = case_dir / out_name
        if ans_out.exists():
            txt = ans_out.read_text(encoding="utf-8", errors="replace")
            log.append("[ANSYS] 输出尾部:\n" + "\n".join(txt.splitlines()[-40:]))
        (case_dir / "run_log.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
        print("  [FAIL] ANSYS 未产出结果 CSV (rc={}, 缺失: {})".format(rc_ansys, missing))
        return False
    for p in csvs:
        print("  ANSYS 结果 : {}  ({} 行)".format(p.name, len(p.read_text(encoding='utf-8').splitlines())))

    # ---- 4. 三方对比 ----
    fem = parse_fem_result(result)
    adisp = parse_ansys_csv(csvs[0])
    areac = parse_ansys_csv(csvs[1])
    aelem = parse_ansys_csv(csvs[2])
    if cfg.get("mode") == "generic":
        aspring = parse_ansys_csv(spring_csv) if cfg.get("spring") else None
        report, ok = compare_generic(spec, fem, adisp, areac, aelem, aspring,
                                     stem=stem, note=cfg.get("note", ""))
    else:
        report, ok = compare(spec, fem, adisp, areac, aelem,
                             stem=stem, n_spans=cfg["n_spans"])
    (case_dir / "compare_report.txt").write_text(report, encoding="utf-8")
    print("\n".join(report.splitlines()[-6:]))

    # ---- 5. 归档清单 ----
    manifest = [
        "算例 {} : {}".format(name, cfg["desc"]),
        "生成时间: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "",
        "唯一数据源: BridgeSpec（BridgeStudio/bridge_spec.py::build_beam_bridge）",
        "  kwargs = {!r}".format(cfg["kwargs"]),
        "  setup  = {}".format(getattr(cfg.get("setup"), "__name__", "?") if cfg.get("setup") else "无"),
        "  工况   = {}".format(cfg.get("note", "等跨满布均布荷载（有闭式解）")),
        "",
        "文件清单:",
        "  {:<28s} BridgeStudio 前处理输出，FEM-Eigen .dat (new format)".format(stem + ".dat"),
        "  {:<28s} BridgeSpec 模型快照（JSON）".format(stem + ".model.json"),
        "  {:<28s} ANSYS MAPDL 命令流（与 .dat 同源）".format(stem + ".inp"),
        "  {:<28s} FEM-Eigen 求解结果".format(stem + ".result"),
        "  {:<28s} ANSYS 批处理输出（许可/求解日志）".format(out_name),
        "  {:<28s} ANSYS 节点位移 node UX UY ROTZ".format(stem + "_disp.csv"),
        "  {:<28s} ANSYS 节点反力 node FX FY".format(stem + "_reac.csv"),
        "  {:<28s} ANSYS 单元端力 elem NI VI MI NJ VJ MJ".format(stem + "_elem.csv"),
        *([("  {:<28s} ANSYS 接地弹簧力 node SMISC1".format(stem + "_spring.csv"))]
          if cfg.get("spring") else []),
        "  {:<28s} ANSYS 作业日志/数据库/结果文件".format(stem + ".log/.err/.rst 等"),
        "  {:<28s} FEM-Eigen / ANSYS / 闭式解 对比报告".format("compare_report.txt"),
        "  {:<28s} 本次运行的命令与退出码".format("run_log.txt"),
        "",
        "复现步骤:",
        "  1) C:\\Users\\user\\anaconda3\\python.exe " + str(Path(__file__).resolve()),
        "     --case " + name,
        "  2) 单独重跑 FEM-Eigen:",
        "     PATH 加入 " + MSYS_BIN + " 后执行",
        "     " + str(FEM_EXE) + " " + str(dat),
        "  3) 单独重跑 ANSYS（工作目录必须是本算例目录）:",
        "     " + str(ANSYS_EXE) + " -b -j " + stem + " -i " + inp.name + " -o " + out_name,
        "",
        "ANSYS 关键约定（本机 v180 / BEAM3 实测）:",
        "  ET,1,BEAM3 ; R,1,A,I,H ; SFBEAM,ELEM,1,PRES,+q,+q 产生向下荷载",
        "  SMISC 1/2/6 = i 端 N/V/M ; 7/8/12 = j 端 N/V/M",
        "  F,node,FY,-P 对应 *CLOAD ; ACEL,0,9.81,0 + MP,DENS 对应 *GRAVITY 0 -9.81",
        "  COMBIN14 + KEYOPT(2)=2 = 2D UY 接地弹簧，对应 *BOUNDARY ... K=<k>",
        "  *CFOPEN + *VWRITE 导出机器可读 CSV，无需人工拷贝列表",
    ]
    (case_dir / "manifest.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")
    (case_dir / "run_log.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="连续梁算例：BridgeStudio -> FEM-Eigen -> ANSYS 校核")
    ap.add_argument("--case", default="all", choices=["all", *CASES.keys()])
    ap.add_argument("--generate-only", action="store_true",
                    help="只生成前处理输出与 APDL，不调用求解器")
    args = ap.parse_args()
    names = list(CASES) if args.case == "all" else [args.case]
    results = {}
    for n in names:
        results[n] = run_case(n, args.generate_only)
    print("\n" + "=" * 78)
    for n, ok in results.items():
        print("  {} : {}".format(n, "PASS" if ok else "FAIL"))
    print("=" * 78)
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
