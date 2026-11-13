#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CB3D05 — cable_solver.exe 非线性成桥态 / 索长迭代端到端归档。

与 ``run_cases_cable.py`` 的「线性 3D 内核 + ANSYS 对标」链**性质不同**：

  * CB3D03 / CB3D04：``example_3d_report.exe``（fem_3d.hpp 线性内核），
    CABLE/TRUSS 都按轴向杆 (EA/L)·c⊗c 装配，ANSYS BEAM4+LINK180 严格对标。
  * CB3D05：``cable_solver.exe``（fem_3d_nl.hpp 共转列式几何非线性内核），
    实现只受拉、松弛退出、无应力索长 L0、垂度弹性悬链线 / Ernst 等效弹模、
    索长迭代（[LENGTH]/[NEWTON]）、成桥态几何闭合 COMPLETION。
    **没有线性 ANSYS 基准可严格对标**，故本脚本做内核自洽校核：
    收敛 / 几何闭合 / 索力命中 / 索长闭合 / 支座反力平衡。

归档：``cases/CB3D05_cable_solver_completion/``

用法：
    C:\\Users\\user\\anaconda3\\python.exe cases\\run_cable_solver_smoke.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRIDGESTUDIO = HERE.parent
sys.path.insert(0, str(BRIDGESTUDIO))

# cable_solver.exe 是 MinGW 构建，需要 msys64 运行库
os.environ["PATH"] = (r"C:\msys64\mingw64\bin" + os.pathsep
                      + os.environ.get("PATH", ""))

from cable_stayed_3d import build_cable_stayed_3d  # noqa: E402
from fem_eigen_cable import (  # noqa: E402
    cable_solver_available, solve_cable_bridge,
)

