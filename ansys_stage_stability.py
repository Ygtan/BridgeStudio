#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""③b / CB3D23：以**施工阶段中间态**为起点的全过程稳定 FEM vs ANSYS 同源导出。

为什么需要这个算例
------------------
③a 收口后，`[STABILITY]` 的起点仍只有两种：参考构形 `u=0`，或**成桥态**
`u0`。`B类后续项接入规格.md` §2.5 明确登记「施工阶段中间态全过程稳定」
未闭环——CB3D13 的 `[ERECTION*]` 只是解析索多边形找形，不提供每个施工
阶段中间态的 FEM 平衡态与一致切线刚度；第 24 项 CTest 证明的也只是
`[STABILITY]` 与施工阶段段**并列输出互不污染**，不是「可在中间态上做
全过程稳定分析」。

本算例补的正是这一层：

* `cable_solver.exe` 新增 `STABASIS=STAGE` + `STAGEID=<id>` opt-in，
  直接调用 `fem_3d_nl.hpp::solveModel3DStagesNonlinear()`，取指定施工阶段
  的**共转列式全量平衡位移 `u_stage`** 与该阶段的同源模型
  `NLStageResult3D::model`（只含已激活单元、累计阶段荷载、当前 `L0`）。
* 弧长法起点 `u0 = u_stage != 0`，`F0` = 该阶段累计恒定荷载，
  `Finc` 只来自 `*STABILITY_LOAD`；一致切线刚度由该阶段模型装配。
* 段头 `BASIS=stage_state`、`STAGE=<id> NELEM=<n> NDOF=<n>`，
  `[STAGES]` 逐阶段留痕，证明选中的确实是**中间态**（前后都还有阶段），
  而不是最后一个成桥阶段。

唯一数据源
----------
本模块的一个常量表同时生成：

    *.dat : 3 个施工阶段 + STABASIS=STAGE/STAGEID=2 + *STABILITY_LOAD
    *.inp : 阶段 2 的**同一份**几何 / 材料 / 约束 / 累计荷载
            → ANSYS LINK180 + NLGEOM 两段求解

λ 口径沿用 ② 定标：`step1 TIME=1` 求阶段 2 平衡态；`step2` 的终点荷载
设为 `F_orig + LAMMAX·PINC`、`TIME = 1 + LAMMAX`，故 **λ = TIME − 1**，
不靠反力求和。控制量是节点 2 的 `UY`，按 `ΔUY = UY − UY_step1`
作单调自变量插值 λ 逐点对拍。

构型说明
--------
4 节点 / 3 根 TRUSS 的浅拱，三阶段：① 激活两侧杆并施加一期恒载，
② 合拢水平中段杆（**取此中间态**），③ 施加二期恒载。拱顶节点 2/3
在 FEM 与 ANSYS 两侧同源地加 X 向导向约束，消除 3 杆体系零轴力时的
一阶运动机构（否则 ANSYS 稀疏求解器报 small pivot，FEM 虽能侥幸收敛
但物理上不可靠）。阶段 2 有 3 个激活单元、2 条累计阶段荷载，
且 `CTRL0 != 0`。这是研究构型，不是实际桥梁尺寸；目的只是让「起点为
施工阶段中间态」这件事可被 FEM 与 ANSYS 双向验证。③a 的 CB3D22 已
单独覆盖「越过极限点 / 下降段」，本项不重复。

APDL 全 ASCII，标题用英文。编排见 `cases/run_cases_stage_state.py`。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

CASE_ID = "cb3d23_stage_state"
CASE_NAME = "CB3D23_stage_state_vs_ansys"
TITLE = "CB3D23 construction-stage intermediate state stability - FEM vs ANSYS two-step"

# ---- 唯一数据源：几何 / 材料 / 截面 / 约束 -------------------------------
E_MODULUS = 2.1e11          # Pa
AREA = 0.001                # m^2
NODES: list[tuple[int, float, float, float]] = [
    (1, -2.0, 0.0, 0.0),
    (2, -0.7, 0.35, 0.0),
    (3, 0.7, 0.35, 0.0),
    (4, 2.0, 0.0, 0.0),
]
ELEMENTS: list[tuple[int, int, int]] = [
    (1, 1, 2),
    (2, 2, 3),
    (3, 3, 4),
]
# 3 杆浅拱在零轴力下是一阶运动机构（节点 2/3 共 4 个平动自由度、
# 3 个杆长约束）。两侧同源地给拱顶节点加 X 向导向约束后成为几何不变
# 体系，ANSYS 稀疏求解器不再报 small pivot。等价于施工期的侧向导向，
# 不是为了让曲线好看而加的弹簧。
FIXED_X = (2, 3)
# FEM dof 3 = Z；节点 1/4 全约束，节点 2/3 约束面外平动。
FIXED_ALL = (1, 4)
FIXED_Z = (2, 3)

