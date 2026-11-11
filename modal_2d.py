"""modal_2d.py —— 二维模态 / 线性屈曲结果的唯一口径层。

FEM-Eigen 的 2D 内核早就能算这两样：`fem_2d.cpp::computeNaturalFrequencies2D`
与 `computeBucklingFactors2D`，由 `example_modal.exe` 写进 `<stem>.modal.txt`；
BridgeStudio 的 2D 链路此前从未读它，能力矩阵因此把 2D 两格记成 `N`。
本模块只做**解析与口径**，不做力学：

- 不重算 K/M、不自己造频率或 λ（能力边界矩阵 §0 铁律）；
- 默认 `== 自振频率 ==` 段**没有**周期 T / 振型 / 参与质量；opt-in 的
  `[MODAL2D]` 明细段由 FE 内核写出这些量，`T = 1/f` 也只在内核算，
  Python 侧只解析、不现算、不反算、不补零；
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


# ---------------------------------------------------------------- 模态明细段
# 2026-11-11 第九次（B2「模态周期 T / 振型 / 参与质量」）：
# FE 2D 内核本来就算出了全阶振型（ModalResult2D.modes）与一致质量矩阵 M，
# 只是 example_modal.exe 没写。本轮由 FE 侧 `example_modal.exe <dat>
# --modal-detail` 追加 [MODAL2D] 段写出，Python 侧在这里**只解析**。
# 上面 parse_modal_txt / modal_table / buckling_table / status_text 一字未改。

DETAIL_SECTION = "[MODAL2D]"
DETAIL_SOURCE = "example_modal.exe <dat> --modal-detail → <stem>.modal.txt [MODAL2D]"

DETAIL_NOTE = (
    "周期 T=1/f、X/Y 两向有效参与质量 Γ²/(φᵀMφ) 与节点振型均由 FEM-Eigen "
    "2D 内核计算并写出，Python 侧只解析、不现算、不重算 K/M、不补零。"
    "方向口径 BASIS=xy：2D 每节点 3 个自由度（1=水平 u、2=竖向 v、3=转角 θz），"
    "没有出平面自由度，因此**不设 Z 向参与质量（不存在，不是 0）**；θz 只作"
    "振型分量展示，不设参与质量。参与质量是模态截断是否足够的判据"
    "（Σ 各阶趋近于 MASS 行的平动总质量），不是规范合格判定。"
)
SHAPE_NOTE = (
    "这是 M-正交归一的振型分量（φᵀMφ≈1），不是位移、不是内力，不构成规范判定；"
    "θz 是转角自由度，只作展示、不设参与质量。内核写出多少阶多少节点就列多少，"
    "不插值、不补零、不挑选「好看」的阶。"
)

_D_HDR_RE = re.compile(r"^NMODES=(\d+)\s+NDOF=(\d+)\s+BASIS=(\S+)\s*$")
_D_MASS_RE = re.compile(
    r"^MASS\s+X=([-+0-9.eE]+)\s+Y=([-+0-9.eE]+)\s*$")
_D_F_RE = re.compile(
    r"^F\s+(\d+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+"
    r"([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*$")
_D_S_RE = re.compile(
    r"^S\s+(\d+)\s+(\d+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+"
    r"([-+0-9.eE]+)\s*$")

DETAIL_STATUS_TEXT = {
    "not_requested": "本次未请求 2D 模态明细段（opt-in，默认关闭）。",
    "no_detail_section": (
        "内核未写出 [MODAL2D] 明细段，本次没有周期 / 振型 / 参与质量；"
        "可能是 example_modal.exe 不是带 --modal-detail 的当前版本。"
        "不补零、不由 Python 现算。"),
    "kernel_error": "2D 内核在 [MODAL2D] 段写了 ERROR，本次明细不可用。",
    "bad_header": "[MODAL2D] 段缺 NMODES/NDOF/BASIS 头行或格式不符，本次不出表。",
    "bad_basis": (
        "[MODAL2D] 段的 BASIS 不是 xy；3D 的 BASIS=xyz 段不得当 2D 明细解析，"
        "本次不出表、不补 Z 向零值。"),
    "missing_mass": "[MODAL2D] 段缺 MASS X/Y 行，参与质量没有总质量可对拍，本次不出表。",
    "no_modes": "[MODAL2D] 段声明了阶数却没有 F 行，本次不出表、不摆空表。",
    "incomplete_mode_row": (
        "[MODAL2D] 段的 F 行数与 NMODES 不符或字段不全，本次不出表、不补零。"),
    "missing_shapes": "[MODAL2D] 段缺 S 振型行，本次不出振型表、不插值补点。",
    "invalid_period": "[MODAL2D] 段出现 T<=0 的阶，周期不可用，本次不出表。",
    "inconsistent_f": (
        "[MODAL2D] 段各阶频率与同一份 .modal.txt 的「== 自振频率 ==」段不一致"
        "（已按两段打印精度 6 位 / 10 位给 5e-6 容差）；"
        "两套频率禁止同时引用，本次判为不可用。"),
    "ok": "2D 内核已写出模态明细段，周期 / 参与质量 / 振型均为内核原值。",
}


def detail_status_text(d: dict[str, Any] | None) -> str:
    """缺明细时的唯一中文文案；调用方不得另写一套。"""
    st = (d or {}).get("status", "not_requested")
    base = DETAIL_STATUS_TEXT.get(st, f"2D 模态明细状态 {st}，本次不出表。")
    if st == "kernel_error":
        return base + f" 内核代码：{(d or {}).get('error', '')}。"
    return base


def parse_modal_detail(txt: str,
                       frequencies: list[float] | None = None) -> dict[str, Any]:
    """解析 `[MODAL2D]` 段；只认内核真正写出的字段，缺项一律显式报错。

    `frequencies` 传入同一份 `.modal.txt` 由 `parse_modal_txt` 解析出的频率，
    用于禁止报告里出现两套频率；不传时不作对拍（离线单元测试用）。
    """
    out: dict[str, Any] = {
        "status": "no_detail_section",
        "basis": "", "nmodes": 0, "ndof": 0,
        "mass_x": None, "mass_y": None,
        "modes": [], "shapes": {},
        "kernel_note": "", "error": "", "source": DETAIL_SOURCE,
        "note": "",
    }
    if DETAIL_SECTION not in (txt or ""):
        out["note"] = detail_status_text(out)
        return out
    body = txt.split(DETAIL_SECTION, 1)[1].splitlines()
    lines: list[str] = []
    for raw in body:
        line = raw.strip()
        if line.startswith("=="):   # 后续新段，不属于明细段
            break
        if line:
            lines.append(line)
    for line in lines:
        if line.startswith("ERROR="):
            out["status"] = "kernel_error"
            out["error"] = line.split("=", 1)[1].strip()
            out["note"] = detail_status_text(out)
            return out
    hdr = next((l for l in lines if l.startswith("NMODES=")), None)
    if hdr is None or not _D_HDR_RE.match(hdr):
        out["status"] = "bad_header"
        out["note"] = detail_status_text(out)
        return out
    m = _D_HDR_RE.match(hdr)
    assert m is not None
    out["nmodes"], out["ndof"], out["basis"] = int(m.group(1)), int(m.group(2)), m.group(3)
    if out["basis"] != "xy":
        out["status"] = "bad_basis"
        out["note"] = detail_status_text(out)
        return out
    out["kernel_note"] = next(
        (l.split("=", 1)[1] for l in lines if l.startswith("NOTE=")), "")
    mass = next((l for l in lines if l.startswith("MASS ")), None)
    if mass is None or not _D_MASS_RE.match(mass):
        out["status"] = "missing_mass"
        out["note"] = detail_status_text(out)
        return out
    mm = _D_MASS_RE.match(mass)
    assert mm is not None
    out["mass_x"], out["mass_y"] = float(mm.group(1)), float(mm.group(2))

    frows: dict[int, dict[str, Any]] = {}
    for line in lines:
        fm = _D_F_RE.match(line)
        if fm:
            frows[int(fm.group(1))] = {
                "i": int(fm.group(1)), "f": float(fm.group(2)),
                "T": float(fm.group(3)), "mx": float(fm.group(4)),
                "my": float(fm.group(5)), "mnorm": float(fm.group(6)),
            }
        sm = _D_S_RE.match(line)
        if sm:
            i, node = int(sm.group(1)), int(sm.group(2))
            out["shapes"].setdefault(i, []).append(
                (node, float(sm.group(3)), float(sm.group(4)), float(sm.group(5))))
    out["modes"] = [frows[k] for k in sorted(frows)]
    if not out["modes"]:
        out["status"] = "no_modes"
    elif len(out["modes"]) != out["nmodes"]:
        out["status"] = "incomplete_mode_row"
    elif any(r["T"] <= 0.0 for r in out["modes"]):
        out["status"] = "invalid_period"
    elif not out["shapes"]:
        out["status"] = "missing_shapes"
    elif frequencies is not None:
        # 既有「== 自振频率 ==」段由 example_modal.exe 用 setprecision(6)
        # 打印，[MODAL2D] 段按 3D oracle 用 setprecision(10)。两者是同一份
        # double，只是打印精度不同（例如 128.199 与 128.1991627）。容差按
        # 6 位有效数字末位的一半取 5e-6，超过才判「两套频率」。
        # 绝不为对拍而降低明细段精度，也不由 Python 反算频率。
        ref = list(frequencies)
        got = [r["f"] for r in out["modes"]]
        same = len(ref) >= len(got) and all(
            abs(got[i] - ref[i]) <= 5e-6 * max(1.0, abs(ref[i]))
            for i in range(len(got)))
        out["status"] = "ok" if same else "inconsistent_f"
    else:
        out["status"] = "ok"
    out["note"] = detail_status_text(out)
    return out


def detail_table(d: dict[str, Any] | None) -> dict[str, Any] | None:
    """周期 / 两向有效参与质量表（唯一数据源）；状态非 ok 时返回 None。"""
    if (d or {}).get("status") != "ok":
        return None
    rows = [[str(r["i"]), f"{r['f']:.6g}", f"{r['T']:.6g}",
             f"{r['mx']:.6g}", f"{r['my']:.6g}", f"{r['mnorm']:.6g}"]
            for r in d["modes"]]
    if d.get("mass_x") is not None and d.get("mass_y") is not None:
        rows.append(["总质量", "—", "—",
                     f"{d['mass_x']:.6g}", f"{d['mass_y']:.6g}", "—"])
    return {"title": "2D 模态明细（周期 / 参与质量）",
            "headers": ["阶", "f (Hz)", "T (s)", "mx (kg)", "my (kg)", "φᵀMφ"],
            "rows": rows,
            "note": DETAIL_NOTE + " 来源：" + DETAIL_SOURCE + "。"}


def shape_table(d: dict[str, Any] | None) -> dict[str, Any] | None:
    """节点振型表：内核写出多少阶多少节点就列多少，不挑阶、不插值。"""
    if (d or {}).get("status") != "ok" or not d.get("shapes"):
        return None
    rows: list[list[str]] = []
    for i in sorted(d["shapes"]):
        for node, ux, uy, th in d["shapes"][i]:
            rows.append([str(i), str(node), f"{ux:.6g}", f"{uy:.6g}",
                         f"{th:.6g}"])
    return {"title": "2D 节点振型（全阶）",
            "headers": ["阶", "节点", "ux", "uy", "θz"],
            "rows": rows,
            "note": SHAPE_NOTE + " 来源：" + DETAIL_SOURCE + "。"}
