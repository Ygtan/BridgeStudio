"""prestress_loss_2d.py —— 2D 分项预应力损失明细的唯一口径层。

只读 FEM-Eigen `example_mixed.exe --loss-detail-only` 写出的 `<stem>.loss.txt`
里的 `[PRESTRESSLOSS2D]` 段，产出表字典与中文状态。铁律：

* **不重算**：σl1~σl6 / σpe / RATIO 全部来自内核段。Python 不调用
  `computePrestressLosses`、不反算 σpe、不拿总损失系数凑明细，不形成第二套口径。
* **不补零**：缺段 / `ERROR=` / 字段缺失 / 行数不符 / RATIO 与段内 SPE、
  SIGMA_CON 不一致，一律给显式中文状态，明细表返回 `None`，不摆空表。
  RATIO 一致性只是**读同一批数字互相核对**，不是新算一遍力学。
* **2D 语义边界**：段是 `BASIS=2d_global`，2D 没有阶段维度，不写 `STAGE`
  占位、不造阶段号，`P<id>` 是输入行首的显式束号；与 3D 的
  `stage prestress loss` 段字段同源但组织键不同，不等于 3D 阶段分项损失。
  σl3 / σl5 / σl6 是后期损失，不进 σpe 与 Pe；σl2 只按直线筋；
  σl6 的 εcs / φ 需按附录 C 查表输入，程序不按龄期自动发展。
"""

from __future__ import annotations

import re
from typing import Any

SECTION = "[PRESTRESSLOSS2D]"
BASIS = "2d_global"
SOURCE = "FEM-Eigen example_mixed.exe --loss-detail-only → <stem>.loss.txt"
FIELDS = ("SIGMA_CON", "SL1", "SL2", "SL3", "SL4", "SL5", "SL6", "SPE", "RATIO")

NOTE = (
    "σl1 摩擦（6.2.2）、σl2 锚具变形/回缩（6.2.3）、σl3 温差（6.2.4）、"
    "σl4 弹性压缩（6.2.5）、σl5 松弛（6.2.6）、σl6 收缩徐变（6.2.7），单位 MPa。"
    "σpe 后张=σcon−σl1−σl2−σl4、先张=σcon−σl2；"
    "σl3/σl5/σl6 是后期损失，不进 σpe、也不进等效荷载 Pe=P·(σpe/σcon)。"
    "σl2 只按直线筋 ΣΔl/l·Ep（曲线筋反摩擦未实现）；"
    "εcs 与 φ 需按附录 C 查表输入，程序不按龄期自动发展。"
    "这是 2D 非阶段束的明细，没有 3D 的阶段维度与三向空间量；"
    "分项损失是复核明细，不是规范合格判定。"
)

_HDR_RE = re.compile(r"^NPRESTRESS=(\d+)\s+BASIS=(\S+)$")
_ROW_RE = re.compile(r"^P(\d+)\s+(.*)$")
_KV_RE = re.compile(r"([A-Z][A-Z0-9_]*)=([^\s]+)")

STATUS_TEXT: dict[str, str] = {
    "not_requested": "本次未请求 2D 分项预应力损失明细（默认关闭），不出表、不补零。",
    "unavailable": "未编译 example_mixed.exe；本次不做 2D 分项损失明细，请重新编译 FEM-Eigen。",
    "no_model": "模型没有引用 *PRESTRESS_LOSS 表的 2D 非阶段预应力束；内核没有算过分项，本次不做明细，不拿零值冒充各项损失为 0。",
    "no_output": "内核未写出 <stem>.loss.txt；本次不做 2D 分项损失明细，不补零。",
    "no_section": "结果文件里没有 [PRESTRESSLOSS2D] 段；本次不做 2D 分项损失明细，不补零。",
    "kernel_error": "内核明确写出 ERROR=，本次没有分项损失明细，不摆空表。",
    "bad_header": "[PRESTRESSLOSS2D] 段缺 NPRESTRESS / BASIS 头行，解析拒绝，不摆空表。",
    "bad_basis": "[PRESTRESSLOSS2D] 段的 BASIS 不是 2d_global，口径不匹配，解析拒绝。",
    "bad_row": "[PRESTRESSLOSS2D] 段有缺字段、非数值或无法识别的行，解析拒绝，不摆空表。",
    "row_count_mismatch": "[PRESTRESSLOSS2D] 段的明细行数与 NPRESTRESS 不符，解析拒绝。",
    "inconsistent_ratio": "[PRESTRESSLOSS2D] 段的 RATIO 与同段 SPE/SIGMA_CON 不一致，解析拒绝，不拿另一套比值凑表。",
    "solver_error": "2D 分项损失明细内核调用失败；本次不做明细，不补零。",
    "ok": "2D 分项预应力损失明细：{n} 束，来自内核 [PRESTRESSLOSS2D] 段；只解析、不重算。",
}


