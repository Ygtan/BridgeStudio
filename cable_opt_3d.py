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
   或线性化目标不降反升被守卫拦下）。此时 ``T`` 行只是过程量，
   绝不能当成设计索力；``MPEAK_FINAL_KIND=NOT_APPLICABLE``。

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
        "steps": [], "cables": [], "error": error,
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
    return issues


# ---------------------------------------------------------------- 展示表

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
    bits.append(scale_note(opt))
    return " ".join(bits)
