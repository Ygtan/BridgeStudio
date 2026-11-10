"""modal_2d.py —— 二维模态 / 线性屈曲结果的唯一口径层。

FEM-Eigen 的 2D 内核早就能算这两样：`fem_2d.cpp::computeNaturalFrequencies2D`
与 `computeBucklingFactors2D`，由 `example_modal.exe` 写进 `<stem>.modal.txt`；
BridgeStudio 的 2D 链路此前从未读它，能力矩阵因此把 2D 两格记成 `N`。
本模块只做**解析与口径**，不做力学：

- 不重算 K/M、不自己造频率或 λ（能力边界矩阵 §0 铁律）；
- 2D 段**没有**周期 T / 振型 / 参与质量，`T = 1/f` 也不在 Python 侧补；
- 屈曲段是条件输出（`bf` 非空才有），缺段时如实写明，不摆空表、不补零；
- λ 是参考荷载的线弹性放大倍数，不是规范稳定安全系数，JTG 3362 无 λ 限值。

「结论与计算书同源」：表格只由 `modal_table()` / `buckling_table()` 生成，
HTML 计算书与界面读同一份数据；`status_text()` 是缺结果时的唯一中文文案。
"""

from __future__ import annotations

import re
from typing import Any

FREQ_SECTION = "== 自振频率 =="
BUCK_SECTION = "== 屈曲荷载因子（Pcr = λ·P_ref）=="
SOURCE = "example_modal.exe → <stem>.modal.txt"

_F_RE = re.compile(r"^f(\d+)\s*=\s*([-+0-9.eE]+)\s*Hz\s*$")
_BASE_RE = re.compile(
    r"^基频\s*f1\s*=\s*([-+0-9.eE]+)\s*Hz[，,]\s*"
    r"冲击系数\s*[μu]\s*=\s*([-+0-9.eE]+)")
_LAM_RE = re.compile(r"^[λl](\d+)\s*=\s*([-+0-9.eE]+)\s*$")

LAMBDA_NOTE = (
    "λ 是参考荷载整体放大倍数（Pcr = λ·P_ref），由线弹性特征值给出，"
    "不含初始缺陷、材料非线性与几何非线性，是稳定承载力的上限；"
    "λ 不是规范稳定安全系数，JTG 3362 没有 λ 的合格限值，"
    "λ 也不是 §5.3 的稳定系数 φ，"
    "更不是非线性稳定全过程（荷载–位移极值点）的结论。"
)
FREQ_NOTE = (
    "频率与冲击系数 μ 直接来自 2D 内核的 `.modal.txt`。该段不含周期 T、"
    "振型与参与质量，缺的字段不补零、不由 Python 反算；"
    "本结果只作为自振特性与汽车冲击的口径说明，不构成规范合格判定。"
)


def _ordered(values: dict[int, float]) -> list[float]:
    """按阶号取回数值；阶号不连续时按升序保留，不臆造缺失阶。"""
    return [v for _, v in sorted(values.items())]


