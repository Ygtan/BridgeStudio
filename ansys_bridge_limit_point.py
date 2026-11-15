#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""③a / CB3D22：真实斜拉桥「越过极值点 + 下降段」两段同源导出。

与 CB3D21（②）的关系
---------------------
② 的 50 节点 / 63 单元 / 16 索双塔三跨斜拉桥在跨中竖向增量荷载下
**单调硬化、无极限点**，ANSYS 荷载控制即可覆盖全程。③a 要在真实桥梁上
证明「越过极值点 / 下降段」，故保留**同一座桥的全部几何、材料、恒载、
16 根索 `L0` 与 `INISTATE eps`**，只改三处：

1. **只把桥塔实常数 `RealConst3D(id=2)` 的 `A / IY / IZ / J / AW`
   按 `TOWER_SCALE = 0.012`（面积）/ `TOWER_SCALE²`（惯性矩）缩放**，
   使塔轴压接近 Euler 临界 —— 这是**稳定敏感构型**，不是实际设计尺寸。
   注意不能用 `tower_b/tower_h` 重新建模：`cable_stayed_3d.py` 的塔自重
   `w_tower = tower_A·ρ·g` 会跟着缩小 83 倍，成桥态恒载与 ② 不再同源，
   极值点也随之消失（实测）。本模块保留 `COMPLETION_CFG` 的**全部节点
   荷载 / 几何 / 材料 / 索**，只改刚度，等价于「塔采用轻质等效材料」的
   研究构型；
2. 增量荷载改为**塔顶 2009 节点 X 向**（水平推向跨中），控制自由度
   `CTRLNODE=2009 CTRLDOF=1`；
3. ANSYS step2 以 `ARCLEN` 为主 job（荷载控制无法越过极值点），另跑
   一个只到 λ=1.5 的荷载控制 job 校核上升段。

唯一数据源仍是 `LIMIT_CFG` + `EPS0` + `PINC` + `LAMMAX`：

    ModelSpec3D (mode=STATIC, 设计线形, 细塔)
      ├─ *.dat : 显式 L0 = L_design/(1+EPS0) + STABASIS=COMPLETION 键
      │          + *STABILITY_LOAD (2009 dof1, +PINC)
      └─ *.inp : 同一份坐标 + INISTATE eps≡EPS0 + 两段求解
                 (step1 恒载成桥态 TIME=1 / step2 塔顶水平增量荷载)

物理口径
--------
* FEM `R = F0 + λ·Finc − Fint(u)`，起点是 `MODE=STATIC` 成桥态 `u0`；
  ③ 已修复 `MODE=COMPLETION` 起点不同源缺陷，但本算例继续用 STATIC
  与 ② 保持同源、避免制造线形引入第二套参考构形。
* ANSYS 两个 job 的 step2 都设 `TIME = 1 + LAMMAX_job`、终点荷载
  `F_end = LAMMAX_job · PINC`，故 **λ = TIME − 1**（V 形双索探针在
  ② 阶段定标的口径）。不靠反力求和换算 λ。
* 路径在 λ 上非单调，**按控制位移 ΔUX 作为单调自变量插值 λ** 对拍；
  极值点 `(λ_max, ΔUX)` 单独比较。
* `One indefinite tangent matrix is detected` 是极值点附近的物理标志，
  不是求解失败；以 ANSYS `.out` 的 `NUMBER OF ERROR MESSAGES = 0` 判定。

APDL 全 ASCII，阶段标题用英文。编排见 `cases/run_cases_limit_point.py`。
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from cable_stayed_3d import build_cable_stayed_3d
from ansys_bridge_nl import spec_nl_to_apdl, initial_cable_strain
from ansys_bridge_completion import (
    COMPLETION_CFG, EPS0, design_coords, cable_elements, cable_l0,
    node_load, read_curve_csv,
)

CASE_ID = "cb3d22_limit_point"
LIMIT_CASE_NAME = "CB3D22_limit_point_vs_ansys"

# 稳定敏感构型：只缩桥塔刚度实常数，其余几何/材料/恒载/索与 CB3D21 完全一致。
TOWER_SCALE = 0.012
LIMIT_CFG = dict(COMPLETION_CFG)   # 唯一数据源 = ② 的原配置 + TOWER_SCALE

PINC = 1.0e6          # FEM *STABILITY_LOAD 参考增量荷载 N（+X）
EPS = EPS0            # 与 ② 同一初应变，保证 INISTATE / L0 同源
CTRL_NODE, CTRL_DOF = 2009, 1
CTRL_COMP = "UX"      # ANSYS *GET 分量
CTRL_F = "FX"         # ANSYS F 分量
CTRLTARGET = 1.5      # FEM 弧长路径终止的控制位移增量 (m)
ARCTRM_U = 1.6        # ANSYS 位移保护阈值，略大于 FEM 终点 1.5017

TITLE = "CB3D22 thin-tower cable-stayed bridge limit point - FEM vs ANSYS two-step"

STAB_KEYS = (
    "STABILITY=1 STABASIS=COMPLETION PATHMETHOD=SPHERE "
    "CTRLNODE=2009 CTRLDOF=1 CTRLTARGET=1.5\n"
    "DS0=0.001 DSMAX=0.01 DSMIN=1.0e-12 PSI=0.5\n"
    "MAXPATHSTEP=2500 STABITER=60 STABTOL=1.0e-5 STRAIN=0\n"
)

