"""opt_2d.py —— 2D 平面索力优化 [OPT2D] 的唯一口径层。

只读 FEM-Eigen `example_mixed.exe --opt-detail-only` 写出的
`<stem>.opt.txt` 里的 `[OPT2D]` 段，产出表字典与中文状态。铁律：

* **不重算**：目标函数 / 归一化 / 中心有限差分 Jacobian / IRLS / 投影梯度 /
  逐轮 objective·mpeak·step / 逐索 T0·Tstar·Nfinal·Lu / 梁端 M0·Mfinal
  全部来自内核段。Python 不解优化、不重算影响矩阵、不另求一组最优索力、
  不由位移反推弯矩，**不形成第二套力学口径**。
* **不补零**：缺段 / `ERROR=` / 头行缺字段 / BASIS 或口径不符 / 行数不符 /
  段内数字自相矛盾，一律给显式中文状态，四张表返回空 dict，不摆空表。
  段内一致性只是**读同一批数字互相核对**，不是重算一遍优化。
* **能力边界**：这是 2D 平面索力优化（MINMAXM，`min max|M|` 只针对用户显式
  登记的 BEAM 端弯矩），**不是** 3D 的 `J=M·D` 闭式影响矩阵，也不等于 3D
  的 `[OPT]→[LENGTH]→[NEWTON]` 三层迭代；2D 无 DECKLOAD / 塔顶平衡 /
  SYSGUARD 三判据，不造占位。命中 T* 属 `[LENGTH2D]` 职责，本段只定目标。
* **「解析 ok」不等于「建议值已采纳」**：内核 `STATUS` / `CONVERGED` /
  `CONVBY` / `APPLIED` / `REJECT` 是过程量，原样保留在表里；只要段格式与
  行数自洽就出表，由读者自己判断采纳与否，Python 不盖过内核口径、不静默
  丢掉内核已经写出的过程量。
* 段名带 `2D` 后缀，解析层只收 `[OPT2D]` 段内行，**不与 3D `[OPT]` 解析链
  串段**；段里没有 `SENS/NJ/J/REFACT/geomErr/STAGE/DECKSHARE/SYSGUARD/BAL/
  TMAXSRC`，解析层不虚构这些列。
"""

from __future__ import annotations

import re
from typing import Any

SECTION = "[OPT2D]"
BASIS = "2d_global"
SOURCE = "FEM-Eigen example_mixed.exe --opt-detail-only → <stem>.opt.txt"

BOUNDARY_NOTE = (
    "① 这是 2D 平面索力优化，min max|M| 只针对用户显式登记的 BEAM 端弯矩，"
    "不自动识别主梁、不补零；"
    "② 变量是无量纲名义索力经 Lu(x) 落到第十三次无垂度线性杆本构，"
    "用中心有限差分 Jacobian + IRLS + 投影梯度，不是 3D 的 J=M·D 闭式影响矩阵；"
    "③ 只做 MINMAXM 目标与显式索力箱 TMIN/TMAX（BND_SRC=USER），不做 MINENERGY；"
    "④ 2D 没有 DECKLOAD / 塔顶 ΣT·cos 平衡 / SYSGUARD 三判据，不造占位；"
    "⑤ 不等于 3D [OPT]→[LENGTH]→[NEWTON] 三层迭代；"
    "⑥ 命中 T* 属 [LENGTH2D] 职责，[OPT2D] 只定目标、不写回 .dat/.result，"
    "APPLIED=1 只表示建议值被采纳，T 行只是过程量、不能当设计索力；"
    "⑦ 能力矩阵该格仍标 P 不标 Y，过程量只说明数值如何收敛，"
    "不构成规范意义上的设计合格判定。段里没有 SENS/NJ/J/REFACT/geomErr/"
    "STAGE/DECKSHARE/SYSGUARD/BAL/TMAXSRC，解析层不虚构这些列。"
)

TABLE_NOTE = (
    "I 行：irls/iter 为 IRLS 轮与步序号，objective 为归一化目标值（无量纲），"
    "mpeak 为线性化预测的 max|M|（N·m），step 为相对步长；"
    "T 行：T0/Tstar/Nfinal 为初始/建议/终值名义索力（N），Lu0/Lu 为下料长度"
    "（m），Lgeom 为几何弦长（m），EA 为轴向刚度（N）；"
    "M 行：M0/Mfinal 为登记梁端优化前/后弯矩（N·m），end 为端号 1|2。"
    "STATUS/CONVERGED/CONVBY/APPLIED/REJECT 是内核过程量，原样保留；"
    "解析 ok 只表示段可出表，不表示建议值已采纳或优化已收敛。"
    "所有数字来自内核 [OPT2D] 段，只解析、不重算、不补零。"
)

