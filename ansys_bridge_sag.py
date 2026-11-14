#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""J 阶段：索垂度 / Ernst 等效弹模的 ANSYS 分段索链对标（CB3D14）。

为什么需要「分段索链」
----------------------
FEM-Eigen 的 ``fem_3d.hpp`` 把**一根** CABLE 单元建成浅垂度弹性悬链线：

    Ln = L0*(1 + T/EA) - w^2*Lh^3/(24*T^2)
    Eeq = E / (1 + w^2*Lh^3*E*A/(12*T^3*L0))       (Ernst 等效弹模)

而 ANSYS 18.0 的 LINK180 是**无垂度**轴向杆，单根单元不可能自行产生
Ernst 软化。要独立验证，只能把一根索离散成 N 段 LINK180、按**水平均布**
把自重加到节点上，让 ``NLGEOM,ON`` 自己收敛出悬链线；此时索链的等效
轴向柔度就包含垂度效应。这与 I 阶段 ``ansys_bridge_erection.stage_apdl``
用的是同一套技术，但这里的对象是**单根本构单元**而非全桥主缆。

同源导出
--------
``SAG_CFG`` 是唯一数据源：它同时生成
  * ``*.sag`` 参数文件 → FEM-Eigen ``cable_sag_probe.exe`` 直接调用内核闭式；
  * 每个工况一份 LINK180 索链 APDL → ANSYS 18.0 批处理；
  * ``*.model.json`` 模型快照（可追溯，不参与求解）。

两侧都不重演对方的力学：FEM 用内核闭式，ANSYS 用分段离散 + 几何非线性。

对标口径
--------
* 每个工况给定水平跨径 Lh（两端等高，弦长 Ln=Lh）、无应力弧长 L0
  与单位水平长度重量 W；ANSYS 初始几何 = 直线 + 正弦扰动，逐段注入
  ``INISTATE,EPEL`` 初应变 ``eps=L_init/L0_seg-1``，靠 NLGEOM 收敛。
* 对比水平力 H（各段轴力水平分量均值）、跨中垂度 f、逐段轴力 T。
* L0 扫 5 个点形成 T(L0) 曲线；用中心差分得到 ANSYS 的 ``dT/dL0``，
  与内核 Ernst 解析切线对标——这是本 benchmark 最敏感的一项。
* 另设 W=0 直杆基线（Eeq/E=1）与 W/2 低重度工况，前者验证退化、
  后者验证「FEM 浅垂度近似 vs ANSYS 分段索链」的偏差随 (V/H)^2 缩放。

实测已知差异（不是错误）
------------------------
内核闭式是**浅垂度一阶近似**（用单一 T 代替沿索变化的张力），ANSYS
分段索链是更精确的离散解。二者差异量级 ``O((V/H)^2)``，实测
V/H=0.0116/0.0231/0.0462 时 dT/T = 4.9e-5 / 2.3e-4 / 7.1e-4；
而 Ernst 修正量是 17%~68% 的**一阶效应**，量级分离 3 个数量级。
因此统一取 ``rtol=2e-3``（与 H/I 阶段相同），切线另用更紧判据。

