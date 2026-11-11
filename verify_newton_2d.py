"""verify_newton_2d.py —— 2D 内层 Newton 逐迭代过程量（第十一次）独立守卫。

背景（2026-11-11 第十一次，B2「三层迭代过程量」）：
只读调研确认 2D 内核**只有**方案C 几何非线性 Newton 一层；第十三次起
`[LENGTH2D]` 平面索长闭合一层已按 opt-in 首次真正新造 2D 力学接通（索长格
`2D=N` → `2D=P`、仍标 `P` 不标 `Y`），`[OPT]` 带约束目标索力优化仍没有
对应力学、仍标 `2D=N`、不造占位。
用户拍板「只闭环 NEWTON」：新增 `NewtonStep2D` / `NewtonTrace2D` 把循环里
已经算出的量留痕（判据 / 步长 / 线搜索 / 收敛计算一字不改），由
`example_mixed.exe --newton-detail[-only]` 写独立 `[NEWTON2D]` 段；
BridgeStudio 只解析、不重算；本格仍标 `P` 不标 `Y`。

本守卫锁住：
- FE 源码契约（独立 2D 段 / BASIS=2d_global / 不写 REFACT 与阶段占位 /
  数值判据零改动 / 默认回归用例不带开关 / 段只在 opt-in 时写出）；
- FE 端到端数值对拍（默认 .result 逐位不变、only 开关不写 .result、
  逐迭代 S 行列数与收敛标志、线性模型 ERROR=）；
- BS 契约与双层 opt-in 默认值、12 格默认 .dat 黄金哈希；
- 解析层正例与 20 类负例，一律显式中文状态、不摆空表、不补零；
- 零污染（默认开关全关、默认 HTML 不出章、diagnostics 无新条目）；
- 接线与四出口同源（HTML / CSV 与表字典同一份数字）；
- 两仓文档口径对拍，含「索长格已标 P、`[OPT]` 仍标 N」不虚报。

铁律：逐迭代过程量只能来自内核段，Python 不解 K·ΔU=R、不由位移反推残差、
不补零；汇总按布尔位统计防「假绿」。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import hashlib
import inspect
import math
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import newton_2d as N
import report_2d
from bridge_spec import BridgeSpec, build_beam_bridge
from export_html import write_2d_html
from model_doc import BUILDERS, DEFAULTS, ModelDoc
from solve_2d import Solve2DResult, _fem_eigen_dir, solve_2d
from verify_stage_tension_3d import DEFAULT_DAT_SHA

FE = _fem_eigen_dir() or Path("D:/AI-work/FEM-Eigen")
OUT = ROOT / "out" / "verify_newton_2d"
PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail else ""))


def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ---------------------------------------------------------------- 样本段

STEP_LINES = [
    "S 1 1 1 2000 0.0005 2000 1 0 0",
    "S 1 2 1 0.2497499531 6.241410639e-08 2000 1 0 0",
    "S 1 3 1 1.180023901e-08 2.948955276e-15 2000 1 0 1",
]
HEAD_OK = ("NSTEP=1 NITER=3 MAXITER=50 TOL=1e-12 CONVERGED=1 "
           "RESFIRST=2000 RESLAST=2.955857781e-12 LINECUTS=0 "
           "STEPREFINE=0 LOADRATIO=1")
NOTE_OK = "NOTE=2D 内层 Newton 留痕；过程量不构成规范合格判定。"


def seg(head: str = HEAD_OK, steps: list[str] | None = None,
        basis: str = "BASIS=2d_global", kind: str = "KIND=newton_cr STATUS=ok",
        note: str = NOTE_OK, error: str | None = None,
        extra: str | None = None) -> str:
    lines = ["[NEWTON2D]"]
    if basis is not None:
        lines.append(basis)
    if kind is not None:
        lines.append(kind)
    if head is not None:
        lines.append(head)
    lines.extend(STEP_LINES if steps is None else steps)
    if note is not None:
        lines.append(note)
    if extra is not None:
        lines.append(extra)
    if error is not None:
        lines.append("ERROR=" + error)
    return "\n".join(lines) + "\n"


SAMPLE_OK = seg()
ERROR_SEG = ("[NEWTON2D]\nBASIS=2d_global\n"
             "KIND=none STATUS=not_applicable\n"
             "NSTEP=0 NITER=0 MAXITER=0 TOL=0 CONVERGED=0 RESFIRST=0 "
             "RESLAST=0 LINECUTS=0 STEPREFINE=0 LOADRATIO=0\n"
             "ERROR=no_newton_solution\nNOTE=线性模型没有 Newton 迭代。\n")


# ---------------------------------------------------------------- [1] FE 源码

def fe_sources() -> None:
    print("\n[1] FE 源码契约")
    hpp = read(FE / "fem_2d.hpp")
    cpp = read(FE / "fem_2d.cpp")
    out_h = read(FE / "newton_output_2d.hpp")
    mixed = read(FE / "example_mixed.cpp")
    cmp_py = read(FE / "tests" / "regression" / "compare_regression.py")

    check("fem_2d.hpp 定义 NewtonStep2D", "struct NewtonStep2D" in hpp)
    check("fem_2d.hpp 定义 NewtonTrace2D", "struct NewtonTrace2D" in hpp)
    check("ModelResult 挂 newtonTrace 字段", "NewtonTrace2D newtonTrace;" in hpp)
    check("注释写明留痕不参与判据/步长/收敛",
          "不参与任何判据" in hpp and "不设 refactor" in hpp)
    check("注释写明 2D 不照抄 3D 空列",
          "不把 3D 的列照抄成 0" in hpp)
    check("Newton 循环里 push_back 留痕",
          cpp.count("trace.steps.push_back(step);") >= 4,
          f"count={cpp.count('trace.steps.push_back(step);')}")
    check("循环里填 loadStep/iter/lambda",
          "step.loadStep = diag.loadSteps + 1;" in cpp
          and "step.lambda = lamTarget;" in cpp)
    check("循环里填 residual/ref",
          "step.residual = rFree;" in cpp and "step.ref = fFree;" in cpp)
    check("循环里填 alpha/lineCuts/du",
          "step.alpha = alpha;" in cpp
          and "step.lineSearchCuts = diag.lineSearchCuts - cutsBefore;" in cpp
          and "step.du = alpha * dU.norm();" in cpp)
    check("残差达标时 |du|=0 如实说明",
          "残差已达标：本步没有求解增量" in cpp)
    check("结果里回填 trace 头部",
          "result.newtonTrace = trace;" in cpp
          and "trace.loadSteps = diag.loadSteps;" in cpp)
    check("原收敛判据一字未改",
          "const double resRef = tol * std::max(1.0, fFree);" in cpp
          and "const double duOk = (alpha * dU.norm()) <= tol * std::max(1.0, U.norm());" in cpp)
    check("原线搜索判据一字未改",
          "if (m.nonlinearLineSearch && !(rBest < rFree))" in cpp)
    check("solveModel2D 分派未改签名",
          "if (m.analysisType == \"NONLINEAR\")" in cpp
          and "return solveModel2DNonlinear(m, type, U0, active0, diagOut, reuse);" in cpp)

    check("newton_output_2d.hpp 存在", bool(out_h))
    check("段头是独立 2D 段 [NEWTON2D]", "[NEWTON2D]" in out_h)
    check("BASIS=2d_global", "BASIS=2d_global" in out_h)
    check("setprecision(10) 与 3D 同源", "setprecision(10)" in out_h)
    check("无 Newton 迭代写 ERROR= 不摆空表",
          "ERROR=no_newton_solution" in out_h)
    check("格式化层不重算力学", "不重算力学" in out_h)
    check("格式化层写明不写 REFACT", "不写 REFACT" in out_h)
    check("格式化层写明过程量不构成规范判定",
          "不是规范意义上的设计合格判定" in out_h)
    check("S 行 9 个数值列",
          "S <loadStep> <iter> <lambda> <|R|> <|du|> <|Fext|> <alpha> <lineCuts> <converged>" in out_h)
    check("输出层不写 TOLF / TOLD / REFACT 键",
          not re.search(r'"\s*(TOLF|TOLD|REFACT)=', out_h))
    check("输出层不写阶段占位数据行",
          not re.search(r'"\s*STAGE\d+', out_h))

    check("example_mixed 引入新输出层",
          '#include "newton_output_2d.hpp"' in mixed)
    check("example_mixed 有 --newton-detail",
          '"--newton-detail"' in mixed)
    check("example_mixed 有 --newton-detail-only",
          '"--newton-detail-only"' in mixed)
    check("opt-in 写独立 .newton.txt",
          'ntPath += ".newton.txt";' in mixed)
    check("only 开关在 writeResult 前返回",
          "if (newtonDetailOnly) return 0;" in mixed
          and mixed.find("if (newtonDetailOnly) return 0;")
          < mixed.find("writeResult(outPath, model, r, forces);"))
    check("默认回归用例不带新开关",
          '"example_mixed.exe"' in cmp_py
          and "--newton-detail" not in cmp_py)
    base = read(FE / "tests" / "regression" / "baselines" / "verify_prestress_loss.result")
    check("黄金基线不含 [NEWTON2D]", "[NEWTON2D]" not in base)


# ---------------------------------------------------------------- [2] FE 端到端

def fe_runtime() -> None:
    print("\n[2] FE 端到端数值对拍")
    exe = FE / "build" / "example_mixed.exe"
    check("example_mixed.exe 已编译", exe.is_file(), str(exe))
    if not exe.is_file():
        return
    work = OUT / "fe"
    work.mkdir(parents=True, exist_ok=True)

    src_nl = FE / "verify_cable_nonlinear.dat"
    src_lin = FE / "verify_prestress_loss.dat"
    check("非线性算例在位", src_nl.is_file())
    check("线性算例在位", src_lin.is_file())
    if not (src_nl.is_file() and src_lin.is_file()):
        return
    shutil.copy2(src_nl, work / "nl.dat")
    shutil.copy2(src_lin, work / "lin.dat")

    def run(args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run([str(exe)] + args, cwd=str(work),
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)

    for suffix in (".newton.txt",):
        for f in work.glob("*" + suffix):
            f.unlink()

    base = run(["nl.dat"])
    check("默认运行 rc=0", base.returncode == 0, (base.stderr or "")[-120:])
    result_path = work / "nl.result"
    nt_path = work / "nl.newton.txt"
    check("默认运行写 .result", result_path.is_file())
    check("默认运行不产生 .newton.txt", not nt_path.is_file())
    default_bytes = result_path.read_bytes() if result_path.is_file() else b""

    opt = run(["nl.dat", "--newton-detail"])
    check("--newton-detail rc=0", opt.returncode == 0, (opt.stderr or "")[-120:])
    check("--newton-detail 写 .newton.txt", nt_path.is_file())
    check("opt-in 与默认 .result 逐位一致",
          result_path.is_file() and result_path.read_bytes() == default_bytes)
    txt = nt_path.read_text(encoding="utf-8", errors="replace") if nt_path.is_file() else ""
    check("段头 [NEWTON2D]", txt.startswith("[NEWTON2D]"))
    check("段内 BASIS=2d_global", "BASIS=2d_global" in txt)
    check("段内 STATUS=ok", "KIND=newton_cr STATUS=ok" in txt)
    check("段内不写 REFACT / TOLF / TOLD",
          not re.search(r"\b(REFACT|TOLF|TOLD)=", txt))
    check("段内不写阶段占位数据行",
          not any(re.match(r"^STAGE\d+", s.strip()) for s in txt.splitlines()))
    check("段内不写 3D 的 [LENGTH]/[OPT] 段",
          "[LENGTH]" not in txt and "[OPT]" not in txt)
    head = re.search(r"^NSTEP=.*$", txt, re.M)
    check("头行 10 个键齐全",
          bool(head) and all(k in head.group(0) for k in N.HEAD_KEYS))
    s_lines = [s for s in txt.splitlines() if s.startswith("S ")]
    check("S 行至少 1 行", len(s_lines) >= 1, f"n={len(s_lines)}")
    check("每条 S 行 10 列", all(len(s.split()) == 10 for s in s_lines))
    if head and s_lines:
        niter = int(re.search(r"NITER=(\d+)", head.group(0)).group(1))
        check("NITER 与 S 行数一致", niter == len(s_lines), f"{niter}/{len(s_lines)}")
        nstep = int(re.search(r"NSTEP=(\d+)", head.group(0)).group(1))
        check("S 行荷载步序号不超过 NSTEP",
              all(int(s.split()[1]) <= nstep for s in s_lines))
    check("最后一条 S 行 converged=1",
          bool(s_lines) and s_lines[-1].split()[-1] == "1")
    res_vals = [float(s.split()[4]) for s in s_lines]
    check("残差序列最终下降且非负",
          all(v >= 0.0 for v in res_vals)
          and (len(res_vals) < 2 or res_vals[-1] <= res_vals[0]))
    check("所有数值有限",
          all(math.isfinite(float(v)) for s in s_lines for v in s.split()[1:]))

    parsed = N.parse_newton_txt(txt)
    check("BS 解析真实内核段 = ok", parsed.get("status") == "ok",
          str(parsed.get("status")) + " " + str(parsed.get("reason") or ""))
    check("解析出两张表", len(N.tables(parsed)) == 2)
    check("汇总表状态与内核一致",
          parsed.get("head", {}).get("CONVERGED") == 1
          and parsed.get("status_seg") == "ok")

    # --newton-detail-only：求解但不写 .result，供有施工阶段的主结果旁路调用。
    result_path.unlink(missing_ok=True)
    nt_path.unlink(missing_ok=True)
    only = run(["nl.dat", "--newton-detail-only"])
    check("--newton-detail-only rc=0", only.returncode == 0,
          (only.stderr or "")[-120:])
    check("--newton-detail-only 不写 .result", not result_path.is_file())
    check("--newton-detail-only 写 .newton.txt", nt_path.is_file())

    # 线性模型：内核如实写 ERROR，不用零行冒充收敛。
    lin_nt = work / "lin.newton.txt"
    lin_nt.unlink(missing_ok=True)
    lin = run(["lin.dat", "--newton-detail-only"])
    check("线性算例 rc=0", lin.returncode == 0, (lin.stderr or "")[-120:])
    lin_txt = lin_nt.read_text(encoding="utf-8", errors="replace") if lin_nt.is_file() else ""
    check("线性算例写 ERROR=no_newton_solution",
          "ERROR=no_newton_solution" in lin_txt)
    check("线性算例不写任何 S 行",
          not any(s.startswith("S ") for s in lin_txt.splitlines()))
    check("BS 解析线性段 = kernel_error",
          N.parse_newton_txt(lin_txt).get("status") == "kernel_error")
    check("线性段不摆空表", N.tables(N.parse_newton_txt(lin_txt)) == {})


# ---------------------------------------------------------------- [3] BS 契约

def bs_contract() -> None:
    print("\n[3] BS 契约与双层 opt-in 默认值")
    src_n = read(ROOT / "newton_2d.py")
    src_s = read(ROOT / "solve_2d.py")
    src_d = read(ROOT / "model_doc.py")
    src_a = read(ROOT / "app.py")
    src_h = read(ROOT / "export_html.py")
    src_r = read(ROOT / "report_2d.py")

    check("口径层 SECTION 是 [NEWTON2D]", N.SECTION == "[NEWTON2D]")
    check("口径层 BASIS 是 2d_global", N.BASIS == "2d_global")
    check("口径层写明不重算", "不重算" in src_n)
    check("口径层写明 [LENGTH2D] 已独立接入、[OPT] 仍没有对应力学",
          "[LENGTH2D]" in src_n and "[OPT]" in src_n
          and "没有对应力学" in src_n and "不重算" in src_n)
    check("第十三次口径：[LENGTH2D] 一层已接通、[OPT] 仍缺",
          "第十三次起 `[LENGTH2D]` 平面索长闭合一层已按 opt-in 新造接入" in src_s
          and "带约束目标索力优化仍没有对应力学" in src_s
          and "第十三次起 [LENGTH2D] 平面索长闭合一层已由独立 opt-in 章接入" in src_r
          and "仍没有对应力学" in src_r)
    check("口径层写明过程量不构成规范判定",
          "不是规范意义上的设计合格判定" in src_n)
    check("口径层有 tables() 唯一取数口", "def tables(" in src_n)
    check("口径层状态字典覆盖 not_requested",
          "not_requested" in N.STATUS_TEXT)
    check("口径层状态字典覆盖 no_nonlinear_model",
          "no_nonlinear_model" in N.STATUS_TEXT)
    check("口径层状态字典覆盖 inconsistent_trace",
          "inconsistent_trace" in N.STATUS_TEXT)

    check("Solve2DResult 有 newton 默认字段",
          "newton: dict[str, Any]" in src_s)
    check("Solve2DResult 有 newton_tables()",
          "def newton_tables(" in src_s)
    check("solve_2d 有 newton_detail 形参",
          "newton_detail: bool = False" in src_s)
    check("solve_2d 调 --newton-detail-only",
          '"--newton-detail-only"' in src_s)
    check("solve_2d 线性模型不强制改分析类型",
          "不强制把模型改成非线性" in src_s)
    check("solve_2d 先清残留 .newton.txt",
          'nt_path.unlink()' in src_s)
    check("solve_2d 第七条链写明 [LENGTH2D] 已按 opt-in 新造接入 / [OPT] 仍缺",
          "第十三次起 `[LENGTH2D]` 平面索长闭合一层已按 opt-in 新造接入" in src_s
          and "带约束目标索力优化仍没有对应力学" in src_s
          and "不等于 3D 三层迭代过程量已移植" in src_s)

    sig = inspect.signature(solve_2d)
    check("solve_2d 四个 opt-in 默认全 False",
          all(sig.parameters[k].default is False
              for k in ("modal", "strength", "modal_detail",
                        "loss_detail", "newton_detail")))
    check("Solve2DResult 默认 newton=not_requested",
          Solve2DResult().newton.get("status") == "not_requested")
    check("Solve2DResult 默认 newton_tables 为空",
          Solve2DResult().newton_tables() == {})

    dsig = inspect.signature(ModelDoc.solve)
    check("ModelDoc.solve 有 newton_2d_detail 且默认 False",
          dsig.parameters["newton_2d_detail"].default is False)
    check("ModelDoc.solve 透传 newton_detail",
          "newton_detail=bool(newton_2d_detail)" in src_d)
    check("app.py 有 newton2ddetail_opt 开关",
          "newton2ddetail_opt" in src_a)
    check("app.py 传 newton_2d_detail",
          "newton_2d_detail=_newton2d_on" in src_a)
    check("app.py stale 判断含 newton2ddetail",
          "newton2ddetail" in src_a)
    check("app.py 结果区出 newton_tables 表",
          "newton_tables" in src_a)
    check("export_html 有 newton_html 章",
          "newton_html" in src_h and "{newton_html}" in src_h)
    check("export_html 章只在非 not_requested 出",
          'if _n.get("status") != "not_requested"' in src_h)
    check("report_2d 有 NEWTON_BOUNDARY_NOTE",
          "NEWTON_BOUNDARY_NOTE" in src_r)
    check("report_2d 有 ⑧ Newton 章",
          "2D 内层 Newton 逐迭代过程量" in src_r)
    check("report_2d 边界写明不等于 3D 三层迭代、[OPT] 仍缺",
          "不等于 3D 三层迭代过程量已移植" in src_r
          and "[OPT]" in src_r)
    check("report_2d 不另写渲染器",
          "R.write_all(rep, out_dir, stem=stem, pdf=pdf)" in src_r)

    # 12 格默认 .dat 黄金哈希：本轮没有改任何模型契约字段。
    for (kind, dim), builder in BUILDERS.items():
        dat = ModelDoc(kind, dict(DEFAULTS[builder]), dimension=dim).to_dat()
        h = hashlib.sha256(dat.encode()).hexdigest()[:16]
        check(f"12 格默认 .dat 黄金哈希：{builder}",
              h == DEFAULT_DAT_SHA[builder], h)
    check("DEFAULTS 不含 newton_2d_detail",
          "newton_2d_detail" not in DEFAULTS)


# ---------------------------------------------------------------- [4] 解析正例 / 负例

def parser_cases() -> None:
    print("\n[4] 解析层正例与负例")
    d = N.parse_newton_txt(SAMPLE_OK)
    check("正例 status=ok", d.get("status") == "ok", str(d.get("reason") or ""))
    check("正例 basis=2d_global", d.get("basis") == N.BASIS)
    check("正例 kind=newton_cr", d.get("kind") == "newton_cr")
    check("正例 3 条 S 行", len(d.get("steps") or []) == 3)
    check("正例 NITER=3", d.get("head", {}).get("NITER") == 3)
    tabs = N.tables(d)
    check("正例出两张表", set(tabs) == {"newton_summary_table", "newton_step_table"})
    st = tabs.get("newton_step_table") or {}
    check("逐迭代表 9 列表头", len(st.get("headers") or []) == 9)
    check("逐迭代表 3 行", len(st.get("rows") or []) == 3)
    check("逐迭代表数值来自内核段",
          bool(st.get("rows")) and st["rows"][0][3] == "2000")
    sm = tabs.get("newton_summary_table") or {}
    check("汇总表 3 列表头", len(sm.get("headers") or []) == 3)
    check("汇总表含 CONVERGED=1",
          any(r[0].startswith("整体收敛") and r[1] == "1"
              for r in sm.get("rows") or []))
    check("表注写明 2D 段不等于 3D 三层迭代、[LENGTH2D] 已接入、[OPT] 仍缺",
          "[LENGTH2D]" in (st.get("note") or "")
          and "[OPT]" in (st.get("note") or "")
          and "不等于 3D 三层迭代过程量已移植" in (st.get("note") or ""))
    check("status_text(ok) 是中文且带步数",
          "2 步" not in N.status_text(d) and "3 步" in N.status_text(d))

    def bad(name: str, text: str, want: str) -> None:
        r = N.parse_newton_txt(text)
        check(name, r.get("status") == want,
              f"got={r.get('status')} want={want}")
        check(name + "（不摆空表）", N.tables(r) == {})

    bad("负例：空文本", "", "no_section")
    bad("负例：无 [NEWTON2D] 段", "[OTHER]\nX=1\n", "no_section")
    bad("负例：内核 ERROR=", ERROR_SEG, "kernel_error")
    bad("负例：缺 BASIS", seg(basis=None), "bad_basis")
    bad("负例：BASIS 不是 2d_global", seg(basis="BASIS=xyz"), "bad_basis")
    bad("负例：缺 KIND/STATUS", seg(kind=None), "bad_header")
    bad("负例：缺头行", seg(head=None), "bad_header")
    bad("负例：头行缺键", seg(head="NSTEP=1 NITER=3 CONVERGED=1"), "bad_row")
    bad("负例：S 行列数少 1", seg(steps=["S 1 1 1 2000 0.0005 2000 1 0"]), "bad_row")
    bad("负例：S 行列数多 1",
        seg(steps=["S 1 1 1 2000 0.0005 2000 1 0 0 9"]), "bad_row")
    bad("负例：S 行有非数值",
        seg(steps=["S 1 1 1 abc 0.0005 2000 1 0 0"]), "bad_row")
    bad("负例：S 行 alpha>1",
        seg(steps=["S 1 1 1 2000 0.0005 2000 2 0 0"]), "bad_row")
    bad("负例：S 行 lambda>1",
        seg(steps=["S 1 1 2 2000 0.0005 2000 1 0 0"]), "bad_row")
    bad("负例：S 行 converged 非 0/1",
        seg(steps=["S 1 1 1 2000 0.0005 2000 1 0 2"]), "bad_row")
    bad("负例：S 行荷载步序号为 0",
        seg(steps=["S 0 1 1 2000 0.0005 2000 1 0 0"]), "bad_row")
    bad("负例：NITER 与 S 行数不符",
        seg(head=HEAD_OK.replace("NITER=3", "NITER=2")), "row_count_mismatch")
    bad("负例：NITER=0 无 S 行",
        seg(head=HEAD_OK.replace("NITER=3", "NITER=0"), steps=[]),
        "no_iteration")
    bad("负例：LINECUTS 与折半合计不符",
        seg(head=HEAD_OK.replace("LINECUTS=0", "LINECUTS=5")),
        "inconsistent_trace")
    bad("负例：荷载步序号超过 NSTEP",
        seg(steps=["S 2 1 1 2000 0.0005 2000 1 0 0",
                   "S 2 2 1 0.25 0.0005 2000 1 0 0",
                   "S 2 3 1 1e-08 1e-15 2000 1 0 1"]),
        "inconsistent_trace")
    bad("负例：CONVERGED 与 STATUS 不符",
        seg(head=HEAD_OK.replace("CONVERGED=1", "CONVERGED=0")),
        "inconsistent_trace")
    bad("负例：收敛但最后 S 行未收敛",
        seg(steps=[STEP_LINES[0], STEP_LINES[1],
                   "S 1 3 1 1.18e-08 2.9e-15 2000 1 0 0"]),
        "inconsistent_trace")
    bad("负例：收敛时 λ 与 LOADRATIO 不符",
        seg(head=HEAD_OK.replace("LOADRATIO=1", "LOADRATIO=0.5")),
        "inconsistent_trace")
    bad("负例：出现无法识别的行", seg(extra="JUNK 1 2 3"), "bad_row")
    # 下一段头是正常的文件分隔，解析器应在此收尾而不是误判成本段脏行。
    check("下一段头不污染本段解析",
          N.parse_newton_txt(seg(extra="[END]")).get("status") == "ok")

    check("status_text(not_requested) 中文说明默认关闭",
          "默认关闭" in N.status_text({"status": "not_requested"}))
    check("status_text(no_nonlinear_model) 不强制改分析类型",
          "不强制" in N.status_text({"status": "no_nonlinear_model"}))
    check("status_text(kernel_error) 附内核理由",
          "内核信息" in N.status_text({"status": "kernel_error",
                                      "reason": "no_newton_solution"}))
    check("status_text(unknown) 不给表",
          "不给表" in N.status_text({"status": "nonsense"}))


# ---------------------------------------------------------------- [5] 零污染

def wiring_and_zero_pollution() -> None:
    print("\n[5] 默认零污染与线性模型显式拒绝")
    OUT.mkdir(parents=True, exist_ok=True)
    spec = BridgeSpec()
    r0 = Solve2DResult()
    a = write_2d_html(r0, spec, OUT / "d0.html").read_bytes()
    b = write_2d_html(r0, spec, OUT / "d1.html").read_bytes()
    check("默认 2D HTML 两次写出逐字节一致", a == b, f"{len(a)}B")
    check("默认 2D HTML 不含 Newton 章",
          "2D 内层 Newton 逐迭代过程量" not in a.decode("utf-8"))
    check("默认 2D HTML 不含 [NEWTON2D]", "[NEWTON2D]" not in a.decode("utf-8"))
    rep0 = report_2d.build_2d_document(r0, spec)
    check("默认四出口 Report 无 Newton 表",
          not any("newton" in k for k in rep0.tables))
    check("默认四出口 Report 无 Newton 章",
          not any(b_.get("type") == "h1"
                  and "Newton" in str(b_.get("text"))
                  for b_ in rep0.blocks))
    check("默认 diagnostics 无 Newton 条目",
          not any("Newton" in str(x) for x in r0.diagnostics))

    # 线性模型 opt-in：不调内核、不写 .newton.txt、中文「不做」。
    lin_dir = OUT / "linear_optin"
    lin_dir.mkdir(parents=True, exist_ok=True)
    lin_spec = build_beam_bridge(span=30.0, n_spans=3, udl_kN=20.0,
                                 staged=False, q2_kN=10.0, lane=False)
    check("建造器默认 analysis=LINEAR", lin_spec.analysis == "LINEAR")
    r1 = solve_2d(lin_spec, out_dir=lin_dir, dat_name="m.dat",
                  newton_detail=True)
    check("线性 opt-in status=no_nonlinear_model",
          r1.newton.get("status") == "no_nonlinear_model",
          str(r1.newton.get("status")))
    check("线性 opt-in 不产生 .newton.txt",
          not (lin_dir / "m.newton.txt").is_file())
    check("线性 opt-in 不出表", r1.newton_tables() == {})
    check("线性 opt-in 有中文诊断",
          any("NONLINEAR" in str(x) for x in r1.diagnostics))
    h1 = write_2d_html(r1, lin_spec, OUT / "linear_optin.html").read_text(encoding="utf-8")
    check("线性 opt-in HTML 出章但写明不做",
          "2D 内层 Newton 逐迭代过程量" in h1 and "不强制" in h1)
    rep1 = report_2d.build_2d_document(r1, lin_spec)
    check("线性 opt-in Report 无表但有状态 note",
          not any("newton" in k for k in rep1.tables)
          and any("不强制" in str(b_.get("text")) for b_ in rep1.blocks))


# ---------------------------------------------------------------- [6] 四出口同源

def exports() -> None:
    print("\n[6] 四出口同源与 CSV 自动收表")
    d = N.parse_newton_txt(SAMPLE_OK)
    r = Solve2DResult(newton=d)
    spec = BridgeSpec()
    tabs = r.newton_tables()
    check("Solve2DResult.newton_tables 与口径层同源", tabs == N.tables(d))
    rep = report_2d.build_2d_document(r, spec)
    check("Report 含两张 Newton 表",
          all(k in rep.tables for k in ("newton_summary_table",
                                        "newton_step_table")))
    check("Report 含 Newton h1 章",
          any(b_.get("type") == "h1" and "Newton" in str(b_.get("text"))
              for b_ in rep.blocks))
    check("Report 含边界 note",
          any("不等于 3D 三层迭代过程量已移植" in str(b_.get("text"))
              for b_ in rep.blocks))
    check("Report.figures 仍为空（不出图）", rep.figures == {})

    html = write_2d_html(r, spec, OUT / "optin.html").read_text(encoding="utf-8")
    check("HTML 出口有 Newton 章", "2D 内层 Newton 逐迭代过程量" in html)
    check("HTML 出口逐迭代表数值同源", "2000" in html and "0.0005" in html)

    d4 = OUT / "four"
    d4.mkdir(parents=True, exist_ok=True)
    paths = report_2d.write_2d_all(rep, d4, stem="nt", pdf=False)
    check("四出口三件套落盘（pdf=False）",
          set(paths) == {"html", "docx", "zip"}, str(sorted(paths)))
    with zipfile.ZipFile(paths["zip"]) as z:
        names = z.namelist()
        check("CSV 自动收 newton_summary_table",
              any("newton_summary_table.csv" in n for n in names))
        check("CSV 自动收 newton_step_table",
              any("newton_step_table.csv" in n for n in names))
        step_csv = next((z.read(n).decode("utf-8") for n in names
                         if "newton_step_table.csv" in n), "")
        check("CSV 与表字典同源（表头）",
              all(h in step_csv for h in tabs["newton_step_table"]["headers"][:3]))
        check("CSV 与表字典同源（数值）", "2000" in step_csv and "0.0005" in step_csv)
    check("DOCX 出口非空", paths["docx"].stat().st_size > 0)


# ---------------------------------------------------------------- [7] 文档对拍

def docs() -> None:
    print("\n[7] 两仓文档口径对拍")
    fe_log = read(FE / "CHANGELOG.md")
    fe_man = read(FE / "USER_MANUAL.md")
    check("FE CHANGELOG 登记 [NEWTON2D] 第十一次",
          "[NEWTON2D]" in fe_log and "第十一次" in fe_log)
    check("FE USER_MANUAL 登记 --newton-detail-only",
          "--newton-detail-only" in fe_man)
    check("FE 文档写明默认输出逐位不变",
          "逐位不变" in fe_log or "逐位不变" in fe_man)
    check("FE 文档写明 [LENGTH2D] 已接入 / [OPT] 仍缺（第十一条历史原文仍在）",
          "新增：2D 平面索长闭合 [LENGTH2D] 段" in fe_log
          and "[LENGTH2D]" in fe_log and "[OPT]" in fe_log
          and "仍标 `2D=N`" in fe_log and "不造占位" in fe_log
          and "本轮不新造力学" in fe_log
          and "一字不改、仍为当时的事实记录" in fe_log)

    mat = read(ROOT / "能力边界矩阵.md")
    spec_doc = read(ROOT / "B类后续项接入规格.md")
    rd = read(ROOT / "README.md")
    use = read(ROOT / "使用说明.md")
    guide = read(ROOT / "开发使用手册.md")
    check("能力矩阵登记第十一次闭环",
          "第十一次" in mat and "[NEWTON2D]" in mat)
    newton_rows = [ln for ln in mat.splitlines()
                   if ln.startswith("| 内层 Newton")]
    check("矩阵 Newton 格改 2D=P 不虚报 Y",
          len(newton_rows) == 1 and "| P | Y |" in newton_rows[0],
          str(newton_rows))
    length_rows = [ln for ln in mat.splitlines()
                   if ln.startswith("| 索长迭代")]
    opt_rows = [ln for ln in mat.splitlines()
                if ln.startswith("| 目标索力优化")]
    check("矩阵索长格按第十三次标 2D=P（不虚报 Y）",
          len(length_rows) == 1 and "| P | Y |" in length_rows[0],
          str(length_rows))
    check("矩阵 OPT 格仍标 2D=N",
          len(opt_rows) == 1 and "| N | Y |" in opt_rows[0], str(opt_rows))
    check("矩阵写明 [LENGTH2D] 一层已接通、[OPT] 仍缺、不造占位",
          "[LENGTH2D]" in mat and "[OPT]" in mat
          and "不造占位" in mat and "没有对应力学" in mat)
    check("矩阵 §4.2 为 1 个 2D=N + 6 个 2D=P",
          "1 个 `2D=N`" in mat and "6 个 `2D=P`" in mat)
    check("矩阵仍写 B2 剩余 2 项（措辞收窄）",
          "B2 剩余 2 项" in mat)
    check("B 类规格登记第十一次与段名",
          "第十一次" in spec_doc and "[NEWTON2D]" in spec_doc)
    check("B 类规格写明 B2 剩余 2 项", "B2 剩余 2 项" in spec_doc)
    check("B 类规格写明索长格 P、[OPT] 仍 N、不造占位",
          "`[OPT]` 格仍 `2D=N`" in spec_doc
          and "不造占位" in spec_doc
          and "[LENGTH2D]" in spec_doc)
    for name, text in (("README", rd), ("使用说明", use), ("开发手册", guide)):
        check(f"{name} 登记 verify_newton_2d.py",
              "verify_newton_2d.py" in text)
    check("README 登记第十一次", "第十一次" in rd)
    check("开发手册回归清单含新守卫",
          "verify_newton_2d.py" in guide)
    check("矩阵守卫有第十一次防漂移断言",
          "[NEWTON2D]" in read(ROOT / "verify_capability_matrix.py"))
    check("B 类规格守卫有第十一次断言",
          "[NEWTON2D]" in read(ROOT / "verify_b_class_spec.py"))
    check("四出口守卫有第十一次断言",
          "[NEWTON2D]" in read(ROOT / "verify_report_2d.py"))
    # 2026-11-11 第十三次：[LENGTH2D] 一层按 opt-in 首次真正新造 2D 力学接通，
    # 索长格 N→P（仍 P 不 Y）；[OPT] 仍 N、不造占位。第十一次既有断言全部保留。
    check("能力矩阵登记第十三次闭环记录",
          "第十三次闭环记录" in mat)
    check("矩阵 [OPT] 格仍 | N | Y |（不虚报）",
          "| 目标索力优化 `[OPT]` + 体系守卫 | N | Y |" in mat)
    check("B 类规格 §6 第 11 条登记 [LENGTH2D] 一层不虚报",
          "11. 第十三次 `[LENGTH2D]` 一层不虚报" in spec_doc)
    check("独立守卫 verify_length_2d.py 在位且手册登记",
          (ROOT / "verify_length_2d.py").is_file()
          and "verify_length_2d.py" in guide)
    check("FE cable_length_output_2d.hpp 段名带 2D 后缀",
          "段名带 2D 后缀" in read(FE / "cable_length_output_2d.hpp")
          and "[LENGTH2D]" in read(FE / "cable_length_output_2d.hpp"))
    check("2D 段不等于 3D 三层迭代、[OPT] 不造占位",
          "三层迭代" in mat and "不等于 3D" in mat
          and "不造占位" in mat
          and "无垂度线性杆" in mat and "J=M·D" in mat)
    check("矩阵 / B 类规格 / 四出口守卫有第十三次防漂移断言",
          "[LENGTH2D]" in read(ROOT / "verify_capability_matrix.py")
          and "[LENGTH2D]" in read(ROOT / "verify_b_class_spec.py")
          and "[LENGTH2D]" in read(ROOT / "verify_report_2d.py"))


def main() -> int:
    print("=" * 78)
    print("2D 内层 Newton 逐迭代过程量段守卫（B2 第十一次，纯离线 + 本地内核实跑）")
    print("=" * 78)
    OUT.mkdir(parents=True, exist_ok=True)
    fe_sources()
    fe_runtime()
    bs_contract()
    parser_cases()
    wiring_and_zero_pollution()
    exports()
    docs()
    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(PASS)} 通过")
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
