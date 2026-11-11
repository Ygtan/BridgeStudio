"""newton_2d.py —— 2D 内层 Newton 逐迭代过程量的唯一口径层。

只读 FEM-Eigen `example_mixed.exe --newton-detail-only` 写出的
`<stem>.newton.txt` 里的 `[NEWTON2D]` 段，产出表字典与中文状态。铁律：

* **不重算**：荷载步 / 迭代号 / λ / |R| / |Δu| / |Fext| / α / 线搜索折半 /
  步收敛标志全部来自内核段。Python 不解 K·ΔU=R、不反推残差、不由位移凑
  收敛过程，不形成第二套口径。
* **不补零**：缺段 / `ERROR=` / 头行缺字段 / BASIS 不符 / S 行列数或数值不对 /
  行数与 NITER 不符 / 段内数字自相矛盾，一律给显式中文状态，明细表返回
  `None`，不摆空表。段内一致性只是**读同一批数字互相核对**，不是重算力学。
* **2D 语义边界**：段是 `BASIS=2d_global`，字段名与单位同 3D `[NEWTON]`，
  但 2D 用直接求解、没有符号分解重建，故不写 `REFACT`；只有一个 tol 同时
  管残差与位移增量判据，故写 `TOL` 不拆 `TOLF`/`TOLD`；2D 主算例没有阶段
  维度，不设阶段占位、不造阶段号。**第十三次起 `[LENGTH2D]` 平面索长闭合
  一层已由独立 opt-in 口径层 `length_2d.py` 接入（无垂度线性杆，只标 `P`
  不标 `Y`），`[OPT]` 带约束索力优化在 2D 内核仍没有对应力学；本段只覆盖
  内层 Newton，不等于 3D 的三层迭代过程量已移植。**
* **过程量不构成规范判定**：规范没有 Newton 迭代轮数的合格限值，收敛快只说明
  数值如何收敛，不是设计合格判定。
"""

from __future__ import annotations

import re
from typing import Any

SECTION = "[NEWTON2D]"
BASIS = "2d_global"
SOURCE = "FEM-Eigen example_mixed.exe --newton-detail-only → <stem>.newton.txt"

HEAD_KEYS = (
    "NSTEP", "NITER", "MAXITER", "TOL", "CONVERGED",
    "RESFIRST", "RESLAST", "LINECUTS", "STEPREFINE", "LOADRATIO",
)
#: S <loadStep> <iter> <lambda> <|R|> <|du|> <|Fext|> <alpha> <lineCuts> <converged>
STEP_COLUMNS = (
    "load_step", "iter", "lambda", "residual", "du", "ref",
    "alpha", "line_cuts", "converged",
)

NOTE = (
    "2D 方案C 几何非线性 Newton 逐迭代留痕，字段名与单位同 3D [NEWTON] 段"
    "（|R|、|Fext| 为 N，|Δu| 为 m，λ 与 α 无量纲，10 位有效数字）。"
    "2D 用直接求解、没有符号分解重建，故不写 REFACT；只有一个 tol 同时管残差"
    "与位移增量判据，故写 TOL 不拆 TOLF/TOLD；没有阶段维度，不设阶段占位。"
    "|Δu|=0 表示该次迭代残差已达标、未求解增量，不是补零。"
    "第十三次起 [LENGTH2D] 平面索长闭合一层由独立 opt-in 章接入（无垂度"
    "线性杆，只标 P 不标 Y），[OPT] 带约束索力优化在 2D 内核仍没有对应力学；"
    "本表只覆盖内层 Newton，不等于 3D 三层迭代过程量已移植。"
    "过程量只说明数值如何收敛，规范没有 Newton 迭代轮数的合格限值，"
    "不是规范意义上的设计合格判定。"
)

_KIND_RE = re.compile(r"^KIND=(\S+)\s+STATUS=(\S+)$")
_BASIS_RE = re.compile(r"^BASIS=(\S+)$")
_KV_RE = re.compile(r"([A-Z][A-Z0-9_]*)=([^\s]+)")

