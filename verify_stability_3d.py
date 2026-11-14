"""verify_stability_3d.py —— B3 `[STABILITY]` 接入层的纯离线守卫。

不调求解器、不写 .dat、不重算力学。逐条锁住：

1. FE 源码契约：`cable_solver.cpp` 的 opt-in 分流、默认全关、
   不改 `NonlinearOptions3D`、保留 `[BUCKLING]` ERROR 段；
2. `stability_3d.py` 是唯一解析器且**只解析不重算**（不组装 K、不求 λ）；
3. 独立 oracle：手工构造的正常段逐字段解析正确、ERROR 段 8 类码中文齐备；
4. 缺段写「做不了」、不摆空表、`lambda_final is None` 不补 0；
5. 报告章与段同源，λ 与线弹性屈曲 λ 分列不互冒充；
6. 能力矩阵该行 `P` 不是 `Y`，规格 §2.5 五条边界在位。
"""

from __future__ import annotations

import sys
from pathlib import Path

from console_utf8 import setup_console

setup_console()

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import stability_3d as st

PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, bool(ok), str(detail)))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""


def fe() -> Path | None:
    from solve_2d import _fem_eigen_dir
    return _fem_eigen_dir()


SAMPLE = [
    "KIND=spherical_arc_length STATUS=ok "
    "BASIS=reference_configuration PATHMETHOD=SPHERE",
    "NSTEP=2 NPOINT=3 CONVERGED=1 CTRLNODE=2 CTRLDOF=2 STRAIN=1 PSI=1",
    "LAMBDA_FINAL=0.25 ERROR=none",
    "I STEP=0 LAMBDA=0 CTRL=0 RES=0 ITER=0 DS=0.005 PIVOT=1 CONVERGED=1",
    "I STEP=1 LAMBDA=0.5 CTRL=-0.1 RES=0.001 ITER=2 DS=0.005 "
    "PIVOT=5.2e-09 CONVERGED=1",
    "I STEP=2 LAMBDA=0.25 CTRL=-0.2 RES=0.002 ITER=3 DS=0.00525 "
    "PIVOT=5.1e-09 CONVERGED=1",
]


def fe_contract() -> None:
    root = fe()
    if root is None:
        check("定位 FEM-Eigen 源码目录", False, "未定位")
        return
    cs = read(root / "cable_solver.cpp")
    out = read(root / "stability_output_3d.hpp")
    nl = read(root / "fem_3d_nl.hpp")
    check("cable_solver 有 STABILITY=1 opt-in 分流",
          "if (cfg.stability.enabled)" in cs
          and "runStabilityMode" in cs)
    check("默认全关、StabilityCfg 独立、主求解器零改动",
          "bool enabled = false;" in cs
          and "struct StabilityCfg" in cs
          and "NonlinearOptions3D nl;" in cs
          and "solveModel3DNonlinear" in nl and "Ftarget" in nl
          and "ArcLength" not in nl and "Riks" not in nl)
    check("保留 [BUCKLING] ERROR=nonlinear_state_not_supported",
          '"nonlinear_state_not_supported"' in cs)
    check("[STABILITY] 段置于 [END] 前且有 ERROR 双函数",
          'stabilityErrorSection3D' in out
          and 'formatStabilitySection3D' in out
          and 'fprintf(f, "[END]\\n")' in cs)
    check("13 个 opt-in 键带 STAB 前缀（不撞既有 TOL / MAXITER）",
          all(k in cs for k in ('"STABILITY"', '"PATHMETHOD"', '"CTRLNODE"',
                                '"CTRLDOF"', '"CTRLTARGET"', '"DS0"',
                                '"DSMIN"', '"DSMAX"', '"PSI"',
                                '"MAXPATHSTEP"', '"STABITER"', '"STABTOL"',
                                '"STRAIN"')))
    check("input_format_3d 登记新段与 PIVOT 如实口径",
          "[STABILITY]" in read(root / "input_format_3d.md")
          and "不是切线奇异判据" in read(root / "input_format_3d.md"))


def parser_is_read_only() -> None:
    src = read(ROOT / "stability_3d.py")
    check("解析器不组装 K / 不重算 λ / 不反算荷载",
          all(k not in src for k in ("numpy", "np.linalg", "inv(", "lu.solve",
                                     "assemble", "stiffness"))
          and "不求解、不重算、不补零" in src)
    check("解析器声明唯一口径与 λ ≠ 规范安全系数",
          "唯一解析器" in src and "不是 JTG 3362 的稳定安全系数" in src
          and "BOUNDARY_NOTE" in src)
    check("解析器不消费 [BUCKLING] 段",
          "parse_buckling_section" not in src and "linear_elastic" not in src)


