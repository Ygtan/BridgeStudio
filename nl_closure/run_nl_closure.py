"""统一闭环编排：13 例技术句 → dat/inp → FEM → ANSYS → 逐点对拍（注册表驱动）。

用法（Anaconda Python，依赖已装齐）：
    C:\\Users\\user\\anaconda3\\python.exe nl_closure\\run_nl_closure.py --case all
    C:\\Users\\user\\anaconda3\\python.exe nl_closure\\run_nl_closure.py --case CB3D02
    C:\\Users\\user\\anaconda3\\python.exe nl_closure\\run_nl_closure.py --case all --no-ansys

链路（每步锚点，冻结证据只读）：
    1. desc_<case>.txt —— 参数齐全中文技术句（唯一输入）
    2. nl_pipeline.run_case() —— 解析 + 独立校验 + build + 同源导出
    3. dat/inp  ↔ cases/<folder> 逐字节
    4. FEM exe 真跑 → report/result ↔ 冻结逐字节
    5. ANSYS180 真跑 → CSV ↔ 冻结逐字节
    6. 专用 compare_*() 逐点对拍
所有产物写 nl_closure/out/<case>/；cases/ 零改动。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BS = HERE.parent
for p in (str(BS), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from case_registry import (CaseDef, FEM_MIXED, FEM_3D, ANSYS,  # noqa: E402
                           get, all_cases)
from nl_pipeline import run_case  # noqa: E402

from ansys_bridge import parse_fem_result, parse_ansys_csv, compare, compare_generic  # noqa: E402
from ansys_bridge_3d import (parse_fem_report_3d, parse_ansys_csv_3d,  # noqa: E402
                             compare_3d)
from ansys_bridge_3d_loads import compare_3d_loads  # noqa: E402
from ansys_bridge_cable import parse_fem_report_mixed, compare_mixed  # noqa: E402

MSYS_BIN = r"C:\msys64\mingw64\bin"
MSYS_USR = r"C:\msys64\usr\bin"
OUT = HERE / "out"

CHECKS: list[tuple[str, str, bool, str]] = []


def check(case: str, name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((case, name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    return ok


def same(a: Path, b: Path) -> bool:
    return a.exists() and b.exists() and a.read_bytes() == b.read_bytes()


def csv_same(a: Path, b: Path, zero_tol: float = 1.0e-6) -> tuple[bool, str]:
    """ANSYS CSV 比对：先逐字节；否则按数值 token 结构比对（容纳末位浮点非确定性）。

    ANSYS 的某些自由度 / 零反力节点残差（~1e-16 ~ 1e-8 量级）每次真跑末位不同，
    属数值零的舍入噪声，不是工程结果差异。判据：
      * 两个值都 |v| < zero_tol（相对工程力/位移量级为数值零）→ 视为一致；
      * 否则量级 > 1 用相对差 1e-9，量级 ≤ 1 用绝对差 1e-9。
    物理有效数字必须逐 token 一致。
    """
    if not (a.exists() and b.exists()):
        return False, "文件缺失"
    if a.read_bytes() == b.read_bytes():
        return True, "逐字节"
    la, lb = a.read_text(errors="replace").splitlines(), b.read_text(errors="replace").splitlines()
    if len(la) != len(lb):
        return False, f"行数 {len(la)} vs {len(lb)}"
    num = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")
    worst = 0.0
    for i, (x, y) in enumerate(zip(la, lb), 1):
        tx, ty = num.findall(x), num.findall(y)
        if len(tx) != len(ty):
            return False, f"第{i}行 token 数 {len(tx)} vs {len(ty)}"
        for u, v in zip(tx, ty):
            du, dv = float(u), float(v)
            d = abs(du - dv)
            worst = max(worst, d)
            # 两侧都是数值零（相对工程力/位移量级可忽略）→ 一致
            if abs(du) < zero_tol and abs(dv) < zero_tol:
                continue
            if max(abs(du), abs(dv)) > 1.0:
                if d / max(abs(du), abs(dv)) > 1.0e-9:
                    return False, f"第{i}行 {u} vs {v}"
            elif d > 1.0e-9:
                return False, f"第{i}行 {u} vs {v}"
    return True, f"容差(最大差 {worst:.2e})"


def _env() -> dict:
    e = os.environ.copy()
    e["PATH"] = MSYS_BIN + os.pathsep + MSYS_USR + os.pathsep + e.get("PATH", "")
    return e


def _run(cmd: list, cwd: Path, timeout: int = 600) -> int:
    try:
        p = subprocess.run([str(c) for c in cmd], cwd=str(cwd), env=_env(),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return p.returncode
    except Exception as exc:  # noqa: BLE001
        print(f"    EXCEPTION {exc}")
        return -1


def fem_exe(case: CaseDef) -> Path:
    return FEM_MIXED if case.chain == "2d_box" else FEM_3D


def fem_result_name(case: CaseDef) -> str:
    return case.stem + (".result" if case.chain == "2d_box" else ".report.txt")


def run_one(case: CaseDef, no_ansys: bool) -> bool:
    work = OUT / case.key
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    print("=" * 78)
    print(f"{case.key}  {case.title_en}  [{case.chain}]")
    print("=" * 78)

    # 1-3. 技术句 → 解析 → 建模 → 同源导出
    fields, model, files, problems = run_case(case, work)
    if problems:
        check(case.key, "技术句解析与参数校验", False, "; ".join(problems[:3]))
        return False
    check(case.key, "技术句字段 vs 冻结参数独立校验一致", True)
    for logical, p in files.items():
        check(case.key, f"同源导出逐字节 = 冻结 {p.name}", same(p, case.frozen(p.name)))

    # 4. FEM 真跑
    rc = _run([fem_exe(case), Path(files["dat"]).name], work)
    check(case.key, f"FEM {fem_exe(case).name} 退出码 0", rc == 0, f"rc={rc}")
    fname = fem_result_name(case)
    fres = work / fname
    check(case.key, f"FEM {fname} 逐字节 = 冻结", same(fres, case.frozen(fname)))

    # 5. ANSYS 真跑（box3d 两份 inp，其余一份）
    inp_jobs: list[tuple[Path, str]] = []
    if "inp_BEAM188" in files:
        inp_jobs = [(files["inp_BEAM188"], case.stem),
                    (files["inp_BEAM4"], case.stem + "_b4")]
    elif "inp" in files:
        inp_jobs = [(files["inp"], case.stem)]
    if not no_ansys:
        for inp, job in inp_jobs:
            rc = _run([ANSYS, "-b", "-j", job, "-i", inp.name,
                       "-o", job + "_ansys.out"], work)
            check(case.key, f"ANSYS180 ({job}) 退出码 0", rc == 0, f"rc={rc}")

    # 6. 收集 CSV：按冻结目录里 <stem>*.csv 全量核对逐字节
    frozen_csvs = sorted(case.case_dir.glob(case.stem + "*.csv"))
    for fc in frozen_csvs:
        live = work / fc.name
        if not no_ansys:
            ok, how = csv_same(live, fc)
            check(case.key, f"ANSYS 真跑 = 冻结 {fc.name} ({how})", ok)
        else:
            if fc.exists():
                shutil.copy(fc, live)
    # 对拍用 CSV 直接从 work 读（真跑或冻结拷贝）
    def csv(name: str) -> Path:
        p = work / name
        if not p.exists():
            p = case.frozen(name)
        return p

    # 7. 专用 compare 逐点对拍
    c = case.chain
    stem = case.stem
    if c == "2d_box":
        fem = parse_fem_result(fres)
        ad = parse_ansys_csv(csv(stem + "_disp.csv"))
        ar = parse_ansys_csv(csv(stem + "_reac.csv"))
        ae = parse_ansys_csv(csv(stem + "_elem.csv"))
        if case.use_generic_compare:
            asp = (parse_ansys_csv(csv(stem + "_spring.csv"))
                   if case.has_spring else None)
            text, ok = compare_generic(model, fem, ad, ar, ae, asp, stem=stem,
                                       note=case.title_en)
        else:
            text, ok = compare(model, fem, ad, ar, ae, stem=stem,
                               n_spans=case.n_spans_compare)
    elif c == "box3d":
        fem = parse_fem_report_3d(fres)
        b4d = parse_ansys_csv_3d(csv(stem + "_b4_disp.csv"))
        b4r = parse_ansys_csv_3d(csv(stem + "_b4_reac.csv"))
        b4e = parse_ansys_csv_3d(csv(stem + "_b4_elem.csv"))
        d188 = parse_ansys_csv_3d(csv(stem + "_disp.csv"))
        r188 = parse_ansys_csv_3d(csv(stem + "_reac.csv"))
        e188 = parse_ansys_csv_3d(csv(stem + "_elem.csv"))
        text, ok = compare_3d(model, fem, b4d, b4r, b4e, d188, r188, e188, stem=stem)
    elif c == "loads3d":
        fem = parse_fem_report_3d(fres)
        ad = parse_ansys_csv_3d(csv(stem + "_disp.csv"))
        ar = parse_ansys_csv_3d(csv(stem + "_reac.csv"))
        ae = parse_ansys_csv_3d(csv(stem + "_elem.csv"))
        text, ok = compare_3d_loads(model, fem, ad, ar, ae, stem=stem)
    elif c == "mixed":
        fem = parse_fem_report_mixed(fres)
        ad = parse_ansys_csv_3d(csv(stem + "_disp.csv"))
        ar = parse_ansys_csv_3d(csv(stem + "_reac.csv"))
        ab = parse_ansys_csv_3d(csv(stem + "_beam.csv"))
        al = parse_ansys_csv_3d(csv(stem + "_link.csv"))
        text, ok = compare_mixed(model, fem, ad, ar, ab, al, stem=stem,
                                 tower=case.tower)
    else:
        check(case.key, "未知链路", False, c)
        return False

    rpt = work / "compare_report.txt"
    rpt.write_text(text, encoding="utf-8")
    check(case.key, "FEM ↔ ANSYS 逐点对拍", ok)
    return all(o for k, n, o, d in CHECKS if k == case.key)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="all", help="算例 key 或 all（默认 all）")
    ap.add_argument("--no-ansys", action="store_true",
                    help="跳过 ANSYS 真跑（复制冻结 CSV 对拍）")
    args = ap.parse_args()

    if args.case.lower() == "all":
        cases = all_cases()
    else:
        cases = [get(k.strip()) for k in args.case.split(",")]

    print(f"自然语言 → dat/inp → FEM → ANSYS 闭环：{len(cases)} 例，"
          f"ANSYS {'跳过' if args.no_ansys else '真跑'}")
    results = {}
    for case in cases:
        try:
            results[case.key] = run_one(case, args.no_ansys)
        except Exception as exc:  # noqa: BLE001
            check(case.key, "闭环异常", False, f"{type(exc).__name__}: {exc}")
            results[case.key] = False

    print("=" * 78)
    print("逐例闭环汇总")
    print("=" * 78)
    for key, ok in results.items():
        n = sum(1 for k, _, o, _ in CHECKS if k == key and o)
        t = sum(1 for k, _, _, _ in CHECKS if k == key)
        print(f"  {'PASS' if ok else 'FAIL'}  {key:8s}  {n}/{t}")
    total = sum(1 for _, _, o, _ in CHECKS if o)
    grand = len(CHECKS)
    passed_cases = sum(1 for v in results.values() if v)
    print("=" * 78)
    print(f"总计：{total}/{grand} 检查通过，{passed_cases}/{len(cases)} 例闭环通过")
    print(f"产物：{OUT}")
    print("=" * 78)
    return 0 if all(results.values()) and total == grand else 1


if __name__ == "__main__":
    sys.exit(main())
