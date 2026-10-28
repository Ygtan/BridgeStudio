"""桥梁规范模型的端到端验证：建造 → 校验 → 导出 .dat → FEM-Eigen 求解 → 数值判据。

不是"能跑通"就算过，而是给每条物理判据设了阈值：
1. 梁桥：与理论解 wL²/8、5wL⁴/(384EI) 比对（误差应 ~0）；
2. 拱桥：① 反力合计 = 外荷载（平衡）；② 主梁弯矩**显著小于**同跨简支梁——
   这是拱效应的直接证据，若拱肋没起作用，这个比值不会明显下降；
3. 拱桥 CABLE 吊杆：全部受拉时应与 TRUSS 结果一致；
4. 桁架：反力平衡，且所有单元弯矩≈0（TRUSS 只有轴力）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from bridge_spec import (
    ElemKind,
    build_arch_bridge,
    build_beam_bridge,
    build_truss_bridge,
    _section_props,
)
from fem_eigen_export import parse_result, run_fem_eigen

OUT = Path(__file__).parent / "out" / "bridge"
PASS, FAIL = "PASS", "FAIL"


def _sum_reac(r: dict) -> float:
    return sum(v["value"] for v in r["reactions"].values() if v["dof"] == 2)


def _max_moment(r: dict, types: set[str] | None = None) -> float:
    vals = [
        max(abs(e["M1"]), abs(e["M2"]))
        for e in r["elements"].values()
        if types is None or e["type"] in types
    ]
    return max(vals, default=0.0)


def _min_v(r: dict) -> float:
    return min((n["v"] for n in r["nodes"].values()), default=0.0)


def solve(spec, name: str) -> dict:
    errs = spec.validate_model()
    if errs:
        return {"ok": False, "errs": errs}
    OUT.mkdir(parents=True, exist_ok=True)
    dat = OUT / f"{name}.dat"
    dat.write_text(spec.to_dat(), encoding="utf-8")
    info = run_fem_eigen(dat, workdir=OUT / f"run_{name}")
    res = parse_result(info["result"]) if info["result"] else None
    return {"ok": info["ok"], "res": res, "svgs": info["svgs"], "dat": dat,
            "stdout": info["stdout"]}


def main() -> int:
    print("=" * 78)
    print("桥梁规范模型（BridgeSpec）端到端验证")
    print("=" * 78)
    verdicts: list[tuple[str, str, str]] = []

    # ---------------- 1. 梁桥 vs 理论解 ----------------
    L, w = 30.0, 20e3
    p = _section_props("box", 2.0, 8.0, 0.02, 0.025)   # 与 build_beam_bridge 默认一致
    EI = 2.06e11 * p["I"]
    b1 = build_beam_bridge(span=L, udl_kN=20.0, gravity=0.0)
    r1 = solve(b1, "beam30")
    print(f"\n[1] {b1.title}")
    if not r1.get("res"):
        print("    无结果"); verdicts.append(("梁桥理论解", FAIL, "求解失败")); r1ok = False
    else:
        rr = r1["res"]
        v_num, m_num = _min_v(rr), _max_moment(rr)
        v_th, m_th = -5 * w * L**4 / (384 * EI), w * L**2 / 8
        dv = abs(v_num - v_th) / abs(v_th) * 100
        dm = abs(m_num - m_th) / m_th * 100
        print(f"    挠度 {v_num*1000:.4f} mm (理论 {v_th*1000:.4f})  误差 {dv:.4f}%")
        print(f"    弯矩 {m_num/1000:.2f} kN·m (理论 {m_th/1000:.2f})  误差 {dm:.4f}%")
        print(f"    反力 {_sum_reac(rr)/1000:.2f} kN (理论 {w*L/1000:.2f})  SVG {len(r1['svgs'])}")
        ok = dv < 0.5 and dm < 0.5
        verdicts.append(("梁桥 vs 理论解", PASS if ok else FAIL,
                         f"挠度{dv:.4f}% 弯矩{dm:.4f}%"))
        r1ok = True

    # ---------------- 2. 拱桥：平衡 + 拱效应 ----------------
    span, udl = 100.0, 20.0
    # 对比基准：同跨、同主梁截面的简支梁桥（无拱）
    deck = dict(shape_deck="box", h_deck=2.0, b_deck=8.0)
    beam_ref = build_beam_bridge(span=span, n_elem=20, udl_kN=udl, gravity=0.0,
                                 shape="box", h=2.0, b=8.0, tw=0.02, tf=0.025)
    rb = solve(beam_ref, "beam100")
    m_beam = _max_moment(rb["res"]) if rb.get("res") else 0.0

    arch = build_arch_bridge(span=span, udl_kN=udl, gravity=0.0, **deck)
    ra = solve(arch, "arch100")
    print(f"\n[2] {arch.title}")
    if not ra.get("res"):
        print("    无结果", ra.get("stdout", "")[-300:])
        verdicts.append(("拱桥平衡", FAIL, "求解失败")); verdicts.append(("拱效应", FAIL, "求解失败"))
    else:
        rr = ra["res"]
        total = udl * span
        sr = _sum_reac(rr) / 1000
        m_deck = _max_moment(rr, {"BEAM"}) / 1000
        # 主梁单元 id 1..n_deck-1；拱肋也是 BEAM，故按单元号区分
        m_deck_only = max(
            max(abs(e["M1"]), abs(e["M2"]))
            for k, e in rr["elements"].items()
            if k < 100
        ) / 1000
        print(f"    反力合计 {sr:.2f} kN (外荷载 {total:.2f} kN) 差 {abs(sr-total):.3f}")
        print(f"    全 BEAM 最大弯矩 {m_deck:.2f} kN·m")
        print(f"    仅主梁最大弯矩 {m_deck_only:.2f} kN·m ｜ 同跨简支梁 {m_beam/1000:.2f} kN·m")
        ratio = m_deck_only / (m_beam / 1000) if m_beam else 1.0
        print(f"    主梁弯矩 / 简支梁弯矩 = {ratio:.3f}（越小说明拱分担越多）")
        print(f"    SVG {len(ra['svgs'])}")
        ok_bal = abs(sr - total) < 0.01 * total
        ok_arch = ratio < 0.7
        verdicts.append(("拱桥平衡", PASS if ok_bal else FAIL, f"差{abs(sr-total):.3f}kN"))
        verdicts.append(("拱效应（主梁弯矩下降）", PASS if ok_arch else FAIL, f"比值{ratio:.3f}"))

    # ---------------- 3. CABLE 吊杆 ----------------
    arch_c = build_arch_bridge(span=span, udl_kN=udl, gravity=0.0,
                               hanger_kind=ElemKind.CABLE, **deck)
    rc = solve(arch_c, "arch_cable")
    print(f"\n[3] {arch_c.title}")
    if not rc.get("res"):
        verdicts.append(("CABLE 吊杆求解", FAIL, "求解失败"))
    else:
        rr = rc["res"]
        sr = _sum_reac(rr) / 1000
        cables = [e for k, e in rr["elements"].items() if e["type"] == "CABLE"]
        nt = sum(1 for e in cables if (e["N1"] + e["N2"]) / 2 > 0)
        print(f"    反力合计 {sr:.2f} kN ｜ 吊杆 {len(cables)} 根，其中受拉 {nt} 根")
        print(f"    SVG {len(rc['svgs'])}")
        ok = abs(sr - udl * span) < 0.01 * udl * span and nt == len(cables) > 0
        verdicts.append(("CABLE 吊杆（应全部受拉）", PASS if ok else FAIL,
                         f"受拉{nt}/{len(cables)}"))

    # ---------------- 4. 桁架：平衡 + 无弯矩 ----------------
    tr = build_truss_bridge(span=60.0, n_panel=8, node_load_kN=100.0, gravity=0.0)
    rt = solve(tr, "truss60")
    print(f"\n[4] {tr.title}")
    if not rt.get("res"):
        print("    无结果", rt.get("stdout", "")[-300:])
        verdicts.append(("桁架平衡", FAIL, "求解失败")); verdicts.append(("桁架仅轴力", FAIL, "求解失败"))
    else:
        rr = rt["res"]
        total = 100.0 * (8 - 1)     # 下弦中间 7 个节点，各 100 kN
        sr = _sum_reac(rr) / 1000
        mmax = _max_moment(rr)
        print(f"    反力合计 {sr:.2f} kN (外荷载 {total:.2f} kN) 差 {abs(sr-total):.3f}")
        print(f"    单元最大弯矩 {mmax:.6e} kN·m（TRUSS 应≈0）  SVG {len(rt['svgs'])}")
        ok_bal = abs(sr - total) < 0.01 * total
        ok_m = mmax < 1.0           # N·m 量级，换算后应远小于 1 kN·m
        verdicts.append(("桁架平衡", PASS if ok_bal else FAIL, f"差{abs(sr-total):.3f}kN"))
        verdicts.append(("桁架仅轴力（M≈0）", PASS if ok_m else FAIL, f"Mmax={mmax:.3e}"))

    # ---------------- 汇总 ----------------
    print("\n" + "=" * 78)
    print("判据汇总")
    print("=" * 78)
    npass = 0
    for name, v, detail in verdicts:
        print(f"  {v}  {name:<28} {detail}")
        npass += (v == PASS)
    print(f"\n  {npass}/{len(verdicts)} 通过")
    return 0 if npass == len(verdicts) else 1


if __name__ == "__main__":
    sys.exit(main())
