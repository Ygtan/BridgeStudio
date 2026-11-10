"""``[OPT]`` 段：带约束成桥目标索力优化的过程量解析与数据契约。

内核 ``cable_solver`` 在 ``OPTIMIZE=1`` 时先解一个带箱约束与（可选）
塔顶水平力软约束的二次型，得到目标索力 T\\*，再交回既有的索长/几何
闭合迭代命中。本模块**只解析内核写出的过程量**，不重算柔度、不组装
影响矩阵、不另求一组索力。

三条必须守住的口径，报告层与校核层都要照抄：

1. ``MPEAK0`` / ``MPEAK`` 是 **索去掉后的裸结构线性化影响矩阵** 口径，
   仅供优化目标使用；``MPEAK_FINAL`` 是 **本次成桥态 [GIRDEREND]
   实际 max|My|**。二者不是同一个量，实测可能反向变化，
   **不能互相相减冒充"弯矩改善量"**，也不能拿线性目标冒充成桥态效应。
2. ``I`` 行的 ``objective`` 是**无量纲归一化目标**（优化变量
   ``x = T / Tscale``），只能看相对下降，不能与 N·m / N 混算。
3. ``APPLIED=0`` 表示这组 T\\* **没有被写回 ``*CABLE``**（求解失败、
   线性化目标不降反升，或体系守卫拒绝）。此时 ``T`` 行只是过程量，
   绝不能当成设计索力；``MPEAK_FINAL_KIND=NOT_APPLICABLE``。
4. ``SYSCHECK_PRED`` 是**写回 T\\* 之前**用 T\\* 按计算书工程校核表同一口径
   预估的体系判据（索承担桥面恒载 / 支座不上拔 / 同跨相邻索力比），
   ``SYSCHECK_FINAL`` 是**成桥态实测索力**回填的同一组量。
   ``APPLIED=0`` 时实测列属于原方案，不是 T 行过程量。
   这三条是体系合理性判据，**不是规范条文**，也不是设计合格判定。

迭代轮数、收敛率、目标下降幅度都**不构成设计规范意义上的合格判定**：
规范没有"成桥索力优化收敛轮数"的限值。未收敛 / 未采纳必须如实标注，
不摆空表、不冒充通过。
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

# ---------------------------------------------------------------- 段解析

_RE_HEAD = re.compile(r"^KIND=(\S+) STATUS=(\S+)$")
_RE_ERR = re.compile(r"^ERROR=(\S+)$")
_RE_OBJECTIVE = re.compile(
    r"^OBJECTIVE=(\S+) START=(\S+)(?: OBJECTIVE_KIND=(\S+))?$")
_RE_COUNT = re.compile(
    r"^NCABLES=(\d+) NMOMENTS=(\d+) NBALANCE=(\d+) IRLS=(\d+)$")
_RE_ITER = re.compile(
    r"^NITER=(\d+) CONVERGED=([01]) CONVBY=(\S+)"
    r"(?: APPLIED=([01]))?$")
_RE_BOUNDS = re.compile(r"^TMIN=(\S+) TMAX=(\S+) TMAXSRC=(\S+)$")
_RE_MPEAK_KIND = re.compile(r"^MPEAK_KIND=(\S+)$")
_RE_MPEAK = re.compile(
    r"^MPEAK0=(\S+) MPEAK=(\S+) BAL0=(\S+) BAL=(\S+)$")
_RE_FINAL_KIND = re.compile(r"^MPEAK_FINAL_KIND=(\S+)$")
_RE_FINAL = re.compile(r"^MPEAK_FINAL=(\S+)$")
_RE_I = re.compile(r"^I (\d+) (\d+) (\S+) (\S+) (\S+) (\S+)$")
_RE_T = re.compile(r"^T (\d+) (\S+) (\S+) (\S+) (\S+)$")
# 体系守卫（写回 T* 前的采纳判据）。SYSCHECK_PASS 允许 NA：守卫关闭或
# DECKLOAD 缺失时是「未判定」，绝不能当成 1 冒充合格。
_RE_SYS = re.compile(r"^SYSCHECK=(\S+) AVAILABLE=([01])$")
_RE_SYS_LIMITS = re.compile(
    r"^SYSCHECK_LIMITS DECKSHARE_MIN=(\S+) DECKSHARE_MAX=(\S+)"
    r" ADJRATIO_MAX=(\S+)$")
_RE_SYS_LINE = re.compile(
    r"^SYSCHECK_(PRED|FINAL) DECKSHARE=(\S+) SUPPORT_KN=(\S+)"
    r" ADJRATIO=(\S+) AT_X=(\S+),(\S+)$")
_RE_SYS_FINAL_KIND = re.compile(r"^SYSCHECK_FINAL_KIND=(\S+)$")
_RE_SYS_PASS = re.compile(r"^SYSCHECK_PASS=(NA|[01]) REJECT=(\S+)$")

#: 内核错误码 → 中文说明。不静默：解析不到段时按这个写明原因。
ERROR_TEXT = {
    "no_optimization_requested":
        "本次未请求成桥目标索力优化（OPTIMIZE=0，默认口径）。索力走既有的"
        "给定值 / 刚性支承连续梁法 / 最小弯曲能量法，链路数值逐位不变。",
    "not_applicable_suspension_formfinding":
        "悬索桥成桥态由主缆找形与吊索闭式反算确定索力，不适用斜拉桥式"
        "目标索力优化，已忽略 OPTIMIZE=1。",
    "no_opt_section":
        "求解器未输出 [OPT] 段（旧版内核，或本次未走索承体系链路）。",
    "no_cables": "模型里没有登记索单元，优化无对象。",
    "cable_element_missing": "*CABLE 段引用的索单元在模型里不存在。",
    "anchor_not_on_cable": "登记的梁侧锚点不是索单元的端点。",
    "degenerate_cable": "索单元两端坐标重合，无法给出单位方向。",
    "bare_structure_is_mechanism":
        "去掉待定索后裸结构是机构，柔度矩阵无意义，无法构造影响矩阵。",
    "start_rigid_failed": "刚性支承连续梁法解析起点求解失败。",
    "start_all_zero": "起点索力全为零，无法定上下界与归一化尺度。",
    "no_girder_moments": "模型里没有可用的水平主梁弯矩，min max|M| 无对象。",
}

#: 内核 STATUS → 中文说明。
STATUS_TEXT = {
    "ok": "带约束二次型在给定容差内收敛，目标索力已产出。",
    "not_converged": "达到迭代上限仍未满足收敛判据：过程量可用，但不得"
                     "当作已收敛的最优解使用。",
    "objective_not_improved":
        "线性化弯矩目标不降反升，内核守卫已拒绝把 T* 写回 *CABLE，"
        "保留原索力口径；段里的 T 行只是过程量，不能当设计索力。",
    "not_applicable": "本次求解路径不含带约束成桥目标索力优化。",
    "system_check_rejected":
        "体系守卫拒绝采纳：这组 T* 虽然改善了线性化弯矩目标，但索承担桥面"
        "恒载、支座不上拔或同跨索力平顺度中至少一项未满足，内核未把 T* "
        "写回 *CABLE，结果仍按原索力口径计算。段里的 T 行只是过程量，"
        "不能当设计索力。",
}

#: 体系守卫拒绝原因码 → 中文说明。与计算书工程校核表同一口径。
REJECT_TEXT = {
    "system_deck_share_low":
        "索承担桥面恒载低于下限：主梁被迫独立跨越主跨，成桥线形会失控。",
    "system_support_uplift":
        "索竖向分量超过桥面恒载，支座需向下压（支座上拔），体系不成立。",
    "system_adjacent_jump":
        "同跨相邻索力比超限：索力沿桥呈锯齿，是索力集中到少数索上的征兆。",
}

def empty_syscheck() -> dict[str, Any]:
    """体系守卫空结构：默认「未判定」，不假判合格。"""
    return {
        "present": False,           # 内核是否真的写了 SYSCHECK 行
        "mode": "NOT_AVAILABLE", "available": False, "guarded": False,
        "limits": {"deck_share_min": 90.0, "deck_share_max": 100.0,
                   "adjratio_max": 1.5},
        "pass": None, "reject": [],
        "pred": None, "final": None, "final_kind": "",
    }


def empty_cable_opt(error: str = "") -> dict[str, Any]:
    """统一空结构。``error`` 为空表示"根本没这段"，非空表示内核明确给出原因。"""
    return {
        "kind": "none", "status": "", "objective": "", "start": "",
        "objective_kind": "", "ncables": 0, "nmoments": 0,
        "nbalance": 0, "irls": 0,
        "niter": 0, "converged": False, "converged_by": "",
        "applied": False,
        "tmin": 0.0, "tmax": 0.0, "tmax_source": "",
        "mpeak_kind": "", "mpeak0": 0.0, "mpeak": 0.0,
        "bal0": 0.0, "bal": 0.0,
        "mpeak_final_kind": "", "mpeak_final": 0.0,
        "steps": [], "cables": [], "syscheck": empty_syscheck(),
        "error": error,
    }


def _f(raw: str) -> float:
    v = float(raw)
    return v if math.isfinite(v) else 0.0


def parse_cable_opt_section(lines: Iterable[str]) -> dict[str, Any]:
    """解析 ``[OPT]`` **段内**的行（不含段头）。

    返回统一结构::

        {"kind", "status", "objective", "start", "objective_kind",
         "ncables", "nmoments", "nbalance", "irls", "niter",
         "converged", "converged_by", "applied",
         "tmin", "tmax", "tmax_source",
         "mpeak_kind", "mpeak0", "mpeak", "bal0", "bal",
         "mpeak_final_kind", "mpeak_final",
         "steps": [{"irls", "iter", "objective", "moment_peak",
                    "balance", "step"}],
         "cables": [{"index", "T0", "T", "lo", "hi"}],
         "error"}

    行格式不认识就跳过（向前兼容），但声明数与实读数不符会在
    ``validate`` 里被抓出来。
    """
    out = empty_cable_opt()
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        m = _RE_HEAD.match(line)
        if m:
            out["kind"], out["status"] = m.group(1), m.group(2)
            continue
        m = _RE_ERR.match(line)
        if m:
            out["error"] = m.group(1)
            continue
        m = _RE_OBJECTIVE.match(line)
        if m:
            out["objective"] = m.group(1)
            out["start"] = m.group(2)
            out["objective_kind"] = m.group(3) or ""
            continue
        m = _RE_COUNT.match(line)
        if m:
            out["ncables"] = int(m.group(1))
            out["nmoments"] = int(m.group(2))
            out["nbalance"] = int(m.group(3))
            out["irls"] = int(m.group(4))
            continue
        m = _RE_ITER.match(line)
        if m:
            out["niter"] = int(m.group(1))
            out["converged"] = m.group(2) == "1"
            out["converged_by"] = m.group(3)
            out["applied"] = m.group(4) == "1"
            continue
        m = _RE_BOUNDS.match(line)
        if m:
            out["tmin"] = _f(m.group(1))
            out["tmax"] = _f(m.group(2))
            out["tmax_source"] = m.group(3)
            continue
        m = _RE_MPEAK_KIND.match(line)
        if m:
            out["mpeak_kind"] = m.group(1)
            continue
        m = _RE_MPEAK.match(line)
        if m:
            out["mpeak0"] = _f(m.group(1))
            out["mpeak"] = _f(m.group(2))
            out["bal0"] = _f(m.group(3))
            out["bal"] = _f(m.group(4))
            continue
        m = _RE_FINAL_KIND.match(line)
        if m:
            out["mpeak_final_kind"] = m.group(1)
            continue
        m = _RE_FINAL.match(line)
        if m:
            out["mpeak_final"] = _f(m.group(1))
            continue
        m = _RE_I.match(line)
        if m:
            out["steps"].append({
                "irls": int(m.group(1)), "iter": int(m.group(2)),
                "objective": _f(m.group(3)),
                "moment_peak": _f(m.group(4)),
                "balance": _f(m.group(5)), "step": _f(m.group(6)),
            })
            continue
        m = _RE_T.match(line)
        if m:
            out["cables"].append({
                "index": int(m.group(1)), "T0": _f(m.group(2)),
                "T": _f(m.group(3)), "lo": _f(m.group(4)),
                "hi": _f(m.group(5)),
            })
            continue
        m = _RE_SYS.match(line)
        if m:
            sc = out["syscheck"]
            sc["present"] = True
            sc["mode"] = m.group(1)
            sc["available"] = m.group(2) == "1"
            sc["guarded"] = sc["mode"] == "GUARDED"
            continue
        m = _RE_SYS_LIMITS.match(line)
        if m:
            out["syscheck"]["limits"] = {
                "deck_share_min": _f(m.group(1)),
                "deck_share_max": _f(m.group(2)),
                "adjratio_max": _f(m.group(3)),
            }
            continue
        m = _RE_SYS_FINAL_KIND.match(line)
        if m:
            out["syscheck"]["final_kind"] = m.group(1)
            continue
        m = _RE_SYS_LINE.match(line)
        if m:
            rec = {
                "deck_share": _f(m.group(2)),
                "support_kn": _f(m.group(3)),
                "adjratio": _f(m.group(4)),
                "at_x": [_f(m.group(5)), _f(m.group(6))],
            }
            key = "pred" if m.group(1) == "PRED" else "final"
            out["syscheck"][key] = rec
            continue
        m = _RE_SYS_PASS.match(line)
        if m:
            sc = out["syscheck"]
            sc["pass"] = None if m.group(1) == "NA" else m.group(1) == "1"
            codes = m.group(2)
            sc["reject"] = ([] if codes in ("", "none")
                            else codes.split(","))
            continue
    return out


# ---------------------------------------------------------------- 派生量

def has_data(opt: dict[str, Any]) -> bool:
    """是否含可用的优化过程数据（有 T 行、无 ERROR）。"""
    return bool(opt) and not opt.get("error") and bool(opt.get("cables"))


def error_note(opt: dict[str, Any]) -> str:
    code = (opt or {}).get("error", "")
    return ERROR_TEXT.get(code, f"内核给出未登记的错误码 {code!r}，按不静默原则"
                               "本次不产出优化过程表。")


def status_note(opt: dict[str, Any]) -> str:
    return STATUS_TEXT.get((opt or {}).get("status", ""),
                           "内核状态未登记，本次按未采纳处理。")


def objective_note(opt: dict[str, Any]) -> str:
    name = (opt or {}).get("objective", "")
    label = {"MINMAXM": "最小化主梁弯矩峰值（IRLS 切比雪夫逼近）",
             "MINENERGY": "最小化裸结构弯曲应变能"}.get(name, name or "未指定")
    start = (opt or {}).get("start", "")
    st = {"GIVEN": "*CABLE 段用户给定的目标索力",
          "RIGID": "刚性支承连续梁法解析解"}.get(start, start or "未记录")
    return (f"目标：{label}；起点：{st}；约束：索力上下界 "
            f"[{opt.get('tmin', 0):.4g}, {opt.get('tmax', 0):.4g}] N"
            + (f"（上界来源 {opt.get('tmax_source', '')}，属程序口径不是规范限值）。"
               if opt.get("tmax_source") else "。")
            + f" 塔顶水平力平衡约束 {opt.get('nbalance', 0)} 条，以软约束罚项"
              "并入目标，与弯矩目标存在权衡。")


def scale_note(opt: dict[str, Any]) -> str:
    """两组弯矩峰值与无量纲目标的口径说明。报告必须原样带上这一段。"""
    return (
        "**口径分列**：MPEAK0/MPEAK 是去掉索后的裸结构线性化影响矩阵量，"
        "只用于优化目标；MPEAK_FINAL 是本次成桥态 [GIRDEREND] 实际 "
        "max|My|；逐轮表的目标值是 x=T/Tscale 的**无量纲归一化值**。"
        "三者不是同一个量，实测可能反向变化，**不得互相相减当作改善量**，"
        "也不得拿线性化目标冒充成桥态效应。未优化基线需另跑 OPTIMIZE=0 "
        "取 [GIRDEREND] max|My| 对拍。规范没有优化迭代轮数或收敛率的"
        "合格限值，本段不构成设计合格判定。")


def validate(opt: dict[str, Any], tol: float = 1e-6) -> list[str]:
    """段内自检：数据自洽、口径一致、上下界可行、采纳状态一致。"""
    if not opt:
        return ["[OPT] 段解析结果为空"]
    if opt.get("error"):
        return []
    issues: list[str] = []
    nc = opt.get("ncables", 0)
    cs = opt.get("cables", [])
    if len(cs) != nc:
        issues.append(f"T 行数 {len(cs)} 与 NCABLES={nc} 不符")
    if cs and [c["index"] for c in cs] != list(range(1, nc + 1)):
        issues.append("T 行索引不是 1..NCABLES 连续编号")
    if cs:
        for c in cs:
            if not (c["lo"] <= c["T"] + tol * max(1.0, abs(c["T"]))
                    and c["T"] <= c["hi"] + tol * max(1.0, abs(c["T"]))):
                issues.append(f"第 {c['index']} 索优化结果越出上下界")
                break
        if opt.get("tmin", 0) > opt.get("tmax", 0) + tol:
            issues.append("TMIN > TMAX，箱约束不可行")
    steps = opt.get("steps", [])
    if steps:
        if any(s["irls"] > opt.get("irls", 0) for s in steps):
            issues.append("逐轮表出现超出 IRLS 轮数的记录")
        # MINENERGY 是单轮二次型、不跑 IRLS：irls=0 且逐轮表轮次全 0。
        # 反过来 MINMAXM 至少要有一轮 irls>=1，否则说明开关没生效。
        if opt.get("objective") == "MINENERGY":
            if opt.get("irls", 0) != 0 or any(s["irls"] != 0 for s in steps):
                issues.append("MINENERGY 不应有 IRLS 轮内记录")
        elif opt.get("objective") == "MINMAXM":
            if opt.get("irls", 0) < 1 or not any(s["irls"] >= 1 for s in steps):
                issues.append("MINMAXM 缺少 IRLS 轮内记录")
    if opt.get("applied"):
        if opt.get("mpeak_final_kind") != "GIRDEREND_FINAL":
            issues.append("APPLIED=1 但 MPEAK_FINAL_KIND 不是 GIRDEREND_FINAL")
        if opt.get("mpeak", 0) > opt.get("mpeak0", 0) * (1 + 1e-6):
            issues.append("APPLIED=1 但线性化弯矩目标不降反升")
    elif opt.get("mpeak_final_kind") not in ("", "NOT_APPLICABLE"):
        issues.append("APPLIED=0 却给出了成桥态 MPEAK_FINAL 口径")
    if opt.get("status") == "objective_not_improved" and opt.get("applied"):
        issues.append("STATUS=objective_not_improved 却被标为已采纳")
    # 体系守卫：未判定绝不冒充合格，拒绝必有原因码，采纳与守卫结论一致。
    sc = opt.get("syscheck") or {}
    if sc:
        passed, rej = sc.get("pass"), sc.get("reject") or []
        if passed is False and not rej:
            issues.append("SYSCHECK_PASS=0 却没给出拒绝原因码")
        if passed is True and rej:
            issues.append("SYSCHECK_PASS=1 却带着拒绝原因码")
        # SYSGUARD=0（诊断）时 PASS=NA 但 REJECT 仍如实记录「哪条判据超限」，
        # 这是「只记录不拦截」的正常输出；只有守卫开启却未判定才是矛盾。
        if passed is None and rej and sc.get("mode") == "GUARDED":
            issues.append("SYSCHECK_PASS=NA 却带着拒绝原因码")
        if sc.get("mode") == "GUARDED" and sc.get("available") and passed is None:
            issues.append("守卫开启且判据可用，SYSCHECK_PASS 却未判定")
        if sc.get("mode") == "NOT_AVAILABLE" and (sc.get("pred") or sc.get("final")):
            issues.append("SYSCHECK=NOT_AVAILABLE 却给出了判据数值")
        if sc.get("final") and not sc.get("final_kind"):
            issues.append("SYSCHECK_FINAL 缺少 FINAL_KIND 口径标注")
        if opt.get("status") == "system_check_rejected":
            if opt.get("applied"):
                issues.append("STATUS=system_check_rejected 却被标为已采纳")
            if not rej:
                issues.append("STATUS=system_check_rejected 却没给出拒绝原因码")
        if passed is False and opt.get("applied"):
            issues.append("体系守卫未满足却 APPLIED=1")
        for code in rej:
            if code not in REJECT_TEXT:
                issues.append(f"未登记的体系拒绝原因码 {code!r}")
    return issues


# ---------------------------------------------------------------- 展示表

def syscheck_note(opt: dict[str, Any]) -> str:
    """体系守卫的中文结论。报告层与校核层共用，不假判合格、不冒充规范。"""
    sc = (opt or {}).get("syscheck") or {}
    mode = sc.get("mode", "NOT_AVAILABLE")
    if not sc or not sc.get("present"):
        return ("本次 [OPT] 段没有体系守卫行（未请求优化，或旧版内核未输出）："
                "三条体系判据本次不可用，按未判定处理，不静默补一条估计结论。")
    if mode == "NOT_AVAILABLE":
        return ("体系守卫：本次缺少可用的桥面恒载或支座断面数据，三条体系"
                "判据未判定，不据此声称合格，也不据此拒绝采纳。")
    if mode == "OFF":
        txt = ("体系守卫由 SYSGUARD=0 关闭（仅供诊断过程量）：三条体系判据"
               "只记录不拦截，本次结果不得作为设计计算书使用。")
        if sc.get("reject"):
            txt += " 本次判据超限（仅记录）：" + "；".join(
                REJECT_TEXT.get(c, c) for c in sc["reject"]) + "。"
        return txt
    lim = sc.get("limits") or {}
    pred = sc.get("pred") or {}
    bits = [f"体系守卫：T* 预估索承担桥面恒载 "
            f"{pred.get('deck_share', 0):.2f}%（要求 "
            f"> {lim.get('deck_share_min', 90):g}% 且 "
            f"≤ {lim.get('deck_share_max', 100):g}% 不上拔）、"
            f"同跨相邻索力比 {pred.get('adjratio', 0):.2f}"
            f"（要求 ≤ {lim.get('adjratio_max', 1.5):g}）。"]
    if sc.get("pass"):
        bits.append("三条均满足，允许把 T* 写回 *CABLE。")
    else:
        reasons = "；".join(REJECT_TEXT.get(c, c)
                            for c in (sc.get("reject") or []))
        bits.append("守卫拒绝采纳：" + reasons + "。内核保留原索力口径，"
                    "段里的 T 行只是过程量，不能当设计索力。")
    return " ".join(bits)


def _sys_verdict(sc: dict[str, Any], code: str) -> str:
    if sc.get("pass") is None:
        return "未判定"
    return "拒绝" if code in (sc.get("reject") or []) else "满足"


def system_check_table(opt: dict[str, Any]) -> dict[str, Any]:
    """三条体系判据：写回前预估 vs 成桥态实测，并排给结论。"""
    sc = (opt or {}).get("syscheck") or empty_syscheck()
    lim = sc.get("limits") or {}
    pred, fin = sc.get("pred"), sc.get("final")

    def cell(rec, key: str, unit: str = "") -> str:
        if not rec:
            return "—"
        return f"{rec.get(key, 0.0):.2f}{unit}"

    return {
        "title": "成桥体系守卫判据（写回 T* 前）",
        "headers": ["判据", "限值", "T* 预估（守卫依据）",
                    "成桥态实测", "守卫结论"],
        "rows": [
            ["索承担桥面恒载", f"> {lim.get('deck_share_min', 90):g} %",
             cell(pred, "deck_share", " %"), cell(fin, "deck_share", " %"),
             _sys_verdict(sc, "system_deck_share_low")],
            ["支座分担（不上拔）",
             f"≥ 0 kN（比例 ≤ {lim.get('deck_share_max', 100):g} %）",
             cell(pred, "support_kn", " kN"), cell(fin, "support_kn", " kN"),
             _sys_verdict(sc, "system_support_uplift")],
            ["同跨相邻索力比", f"≤ {lim.get('adjratio_max', 1.5):g}",
             cell(pred, "adjratio"), cell(fin, "adjratio"),
             _sys_verdict(sc, "system_adjacent_jump")],
        ],
        "note": "预估列用 T* 在写回前判定，是内核采纳守卫的依据；实测列用本次"
                "成桥态实际索力按同一口径回填。APPLIED=0 时实测列是原方案，"
                "不是 T 行过程量。三条均为体系合理性判据，不是规范条文，"
                "也不构成设计合格判定；SYSGUARD=0（诊断）时只记录不拦截。",
    }


def iteration_table(opt: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": "成桥目标索力优化逐轮收敛",
        "headers": ["IRLS 轮", "迭代", "归一化目标", "max|M|(N·m)",
                    "塔顶不平衡(N)", "相对步长"],
        "rows": [[s["irls"], s["iter"], s["objective"], s["moment_peak"],
                  s["balance"], s["step"]] for s in opt.get("steps", [])],
        "note": "归一化目标只反映无量纲优化变量 x=T/Tscale 的下降，"
                "不与 max|M| / 塔顶不平衡混算；跨 IRLS 轮目标权重不同，"
                "不能连成一条单调收敛曲线。",
    }


def cable_table(opt: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": "逐索目标索力优化结果",
        "headers": ["索序号", "起点 T0(N)", "优化 T*(N)", "下限(N)", "上限(N)"],
        "rows": [[c["index"], c["T0"], c["T"], c["lo"], c["hi"]]
                 for c in opt.get("cables", [])],
        "note": ("APPLIED=1 时 T* 已写回 *CABLE 并交由索长/几何闭合迭代命中；"
                 "APPLIED=0 时本表只是过程量，不能当设计索力。"
                 if not opt.get("applied") else
                 "T* 已写回 *CABLE，成桥态索力由索长/几何闭合迭代命中；"
                 "本表是优化目标值，不是成桥态实测索力。"),
    }


def summary_note(opt: dict[str, Any]) -> str:
    if opt.get("error"):
        return error_note(opt)
    bits = [status_note(opt), objective_note(opt)]
    if has_data(opt):
        bits.append(
            f"线性化弯矩峰值 {opt.get('mpeak0', 0):.4g} → "
            f"{opt.get('mpeak', 0):.4g} N·m；塔顶不平衡 {opt.get('bal0', 0):.4g}"
            f" → {opt.get('bal', 0):.4g} N；共 {opt.get('niter', 0)} 次迭代，"
            f"采纳状态 APPLIED={1 if opt.get('applied') else 0}。")
        if opt.get("applied") and opt.get("mpeak_final", 0) > 0:
            bits.append(f"本次成桥态 [GIRDEREND] 实际 max|My| = "
                        f"{opt['mpeak_final']:.4g} N·m。")
    bits.append(syscheck_note(opt))
    bits.append(scale_note(opt))
    return " ".join(bits)
