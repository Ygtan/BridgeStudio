"""verify_cable_length_3d.py —— 索长迭代 / 成桥态几何闭合过程量回归。

覆盖五件事：

1. **解析层纯函数**：手写 ``[LENGTH]`` 段逐字段对拍（数值硬编码在断言里，
   不用被验代码反算）、未知行向前兼容、三种 ERROR 段。
2. **派生量与自检**：末轮误差、收敛率、两种误差口径、影响矩阵诊断、
   逐轮表 / 逐索表；validate 的各类故障注入（轮数不符 / 编号错乱 /
   状态矛盾 / J 维度不符 / 终值不同源）。
3. **段分派不串段**：新段不能污染 ``[MODAL]`` / ``[BUCKLING]`` /
   ``[CABLE]``；没有段时明确给 ``no_length_section``。
4. **结果层接入**：SolveResult 的指标卡、info 校核、两张表、summary 键。
5. **独立 oracle + 端到端**（需内核）：影响矩阵 J 对角必须为负
   （∂T/∂L0 < 0 的物理事实）、逐轮控制误差单调下降且末轮命中容差、
   ``[LENGTH]`` 的 E 行与 ``[CABLE]`` 段的 T/L0/chord 同源对拍、
   TENSION 的 geomErr 稳定在非零（索力命中不等于线形闭合）、
   COMPLETION 走几何闭合、STATIC 写 ERROR 段；四出口 PDF 读回新章。

用法：``python verify_cable_length_3d.py``（``--offline`` 只跑不依赖内核的判据）。
"""

from __future__ import annotations

import math
import subprocess
import sys
import tempfile
from pathlib import Path

from console_utf8 import setup_console

setup_console()
sys.path.insert(0, str(Path(__file__).parent))

