"""verify_completion_stability_3d.py —— 第十九次 ① 成桥态全过程稳定接入守卫。

纯离线：不调求解器、不写 .dat、不重算力学、不读黄金值。逐条锁住：

1. FE 源码契约：``ArcLengthState3D`` / ``R = F0 + lam*Finc - Fint(u)`` /
   带默认值的第三参数（默认零污染）、``fem_3d_nl.hpp`` 主求解器零改动；
2. ``cable_solver.cpp``：``STABASIS`` / ``*STABILITY_LOAD`` 独立键、
   默认全关、``if (cfg.stability.enabled)`` 分流仍在、
   ``COMPLETION`` 基准不提前 return、``[BUCKLING]`` ERROR 段保留；
3. ``stability_output_3d.hpp``：``BASIS`` 参数化、``STATE CTRL0=`` 行、
   ERROR 双函数支持新基准；
4. ``stability_3d.py`` 唯一解析器：ERROR 段 BASIS 通配、``STATE`` 解析、
   4 类新错误码中文齐备、``BOUNDARY_NOTE`` 把两种 λ 分列；
5. 文档如实：能力矩阵仍 ``| N | P |``、规格 §2.6 三阶段表与四条硬口径、
   三份文档登记第十九次、``input_format_3d.md`` 登记新键与新段；
6. 独立 oracle：手工 ``completion_state`` 正常段 / ERROR 段逐字段解析，
   ``ctrl0`` 缺段时为 ``None`` 不补 0。

**本守卫存在本身就在锁住「① / ② 完成不等于 B3 推 Y」**：任何一项把矩阵
涂成 `Y` 或删掉 §2.6 的阶段表，这里立刻红。

第十九次 ② 增补 `stage2_contract()`：真实斜拉桥 ANSYS 两段对标的
同源导出、两个 SOLVE / 16 条 INISTATE、`--live-ansys` 不覆写冻结 CSV、
MODE=COMPLETION 起点不同源缺陷如实登记、② 完成仍 `P` 不 `Y`。

第十九次 ③a 增补 `stage3_contract()`：`basisModel` 起点同源修复、
施工阶段段共存核对、CB3D22 越过极值点 / 下降段、第 24 / 25 项 CTest、
25 个 CTest 总索引，以及「③a 完成仍 `P` 不 `Y`」的不虚报口径。

第二十次 ③b 增补 `stage4_contract()`：`NLStageResult3D::model` 同源
阶段模型回传、`STABASIS=STAGE` / `STAGEID` 独立 opt-in、
`BASIS=stage_state` 与另两种基准分列、`ERROR=stage_not_found` 不降级、
CB3D23 同源导出与两个 ANSYS 两段 job、第 26 项 CTest / 26 个 CTest
总索引，以及「③b 完成仍 `P` 不 `Y`」「不得靠一个 opt-in 算例提前
打勾」的不虚报口径。
"""

from __future__ import annotations

import sys
from pathlib import Path

from console_utf8 import setup_console

setup_console()

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import stability_3d as st  # noqa: E402

PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, bool(ok), str(detail)))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""


def fe() -> Path | None:
    from solve_2d import _fem_eigen_dir
    return _fem_eigen_dir()


SAMPLE_COMPLETION = [
    "KIND=spherical_arc_length STATUS=ok "
    "BASIS=completion_state PATHMETHOD=SPHERE",
    "NSTEP=2 NPOINT=3 CONVERGED=1 CTRLNODE=2 CTRLDOF=2 STRAIN=0 PSI=1",
    "STATE CTRL0=-0.1 F0_NORM=2760825.56 FINC_NORM=500000 RES0=1.2e-08",
    "LAMBDA_FINAL=0.25 ERROR=none",
    "I STEP=0 LAMBDA=0 CTRL=0 RES=0 ITER=0 DS=0.002 PIVOT=1 CONVERGED=1",
    "I STEP=1 LAMBDA=0.1 CTRL=-0.01 RES=0.00011 ITER=2 DS=0.002 "
    "PIVOT=5.2e-09 CONVERGED=1",
    "I STEP=2 LAMBDA=0.25 CTRL=-0.02 RES=0.00012 ITER=3 DS=0.0021 "
    "PIVOT=5.1e-09 CONVERGED=1",
]