_KV_RE = re.compile(r"([A-Z][A-Z0-9_]*)=([^\s]+)")

#: 合法段必须给全的头行键（分散在多行 KEY=value，统一收进 kv 后校验）。
HEADER_KEYS = (
    "OBJECTIVE", "OBJECTIVE_KIND", "JMETHOD",
    "NCABLES", "NMOMENTS", "NITER", "IRLS", "MAXITER", "IRLSMAX",
    "TOL", "ROUNDTOL", "TSCALE", "MSCALE", "CONVERGED", "CONVBY",
    "APPLIED", "REJECT", "TMIN", "TMAX", "BND_SRC",
    "MPEAK_KIND", "MPEAK0", "MPEAK",
)

STATUS_TEXT: dict[str, str] = {
    "not_requested": "本次未请求 2D 平面索力优化明细（默认关闭），不出表、不补零。",
    "unavailable": "未编译 example_mixed.exe；本次不做 2D 平面索力优化，请重新编译 FEM-Eigen。",
    "no_contract": "模型没有显式写 *CABLE_OPT2D 索力优化契约（内核 ERROR=no_opt_request / KIND=none）；内核没有算过优化，本次不做，不拿零行冒充收敛。",
    "no_output": "内核未写出 <stem>.opt.txt；本次不做 2D 平面索力优化，不补零。",
    "no_section": "结果文件里没有 [OPT2D] 段；本次不做 2D 平面索力优化，不补零。",
    "kernel_error": "内核明确写出 ERROR= 或 STATUS=invalid_contract/solve_failed，本次不做 2D 平面索力优化表，不摆空表。",
    "bad_header": "[OPT2D] 段缺必需头行键（OBJECTIVE/OBJECTIVE_KIND/JMETHOD/NCABLES/NMOMENTS/NITER/IRLS/MAXITER/IRLSMAX/TOL/ROUNDTOL/TSCALE/MSCALE/CONVERGED/CONVBY/APPLIED/REJECT/TMIN/TMAX/BND_SRC/MPEAK_KIND/MPEAK0/MPEAK），解析拒绝，不摆空表。",
    "bad_basis": "[OPT2D] 段的 BASIS 不是 2d_global，口径不匹配，解析拒绝。",
    "bad_kind": "[OPT2D] 段的 KIND/OBJECTIVE/OBJECTIVE_KIND/JMETHOD/BND_SRC/MPEAK_KIND 不是 plane_cable_force_opt/MINMAXM/NORMALIZED/FINITE_DIFFERENCE/USER/REGISTERED_BEAM_END，口径不匹配，解析拒绝。",
    "bad_row": "[OPT2D] 段有缺字段、非数值、端号越界或无法识别的行，解析拒绝，不摆空表。",
    "row_count_mismatch": "[OPT2D] 段的 I/T/M 行数与 NITER/NCABLES/NMOMENTS 不符，解析拒绝。",
    "inconsistent_trace": "[OPT2D] 段内数字自相矛盾（CONVERGED/CONVBY 或 APPLIED/REJECT），解析拒绝，不拿另一套数凑表。",
    "solver_error": "2D 平面索力优化内核调用失败；本次不做明细，不补零。",
    "ok": "2D 平面索力优化：{nc} 根 CABLE、{nm} 个登记梁端、{niter} 轮迭代，来自内核 [OPT2D] 段；只解析、不重算。",
}


def status_text(d: dict[str, Any] | None) -> str:
    """把状态翻成一句中文；异常状态附内核给的理由。"""
    d = d or {}
    st = str(d.get("status") or "not_requested")
    text = STATUS_TEXT.get(st, "2D 平面索力优化状态未知，不给表、不补零。")
    if st == "ok":
        return text.format(nc=len(d.get("cables") or []),
                           nm=len(d.get("moments") or []),
                           niter=len(d.get("steps") or []))
    reason = d.get("reason") or d.get("stdout")
    if reason and st in ("kernel_error", "solver_error", "bad_row",
                         "no_output", "no_contract", "inconsistent_trace"):
        return text + " 内核信息：" + str(reason)[-300:]
    return text