def oracle() -> None:
    r = st.parse_stability_section(SAMPLE)
    check("正常段：6 个标量字段逐格解析",
          (r["kind"], r["status"], r["basis"], r["pathmethod"])
          == ("spherical_arc_length", "ok",
              "reference_configuration", "SPHERE")
          and (r["nstep"], r["npoint"], r["ctrlnode"], r["ctrldof"],
               r["strain"], r["psi"], r["converged"])
          == (2, 3, 2, 2, 1, 1.0, True), str(r["npoint"]))
    check("正常段：终点 λ 与 3 个平衡点逐值同源",
          r["lambda_final"] == 0.25 and len(r["points"]) == 3
          and r["points"][1]["lambda"] == 0.5
          and r["points"][1]["ctrl"] == -0.1
          and r["points"][2]["iter"] == 3
          and r["points"][2]["pivot"] == 5.1e-09)
    tbl = st.stability_path_table(r)
    check("报告表 v = -CTRL（不反号丢失、不补 0）",
          tbl is not None and tbl[1][2] == 0.1 and len(tbl) == 3)
    check("ERROR=none 不当作错误码",
          r["error"] == "" and st.stability_error_text(r) == "")

    for code in ("zero_reference_load", "empty_dof_map", "invalid_control_dof",
                 "arc_length_step_collapsed", "max_steps_reached",
                 "solver_exception", "unsupported_path_method",
                 "no_equilibrium_point", "no_stability_section"):
        e = st.empty_stability(code)
        txt = st.stability_error_text(e)
        check(f"错误码 {code} 有中文说明", bool(txt) and code not in txt
              or (code == "no_stability_section" and "做不了" in txt))

    err = st.parse_stability_section([
        "KIND=none STATUS=not_applicable "
        "BASIS=reference_configuration",
        "NSTEP=0 NPOINT=0 CONVERGED=0",
        "ERROR=invalid_control_dof",
    ])
    check("ERROR 段：不出空表、λ 不补 0、写中文做不了",
          err["points"] == [] and err["lambda_final"] is None
          and st.stability_path_table(err) is None
          and "做不了" in st.stability_report_text(err)
          and "控制" in st.stability_report_text(err))

    missing = st.parse_stability_section([])
    check("缺段写 no_stability_section 与中文做不了",
          missing["error"] == "no_stability_section"
          and "做不了" in st.stability_report_text(missing)
          and st.stability_path_table(missing) is None)
    check("报告章含如实边界、λ 与全过程极值点不互冒充",
          st.BOUNDARY_NOTE in st.stability_report_text(r)
          and "不是规范稳定安全系数" in st.stability_report_text(r))


def docs() -> None:
    mat = read(ROOT / "能力边界矩阵.md")
    spec = read(ROOT / "B类后续项接入规格.md")
    guide = read(ROOT / "开发使用手册.md")
    check("能力矩阵该行 3D=P 不是 Y",
          "| 非线性稳定全过程（球面弧长法 opt-in） | N | P |" in mat)
    check("规格 §2.5 五条边界在位",
          "### 2.5 如实边界" in spec and "P ≠ Y" in spec
          and "分支屈曲" in spec and "CR 梁后屈曲" in spec
          and "初始缺陷" in spec and "材料非线性" in spec)
    check("规格 §2.3 登记 STAB 前缀理由与 13 键",
          "STAB 前缀" in spec or "必须带 `STAB`" in spec)
    check("开发手册登记第十七次接入与边界",
          "第十七次" in guide and "stability_3d.py" in guide
          and "P` 不标 `Y`" in guide)
    check("README / 使用说明同步 B3 不再写「未实现力学」",
          "STABILITY=1" in read(ROOT / "README.md")
          and "第十七次" in read(ROOT / "使用说明.md"))


def main() -> int:
    print("=" * 78)
    print("B3 [STABILITY] 接入层独立守卫（纯离线）")
    print("=" * 78)
    fe_contract()
    parser_is_read_only()
    oracle()
    docs()
    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(PASS)} 通过")
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