def fe_contract() -> None:
    root = fe()
    if root is None:
        check("定位 FEM-Eigen 源码目录", False, "未定位")
        return
    sp = read(root / "stability_path_3d.hpp")
    cs = read(root / "cable_solver.cpp")
    out = read(root / "stability_output_3d.hpp")
    nl = read(root / "fem_3d_nl.hpp")
    fmt = read(root / "input_format_3d.md")

    check("内核新增 ArcLengthState3D（u0 / F0 / Finc）",
          "struct ArcLengthState3D" in sp
          and all(k in sp for k in ("u0", "F0", "Finc")))
    check("平衡方程推广为 R = F0 + lam*Finc - Fint(u)",
          "F0 + lamT * Fref - Fint" in sp
          and "st.F0" in sp)
    check("第三参数带默认值、参考构形行为逐位不变",
          "const ArcLengthState3D& st = {}" in sp
          and "st.u0.empty()" in sp and "st.Finc.empty()" in sp)
    check("弧长约束仍对 du、控制位移改用相对 u0 增量",
          "duAcc" in sp and "ctrlDisp0" in sp
          and "std::abs(u(ctrl) - ctrlDisp0)" in sp)
    check("fem_3d_nl.hpp 主求解器零改动（无 ArcLength / Riks）",
          "solveModel3DNonlinear" in nl and "Ftarget" in nl
          and "ArcLength" not in nl and "Riks" not in nl)
    check("STABASIS / *STABILITY_LOAD 是独立 opt-in 键与独立段",
          '"STABASIS"' in cs and "STABILITY_LOAD" in cs
          and 'std::string basis = "REFERENCE"' in cs
          and "incLoads" in cs)
    check("*STABILITY_LOAD 不进 m.loads（成桥态恒载不被污染）",
          "不进 m.loads" in cs)
    check("默认全关、既有 STABILITY=1 分流保留、COMPLETION 不提前 return",
          "bool enabled = false;" in cs
          and "if (cfg.stability.enabled)" in cs
          and "completionBasis" in cs
          and "runStabilityMode" in cs)
    check("[BUCKLING] ERROR 段与 [NEWTON] / [LENGTH] 段原样保留",
          '"nonlinear_state_not_supported"' in cs
          and "formatNewtonSection3D" in cs
          and "formatCableLengthSection3D" in cs)
    check("[STABILITY] 段置于主链路 [END] 前",
          'fprintf(f, "[END]\\n")' in cs
          and "formatStabilitySection3D" in cs
          and "completion_state" in cs)
    check("输出层 BASIS 参数化 + STATE CTRL0 行 + ERROR 双函数",
          "const std::string& basis" in out
          and "STATE CTRL0=" in out
          and "stabilityErrorSection3D" in out
          and "formatStabilitySection3D" in out)
    check("input_format_3d.md 登记新键 / 新段 / 两种 λ 不是同一个量",
          "STABASIS" in fmt and "STABILITY_LOAD" in fmt
          and "completion_state" in fmt and "不是同一个量" in fmt)


def parser_contract() -> None:
    src = read(ROOT / "stability_3d.py")
    check("仍是唯一解析器且只解析不重算",
          "唯一解析器" in src and "不求解、不重算、不补零" in src
          and all(k not in src for k in ("numpy", "np.linalg", "inv(",
                                         "lu.solve", "assemble", "stiffness")))
    check("ERROR 段 BASIS 通配、不再硬编码 reference_configuration",
          r"BASIS=(\S+)$" in src and 'out["basis"] = m.group(1)' in src)
    check("STATE CTRL0 行解析、缺段不补 0",
          "_RE_STATE" in src and '"ctrl0": None' in src
          and 'out["ctrl0"] = _f(m.group(1))' in src)
    check("BOUNDARY_NOTE 把 REFERENCE / COMPLETION 两种 λ 分列",
          "BASIS=reference_configuration" in st.BOUNDARY_NOTE
          and "BASIS=completion_state" in st.BOUNDARY_NOTE
          and "不是同一个量" in st.BOUNDARY_NOTE
          and "不是 JTG 3362 的稳定安全系数" in st.BOUNDARY_NOTE)
    new_codes = ("zero_incremental_load", "unsupported_stability_basis",
                 "completion_not_converged", "invalid_initial_state")
    for code in new_codes:
        txt = st.stability_error_text(st.empty_stability(code))
        check(f"新错误码 {code} 有中文说明", bool(txt) and code not in txt)


