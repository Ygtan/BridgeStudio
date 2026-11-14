#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""K 阶段：大转动梁柱效应（共转列式 CR 梁）ANSYS 同源导出。

FEM-Eigen 的 ``fem_3d_nl.hpp`` 早已实现共转列式梁（随动三元组 + 解析
几何刚度 ``crBeamTangent12``），但只在 ``test_3d_nl.cpp`` 里以硬编码
断言存在，既不能导出结果，也没有 ANSYS 侧对标。本模块把两个经典解析
题目做成**文件驱动、同源导出**的端到端算例 CB3D16：

  CIRCLE : 悬臂梁端弯矩 M = lambda * 2*pi*E*Iz/L
           纯弯曲 elastica。M_full = 2*pi*EI/L 时梁卷成整圆，尖端回到
           根部、转角 2*pi。验证**有限转动**下随动坐标与内力平衡。
  PDELTA : 简支梁轴压 P = lambda*Pcr + 跨中横向力 Q
           Timoshenko 梁柱精确解 y = Q/(2Pk)*(tan(kL/2)-kL/2)。
           验证**解析几何刚度**（P-Delta 放大），放大系数 1 -> ~3.95。

同一份 ``BNL_GROUPS`` / ``BNL_CASES`` 同时导出：
  * ``cb3d16_beam_nl.beam`` -> FEM-Eigen ``beam_nl_probe.exe`` 的输入
  * 每个 case 一份 BEAM188 + NLGEOM APDL -> ANSYS 18.0 批处理

------------------------------------------------------------------
BEAM188 的口径处理（实测诊断，不是猜测）
------------------------------------------------------------------
BEAM188 是 Timoshenko 单元，FEM-Eigen 的 BEAM 是 Euler-Bernoulli。
直接拿默认 BEAM188 + ASEC 对标简支梁，线性基线就有 -5.07e-4 相对差，
且 NLGEOM 开/关完全不变 -> 与几何非线性无关。网格扫描把偏差拆成两项：

  1) 横向剪切锁定：n=16/32/64/128/256 时相对 EB 理论
     -2.96e-3 / -5.07e-4 / +1.07e-4 / +2.60e-4 / +2.98e-4，
     按 1/n^2 收敛到 +2.98e-4（真实 Timoshenko 剪切柔度）。
  2) 真实剪切柔度 delta_s = Q*L/(4*G*A)。

因此 PDELTA 组用 ``KEYOPT(1,3,2)``（三次插值，消除剪切锁定）并把
``MP,GXY`` 提高 1000 倍（令剪切柔度可忽略）；线性基线随即从
-5.07e-4 收敛到 +2.3e-7。CIRCLE 组是纯弯矩、无横向剪力，两项都不
出现，保持默认 KEYOPT(3)=0 与真实 G 即可，实测 FEM vs ANSYS 逐节点
差 4e-10 ~ 4e-9 m。

CIRCLE 的端弯矩加载会让 ANSYS 报一条 ``MINREF`` 收敛参考值提示；
显式 ``CNVTOL,F,1.0E-2,0.005,2`` 给出同样的参考力，warning 归零且
结果逐位不变。

