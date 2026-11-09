"""newton_3d.py —— 内层共转列式 Newton 过程量的数据契约与派生量。

定位
----
**这里不重算任何力学。** 荷载步、共转列式一致切线、残差收敛判据全在
FEM-Eigen 的 ``fem_3d_nl.hpp::solveModel3DNonlinear`` 里。内核用
``newton_output_3d.hpp`` 把同一份 ``NewtonTrace3D`` 写成 ``[NEWTON]`` 段；
本模块是**唯一解析器**，只做解析、派生展示量与自检。

两层迭代不能混为一谈
--------------------
- ``[LENGTH]``（``cable_length_3d.py``）是**外层**：改模型参数（无应力索长
  L0 或制造线形 ΔX），每轮都要重跑一次完整非线性平衡。
- ``[NEWTON]``（本模块）是**内层**：给定模型上的共转列式 Newton + 荷载步，
  未知量是节点位移 u，控制量是残差 ``|R|`` 与位移增量 ``|du|``。
  一个外层轮次对应一次内层全过程；成桥态只暴露**收敛那一轮**的内层过程，
  非线性施工阶段则每阶段各暴露一段。

段格式（详见 FEM-Eigen/newton_output_3d.hpp）::

    KIND=newton_cr STATUS=ok
    STAGE=3                         仅施工阶段算例
    NSTEP=4 NITER=7 MAXITER=25 TOLF=1e-06 TOLD=1e-09 CONVERGED=1
           REFACT=3 RESFIRST=1.2e6 RESLAST=3.4e-5
    S 1 1 0.25 1.2e6 0.03 4.8e6 1 0
    ERROR=no_newton_solution

口径（必须写进报告）
--------------------
- 收敛判据是 **或**：``|R| ≤ tolForce·max(|Ftarget|, 1e-12)`` 或
  ``|du| ≤ tolDisp·max(1, |u|)``。每个荷载步都要满足，否则整体不收敛。
- ``REFACT`` 只统计符号分解重建（首次 / 稀疏模式变化），数值
  ``refactorize`` 不算；是性能诊断量，不是力学量。
- 过程量只说明"数值怎么收敛"，不构成规范意义上的合格判定；规范也没有
  Newton 迭代轮数的限值。未收敛必须如实标 ``not_converged``。

不做
----
不组装 K、不重跑 Newton；不给迭代轮数编造规范限值；不把"收敛了"写成
"设计合格"；不把多个荷载步的残差当成一条跨步单调曲线来断言收敛。
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

# ---------------------------------------------------------------- 段解析

_RE_HEAD = re.compile(r"^KIND=(\S+) STATUS=(\S+)$")
_RE_STAGE = re.compile(r"^STAGE=(\d+)$")
_RE_META = re.compile(
    r"^NSTEP=(\d+) NITER=(\d+) MAXITER=(\d+) TOLF=(\S+) TOLD=(\S+) "
    r"CONVERGED=([01]) REFACT=(\d+) RESFIRST=(\S+) RESLAST=(\S+)$")
_RE_S = re.compile(
    r"^S (\d+) (\d+) (\S+) (\S+) (\S+) (\S+) ([01]) ([01])$")
_RE_ERR = re.compile(r"^ERROR=(\S+)$")

#: 内核错误码 → 中文说明。不静默：解析不到段时按这个写明原因。
ERROR_TEXT = {
    "no_newton_solution":
        "内核未记录到内层 Newton 过程（可能是旧版内核，或本次求解路径"
        "没有走几何非线性平衡）。不是求解失败，也没有过程量可展示。",
    "no_newton_section":
        "求解器未输出 [NEWTON] 段（旧版内核，或本次走的是线性内核链路）。",
}

#: 内核 STATUS → 中文说明。
STATUS_TEXT = {
    "ok": "内层 Newton 在每个荷载步都满足收敛容差。",
    "not_converged": "达到单步 Newton 迭代上限仍未满足收敛容差：本次平衡"
                     "不能当作已收敛的构形使用。",
    "no_iteration": "内核没有记录到任何 Newton 迭代。",
    "not_applicable": "本次求解路径不含内层共转列式 Newton 过程。",
}


def empty_newton(error: str = "") -> dict[str, Any]:
    """单个 trace 的统一空结构。``error`` 为空表示"根本没这段"。"""
    return {
        "kind": "none", "status": "", "stage": None,
        "nstep": 0, "niter": 0, "maxiter": 0,
        "tol_force": 0.0, "tol_disp": 0.0,
        "converged": False, "refact": 0,
        "res_first": 0.0, "res_last": 0.0,
        "steps": [], "error": error,
    }


def empty_bundle(error: str = "") -> dict[str, Any]:
    """结果层统一容器：成桥态一段、施工阶段每段一条。"""
    return {"traces": [], "error": error}


def bundle(traces: Iterable[dict[str, Any]], error: str = "") -> dict[str, Any]:
    """把若干 trace 打包成 ``SolveResult.newton`` 的容器。"""
    out = empty_bundle(error)
    out["traces"] = [t for t in traces if t]
    return out


def _f(raw: str) -> float:
    v = float(raw)
    return v if math.isfinite(v) else 0.0


def parse_newton_section(lines: Iterable[str]) -> dict[str, Any]:
    """解析 ``[NEWTON]`` **段内**的行（不含段头）。

    返回单个 trace::

        {"kind", "status", "stage", "nstep", "niter", "maxiter",
         "tol_force", "tol_disp", "converged", "refact",
         "res_first", "res_last",
         "steps": [{"load_step", "iter", "lambda", "residual", "du",
                    "ref", "refactor", "converged"}],
         "error"}

    行格式不认识就跳过（向前兼容），但声明数与实读数不符会在
    ``validate`` 里被抓出来。
    """
    out = empty_newton()
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        m = _RE_HEAD.match(line)
        if m:
            out["kind"] = m.group(1)
            out["status"] = m.group(2)
            continue
        m = _RE_ERR.match(line)
        if m:
            out["error"] = m.group(1)
            continue
        m = _RE_STAGE.match(line)
        if m:
            out["stage"] = int(m.group(1))
            continue
        m = _RE_META.match(line)
        if m:
            out["nstep"] = int(m.group(1))
            out["niter"] = int(m.group(2))
            out["maxiter"] = int(m.group(3))
            out["tol_force"] = _f(m.group(4))
            out["tol_disp"] = _f(m.group(5))
            out["converged"] = m.group(6) == "1"
            out["refact"] = int(m.group(7))
            out["res_first"] = _f(m.group(8))
            out["res_last"] = _f(m.group(9))
            continue
        m = _RE_S.match(line)
        if m:
            out["steps"].append({
                "load_step": int(m.group(1)),
                "iter": int(m.group(2)),
                "lambda": _f(m.group(3)),
                "residual": _f(m.group(4)),
                "du": _f(m.group(5)),
                "ref": _f(m.group(6)),
                "refactor": m.group(7) == "1",
                "converged": m.group(8) == "1",
            })
            continue
    return out


# ---------------------------------------------------------------- 派生量

def load_step_groups(tr: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """按荷载步分组；不要求编号连续（编号异常由 validate 抓）。"""
    groups: list[list[dict[str, Any]]] = []
    for s in tr.get("steps") or []:
        n = s["load_step"]
        while len(groups) < n:
            groups.append([])
        groups[n - 1].append(s)
    return groups


def residual_ratio(tr: dict[str, Any]) -> float | None:
    """末个 / 首个残差（纯展示量；跨荷载步不具单调性，别当收敛率）。"""
    a, b = tr.get("res_first", 0.0), tr.get("res_last", 0.0)
    return b / a if a > 0.0 and b > 0.0 else None


def status_note(tr: dict[str, Any]) -> str:
    """把 STATUS / ERROR 翻成一句可写进校核的话。"""
    if tr.get("error"):
        return ERROR_TEXT.get(tr["error"], f"内层 Newton 段报错：{tr['error']}")
    return STATUS_TEXT.get(tr.get("status", ""),
                           f"内核给出未知状态 STATUS={tr.get('status')!r}，"
                           "不能据此判定本次平衡是否收敛。")


def mode_note(tr: dict[str, Any]) -> str:
    """报告里必须写清的口径说明。"""
    return ("内层迭代是给定模型上的共转列式几何非线性平衡：外荷载按 "
            f"{tr.get('nstep', 0)} 个荷载步逐级施加（λ = k/NSTEP），每步做 "
            f"至多 {tr.get('maxiter', 0)} 次 Newton 迭代，用一致切线组装并"
            "分解、求位移增量。收敛判据是 |R| ≤ tolForce·max(|Ftarget|,1e-12) "
            "**或** |du| ≤ tolDisp·max(1,|u|)，每个荷载步都要满足。"
            "它与外层索长/几何闭合迭代不是同一层：外层改 L0 或制造线形，"
            "本层只解节点位移。规范没有 Newton 迭代轮数的合格限值，"
            "本报告不据此给出设计合格判定。")


def convergence_note(tr: dict[str, Any]) -> str:
    """残差与分解重建的诊断说明。"""
    rr = residual_ratio(tr)
    tail = (f"首个 |R| {tr.get('res_first', 0.0):.4g} → 末个 "
            f"|R| {tr.get('res_last', 0.0):.4g}")
    if rr is not None:
        tail += f"（末/首 = {rr:.3g}；跨荷载步残差会随 λ 跳升，不是单调收敛曲线）"
    return (f"共 {tr.get('niter', 0)} 次 Newton 迭代、"
            f"{len(load_step_groups(tr))} 个荷载步；符号分解重建 "
            f"{tr.get('refact', 0)} 次（首次 / 稀疏模式变化才重建，"
            "数值 refactorize 不计）。" + tail + "。")


def validate(tr: dict[str, Any]) -> list[str]:
    """单个 trace 自检，返回问题列表（空 = 全通过）。不静默。"""
    issues: list[str] = []
    if tr.get("error"):
        issues.append(status_note(tr))
        return issues
    if not tr.get("status"):
        issues.append(ERROR_TEXT["no_newton_section"])
        return issues
    steps = tr.get("steps") or []
    if tr["status"] == "not_applicable":
        if steps:
            issues.append("STATUS=not_applicable 却读到了 Newton 迭代数据，状态矛盾")
        return issues
    if not steps:
        issues.append("声明有内层迭代但没读到任何 S 行（逐迭代过程缺失）")
        return issues
    if tr["niter"] != len(steps):
        issues.append(f"段声明 {tr['niter']} 次迭代，实际读到 {len(steps)} 次")
    if tr["converged"] != (tr["status"] == "ok"):
        issues.append("CONVERGED 标志与 STATUS 不一致")
    if not tr["converged"]:
        issues.append("内层 Newton 未收敛：" + status_note(tr))
    # 每个荷载步的末次迭代必须标收敛；编号连续；λ = k/NSTEP
    groups = load_step_groups(tr)
    if tr["nstep"] and len(groups) != tr["nstep"]:
        issues.append(f"段声明 {tr['nstep']} 个荷载步，实际读到 {len(groups)} 个")
    for k, g in enumerate(groups, start=1):
        if not g:
            issues.append(f"荷载步 {k} 没有任何迭代")
            continue
        for j, s in enumerate(g, start=1):
            if s["iter"] != j:
                issues.append(f"荷载步 {k} 的迭代编号不连续（第 {j} 行是 {s['iter']}）")
                break
            if tr["nstep"] and abs(s["lambda"] - k / tr["nstep"]) > 1e-9:
                issues.append(f"荷载步 {k} 的 λ={s['lambda']} 与 k/NSTEP 不符")
                break
        if tr["converged"] and not g[-1]["converged"]:
            issues.append(f"整体标为收敛，但荷载步 {k} 的末次迭代没有收敛标记")
    if abs(tr["res_first"] - steps[0]["residual"]) > 1e-6 * max(1.0, abs(tr["res_first"])):
        issues.append("RESFIRST 与首个 S 行的 |R| 不同源")
    if abs(tr["res_last"] - steps[-1]["residual"]) > 1e-6 * max(1.0, abs(tr["res_last"])):
        issues.append("RESLAST 与末个 S 行的 |R| 不同源")
    if tr["refact"] != sum(1 for s in steps if s["refactor"]):
        issues.append("REFACT 与 S 行的重建标记数不一致")
    return issues


def step_table(tr: dict[str, Any]) -> dict[str, Any]:
    """逐荷载步汇总表（正文用，行数 = 荷载步数）。"""
    rows: list[list[Any]] = []
    for k, g in enumerate(load_step_groups(tr), start=1):
        if not g:
            continue
        rows.append([
            k, f"{g[0]['lambda']:.4g}", len(g),
            f"{g[0]['residual']:.4g}", f"{g[-1]['residual']:.4g}",
            f"{g[-1]['du']:.4g}",
            sum(1 for s in g if s["refactor"]),
            "是" if g[-1]["converged"] else "否",
        ])
    return {
        "title": "共转列式 Newton 逐荷载步收敛",
        "note": mode_note(tr) + " " + convergence_note(tr),
        "headers": ["荷载步", "λ", "本步迭代数", "首个 |R| (N)",
                    "末个 |R| (N)", "末个 |du| (m)", "重建分解", "本步收敛"],
        "rows": rows,
    }


def iteration_table(tr: dict[str, Any]) -> dict[str, Any]:
    """逐 Newton 迭代明细表（归文末明细表章，行数 = 总迭代数）。"""
    rows: list[list[Any]] = []
    for s in tr.get("steps") or []:
        rows.append([
            s["load_step"], s["iter"], f"{s['lambda']:.4g}",
            f"{s['residual']:.6g}", f"{s['du']:.6g}", f"{s['ref']:.6g}",
            "是" if s["refactor"] else "—",
            "是" if s["converged"] else "—",
        ])
    return {
        "title": "内层 Newton 逐迭代残差明细",
        "note": "|R|、|du|、|Ftarget| 均只统计自由自由度，取自内核 "
                "[NEWTON] 段同一份 S 行；Python 不重算、不另判收敛。",
        "headers": ["荷载步", "步内迭代", "λ", "|R| (N)", "|du| (m)",
                    "|Ftarget| (N)", "重建分解", "本步收敛"],
        "rows": rows,
    }


def stages_table(bundle_: dict[str, Any]) -> dict[str, Any]:
    """非线性施工阶段：每阶段一行；成桥态单段时也返回一行。"""
    rows: list[list[Any]] = []
    for t in bundle_.get("traces") or []:
        if t.get("error") or not t.get("steps"):
            continue
        rows.append([
            t["stage"] if t.get("stage") is not None else "成桥态",
            t.get("nstep", 0), t.get("niter", 0),
            f"{t.get('res_first', 0.0):.4g}", f"{t.get('res_last', 0.0):.4g}",
            t.get("refact", 0),
            "收敛" if t.get("converged") else "未收敛",
        ])
    return {
        "title": "各阶段内层 Newton 收敛汇总",
        "note": "非线性施工阶段的每个阶段都是当前已激活结构上的**全量平衡**，"
                "位移不能跨阶段累加；本表各阶段相互独立。过程量只说明数值"
                "如何收敛，不构成规范合格判定。",
        "headers": ["阶段", "荷载步数", "Newton 迭代数", "首个 |R| (N)",
                    "末个 |R| (N)", "重建分解", "本阶段状态"],
        "rows": rows,
    }
