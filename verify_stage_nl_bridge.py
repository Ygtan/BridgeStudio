"""缩小的悬臂斜拉桥：非线性阶段必须命中各阶段目标索力。

这是把界面从线性阶段换成非线性阶段之前的门槛。线性结果只作对照，
两种位移不能相加，本脚本不要求它们相等。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import sys
from pathlib import Path

from cable_stayed_3d import build_cable_stayed_3d
from fem_eigen_3d import solve_3d
from fem_eigen_stage_nl import solve_stages_nonlinear

PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<32} {detail}")


def main() -> int:
    print("=" * 74)
    print("缩小悬臂斜拉桥：非线性阶段索力")
    print("=" * 74)
    model, info = build_cable_stayed_3d(
        main_span=40.0, side_span=16.0, tower_height=12.0,
        n_cable=2, panel=8.0, q_deck=20e3, q2_deck=0.0,
        staged=True, retune=True, title="缩小悬臂斜拉桥")
    print(f"节点 {len(model.nodes)}  单元 {len(model.elements)}  "
          f"阶段 {len(model.stages)}  索 {info['n_cable_actual']}")

    out = Path("out") / "verify_stage_nl_bridge"
    nl = solve_stages_nonlinear(model, out, dat_name="bridge_nl.dat", timeout=600)
    check("非线性各阶段收敛",
          len(nl) == len(model.stages) and all(s["converged"] for s in nl),
          f"{len(nl)} 阶段")

    worst = 0.0
    n_tgt = 0
    for stage, row in zip(model.stages, nl):
        for eid, target in stage.tensions.items():
            got = row["cables"].get(eid, {}).get("T")
            n_tgt += 1
            if got is None or target <= 0:
                worst = 1.0
                continue
            worst = max(worst, abs(got / target - 1.0))
    check("张拉阶段索力相对目标 < 1%",
          n_tgt > 0 and worst < 0.01, f"{n_tgt} 根次，最大相对差 {worst:.3e}")

    lin = solve_3d(model, out / "linear", dat_name="bridge_lin.dat")
    last = model.stages[-1].id
    lin_miss = 0
    lin_n = 0
    for eid, target in model.stages[-1].tensions.items():
        rec = lin["stage_cables"].get((last, eid))
        lin_n += 1
        if not rec or abs(rec.get("T", 0.0) / target - 1.0) >= 0.01:
            lin_miss += 1
    check("线性末阶段二次调索也命中目标",
          lin_n > 0 and lin_miss == 0, f"{lin_n - lin_miss}/{lin_n}")

    # 线形对照：线性末态位移是增量累加，非线性末态是全量平衡。只报告，不判等。
    uz = []
    nu = nl[-1]["u"]
    for (node, dof), u in lin["final_disp"].items():
        if dof != 3 or (node, 3) not in nu:
            continue
        uz.append(abs(u - nu[(node, 3)]))
    if uz:
        print(f"  末态竖向位移差：最大 {max(uz) * 1e3:.2f} mm，"
              f"平均 {sum(uz) / len(uz) * 1e3:.2f} mm（两种列式，预期不必相同）")

    # 同一座桥关掉徐变曲线。第二段是第一次龄期增量，先浇的梁应该已经变位。
    bare = model.model_copy(deep=True)
    bare.creep_phi0 = 0.0
    nl_bare = solve_stages_nonlinear(
        bare, out, dat_name="bridge_nl_nocreep.dat", timeout=600)
    u_c = nl[1]["u"]
    u_b = nl_bare[1]["u"]
    node = max((n for (n, d) in u_b if d == 3),
               key=lambda n: abs(u_b.get((n, 3), 0.0)))
    uc, ub = u_c.get((node, 3), 0.0), u_b.get((node, 3), 0.0)
    rel = abs(uc - ub) / max(abs(ub), 1e-9)
    check("先浇梁在下一段发生徐变",
          nl_bare[1]["converged"] and abs(ub) > 1e-6 and rel > 0.02,
          f"节点 {node}  uz 有徐变 {uc:.6e}  无徐变 {ub:.6e}  相对差 {rel:.3f}")

    code = build_cable_stayed_3d(
        main_span=40.0, side_span=16.0, tower_height=12.0,
        n_cable=2, panel=8.0, q_deck=20e3, q2_deck=0.0,
        staged=True, retune=True, title="规范徐变悬臂斜拉桥",
        creep_rh=55.0, creep_h=200.0, creep_fcm=48.0)[0]
    code_dat = code.to_dat(compact=False)
    nl_code = solve_stages_nonlinear(
        code, out, dat_name="bridge_nl_jtg.dat", timeout=600)
    u_j = nl_code[1]["u"]
    uj = u_j.get((node, 3), 0.0)
    rel_j = abs(uj - uc) / max(abs(uc), 1e-9)
    worst_j = 0.0
    for stage, row in zip(code.stages, nl_code):
        for eid, target in stage.tensions.items():
            got = row["cables"].get(eid, {}).get("T")
            if got is None or target <= 0:
                worst_j = 1.0
                continue
            worst_j = max(worst_j, abs(got / target - 1.0))
    check("规范湿度曲线改变先浇梁位移，索力仍命中",
          nl_code[1]["converged"]
          and code.creep_phi0 == 0.0
          and "PHI0=" not in code_dat and "RH=55" in code_dat
          and rel_j > 0.005 and worst_j < 0.01,
          f"节点 {node}  规范 {uj:.6e}  φ0=2 {uc:.6e}  相对差 {rel_j:.3f}"
          f"  索力差 {worst_j:.3e}")

    from fem_eigen_stage_nl import as_solve_result
    res = as_solve_result(model, nl, dat_path=out / "bridge_nl.dat")
    sched = res.tables(model).get("stage_schedule")
    got_ages = [row[1] for row in sched["rows"]] if sched else []
    want_ages = [s.time for s in model.stages]
    from export_html import write_calculation_html
    html_path = write_calculation_html(
        res, model, out / "bridge.html", project=model.title)
    html = html_path.read_text(encoding="utf-8")
    check("HTML 计算书含龄期表",
          "施工阶段龄期" in html and "7.0" in html and "35.0" in html
          and "简化曲线" in html,
          html_path.name)
    code_res = as_solve_result(code, nl_code, dat_path=out / "bridge_nl_jtg.dat")
    code_note = code_res.tables(code).get("stage_schedule", {}).get("note", "")
    check("计算书写明本次用了规范徐变和收缩",
          "φ0=2.727" in code_note and "附录 C" in code_note
          and "非线性阶段" in code_note,
          code_note[-48:])

    check("计算书列出每段龄期",
          sched is not None
          and got_ages == [7.0, 14.0, 21.0, 28.0, 35.0]
          and got_ages == want_ages,
          " ".join(str(a) for a in got_ages))

    check("结果可送进界面",
          res.kind == "grillage3d" and res.converged
          and len(res.stage_cables) >= info["n_cable_actual"],
          f"索记录 {len(res.stage_cables)}")

    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print(f"\n  {n_pass}/{len(PASS)} 通过")
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