def oracle() -> None:
    r = st.parse_stability_section(SAMPLE_COMPLETION)
    check("completion_state 正常段：BASIS 不被改写成参考构形",
          r["basis"] == "completion_state"
          and r["kind"] == "spherical_arc_length"
          and r["status"] == "ok")
    check("STATE 行 4 个标量逐格解析、λ_final 与 3 点同源",
          r["ctrl0"] == -0.1 and r["f0_norm"] == 2760825.56
          and r["finc_norm"] == 500000.0 and r["res0"] == 1.2e-08
          and r["lambda_final"] == 0.25 and len(r["points"]) == 3)
    check("报告章写明基准与 CTRL 增量语义",
          "BASIS=completion_state" in st.stability_report_text(r)
          and "CTRL0" in st.stability_report_text(r)
          and "相对成桥态的位移增量" in st.stability_report_text(r)
          and st.BOUNDARY_NOTE in st.stability_report_text(r))
    ref = st.parse_stability_section([
        "KIND=spherical_arc_length STATUS=ok "
        "BASIS=reference_configuration PATHMETHOD=SPHERE",
        "NSTEP=0 NPOINT=1 CONVERGED=1 CTRLNODE=2 CTRLDOF=2 STRAIN=1 PSI=1",
        "LAMBDA_FINAL=0 ERROR=none",
        "I STEP=0 LAMBDA=0 CTRL=0 RES=0 ITER=0 DS=0.005 "
        "PIVOT=1 CONVERGED=1",
    ])
    check("reference_configuration 段没有 STATE 行时不补 0",
          ref["basis"] == "reference_configuration"
          and ref["ctrl0"] is None and ref["f0_norm"] is None
          and ref["finc_norm"] is None and ref["res0"] is None)
    err = st.parse_stability_section([
        "KIND=none STATUS=not_applicable BASIS=completion_state",
        "NSTEP=0 NPOINT=0 CONVERGED=0",
        "ERROR=zero_incremental_load",
    ])
    check("completion_state 的 ERROR 段：BASIS 保留、不摆空表、中文做不了",
          err["basis"] == "completion_state" and err["points"] == []
          and err["lambda_final"] is None
          and st.stability_path_table(err) is None
          and "做不了" in st.stability_report_text(err)
          and "*STABILITY_LOAD" in st.stability_error_text(err))


def docs() -> None:
    mat = read(ROOT / "能力边界矩阵.md")
    spec = read(ROOT / "B类后续项接入规格.md")
    guide = read(ROOT / "开发使用手册.md")
    use = read(ROOT / "使用说明.md")
    readme = read(ROOT / "README.md")
    check("能力矩阵该行仍 3D=P 不是 Y（① 完成不推 Y）",
          "| 非线性稳定全过程（球面弧长法 opt-in） | N | P |" in mat
          and "第十九次 ①" in mat and "仍 `P ≠ Y`" in mat)
    check("规格 §2.6 三阶段表与四条硬口径在位",
          "### 2.6 a 阶段三阶段验收口径" in spec
          and "①" in spec and "②" in spec and "③" in spec
          and "四条硬口径" in spec
          and "不得拿参考构形 `ARCLEN` 冒充" in spec)
    check("规格 §2.5 第十九次补记与 §6 第 16 条守卫登记",
          "第十九次 ① 补记" in spec
          and "16. 第十九次 ①" in spec
          and "verify_completion_stability_3d.py" in spec)
    check("规格明确 ① 结束仍 P、ANSYS 两段对标属 ②",
          "① 只做 FEM 侧自洽与解析锚点" in spec
          and "真实桥梁算例的 ANSYS 两段对标属 ②" in spec)
    for name, txt in (("README.md", readme), ("使用说明.md", use),
                      ("开发使用手册.md", guide)):
        check(f"{name} 登记第十九次且仍 P 不 Y",
              "第十九次" in txt and "STABASIS=COMPLETION" in txt
              and ("仍 `P`" in txt or "仍 `P ≠ Y`" in txt))


