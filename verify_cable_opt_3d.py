"""verify_cable_opt_3d.py —— 带约束成桥目标索力优化（`[OPT]` 段）回归。

覆盖六件事：

1. **解析层纯函数**：手写 ``[OPT]`` 段逐字段对拍（数值硬编码在断言里，
   不用被验代码反算）、未知行向前兼容、ERROR 段、旧内核无段。
2. **派生量与自检**：逐轮表 / 逐索表、口径说明、``validate`` 的故障注入
   （T 行数不符 / 索引错乱 / 越界 / 上下界不可行 / APPLIED 与
   MPEAK_FINAL_KIND 矛盾 / 未改善却被采纳 / IRLS 轮数越界）。
3. **段分派不串段**：``[OPT]`` 的 I/T 行不能污染 ``[LENGTH]`` /
   ``[CABLE]``；OPTIMIZE=0 的 ERROR 段如实给出中文说明。
4. **结果层接入**：指标卡、info 校核、两张表、to_dict 键；未采纳 /
   未收敛 / 未请求都不许凭空多一条「不通过」。
5. **独立 oracle（需内核）**：默认 OPTIMIZE=0 数值口径不变；OPTIMIZE=1
   的 T\\* 落在箱约束内、线性化目标不升、APPLIED 与 tension_method 一致；
   未采纳时原索力逐位保留；两组 MPEAK 口径分列且与 [GIRDEREND] 同源。
6. **端到端四出口**：报告独立章、逐轮表在正文 / 逐索表在文末明细表、
   HTML 无星号残留、四出口都写出。

用法：``python verify_cable_opt_3d.py``（``--offline`` 只跑不依赖内核的判据）。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

from console_utf8 import setup_console

setup_console()
sys.path.insert(0, str(Path(__file__).parent))

import cable_opt_3d as O
import fem_eigen_cable
import report_3d as R
from result_3d import SolveResult

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def near(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


#: 只属于优化过程量的校核名。手工构造的最小 SolveResult 没有节点/荷载/
#: 内力数据，其它链路会正常给出不通过项；本回归只该对拍自己这一段。
_OPT_CHECKS = ("带约束成桥目标索力优化", "成桥目标索力优化",
               "优化目标与边界", "两组弯矩口径不可混用",
               "优化迭代收敛状态")


# 手写一段完整的 [OPT]（数值照抄内核输出，故意不用被验代码反算）
_FINAL = """KIND=constrained_cable_force STATUS=ok
OBJECTIVE=MINENERGY START=GIVEN OBJECTIVE_KIND=NORMALIZED
NCABLES=3 NMOMENTS=8 NBALANCE=2 IRLS=0
NITER=8 CONVERGED=1 CONVBY=projected_gradient APPLIED=1
TMIN=10000 TMAX=1500000 TMAXSRC=DEFAULT_1.5_RIGID
MPEAK_KIND=LINEAR_BARE
MPEAK0=88220430.11 MPEAK=47654595.99 BAL0=9050939.947 BAL=0.1405715031
MPEAK_FINAL_KIND=GIRDEREND_FINAL
MPEAK_FINAL=49012548.43
I 0 1 1.614327873e+14 42614520.76 568212.6337 0.2372345649
I 0 8 -11281406.28 47654595.99 0.1405715031 3.049710149e-08
T 1 794490.7271 987799.9369 10000 1500000
T 2 713978.7287 802111.1234 10000 1500000
T 3 682510.3021 755000.4321 10000 1500000
"""

_NO_OPT = """KIND=constrained_cable_force STATUS=no_optimization_requested
ERROR=no_optimization_requested
"""

_NOT_APPLIED = """KIND=constrained_cable_force STATUS=objective_not_improved
OBJECTIVE=MINMAXM START=GIVEN OBJECTIVE_KIND=NORMALIZED
NCABLES=2 NMOMENTS=8 NBALANCE=2 IRLS=4
NITER=422 CONVERGED=1 CONVBY=projected_gradient APPLIED=0
TMIN=28056.32906 TMAX=2104224.68 TMAXSRC=DEFAULT_1.5_RIGID
MPEAK_KIND=LINEAR_BARE
MPEAK0=35504728.63 MPEAK=120818421.8 BAL0=4383519.447 BAL=1936327.432
MPEAK_FINAL_KIND=NOT_APPLICABLE
MPEAK_FINAL=0
I 1 1 -7.307353284 70342273.09 87162.12607 0.2260755227
T 1 682510.3021 900000 28056.32906 2104224.68
T 2 713978.7287 950000 28056.32906 2104224.68
"""

#: 段分派回归：[OPT] 夹在 [LENGTH] 与 [CABLE] 等段之间，I/T 行不得串段。
_MIXED = """[GIRDEREND]
101 1 -93241.45069 31.22805417 -108016.9498
[OPT]
KIND=constrained_cable_force STATUS=ok
NCABLES=1 NMOMENTS=2 NBALANCE=0 IRLS=0
NITER=2 CONVERGED=1 CONVBY=projected_gradient APPLIED=1
TMIN=1 TMAX=1000 TMAXSRC=USER
MPEAK_KIND=LINEAR_BARE
MPEAK0=100 MPEAK=80 BAL0=0 BAL=0
MPEAK_FINAL_KIND=GIRDEREND_FINAL
MPEAK_FINAL=82
I 0 1 1.0 90 0 0.1
T 1 100 80 1 1000
[END]
"""


#: 体系守卫手写段：守卫拒绝（APPLIED=0，实测列属于原方案）。
_SYS_REJECTED = """KIND=constrained_cable_force STATUS=system_check_rejected
OBJECTIVE=MINENERGY START=GIVEN OBJECTIVE_KIND=NORMALIZED
NCABLES=2 NMOMENTS=8 NBALANCE=0 IRLS=0
NITER=196 CONVERGED=1 CONVBY=projected_gradient APPLIED=0
TMIN=28056.32906 TMAX=2104224.68 TMAXSRC=DEFAULT_1.5_RIGID
MPEAK_KIND=LINEAR_BARE
MPEAK0=35504728.63 MPEAK=29328621.44 BAL0=0 BAL=0
MPEAK_FINAL_KIND=NOT_APPLICABLE
MPEAK_FINAL=0
SYSCHECK=GUARDED AVAILABLE=1
SYSCHECK_LIMITS DECKSHARE_MIN=90 DECKSHARE_MAX=100 ADJRATIO_MAX=1.5
SYSCHECK_PRED DECKSHARE=89.04 SUPPORT_KN=3944.29 ADJRATIO=31.98 AT_X=261.25,267.5
SYSCHECK_FINAL_KIND=ORIGINAL_TENSIONS
SYSCHECK_FINAL DECKSHARE=99.31 SUPPORT_KN=250 ADJRATIO=1.08 AT_X=248.75,255
SYSCHECK_PASS=0 REJECT=system_deck_share_low,system_adjacent_jump
I 0 1 1.0 33000000 0 0.1
T 1 682510.3021 28056.32906 28056.32906 2104224.68
T 2 713978.7287 2104224.68 28056.32906 2104224.68
"""

#: 体系守卫手写段：三条全过（未收敛但仍被采纳，如实标 CONVERGED=0）。
_SYS_PASSED = """KIND=constrained_cable_force STATUS=ok
OBJECTIVE=MINMAXM START=GIVEN OBJECTIVE_KIND=NORMALIZED
NCABLES=2 NMOMENTS=8 NBALANCE=0 IRLS=4
NITER=324 CONVERGED=0 CONVBY=none APPLIED=1
TMIN=420845.0 TMAX=2104224.68 TMAXSRC=DEFAULT_1.5_RIGID
MPEAK_KIND=LINEAR_BARE
MPEAK0=35504728.63 MPEAK=25364000 BAL0=0 BAL=0
MPEAK_FINAL_KIND=GIRDEREND_FINAL
MPEAK_FINAL=21674900
SYSCHECK=GUARDED AVAILABLE=1
SYSCHECK_LIMITS DECKSHARE_MIN=90 DECKSHARE_MAX=100 ADJRATIO_MAX=1.5
SYSCHECK_PRED DECKSHARE=97.92 SUPPORT_KN=748.05 ADJRATIO=1.39 AT_X=211.25,217.5
SYSCHECK_FINAL_KIND=OPTIMIZED_TENSIONS
SYSCHECK_FINAL DECKSHARE=97.92 SUPPORT_KN=748.05 ADJRATIO=1.39 AT_X=142.5,148.75
SYSCHECK_PASS=1 REJECT=none
I 1 1 -1.2 30000000 0 0.1
I 2 8 -1.5 25364000 0 1e-6
T 1 682510.3021 700000 420845.0 2104224.68
T 2 713978.7287 760000 420845.0 2104224.68
"""


def offline_checks() -> None:
    print("\n[解析层：手写段逐字段对拍]")
    t = O.parse_cable_opt_section(_FINAL.splitlines())
    check("段头 / 状态", t["kind"] == "constrained_cable_force"
          and t["status"] == "ok", str(t["status"]))
    check("目标 / 起点 / 归一化口径",
          t["objective"] == "MINENERGY" and t["start"] == "GIVEN"
          and t["objective_kind"] == "NORMALIZED")
    check("维度字段", (t["ncables"], t["nmoments"], t["nbalance"], t["irls"])
          == (3, 8, 2, 0))
    check("迭代与采纳状态",
          t["niter"] == 8 and t["converged"] and t["applied"]
          and t["converged_by"] == "projected_gradient")
    check("上下界与来源",
          near(t["tmin"], 10000) and near(t["tmax"], 1500000)
          and t["tmax_source"] == "DEFAULT_1.5_RIGID")
    check("线性化目标量逐字段",
          t["mpeak_kind"] == "LINEAR_BARE"
          and near(t["mpeak0"], 88220430.11)
          and near(t["mpeak"], 47654595.99)
          and near(t["bal0"], 9050939.947)
          and near(t["bal"], 0.1405715031))
    check("成桥态回填与口径分列",
          t["mpeak_final_kind"] == "GIRDEREND_FINAL"
          and near(t["mpeak_final"], 49012548.43)
          and t["mpeak_final_kind"] != t["mpeak_kind"])
    check("逐轮表 2 行且末行数值对拍",
          len(t["steps"]) == 2
          and near(t["steps"][-1]["objective"], -11281406.28)
          and near(t["steps"][-1]["moment_peak"], 47654595.99)
          and near(t["steps"][-1]["balance"], 0.1405715031))
    check("逐索表 3 行且 T0/T/lo/hi 对拍",
          len(t["cables"]) == 3
          and near(t["cables"][0]["T0"], 794490.7271)
          and near(t["cables"][0]["T"], 987799.9369)
          and near(t["cables"][0]["lo"], 10000))
    check("validate 全绿", O.validate(t) == [], str(O.validate(t))[:100])
    check("has_data 为真", O.has_data(t))

    print("\n[向前兼容与错误段]")
    t2 = O.parse_cable_opt_section(_FINAL.splitlines() + [
        "FUTURE_FIELD=abc", "SOMETHING 1 2 3"])
    check("未知行不破坏解析且不影响数值",
          O.validate(t2) == [] and near(t2["mpeak"], t["mpeak"]))
    no = O.parse_cable_opt_section(_NO_OPT.splitlines())
    check("OPTIMIZE=0 ERROR 段", no["error"] == "no_optimization_requested"
          and no["status"] == "no_optimization_requested"
          and not no["cables"] and not O.has_data(no) and O.validate(no) == [])
    check("ERROR 段有中文说明且不静默",
          "OPTIMIZE=0" in O.error_note(no) and "链路数值逐位不变" in O.error_note(no))
    old = O.parse_cable_opt_section([])
    old["error"] = "no_opt_section"
    check("旧内核无段有中文说明", "旧版内核" in O.error_note(old)
          or "[OPT]" in O.error_note(old))
    na = O.parse_cable_opt_section(_NOT_APPLIED.splitlines())
    check("未采纳段：APPLIED=0 且 NOT_APPLICABLE",
          not na["applied"] and na["status"] == "objective_not_improved"
          and na["mpeak_final_kind"] == "NOT_APPLICABLE"
          and O.validate(na) == [])
    check("未采纳中文说明写明 T* 不能当设计索力",
          "不能当设计索力" in O.status_note(na))
    check("未收敛中文说明不冒充通过",
          "不得" in O.STATUS_TEXT["not_converged"])

    print("\n[故障注入：validate]")
    base = O.parse_cable_opt_section(_FINAL.splitlines())
    cases: list[tuple[str, dict]] = []
    d = copy.deepcopy(base); d["ncables"] = 4; cases.append(("T 行数不符", d))
    d = copy.deepcopy(base); d["cables"][1]["index"] = 7
    cases.append(("T 行索引错乱", d))
    d = copy.deepcopy(base); d["cables"][0]["T"] = 9e6
    cases.append(("优化结果越上界", d))
    d = copy.deepcopy(base); d["cables"][0]["T"] = -1.0
    cases.append(("优化结果越下界", d))
    d = copy.deepcopy(base); d["tmin"], d["tmax"] = 2e6, 1e3
    cases.append(("TMIN > TMAX 箱约束不可行", d))
    d = copy.deepcopy(base); d["mpeak_final_kind"] = "NOT_APPLICABLE"
    cases.append(("APPLIED=1 但成桥态口径不适用", d))
    d = copy.deepcopy(base); d["mpeak"] = 2 * d["mpeak0"]
    cases.append(("APPLIED=1 但线性化目标不降反升", d))
    d = copy.deepcopy(base); d["status"] = "objective_not_improved"
    cases.append(("未改善却被标已采纳", d))
    d = copy.deepcopy(base); d["steps"][0]["irls"] = 9
    cases.append(("逐轮表超出 IRLS 轮数", d))
    d = copy.deepcopy(base); d["irls"] = 4
    cases.append(("MINENERGY 却声明 IRLS 轮数", d))
    d = copy.deepcopy(base); d["steps"][0]["irls"] = 2; d["irls"] = 4
    cases.append(("MINENERGY 逐轮表出现轮内记录", d))
    for name, bad in cases:
        check(f"故障注入：{name}", bool(O.validate(bad)),
              str(O.validate(bad))[:70])

    print("\n[展示表与口径文案]")
    it = O.iteration_table(base)
    ct = O.cable_table(base)
    check("逐轮表 6 列 2 行", len(it["headers"]) == 6 and len(it["rows"]) == 2)
    check("逐索表 5 列 3 行", len(ct["headers"]) == 5 and len(ct["rows"]) == 3)
    check("表键与报告层契约一致（headers / rows / note / title）",
          all(k in it and k in ct for k in ("headers", "rows", "note", "title")))
    check("逐轮表注写明归一化目标不可混算",
          "归一化目标" in it["note"] and "混算" in it["note"])
    check("未采纳时逐索表注写明只是过程量",
          "只是过程量" in O.cable_table(na)["note"]
          and "不能当设计索力" in O.cable_table(na)["note"])
    check("口径说明同时点名三个量",
          all(k in O.scale_note(base) for k in
              ("MPEAK0", "MPEAK_FINAL", "无量纲", "不得互相相减")))
    check("汇总说明含状态 / 目标 / 采纳 / 口径",
          all(k in O.summary_note(base) for k in
              ("APPLIED=1", "最小化裸结构弯曲应变能", "成桥态", "口径分列")))
    check("目标说明写明软约束权衡与上界来源",
          "软约束" in O.objective_note(base)
          and "DEFAULT_1.5_RIGID" in O.objective_note(base)
          and "程序口径不是规范限值" in O.objective_note(base))

    print("\n[段分派：不串段]")
    tmp = Path("out/verify_cable_opt")
    tmp.mkdir(parents=True, exist_ok=True)
    p = tmp / "_mixed_cable.txt"
    p.write_text(_MIXED, encoding="utf-8")
    raw = fem_eigen_cable.parse_cable_result(p)
    check("[OPT] 被登记且解析出 1 根索",
          raw["cable_opt"]["status"] == "ok"
          and len(raw["cable_opt"]["cables"]) == 1,
          str(raw["cable_opt"].get("error", "")))
    check("[OPT] 的 I/T 行没污染 [GIRDEREND]",
          len(raw["girder_ends"]) == 1
          and near(raw["girder_ends"][0]["My"], 31.22805417))
    check("无 [OPT] 段时明确写 no_opt_section",
          (lambda q: q["cable_opt"]["error"] == "no_opt_section")(
              _parse_text(_NO_OPT)))

    print("\n[结果层接入]")
    res = SolveResult(kind="cable_stayed", cable_opt=base)
    met = [m.label for m in res.metrics()]
    check("指标卡出现「成桥索力优化」且写已采纳",
          "成桥索力优化" in met
          and any("已采纳" in str(getattr(m, "value", "")) for m in res.metrics()
                  if m.label == "成桥索力优化"))
    cc = [c for c in res.checks() if c.name in _OPT_CHECKS]
    check("校核是 info 且不假判不通过",
          len(cc) >= 3 and all(c.ok and c.level == "info" for c in cc),
          str([(c.name, c.ok, c.level) for c in cc]))
    tabs = res.tables()
    check("两张表都进 tables()",
          "cable_opt_iteration_table" in tabs
          and "cable_opt_cables_table" in tabs)
    d = res.to_dict()
    check("to_dict 五个优化键齐全",
          all(k in d for k in ("cable_opt_status", "cable_opt_applied",
                               "cable_opt_objective", "cable_opt_niter",
                               "cable_opt_error"))
          and d["cable_opt_applied"] is True)
    # 未采纳 / 未请求 / 旧内核三种状态都不能凭空多一条不通过。
    # 未采纳**应该**保留逐索表（过程量可见、表注警示不能当设计索力），
    # 未请求 / 旧内核则一张表都不给。
    for tag, co in (("未采纳", na), ("未请求", no), ("旧内核无段", old)):
        r = SolveResult(kind="cable_stayed", cable_opt=co)
        bad = [c for c in r.checks() if c.name in _OPT_CHECKS and not c.ok]
        tabs2 = r.tables()
        if tag == "未采纳":
            good = (not bad and "cable_opt_cables_table" in tabs2
                    and "只是过程量" in tabs2["cable_opt_cables_table"]["note"])
        else:
            good = not bad and not any(k.startswith("cable_opt") for k in tabs2)
        check(f"{tag}：不给假不通过、不摆空表", good,
              str([(c.name, c.ok) for c in bad]))
    check("旧序列化结果（空 dict）不产生优化校核",
          not [c for c in SolveResult(kind="cable_stayed").checks()
               if c.name in _OPT_CHECKS])
    check("线性链路适配器写 no_optimization_requested",
          "no_optimization_requested" in _linear_adapt_probe())

    print("\n[体系守卫：SYSCHECK 段解析与自检]")
    rej = O.parse_cable_opt_section(_SYS_REJECTED.splitlines())
    sc = rej["syscheck"]
    check("守卫拒绝段：模式 / 可用性 / 结论 / 原因码",
          sc["present"] and sc["mode"] == "GUARDED" and sc["available"]
          and sc["pass"] is False
          and sc["reject"] == ["system_deck_share_low",
                               "system_adjacent_jump"])
    check("守卫拒绝段：限值与预估数值逐字段对拍",
          sc["limits"] == {"deck_share_min": 90.0, "deck_share_max": 100.0,
                           "adjratio_max": 1.5}
          and near(sc["pred"]["deck_share"], 89.04)
          and near(sc["pred"]["support_kn"], 3944.29)
          and near(sc["pred"]["adjratio"], 31.98)
          and sc["pred"]["at_x"] == [261.25, 267.5])
    check("守卫拒绝段：成桥态实测标 ORIGINAL_TENSIONS 且为原方案值",
          sc["final_kind"] == "ORIGINAL_TENSIONS"
          and near(sc["final"]["deck_share"], 99.31)
          and near(sc["final"]["adjratio"], 1.08))
    check("守卫拒绝段：validate 全绿且未采纳",
          O.validate(rej) == [] and not rej["applied"]
          and rej["mpeak_final_kind"] == "NOT_APPLICABLE",
          str(O.validate(rej))[:90])
    check("守卫拒绝中文说明写明原因与保留原索力",
          "体系守卫拒绝" in O.STATUS_TEXT["system_check_rejected"]
          and "不能当设计索力" in O.STATUS_TEXT["system_check_rejected"]
          and all(k in O.syscheck_note(rej) for k in
                  ("守卫拒绝采纳", "低于下限", "锯齿")))
    check("三条拒绝原因码都有中文说明",
          all(O.REJECT_TEXT.get(k) for k in
              ("system_deck_share_low", "system_support_uplift",
               "system_adjacent_jump")))
    st = O.system_check_table(rej)
    check("体系守卫表契约（headers / rows / note / title）",
          all(k in st for k in ("headers", "rows", "note", "title"))
          and len(st["headers"]) == 5 and len(st["rows"]) == 3)
    check("体系守卫表：两拒绝一满足，预估 / 实测分列",
          [r[4] for r in st["rows"]] == ["拒绝", "满足", "拒绝"]
          and "89.04" in st["rows"][0][2] and "99.31" in st["rows"][0][3])
    check("体系守卫表注写明不是规范条文、APPLIED=0 实测属原方案",
          "不是规范条文" in st["note"] and "原方案" in st["note"])

    okc = O.parse_cable_opt_section(_SYS_PASSED.splitlines())
    check("守卫放行段：三条全过、无原因码、validate 全绿",
          okc["syscheck"]["pass"] is True
          and okc["syscheck"]["reject"] == []
          and okc["syscheck"]["final_kind"] == "OPTIMIZED_TENSIONS"
          and O.validate(okc) == [], str(O.validate(okc))[:90])
    check("守卫放行表三行都是「满足」",
          [r[4] for r in O.system_check_table(okc)["rows"]] == ["满足"] * 3)
    na_sc = O.parse_cable_opt_section(
        _SYS_PASSED.replace("SYSCHECK=GUARDED", "SYSCHECK=OFF")
                   .replace("SYSCHECK_PASS=1 REJECT=none",
                            "SYSCHECK_PASS=NA REJECT=none").splitlines())
    check("SYSGUARD=0 诊断：未判定且只记录不拦截",
          na_sc["syscheck"]["pass"] is None
          and na_sc["syscheck"]["mode"] == "OFF"
          and O.validate(na_sc) == []
          and "只记录不拦截" in O.syscheck_note(na_sc)
          and [r[4] for r in O.system_check_table(na_sc)["rows"]]
          == ["未判定"] * 3)

    print("\n[体系守卫：validate 故障注入]")
    sc_cases: list[tuple[str, dict]] = []
    d = copy.deepcopy(okc); d["syscheck"]["pass"] = False
    sc_cases.append(("PASS=0 无原因码", d))
    d = copy.deepcopy(okc); d["syscheck"]["reject"] = ["system_deck_share_low"]
    sc_cases.append(("PASS=1 却带原因码", d))
    d = copy.deepcopy(okc); d["syscheck"]["pass"] = None
    d["syscheck"]["reject"] = ["system_deck_share_low"]
    sc_cases.append(("PASS=NA 却带原因码", d))
    d = copy.deepcopy(okc); d["syscheck"]["pass"] = None
    sc_cases.append(("GUARDED 却未判定", d))
    d = copy.deepcopy(okc); d["syscheck"]["mode"] = "NOT_AVAILABLE"
    sc_cases.append(("NOT_AVAILABLE 却有判据数值", d))
    d = copy.deepcopy(okc); d["syscheck"]["final_kind"] = ""
    sc_cases.append(("SYSCHECK_FINAL 缺口径标注", d))
    d = copy.deepcopy(rej); d["applied"] = True
    sc_cases.append(("体系拒绝却被标已采纳", d))
    d = copy.deepcopy(rej); d["syscheck"]["reject"] = ["unknown_code"]
    sc_cases.append(("未登记的拒绝原因码", d))
    for name, bad in sc_cases:
        check(f"守卫故障注入：{name}", bool(O.validate(bad)),
              str(O.validate(bad))[:70])

    r_sys = SolveResult(kind="cable_stayed", cable_opt=rej)
    check("体系守卫表进 tables() 且 to_dict 三个新键齐全",
          R.OPT_SYSTEM_TABLE_KEY in r_sys.tables()
          and all(k in r_sys.to_dict() for k in
                  ("cable_opt_syscheck_mode", "cable_opt_syscheck_pass",
                   "cable_opt_reject")))
    check("体系守卫拒绝时校核仍为 info、不凭空多一条不通过",
          all(c.ok and c.level == "info" for c in r_sys.checks()
              if c.name in _OPT_CHECKS))
    check("旧手写段（无 SYSCHECK）不摆守卫空表",
          R.OPT_SYSTEM_TABLE_KEY not in res.tables())


def _parse_text(txt: str) -> dict:
    p = Path("out/verify_cable_opt/_no_section_cable.txt")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("[GIRDEREND]\n1 1 1 2 3\n[END]\n" + txt.replace(
        "[OPT]", "[OTHER]"), encoding="utf-8")
    return fem_eigen_cable.parse_cable_result(p)


def _linear_adapt_probe() -> str:
    """线性梁格适配器不带 [OPT] 段时的兜底错误码。"""
    from solver_3d import _adapt_grillage
    r = SolveResult(kind="grillage3d")
    _adapt_grillage(None, {"meta": {}}, r)
    return r.cable_opt.get("error", "")


def _run_dat(tmp: Path, name: str, text: str) -> dict:
    import subprocess
    dat = tmp / f"{name}.dat"
    dat.write_text(text, encoding="utf-8")
    exe = fem_eigen_cable.find_cable_solver()
    proc = subprocess.run([str(exe), str(dat)], capture_output=True,
                          text=True, cwd=str(tmp), timeout=300)
    result = dat.with_name(dat.stem + "_cable.txt")
    if proc.returncode not in (0, 3):
        raise RuntimeError(f"cable_solver rc={proc.returncode}: {proc.stdout[:400]}")
    return fem_eigen_cable.parse_cable_result(result)


def live_checks() -> None:
    import tempfile
    from cable_stayed_3d import build_cable_stayed_3d
    from solver_3d import solve

    print("\n[内核 oracle：默认 OPTIMIZE=0 数值口径不变]")
    model = build_cable_stayed_3d()[0]
    out = Path("out/verify_cable_opt")
    out.mkdir(parents=True, exist_ok=True)
    res = solve(model, kind="cable_stayed", out_dir=str(out))
    co = res.cable_opt or {}
    check("默认链路写 no_optimization_requested",
          co.get("error") == "no_optimization_requested"
          and not O.has_data(co))
    check("默认链路不改 [LENGTH] / [NEWTON]",
          bool(res.cable_length.get("steps")) and bool(res.newton_traces()))
    base_T = [c.get("T") for c in res.cables]
    check("默认链路 tension_method 不是 ConstrainedOpt",
          "ConstrainedOpt" not in str(res.meta.get("tension_method", "")))

    print("\n[内核 oracle：OPTIMIZE=1 带约束优化]")
    text = model.to_dat()
    assert "MODE=COMPLETION" in text
    # 关掉塔顶平衡软约束取已验证的稳定路径；MINMAXM / MINENERGY 各跑一次。
    # 这一组专门验证「优化器本身」的采纳链路，因此显式 SYSGUARD=0 把体系
    # 守卫置于诊断模式（只记录不拦截）；守卫行为另由下面一组真实算例验证。
    variants = {
        "opt_mm": "OPTIMIZE=1\nOPTOBJ=MINMAXM\nOPTBALANCE=0\nSYSGUARD=0\n",
        "opt_en": "OPTIMIZE=1\nOPTOBJ=MINENERGY\nOPTBALANCE=0\nSYSGUARD=0\n",
        "opt_bal": "OPTIMIZE=1\nOPTOBJ=MINENERGY\nOPTBALANCE=1\nSYSGUARD=0\n",
    }
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        raws = {}
        for name, kv in variants.items():
            t = text.replace("*CABLE_BRIDGE\n", "*CABLE_BRIDGE\n" + kv, 1)
            raws[name] = {"raw": _run_dat(tmp, name, t), "dat": t}
        mm = raws["opt_mm"]["raw"]["cable_opt"]
        en = raws["opt_en"]["raw"]["cable_opt"]
        bal = raws["opt_bal"]["raw"]["cable_opt"]
        for tag, co2 in (("MINMAXM", mm), ("MINENERGY", en)):
            check(f"{tag}：段解析出 {co2.get('ncables')} 根索且有过程数据",
                  O.has_data(co2) and co2.get("ncables") > 0,
                  f"status={co2.get('status')}")
            check(f"{tag}：validate 全绿", O.validate(co2) == [],
                  str(O.validate(co2))[:90])
            check(f"{tag}：逐索 T* 全部落在箱约束内",
                  bool(co2.get("cables")) and all(
                      c["lo"] <= c["T"] <= c["hi"] for c in co2["cables"]))
            check(f"{tag}：线性化目标不升（守卫前提）",
                  co2["mpeak"] <= co2["mpeak0"] * (1 + 1e-6),
                  f"{co2['mpeak0']:.4g} → {co2['mpeak']:.4g}")
            check(f"{tag}：APPLIED=1 且 tension_method=ConstrainedOpt",
                  co2.get("applied")
                  and raws["opt_mm" if tag == "MINMAXM" else "opt_en"]["raw"]
                  ["meta"].get("tension_method") == "ConstrainedOpt")
            check(f"{tag}：MPEAK_FINAL 与 [GIRDEREND] max|My| 同源",
                  _same_as_girderend(
                      raws["opt_mm" if tag == "MINMAXM" else "opt_en"]["raw"],
                      co2["mpeak_final"]))
        check("MINMAXM 与 MINENERGY 目标不同、都能产出",
              mm.get("objective") == "MINMAXM" and en.get("objective") == "MINENERGY")
        check("起点如实标 GIVEN（*CABLE 段全给了目标索力）",
              mm.get("start") == "GIVEN" and en.get("start") == "GIVEN")
        check("归一化目标口径写明且不等于 N·m 量级",
              mm.get("objective_kind") == "NORMALIZED"
              and abs(mm["steps"][0]["objective"]) < 1e4)
        check("两组 MPEAK 口径分列",
              mm.get("mpeak_kind") == "LINEAR_BARE"
              and mm.get("mpeak_final_kind") == "GIRDEREND_FINAL")
        check("塔顶平衡开关生效（NBALANCE 不同）",
              en.get("nbalance") == 0 and bal.get("nbalance") > 0,
              f"off={en.get('nbalance')} on={bal.get('nbalance')}")
        check("塔顶平衡开启时如实给出 BAL0/BAL",
              bal.get("bal0", 0) > 0 and bal.get("bal", 0) >= 0)
        check("OPTIMIZE=1 的 [LENGTH] 仍收敛（命中 T* 的下层迭代在跑）",
              raws["opt_en"]["raw"]["cable_length"].get("converged") is True)
        check("优化 T* 与 [CABLE] 成桥索力不同口径但数量一致",
              len(en["cables"]) == len(raws["opt_en"]["raw"]["cables"]))

        print("\n[内核 oracle：未采纳守卫]")
        # 强制一个线性化目标必然恶化的配置：把上界压到起点附近、只留平衡罚。
        bad_text = text.replace(
            "*CABLE_BRIDGE\n",
            "*CABLE_BRIDGE\nOPTIMIZE=1\nOPTOBJ=MINMAXM\nOPTBALANCE=1\nTMAX=1\n",
            1)
        # TMAX 小于下界时内核会放大 hi 保证可行；这里只验证守卫文案存在，
        # 真正的 objective_not_improved 由实桥 OPTBALANCE=1 的 MINMAXM 触发。
        # 这里只验数学守卫（线性化目标不降反升），体系守卫同样置诊断模式，
        # 避免两种拒绝原因混在同一条断言里。
        bad_t = text.replace(
            "*CABLE_BRIDGE\n",
            "*CABLE_BRIDGE\nOPTIMIZE=1\nOPTOBJ=MINMAXM\nOPTBALANCE=1\n"
            "SYSGUARD=0\n", 1)
        bad_raw = _run_dat(tmp, "opt_guard", bad_t)
        bad = bad_raw["cable_opt"]
        check("守卫路径：段里有 APPLIED 字段且状态可解析",
              "applied" in bad and bad.get("status") in
              ("ok", "not_converged", "objective_not_improved"),
              f"status={bad.get('status')} applied={bad.get('applied')}")
        if bad.get("status") == "objective_not_improved":
            check("未采纳时 MPEAK_FINAL_KIND=NOT_APPLICABLE",
                  bad.get("mpeak_final_kind") == "NOT_APPLICABLE"
                  and not bad.get("applied"))
            check("未采纳时原索力口径逐位保留",
                  len(bad_raw["cables"]) == len(base_T)
                  and all(near(a["T"], b, 1e-5)
                          for a, b in zip(bad_raw["cables"], base_T)))
        else:
            check("守卫路径：状态与 APPLIED 自洽",
                  O.validate(bad) == [], str(O.validate(bad))[:90])
        (void := bad_text)  # noqa: F841  保留取样文本，避免误删诊断分支

        print("\n[内核 oracle：体系守卫真实算例（默认 SYSGUARD=1）]")
        import re
        from solver_3d import _adapt_cable

        def _variant(name: str, obj: str, bal: str,
                     mint: str | None = None, extra: str = "") -> dict:
            t = text
            if mint is not None:
                # 「后写覆盖先写」：改索力下界必须替换原 MINT 行，
                # 不能在段头插一行，否则原 MINT=0.02 会把它盖掉。
                t, n = re.subn(r"(?m)^MINT=\S+", f"MINT={mint}", t, count=1)
                assert n == 1, "MINT 行替换失败"
            kv = f"OPTIMIZE=1\nOPTOBJ={obj}\nOPTBALANCE={bal}\n" + extra
            return _run_dat(tmp, name,
                            t.replace("*CABLE_BRIDGE\n",
                                      "*CABLE_BRIDGE\n" + kv, 1))

        _SYS_CHECK_NAMES = ("索承担桥面恒载",
                            "同跨索力无跳变（相邻比 ≤ 1.5）")

        def _cable_checks(raw: dict) -> list:
            r = SolveResult(kind="cable_stayed")
            _adapt_cable(model, raw, r)
            return [c for c in r.checks(model) if c.name in _SYS_CHECK_NAMES]

        # ① 原示例：数学上收敛，但索承担恒载跌到 89.04%、相邻比涨到
        #    31.98 —— 必须被守卫拦下，工程校核表两条体系判据回到通过。
        g_raw = _variant("opt_sys_reject", "MINENERGY", "0")
        g = g_raw["cable_opt"]
        gsc = g.get("syscheck") or {}
        check("默认守卫：原示例 STATUS=system_check_rejected / APPLIED=0",
              g.get("status") == "system_check_rejected"
              and not g.get("applied")
              and g.get("mpeak_final_kind") == "NOT_APPLICABLE",
              f"status={g.get('status')}")
        check("默认守卫：SYSCHECK_PASS=0 且两条原因码齐全",
              gsc.get("mode") == "GUARDED" and gsc.get("pass") is False
              and "system_deck_share_low" in (gsc.get("reject") or [])
              and "system_adjacent_jump" in (gsc.get("reject") or []),
              str(gsc.get("reject")))
        check("默认守卫：T* 预估 89% / 31.98 与内核实测口径对得上",
              near(gsc["pred"]["deck_share"], 89.04, 0.01)
              and gsc["pred"]["adjratio"] > 1.5
              and near(gsc["final"]["deck_share"],
                       float(g_raw["meta"]["deck_share_pct"]), 0.01))
        check("默认守卫：未写回时 tension_method 不是 ConstrainedOpt",
              "ConstrainedOpt" not in str(g_raw["meta"].get("tension_method", "")))
        gcc = _cable_checks(g_raw)
        check("默认守卫：计算书两条体系判据回到通过（用户报告里的红灯）",
              len(gcc) == 2 and all(c.ok for c in gcc),
              str([(c.name, c.ok) for c in gcc]))
        check("默认守卫：段自检全绿", O.validate(g) == [],
              str(O.validate(g))[:90])

        # ② 诊断模式：守卫关闭只记录不拦截，两条体系判据仍如实标出。
        o_raw = _variant("opt_sys_off", "MINENERGY", "0", extra="SYSGUARD=0\n")
        o = o_raw["cable_opt"]
        check("SYSGUARD=0：只记录不拦截，APPLIED=1 且 PASS=NA",
              o.get("applied") and (o.get("syscheck") or {}).get("mode") == "OFF"
              and (o.get("syscheck") or {}).get("pass") is None
              and near(o["syscheck"]["pred"]["deck_share"], 89.04, 0.01))
        occ = _cable_checks(o_raw)
        check("SYSGUARD=0：两条体系判据仍如实标出（不掩盖工程后果）",
              len(occ) == 2 and all(not c.ok for c in occ),
              str([(c.name, c.ok) for c in occ]))

        # ③ 守卫放行：MINMAXM + MINT=0.30 三条全过，证明守卫不误杀。
        p_raw = _variant("opt_sys_pass", "MINMAXM", "0", mint="0.30")
        p = p_raw["cable_opt"]
        check("守卫放行：三条全过时 APPLIED=1 / SYSCHECK_PASS=1",
              (p.get("syscheck") or {}).get("pass") is True
              and p.get("applied")
              and (p.get("syscheck") or {}).get("reject") == []
              and p_raw["meta"].get("tension_method") == "ConstrainedOpt",
              f"status={p.get('status')}")
        check("守卫放行：未收敛也如实标注，不冒充已收敛最优解",
              p.get("status") == "not_converged"
              and "不得" in O.STATUS_TEXT["not_converged"])

        # ④ 第三条判据：索力下界过高会把索竖向分量顶过恒载 → 支座上拔。
        u_raw = _variant("opt_sys_uplift", "MINMAXM", "0", mint="0.40")
        u = u_raw["cable_opt"]
        usc = u.get("syscheck") or {}
        check("守卫能拦支座上拔（system_support_uplift）",
              "system_support_uplift" in (usc.get("reject") or [])
              and usc["pred"]["deck_share"] > 100
              and usc["pred"]["support_kn"] < 0
              and not u.get("applied"), str(usc.get("reject")))

        # ⑤ 数学守卫优先时 REJECT 仍如实列出体系原因码，不让段自检矛盾。
        ni_raw = _variant("opt_sys_notimproved", "MINMAXM", "0", mint="0.35")
        ni = ni_raw["cable_opt"]
        check("两种守卫叠加时 REJECT 与 SYSCHECK_PASS 自洽",
              ni.get("status") == "objective_not_improved"
              and (ni.get("syscheck") or {}).get("pass") is False
              and bool((ni.get("syscheck") or {}).get("reject"))
              and O.validate(ni) == [], str(O.validate(ni))[:90])

        # ⑥ 报告层：体系守卫表跟优化章走，不落在文末明细表。
        res3 = SolveResult(kind="cable_stayed")
        for f in ("cables", "cable_length", "newton", "modal", "buckling",
                  "meta", "girder_ends", "camber", "reaction", "stats"):
            setattr(res3, f, getattr(res, f, None) or {})
        res3.cable_opt = g
        rep3 = R.build_document(res3, model, out_dir=str(out))
        keys3 = [b.get("key") for b in rep3.blocks if b["type"] == "table"]
        check("体系守卫表跟优化章走、排在逐索明细表之前",
              R.OPT_SYSTEM_TABLE_KEY in keys3
              and (R.OPT_CABLES_TABLE_KEY not in keys3
                   or keys3.index(R.OPT_SYSTEM_TABLE_KEY)
                   < keys3.index(R.OPT_CABLES_TABLE_KEY)))
        html3 = R.render_html(rep3)
        check("守卫拒绝章 HTML 无星号残留且写明体系守卫",
              "**" not in html3 and "体系守卫" in html3)

    print("\n[端到端：报告与四出口]")
    rep = R.build_document(res, model, out_dir=str(out))
    h1 = [b["text"] for b in rep.blocks if b["type"] == "h1"]
    check("报告含「带约束成桥目标索力优化」章（OPTIMIZE=0 也写明未请求）",
          "带约束成桥目标索力优化" in h1, " > ".join(h1))
    check("该章排在「内层 Newton」之后",
          "内层 Newton 与共转列式求解" in h1
          and h1.index("内层 Newton 与共转列式求解")
          < h1.index("带约束成桥目标索力优化"))
    # 用带 [OPT] 数据的结果再生成一份，验证两张表的分流
    res2 = SolveResult(kind="cable_stayed")
    for f in ("cables", "cable_length", "newton", "modal", "buckling",
              "meta", "girder_ends", "camber", "reaction", "stats"):
        setattr(res2, f, getattr(res, f, None) or {})
    res2.cable_opt = O.parse_cable_opt_section(_FINAL.splitlines())
    rep2 = R.build_document(res2, model, out_dir=str(out))
    detail = [b.get("key") for b in rep2.blocks if b["type"] == "table"]
    check("逐轮表在正文、逐索表在文末明细表",
          R.OPT_ITERATION_TABLE_KEY in detail
          and R.OPT_CABLES_TABLE_KEY in detail
          and detail.index(R.OPT_ITERATION_TABLE_KEY)
          < detail.index(R.OPT_CABLES_TABLE_KEY))
    check("本章不产出一图两处（无优化图）",
          not any(k.startswith("cable_opt") for k in rep2.figures))
    html = R.render_html(rep2)
    check("HTML 富文本无 ** 残留", "**" not in html)
    check("HTML 写出口径分列与未采纳警示",
          "口径分列" in html or "MPEAK_FINAL" in html)
    paths = R.write_all(rep2, out, stem="cable_opt_report", pdf=False)
    check("三出口都写出（PDF 由报告脚本专项覆盖）",
          all(Path(v).stat().st_size > 1000 for k, v in paths.items()
              if k in ("html", "docx", "csv")),
          str({k: Path(v).stat().st_size for k, v in paths.items()
               if k in ("html", "docx", "csv")}))


def _same_as_girderend(raw: dict, mpeak_final: float) -> bool:
    ges = raw.get("girder_ends") or []
    if not ges or not mpeak_final:
        return False
    actual = max(abs(g["My"]) for g in ges)
    return near(actual, mpeak_final, 1e-6)


def main() -> int:
    offline = "--offline" in sys.argv
    print("=" * 78)
    print("带约束成桥目标索力优化 `[OPT]` 回归" + ("（离线）" if offline else ""))
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
