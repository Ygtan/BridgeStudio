#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CB3D19 同源导出：共转 CR 梁刚接框架后屈曲 vs ANSYS BEAM188 + ARCLEN。

唯一数据源：本文件的 FRAME_CFG / FRAME_FEM_CASES / FRAME_ANSYS_CASES。
同一份参数导出：

  * ``*.frame``       -> FEM-Eigen/frame_probe.exe（球面弧长法 + CR 梁）
  * ``*.inp``         -> ANSYS 18.0 BEAM188 + NLGEOM + ARCLEN,ON + ARCTRM
  * ``*.model.json``  -> 可追溯参数快照

物理与口径
----------
Williams toggle 型刚接框架：左支座 (-B,0) -- 顶点 (0,H) -- 右支座 (B,0)，
每根斜杆 NELEM 个 BEAM 单元，端部铰支（释放 ROTZ）、顶点与中间节点刚接，
全部节点约束 UZ/ROTX/ROTY 保持 XY 平面内。顶点施加 -Y 参考荷载 PREF，
控制自由度为顶点 UY。

与 CB3D17（两杆 TRUSS 桁架）的本质区别：这里**必须用梁单元**——顶点
刚接带来弯曲刚度，路径出现「第一极限点 -> 下降段 -> 反向荷载 -> 再硬化」。
弯曲刚度主导时后段 λ 会远超第一极限点，因此极限点判据取**第一个 λ 局部
极大**，而不是全局最大。

两套求解器的口径统一
--------------------
* 截面取 IY = IZ 的对称截面：beamRefTriad 以 global Z 建局部轴，ANSYS
  BEAM188 无 K 节点时缺省局部轴未必同向；IY=IZ 时绕杆轴的任意旋转都不
  改变刚度，从根上消除局部轴约定差异。
* ANSYS 用 ``KEYOPT(3)=2``（三次形函数）+ ``MP,GXY`` 取 G×1000，把
  BEAM188 的 Timoshenko 剪切柔度退化到 Euler-Bernoulli 口径（K 阶段
  CB3D16 已验证：线性基线偏差由 -5.07e-4 降到 +2.3e-7）。
* FEM 侧 e.G 只进入扭转刚度，而 ROTX/ROTY 已约束，平面内不激活；两侧
  仍取同一个 G 值以保持参数同源。
* BEAM188 没有 LINK180 那样的 ``KEYOPT(2)`` 应变度量开关；实测完整路径
  主体一致到 1e-5~3e-4，残余差异集中在 v→1 的强硬化陡升段，故
  VMAX 取 0.95，避开该段并把判据留给可比较的物理区间。

独立解析锚点（与两个求解器都无关）
--------------------------------
``linear_frame_stiffness()`` 用纯 Python 从标准 2D Euler-Bernoulli 刚架
单元刚度矩阵组装整套框架的线性刚度，约束两端支座平动，顶点施加单位
-Y 荷载，解出顶点柔度并反算初始切线刚度 k0。它不调用 FEM 内核、不依赖
numpy，与 ``frame_probe.exe`` 输出的 ``k0_fem`` 严格对拍；同时用 ANSYS
前几个子步的 λ/v 作为第三方复核。