def parse_opt_txt(txt: str | None) -> dict[str, Any]:
    """解析 `[OPT2D]` 段；任何异常都给显式 status，绝不返回半张表。

    成功时 ``{"status": "ok", "basis": BASIS, "kind": str,
    "status_kernel": str, "objective": str, "objective_kind": str,
    "jmethod": str, "ncables": int, "nmoments": int, "niter": int,
    "irls": int, "maxiter": int, "irlsmax": int, "tol": float,
    "roundtol": float, "tscale": float, "mscale": float,
    "converged": int, "convby": str, "applied": int, "reject": str,
    "tmin": float, "tmax": float, "bnd_src": str, "mpeak_kind": str,
    "mpeak0": float, "mpeak": float, "steps": [...], "cables": [...],
    "moments": [...], "note": str, "reason": "", "source": SOURCE}``。
    """
    out: dict[str, Any] = {
        "status": "no_section", "source": SOURCE,
        "basis": "", "kind": "", "status_kernel": "",
        "objective": "", "objective_kind": "", "jmethod": "",
        "ncables": 0, "nmoments": 0, "niter": 0, "irls": 0,
        "maxiter": 0, "irlsmax": 0, "tol": 0.0, "roundtol": 0.0,
        "tscale": 0.0, "mscale": 0.0, "converged": 0, "convby": "",
        "applied": 0, "reject": "", "tmin": 0.0, "tmax": 0.0,
        "bnd_src": "", "mpeak_kind": "", "mpeak0": 0.0, "mpeak": 0.0,
        "steps": [], "cables": [], "moments": [], "note": "", "reason": "",
    }
    if not txt or SECTION not in txt:
        return out
    lines = txt.splitlines()
    try:
        start = next(i for i, s in enumerate(lines) if s.strip() == SECTION)
    except StopIteration:
        return out

    basis = ""
    kv: dict[str, str] = {}
    steps: list[dict[str, Any]] = []
    cables: list[dict[str, Any]] = []
    moments: list[dict[str, Any]] = []
    note = ""
    error = ""
    reason = ""

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
            reason = s[len("FAILREASON="):]
            continue
        if s.startswith("ERROR="):
            error = s[len("ERROR="):].strip()
            continue
        if s.startswith("BASIS="):
            basis = s[len("BASIS="):].strip()
            continue
        if s.startswith("I "):
            parts = s.split()
            if len(parts) != 6:
                out["status"] = "bad_row"
                out["reason"] = "I 行列数不是 6（I + 5 字段）"
                return out
            try:
                irls, it = int(parts[1]), int(parts[2])
                steps.append({
                    "irls": irls, "iter": it,
                    "objective": float(parts[3]), "mpeak": float(parts[4]),
                    "step": float(parts[5]),
                })
            except (ValueError, TypeError):
                out["status"] = "bad_row"
                out["reason"] = "I 行有非数值字段"
                return out
            if irls < 1 or it < 1:
                out["status"] = "bad_row"
                out["reason"] = "I 行的 irls/iter 必须为正整数"
                return out
            continue
        if s.startswith("T "):
            parts = s.split()
            if len(parts) != 10:
                out["status"] = "bad_row"
                out["reason"] = "T 行列数不是 10（T + 9 字段）"
                return out
            try:
                k, elem = int(parts[1]), int(parts[2])
                cables.append({
                    "k": k, "elem": elem,
                    "T0": float(parts[3]), "Tstar": float(parts[4]),
                    "Nfinal": float(parts[5]), "Lu0": float(parts[6]),
                    "Lu": float(parts[7]), "Lgeom": float(parts[8]),
                    "EA": float(parts[9]),
                })
            except (ValueError, TypeError):
                out["status"] = "bad_row"
                out["reason"] = "T 行有非数值字段"
                return out
            if k < 1 or elem < 1:
                out["status"] = "bad_row"
                out["reason"] = "T 行的 k/elem 必须为正整数"
                return out
            continue
        if s.startswith("M "):
            parts = s.split()
            if len(parts) != 6:
                out["status"] = "bad_row"
                out["reason"] = "M 行列数不是 6（M + 5 字段）"
                return out
            try:
                idx, elem, end = int(parts[1]), int(parts[2]), int(parts[3])
                moments.append({
                    "i": idx, "elem": elem, "end": end,
                    "M0": float(parts[4]), "Mfinal": float(parts[5]),
                })
            except (ValueError, TypeError):
                out["status"] = "bad_row"
                out["reason"] = "M 行有非数值字段"
                return out
            if idx < 1 or elem < 1 or end not in (1, 2):
                out["status"] = "bad_row"
                out["reason"] = "M 行的 i/elem 必须为正整数、end 必须是 1|2"
                return out
            continue
        found = dict(_KV_RE.findall(s))
        if not found:
            out["status"] = "bad_row"
            out["reason"] = s[:120]
            return out
        kv.update(found)

    out["basis"], out["note"], out["reason"] = basis, note, reason
    kind = kv.get("KIND", "")
    status_kernel = kv.get("STATUS", "")

    # 无契约：内核根本没算过，显式中文状态，不摆空表。
    if error == "no_opt_request" or kind == "none" or status_kernel == "not_applicable":
        out["status"] = "no_contract"
        out["reason"] = error or "no_opt_request"
        return out
    # 内核 ERROR 行 / 契约非法 / 求解失败：一律 kernel_error，不静默兜底。
    if error:
        out["status"] = "kernel_error"
        out["reason"] = error + (("；" + reason) if reason else "")
        return out
    if status_kernel in ("invalid_contract", "solve_failed"):
        out["status"] = "kernel_error"
        out["reason"] = "STATUS=" + status_kernel + (("；" + reason) if reason else "")
        return out
    # 头行必须全：缺任一必需键都拒绝出表。
    if any(k not in kv for k in HEADER_KEYS) or not kind or not status_kernel:
        out["status"] = "bad_header"
        return out
    if basis != BASIS:
        out["status"] = "bad_basis"
        return out
    if (kind != "plane_cable_force_opt"
            or kv.get("OBJECTIVE") != "MINMAXM"
            or kv.get("OBJECTIVE_KIND") != "NORMALIZED"
            or kv.get("JMETHOD") != "FINITE_DIFFERENCE"
            or kv.get("BND_SRC") != "USER"
            or kv.get("MPEAK_KIND") != "REGISTERED_BEAM_END"):
        out["status"] = "bad_kind"
        return out

    try:
        ncables = int(kv["NCABLES"])
        nmoments = int(kv["NMOMENTS"])
        niter = int(kv["NITER"])
        irls = int(kv["IRLS"])
        maxiter = int(kv["MAXITER"])
        irlsmax = int(kv["IRLSMAX"])
        tol = float(kv["TOL"])
        roundtol = float(kv["ROUNDTOL"])
        tscale = float(kv["TSCALE"])
        mscale = float(kv["MSCALE"])
        converged = int(kv["CONVERGED"])
        applied = int(kv["APPLIED"])
        tmin = float(kv["TMIN"])
        tmax = float(kv["TMAX"])
        mpeak0 = float(kv["MPEAK0"])
        mpeak = float(kv["MPEAK"])
    except (KeyError, ValueError, TypeError):
        out["status"] = "bad_header"
        out["reason"] = "头行有非数值字段"
        return out
    convby = kv.get("CONVBY", "")
    reject = kv.get("REJECT", "")

    # 行数核对：读同一批数字互相核对，不重算优化。
    if (len(steps) != niter or len(cables) != ncables
            or len(moments) != nmoments
            or niter < 1 or ncables < 1 or nmoments < 1):
        out["status"] = "row_count_mismatch"
        return out
    # 段内一致性：收敛却没有收敛方式 / 采纳却给了拒绝码，都是自相矛盾。
    if converged == 1 and convby == "none":
        out["status"] = "inconsistent_trace"
        out["reason"] = "CONVERGED=1 但 CONVBY=none"
        return out
    if applied == 1 and reject != "none":
        out["status"] = "inconsistent_trace"
        out["reason"] = "APPLIED=1 但 REJECT=" + reject
        return out

    out.update({
        "status": "ok", "basis": basis, "kind": kind,
        "status_kernel": status_kernel,
        "objective": kv["OBJECTIVE"], "objective_kind": kv["OBJECTIVE_KIND"],
        "jmethod": kv["JMETHOD"],
        "ncables": ncables, "nmoments": nmoments, "niter": niter,
        "irls": irls, "maxiter": maxiter, "irlsmax": irlsmax,
        "tol": tol, "roundtol": roundtol, "tscale": tscale, "mscale": mscale,
        "converged": converged, "convby": convby,
        "applied": applied, "reject": reject,
        "tmin": tmin, "tmax": tmax, "bnd_src": kv["BND_SRC"],
        "mpeak_kind": kv["MPEAK_KIND"], "mpeak0": mpeak0, "mpeak": mpeak,
        "steps": steps, "cables": cables, "moments": moments,
        "note": note, "reason": "",
    })
    return out


