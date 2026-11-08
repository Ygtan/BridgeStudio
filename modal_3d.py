"""modal_3d.py —— 模态（自振特性）结果的数据契约与派生量。

定位
----
**这里不求解特征值。** K φ = λ M φ 由 FEM-Eigen 内核完成：

- 线性内核（梁格 / 桁架 / 施工阶段）：``computeNaturalFrequencies3D``，
  成桥态构型 ``finalStageModel3D`` + 线弹性刚度 + 一致质量矩阵；
- 索承体系（斜拉 / 悬索）：``modalFromTangent``，**成桥态一致切线刚度**
  （含索的 Ernst 效应与几何刚度），张紧索的切线刚度远大于弹性刚度，
  用线弹性装配刚度会把基频算低。

两个 exe 用 ``modal_output_3d.hpp`` 把同一份 ``ModalResult3D`` 写成文本段
（``modal`` / ``[MODAL]``）。本模块把它解析成统一结构，供结果层、报告层、
界面层共用——**解析只有这一处**，避免两个求解器各写一套正则而漂移。

段格式（详见 FEM-Eigen/modal_output_3d.hpp）::

    NMODES=8 NDOF=1234 BASIS=xyz
    MASS X=<kg> Y=<kg> Z=<kg>           各方向平动总质量 rᵀ M r
    F <i> <f_Hz> <T_s> <mx> <my> <mz> <mnorm>
    S <i> <node> <ux> <uy> <uz>

自由度顺序 [u, v, w, θx, θy, θz]，故 **local 2 = w = 竖向 Z**；
纯轴向（TRUSS/CABLE）节点只有 3 个平动自由度，内核 ``safeDof`` 已处理。

参与质量
--------
Γ_d = φᵀ M r_d（r_d 为方向 d 的刚体平动向量），
有效参与质量 = Γ_d² / (φᵀ M φ)。全部阶次累计应趋近 r_dᵀ M r_d，
这是"截断到第 N 阶够不够"的直接判据；只取前 8/12 阶时，累计占比不足
必须在报告里写出来，不能假装振型齐全。

不做
----
不组装 K、M，不重算频率（那会与既有 ``f1_vertical`` / JTG D60 冲击系数
口径分裂）；不做反应谱、时程、抖振、风致振动；不做模态叠加。
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

# ---------------------------------------------------------------- 段解析

_RE_HEAD = re.compile(r"^NMODES=(\d+) NDOF=(\d+) BASIS=(\S+)$")
_RE_MASS = re.compile(r"^MASS X=(\S+) Y=(\S+) Z=(\S+)$")
_RE_F = re.compile(r"^F (\d+) (\S+) (\S+) (\S+) (\S+) (\S+) (\S+)$")
_RE_S = re.compile(r"^S (\d+) (\d+) (\S+) (\S+) (\S+)$")
_RE_ERR = re.compile(r"^ERROR=(\S+)$")

#: 内核给出的错误码 → 中文说明。不静默：解析不到模态时按这个写明原因。
ERROR_TEXT = {
    "no_mass": "全部单元质量为零（*MP 缺 DENS 且 *R 缺 ADDMASS），无法组装质量矩阵。",
    "no_free_dof": "没有可用的自由自由度（约束过强或刚度/质量对角线为零）。",
    "solver_exception": "特征求解抛异常（刚度矩阵可能奇异 / 存在机构）。",
    "no_lane_or_no_mass": "未定义车道荷载或质量为零，成桥态切线刚度上的模态未产出。",
    "not_converged": "非线性成桥态未收敛，模态未产出。",
    "no_modal_section": "求解器未输出模态段（可能是旧版内核）。",
}


def empty_modal(error: str = "") -> dict[str, Any]:
    """统一空结构。``error`` 为空表示"根本没这段"，非空表示内核明确报错。"""
    return {
        "nmodes": 0, "ndof": 0, "basis": "xyz",
        "mass": {"X": 0.0, "Y": 0.0, "Z": 0.0},
        "modes": [], "error": error,
    }


def _f(raw: str) -> float:
    v = float(raw)
    return v if math.isfinite(v) else 0.0


def parse_modal_section(lines: Iterable[str]) -> dict[str, Any]:
    """解析模态**段内**的行（不含段头 ``modal`` / ``[MODAL]``）。

    返回统一结构::

        {"nmodes": int, "ndof": int, "basis": str,
         "mass": {"X": kg, "Y": kg, "Z": kg},
         "modes": [{"order": int, "f": Hz, "T": s,
                    "mx": kg, "my": kg, "mz": kg, "mnorm": 1,
                    "shape": {node: (ux, uy, uz)}}],
         "error": str}

    行格式不认识就跳过（向前兼容：内核以后加字段不会把旧解析弄崩），
    但 ``nmodes`` 声明了却没读到对应 F 行，会在 ``validate`` 里被抓出来。
    """
    out = empty_modal()
    by_order: dict[int, dict[str, Any]] = {}
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        m = _RE_HEAD.match(line)
        if m:
            out["nmodes"] = int(m.group(1))
            out["ndof"] = int(m.group(2))
            out["basis"] = m.group(3)
            continue
        m = _RE_MASS.match(line)
        if m:
            out["mass"] = {"X": _f(m.group(1)), "Y": _f(m.group(2)),
                           "Z": _f(m.group(3))}
            continue
        m = _RE_ERR.match(line)
        if m:
            out["error"] = m.group(1)
            continue
        m = _RE_F.match(line)
        if m:
            mode = {
                "order": int(m.group(1)), "f": _f(m.group(2)), "T": _f(m.group(3)),
                "mx": _f(m.group(4)), "my": _f(m.group(5)), "mz": _f(m.group(6)),
                "mnorm": _f(m.group(7)), "shape": {},
            }
            out["modes"].append(mode)
            by_order[mode["order"]] = mode
            continue
        m = _RE_S.match(line)
        if m:
            mode = by_order.get(int(m.group(1)))
            if mode is not None:
                mode["shape"][int(m.group(2))] = (
                    _f(m.group(3)), _f(m.group(4)), _f(m.group(5)))
    return out


# ---------------------------------------------------------------- 派生量

def mode_direction(mode: dict[str, Any], total: float = 0.0) -> str:
    """该阶振型的主导平动方向（按有效参与质量取最大）。

    ``total`` 为该方向平动总质量；传入后，若最大参与质量不足其 0.1%，
    返回 ``"—"``（扭转/局部模态在三个平动方向都几乎不参与，硬报一个方向
    是假的）。不传则只做三者比较，供 ``vertical_fundamental`` 用。
    """
    best = max(("X", "Y", "Z"), key=lambda d: mode["m" + d.lower()])
    if total > 0.0 and mode["m" + best.lower()] < 1e-3 * total:
        return "—"
    return best


def cumulative_ratio(modal: dict[str, Any], direction: str = "Z") -> list[float]:
    """逐阶累计有效参与质量占该方向总质量的比例（0~1）。"""
    total = float(modal["mass"].get(direction, 0.0) or 0.0)
    if total <= 0.0:
        return []
    acc, out = 0.0, []
    for mode in modal["modes"]:
        acc += float(mode["m" + direction.lower()])
        out.append(acc / total)
    return out


def vertical_fundamental(modal: dict[str, Any]) -> float | None:
    """竖向（Z）主导的最低阶频率 Hz，用于与 ``f1_vertical`` 对拍。

    内核 ``verticalBendingFrequency3D`` 的判据是"主梁节点 w² 占平动能量
    过半"，本函数用"Z 有效参与质量最大"近似，两者通常指向同一阶；对拍
    时以"频率值是否出现在序列里"为准，不拿本函数当严格 oracle。
    """
    for mode in modal["modes"]:
        if mode["f"] > 0.0 and mode_direction(mode) == "Z":
            return float(mode["f"])
    return None


def mode_shape_series(modal: dict[str, Any], order: int,
                      nodes: dict[int, tuple[float, float, float]],
                      axis: str = "x", component: int = 2) -> tuple[list[float], list[float]]:
    """取某阶振型沿 ``axis``（x/y/z）的 ``component`` 分量，按坐标升序。

    供振型图使用：``component=2`` 是竖向 w。返回 ``(坐标, 分量)``；
    没有振型数据就返回两条空列表（调用方据此不画图，不画空坐标轴）。
    """
    mode = next((m for m in modal["modes"] if m["order"] == order), None)
    if mode is None or not mode["shape"]:
        return [], []
    ax = {"x": 0, "y": 1, "z": 2}[axis]
    pairs = []
    for nid, vec in mode["shape"].items():
        if nid not in nodes:
            continue
        pairs.append((float(nodes[nid][ax]), float(vec[component])))
    pairs.sort(key=lambda p: p[0])
    return [p[0] for p in pairs], [p[1] for p in pairs]


def validate(modal: dict[str, Any], f1_vertical: float | None = None,
             tol: float = 1e-6) -> list[str]:
    """自检，返回问题列表（空 = 全通过）。不静默：每条都是可写进校核的话。"""
    issues: list[str] = []
    if modal.get("error"):
        issues.append(ERROR_TEXT.get(modal["error"], f"模态段报错：{modal['error']}"))
        return issues
    modes = modal.get("modes") or []
    if not modes:
        issues.append(ERROR_TEXT["no_modal_section"])
        return issues
    if modal["nmodes"] and len(modes) != modal["nmodes"]:
        issues.append(f"模态段声明 {modal['nmodes']} 阶，实际读到 {len(modes)} 阶")
    fs = [float(m["f"]) for m in modes]
    if any(f <= 0.0 for f in fs):
        issues.append("存在非正频率（刚度奇异或存在机构），模态结果不可用")
    if any(fs[i + 1] < fs[i] - 1e-9 for i in range(len(fs) - 1)):
        issues.append("频率未按升序排列")
    for m in modes:
        if m["mnorm"] <= 0.0 or abs(m["mnorm"] - 1.0) > 0.05:
            issues.append(f"第 {m['order']} 阶 φᵀMφ={m['mnorm']:.4g}，"
                          "振型未按质量归一，参与质量不可直接累加")
            break
        if min(m["mx"], m["my"], m["mz"]) < -1e-6:
            issues.append(f"第 {m['order']} 阶出现负的有效参与质量")
            break
    if f1_vertical and f1_vertical > 0.0:
        hit = min(fs, key=lambda f: abs(f - f1_vertical)) if fs else 0.0
        if abs(hit - f1_vertical) / f1_vertical > tol:
            issues.append(f"冲击系数基频 f1_vertical={f1_vertical:.6g} Hz 不在"
                          f"模态频率序列里（最近 {hit:.6g} Hz），两套结果不同源")
    return issues


def participation_note(modal: dict[str, Any]) -> str:
    """截断阶数是否够用的一句话结论（写进工程校核，不静默）。"""
    ratios = cumulative_ratio(modal, "Z")
    if not ratios:
        return "竖向平动总质量为零，无法统计参与质量。"
    last = ratios[-1]
    if last >= 0.90:
        return f"前 {len(ratios)} 阶累计竖向参与质量 {last * 100:.1f}%，截断阶数足够。"
    return (f"前 {len(ratios)} 阶累计竖向参与质量仅 {last * 100:.1f}%，"
            "低于 90%：高阶振型尚未覆盖，本结果可用于基频与冲击系数判断，"
            "但不足以做反应谱/模态叠加/风振分析。")


def modal_table(modal: dict[str, Any]) -> dict[str, Any]:
    """计算书用的「各阶自振特性」明细表（键格式与其余 tables 一致）。"""
    rows: list[list[Any]] = []
    ratios = cumulative_ratio(modal, "Z")
    total = max(float(v) for v in modal["mass"].values()) if modal["mass"] else 0.0
    for i, m in enumerate(modal["modes"]):
        r = ratios[i] if i < len(ratios) else 0.0
        rows.append([
            m["order"], f"{m['f']:.4f}", f"{m['T']:.4f}" if m["T"] > 0 else "—",
            mode_direction(m, total),
            f"{m['mx'] / 1000.0:,.1f}", f"{m['my'] / 1000.0:,.1f}",
            f"{m['mz'] / 1000.0:,.1f}", f"{r * 100.0:.1f}%",
            f"{m['mnorm']:.4f}",
        ])
    mass_z = float(modal["mass"].get("Z", 0.0) or 0.0)
    note = (
        "按 Kφ=λMφ 求前若干阶自振特性，K/M 与振型均由 FEM-Eigen 内核给出；"
        "线性内核用成桥态构型 + 线弹性刚度，斜拉/悬索桥用成桥态一致切线刚度。"
        "质量 = DENS×A + ADDMASS 的一致质量矩阵（kg），Z 为竖向。"
        "有效参与质量 Γ_d²/(φᵀMφ) 以 t 计，累计 Z 占比越接近 100% 说明"
        "截断阶数越充分；该值偏低时不宜据此做反应谱或风振分析。"
        f"竖向平动总质量 {mass_z / 1000.0:,.1f} t。"
    )
    return {
        "title": "各阶自振频率、周期与振型参与质量",
        "note": note,
        "headers": ["阶次", "频率 (Hz)", "周期 (s)", "主导方向",
                    "X 参与质量 (t)", "Y 参与质量 (t)", "Z 参与质量 (t)",
                    "累计 Z 占比", "φᵀMφ"],
        "rows": rows,
    }
