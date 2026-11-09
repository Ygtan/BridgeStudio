"""cable_length_3d.py —— 索长迭代 / 成桥态几何闭合过程量的数据契约与派生量。

定位
----
**这里不重算任何力学。** 共转列式非线性、索长影响矩阵 J = M·D 的 Newton、
制造线形几何闭合的 Woodbury 低秩 Newton + 回溯线搜 + 不动点兜底，全在
FEM-Eigen 的 ``fem_3d_nl.hpp`` 里。内核用 ``cable_length_output_3d.hpp``
把同一份 ``IterationTrace3D`` 写成 ``[LENGTH]`` 段；本模块是**唯一解析器**，
只做解析、派生展示量与自检。

段格式（详见 FEM-Eigen/cable_length_output_3d.hpp）::

    KIND=cable_length STATUS=ok ERRKIND=tension_rel
    NCABLES=48 NITER=6 MAXITER=15 TOL=1e-07 CONVERGED=1
    SENS JAVAIL=1 JREBUILD=1 JDIAGMIN=-3.05e7 JDIAGMAX=-9.6e6
    I 1 0.6239 0.0514 0.1565 0.0017 1 0 REBUILD
    E 9001 682510.3 682510.3 36.395 36.416 36.401
    NJ=48
    J 0 0 -30544922.38
    ERROR=no_iteration_static_mode

两种迭代，**控制误差单位不同，不能画同一根轴**
------------------------------------------------
- ``KIND=cable_length``（MODE=TENSION）：未知量是各索无应力长度 L0，
  控制误差是 **max 相对索力偏差**（无量纲，ERRKIND=tension_rel），
  唯一收敛判据是它。成桥弦长偏差 ``geomErr`` 只是结果量、不作判据——
  实测它会在 0.15 m 左右稳定下来，说明"索力对了但线形没闭合"。
- ``KIND=erection_geometry``（MODE=COMPLETION）：未知量是节点坐标偏移 ΔX，
  控制误差是 **max|ΔX + u|**（m，ERRKIND=closure_m），决定制造线形预抬高；
  索力偏差在这里是结果而不是判据。
- ``MODE=STATIC`` 与悬索桥找形是闭式反算 L0 后一次解完，**本来就没有外层
  迭代**，内核写 ERROR 段，不摆一张空表冒充"迭代 0 轮收敛"。

不做
----
不组装 K / J，不重跑 Newton；不给迭代轮数编造规范限值；不把
"收敛了"写成"设计合格"——过程量只说明数值如何收敛。
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

# ---------------------------------------------------------------- 段解析

_RE_HEAD = re.compile(r"^KIND=(\S+) STATUS=(\S+) ERRKIND=(\S+)$")
_RE_META = re.compile(
    r"^NCABLES=(\d+) NITER=(\d+) MAXITER=(\d+) TOL=(\S+) CONVERGED=([01])$")
_RE_SENS = re.compile(
    r"^SENS JAVAIL=([01]) JREBUILD=(\d+) JDIAGMIN=(\S+) JDIAGMAX=(\S+)$")
_RE_I = re.compile(
    r"^I (\d+) (\S+) (\S+) (\S+) (\S+) (\S+) (\d+) (\S+)$")
_RE_E = re.compile(r"^E (\d+) (\S+) (\S+) (\S+) (\S+) (\S+)$")
_RE_NJ = re.compile(r"^NJ=(\d+)$")
_RE_J = re.compile(r"^J (\d+) (\d+) (\S+)$")
_RE_ERR = re.compile(r"^ERROR=(\S+)$")

#: 内核错误码 → 中文说明。不静默：解析不到段时按这个写明原因。
ERROR_TEXT = {
    "no_iteration_static_mode":
        "MODE=STATIC 按给定 L0 一次求解，不进入外层索长/几何闭合迭代；"
        "本次没有迭代过程量，不是求解失败。",
    "no_iteration_suspension_formfinding":
        "悬索桥成桥态由主缆索多边形找形 → 平衡态弦长闭式反算 L0 → 一次"
        "几何非线性求解完成，不经外层索长迭代；本次没有迭代过程量。",
    "no_length_section":
        "求解器未输出索长迭代段（可能是旧版内核，或本次未走索承体系链路）。",
}

#: 内核 STATUS → 中文说明。
STATUS_TEXT = {
    "ok": "外层迭代在给定容差内收敛。",
    "not_converged": "达到迭代上限仍未满足收敛容差：过程量可用，但该成桥态"
                     "不能当作已收敛的平衡态使用。",
    "no_iteration": "内核没有记录到任何迭代轮次。",
    "not_applicable": "本次求解路径不含外层索长/几何闭合迭代。",
}


def empty_cable_length(error: str = "") -> dict[str, Any]:
    """统一空结构。``error`` 为空表示"根本没这段"，非空表示内核明确报错。"""
    return {
        "kind": "none", "status": "", "err_kind": "-",
        "ncables": 0, "niter": 0, "maxiter": 0, "tol": 0.0,
        "converged": False,
        "sens": {"j_available": False, "j_rebuild": 0,
                 "j_diag_min": 0.0, "j_diag_max": 0.0},
        "steps": [], "cables": [], "nj": 0, "J": [],
        "error": error,
    }


def _f(raw: str) -> float:
    v = float(raw)
    return v if math.isfinite(v) else 0.0


def parse_cable_length_section(lines: Iterable[str]) -> dict[str, Any]:
    """解析 ``[LENGTH]`` **段内**的行（不含段头）。

    返回统一结构::

        {"kind", "status", "err_kind", "ncables", "niter", "maxiter",
         "tol", "converged", "sens": {...},
         "steps": [{"iter", "err", "geom_err", "step_max", "step_rel_max",
                    "alpha", "line_search", "flags", ...}],
         "cables": [{"elem", "T_target_kN", "T_final_kN", "L0_m",
                     "chord_m", "Ltar_m", "dL_m", "T_err_rel"}],
         "nj", "J": [[...]], "error"}

    行格式不认识就跳过（向前兼容：内核以后加字段不会把旧解析弄崩），
    但声明数与实读数不符会在 ``validate`` 里被抓出来。
    """
    out = empty_cable_length()
    jcells: dict[tuple[int, int], float] = {}
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        m = _RE_HEAD.match(line)
        if m:
            out["kind"] = m.group(1)
            out["status"] = m.group(2)
            out["err_kind"] = m.group(3)
            continue
        m = _RE_ERR.match(line)
        if m:
            out["error"] = m.group(1)
            continue
        m = _RE_META.match(line)
        if m:
            out["ncables"] = int(m.group(1))
            out["niter"] = int(m.group(2))
            out["maxiter"] = int(m.group(3))
            out["tol"] = _f(m.group(4))
            out["converged"] = m.group(5) == "1"
            continue
        m = _RE_SENS.match(line)
        if m:
            out["sens"] = {
                "j_available": m.group(1) == "1",
                "j_rebuild": int(m.group(2)),
                "j_diag_min": _f(m.group(3)),
                "j_diag_max": _f(m.group(4)),
            }
            continue
        m = _RE_I.match(line)
        if m:
            flags = [] if m.group(8) == "-" else m.group(8).split(",")
            out["steps"].append({
                "iter": int(m.group(1)),
                "err": _f(m.group(2)),
                "geom_err": _f(m.group(3)),
                "step_max": _f(m.group(4)),
                "step_rel_max": _f(m.group(5)),
                "alpha": _f(m.group(6)),
                "line_search": int(m.group(7)),
                "flags": flags,
                "rebuilt": "REBUILD" in flags,
                "capped": "CAPPED" in flags,
                "fallback": "FALLBACK" in flags,
                "converged": "CONVERGED" in flags,
            })
            continue
        m = _RE_E.match(line)
        if m:
            tt = _f(m.group(2)) / 1000.0
            tf = _f(m.group(3)) / 1000.0
            chord = _f(m.group(5))
            ltar = _f(m.group(6))
            out["cables"].append({
                "elem": int(m.group(1)),
                "T_target_kN": tt, "T_final_kN": tf,
                "L0_m": _f(m.group(4)), "chord_m": chord, "Ltar_m": ltar,
                "dL_m": chord - ltar,
                "T_err_rel": abs(tf - tt) / abs(tt) if abs(tt) > 1e-12 else 0.0,
            })
            continue
        m = _RE_NJ.match(line)
        if m:
            out["nj"] = int(m.group(1))
            continue
        m = _RE_J.match(line)
        if m:
            jcells[(int(m.group(1)), int(m.group(2)))] = _f(m.group(3))
            continue
    if out["nj"] > 0:
        n = out["nj"]
        out["J"] = [[0.0] * n for _ in range(n)]
        for (i, j), v in jcells.items():
            if 0 <= i < n and 0 <= j < n:
                out["J"][i][j] = v
    return out


# ---------------------------------------------------------------- 派生量

def final_error(cl: dict[str, Any]) -> float | None:
    """最后一轮控制误差；没有迭代返回 None。"""
    steps = cl.get("steps") or []
    return float(steps[-1]["err"]) if steps else None


def mean_convergence_rate(cl: dict[str, Any]) -> float | None:
    """相邻轮控制误差比的几何平均（<1 表示在收敛）。纯展示量。"""
    errs = [float(s["err"]) for s in (cl.get("steps") or []) if s["err"] > 0.0]
    if len(errs) < 2:
        return None
    ratios = [errs[i + 1] / errs[i] for i in range(len(errs) - 1)]
    return math.exp(sum(math.log(r) for r in ratios) / len(ratios))


def err_unit(cl: dict[str, Any]) -> str:
    """控制误差的单位/含义，写表头用。"""
    return "相对偏差" if cl.get("err_kind") == "tension_rel" else "闭合误差 (m)"


def status_note(cl: dict[str, Any]) -> str:
    """把 STATUS / ERROR 翻成一句可写进校核的话。"""
    if cl.get("error"):
        return ERROR_TEXT.get(cl["error"], f"索长迭代段报错：{cl['error']}")
    return STATUS_TEXT.get(cl.get("status", ""),
                           f"内核给出未知状态 STATUS={cl.get('status')!r}，"
                           "不能据此判定成桥态收敛性。")


def mode_note(cl: dict[str, Any]) -> str:
    """两种迭代的口径说明（报告必须写清，别把两种误差混为一谈）。"""
    kind = cl.get("kind")
    if kind == "cable_length":
        return ("本次为索长迭代（MODE=TENSION）：内核以各索无应力长度 L0 为"
                "未知量，用影响矩阵 J = ∂T_i/∂L0_j 的 Newton 步命中目标成桥索力；"
                "唯一收敛判据是最大相对索力偏差。成桥弦长相对设计弦长的偏差"
                "只是结果量、不参与判据——索力命中不等于线形闭合，制造线形需"
                "另走 MODE=COMPLETION 的几何闭合迭代。")
    if kind == "erection_geometry":
        return ("本次为制造线形几何闭合（MODE=COMPLETION）：目标成桥索力"
                "先由给定值或最小弯曲能量法确定，设计弦长 + 目标索力闭式反算"
                "下料 L0 后，内核以节点坐标偏移 ΔX 为未知量做低秩 Newton + "
                "回溯线搜索，使 ΔX + u = 0；收敛判据是最大几何闭合误差 (m)。"
                "逐轮索力偏差在这里是结果而不是判据。")
    return "本次求解路径不含外层索长/几何闭合迭代。"


def sens_note(cl: dict[str, Any]) -> str:
    """影响矩阵诊断的一句话说明。"""
    s = cl.get("sens") or {}
    if not s.get("j_available"):
        if cl.get("kind") == "erection_geometry":
            return ("几何闭合迭代用的是索敏感度算子 M（低秩 Newton / Woodbury），"
                    f"本轮重建 {s.get('j_rebuild', 0)} 次，M 对角范围 "
                    f"{s.get('j_diag_min', 0.0):.4g} ~ {s.get('j_diag_max', 0.0):.4g}；"
                    "该路径不产生 ∂T/∂L0 影响矩阵 J。")
        return "本次没有影响矩阵 J（无索长迭代）。"
    n = cl.get("nj", 0)
    return (f"影响矩阵 J 为 {n}×{n}（J = M·D，一次矩阵分解 + {n} 次回代给出，"
            f"冻结复用、残差停滞才重建；本次重建 {s.get('j_rebuild', 0)} 次）。"
            f"对角范围 {s.get('j_diag_min', 0.0):.4g} ~ "
            f"{s.get('j_diag_max', 0.0):.4g}：对 ∂T_i/∂L0_i < 0（无应力索长"
            "放长则索力下降），非对角项反映索与索通过结构位移的耦合。")


def validate(cl: dict[str, Any], tol: float = 1e-6) -> list[str]:
    """自检，返回问题列表（空 = 全通过）。不静默：每条都能写进校核。"""
    issues: list[str] = []
    if cl.get("error"):
        issues.append(status_note(cl))
        return issues
    if not cl.get("status"):
        issues.append(ERROR_TEXT["no_length_section"])
        return issues
    steps = cl.get("steps") or []
    cables = cl.get("cables") or []
    if cl["status"] == "not_applicable":
        if steps:
            issues.append("STATUS=not_applicable 却读到了逐轮迭代数据，状态矛盾")
        return issues
    if not steps:
        issues.append("声明有迭代但没读到任何 I 行（逐轮过程缺失）")
        return issues
    if cl["niter"] != len(steps):
        issues.append(f"段声明 {cl['niter']} 轮，实际读到 {len(steps)} 轮")
    if cl["ncables"] != len(cables):
        issues.append(f"段声明 {cl['ncables']} 根索，实际读到 {len(cables)} 行终值")
    for i, s in enumerate(steps):
        if s["iter"] != i + 1:
            issues.append(f"第 {i + 1} 行的轮次编号是 {s['iter']}，编号不连续")
            break
    if cl["converged"] != (cl["status"] == "ok"):
        issues.append("CONVERGED 标志与 STATUS 不一致")
    if cl["converged"]:
        fe = final_error(cl)
        if fe is None or fe > cl["tol"] * (1.0 + tol) + 1e-15:
            issues.append("标为收敛，但末轮控制误差仍大于容差")
        if not steps[-1]["converged"]:
            issues.append("标为收敛，但末轮没有 CONVERGED 事件标记")
    else:
        issues.append("外层迭代未收敛：" + status_note(cl))
    if cl["niter"] > cl["maxiter"] > 0:
        issues.append(f"迭代轮数 {cl['niter']} 超过上限 {cl['maxiter']}")
    nj = cl.get("nj", 0)
    J = cl.get("J") or []
    if nj and cl["ncables"] and nj != cl["ncables"]:
        issues.append(f"影响矩阵阶数 NJ={nj} 与索数 {cl['ncables']} 不一致")
    if nj:
        if len(J) != nj or any(len(r) != nj for r in J):
            issues.append(f"NJ={nj} 但实际 J 矩阵维度不符")
        elif cl["sens"].get("j_available") is False:
            issues.append("读到了 J 矩阵但 SENS JAVAIL=0，状态矛盾")
    elif cl["sens"].get("j_available"):
        issues.append("SENS JAVAIL=1 但没有读到 NJ / J 行")
    # 终值自洽：弦长偏差与索力偏差只是展示派生，用宽松容差对拍。
    for c in cables[:3]:
        if abs(c["dL_m"] - (c["chord_m"] - c["Ltar_m"])) > 1e-9:
            issues.append(f"索 {c['elem']} 的弦长偏差与弦长/设计弦长不同源")
            break
    return issues


def iteration_table(cl: dict[str, Any]) -> dict[str, Any]:
    """逐轮收敛明细表（正文用，行数 = 迭代轮数）。"""
    rows: list[list[Any]] = []
    for s in cl.get("steps") or []:
        ev = []
        if s["rebuilt"]:
            ev.append("重建影响矩阵" if cl.get("kind") == "cable_length" else "重建敏感度")
        if s["capped"]:
            ev.append("步长限幅")
        if s["fallback"]:
            ev.append("不动点兜底")
        if s["line_search"]:
            ev.append(f"线搜索 {s['line_search']} 次")
        if s["converged"]:
            ev.append("收敛")
        rows.append([
            s["iter"], f"{s['err']:.6g}", f"{s['geom_err']:.6g}",
            f"{s['step_max']:.6g}", f"{s['alpha']:.4g}",
            "、".join(ev) if ev else "—",
        ])
    note = (mode_note(cl) + " " + sens_note(cl)
            + f" 收敛容差 {cl.get('tol', 0):.3g}，迭代上限 {cl.get('maxiter', 0)} 轮，"
            f"本次 {cl.get('niter', 0)} 轮。")
    return {
        "title": "索长迭代 / 成桥态几何闭合逐轮收敛",
        "note": note,
        "headers": ["轮次", err_unit(cl), "成桥弦长偏差 (m)",
                    "最大修正量 (m)", "步长因子 α", "本轮事件"],
        "rows": rows,
    }


def cable_table(cl: dict[str, Any]) -> dict[str, Any]:
    """每索目标/成桥索力与下料索长明细表（归文末明细表章）。"""
    rows: list[list[Any]] = []
    for c in cl.get("cables") or []:
        rows.append([
            c["elem"], f"{c['T_target_kN']:,.1f}", f"{c['T_final_kN']:,.1f}",
            f"{c['T_err_rel']:.3g}", f"{c['L0_m']:.4f}",
            f"{c['chord_m']:.4f}", f"{c['Ltar_m']:.4f}",
        ])
    return {
        "title": "成桥索力命中与下料索长（逐索）",
        "note": "目标索力、成桥索力、下料 L0、成桥弦长与设计弦长均取自内核 "
                "[LENGTH] 段的同一份 E 行；相对偏差与弦长偏差只是展示派生。"
                "索力由内核共转列式成桥态给出，Python 不重算。",
        "headers": ["索单元", "目标索力 (kN)", "成桥索力 (kN)", "相对偏差",
                    "下料 L0 (m)", "成桥弦长 (m)", "设计弦长 (m)"],
        "rows": rows,
    }