def status_text(d: dict[str, Any] | None) -> str:
    """把状态翻成一句中文；异常状态附内核给的理由。"""
    d = d or {}
    st = str(d.get("status") or "not_requested")
    text = STATUS_TEXT.get(st, "2D 分项预应力损失明细状态未知，不给表、不补零。")
    if st == "ok":
        return text.format(n=len(d.get("rows") or []))
    reason = d.get("reason") or d.get("stdout")
    if reason and st in ("kernel_error", "solver_error", "bad_row", "no_output"):
        return text + " 内核信息：" + str(reason)[-300:]
    return text


def parse_loss_txt(txt: str | None) -> dict[str, Any]:
    """解析 `[PRESTRESSLOSS2D]` 段；任何异常都给显式 status，绝不返回半张表。

    成功时 ``{"status": "ok", "np": n, "basis": BASIS, "note": str,
    "rows": [{"id": int, "SIGMA_CON": float, "SL1"..."SL6": float,
    "SPE": float, "RATIO": float}, ...], "source": SOURCE}``。
    """
    out: dict[str, Any] = {
        "status": "no_section", "source": SOURCE,
        "np": 0, "basis": "", "note": "", "rows": [],
    }
    if not txt or SECTION not in txt:
        return out
    lines = txt.splitlines()
    try:
        start = next(i for i, s in enumerate(lines) if s.strip() == SECTION)
    except StopIteration:
        return out
    ndecl: int | None = None
    basis = ""
    rows: list[dict[str, Any]] = []
    note = ""
    for raw in lines[start + 1:]:
        s = raw.strip()
        if not s:
            continue
        if s.startswith("["):
            break  # 下一段开始，不把别人的行收进来
        if s.startswith("ERROR="):
            out["status"] = "kernel_error"
            out["reason"] = s[len("ERROR="):]
            return out
        if s.startswith("NOTE="):
            note = s[len("NOTE="):]
            continue
        m = _HDR_RE.match(s)
        if m:
            ndecl, basis = int(m.group(1)), m.group(2)
            continue
        m = _ROW_RE.match(s)
        if m:
            pid = int(m.group(1))
            kv = dict(_KV_RE.findall(m.group(2)))
            if not all(f in kv for f in FIELDS):
                out["status"] = "bad_row"
                out["reason"] = f"P{pid} 缺字段"
                return out
            try:
                vals: dict[str, Any] = {"id": pid}
                for f in FIELDS:
                    vals[f] = float(kv[f])
            except (ValueError, TypeError):
                out["status"] = "bad_row"
                out["reason"] = f"P{pid} 有非数值字段"
                return out
            rows.append(vals)
            continue
        out["status"] = "bad_row"
        out["reason"] = s[:120]
        return out

    if ndecl is None:
        out["status"] = "bad_header"
        return out
    out["np"], out["basis"], out["note"] = ndecl, basis, note
    if basis != BASIS:
        out["status"] = "bad_basis"
        return out
    if len(rows) != ndecl or not rows:
        out["status"] = "row_count_mismatch"
        return out
    for r in rows:
        sc, spe, ratio = r["SIGMA_CON"], r["SPE"], r["RATIO"]
        want = spe / sc if sc > 0.0 else 0.0
        if abs(ratio - want) > 5e-9 * max(1.0, abs(ratio)):
            out["status"] = "inconsistent_ratio"
            out["reason"] = f"P{r['id']} RATIO={ratio} 与 SPE/SIGMA_CON={want} 不符"
            return out
    out["status"] = "ok"
    out["rows"] = rows
    return out


def loss_table(d: dict[str, Any] | None) -> dict[str, Any] | None:
    """明细表字典 `title/headers/rows/note`；状态非 ok 时返回 None，不摆空表。"""
    if not d or d.get("status") != "ok" or not d.get("rows"):
        return None
    rows: list[list[str]] = []
    for r in d["rows"]:
        rows.append([
            str(r["id"]),
            f'{r["SIGMA_CON"] / 1e6:.2f}',
            *[f'{r[f"SL{i}"] / 1e6:.2f}' for i in range(1, 7)],
            f'{r["SPE"] / 1e6:.2f}',
            f'{r["RATIO"]:.4f}',
        ])
    return {
        "title": "2D 分项预应力损失（JTG 3362-2018 §6.2）",
        "headers": ["束号", "σcon (MPa)", "σl1", "σl2", "σl3", "σl4",
                    "σl5", "σl6", "σpe (MPa)", "σpe/σcon"],
        "rows": rows,
        "note": NOTE + " 来源：" + SOURCE + "。",
    }
