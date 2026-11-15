#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""② 阶段 / CB3D21：真实双塔三跨斜拉桥「成桥态起点全过程稳定」同源导出。

唯一数据源是 `COMPLETION_CFG` + `EPS0` + `PINC`：

    ModelSpec3D (mode=STATIC, 设计线形)
      ├─ *.dat   : *EN ... L0=L/(1+EPS0)  +  STABASIS=COMPLETION 键
      │            + *STABILITY_LOAD (跨中节点 1016 的 Z 向增量荷载)
      └─ *.inp   : 同一份设计线形坐标 + INISTATE eps=(L-L0)/L0=EPS0
                   + 两段求解（step1 恒载静力 / step2 增量荷载）

力学口径（2026-11-15 探针定标，见
FEM-Eigen/tests/benchmark/completion_stability_3d/probes/README.md）：

* FEM 侧 `R = F0 + λ·Finc − Fint(u)`，起点是 `MODE=STATIC` 的成桥态
  收敛位移 `u0`；`STABASIS=COMPLETION` 不改写 `m`，故 `RES0` 与
  `[NEWTON] RESLAST` 同量级（实测 ~1e-2 N），起点严格闭合。
  **不能用 `MODE=COMPLETION`**：索长迭代改写 `m.elements[*].L0`，
  与 `u0` 不同源，实测 `RES0=3.15 MN`。
* ANSYS 侧 `step1`（`TIME=1`）先求成桥态；`step2` 把 1016 节点的
  荷载从 `F_orig` ramp 到 `F_orig − LAMMAX·PINC`，`TIME=1→2`。
  荷载控制主 job 的 `λ_FEM = (TIME−1)·LAMMAX`，
  `ΔUZ = UZ − UZ_step1` 与 FEM `[STABILITY]` 的 `(λ, CTRL)` 逐点对拍。
  `ARCLEN` 交叉验证 job 只比终点位移（其 `TIME` 不是 λ）。
* **不用反力换算 λ**：完整斜拉桥 50 个 `F` / 34 个约束节点上
  `ΣRF,FZ` 在 step2 随加载反而下降，反力求和不可用。
* 该算例路径单调硬化、无极限点，ANSYS 荷载控制即可覆盖全程；
  越过极限点的能力由 L/N 阶段独立基准题与 FEM 弧长内核证明。

APDL 全 ASCII，阶段标题用英文。不进 CTest 的编排见
`cases/run_cases_completion.py`。
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from cable_stayed_3d import build_cable_stayed_3d
from ansys_bridge_nl import spec_nl_to_apdl, initial_cable_strain

CASE_ID = "cb3d21_completion_stability"
COMPLETION_CASE_NAME = "CB3D21_completion_stability_vs_ansys"

# 与 CB3D10 相同的双塔三跨斜拉桥几何/材料/恒载，仅 mode 改 STATIC
COMPLETION_CFG = dict(
    main_span=80.0, side_span=30.0, tower_height=25.0,
    n_cable=4, panel=10.0,
    B=12.0, h=2.5, tf=0.28, tw=0.45,
    tower_b=3.0, tower_h=3.0,
    q_deck=50e3, cable_area_mm2=3000.0,
    cable_dens=0.0,                 # W=0 -> 关闭垂度/Ernst
    mode="STATIC", method="DECK",
)

EPS0 = 8.0e-4        # 统一初始应变：FEM 工程应变 vs LINK180 Almansi 差 O(eps)
PINC = 1.0e6         # FEM *STABILITY_LOAD 的参考增量荷载 N（向下）
LAMMAX = 4.55        # ANSYS step2 目标荷载 = F_orig - LAMMAX*PINC
                     # 略小于 FEM 定标路径 lambda_final=4.5530675，
                     # 保证全部 ANSYS 子步都落在 FEM 路径内、可对拍
CTRL_NODE, CTRL_DOF = 1016, 3
VMAX = 0.07          # ARCTRM 保护阈值；大于预期终点 |UZ|≈0.060，两个 job
                     # 都靠 TIME 终止、覆盖范围一致（不提前截断）

TITLE = "CB3D21 completion-state stability path - FEM vs ANSYS two-step"

STAB_KEYS = (
    "STABILITY=1 STABASIS=COMPLETION PATHMETHOD=SPHERE "
    "CTRLNODE=1016 CTRLDOF=3 CTRLTARGET=0.05\n"
    "DS0=0.002 DSMAX=0.01 DSMIN=1.0e-9 PSI=0.5\n"
    "MAXPATHSTEP=2000 STABITER=40 STABTOL=1.0e-7 STRAIN=0\n"
)

