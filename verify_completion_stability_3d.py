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

**本守卫存在本身就在锁住「① 完成不等于 B3 推 Y」**：任何一项把矩阵
涂成 `Y` 或删掉 §2.6 的阶段表，这里立刻红。
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


def main() -> int:
    print("=" * 78)
    print("第十九次 ① 成桥态全过程稳定接入层独立守卫（纯离线）")
    print("=" * 78)
    fe_contract()
    parser_contract()
    oracle()
    docs()
    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(PASS)} 通过")
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
