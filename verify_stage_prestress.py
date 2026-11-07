"""verify_stage_prestress.py
============================
P3 端到端回归：施工阶段预应力（直线筋 + 二次抛物线曲线筋，等效荷载法）。

校验内容
--------
1. 离线（建模与导出）
   - PRESTRESS= 正确写出：连续单元压缩成 a-b、loss=1 时不写 :loss、
     loss≠1 时写；直线筋走位置模式、曲线筋走关键字模式（含 S=）。
2. 在线（FEM-Eigen 3D）
   - 内核输出 stage prestress force 段，Pe = P·loss 解析正确。
   - SolveResult.stage_prestress 承接、checks 出「有效预加力为正」与合计。
   - 曲线筋跨中上拱 = −Pe·s·L²/(12EI)（s<0 → 上拱），闭式解对拍。

理论基准（内核 test_3d [25]/[26] 已锁，这里只验链路）：
   直线筋（s=0）只给轴压 + 锚固端偏心弯矩，不产生上拱；上拱来自曲线筋
   （s≠0）的跨内反向均布力 + 两端锚固切线竖向力。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fem_eigen_3d import fem_eigen_3d_available, solve_3d
from model_spec_3d import (Boundary3D, Element3D, ElemTypeDef3D, Influence3D,
                           Lane3D, Material3D, ModelSpec3D, Node3D, NodalLoad3D,
                           Prestress3D, RealConst3D, Stage3D)

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((ok, name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def _beam_model(loss: float = 0.85) -> ModelSpec3D:
    """单根 10 m 简支梁（沿 x，竖向弯曲绕 y）+ 一根直线束在下缘（e<0）。"""
    m = ModelSpec3D(title="prestress_probe")
    m.nodes = [Node3D(id=1, x=0.0, y=0.0, z=0.0),
               Node3D(id=2, x=10.0, y=0.0, z=0.0)]
    m.materials = [Material3D(id=1, E=2.0e11)]
    m.reals = [RealConst3D(id=1, A=0.01, IY=0.01, IZ=0.01, J=1e-4, G=8.0e10)]
    m.etypes = [ElemTypeDef3D(id=1, kind="BEAM", mat=1, real=1)]
    m.elements = [Element3D(id=1, etype=1, n1=1, n2=2)]
    m.boundaries = [
        Boundary3D(node=1, dofs=(1, 3), value=0.0),
        Boundary3D(node=1, dofs=(4, 4), value=0.0),
        Boundary3D(node=1, dofs=(6, 6), value=0.0),
        Boundary3D(node=2, dofs=(2, 3), value=0.0),
        Boundary3D(node=2, dofs=(4, 4), value=0.0),
        Boundary3D(node=2, dofs=(6, 6), value=0.0),
    ]
    m.stages = [Stage3D(id=1, activate=[1],
                        prestress=[Prestress3D(elems=[1], P=1.0e6, e=-0.5,
                                               loss=loss)])]
    return m


def _curved_model() -> ModelSpec3D:
    """3 节点 2 单元简支梁 + 曲线筋（端部偏心 e=0、矢高 s<0 → 上拱）。"""
    m = ModelSpec3D(title="prestress_curved_probe")
    m.nodes = [Node3D(id=1, x=0.0, y=0.0, z=0.0),
               Node3D(id=2, x=5.0, y=0.0, z=0.0),
               Node3D(id=3, x=10.0, y=0.0, z=0.0)]
    m.materials = [Material3D(id=1, E=2.0e11)]
    m.reals = [RealConst3D(id=1, A=0.01, IY=0.01, IZ=0.01, J=1e-4, G=8.0e10)]
    m.etypes = [ElemTypeDef3D(id=1, kind="BEAM", mat=1, real=1)]
    m.elements = [Element3D(id=1, etype=1, n1=1, n2=2),
                  Element3D(id=2, etype=1, n1=2, n2=3)]
    m.boundaries = [
        Boundary3D(node=1, dofs=(1, 3), value=0.0),   # ux,uy,uz
        Boundary3D(node=1, dofs=(4, 4), value=0.0),   # rx
        Boundary3D(node=1, dofs=(6, 6), value=0.0),   # rz
        Boundary3D(node=3, dofs=(2, 3), value=0.0),   # uy,uz
        Boundary3D(node=3, dofs=(4, 4), value=0.0),   # rx
        Boundary3D(node=3, dofs=(6, 6), value=0.0),   # rz
        Boundary3D(node=2, dofs=(2, 2), value=0.0),   # uy（跨中只锁面外）
        Boundary3D(node=2, dofs=(4, 4), value=0.0),   # rx
        Boundary3D(node=2, dofs=(6, 6), value=0.0),   # rz
    ]
    m.stages = [Stage3D(id=1, activate=[1, 2],
                        prestress=[Prestress3D(elems=[1, 2], P=1.0e6, e=0.0,
                                               s=-0.3, loss=1.0)])]
    return m


def stress_logic() -> None:
    """束面积只活在 Python 模型里。没给面积不得判成通过。"""
    from model_doc import ModelDoc
    from model_spec_3d import Prestress3D, Stage3D
    from result_3d import SolveResult

    spoken, _, diag = ModelDoc.from_text(
        "跨度10米简支梁，曲线预应力，张拉力1000千牛，钢束面积1400平方毫米")
    check("钢束面积 1400 平方毫米",
          abs(spoken.params["Ap_mm2"] - 1400.0) < 1e-9
          and abs(spoken.model.stages[0].prestress[0].Ap - 1.4e-3) < 1e-12,
          str(spoken.params.get("Ap_mm2")))
    check("面积不写进 .dat",
          "AP=" not in spoken.model.to_dat().upper()
          and "1400" not in spoken.model.to_dat(), "")
    check("未写强度时按 1860 MPa",
          spoken.params["fpk_MPa"] == 1860.0
          and any("1860" in n for n in diag.notes), "")

    class Holder:
        pass

    res = SolveResult(kind="grillage3d")
    res.stage_prestress = {(1, 1): {"Pe": 1.0e6}}
    bare = Holder()
    bare.stages = [Stage3D(id=1, prestress=[Prestress3D(elems=[1], P=1.0e6)])]
    missed = res._checks_stage_prestress(bare)
    info = next(c for c in missed if c.name == "张拉控制应力")
    check("没给面积时张拉控制应力标成没做",
          info.ok and info.level == "info" and "没做" in info.detail, info.detail)

    def judge(ap_m2: float):
        holder = Holder()
        holder.stages = [Stage3D(
            id=1, prestress=[Prestress3D(elems=[1], P=1.0e6, Ap=ap_m2)])]
        return next(c for c in res._checks_stage_prestress(holder)
                    if "0.75 fpk" in c.name)

    ok = judge(1.4e-3)
    check("1400 mm² 时 σpe 低于 0.75 fpk",
          ok.ok and abs(ok.value - 1.0e6 / 1.4e-3) < 1.0,
          f"σ={ok.value}")
    bad = judge(1.0e-4)
    check("100 mm² 时 σpe 超过 0.75 fpk",
          (not bad.ok) and bad.level == "error" and bad.value > bad.limit,
          f"σ={bad.value} cap={bad.limit}")


def offline() -> None:
    print("[离线] PRESTRESS= 写出")
    text = _beam_model(loss=0.85).to_dat()
    check("PRESTRESS= 含 P:e:loss（loss≠1 时写）",
          "PRESTRESS=1:1000000:-0.5:0.85" in text, "")
    text1 = _beam_model(loss=1.0).to_dat()
    check("loss=1 时省略 :loss（缺省值不外露）",
          "PRESTRESS=1:1000000:-0.5" in text1
          and ":-0.5:1" not in text1, "")

    # 多单元连续压缩成 a-b
    m = _beam_model()
    m.nodes += [Node3D(id=3, x=20.0, y=0.0, z=0.0),
                Node3D(id=4, x=30.0, y=0.0, z=0.0)]
    m.elements += [Element3D(id=2, etype=1, n1=2, n2=3),
                   Element3D(id=3, etype=1, n1=3, n2=4)]
    m.stages = [Stage3D(id=1, activate=[1, 2, 3],
                        prestress=[Prestress3D(elems=[1, 2, 3], P=1.0e6,
                                               e=-0.5, loss=1.0)])]
    text3 = m.to_dat()
    check("连续单元压缩成 1-3", "PRESTRESS=1-3:1000000:-0.5" in text3, "")

    # 曲线筋走关键字模式（矢高 S 必须命名，否则与 loss 位置冲突）
    tc = _curved_model().to_dat()
    check("曲线筋关键字模式含 S=",
          "PRESTRESS=1-2:P=1000000:E=0:S=-0.3" in tc, tc.split("PRESTRESS=")[-1])
    stress_logic()


def online() -> None:
    print("[在线] FEM-Eigen 3D")
    raw = solve_3d(_beam_model(), out_dir="out/verify_prestress",
                   dat_name="prestress.dat")
    sp = raw.get("stage_prestress", {})
    check("内核输出 stage prestress force 段", bool(sp),
          str(sp))
    check("Pe = P·loss = 850 kN", sp.get((1, 1), {}).get("Pe", 0.0) == 850000.0,
          f"{sp.get((1, 1), {})}")

    import solver_3d
    from result_3d import SolveResult
    m = _beam_model()
    res = solver_3d.solve(m, out_dir="out/verify_prestress", kind="grillage3d")
    check("SolveResult.stage_prestress 承接",
          res.stage_prestress == {(1, 1): {"Pe": 850000.0}},
          str(res.stage_prestress))
    names = [c.name for c in res.checks(m)]
    check("checks 出「有效预加力为正」",
          any("有效预加力为正" in n for n in names), "")
    check("checks 出「有效预加力合计」",
          any("有效预加力合计" in n for n in names), "")

    # 曲线筋跨中上拱闭式解（s<0 → 上拱；dof 3 = uz，节点 2 = 跨中）
    rc = solve_3d(_curved_model(), out_dir="out/verify_prestress",
                  dat_name="curved.dat")
    uz = rc.get("final_disp", {}).get((2, 3), 0.0)
    want = -1.0e6 * (-0.3) * 100.0 / (12.0 * 2.0e11 * 0.01)   # 1.25e-3 m
    check("曲线筋跨中上拱 = −Pe·s·L²/(12EI)",
          abs(uz - want) < 1e-9, f"uz={uz:.6e} want={want:.6e}")
    check("方向：s<0 → 上拱（uz>0）", uz > 0, f"uz={uz:.6e}")

    from model_doc import ModelDoc
    doc, _, _ = ModelDoc.from_text(
        "跨度10米简支梁，曲线预应力，张拉力1000千牛，矢高0.3米向下")
    spoken = solve_3d(doc.model, out_dir="out/verify_prestress", dat_name="spoken.dat")
    uz_s = spoken.get("final_disp", {}).get((2, 3), 0.0)
    check("自然语言预应力梁跨中上拱与闭式解一致",
          abs(uz_s - want) < 1e-9, f"uz={uz_s:.6e} want={want:.6e}")

    creep_doc, _, _ = ModelDoc.from_text(
        "跨度10米简支梁，曲线预应力，张拉力1000千牛，矢高0.3米向下，徐变系数2")
    factor = 1.0 + 0.8 * 2.0
    check("徐变系数写进 *CREEP",
          "PHI=2" in creep_doc.model.to_dat() and creep_doc.params["creep_phi"] == 2.0)
    uz_c = solve_3d(creep_doc.model, out_dir="out/verify_prestress",
                    dat_name="creep.dat").get("final_disp", {}).get((2, 3), 0.0)
    check("徐变后上拱放大为 (1+χφ) 倍",
          abs(uz_c - want * factor) < 1e-8,
          f"uz={uz_c:.6e} want={want * factor:.6e}")

    ecs = 2.0e-4
    sh_doc, _, _ = ModelDoc.from_text(
        "跨度10米简支梁，曲线预应力，张拉力1000千牛，矢高0.3米向下，"
        "收缩应变0.0002")
    sh = solve_3d(sh_doc.model, out_dir="out/verify_prestress", dat_name="shrink.dat")
    ux = sh.get("final_disp", {}).get((3, 1), 0.0)
    # 预加力缩短 Pe·L/(EA)，收缩再缩短 ε·L。活动端是节点 3。
    want_ux = -1.0e6 * 10.0 / (2.0e11 * 0.01) - ecs * 10.0
    check("收缩使梁端再缩短 ε·L",
          abs(ux - want_ux) < 1e-8, f"ux={ux:.6e} want={want_ux:.6e}")

    both, _, _ = ModelDoc.from_text(
        "跨度10米简支梁，曲线预应力，张拉力1000千牛，矢高0.3米向下，"
        "收缩应变0.0002，徐变增量1")
    both_res = solve_3d(both.model, out_dir="out/verify_prestress",
                        dat_name="shrink_creep.dat")
    # 徐变把预应力位移再放大 Δφ，收缩仍只缩短一次 ε·L。
    both_ux = both_res.get("final_disp", {}).get((3, 1), 0.0)
    both_uz = both_res.get("final_disp", {}).get((2, 3), 0.0)
    want_both_ux = -2.0 * 1.0e6 * 10.0 / (2.0e11 * 0.01) - ecs * 10.0
    want_both_uz = 2.0 * want
    both_n = both_res.get("final_beam", {}).get((1, "i"), {}).get("N", 0.0)
    check("收缩不在徐变增量阶段再加一遍",
          len(both.model.stages) == 2
          and abs(both_ux - want_both_ux) < 1e-8
          and abs(both_uz - want_both_uz) < 1e-8,
          f"ux={both_ux:.6e} want={want_both_ux:.6e} "
          f"uz={both_uz:.6e} want={want_both_uz:.6e}")
    check("自由收缩不留下轴力",
          abs(both_n + 1.0e6) < 1.0, f"N={both_n:.6e}")

    from model_doc import creep_phi_from_age
    age_doc, _, _ = ModelDoc.from_text(
        "跨度10米简支梁，曲线预应力，张拉力1000千牛，矢高0.3米向下，"
        "加载龄期7天，计算龄期10000天")
    phi_age = creep_phi_from_age(10000.0, 7.0)
    uz_age = solve_3d(age_doc.model, out_dir="out/verify_prestress",
                      dat_name="age.dat").get("final_disp", {}).get((2, 3), 0.0)
    want_age = want * (1.0 + 0.8 * phi_age)
    check("龄期曲线换成 φ 后上拱按 1+χφ 放大",
          abs(age_doc.params["creep_phi"] - phi_age) < 1e-12
          and abs(uz_age - want_age) < 1e-8,
          f"φ={phi_age:.4f} uz={uz_age:.6e} want={want_age:.6e}")

    hist, _, _ = ModelDoc.from_text(
        "跨度10米简支梁，曲线预应力，张拉力1000千牛，矢高0.3米向下，徐变增量1")
    hist_res = solve_3d(hist.model, out_dir="out/verify_prestress",
                        dat_name="history.dat")
    uz_h = hist_res.get("final_disp", {}).get((2, 3), 0.0)
    check("徐变增量作用在已有内力上，上拱为 (1+Δφ) 倍",
          len(hist.model.stages) == 2 and abs(uz_h - 2.0 * want) < 1e-8,
          f"uz={uz_h:.6e} want={2.0 * want:.6e}")
    # 两段梁在跨中节点的弯矩。Δφ=1 只放大位移，静定梁应力不变。
    ends = hist_res.get("final_beam", {})
    axial = ends.get((1, "i"), {}).get("N", 0.0)
    my = ends.get((1, "j"), {}).get("My", 0.0)
    check("徐变增量不改变静定梁的轴力和弯矩",
          abs(axial + 1.0e6) < 1.0 and abs(my - 3.0e5) < 1.0,
          f"N={axial:.6e} My={my:.6e}")

    twice, _, _ = ModelDoc.from_text(
        "跨度10米简支梁，曲线预应力，张拉力1000千牛，矢高0.3米向下，"
        "徐变增量1，再徐变增量1")
    twice_res = solve_3d(twice.model, out_dir="out/verify_prestress",
                         dat_name="creep_twice.dat")
    uz2 = twice_res.get("final_disp", {}).get((2, 3), 0.0)
    my2 = twice_res.get("final_beam", {}).get((1, "j"), {}).get("My", 0.0)
    # 两次增量都作用在张拉内力上：u = (1+1+1)·u0。错把第一次徐变位移当应力会得到 4 倍。
    check("第二次徐变增量仍按张拉内力，上拱为 3 倍",
          twice.params.get("creep_dphis") == [1.0, 1.0]
          and len(twice.model.stages) == 3
          and abs(uz2 - 3.0 * want) < 1e-8
          and abs(my2 - 3.0e5) < 1.0,
          f"uz={uz2:.6e} want={3.0 * want:.6e} My={my2:.6e}")

    cant, _, _ = ModelDoc.from_text(
        "跨度10米悬臂梁，直线预应力，张拉力1000千牛，偏心0.5米下缘")
    # 直线束常偏心不产生上拱。e<0 时自由端弯矩使悬臂下挠：uz = Pe·e·L²/(2EI)。
    e_cant = -0.5
    want_uz = 1.0e6 * e_cant * 100.0 / (2.0 * 2.0e11 * 0.01)
    want_ux = -1.0e6 * 10.0 / (2.0e11 * 0.01)
    cant_res = solve_3d(cant.model, out_dir="out/verify_prestress",
                        dat_name="cantilever.dat")
    tip = max(nd.id for nd in cant.model.nodes)
    uz_tip = cant_res.get("final_disp", {}).get((tip, 3), 0.0)
    ux_tip = cant_res.get("final_disp", {}).get((tip, 1), 0.0)
    check("悬臂直线束自由端挠度 = Pe·e·L²/(2EI)",
          cant.params.get("support") == "cantilever"
          and abs(uz_tip - want_uz) < 1e-8,
          f"uz={uz_tip:.6e} want={want_uz:.6e}")
    check("方向：筋在下缘 → 自由端下挠", uz_tip < 0, f"uz={uz_tip:.6e}")
    check("悬臂自由端轴向缩短 = Pe·L/(EA)",
          abs(ux_tip - want_ux) < 1e-8, f"ux={ux_tip:.6e} want={want_ux:.6e}")

    creep_cant, _, _ = ModelDoc.from_text(
        "跨度10米悬臂梁，直线预应力，张拉力1000千牛，偏心0.5米下缘，徐变增量1")
    # 静定悬臂：位移乘 (1+Δφ)，常偏心弯矩 My=−Pe·e 与轴力保持不变。
    cc = solve_3d(creep_cant.model, out_dir="out/verify_prestress",
                  dat_name="cantilever_creep.dat")
    ctip = max(nd.id for nd in creep_cant.model.nodes)
    cuz = cc.get("final_disp", {}).get((ctip, 3), 0.0)
    cux = cc.get("final_disp", {}).get((ctip, 1), 0.0)
    cends = cc.get("final_beam", {})
    cmy = cends.get((1, "i"), {}).get("My", 0.0)
    cn = cends.get((1, "i"), {}).get("N", 0.0)
    check("悬臂徐变增量后自由端挠度为 (1+Δφ) 倍",
          creep_cant.params.get("creep_dphi") == 1.0
          and creep_cant.params.get("creep_phi") == 0.0
          and len(creep_cant.model.stages) == 2
          and abs(cuz - 2.0 * want_uz) < 1e-8,
          f"uz={cuz:.6e} want={2.0 * want_uz:.6e}")
    check("悬臂徐变增量后轴向缩短为 (1+Δφ) 倍",
          abs(cux - 2.0 * want_ux) < 1e-8,
          f"ux={cux:.6e} want={2.0 * want_ux:.6e}")
    check("悬臂徐变增量不改变轴力和弯矩",
          abs(cn + 1.0e6) < 1.0 and abs(cmy - 5.0e5) < 1.0,
          f"N={cn:.6e} My={cmy:.6e}")

    curved_cant, _, _ = ModelDoc.from_text(
        "跨度10米悬臂梁，曲线预应力，张拉力1000千牛，矢高0.3米向下")
    # 两段网格把均布力收到节点上。e=0、s<0 时自由端 uz = Pe·s·L²/(4EI)，方向向下。
    s_cant = -0.3
    want_cuz = 1.0e6 * s_cant * 100.0 / (4.0 * 2.0e11 * 0.01)
    cc_res = solve_3d(curved_cant.model, out_dir="out/verify_prestress",
                      dat_name="cantilever_curved.dat")
    ctip = max(nd.id for nd in curved_cant.model.nodes)
    cuz = cc_res.get("final_disp", {}).get((ctip, 3), 0.0)
    cux = cc_res.get("final_disp", {}).get((ctip, 1), 0.0)
    check("两段悬臂曲线束自由端挠度 = Pe·s·L²/(4EI)",
          curved_cant.params.get("s") == s_cant
          and abs(cuz - want_cuz) < 1e-8,
          f"uz={cuz:.6e} want={want_cuz:.6e}")
    check("方向：悬臂曲线束矢高向下 → 自由端下挠", cuz < 0, f"uz={cuz:.6e}")
    check("悬臂曲线束自由端仍缩短 Pe·L/(EA)",
          abs(cux - want_ux) < 1e-8, f"ux={cux:.6e} want={want_ux:.6e}")

    area_doc, _, _ = ModelDoc.from_text(
        "跨度10米简支梁，曲线预应力，张拉力1000千牛，矢高0.3米向下，"
        "钢束面积1400平方毫米")
    area_res = solver_3d.solve(area_doc.model, out_dir="out/verify_prestress",
                               dat_name="area.dat", kind="grillage3d")
    hit = next((c for c in area_res.checks(area_doc.model)
                if "0.75 fpk" in c.name), None)
    lane_doc, _, _ = ModelDoc.from_text(
        "跨度10米简支梁，曲线预应力，张拉力1000千牛，矢高0.3米向下，"
        "车道均布10千牛每米")
    lane_res = solve_3d(lane_doc.model, out_dir="out/verify_prestress",
                        dat_name="lane.dat")
    # 两段简支、均布只收到节点：跨中弯矩影响线面积正好是 L²/8。
    # 未写冲击系数时按竖向基频自动乘 1+μ。这根基准梁 f1>14 Hz，μ=0.45。
    import math
    want_m = 10.0e3 * 100.0 / 8.0
    f_ss = (math.pi / (2.0 * 100.0)) * math.sqrt(2.0e11 * 0.01 / (2500.0 * 0.01))
    live = lane_res.get("live_beam", {})
    imp = lane_res.get("lane_impact", {})
    # 单位荷载沿 +z。向下的均布取负号，跨中弯矩因此落在 MyMin。
    my_mid = live.get((1, "j"), {}).get("MyMin", 0.0)
    my_sup = live.get((1, "i"), {}).get("MyMin", 0.0)
    uz_lane = lane_res.get("final_disp", {}).get((2, 3), 0.0)
    check("未写冲击系数时按基频自动取 μ=0.45",
          abs(lane_doc.params.get("lane_q_kNpm") - 10.0) < 1e-9
          and f_ss > 14.0
          and abs(imp.get("f1_vertical", 0.0) - f_ss) / f_ss < 0.02
          and abs(imp.get("impact_mu", 0.0) - 0.45) < 1e-9
          and abs(my_mid + 1.45 * want_m) < 1.0
          and abs(my_sup) < 1.0,
          f"f1={imp.get('f1_vertical')} want={f_ss:.4f} "
          f"μ={imp.get('impact_mu')} MyMid={my_mid:.6e}")
    book = solver_3d.solve(lane_doc.model, out_dir="out/verify_prestress",
                           dat_name="lane_book.dat", kind="grillage3d")
    told = next((c for c in book.checks(lane_doc.model)
                 if c.name == "汽车冲击系数"), None)
    check("计算书写出自动基频和冲击系数",
          told is not None and told.ok
          and "141.05" in told.detail and "0.450" in told.detail,
          "" if told is None else told.detail)

    cant_lane, _, _ = ModelDoc.from_text(
        "跨度10米悬臂梁，直线预应力，张拉力1000千牛，偏心0.5米下缘，"
        "车道均布10千牛每米")
    # 悬臂一阶：βL=1.875104，f=(βL)²/(2π L²)·√(EI/m)。固端弯矩 qL²/2。
    beta = 1.8751040684
    f_cant = (beta ** 2) / (2.0 * math.pi * 100.0) * math.sqrt(
        2.0e11 * 0.01 / (2500.0 * 0.01))
    cant_lane_res = solve_3d(cant_lane.model, out_dir="out/verify_prestress",
                             dat_name="cantilever_lane.dat")
    cimp = cant_lane_res.get("lane_impact", {})
    cmy = cant_lane_res.get("live_beam", {}).get((1, "i"), {}).get("MyMax", 0.0)
    want_root = 1.45 * 10.0e3 * 100.0 / 2.0
    check("悬臂基频按悬臂公式，固端弯矩 = (1+μ)qL²/2",
          cant_lane.params.get("support") == "cantilever"
          and f_cant > 14.0
          and abs(cimp.get("f1_vertical", 0.0) - f_cant) / f_cant < 0.02
          and abs(cimp.get("f1_vertical", 0.0) - f_ss) / f_ss > 0.2
          and abs(cimp.get("impact_mu", 0.0) - 0.45) < 1e-9
          and abs(cmy - want_root) < 1.0,
          f"f1={cimp.get('f1_vertical')} want={f_cant:.4f} "
          f"My={cmy:.6e} want={want_root:.6e}")

    from fem_eigen_stage_nl import solve_staged_result
    # 全量平衡：徐变等效荷载留下，下一次仍乘张拉内力。丢掉荷载会回到瞬时上拱，
    # 把徐变位移当成应力则会放到 4 倍。
    nl_sh = solve_staged_result(sh_doc.model, "out/verify_prestress",
                                dat_name="shrink_nl.dat")
    ux_nl = nl_sh.final_disp.get((3, 1), 0.0)
    n_sh = nl_sh.final_beam.get((1, "i"), {}).get("N", 0.0)
    # 悬臂那段会把 want_ux 改成不含收缩的 Pe·L/(EA)，这里重算。
    want_sh_ux = -1.0e6 * 10.0 / (2.0e11 * 0.01) - ecs * 10.0
    check("非线性阶段收缩使梁端再缩短 ε·L，轴力仍是预加力",
          abs(ux_nl - want_sh_ux) / abs(want_sh_ux) < 1.5e-2
          and abs(ux_nl - (want_sh_ux - ecs * 10.0)) / abs(want_sh_ux) > 0.1
          and abs(n_sh + 1.0e6) / 1.0e6 < 2.0e-2,
          f"ux={ux_nl:.6e} want={want_sh_ux:.6e} N={n_sh:.6e}")
    nl_both = solve_staged_result(both.model, "out/verify_prestress",
                                  dat_name="shrink_creep_nl.dat")
    ux_nb = nl_both.final_disp.get((3, 1), 0.0)
    uz_nb = nl_both.final_disp.get((2, 3), 0.0)
    n_nb = nl_both.final_beam.get((1, "i"), {}).get("N", 0.0)
    check("非线性阶段收缩不在徐变增量里再加一遍",
          abs(ux_nb - want_both_ux) / abs(want_both_ux) < 1.5e-2
          and abs(uz_nb - want_both_uz) / abs(want_both_uz) < 1.5e-2
          and abs(n_nb + 1.0e6) / 1.0e6 < 2.0e-2,
          f"ux={ux_nb:.6e} want={want_both_ux:.6e} "
          f"uz={uz_nb:.6e} want={want_both_uz:.6e} N={n_nb:.6e}")
    nl_phi = solve_staged_result(creep_doc.model, "out/verify_prestress",
                                dat_name="creep_phi_nl.dat")
    uz_phi = nl_phi.final_disp.get((2, 3), 0.0)
    my_phi = nl_phi.final_beam.get((1, "j"), {}).get("My", 0.0)
    # 7 天张拉第一束，100 天再张同样的一束，10000 天收口。
    # 第二束只吃 φ(10000)−φ(100)，不是从 7 天起的整段 φ。
    from model_doc import creep_phi_from_age
    phi100 = creep_phi_from_age(100.0, 7.0)
    phi10000 = creep_phi_from_age(10000.0, 7.0)
    age_src, _, _ = ModelDoc.from_text(
        "跨度10米简支梁，曲线预应力，张拉力1000千牛，矢高0.3米向下")
    age_src.build()
    ps_a = age_src.model.stages[0].prestress[0]
    age_src.model.creep_phi0 = 2.0
    age_src.model.stages = [
        Stage3D(id=1, activate=[1, 2], prestress=[ps_a], time=7.0),
        Stage3D(id=2, prestress=[ps_a.model_copy()], time=100.0),
        Stage3D(id=3, time=10000.0),
    ]
    age_nl = solve_staged_result(age_src.model, "out/verify_prestress",
                                 dat_name="creep_age_nl.dat")
    uz_age_nl = age_nl.final_disp.get((2, 3), 0.0)
    my_age = age_nl.final_beam.get((1, "j"), {}).get("My", 0.0)
    n_age = age_nl.final_beam.get((1, "i"), {}).get("N", 0.0)
    want_hist = want * (2.0 + 2.0 * phi10000 - phi100)
    want_same = 2.0 * want * (1.0 + phi10000)
    # 共转列式在这段徐变上比线性闭式解大约高 2%。和「两束都从 7 天起徐变」相差约两成。
    check("多龄期：后张的一束不从第一天起徐变",
          abs(uz_age_nl - want_hist) / abs(want_hist) < 2.0e-2
          and abs(want_hist - want_same) / abs(want_same) > 0.1
          and abs(my_age - 6.0e5) / 6.0e5 < 3.0e-2
          and abs(n_age + 2.0e6) / 2.0e6 < 2.0e-2,
          f"uz={uz_age_nl:.6e} hist={want_hist:.6e} same={want_same:.6e} "
          f"φ100={phi100:.4f} φ10000={phi10000:.4f} My={my_age:.6e} N={n_age:.6e}")

    from model_doc import jtg3362_nominal_creep, creep_phi_jtg3362
    phi0_tab, _bh = jtg3362_nominal_creep(55.0, 100.0, 40.0, 7.0)
    phi0_wet, _bh = jtg3362_nominal_creep(80.0, 100.0, 40.0, 7.0)
    phi0_28, _bh = jtg3362_nominal_creep(55.0, 100.0, 40.0, 28.0)
    _phi, beta_cap = jtg3362_nominal_creep(99.0, 600.0, 40.0, 7.0)
    check("JTG 3362 名义徐变系数对上表 C.2.2",
          abs(phi0_tab - 3.33) < 0.02
          and abs(phi0_wet - 2.41) < 0.02
          and abs(phi0_28 - 2.56) < 0.02
          and abs(beta_cap - 1500.0) < 1e-6,
          f"φ0(55%,h100,t0=7)={phi0_tab:.3f} φ0(80%)={phi0_wet:.3f} "
          f"φ0(t0=28)={phi0_28:.3f} βHcap={beta_cap:.1f}")
    code_doc, _, code_diag = ModelDoc.from_text(
        "跨度10米简支梁，曲线预应力，张拉力1000千牛，矢高0.3米向下，"
        "相对湿度55%，理论厚度100毫米，混凝土C40，加载龄期7天，计算龄期10000天")
    code_doc.build()
    phi_code = creep_phi_jtg3362(10000.0, 7.0, 55.0, 100.0, 40.0)
    phi_old = creep_phi_from_age(10000.0, 7.0)
    dat = code_doc.model.to_dat()
    nl_code = solve_staged_result(code_doc.model, "out/verify_prestress",
                                  dat_name="creep_jtg3362.dat")
    uz_code = nl_code.final_disp.get((2, 3), 0.0)
    want_code = want * (1.0 + phi_code)
    want_old = want * (1.0 + phi_old)
    check("规范徐变曲线按湿度和理论厚度，不是 φ0=2",
          code_doc.model.creep_phi == 0.0
          and code_doc.model.creep_rh == 55.0
          and code_doc.model.creep_h == 100.0
          and code_doc.model.creep_fcm == 40.0
          and "PHI0=" not in dat
          and "RH=" in dat
          and abs(code_doc.model.stages[0].time - 7.0) < 1e-9
          and abs(code_doc.model.stages[-1].time - 10000.0) < 1e-9
          and any("JTG 3362" in n for n in code_diag.notes)
          and abs(uz_code - want_code) / abs(want_code) < 2.0e-2
          and abs(want_code - want_old) / abs(want_old) > 0.1,
          f"uz={uz_code:.6e} code={want_code:.6e} old={want_old:.6e} φ={phi_code:.3f}")
    keep, _, keep_diag = ModelDoc.from_text(
        "跨度10米简支梁，曲线预应力，张拉力1000千牛，矢高0.3米向下，"
        "徐变系数2，相对湿度55%，理论厚度100毫米")
    check("写了徐变系数就不用规范湿度曲线",
          keep.params.get("creep_phi") == 2.0
          and not keep.params.get("creep_rh")
          and any("不再改用" in n for n in keep_diag.notes),
          str(keep.params.get("creep_phi")))
    from model_doc import jtg3362_shrinkage_nominal, jtg3362_shrinkage
    eps55 = jtg3362_shrinkage_nominal(55.0, 40.0)
    eps80 = jtg3362_shrinkage_nominal(80.0, 40.0)
    check("JTG 3362 名义收缩系数对上表 C.1.2",
          abs(eps55 - 0.529e-3) < 2.0e-6 and abs(eps80 - 0.310e-3) < 2.0e-6,
          f"55%={eps55:.6e} 80%={eps80:.6e}")
    eps = jtg3362_shrinkage(10000.0, 7.0, 7.0, 55.0, 100.0, 40.0)
    bare = code_doc.model.model_copy(deep=True)
    for st in bare.stages:
        st.prestress.clear()
    nl_sh = solve_staged_result(bare, "out/verify_prestress",
                                dat_name="shrink_jtg.dat")
    ux_sh = nl_sh.final_disp.get((3, 1), 0.0)
    n_sh = nl_sh.final_beam.get((1, "i"), {}).get("N", 0.0)
    want_sh = -eps * 10.0
    check("规范收缩从参与受力起缩短，自由缩短不留轴力",
          abs(ux_sh - want_sh) / abs(want_sh) < 2.0e-2
          and abs(n_sh) < 1.0e4
          and abs(ux_sh) > 1.0e-3,
          f"ux={ux_sh:.6e} want={want_sh:.6e} N={n_sh:.6e} ε={eps:.6e}")
    over = bare.model_copy(deep=True)
    over.shrinkage_ecs = 2.0e-4
    nl_over = solve_staged_result(over, "out/verify_prestress",
                                  dat_name="shrink_explicit.dat")
    ux_over = nl_over.final_disp.get((3, 1), 0.0)
    want_over = -2.0e-4 * 10.0
    early = bare.model_copy(deep=True)
    early.shrinkage_ts = 3.0
    eps_early = jtg3362_shrinkage(10000.0, 7.0, 3.0, 55.0, 100.0, 40.0)
    nl_early = solve_staged_result(early, "out/verify_prestress",
                                   dat_name="shrink_jtg_ts3.dat")
    ux_early = nl_early.final_disp.get((3, 1), 0.0)
    want_early = -eps_early * 10.0
    check("干燥早于加载时，参与受力前的收缩不进入平衡",
          "TS=3" in early.to_dat()
          and abs(ux_early - want_early) / abs(want_early) < 2.0e-2
          and abs(want_early) < abs(want_sh) * 0.95,
          f"ux={ux_early:.6e} want={want_early:.6e} from_load={want_sh:.6e}")
    check("手写收缩应变优先，不与规范收缩相加",
          abs(ux_over - want_over) / abs(want_over) < 2.0e-2
          and abs(ux_over - (want_over + want_sh)) / abs(want_sh) > 0.2,
          f"ux={ux_over:.6e} explicit={want_over:.6e} code={want_sh:.6e}")

    check("非线性阶段徐变系数把上拱放大为 (1+χφ) 倍",
          abs(uz_phi - want * factor) / abs(want * factor) < 1.5e-2
          and abs(my_phi - 3.0e5) / 3.0e5 < 2.0e-2,
          f"uz={uz_phi:.6e} want={want * factor:.6e} My={my_phi:.6e}")
    nl_creep = solve_staged_result(hist.model, "out/verify_prestress",
                                   dat_name="creep_nl.dat")
    uz_nl = nl_creep.final_disp.get((2, 3), 0.0)
    my_nl = nl_creep.final_beam.get((1, "j"), {}).get("My", 0.0)
    n_nl = nl_creep.final_beam.get((1, "i"), {}).get("N", 0.0)
    # 共转列式在这级位移上比线性闭式解大约高 1%。轴力仍是预加力，弯矩不随 Δφ 放大。
    check("非线性阶段徐变增量后上拱为 (1+Δφ) 倍，内力不变",
          abs(uz_nl - 2.0 * want) / abs(2.0 * want) < 1.5e-2
          and abs(n_nl + 1.0e6) / 1.0e6 < 1.5e-2
          and abs(my_nl - 3.0e5) / 3.0e5 < 2.0e-2,
          f"uz={uz_nl:.6e} want={2.0 * want:.6e} N={n_nl:.6e} My={my_nl:.6e}")
    nl_twice = solve_staged_result(twice.model, "out/verify_prestress",
                                   dat_name="creep_nl_twice.dat")
    uz_nt = nl_twice.final_disp.get((2, 3), 0.0)
    my_nt = nl_twice.final_beam.get((1, "j"), {}).get("My", 0.0)
    check("非线性阶段两次徐变增量后上拱为 3 倍，不是 4 倍",
          abs(uz_nt - 3.0 * want) / abs(3.0 * want) < 1.5e-2
          and abs(uz_nt - 4.0 * want) / abs(4.0 * want) > 0.2
          and abs(my_nt - 3.0e5) / 3.0e5 < 2.0e-2,
          f"uz={uz_nt:.6e} want={3.0 * want:.6e} My={my_nt:.6e}")
    nl_lane = solve_staged_result(lane_doc.model, "out/verify_prestress",
                                  dat_name="lane_nl.dat")
    nl_my = nl_lane.live_beam.get((1, "j"), {}).get("MyMin", 0.0)
    check("无索的非线性阶段活载弯矩与线性相同",
          abs(nl_my - my_mid) < 1.0 and abs(nl_my + 1.45 * want_m) < 1.0,
          f"nl={nl_my:.6e} linear={my_mid:.6e}")

    check("车道活载不加进预应力上拱",
          abs(uz_lane - want) < 1e-9, f"uz={uz_lane:.6e}")

    imp_doc, _, _ = ModelDoc.from_text(
        "跨度10米简支梁，曲线预应力，张拉力1000千牛，矢高0.3米向下，"
        "车道均布10千牛每米，冲击系数0.3")
    imp_res = solve_3d(imp_doc.model, out_dir="out/verify_prestress",
                       dat_name="lane_impact.dat")
    imp_my = imp_res.get("live_beam", {}).get((1, "j"), {}).get("MyMin", 0.0)
    check("冲击系数 0.3 把跨中弯矩乘 1.3",
          abs(imp_doc.params.get("lane_impact") - 1.3) < 1e-12
          and "IMPACT=1.3" in imp_doc.model.to_dat()
          and abs(imp_my + 1.3 * want_m) < 1.0,
          f"MyMin={imp_my:.6e} want={-1.3 * want_m:.6e}")

    code_doc, _, _ = ModelDoc.from_text(
        "跨度10米简支梁，曲线预应力，张拉力1000千牛，矢高0.3米向下，"
        "按公路桥涵车道荷载")
    # L=10 m：qk=10.5 kN/m，Pk=280 kN。同一根梁基频 >14 Hz，自动 μ=0.45。
    span = 10.0
    qk, pk = 10500.0, 280000.0
    want_code = -(qk * span * span / 8.0 + pk * span / 4.0) * 1.45
    code_res = solve_3d(code_doc.model, out_dir="out/verify_prestress",
                        dat_name="lane_code.dat")
    code_my = code_res.get("live_beam", {}).get((1, "j"), {}).get("MyMin", 0.0)
    check("公路车道荷载跨中弯矩含自动冲击",
          code_doc.params.get("lane_code") == "JTG_D60"
          and "CODE=JTG_D60" in code_doc.model.to_dat()
          and abs(code_my - want_code) < 5.0,
          f"MyMin={code_my:.6e} want={want_code:.6e}")

    # 跨中一根竖向拉索。切线刚度上的活载弯矩应明显小于无索梁。
    stay = ModelSpec3D(
        title="带竖向拉索的简支梁",
        nodes=[Node3D(id=1, x=0, y=0, z=0), Node3D(id=2, x=5, y=0, z=0),
               Node3D(id=3, x=10, y=0, z=0), Node3D(id=4, x=5, y=0, z=8)],
        materials=[Material3D(id=1, E=2.0e11, dens=2500.0),
                   Material3D(id=2, E=1.95e11)],
        reals=[RealConst3D(id=1, A=0.01, IY=0.01, IZ=0.01, J=1e-4, G=2.0e11 / 2.6),
               RealConst3D(id=2, A=2.0e-3, L0=7.98)],
        etypes=[ElemTypeDef3D(id=1, kind="BEAM", mat=1, real=1),
                ElemTypeDef3D(id=2, kind="CABLE", mat=2, real=2)],
        elements=[Element3D(id=1, etype=1, n1=1, n2=2),
                  Element3D(id=2, etype=1, n1=2, n2=3),
                  Element3D(id=3, etype=2, n1=2, n2=4)],
        boundaries=[
            Boundary3D(node=1, dofs=(1, 3)), Boundary3D(node=1, dofs=(4, 4)),
            Boundary3D(node=1, dofs=(6, 6)),
            Boundary3D(node=3, dofs=(2, 3)), Boundary3D(node=3, dofs=(4, 4)),
            Boundary3D(node=3, dofs=(6, 6)),
            Boundary3D(node=2, dofs=(2, 2)), Boundary3D(node=2, dofs=(4, 4)),
            Boundary3D(node=2, dofs=(6, 6)),
            Boundary3D(node=4, dofs=(1, 6)),
        ],
        stages=[Stage3D(id=1, activate=[1, 2, 3])],
        lanes=[Lane3D(nodes=[1, 2, 3], q=-10000.0, p=0.0, dof=3)],
        influences=[Influence3D(node=2, dof=3)],
    )
    stay_nl = solve_staged_result(stay, "out/verify_prestress", dat_name="stay_lane_nl.dat")
    stay_my = stay_nl.live_beam.get((1, "j"), {}).get("MyMin", 0.0)
    stay_txt = Path("out/verify_prestress/stay_lane_nl.dat.nlstage.txt").read_text(
        encoding="utf-8", errors="replace")
    # 无索弯矩按跨中集中力分配。索的切线刚度 EA/L0 与梁的跨中刚度 48EI/L³ 并联。
    k_beam = 48.0 * 2.0e11 * 0.01 / (10.0 ** 3)
    k_cable = 1.95e11 * 2.0e-3 / 7.98
    want_stay = my_mid * k_beam / (k_beam + k_cable)
    check("有索末阶段活载弯矩按切线刚度与梁并联",
          "basis=tangent" in stay_txt
          and abs(stay_my - want_stay) / abs(want_stay) < 5e-3,
          f"stay={stay_my:.6e} want={want_stay:.6e}")
    # 跨中等效集中力 = q·(L/2)·(1+μ)，挠度 = 该力 / (k梁 + EA/L0)。
    f_mid = 10000.0 * 5.0 * 1.45
    want_uz = -f_mid / (k_beam + k_cable)
    got = stay_nl.live.get(1)
    got_uz = got[1] if got else 0.0
    check("有索末阶段活载挠度按同一套切线刚度",
          got is not None and abs(got_uz - want_uz) / abs(want_uz) < 5e-3,
          f"uz={got_uz:.6e} want={want_uz:.6e}")

    # 公路车道：均布和集中力都在跨中竖标上，跟无索弯矩用同一个 kb/(kb+kc)。
    qk_s, pk_s = 10500.0, 280000.0
    m_code_bare = -(qk_s * 100.0 / 8.0 + pk_s * 10.0 / 4.0) * 1.45
    stay_code = ModelSpec3D(
        title="带竖向拉索的公路车道",
        nodes=[Node3D(id=1, x=0, y=0, z=0), Node3D(id=2, x=5, y=0, z=0),
               Node3D(id=3, x=10, y=0, z=0), Node3D(id=4, x=5, y=0, z=8)],
        materials=[Material3D(id=1, E=2.0e11, dens=2500.0),
                   Material3D(id=2, E=1.95e11)],
        reals=[RealConst3D(id=1, A=0.01, IY=0.01, IZ=0.01, J=1e-4, G=2.0e11 / 2.6),
               RealConst3D(id=2, A=2.0e-3, L0=7.98)],
        etypes=[ElemTypeDef3D(id=1, kind="BEAM", mat=1, real=1),
                ElemTypeDef3D(id=2, kind="CABLE", mat=2, real=2)],
        elements=[Element3D(id=1, etype=1, n1=1, n2=2),
                  Element3D(id=2, etype=1, n1=2, n2=3),
                  Element3D(id=3, etype=2, n1=2, n2=4)],
        boundaries=[
            Boundary3D(node=1, dofs=(1, 3)), Boundary3D(node=1, dofs=(4, 4)),
            Boundary3D(node=1, dofs=(6, 6)),
            Boundary3D(node=3, dofs=(2, 3)), Boundary3D(node=3, dofs=(4, 4)),
            Boundary3D(node=3, dofs=(6, 6)),
            Boundary3D(node=2, dofs=(2, 2)), Boundary3D(node=2, dofs=(4, 4)),
            Boundary3D(node=2, dofs=(6, 6)),
            Boundary3D(node=4, dofs=(1, 6)),
        ],
        stages=[Stage3D(id=1, activate=[1, 2, 3])],
        lanes=[Lane3D(nodes=[1, 2, 3], code="JTG_D60", dof=3)],
        influences=[Influence3D(node=2, dof=3)],
    )
    stay_code_nl = solve_staged_result(
        stay_code, "out/verify_prestress", dat_name="stay_code_nl.dat")
    stay_code_my = stay_code_nl.live_beam.get((1, "j"), {}).get("MyMin", 0.0)
    want_code_my = m_code_bare * k_beam / (k_beam + k_cable)
    code_uz = (stay_code_nl.live.get(1) or (0.0, 0.0))[1]
    want_code_uz = -(qk_s * 5.0 + pk_s) * 1.45 / (k_beam + k_cable)
    check("有索公路车道的弯矩和挠度按切线刚度折减",
          abs(stay_code_my - want_code_my) / abs(want_code_my) < 5e-3
          and abs(code_uz - want_code_uz) / abs(want_code_uz) < 5e-3,
          f"My={stay_code_my:.6e} want={want_code_my:.6e} "
          f"uz={code_uz:.6e} want={want_code_uz:.6e}")

    # 斜索：k_z = (EA/L0) c_z² + (N/Ln)(1−c_z²)，N = EA/L0·(Ln−L0)。跨中 ux 固定，只留竖向。
    import math
    chord = math.sqrt(5.0 ** 2 + 8.0 ** 2)
    L0i = 0.995 * chord
    cz = 8.0 / chord
    EA = 1.95e11 * 2.0e-3
    N = EA / L0i * (chord - L0i)
    k_z = (EA / L0i) * cz * cz + (N / chord) * (1.0 - cz * cz)
    incl = ModelSpec3D(
        title="带斜索的简支梁",
        nodes=[Node3D(id=1, x=0, y=0, z=0), Node3D(id=2, x=5, y=0, z=0),
               Node3D(id=3, x=10, y=0, z=0), Node3D(id=4, x=10, y=0, z=8)],
        materials=[Material3D(id=1, E=2.0e11, dens=2500.0),
                   Material3D(id=2, E=1.95e11)],
        reals=[RealConst3D(id=1, A=0.01, IY=0.01, IZ=0.01, J=1e-4, G=2.0e11 / 2.6),
               RealConst3D(id=2, A=2.0e-3, L0=L0i)],
        etypes=[ElemTypeDef3D(id=1, kind="BEAM", mat=1, real=1),
                ElemTypeDef3D(id=2, kind="CABLE", mat=2, real=2)],
        elements=[Element3D(id=1, etype=1, n1=1, n2=2),
                  Element3D(id=2, etype=1, n1=2, n2=3),
                  Element3D(id=3, etype=2, n1=2, n2=4)],
        boundaries=[
            Boundary3D(node=1, dofs=(1, 3)), Boundary3D(node=1, dofs=(4, 4)),
            Boundary3D(node=1, dofs=(6, 6)),
            Boundary3D(node=3, dofs=(2, 3)), Boundary3D(node=3, dofs=(4, 4)),
            Boundary3D(node=3, dofs=(6, 6)),
            Boundary3D(node=2, dofs=(1, 2)), Boundary3D(node=2, dofs=(4, 4)),
            Boundary3D(node=2, dofs=(6, 6)),
            Boundary3D(node=4, dofs=(1, 6)),
        ],
        stages=[Stage3D(id=1, activate=[1, 2, 3])],
        lanes=[Lane3D(nodes=[1, 2, 3], q=-10000.0, p=0.0, dof=3, impact=1.45)],
        influences=[Influence3D(node=2, dof=3)],
    )
    incl_nl = solve_staged_result(incl, "out/verify_prestress", dat_name="incline_lane_nl.dat")
    incl_uz = (incl_nl.live.get(1) or (0.0, 0.0))[1]
    want_incl = -f_mid / (k_beam + k_z)
    check("斜索竖向刚度含方向余弦和几何刚度",
          abs(incl_uz - want_incl) / abs(want_incl) < 5e-3,
          f"uz={incl_uz:.6e} want={want_incl:.6e}")

    # 有重度：切线轴向刚度是 Ernst 模量，不是 EA/L0。
    # g(T)=L0(1+T/EA)−w²Lh³/(24T²)−Ln，k1=1/(L0/EA+w²Lh³/(12T³))。
    dx_s, dz_s = 10.0, 8.0
    Ln_s = math.sqrt(dx_s * dx_s + dz_s * dz_s)
    Lh_s = dx_s
    w_s = 800.0
    EA_s = 2.0e8
    T_s = 1.0e5
    sag_s = w_s * w_s * Lh_s ** 3 / (24.0 * T_s * T_s)
    L0_s = (Ln_s + sag_s) / (1.0 + T_s / EA_s)
    c_sag = w_s * w_s * Lh_s ** 3 / 24.0

    def tension_at(ln: float) -> tuple[float, float]:
        """返回 (T, k1)。k1 = ∂T/∂Ln，即 Ernst 切线。"""
        t = max(EA_s * (ln - L0_s) / L0_s, 1.0e3)
        for _ in range(60):
            g = L0_s * (1.0 + t / EA_s) - c_sag / (t * t) - ln
            gp = L0_s / EA_s + 2.0 * c_sag / (t ** 3)
            step = g / gp
            t -= step
            if t < 1.0e-6:
                t = 1.0e-6
            if abs(step) <= 1.0e-12 * max(1.0, t):
                break
        k1_now = 1.0 / (L0_s / EA_s + 2.0 * c_sag / (t ** 3))
        return t, k1_now

    k_soft = 48.0 * 2.0e11 * 1.0e-3 / (10.0 ** 3)
    # 索力竖向分量把梁压下去，切线取平衡后的弦，不是初始几何。
    dz_eq, ln_eq = dz_s, Ln_s
    for _ in range(40):
        t_eq, _ = tension_at(ln_eq)
        cz_eq = dz_eq / ln_eq
        dz_eq = dz_s - (t_eq * cz_eq) / k_soft
        ln_eq = math.sqrt(dx_s * dx_s + dz_eq * dz_eq)
    T_n, k1 = tension_at(ln_eq)
    cz_s = dz_eq / ln_eq
    k_zz = k1 * cz_s * cz_s + (T_n / ln_eq) * (1.0 - cz_s * cz_s)
    k_zz_bar = (EA_s / L0_s) * cz_s * cz_s + (T_n / ln_eq) * (1.0 - cz_s * cz_s)
    sag_model = ModelSpec3D(
        title="带垂度斜索的简支梁",
        nodes=[Node3D(id=1, x=0, y=0, z=0), Node3D(id=2, x=5, y=0, z=0),
               Node3D(id=3, x=10, y=0, z=0), Node3D(id=4, x=15, y=0, z=8)],
        materials=[Material3D(id=1, E=2.0e11, dens=2500.0),
                   Material3D(id=2, E=1.95e11)],
        reals=[RealConst3D(id=1, A=0.01, IY=1.0e-3, IZ=1.0e-3, J=1e-4,
                           G=2.0e11 / 2.6),
               RealConst3D(id=2, A=EA_s / 1.95e11, W=w_s, L0=L0_s)],
        etypes=[ElemTypeDef3D(id=1, kind="BEAM", mat=1, real=1),
                ElemTypeDef3D(id=2, kind="CABLE", mat=2, real=2)],
        elements=[Element3D(id=1, etype=1, n1=1, n2=2),
                  Element3D(id=2, etype=1, n1=2, n2=3),
                  Element3D(id=3, etype=2, n1=2, n2=4)],
        boundaries=[
            Boundary3D(node=1, dofs=(1, 3)), Boundary3D(node=1, dofs=(4, 4)),
            Boundary3D(node=1, dofs=(6, 6)),
            Boundary3D(node=3, dofs=(2, 3)), Boundary3D(node=3, dofs=(4, 4)),
            Boundary3D(node=3, dofs=(6, 6)),
            Boundary3D(node=2, dofs=(1, 2)), Boundary3D(node=2, dofs=(4, 4)),
            Boundary3D(node=2, dofs=(6, 6)),
            Boundary3D(node=4, dofs=(1, 6)),
        ],
        stages=[Stage3D(id=1, activate=[1, 2, 3])],
        lanes=[Lane3D(nodes=[1, 2, 3], q=-10000.0, p=0.0, dof=3, impact=1.2)],
        influences=[Influence3D(node=2, dof=3)],
    )
    sag_nl = solve_staged_result(sag_model, "out/verify_prestress",
                                 dat_name="sag_lane_nl.dat")
    sag_uz = (sag_nl.live.get(1) or (0.0, 0.0))[1]
    f_sag = 10000.0 * 5.0 * 1.2
    want_sag = -f_sag / (k_soft + k_zz)
    want_bar = -f_sag / (k_soft + k_zz_bar)
    check("垂度索活载挠度用 Ernst 切线，不是 EA/L0",
          abs(sag_uz - want_sag) / abs(want_sag) < 1e-2
          and abs(want_sag - want_bar) / abs(want_bar) > 0.05,
          f"uz={sag_uz:.6e} ernst={want_sag:.6e} elastic={want_bar:.6e}")

    # 冲击系数的基频必须用切线，不能用 EA/L。梁两端转角固定，竖向刚度是 12EI/L³。
    # 外力抵消参考弦上的索力，平衡位移接近 0，切线就在这根弦上。
    dx_f, dz_f = 10.0, 8.0
    Ln_f = math.sqrt(dx_f * dx_f + dz_f * dz_f)
    Lh_f = dx_f
    w_f, EA_f, T_f = 800.0, 4.0e7, 2.0e4
    k_beam_f = 2.0e5
    E_f = 2.0e11
    rho_f, A_f, L_f = 2500.0, 0.04, 10.0
    I_f = k_beam_f * L_f ** 3 / (12.0 * E_f)
    sag_f = w_f * w_f * Lh_f ** 3 / (24.0 * T_f * T_f)
    L0_f = (Ln_f + sag_f) / (1.0 + T_f / EA_f)
    c_sag_f = w_f * w_f * Lh_f ** 3 / 24.0

    def tension_f(ln: float) -> tuple[float, float]:
        t = max(EA_f * (ln - L0_f) / L0_f, 1.0e3)
        for _ in range(60):
            g = L0_f * (1.0 + t / EA_f) - c_sag_f / (t * t) - ln
            gp = L0_f / EA_f + 2.0 * c_sag_f / (t ** 3)
            step = g / gp
            t -= step
            if t < 1.0e-6:
                t = 1.0e-6
            if abs(step) <= 1.0e-12 * max(1.0, t):
                break
        k1_now = 1.0 / (L0_f / EA_f + 2.0 * c_sag_f / (t ** 3))
        return t, k1_now

    T0, k1_f = tension_f(Ln_f)
    cz_f = dz_f / Ln_f
    k_zz_f = k1_f * cz_f * cz_f + (T0 / Ln_f) * (1.0 - cz_f * cz_f)
    k_zz_el = (EA_f / L0_f) * cz_f * cz_f + (T0 / Ln_f) * (1.0 - cz_f * cz_f)
    # 索把下端向上拉（N·cz），自重向下（w·L0/2）。外力补上差额，参考弦才是平衡位置。
    hold = -T0 * cz_f + 0.5 * w_f * L0_f
    # 转角被约束后，自由端竖向一致质量是 156/420·ρAL。
    m_tip = 156.0 / 420.0 * rho_f * A_f * L_f
    k_f = k_beam_f + k_zz_f
    k_el = k_beam_f + k_zz_el
    f_ernst = math.sqrt(k_f / m_tip) / (2.0 * math.pi)
    f_elastic = math.sqrt(k_el / m_tip) / (2.0 * math.pi)

    def mu_of(freq: float) -> float:
        if freq < 1.5:
            return 0.05
        if freq <= 14.0:
            return 0.1767 * math.log(freq) - 0.0157
        return 0.45

    factor_f = 1.0 + mu_of(f_ernst)
    freq_model = ModelSpec3D(
        title="垂度索切线基频",
        nodes=[Node3D(id=1, x=0, y=0, z=0), Node3D(id=2, x=10, y=0, z=0),
               Node3D(id=3, x=20, y=0, z=8)],
        materials=[Material3D(id=1, E=E_f, dens=rho_f),
                   Material3D(id=2, E=1.95e11, dens=0.0)],
        reals=[RealConst3D(id=1, A=A_f, IY=I_f, IZ=I_f, J=1.0e-4, G=E_f / 2.6),
               RealConst3D(id=2, A=EA_f / 1.95e11, W=w_f, L0=L0_f)],
        etypes=[ElemTypeDef3D(id=1, kind="BEAM", mat=1, real=1),
                ElemTypeDef3D(id=2, kind="CABLE", mat=2, real=2)],
        elements=[Element3D(id=1, etype=1, n1=1, n2=2),
                  Element3D(id=2, etype=2, n1=2, n2=3)],
        boundaries=[
            Boundary3D(node=1, dofs=(1, 6)),
            Boundary3D(node=3, dofs=(1, 3)),
            Boundary3D(node=2, dofs=(1, 2)),
            Boundary3D(node=2, dofs=(4, 6)),
        ],
        stages=[Stage3D(id=1, activate=[1, 2],
                        loads=[NodalLoad3D(node=2, dof=3, value=hold)])],
        lanes=[Lane3D(nodes=[1, 2], q=-10000.0, p=0.0, dof=3)],
        influences=[Influence3D(node=2, dof=3)],
    )
    freq_nl = solve_staged_result(freq_model, "out/verify_prestress",
                                  dat_name="sag_freq_nl.dat")
    f_got = float(freq_nl.meta.get("f1_vertical") or 0.0)
    mu_got = float(freq_nl.meta.get("impact_mu") or 0.0)
    uz_dead = freq_nl.final_disp.get((2, 3), 0.0)
    uz_live = (freq_nl.live.get(1) or (0.0, 0.0))[1]
    want_live = -10000.0 * (L_f / 2.0) * factor_f / k_f
    # 弹性支承必须进基频。两端转角固定时梁的竖向刚度是 12EI/L³，再并联弹簧。
    # 若把弹簧当成固结，基频只剩梁，大约低一半。
    k_b = 1.0e5
    k_s = 3.0e5
    E_s, L_s = 2.0e11, 10.0
    rho_s, A_s = 2500.0, 0.04
    I_s = k_b * L_s ** 3 / (12.0 * E_s)
    m_s = 156.0 / 420.0 * rho_s * A_s * L_s
    f_with = math.sqrt((k_b + k_s) / m_s) / (2.0 * math.pi)
    f_beam_only = math.sqrt(k_b / m_s) / (2.0 * math.pi)

    def mu_s(freq: float) -> float:
        if freq < 1.5:
            return 0.05
        if freq <= 14.0:
            return 0.1767 * math.log(freq) - 0.0157
        return 0.45

    spring = ModelSpec3D(
        title="弹性支承上的梁",
        nodes=[Node3D(id=1, x=0, y=0, z=0), Node3D(id=2, x=L_s, y=0, z=0)],
        materials=[Material3D(id=1, E=E_s, dens=rho_s)],
        reals=[RealConst3D(id=1, A=A_s, IY=I_s, IZ=I_s, J=1.0e-4, G=E_s / 2.6)],
        etypes=[ElemTypeDef3D(id=1, kind="BEAM", mat=1, real=1)],
        elements=[Element3D(id=1, etype=1, n1=1, n2=2)],
        boundaries=[
            Boundary3D(node=1, dofs=(1, 6)),
            Boundary3D(node=2, dofs=(1, 2)),
            Boundary3D(node=2, dofs=(4, 6)),
            Boundary3D(node=2, dofs=(3, 3), k=k_s),
        ],
        stages=[Stage3D(id=1, activate=[1])],
        lanes=[Lane3D(nodes=[1, 2], q=-10000.0, p=0.0, dof=3)],
        influences=[Influence3D(node=2, dof=3)],
    )
    spring_res = solve_3d(spring, out_dir="out/verify_prestress", dat_name="spring_lane.dat")
    f_spring = float(spring_res.get("lane_impact", {}).get("f1_vertical") or 0.0)
    uz_spring = (spring_res.get("live", {}).get(1) or (0.0, 0.0))[1]
    want_uz_s = -10000.0 * (L_s / 2.0) * (1.0 + mu_s(f_with)) / (k_b + k_s)
    check("弹性支承进入冲击基频，不当成固结",
          abs(f_spring - f_with) / f_with < 1.0e-2
          and abs(f_with - f_beam_only) / f_beam_only > 0.05
          and abs(uz_spring - want_uz_s) / abs(want_uz_s) < 1.0e-2,
          f"f={f_spring:.4f} with={f_with:.4f} beam={f_beam_only:.4f} "
          f"uz={uz_spring:.6e} want={want_uz_s:.6e}")

    check("垂度索冲击系数用切线基频，不是 EA/L0",
          abs(uz_dead) < 1.0e-3
          and abs(f_got - f_ernst) / f_ernst < 1.0e-2
          and abs(f_ernst - f_elastic) / f_elastic > 0.05
          and abs(mu_got - mu_of(f_ernst)) < 1.0e-3
          and abs(uz_live - want_live) / abs(want_live) < 1.0e-2,
          f"f={f_got:.4f} ernst={f_ernst:.4f} elastic={f_elastic:.4f} "
          f"μ={mu_got:.4f} uz={uz_live:.6e} want={want_live:.6e} dead={uz_dead:.3e}")

    check("求解后 1400 mm² 的有效预应力校核通过",
          hit is not None and hit.ok and abs(hit.value - 1.0e6 / 1.4e-3) < 1.0,
          "" if hit is None else f"σ={hit.value:.3e} ok={hit.ok}")


def main() -> int:
    offline()
    if "--offline" in sys.argv:
        n = sum(1 for r in RESULTS if r[0])
        print(f"\n离线部分: {n}/{len(RESULTS)} 通过")
        return 0 if n == len(RESULTS) else 1
    if not fem_eigen_3d_available():
        print("\n[跳过在线部分] FEM-Eigen 3D 不可用")
        return 1
    online()
    n = sum(1 for r in RESULTS if r[0])
    print("=" * 60)
    print(f"  {n}/{len(RESULTS)} 通过")
    return 0 if n == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