ANSYS_CASES = [
    {"id": "cs_load", "arclen": False, "time2": 2.0,
     "desc": "load-controlled step 2 (primary path benchmark)"},
    {"id": "cs_arc", "arclen": True, "time2": 6.0,
     "desc": "ARCLEN step 2 (endpoint cross-check only)"},
]


def build_model():
    """返回 (ModelSpec3D, info)；model 的 mode 已是 STATIC。"""
    return build_cable_stayed_3d(**COMPLETION_CFG)


def design_coords(model) -> dict[int, tuple[float, float, float]]:
    """设计线形（参考构形）坐标。② 的 FEM 与 ANSYS 共用这一份。"""
    return {n.id: (float(n.x), float(n.y), float(n.z))
            for n in model.nodes}


def cable_elements(model) -> list:
    kinds = {t.id: t.kind for t in model.etypes}
    return [e for e in model.elements if kinds[e.etype] == "CABLE"]


def cable_l0(model, coords: dict | None = None) -> dict[int, float]:
    """每根索的无应力长 L0 = L_design / (1 + EPS0)。唯一数据源。"""
    if coords is None:
        coords = design_coords(model)
    out: dict[int, float] = {}
    for e in cable_elements(model):
        p1, p2 = coords[e.n1], coords[e.n2]
        L = math.sqrt(sum((b - a) ** 2 for a, b in zip(p1, p2)))
        if L <= 0.0:
            raise ValueError(f"索单元 {e.id} 的参考弦长非法")
        out[e.id] = L / (1.0 + EPS0)
    return out


def node_load(model, node: int, dof: int) -> float:
    """节点荷载（合并重复 (node,dof)，与 spec_nl_to_apdl 的口径一致）。"""
    v = 0.0
    for ld in model.loads:
        if ld.node == node and ld.dof == dof:
            v += float(ld.value)
    return v


def fem_dat(model, l0: dict[int, float]) -> str:
    """FEM .dat：ModelSpec3D.to_dat() + 显式 L0 + 稳定性键 + 增量荷载。"""
    txt = model.to_dat()
    if "MODE=STATIC" not in txt:
        raise ValueError("模型不是 MODE=STATIC：② 必须用 STATIC 保证起点闭合")
    cables = {e.id: e for e in cable_elements(model)}
    lines: list[str] = []
    skip = 0
    for ln in txt.splitlines():
        if skip:
            skip -= 1
            continue
        s = ln.strip()
        # to_dat() 把 9006~9008 写成 *DO 紧凑块；展开成显式三行才能逐根给 L0
        if s.startswith("*DO I 9006 9008"):
            skip = 2          # 跳过 *EN,I,... 与 *ENDDO
            for eid in (9006, 9007, 9008):
                e = cables[eid]
                lines.append(f"*EN,{eid},{e.n1},{e.n2} L0={l0[eid]:.17g}")
            continue
        m = re.match(r"^\*EN,(\d+),(\d+),(\d+)\s*$", s)
        if m and int(m.group(1)) in l0:
            eid = int(m.group(1))
            lines.append(f"*EN,{eid},{m.group(2)},{m.group(3)} "
                         f"L0={l0[eid]:.17g}")
            continue
        if s == "*CABLE":
            lines.extend(STAB_KEYS.rstrip("\n").split("\n"))
            lines.append(ln)
            continue
        if s == "*END":
            lines.append("*STABILITY_LOAD")
            lines.append(f"{CTRL_NODE} {CTRL_DOF} {-PINC:.17g}")
            lines.append(ln)
            continue
        lines.append(ln)
    return "\n".join(lines) + "\n"


