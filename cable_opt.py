"""cable_opt.py —— 斜拉桥成桥态的一轮可复核优化。

力学仍在 cable_solver 里。这里只做三件事：

1. 按当前索力把拉索面积向安全系数 2.5 收一档（一轮最多减 30%）。
2. 清掉预先给定的目标索力，第二份 .dat 用 METHOD=MINENERGY，由内核重分配索力。
3. 用主梁弯矩、塔顶顺桥向偏位和索安全系数决定采纳或退回原方案。

悬索桥的索力由找形决定，不走这条路径。悬臂施工的线性模型也不走。
"""

from __future__ import annotations

from typing import Any

from result_3d import CABLE_FPU_DEFAULT, CABLE_SAFETY_MIN, _elem_area_m2

# 目标取限值再留 5%，避免第二轮求解的索力略增后贴线失败。
TARGET_SF = CABLE_SAFETY_MIN * 1.05
# 一轮只收一档，不把面积一次砍到限值上。
SCALE_MIN = 0.70
# 主梁弯矩允许的相对上浮，用来盖住非线性迭代的末位差别。
MOMENT_SLACK = 1.02
# 塔顶偏位允许比基准多出的量（m）。
TOWER_SLACK_M = 0.005


def propose_area_scale(res: Any, model: Any, fpu: float = CABLE_FPU_DEFAULT) -> float | None:
    """返回拉索面积乘子。已经贴近限值时返回 None，不再减面积。"""
    ratios = _safety_ratios(res, model, fpu)
    if not ratios:
        return None
    kmin = min(ratios)
    if kmin <= TARGET_SF:
        return None
    scale = TARGET_SF / kmin
    scale = min(1.0, max(SCALE_MIN, scale))
    if scale > 0.98:
        return None
    return scale


def retarget_model(model: Any, scale: float) -> Any:
    """复制模型：拉索面积乘 scale，目标索力交给最小弯曲能。"""
    if scale <= 0:
        raise ValueError("面积乘子必须为正")
    cb = getattr(model, "cable_bridge", None)
    if cb is None or getattr(cb, "system", ""):
        raise ValueError("这一轮优化只用于斜拉桥成桥态")
    stay = {c.elem for c in cb.cables}
    et = {t.id: t for t in model.etypes}
    real_ids = set()
    for e in model.elements:
        if e.id in stay:
            real_ids.add(et[e.etype].real)
    reals = []
    for rc in model.reals:
        if rc.id in real_ids:
            reals.append(rc.model_copy(update={"A": float(rc.A) * scale}))
        else:
            reals.append(rc)
    cables = [c.model_copy(update={"tension": 0.0}) for c in cb.cables]
    bridge = cb.model_copy(update={"method": "MINENERGY", "cables": cables})
    return model.model_copy(update={"reals": reals, "cable_bridge": bridge})


def judge(baseline: Any, candidate: Any, base_model: Any, cand_model: Any,
          fpu: float = CABLE_FPU_DEFAULT) -> dict[str, Any]:
    """比较两轮求解。约束不满足就退回基准方案。"""
    b_sf = min(_safety_ratios(baseline, base_model, fpu) or [float("inf")])
    c_sf = min(_safety_ratios(candidate, cand_model, fpu) or [0.0])
    b_my = _deck_my_max(baseline, base_model)
    c_my = _deck_my_max(candidate, cand_model)
    b_ux = _tower_ux_max(baseline, base_model)
    c_ux = _tower_ux_max(candidate, cand_model)
    reasons: list[str] = []
    if candidate.converged is False:
        reasons.append("第二轮没有收敛")
    if c_sf < CABLE_SAFETY_MIN:
        reasons.append(f"最小安全系数 {c_sf:.2f} 低于 {CABLE_SAFETY_MIN:.1f}")
    if b_my <= 0 and c_my <= 0:
        reasons.append("两轮都没有主梁弯矩，不能判断弯矩是否变好")
    elif c_my > b_my * MOMENT_SLACK + 1.0:
        reasons.append("主梁弯矩峰值上升")
    if c_ux > b_ux + TOWER_SLACK_M and c_ux > b_ux * 1.05:
        reasons.append("塔顶顺桥向偏位增大")
    return {
        "accepted": not reasons,
        "reasons": reasons,
        "sf_before": b_sf,
        "sf_after": c_sf,
        "my_before": b_my,
        "my_after": c_my,
        "ux_before": b_ux,
        "ux_after": c_ux,
    }


