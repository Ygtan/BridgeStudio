#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""3D real distributed loads / selfweight: same-source FEM-Eigen .dat + ANSYS APDL.

E-stage module. Deliberately SEPARATE from ``ansys_bridge_3d.py`` so the
already-verified A/B/C/D 3D chain (nodalized loads) is untouched.

Data flow (single source of truth = one ModelSpec3D):
    build_box_beam_3d_loads() -> ModelSpec3D
        ├─ to_dat()                -> *.dat   (*ELEMENT_LOAD / *GRAVITY /
        │                                     *SELF_WEIGHT / DENS / ADDMASS)
        └─ spec3d_loads_to_apdl() -> *.inp   (SFBEAM / MP,DENS / ACEL / ADDMAS)
    FEM-Eigen example_3d_report.exe -> *.report.txt
    ANSYS180 BEAM4                  -> *_disp/_reac/_elem.csv
    compare_3d_loads()              -> compare_report.txt

ANSYS semantics calibrated by measurement (ansys_probe/sfbeam_probe.py):
  * BEAM4 ``SFBEAM,ELEM,1,PRES,+q,+q`` produces a GLOBAL -Z UDL and gives
    exactly the Euler-Bernoulli closed form (consistent nodal loads incl. FEF).
  * ``MP,DENS`` + ``ACEL,0,0,9.81`` gives EXACTLY the same result as an
    equivalent ``SFBEAM`` with q = DENS*A*9.81.
  * BEAM4 face 2 (lateral Y) is NOT usable as a UDL here; APDL cross-checks
    therefore support GZ direction and pure -Z gravity only. Other directions
    are covered by the kernel-level closed-form suite verify_kernel_loads.py.

