#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CB3D17 同源导出：von Mises 两杆浅桁架 snap-through 全过程。

唯一数据源：本文件的 SNAP_CFG / SNAP_FEM_CASES / SNAP_ANSYS_CASES。
同一份参数导出：

  * ``*.snap``        -> FEM-Eigen/snap_probe.exe（球面弧长法，stability_path_3d.hpp）
  * ``*.inp``         -> ANSYS 18.0 LINK180 + NLGEOM + ARCLEN,ON + ARCTRM
  * ``*.model.json``  -> 可追溯参数快照

物理与口径
----------
两杆浅桁架 b=2 m、h=0.5 m、A=1e-3 m2、E=2.1e11 Pa，两端固定，
顶点施加 -Y 参考荷载 PREF=2 MN（**大于**解析极限荷载 1.188 MN，否则
ANSYS 荷载步在 lambda=1 就停、根本到不了极限点）。

独立解析锚点（共转轴向杆，与 fem_3d_nl.hpp 同几何）：

    L(v) = sqrt(b^2 + (h-v)^2)
    N(v) = EA*(L-L0)/L0            （工程应变，FEM 内核口径）
         = EA*ln(L/L0)             （对数应变，ANSYS LINK180 KEYOPT(2)=1）
    P(v) = -2*N*(h-v)/L

解析极限点：dP/dv=0 -> v_cr=0.214246442 m / 0.215213687 m，
P_cr=1.1884196 MN / 1.2004882 MN。P(h)=0（压平），P(2h)=0（翻转）。

ANSYS LINK180 的 KEYOPT(2) 是应变度量开关：实测 =1 与对数应变解析
吻合到 7e-5，=0 则高约 3%（另一种应变度量）。因此正式对标用 =1，
并保留一个 =0 作业量化「应变度量效应」，不与 FEM 严格比较。