def stage2_contract() -> None:
    """第十九次 ②：真实桥梁两段对标的如实边界（纯离线，不调求解器）。"""
    root = fe()
    ac = read(ROOT / "ansys_bridge_completion.py")
    rc = read(ROOT / "cases" / "run_cases_completion.py")
    cases_idx = read(ROOT / "cases" / "README.md")
    mat = read(ROOT / "能力边界矩阵.md")
    spec = read(ROOT / "B类后续项接入规格.md")
    cb_case = ROOT / "cases" / "CB3D21_completion_stability_vs_ansys"

    check("② 同源导出模块与编排脚本在位",
          bool(ac) and bool(rc)
          and "mode=\"STATIC\"" in ac
          and "COMPLETION_CFG" in ac
          and "--sync-only" in rc)
    check("② 唯一数据源显式 L0 / STABASIS / *STABILITY_LOAD",
          all(k in ac for k in (
              "cable_l0", "L0 = L_design", "STABASIS=COMPLETION",
              "*STABILITY_LOAD", "initial_cable_strain", "LAMMAX", "PINC")))
    check("② INISTATE eps 同源断言 + 两段 APDL 建模",
          "INISTATE" in ac and "eps" in ac
          and "spec_nl_to_apdl" in ac and "SOLVE" in ac
          and "eps_uniform" in rc)
    check("② 不用反力求和、λ 用 TIME 换算、ARCLEN 只作终点",
          "反力求和" in ac and "lambda_FEM=(TIME-1)*LAMMAX" in rc
          and "ARCLEN" in ac and "cs_arc" in rc)
    check("② 正式算例目录与 ALL CHECKS PASSED 报告在位",
          cb_case.is_dir()
          and "ALL CHECKS PASSED" in read(
              cb_case / "cb3d21_completion_stability_compare_report.txt"))
    for job in ("cs_load", "cs_arc"):
        inp = read(cb_case / (job + ".inp"))
        check(f"② ANSYS {job} 恰有 2 个 SOLVE / 2 条 F / 16 条 INISTATE",
              len([x for x in inp.splitlines() if x.strip() == "SOLVE"]) == 2
              and len([x for x in inp.splitlines()
                       if x.startswith("F,1016,FZ,")]) == 2
              and inp.count("INISTATE,DEFINE,") == 16)
    check("② 主 job 荷载控制、交叉 job 才有 ARCLEN",
          "ARCLEN,ON" not in read(cb_case / "cs_load.inp")
          and "ARCLEN,ON" in read(cb_case / "cs_arc.inp"))
    if root is not None:
        bench = root / "tests" / "benchmark" / "completion_bridge_3d"
        cmp_src = read(bench / "compare_completion_bridge_3d.py")
        check("② 第 23 项 CTest 离线入口 + --live-ansys 不写回冻结 cases",
              bool(cmp_src) and "--live-ansys" in cmp_src
              and "绝不写回" in cmp_src
              and "completion_state" in cmp_src)
        check("② 第 23 项 README 如实写明无极限点 / 仍 P 不 Y",
              "无极限点" in read(bench / "README.md")
              and "仍 `P` 不标 `Y`" in read(bench / "README.md"))
        fe_idx = read(root / "tests" / "benchmark" / "README.md")
        check("② FE 总索引登记 23 个 CTest（③a 后合计 25 个）",
              "23 个 CTest" in fe_idx and "25 个 CTest" in fe_idx)
    check("② 算例索引登记 CB3D21 / completion_bridge_3d",
          "CB3D21" in cases_idx and "completion_bridge_3d" in cases_idx
          and "CB3D01~CB3D21" in cases_idx)
    check("② 能力矩阵仍 | N | P | 且 §2.6 三阶段表仍在",
          "| 非线性稳定全过程（球面弧长法 opt-in） | N | P |" in mat
          and "### 2.6 a 阶段三阶段验收口径" in spec
          and "| ③ |" in spec and "③ 才推 `Y`" in spec)
    check("② MODE=COMPLETION 起点不同源缺陷如实登记（不得略过）",
          "起点不同源" in spec and "RES0=3.15 MN" in spec
          and "独立后续项" in spec)
    check("② 完成仍 P 不 Y、施工阶段链路属 ③",
          "② 完成**仍 `P` 不 `Y`**" in spec
          and "施工阶段链路核对" in spec)