STATUS_TEXT: dict[str, str] = {
    "not_requested": "本次未请求 2D 内层 Newton 过程量（默认关闭），不出表、不补零。",
    "unavailable": "未编译 example_mixed.exe；本次不做 2D Newton 过程量，请重新编译 FEM-Eigen。",
    "no_nonlinear_model": "模型不是 NONLINEAR 分析（*ANALYSIS TYPE=NONLINEAR）；内核没有 Newton 迭代，本次不做过程量，也不强制把分析类型改成非线性。",
    "no_output": "内核未写出 <stem>.newton.txt；本次不做 2D Newton 过程量，不补零。",
    "no_section": "结果文件里没有 [NEWTON2D] 段；本次不做 2D Newton 过程量，不补零。",
    "kernel_error": "内核明确写出 ERROR=，本次没有 Newton 过程量，不摆空表。",
    "bad_header": "[NEWTON2D] 段缺 KIND/STATUS/BASIS 或头部键不全，解析拒绝，不摆空表。",
    "bad_basis": "[NEWTON2D] 段的 BASIS 不是 2d_global，口径不匹配，解析拒绝。",
    "bad_row": "[NEWTON2D] 段有列数不符、非数值或无法识别的行，解析拒绝，不摆空表。",
    "row_count_mismatch": "[NEWTON2D] 段的 S 行数与 NITER 不符，解析拒绝。",
    "no_iteration": "[NEWTON2D] 段声明 NITER=0；内核没有产生 Newton 迭代，不摆空表。",
    "inconsistent_trace": "[NEWTON2D] 段内数字自相矛盾（线搜索折半合计 / 荷载步序号 / 收敛标志 / λ 与 LOADRATIO），解析拒绝，不拿另一套数凑表。",
    "solver_error": "2D Newton 过程量内核调用失败；本次不做过程量，不补零。",
    "ok": "2D 内层 Newton 过程量：{n} 步 / {ns} 个荷载步，来自内核 [NEWTON2D] 段；只解析、不重算。",
}


def status_text(d: dict[str, Any] | None) -> str:
    """把状态翻成一句中文；异常状态附内核给的理由。"""
    d = d or {}
    st = str(d.get("status") or "not_requested")
    text = STATUS_TEXT.get(st, "2D Newton 过程量状态未知，不给表、不补零。")
    if st == "ok":
        return text.format(n=len(d.get("steps") or []),
                           ns=d.get("nstep", 0))
    reason = d.get("reason") or d.get("stdout")
    if reason and st in ("kernel_error", "solver_error", "bad_row", "no_output",
                         "inconsistent_trace", "no_iteration"):
        return text + " 内核信息：" + str(reason)[-300:]
    return text