用法见 ``cases/run_cases_beamnl.py``。
"""
from __future__ import annotations

import math
from pathlib import Path

# ------------------------------------------------------------------ 同源参数
# 与 FEM-Eigen/test_3d_nl.cpp::testCRCantileverCircle / testCRBeamColumn
# 同一组量纲（SI：N, m, Pa）。
BNL_GROUPS: dict[str, dict] = {
    "cir": {
        "E": 2.1e11, "G": 8.1e10, "L": 10.0,
        "A": 0.02, "IY": 5e-2, "IZ": 2e-4, "J": 1e-2,
        "NELEM": 8, "LOADSTEPS": 40, "MAXITER": 40, "TOL": 1e-8, "Q": 0.0,
        "keyopt3": 0, "nsubst": 100, "cnvtol_f": 1.0e-2,
        "desc": "Cantilever end-moment roll-up (pure finite rotation)",
    },
    "pd": {
        "E": 2.1e11, "G": 8.1e13, "L": 10.0,
        "A": 20.0, "IY": 5e-2, "IZ": 2e-4, "J": 1e-3,
        "NELEM": 64, "LOADSTEPS": 20, "MAXITER": 40, "TOL": 1e-10, "Q": 2.0e4,
        "keyopt3": 2, "nsubst": 60, "cnvtol_f": None,
        "desc": "Simply supported beam-column (analytic geometric stiffness)",
    },
}

BNL_CASES: list[dict] = [
    {"id": "cir025", "group": "cir", "kind": "CIRCLE", "lam": 0.25,
     "desc": "quarter circle, theta=pi/2"},
    {"id": "cir050", "group": "cir", "kind": "CIRCLE", "lam": 0.50,
     "desc": "half circle, theta=pi"},
    {"id": "cir075", "group": "cir", "kind": "CIRCLE", "lam": 0.75,
     "desc": "three-quarter circle, theta=3pi/2"},
    {"id": "cir100", "group": "cir", "kind": "CIRCLE", "lam": 1.00,
     "desc": "full circle, theta=2pi, tip returns to root"},
    {"id": "pd000", "group": "pd", "kind": "PDELTA", "lam": 0.00,
     "desc": "no axial force, linear EB baseline"},
    {"id": "pd025", "group": "pd", "kind": "PDELTA", "lam": 0.25,
     "desc": "P/Pcr=0.25, mild P-Delta"},
    {"id": "pd050", "group": "pd", "kind": "PDELTA", "lam": 0.50,
     "desc": "P/Pcr=0.50, matches test_3d_nl.cpp beam-column"},
    {"id": "pd075", "group": "pd", "kind": "PDELTA", "lam": 0.75,
     "desc": "P/Pcr=0.75, strong P-Delta amplification"},
]

CASE_ID = "cb3d16_beam_nl"
PARAMS_FILE = CASE_ID + ".beam"


def num(v: float) -> str:
    """APDL / .beam 统一数值格式（纯 ASCII 科学计数）。"""
    return "{:.12E}".format(v)


def group_line(gid: str, g: dict) -> str:
    keys = ("E", "G", "L", "A", "IY", "IZ", "J", "NELEM",
            "LOADSTEPS", "MAXITER", "TOL", "Q")
    return "G {} {}".format(gid, " ".join("{}={}".format(k, num(g[k]))
                                          for k in keys))


def params_text(groups: dict | None = None, cases: list | None = None) -> str:
    """生成 ``*.beam``：组参数 + 工况，供 beam_nl_probe.exe 与 APDL 同源。"""
    groups = groups or BNL_GROUPS
    cases = cases or BNL_CASES
    lines = [
        "# CB3D16 large-rotation beam-column: kernel CR beam vs ANSYS BEAM188+NLGEOM",
        "# G <gid> E G L A IY IZ J NELEM LOADSTEPS MAXITER TOL Q",
        "# C <cid> <gid> <CIRCLE|PDELTA> <lambda>",
    ]
    for gid, g in groups.items():
        lines.append(group_line(gid, g))
    for c in cases:
        lines.append("C {} {} {} {}".format(c["id"], c["group"],
                                            c["kind"], num(c["lam"])))
    return "\n".join(lines) + "\n"


def case_loads(g: dict, c: dict) -> tuple[float, float]:
    """返回 (M, P)；两组荷载都只从 group + lambda 派生，杜绝手工偏差。"""
    L, E, IZ = g["L"], g["E"], g["IZ"]
    if c["kind"] == "CIRCLE":
        return c["lam"] * 2.0 * math.pi * E * IZ / L, 0.0
    if c["kind"] == "PDELTA":
        pcr = math.pi ** 2 * E * IZ / (L * L)
        return 0.0, c["lam"] * pcr
    raise ValueError("unknown kind: " + c["kind"])


def exact_values(g: dict, c: dict) -> dict[str, float]:
    """解析标尺（与 beam_nl_probe.cpp 中同一组闭式公式）。"""
    L, E, IZ, Q = g["L"], g["E"], g["IZ"], g["Q"]
    M, P = case_loads(g, c)
    out: dict[str, float] = {"M": M, "P": P, "Q": Q, "lambda": c["lam"]}
    if c["kind"] == "CIRCLE":
        theta = M * L / (E * IZ)
        R = L / theta if abs(theta) > 1e-14 else float("inf")
        out.update(theta=theta, xtip_ex=R * math.sin(theta),
                   ytip_ex=R * (1.0 - math.cos(theta)), rot_ex=theta)
    else:
        ylin = Q * L ** 3 / (48.0 * E * IZ)
        pcr = math.pi ** 2 * E * IZ / (L * L)
        if abs(P) > 1e-12:
            k = math.sqrt(P / (E * IZ))
            yex = (Q / (2.0 * P * k)) * (math.tan(k * L / 2.0) - k * L / 2.0)
        else:
            yex = ylin
        out.update(Pcr=pcr, ylin=ylin, y_pd_ex=yex,
                   amp_ex=yex / ylin if ylin else 0.0)
    return out


def beamnl_apdl(g: dict, c: dict, *, jobname: str,
                title: str | None = None) -> str:
    """生成单个 case 的 BEAM188 + NLGEOM APDL（纯 ASCII）。

    输出 ``<jobname>_disp.csv``：7 列 [id, UX,UY,UZ,ROTX,ROTY,ROTZ]，
    与 ``beam_nl_probe.exe`` 的 N 行逐列对应。
    """
    n = int(g["NELEM"])
    L = g["L"]
    tip, mid = n + 1, n // 2 + 1
    M, P = case_loads(g, c)
    Ls: list[str] = []
    a = Ls.append
    a("/BATCH")
    a("/TITLE,{} - {}".format(
        (title or "CB3D16 large-rotation beam-column").encode(
            "ascii", "backslashreplace").decode("ascii"),
        c["id"].upper()))
    a("/PREP7")
    a("ET,1,BEAM188")
    a("KEYOPT,1,1,0")
    a("KEYOPT,1,3,{}".format(int(g["keyopt3"])))
    a("! ASEC: integrated section properties, no ANSYS I/J recompute")
    a("SECTYPE,1,BEAM,ASEC")
    # SECDATA: A, IYY, IYZ, IZZ, IW, J, CGY, CGZ, SHY, SHZ
    a("SECDATA,{},{},0,{},0,{},0,0,0,0".format(
        num(g["A"]), num(g["IY"]), num(g["IZ"]), num(g["J"])))
    a("SECNUM,1")
    a("MP,EX,1,{}".format(num(g["E"])))
    a("MP,PRXY,1,0.3")
    a("MP,GXY,1,{}".format(num(g["G"])))
    a("! ---- nodes: straight beam along global X ----")
    for i in range(1, n + 2):
        a("N,{},{},0,0".format(i, num((i - 1) * L / n)))
    a("TYPE,1")
    a("MAT,1")
    for i in range(1, n + 1):
        a("E,{},{}".format(i, i + 1))
    a("! ---- boundary conditions ----")
    if c["kind"] == "CIRCLE":
        a("D,1,ALL,0")
    else:
        for d in ("UX", "UY", "UZ", "ROTX"):
            a("D,1,{},0".format(d))
        for d in ("UY", "UZ"):
            a("D,{},{},0".format(tip, d))
    a("! ---- nodal loads (dead load, same global direction as FEM *CLOAD) ----")
    if c["kind"] == "CIRCLE":
        a("F,{},MZ,{}".format(tip, num(M)))
    else:
        a("F,{},FX,{}".format(tip, num(-P)))
        a("F,{},FY,{}".format(mid, num(-g["Q"])))
    a("FINISH")
    a("/SOLU")
    a("ANTYPE,STATIC")
    a("NLGEOM,ON")
    if g.get("cnvtol_f"):
        # 纯弯矩加载的自动参考力低于 MINREF；显式给出同一参考力可消除
        # 提示而不改变收敛判据（实测结果逐位不变）。
        a("CNVTOL,F,{},0.005,2".format(num(g["cnvtol_f"])))
    ns = int(g["nsubst"])
    a("NSUBST,{},{},{}".format(ns, ns * 5, max(10, ns // 10)))
    a("NEQIT,200")
    a("OUTRES,ALL,LAST")
    a("SOLVE")
    a("FINISH")
    a("! ---- post-processing: machine-readable CSV ----")
    a("/POST1")
    a("SET,LAST")
    a("*DIM,ADSP,ARRAY,{},7".format(n + 1))
    a("*DO,I,1,{}".format(n + 1))
    a(" ADSP(I,1)=I")
    for k, comp in enumerate(("X", "Y", "Z"), start=2):
        a(" *GET,TMP,NODE,I,U,{}".format(comp))
        a(" ADSP(I,{})=TMP".format(k))
    for k, comp in enumerate(("X", "Y", "Z"), start=5):
        a(" *GET,TMP,NODE,I,ROT,{}".format(comp))
        a(" ADSP(I,{})=TMP".format(k))
    a("*ENDDO")
    # *VWRITE 一次写整个数组；外层套 *DO 会重复写首行（历史坑）。
    a("*CFOPEN,{}_disp,csv".format(jobname))
    a("*VWRITE," + ",".join("ADSP(1,{})".format(k) for k in range(1, 8)))
    a(" (1X,F8.0,6(1X,E18.10))")
    a("*CFCLOS")
    a("FINISH")
    a("/EXIT,NOSAVE")
    return "\n".join(Ls) + "\n"


def read_disp_csv(path: Path) -> dict[int, list[float]]:
    """解析 ANSYS ``*_disp.csv``：id -> [UX,UY,UZ,ROTX,ROTY,ROTZ]。"""
    out: dict[int, list[float]] = {}
    for line in path.read_text(encoding="ascii", errors="replace").splitlines():
        p = line.split()
        if not p:
            continue
        try:
            out[int(float(p[0]))] = [float(v) for v in p[1:]]
        except ValueError:
            continue
    return out