def stage3_contract() -> None:
    """第十九次 ③a：起点同源修复 / 施工阶段共存 / 越过极值点 / 仍 P 不 Y。

    纯离线：只读源码与冻结证据文本，不调求解器、不重跑 ANSYS、不写文件。
    """
    root = fe()
    lp = read(ROOT / "ansys_bridge_limit_point.py")
    rc = read(ROOT / "cases" / "run_cases_limit_point.py")
    spec = read(ROOT / "B类后续项接入规格.md")
    mat = read(ROOT / "能力边界矩阵.md")
    cases_idx = read(ROOT / "cases" / "README.md")
    case_dir = ROOT / "cases" / "CB3D22_limit_point_vs_ansys"

    # ---- 1. ③a 同源构型：只缩实常数，不重新建模 ----
    check("③a 导出模块 / 编排脚本在位且锁定 TOWER_SCALE / 同源配置",
          bool(lp) and bool(rc)
          and "TOWER_SCALE = 0.012" in lp
          and "LIMIT_CFG = dict(COMPLETION_CFG)" in lp
          and "--check-only" in rc and "--sync-to" in rc)
    check("③a 只缩放桥塔实常数，不改几何 / 恒载 / 索（避免成桥态不同源）",
          "def build_model():" in lp and "r.A *= TOWER_SCALE" in lp
          and "r.IY *= TOWER_SCALE ** 2" in lp
          and "build_cable_stayed_3d(**LIMIT_CFG)" in lp)
    check("③a ANSYS 两段仍用 λ = TIME − 1、ARCLEN 只给 lp_arc",
          "λ = TIME − 1" in lp and "ARCTRM,U," in lp
          and "ARCLEN,ON" in lp)
    check("③a 正式算例目录与 20 checks / 0 failed 报告在位",
          case_dir.is_dir()
          and "20 checks, 0 failed" in read(
              case_dir / "cb3d22_limit_point_compare_report.txt"))
    for job in ("lp_load", "lp_arc"):
        inp = read(case_dir / (job + ".inp"))
        out = read(case_dir / (job + "_ansys.out"))
        check(f"③a ANSYS {job}：2 SOLVE / 16 INISTATE / 0 error",
              len([x for x in inp.splitlines() if x.strip() == "SOLVE"]) == 2
              and inp.count("INISTATE,DEFINE,") == 16
              and "NUMBER OF ERROR   MESSAGES ENCOUNTERED=          0" in out)
    check("③a lp_load 荷载控制、lp_arc 才用 ARCLEN",
          "ARCLEN,ON" not in read(case_dir / "lp_load.inp")
          and "ARCLEN,ON" in read(case_dir / "lp_arc.inp"))
    check("③a 按 ΔUX 插值 λ（λ 非单调）并分段统计",
          "interp_lambda_by_ctrl" in rc
          and "TOL_DESCEND" in rc and "TOL_MEAN" in rc)

    # ---- 2. FE 侧起点同源修复与两项新 CTest ----
    if root is None:
        check("定位 FEM-Eigen 源码目录（③a）", False, "未定位")
        return
    nl = read(root / "fem_3d_nl.hpp")
    cs = read(root / "cable_solver.cpp")
    bench = root / "tests" / "benchmark"
    b24 = bench / "completion_basis_3d"
    b25 = bench / "bridge_limit_point_3d"
    check("③a 内核三个结果结构回传同源 basisModel",
          nl.count("Model3D basisModel;") == 3
          and "out.basisModel = m;" in nl)
    check("③a 主链路只在 opt-in 成桥态改用同源模型并做自由度一致性检查",
          "useStabilityModel" in cs
          and "invalid_initial_state" in cs
          and "useStabilityModel ? stabilityModel : m" in cs)
    check("③a 第 24 项 CTest（起点同源 + 施工阶段共存）在位",
          (b24 / "compare_completion_basis_3d.py").is_file()
          and (b24 / "reference" / "completion_basis_3d.json").is_file()
          and "CB3D10_BASIS_FIX" in read(b24 / "build_reference_basis.py")
          and "CB3D13" in read(b24 / "README.md")
          and "不得据此把 B3 涂成 `Y`" in read(b24 / "README.md"))
    check("③a 第 25 项 CTest（越过极值点 / 下降段）在位",
          (b25 / "compare_bridge_limit_point_3d.py").is_file()
          and (b25 / "reference" / "cb3d22_limit_point.json").is_file()
          and "越过极值点" in read(b25 / "README.md")
          and "下降段" in read(b25 / "README.md"))
    check("③a CTest 注册与 run_tests.ps1 -BuildDir 同步",
          "bridge_limit_point_3d_benchmark" in read(root / "CMakeLists.txt")
          and "completion_basis_3d_benchmark" in read(root / "CMakeLists.txt")
          and "-BuildDir" in read(root / "run_tests.ps1")
          and "bridge_limit_point_3d" in read(root / "run_tests.ps1"))
    check("③a 跨工具链重放口径集中且不放宽冻结证据判据",
          (bench / "replay_tolerance.py").is_file()
          and "TOL_REL = 1.0e-9" in read(bench / "replay_tolerance.py")
          and "TOL_RES_ABS = 0.1" in read(bench / "replay_tolerance.py")
          and "25 个 CTest" in read(bench / "README.md")
          and "mingw64/bin/g++.exe" in read(bench / "README.md"))
    check("③a 编号不复用：CB3D22 只属极限点算例",
          "CB3D22" in read(b25 / "README.md")
          and "CB3D10" in read(b24 / "README.md")
          and "CB3D13" in read(b24 / "README.md")
          and "CB3D10_BASIS_FIX" in read(b24 / "README.md"))

    # ---- 3. 文档 / 矩阵如实：③a 完成仍 P 不 Y ----
    check("③a 规格 §2.5 / §2.6 如实登记且明确仍 P 不 Y",
          "第十九次 ③a 补记" in spec
          and "③a 阶段实测收口" in spec
          and "**B3 结论：仍 `P` 不 `Y`**" in spec
          and "不得靠改文档提前打勾" in spec
          and "分支屈曲" in spec and "施工阶段中间态全过程稳定" in spec)
    check("③a 能力矩阵仍 | N | P | 且缺口条数不变",
          "| 非线性稳定全过程（球面弧长法 opt-in） | N | P |" in mat
          and "第十九次 ③a 记录" in mat
          and "仍 `P ≠ Y`" in mat)
    check("③a 算例索引登记 CB3D22 / bridge_limit_point_3d",
          "CB3D22" in cases_idx
          and "bridge_limit_point_3d" in cases_idx
          and "CB3D01~CB3D21" in cases_idx)
    for name in ("README.md", "使用说明.md", "开发使用手册.md"):
        txt = read(ROOT / name)
        check(f"{name} 登记 ③a 且仍 P 不 Y",
              "③a" in txt and "STABASIS=COMPLETION" in txt
              and ("仍 `P`" in txt or "仍 `P ≠ Y`" in txt))

    # ---- 4. FE 侧四份文档同步（③a 收尾补记，防止回退后无人发现）----
    fe_docs = {
        "CHANGELOG.md": "③a 起点同源修复",
        "input_format_3d.md": "basisModel",
        "USER_MANUAL.md": "9.10 成桥态全过程稳定",
        "DEVELOPMENT_GUIDE.md": "同源模型",
    }
    for name, marker in fe_docs.items():
        txt = read(root / name)
        check(f"FE {name} 登记 ③a 且写明 {marker}",
              "③a" in txt and marker in txt)
    check("FE USER_MANUAL / DEVELOPMENT_GUIDE 如实写明 B3 仍 P 不 Y",
          "仍 `P` 不 `Y`" in read(root / "USER_MANUAL.md")
          and "仍 `P` 不 `Y`" in read(root / "DEVELOPMENT_GUIDE.md"))