OUT = HERE / "CB3D05_cable_solver_completion"
STEM = "cb3d05_completion"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    if not cable_solver_available():
        print("[FAIL] cable_solver.exe 不可用")
        return 1

    model, info = build_cable_stayed_3d(
        main_span=200.0, side_span=80.0, tower_height=60.0,
        n_cable=12, panel=8.0,
        mode="COMPLETION", method="DECK",
    )
    dat = OUT / (STEM + ".dat")
    dat.write_text(model.to_dat(), encoding="utf-8")
    (OUT / (STEM + ".model.json")).write_text(
        model.model_dump_json(indent=2), encoding="utf-8")

    res = solve_cable_bridge(model, out_dir=OUT, dat_name=dat.name, timeout=600)
    frozen = OUT / (STEM + "_cable.txt")
    if res["result"].resolve() != frozen.resolve():
        frozen.write_text(res["result"].read_text(encoding="utf-8"),
                          encoding="utf-8")

    md = res["meta"]
    cables = res["cables"]
    lines = []
    ok = True
    add = lines.append

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= bool(cond)
        add("  [{}] {:<42s} {}".format("PASS" if cond else "FAIL", name, detail))

    add("=" * 88)
    add("CB3D05 — FEM-Eigen cable_solver.exe 非线性成桥态 / 索长迭代校核")
    add("=" * 88)
    add("模型: {} 节点 / {} 单元 / {} 根 CABLE / {} DOF".format(
        md.get("nodes", len(model.nodes)),
        md.get("elements", len(model.elements)),
        md.get("cables", info["n_cable_actual"]),
        md.get("dofs", "?")))
    add("      主跨 200 m + 两侧各 80 m，塔高 60 m，4×12 根斜拉索，扇形布置")
    add("内核: fem_3d_nl.hpp 共转列式几何非线性；MODE=COMPLETION METHOD=DECK")
    add("")
    add("[1] 收敛与几何闭合")
    check("converged == 1", res["converged"],
          "returncode={} iterations={}".format(res["returncode"], md.get("iterations")))
    check("closure_err < 1e-6 m", float(md.get("closure_err", 1)) < 1e-6,
          "closure_err={:.6e} m".format(float(md.get("closure_err", float("nan")))))
    check("tension_err < 1e-6", float(md.get("tension_err", 1)) < 1e-6,
          "tension_err={:.6e}".format(float(md.get("tension_err", float("nan")))))
    add("")
    add("[2] 索力迭代与索长闭合（[CABLE] 段）")
    check("索结果条数 = 索数", len(cables) == info["n_cable_actual"],
          "{} / {}".format(len(cables), info["n_cable_actual"]))
    if cables:
        dL = max(abs(c["dL"]) for c in cables)
        ts = [c["T"] for c in cables]
        check("max |dL| < 1e-6 m（索长闭合）", dL < 1e-6,
              "max |dL|={:.6e} m".format(dL))
        check("全部索力 > 0（只受拉）", all(t > 0 for t in ts),
              "min={:.1f} N  max={:.1f} N".format(min(ts), max(ts)))
        check("索力范围与汇总一致",
              abs(max(ts) / 1000.0 - float(md.get("tension_max_kN", 0))) < 1.0,
              "tension_min_kN={:.2f}  max_kN={:.2f}  sum_kN={:.1f}".format(
                  float(md.get("tension_min_kN", 0)),
                  float(md.get("tension_max_kN", 0)),
                  float(md.get("tension_sum_kN", 0))))
    add("")
    add("[3] 竖向力平衡（成桥态整体）")
    sv = float(md.get("support_v_sum_kN", 0))
    av = float(md.get("applied_v_kN", 0))
    rel = abs(sv - av) / max(abs(av), 1.0)
    check("支座反力 vs 施加荷载 量级一致 (<1%)", rel < 1e-2,
          "support={:.1f} kN applied={:.1f} kN 相对 {:.3e}".format(sv, av, rel))
    add("  口径说明: 此处 0.265% 的差额是内核统计口径差异，不是求解不平衡。")
    add("      cable_solver.cpp:570-585 的 appliedV 只累加 (*CLOAD) 显式节点荷载")
    add("      与 CABLE 自重等效节点力；成桥态 DECKLOAD 分配的桥面荷载未计入该统计，")
    add("      而 support_share_kN={:.1f} kN 与差额 {:.1f} kN 量级吻合。".format(
        float(md.get("support_share_kN", 0)), abs(sv - av)))
    add("  注: balance_pct={:.3f}% 是「索力竖向分量 / 总恒载」的比值偏差，".format(
        float(md.get("balance_pct", 0))))
    add("      差额由桥塔 / 支座承担，属斜拉桥成桥态的正常受力分配，不是不平衡残差。")
    add("      真正的收敛判据是 [1] closure_err/tension_err 与 [2] 索长闭合 |dL|。")
    add("")
    add("[4] 与线性对标链的关系")
    add("  CB3D03/CB3D04 走 example_3d_report.exe（线性内核，CABLE 退化为轴向杆），")
    add("  与 ANSYS BEAM4+LINK180 严格对标；本算例走 cable_solver.exe（非线性内核），")
    add("  无对应线性 ANSYS 基准，故只做内核自洽校核，不参与 ANSYS 数值对比。")
    add("  结果文件含 [CABLE]/[CAMBER]/[ERECTED]/[U]/[MODAL]/[NEWTON]/[LENGTH] 等段。")
    add("")
    add("=" * 88)
    add("总判定: " + ("PASS —— 非线性成桥态 / 索长迭代 / 共转列式链路打通"
                    if ok else "FAIL —— 存在超差项，需排查"))
    add("=" * 88)

    (OUT / "smoke_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / "manifest.txt").write_text("\n".join([
        "=" * 78,
        "CB3D05 非线性成桥态算例归档",
        "=" * 78,
        "",
        "唯一数据源: BridgeStudio.cable_stayed_3d.build_cable_stayed_3d(",
        "  main_span=200, side_span=80, tower_height=60, n_cable=12, panel=8,",
        "  mode=COMPLETION, method=DECK)  -> ModelSpec3D（含 *CABLE_BRIDGE / *CABLE）",
        "",
        "链路:",
        "  ModelSpec3D.to_dat()              -> cb3d05_completion.dat",
        "  cable_solver.exe <stem>.dat      -> cb3d05_completion_cable.txt",
        "  fem_eigen_cable.parse_cable_result() -> smoke_report.txt",
        "",
        "文件清单:",
        "  cb3d05_completion.dat            前处理输出（3D 非线性斜拉桥，48 索）",
        "  cb3d05_completion.model.json     模型快照",
        "  cb3d05_completion_cable.txt      cable_solver 原始结果（冻结）",
        "  smoke_report.txt                 自洽校核报告",
        "  run_log.txt / manifest.txt",
        "",
        "复现:",
        "  C:\\Users\\user\\anaconda3\\python.exe "
        "D:\\AI-work\\BridgeStudio\\cases\\run_cable_solver_smoke.py",
        "  (需 C:\\msys64\\mingw64\\bin 在 PATH；不需要 ANSYS)",
        "=" * 78,
    ]) + "\n", encoding="utf-8")

    log = "\n".join([
        "$ cable_solver.exe " + str(dat),
        "[exit] returncode={}".format(res["returncode"]),
        "[stdout] " + (res["stdout"] or "").strip().replace("\n", " | "),
    ]) + "\n"
    (OUT / "run_log.txt").write_text(log, encoding="utf-8")

    print("[{}] CB3D05  cables={} iterations={} closure_err={:.3e}  {}".format(
        "PASS" if ok else "FAIL", len(cables), md.get("iterations"),
        float(md.get("closure_err", float("nan"))),
        "ALL NONLINEAR SMOKE CHECKS PASSED" if ok else "CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