# ---- 施工阶段：激活单元 + 该阶段新增的累计节点荷载 (dof 2 = Y) ----------
STAGE_ACTIVE: dict[int, list[int]] = {1: [1, 3], 2: [2], 3: []}
STAGE_LOADS: dict[int, dict[int, float]] = {
    1: {2: -10000.0, 3: -10000.0},
    2: {},
    3: {2: -5000.0, 3: -5000.0},
}
STAGE_ID = 2                # 中间态：合拢后、二期恒载前
PINC = -20000.0             # *STABILITY_LOAD 每节点的增量荷载 N（FY，向下）
INC_NODES = (2, 3)
CTRL_NODE, CTRL_DOF = 2, 2  # FEM dof 2 = Y
CTRL_COMP = "UY"
CTRL_F = "FY"
CTRLTARGET = 0.005          # 小荷载档：轴向应变约 2e-4，抑制应变度量差异

STAB_KEYS = (
    "STABILITY=1 STABASIS=STAGE STAGEID=2 PATHMETHOD=SPHERE "
    "CTRLNODE=2 CTRLDOF=2 CTRLTARGET=0.005\n"
    "DS0=0.01 DSMAX=0.02 DSMIN=1.0e-8 PSI=1.0\n"
    "MAXPATHSTEP=800 STABITER=40 STABTOL=1.0e-9 STRAIN=0\n"
    "NLSTEPS=3 NEWTON=30\n"
)

# 两个 job 都令 TIME2 = 1 + LAMMAX，终点荷载 = F_orig + LAMMAX*PINC，
# 故 λ = TIME - 1。st_load 用荷载控制校核上升段；st_arc 用 ARCLEN
# 证明同一条路径也可由弧长法跟踪（不依赖荷载控制）。
ANSYS_CASES = [
    {"id": "st_load", "arclen": False, "lammax": 2.7, "time2": 3.7,
     "desc": "load-controlled step 2 from stage-2 equilibrium"},
    {"id": "st_arc", "arclen": True, "lammax": 2.7, "time2": 3.7,
     "desc": "ARCLEN step 2 from stage-2 equilibrium"},
]


def stage_constant_loads() -> dict[int, float]:
    """阶段 STAGE_ID 的**累计**节点荷载（F0 的唯一来源）。"""
    acc: dict[int, float] = {}
    for sid in sorted(STAGE_LOADS):
        if sid > STAGE_ID:
            break
        for node, val in STAGE_LOADS[sid].items():
            acc[node] = acc.get(node, 0.0) + val
    return acc


def stage_active_elements() -> list[int]:
    """阶段 STAGE_ID 结束时已激活的单元（ANSYS 只建这些单元）。"""
    out: list[int] = []
    for sid in sorted(STAGE_ACTIVE):
        if sid > STAGE_ID:
            break
        out.extend(STAGE_ACTIVE[sid])
    return sorted(out)


def fem_dat() -> str:
    """FEM `.dat`：几何 + 3 个施工阶段 + STABASIS=STAGE + 增量荷载。"""
    L: list[str] = [
        "# CB3D23 ③b: construction-stage intermediate state stability (STABASIS=STAGE)",
        "*NODE",
    ]
    for nid, x, y, z in NODES:
        L.append(f"{nid} {x:.17g} {y:.17g} {z:.17g}")
    L += ["*ET", "1 TRUSS", "*MP", f"1 E={E_MODULUS:.17g}",
          "*R", f"1 A={AREA:.17g}", "*TYPE,1", "*MAT,1", "*REAL,1"]
    for eid, n1, n2 in ELEMENTS:
        L.append(f"*EN,{eid},{n1},{n2}")
    L.append("*BOUNDARY")
    for nid in FIXED_ALL:
        L.append(f"{nid} 1-3 0.0")
    for nid in FIXED_Z:
        L.append(f"{nid} 3 0.0")
    for nid in FIXED_X:
        L.append(f"{nid} 1 0.0")
    L.append("*STAGE")
    for sid in sorted(STAGE_ACTIVE):
        active = STAGE_ACTIVE[sid]
        L.append(f"{sid}" + ((" ACTIVE=" + ",".join(str(e) for e in active))
                             if active else ""))
    L.append("*STAGE_LOAD")
    for sid in sorted(STAGE_LOADS):
        for node in sorted(STAGE_LOADS[sid]):
            L.append(f"{sid} NODE={node} DOF=2 VALUE={STAGE_LOADS[sid][node]:.17g}")
    L.append("*CABLE_BRIDGE")
    L.append("MODE=STATIC")
    L.extend(STAB_KEYS.rstrip("\n").split("\n"))
    L.append("*STABILITY_LOAD")
    for node in INC_NODES:
        L.append(f"{node} {CTRL_DOF} {PINC:.17g}")
    L.append("*END")
    return "\n".join(L) + "\n"