def stage4_contract() -> None:
    """第二十次 ③b：施工阶段中间态起点 opt-in / CB3D23 / 仍 P 不 Y。

    纯离线：只读源码与冻结证据文本，不调求解器、不重跑 ANSYS、不写文件。
    """
    root = fe()
    export = read(ROOT / "ansys_stage_stability.py")
    runner = read(ROOT / "cases" / "run_cases_stage_state.py")
    spec = read(ROOT / "B类后续项接入规格.md")
    mat = read(ROOT / "能力边界矩阵.md")
    cases_idx = read(ROOT / "cases" / "README.md")
    case_dir = ROOT / "cases" / "CB3D23_stage_state_vs_ansys"

    # ---- 1. BS 侧同源导出 / 编排 / 正式算例 ----
    check("③b 导出模块与编排脚本在位，且用一份常量表同源生成 .dat / .inp",
          bool(export) and bool(runner)
          and 'CASE_ID = "cb3d23_stage_state"' in export
          and "def fem_dat(" in export and "def ansys_inp(" in export
          and "--check-only" in runner and "--sync-to" in runner
          and "--sync-only" in runner)
    check("③b STABASIS=STAGE STAGEID=2 与两种 λ 口径分列",
          "STABASIS=STAGE" in export and "STAGEID=2" in export
          and "stage_state" in export
          and "STAGE_ID = 2" in export
          and "PINC = -20000.0" in export
          and "CTRLTARGET = 0.005" in export)
    check("③b 两侧同源加 UX 导向约束消除一阶运动机构",
          "FIXED_X" in export and "FIXED_ALL" in export
          and "D,2,UX,0.0" in read(case_dir / "st_load.inp")
          and "D,3,UX,0.0" in read(case_dir / "st_load.inp"))
    check("③b 正式算例目录与 72 checks / 0 failed 报告在位",
          case_dir.is_dir()
          and "72 checks, 0 failed" in read(
              case_dir / "cb3d23_stage_state_compare_report.txt")
          and "ERROR=stage_not_found" in read(
              case_dir / "st_bad_stage_stage_stability.txt"))
    for job in ("st_load", "st_arc"):
        inp = read(case_dir / (job + ".inp"))
        out = read(case_dir / (job + "_ansys.out"))
        check(f"③b ANSYS {job}：2 SOLVE / TIME=1,3.7 / 0 error",
              len([x for x in inp.splitlines() if x.strip() == "SOLVE"]) == 2
              and "TIME,1" in inp and "TIME,3.7" in inp
              and "NUMBER OF ERROR   MESSAGES ENCOUNTERED=          0" in out)
    check("③b st_load 荷载控制、st_arc 才用 ARCLEN",
          "ARCLEN,ON" not in read(case_dir / "st_load.inp")
          and "ARCLEN,ON" in read(case_dir / "st_arc.inp"))
    check("③b 按 |ΔUY| 插值 λ 对拍、λ = TIME − 1 不靠反力求和",
          "interp_lambda_by_ctrl" in runner
          and "λ = TIME − 1" in runner
          and "TIME,1" in runner
          and "lammax" in export)

    # ---- 2. FE 侧同源阶段模型与独立 opt-in ----
    if root is None:
        check("定位 FEM-Eigen 源码目录（③b）", False, "未定位")
        return
    nl = read(root / "fem_3d_nl.hpp")
    cs = read(root / "cable_solver.cpp")
    out = read(root / "stability_output_3d.hpp")
    bench = root / "tests" / "benchmark"
    b26 = bench / "stage_stability_3d"
    check("③b NLStageResult3D 回传同源阶段模型 model",
          "Model3D model;" in nl and "row.model = mc;" in nl)
    check("③b 主链路 STABASIS=STAGE / STAGEID 与 runStageStabilityMode",
          "runStageStabilityMode" in cs
          and '"STAGEID"' in cs
          and "STAGE" in cs
          and "pick->model" in cs
          and "stage_not_found" in cs
          and '"STAGE"' in cs)
    check("③b 未知阶段不降级：stage_not_found 且输出独立文件",
          'fail("stage_not_found")' in cs
          and "_stage_stability.txt" in cs
          and "ERROR=stage_not_found" in read(
              case_dir / "st_bad_stage_stage_stability.txt"))
    check("③b StabilityStateHeader3D 增 STAGE= 行且只在 stageId>=0 写出",
          "stageId" in out and "stageElems" in out and "stageDofs" in out
          and "sh.stageId >= 0" in out)
    check("③b [STAGES] 段与三种 BASIS 分列",
          "[STAGES]" in cs
          and '"STAGE"' in cs
          and '"stage_state"' in cs
          and '"completion_state"' in cs
          and "BASIS=stage_state" in read(bench / "stage_stability_3d" / "README.md"))

    # ---- 3. 第 26 项 CTest / 双入口 / 总索引 ----
    check("③b 第 26 项 CTest（黄金值 / 生成器 / 比对脚本 / README）在位",
          (b26 / "compare_stage_state_3d.py").is_file()
          and (b26 / "build_reference_stage_state.py").is_file()
          and (b26 / "reference" / "cb3d23_stage_state.json").is_file()
          and (b26 / "README.md").is_file()
          and "214" in read(b26 / "README.md")
          and "仍 `P` 不 `Y`" in read(b26 / "README.md"))
    check("③b 15 份冻结证据与 BS manifest 一致",
          len([p for p in (b26 / "cases").glob("*") if p.is_file()]) == 15
          and "cb3d23_stage_state_stage_stability.txt" in read(
              case_dir / "cb3d23_stage_state_manifest.txt"))
    check("③b CMakeLists / run_tests.ps1 / FE 总索引同步第 26 项",
          "stage_stability_3d_benchmark" in read(root / "CMakeLists.txt")
          and "stage_stability_3d" in read(root / "run_tests.ps1")
          and "-BuildDir" in read(root / "run_tests.ps1")
          and "26 个 CTest" in read(bench / "README.md")
          and "25 个 CTest" in read(bench / "README.md")
          and "mingw64/bin/g++.exe" in read(bench / "README.md"))
    check("③b 算例索引登记 CB3D23 / stage_stability_3d 且不复用旧编号",
          "CB3D23" in cases_idx
          and "stage_stability_3d" in cases_idx
          and "CB3D01~CB3D21" in cases_idx
          and "CB3D22" in cases_idx)

    # ---- 4. 文档 / 矩阵如实：③b 完成仍 P 不 Y ----
    check("③b 规格 §2.5 / §2.6 / §6 如实登记且明确仍 P 不 Y",
          "第二十次 ③b 补记" in spec
          and "③b 阶段实测收口" in spec
          and "③b 完成后的 B3 结论：仍 `P` 不 `Y`" in spec
          and "19. 第二十次 ③b 不虚报" in spec
          and "不得靠一个 opt-in 算例提前打勾" in spec
          and "**B3 结论：仍 `P` 不 `Y`**" in spec)
    check("③b 能力矩阵仍 | N | P | 且缺口条数不变",
          "| 非线性稳定全过程（球面弧长法 opt-in） | N | P |" in mat
          and "第二十次 ③b 记录" in mat
          and "仍 `P ≠ Y`" in mat
          and "B2 剩余 0 项" in mat)
    for name in ("README.md", "使用说明.md", "开发使用手册.md"):
        txt = read(ROOT / name)
        check(f"{name} 登记 ③b 且仍 P 不 Y",
              "③b" in txt and "STABASIS=STAGE" in txt
              and ("仍 `P`" in txt or "仍 `P ≠ Y`" in txt))

    # ---- 5. FE 侧四份文档同步（③b 收尾补记，防止回退后无人发现）----
    fe_docs = {
        "CHANGELOG.md": "③b 施工阶段中间态起点的全过程稳定",
        "input_format_3d.md": "STABASIS=STAGE",
        "USER_MANUAL.md": "9.11 施工阶段中间态全过程稳定",
        "DEVELOPMENT_GUIDE.md": "③b：施工阶段链路也必须回传",
    }
    for name, marker in fe_docs.items():
        txt = read(root / name)
        check(f"FE {name} 登记 ③b 且写明 {marker}",
              "③b" in txt and marker in txt)
    check("FE USER_MANUAL / DEVELOPMENT_GUIDE 如实写明 B3 仍 P 不 Y",
          "仍 `P` 不 `Y`" in read(root / "USER_MANUAL.md")
          and "仍 `P` 不 `Y`" in read(root / "DEVELOPMENT_GUIDE.md"))


def main() -> int:
    print("=" * 78)
    print("第十九次 ① / ② / ③a 与第二十次 ③b 全过程稳定接入层独立守卫（纯离线）")
    print("=" * 78)
    fe_contract()
    parser_contract()
    oracle()
    docs()
    stage2_contract()
    stage3_contract()
    stage4_contract()
    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(PASS)} 通过")
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
