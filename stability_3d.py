"""stability_3d.py —— 非线性稳定全过程 ``[STABILITY]`` 段的唯一数据契约。

定位
----
**这里不求解、不重算、不补零。** 球面弧长法（Crisfield 球面约束、
λ 作为未知量的增广系统、越过极值点的下降段）全部由 FEM-Eigen 内核
``stability_path_3d.hpp::solveArcLengthPath3D`` 完成，经
``stability_output_3d.hpp::formatStabilitySection3D`` 写成文本段；
本模块是**唯一解析器**，避免求解器、报告、界面各写一套正则而漂移。

与 ``[BUCKLING]`` 分工
----------------------
``buckling_3d.py`` 管**线弹性一次特征值** λ；索承体系共转成桥态的
切线刚度已含几何刚度，那个 λ 不适用，内核写
``ERROR=nonlinear_state_not_supported``。本模块管的是**荷载–位移
全过程平衡路径**：逐平衡点的 λ、控制点位移、残差、LU 对角量级比与
收敛标志。两者**分列**，不得互相冒充：λ 是参考荷载放大倍数，
不是 JTG 3362 的稳定安全系数，规范也没有 λ 的合格限值。

段格式（详见 ``FEM-Eigen/stability_output_3d.hpp``）::

    [STABILITY]
    KIND=spherical_arc_length STATUS=ok BASIS=reference_configuration PATHMETHOD=SPHERE
    NSTEP=273 NPOINT=274 CONVERGED=1 CTRLNODE=2 CTRLDOF=2 STRAIN=1 PSI=1
    LAMBDA_FINAL=0.00629373095822 ERROR=none
    I STEP=0 LAMBDA=0 CTRL=0 RES=0 ITER=0 DS=0.005 PIVOT=1 CONVERGED=1
    I STEP=1 LAMBDA=0.00493 CTRL=-0.00082 RES=0.00011 ITER=2 DS=0.005 PIVOT=5.2e-09 CONVERGED=1

不做
----
不组装 K / 切线，不重算 λ 或位移，不求极值点（极值点由内核路径的
λ 转折给出，或由 benchmark 里的独立解析闭式 oracle 校验），
不反算荷载，不给 λ 编造规范限值，缺段不补 0、不摆空表。

如实边界（``BOUNDARY_NOTE``）
----------------------------
只有球面弧长法、默认 ``STABILITY=0`` 全关；位移控制 / Riks /
分支屈曲 / 初始缺陷 / 材料非线性 / CR 梁后屈曲 / 真实桥梁全过程
对标均未覆盖，且**不等于**成桥态闭环已具备全过程稳定分析。
能力矩阵该行标 ``P`` 不标 ``Y``。
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

BOUNDARY_NOTE = (
    "只有球面弧长法（PATHMETHOD=SPHERE），默认 STABILITY=0 全关；"
    "位移控制 / Riks / 分支屈曲与分支切换 / 初始缺陷与缺陷敏感性 / "
    "材料非线性 / CR 梁后屈曲 / 真实桥梁全过程稳定对标均未覆盖。"
    "本段基于 readModel3D 参考构形，不等于成桥态闭环已具备全过程稳定分析。"
    "λ 是参考荷载放大倍数，不是规范稳定安全系数，JTG 3362 无 λ 合格限值。"
)

#: 内核错误码 → 中文说明。不静默：解析不到稳定段时按这个写明原因。
ERROR_TEXT = {
    "zero_reference_load": "模型没有参考荷载（*CLOAD 为空或全为 0），"
                           "弧长法无法组装参考荷载向量，本次未产出平衡路径。",
    "empty_dof_map": "模型没有可用自由度（约束过强或节点缺失），"
                     "本次未产出平衡路径。",
    "invalid_control_dof": "控制节点 / 控制自由度无效或未给 CTRLNODE，"
                           "内核无法确定路径终止判据，本次未产出平衡路径。",
    "arc_length_step_collapsed": "弧长半径反复折半到下限仍不收敛，"
                                 "路径跟踪中止；已产出的平衡点不可用。",
    "max_steps_reached": "达到 MAXPATHSTEP 上限但控制点位移未达 CTRLTARGET，"
                         "路径未走完；不把中途结果冒充全过程曲线。",
    "solver_exception": "弧长法求解抛异常（刚度奇异 / 存在机构等），"
                        "本次未产出平衡路径。",
    "unsupported_path_method": "PATHMETHOD 不是 SPHERE；本内核只实现"
                               "球面弧长法，不做位移控制 / Riks。",
    "no_equilibrium_point": "内核没有产出任何平衡点，本次无路径可展示。",
    "no_stability_section": "求解器未输出稳定路径段（STABILITY=0 未开启，"
                            "或使用了旧版内核）；本项做不了。",
}

_RE_HEAD = re.compile(
    r"^KIND=(\S+) STATUS=(\S+) BASIS=(\S+) PATHMETHOD=(\S+)$")
_RE_SUM = re.compile(
    r"^NSTEP=(\d+) NPOINT=(\d+) CONVERGED=([01]) "
    r"CTRLNODE=(\d+) CTRLDOF=(\d+) STRAIN=([01]) PSI=(\S+)$")
_RE_FINAL = re.compile(r"^LAMBDA_FINAL=(\S+) ERROR=(\S+)$")
_RE_POINT = re.compile(
    r"^I STEP=(\d+) LAMBDA=(\S+) CTRL=(\S+) RES=(\S+) "
    r"ITER=(\d+) DS=(\S+) PIVOT=(\S+) CONVERGED=([01])$")
_RE_ERR_HEAD = re.compile(
    r"^KIND=none STATUS=not_applicable BASIS=reference_configuration$")
_RE_ERR_CODE = re.compile(r"^ERROR=(\S+)$")


def empty_stability(error: str = "") -> dict[str, Any]:
    """统一空结构。``error`` 为空表示「根本没这段」，非空表示内核明确报错。

    ``lambda_final`` 用 ``None`` 而不是 0，**不补零**：0 是一个合法的
    λ 值（路径穿过 P(2H)=0），拿 0 冒充「未产出」会造出假平衡点。
    """
    return {
        "kind": "", "status": "", "basis": "reference_configuration",
        "pathmethod": "", "nstep": 0, "npoint": 0, "converged": False,
        "ctrlnode": 0, "ctrldof": 0, "strain": 0, "psi": 0.0,
        "lambda_final": None, "points": [], "error": error,
    }


def _f(raw: str) -> float:
    v = float(raw)
    return v if math.isfinite(v) else 0.0


def parse_stability_section(lines: Iterable[str]) -> dict[str, Any]:
    """解析 ``[STABILITY]`` **段内**的行（不含段头）。

    正常段与 ERROR 段两条路径都走这里：ERROR 段返回
    ``kind="none" / status="not_applicable" / points=[]`` 与错误码，
    由报告层按 ``ERROR_TEXT`` 写中文「做不了」，不摆空表。
    """
    out = empty_stability()
    saw_head = False
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if _RE_ERR_HEAD.match(line):
            out["kind"] = "none"
            out["status"] = "not_applicable"
            saw_head = True
            continue
        m = _RE_ERR_CODE.match(line)
        if m:
            out["error"] = "" if m.group(1) == "none" else m.group(1)
            continue
        m = _RE_HEAD.match(line)
        if m:
            out["kind"] = m.group(1)
            out["status"] = m.group(2)
            out["basis"] = m.group(3)
            out["pathmethod"] = m.group(4)
            saw_head = True
            continue
        if not saw_head:
            continue
        m = _RE_SUM.match(line)
        if m:
            out["nstep"] = int(m.group(1))
            out["npoint"] = int(m.group(2))
            out["converged"] = m.group(3) == "1"
            out["ctrlnode"] = int(m.group(4))
            out["ctrldof"] = int(m.group(5))
            out["strain"] = int(m.group(6))
            out["psi"] = _f(m.group(7))
            continue
        m = _RE_FINAL.match(line)
        if m:
            out["lambda_final"] = _f(m.group(1))
            out["error"] = "" if m.group(2) == "none" else m.group(2)
            continue
        m = _RE_POINT.match(line)
        if m:
            out["points"].append({
                "step": int(m.group(1)),
                "lambda": _f(m.group(2)),
                "ctrl": _f(m.group(3)),
                "res": _f(m.group(4)),
                "iter": int(m.group(5)),
                "ds": _f(m.group(6)),
                "pivot": _f(m.group(7)),
                "converged": m.group(8) == "1",
            })
    if not saw_head:
        out["error"] = "no_stability_section"
    elif out["kind"] == "none" and not out["error"]:
        out["error"] = "no_equilibrium_point"
    return out


def stability_error_text(result: dict[str, Any]) -> str:
    """内核错误码 → 中文原因；未知码也如实写出，不静默吞掉。"""
    code = result.get("error", "")
    if not code:
        return ""
    return ERROR_TEXT.get(code, f"内核返回未登记的错误码 {code}，本次未产出。")


def stability_report_text(result: dict[str, Any]) -> str:
    """报告章正文。**未产出时照出章节但写中文「做不了」，不出空表。**

    只引用段内字段，不在这里重算极值点 / λ / 位移。
    """
    if result.get("points"):
        lam = [p["lambda"] for p in result["points"]]
        lam_max, lam_min = max(lam), min(lam)
        return (
            f"球面弧长法平衡路径：{len(result['points'])} 个平衡点，"
            f"λ 范围 {lam_min:.6g} ~ {lam_max:.6g}（越过极限点后出现下降段），"
            f"终点 λ={result['lambda_final']:.6g}，"
            f"控制量 CTRLDOF={result['ctrldof']}、应变度量 STRAIN={result['strain']}。"
            f"λ 是参考荷载放大倍数，不是规范稳定安全系数；"
            f"极限点由 λ 转折判定，PIVOT 是全矩阵 LU 对角量级比（含约束"
            f"自由度置 1），不是奇异判据。边界：{BOUNDARY_NOTE}"
        )
    why = stability_error_text(result) or "本次未产出稳定路径。"
    return (
        f"非线性稳定全过程：做不了（{why}）"
        f"不摆空表、不补 0，也不拿线弹性屈曲 λ 冒充。边界：{BOUNDARY_NOTE}"
    )


def stability_path_table(result: dict[str, Any]) -> list[list[Any]] | None:
    """有平衡点才返回表；无产出返回 ``None``（调用方据此不出空表）。"""
    if not result.get("points"):
        return None
    return [[p["step"], p["lambda"], -p["ctrl"], p["res"], p["iter"],
             p["ds"], p["pivot"], int(p["converged"])]
            for p in result["points"]]


def main() -> int:
    """命令行自检：无参数时打印模块口径，不写文件、不调求解器。"""
    print("stability_3d.py — [STABILITY] 唯一解析器（只解析，不重算）")
    print(BOUNDARY_NOTE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