def ansys_inp(case: dict) -> str:
    """两段 ANSYS APDL：阶段 2 同源模型 + NLGEOM + 曲线 CSV。"""
    active = stage_active_elements()
    loads = stage_constant_loads()
    by_id = {e[0]: e for e in ELEMENTS}
    job = case["id"]
    f_end = {node: loads.get(node, 0.0) + case["lammax"] * PINC
             for node in INC_NODES}

    L: list[str] = [
        "/BATCH",
        f"/TITLE,{TITLE} - {job} - LINK180 NLGEOM",
        "/PREP7",
        "ET,1,LINK180",
        f"R,1,{AREA:.17g}",
        f"MP,EX,1,{E_MODULUS:.17g}",
        "MP,PRXY,1,0.3",
        "! ---- Nodes: same source as FEM .dat ----",
    ]
    for nid, x, y, z in NODES:
        L.append(f"N,{nid},{x:.17g},{y:.17g},{z:.17g}")
    L += ["! ---- Elements: stage-2 active elements only (ANSYS elem no. = creation order) ----",
          "TYPE,1", "MAT,1", "REAL,1"]
    for eid in active:
        e = by_id[eid]
        L.append(f"E,{e[1]},{e[2]}")
    L.append("! ---- Boundary conditions (FEM dof 1/2/3 = UX/UY/UZ) ----")
    for nid in FIXED_ALL:
        for comp in ("UX", "UY", "UZ"):
            L.append(f"D,{nid},{comp},0.0")
    for nid in FIXED_Z:
        L.append(f"D,{nid},UZ,0.0")
    for nid in FIXED_X:
        L.append(f"D,{nid},UX,0.0")
    L.append(f"! ---- Stage {STAGE_ID} accumulated constant loads (F0) ----")
    for node in sorted(loads):
        L.append(f"F,{node},FY,{loads[node]:.17g}")
    L += ["FINISH", "/SOLU", "ANTYPE,STATIC", "NLGEOM,ON",
          "NSUBST,20,100,10", "NEQIT,50", "OUTRES,ALL,ALL", "TIME,1", "SOLVE"]
    L.append(f"! ---- load step 2: incremental load ({case['desc']}) ----")
    for node in INC_NODES:
        L.append(f"F,{node},{CTRL_F},{f_end[node]:.17g}")
    if case["arclen"]:
        L.append("ARCLEN,ON,0.005,1.0E-8")
    L += ["NSUBST,400,1000,20", "NEQIT,60",
          f"TIME,{case['time2']:.12g}", "OUTRES,ALL,ALL", "SOLVE", "FINISH"]
    L += [
        "! ---- Machine-readable results (first column = FEM id) ----",
        "/POST1",
        "*GET,NSET,ACTIVE,0,SET,NSET",
        "*DIM,CURV,ARRAY,NSET,4",
        "*DO,I,1,NSET",
        " SET,,,,,,,I",
        f" *GET,VY,NODE,{CTRL_NODE},U,{CTRL_COMP[-1]}",
        " *GET,TNOW,ACTIVE,0,SET,TIME",
        " CURV(I,1)=I", " CURV(I,2)=TNOW", " CURV(I,3)=VY", " CURV(I,4)=0",
        "*ENDDO",
        f"*CFOPEN,{job}_curve,csv",
        "*VWRITE,CURV(1,1),CURV(1,2),CURV(1,3),CURV(1,4)",
        " (1X,F8.0,3(1X,E20.12))",
        "*CFCLOS", "FINISH", "/EXIT,NOSAVE",
    ]
    return "\n".join(L) + "\n"