def tables(d: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """四张表字典 `title/headers/rows/note`；状态非 ok 时返回空 dict。"""
    if not d or d.get("status") != "ok":
        return {}
    tail = " 来源：" + SOURCE + "。" + BOUNDARY_NOTE
    summary = {
        "title": "2D 平面索力优化 · 收敛与采纳摘要",
        "headers": ["项", "值"],
        "rows": [
            ["目标函数 OBJECTIVE", d["objective"]],
            ["目标口径 OBJECTIVE_KIND", d["objective_kind"]],
            ["Jacobian JMETHOD", d["jmethod"]],
            ["CABLE 数 NCABLES", str(d["ncables"])],
            ["登记梁端数 NMOMENTS", str(d["nmoments"])],
            ["迭代行数 NITER", str(d["niter"])],
            ["IRLS 轮数 IRLS / 上限 IRLSMAX",
             f'{d["irls"]} / {d["irlsmax"]}'],
            ["单轮迭代上限 MAXITER", str(d["maxiter"])],
            ["收敛容差 TOL", f'{d["tol"]:.6g}'],
            ["圆整容差 ROUNDTOL", f'{d["roundtol"]:.6g}'],
            ["索力尺度 TSCALE (N)", f'{d["tscale"]:.6g}'],
            ["弯矩尺度 MSCALE (N·m)", f'{d["mscale"]:.6g}'],
            ["收敛 CONVERGED", str(d["converged"])],
            ["收敛方式 CONVBY", d["convby"]],
            ["建议值采纳 APPLIED", str(d["applied"])],
            ["拒绝码 REJECT", d["reject"]],
            ["内核状态 STATUS", d["status_kernel"]],
            ["索力箱 TMIN / TMAX (N)", f'{d["tmin"]:.6g} / {d["tmax"]:.6g}'],
            ["箱来源 BND_SRC", d["bnd_src"]],
            ["峰值口径 MPEAK_KIND", d["mpeak_kind"]],
            ["初始 max|M| MPEAK0 (N·m)", f'{d["mpeak0"]:.6g}'],
            ["优化后 max|M| MPEAK (N·m)", f'{d["mpeak"]:.6g}'],
        ],
        "note": TABLE_NOTE + tail,
    }
    iters = {
        "title": "2D 平面索力优化 · 逐轮迭代",
        "headers": ["IRLS 轮", "步序 iter", "目标 objective(归一化)",
                    "mpeak 线性化预测 (kN·m)", "相对步长 step"],
        "rows": [[str(s["irls"]), str(s["iter"]),
                  f'{s["objective"]:.6g}', f'{s["mpeak"] / 1e3:.4f}',
                  f'{s["step"]:.6g}'] for s in d["steps"]],
        "note": TABLE_NOTE + tail,
    }
    cables_t = {
        "title": "2D 平面索力优化 · 逐索过程量",
        "headers": ["序号 k", "CABLE 单元", "T0 (kN)", "Tstar (kN)",
                    "Nfinal (kN)", "Lu0 (m)", "Lu (m)", "Lgeom (m)",
                    "EA (MN)"],
        "rows": [[str(c["k"]), str(c["elem"]),
                  f'{c["T0"] / 1e3:.4f}', f'{c["Tstar"] / 1e3:.4f}',
                  f'{c["Nfinal"] / 1e3:.4f}', f'{c["Lu0"]:.6f}',
                  f'{c["Lu"]:.6f}', f'{c["Lgeom"]:.6f}',
                  f'{c["EA"] / 1e6:.4f}'] for c in d["cables"]],
        "note": TABLE_NOTE + tail,
    }
    moments_t = {
        "title": "2D 平面索力优化 · 登记梁端弯矩",
        "headers": ["序号 i", "BEAM 单元", "端 (1|2)",
                    "M0 (kN·m)", "Mfinal (kN·m)"],
        "rows": [[str(m["i"]), str(m["elem"]), str(m["end"]),
                  f'{m["M0"] / 1e3:.4f}', f'{m["Mfinal"] / 1e3:.4f}']
                 for m in d["moments"]],
        "note": TABLE_NOTE + tail,
    }
    return {
        "opt_2d_summary_table": summary,
        "opt_2d_iter_table": iters,
        "opt_2d_cable_table": cables_t,
        "opt_2d_moment_table": moments_t,
    }
