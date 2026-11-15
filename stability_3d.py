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
分支屈曲 / 初始缺陷 / 材料非线性 / CR 梁后屈曲主链路 / 真实桥梁全过程
对标均未覆盖，且**不等于**成桥态闭环已具备全过程稳定分析。
能力矩阵该行标 ``P`` 不标 ``Y``。

第十九次 ① 起有两种 ``BASIS``，**不得混成一个口径**：
``reference_configuration`` 从参考构形 ``u=0`` 起算、λ 放大总参考荷载；
``completion_state`` 从成桥态收敛位移 ``u0`` + 恒定荷载 ``F0`` 起算、
λ 只放大 ``*STABILITY_LOAD`` 增量荷载。后者段内另有 ``STATE CTRL0=``
行给出起点绝对控制位移，用于与 ``[U]`` 段自洽对拍。
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

BOUNDARY_NOTE = (
    "只有球面弧长法（PATHMETHOD=SPHERE），默认 STABILITY=0 全关；"
    "位移控制 / Riks / 分支屈曲与分支切换 / 初始缺陷与缺陷敏感性 / "
    "材料非线性 / CR 梁后屈曲主链路 / 真实桥梁全过程稳定对标均未覆盖。"
    "BASIS=reference_configuration 基于 readModel3D 参考构形、λ 放大总参考荷载；"
    "BASIS=completion_state 以成桥态收敛位移 u0 + 恒定荷载 F0 为起点、"
    "λ 只放大 *STABILITY_LOAD 增量荷载，两者不是同一个量、不得互相冒充。"
    "后者（第十九次 ①）只有 FEM 自洽与独立闭式锚点，ANSYS 真实桥梁两段对标"
    "与施工阶段链路仍未做，不等于成桥态闭环已具备全过程稳定分析。"
    "λ 是参考荷载放大倍数，不是 JTG 3362 的稳定安全系数，规范无 λ 合格限值。"
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
    # 第十九次 ①：STABASIS=COMPLETION 专属，不复用参考构形的错误码。
    "zero_incremental_load": "*STABILITY_LOAD 为空或全为 0，无法组装增量"
                             "参考荷载 Finc；成桥态全过程路径做不了。",
    "unsupported_stability_basis": "STABASIS 不是 REFERENCE / COMPLETION；"
                                   "本内核只有这两种基准，本次未产出平衡路径。",
    "completion_not_converged": "成桥态闭环（找形 / 索长迭代 / 内层 Newton）"
                                "未收敛，不能拿未收敛位移当路径起点；本次未产出。",
    "invalid_initial_state": "成桥态初态向量 u0 / F0 与模型自由度数不一致，"
                             "路径起点无效；本次未产出平衡路径。",
}

_RE_HEAD = re.compile(
    r"^KIND=(\S+) STATUS=(\S+) BASIS=(\S+) PATHMETHOD=(\S+)$")
_RE_SUM = re.compile(
    r"^NSTEP=(\d+) NPOINT=(\d+) CONVERGED=([01]) "
    r"CTRLNODE=(\d+) CTRLDOF=(\d+) STRAIN=([01]) PSI=(\S+)$")
_RE_FINAL = re.compile(r"^LAMBDA_FINAL=(\S+) ERROR=(\S+)$")
_RE_STATE = re.compile(
    r"^STATE CTRL0=(\S+) F0_NORM=(\S+) FINC_NORM=(\S+) RES0=(\S+)$")
_RE_POINT = re.compile(
    r"^I STEP=(\d+) LAMBDA=(\S+) CTRL=(\S+) RES=(\S+) "
    r"ITER=(\d+) DS=(\S+) PIVOT=(\S+) CONVERGED=([01])$")
# ERROR 段的 BASIS 也要如实带回：completion_state 的报错不能写成
# reference_configuration，否则下游会把两种基准的失败原因混在一起。
_RE_ERR_HEAD = re.compile(
    r"^KIND=none STATUS=not_applicable BASIS=(\S+)$")
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
        # 第十九次 ①：completion_state 基准的起点状态（缺省 None 不补 0）。
        "ctrl0": None, "f0_norm": None, "finc_norm": None, "res0": None,
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
            m = _RE_ERR_HEAD.match(line)
            out["kind"] = "none"
            out["status"] = "not_applicable"
            out["basis"] = m.group(1)
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
        m = _RE_STATE.match(line)
        if m:
            out["ctrl0"] = _f(m.group(1))
            out["f0_norm"] = _f(m.group(2))
            out["finc_norm"] = _f(m.group(3))
            out["res0"] = _f(m.group(4))
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
            f"球面弧长法平衡路径：基准 BASIS={result['basis']}，"
            f"{len(result['points'])} 个平衡点，"
            f"λ 范围 {lam_min:.6g} ~ {lam_max:.6g}（越过极限点后出现下降段），"
            f"终点 λ={result['lambda_final']:.6g}，"
            f"控制量 CTRLDOF={result['ctrldof']}、应变度量 STRAIN={result['strain']}。"
            + (f"成桥态起点 CTRL0={result['ctrl0']:.6g}，"
               f"|F0|={result['f0_norm']:.6g}、|Finc|={result['finc_norm']:.6g}，"
               f"段内 CTRL 是相对成桥态的位移增量（不是绝对位移）。"
               if result.get("basis") == "completion_state"
               and result.get("ctrl0") is not None else "")
            + "reference_configuration 的 λ 放大总参考荷载、"
              "completion_state 的 λ 只放大增量荷载，两者不得互相冒充。"
              "λ 是参考荷载放大倍数，不是规范稳定安全系数；"
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