def config_json() -> str:
    """冻结配置快照，供离线 CTest 核对 FEM/ANSYS 同源。"""
    return json.dumps({
        "case": CASE_ID,
        "title": TITLE,
        "E": E_MODULUS, "A": AREA,
        "nodes": [list(map(float, n)) for n in NODES],
        "elements": [list(map(int, e)) for e in ELEMENTS],
        "fixed_all": list(FIXED_ALL), "fixed_z": list(FIXED_Z),
        "fixed_x": list(FIXED_X),
        "stage_active": {str(k): v for k, v in STAGE_ACTIVE.items()},
        "stage_loads": {str(k): {str(n): v for n, v in d.items()}
                        for k, d in STAGE_LOADS.items()},
        "stage_id": STAGE_ID,
        "stage_constant_loads": {str(k): v for k, v in stage_constant_loads().items()},
        "stage_active_elements": stage_active_elements(),
        "pinc": PINC, "inc_nodes": list(INC_NODES),
        "ctrl_node": CTRL_NODE, "ctrl_dof": CTRL_DOF,
        "ctrltarget": CTRLTARGET,
        "ansys_cases": ANSYS_CASES,
        "lambda_rule": "lambda = TIME - 1; F_end = F_orig + LAMMAX * PINC",
    }, indent=2, sort_keys=True) + "\n"


def parse_fem_stability(txt: str) -> dict:
    """解析 `<stem>_stage_stability.txt`：[STAGES] + [STABILITY] 段。"""
    stages: list[dict] = []
    sm = re.search(r"\[STAGES\]\n(.*?)(?=\n\[)", txt, re.S)
    if sm:
        for line in sm.group(1).splitlines():
            kv = dict(re.findall(r"(\w+)=([^\s]+)", line))
            if "ID" in kv:
                stages.append({k: float(v) for k, v in kv.items()})
    m = re.search(r"\[STABILITY\]\n(.*?)(?=\n\[END\])", txt, re.S)
    if not m:
        return {"present": False, "stages": stages}
    sec = m.group(1)
    head = {}
    for k, v in re.findall(r"(\w+)=([^\s]+)", sec):
        head.setdefault(k, v)
    state = {}
    sm2 = re.search(r"^STATE (.+)$", sec, re.M)
    if sm2:
        state = {k: float(v) for k, v in re.findall(r"(\w+)=([^\s]+)", sm2.group(1))}
    stage_line: dict[str, float] = {}
    sm3 = re.search(r"^STAGE=(.+)$", sec, re.M)
    if sm3:
        # 段头是 `STAGE=<裸编号> NELEM=.. NDOF=..`，第一个字段不是 key=value。
        toks = sm3.group(1).split()
        stage_line = {"STAGE": float(toks[0])}
        stage_line.update({k: float(v)
                           for k, v in re.findall(r"(\w+)=([^\s]+)", sm3.group(1))})
    pts = [[int(s), float(lam), float(ctrl), float(res)]
           for s, lam, ctrl, res in
           re.findall(r"^I STEP=(\d+) LAMBDA=(\S+) CTRL=(\S+) RES=(\S+)",
                      sec, re.M)]
    return {"present": True, "head": head, "state": state,
            "stage": stage_line, "stages": stages, "points": pts}


def read_curve_csv(path: Path) -> list[tuple[float, float, float]]:
    """ANSYS 曲线 CSV：[(iset, TIME, UY)]，跳过非数值行。"""
    rows: list[tuple[float, float, float]] = []
    for ln in path.read_text(encoding="ascii", errors="replace").splitlines():
        parts = ln.split()
        if len(parts) < 3:
            continue
        try:
            rows.append((float(parts[0]), float(parts[1]), float(parts[2])))
        except ValueError:
            continue
    return rows


__all__ = [
    "CASE_ID", "CASE_NAME", "TITLE", "E_MODULUS", "AREA", "NODES", "ELEMENTS",
    "STAGE_ACTIVE", "STAGE_LOADS", "STAGE_ID", "PINC", "INC_NODES",
    "FIXED_ALL", "FIXED_Z", "FIXED_X",
    "CTRL_NODE", "CTRL_DOF", "CTRLTARGET", "STAB_KEYS", "ANSYS_CASES",
    "stage_constant_loads", "stage_active_elements", "fem_dat", "ansys_inp",
    "config_json", "parse_fem_stability", "read_curve_csv",
]