def parse_newton_txt(txt: str | None) -> dict[str, Any]:
    """解析 `[NEWTON2D]` 段；任何异常都给显式 status，绝不返回半张表。

    成功时 ``{"status": "ok", "basis": BASIS, "kind": str, "status_seg": str,
    "head": {...HEAD_KEYS}, "steps": [dict(STEP_COLUMNS)], "note": str,
    "failreason": str, "source": SOURCE}``。
    """
    out: dict[str, Any] = {
        "status": "no_section", "source": SOURCE,
        "basis": "", "kind": "", "status_seg": "",
        "head": {}, "steps": [], "note": "", "failreason": "",
    }
    if not txt or SECTION not in txt:
        return out
    lines = txt.splitlines()
    try:
        start = next(i for i, s in enumerate(lines) if s.strip() == SECTION)
    except StopIteration:
        return out

    basis = ""
    kind = ""
    seg_status = ""
    head: dict[str, Any] = {}
    steps: list[dict[str, Any]] = []
    note = ""
    failreason = ""
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
        if s.startswith("FAILREASON="):
            failreason = s[len("FAILREASON="):]
            continue
        m = _BASIS_RE.match(s)
        if m:
            basis = m.group(1)
            continue
        m = _KIND_RE.match(s)
        if m:
            kind, seg_status = m.group(1), m.group(2)
            continue
        if s.startswith("S "):
            parts = s.split()
            if len(parts) != 1 + len(STEP_COLUMNS):
                out["status"] = "bad_row"
                out["reason"] = f"S 行列数 {len(parts)} != 10"
                return out
            try:
                vals = [float(v) for v in parts[1:]]
            except (ValueError, TypeError):
                out["status"] = "bad_row"
                out["reason"] = f"S 行有非数值字段：{s[:100]}"
                return out
            if any(v != v or v in (float("inf"), float("-inf")) for v in vals):
                out["status"] = "bad_row"
                out["reason"] = f"S 行有非有限数值：{s[:100]}"
                return out
            step = dict(zip(STEP_COLUMNS, vals))
            step["load_step"] = int(vals[0])
            step["iter"] = int(vals[1])
            step["line_cuts"] = int(vals[7])
            step["converged"] = int(vals[8])
            if (step["load_step"] < 1 or step["iter"] < 1
                    or step["line_cuts"] < 0 or step["converged"] not in (0, 1)
                    or step["alpha"] <= 0.0 or step["alpha"] > 1.0
                    or step["residual"] < 0.0 or step["du"] < 0.0
                    or step["ref"] < 0.0 or step["lambda"] <= 0.0
                    or step["lambda"] > 1.0 + 1e-9):
                out["status"] = "bad_row"
                out["reason"] = f"S 行取值越界：{s[:100]}"
                return out
            steps.append(step)
            continue
        kv = dict(_KV_RE.findall(s))
        if kv and all(k in kv for k in HEAD_KEYS):
            try:
                head = {k: float(kv[k]) for k in HEAD_KEYS}
            except (ValueError, TypeError):
                out["status"] = "bad_header"
                out["reason"] = s[:120]
                return out
            head["NSTEP"] = int(head["NSTEP"])
            head["NITER"] = int(head["NITER"])
            head["MAXITER"] = int(head["MAXITER"])
            head["CONVERGED"] = int(head["CONVERGED"])
            head["LINECUTS"] = int(head["LINECUTS"])
            head["STEPREFINE"] = int(head["STEPREFINE"])
            continue
        out["status"] = "bad_row"
        out["reason"] = s[:120]
        return out

    out["basis"], out["kind"], out["status_seg"] = basis, kind, seg_status
    out["note"], out["failreason"] = note, failreason
    if basis != BASIS:
        out["status"] = "bad_basis"
        return out
    if not kind or not seg_status or not head:
        out["status"] = "bad_header"
        return out
    niter = head["NITER"]
    if niter <= 0:
        out["status"] = "no_iteration"
        out["reason"] = "NITER=0"
        return out
    if len(steps) != niter:
        out["status"] = "row_count_mismatch"
        out["reason"] = f"NITER={niter}，S 行={len(steps)}"
        return out
    # 段内数字互相核对（不重算力学）：线搜索合计 / 荷载步序号 / 收敛标志 /
    # 收敛时最后 λ 与 LOADRATIO。任何一项不符都拒绝出表。
    if sum(s["line_cuts"] for s in steps) != head["LINECUTS"]:
        out["status"] = "inconsistent_trace"
        out["reason"] = "LINECUTS 与 S 行折半合计不符"
        return out
    if max(s["load_step"] for s in steps) > head["NSTEP"] or head["NSTEP"] < 1:
        out["status"] = "inconsistent_trace"
        out["reason"] = "S 行荷载步序号超过 NSTEP"
        return out
    if (head["CONVERGED"] == 1) != (seg_status == "ok"):
        out["status"] = "inconsistent_trace"
        out["reason"] = "CONVERGED 与 STATUS 不符"
        return out
    if head["CONVERGED"] == 1:
        if steps[-1]["converged"] != 1:
            out["status"] = "inconsistent_trace"
            out["reason"] = "整体收敛但最后一条 S 行未收敛"
            return out
        if abs(max(s["lambda"] for s in steps) - head["LOADRATIO"]) > 1e-9:
            out["status"] = "inconsistent_trace"
            out["reason"] = "收敛时最大 λ 与 LOADRATIO 不符"
            return out
    out["status"] = "ok"
    out["head"] = head
    out["steps"] = steps
    out["nstep"] = head["NSTEP"]
    return out