import cable_length_3d as CL
import fem_eigen_cable
import report_3d as R
from cable_stayed_3d import build_cable_stayed_3d
from result_3d import SolveResult

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def near(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


#: 只属于索长迭代过程量的校核名。手工构造的最小 SolveResult 没有节点/荷载/
#: 内力数据，其它链路会正常给出「模型规模」「非线性收敛」等不通过项；本回归
#: 只该对拍自己这一段，不能拿全量 checks() 去要求「无假不通过」。
_LENGTH_CHECKS = (
    "索长迭代与成桥态几何闭合", "成桥态外层迭代收敛", "迭代口径",
    "影响矩阵 / 敏感度", "索力命中与线形闭合的区别",
)


def length_checks(res: SolveResult) -> list:
    return [c for c in res.checks(None) if c.name in _LENGTH_CHECKS]


# ---------------------------------------------------------------- 手写段
_TENSION = """
KIND=cable_length STATUS=ok ERRKIND=tension_rel
NCABLES=2 NITER=3 MAXITER=15 TOL=1e-07 CONVERGED=1
SENS JAVAIL=1 JREBUILD=1 JDIAGMIN=-30544922.38 JDIAGMAX=-9627412.587
FUTURE_FIELD whatever=1
I 1 0.6239684236 0.05139830483 0.1564928983 0.001698811455 1 0 REBUILD
I 2 0.007025579264 0.1572006999 0.0008461001838 9.169278796e-06 1 0 -
I 3 3.069754663e-08 0.1574791316 0 0 1 0 CONVERGED
E 9001 682510.3021 682510.3027 36.39508919 36.41630034 36.40054945
E 9002 713978.7287 713978.7295 38.07977396 38.10295169 38.07886553
NJ=2
J 0 0 -30544922.38
J 0 1 1791540.618
J 1 0 1791540.618
J 1 1 -9627412.587
""".strip().splitlines()

_COMPLETION = """
KIND=erection_geometry STATUS=ok ERRKIND=closure_m
NCABLES=2 NITER=3 MAXITER=20 TOL=1e-08 CONVERGED=1
SENS JAVAIL=0 JREBUILD=3 JDIAGMIN=0.8562 JDIAGMAX=0.9526
I 1 0.1583235092 0.05139830483 0.2190873234 0 1 1 REBUILD
I 2 0.003029056139 0.000948262776 0.006921262578 0 1 1 -
I 3 1.421777418e-09 3.892779432e-10 0 0 1 0 CONVERGED
E 9001 682510.3021 682510.3057 36.37934694 36.40054945 36.40054945
E 9002 713978.7287 713978.7310 38.05570138 38.07886553 38.07886553
""".strip().splitlines()

_FULL_FILE = """
cable_solver v1
mode=COMPLETION
model nodes=10 elements=8 cables=2 dofs=20
converged=1 iterations=3 elapsed_ms=12.0
[CABLE]
9001 0 682510.3027 36.39508919 36.41630034 36.40054945 0.01575089
[MODAL]
NMODES=0 NDOF=0 BASIS=xyz
[BUCKLING]
ERROR=nonlinear_state_not_supported
[LENGTH]
KIND=cable_length STATUS=ok ERRKIND=tension_rel
NCABLES=2 NITER=1 MAXITER=15 TOL=1e-07 CONVERGED=1
SENS JAVAIL=0 JREBUILD=0 JDIAGMIN=0 JDIAGMAX=0
I 1 1e-09 0.01 0 0 1 0 CONVERGED
E 9001 682510.3021 682510.3027 36.39508919 36.41630034 36.40054945
[END]
""".strip()


def offline_checks() -> None:
    print("\n[解析层：手写段逐字段对拍]")
    cl = CL.parse_cable_length_section(_TENSION)
    check("KIND / STATUS / ERRKIND",
          cl["kind"] == "cable_length" and cl["status"] == "ok"
          and cl["err_kind"] == "tension_rel")
    check("元字段",
          (cl["ncables"], cl["niter"], cl["maxiter"]) == (2, 3, 15)
          and near(cl["tol"], 1e-7) and cl["converged"] is True)
    check("SENS 字段",
          cl["sens"]["j_available"] is True and cl["sens"]["j_rebuild"] == 1
          and near(cl["sens"]["j_diag_min"], -30544922.38, 1e-6))
    check("I 行数值硬编码对拍",
          near(cl["steps"][0]["err"], 0.6239684236)
          and near(cl["steps"][1]["geom_err"], 0.1572006999)
          and near(cl["steps"][0]["step_rel_max"], 0.001698811455))
    check("I 行 flags 解析",
          cl["steps"][0]["rebuilt"] and not cl["steps"][1]["rebuilt"]
          and cl["steps"][2]["converged"]
          and cl["steps"][2]["flags"] == ["CONVERGED"])
    check("E 行单位换算与派生量",
          near(cl["cables"][0]["T_target_kN"], 682.5103021)
          and near(cl["cables"][0]["dL_m"], 0.01575089)
          and cl["cables"][0]["T_err_rel"] > 0.0)
    check("NJ / J 矩阵",
          cl["nj"] == 2 and len(cl["J"]) == 2 and len(cl["J"][0]) == 2
          and near(cl["J"][0][1], 1791540.618))
    check("未知行向前兼容（不崩、不多读）",
          len(cl["steps"]) == 3 and len(cl["cables"]) == 2)
    check("validate 正常返回空", CL.validate(cl) == [])

    print("\n[派生量]")
    check("末轮控制误差", near(CL.final_error(cl), 3.069754663e-08))
    rate = CL.mean_convergence_rate(cl)
    check("收敛率几何均值在 (0,1)", rate is not None and 0.0 < rate < 1.0,
          f"{rate:.4g}")
    check("两种误差口径表头不同",
          CL.err_unit(cl) == "相对偏差"
          and CL.err_unit(CL.parse_cable_length_section(_COMPLETION)) == "闭合误差 (m)")
    check("口径说明不混用两种迭代",
          "MODE=TENSION" in CL.mode_note(cl)
          and "MODE=COMPLETION" in CL.mode_note(
              CL.parse_cable_length_section(_COMPLETION)))
    check("影响矩阵说明含 J = M·D", "J = M·D" in CL.sens_note(cl))
    check("表格行数与列数",
          len(CL.iteration_table(cl)["rows"]) == 3
          and len(CL.iteration_table(cl)["headers"]) == 6
          and len(CL.cable_table(cl)["rows"]) == 2)
    check("注记不含 markdown 星号",
          all("**" not in str(x) for x in (
              CL.mode_note(cl), CL.sens_note(cl),
              CL.iteration_table(cl)["note"], CL.cable_table(cl)["note"])))

    print("\n[ERROR 段与故障注入]")
    for code, why in (("no_iteration_static_mode", "STATIC"),
                      ("no_iteration_suspension_formfinding", "找形"),
                      ("no_length_section", "旧版内核")):
        e = CL.parse_cable_length_section(
            [f"KIND=none STATUS=not_applicable ERRKIND=-",
             "NCABLES=0 NITER=0 MAXITER=0 TOL=0 CONVERGED=0",
             f"ERROR={code}"])
        check(f"ERROR={code} 有中文说明且不静默",
              e["error"] == code and why in CL.status_note(e)
              and CL.validate(e) != [], CL.status_note(e)[:24])

    def mutated(repl: dict[int, str]) -> list[str]:
        out = list(_TENSION)
        for i, s in repl.items():
            out[i] = s
        return out

    cases = [
        ("轮数不符", mutated({1: "NCABLES=2 NITER=9 MAXITER=15 TOL=1e-07 CONVERGED=1"})),
        ("索数不符", mutated({1: "NCABLES=7 NITER=3 MAXITER=15 TOL=1e-07 CONVERGED=1"})),
        ("轮次编号错乱", mutated({4: "I 7 0.6239684236 0.051 0.15 0.001 1 0 REBUILD"})),
        ("状态与 CONVERGED 矛盾", mutated({0: "KIND=cable_length STATUS=not_converged ERRKIND=tension_rel"})),
        ("末轮无 CONVERGED 标记", mutated({6: "I 3 3.069754663e-08 0.157 0 0 1 0 -"})),
        ("未收敛仍标 ok", mutated({0: "KIND=cable_length STATUS=ok ERRKIND=tension_rel",
                                    6: "I 3 0.5 0.157 0 0 1 0 -"})),
        ("J 矩阵阶数与索数不符", mutated({9: "NJ=3"})),
        ("JAVAIL=0 却给 J", mutated({2: "SENS JAVAIL=0 JREBUILD=1 JDIAGMIN=-1 JDIAGMAX=-2"})),
        ("JAVAIL=1 却无 NJ", [x for x in _TENSION if not x.startswith("NJ=")
                                    and not x.startswith("J ")]),
    ]
    for name, lines in cases:
        issues = CL.validate(CL.parse_cable_length_section(lines))
        check(f"故障注入：{name} 被抓出", bool(issues), issues[0][:38] if issues else "")

    print("\n[段分派不串段]")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "x_cable.txt"
        p.write_text(_FULL_FILE, encoding="utf-8")
        raw = fem_eigen_cable.parse_cable_result(p)
    check("[LENGTH] 独立解析", raw["cable_length"]["kind"] == "cable_length"
          and raw["cable_length"]["niter"] == 1)
    check("[CABLE] 未被 LENGTH 的 E 行污染", len(raw["cables"]) == 1
          and raw["cables"][0]["elem"] == 9001)
    check("[MODAL] / [BUCKLING] 未被污染",
          raw["modal"]["error"] == "" and raw["buckling"]["error"]
          == "nonlinear_state_not_supported")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "old_cable.txt"
        p.write_text("cable_solver v1\nmode=STATIC\n[CABLE]\n[END]\n",
                     encoding="utf-8")
        old = fem_eigen_cable.parse_cable_result(p)
    check("旧内核无段 → no_length_section",
          old["cable_length"]["error"] == "no_length_section")

    print("\n[结果层接入]")
    res = SolveResult(kind="cable_stayed", cable_length=cl)
    metrics = [m.label for m in res.metrics()]
    own = length_checks(res)
    names = [c.name for c in own]
    tabs = res.tables(None)
    check("指标卡含成桥态迭代", "成桥态迭代" in metrics)
    check("校核含三条 info 且无假不通过",
          "成桥态外层迭代收敛" in names and "迭代口径" in names
          and "影响矩阵 / 敏感度" in names
          and all(c.passed and c.level == "info" for c in own),
          f"过程量校核 {len(own)} 条 / 全量 {len(res.checks(None))} 条")
    check("两张表都进 tables()", "cable_length_table" in tabs
          and "cable_length_cables_table" in tabs)
    d = res.to_dict()
    check("序列化带过程量状态",
          d.get("cable_length_kind") == "cable_length"
          and d.get("cable_length_status") == "ok"
          and d.get("cable_length_niter") == 3
          and d.get("cable_length_error") == "")
    err_res = SolveResult(kind="suspension", cable_length=CL.parse_cable_length_section(
        ["KIND=none STATUS=not_applicable ERRKIND=-",
         "ERROR=no_iteration_suspension_formfinding"]))
    err_own = length_checks(err_res)
    check("无迭代路径不摆空表、不判不通过",
          "cable_length_table" not in err_res.tables(None)
          and len(err_own) == 1
          and all(c.passed and c.level == "info" for c in err_own))


def run_mode(mode: str, tmp: Path) -> dict:
    """自包含地生成一个 .dat、跑内核、读回解析结果。"""
    model = build_cable_stayed_3d()[0]
    text = model.to_dat()
    assert "MODE=COMPLETION" in text, "建造器默认应为 COMPLETION"
    dat = tmp / f"{mode.lower()}.dat"
    dat.write_text(text.replace("MODE=COMPLETION", f"MODE={mode}", 1),
                   encoding="utf-8")
    exe = fem_eigen_cable.find_cable_solver()
    proc = subprocess.run([str(exe), str(dat)], capture_output=True,
                          text=True, cwd=str(tmp), timeout=300)
    result = dat.with_name(dat.stem + "_cable.txt")
    if proc.returncode not in (0, 3):
        raise RuntimeError(f"cable_solver rc={proc.returncode}: {proc.stdout[:400]}")
    raw = fem_eigen_cable.parse_cable_result(result)
    return {"raw": raw, "cl": raw["cable_length"], "cables": raw["cables"],
            "dat": dat, "result": result}


def live_checks() -> None:
    print("\n[内核 oracle：TENSION 索长迭代]")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        t = run_mode("TENSION", tmp)
        cl, cables = t["cl"], t["cables"]
        check("TENSION 走索长迭代并收敛",
              cl["kind"] == "cable_length" and cl["status"] == "ok"
              and cl["err_kind"] == "tension_rel", f"niter={cl['niter']}")
        check("影响矩阵 J 完整", cl["sens"]["j_available"] and cl["nj"] > 0
              and len(cl["J"]) == cl["nj"], f"NJ={cl['nj']}")
        diag = [cl["J"][i][i] for i in range(min(cl["nj"], len(cl["J"])))]
        check("独立 oracle：J 对角全负（∂T/∂L0 < 0）",
              bool(diag) and all(v < 0 for v in diag),
              f"min={min(diag):.4g}" if diag else "缺")
        off = [cl["J"][i][j] for i in range(min(cl["nj"], 8))
               for j in range(min(cl["nj"], 8)) if i != j]
        check("非对角项非零（索间耦合确实进了 J）",
              any(abs(v) > 1e-6 for v in off))
        errs = [s["err"] for s in cl["steps"]]
        check("独立 oracle：逐轮控制误差单调下降",
              len(errs) >= 2 and all(errs[i + 1] < errs[i] for i in range(len(errs) - 1)),
              f"{errs[0]:.3g} → {errs[-1]:.3g}")
        check("独立 oracle：末轮命中容差",
              CL.final_error(cl) <= cl["tol"] * 1.001)
        check("两段同源：[LENGTH].E 与 [CABLE] 的 T / L0 / chord 一致",
              len(cl["cables"]) == len(cables)
              and all(near(cl["cables"][i]["T_final_kN"], cables[i]["T"] / 1000.0, 1e-6)
                      and near(cl["cables"][i]["L0_m"], cables[i]["L0"], 1e-8)
                      for i in range(min(len(cables), 6))))
        check("逐索命中目标索力",
              all(c["T_err_rel"] < 1e-5 for c in cl["cables"]))
        geoms = [s["geom_err"] for s in cl["steps"]]
        check("口径 oracle：索力收敛但成桥线形未闭合",
              geoms[-1] > 1e-3 and abs(geoms[-1] - geoms[-2]) < 0.05 * geoms[-1],
              f"末轮弦长偏差 {geoms[-1]:.4g} m")
        check("validate 全绿", CL.validate(cl) == [], str(CL.validate(cl))[:60])

        print("\n[内核 oracle：COMPLETION 几何闭合 / STATIC 无迭代]")
        c = run_mode("COMPLETION", tmp)
        cc = c["cl"]
        check("COMPLETION 走制造线形几何闭合",
              cc["kind"] == "erection_geometry" and cc["err_kind"] == "closure_m"
              and cc["status"] == "ok", f"niter={cc['niter']}")
        ce = [s["err"] for s in cc["steps"]]
        check("几何闭合误差单调下降且命中容差",
              len(ce) >= 2 and all(ce[i + 1] < ce[i] for i in range(len(ce) - 1))
              and ce[-1] <= cc["tol"] * 1.001,
              f"{ce[0]:.3g} → {ce[-1]:.3g} m")
        check("几何闭合不产出 J（两条路径不混）",
              not cc["sens"]["j_available"] and cc["nj"] == 0)
        s = run_mode("STATIC", tmp)
        check("STATIC 写 ERROR 段而非空表",
              s["cl"]["error"] == "no_iteration_static_mode"
              and s["cl"]["steps"] == [])

        print("\n[端到端：报告与四出口]")
        model = build_cable_stayed_3d()[0]
        from solver_3d import solve
        out = Path("out/verify_cable_length")
        res = solve(model, kind="cable_stayed", out_dir=str(out))
        rep = R.build_document(res, model, out_dir=str(out))
        h1 = [b["text"] for b in rep.blocks if b["type"] == "h1"]
        check("报告含独立新章", "索长迭代与成桥态几何闭合" in h1,
              " > ".join(h1))
        check("收敛图与两张表都进了报告",
              R.LENGTH_FIGURE_KEY in rep.figures
              and R.LENGTH_TABLE_KEY in rep.tables
              and R.LENGTH_CABLES_TABLE_KEY in rep.tables)
        keys = [b.get("key") for b in rep.blocks if b["type"] == "figure"]
        check("收敛图只出现一次", keys.count(R.LENGTH_FIGURE_KEY) == 1)
        detail = [b.get("key") for b in rep.blocks if b["type"] == "table"]
        check("逐轮表在正文、逐索表在文末明细表",
              R.LENGTH_TABLE_KEY in detail and R.LENGTH_CABLES_TABLE_KEY in detail
              and h1.index("明细表") < len(h1)
              and detail.index(R.LENGTH_TABLE_KEY)
              < detail.index(R.LENGTH_CABLES_TABLE_KEY))
        paths = R.write_all(rep, out, stem="cable_length_report", pdf=True)
        check("四出口都写出", all(Path(v).stat().st_size > 2000
                                  for k, v in paths.items() if k != "zip"))
        from pypdf import PdfReader
        pdf = PdfReader(str(paths["pdf"]))
        text = "\n".join(pg.extract_text() or "" for pg in pdf.pages)
        check("PDF 读回新章与逐轮数据",
              "索长迭代与成桥态几何闭合" in text
              and ("闭合误差 (m)" in text or "最大相对索力偏差" in text))
        check("PDF 页码与星号残留",
              "BridgeStudio  |  1 /" in (pdf.pages[0].extract_text() or "")
              and "**" not in text)


def main() -> int:
    offline = "--offline" in sys.argv
    print("=" * 78)
    print("索长迭代 / 成桥态几何闭合过程量回归" + ("（离线）" if offline else ""))
    print("=" * 78)
    offline_checks()
    if not offline:
        live_checks()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n" + "=" * 78)
    print(f"  {passed}/{len(RESULTS)} 通过")
    print("=" * 78)
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  FAIL  {name}  {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
