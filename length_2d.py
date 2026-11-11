"""length_2d.py —— 2D 平面索长闭合 [LENGTH2D] 的唯一口径层。

只读 FEM-Eigen `example_mixed.exe --length-detail-only` 写出的
`<stem>.length.txt` 里的 `[LENGTH2D]` 段，产出表字典与中文状态。铁律：

* **不重算**：`Lu` / `T*` / `N` / 逐轮残差 / 有限差分 Jacobian 结果全部来自
  内核段。Python 不解 `K·ΔU=R`、不由位移反推索力、不另求一组下料长度、
  不重新迭代，**不形成第二套力学口径**。
* **不补零**：缺段 / `ERROR=` / 字段缺失 / 行数不符 / BASIS 或 ERRKIND 不符 /
  闭式初值核对不过，一律给显式中文状态，三张表返回空 dict，不摆空表。
  `Lu0 = L*/(1+T*/EA)` 只是**读同一批数字互相核对**，不是新算一遍力学。
* **能力边界**：这是 2D **首次真正新造的力学**，不是第十一次那种「内核已算、
  只补留痕」。2D 用无垂度线性杆本构 + 中心有限差分 J + LM 阻尼最小二乘，
  唯一判据是索力相对偏差 `ERRKIND=tension_rel`；**不是** 3D 的垂度缩短
  `s(T)` / Ernst 等效弹模 / 闭式影响矩阵 `J=M·D` / 共转成桥态 / 塔顶
  `ΣT·cos` 平衡 / DECKLOAD / SYSGUARD，也不等于 3D 的
  `[OPT] → [LENGTH] → [NEWTON]` 三层迭代。2D 没有阶段维度，不设 STAGE 占位。
* `[OPT]` 带约束目标索力优化这一层**仍标 N、不造占位**：本轮只闭环
  `[LENGTH2D]` 一层，段里不写假的目标函数 / IRLS / 体系守卫过程量。
  过程量只说明数值如何收敛，不是规范意义上的设计合格判定。
"""

from __future__ import annotations

import re
from typing import Any

SECTION = "[LENGTH2D]"
BASIS = "2d_global"
SOURCE = "FEM-Eigen example_mixed.exe --length-detail-only → <stem>.length.txt"

BOUNDARY_NOTE = (
    "2D 平面索长闭合只做「无垂度线性杆的 Lu 命中 T*」一层：闭式初值 "
    "Lu0=L*/(1+T*/EA)、中心有限差分 Jacobian + LM 阻尼最小二乘，唯一判据是"
    "索力相对偏差。没有 3D 的垂度 / Ernst 等效弹模 / 闭式影响矩阵 / 共转成桥态"
    " / 塔顶平衡 / SYSGUARD，也不等于 3D 的三层迭代；[OPT] 带约束目标索力优化"
    "仍标 N、不造占位。段里的 Lu 只是诊断产物，不写回主 .result、不改 .dat。"
    "能力矩阵该格仍标 P 不标 Y，过程量不构成规范合格判定。"
)

TABLE_NOTE = (
    "T* 是 .dat 里显式给出的目标索力（N）；N 是内核最后一轮算出的成桥索力"
    "（N）；Lu0 是无垂度线性杆闭式初值、Lu 是最后一轮下料长度（m），未收敛时"
    "Lu 是未采纳值；L* 为几何初始弦长（m），EA 为轴向刚度（N）。"
    "I 行 stepMax=0 是收敛轮未解增量的事实值，不是补零。"
)

_HDR_RE = re.compile(
    r"^NCABLES=(\d+)\s+NITER=(\d+)\s+MAXITER=(\d+)\s+"
    r"TOLT=([^\s]+)\s+CONVERGED=([01])$"
)
_KV_RE = re.compile(r"([A-Z][A-Z0-9_]*)=([^\s]+)")

