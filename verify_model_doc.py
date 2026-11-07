"""ModelDoc（C 方案）回归验证。

判据不是"能跑通"，而是：
1. 梁路径与改造前逐位一致（不引入数值偏移）；
2. 桥路径的 .dat 与直接调建造器**逐字节相同**；
3. 改参数后拓扑真的重建（C 方案的核心承诺，不是把模型冻住）；
4. 工程文件可存可读，往返不丢参数；
5. capability() 如实反映能力边界（尤其"计入自重就不能互校"）。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()          # 输出被重定向到管道/文件时，GBK 会崩（见模块说明）

import sys
import tempfile
from pathlib import Path

from bridge_spec import ElemKind, build_arch_bridge, build_truss_bridge
from fe_solver import solve
from fem_eigen_export import spec_to_dat
from model_doc import ModelDoc, classify_text
from nl_parser import parse_nl

PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<28} {detail}")


BEAM_TXT = "一座跨度30米的简支钢梁，矩形截面高1.2米宽0.6米，承受20kN/m均布荷载"


def t_beam_equivalent():
    """梁路径：ModelDoc 与旧管线（parse_nl + ModelSpec）结果必须一致。"""
    doc, src, _ = ModelDoc.from_text(BEAM_TXT)
    spec_old, _, _ = parse_nl(BEAM_TXT)

    r_doc = solve(doc.model)
    r_old = solve(spec_old)
    dm = abs(r_doc["max_moment"] - r_old["max_moment"])
    dv = abs(r_doc["max_deflection"] - r_old["max_deflection"])
    check("梁：内力与旧管线一致", dm < 1e-6 and dv < 1e-12,
          f"ΔM={dm:.2e} Δv={dv:.2e}")

    dat_doc = doc.to_dat()
    dat_old = spec_to_dat(spec_old, target_len=0.5, self_weight=False)
    check("梁：.dat 与旧管线一致", dat_doc == dat_old,
          f"{len(dat_doc)} 字符")


def t_bridge_dat_identical():
    """桥路径：从 params 重建出的 .dat 必须与直接建造器逐字节相同。"""
    doc, _, _ = ModelDoc.from_text("跨度100米的拱桥，矢高20米，9对吊杆，20kN/m均布荷载")
    direct = build_arch_bridge(span=100.0, rise=20.0, n_hanger=9,
                               udl_kN=20.0, point_kN=0.0,
                               hanger_kind=ElemKind.TRUSS, gravity=-9.81)
    check("拱桥：.dat 与建造器一致", doc.to_dat() == direct.to_dat(),
          f"节点 {len(doc.model.nodes)} 单元 {len(doc.model.elements)}")

    doc_t, _, _ = ModelDoc.from_text("跨度60米桁架桥，8个节间，桁高6米")
    # 文本未给均布荷载 → 解析侧按 q=20 kN/m 折算节点荷载：20 × (60/8)
    direct_t = build_truss_bridge(span=60.0, n_panel=8, depth=6.0,
                                  node_load_kN=20.0 * 60.0 / 8.0)
    check("桁架：.dat 与建造器一致", doc_t.to_dat() == direct_t.to_dat(),
          f"节点 {len(doc_t.model.nodes)}")


def t_topology_rebuild():
    """C 方案的核心承诺：改参数 → 拓扑重建，不是把模型冻住。"""
    doc, _, _ = ModelDoc.from_text("跨度100米的拱桥，矢高20米，9对吊杆")
    n_before = len(doc.model.nodes)
    doc.params["n_hanger"] = 15
    doc.build()
    n_after = len(doc.model.nodes)
    check("拱桥：改吊杆数拓扑重建", n_after > n_before, f"{n_before} → {n_after} 节点")

    # 梁：改跨度后支座必须跟着走（旧代码里支座位置会留在原地）
    b, _, _ = ModelDoc.from_text(BEAM_TXT)
    b.params["length"] = 60.0
    b.build()
    xs = sorted(round(s.position, 6) for s in b.model.supports)
    check("梁：改跨度支座跟随", xs == [0.0, 60.0], f"支座位置 {xs}")


def t_json_roundtrip():
    for txt in (BEAM_TXT, "跨度100米的拱桥，矢高20米，9对吊杆，20kN/m"):
        doc, _, _ = ModelDoc.from_text(txt)
        with tempfile.TemporaryDirectory() as td:
            p = doc.save(Path(td) / "proj.json")
            size = p.stat().st_size
            back = ModelDoc.load(p)
        same = back.kind == doc.kind and back.to_dat() == doc.to_dat()
        check(f"工程文件往返（{doc.kind}）", same, f"proj.json {size}B")


def t_validate():
    doc, _, _ = ModelDoc.from_text(BEAM_TXT)
    errs = doc.validate()
    check("梁：合法模型无误报", errs == [], "；".join(errs) or "无")

    doc.params["h"] = 0.0
    check("梁：h=0 被拦下", any("截面高度" in e for e in doc.validate()))

    doc2, _, _ = ModelDoc.from_text(BEAM_TXT)
    doc2.params["h"] = "abc"          # type: ignore[assignment]
    errs2 = doc2.validate()
    check("梁：非法类型不抛异常", bool(errs2), errs2[0][:34] if errs2 else "")

    for txt, label in (("跨度100米的拱桥，矢高20米", "拱桥"),
                       ("跨度60米桁架桥，8个节间", "桁架"),
                       ("跨度90米三跨连续梁桥", "梁桥")):
        d, _, _ = ModelDoc.from_text(txt)
        check(f"{label}：合法模型无误报", d.validate() == [],
              "；".join(d.validate()) or "无")


def t_capability():
    beam, _, _ = ModelDoc.from_text(BEAM_TXT)
    cap = beam.capability()
    check("能力：梁可草算+可互校",
          cap["anastruct"] and cap["cross_check"], str(cap))

    beam.params["self_weight"] = True
    beam.build()
    check("能力：计自重后关掉互校",
          beam.capability()["cross_check"] is False, "anastruct 无重力项")

    arch, _, _ = ModelDoc.from_text("跨度100米的拱桥，矢高20米")
    cap_a = arch.capability()
    check("能力：桥只能走 FEM-Eigen",
          cap_a["anastruct"] is False and cap_a["fem_eigen"] is True, str(cap_a))


def t_classify():
    cases = [
        (BEAM_TXT, ("beam", "2d")),
        ("跨度100米的拱桥", ("arch", "2d")),
        ("60米桁架桥", ("truss", "2d")),
        ("30米简支梁桥", ("beam_bridge", "2d")),
    ]
    ok = all(classify_text(t) == k for t, k in cases)
    check("意图分类", ok, "、".join(f"{k[0]}×{k[1]}" for _, k in cases))
    check("意图分类：人工提示优先", classify_text(BEAM_TXT, "拱桥") == ("arch", "2d"))


def t_beam_bridge_staged():
    """档二：2D 梁桥的自然语言施工阶段 + 车道活载解析。"""
    doc, src, _ = ModelDoc.from_text(
        "三跨连续梁桥总长90米，逐跨施工，一期恒载20kN/m，二期铺装10kN/m，双向四车道")
    check("梁桥：施工/车道/二期解析",
          doc.kind == "beam_bridge" and doc.dimension == "2d"
          and doc.params["staged"] is True and doc.params["lane"] is True
          and doc.params["q2_kN"] == 10.0 and doc.params["n_spans"] == 3,
          f"staged={doc.params['staged']} lane={doc.params['lane']} "
          f"q2={doc.params['q2_kN']} ns={doc.params['n_spans']}")
    spec = doc.model
    check("梁桥：施工序列 + 车道生成",
          len(spec.stages) == 4 and len(spec.lanes) == 1,
          f"阶段{len(spec.stages)} 车道{len(spec.lanes)}")


def t_scope():
    """口径：双索面拒绝、悬臂与半漂浮不能混写、能力标志跟随实际内核。"""
    dual, _, _ = ModelDoc.from_text("双索面空间斜拉桥，主跨200米，边跨80米")
    errs = dual.validate()
    check("双索面被拒绝", any("单索面" in e for e in errs),
          errs[0][:42] if errs else "未拒绝")

    single, _, _ = ModelDoc.from_text(
        "空间单索面斜拉桥，主跨200米，边跨80米，塔高60米")
    check("单索面空间模型可计算", not single.validate(),
          "；".join(single.validate())[:60])
    check("成桥默认半漂浮", single.info.get("system") == "semi",
          str(single.info.get("system")))
    check("成桥走非线性内核",
          single.solver_kind == "cable_stayed" and single.capability()["nonlinear"],
          single.solver_kind)

    mixed, _, _ = ModelDoc.from_text(
        "空间斜拉桥，主跨200米，悬臂施工，合拢后改为半漂浮")
    merrs = mixed.validate()
    check("悬臂加半漂浮被拒绝", any("塔梁固结" in e or "半漂浮" in e for e in merrs),
          merrs[0][:48] if merrs else "未拒绝")

    staged, _, _ = ModelDoc.from_text(
        "空间单索面斜拉桥，主跨200米，边跨80米，悬臂施工")
    check("悬臂模型校验通过", not staged.validate(),
          "；".join(staged.validate())[:60])
    check("悬臂与成桥都是塔梁固结", staged.info.get("system") == "fixed",
          str(staged.info.get("system")))
    check("悬臂走线性内核且仍记为有索",
          staged.solver_kind == "grillage3d"
          and staged.capability()["cable"]
          and not staged.capability()["nonlinear"],
          staged.solver_kind)

    sus, _, _ = ModelDoc.from_text("空间单索面地锚式悬索桥，主跨300米")
    check("悬索走非线性内核",
          sus.solver_kind == "suspension" and sus.capability()["nonlinear"],
          sus.solver_kind)

    flat, _, _ = ModelDoc.from_text("双塔三跨斜拉桥，主跨200米，边跨80米")
    nl_stage, _, notes = ModelDoc.from_text(
        "空间单索面斜拉桥，主跨80米，悬臂施工，非线性施工")
    check("非线性施工被单独打开",
          nl_stage.params.get("staged") and nl_stage.params.get("nonlinear_stages"),
          "；".join(notes.notes)[:48] if notes else "")
    plain, _, _ = ModelDoc.from_text("空间单索面斜拉桥，主跨80米，悬臂施工")
    ps, src, _ = ModelDoc.from_text(
        "跨度10米简支梁，曲线预应力，张拉力1000千牛，矢高0.3米向下")
    ps.build()
    tendon = ps.model.stages[0].prestress[0]
    check("预应力梁从自然语言生成",
          ps.dimension == "3d" and src == "rule(预应力梁)"
          and tendon.P == 1.0e6 and tendon.s == -0.3 and tendon.elems == [1, 2],
          src)
    plain_beam, _, _ = ModelDoc.from_text("跨度10米简支梁，均布荷载10kN/m")
    check("不含预应力的简支梁仍是二维梁",
          plain_beam.dimension == "2d" and plain_beam.builder_key == "beam")

    check("只写悬臂施工仍走线性阶段",
          plain.params.get("staged") and not plain.params.get("nonlinear_stages"))
    plain.build()
    ages = [s.time for s in plain.model.stages]
    dat = plain.model.to_dat(compact=False)
    check("悬臂每段都写了递增龄期",
          len(ages) >= 4 and ages[0] == 7.0
          and all(b > a for a, b in zip(ages, ages[1:]))
          and plain.model.creep_phi0 == 2.0
          and dat.count("TIME=") == len(ages)
          and "PHI0=2" in dat,
          " ".join(f"{t:g}" for t in ages[:4]) + "…")
    paced, _, pace_notes = ModelDoc.from_text(
        "空间单索面斜拉桥，主跨80米，悬臂施工，加载龄期14天，每段10天")
    paced.build()
    pace_ages = [s.time for s in paced.model.stages]
    check("悬臂工期按句子里的加载龄期和每段天数",
          pace_ages[0] == 14.0
          and all(abs((b - a) - 10.0) < 1e-9 for a, b in zip(pace_ages, pace_ages[1:]))
          and paced.model.creep_t0 == 14.0
          and paced.model.creep_phi0 == 2.0
          and any("14" in n and "10" in n for n in pace_notes.notes),
          " ".join(f"{t:g}" for t in pace_ages[:3]))
    from model_doc import jtg3362_nominal_creep
    aged, _, aged_notes = ModelDoc.from_text(
        "空间单索面斜拉桥，主跨80米，悬臂施工，加载龄期14天，"
        "相对湿度55%，理论厚度200毫米，混凝土C50")
    aged.build()
    phi14, _ = jtg3362_nominal_creep(55.0, 200.0, 48.0, 14.0)
    phi7, _ = jtg3362_nominal_creep(55.0, 200.0, 48.0, 7.0)
    check("规范徐变的加载龄期跟句子走",
          aged.model.creep_t0 == 14.0
          and aged.model.stages[0].time == 14.0
          and abs(phi14 - phi7) / phi7 > 0.05
          and f"{phi14:.3f}" in " ".join(aged_notes.notes),
          f"φ0(14)={phi14:.3f} φ0(7)={phi7:.3f}")
    dry, _, dry_notes = ModelDoc.from_text(
        "空间单索面斜拉桥，主跨80米，悬臂施工，相对湿度55%，理论厚度200毫米，"
        "混凝土C50，收缩开始龄期3天")
    dry.build()
    check("悬臂收缩开始龄期写进模型",
          dry.model.shrinkage_ts == 3.0
          and "TS=3" in dry.model.to_dat(compact=False)
          and any("干燥从第 3" in n for n in dry_notes.notes),
          "")
    humid, _, hnotes = ModelDoc.from_text(
        "空间单索面斜拉桥，主跨80米，悬臂施工，相对湿度55%，理论厚度200毫米，混凝土C50")
    humid.build()
    hdat = humid.model.to_dat(compact=False)
    phi0_h, beta_h = jtg3362_nominal_creep(55.0, 200.0, 48.0, 7.0)
    check("悬臂写了湿度就改用规范徐变曲线",
          humid.model.creep_phi0 == 0.0
          and humid.model.creep_rh == 55.0
          and humid.model.creep_h == 200.0
          and abs(humid.model.creep_fcm - 48.0) < 1e-9
          and "PHI0=" not in hdat
          and "RH=55" in hdat and "H=200" in hdat and "FCM=48" in hdat
          and any("JTG 3362" in n for n in hnotes.notes)
          and phi0_h > 2.2,
          f"φ0={phi0_h:.3f} βH={beta_h:.1f}")
    from result_3d import SolveResult
    lin_note = SolveResult(kind="grillage3d", solver="example_3d_grillage.exe").tables(
        humid.model)["stage_schedule"]["note"]
    check("线性阶段写明未按规范曲线计算徐变和收缩",
          "φ0=2.727" in lin_note and "4.781e-04" in lin_note
          and "没有按这条曲线" in lin_note,
          lin_note[-40:])
    keep_phi, _, keep_notes = ModelDoc.from_text(
        "空间单索面斜拉桥，主跨80米，悬臂施工，徐变系数2，相对湿度55%，理论厚度200毫米")
    keep_phi.build()
    check("悬臂写了徐变系数仍用 φ0=2",
          keep_phi.model.creep_phi0 == 2.0
          and keep_phi.model.creep_rh == 0.0
          and "PHI0=2" in keep_phi.model.to_dat(compact=False)
          and any("不改用" in n for n in keep_notes.notes),
          "")
    long_span, _, long_notes = ModelDoc.from_text(
        "空间单索面斜拉桥，主跨400米，悬臂施工")
    check("主跨达到400米的悬臂施工走非线性",
          long_span.params.get("staged") and long_span.params.get("nonlinear_stages")
          and any("400" in n for n in long_notes.notes),
          "；".join(long_notes.notes)[:80])
    mid_span, _, _ = ModelDoc.from_text(
        "空间单索面斜拉桥，主跨200米，悬臂施工")
    check("主跨200米的悬臂施工仍走线性",
          mid_span.params.get("staged") and not mid_span.params.get("nonlinear_stages"))
    forced_linear, _, _ = ModelDoc.from_text(
        "空间单索面斜拉桥，主跨400米，悬臂施工，线性施工")
    check("写明线性施工时大跨仍走线性",
          forced_linear.params.get("staged")
          and not forced_linear.params.get("nonlinear_stages"))

    check("二维斜拉标记为非线性索",
          flat.capability()["cable"] and flat.capability()["nonlinear"],
          str(flat.capability()))

    from model_spec_3d import (
        Element3D, ElemTypeDef3D, Material3D, ModelSpec3D, Node3D, RealConst3D,
    )
    from monitor_plan import monitoring_table
    from result_3d import CABLE_SAFETY_MIN, SolveResult

    check("拉索安全系数只保留 2.5", CABLE_SAFETY_MIN == 2.5, str(CABLE_SAFETY_MIN))
    book = Path(__file__).with_name("report_docx.py").read_text(encoding="utf-8")
    check("计算书不再写拉索不小于 2.0", "不小于 2.0" not in book and "低于 2.0" not in book)

    model = ModelSpec3D(
        title="监控样例",
        materials=[Material3D(id=1, E=3.45e10)],
        reals=[RealConst3D(id=1, A=1.0, IY=1.0, IZ=1.0, J=1.0)],
        nodes=[
            Node3D(id=1001, x=0, y=0, z=0),
            Node3D(id=1002, x=100, y=0, z=0),
            Node3D(id=1003, x=200, y=0, z=0),
            Node3D(id=2001, x=0, y=0, z=0),
            Node3D(id=2008, x=0, y=0, z=60),
        ],
        etypes=[ElemTypeDef3D(id=1, kind="BEAM", mat=1, real=1)],
        elements=[
            Element3D(id=101, etype=1, n1=1001, n2=1002),
            Element3D(id=102, etype=1, n1=1002, n2=1003),
            Element3D(id=201, etype=1, n1=2001, n2=2008),
        ],
    )
    res = SolveResult(
        kind="cable_stayed",
        final_disp={(1002, 3): -0.030, (2008, 1): 0.040},
        cables=[{"elem": 9001, "T": 2.0e6}, {"elem": 9002, "T": 1.0e6}],
    )
    tab = monitoring_table(res, model)
    text = " ".join(" ".join(map(str, row)) for row in (tab or {}).get("rows", []))
    check("监控细则含跨中、塔顶和索力",
          tab is not None and "跨中" in text and "塔顶" in text and "9001" in text,
          text[:80])
    check("跨中允许偏差取 L/5000",
          any("±40 mm" in row[2] for row in tab["rows"]),
          str([row[2] for row in tab["rows"]]))


def t_opt():
    """一轮优化：面积乘子、最小弯曲能重算模型、弯矩变差则退回。"""
    from cable_opt import SCALE_MIN, TARGET_SF, judge, propose_area_scale, retarget_model
    from cable_stayed_3d import build_cable_stayed_3d
    from result_3d import CABLE_FPU_DEFAULT, SolveResult, _elem_area_m2

    model, _info = build_cable_stayed_3d(
        n_cable=2, main_span=80.0, side_span=32.0, tower_height=24.0)
    eid = model.cable_bridge.cables[0].elem
    area = _elem_area_m2(model, eid)
    res = SolveResult(
        kind="cable_stayed", converged=True,
        cables=[{"elem": eid, "T": area * CABLE_FPU_DEFAULT / 4.0}])
    scale = propose_area_scale(res, model)
    expect = min(1.0, max(SCALE_MIN, TARGET_SF / 4.0))
    check("面积乘子朝安全系数 2.5 收一档",
          scale is not None and abs(scale - expect) < 1e-12, f"{scale} vs {expect}")

    tight = SolveResult(
        kind="cable_stayed",
        cables=[{"elem": eid, "T": area * CABLE_FPU_DEFAULT / 2.6}])
    check("贴近限值不再减面积", propose_area_scale(tight, model) is None)

    new = retarget_model(model, scale)
    new_area = _elem_area_m2(new, eid)
    check("拉索面积按乘子写入复制模型",
          abs(new_area - area * scale) < 1e-12 and abs(_elem_area_m2(model, eid) - area) < 1e-12,
          f"{area:.6g} → {new_area:.6g}")
    check("目标索力清零并改用最小弯曲能",
          new.cable_bridge.method == "MINENERGY"
          and all(c.tension == 0.0 for c in new.cable_bridge.cables)
          and model.cable_bridge.cables[0].tension > 0)

    deck = next(e.id for e in model.elements if e.id < 500)
    base = SolveResult(
        kind="cable_stayed", converged=True,
        cables=[{"elem": eid, "T": area * CABLE_FPU_DEFAULT / 4.0}],
        girder_ends={(deck, 1): {"My": 1.0e6}})
    cand_model = retarget_model(model, scale)
    good = SolveResult(
        kind="cable_stayed", converged=True,
        cables=[{"elem": eid, "T": new_area * CABLE_FPU_DEFAULT / 2.7}],
        girder_ends={(deck, 1): {"My": 0.9e6}})
    bad = SolveResult(
        kind="cable_stayed", converged=True,
        cables=[{"elem": eid, "T": new_area * CABLE_FPU_DEFAULT / 2.7}],
        girder_ends={(deck, 1): {"My": 1.2e6}})
    check("弯矩下降且安全系数够则采纳",
          judge(base, good, model, cand_model)["accepted"])
    rejected = judge(base, bad, model, cand_model)
    check("弯矩上升则退回",
          not rejected["accepted"] and any("弯矩" in s for s in rejected["reasons"]),
          "；".join(rejected["reasons"]))


def main() -> int:
    print("=" * 74)
    print("ModelDoc（C 方案）验证")
    print("=" * 74)
    for fn in (t_beam_equivalent, t_bridge_dat_identical, t_topology_rebuild,
               t_json_roundtrip, t_validate, t_capability, t_classify,
               t_beam_bridge_staged, t_scope, t_opt):
        print(f"\n[{fn.__name__}]")
        fn()

    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 74)
    print(f"  {n_pass}/{len(PASS)} 通过")
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
