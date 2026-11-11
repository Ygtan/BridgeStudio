"""verify_length_2d.py —— 2D 平面索长闭合 [LENGTH2D]（第十三次）独立守卫。

背景（2026-11-11 第十三次，B2「首次真正新造 2D 力学」）：
只读调研确认 2D 内核此前连索长输入契约都没有；第九 / 十 / 十一次只是把
「内核已算、当场丢弃的量」补留痕 / 补段，本轮不同——FE 侧新造了
`*CABLE_LENGTH2D` 契约、`Model2D::Element::Lu` 独立无应力下料长度、
`fem_2d.cpp::iterateCableLength2D()`（无垂度线性杆本构 + 闭式初值
`Lu0=L*/(1+T*/EA)` + 中心有限差分 Jacobian + LM 阻尼最小二乘）、
`cable_length_output_2d.hpp::formatCableLengthSection2D()` 与
`example_mixed.exe --length-detail[-only]` 写出的独立 `[LENGTH2D]` 段；
BridgeStudio 只解析、不重算。

用户拍板口径：**只闭环 `[LENGTH2D]` 一层**，`[OPT]` 带约束目标索力优化
**仍不做、仍标 N、不造占位**；能力矩阵该格 `2D=N` → `2D=P`（**仍标 P 不标 Y**），
§4.2 变 `1 个 2D=N + 6 个 2D=P`，**B2 剩余仍 2 项**。2D 版不等于 3D 的
垂度 s(T) / Ernst 等效弹模 / 共转成桥态 / 闭式影响矩阵 `J=M·D` /
`[OPT]→[LENGTH]→[NEWTON]` 三层迭代；段里不写 SENS / NJ / REFACT / STAGE 占位。

本守卫锁住：
- FE 源码契约（符号在位、`Lu<=0` 默认分支逐位不变的表述、`-only` 在
  `writeResult` 之前 return、段名带 2D 后缀、段里不写占位、无契约写
  `ERROR=no_length_request`、默认回归用例不带开关）；
- FE 端到端实跑：三组零污染（默认与 `--length-detail` 的 `.result` 逐位一致 /
  `--length-detail-only` 不写 `.result` / 去契约段写 `ERROR=no_length_request`
  不摆空表）+ `verify_length_2d.dat` 闭式解 oracle；
- BS 契约（`LengthSpec2D` pydantic 边界、12 格 `DEFAULT_DAT_SHA`、
  `to_dat()` 带/不带契约逐位差只在新增段、`validate_model()` 四条中文拒绝、
  默认 `Solve2DResult.length`、`length_tables()` 默认空、model_doc 透传、app 控件）；
- 解析层正例与十余类负例，一律显式中文状态、不摆空表、不补零；
- 零污染（默认 HTML 两次写出逐字节一致、默认 HTML 不含索长章、
  `solve_2d` 默认不调 `--length-detail`）；
- 四出口同源（`res.length=ok` 出三张表且 CSV 自动收表、状态非 ok 不出表、
  HTML 与四出口同一份数字）；
- 两仓文档口径对拍（矩阵两格 / `1N+6P` / B2 剩余仍 2 项 / 第十三次闭环记录 /
  B 类规格第 11 条 / 三份 BS 文档登记 / FE CHANGELOG 新条目与基线不刷新 /
  `[OPT]` 仍 N / 仍标 P 不 Y / 不等于 3D 三层迭代）。

铁律：**不重算**（Lu / T* / N / 逐轮残差 / 有限差分 J 全部来自内核段，
Python 不解 `K·ΔU=R`、不由位移反推索力、不另求一组下料长度）、**不补零**
（缺段 / `ERROR=` / 字段不符 / 行数不符 / 闭式初值核对不过一律给中文状态且
三张表返回空 dict）、**不猜 T***（目标索力必须 .dat 显式给出，Python 不编）。
汇总按布尔位统计防「假绿」：`n_pass = sum(1 for _, ok, _ in PASS if ok)`，
`n_pass == len(PASS)` 才返回 0。
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
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import length_2d as L
import report_2d
from bridge_spec import (BridgeSpec, ElemKind, LengthSpec2D,
                         build_arch_bridge, build_beam_bridge)
from export_html import write_2d_html
from model_doc import BUILDERS, DEFAULTS, ModelDoc
from pydantic import ValidationError
from solve_2d import Solve2DResult, _fem_eigen_dir, solve_2d
from verify_stage_tension_3d import DEFAULT_DAT_SHA

FE = _fem_eigen_dir() or Path("D:/AI-work/FEM-Eigen")
OUT = ROOT / "out" / "verify_length_2d"
PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, bool(ok), str(detail)))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail else ""))


def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ---------------------------------------------------------------- 样本段
# 与 FE 实测段同构的手写夹具（只用于解析层测试，不是另算一遍力学）。
# 三条 I 行让 ERRLAST 与末轮 errMax 自洽、E 行让 Lu0 与闭式初值自洽，
# 从而 parse_length_txt 能走完「只核对同一批数字彼此一致」这条链。
HEAD_OK = "NCABLES=1 NITER=3 MAXITER=20 TOLT=1e-09 CONVERGED=1"
ERRFIRST_OK = "ERRFIRST=0.04 ERRLAST=3.830269435e-13"
I_LINES = [
    "I 1 0.04 0.04 0.001980198 1",
    "I 2 2.5e-8 2.5e-8 0.00198 1",
    "I 3 3.830269435e-13 3.830269435e-13 0 1",
]
E_LINE = "E 1 1000000 1000000 9.950248756 9.952229299 10 200000000"
NOTE_OK = "NOTE=2D 平面索长闭合过程量，只解析不重算；不构成规范合格判定。"


def seg(basis: str | None = "BASIS=2d_global",
        kind: str | None = "KIND=plane_cable_length STATUS=ok",
        errkind: str | None = ("ERRKIND=tension_rel ANALYSIS=LINEAR "
                               "JMETHOD=FINITE_DIFFERENCE"),
        head: str | None = HEAD_OK,
        errfirst: str | None = ERRFIRST_OK,
        steps: list[str] | None = None,
        cables: list[str] | None = None,
        note: str | None = NOTE_OK,
        error: str | None = None,
        extra: str | None = None) -> str:
    lines = ["[LENGTH2D]"]
    for x in (basis, kind, errkind, head, errfirst):
        if x is not None:
            lines.append(x)
    lines.extend(I_LINES if steps is None else steps)
    lines.extend([E_LINE] if cables is None else cables)
    if note is not None:
        lines.append(note)
    if extra is not None:
        lines.append(extra)
    if error is not None:
        lines.append("ERROR=" + error)
    return "\n".join(lines) + "\n"


SAMPLE_OK = seg()
# 无契约 / 首次求解失败两种内核拒绝段，形态照 cable_length_output_2d.hpp。
NO_CONTRACT_SEG = (
    "[LENGTH2D]\nBASIS=2d_global\n"
    "KIND=none STATUS=not_applicable\n"
    "NCABLES=0 NITER=0 MAXITER=20 TOLT=1e-06 CONVERGED=0\n"
    "ERROR=no_length_request\n"
    "NOTE=本次模型没有 *CABLE_LENGTH2D 目标索力契约；不输出零行冒充收敛，"
    "也不摆空表。\n"
)
SOLVE_FAILED_SEG = (
    "[LENGTH2D]\nBASIS=2d_global\n"
    "KIND=plane_cable_length STATUS=ok\n"
    "ERRKIND=tension_rel ANALYSIS=LINEAR JMETHOD=FINITE_DIFFERENCE\n"
    "ERROR=solve_failed\n"
    "NOTE=内核在第一次完整求解中失败，没有逐轮残差与成桥索力；"
    "不写 I/E 零行冒充过程量。\n"
)


# ---------------------------------------------------------------- [1] FE 源码

def fe_sources() -> None:
    print("\n[1] FE 源码契约")
    hpp = read(FE / "fem_2d.hpp")
    cpp = read(FE / "fem_2d.cpp")
    out_h = read(FE / "cable_length_output_2d.hpp")
    mixed = read(FE / "example_mixed.cpp")
    cmp_py = read(FE / "tests" / "regression" / "compare_regression.py")

    check("fem_2d.hpp 定义 Element::Lu 默认 0", "double Lu = 0.0;" in hpp)
    check("fem_2d.hpp 写明 Lu 未指定时本构逐位不变",
          "0 = 未指定" in hpp and "逐位不变" in hpp)
    check("fem_2d.hpp 定义 LengthTarget", "struct LengthTarget" in hpp)
    check("fem_2d.hpp 定义 LengthStep2D", "struct LengthStep2D" in hpp)
    check("fem_2d.hpp 定义 LengthCable2D", "struct LengthCable2D" in hpp)
    check("fem_2d.hpp 定义 LengthIterationResult2D",
          "struct LengthIterationResult2D" in hpp)
    check("fem_2d.hpp 声明 iterateCableLength2D",
          "LengthIterationResult2D iterateCableLength2D" in hpp)

    check("fem_2d.cpp 定义 axialRefLength2D / axialInitialStrain2D",
          "inline double axialRefLength2D" in cpp
          and "inline double axialInitialStrain2D" in cpp)
    check("Lu<=0 默认分支回落到几何弦长 Lgeom",
          "(e.Lu > 0.0) ? e.Lu : Lgeom;" in cpp)
    check("Lu<=0 默认分支回落到原 istrn",
          "if (e.Lu <= 0.0) return e.istrn;" in cpp)
    check("Lu<=0 默认分支逐位不变的表述在位",
          "Lu<=0" in cpp and "逐位不变" in cpp)
    check("fem_2d.cpp 实现 iterateCableLength2D",
          "LengthIterationResult2D iterateCableLength2D" in cpp)
    check("fem_2d.cpp 写明中心有限差分 Jacobian + LM",
          "有限差分" in cpp and "LM" in cpp)
    check("fem_2d.cpp 解析 *CABLE_LENGTH2D 段",
          '"*CABLE_LENGTH2D"' in cpp)
    check("fem_2d.cpp 无契约写 no_length_request",
          "no_length_request" in cpp)

    check("cable_length_output_2d.hpp 存在", bool(out_h))
    check("段头是独立 2D 段 [LENGTH2D]", "[LENGTH2D]" in out_h)
    check("段名带 2D 后缀（不撞 3D [LENGTH] 解析链）",
          "段名带 2D 后缀" in out_h)
    check("BASIS=2d_global", "BASIS=2d_global" in out_h)
    check("KIND=plane_cable_length", "plane_cable_length" in out_h)
    check("ERRKIND=tension_rel / JMETHOD=FINITE_DIFFERENCE",
          "ERRKIND=tension_rel" in out_h
          and "JMETHOD=FINITE_DIFFERENCE" in out_h)
    check("setprecision(10) 与 3D 同源", "setprecision(10)" in out_h)
    check("无契约写 KIND=none STATUS=not_applicable + ERROR=no_length_request",
          "KIND=none STATUS=not_applicable" in out_h
          and "no_length_request" in out_h)
    check("首次求解失败写 ERROR=solve_failed 不摆空表",
          "solve_failed" in out_h and "不写 I/E 零行" in out_h)
    out_lines = "\n".join(ln for ln in out_h.splitlines() if "ss <<" in ln)
    check("段里不写 SENS/NJ/REFACT/STAGE 占位行",
          not re.search(r"SENS|NJ=|REFACT|STAGE\d", out_lines))
    check("格式化层写明不写 SENS/NJ/J 与不设 STAGE 占位",
          "SENS/NJ/J" in out_h and "不设 STAGE 占位" in out_h)

    check("example_mixed 引入新输出层",
          '#include "cable_length_output_2d.hpp"' in mixed)
    check("example_mixed 有 --length-detail",
          '"--length-detail"' in mixed)
    check("example_mixed 有 --length-detail-only",
          '"--length-detail-only"' in mixed)
    check("opt-in 写独立 .length.txt",
          'lgPath += ".length.txt";' in mixed)
    check("opt-in 调 iterateCableLength2D",
          "iterateCableLength2D(model, solverType)" in mixed)
    check("-only 开关在 writeResult 之前 return",
          "if (lengthDetailOnly) return 0;" in mixed
          and mixed.find("if (lengthDetailOnly) return 0;")
          < mixed.find("writeResult(outPath, model, r, forces);"))
    check("默认回归用例不带新开关、不刷新基线",
          '"example_mixed.exe"' in cmp_py
          and "--length-detail" not in cmp_py
          and "verify_length_2d" not in cmp_py)


# ---------------------------------------------------------------- [2] FE 端到端

def fe_runtime() -> None:
    print("\n[2] FE 端到端零污染实跑 + 闭式解 oracle")
    exe = FE / "build" / "example_mixed.exe"
    if not exe.is_file():
        check("FE 端到端数值对拍", True,
              "跳过：未找到 example_mixed.exe，未做数值对拍；[1] 静态源码契约"
              "与 [3]~[7] 的 BS / 文档守卫仍逐条检查，不把跳过当作数值通过")
        return
    check("example_mixed.exe 已编译（FE 实跑前置）", exe.is_file(), str(exe))
    src = FE / "verify_length_2d.dat"
    check("闭式算例 verify_length_2d.dat 在位", src.is_file(), str(src))
    if not src.is_file():
        check("FE 端到端数值对拍", True,
              "跳过：verify_length_2d.dat 缺失，未做数值对拍；"
              "[1] 静态源码契约仍逐条检查")
        return

    # 全部临时文件放 TemporaryDirectory，退出即清理 .result / .length.txt / .dat，
    # 不污染两个仓库根目录。
    with tempfile.TemporaryDirectory(prefix="verify_length_2d_fe_") as td:
        work = Path(td)
        shutil.copy2(src, work / "vl2.dat")

        def run(args: list[str]) -> subprocess.CompletedProcess:
            return subprocess.run([str(exe)] + args, cwd=str(work),
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=120)

        for suffix in (".length.txt", ".result"):
            for f in work.glob("*" + suffix):
                f.unlink()

        rp = work / "vl2.result"
        lp = work / "vl2.length.txt"
        base = run(["vl2.dat"])
        check("默认运行 rc=0", base.returncode == 0, (base.stderr or "")[-120:])
        check("默认运行写 .result", rp.is_file())
        check("默认运行不产生 .length.txt", not lp.is_file())
        default_bytes = rp.read_bytes() if rp.is_file() else b""

        opt = run(["vl2.dat", "--length-detail"])
        check("--length-detail rc=0", opt.returncode == 0,
              (opt.stderr or "")[-120:])
        check("--length-detail 写 .length.txt", lp.is_file())
        check("零污染①：--length-detail 与默认 .result 逐位一致",
              rp.is_file() and rp.read_bytes() == default_bytes)
        txt = lp.read_text(encoding="utf-8", errors="replace") if lp.is_file() else ""

        # 只查独立段头行：NOTE 里「[OPT]→[LENGTH]→[NEWTON] 三层迭代」是
        # 边界说明文字，不是 3D 段头，不能被全文子串误判成串段。
        check("段头 [LENGTH2D] 且没有 3D 的 [LENGTH]/[OPT] 段头行",
              txt.startswith("[LENGTH2D]")
              and not any(s.strip() in ("[LENGTH]", "[OPT]")
                          for s in txt.splitlines()))
        check("段内 BASIS=2d_global", "BASIS=2d_global" in txt)
        check("段内 KIND=plane_cable_length STATUS=ok",
              "KIND=plane_cable_length STATUS=ok" in txt)
        check("段内 ERRKIND=tension_rel / ANALYSIS=LINEAR / "
              "JMETHOD=FINITE_DIFFERENCE",
              "ERRKIND=tension_rel" in txt
              and "ANALYSIS=LINEAR" in txt
              and "JMETHOD=FINITE_DIFFERENCE" in txt)
        check("段内实测头行 NCABLES=1 NITER=3 MAXITER=20 TOLT=1e-09 "
              "CONVERGED=1",
              "NCABLES=1 NITER=3 MAXITER=20 TOLT=1e-09 CONVERGED=1" in txt)
        check("段内不写 SENS/NJ/REFACT/STAGE 占位行",
              not any(re.match(r"^(SENS|NJ|REFACT|STAGE\d)", s.strip())
                      for s in txt.splitlines()))

        d = L.parse_length_txt(txt)
        check("BS 解析真实内核段 = ok", d.get("status") == "ok",
              str(d.get("status")) + " " + str(d.get("reason") or ""))
        check("解析出三张表", len(L.tables(d)) == 3)
        cables = d.get("cables") or []
        c = cables[0] if cables else {}
        check("实测 3 条 I 行 / 1 条 E 行",
              len(d.get("steps") or []) == 3 and len(cables) == 1)
        check("闭式 oracle：ERRFIRST = EA·istrn/T* = 0.04",
              math.isclose(d.get("errfirst", -1), 2e8 * 0.0002 / 1e6,
                           rel_tol=1e-6), str(d.get("errfirst")))
        check("闭式 oracle：Lu0 = L*/(1+T*/EA)",
              math.isclose(c.get("Lu0", -1), 10.0 / (1.0 + 1e6 / 2e8),
                           rel_tol=1e-6), str(c.get("Lu0")))
        check("闭式 oracle：Lu = L*/(1+T*/EA−istrn)",
              math.isclose(c.get("Lu", -1), 10.0 / (1.0 + 1e6 / 2e8 - 0.0002),
                           rel_tol=1e-6), str(c.get("Lu")))
        check("闭式 oracle：N = T* = 1e6 N",
              math.isclose(c.get("finalT", -1), 1e6, rel_tol=1e-6)
              and math.isclose(c.get("target", -1), 1e6, rel_tol=1e-6))
        check("闭式 oracle：ERRLAST 收敛到 3.830269435e-13",
              math.isclose(d.get("errlast", -1), 3.830269435e-13,
                           rel_tol=1e-6), str(d.get("errlast")))
        check("闭式 oracle：EA = 2e8 N、L* = 10 m",
              math.isclose(c.get("EA", -1), 2e8, rel_tol=1e-6)
              and math.isclose(c.get("Lgeom", -1), 10.0, rel_tol=1e-6))

        # 零污染②：--length-detail-only 求解后直接返回，不写 .result。
        rp.unlink(missing_ok=True)
        lp.unlink(missing_ok=True)
        only = run(["vl2.dat", "--length-detail-only"])
        check("--length-detail-only rc=0", only.returncode == 0,
              (only.stderr or "")[-120:])
        check("零污染②：--length-detail-only 不写 .result", not rp.is_file())
        check("--length-detail-only 写 .length.txt", lp.is_file())

        # 零污染③：去掉 *CABLE_LENGTH2D 契约段后，段如实写 no_length_request。
        raw_lines = (work / "vl2.dat").read_text(
            encoding="utf-8", errors="replace").splitlines()
        kept: list[str] = []
        skip = False
        for ln in raw_lines:
            if ln.strip() == "*CABLE_LENGTH2D":
                skip = True
                continue
            if skip:
                if ln.startswith("*") or ln.strip() == "*END":
                    skip = False
                else:
                    continue
            kept.append(ln)
        (work / "nocontract.dat").write_text("\n".join(kept) + "\n",
                                             encoding="utf-8")
        nl = work / "nocontract.length.txt"
        nl.unlink(missing_ok=True)
        nr = run(["nocontract.dat", "--length-detail-only"])
        check("去契约算例 rc=0", nr.returncode == 0,
              (nr.stderr or "")[-120:])
        nt = nl.read_text(encoding="utf-8", errors="replace") if nl.is_file() else ""
        check("零污染③：无契约写 KIND=none STATUS=not_applicable",
              "KIND=none STATUS=not_applicable" in nt)
        check("零污染③：无契约写 ERROR=no_length_request",
              "ERROR=no_length_request" in nt)
        check("零污染③：无契约不摆 I/E 行",
              not any(s.strip().startswith(("I ", "E "))
                      for s in nt.splitlines()))
        nd = L.parse_length_txt(nt)
        check("BS 解析无契约段 = no_contract",
              nd.get("status") == "no_contract", str(nd.get("status")))
        check("无契约段不摆空表", L.tables(nd) == {})


# ---------------------------------------------------------------- [3] BS 契约

def bs_contract() -> None:
    print("\n[3] BS 契约与双层 opt-in 默认值")
    src_s = read(ROOT / "solve_2d.py")
    src_d = read(ROOT / "model_doc.py")
    src_a = read(ROOT / "app.py")
    src_h = read(ROOT / "export_html.py")
    src_r = read(ROOT / "report_2d.py")
    src_b = read(ROOT / "bridge_spec.py")
    src_n = read(ROOT / "length_2d.py")

    check("口径层 SECTION 是 [LENGTH2D]", L.SECTION == "[LENGTH2D]")
    check("口径层 BASIS 是 2d_global", L.BASIS == "2d_global")
    check("口径层写明不重算 / 不补零 / 不由位移反推索力",
          "不重算" in src_n and "不补零" in src_n
          and "不由位移反推索力" in src_n)
    check("契约层 / 求解链写明不猜 T*",
          "不替设计猜目标索力" in src_b or "不猜 T*" in src_b
          or "不猜 T*" in src_s)
    check("口径层写明 [OPT] 仍标 N、不造占位",
          "[OPT]" in src_n and "仍标 N" in src_n and "不造占位" in src_n)
    check("口径层写明不等于 3D 三层迭代", "三层迭代" in src_n)
    check("口径层有 tables() 唯一取数口", "def tables(" in src_n)
    for st in ("not_requested", "no_contract", "kernel_error", "bad_header",
               "bad_basis", "bad_kind", "bad_row", "row_count_mismatch",
               "inconsistent_trace", "ok"):
        check(f"口径层状态字典覆盖 {st}", st in L.STATUS_TEXT)

    def rejects(fn) -> bool:
        try:
            fn()
            return False
        except (ValidationError, ValueError, TypeError):
            return True

    check("LengthSpec2D 默认 targets 为空",
          LengthSpec2D().targets == {})
    check("LengthSpec2D 默认 tol_t>0",
          LengthSpec2D().tol_t > 0)
    check("LengthSpec2D 默认 max_iter>=1",
          LengthSpec2D().max_iter >= 1)
    check("LengthSpec2D 拒绝 tol_t<=0",
          rejects(lambda: LengthSpec2D(tol_t=0.0)))
    check("LengthSpec2D 拒绝 max_iter<1",
          rejects(lambda: LengthSpec2D(max_iter=0)))
    check("LengthSpec2D.to_dat 写 *CABLE_LENGTH2D 段",
          "*CABLE_LENGTH2D" in LengthSpec2D(targets={1: 1e6}).to_dat())

    # to_dat() 带/不带契约逐位差只在新增段：把新增块整段摘掉后应与不带契约
    # 逐位相等（不进 DEFAULTS / FIELDS，默认 path 一个字节不多写）。
    spec = build_beam_bridge()
    without = spec.to_dat()
    with_spec = spec.model_copy(deep=True)
    with_spec.length_2d = LengthSpec2D(targets={1: 1e6}, tol_t=1e-9,
                                       max_iter=20)
    with_ = with_spec.to_dat()
    seg_txt = with_spec.length_2d.to_dat()
    comment = "! 2D 平面索长闭合目标索力契约（opt-in，默认不写）"
    wl = with_.splitlines()
    i = wl.index(comment)
    block_len = 1 + len(seg_txt.split("\n")) + 1  # 注释 + 契约多行 + 空行
    removed = wl[:i] + wl[i + block_len:]
    check("不带契约 to_dat 不含 *CABLE_LENGTH2D",
          "*CABLE_LENGTH2D" not in without)
    check("带契约 to_dat 恰含一段 *CABLE_LENGTH2D",
          with_.count("*CABLE_LENGTH2D") == 1)
    check("带/不带契约逐位差只在新增段",
          "\n".join(removed) + "\n" == without)
    check("to_dat 新段由 if length_2d is not None and targets 包住",
          "if self.length_2d is not None and self.length_2d.targets:" in src_b)

    # validate_model() 四条中文拒绝负例，合法 CABLE 目标不误报。
    arch = build_arch_bridge(hanger_kind=ElemKind.CABLE)

    def errs(lsp: LengthSpec2D) -> list[str]:
        s = arch.model_copy(deep=True)
        s.length_2d = lsp
        return s.validate_model()

    check("合法 CABLE 目标 validate_model 不报索长错",
          not any("2D 索长闭合" in e
                  for e in errs(LengthSpec2D(targets={201: 1e6}))))
    check("中文拒绝：契约没有给任何目标索力 T*",
          any("没有给任何目标索力" in e
              for e in errs(LengthSpec2D(targets={}))))
    check("中文拒绝：目标单元不存在",
          any("不存在" in e
              for e in errs(LengthSpec2D(targets={99999: 1e6}))))
    check("中文拒绝：目标单元必须是 CABLE",
          any("必须是 CABLE" in e
              for e in errs(LengthSpec2D(targets={1: 1e6}))))
    check("中文拒绝：目标索力 T* 必须大于 0",
          any("必须大于 0" in e
              for e in errs(LengthSpec2D(targets={201: -1.0}))))

    check("Solve2DResult 有 length 默认字段",
          "length: dict[str, Any]" in src_s)
    check("Solve2DResult 有 length_tables()",
          "def length_tables(" in src_s)
    check("solve_2d 有 length_detail 形参",
          "length_detail: bool = False" in src_s)
    check("solve_2d 调 --length-detail-only",
          '"--length-detail-only"' in src_s)
    check("solve_2d 无契约即 no_contract、不调内核",
          "no_contract" in src_s and "length_2d.SOURCE" in src_s)
    check("solve_2d 读前清残留 .length.txt",
          "lg_path.unlink()" in src_s)
    check("solve_2d 写明 [OPT] 仍没有对应力学",
          "[OPT]" in src_s and "仍标" in src_s)
    check("默认 Solve2DResult.length=not_requested",
          Solve2DResult().length.get("status") == "not_requested")
    check("默认 Solve2DResult.length_tables 为空",
          Solve2DResult().length_tables() == {})

    dsig = inspect.signature(ModelDoc.solve)
    check("ModelDoc.solve 有 length_2d_detail 且默认 False",
          dsig.parameters["length_2d_detail"].default is False)
    check("ModelDoc.solve 透传 length_detail",
          "length_detail=bool(length_2d_detail)" in src_d)
    check("model_doc.capability() 有 length2d 格",
          '"length2d"' in src_d)

    check("app.py 有 length2ddetail_opt 开关", "length2ddetail_opt" in src_a)
    check("app.py 有 length2d_cfg 契约输入", "length2d_cfg" in src_a)
    check("app.py 每次 rerun 清空 length_2d",
          "doc.model.length_2d = None" in src_a)
    check("app.py 构造 LengthSpec2D", "LengthSpec2D(" in src_a)
    check("app.py 传 length_2d_detail",
          "length_2d_detail=_length2d_on" in src_a)
    check("app.py stale 判断含 length2ddetail / length2dcfg",
          "length2ddetail" in src_a and "length2dcfg" in src_a)
    check("app.py 结果区出 length_tables 表", "length_tables" in src_a)

    check("export_html 有 length_html 章并接入 body",
          "length_html" in src_h and "{length_html}" in src_h)
    check("export_html 章只在非 not_requested 出",
          'if _lg.get("status") != "not_requested"' in src_h)
    check("report_2d import length_2d 且有 LENGTH_BOUNDARY_NOTE",
          "import length_2d" in src_r and "LENGTH_BOUNDARY_NOTE" in src_r)
    check("report_2d 有 ⑩ 索长章、诊断顺延 ⑪",
          "2D 平面索长闭合（无应力下料长度）" in src_r
          and "⑩" in src_r and "⑪" in src_r)
    check("report_2d 用 res.length_tables()",
          "res.length_tables()" in src_r)
    check("report_2d 不另写渲染器",
          "R.write_all(rep, out_dir, stem=stem, pdf=pdf)" in src_r)

    # 12 格默认 .dat 黄金哈希：本轮没有改任何 DEFAULTS 模型契约字段。
    for (kind, dim), builder in BUILDERS.items():
        dat = ModelDoc(kind, dict(DEFAULTS[builder]), dimension=dim).to_dat()
        h = hashlib.sha256(dat.encode()).hexdigest()[:16]
        check(f"12 格默认 .dat 黄金哈希：{builder}",
              h == DEFAULT_DAT_SHA[builder], h)
    check("DEFAULTS 不含 length_2d_detail 求解选项",
          "length_2d_detail" not in DEFAULTS)
    check("DEFAULTS 各格参数不写 length_2d 契约",
          all("length_2d" not in str(params) for params in DEFAULTS.values()))


# ---------------------------------------------------------------- [4] 解析正例 / 负例

def parser_cases() -> None:
    print("\n[4] 解析层正例与负例")
    d = L.parse_length_txt(SAMPLE_OK)
    check("正例 status=ok", d.get("status") == "ok", str(d.get("reason") or ""))
    check("正例 basis=2d_global", d.get("basis") == L.BASIS)
    check("正例 kind=plane_cable_length", d.get("kind") == "plane_cable_length")
    check("正例 errkind=tension_rel", d.get("errkind") == "tension_rel")
    check("正例 jmethod=FINITE_DIFFERENCE",
          d.get("jmethod") == "FINITE_DIFFERENCE")
    check("正例 ANALYSIS=LINEAR", d.get("analysis") == "LINEAR")
    check("正例 NCABLES=1 NITER=3 CONVERGED",
          d.get("ncables") == 1 and d.get("niter") == 3
          and d.get("converged") is True)
    check("正例 3 条 I 行 / 1 条 E 行",
          len(d.get("steps") or []) == 3
          and len(d.get("cables") or []) == 1)
    tabs = L.tables(d)
    check("正例出三张表",
          set(tabs) == {"length_2d_summary_table",
                        "length_2d_iter_table",
                        "length_2d_cable_table"})
    check("汇总表 2 列且含 CONVERGED=1",
          len(tabs["length_2d_summary_table"]["headers"]) == 2
          and any(r[0].startswith("收敛 CONVERGED") and r[1] == "1"
                  for r in tabs["length_2d_summary_table"]["rows"]))
    check("逐轮表 5 列 3 行",
          len(tabs["length_2d_iter_table"]["headers"]) == 5
          and len(tabs["length_2d_iter_table"]["rows"]) == 3)
    check("逐索表 8 列 1 行且数值来自内核段",
          len(tabs["length_2d_cable_table"]["headers"]) == 8
          and len(tabs["length_2d_cable_table"]["rows"]) == 1
          and tabs["length_2d_cable_table"]["rows"][0][0] == "1")
    check("表注写明能力边界 / [OPT] 仍 N",
          "[OPT]" in (tabs["length_2d_cable_table"]["note"] or ""))
    check("status_text(ok) 是中文且带轮数",
          "3 轮收敛" in L.status_text(d))

    def bad(name: str, text: str, want: str) -> None:
        r = L.parse_length_txt(text)
        check(name, r.get("status") == want,
              f"got={r.get('status')} want={want}")
        check(name + "（不摆空表）", L.tables(r) == {})

    bad("负例：空文本", "", "no_section")
    bad("负例：无 [LENGTH2D] 段", "[OTHER]\nX=1\n", "no_section")
    bad("负例：内核 ERROR=no_length_request", NO_CONTRACT_SEG, "no_contract")
    bad("负例：内核 ERROR=solve_failed", SOLVE_FAILED_SEG, "kernel_error")
    bad("负例：BASIS 不是 2d_global", seg(basis="BASIS=xyz"), "bad_basis")
    bad("负例：缺 BASIS", seg(basis=None), "bad_basis")
    bad("负例：ERRKIND 不是 tension_rel",
        seg(errkind="ERRKIND=geom_error ANALYSIS=LINEAR "
                    "JMETHOD=FINITE_DIFFERENCE"), "bad_kind")
    bad("负例：JMETHOD 不是 FINITE_DIFFERENCE",
        seg(errkind="ERRKIND=tension_rel ANALYSIS=LINEAR "
                    "JMETHOD=CLOSED_FORM"), "bad_kind")
    bad("负例：I 行列数不是 6",
        seg(steps=["I 1 0.04 0.04 0.001980198"]), "bad_row")
    bad("负例：E 行有非数值",
        seg(cables=["E 1 1000000 abc 9.950248756 9.952229299 10 200000000"]),
        "bad_row")
    bad("负例：E 行列数不是 8",
        seg(cables=["E 1 1000000 1000000 9.950248756 9.952229299 10"]),
        "bad_row")
    bad("负例：行数与 NITER/NCABLES 不符",
        seg(head=HEAD_OK.replace("NITER=3", "NITER=2")),
        "row_count_mismatch")
    bad("负例：改 ERRLAST 触发 inconsistent_trace",
        seg(errfirst="ERRFIRST=0.04 ERRLAST=1e-3"), "inconsistent_trace")
    bad("负例：改 Lu0 触发 inconsistent_trace",
        seg(cables=["E 1 1000000 1000000 9.0 9.952229299 10 200000000"]),
        "inconsistent_trace")
    bad("负例：改 N 触发 inconsistent_trace",
        seg(cables=["E 1 1000000 900000 9.950248756 9.952229299 10 200000000"]),
        "inconsistent_trace")
    bad("负例：缺 NCABLES/NITER/... 头行", seg(head=None), "bad_header")
    bad("负例：CONVERGED=0",
        seg(head=HEAD_OK.replace("CONVERGED=1", "CONVERGED=0")),
        "kernel_error")
    bad("负例：出现无法识别的行", seg(extra="JUNK 1 2 3"), "bad_row")
    bad("负例：ERRFIRST 非数值",
        seg(errfirst="ERRFIRST=abc ERRLAST=1e-3"), "bad_row")
    # 下一段头是正常的文件分隔，解析器应在此收尾，不误判成本段脏行。
    check("下一段头不污染本段解析",
          L.parse_length_txt(seg(extra="[END]")).get("status") == "ok")

    check("status_text(not_requested) 中文说明默认关闭",
          "默认关闭" in L.status_text({"status": "not_requested"}))
    check("status_text(no_contract) 说明没有显式契约",
          "没有显式写" in L.status_text({"status": "no_contract"}))
    check("status_text(kernel_error) 附内核理由",
          "内核信息" in L.status_text({"status": "kernel_error",
                                      "reason": "solve_failed"}))
    check("status_text(unknown) 不给表",
          "不给表" in L.status_text({"status": "nonsense"}))


# ---------------------------------------------------------------- [5] 零污染

def wiring_and_zero_pollution() -> None:
    print("\n[5] 默认零污染与 solve_2d 默认路径")
    OUT.mkdir(parents=True, exist_ok=True)
    spec = BridgeSpec()
    r0 = Solve2DResult()
    a = write_2d_html(r0, spec, OUT / "d0.html").read_bytes()
    b = write_2d_html(r0, spec, OUT / "d1.html").read_bytes()
    check("默认 2D HTML 两次写出逐字节一致", a == b, f"{len(a)}B")
    html0 = a.decode("utf-8")
    check("默认 2D HTML 不含索长章",
          "2D 平面索长闭合" not in html0 and "[LENGTH2D]" not in html0)
    rep0 = report_2d.build_2d_document(r0, spec)
    check("默认四出口 Report 无索长表",
          not any("length_2d" in k for k in rep0.tables))
    check("默认四出口 Report 无索长章",
          not any(blk.get("type") == "h1"
                  and "平面索长闭合" in str(blk.get("text"))
                  for blk in rep0.blocks))
    check("默认 diagnostics 无索长条目",
          not any("索长闭合" in str(x) or "[LENGTH2D]" in str(x)
                  for x in r0.diagnostics))
    check("默认 Solve2DResult.length=not_requested",
          r0.length.get("status") == "not_requested")
    check("默认 length_tables() 为空", r0.length_tables() == {})

    sig = inspect.signature(solve_2d)
    check("solve_2d length_detail 默认 False",
          sig.parameters["length_detail"].default is False)
    check("solve_2d 所有 opt-in 默认全 False",
          all(sig.parameters[k].default is False
              for k in ("modal", "strength", "modal_detail",
                        "loss_detail", "newton_detail",
                        "capacity", "length_detail")))

    # 真实跑一次默认 solve_2d：不带 length_detail 就不该产生 .length.txt。
    exe = FE / "build" / "example_mixed.exe"
    if not exe.is_file():
        check("默认 solve_2d 实跑", True,
              "跳过：未编译 example_mixed.exe，改由静态源码契约断言"
              "默认不调 --length-detail")
        return
    with tempfile.TemporaryDirectory(prefix="verify_length_2d_solve_") as td:
        dflt = Path(td)
        lin = build_beam_bridge(span=30.0, n_spans=3, udl_kN=20.0,
                                staged=False, q2_kN=10.0, lane=False)
        r1 = solve_2d(lin, out_dir=dflt, dat_name="m.dat")
        check("默认 solve_2d 不产生 .length.txt",
              not (dflt / "m.length.txt").is_file())
        check("默认 solve_2d length=not_requested",
              r1.length.get("status") == "not_requested")
        check("默认 solve_2d 不出索长表", r1.length_tables() == {})


# ---------------------------------------------------------------- [6] 四出口同源

def exports() -> None:
    print("\n[6] 四出口同源与 CSV 自动收表")
    d = L.parse_length_txt(SAMPLE_OK)
    r = Solve2DResult(length=d)
    spec = BridgeSpec()
    tabs = r.length_tables()
    check("Solve2DResult.length_tables 与口径层同源", tabs == L.tables(d))
    rep = report_2d.build_2d_document(r, spec)
    check("Report 含三张索长表",
          all(k in rep.tables for k in ("length_2d_summary_table",
                                        "length_2d_iter_table",
                                        "length_2d_cable_table")))
    check("Report 含索长 h1 章",
          any(blk.get("type") == "h1"
              and "平面索长闭合" in str(blk.get("text"))
              for blk in rep.blocks))
    check("Report 含边界 note（不等于 3D 三层迭代）",
          any("不等于 3D 的三层迭代" in str(blk.get("text"))
              for blk in rep.blocks))
    check("Report.figures 仍为空（不出图）", rep.figures == {})

    html = write_2d_html(r, spec, OUT / "optin.html").read_text(encoding="utf-8")
    check("HTML 出口有索长章",
          "2D 平面索长闭合（无应力下料长度）" in html)
    check("HTML 出口数值与表字典同源",
          "1000.0000" in html and "9.950249" in html and "0.04" in html)

    d4 = OUT / "four"
    d4.mkdir(parents=True, exist_ok=True)
    paths = report_2d.write_2d_all(rep, d4, stem="lg", pdf=False)
    check("四出口三件套落盘（pdf=False）",
          set(paths) == {"html", "docx", "zip"}, str(sorted(paths)))
    with zipfile.ZipFile(paths["zip"]) as z:
        names = z.namelist()
        check("CSV 自动收 length_2d_summary_table",
              any("length_2d_summary_table.csv" in n for n in names))
        check("CSV 自动收 length_2d_iter_table",
              any("length_2d_iter_table.csv" in n for n in names))
        check("CSV 自动收 length_2d_cable_table",
              any("length_2d_cable_table.csv" in n for n in names))
        cable_csv = next((z.read(n).decode("utf-8") for n in names
                          if "length_2d_cable_table.csv" in n), "")
        check("CSV 与表字典同源（表头）",
              all(h in cable_csv
                  for h in tabs["length_2d_cable_table"]["headers"][:3]))
        check("CSV 与表字典同源（数值）",
              "1000.0000" in cable_csv and "9.950249" in cable_csv)
    check("DOCX 出口非空", paths["docx"].stat().st_size > 0)
    check("HTML 与 CSV 同一份数字",
          "1000.0000" in html and "1000.0000" in cable_csv)

    # 状态非 ok：整章只出中文说明，不出任何表。
    rbad = Solve2DResult(length=L.parse_length_txt(NO_CONTRACT_SEG))
    check("状态非 ok 不出表", rbad.length_tables() == {})
    repbad = report_2d.build_2d_document(rbad, spec)
    check("状态非 ok Report 无索长表但有中文 note",
          not any("length_2d" in k for k in repbad.tables)
          and any("没有显式写" in str(blk.get("text"))
                  for blk in repbad.blocks))


# ---------------------------------------------------------------- [7] 文档对拍

def docs() -> None:
    print("\n[7] 两仓文档口径对拍")
    fe_log = read(FE / "CHANGELOG.md")
    fe_man = read(FE / "USER_MANUAL.md")
    fe_fmt = read(FE / "input_format.md")
    check("FE CHANGELOG 登记 [LENGTH2D] 第十三次新条目",
          "新增：2D 平面索长闭合 [LENGTH2D] 段" in fe_log
          and "第十三次" in fe_log)
    check("FE CHANGELOG 写明 37 个黄金基线不刷新",
          "基线不刷新" in fe_log and "CASES 不加该算例" in fe_log)
    check("FE CHANGELOG 写明默认输出逐位不变",
          "逐位不变" in fe_log)
    check("FE CHANGELOG 写明 [OPT] 仍 N、不造占位",
          "仍标 `2D=N`" in fe_log and "不造占位" in fe_log)
    check("FE CHANGELOG 写明不等于 3D 三层迭代",
          "三层迭代" in fe_log)
    check("FE USER_MANUAL 登记 --length-detail-only",
          "--length-detail-only" in fe_man)
    check("FE USER_MANUAL 登记 [LENGTH2D] 专章",
          "二维 opt-in `[LENGTH2D]`：平面索长闭合" in fe_man)
    check("FE input_format 登记 *CABLE_LENGTH2D 段",
          "`*CABLE_LENGTH2D`" in fe_fmt and "6.5" in fe_fmt)
    check("FE input_format 登记 --length-detail 输出",
          "--length-detail" in fe_fmt)

    mat = read(ROOT / "能力边界矩阵.md")
    spec_doc = read(ROOT / "B类后续项接入规格.md")
    rd = read(ROOT / "README.md")
    use = read(ROOT / "使用说明.md")
    guide = read(ROOT / "开发使用手册.md")
    length_rows = [ln for ln in mat.splitlines()
                   if ln.startswith("| 索长迭代")]
    check("矩阵索长格改 2D=P 不虚报 Y（3D 仍 Y）",
          len(length_rows) == 1 and "| P | Y |" in length_rows[0],
          str(length_rows))
    opt_rows = [ln for ln in mat.splitlines()
                if ln.startswith("| 目标索力优化")]
    check("矩阵 [OPT] 格仍标 2D=N（3D 仍 Y）",
          len(opt_rows) == 1 and "| N | Y |" in opt_rows[0], str(opt_rows))
    check("矩阵 §4.2 为 1 个 2D=N + 6 个 2D=P",
          "1 个 `2D=N`" in mat and "6 个 `2D=P`" in mat)
    check("矩阵仍写 B2 剩余仍 2 项", "B2 剩余仍 2 项" in mat)
    check("矩阵有第十三次闭环记录", "第十三次闭环记录" in mat)
    check("矩阵写明仍标 P 不标 Y", "仍标 `P` 不标 `Y`" in mat)
    check("矩阵写明不等于 3D 三层迭代",
          "不等于 3D" in mat and "三层迭代" in mat)

    check("B 类规格 §6 第 11 条登记 [LENGTH2D] 一层不虚报",
          "11. 第十三次 `[LENGTH2D]` 一层不虚报" in spec_doc)
    check("B 类规格第 11 条写明 1N+6P / B2 剩余 2 项 / 不等于 3D",
          "1 个 `2D=N` + 6 个 `2D=P`" in spec_doc
          and "B2 剩余仍 2 项" in spec_doc
          and "三层迭代" in spec_doc)
    check("B 类规格写明 [OPT] 仍 2D=N、仍标 P 不 Y",
          "`[OPT]` 格仍 `2D=N`" in spec_doc and "不是 `Y`" in spec_doc)

    # 守卫名在矩阵依据列 / B 类规格 §6 / 开发手册三处登记；README 与
    # 使用说明以「第十三次索长闭合专章 / 表格行」登记同一次能力。
    check("三份文档登记 verify_length_2d.py 守卫名",
          all("verify_length_2d.py" in t for t in (mat, spec_doc, guide)))
    check("README 登记第十三次 [LENGTH2D] 索长闭合专章",
          "2D 平面索长闭合 `[LENGTH2D]`" in rd
          and "第十三次" in rd and "B2 剩余仍 2 项" in rd)
    check("使用说明 登记第十三次 [LENGTH2D] 能力",
          "[LENGTH2D]" in use and "第十三次" in use)
    check("开发手册 登记 [LENGTH2D] 与第十三次",
          "[LENGTH2D]" in guide and "第十三次" in guide)
    check("README 写明 [OPT] 仍 N / 仍 P 不 Y / 1N+6P",
          "`[OPT]` 仍标 `N`" in rd
          and "仍 `P` 不 `Y`" in rd
          and "1 个 `2D=N` + 6 个 `2D=P`" in rd)


def main() -> int:
    print("=" * 78)
    print("2D 平面索长闭合 [LENGTH2D] 守卫（B2 第十三次，纯离线 + 本地内核实跑）")
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