def summary_table(d: dict[str, Any] | None) -> dict[str, Any] | None:
    """汇总表 `title/headers/rows/note`；状态非 ok 时返回 None，不摆空表。"""
    if not d or d.get("status") != "ok":
        return None
    h = d["head"]
    rows = [
        ["荷载分级步数 NSTEP", str(h["NSTEP"]), "已成功完成的荷载步数"],
        ["Newton 迭代总数 NITER", str(len(d["steps"])), "逐迭代 S 行数"],
        ["每步迭代上限 MAXITER", str(h["MAXITER"]), "*ANALYSIS MAXITER"],
        ["收敛容差 TOL", f'{h["TOL"]:.6g}', "同时管残差与位移增量判据"],
        ["整体收敛 CONVERGED", str(h["CONVERGED"]), "1=收敛，0=未收敛"],
        ["初始残差 RESFIRST (N)", f'{h["RESFIRST"]:.6g}', "自由自由度 |R|"],
        ["末残差 RESLAST (N)", f'{h["RESLAST"]:.6g}', "最终构形重装配后的 |R|"],
        ["线搜索折半合计 LINECUTS", str(h["LINECUTS"]), "S 行折半次数之和"],
        ["荷载步细分 STEPREFINE", str(h["STEPREFINE"]), "不收敛时步长折半次数"],
        ["已施加荷载比例 LOADRATIO", f'{h["LOADRATIO"]:.6g}', "收敛时为 1"],
        ["内核状态 STATUS", str(d["status_seg"]), "ok / not_converged"],
    ]
    if d.get("failreason"):
        rows.append(["未收敛原因 FAILREASON", str(d["failreason"]), "内核原文"])
    return {
        "title": "2D 内层 Newton 收敛汇总",
        "headers": ["项目", "值", "说明"],
        "rows": rows,
        "note": NOTE + " 来源：" + SOURCE + "。",
    }


def step_table(d: dict[str, Any] | None) -> dict[str, Any] | None:
    """逐迭代表 `title/headers/rows/note`；状态非 ok 时返回 None，不摆空表。"""
    if not d or d.get("status") != "ok" or not d.get("steps"):
        return None
    rows: list[list[str]] = []
    for s in d["steps"]:
        rows.append([
            str(s["load_step"]), str(s["iter"]),
            f'{s["lambda"]:.6g}', f'{s["residual"]:.6g}',
            f'{s["du"]:.6g}', f'{s["ref"]:.6g}',
            f'{s["alpha"]:.6g}', str(s["line_cuts"]),
            "是" if s["converged"] else "否",
        ])
    return {
        "title": "2D 内层 Newton 逐迭代残差明细",
        "headers": ["荷载步", "步内迭代", "λ", "|R| (N)", "|Δu| (m)",
                    "|Fext| (N)", "α", "线搜索折半", "步收敛"],
        "rows": rows,
        "note": NOTE + " 来源：" + SOURCE + "。",
    }


def tables(d: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """报告 / 界面唯一取数口：状态非 ok 时为空 dict，不摆空表。"""
    out: dict[str, dict[str, Any]] = {}
    a, b = summary_table(d), step_table(d)
    if a:
        out["newton_summary_table"] = a
    if b:
        out["newton_step_table"] = b
    return out