FEM-Eigen 3D side (fem_3d.hpp, E-stage):
  * ``*ELEMENT_LOAD <range> <dir> <q1> <q2>``, dir in GX/GY/GZ/LX/LY/LZ
  * ``*GRAVITY <gx> <gy> <gz>`` (default 0 = OFF, unlike 2D's gy=-9.81)
  * ``*SELF_WEIGHT <ALL|elem> <RHO|MASS> <value>`` (adds to automatic weight)
  * automatic weight = (DENS*A + ADDMASS)*|g|, distributed along g
  * ``beam3DLocalEndForces`` subtracts the fixed-end forces, so element
    end forces are physical (not just restoring forces).
"""
from __future__ import annotations

import math
from pathlib import Path

from model_spec_3d import (ElemLoad3D, ModelSpec3D, SelfWeight3D,
                           validate_model_3d)
from ansys_bridge_3d import (DOF3_NAME, FORCE_KEYS, _dofs, _fmt, _num,
                             build_box_beam_3d, parse_ansys_csv_3d,
                             parse_fem_report_3d, spec3d_to_apdl)


# --------------------------------------------------------------------------
# model builder
# --------------------------------------------------------------------------
def build_box_beam_3d_loads(*,
                            spans: tuple[float, ...] = (30.0, 30.0, 30.0),
                            n_elem_per_span: int = 10,
                            B: float = 8.0, h: float = 2.0,
                            tw: float = 0.02, tf: float = 0.025,
                            E: float = 2.06e11, nu: float = 0.2,
                            q_kN: float = 20.0,
                            q2_kN: float | None = None,
                            direction: str = "GZ",
                            dens: float = 0.0,
                            addmass: float = 0.0,
                            gravity: tuple[float, float, float] = (0.0, 0.0, 0.0),
                            self_weights: tuple[dict, ...] = (),
                            extra_loads: tuple[tuple[int, int, float], ...] = (),
                            title: str = "3D box beam with real distributed loads",
                            ) -> ModelSpec3D:
    """Three-span box beam whose UDL is a REAL *ELEMENT_LOAD, not nodalized.

    Geometry / supports / section reuse ``build_box_beam_3d`` verbatim.
    ``q2_kN`` enables a trapezoidal load (i/j end of EVERY element).
    """
    model = build_box_beam_3d(
        spans=spans, n_elem_per_span=n_elem_per_span,
        B=B, h=h, tw=tw, tf=tf, E=E, nu=nu,
        udl_kN=0.0,                # nodalized UDL off -> no double counting
        extra_loads=extra_loads, title=title)
    # build_box_beam_3d emits q=0 nodal loads; drop them for a clean .dat.
    model.loads = [ld for ld in model.loads if ld.value != 0.0]

    q1 = -abs(q_kN) * 1000.0
    q2 = -abs(q2_kN if q2_kN is not None else q_kN) * 1000.0
    model.elem_loads = [ElemLoad3D(e_from=1, e_to=len(model.elements),
                                   dir=direction, q1=q1, q2=q2)]
    model.self_weights = [SelfWeight3D(**dict(sw)) for sw in self_weights]
    model.gravity = tuple(float(v) for v in gravity)   # type: ignore[assignment]
    if dens:
        model.materials[0].dens = float(dens)
    if addmass:
        model.reals[0].ADDMASS = float(addmass)

    errs, warnings = validate_model_3d(model)
    if errs:
        raise ValueError("3D 分布荷载模型校验失败: " + "; ".join(errs))
    return model


# --------------------------------------------------------------------------
# APDL exporter
# --------------------------------------------------------------------------
def _sfbeam_lines(model: ModelSpec3D) -> list[str]:
    """Collapse *ELEMENT_LOAD + *SELF_WEIGHT into one SFBEAM per element.

    ANSYS applies the LAST SFBEAM on a given face, so multiple loads must be
    summed here instead of being emitted as separate commands.
    """
    gx, gy, gz = model.gravity
    gmag = math.sqrt(gx * gx + gy * gy + gz * gz)
    sec = model.reals[0]
    beam_ids = [e.id for e in model.elements]
    pres: dict[int, list[float]] = {eid: [0.0, 0.0] for eid in beam_ids}

    for el in model.elem_loads:
        if el.dir.upper() != "GZ":
            raise ValueError(
                "APDL 对标仅支持 GZ 方向分布荷载（BEAM4 SFBEAM LKEY=1 实测对应 -Z）；"
                "其它方向请用 verify_kernel_loads.py 的闭式解验证: " + el.dir)
        hi = el.e_to if el.e_to is not None else el.e_from
        for eid in range(el.e_from, hi + 1):
            if eid not in pres:
                raise ValueError("*ELEMENT_LOAD 引用了不存在的单元 %d" % eid)
            pres[eid][0] += -el.q1     # q<0 (downward) -> positive PRES
            pres[eid][1] += -el.q2

    if self_weights_w := model.self_weights:
        if gmag <= 0.0:
            raise ValueError("*SELF_WEIGHT 需要同时写 *GRAVITY")
        if gx != 0.0 or gy != 0.0 or gz >= 0.0:
            raise ValueError("APDL 对标仅支持纯 -Z 重力；当前 gravity=%r" %
                             (model.gravity,))
        for sw in self_weights_w:
            rule = sw.rule.upper()
            if rule == "RHO":
                w = sw.value * gmag * sec.A
            elif rule == "MASS":
                w = sw.value * gmag
            else:
                raise ValueError("*SELF_WEIGHT 规则必须是 RHO 或 MASS: " + sw.rule)
            targets = beam_ids if sw.who.upper() == "ALL" else [int(sw.who)]
            for eid in targets:
                if eid not in pres:
                    raise ValueError("*SELF_WEIGHT 引用了不存在的单元 %d" % eid)
                pres[eid][0] += w
                pres[eid][1] += w

    return ["SFBEAM,%d,1,PRES,%s,%s" % (eid, _num(pres[eid][0]), _num(pres[eid][1]))
            for eid in sorted(pres)
            if pres[eid][0] != 0.0 or pres[eid][1] != 0.0]


def spec3d_loads_to_apdl(model: ModelSpec3D, *, jobname: str,
                         title: str | None = None) -> str:
    """BEAM4 APDL for a model carrying real distributed loads / selfweight.

    Reuses the fully verified ``spec3d_to_apdl(... element="BEAM4")`` output
    (including all 12 ETABLE columns and the three *VWRITE CSV exports) and
    injects only four load-related commands at well-defined anchors.
    """
    errs, _ = validate_model_3d(model)
    if errs:
        raise ValueError("模型校验失败: " + "; ".join(errs))
    if model.stages:
        raise ValueError("3D 内核拒绝「阶段 + 分布荷载/自重」组合，请改用无阶段模型")
    gx, gy, gz = model.gravity
    gmag = math.sqrt(gx * gx + gy * gy + gz * gz)
    if gmag > 0.0 and (gx != 0.0 or gy != 0.0):
        raise ValueError("APDL 对标仅支持纯 Z 重力，当前 gravity=%r" % (model.gravity,))

    base = spec3d_to_apdl(model, jobname=jobname, element="BEAM4", title=title)
    sec = model.reals[0]
    dens = model.materials[0].dens
    addmass = sec.ADDMASS
    sf_lines = _sfbeam_lines(model)

    out: list[str] = []
    sf_done = False
    n_rmore = n_gxy = n_static = 0
    for ln in base.splitlines():
        if ln.startswith("RMORE,") and addmass:
            # BEAM4 RMORE = ISTRN, IXX(=J), SHEARZ, SHEARY, SPIN, ADDMAS.
            # Rebuild explicitly: the upstream line carries a trailing extra
            # 0.0, so blindly replacing the last token would NOT set ADDMAS.
            ln = "RMORE,0.0,{},0.0,0.0,0.0,{}".format(_num(sec.J), _num(addmass))
            n_rmore += 1
        if ln.strip() == "FINISH" and not sf_done:
            # SFBEAM is a PREP7 command: put it BEFORE the first FINISH.
            out.append("! ---- Distributed loads (*ELEMENT_LOAD + *SELF_WEIGHT) ----")
            out.extend(sf_lines)
            sf_done = True
        out.append(ln)
        if ln.startswith("MP,GXY,"):
            n_gxy += 1
            if dens:
                out.append("MP,DENS,1,{}".format(_num(dens)))
        if ln.strip() == "ANTYPE,STATIC":
            n_static += 1
            if gmag > 0.0:
                # FEM *GRAVITY g  <->  ANSYS ACEL = -g (inertial acceleration)
                neg = lambda v: 0.0 if v == 0.0 else -v   # noqa: E731
                out.append("ACEL,{},{},{}".format(
                    _num(neg(gx)), _num(neg(gy)), _num(neg(gz))))

    if not (sf_done and n_gxy == 1 and n_static == 1) or (addmass and n_rmore != 1):
        raise RuntimeError("APDL 锚点注入失败（spec3d_to_apdl 输出格式已变化）")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------
def total_vertical_load(model: ModelSpec3D) -> tuple[float, list[str]]:
    """Total GLOBAL -Z applied load [N], with a human-readable breakdown."""
    sec = model.reals[0]
    lens = {}
    nodes = {n.id: n for n in model.nodes}
    for e in model.elements:
        n1, n2 = nodes[e.n1], nodes[e.n2]
        lens[e.id] = math.sqrt((n2.x - n1.x) ** 2 + (n2.y - n1.y) ** 2 +
                               (n2.z - n1.z) ** 2)
    lines: list[str] = []
    total = 0.0

    s_udl = 0.0
    for el in model.elem_loads:
        hi = el.e_to if el.e_to is not None else el.e_from
        for eid in range(el.e_from, hi + 1):
            s_udl += 0.5 * (el.q1 + el.q2) * lens[eid]
    total += s_udl
    lines.append("  *ELEMENT_LOAD 竖向合力      {:>18.6f} N".format(s_udl))

    gx, gy, gz = model.gravity
    gmag = math.sqrt(gx * gx + gy * gy + gz * gz)
    s_auto = 0.0
    if gmag > 0.0:
        line_mass = sec.A * model.materials[0].dens + sec.ADDMASS
        s_auto = line_mass * gz * sum(lens.values())
        total += s_auto
    lines.append("  DENS/ADDMASS 自重竖向合力   {:>18.6f} N".format(s_auto))

    s_sw = 0.0
    for sw in model.self_weights:
        rule = sw.rule.upper()
        w = (sw.value * gmag * sec.A) if rule == "RHO" else (sw.value * gmag)
        targets = lens.keys() if sw.who.upper() == "ALL" else [int(sw.who)]
        s_sw += gz / gmag * w * sum(lens[t] for t in targets)
    total += s_sw
    lines.append("  *SELF_WEIGHT 竖向合力      {:>18.6f} N".format(s_sw))

    s_node = sum(ld.value for ld in model.loads if ld.dof == 3)
    total += s_node
    lines.append("  *CLOAD 节点竖向力           {:>18.6f} N".format(s_node))
    lines.append("  外荷载 ΣFZ                  {:>18.6f} N".format(total))
    return total, lines


def compare_3d_loads(model: ModelSpec3D, fem: dict,
                     adisp: dict, areac: dict, aelem: dict,
                     *, stem: str, tol: float = 1e-3) -> tuple[str, bool]:
    """FEM-Eigen 3D vs ANSYS BEAM4, plus a vertical force-balance self-check."""
    lines: list[str] = []
    add = lines.append
    ok_all = True
    add("=" * 78)
    add("3D 真实分布荷载 / 自重 三方校核: " + stem)
    add("  FEM-Eigen 3D (example_3d_report)  <->  ANSYS 18.0 BEAM4")
    add("  荷载: *ELEMENT_LOAD + *GRAVITY(DENS/ADDMASS) + *SELF_WEIGHT")
    add("=" * 78)

    # [1] nodal displacements, all 6 DOFs
    n_cmp = 0
    max_abs = 0.0
    scale = 1e-300
    for nd in sorted(fem["disp"]):
        f = fem["disp"][nd]
        a = adisp.get(nd, [0.0] * 6)
        for k in range(min(len(f), len(a))):
            max_abs = max(max_abs, abs(f[k] - a[k]))
            scale = max(scale, abs(a[k]))
            n_cmp += 1
    rel = max_abs / scale
    ok = rel <= tol
    ok_all &= ok
    add("[1] 节点位移 ({} 个分量): 最大绝对差 {:.6e} m/rad, 最大量级 {:.6e}, "
        "比值 {:.3e}  {}".format(n_cmp, max_abs, scale, rel,
                                 "PASS" if ok else "FAIL"))

    # [2] element i-end forces (compare magnitude: sign conventions differ)
    pairs = []
    for e in sorted(fem["force"]):
        if e not in aelem:
            continue
        f, a = fem["force"][e], aelem[e]
        for k, key in enumerate(FORCE_KEYS):
            pairs.append((abs(f.get(key, 0.0)), abs(a[k])))
    if pairs:
        err_max = max(max(abs(x - y) for x, y in [p]) for p in pairs)
        scale_f = max(max(x, y) for x, y in pairs) or 1.0
        rel_f = err_max / scale_f
        ok = rel_f <= tol
        ok_all &= ok
        add("[2] 单元 i 端内力 ({} 项, 比幅值): 最大差 {:.6e}, 相对 {:.3e}  {}".format(
            len(pairs), err_max, rel_f, "PASS" if ok else "FAIL"))

    # [3] vertical force balance using ANSYS support reactions only
    sup = sorted({b.node for b in model.boundaries
                  if 3 in _dofs(*b.dofs) and b.k == 0})
    rf = sum(areac.get(nd, [0.0] * 6)[2] for nd in sup)
    ext, breakdown = total_vertical_load(model)
    bal = abs(rf + ext)
    scale_b = max(abs(ext), 1.0)
    rel_b = bal / scale_b
    ok = rel_b <= tol
    ok_all &= ok
    add("[3] 竖向力平衡（仅统计 {} 个支座节点 {}）".format(len(sup), sup))
    for s in breakdown:
        add(s)
    add("  ANSYS 支座反力 ΣRF_FZ        {:>18.6f} N".format(rf))
    add("  残差 |ΣRF + ΣFZ| = {:.6e} N, 相对 {:.3e}  {}".format(
        bal, rel_b, "PASS" if ok else "FAIL"))
    add("  注: *GET,NODE,RF,FZ 在「无约束但有外荷载」的节点返回节点荷载残差，")
    add("      故只统计竖向支座节点（C 阶段实测踩过的坑）。")

    add("=" * 78)
    add("总判定: " + ("PASS —— 3D 建模 -> FEM-Eigen 真实分布荷载/自重计算 -> "
                    "ANSYS BEAM4 校核 一致" if ok_all else "FAIL —— 存在超差项"))
    add("=" * 78)
    return "\n".join(lines) + "\n", bool(ok_all)
