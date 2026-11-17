"""3D 成桥态无应力曲率明细的唯一 Python 口径层（B5 第二十六次，opt-in）。

**只解析、不重算。** 数据来源是 FEM-Eigen 的独立 opt-in 文件
``<stem>.curvature.txt``（``example_3d_report.exe --curvature-detail``）：:

    [UNSTRESSED_CURVATURE]
    BASIS=unstressed_curvature_fem_only
    NELEM=1
    1 K0Y=0 K0Z=0.001 EIY=34500000000 EIZ=34500000000 M0Y=0 M0Z=34500000

κ0（1/m）是**制造/设计给定**的无应力曲率，程序不反算；这里也绝不从位移或
内力反推 κ0，只把内核写出的数原样搬进结构化字典。``M0Y/M0Z = EI·κ0`` 只作
同一批数字的内部互核（相对容差 1e-9），不参与任何力学计算。

口径与 B4 的 ``[PRESTRESSLOSS2D]`` 独立段一致：段名固定、``BASIS`` 固定、
状��码显式中文、缺段/字段不符一律不出表。
"""
from __future__ import annotations

import re
from typing import Any

#: 段名（与 FEM-Eigen ``example_3d_report.cpp`` 的段头严格一致）
SECTION = "[UNSTRESSED_CURVATURE]"
#: 口径标签（FEM 自洽，不含制造参数、不做 ANSYS 对拍）
BASIS = "unstressed_curvature_fem_only"
#: 数据来源自述（计算书/报告直接引用，避免"来路不明"）
SOURCE = (
    "FEM-Eigen example_3d_report.exe --curvature-detail → <stem>.curvature.txt"
)

#: 每行必须齐全的键（缺一不出表，不补零）
ROW_KEYS = ("K0Y", "K0Z", "EIY", "EIZ", "M0Y", "M0Z")

_HDR_BASIS_RE = re.compile(r"^BASIS=(\S+)$")
_NELEM_RE = re.compile(r"^NELEM=(\d+)$")
_ERROR_RE = re.compile(r"^ERROR=(\S+)$")
_ROW_RE = re.compile(r"^(\d+)\s+(.*)$")
_KV_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(\S+)")

#: 状态码 → 中文说明
_STATUS_TEXT = {
    "ok": "已读出无应力曲率明细",
    "no_section": "无 [UNSTRESSED_CURVATURE] 段（未启用 --curvature-detail）",
    "bad_basis": "BASIS 与 FEM 自洽口径不符，拒绝出表",
    "no_curvature": "内核显式声明没有单元设置无应力曲率",
    "bad_nelem": "NELEM 缺失或非法",
    "nelem_mismatch": "NELEM 与数据行数不一致，拒绝出表",
    "bad_row": "存在无法解析的数据行，拒绝出表",
    "missing_key": "数据行缺必需字段，拒绝出表",
    "moment_mismatch": "M0 与 EI·κ0 互核不一致，拒绝出表",
    "not_requested": "未请求无应力曲率明细",
}


def status_text(d: dict[str, Any] | None) -> str:
    """把解析状态翻译成一句中文（报告/界面直接用）。"""
    if not d:
        return _STATUS_TEXT["not_requested"]
    return _STATUS_TEXT.get(d.get("status", ""), f"未知状态 {d.get('status')!r}")


def _close(a: float, b: float, rel: float = 1e-9) -> bool:
    return abs(a - b) <= rel * max(1.0, abs(a), abs(b))


def parse_curvature_txt(txt: str | None) -> dict[str, Any]:
    """解析 ``<stem>.curvature.txt``，返回结构化字典（只解析、不重算）。

    返回键：``status`` / ``nelem`` / ``rows`` / ``source`` / ``raw_error``。
    ``rows`` 以单元号为键，值为 ``{K0Y,K0Z,EIY,EIZ,M0Y,M0Z}``（float）。
    """
    out: dict[str, Any] = {
        "status": "no_section",
        "nelem": None,
        "rows": {},
        "source": SOURCE,
        "raw_error": None,
    }
    if not txt:
        return out

    lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
    if SECTION not in lines:
        return out

    basis: str | None = None
    nelem: int | None = None
    raw_error: str | None = None
    rows: dict[int, dict[str, float]] = {}
    bad_row = False
    missing_key = False

    for ln in lines:
        if ln.startswith("["):
            continue
        m = _HDR_BASIS_RE.match(ln)
        if m:
            basis = m.group(1)
            continue
        m = _NELEM_RE.match(ln)
        if m:
            nelem = int(m.group(1))
            continue
        m = _ERROR_RE.match(ln)
        if m:
            raw_error = m.group(1)
            continue
        m = _ROW_RE.match(ln)
        if not m:
            continue
        eid = int(m.group(1))
        kv = {k: float(v) for k, v in _KV_RE.findall(m.group(2))}
        if not kv:
            bad_row = True
            continue
        if any(k not in kv for k in ROW_KEYS):
            missing_key = True
            continue
        rows[eid] = {k: kv[k] for k in ROW_KEYS}

    out["nelem"] = nelem
    out["raw_error"] = raw_error
    out["rows"] = rows

    if basis is None or basis != BASIS:
        out["status"] = "bad_basis"
        return out
    if raw_error == "no_curvature_elements":
        out["status"] = "no_curvature"
        return out
    if bad_row:
        out["status"] = "bad_row"
        return out
    if missing_key:
        out["status"] = "missing_key"
        return out
    if nelem is None or nelem < 0:
        out["status"] = "bad_nelem"
        return out
    if nelem != len(rows):
        out["status"] = "nelem_mismatch"
        return out
    # M0 = EI·κ0 互核：只作同批数字自我一致性检查，不重算力学
    for kv in rows.values():
        if not _close(kv["M0Y"], kv["EIY"] * kv["K0Y"]):
            out["status"] = "moment_mismatch"
            return out
        if not _close(kv["M0Z"], kv["EIZ"] * kv["K0Z"]):
            out["status"] = "moment_mismatch"
            return out
    out["status"] = "ok"
    return out


def curvature_table(d: dict[str, Any] | None) -> dict[str, Any] | None:
    """把解析结果转成报告可直接渲染的表；状态非 ``ok`` 一律返回 ``None``。"""
    if not d or d.get("status") != "ok":
        return None
    rows = []
    for eid in sorted(d.get("rows", {})):
        kv = d["rows"][eid]
        rows.append({
            "elem": eid,
            "k0y": kv["K0Y"],
            "k0z": kv["K0Z"],
            "eiy": kv["EIY"],
            "eiz": kv["EIZ"],
            "m0y": kv["M0Y"],
            "m0z": kv["M0Z"],
        })
    return {
        "title": "成桥态无应力曲率（FEM 自洽口径）",
        "basis": BASIS,
        "source": SOURCE,
        "nelem": d.get("nelem"),
        "rows": rows,
        "note": "κ0 为设计/制造给定参数，程序不反算；本表不含真实制造参数与 ANSYS 对拍",
    }
