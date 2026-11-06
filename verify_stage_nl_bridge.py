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

    from fem_eigen_stage_nl import as_solve_result
    res = as_solve_result(model, nl, dat_path=out / "bridge_nl.dat")
    check("结果可送进界面",
          res.kind == "grillage3d" and res.converged
          and len(res.stage_cables) >= info["n_cable_actual"],
          f"索记录 {len(res.stage_cables)}")

    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print(f"\n  {n_pass}/{len(PASS)} 通过")
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