ANSYS 弧长法里 TIME_END=1 时 λ=TIME；CSV 同时导出支座反力
(R1+R3)/PREF 作为**独立**的 λ 反算，两者互相校验。逐子步 CSV 由
/POST1 + SET(按数据集号) + *CFOPEN 一次性写出。
"""
from __future__ import annotations

import math
from pathlib import Path

# L 阶段已验证的三点二次插值 / 极值拟合算法（纯函数、无模块状态）。
from ansys_bridge_snap import interp_lambda, quadratic_extremum  # noqa: F401

CASE_ID = "cb3d19_frame_postbuckling"
PARAMS_FILE = CASE_ID + ".frame"
FRAME_CASE_NAME = "CB3D19_frame_postbuckling_arc_length"

# ---------------------------------------------------------------- 唯一数据源
FRAME_CFG = {
    "E": 2.1e11,          # Pa
    "G": 8.1e13,          # Pa，G×1000：BEAM188 退化 EB（K 阶段口径）
    "A": 1.0e-3,          # m2
    "IY": 1.0e-6,         # m4，= IZ（对称截面）
    "IZ": 1.0e-6,
    "J": 2.0e-6,          # m4，扭转常数（平面内不激活）
    "B": 2.0,             # m，半跨
    "H": 0.5,             # m，初始矢高
    "NELEM": 24,          # 每根斜杆的 BEAM 单元数（FEM 与 ANSYS 同源）
    "PREF": 4.0e6,        # N，顶点参考荷载（> 第一极限荷载 ~0.36 MN）
    "VMAX": 0.95,         # m，控制位移终止（避开 v→1 强硬化陡升段）
    "TOL": 1.0e-9,        # FEM 弧长法残差相对容差
    "MAXSTEPS": 3000,
    "MAXITER": 40,
    "DSMAX": 0.01,
    "DSMIN": 1.0e-8,
    "TIME_END": 1.0,      # ANSYS 伪时间；=1 时 λ=TIME
    "MINARC": 1.0e-7,
    "NEQIT": 60,
    "PRXY": 0.3,
}

# FEM 侧算例：弧长步 / 权重敏感性 + 一个负例（无参考荷载）。
# gbad 组由 params_text() 从 g1 派生、仅把 PREF 置 0，用于验证内核
# zero_reference_load 错误码不会静默产出空路径。
FRAME_FEM_CASES = [
    {"id": "fine", "group": "g1", "DS": 0.005, "PSI": 1.0,
     "desc": "primary ANSYS counterpart"},
    {"id": "coarse", "group": "g1", "DS": 0.010, "PSI": 1.0,
     "desc": "arc-length step sensitivity"},
    {"id": "psi_half", "group": "g1", "DS": 0.005, "PSI": 0.5,
     "desc": "arc-length lambda weight sensitivity"},
    {"id": "bad_noload", "group": "gbad", "DS": 0.005, "PSI": 1.0,
     "negative": "zero_reference_load",
     "desc": "negative case: PREF=0 must report zero_reference_load"},
]

# ANSYS 作业：两个弧长步上限，互相校验路径离散无关性。
FRAME_ANSYS_CASES = [
    {"id": "fr_fine", "maxarc": 0.002, "nsub": 1200, "fem": "fine",
     "desc": "BEAM188 KEYOPT(3)=2, ARCLEN maxarc=0.002 (primary)"},
    {"id": "fr_coarse", "maxarc": 0.008, "nsub": 500, "fem": "coarse",
     "desc": "BEAM188 KEYOPT(3)=2, ARCLEN maxarc=0.008"},
]


def num(x: float) -> str:
    return "%.12E" % x


def frame_nodes(cfg: dict) -> list[tuple[float, float]]:
    """框架节点坐标（1-based 逻辑编号 = 列表下标 + 1），两侧同源。"""
    n = int(cfg["NELEM"])
    B, H = cfg["B"], cfg["H"]
    pts: list[tuple[float, float]] = []
    for i in range(n + 1):
        t = i / n
        pts.append((-B + t * B, t * H))
    for i in range(1, n + 1):
        t = i / n
        pts.append((t * B, H - t * H))
    return pts


def apex_node(cfg: dict) -> int:
    return int(cfg["NELEM"]) + 1


def right_node(cfg: dict) -> int:
    return 2 * int(cfg["NELEM"]) + 1


def group_line(gid: str, cfg: dict) -> str:
    return ("G %s E=%g G=%g A=%g IY=%g IZ=%g J=%g B=%g H=%g NELEM=%d "
            "PREF=%g TOL=%g MAXSTEPS=%d MAXITER=%d VMAX=%g DSMAX=%g DSMIN=%g" % (
                gid, cfg["E"], cfg["G"], cfg["A"], cfg["IY"], cfg["IZ"],
                cfg["J"], cfg["B"], cfg["H"], int(cfg["NELEM"]), cfg["PREF"],
                cfg["TOL"], int(cfg["MAXSTEPS"]), int(cfg["MAXITER"]),
                cfg["VMAX"], cfg["DSMAX"], cfg["DSMIN"]))


def params_text(cfg: dict, fem_cases: list) -> str:
    """生成 frame_probe.exe 的 *.frame 参数文件（纯 ASCII）。"""
    lines = [group_line("g1", cfg)]
    bad = dict(cfg)
    bad["PREF"] = 0.0
    lines.append(group_line("gbad", bad))
    for c in fem_cases:
        lines.append("C %s %s DS=%g PSI=%g TAG=%s" % (
            c["id"], c["group"], c["DS"], c["PSI"], c["id"]))
    return "\n".join(lines) + "\n"


def frame_apdl(cfg: dict, case: dict, jobname: str) -> str:
    """生成单个 case 的 BEAM188 + NLGEOM + ARCLEN APDL（全 ASCII）。

    输出 ``<jobname>_curve.csv``：5 列 [iset, UY, R1, R3, TIME]，
    由 read_curve_csv() 换算成 (v, lambda_reaction, lambda_time, iset)。
    """
    pts = frame_nodes(cfg)
    apex = apex_node(cfg)
    right = right_node(cfg)
    a: list[str] = []
    a.append("/BATCH")
    a.append("/TITLE,Williams toggle frame postbuckling - " + jobname)
    a.append("/PREP7")
    a.append("ET,1,BEAM188")
    a.append("KEYOPT,1,1,0")
    a.append("KEYOPT,1,3,2")
    a.append("! ASEC + Iy=Iz: local-axis rotation about the beam axis is irrelevant")
    a.append("SECTYPE,1,BEAM,ASEC")
    # SECDATA: A, IYY, IYZ, IZZ, IW, J, CGY, CGZ, SHY, SHZ
    a.append("SECDATA,%s,%s,0,%s,0,%s,0,0,0,0" % (
        num(cfg["A"]), num(cfg["IY"]), num(cfg["IZ"]), num(cfg["J"])))
    a.append("SECNUM,1")
    a.append("MP,EX,1," + num(cfg["E"]))
    a.append("MP,PRXY,1," + num(cfg["PRXY"]))
    a.append("MP,GXY,1," + num(cfg["G"]))
    a.append("MP,DENS,1,0")
    for i, (x, y) in enumerate(pts, start=1):
        a.append("N,%d,%s,%s,0" % (i, num(x), num(y)))
    a.append("TYPE,1")
    a.append("MAT,1")
    for i in range(1, len(pts)):
        a.append("E,%d,%d" % (i, i + 1))
    a.append("! ---- keep the frame in the XY plane ----")
    for lab in ("UZ", "ROTX", "ROTY"):
        a.append("D,ALL,%s,0" % lab)
    a.append("! ---- pinned end supports (ROTZ released) ----")
    for nd in (1, right):
        for lab in ("UX", "UY"):
            a.append("D,%d,%s,0" % (nd, lab))
    a.append("FINISH")
    a.append("/SOLU")
    a.append("ANTYPE,STATIC")
    a.append("NLGEOM,ON")
    a.append("AUTOTS,OFF")
    a.append("ARCLEN,ON,%s,%s" % (num(case["maxarc"]), num(cfg["MINARC"])))
    ns = int(case["nsub"])
    a.append("NSUBST,%d,%d,20" % (ns, ns * 5))
    a.append("NEQIT,%d" % int(cfg["NEQIT"]))
    a.append("TIME," + num(cfg["TIME_END"]))
    a.append("OUTRES,ALL,ALL")
    a.append("F,%d,FY,%s" % (apex, num(-cfg["PREF"])))
    a.append("ARCTRM,U,%s,%d,UY" % (num(cfg["VMAX"]), apex))
    a.append("SOLVE")
    a.append("FINISH")
    a.append("/POST1")
    a.append("*GET,NSET,ACTIVE,0,SET,NSET")
    a.append("*DIM,CURV,ARRAY,NSET,5")
    a.append("*DO,I,1,NSET")
    a.append(" SET,,,,,,,I")
    a.append(" *GET,VY,NODE,%d,U,Y" % apex)
    a.append(" *GET,R1,NODE,1,RF,FY")
    a.append(" *GET,R3,NODE,%d,RF,FY" % right)
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


def read_curve_csv(path: Path, cfg: dict) -> list[tuple[float, float, float, float]]:
    """ANSYS 逐子步曲线：[(v, lambda_reaction, lambda_time, iset)]。"""
    out: list[tuple[float, float, float, float]] = []
    for line in Path(path).read_text(encoding="ascii", errors="replace").splitlines():
        p = line.split()
        if len(p) < 5:
            continue
        try:
            iset = float(p[0])
            uy = float(p[1])
            r1, r3, tnow = float(p[2]), float(p[3]), float(p[4])
        except ValueError:
            continue
        out.append((-uy, (r1 + r3) / cfg["PREF"],
                    tnow / cfg["TIME_END"], iset))
    return out


def first_limit_point(curve: list[tuple[float, float]]):
    """路径上**第一个** λ 局部极大，用相邻三点二次插值消除采样误差。

    刚接框架翻越后弯曲刚度再硬化，全局 λ 最大通常落在终点而不是极限点，
    因此不能用 quadratic_extremum(curve, find_max=True)。返回
    (v_fit, lam_fit, v_raw, lam_raw)；无极值时返回 (0,0,0,0)。
    """
    if len(curve) < 3:
        return 0.0, 0.0, 0.0, 0.0
    i = -1
    for k in range(1, len(curve) - 1):
        if curve[k][1] > curve[k - 1][1] and curve[k][1] >= curve[k + 1][1]:
            i = k
            break
    if i < 0:
        return 0.0, 0.0, 0.0, 0.0
    x0, y0 = curve[i - 1]
    x1, y1 = curve[i]
    x2, y2 = curve[i + 1]
    v_raw, lam_raw = x1, y1
    if abs(x1 - x0) < 1e-14 or abs(x2 - x1) < 1e-14:
        return v_raw, lam_raw, v_raw, lam_raw
    s1 = (y1 - y0) / (x1 - x0)
    s2 = (y2 - y1) / (x2 - x1)
    d2 = (s2 - s1) / (x2 - x0)
    if abs(d2) < 1e-14:
        return v_raw, lam_raw, v_raw, lam_raw
    vs = 0.5 * (x0 + x1) - s1 / (2.0 * d2)
    ls = y0 + s1 * (vs - x0) + d2 * (vs - x0) * (vs - x1)
    return vs, ls, v_raw, lam_raw


# -------------------------------------------------- 独立线性刚架刚度锚点
def _frame_local_stiffness(L: float, E: float, A: float, I: float) -> list[list[float]]:
    """标准 2D Euler-Bernoulli 刚架单元局部刚度（6x6）。"""
    ea = E * A / L
    ei = E * I
    return [
        [ea, 0.0, 0.0, -ea, 0.0, 0.0],
        [0.0, 12.0 * ei / L**3, 6.0 * ei / L**2,
         0.0, -12.0 * ei / L**3, 6.0 * ei / L**2],
        [0.0, 6.0 * ei / L**2, 4.0 * ei / L,
         0.0, -6.0 * ei / L**2, 2.0 * ei / L],
        [-ea, 0.0, 0.0, ea, 0.0, 0.0],
        [0.0, -12.0 * ei / L**3, -6.0 * ei / L**2,
         0.0, 12.0 * ei / L**3, -6.0 * ei / L**2],
        [0.0, 6.0 * ei / L**2, 2.0 * ei / L,
         0.0, -6.0 * ei / L**2, 4.0 * ei / L],
    ]


def _frame_transform(c: float, s: float) -> list[list[float]]:
    return [
        [c, s, 0.0, 0.0, 0.0, 0.0],
        [-s, c, 0.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, c, s, 0.0],
        [0.0, 0.0, 0.0, -s, c, 0.0],
        [0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
    ]


def _spd_solve(K: list[list[float]], F: list[float]) -> list[float]:
    """对称正定 LDL^T 求解（纯 Python，零第三方依赖）。"""
    n = len(F)
    L = [[0.0] * n for _ in range(n)]
    D = [0.0] * n
    for i in range(n):
        for j in range(i + 1):
            s = K[i][j]
            for k in range(j):
                s -= L[i][k] * L[j][k] * D[k]
            if i == j:
                if abs(s) < 1.0e-300:
                    raise ValueError("singular linear anchor stiffness")
                D[i] = s
                L[i][i] = 1.0
            else:
                L[i][j] = s / D[j]
    z = [0.0] * n
    for i in range(n):
        z[i] = F[i] - sum(L[i][k] * z[k] for k in range(i))
    y = [z[i] / D[i] for i in range(n)]
    u = [0.0] * n
    for i in range(n - 1, -1, -1):
        u[i] = y[i] - sum(L[k][i] * u[k] for k in range(i + 1, n))
    return u


def linear_frame_stiffness(cfg: dict) -> float:
    """独立解析锚点：顶点初始切线刚度 k0（N/m）。

    用标准 2D 刚架单元刚度矩阵组装整框架（每节点 UX/UY/ROTZ 三个自由度），
    约束两端支座的 UX/UY（释放转动），顶点施加单位 -Y 荷载，解出顶点
    柔度 v 并反算 k0 = 1/v。该实现不调用 FEM 内核、不读任何求解器输出，
    与 frame_probe.exe 的 k0_fem 构成两条独立实现。
    """
    pts = frame_nodes(cfg)
    nn = len(pts)
    nd = 3 * nn
    K = [[0.0] * nd for _ in range(nd)]
    E, A, IZ = cfg["E"], cfg["A"], cfg["IZ"]
    for i in range(nn - 1):
        dx = pts[i + 1][0] - pts[i][0]
        dy = pts[i + 1][1] - pts[i][1]
        L = math.hypot(dx, dy)
        c, s = dx / L, dy / L
        kl = _frame_local_stiffness(L, E, A, IZ)
        T = _frame_transform(c, s)
        # kg = T^T * kl * T
        tmp = [[sum(kl[r][k] * T[k][q] for k in range(6)) for q in range(6)]
               for r in range(6)]
        kg = [[sum(T[k][r] * tmp[k][q] for k in range(6)) for q in range(6)]
              for r in range(6)]
        dofs = [3 * i, 3 * i + 1, 3 * i + 2,
                3 * (i + 1), 3 * (i + 1) + 1, 3 * (i + 1) + 2]
        for r in range(6):
            for q in range(6):
                K[dofs[r]][dofs[q]] += kg[r][q]

    F = [0.0] * nd
    F[3 * (apex_node(cfg) - 1) + 1] = -1.0
    fixed = {0, 1, 3 * (nn - 1), 3 * (nn - 1) + 1}
    free = [i for i in range(nd) if i not in fixed]
    Kf = [[K[r][q] for q in free] for r in free]
    Ff = [F[r] for r in free]
    uf = _spd_solve(Kf, Ff)
    v = -uf[free.index(3 * (apex_node(cfg) - 1) + 1)]
    if v <= 0.0:
        raise ValueError("non-positive apex flexibility in linear anchor")
    return 1.0 / v
