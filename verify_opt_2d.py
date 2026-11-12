"""verify_opt_2d.py —— 2D 平面索力优化 [OPT2D]（第十四次）独立守卫。

背景（2026-11-12 第十四次，B2「首次新造第二层 2D 力学」）：
第十三次只闭环 `[LENGTH2D]` 无应力下料长度一层；第十四次在其无垂度
线性杆本构之上再新造 `[OPT2D]`：显式索力箱 TMIN/TMAX + 用户显式登记的
BEAM 端弯矩 → MINMAXM（`min max|M|`），变量是无量纲名义索力 x=T/Tscale，
经 `Lu(x)=Lgeom/(1+Tscale*x/EA-istrn)` 落到本构，用中心有限差分
Jacobian + IRLS(p=2·2^r) + 投影梯度 / Armijo 求解。FE 侧新增
`*CABLE_OPT2D` 契约、`iterateCableOpt2D()`、
`cable_opt_output_2d.hpp::formatCableOptSection2D()` 与
`example_mixed.exe --opt-detail[-only]` 写出的独立 `[OPT2D]` 段；
BridgeStudio 只解析、不重算、不补零、不另求一组最优索力。

本守卫锁住 8 组（A~H）：
- A FE 源码契约（fem_2d.hpp / fem_2d.cpp / 输出层 / example_mixed / 两个
  oracle .dat / 回归 CASES 不刷新），含第七条链零污染；
- B FE 端到端实跑闭式 oracle（真实调 exe）、负例、三条默认零污染；
- C BS 唯一口径层 opt_2d.py 契约（SECTION/SOURCE/BOUNDARY_NOTE/STATUS_TEXT/
  HEADER_KEYS/四张表）；
- D parse_opt_txt() 正例（真实合法段全文）+ 负例段仍解析为 ok；
- E 解析负例 27 类（status != ok / tables() == {} / 状态名精确）+ 不串段；
- F BS 接线契约（bridge_spec / solve_2d / model_doc / app / export_html /
  report_2d）+ 默认 HTML 逐字节 + 12 格 DEFAULT_DAT_SHA；
- G 四出口同源（Report.tables / CSV / opt_tables()）；
- H 两仓文档口径对拍（第十四次当前陈述 / 历史条目仍在 / 统计数字未提前改）。

铁律：**不重算**（目标函数 / 归一化 / 有限差分 J / IRLS / 投影梯度 /
逐轮 objective·mpeak / 逐索 T0·Tstar·Nfinal·Lu / 梁端 M0·Mfinal 全部来自
内核段，Python 不解优化、不重算影响矩阵、不由位移反推弯矩、不另求最优索力）、
**不补零**（缺段 / ERROR= / 头行缺字段 / 口径不符 / 行数不符 / 段内矛盾
一律显式中文状态且四张表返回空 dict）、**不猜索力箱**（TMIN/TMAX 必须
.dat 显式给出，Python 不编）。
汇总按布尔位统计防「假绿」：`n_pass = sum(1 for _, ok, _ in PASS if ok)`，
`n_pass == len(PASS)` 才返回 0。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import hashlib
import math
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import opt_2d as L
import report_2d
from bridge_spec import BridgeSpec, OptSpec2D, build_beam_bridge
from export_html import write_2d_html
from model_doc import BUILDERS, DEFAULTS, ModelDoc
from pydantic import ValidationError
from solve_2d import Solve2DResult
from verify_stage_tension_3d import DEFAULT_DAT_SHA

FE = Path("D:/AI-work/FEM-Eigen")
OUT = ROOT / "out" / "verify_opt_2d"
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
# D 组正例：FE 实跑 `example_mixed.exe verify_opt_2d.dat --opt-detail-only`
# 写出的真实合法段全文（模块级常量，只用于解析层测试，不是另算一遍力学）。
H_KIND = "KIND=plane_cable_force_opt STATUS=ok"
H_OBJ = "OBJECTIVE=MINMAXM OBJECTIVE_KIND=NORMALIZED JMETHOD=FINITE_DIFFERENCE"
H_CNT = ("NCABLES=1 NMOMENTS=1 NITER=3 IRLS=2 MAXITER=120 "
         "IRLSMAX=4 TOL=1e-07 ROUNDTOL=1e-05")
H_SCL = ("TSCALE=1000000 MSCALE=1304347.826 CONVERGED=1 "
         "CONVBY=irls_round_stable APPLIED=1 REJECT=none")
H_BND = "TMIN=200000 TMAX=1500000 BND_SRC=USER"
H_MPK = "MPEAK_KIND=REGISTERED_BEAM_END"
H_MPV = "MPEAK0=1304347.826 MPEAK=260869.5652"
I_LINES = [
    "I 1 1 -0.4800000039 260869.5379 0.8",
    "I 1 2 -0.4800000039 260869.5379 0",
    "I 2 1 -0.4800000039 260869.5379 0",
]
T_LINE = "T 1 2 1000000 200000 25913.04348 4.975124378 4.995004995 5 200000000"
M_LINE = "M 1 1 1 1304347.826 260869.5652"
NOTE_LINE = "NOTE=2D 平面索力优化（第十四次新造力学）"

#: 内核 NOTE 的 5 条边界措辞（A 组输出层 / B 组实跑段共用同一组关键词）。
NOTE_5 = [
    ("不是 3D 的 J=M·D 闭式影响矩阵", ["不是 3D 的 J=M·D 闭式影响矩阵"]),
    ("2D 无 DECKLOAD / 塔顶 ΣT·cos / SYSGUARD / 不造占位",
     ["DECKLOAD", "ΣT·cos", "SYSGUARD", "不造占位"]),
    ("只 MINMAXM + 显式索力箱 TMIN/TMAX / BND_SRC=USER / 不做 MINENERGY",
     ["MINMAXM", "TMIN/TMAX", "BND_SRC=USER", "不做 MINENERGY"]),
    ("不等于 3D 三层迭代 + 命中 T* 属 [LENGTH2D]",
     ["三层迭代", "[LENGTH2D]"]),
    ("APPLIED=1 只是建议值 / T 行过程量 / 不写回 .dat",
     ["APPLIED=1", "过程量", "不写回 .dat"]),
]


def seg(*, basis: str | None = "BASIS=2d_global",
        kind: str | None = H_KIND, obj: str | None = H_OBJ,
        cnt: str | None = H_CNT, scl: str | None = H_SCL,
        bnd: str | None = H_BND, mpk: str | None = H_MPK,
        mpv: str | None = H_MPV, steps: list[str] | None = None,
        cables: list[str] | None = None, moments: list[str] | None = None,
        note: str | None = NOTE_LINE, extra: str | None = None,
        error: str | None = None) -> str:
    lines = ["[OPT2D]"]
    for x in (basis, kind, obj, cnt, scl, bnd, mpk, mpv):
        if x is not None:
            lines.append(x)
    lines.extend(I_LINES if steps is None else steps)
    lines.extend([T_LINE] if cables is None else cables)
    lines.extend([M_LINE] if moments is None else moments)
    if note is not None:
        lines.append(note)
    if extra is not None:
        lines.append(extra)
    if error is not None:
        lines.append("ERROR=" + error)
    return "\n".join(lines) + "\n"


SAMPLE_OK = seg()
# 负例段：APPLIED=0 / STATUS=objective_not_improved / REJECT 同码 /
# MPEAK>MPEAK0，段格式仍自洽，按当前设计仍应解析为 ok 并出四张表。
NEG_OK = seg(
    kind="KIND=plane_cable_force_opt STATUS=objective_not_improved",
    scl=H_SCL.replace("APPLIED=1 REJECT=none",
                      "APPLIED=0 REJECT=objective_not_improved"),
    mpv="MPEAK0=1304347.826 MPEAK=1565217.391",
)
NO_CONTRACT_SEG = (
    "[OPT2D]\nBASIS=2d_global\n"
    "KIND=none STATUS=not_applicable\n"
    "NCABLES=0 NMOMENTS=0 NITER=0\n"
    "ERROR=no_opt_request\n"
    "NOTE=本次模型没有 *CABLE_OPT2D 契约；2D 内核没有算过平面索力优化，"
    "不输出零行冒充收敛，也不摆空表。\n"
)

#: FE 实跑可能生成的临时产物（守卫结尾统一 unlink，不污染 FE 工作区）。
ARTIFACTS = (
    "verify_opt_2d.opt.txt", "verify_opt_2d.result",
    "verify_opt_2d_negative.opt.txt", "verify_opt_2d_negative.result",
    "verify_length_2d.opt.txt", "verify_length_2d.result",
)


# ---------------------------------------------------------------- [A] FE 源码

def fe_sources() -> None:
    print("\n[A] FE 源码契约（读文本，不重算）")
    hpp = read(FE / "fem_2d.hpp")
    cpp = read(FE / "fem_2d.cpp")
    out_h = read(FE / "cable_opt_output_2d.hpp")
    mixed = read(FE / "example_mixed.cpp")
    cmp_py = read(FE / "tests" / "regression" / "compare_regression.py")
    dpos = read(FE / "verify_opt_2d.dat")
    dneg = read(FE / "verify_opt_2d_negative.dat")

    # fem_2d.hpp 16 个声明（按 4 组逐符号断言）。
    check("fem_2d.hpp 声明 OptTarget/OptMoment/optTargets/optMoments",
          all(s in hpp for s in ("struct OptTarget", "struct OptMoment",
                                 "optTargets", "optMoments")))
    check("fem_2d.hpp 声明 optEnabled/optTMin/optTMax/optTol",
          all(s in hpp for s in ("optEnabled", "optTMin", "optTMax", "optTol")))
    check("fem_2d.hpp 声明 optMaxIter/optItersMax/optRoundTol/OptStep2D",
          all(s in hpp for s in ("optMaxIter", "optItersMax", "optRoundTol",
                                 "struct OptStep2D")))
    check("fem_2d.hpp 声明 OptCable2D/OptMoment2D/CableOptResult2D/iterateCableOpt2D",
          all(s in hpp for s in ("struct OptCable2D", "struct OptMoment2D",
                                 "struct CableOptResult2D",
                                 "CableOptResult2D iterateCableOpt2D")))

    # fem_2d.cpp 关键字。
    check("fem_2d.cpp 解析 *CABLE_OPT2D 段", '"*CABLE_OPT2D"' in cpp)
    check("fem_2d.cpp 实现 iterateCableOpt2D()",
          "CableOptResult2D iterateCableOpt2D" in cpp)
    check("fem_2d.cpp 写明 Lu(x)=Lgeom/(1+Tscale*x/EA-istrn) 映射",
          "Lu(x)=Lgeom/(1+Tscale*x/EA-istrn)" in cpp
          and "1.0 + Tscale * xk / EA[k] - istrn[k]" in cpp)
    check("fem_2d.cpp 写明中心有限差分 Jacobian",
          "中心有限差分 Jacobian" in cpp)
    check("fem_2d.cpp IRLS 权重 p=2·2^r",
          "2.0 * std::pow(2.0, r)" in cpp and "IRLS" in cpp)
    check("fem_2d.cpp 投影梯度 / Armijo",
          "投影梯度" in cpp and "Armijo" in cpp)
    check("fem_2d.cpp 状态关键字 no_opt_request / missing_tension_bounds / "
          "objective_not_improved 齐全",
          all(kw in cpp for kw in ("no_opt_request",
                                   "missing_tension_bounds",
                                   "objective_not_improved")))
    check("fem_2d.cpp APPLIED 判定（峰值是否改善）",
          'out.status = "objective_not_improved"' in cpp
          and "out.applied = true" in cpp and "out.applied = false" in cpp)

    # 第七条链零污染：第十三次的关键字面量仍在第十二次改写后完好。
    check("第七链 axialRefLength2D 仍在",
          "inline double axialRefLength2D" in cpp)
    check("第七链 axialInitialStrain2D 仍在",
          "inline double axialInitialStrain2D" in cpp)
    check("第七链 Lu<=0 默认分支逐位不变仍在",
          "(e.Lu > 0.0) ? e.Lu : Lgeom;" in cpp
          and "if (e.Lu <= 0.0) return e.istrn;" in cpp)
    check("第七链 iterateCableLength2D 仍在",
          "LengthIterationResult2D iterateCableLength2D" in cpp)
    check("第七链 writeResult 仍在主求解后",
          "writeResult(outPath, model, r, forces);" in mixed)

    # cable_opt_output_2d.hpp 输出层。
    check("cable_opt_output_2d.hpp 存在且定义 formatCableOptSection2D",
          bool(out_h) and "formatCableOptSection2D" in out_h)
    check("段头是独立 2D 段 [OPT2D]", "[OPT2D]" in out_h)
    check("输出 BASIS=2d_global / KIND=plane_cable_force_opt",
          "BASIS=2d_global" in out_h and "plane_cable_force_opt" in out_h)
    check("输出 OBJECTIVE=MINMAXM / NORMALIZED / FINITE_DIFFERENCE",
          "OBJECTIVE=MINMAXM" in out_h and "NORMALIZED" in out_h
          and "FINITE_DIFFERENCE" in out_h)
    check("输出 MPEAK_KIND=REGISTERED_BEAM_END / BND_SRC=USER",
          "MPEAK_KIND=REGISTERED_BEAM_END" in out_h
          and "BND_SRC=USER" in out_h)
    check("输出层 NOTE 含 5 条边界措辞",
          all(all(k in out_h for k in keys) for _, keys in NOTE_5))
    out_lines = "\n".join(ln for ln in out_h.splitlines() if "ss <<" in ln)
    forbidden = (r"SENS|NJ=|J=(?!M·D)|REFACT|geomErr|STAGE|DECKSHARE|"
                 r"SYSGUARD=|BAL=|TMAXSRC")
    check("段里不写 SENS/NJ=/J=/REFACT/geomErr/STAGE/DECKSHARE/"
          "SYSGUARD=/BAL=/TMAXSRC 作为写出字段",
          not re.search(forbidden, out_lines))

    # example_mixed.cpp opt-in 接线。
    check("example_mixed 引入 cable_opt_output_2d.hpp",
          '#include "cable_opt_output_2d.hpp"' in mixed)
    check("example_mixed 有 --opt-detail", '"--opt-detail"' in mixed)
    check("example_mixed 有 --opt-detail-only", '"--opt-detail-only"' in mixed)
    check("example_mixed 写 <stem>.opt.txt", 'opPath += ".opt.txt";' in mixed)
    check("example_mixed 调 iterateCableOpt2D",
          "iterateCableOpt2D(model, solverType)" in mixed)
    check("-only 在主求解之后、writeResult 之前 return",
          "if (optDetailOnly) return 0;" in mixed
          and mixed.find("timedSolve(model, solverType, solveMs)")
          < mixed.find("if (optDetailOnly) return 0;")
          < mixed.find("writeResult(outPath, model, r, forces);"))

    # 两个 oracle .dat + 回归 CASES 不刷新（37 个黄金基线）。
    check("verify_opt_2d.dat 在位且含 *CABLE_OPT2D / 显式 TMIN/TMAX / M 1 1",
          bool(dpos) and "*CABLE_OPT2D" in dpos
          and "TMIN=200000" in dpos and "TMAX=1500000" in dpos
          and "M 1 1" in dpos)
    check("verify_opt_2d_negative.dat 在位且含 *CABLE_OPT2D / 显式 TMIN/TMAX / M 1 1",
          bool(dneg) and "*CABLE_OPT2D" in dneg
          and "TMIN=1200000" in dneg and "TMAX=1500000" in dneg
          and "M 1 1" in dneg)
    check("两个 oracle 都不在 compare_regression.py 的 CASES（37 基线不刷新）",
          '"example_mixed.exe"' in cmp_py
          and "verify_opt_2d" not in cmp_py
          and "--opt-detail" not in cmp_py)


# ---------------------------------------------------------------- [B] FE 实跑

def fe_runtime() -> None:
    print("\n[B] FE 端到端实跑闭式 oracle + 三条默认零污染")
    exe = FE / "build" / "example_mixed.exe"

    def cleanup() -> None:
        for n in ARTIFACTS:
            (FE / n).unlink(missing_ok=True)

    def run(args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run([str(exe)] + args, cwd=str(FE),
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=120)

    cleanup()  # 实跑前先 unlink 残留
    try:
        check("example_mixed.exe 已编译（FE 实跑前置）", exe.is_file(), str(exe))
        check("闭式算例 verify_opt_2d.dat 在位",
              (FE / "verify_opt_2d.dat").is_file())

        # ---- 正例：--opt-detail-only 读真实 [OPT2D] 段。
        r = run(["verify_opt_2d.dat", "--opt-detail-only"])
        check("正例 --opt-detail-only rc=0", r.returncode == 0,
              (r.stderr or "")[-120:])
        op = FE / "verify_opt_2d.opt.txt"
        check("正例写 verify_opt_2d.opt.txt", op.is_file())
        check("正例 -only 不写 .result",
              not (FE / "verify_opt_2d.result").is_file())
        txt = op.read_text(encoding="utf-8", errors="replace") if op.is_file() else ""
        check("[OPT2D] 段存在且以段头起始", txt.startswith("[OPT2D]"))
        check("段内 BASIS / OBJECTIVE / OBJECTIVE_KIND / JMETHOD 口径",
              all(k in txt for k in ("BASIS=2d_global", "OBJECTIVE=MINMAXM",
                                     "OBJECTIVE_KIND=NORMALIZED",
                                     "JMETHOD=FINITE_DIFFERENCE")))
        check("段内 BND_SRC=USER / MPEAK_KIND=REGISTERED_BEAM_END",
              "BND_SRC=USER" in txt
              and "MPEAK_KIND=REGISTERED_BEAM_END" in txt)
        check("段内 CONVERGED=1 / APPLIED=1 / REJECT=none",
              "CONVERGED=1" in txt and "APPLIED=1" in txt
              and "REJECT=none" in txt)
        d = L.parse_opt_txt(txt)
        check("BS 解析真实内核段 = ok", d.get("status") == "ok",
              str(d.get("reason") or "") + " " + str(d.get("status")))
        cs = d.get("cables") or [{}]
        check("闭式 oracle：Tstar=200000=TMIN",
              math.isclose(d.get("tmin", -1), 200000, rel_tol=1e-9)
              and math.isclose(cs[0].get("Tstar", -1), 200000, rel_tol=1e-6),
              str(cs[0].get("Tstar")))
        check("闭式 oracle：MPEAK0≈1304347.826",
              math.isclose(d.get("mpeak0", -1), 1304347.826, rel_tol=1e-6),
              str(d.get("mpeak0")))
        check("闭式 oracle：MPEAK≈260869.5652",
              math.isclose(d.get("mpeak", -1), 260869.5652, rel_tol=1e-6),
              str(d.get("mpeak")))
        check("闭式 oracle：Lu≈5/(1+200000/2e8)",
              math.isclose(cs[0].get("Lu", -1), 5.0 / (1.0 + 200000 / 2e8),
                           rel_tol=1e-6), str(cs[0].get("Lu")))
        check("闭式 oracle：MPEAK < MPEAK0",
              d.get("mpeak", 0) < d.get("mpeak0", 0))
        check("I/T/M 行数与 NITER/NCABLES/NMOMENTS 相符",
              len(d.get("steps") or []) == d.get("niter") == 3
              and len(d.get("cables") or []) == d.get("ncables") == 1
              and len(d.get("moments") or []) == d.get("nmoments") == 1)
        check("实跑 NOTE 含 5 条边界措辞",
              all(all(k in txt for k in keys) for _, keys in NOTE_5))

        # ---- 负例：峰值不降，STATUS / APPLIED / REJECT 如实写出。
        rn = run(["verify_opt_2d_negative.dat", "--opt-detail-only"])
        check("负例 --opt-detail-only rc=0", rn.returncode == 0,
              (rn.stderr or "")[-120:])
        np_ = FE / "verify_opt_2d_negative.opt.txt"
        nt = np_.read_text(encoding="utf-8", errors="replace") if np_.is_file() else ""
        check("负例写 verify_opt_2d_negative.opt.txt", bool(nt))
        check("负例 STATUS=objective_not_improved",
              "STATUS=objective_not_improved" in nt)
        check("负例 APPLIED=0 与 REJECT=objective_not_improved",
              "APPLIED=0" in nt and "REJECT=objective_not_improved" in nt)
        nd = L.parse_opt_txt(nt)
        check("负例 MPEAK > MPEAK0",
              nd.get("mpeak", 0) > nd.get("mpeak0", 0))
        check("负例 T 行仍在",
              any(s.strip().startswith("T ") for s in nt.splitlines()))
        check("负例 NOTE 明确 T 行只是过程量 / 不能当设计索力",
              "T 行只是过程量" in nt and "不能当设计索力" in nt)

        # ---- 默认零污染三条（用既有 verify_length_2d.dat，无 *CABLE_OPT2D）。
        cleanup()
        rv = run(["verify_length_2d.dat"])
        check("零污染(a) 默认跑 rc=0", rv.returncode == 0)
        rp = FE / "verify_length_2d.result"
        check("零污染(a) 默认跑写 .result", rp.is_file())
        default_bytes = rp.read_bytes() if rp.is_file() else b""
        ro = run(["verify_length_2d.dat", "--opt-detail"])
        check("零污染(a) --opt-detail rc=0", ro.returncode == 0)
        check("零污染①：--opt-detail 与默认 .result 逐字节一致",
              rp.is_file() and rp.read_bytes() == default_bytes,
              f"{len(default_bytes)}B")
        lo = FE / "verify_length_2d.opt.txt"
        check("零污染：无契约 dat 的 .opt.txt 写 ERROR=no_opt_request",
              lo.is_file()
              and "ERROR=no_opt_request" in lo.read_text(encoding="utf-8",
                                                         errors="replace"))
        before = rp.read_bytes() if rp.is_file() else b""
        ronly = run(["verify_length_2d.dat", "--opt-detail-only"])
        check("零污染(b) --opt-detail-only rc=0", ronly.returncode == 0)
        check("零污染②：-only 不改写已存在的 .result（前后字节一致）",
              rp.is_file() and rp.read_bytes() == before)
        rp.unlink(missing_ok=True)
        run(["verify_length_2d.dat", "--opt-detail-only"])
        check("零污染②：-only 不新建 .result", not rp.is_file())
        check("零污染③：verify_length_2d.dat 文本没有 *CABLE_OPT2D",
              "*CABLE_OPT2D" not in read(FE / "verify_length_2d.dat"))
    finally:
        cleanup()
    check("FE 实跑临时产物已全部清理（无 .opt.txt / .result 残留）",
          all(not (FE / n).exists() for n in ARTIFACTS))


# ---------------------------------------------------------------- [C] BS 口径层

def bs_contract() -> None:
    print("\n[C] BS 唯一口径层 opt_2d.py 契约")
    src = read(ROOT / "opt_2d.py")
    check("SECTION=[OPT2D]", L.SECTION == "[OPT2D]")
    check("BASIS=2d_global", L.BASIS == "2d_global")
    check("SOURCE 含 --opt-detail-only 与 <stem>.opt.txt",
          "--opt-detail-only" in L.SOURCE and "<stem>.opt.txt" in L.SOURCE)
    check("段名带 2D 后缀、不与 3D [OPT] 串段",
          "段名带" in src and "不与 3D" in src and "串段" in src)
    check("解析层不收 3D [OPT] 段",
          L.parse_opt_txt("[OPT]\nBASIS=2d_global\n"
                          "KIND=plane_cable_force_opt STATUS=ok\n")
          .get("status") == "no_section")

    bn = L.BOUNDARY_NOTE
    check("BOUNDARY_NOTE① min max|M| 只显式登记 BEAM 端 / 不补零",
          all(k in bn for k in ("min max|M|", "显式登记的 BEAM 端弯矩",
                                "不补零")))
    check("BOUNDARY_NOTE② Lu(x)/中心有限差分/IRLS/投影梯度/不是 3D J=M·D",
          all(k in bn for k in ("Lu(x)", "中心有限差分 Jacobian", "IRLS",
                                "投影梯度", "不是 3D 的 J=M·D 闭式影响矩阵")))
    check("BOUNDARY_NOTE③ 只 MINMAXM + TMIN/TMAX + BND_SRC=USER / 不做 MINENERGY",
          all(k in bn for k in ("MINMAXM", "TMIN/TMAX", "BND_SRC=USER",
                                "不做 MINENERGY")))
    check("BOUNDARY_NOTE④ 2D 无 DECKLOAD / 塔顶 ΣT·cos / SYSGUARD / 不造占位",
          all(k in bn for k in ("DECKLOAD", "ΣT·cos", "SYSGUARD", "不造占位")))
    check("BOUNDARY_NOTE⑤ 不等于 3D 三层迭代",
          "不等于 3D" in bn and "三层迭代" in bn)
    check("BOUNDARY_NOTE⑥ 命中 T* 属 [LENGTH2D] / 不写回 .dat/.result / "
          "APPLIED=1 只是建议值 / T 行不能当设计索力",
          all(k in bn for k in ("[LENGTH2D]", ".dat/.result", "APPLIED=1",
                                "不能当设计索力")))
    check("BOUNDARY_NOTE⑦ 仍标 P 不标 Y / 不构成规范合格判定",
          "仍标 P 不标 Y" in bn and "不构成规范" in bn)

    check("STATUS_TEXT 至少 14 个状态键", len(L.STATUS_TEXT) >= 14,
          str(len(L.STATUS_TEXT)))
    check("STATUS_TEXT 值都是中文",
          all(any("\u4e00" <= ch <= "\u9fff" for ch in v)
              for v in L.STATUS_TEXT.values()))
    for st in ("kernel_error", "solver_error", "bad_row"):
        check(f"status_text({st}) 附「 内核信息：」",
              " 内核信息：" in L.status_text({"status": st, "reason": "内核拒绝"}))
    check("status_text(None) 不给表（不出表）",
          "不出表" in L.status_text(None) or "不给表" in L.status_text(None))

    check("HEADER_KEYS 22+ 键齐全且无重复",
          len(L.HEADER_KEYS) >= 22
          and len(set(L.HEADER_KEYS)) == len(L.HEADER_KEYS),
          str(len(L.HEADER_KEYS)))
    check("HEADER_KEYS 含全部 23 个必需头键",
          all(k in L.HEADER_KEYS for k in (
              "OBJECTIVE", "OBJECTIVE_KIND", "JMETHOD", "NCABLES", "NMOMENTS",
              "NITER", "IRLS", "MAXITER", "IRLSMAX", "TOL", "ROUNDTOL",
              "TSCALE", "MSCALE", "CONVERGED", "CONVBY", "APPLIED", "REJECT",
              "TMIN", "TMAX", "BND_SRC", "MPEAK_KIND", "MPEAK0", "MPEAK")))
    check("tables() 非 ok 返回 {}",
          L.tables({"status": "no_section"}) == {} and L.tables(None) == {})
    tabs = L.tables(L.parse_opt_txt(SAMPLE_OK))
    check("四张表键名齐全（opt_2d_*_table）",
          set(tabs) == {"opt_2d_summary_table", "opt_2d_iter_table",
                        "opt_2d_cable_table", "opt_2d_moment_table"})
    keys = ("opt_2d_summary_table", "opt_2d_iter_table",
            "opt_2d_cable_table", "opt_2d_moment_table")
    check("四张表都有 title/headers/rows/note（不是 columns）",
          all(all(f in tabs[t] for f in ("title", "headers", "rows", "note"))
              and "columns" not in tabs[t] for t in keys))
    check("四张表 note 都含 TABLE_NOTE + SOURCE + BOUNDARY_NOTE",
          all(L.TABLE_NOTE in tabs[k]["note"]
              and L.SOURCE in tabs[k]["note"]
              and L.BOUNDARY_NOTE in tabs[k]["note"] for k in keys))


# ---------------------------------------------------------------- [D] 解析正例

def parser_positive() -> None:
    print("\n[D] parse_opt_txt() 正例（FE 实跑真实合法段全文）")
    d = L.parse_opt_txt(SAMPLE_OK)
    check("正例 status=ok", d.get("status") == "ok", str(d.get("reason") or ""))
    check("正例 basis/kind/status_kernel",
          d.get("basis") == L.BASIS
          and d.get("kind") == "plane_cable_force_opt"
          and d.get("status_kernel") == "ok")
    check("正例 objective/objective_kind/jmethod",
          d.get("objective") == "MINMAXM"
          and d.get("objective_kind") == "NORMALIZED"
          and d.get("jmethod") == "FINITE_DIFFERENCE")
    check("正例 NCABLES/NMOMENTS/NITER/IRLS/MAXITER/IRLSMAX",
          d.get("ncables") == 1 and d.get("nmoments") == 1
          and d.get("niter") == 3 and d.get("irls") == 2
          and d.get("maxiter") == 120 and d.get("irlsmax") == 4)
    check("正例 tol/roundtol/tscale/mscale",
          math.isclose(d.get("tol", 0), 1e-7)
          and math.isclose(d.get("roundtol", 0), 1e-5)
          and math.isclose(d.get("tscale", 0), 1e6)
          and math.isclose(d.get("mscale", 0), 1304347.826, rel_tol=1e-6))
    check("正例 converged/convby/applied/reject",
          d.get("converged") == 1 and d.get("convby") == "irls_round_stable"
          and d.get("applied") == 1 and d.get("reject") == "none")
    check("正例 tmin/tmax/bnd_src/mpeak_kind",
          math.isclose(d.get("tmin", 0), 200000)
          and math.isclose(d.get("tmax", 0), 1500000)
          and d.get("bnd_src") == "USER"
          and d.get("mpeak_kind") == "REGISTERED_BEAM_END")
    check("正例 mpeak0/mpeak",
          math.isclose(d.get("mpeak0", 0), 1304347.826, rel_tol=1e-6)
          and math.isclose(d.get("mpeak", 0), 260869.5652, rel_tol=1e-6))
    check("正例 source", d.get("source") == L.SOURCE)
    steps = d.get("steps") or []
    cables = d.get("cables") or []
    moments = d.get("moments") or []
    check("正例 3 条 I 行且 irls/iter 自洽",
          len(steps) == 3 and steps[0]["irls"] == 1
          and steps[2]["irls"] == 2)
    check("正例 1 条 T 行且数值来自内核段",
          len(cables) == 1
          and math.isclose(cables[0]["Tstar"], 200000, rel_tol=1e-6)
          and math.isclose(cables[0]["Lu"], 4.995004995, rel_tol=1e-6))
    check("正例 1 条 M 行 end=1",
          len(moments) == 1 and moments[0]["end"] == 1
          and math.isclose(moments[0]["Mfinal"], 260869.5652, rel_tol=1e-6))
    tabs = L.tables(d)
    check("正例 tables() 四张表齐备",
          set(tabs) == {"opt_2d_summary_table", "opt_2d_iter_table",
                        "opt_2d_cable_table", "opt_2d_moment_table"})
    check("汇总表 2 列且含 CONVERGED=1 / APPLIED=1",
          len(tabs["opt_2d_summary_table"]["headers"]) == 2
          and any(r[0].startswith("收敛 CONVERGED") and r[1] == "1"
                  for r in tabs["opt_2d_summary_table"]["rows"]))
    check("逐轮表 5 列 3 行",
          len(tabs["opt_2d_iter_table"]["headers"]) == 5
          and len(tabs["opt_2d_iter_table"]["rows"]) == 3)
    check("逐索表 9 列 1 行",
          len(tabs["opt_2d_cable_table"]["headers"]) == 9
          and len(tabs["opt_2d_cable_table"]["rows"]) == 1)
    check("梁端表 5 列 1 行",
          len(tabs["opt_2d_moment_table"]["headers"]) == 5
          and len(tabs["opt_2d_moment_table"]["rows"]) == 1)
    check("status_text(ok) 中文且带根数/轮数",
          "1 根 CABLE" in L.status_text(d) and "3 轮迭代" in L.status_text(d))

    # 负例段：APPLIED=0 / objective_not_improved / MPEAK>MPEAK0 仍解析为 ok。
    nd = L.parse_opt_txt(NEG_OK)
    check("负例段（APPLIED=0/objective_not_improved）仍解析为 ok",
          nd.get("status") == "ok", str(nd.get("reason") or ""))
    check("负例段 tables() 仍 4 张表", len(L.tables(nd)) == 4)
    check("负例段 applied==0", nd.get("applied") == 0)
    check("负例段 reject 原样保留",
          nd.get("reject") == "objective_not_improved")


# ---------------------------------------------------------------- [E] 解析负例

def parser_negative() -> None:
    print("\n[E] 解析负例（27 类：status != ok / tables()=={} / 状态名精确）")

    def bad(name: str, text: str | None, want: str) -> None:
        r = L.parse_opt_txt(text)
        check(name, r.get("status") == want,
              f"got={r.get('status')} want={want}")
        check(name + "（非 ok）", r.get("status") != "ok")
        check(name + "（不摆空表）", L.tables(r) == {})

    bad("负例：None", None, "no_section")
    bad("负例：空串", "", "no_section")
    bad("负例：无 [OPT2D] 段", "[OTHER]\nX=1\n", "no_section")
    bad("负例：只有段头", "[OPT2D]\n", "bad_header")
    bad("负例：内核 ERROR=no_opt_request", NO_CONTRACT_SEG, "no_contract")
    bad("负例：KIND=none",
        seg(kind="KIND=none STATUS=not_applicable"), "no_contract")
    bad("负例：ERROR=missing_tension_bounds",
        seg(error="missing_tension_bounds"), "kernel_error")
    bad("负例：STATUS=invalid_contract",
        seg(kind="KIND=plane_cable_force_opt STATUS=invalid_contract"),
        "kernel_error")
    bad("负例：STATUS=solve_failed",
        seg(kind="KIND=plane_cable_force_opt STATUS=solve_failed"),
        "kernel_error")
    bad("负例：缺 OBJECTIVE 头键", seg(obj=None), "bad_header")
    bad("负例：BASIS=2d_local", seg(basis="BASIS=2d_local"), "bad_basis")
    bad("负例：OBJECTIVE=MINENERGY",
        seg(obj="OBJECTIVE=MINENERGY OBJECTIVE_KIND=NORMALIZED "
                "JMETHOD=FINITE_DIFFERENCE"), "bad_kind")
    bad("负例：OBJECTIVE_KIND=RAW",
        seg(obj="OBJECTIVE=MINMAXM OBJECTIVE_KIND=RAW "
                "JMETHOD=FINITE_DIFFERENCE"), "bad_kind")
    bad("负例：JMETHOD=CLOSED_FORM",
        seg(obj="OBJECTIVE=MINMAXM OBJECTIVE_KIND=NORMALIZED "
                "JMETHOD=CLOSED_FORM"), "bad_kind")
    bad("负例：BND_SRC=RIGID",
        seg(bnd="TMIN=200000 TMAX=1500000 BND_SRC=RIGID"), "bad_kind")
    bad("负例：MPEAK_KIND=GIRDEREND",
        seg(mpk="MPEAK_KIND=GIRDEREND"), "bad_kind")
    bad("负例：头行非数值",
        seg(cnt="NCABLES=1 NMOMENTS=1 NITER=abc IRLS=2 MAXITER=120 "
                "IRLSMAX=4 TOL=1e-07 ROUNDTOL=1e-05"), "bad_header")
    bad("负例：I 行列数错",
        seg(steps=["I 1 1 -0.48 260869.5379"]), "bad_row")
    bad("负例：I 行非数值",
        seg(steps=["I 1 1 abc 260869.5379 0.8"] + I_LINES[1:]), "bad_row")
    bad("负例：T 行列数错",
        seg(cables=["T 1 2 1000000 200000"]), "bad_row")
    bad("负例：T 行非数值",
        seg(cables=["T 1 2 1000000 200000 25913.04348 4.975124378 "
                    "4.995004995 5 abc"]), "bad_row")
    bad("负例：M 行 end=3",
        seg(moments=["M 1 1 3 1304347.826 260869.5652"]), "bad_row")
    bad("负例：删一行 I 导致 NITER 不符",
        seg(steps=I_LINES[:2]), "row_count_mismatch")
    bad("负例：删 T 行", seg(cables=[]), "row_count_mismatch")
    bad("负例：删 M 行", seg(moments=[]), "row_count_mismatch")
    bad("负例：CONVERGED=1 CONVBY=none",
        seg(scl=H_SCL.replace("CONVBY=irls_round_stable", "CONVBY=none")),
        "inconsistent_trace")
    bad("负例：APPLIED=1 REJECT=objective_not_improved",
        seg(scl=H_SCL.replace("REJECT=none",
                              "REJECT=objective_not_improved")),
        "inconsistent_trace")

    # 段外行不串段：下一段头是文件分隔，解析器应 break，不收别人的行。
    x = L.parse_opt_txt(seg(extra="[OTHER]\nX 1 2 3"))
    check("段外 [OTHER] 段不串段（解析仍 ok）", x.get("status") == "ok")
    check("段外行不收进本段（I/T/M 行数不变）",
          len(x.get("steps") or []) == 3
          and len(x.get("cables") or []) == 1
          and len(x.get("moments") or []) == 1)


# ---------------------------------------------------------------- [F] BS 接线

def wiring() -> None:
    print("\n[F] BS 接线契约 + 默认零污染 + 12 格 DEFAULT_DAT_SHA")
    src_b = read(ROOT / "bridge_spec.py")
    src_s = read(ROOT / "solve_2d.py")
    src_d = read(ROOT / "model_doc.py")
    src_a = read(ROOT / "app.py")
    src_h = read(ROOT / "export_html.py")
    src_r = read(ROOT / "report_2d.py")

    check("bridge_spec 定义 OptSpec2D", "class OptSpec2D(BaseModel):" in src_b)
    check("OptSpec2D 字段 targets/moments/t_min/t_max/tol/max_iter/irls/round_tol",
          all(k in src_b for k in (
              "targets: dict[int, float]", "moments: list[tuple[int, int]]",
              "t_min: float", "t_max: float", "tol: float = 1e-7",
              "max_iter: int = 120", "irls: int = 4",
              "round_tol: float = 1e-5")))
    check("OptSpec2D 校验 0<t_min<t_max 中文拒绝",
          "if not (0.0 < self.t_min < self.t_max):" in src_b
          and "0 < TMIN < TMAX" in src_b)
    check("BridgeSpec.opt_2d 默认 None",
          "opt_2d: OptSpec2D | None = None" in src_b)
    check("to_dat() 条件写入契约",
          "if self.opt_2d is not None and self.opt_2d.targets:" in src_b)
    check("validate_model() 补中文校验",
          "2D 索力优化契约 *CABLE_OPT2D 没有给任何 CABLE 初始名义索力 T0" in src_b
          and "必须是 CABLE" in src_b and "必须是 BEAM" in src_b)

    def rejects(fn) -> bool:
        try:
            fn()
            return False
        except (ValidationError, ValueError, TypeError):
            return True

    check("OptSpec2D 拒绝 t_min>=t_max",
          rejects(lambda: OptSpec2D(t_min=1500000, t_max=200000)))
    check("OptSpec2D 拒绝 T0<=0",
          rejects(lambda: OptSpec2D(t_min=200000, t_max=1500000,
                                    targets={1: -1.0})))

    # to_dat() 带/不带契约逐位差只在新增段（默认不多写一个字节）。
    spec = build_beam_bridge()
    without = spec.to_dat()
    ws = spec.model_copy(deep=True)
    ws.opt_2d = OptSpec2D(t_min=200000, t_max=1500000,
                          targets={1: 1e6}, moments=[(1, 1)])
    with_ = ws.to_dat()
    seg_txt = ws.opt_2d.to_dat()
    comment = "! 2D 平面索力优化目标与显式索力箱契约（opt-in，默认不写）"
    wl = with_.splitlines()
    i = wl.index(comment)
    block_len = 1 + len(seg_txt.split("\n")) + 1
    removed = wl[:i] + wl[i + block_len:]
    check("不带契约 to_dat 不含 *CABLE_OPT2D",
          "*CABLE_OPT2D" not in without)
    check("带契约 to_dat 恰含一段 *CABLE_OPT2D",
          with_.count("*CABLE_OPT2D") == 1)
    check("带/不带契约逐位差只在新增段",
          "\n".join(removed) + "\n" == without)

    check("solve_2d 有 opt_detail 形参且默认 False",
          "opt_detail: bool = False" in src_s)
    check("Solve2DResult.opt 默认 not_requested",
          "opt: dict[str, Any] = field(" in src_s
          and Solve2DResult().opt.get("status") == "not_requested")
    check("Solve2DResult 有 opt_tables()", "def opt_tables(" in src_s)
    check("⑧ 分支调 --opt-detail-only", '"--opt-detail-only"' in src_s)
    check("⑧ 分支读前 unlink 残留 .opt.txt", "op_path.unlink()" in src_s)
    check("⑧ 分支状态分派 no_contract/unavailable/no_output/solver_error",
          all(k in src_s for k in ('"no_contract"', '"unavailable"',
                                   '"no_output"', '"solver_error"')))
    check("⑧ 分支 diagnostics.append",
          "res.diagnostics.append(opt_2d.status_text(res.opt))" in src_s)

    check("model_doc.capability() 有 opt2d 格", '"opt2d"' in src_d)
    check("ModelDoc.solve 有 opt_2d_detail 且默认 False 并透传",
          "opt_2d_detail: bool = False" in src_d
          and "opt_detail=bool(opt_2d_detail)" in src_d)

    check("app.py 有 opt2ddetail_opt / opt2d_cfg",
          "opt2ddetail_opt" in src_a and "opt2d_cfg" in src_a)
    check("app.py 每次 rerun 先清空 opt_2d",
          "doc.model.opt_2d = None" in src_a)
    check("app.py JSON 非法给中文 warning",
          "2D 索力优化契约无效" in src_a and "st.warning" in src_a)

    check("export_html opt_html 默认空串", 'opt_html = ""' in src_h)
    check("export_html 仅 status != not_requested 出章",
          'if _op.get("status") != "not_requested":' in src_h)
    check("export_html 非 ok 不摆空表（无表只出中文 note）",
          "if not _tables:" in src_h)

    check("report_2d 有 OPT_BOUNDARY_NOTE",
          "OPT_BOUNDARY_NOTE = opt_2d.BOUNDARY_NOTE" in src_r)
    check("report_2d 章 ⑪ / 诊断顺延 ⑫", "⑪" in src_r and "⑫" in src_r)

    # 默认 HTML 逐字节 + 不含 [OPT2D] 章。
    OUT.mkdir(parents=True, exist_ok=True)
    r0 = Solve2DResult()
    spec0 = BridgeSpec()
    a = write_2d_html(r0, spec0, OUT / "d0.html").read_bytes()
    b = write_2d_html(r0, spec0, OUT / "d1.html").read_bytes()
    check("默认 2D HTML 两次写出逐字节一致", a == b, f"{len(a)}B")
    html0 = a.decode("utf-8", errors="replace")
    check("默认 HTML 不含 [OPT2D] / 平面索力优化 / opt_2d_summary_table",
          "[OPT2D]" not in html0
          and "平面索力优化" not in html0
          and "opt_2d_summary_table" not in html0)

    # 12 格默认 .dat 黄金哈希：本轮没有改任何 DEFAULTS 模型契约字段。
    check("DEFAULT_DAT_SHA 仍是 12 项", len(DEFAULT_DAT_SHA) == 12,
          str(len(DEFAULT_DAT_SHA)))
    for (kind, dim), builder in BUILDERS.items():
        dat = ModelDoc(kind, dict(DEFAULTS[builder]), dimension=dim).to_dat()
        h16 = hashlib.sha256(dat.encode()).hexdigest()[:16]
        check(f"12 格默认 .dat 黄金哈希：{builder}",
              h16 == DEFAULT_DAT_SHA[builder], h16)
    check("DEFAULTS 不含 opt_2d_detail 求解选项",
          "opt_2d_detail" not in DEFAULTS)


# ---------------------------------------------------------------- [G] 四出口

def exports() -> None:
    print("\n[G] 四出口同源（Report.tables / CSV / opt_tables()）")
    d = L.parse_opt_txt(SAMPLE_OK)
    r = Solve2DResult(opt=d)
    spec = BridgeSpec()
    tabs = r.opt_tables()
    keys = ("opt_2d_summary_table", "opt_2d_iter_table",
            "opt_2d_cable_table", "opt_2d_moment_table")
    check("Solve2DResult.opt_tables() 与 opt_2d.tables() 一致",
          tabs == L.tables(d))
    rep = report_2d.build_2d_document(r, spec)
    check("res.opt=ok 时 Report 含四张 opt 表",
          all(k in rep.tables for k in keys))
    check("Report 含 [OPT2D] h1 章",
          any(blk.get("type") == "h1"
              and "2D 平面索力优化" in str(blk.get("text"))
              for blk in rep.blocks))
    check("Report 含边界 note（不等于 3D）",
          any("不等于 3D" in str(blk.get("text")) for blk in rep.blocks))

    d4 = OUT / "four"
    d4.mkdir(parents=True, exist_ok=True)
    paths = report_2d.write_2d_all(rep, d4, stem="op", pdf=False)
    check("四出口三件套落盘（pdf=False）",
          set(paths) == {"html", "docx", "zip"}, str(sorted(paths)))
    with zipfile.ZipFile(paths["zip"]) as z:
        names = z.namelist()
        check("CSV 自动收四张 opt 表",
              all(any(k + ".csv" in n for n in names) for k in keys))
        cable_csv = next((z.read(n).decode("utf-8") for n in names
                          if "opt_2d_cable_table.csv" in n), "")
        check("CSV 与表字典同源（表头）",
              all(h in cable_csv
                  for h in tabs["opt_2d_cable_table"]["headers"][:3]))
        check("CSV 与表字典同源（数值）",
              "1000.0000" in cable_csv and "4.995005" in cable_csv)
    check("DOCX 出口非空", paths["docx"].stat().st_size > 0)

    # 状态非 ok：不追加任何 opt 表。
    rbad = Solve2DResult(opt={"status": "no_contract", "source": L.SOURCE})
    check("状态非 ok Solve2DResult.opt_tables() 为空",
          rbad.opt_tables() == {})
    repbad = report_2d.build_2d_document(rbad, spec)
    check("状态非 ok Report 不追加任何 opt 表",
          not any("opt_2d" in k for k in repbad.tables))


# ---------------------------------------------------------------- [H] 文档对拍

def docs() -> None:
    print("\n[H] 两仓文档口径对拍（第十四次）")
    fe_log = read(FE / "CHANGELOG.md")
    fe_man = read(FE / "USER_MANUAL.md")
    fe_fmt = read(FE / "input_format.md")
    mat = read(ROOT / "能力边界矩阵.md")
    spec_doc = read(ROOT / "B类后续项接入规格.md")
    rd = read(ROOT / "README.md")
    use = read(ROOT / "使用说明.md")
    guide = read(ROOT / "开发使用手册.md")

    # 矩阵 §2 表格行：目标索力优化格现在是 | P | Y |（3D 仍 Y）。
    opt_rows = [ln for ln in mat.splitlines()
                if ln.startswith("| 目标索力优化")]
    check("矩阵 §2 [OPT] 格现在是 | P | Y |（3D 仍 Y）",
          len(opt_rows) == 1 and "| P | Y |" in opt_rows[0], str(opt_rows))
    check("历史段落仍登记矩阵 §4.2 第十四次计数 0N+7P",
          "0 个 `2D=N` + 7 个 `2D=P`" in mat)
    check("历史段落仍登记矩阵 B2 剩余由 2 项收窄为 1 项",
          "B2 剩余由 2 项收窄为 1 项" in mat)
    check("矩阵有第十四次闭环记录段",
          "2026-11-12 第十四次闭环记录" in mat)
    check("矩阵仍写 P ≠ Y", "P ≠ Y" in mat)
    check("矩阵仍写不等于 3D [OPT]", "不等于 3D `[OPT]`" in mat)
    # 历史条目仍在（D 类准则）：第十四次闭环记录之前的带日期历史段落。
    cut = mat.find("2026-11-12 第十四次闭环记录")
    hist = mat[:cut] if cut > 0 else mat
    check("矩阵历史计数 1 个 2D=N + 6 个 2D=P 仍在",
          "1 个 `2D=N` + 6 个 `2D=P`" in hist)
    check("矩阵带日期历史段落仍有 B2 剩余 2 项",
          "B2 剩余 2 项" in hist and "2026-11-11" in hist
          and "第十三次" in hist)

    # B 类规格 §0 / §6 第 12 条 / 未闭环清单。
    check("历史段落仍登记十四次逐次接入、当前为十五次",
          "部分实现（十五次逐次接入）" in spec_doc
          and "十四次" in spec_doc)
    check("B 类规格 §6 第 12 条 [OPT2D] 不虚报",
          "12. 第十四次 `[OPT2D]` 2D 平面索力优化不虚报" in spec_doc)
    check("历史段落仍登记 B 类规格第十四次未闭环清单 B2 剩余 1 项与 X20 契约锁死项",
          "0 个 `2D=N` + 7 个 `2D=P`" in spec_doc
          and "B2 剩余 1 项" in spec_doc
          and "X20 契约锁死" in spec_doc)

    # README / 使用说明 / 开发手册：第十五次当前口径 + 第十四次历史章节标题仍在。
    check("历史段落仍登记 README 第十四次 [OPT2D] 章节标题",
          "第十四次" in rd and "### 2D 平面索力优化 `[OPT2D]`" in rd)
    check("README §4.2 现为 0N+9P / 不等于 3D [OPT]",
          "0 个 `2D=N` + 9 个 `2D=P`" in rd
          and "不等于 3D `[OPT]`" in rd)
    check("使用说明第十五次 [OPT2D] 与 B2 剩余 0 项",
          "第十五次" in use and "[OPT2D]" in use
          and "B2 剩余 0 项" in use
          and "第十三次起 `[LENGTH2D]`" in use)
    check("开发手册第十五次 [OPT2D] 当前陈述与 0N+9P",
          "第十五次" in guide and "[OPT2D]" in guide
          and "0 个 `2D=N` + 9 个 `2D=P`" in guide
          and "B2 剩余 0 项" in guide)

    # FE 三份：CHANGELOG 新小节 / input_format §6.6 / USER_MANUAL 专章与对照表。
    check("FE CHANGELOG 顶部 ## 2026-11-12",
          "## 2026-11-12" in "\n".join(fe_log.splitlines()[:12]))
    check("FE CHANGELOG 有 [OPT2D] 新小节",
          "### 新增：2D 平面索力优化 [OPT2D] 段" in fe_log)
    check("FE CHANGELOG 写明 37 个黄金基线不刷新 / 不等于 3D [OPT]",
          "基线不刷新" in fe_log and "不等于 3D `[OPT]`" in fe_log)
    check("FE input_format 有 ### 6.6 `*CABLE_OPT2D`",
          "### 6.6 `*CABLE_OPT2D`" in fe_fmt)
    check("FE input_format 校验清单有 *CABLE_OPT2D 条目",
          "- `*CABLE_OPT2D`（`OPT2D=1` 时）" in fe_fmt)
    check("FE USER_MANUAL 有 #### 二维 opt-in `[OPT2D]`",
          "#### 二维 opt-in `[OPT2D]`" in fe_man)
    check("FE USER_MANUAL 对照表新 2D 列",
          "索长 / 目标索力 opt-in 段" in fe_man
          and "*CABLE_OPT2D" in fe_man and "→ `[OPT2D]`" in fe_man)

    # 历史条目仍在（D 类准则）：CHANGELOG 第十三次历史句。
    check("FE CHANGELOG 第十三次历史句 [OPT] 在 2D 仍不做 保留",
          "带约束目标索力优化在 2D" in fe_log and "仍不做" in fe_log)

    # 统计口径由 README 质量保障段统一维护，final11 实测后回填具体数字；
    # 这里只锁「该段确实登记了脚本数与断言总数」，不写死会随每轮增长的数字。
    check("README 质量保障段登记全套脚本数", "个脚本全绿" in rd)
    check("README 质量保障段登记断言总数", "项断言" in rd)


def fifteenth() -> None:
    """第十五次：核对 README / 使用说明 / 开发手册第十五次新章与历史串、
    矩阵 §2 两格与 §4.2 计数 / 第十五次闭环记录 / 云图格仍 N、B 类规格
    十五次口径、2D 源码正交与 3D 签名；逐条按布尔位统计。"""
    mat = read(ROOT / "能力边界矩阵.md")
    spec_doc = read(ROOT / "B类后续项接入规格.md")
    rd = read(ROOT / "README.md")
    use = read(ROOT / "使用说明.md")
    guide = read(ROOT / "开发使用手册.md")
    e2d = read(ROOT / "export_2d_formats.py")
    e3d = read(ROOT / "export_formats.py")
    check("第十五次：README 新章 + 0N+9P + B2 剩余 0 项",
          "### 2D 平行导出 / 导入链（2026-11-12，第十五次）" in rd
          and "0 个 `2D=N` + 9 个 `2D=P`" in rd
          and "B2 剩余 0 项" in rd)
    check("第十五次：README 第十四次 [OPT2D] 章节标题仍在、不等于 3D [OPT]",
          "### 2D 平面索力优化 `[OPT2D]`" in rd
          and "不等于 3D `[OPT]`" in rd)
    check("第十五次：使用说明 §8.5 新章 / B2 剩余 0 项 / 第十五次 / 第十三次起 [LENGTH2D]",
          all(k in use for k in (
              "### 8.5 2D 平行导出 / 导入链（2026-11-12，第十五次）",
              "B2 剩余 0 项", "第十五次", "第十三次起 `[LENGTH2D]`")))
    check("第十五次：使用说明第十四次 [OPT2D] 按 P 接通串已不存在（改第十五次表述）",
          "第十四次 `[OPT2D]` 按 `P` 接通" not in use
          and "第十四次起 `[OPT2D]` 2D 平面索力优化按 opt-in 新造接通" in use)
    check("第十五次：开发手册 §8.x 新章 / 登记表 12 项 / B2 剩余由 2 项收窄为 1 项 / 0N+9P",
          all(k in guide for k in (
              "### 8.x 2D 平行导出 / 导入链（2026-11-12，第十五次）",
              "登记表 12 项", "B2 剩余由 2 项收窄为 1 项",
              "0 个 `2D=N` + 9 个 `2D=P`")))
    check("第十五次：开发手册第十四次历史计数跨行保留（连续串 0N+7P / B2 剩余 1 项 均不在）",
          "0 个 `2D=N` +" in guide and "7 个 `2D=P`" in guide
          and "0 个 `2D=N` + 7 个 `2D=P`" not in guide
          and "B2 剩余 1 项" not in guide)
    check("第十五次：矩阵 §2 两格 | P | Y | 与两条 2D 依据符号",
          "| ANSYS / Abaqus / MIDAS 多格式导出 | P | Y |" in mat
          and "| `.mct` 反向导入 | P | Y |" in mat
          and "export_2d_formats.py::to_plane" in mat
          and "export_2d_formats.py::read_mct_2d" in mat)
    check("第十五次：矩阵 §4.2 0N+9P / B2 剩余 0 项 / 第十五次闭环记录 / 云图格仍 N",
          all(k in mat for k in (
              "0 个 `2D=N` + 9 个 `2D=P`", "B2 剩余 0 项",
              "第十五次闭环记录",
              "| 3D 活载包络结构云图 / 逐端明细表 | N | Y |")))
    check("第十五次：B 类规格 部分实现（十五次逐次接入） / 13. 第十五次 / B2 剩余 0 项",
          all(k in spec_doc for k in (
              "部分实现（十五次逐次接入）", "13. 第十五次", "B2 剩余 0 项")))
    check("第十五次：export_2d_formats.py 正交不 import 3D 链、3D to_canonical 签名未改",
          "from export_formats import" not in e2d
          and "import mct_io" not in e2d
          and "def to_canonical(model: ModelSpec3D) -> tuple[dict[str, Any], list[str]]:" in e3d)


def main() -> int:
    print("=" * 78)
    print("2D 平面索力优化 [OPT2D] 守卫（B2 第十四次，纯离线 + 本地内核实跑）")
    print("=" * 78)
    OUT.mkdir(parents=True, exist_ok=True)
    fe_sources()
    fe_runtime()
    bs_contract()
    parser_positive()
    parser_negative()
    wiring()
    exports()
    docs()
    fifteenth()
    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(PASS)} 通过")
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