# 两个 job 都用 TIME=1+LAMMAX_job，故 λ = TIME-1。
# lp_load 只到 λ=1.5（极值点 λ_max≈1.625 之前）校核上升段；
# lp_arc 到 λ=2 但靠 ARCLEN 与 ARCTRM 越过极值点进入下降段。
ANSYS_CASES = [
    {"id": "lp_load", "arclen": False, "lammax": 1.5, "time2": 2.5,
     "desc": "load-controlled step 2 (ascending branch only, pre-limit)"},
    {"id": "lp_arc", "arclen": True, "lammax": 2.0, "time2": 3.0,
     "desc": "ARCLEN step 2 (pass limit point and follow descending branch)"},
]


def build_model():
    """② 的原模型 + 只缩放塔刚度实常数（恒载/自重一字不改）。"""
    model, info = build_cable_stayed_3d(**LIMIT_CFG)
    towers = [r for r in model.reals if r.id == 2]
    if len(towers) != 1:
        raise ValueError("找不到唯一的桥塔实常数 RealConst3D(id=2)")
    r = towers[0]
    r.A *= TOWER_SCALE
    r.IY *= TOWER_SCALE ** 2
    r.IZ *= TOWER_SCALE ** 2
    r.J *= TOWER_SCALE ** 2
    if getattr(r, "AW", None):
        r.AW *= TOWER_SCALE
    return model, info


def fem_dat(model, l0: dict[int, float]) -> str:
    """FEM .dat：ModelSpec3D.to_dat() + 显式 L0 + 稳定性键 + 增量荷载。"""
    txt = model.to_dat()
    if "MODE=STATIC" not in txt:
        raise ValueError("模型不是 MODE=STATIC：③a 必须用 STATIC 保证起点闭合")
    cables = {e.id: e for e in cable_elements(model)}
    lines: list[str] = []
    skip = 0
    for ln in txt.splitlines():
        if skip:
            skip -= 1
            continue
        s = ln.strip()
        if s.startswith("*DO I 9006 9008"):
            skip = 2
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
            lines.append(f"{CTRL_NODE} {CTRL_DOF} {PINC:.17g}")
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
        if abs(val - EPS) > 1e-12:
            raise ValueError(f"索 {eid} 的 INISTATE eps={val} 与 EPS0 不同源")
    base, info = spec_nl_to_apdl(model, jobname=jobname, coords=coords,
                                 l0=l0, title=TITLE)
    f_orig = node_load(model, CTRL_NODE, CTRL_DOF)   # 恒载里塔顶无水平力
    f_end = f_orig + case["lammax"] * PINC
    if abs(f_orig) > 1e-9:
        raise ValueError("③a 要求塔顶初始水平荷载为 0，才能用 λ=TIME-1")

    step2 = [
        f"! ---- load step 2: tower-top horizontal load ({case['desc']}) ----",
        f"F,{CTRL_NODE},{CTRL_F},{f_end:.15g}",
    ]
    if case["arclen"]:
        step2.append("ARCLEN,ON,0.005,1.0E-8")
    step2 += [
        "NSUBST,2000,5000,20",
        "NEQIT,60",
        f"TIME,{case['time2']:.12g}",
        f"ARCTRM,U,{ARCTRM_U:.9g},{CTRL_NODE},{CTRL_COMP}",
        "OUTRES,ALL,ALL",
        "SOLVE",
    ]
    post = [
        "/POST1",
        "*GET,NSET,ACTIVE,0,SET,NSET",
        "*DIM,CURV,ARRAY,NSET,4",
        "*DO,I,1,NSET",
        " SET,,,,,,,I",
        f" *GET,VX,NODE,{CTRL_NODE},U,{CTRL_COMP[-1]}",
        " *GET,TNOW,ACTIVE,0,SET,TIME",
        " CURV(I,1)=I", " CURV(I,2)=TNOW", " CURV(I,3)=VX", " CURV(I,4)=0",
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
    info["eps_uniform"] = EPS
    info["lammax"] = case["lammax"]
    return "\n".join(out) + "\n", info


def parse_fem_stability(txt: str) -> dict:
    """解析 FEM [STABILITY] 段：状态行 + 逐平衡点 (step, λ, ΔCTRL, RES)。"""
    m = re.search(r"\[STABILITY\]\n(.*?)(?=\n\[END\])", txt, re.S)
    if not m:
        return {"present": False}
    sec = m.group(1)
    head = {}
    for k, v in re.findall(r"(\w+)=([^\s]+)", sec):
        head.setdefault(k, v)
    state = {}
    sm = re.search(r"^STATE (.+)$", sec, re.M)
    if sm:
        state = {k: float(v) for k, v in re.findall(r"(\w+)=([^\s]+)", sm.group(1))}
    pts = [[int(s), float(lam), float(ctrl), float(res)]
           for s, lam, ctrl, res in
           re.findall(r"^I STEP=(\d+) LAMBDA=(\S+) CTRL=(\S+) RES=(\S+)",
                      sec, re.M)]
    return {"present": True, "head": head, "state": state, "points": pts}


__all__ = [
    "CASE_ID", "LIMIT_CASE_NAME", "LIMIT_CFG", "TOWER_SCALE", "EPS",
    "PINC", "CTRL_NODE", "CTRL_DOF", "CTRLTARGET", "ARCTRM_U",
    "STAB_KEYS", "ANSYS_CASES", "TITLE", "build_model", "fem_dat",
    "ansys_curve_apdl", "parse_fem_stability", "read_curve_csv",
    "cable_l0", "design_coords",
]