ANSYS 弧长法里 TIME 即荷载因子（TIME_end=1 时 lambda=TIME）；CSV 同时
导出支座反力 (R1+R3)/PREF 作为**独立**的 lambda 反算，两者互相校验。
逐子步 CSV 由 /POST1 + SET(按数据集号) + *CFOPEN 一次性写出。
"""
from __future__ import annotations

import math
from pathlib import Path

CASE_ID = "cb3d17_snap_through_arc_length"
PARAMS_FILE = CASE_ID + ".snap"
SNAP_CASE_NAME = "CB3D17_snap_through_arc_length"

# ---------------------------------------------------------------- 唯一数据源
SNAP_CFG = {
    "E": 2.1e11,          # Pa
    "A": 1.0e-3,          # m2
    "B": 2.0,             # m，半跨
    "H": 0.5,             # m，初始矢高
    "PREF": 2.0e6,        # N，参考荷载（> 极限荷载 1.188 MN）
    "VMAX": 1.0,          # m，控制位移终止（= 2h）
    "TOL": 1.0e-9,        # FEM 弧长法残差相对容差
    "MAXSTEPS": 2000,
    "MAXITER": 40,
    "DSMAX": 0.01,        # 弧长半径上限（控制极值点采样密度）
    "DSMIN": 1.0e-8,
    "TIME_END": 1.0,      # ANSYS 荷载步终止伪时间
    "MINARC": 1.0e-7,
    "NEQIT": 60,
    "PRXY": 0.3,
}

# FEM 弧长法算例：两种应变度量 x 两种初始弧长步
SNAP_FEM_CASES = [
    {"id": "log_fine", "DS": 0.005, "PSI": 1.0, "STRAIN": 1,
     "desc": "log strain, ds0=0.005 (primary ANSYS counterpart)"},
    {"id": "log_coarse", "DS": 0.02, "PSI": 1.0, "STRAIN": 1,
     "desc": "log strain, ds0=0.02 (arc-length step sensitivity)"},
    {"id": "eng_fine", "DS": 0.005, "PSI": 1.0, "STRAIN": 0,
     "desc": "engineering strain, kernel constitutive"},
    {"id": "eng_coarse", "DS": 0.02, "PSI": 1.0, "STRAIN": 0,
     "desc": "engineering strain, ds0=0.02"},
]

# ANSYS 作业：KEYOPT(2)=1 两个弧长步 + KEYOPT(2)=0 应变度量对照
SNAP_ANSYS_CASES = [
    {"id": "sn_log_fine", "keyopt2": 1, "maxarc": 0.005, "nsub": 1200,
     "fem": "log_fine",
     "desc": "LINK180 KEYOPT(2)=1, ARCLEN maxarc=0.005 (primary)"},
    {"id": "sn_log_coarse", "keyopt2": 1, "maxarc": 0.02, "nsub": 500,
     "fem": "log_coarse",
     "desc": "LINK180 KEYOPT(2)=1, ARCLEN maxarc=0.02"},
    {"id": "sn_eng", "keyopt2": 0, "maxarc": 0.005, "nsub": 1200,
     "fem": None,
     "desc": "LINK180 KEYOPT(2)=0, strain-measure contrast only"},
]


def num(x: float) -> str:
    return "%.12E" % x


def analytic_p(v: float, cfg: dict, strain: int) -> float:
    """独立解析荷载-位移闭式 P(v)（N）。strain: 0=工程应变 1=对数应变。"""
    B, H = cfg["B"], cfg["H"]
    L0 = math.hypot(B, H)
    L = math.hypot(B, H - v)
    N = (cfg["E"] * cfg["A"] * math.log(L / L0) if strain == 1
         else cfg["E"] * cfg["A"] * (L - L0) / L0)
    return -2.0 * N * (H - v) / L


def analytic_limit(cfg: dict, strain: int) -> tuple[float, float]:
    """黄金分割求 dP/dv=0 的解析极限点 (v_cr, P_cr)。"""
    lo, hi = 0.0, cfg["H"]
    for _ in range(200):
        m1 = lo + (hi - lo) * 0.3819660112501051
        m2 = lo + (hi - lo) * 0.6180339887498949
        if analytic_p(m1, cfg, strain) < analytic_p(m2, cfg, strain):
            lo = m1
        else:
            hi = m2
    v = 0.5 * (lo + hi)
    return v, analytic_p(v, cfg, strain)


def params_text(cfg: dict, fem_cases: list) -> str:
    """生成 snap_probe.exe 的 *.snap 参数文件（纯 ASCII）。"""
    L = ["G g1 E=%g A=%g B=%g H=%g PREF=%g TOL=%g MAXSTEPS=%d MAXITER=%d "
         "VMAX=%g DSMAX=%g DSMIN=%g" % (
             cfg["E"], cfg["A"], cfg["B"], cfg["H"], cfg["PREF"],
             cfg["TOL"], cfg["MAXSTEPS"], cfg["MAXITER"], cfg["VMAX"],
             cfg["DSMAX"], cfg["DSMIN"])]
    for c in fem_cases:
        L.append("C %s g1 DS=%g PSI=%g STRAIN=%d TAG=%s" % (
            c["id"], c["DS"], c["PSI"], c["STRAIN"], c["id"]))
    return "\n".join(L) + "\n"


def snap_apdl(cfg: dict, case: dict, jobname: str) -> str:
    """LINK180 + NLGEOM + ARCLEN APDL（全 ASCII，阶段标题用英文）。"""
    a: list[str] = []
    a.append("/TITLE,von Mises truss snap-through - " + jobname)
    a.append("/PREP7")
    a.append("ET,1,LINK180")
    a.append("KEYOPT,1,2,%d" % case["keyopt2"])
    a.append("KEYOPT,1,3,0")
    a.append("R,1," + num(cfg["A"]))
    a.append("MP,EX,1," + num(cfg["E"]))
    a.append("MP,PRXY,1," + num(cfg["PRXY"]))
    a.append("MP,DENS,1,0")
    a.append("N,1," + num(-cfg["B"]) + "," + num(0.0) + ",0")
    a.append("N,2," + num(0.0) + "," + num(cfg["H"]) + ",0")
    a.append("N,3," + num(cfg["B"]) + "," + num(0.0) + ",0")
    a.append("E,1,2")
    a.append("E,2,3")
    a.append("D,1,ALL,0")
    a.append("D,3,ALL,0")
    a.append("D,2,UZ,0")
    a.append("FINISH")
    a.append("/SOLU")
    a.append("ANTYPE,STATIC")
    a.append("NLGEOM,ON")
    a.append("AUTOTS,OFF")
    a.append("ARCLEN,ON," + num(case["maxarc"]) + "," + num(cfg["MINARC"]))
    ns = int(case["nsub"])
    a.append("NSUBST,%d,%d,%d" % (ns, ns * 5, 20))
    a.append("NEQIT,%d" % cfg["NEQIT"])
    a.append("TIME," + num(cfg["TIME_END"]))
    a.append("OUTRES,ALL,ALL")
    a.append("F,2,FY," + num(-cfg["PREF"]))
    a.append("ARCTRM,U," + num(cfg["VMAX"]) + ",2,UY")
    a.append("SOLVE")
    a.append("FINISH")
    a.append("/POST1")
    a.append("*GET,NSET,ACTIVE,0,SET,NSET")
    a.append("*DIM,CURV,ARRAY,NSET,5")
    a.append("*DO,I,1,NSET")
    a.append(" SET,,,,,,,I")
    a.append(" *GET,VY,NODE,2,U,Y")
    a.append(" *GET,R1,NODE,1,RF,FY")
    a.append(" *GET,R3,NODE,3,RF,FY")
    a.append(" *GET,TNOW,ACTIVE,0,SET,TIME")
    a.append(" CURV(I,1)=I")
    a.append(" CURV(I,2)=VY")
    a.append(" CURV(I,3)=R1")
    a.append(" CURV(I,4)=R3")
    a.append(" CURV(I,5)=TNOW")
    a.append("*ENDDO")
    a.append("*CFOPEN," + jobname + "_curve,csv")
    a.append("*VWRITE,CURV(1,1),CURV(1,2),CURV(1,3),CURV(1,4),CURV(1,5)")
    a.append(" (1X,F8.0,4(1X,E20.12))")
    a.append("*CFCLOS")
    a.append("FINISH")
    a.append("/EXIT,NOSAVE")
    return "\n".join(a) + "\n"


def read_curve_csv(path: Path) -> list[tuple[float, float, float, float]]:
    """ANSYS 逐子步曲线：返回 [(v, lambda_reaction, lambda_time, iset)]。"""
    cfg = SNAP_CFG
    out: list[tuple[float, float, float, float]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        p = line.split()
        if len(p) < 5:
            continue
        try:
            iset = float(p[0])
            uy = float(p[1])
            r1, r3, tnow = float(p[2]), float(p[3]), float(p[4])
        except ValueError:
            continue
        v = -uy
        lam_r = (r1 + r3) / cfg["PREF"]
        lam_t = tnow / cfg["TIME_END"]
        out.append((v, lam_r, lam_t, iset))
    return out


def quadratic_extremum(curve: list[tuple[float, float]], find_max: bool):
    """与 snap_probe.cpp 同算法：相邻三点二次插值求 (v*, lambda*)。"""
    if not curve:
        return 0.0, 0.0
    pts = sorted(curve, key=lambda x: x[0])
    vals = [p[1] for p in pts]
    i = (max if find_max else min)(range(len(vals)), key=lambda k: vals[k])
    if i <= 0 or i + 1 >= len(pts):
        return pts[i]
    x0, y0 = pts[i - 1]
    x1, y1 = pts[i]
    x2, y2 = pts[i + 1]
    s1 = (y1 - y0) / (x1 - x0)
    s2 = (y2 - y1) / (x2 - x1)
    d2 = (s2 - s1) / (x2 - x0)
    if abs(d2) < 1e-14 or abs(x1 - x0) < 1e-14:
        return x1, y1
    xs = 0.5 * (x0 + x1) - s1 / (2.0 * d2)
    ys = y0 + s1 * (xs - x0) + d2 * (xs - x0) * (xs - x1)
    return xs, ys


def interp_lambda(curve: list[tuple[float, float]], vq: float) -> float:
    """在 FEM 平衡路径上按 v 做**三点二次**插值取 lambda。

    路径在极值点附近曲率大，线性插值会带来 ~2.5e-4 的纯插值误差；
    二次插值把它压到 ~3.3e-6，才能让「FEM 弧长路径 vs ANSYS ARCLEN
    路径」的判据真正反映两套求解器的一致性。
    """
    pts = sorted(curve, key=lambda x: x[0])
    if len(pts) < 3:
        for (a, ya), (b, yb) in zip(pts, pts[1:]):
            if a <= vq <= b and b > a:
                return ya + (yb - ya) * (vq - a) / (b - a)
        return 0.0
    i = min(range(len(pts)), key=lambda k: abs(pts[k][0] - vq))
    j = max(0, min(i - 1, len(pts) - 3))
    idx = [j, j + 1, j + 2]
    xs = [pts[k][0] for k in idx]
    ys = [pts[k][1] for k in idx]
    if any(abs(xs[a] - xs[b]) < 1e-14 for a in range(3) for b in range(a + 1, 3)):
        return 0.0
    s = 0.0
    for a in range(3):
        t = ys[a]
        for b in range(3):
            if a != b:
                t *= (vq - xs[b]) / (xs[a] - xs[b])
        s += t
    return s