def parse_modal_txt(txt: str) -> dict[str, Any]:
    """解析 `example_modal.exe` 的 `.modal.txt`，只认内核真正写出的字段。

    返回 ``{status, frequencies, f1, mu, buckling, has_buckling_section,
    f1_matches_section, source, note}``。任何「段缺失 / 字段不全 / 自相矛盾」
    都用 `status` 显式给出，不静默补默认值。
    """
    out: dict[str, Any] = {
        "status": "no_frequency_section",
        "frequencies": [],
        "f1": None,
        "mu": None,
        "buckling": [],
        "has_buckling_section": False,
        "f1_matches_section": True,
        "source": SOURCE,
        "note": "",
    }
    section: str | None = None
    freqs: dict[int, float] = {}
    lams: dict[int, float] = {}
    base: tuple[float, float] | None = None
    for raw in (txt or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("=="):
            if "自振频率" in line:
                section = "freq"
            elif "屈曲荷载因子" in line:
                section = "buck"
                out["has_buckling_section"] = True
            else:
                section = None
            continue
        if section == "freq":
            m = _F_RE.match(line)
            if m:
                freqs[int(m.group(1))] = float(m.group(2))
                continue
            b = _BASE_RE.match(line)
            if b:
                base = (float(b.group(1)), float(b.group(2)))
            continue
        if section == "buck":
            m = _LAM_RE.match(line)
            if m:
                lams[int(m.group(1))] = float(m.group(2))
    out["frequencies"] = _ordered(freqs)
    out["buckling"] = _ordered(lams)
    if section is None and not freqs and "自振频率" not in (txt or ""):
        out["status"] = "no_frequency_section"
    elif not out["frequencies"]:
        out["status"] = "no_frequencies"
    elif base is None:
        out["status"] = "incomplete_base_line"
    else:
        out["f1"], out["mu"] = base
        out["f1_matches_section"] = (
            abs(out["f1"] - out["frequencies"][0])
            <= 1e-6 * max(1.0, abs(out["f1"])))
        out["status"] = "ok" if out["f1_matches_section"] else "inconsistent_f1"
    if out["status"] == "ok" and out["has_buckling_section"] and not out["buckling"]:
        # 段头有了却一行 λ 都没有：仍算「本次无屈曲结果」，不许拿空表冒充。
        out["has_buckling_section"] = False
    out["note"] = status_text(out)
    return out


def status_text(m: dict[str, Any] | None) -> str:
    """缺结果时的唯一中文文案；调用方（HTML / 界面）不得另写一套。"""
    m = m or {}
    st = m.get("status", "not_requested")
    if st == "ok":
        nf, nl = len(m.get("frequencies") or []), len(m.get("buckling") or [])
        head = (f"2D 内核已输出 {nf} 阶自振频率（f1 = {m.get('f1'):g} Hz，"
                f"μ = {m.get('mu'):g}）" if m.get("f1") is not None
                else f"2D 内核已输出 {nf} 阶自振频率")
        if nl:
            return head + f"与 {nl} 阶线性屈曲因子（λ1 = {m['buckling'][0]:g}）。"
        return (head + "；本次内核未写出屈曲荷载因子段（无参考轴压荷载时 "
                "`bf` 为空），因此不做屈曲验算，也不补零、不推算 λ。")
    return {
        "not_requested": "本次未请求 2D 自振 / 屈曲分析（默认关闭，"
                         "勾选后由 example_modal.exe 读同一份 .dat 输出）。",
        "unavailable": "未找到 example_modal.exe，2D 自振 / 屈曲本次做不了。"
                       "编译：cd D:/AI-work/FEM-Eigen && cmake --build build "
                       "--target example_modal。静力与活载结果不受影响。",
        "no_output": "example_modal.exe 未写出 <stem>.modal.txt，本次无 "
                     "自振 / 屈曲结果（不补零、不推算 λ）。静力与活载结果不受影响。",
        "solver_error": "example_modal.exe 运行失败，本次无自振 / 屈曲结果"
                        "（不补零、不推算 λ）。静力与活载结果不受影响。",
        "no_frequency_section": ".modal.txt 里没有「== 自振频率 ==」段，"
                                "本次无自振结果（不补零、不造频率）。",
        "no_frequencies": ".modal.txt 的自振频率段里没有 f<i> 行，"
                          "本次无自振结果（内核可能无自由自由度或质量为零）。",
        "incomplete_base_line": ".modal.txt 缺「基频 f1 / 冲击系数 μ」行，"
                                "字段不全，本次不把频率表当成完整结果。",
        "inconsistent_f1": ".modal.txt 的基频 f1 与 f1 行不一致，"
                           "两套频率不得同时引用，本次判为不可用。",
    }.get(st, f"2D 自振 / 屈曲结果状态 {st}，本次不出表。")


def modal_table(m: dict[str, Any]) -> dict[str, Any] | None:
    """自振频率表（唯一数据源）。status 不是 ok 时返回 None。"""
    if (m or {}).get("status") != "ok":
        return None
    rows = [[str(i + 1), f"{v:.6g}"] for i, v in enumerate(m["frequencies"])]
    return {"title": "2D 自振频率", "headers": ["阶", "频率 f (Hz)"],
            "rows": rows, "note": FREQ_NOTE + " 来源：" + SOURCE + "。"}


def buckling_table(m: dict[str, Any]) -> dict[str, Any] | None:
    """线性屈曲因子表；内核没写 λ 段时返回 None（章节仍出说明）。"""
    if (m or {}).get("status") != "ok" or not m.get("buckling"):
        return None
    rows = [[str(i + 1), f"{v:.6g}"] for i, v in enumerate(m["buckling"])]
    return {"title": "2D 线性屈曲因子",
            "headers": ["阶", "λ（Pcr = λ·P_ref）"], "rows": rows,
            "note": LAMBDA_NOTE + " 来源：" + SOURCE + "。"}


def consistency(m: dict[str, Any], f1_vertical: float | None) -> dict[str, Any]:
    """与车道影响线的 `f1_vertical` 对拍：禁止报告里出现两套频率。

    同一份 .dat 下两个 2D exe 都调用 `computeNaturalFrequencies2D`；
    不一致说明链路被改过或模型读法不同，必须写明而不是挑一个好看的。
    """
    f1 = (m or {}).get("f1")
    c: dict[str, Any] = {"status": "not_available", "f1_modal": f1,
                         "f1_vertical": f1_vertical, "note": ""}
    if (m or {}).get("status") != "ok" or f1 is None:
        c["note"] = "本次无可用 2D 自振结果，不做 f1 同源对拍。"
        return c
    if f1_vertical is None:
        c["status"] = "no_reference"
        c["note"] = ("本次未产出车道影响线频率 f1_vertical，没有第二套频率可对拍；"
                     "报告只引用 .modal.txt 的 f1，不拿冲击系数反推频率。")
        return c
    rel = abs(f1 - f1_vertical) / max(1.0, abs(f1))
    if rel > 1e-3:
        c["status"] = "mismatch"
        c["note"] = (f"两套频率不一致：.modal.txt f1={f1:.6g} Hz，"
                     f"影响线 f1_vertical={f1_vertical:.6g} Hz（相对差 {rel:.3g}）。"
                     "禁止同时引用两者，须核对两个 exe 是否读了同一份 .dat。")
        return c
    c["status"] = "ok"
    c["note"] = (f"f1 与车道影响线 f1_vertical 同源一致"
                 f"（{f1:.6g} Hz），冲击系数 μ={m.get('mu'):g} 来自同一次自振求解。")
    return c