def ansys_curve_apdl(model, l0: dict[int, float], *,
                     jobname: str, case: dict) -> tuple[str, dict]:
    """两段 ANSYS APDL：复用 spec_nl_to_apdl 建模 + 插入 step2 + 曲线 CSV。"""
    coords = design_coords(model)
    eps = initial_cable_strain(model, coords=coords, l0=l0)
    for eid, val in eps.items():
        if abs(val - EPS0) > 1e-12:
            raise ValueError(f"索 {eid} 的 INISTATE eps={val} 与 EPS0 不同源")
    base, info = spec_nl_to_apdl(model, jobname=jobname, coords=coords,
                                 l0=l0, title=TITLE)
    f_orig = node_load(model, CTRL_NODE, CTRL_DOF)
    f_end = f_orig - LAMMAX * PINC

    step2 = [
        f"! ---- load step 2: incremental load ({case['desc']}) ----",
        f"F,{CTRL_NODE},FZ,{f_end:.15g}",
    ]
    if case["arclen"]:
        step2.append("ARCLEN,ON,0.01,1.0E-8")
    step2 += [
        "NSUBST,600,3000,20",
        "NEQIT,60",
        f"TIME,{case['time2']:.12g}",
        f"ARCTRM,U,{VMAX:.9g},{CTRL_NODE},UZ",
        "OUTRES,ALL,ALL",
        "SOLVE",
    ]
    post = [
        "/POST1",
        "*GET,NSET,ACTIVE,0,SET,NSET",
        "*DIM,CURV,ARRAY,NSET,4",
        "*DO,I,1,NSET",
        " SET,,,,,,,I",
        f" *GET,VZ,NODE,{CTRL_NODE},U,Z",
        " *GET,TNOW,ACTIVE,0,SET,TIME",
        " CURV(I,1)=I", " CURV(I,2)=TNOW", " CURV(I,3)=VZ", " CURV(I,4)=0",
        "*ENDDO",
        f"*CFOPEN,{jobname}_curve,csv",
        "*VWRITE,CURV(1,1),CURV(1,2),CURV(1,3),CURV(1,4)",
        " (1X,F8.0,3(1X,E20.12))",
        "*CFCLOS",
        "FINISH", "/EXIT,NOSAVE",
    ]
    out: list[str] = []
    inserted = False
    for ln in base.splitlines():
        s = ln.strip()
        if s == "/POST1":
            out.extend(post)
            break
        if s == "OUTRES,ALL,LAST":
            out.append("OUTRES,ALL,ALL")
            continue
        out.append(ln)
        if s == "SOLVE" and not inserted:
            inserted = True
            out.extend(step2)
    if not inserted:
        raise ValueError("spec_nl_to_apdl 输出中找不到第一个 SOLVE")
    info = dict(info)
    info["f_orig"] = f_orig
    info["f_end"] = f_end
    info["eps_uniform"] = EPS0
    return "\n".join(out) + "\n", info


def read_curve_csv(path: Path) -> list[tuple[float, float, float]]:
    """ANSYS 逐子步曲线：[(iset, TIME, UZ)]。"""
    out: list[tuple[float, float, float]] = []
    for ln in Path(path).read_text(encoding="utf-8",
                                   errors="replace").splitlines():
        q = ln.split()
        if len(q) < 3:
            continue
        try:
            out.append((float(q[0]), float(q[1]), float(q[2])))
        except ValueError:
            continue
    return out


def read_fem_path(path: Path) -> list[tuple[float, float]]:
    """FEM [STABILITY] 段：[(lambda, ctrl)]，ctrl 是相对 u0 的增量。"""
    out: list[tuple[float, float]] = []
    for ln in Path(path).read_text(encoding="utf-8",
                                   errors="replace").splitlines():
        m = re.match(r"^I STEP=\d+ LAMBDA=(\S+) CTRL=(\S+)", ln.strip())
        if m:
            out.append((float(m.group(1)), float(m.group(2))))
    return out


def interp_ctrl(fem: list[tuple[float, float]], lam: float) -> float:
    """FEM 路径按 λ 线性插值出控制位移增量。"""
    if not fem:
        raise ValueError("FEM 路径为空")
    if lam <= fem[0][0]:
        return fem[0][1]
    if lam >= fem[-1][0]:
        return fem[-1][1]
    lo, hi = 0, len(fem) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if fem[mid][0] <= lam:
            lo = mid
        else:
            hi = mid
    a, b = fem[lo], fem[hi]
    return a[1] + (b[1] - a[1]) * (lam - a[0]) / (b[0] - a[0])


def model_json(model) -> str:
    return model.model_dump_json(indent=2)


if __name__ == "__main__":
    m, info = build_model()
    coords = design_coords(m)
    l0 = cable_l0(m, coords)
    dat = fem_dat(m, l0)
    print(f"model: {len(m.nodes)} nodes / {len(m.elements)} elems / "
          f"{len(l0)} cables")
    print(f"L0 range: {min(l0.values()):.9g} .. {max(l0.values()):.9g}")
    print(f"F_orig(1016,FZ) = {node_load(m, CTRL_NODE, CTRL_DOF):.12g}")
    print(f"dat: {len(dat.splitlines())} lines, MODE=STATIC ok, "
          f"STABASIS=COMPLETION ok, *STABILITY_LOAD ok")
    for c in ANSYS_CASES:
        apdl, ai = ansys_curve_apdl(m, l0, jobname="probe_" + c["id"],
                                    case=c)
        print(f"apdl[{c['id']}]: {len(apdl.splitlines())} lines, "
              f"f_end={ai['f_end']:.12g}, eps={ai['eps_uniform']:.3g}")