def run_opt_round(model: Any, baseline: Any, out_dir: str,
                  fpu: float = CABLE_FPU_DEFAULT) -> dict[str, Any]:
    """跑一轮并返回可放进计算书的记录。不修改传入的模型。"""
    scale = propose_area_scale(baseline, model, fpu)
    if scale is None:
        return {
            "status": "skipped",
            "scale": None,
            "reason": (f"最小安全系数已不高于 {TARGET_SF:.2f}，"
                       "再减面积会贴上限，本轮不重算。"),
            "rows": [],
        }
    cand_model = retarget_model(model, scale)
    from solver_3d import solve

    candidate = solve(cand_model, out_dir=out_dir, dat_name="cable_opt.dat")
    verdict = judge(baseline, candidate, model, cand_model, fpu)
    status = "accepted" if verdict["accepted"] else "rejected"
    reason = ("采纳。面积按安全系数收一档，索力改由最小弯曲能确定，"
              "主梁弯矩和塔顶偏位没有变差。"
              if verdict["accepted"] else
              "退回原方案。" + "；".join(verdict["reasons"]) + "。")
    rows = [
        ["拉索面积乘子", "1.00", f"{scale:.2f}", f"{SCALE_MIN:.2f}~1.00"],
        ["最小安全系数", f"{verdict['sf_before']:.2f}", f"{verdict['sf_after']:.2f}",
         f"≥ {CABLE_SAFETY_MIN:.1f}"],
        ["主梁弯矩峰值 (kN·m)", f"{verdict['my_before'] / 1e3:.1f}",
         f"{verdict['my_after'] / 1e3:.1f}", "不大于基准的 2%"],
        ["塔顶顺桥向偏位 (mm)", f"{verdict['ux_before'] * 1e3:.1f}",
         f"{verdict['ux_after'] * 1e3:.1f}", f"不多于基准 {TOWER_SLACK_M * 1e3:.0f} mm"],
    ]
    return {
        "status": status,
        "scale": scale,
        "reason": reason,
        "rows": rows,
        "candidate_dat": candidate.dat_path,
    }


def opt_table(opt: dict[str, Any]) -> dict[str, Any] | None:
    if not opt or not opt.get("rows"):
        return None
    title = {"accepted": "采纳的一轮优化", "rejected": "未采纳的一轮优化"}.get(
        opt.get("status", ""), "一轮优化")
    return {
        "title": title,
        "note": "第二列是当前方案，第三列是面积调整后由最小弯曲能重算的结果。"
                "面积乘子写在第二份 .dat 的拉索实常数里，目标索力为 0，"
                "METHOD=MINENERGY。限值是这一轮的接受条件，不是新的规范条款。",
        "headers": ["项目", "基准", "重算", "接受条件"],
        "rows": opt["rows"],
    }


def _safety_ratios(res: Any, model: Any, fpu: float) -> list[float]:
    out = []
    for c in getattr(res, "cables", None) or []:
        t = float(c.get("T") or 0.0)
        if t <= 0:
            continue
        area = _elem_area_m2(model, c.get("elem"))
        if not area:
            continue
        out.append(area * fpu / t)
    return out


def _deck_my_max(res: Any, model: Any) -> float:
    nodes = {n.id: n for n in model.nodes}
    elem = {e.id: e for e in model.elements}
    worst = 0.0
    for (eid, _end), v in (getattr(res, "girder_ends", None) or {}).items():
        e = elem.get(eid)
        if e is None:
            continue
        a, b = nodes.get(e.n1), nodes.get(e.n2)
        if a is None or b is None:
            continue
        dx, dy, dz = b.x - a.x, b.y - a.y, b.z - a.z
        if abs(dx) + 1e-9 < max(abs(dy), abs(dz)):
            continue
        worst = max(worst, abs(float(v.get("My") or 0.0)))
    return worst


def _tower_ux_max(res: Any, model: Any) -> float:
    from monitor_plan import _tower_tops

    nodes = {n.id: n for n in model.nodes}
    worst = 0.0
    final = getattr(res, "final_disp", None) or {}
    disp = getattr(res, "disp", None) or {}
    for _label, nid, _h in _tower_tops(model, nodes):
        if (nid, 1) in final:
            u = float(final[(nid, 1)])
        elif (nid, 1) in disp:
            lo, hi = disp[(nid, 1)]
            u = lo if abs(lo) >= abs(hi) else hi
        else:
            continue
        worst = max(worst, abs(u))
    return worst
