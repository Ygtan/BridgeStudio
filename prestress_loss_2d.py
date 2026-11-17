"""prestress_loss_2d.py —— 2D 分项预应力损失明细的唯一口径层。

只读 FEM-Eigen `example_mixed.exe --loss-detail-only` 写出的 `<stem>.loss.txt`
里的 `[PRESTRESSLOSS2D]` 段，产出表字典与中文状态。铁律：

* **不重算**：σl1~σl6 / σpe / RATIO 全部来自内核段。Python 不调用
  `computePrestressLosses`、不反算 σpe、不拿总损失系数凑明细，不形成第二套口径。
* **不补零**：缺段 / `ERROR=` / 字段缺失 / 行数不符 / RATIO 与段内 SPE、
  SIGMA_CON 不一致，一律给显式中文状态，明细表返回 `None`，不摆空表。
  RATIO 一致性只是**读同一批数字互相核对**，不是新算一遍力学。
* **2D 语义边界**：本文件前半是**非阶段**段 `BASIS=2d_global`，那部分束不属于
  任何施工阶段，行首只用 `P<id>`（输入行首的显式束号），不写 `STAGE` 占位、
  不造阶段号；阶段维度在文件后半（第二十五次 `2d_stage` 损失段、第二十七次
  `2d_stage` 有效预加力段），两种口径不同文件、互不混行。2D 仍没有三向空间量。
  σl3 / σl5 / σl6 是后期损失，不进 σpe 与 Pe；σl2 默认按直线筋，
  opt-in L2ANTI=1 时按附录 G 反摩擦、代表值 x=0（张拉端保守）；
  σl6 默认由 εcs / φ 用户直给，程序不按龄期自动发展；opt-in C2D=1 时用
  附录 C 显式表值龄期（表值由用户核对录入、程序不查表不拟合兜底），
  C_SKIP=1 显式声明不参与。本模块只解析、不现算这些发展公式。
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
    "σl2 默认按直线筋 ΣΔl/l·Ep；opt-in L2ANTI=1 按附录 G 反摩擦、代表值 x=0"
    "（张拉端保守），段内 LF/DSL2D/DSL2PEAK/BRANCH 为反摩擦中间量，本表仍只列九项。"
    "σl6 默认由 εcs / φ 用户直给、程序不按龄期自动发展；opt-in C2D=1 时用附录 C"
    "显式表值龄期（表值由用户核对录入、程序不查表不拟合兜底，段内 "
    "C_SOURCE/ECS_DEV/PHI_DEV/BETAS/BETAS0/BETAC/BETAH/RHREP 为中间量），"
    "C_SKIP=1 显式声明不参与。本表仍只列九项。"
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


# ---------------------------------------------------------------------------
# 第二十五次：2D **阶段**分项预应力损失（opt-in）。
# 与上面的非阶段口径严格隔离：
#   * 不同输出文件（<stem>.stage.loss.txt），段名同 [PRESTRESSLOSS2D]；
#   * 头行 BASIS=2d_stage，行首 STAGE<n> P<id>（组织键 (阶段号, 束号)、
#     字段顺序 / 单位 / 精度与 3D 阶段段一致）；
#   * parse_loss_txt() / loss_table() 一字不改，仍只认 2d_global + P<id>。
# 铁律不变：只解析、不重算、不补零；RATIO 只作同批数字互核。
# ---------------------------------------------------------------------------

STAGE_BASIS = "2d_stage"
STAGE_SOURCE = (
    "FEM-Eigen example_stage.exe --stage-loss-detail → <stem>.stage.loss.txt")
_STAGE_HDR_RE = re.compile(r"^NPRESTRESS=(\d+)\s+BASIS=(\S+)$")
_STAGE_ROW_RE = re.compile(r"^STAGE(\d+)\s+P(\d+)\s+(.*)$")

STAGE_NOTE = (
    "σl1 摩擦（6.2.2）、σl2 锚具变形/回缩（6.2.3）、σl3 温差（6.2.4）、"
    "σl4 弹性压缩（6.2.5）、σl5 松弛（6.2.6）、σl6 收缩徐变（6.2.7），单位 MPa。"
    "这是 2D 阶段 *STAGE_PRESTRESS LOSSREF 束的明细，组织键 (阶段号, 束号) "
    "与 3D 一致；σl2/σl6 opt-in 选项经同一张 *PRESTRESS_LOSS 表映射。"
    "σpe 后张=σcon−σl1−σl2−σl4、先张=σcon−σl2；σl3/σl5/σl6 是后期损失，"
    "不进 σpe、也不进等效荷载 Pe=P·(σpe/σcon)。本表只解析、不现算。"
)

STAGE_STATUS_TEXT: dict[str, str] = {
    "stage_not_requested": "本次未请求 2D 阶段分项损失明细（默认关闭），不出表、不补零。",
    "stage_unavailable": "未编译 example_stage.exe；本次不做 2D 阶段分项损失明细。",
    "stage_no_model": "模型没有引用 *PRESTRESS_LOSS 表的 2D 阶段预应力束；内核没有算过阶段分项，不拿零值冒充各项损失为 0。",
    "stage_no_output": "内核未写出 <stem>.stage.loss.txt；本次不做阶段分项损失明细，不补零。",
    "stage_no_section": "结果文件里没有 [PRESTRESSLOSS2D] 段；本次不做阶段分项损失明细，不补零。",
    "stage_kernel_error": "内核明确写出 ERROR=，本次没有阶段分项损失明细，不摆空表。",
    "stage_bad_header": "阶段段缺 NPRESTRESS / BASIS 头行，解析拒绝，不摆空表。",
    "stage_bad_basis": "阶段段的 BASIS 不是 2d_stage，口径不匹配，解析拒绝。",
    "stage_bad_row": "阶段段有缺字段、非数值或无法识别的行，解析拒绝，不摆空表。",
    "stage_row_count_mismatch": "阶段段明细行数与 NPRESTRESS 不符，解析拒绝。",
    "stage_inconsistent_ratio": "阶段段 RATIO 与同段 SPE/SIGMA_CON 不一致，解析拒绝，不拿另一套比值凑表。",
    "stage_solver_error": "2D 阶段分项损失内核调用失败；本次不做明细，不补零。",
    "stage_ok": "2D 阶段分项预应力损失明细：{n} 束·阶段，来自内核 BASIS=2d_stage 段；只解析、不重算。",
}


def stage_status_text(d: dict[str, Any] | None) -> str:
    d = d or {}
    st = str(d.get("status") or "stage_not_requested")
    text = STAGE_STATUS_TEXT.get(st, "2D 阶段分项损失明细状态未知，不给表、不补零。")
    if st == "stage_ok":
        return text.format(n=len(d.get("rows") or []))
    reason = d.get("reason") or d.get("stdout")
    if reason and st in ("stage_kernel_error", "stage_solver_error",
                         "stage_bad_row", "stage_no_output"):
        return text + " 内核信息：" + str(reason)[-300:]
    return text


def parse_stage_loss_txt(txt: str | None) -> dict[str, Any]:
    """解析阶段段 `[PRESTRESSLOSS2D]` / `BASIS=2d_stage`。

    成功时 ``{"status": "stage_ok", "np": n, "basis": "2d_stage",
    "note": str, "rows": [{"stage": int, "id": int, SIGMA_CON..."RATIO"},
    ...], "source": STAGE_SOURCE}``。任何异常都给显式 status，绝不返回半张表。
    """
    out: dict[str, Any] = {
        "status": "stage_no_section", "source": STAGE_SOURCE,
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
            break
        if s.startswith("ERROR="):
            out["status"] = "stage_kernel_error"
            out["reason"] = s[len("ERROR="):]
            if "no_lossref_stage_prestress" in s:
                out["status"] = "stage_no_model"
            return out
        if s.startswith("NOTE="):
            note = s[len("NOTE="):]
            continue
        m = _STAGE_HDR_RE.match(s)
        if m:
            ndecl, basis = int(m.group(1)), m.group(2)
            continue
        m = _STAGE_ROW_RE.match(s)
        if m:
            stage_no, pid = int(m.group(1)), int(m.group(2))
            kv = dict(_KV_RE.findall(m.group(3)))
            if not all(f in kv for f in FIELDS):
                out["status"] = "stage_bad_row"
                out["reason"] = f"STAGE{stage_no} P{pid} 缺字段"
                return out
            try:
                vals: dict[str, Any] = {"stage": stage_no, "id": pid}
                for f in FIELDS:
                    vals[f] = float(kv[f])
            except (ValueError, TypeError):
                out["status"] = "stage_bad_row"
                out["reason"] = f"STAGE{stage_no} P{pid} 有非数值字段"
                return out
            rows.append(vals)
            continue
        # 旧全局格式的行不得混进阶段段
        out["status"] = "stage_bad_row"
        out["reason"] = s[:120]
        return out

    if ndecl is None:
        out["status"] = "stage_bad_header"
        return out
    out["np"], out["basis"], out["note"] = ndecl, basis, note
    if basis != STAGE_BASIS:
        out["status"] = "stage_bad_basis"
        return out
    if len(rows) != ndecl or not rows:
        out["status"] = "stage_row_count_mismatch"
        return out
    for r in rows:
        sc, spe, ratio = r["SIGMA_CON"], r["SPE"], r["RATIO"]
        want = spe / sc if sc > 0.0 else 0.0
        if abs(ratio - want) > 5e-9 * max(1.0, abs(ratio)):
            out["status"] = "stage_inconsistent_ratio"
            out["reason"] = (
                f"STAGE{r['stage']} P{r['id']} RATIO={ratio} 与 "
                f"SPE/SIGMA_CON={want} 不符")
            return out
    out["status"] = "stage_ok"
    out["rows"] = rows
    return out


def stage_loss_table(d: dict[str, Any] | None) -> dict[str, Any] | None:
    """阶段明细表 `title/headers/rows/note`；状态非 stage_ok 时返回 None。"""
    if not d or d.get("status") != "stage_ok" or not d.get("rows"):
        return None
    rows: list[list[str]] = []
    for r in d["rows"]:
        rows.append([
            str(r["stage"]), str(r["id"]),
            f'{r["SIGMA_CON"] / 1e6:.2f}',
            *[f'{r[f"SL{i}"] / 1e6:.2f}' for i in range(1, 7)],
            f'{r["SPE"] / 1e6:.2f}',
            f'{r["RATIO"]:.4f}',
        ])
    return {
        "title": "2D 阶段分项预应力损失（JTG 3362-2018 §6.2）",
        "headers": ["阶段", "束号", "σcon (MPa)", "σl1", "σl2", "σl3",
                    "σl4", "σl5", "σl6", "σpe (MPa)", "σpe/σcon"],
        "rows": rows,
        "note": STAGE_NOTE + " 来源：" + STAGE_SOURCE + "。",
    }


# ---------------------------------------------------------------------------
# 第二十七次：2D **阶段有效预加力**（opt-in）。
#
# 补的是 3D 早已有的 `stage prestress force` 段（STAGE<n> P<id> Pe=）在 2D 侧
# 缺失的**输出能力**——力学上 2D 一直在算 Pe=P·loss，只是没写进结果段。
#   * 独立文件 <stem>.stage.force.txt、段名 [PRESTRESSFORCE2D]；
#   * 头行 BASIS=2d_stage，行首 STAGE<n> P<id>，组织键与 3D 一致；
#   * 2D 的 PE= 即 3D 的 Pe=，同参同值；
#   * 与阶段损失段（[PRESTRESSLOSS2D]）不同文件、互不混行。
# 铁律不变：只解析、不重算、不补零。PE=P·LOSS 的一致性核对只是**读同一批
# 数字互核**，不是新算一遍力学；SIGMA_CON=0 的束不拿 P/Ap 反造控制应力。
# ---------------------------------------------------------------------------

FORCE_SECTION = "[PRESTRESSFORCE2D]"
FORCE_BASIS = "2d_stage"
FORCE_SOURCE = (
    "FEM-Eigen example_stage.exe --stage-prestress-force → <stem>.stage.force.txt")
FORCE_FIELDS = ("SIGMA_CON", "P", "LOSS", "PE")

FORCE_NOTE = (
    "这是 2D 施工阶段每束的有效预加力明细（P 是张拉控制力、SIGMA_CON 是张拉"
    "控制应力、LOSS 是有效预应力系数、PE=P·LOSS 是有效预加力）。组织键 "
    "(阶段号, 束号) 与 3D `stage prestress force` 段一致，2D 的 PE= 即 3D 的 "
    "Pe=。只给 LOSS= 的束未登记控制应力，SIGMA_CON 记 0，不拿 P/Ap 反造一个。"
    "本表只解析、不重算。分项损失的来龙去脉见 2D 阶段分项预应力损失章节，"
    "两段不同文件、互不混行。"
)

FORCE_STATUS_TEXT: dict[str, str] = {
    "force_not_requested": "本次未请求 2D 阶段有效预加力明细（默认关闭），不出表、不补零。",
    "force_unavailable": "未编译 example_stage.exe；本次不做 2D 阶段有效预加力明细。",
    "force_no_model": "模型施工阶段没有 *STAGE_PRESTRESS 束；没有张拉的束就没有有效预加力，不拿零值冒充已张拉。",
    "force_no_output": "内核未写出 <stem>.stage.force.txt；本次不做阶段有效预加力明细，不补零。",
    "force_no_section": "结果文件里没有 [PRESTRESSFORCE2D] 段；本次不做阶段有效预加力明细。",
    "force_kernel_error": "内核明确写出 ERROR=，本次没有阶段有效预加力明细，不摆空表。",
    "force_bad_header": "有效预加力段缺 NPRESTRESS / BASIS 头行，解析拒绝，不摆空表。",
    "force_bad_basis": "有效预加力段的 BASIS 不是 2d_stage，口径不匹配，解析拒绝。",
    "force_bad_row": "有效预加力段有缺字段、非数值或无法识别的行，解析拒绝，不摆空表。",
    "force_row_count_mismatch": "有效预加力段明细行数与 NPRESTRESS 不符，解析拒绝。",
    "force_inconsistent_pe": "有效预加力段的 PE 与同段 P·LOSS 不一致，解析拒绝，不拿另一套数凑表。",
    "force_solver_error": "2D 阶段有效预加力内核调用失败；本次不做明细，不补零。",
    "force_ok": "2D 阶段有效预加力明细：{n} 束·阶段，来自内核 BASIS=2d_stage 段；只解析、不重算。",
}


def force_status_text(d: dict[str, Any] | None) -> str:
    d = d or {}
    st = str(d.get("status") or "force_not_requested")
    text = FORCE_STATUS_TEXT.get(st, "2D 阶段有效预加力明细状态未知，不给表、不补零。")
    if st == "force_ok":
        return text.format(n=len(d.get("rows") or []))
    reason = d.get("reason") or d.get("stdout")
    if reason and st in ("force_kernel_error", "force_solver_error",
                         "force_bad_row", "force_no_output"):
        return text + " 内核信息：" + str(reason)[-300:]
    return text


def parse_stage_force_txt(txt: str | None) -> dict[str, Any]:
    """解析 `[PRESTRESSFORCE2D]` / `BASIS=2d_stage` 段。

    成功时 ``{"status": "force_ok", "np": n, "basis": "2d_stage",
    "note": str, "rows": [{"stage": int, "id": int, "SIGMA_CON": f,
    "P": f, "LOSS": f, "PE": f}, ...], "source": FORCE_SOURCE}``。
    任何异常都给显式 status，绝不返回半张表。
    """
    out: dict[str, Any] = {
        "status": "force_no_section", "source": FORCE_SOURCE,
        "np": 0, "basis": "", "note": "", "rows": [],
    }
    if not txt or FORCE_SECTION not in txt:
        return out
    lines = txt.splitlines()
    try:
        start = next(i for i, s in enumerate(lines) if s.strip() == FORCE_SECTION)
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
            break
        if s.startswith("ERROR="):
            out["status"] = "force_kernel_error"
            out["reason"] = s[len("ERROR="):]
            if "no_stage_prestress" in s:
                out["status"] = "force_no_model"
            return out
        if s.startswith("NOTE="):
            note = s[len("NOTE="):]
            continue
        m = _STAGE_HDR_RE.match(s)
        if m:
            ndecl, basis = int(m.group(1)), m.group(2)
            continue
        m = _STAGE_ROW_RE.match(s)
        if m:
            stage_no, pid = int(m.group(1)), int(m.group(2))
            kv = dict(_KV_RE.findall(m.group(3)))
            if not all(f in kv for f in FORCE_FIELDS):
                out["status"] = "force_bad_row"
                out["reason"] = f"STAGE{stage_no} P{pid} 缺字段"
                return out
            try:
                vals: dict[str, Any] = {"stage": stage_no, "id": pid}
                for f in FORCE_FIELDS:
                    vals[f] = float(kv[f])
            except (ValueError, TypeError):
                out["status"] = "force_bad_row"
                out["reason"] = f"STAGE{stage_no} P{pid} 有非数值字段"
                return out
            rows.append(vals)
            continue
        # 损失段的字段（SL1..SL6/SPE/RATIO）不得混进有效预加力段
        out["status"] = "force_bad_row"
        out["reason"] = s[:120]
        return out

    if ndecl is None:
        out["status"] = "force_bad_header"
        return out
    out["np"], out["basis"], out["note"] = ndecl, basis, note
    if basis != FORCE_BASIS:
        out["status"] = "force_bad_basis"
        return out
    if len(rows) != ndecl or not rows:
        out["status"] = "force_row_count_mismatch"
        return out
    for r in rows:
        want = r["P"] * r["LOSS"]
        if abs(r["PE"] - want) > 5e-9 * max(1.0, abs(want)):
            out["status"] = "force_inconsistent_pe"
            out["reason"] = (
                f"STAGE{r['stage']} P{r['id']} PE={r['PE']} 与 P·LOSS={want} 不符")
            return out
    out["status"] = "force_ok"
    out["rows"] = rows
    return out


def stage_force_table(d: dict[str, Any] | None) -> dict[str, Any] | None:
    """有效预加力表 `title/headers/rows/note`；状态非 force_ok 时返回 None。"""
    if not d or d.get("status") != "force_ok" or not d.get("rows"):
        return None
    rows: list[list[str]] = []
    for r in d["rows"]:
        rows.append([
            str(r["stage"]), str(r["id"]),
            f'{r["SIGMA_CON"] / 1e6:.2f}',
            f'{r["P"] / 1e3:.2f}',
            f'{r["LOSS"]:.4f}',
            f'{r["PE"] / 1e3:.2f}',
        ])
    return {
        "title": "2D 阶段有效预加力（Pe = P·loss）",
        "headers": ["阶段", "束号", "σcon (MPa)", "P (kN)", "有效系数", "Pe (kN)"],
        "rows": rows,
        "note": FORCE_NOTE + " 来源：" + FORCE_SOURCE + "。",
    }