STATUS_TEXT: dict[str, str] = {
    "not_requested": "本次未请求 2D 平面索长闭合明细（默认关闭），不出表、不补零。",
    "unavailable": "未编译 example_mixed.exe；本次不做 2D 索长闭合，请重新编译 FEM-Eigen。",
    "no_contract": "模型没有显式写 *CABLE_LENGTH2D 目标索力契约；内核没有算过下料长度闭合，本次不做，不拿零行冒充收敛。",
    "no_output": "内核未写出 <stem>.length.txt；本次不做 2D 索长闭合，不补零。",
    "no_section": "结果文件里没有 [LENGTH2D] 段；本次不做 2D 索长闭合，不补零。",
    "kernel_error": "内核明确写出 ERROR= 或未收敛，本次不做 2D 索长闭合表，不摆空表。",
    "bad_header": "[LENGTH2D] 段缺 NCABLES/NITER/MAXITER/TOLT/CONVERGED 头行，解析拒绝，不摆空表。",
    "bad_basis": "[LENGTH2D] 段的 BASIS 不是 2d_global，口径不匹配，解析拒绝。",
    "bad_kind": "[LENGTH2D] 段的 ERRKIND/JMETHOD 不是 tension_rel / FINITE_DIFFERENCE，口径不匹配，解析拒绝。",
    "bad_row": "[LENGTH2D] 段有缺字段、非数值或无法识别的行，解析拒绝，不摆空表。",
    "row_count_mismatch": "[LENGTH2D] 段的 I/E 行数与 NITER/NCABLES 不符，解析拒绝。",
    "inconsistent_trace": "[LENGTH2D] 段的 ERRLAST 与最后一轮 I 行残差不符，或 E 行闭式初值核对不过，解析拒绝。",
    "solver_error": "2D 索长闭合内核调用失败；本次不做明细，不补零。",
    "ok": "2D 平面索长闭合：{n} 根 CABLE、{niter} 轮收敛，来自内核 [LENGTH2D] 段；只解析、不重算。",
}


def status_text(d: dict[str, Any] | None) -> str:
    """把状态翻成一句中文；异常状态附内核给的理由。"""
    d = d or {}
    st = str(d.get("status") or "not_requested")
    text = STATUS_TEXT.get(st, "2D 索长闭合状态未知，不给表、不补零。")
    if st == "ok":
        return text.format(n=len(d.get("cables") or []),
                           niter=len(d.get("steps") or []))
    reason = d.get("reason") or d.get("stdout")
    if reason and st in ("kernel_error", "solver_error", "bad_row",
                         "no_output", "no_contract", "inconsistent_trace"):
        return text + " 内核信息：" + str(reason)[-300:]
    return text