坐标系：X 水平跨径 / Y 面外 / Z 竖直向上；自重向下对应 ``FZ < 0``。
"""
from __future__ import annotations

import json
import math
from typing import Any

G = 9.81

#: 唯一数据源。A/W 取既有 CB3D05 索参数的同量级值（7850 kg/m3 * 0.006 m2 * g）。
SAG_CFG: dict[str, Any] = dict(
    E=195e9,             # Pa，主缆钢材
    A=0.006,             # m2
    dens=7850.0,         # kg/m3
    LH=100.0,            # m，水平跨径 / 弦长（两端等高）
    NSEG=80,             # ANSYS 分段数
    T_target=1.0e6,      # N，基准工况的目标张力
    amp=0.20,            # m，初始几何正弦扰动幅值
    L0_offsets=(-0.004, -0.002, 0.0, 0.002, 0.004),  # m，L0 扫描
)

#: CB3D15：与 CB3D13 完全相同的自锚式悬索桥参数，只把 cable_dens 从 0
#: 改成 7850（W>0），用来验证「空缆 stage 0 不再退化」。
BRIDGE_CFG: dict[str, Any] = dict(
    system="SELF",
    main_span=120.0, side_span=0.0, sag_ratio=0.10, panel=10.0,
    clearance=5.0, tower_below=6.0, n_tower_seg=4,
    B=12.0, h=3.0, tf=0.028, tw=0.020,
    q_deck=20e3, q2_deck=5e3, cable_dens=7850.0, tower_dens=2500.0,
    side_hangers=True, anchor_offset=8.0,
    main_area_mm2=30000.0, hanger_area_mm2=5000.0,
    tower_b=2.0, tower_h=2.0, lane_count=1,
    erection=True, erection_panels=2,
    title="CB3D15 self-anchored suspension with cable self-weight",
)


def _num(v: float, fmt: str = "{:.15g}") -> str:
    s = fmt.format(float(v))
    return "0" if s in ("-0", "-0.0") else s


def w_of(dens: float, A: float) -> float:
    """索单位水平长度重量 N/m；``W=0`` 即关闭垂度 / Ernst。"""
    return dens * A * G


def c_sag(W: float, LH: float) -> float:
    """``s(T) = cSag/T^2``，抛物线悬链线的垂度缩短系数。"""
    return W * W * LH ** 3 / 24.0


def L0_for_tension(T: float, W: float, cfg: dict[str, Any] | None = None) -> float:
    """闭式反算无应力弧长（与 fem_3d.hpp::cableUnstrainedLengthForTension 同式）。"""
    c = cfg or SAG_CFG
    EA = c["E"] * c["A"]
    LH = c["LH"]
    return (LH + c_sag(W, LH) / (T * T)) / (1.0 + T / EA)


def build_cases(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """生成 7 个工况：5 个 L0 扫描 + 1 个 W=0 直杆 + 1 个 W/2 低重度。"""
    c = cfg or SAG_CFG
    W = w_of(c["dens"], c["A"])
    base = L0_for_tension(c["T_target"], W, c)
    out: list[dict[str, Any]] = []
    for i, off in enumerate(c["L0_offsets"], 1):
        out.append(dict(id="sag_%02d" % i, W=W, L0=base + off,
                        amp=c["amp"], group="sag",
                        note="L0 sweep %+.4f m" % off))
    out.append(dict(id="rod_00", W=0.0, L0=base, amp=0.0, group="rod",
                    note="W=0 straight rod, Eeq/E must be 1"))
    Wh = W * 0.5
    out.append(dict(id="sag_half", W=Wh,
                    L0=L0_for_tension(c["T_target"], Wh, c),
                    amp=c["amp"], group="sag",
                    note="half line weight, checks (V/H)^2 scaling"))
    return out


def sag_param_text(cfg: dict[str, Any], cases: list[dict[str, Any]]) -> str:
    """``*.sag``：FEM-Eigen ``cable_sag_probe.exe`` 的输入（纯 ASCII）。"""
    lines = [
        "# CB3D14 cable sag / Ernst probe",
        "# Single source: BridgeStudio/ansys_bridge_sag.py::SAG_CFG",
        "# Consumed by FEM-Eigen cable_sag_probe.exe (kernel closed-form).",
        "E={}".format(_num(cfg["E"])),
        "A={}".format(_num(cfg["A"])),
        "LH={}".format(_num(cfg["LH"])),
        "NSEG={}".format(int(cfg["NSEG"])),
    ]
    for cs in cases:
        lines.append("C {} {} {} {}".format(
            cs["id"], _num(cs["W"]), _num(cs["L0"]), _num(cs["amp"])))
    return "\n".join(lines) + "\n"


def model_snapshot(cfg: dict[str, Any], cases: list[dict[str, Any]]) -> dict:
    """模型快照：与 .sag / APDL 同一数据源，便于人工复核与追责。"""
    return {
        "id": "cb3d14_cable_sag",
        "kind": "single CABLE element / segmented LINK180 chain",
        "units": {"E": "Pa", "A": "m2", "W": "N/m", "LH": "m", "L0": "m",
                  "T": "N", "f": "m", "Eeq": "Pa"},
        "material": {"E": cfg["E"], "dens": cfg["dens"]},
        "real": {"A": cfg["A"], "W": w_of(cfg["dens"], cfg["A"])},
        "geometry": {"LH": cfg["LH"], "NSEG": cfg["NSEG"],
                     "perturb_amp": cfg["amp"]},
        "T_target": cfg["T_target"],
        "cases": [dict(cs) for cs in cases],
        "ansys": {"element": "LINK180", "KEYOPT_2": 0, "KEYOPT_3": 0,
                  "NLGEOM": "ON", "NSUBST": [100, 500, 20], "NEQIT": 200,
                  "initial_state": "INISTATE,SET,DTYP,EPEL + per-element EPEL,X"},
    }


def sag_apdl(cs: dict[str, Any], cfg: dict[str, Any] | None = None,
             jobname: str | None = None) -> tuple[str, dict[str, Any]]:
    """生成单个工况的分段 LINK180 + NLGEOM APDL。

    初始几何 = 直线 + ``amp*sin(pi*k/N)``（W=0 基线取 amp=0）；节点自重按
    水平均布施加（内部节点 ``W*dx``、两端 ``W*dx/2``）；每段下料长度
    ``L0_seg = L0/N``，初应变 ``eps = L_init/L0_seg - 1``。
    """
    c = cfg or SAG_CFG
    E, A, LH, N = c["E"], c["A"], c["LH"], int(c["NSEG"])
    W, L0, amp = cs["W"], cs["L0"], cs["amp"]
    job = jobname or cs["id"]
    dx = LH / N
    z0 = [-amp * math.sin(math.pi * k / N) for k in range(N + 1)]
    L0seg = L0 / N
    eps = [math.hypot(dx, z0[k + 1] - z0[k]) / L0seg - 1.0 for k in range(N)]

    lines: list[str] = []
    a = lines.append
    a("/TITLE,CB3D14 cable sag Ernst probe - {}".format(cs["id"]))
    a("/PREP7")
    a("ET,1,LINK180")
    a("KEYOPT,1,2,0")
    a("! KEYOPT(3)=0: every segment stays in tension, no on/off switching")
    a("R,1,{}".format(_num(A)))
    a("MP,EX,1,{}".format(_num(E)))
    for k in range(N + 1):
        a("N,{}, {},0,{}".format(k + 1, _num(k * dx), _num(z0[k])))
    for k in range(N):
        a("E,{},{}".format(k + 1, k + 2))
    a("! Both ends fixed in X/Z; out-of-plane UY fixed everywhere")
    a("D,ALL,UY,0")
    a("D,1,UX,0")
    a("D,1,UZ,0")
    a("D,{},UX,0".format(N + 1))
    a("D,{},UZ,0".format(N + 1))
    a("INISTATE,SET,DTYP,EPEL")
    for k, ep in enumerate(eps, 1):
        a("INISTATE,DEFINE,{},,EPEL,X,{}".format(k, _num(ep)))
    a("/SOLU")
    a("ANTYPE,STATIC")
    a("NLGEOM,ON")
    a("NSUBST,100,500,20")
    a("NEQIT,200")
    a("OUTRES,ALL,LAST")
    if W > 0.0:
        a("! Horizontal-uniform self weight, downward = -FZ")
        for k in range(1, N + 2):
            q = W * dx / 2.0 if k in (1, N + 1) else W * dx
            a("F,{},FZ,{}".format(k, _num(-q, "{:.10g}")))
    a("SOLVE")
    a("/POST1")
    a("SET,LAST")
    a("*DIM,ADSP,ARRAY,{},4".format(N + 1))
    for k in range(1, N + 2):
        a(" ADSP({},1)={}".format(k, k))
        a(" *GET,ADSP({},2),NODE,{},U,X".format(k, k))
        a(" *GET,ADSP({},3),NODE,{},U,Y".format(k, k))
        a(" *GET,ADSP({},4),NODE,{},U,Z".format(k, k))
    a("*CFOPEN,{}_disp,csv".format(job))
    a("*VWRITE,ADSP(1,1),ADSP(1,2),ADSP(1,3),ADSP(1,4)")
    a(" (1X,F8.0,3(1X,E18.10))")
    a("*CFCLOS")
    a("ETABLE,LKN,SMISC,1")
    a("*DIM,ALK,ARRAY,{},2".format(N))
    for k in range(1, N + 1):
        a(" ALK({},1)={}".format(k, k))
        a(" *GET,ALK({},2),ELEM,{},ETAB,LKN".format(k, k))
    a("*CFOPEN,{}_link,csv".format(job))
    a("*VWRITE,ALK(1,1),ALK(1,2)")
    a(" (1X,F8.0,1(1X,E18.10))")
    a("*CFCLOS")
    a("FINISH")
    info = {"z_init": z0, "eps": eps, "L0seg": L0seg, "dx": dx,
            "N": N, "job": job, "W": W, "L0": L0}
    return "\n".join(lines) + "\n", info


def read_csv_table(path) -> dict[int, list[float]]:
    """ANSYS ``*VWRITE`` CSV：首列整数 id，其余为浮点。"""
    out: dict[int, list[float]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        p = line.split()
        if not p:
            continue
        try:
            out[int(float(p[0]))] = [float(v) for v in p[1:]]
        except ValueError:
            continue
    return out


def sag_from_ansys(disp: dict[int, list[float]], link: dict[int, list[float]],
                   info: dict[str, Any]) -> dict[str, Any]:
    """从冻结 CSV 还原水平力 H / 垂度 f / 逐段轴力 T。"""
    N = info["N"]
    z0 = info["z_init"]
    dx = info["dx"]
    if any(k not in disp for k in range(1, N + 2)) or \
       any(k not in link for k in range(1, N + 1)):
        raise ValueError("ANSYS CSV 节点/单元数据不完整")
    x = [(k - 1) * dx + disp[k][0] for k in range(1, N + 2)]
    z = [z0[k - 1] + disp[k][2] for k in range(1, N + 2)]
    T = [link[k][0] for k in range(1, N + 1)]
    Hs = []
    for k in range(N):
        ddx, ddz = x[k + 1] - x[k], z[k + 1] - z[k]
        Lk = math.hypot(ddx, ddz)
        Hs.append(T[k] * abs(ddx) / Lk if Lk > 0 else 0.0)
    return {
        "H": sum(Hs) / N,
        "f": z[0] - min(z),
        "T": T,
        "T_mid": T[N // 2],
        "T_min": min(T), "T_max": max(T),
        "H_spread": max(Hs) - min(Hs),
        "z": z, "x": x, "H_seg": Hs,
    }


def bridge_report(res: dict, res_w0: dict | None = None) -> str:
    """CB3D15：W>0 时空缆 stage 0 的空缆态判据报告。"""
    lines: list[str] = []
    a = lines.append
    a("=" * 92)
    a("CB3D15  self-anchored suspension WITH cable self-weight (W>0)")
    a("=" * 92)
    er = res.get("erection", [])
    if not er:
        a("[FAIL] no [ERECTION] section")
        return "\n".join(lines) + "\n"
    s0 = er[0]
    a("stage 0 (empty cable): H=%.6g kN   sag=%.9g m   midz=%.6g m" % (
        s0.get("H", 0.0) / 1e3, s0.get("sag", 0.0), s0.get("midz", 0.0)))
    a("kernel H bisection lower bound = 0.1 kN (cable_solver.cpp: lo=1e2)")
    ok = (s0.get("H", 0.0) > 1e3 and s0.get("sag", 0.0) > 1e-6)
    a("[%s] empty-cable stage is a real catenary, not the degenerate straight state"
      % ("PASS" if ok else "FAIL"))
    a("")
    a("stage   name      H (kN)        sag (m)       midz (m)")
    for r in er:
        a("  %2d    %-8s  %13.6g  %13.9g  %12.6g" % (
            r["stage"], r["name"], r.get("H", 0.0) / 1e3,
            r.get("sag", 0.0), r.get("midz", 0.0)))
    if res_w0 is not None and res_w0.get("erection"):
        a("")
        a("W=0 reference (CB3D13 caliber): stage 0 H=%.6g kN, sag=%.9g m" % (
            res_w0["erection"][0].get("H", 0.0) / 1e3,
            res_w0["erection"][0].get("sag", 0.0)))
        a("final stage H: W>0 = %.6g kN, W=0 = %.6g kN, delta = %+.4g %%" % (
            er[-1].get("H", 0.0) / 1e3, res_w0["erection"][-1].get("H", 0.0) / 1e3,
            100.0 * (er[-1].get("H", 0.0) / res_w0["erection"][-1].get("H", 1.0) - 1.0)))
    return "\n".join(lines) + "\n"
