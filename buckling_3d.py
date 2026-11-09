"""buckling_3d.py —— 线性屈曲荷载因子的数据契约与派生量。

定位
----
**这里不求解特征值。** (K + λ·Kg)·φ = 0 由 FEM-Eigen 内核完成：

- 线性阶段内核（梁格 / 空间桁架 / 拱桥施工）：``computeBucklingResult3D``，
  参考荷载 = 成桥态构型 + 成桥态恒载合力（``finalStageBucklingModel3D``，
  阶段增量按 (node,dof) 累加），K 为线弹性刚度、Kg 由参考荷载下的轴力组装；
- 索承体系（斜拉 / 悬索）：**没有**对应的线性屈曲结果。成桥态是共转列式
  几何非线性平衡态，一致切线刚度已含成桥态几何刚度，把未变形构型上的
  一次特征值 λ 套上去会得到一个既不是"荷载放大倍数"、也不是"稳定安全
  系数"的数。内核写 ``ERROR=nonlinear_state_not_supported``，这里如实标注。

两个 exe 用 ``buckling_output_3d.hpp`` 把同一份 ``BucklingResult3D`` 写成
文本段（``buckling`` / ``[BUCKLING]``）。本模块是**唯一解析器**，避免两个
求解器各写一套正则而漂移。

段格式（详见 FEM-Eigen/buckling_output_3d.hpp）::

    NMODES=6 NDOF=126 BASIS=linear_elastic
    REF NLOAD=1 PREF=1000000 SUMFZ=0 NBEAM=20 NAXIAL=0 KGNZ=488
        NFREE=120 COMPRESSION=1 CHOLESKY=1 STATUS=ok
    L 1 20.33138614 20331.38614          # 阶次 λ Pcr(kN)

PREF 是参考荷载的规模 Σ|Fi|（N，不限方向），``Pcr = λ·PREF/1000``（kN）。
SUMFZ 只是诊断：dof 3 = w = 竖向 Z，带符号。

λ 的口径（**不要**写成"稳定验算通过/不通过"）
---------------------------------------------
λ 是**参考荷载整体放大倍数**：λ > 1 表示成桥态参考荷载下未失稳，λ ≤ 1
表示参考荷载已达到/超过临界荷载。JTG 3362 / JTG D60 没有给 λ 的合格限值，
硬编一个就是假校核。线性屈曲不含初始缺陷、材料非线性与几何非线性，是
真实稳定承载力的**上限**；它不能替代 §5.3 的构件稳定系数 φ 验算，更不能
替代非线性稳定全过程（荷载–位移极值点）分析。

不做
----
不组装 K / Kg，不重算 λ；不做非线性稳定、初始缺陷、屈曲后路径；不给
λ 编造规范限值。
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

# ---------------------------------------------------------------- 段解析

_RE_HEAD = re.compile(r"^NMODES=(\d+) NDOF=(\d+) BASIS=(\S+)$")
_RE_REF = re.compile(
    r"^REF NLOAD=(\d+) PREF=(\S+) SUMFZ=(\S+) "
    r"NBEAM=(\d+) NAXIAL=(\d+) KGNZ=(\d+) NFREE=(\d+) "
    r"COMPRESSION=([01]) CHOLESKY=([01]) STATUS=(\S+)$")
_RE_L = re.compile(r"^L (\d+) (\S+) (\S+)$")
_RE_ERR = re.compile(r"^ERROR=(\S+)$")

#: 内核错误码 → 中文说明。不静默：解析不到屈曲段时按这个写明原因。
ERROR_TEXT = {
    "no_reference_load": "成桥态没有参考荷载（*CLOAD / *STAGE_LOAD 全为空），"
                         "无法组装几何刚度，线性屈曲因子未产出。",
    "solver_exception": "屈曲特征求解抛异常（刚度矩阵可能奇异 / 存在机构），"
                        "本次未产出 λ。",
    "nonlinear_state_not_supported": "索承体系成桥态为共转列式几何非线性平衡态，"
                                     "切线刚度已含成桥态几何刚度；基于未变形构型的"
                                     "线弹性屈曲因子不适用。需做非线性稳定全过程分析"
                                     "（荷载–位移极值点），本版本未覆盖。",
    "no_buckling_section": "求解器未输出屈曲段（可能是旧版内核）。",
}

#: 内核 STATUS → 中文说明。λ 为空时「物理上无压杆」与「求解失败」必须分开。
STATUS_TEXT = {
    "ok": "已求得前若干阶线性屈曲荷载因子。",
    "no_compression": "参考荷载下几何刚度无受压软化项（结构中没有受压杆件，"
                      "例如连续梁格只受竖向荷载），因此不存在压屈模态。"
                      "这是物理预期，不是求解失败；也不代表稳定安全系数无限大——"
                      "本结论只对所加的参考荷载成立。",
    "no_modes": "几何刚度里存在受压软化项，但特征求解没有给出有效的正 λ"
                "（可能未收敛或刚度奇异）。该结果不能当作稳定验算通过。",
    "no_free_dof": "没有可用的自由自由度（约束过强或刚度对角线为零）。",
    "cholesky_failed": "刚度矩阵 Cholesky 分解失败（存在机构或刚度非正定）。",
}


def empty_buckling(error: str = "") -> dict[str, Any]:
    """统一空结构。``error`` 为空表示"根本没这段"，非空表示内核明确报错。"""
    return {
        "nmodes": 0, "ndof": 0, "basis": "linear_elastic", "status": "",
        "ref": {
            "nload": 0, "pref_kN": 0.0, "sumFz_kN": 0.0,
            "nbeam": 0, "naxial": 0, "kgnz": 0, "nfree": 0,
            "compression": False, "cholesky": True,
        },
        "modes": [], "error": error,
    }


def _f(raw: str) -> float:
    v = float(raw)
    return v if math.isfinite(v) else 0.0


def parse_buckling_section(lines: Iterable[str]) -> dict[str, Any]:
    """解析屈曲**段内**的行（不含段头 ``buckling`` / ``[BUCKLING]``）。

    返回统一结构::

        {"nmodes": int, "ndof": int, "basis": str,
         "ref": {"nload", "pref_kN", "sumFz_kN", "nbeam", "naxial",
                 "kgnz", "nfree", "compression", "cholesky"},
         "status": str,
         "modes": [{"order": int, "lambda": float, "pcr_kN": float}],
         "error": str}

    行格式不认识就跳过（向前兼容：内核以后加字段不会把旧解析弄崩），
    但 ``NMODES`` 声明了却没读到对应 L 行，会在 ``validate`` 里被抓出来。
    """
    out = empty_buckling()
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
        m = _RE_ERR.match(line)
        if m:
            out["error"] = m.group(1)
            continue
        m = _RE_REF.match(line)
        if m:
            out["ref"] = {
                "nload": int(m.group(1)),
                "pref_kN": _f(m.group(2)) / 1000.0,
                "sumFz_kN": _f(m.group(3)) / 1000.0,
                "nbeam": int(m.group(4)),
                "naxial": int(m.group(5)),
                "kgnz": int(m.group(6)),
                "nfree": int(m.group(7)),
                "compression": m.group(8) == "1",
                "cholesky": m.group(9) == "1",
            }
            out["status"] = m.group(10)
            continue
        m = _RE_L.match(line)
        if m:
            out["modes"].append({
                "order": int(m.group(1)),
                "lambda": _f(m.group(2)),
                "pcr_kN": _f(m.group(3)),
            })
    return out


# ---------------------------------------------------------------- 派生量

def first_factor(bk: dict[str, Any]) -> float | None:
    """一阶屈曲荷载因子 λ₁；没有模态返回 None。"""
    modes = bk.get("modes") or []
    return float(modes[0]["lambda"]) if modes else None


def status_note(bk: dict[str, Any]) -> str:
    """把 STATUS / ERROR 翻成一句可写进校核的话。"""
    if bk.get("error"):
        return ERROR_TEXT.get(bk["error"], f"屈曲段报错：{bk['error']}")
    return STATUS_TEXT.get(bk.get("status", ""),
                           f"内核给出未知状态 STATUS={bk.get('status')!r}，"
                           "不能据此判定稳定。")


def load_note(bk: dict[str, Any]) -> str:
    """λ₁ 相对参考荷载的含义（数学事实，不是规范限值判定）。"""
    lam = first_factor(bk)
    if lam is None:
        return ""
    if lam > 1.0:
        return (f"λ₁ = {lam:.4f} > 1：成桥态参考荷载 "
                f"{bk['ref']['pref_kN']:,.1f} kN 下线性化切线刚度仍正定，"
                f"临界荷载 Pcr = λ₁·P_ref = {bk['modes'][0]['pcr_kN']:,.1f} kN。"
                "这是上限估计，不是规范稳定安全系数。")
    return (f"λ₁ = {lam:.4f} ≤ 1：参考荷载已达到或超过线性临界荷载，"
            "按线性屈曲模型结构在该荷载下已失稳。须复核荷载、约束与构型，"
            "并改用非线性稳定全过程分析。")


def validate(bk: dict[str, Any], tol: float = 1e-6) -> list[str]:
    """自检，返回问题列表（空 = 全通过）。不静默：每条都能写进校核。"""
    issues: list[str] = []
    if bk.get("error"):
        issues.append(status_note(bk))
        return issues
    if not bk.get("status"):
        issues.append(ERROR_TEXT["no_buckling_section"])
        return issues
    modes = bk.get("modes") or []
    status = bk.get("status")
    if status == "ok" and not modes:
        issues.append("STATUS=ok 但没有读到任何 L 行（λ 缺失）")
    if status != "ok" and modes:
        issues.append(f"STATUS={status} 却读到了 {len(modes)} 阶 λ，状态与数据矛盾")
    if bk["nmodes"] != len(modes):
        issues.append(f"屈曲段声明 {bk['nmodes']} 阶，实际读到 {len(modes)} 阶")
    pref = float(bk["ref"]["pref_kN"])
    if pref <= 0.0:
        issues.append("参考荷载规模 P_ref ≤ 0，λ·P_ref 没有物理意义")
    lams = [float(m["lambda"]) for m in modes]
    if any(v <= 0.0 for v in lams):
        issues.append("存在非正屈曲因子（λ ≤ 0 不是压屈模态）")
    if any(lams[i + 1] < lams[i] - 1e-9 for i in range(len(lams) - 1)):
        issues.append("屈曲因子未按升序排列")
    if pref > 0.0:
        for m in modes:
            expect = float(m["lambda"]) * pref
            if abs(float(m["pcr_kN"]) - expect) > tol * max(1.0, abs(expect)):
                issues.append(
                    f"第 {m['order']} 阶 Pcr={m['pcr_kN']:.6g} kN 与 "
                    f"λ·P_ref={expect:.6g} kN 不一致，数值不同源")
                break
    if status in ("no_modes", "no_free_dof", "cholesky_failed"):
        issues.append("屈曲求解未产出可用 λ：" + status_note(bk))
    return issues


def buckling_table(bk: dict[str, Any]) -> dict[str, Any]:
    """计算书用的「各阶线性屈曲荷载因子」明细表（键格式与其余 tables 一致）。"""
    modes = bk.get("modes") or []
    lam1 = first_factor(bk)
    rows: list[list[Any]] = []
    for m in modes:
        lam = float(m["lambda"])
        rows.append([
            m["order"], f"{lam:.4f}", f"{m['pcr_kN']:,.1f}",
            f"{lam / lam1:.2f}" if lam1 else "—",
        ])
    ref = bk["ref"]
    note = (
        "按 (K + λ·Kg)·φ = 0 求前若干阶线性屈曲荷载因子，K / Kg 与 λ 均由 "
        "FEM-Eigen 内核给出。参考荷载 P_ref = Σ|Fi| = "
        f"{ref['pref_kN']:,.1f} kN（成桥态构型 + 成桥态恒载合力；阶段增量按 "
        "节点/自由度累加）；Pcr = λ·P_ref。几何刚度由参考荷载下各单元轴力组装："
        f"BEAM {ref['nbeam']} 根、TRUSS/CABLE {ref['naxial']} 根，"
        f"Kg 非零元 {ref['kgnz']}，自由自由度 {ref['nfree']}。"
        "λ 是荷载整体放大倍数，不是规范稳定安全系数；线性屈曲不含初始缺陷、"
        "材料非线性与几何非线性，是稳定承载力的上限，不能替代 JTG 3362 §5.3 "
        "构件稳定验算或非线性稳定全过程分析。"
    )
    return {
        "title": "各阶线性屈曲荷载因子与临界荷载",
        "note": note,
        "headers": ["阶次", "屈曲因子 λ", "临界荷载 Pcr (kN)", "λ / λ₁"],
        "rows": rows,
    }
