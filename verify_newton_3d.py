"""verify_newton_3d.py —— 内层共转列式 Newton 过程量回归。

覆盖六件事：

1. **解析层纯函数**：手写 ``[NEWTON]`` 段逐字段对拍（数值硬编码在断言里，
   不用被验代码反算）、未知行向前兼容、两种 ERROR 段。
2. **派生量与自检**：荷载步分组、残差末/首比、逐荷载步表、逐迭代表、
   阶段汇总表；validate 的故障注入（轮数不符 / 编号错乱 / λ 不符 /
   REFACT 不符 / RESFIRST/LAST 不同源 / 状态矛盾 / 末步未标收敛 / 未收敛）。
3. **段分派不串段**：``[NEWTON]`` 不能污染 ``[LENGTH]`` / ``[MODAL]`` /
   ``[CABLE]``；``.nlstage.txt`` 里每阶段一段，STAGE 字段与阶段 id 一致，
   ``[BEAM]`` 数据不被 S 行污染；没有段时明确给 ``no_newton_section``。
4. **结果层接入**：指标卡、info 校核、三张表、to_dict 键；线性链路的
   ERROR 段走 info、不摆空表、不假判不通过。
5. **独立 oracle（需内核）**：λ = k/NSTEP；每个荷载步末次残差命中
   tolForce*max(|Ftarget|,1e-12) 或位移增量判据；步内残差单调下降；
   REFACT 与 S 行重建标记一致；``[NEWTON]``（内层）与 ``[LENGTH]``
   （外层）轮数口径分开；staged 每阶段一段且 STAGE 字段匹配。
6. **端到端四出口**：报告独立章、残差图只出现一次、PDF 读回新章与
   逐荷载步表、页码与无星号残留。

用法：``python verify_newton_3d.py``（``--offline`` 只跑不依赖内核的判据）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from console_utf8 import setup_console

setup_console()
sys.path.insert(0, str(Path(__file__).parent))

import fem_eigen_cable
import fem_eigen_stage_nl
import newton_3d as N
import report_3d as R
from result_3d import SolveResult

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def near(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


#: 只属于内层 Newton 过程量的校核名。手工构造的最小 SolveResult 没有节点/
#: 荷载/内力数据，其它链路会正常给出不通过项；本回归只该对拍自己这一段。
_NEWTON_CHECKS = ("内层 Newton 与共转列式", "内层 Newton 收敛",
                  "共转列式求解口径")


def newton_checks(res: SolveResult) -> list:
    return [c for c in res.checks(None) if c.name in _NEWTON_CHECKS]


# ---------------------------------------------------------------- 手写段

_STAGE2 = """
KIND=newton_cr STATUS=ok
STAGE=2
NSTEP=2 NITER=5 MAXITER=25 TOLF=1e-06 TOLD=1e-09 CONVERGED=1 REFACT=2 RESFIRST=1250 RESLAST=6.355430947e-05
FUTURE_FIELD whatever=1
S 1 1 0.5 1250 0.01404426973 1250 1 0
S 1 2 0.5 14.46584951 9.643900878e-06 1250 1 0
S 1 3 0.5 6.440198017e-06 7.143052831e-11 1250 0 1
S 2 1 1 1250 0.0140442291 2500 0 0
S 2 2 1 6.355430947e-05 7.140580922e-10 2500 0 1
""".strip().splitlines()

_FINAL = """
KIND=newton_cr STATUS=ok
NSTEP=1 NITER=6 MAXITER=30 TOLF=1e-08 TOLD=1e-09 CONVERGED=1 REFACT=1 RESFIRST=19024291.18 RESLAST=0.02915085161
S 1 1 1 19024291.18 0.4578740125 3059597.38 1 0
S 1 2 1 4900282.175 0.1468978793 3059597.38 0 0
S 1 3 1 275780.1172 0.02117379432 3059597.38 0 0
S 1 4 1 7025.121017 0.0004874481821 3059597.38 0 0
S 1 5 1 3.680744951 8.546504189e-07 3059597.38 0 0
S 1 6 1 0.02915085161 8.765178802e-10 3059597.38 0 1
""".strip().splitlines()

_ERROR_SEG = """
KIND=none STATUS=not_applicable
NSTEP=0 NITER=0 MAXITER=0 TOLF=0 TOLD=0 CONVERGED=0 REFACT=0 RESFIRST=0 RESLAST=0
ERROR=no_newton_solution
""".strip().splitlines()

_CABLE_FILE = """
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
[NEWTON]
KIND=newton_cr STATUS=ok
NSTEP=1 NITER=2 MAXITER=30 TOLF=1e-08 TOLD=1e-09 CONVERGED=1 REFACT=1 RESFIRST=1000 RESLAST=0.001
S 1 1 1 1000 0.01 1000 1 0
S 1 2 1 0.001 1e-09 1000 0 1
[END]
""".strip()

_NLSTAGE_FILE = """
# total equilibrium; do not sum stage displacements
NSTAGES 2
STAGE 1 converged=1 ndof=12
[U]
1 3 -0.01
[CABLE]
9001 20000.0 9.99
[BEAM]
1 1 -100.0 0.0 12.0 0.0 50.0 0.0
1 2 100.0 0.0 12.0 0.0 -50.0 0.0
[NEWTON]
KIND=newton_cr STATUS=ok
STAGE=1
NSTEP=1 NITER=2 MAXITER=25 TOLF=1e-06 TOLD=1e-09 CONVERGED=1 REFACT=1 RESFIRST=1250 RESLAST=6.4e-06
S 1 1 1 1250 0.014 1250 1 0
S 1 2 1 6.4e-06 7.1e-11 1250 0 1
[ENDSTAGE]
STAGE 2 converged=1 ndof=12
[U]
1 3 -0.02
[CABLE]
9001 20000.0 9.99
[BEAM]
1 1 -100.0 0.0 12.0 0.0 50.0 0.0
1 2 100.0 0.0 12.0 0.0 -50.0 0.0
[NEWTON]
KIND=newton_cr STATUS=ok
STAGE=2
NSTEP=1 NITER=2 MAXITER=25 TOLF=1e-06 TOLD=1e-09 CONVERGED=1 REFACT=1 RESFIRST=1250 RESLAST=6.4e-06
S 1 1 1 1250 0.014 1250 1 0
S 1 2 1 6.4e-06 7.1e-11 1250 0 1
[ENDSTAGE]
""".strip()


def mutated(lines: list[str], repl: dict[int, str]) -> list[str]:
    out = list(lines)
    for i, v in repl.items():
        out[i] = v
    return out


def offline_checks() -> None:
    print("\n[解析层：逐字段对拍与向前兼容]")
    t = N.parse_newton_section(_STAGE2)
    check("头/阶段/元数据逐字段对拍",
          t["kind"] == "newton_cr" and t["status"] == "ok" and t["stage"] == 2
          and t["nstep"] == 2 and t["niter"] == 5 and t["maxiter"] == 25
          and near(t["tol_force"], 1e-6) and near(t["tol_disp"], 1e-9)
          and t["converged"] and t["refact"] == 2
          and near(t["res_first"], 1250.0)
          and near(t["res_last"], 6.355430947e-05))
    check("FUTURE_FIELD 未知行不破坏解析", len(t["steps"]) == 5
          and N.validate(t) == [], str(N.validate(t)))
    s = t["steps"]
    check("S 行数值与标志对拍",
          s[0]["load_step"] == 1 and s[0]["iter"] == 1
          and near(s[0]["lambda"], 0.5) and near(s[0]["residual"], 1250.0)
          and near(s[0]["du"], 0.01404426973)
          and near(s[0]["ref"], 1250.0)
          and s[0]["refactor"] and not s[0]["converged"]
          and s[2]["converged"] and not s[2]["refactor"])
    check("分组 = 2 个荷载步、每步末次收敛",
          [len(g) for g in N.load_step_groups(t)] == [3, 2]
          and all(g[-1]["converged"] for g in N.load_step_groups(t)))
    check("残差末/首比", near(N.residual_ratio(t), 6.355430947e-05 / 1250.0))
    f = N.parse_newton_section(_FINAL)
    check("成桥态单段没有 STAGE 字段", f["stage"] is None
          and f["nstep"] == 1 and f["niter"] == 6
          and N.validate(f) == [], str(N.validate(f)))
    check("跨荷载步残差允许跳升（不误判发散）",
          s[2]["residual"] < s[0]["residual"]
          and s[3]["residual"] > s[2]["residual"]
          and N.validate(t) == [])

    print("\n[ERROR 段与中文说明]")
    e = N.parse_newton_section(_ERROR_SEG)
    check("ERROR 段解析", e["status"] == "not_applicable"
          and e["error"] == "no_newton_solution" and e["steps"] == [])
    check("ERROR 有中文说明且不静默",
          "旧版内核" in N.status_note(e)
          and N.ERROR_TEXT["no_newton_section"].startswith("求解器未输出"))
    check("未收敛状态有中文说明",
          "未满足收敛容差" in N.STATUS_TEXT["not_converged"])

    print("\n[故障注入：validate 必须抓出来]")
    cases = [
        ("轮数不符", mutated(_STAGE2, {2: _STAGE2[2].replace("NITER=5", "NITER=9")})),
        ("荷载步数不符", mutated(_STAGE2, {2: _STAGE2[2].replace("NSTEP=2", "NSTEP=3")})),
        ("迭代编号错乱", mutated(_STAGE2, {4: "S 1 7 0.5 1250 0.014 1250 1 0"})),
        ("λ 与 k/NSTEP 不符", mutated(_STAGE2, {7: "S 2 1 0.75 1250 0.014 2500 0 0"})),
        ("REFACT 与重建标记不符", mutated(_STAGE2, {2: _STAGE2[2].replace("REFACT=2", "REFACT=3")})),
        ("RESFIRST 不同源", mutated(_STAGE2, {2: _STAGE2[2].replace("RESFIRST=1250", "RESFIRST=999")})),
        ("RESLAST 不同源", mutated(_STAGE2, {2: _STAGE2[2].replace("RESLAST=6.355430947e-05", "RESLAST=1")})),
        ("CONVERGED 与 STATUS 矛盾", mutated(_STAGE2, {2: _STAGE2[2].replace("CONVERGED=1", "CONVERGED=0")})),
        ("末步没标收敛", mutated(_STAGE2, {8: "S 2 2 1 6.355430947e-05 7.14e-10 2500 0 0"})),
        ("未收敛却给 STATUS=ok", mutated(_STAGE2, {0: "KIND=newton_cr STATUS=not_converged"})),
        ("not_applicable 却有 S 行",
         ["KIND=none STATUS=not_applicable", _STAGE2[2]] + _STAGE2[3:]),
    ]
    for name, lines in cases:
        got = N.validate(N.parse_newton_section(lines))
        check(f"故障注入：{name}", bool(got), "；".join(got)[:70])

    print("\n[段分派：两条链路都不串段]")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "cable_cable.txt"
        p.write_text(_CABLE_FILE, encoding="utf-8")
        r = fem_eigen_cable.parse_cable_result(p)
    check("cable.txt 同时解析出 [LENGTH] 与 [NEWTON]",
          r["cable_length"]["status"] == "ok"
          and r["cable_length"]["niter"] == 1
          and len(r["newton"]["traces"]) == 1
          and r["newton"]["traces"][0]["niter"] == 2
          and N.validate(r["newton"]["traces"][0]) == [])
    check("[CABLE] / [MODAL] / [BUCKLING] 没被 S 行污染",
          len(r["cables"]) == 1 and r["modal"]["error"] != "no_modal_section"
          and r["buckling"]["error"] == "nonlinear_state_not_supported")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "no_newton.txt"
        p.write_text(_CABLE_FILE.replace("[NEWTON]\n", "[NOPE]\n"), encoding="utf-8")
        r2 = fem_eigen_cable.parse_cable_result(p)
    check("旧内核无 [NEWTON] 段写 no_newton_section",
          r2["newton"]["error"] == "no_newton_section")
    rows = fem_eigen_stage_nl.parse_nlstage(_NLSTAGE_FILE)
    check("nlstage 每阶段一段且 STAGE 字段匹配 id",
          len(rows) == 2
          and [x["id"] for x in rows] == [1, 2]
          and [x["newton"]["stage"] for x in rows] == [1, 2]
          and all(N.validate(x["newton"]) == [] for x in rows))
    check("[BEAM] 数据不被 S 行污染",
          all(len(x["beams"][1]) == 2 for x in rows)
          and rows[0]["beams"][1][1]["My"] == 50.0
          and rows[0]["u"][(1, 3)] == -0.01)
    old_lines: list[str] = []
    skip = False
    for x in _NLSTAGE_FILE.splitlines():
        if x == "[NEWTON]":
            skip = True
            continue
        if x == "[ENDSTAGE]":
            skip = False
            old_lines.append(x)
            continue
        if not skip:
            old_lines.append(x)
    rows_old = fem_eigen_stage_nl.parse_nlstage("\n".join(old_lines))
    check("旧 stage_nl 无段逐阶段标 no_newton_section",
          len(rows_old) == 2
          and all(x["newton"]["error"] == "no_newton_section" for x in rows_old)
          and all(len(x["beams"][1]) == 2 for x in rows_old))

    print("\n[结果层接入]")
    res = SolveResult(kind="cable_stayed",
                      newton=N.bundle([N.parse_newton_section(_STAGE2),
                                       N.parse_newton_section(_FINAL)]))
    metrics = [m.label for m in res.metrics()]
    cs = newton_checks(res)
    tabs = res.tables(None)
    check("指标卡含内层 Newton 且显示阶段数", "内层 Newton" in metrics
          and "2 阶段" in next(m.value for m in res.metrics()
                              if m.label == "内层 Newton"))
    check("校核是 info 且不假判不通过",
          len(cs) == 2 and all(c.passed and c.level == "info" for c in cs),
          str([(c.name, c.passed) for c in cs]))
    check("三张表都进 tables()",
          all(k in tabs for k in ("newton_stages_table", "newton_step_table",
                                  "newton_iteration_table")))
    check("阶段汇总表两行、逐荷载步/逐迭代表行数自洽",
          len(tabs["newton_stages_table"]["rows"]) == 2
          and len(tabs["newton_step_table"]["rows"])
          == len(N.load_step_groups(N.parse_newton_section(_FINAL)))
          and len(tabs["newton_iteration_table"]["rows"]) == 6)
    d = res.to_dict()
    check("to_dict 带过程量状态",
          d["newton_traces"] == 2 and d["newton_niter"] == 11
          and d["newton_status"] == "ok" and d["newton_error"] == "")
    err_res = SolveResult(kind="grillage3d",
                          newton=N.empty_bundle("no_newton_section"))
    check("线性链路不摆空表、不假判不通过",
          not [k for k in err_res.tables(None) if k.startswith("newton_")]
          and len(newton_checks(err_res)) == 1
          and all(c.passed and c.level == "info"
                  for c in newton_checks(err_res)))
    check("空 dict 旧结果不产生校核",
          newton_checks(SolveResult(kind="grillage3d")) == [])


# ---------------------------------------------------------------- live

def live_checks() -> None:
    import tempfile
    from cable_stayed_3d import build_cable_stayed_3d
    from solver_3d import solve

    print("\n[内核 oracle：cable_solver 成桥态]")
    model = build_cable_stayed_3d()[0]
    out = Path("out/verify_newton")
    res = solve(model, kind="cable_stayed", out_dir=str(out))
    trs = res.newton_traces()
    check("成桥态产出一段内层 Newton", len(trs) == 1
          and trs[0]["status"] == "ok" and trs[0]["stage"] is None)
    t = trs[0]
    check("validate 全绿", N.validate(t) == [], str(N.validate(t))[:80])
    groups = N.load_step_groups(t)
    check("λ = k/NSTEP 且荷载步齐全", len(groups) == t["nstep"]
          and all(near(s["lambda"], k / t["nstep"])
                  for k, g in enumerate(groups, start=1) for s in g))
    check("每个荷载步末次残差命中 tolForce 容差",
          all(g[-1]["residual"] <= t["tol_force"] * max(g[-1]["ref"], 1e-12) * 1.001
              or g[-1]["du"] <= t["tol_disp"] * 1.001 for g in groups))
    check("步内残差单调下降",
          all(all(g[i + 1]["residual"] < g[i]["residual"]
                  for i in range(len(g) - 1)) for g in groups))
    check("REFACT 与 S 行重建标记一致",
          t["refact"] == sum(1 for s in t["steps"] if s["refactor"]))
    check("内外层轮数口径分开",
          res.cable_length.get("niter", 0) >= 1
          and t["niter"] > t["nstep"],
          f"外层 {res.cable_length.get('niter')} 轮 / 内层 "
          f"{t['nstep']} 步 {t['niter']} 次迭代")

    print("\n[内核 oracle：非线性施工阶段每阶段一段]")
    from verify_stage_nl import cantilever  # 与 verify_stage_nl 同一算例
    with tempfile.TemporaryDirectory() as td:
        rows = fem_eigen_stage_nl.solve_stages_nonlinear(
            cantilever(), Path(td), dat_name="stage_nl.dat")
    check("阶段数 >= 2 且每阶段一段",
          len(rows) >= 2 and all(N.validate(x["newton"]) == [] for x in rows),
          f"stages={len(rows)}")
    check("STAGE 字段逐阶段匹配",
          [x["newton"]["stage"] for x in rows] == [x["id"] for x in rows])
    check("每阶段都是全量平衡、各段独立",
          all(x["newton"]["nstep"] >= 1 for x in rows)
          and all(x["newton"]["converged"] for x in rows))

    print("\n[端到端：报告与四出口]")
    rep = R.build_document(res, model, out_dir=str(out))
    h1 = [b["text"] for b in rep.blocks if b["type"] == "h1"]
    check("报告含独立新章", "内层 Newton 与共转列式求解" in h1,
          " > ".join(h1))
    check("新章排在索长迭代章之后",
          h1.index("索长迭代与成桥态几何闭合")
          < h1.index("内层 Newton 与共转列式求解"))
    check("残差图与两张表都进报告",
          R.NEWTON_FIGURE_KEY in rep.figures
          and R.NEWTON_STEP_TABLE_KEY in rep.tables
          and R.NEWTON_ITERATION_TABLE_KEY in rep.tables)
    keys = [b.get("key") for b in rep.blocks if b["type"] == "figure"]
    check("残差图只出现一次", keys.count(R.NEWTON_FIGURE_KEY) == 1)
    detail = [b.get("key") for b in rep.blocks if b["type"] == "table"]
    check("逐荷载步表在正文、逐迭代表在文末明细表",
          R.NEWTON_STEP_TABLE_KEY in detail
          and R.NEWTON_ITERATION_TABLE_KEY in detail
          and detail.index(R.NEWTON_STEP_TABLE_KEY)
          < detail.index(R.NEWTON_ITERATION_TABLE_KEY))
    paths = R.write_all(rep, out, stem="newton_report", pdf=True)
    check("四出口都写出", all(Path(v).stat().st_size > 2000
                              for k, v in paths.items() if k != "zip"))
    from pypdf import PdfReader
    pdf = PdfReader(str(paths["pdf"]))
    text = "\n".join(pg.extract_text() or "" for pg in pdf.pages)
    check("PDF 读回新章与逐荷载步表",
          "内层 Newton 与共转列式求解" in text
          and "共转列式 Newton 逐荷载步收敛" in text)
    check("PDF 页码与星号残留",
          "BridgeStudio  |  1 /" in (pdf.pages[0].extract_text() or "")
          and "**" not in text)


def main() -> int:
    offline = "--offline" in sys.argv
    print("=" * 78)
    print("内层共转列式 Newton 过程量回归" + ("（离线）" if offline else ""))
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