def parse_length_txt(txt: str | None) -> dict[str, Any]:
    """解析 `[LENGTH2D]` 段；任何异常都给显式 status，绝不返回半张表。

    成功时 ``{"status": "ok", "basis": BASIS, "kind": str,
    "errkind": str, "jmethod": str, "analysis": str, "ncables": int,
    "niter": int, "maxiter": int, "tolt": float, "converged": bool,
    "errfirst": float, "errlast": float, "steps": [...],
    "cables": [...], "note": str, "source": SOURCE}``。
    """
    out: dict[str, Any] = {
        "status": "no_section", "source": SOURCE,
        "basis": "", "kind": "", "errkind": "", "jmethod": "",
        "analysis": "", "ncables": 0, "niter": 0, "maxiter": 0,
        "tolt": 0.0, "converged": False, "errfirst": 0.0, "errlast": 0.0,
        "steps": [], "cables": [], "note": "", "reason": "",
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
    status = ""
    errkind = ""
    jmethod = ""
    analysis = ""
    hdr: tuple[int, int, int, float, bool] | None = None
    errfirst: float | None = None
    errlast: float | None = None
    steps: list[dict[str, Any]] = []
    cables: list[dict[str, Any]] = []
    note = ""
    error = ""

    for raw in lines[start + 1:]:
        s = raw.strip()
        if not s:
            continue
        if s.startswith("["):
            break  # 下一段开始，不把别人的行收进来
        if s.startswith("NOTE="):
            note = s[len("NOTE="):]
            continue
        if s.startswith("FAILREASON="):
            out["reason"] = s[len("FAILREASON="):]
            continue
        if s.startswith("ERROR="):
            error = s[len("ERROR="):].strip()
            continue
        if s.startswith("BASIS="):
            basis = s[len("BASIS="):].strip()
            continue
        if s.startswith("KIND="):
            kv = dict(_KV_RE.findall(s))
            kind, status = kv.get("KIND", ""), kv.get("STATUS", "")
            continue
        if s.startswith("ERRKIND="):
            kv = dict(_KV_RE.findall(s))
            errkind = kv.get("ERRKIND", "")
            analysis = kv.get("ANALYSIS", "")
            jmethod = kv.get("JMETHOD", "")
            continue
        m = _HDR_RE.match(s)
        if m:
            hdr = (int(m.group(1)), int(m.group(2)), int(m.group(3)),
                   float(m.group(4)), m.group(5) == "1")
            continue
        if s.startswith("ERRFIRST="):
            kv = dict(_KV_RE.findall(s))
            try:
                errfirst = float(kv["ERRFIRST"])
                errlast = float(kv["ERRLAST"])
            except (KeyError, ValueError, TypeError):
                out["status"] = "bad_row"
                out["reason"] = "ERRFIRST/ERRLAST 非数值"
                return out
            continue
        if s.startswith("I "):
            parts = s.split()
            if len(parts) != 6:
                out["status"] = "bad_row"
                out["reason"] = "I 行列数不是 6"
                return out
            try:
                steps.append({
                    "iter": int(parts[1]), "errMax": float(parts[2]),
                    "errNorm": float(parts[3]), "stepMax": float(parts[4]),
                    "alpha": float(parts[5]),
                })
            except (ValueError, TypeError):
                out["status"] = "bad_row"
                out["reason"] = "I 行有非数值字段"
                return out
            continue
        if s.startswith("E "):
            parts = s.split()
            if len(parts) != 8:
                out["status"] = "bad_row"
                out["reason"] = "E 行列数不是 8"
                return out
            try:
                cables.append({
                    "elem": int(parts[1]), "target": float(parts[2]),
                    "finalT": float(parts[3]), "Lu0": float(parts[4]),
                    "Lu": float(parts[5]), "Lgeom": float(parts[6]),
                    "EA": float(parts[7]),
                })
            except (ValueError, TypeError):
                out["status"] = "bad_row"
                out["reason"] = "E 行有非数值字段"
                return out
            continue
        out["status"] = "bad_row"
        out["reason"] = s[:120]
        return out

    # 无契约：内核根本没算过，显式中文状态，不摆空表。
    if error == "no_length_request" or kind == "none":
        out["status"] = "no_contract"
        out["reason"] = error or "no_length_request"
        out["basis"], out["note"] = basis, note
        return out
    if error:
        out["status"] = "kernel_error"
        out["reason"] = error + (("；" + out["reason"]) if out["reason"] else "")
        out["basis"], out["note"] = basis, note
        return out
    if hdr is None:
        out["status"] = "bad_header"
        return out
    if basis != BASIS:
        out["status"] = "bad_basis"
        return out
    if errkind != "tension_rel" or jmethod != "FINITE_DIFFERENCE":
        out["status"] = "bad_kind"
        return out
    if status != "ok" or not hdr[4]:
        out["status"] = "kernel_error"
        out["reason"] = "STATUS=" + status + " CONVERGED=0"
        out["basis"], out["note"] = basis, note
        return out
    nc, ni, maxit, tolt, conv = hdr
    if len(steps) != ni or len(cables) != nc or ni < 1 or nc < 1:
        out["status"] = "row_count_mismatch"
        return out
    if errfirst is None or errlast is None:
        out["status"] = "bad_header"
        return out
    # 只核对同一批数字彼此一致，不重算力学、不另求一组 Lu。
    if abs(errlast - steps[-1]["errMax"]) > 5e-9 * max(1.0, abs(errlast)):
        out["status"] = "inconsistent_trace"
        out["reason"] = f"ERRLAST={errlast} 与末轮 errMax={steps[-1]['errMax']} 不符"
        return out
    for c in cables:
        if c["EA"] <= 0.0 or c["target"] <= 0.0 or c["Lgeom"] <= 0.0:
            out["status"] = "bad_row"
            out["reason"] = f"E {c['elem']} 的 EA/T*/L* 必须大于 0"
            return out
        want = c["Lgeom"] / (1.0 + c["target"] / c["EA"])
        if abs(c["Lu0"] - want) > 1e-8 * max(1.0, abs(want)):
            out["status"] = "inconsistent_trace"
            out["reason"] = (f"E {c['elem']} 的 Lu0={c['Lu0']} 与闭式初值"
                             f"L*/(1+T*/EA)={want} 不符")
            return out
        if abs(c["finalT"] / c["target"] - 1.0) > errlast + 1e-8:
            out["status"] = "inconsistent_trace"
            out["reason"] = (f"E {c['elem']} 的 N/T*-1 超过末轮 errMax={errlast}")
            return out

    out.update({
        "status": "ok", "basis": basis, "kind": kind, "status_kernel": status,
        "errkind": errkind, "jmethod": jmethod, "analysis": analysis,
        "ncables": nc, "niter": ni, "maxiter": maxit, "tolt": tolt,
        "converged": conv, "errfirst": errfirst, "errlast": errlast,
        "steps": steps, "cables": cables, "note": note, "reason": "",
    })
    return out


def tables(d: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """三张表字典 `title/headers/rows/note`；状态非 ok 时返回空 dict。"""
    if not d or d.get("status") != "ok":
        return {}
    tail = " 来源：" + SOURCE + "。" + BOUNDARY_NOTE
    summary = {
        "title": "2D 平面索长闭合 · 收敛摘要",
        "headers": ["项", "值"],
        "rows": [
            ["目标索数 NCABLES", str(d["ncables"])],
            ["迭代轮数 NITER", str(d["niter"])],
            ["最大轮数 MAXITER", str(d["maxiter"])],
            ["容差 TOLT", f'{d["tolt"]:.6g}'],
            ["收敛 CONVERGED", "1" if d["converged"] else "0"],
            ["误差口径 ERRKIND", d["errkind"]],
            ["分析类型 ANALYSIS", d["analysis"]],
            ["Jacobian JMETHOD", d["jmethod"]],
            ["首轮 max|N/T*-1|", f'{d["errfirst"]:.6g}'],
            ["末轮 max|N/T*-1|", f'{d["errlast"]:.6g}'],
        ],
        "note": TABLE_NOTE + tail,
    }
    iters = {
        "title": "2D 平面索长闭合 · 逐轮残差",
        "headers": ["轮次", "max|N/T*-1|", "残差范数", "max|ΔLu| (m)", "alpha"],
        "rows": [[str(s["iter"]), f'{s["errMax"]:.6g}',
                  f'{s["errNorm"]:.6g}', f'{s["stepMax"]:.6g}',
                  f'{s["alpha"]:.6g}'] for s in d["steps"]],
        "note": TABLE_NOTE + tail,
    }
    cables_t = {
        "title": "2D 平面索长闭合 · 逐索下料长度",
        "headers": ["单元", "T* (kN)", "N (kN)", "Lu0 (m)", "Lu (m)",
                    "L* (m)", "EA (MN)", "N/T*−1"],
        "rows": [[str(c["elem"]), f'{c["target"] / 1e3:.4f}',
                  f'{c["finalT"] / 1e3:.4f}', f'{c["Lu0"]:.6f}',
                  f'{c["Lu"]:.6f}', f'{c["Lgeom"]:.6f}',
                  f'{c["EA"] / 1e6:.4f}',
                  f'{c["finalT"] / c["target"] - 1.0:.3e}']
                 for c in d["cables"]],
        "note": TABLE_NOTE + tail,
    }
    return {
        "length_2d_summary_table": summary,
        "length_2d_iter_table": iters,
        "length_2d_cable_table": cables_t,
    }
