"""verify_stage_tension_3d.py
=============================
3D 梁格「逐跨施工」补齐阶段张拉入口的端到端回归。

原先 `grillage_3d.py` 的 `staged=True` 只拆阶段 + 挂本跨荷载，界面「逐跨施工」
不生成任何预应力束；3D 内核早就支持 `*STAGE ... PRESTRESS=`（等效荷载法），
BridgeStudio 却既没有建造器参数、也没有控件，用户只能手写 `.dat` 或调 API。
`使用说明.md` 11.1 把这条明确记成「3D 梁格的逐跨施工仍只拆阶段、不生成张拉」。
本脚本锁住整条链：

1. 离线：默认口径 `.dat` 逐位不变；P>0 时每跨每条纵梁线各挂一束，单元链
   必须是本阶段激活、首尾相接的连续开链；非法参数硬报错，不静默兜底。
2. ModelDoc：DEFAULTS / FIELDS / `param_rows()` / `validate()` / `to_dat()`
   走同一条链，控件只在开了逐跨施工时出现。
3. live：`example_3d_grillage.exe` 能解带多束阶段预应力的 `.dat`；
   无外荷载时每条纵梁的成桥态轴力 **精确 = −P·loss**（等效荷载法的
   自平衡轴压力对，全宽 P 按 n_lines 均分），loss / e / s 各自生效。
4. 结果→报告：`SolveResult.stage_prestress`、`stage_prestress_table` 明细表、
   两条工程校核、报告「施工阶段预应力」专章与 HTML 出口同源。
5. 编号容量：默认参数走 legacy 号段（与改动前 `.dat` 逐位一致，不刷新
   黄金基线）；`n_lines ≥ 5` 或总梁段数 > 99 自动切 wide 大号段，节点/单元
   号唯一、张拉链引用完整，live 闭式解仍是 ``−P·loss/n_lines``。

理论口径：等效荷载法把 Pe=P·loss 换成轴向缩短与端部偏心弯矩（曲线筋再加
跨内均布与锚固竖向力），不是 TENDON 的筋-混凝土协同，也不含 JTG 3362
分项损失；后者走 `LOSSREF=` / `loss_json`，本脚本另有一条「不互相污染」断言。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fem_eigen_3d import fem_eigen_3d_available, solve_3d
from grillage_3d import build_box_grillage_3d, numbering_plan
from model_doc import BUILDERS, DEFAULTS, ModelDoc
from model_spec_3d import validate_model_3d

OUT = Path(__file__).parent / "out" / "verify_stage_tension_3d"
RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((ok, name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


# ---------------------------------------------------------------- 基准
SPANS = (30.0, 50.0, 30.0)
N_LINES = 3
P_TOTAL_KN = 3000.0
P_LINE = P_TOTAL_KN * 1e3 / N_LINES      # 1e6 N / 束
LOSS = 0.85
E_M = -0.4
S_M = -0.2
# 无外荷载：成桥态轴力只由预应力产生，oracle 才能写成干净闭式解。
BASE = dict(spans=SPANS, n_lines=N_LINES, q_deck=0.0, dens=0.0,
            lane_code="", staged=True)


def stage_tokens(dat: str, sid: int) -> list[str]:
    """取 `*STAGE` 第 sid 行的空白分隔令牌。"""
    for ln in dat.splitlines():
        t = ln.split()
        if t and t[0] == str(sid) and any(x.startswith("ACTIVE=") for x in t):
            return t
    return []


def prestress_tokens(dat: str, sid: int) -> list[str]:
    return [t for t in stage_tokens(dat, sid) if t.startswith("PRESTRESS=")]


def longitudinal_elements(m) -> list[int]:
    """纵梁单元按 etype 判定，不按 `e.id < 500` 反解（wide 号段会失效）。"""
    return [e.id for e in m.elements if e.etype == 1]


# ---------------------------------------------------------------- 离线
def offline() -> None:
    print("\n[1] 离线：默认口径、束链格式与非法参数")
    m0 = build_box_grillage_3d(staged=True)
    m1 = build_box_grillage_3d(staged=True, stage_tension_kN=0.0,
                               stage_tension_e=0.0, stage_tension_loss=1.0,
                               stage_tension_s=0.0)
    check("默认张拉力 0 → .dat 逐位不变", m0.to_dat() == m1.to_dat())
    check("默认不写 PRESTRESS=", "PRESTRESS=" not in m0.to_dat())
    check("默认阶段不挂束", all(not s.prestress for s in m0.stages))

    m = build_box_grillage_3d(**BASE, stage_tension_kN=P_TOTAL_KN,
                              stage_tension_e=E_M, stage_tension_loss=LOSS)
    dat = m.to_dat()
    check("3 阶段 × 3 纵梁线 = 9 束",
          len(m.stages) == 3 and [len(s.prestress) for s in m.stages] == [3, 3, 3],
          str([len(s.prestress) for s in m.stages]))
    check("每阶段行内 3 个 PRESTRESS= 令牌",
          all(len(prestress_tokens(dat, k)) == 3 for k in (1, 2, 3)),
          str([len(prestress_tokens(dat, k)) for k in (1, 2, 3)]))
    check("直线筋走位置模式且写 loss",
          prestress_tokens(dat, 1)[0] == "PRESTRESS=101-112:1000000:-0.4:0.85",
          prestress_tokens(dat, 1)[0])
    check("三跨单元链连续不重叠",
          [t.split(":")[0].replace("PRESTRESS=", "")
           for k in (1, 2, 3) for t in prestress_tokens(dat, k)][::3]
          == ["101-112", "113-132", "133-144"],
          " ".join(t.split(":")[0] for t in prestress_tokens(dat, 1)))

    # 链校验：全部在本阶段激活、首尾相接成开链（内核 solveModel3DStages 的
    # 硬条件，写错了 live 才炸，不如离线先锁住）。
    node_of = {e.id: (e.n1, e.n2) for e in m.elements}
    chain_ok = True
    for s in m.stages:
        act = set(s.activate)
        for ps in s.prestress:
            if not set(ps.elems) <= act:
                chain_ok = False
            deg: dict[int, int] = {}
            for eid in ps.elems:
                for nd in node_of[eid]:
                    deg[nd] = deg.get(nd, 0) + 1
            if (len(deg) != len(ps.elems) + 1
                    or sorted(deg.values()) != [1, 1] + [2] * (len(ps.elems) - 1)):
                chain_ok = False
    check("每束都是本阶段激活的连续开链", chain_ok)

    check("全宽 P 按纵梁线均分",
          all(abs(ps.P - P_LINE) < 1e-9 for s in m.stages for ps in s.prestress),
          f"P={m.stages[0].prestress[0].P:.0f} N")
    check("e / s / loss 进数据类",
          all(ps.e == E_M and ps.s == 0.0 and ps.loss == LOSS
              for s in m.stages for ps in s.prestress))
    check("引用完整性校验通过",
          not validate_model_3d(m)[0], str(validate_model_3d(m)[0])[:80])

    curved = build_box_grillage_3d(**BASE, stage_tension_kN=P_TOTAL_KN,
                                   stage_tension_s=S_M)
    check("曲线筋走关键字模式且写 S=",
          all("P=" in t and ":E=" in t and ":S=" in t
              for k in (1, 2, 3) for t in prestress_tokens(curved.to_dat(), k)),
          prestress_tokens(curved.to_dat(), 1)[0])

    bad = [
        (dict(staged=False, stage_tension_kN=P_TOTAL_KN), "未开逐跨施工"),
        (dict(stage_tension_kN=P_TOTAL_KN, stage_tension_loss=1.5), "loss>1"),
        (dict(stage_tension_kN=P_TOTAL_KN, stage_tension_loss=0.0), "loss=0"),
        (dict(stage_tension_kN=P_TOTAL_KN, stage_tension_e=3.0), "|e|>h/2"),
        (dict(stage_tension_kN=P_TOTAL_KN, stage_tension_s=3.0), "|s|>h/2"),
        (dict(stage_tension_kN=-1.0), "张拉力为负"),
    ]
    for kw, label in bad:
        try:
            build_box_grillage_3d(**{**BASE, **kw})
            check(f"非法参数硬报错：{label}", False, "静默通过了")
        except ValueError as e:
            check(f"非法参数硬报错：{label}", True, str(e)[:38])
    p0, p99 = numbering_plan(3, 44), numbering_plan(3, 99)
    p100, p5 = numbering_plan(3, 100), numbering_plan(5, 44)
    check("默认 (3,44) 走 legacy 号段（黄金基线不变）",
          p0.legacy and (p0.node_stride, p0.elem_stride, p0.cross_base)
          == (1000, 100, 500))
    check("边界 (3,99) 仍是 legacy；(3,100)/(5,44) 切 wide",
          p99.legacy and not p100.legacy and not p5.legacy)
    check("wide 号段留足段间空隙",
          p100.node_stride > 100 and p100.elem_stride > 100
          and p5.cross_base > 5 * p5.elem_stride + 44)


def doc_chain() -> None:
    print("\n[2] ModelDoc：默认参数、控件显隐、中文校验、.dat 同源")
    d = DEFAULTS["grillage3d"]
    check("DEFAULTS 四个张拉键且默认不改口径",
          d.get("stage_tension_kN") == 0.0
          and d.get("stage_tension_e") == 0.0
          and d.get("stage_tension_loss") == 1.0
          and d.get("stage_tension_s") == 0.0)

    doc = ModelDoc("beam_bridge", dimension="3d")
    keys = lambda x: [f["key"] for row in x.param_rows() for f in row]
    check("builder_key 是 grillage3d", doc.builder_key == "grillage3d")
    doc.params["staged"] = False
    check("未开逐跨施工时控件隐藏",
          "stage_tension_kN" not in keys(doc))
    doc.params["staged"] = True
    check("开了逐跨施工后四个控件齐全",
          all(k in keys(doc) for k in ("stage_tension_kN", "stage_tension_e",
                                       "stage_tension_loss", "stage_tension_s")))

    doc.params.update(stage_tension_kN=P_TOTAL_KN, stage_tension_loss=LOSS,
                      stage_tension_e=E_M, stage_tension_s=0.0)
    check("validate 通过", not doc.validate(), str(doc.validate())[:60])
    direct = build_box_grillage_3d(
        spans=SPANS, B=doc.params["B"], h=doc.params["h"],
        tf=doc.params["tf"], tw=doc.params["tw"],
        n_lines=doc.params["n_lines"], panel=doc.params["panel"],
        q_deck=doc.params["q"] * 1e3, staged=True,
        lane_code="JTG_D60" if doc.params["lane"] else "",
        stage_tension_kN=P_TOTAL_KN, stage_tension_e=E_M,
        stage_tension_loss=LOSS, stage_tension_s=0.0)
    check("ModelDoc.to_dat 与建造器同源",
          doc.to_dat() == direct.to_dat())

    for key, val, word in [
        ("stage_tension_e", 2.0, "钢束偏心"),
        ("stage_tension_s", 2.0, "曲线筋矢高"),
        ("stage_tension_loss", 1.5, "总损失系数"),
    ]:
        doc.params[key] = val
        errs = doc.validate()
        check(f"{word}越界给中文提示", any(word in e for e in errs),
              "；".join(errs)[:70])
        doc.params[key] = {"stage_tension_e": E_M,
                           "stage_tension_s": 0.0,
                           "stage_tension_loss": LOSS}[key]
    doc.params["staged"] = False
    check("未开 staged 却设张拉力被拦下",
          any("逐跨张拉" in e for e in doc.validate()),
          "；".join(doc.validate())[:70])
    doc.params["staged"] = True

    # 3D 分项损失走 ps_beam 的 loss_json，不能被梁格张拉键污染。
    ps = ModelDoc("beam", dimension="3d")
    ps.params["loss_json"] = '{"sigma_con": 1395000000.0, "x": 10.0}'
    txt = ps.to_dat()
    check("ps_beam 的 LOSSREF 路径不受影响",
          "LOSSREF=1" in txt and "*PRESTRESS_LOSS" in txt
          and "stage_tension" not in txt)
    check("ps_beam 分项损失参数校验通过", not ps.validate(),
          str(ps.validate())[:60])
    # ModelDoc.to_dat() 必须按维度分流：3D 的 ps_beam 若按 kind="beam"
    # 掉进 2D spec_to_dat 会抛 "ModelSpec3D 没有 length"，界面 is_stale()
    # 正好走这里。
    check("3D ps_beam 的 to_dat() 与模型同源",
          txt == ps.build().model.to_dat())


# ---------------------------------------------------------------- live
def live() -> None:
    print("\n[3] live：example_3d_grillage.exe 求解多束阶段预应力")
    if not fem_eigen_3d_available():
        check("3D 内核可用", False, "未找到 example_3d_grillage.exe")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        r0 = solve_3d(build_box_grillage_3d(**BASE),
                      out_dir=OUT / "none", dat_name="none.dat")
        r1 = solve_3d(build_box_grillage_3d(**BASE,
                                            stage_tension_kN=P_TOTAL_KN),
                      out_dir=OUT / "tension", dat_name="tension.dat")
        r2 = solve_3d(build_box_grillage_3d(
            **BASE, stage_tension_kN=P_TOTAL_KN,
            stage_tension_loss=LOSS, stage_tension_e=E_M),
            out_dir=OUT / "loss_e", dat_name="loss_e.dat")
        r3 = solve_3d(build_box_grillage_3d(
            **BASE, stage_tension_kN=P_TOTAL_KN, stage_tension_s=S_M),
            out_dir=OUT / "curved", dat_name="curved.dat")
    except Exception as e:                       # noqa: BLE001 - 要打印内核报错
        check("live 求解不抛异常", False, f"{type(e).__name__}: {str(e)[:180]}")
        return
    check("live 求解不抛异常", True)

    sp = r1["stage_prestress"]
    check("内核回传 9 束·阶段有效预加力", len(sp) == 9, str(sorted(sp)))
    check("每束 Pe = P·loss = 1000 kN",
          all(abs(v["Pe"] - P_LINE) < 1e-6 for v in sp.values()),
          str(sp.get((1, 1))))
    check("无外荷载对照：纵梁轴力≈0",
          all(abs(r0["final_beam"][(e, "i")]["N"]) < 1e-6
              for e in longitudinal_elements(build_box_grillage_3d(**BASE))),
          "")

    m = build_box_grillage_3d(**BASE)
    lon = longitudinal_elements(m)
    n1 = [r1["final_beam"][(e, "i")]["N"] for e in lon]
    check("逐跨张拉：每条纵梁成桥态轴力 = −P/n_lines",
          bool(n1) and max(abs(x + P_LINE) for x in n1) < 1e-6 * P_LINE,
          f"N∈[{min(n1):.0f}, {max(n1):.0f}] N")

    n2 = [r2["final_beam"][(e, "i")]["N"] for e in lon]
    check("loss=0.85 → 轴力 = −0.85·P/n_lines",
          bool(n2) and max(abs(x + LOSS * P_LINE) for x in n2) < 1e-6 * P_LINE,
          f"N∈[{min(n2):.0f}, {max(n2):.0f}] N")
    check("loss=0.85 → 每束 Pe = 850 kN",
          all(abs(r2["stage_prestress"][k]["Pe"] - LOSS * P_LINE) < 1e-6
              for k in r2["stage_prestress"]))
    my = [abs(v["My"]) for (e, end), v in r2["final_beam"].items() if e in set(lon)]
    check("偏心束 e=−0.4 m 产生非零弯矩",
          bool(my) and max(my) > 1.0e3, f"max|My|={max(my, default=0):.3g} N·m")

    # 曲线筋：s<0 → 跨内上拱（uz>0），且不改变轴压闭式解。
    uz = [r3["final_disp"].get((2000 + i, 3), 0.0) for i in (6, 22, 38)]
    check("s<0 → 跨内上拱（uz>0）", all(u > 0 for u in uz),
          " ".join(f"{u*1e3:.3f}mm" for u in uz))
    check("直线筋 s=0 无外荷载时不产生竖向位移",
          all(abs(r1["final_disp"].get((2000 + i, 3), 0.0)) < 1e-12
              for i in (6, 22, 38)))
    check("曲线筋不破坏轴压闭式解",
          all(abs(r3["final_beam"][(e, "i")]["N"] + P_LINE) < 1e-6 * P_LINE
              for e in lon))
    check("横向对称：边线与中线跨中上拱一致",
          abs(r3["final_disp"].get((1006, 3), 0.0)
              - r3["final_disp"].get((3006, 3), 0.0)) < 1e-8)

    # 结果层 → 报告层：明细表、校核、专章、HTML 出口。
    import solver_3d
    from report_3d import build_document, render_html
    # 报告模型带一点桥面荷载：全零荷载会让内力图的 ylim 退化成 0 区间，
    # matplotlib 抛 ylim 警告与本章无关，别让它污染回归输出。
    REP = {**BASE, "q_deck": 50e3}
    report_model = lambda **kw: build_box_grillage_3d(**{**REP, **kw})
    model_t = report_model(stage_tension_kN=P_TOTAL_KN,
                           stage_tension_loss=LOSS, stage_tension_e=E_M)
    res = solver_3d.solve(model_t, out_dir=OUT / "report", kind="grillage3d")
    check("SolveResult.stage_prestress 承接 9 束",
          len(res.stage_prestress) == 9, str(len(res.stage_prestress)))
    tab = res.tables(model_t).get("stage_prestress_table")
    check("结果层出施工阶段预应力明细表",
          tab is not None and len(tab["rows"]) == 9
          and "Pe (kN)" in tab["headers"], "" if tab is None else str(tab["headers"]))
    check("明细表 Pe 列与内核同源",
          tab is not None and all(float(row[-1]) == LOSS * P_LINE / 1e3
                                  for row in tab["rows"]))
    names = [c.name for c in res.checks(model_t)]
    check("校核出「有效预加力为正」",
          any("有效预加力为正" in n for n in names))
    check("校核出「有效预加力合计」",
          any("有效预加力合计" in n for n in names))
    rep = build_document(res, model_t)
    check("报告出「施工阶段预应力」专章与其表",
          any(b.get("type") == "h1" and b.get("text") == "施工阶段预应力"
              for b in rep.blocks)
          and any(b.get("type") == "table"
                  and b.get("key") == "stage_prestress_table"
                  for b in rep.blocks))
    html = render_html(rep)
    check("HTML 出口含专章且无 ** 残留",
          "施工阶段预应力" in html and "**" not in html)
    model_0 = report_model()
    check("未张拉模型不凭空造章",
          not any(b.get("key") == "stage_prestress_table"
                  for b in build_document(
                      solver_3d.solve(model_0, out_dir=OUT / "none_doc",
                                      kind="grillage3d"),
                      model_0).blocks))


#: 12 个建造器默认 `.dat` 的 sha256 前 16 位（2026-11-10 基线）。
#: 这是「编号方案切换后默认输出逐位不变」的黄金对拍：改默认输出行 /
#: 包络乘子 / 积分权重必须刷新本表，并在 FEM-Eigen/CHANGELOG.md 写数值依据。
DEFAULT_DAT_SHA = {
    "beam": "63093dbf87e2c54a",
    "ps_beam": "ea8ee5edf8ad36e0",
    "arch": "cdf5d3175778c56a",
    "arch_3d": "a2291d7f2a6d66b5",
    "truss": "de23ea23554569ff",
    "truss_3d": "8f5a779919a66681",
    "beam_bridge": "5771177801ba2730",
    "grillage3d": "a806a48d86cbb56c",
    "cable_stayed_2d": "e3c8d2f735ab89d9",
    "cable_stayed": "ea74801fc392d992",
    "suspension_2d": "bab979987f61d4ee",
    "suspension": "acebaabcda72c45e",
}


def wide_numbering() -> None:
    """编号容量放开：默认哈希不变、wide 号段自洽、live 闭式解同源。"""
    print("\n[5] 编号容量：默认 .dat 黄金哈希 + wide 自洽 + live 闭式解")
    for (kind, dim), b in BUILDERS.items():
        dat = ModelDoc(kind, dict(DEFAULTS[b]), dimension=dim).to_dat()
        h = hashlib.sha256(dat.encode()).hexdigest()[:16]
        check(f"默认 .dat 黄金哈希：{b}", h == DEFAULT_DAT_SHA[b],
              f"{h} / {DEFAULT_DAT_SHA[b]}")

    cases = (
        ("n_lines5", dict(n_lines=5, stage_tension_kN=P_TOTAL_KN), 5),
        ("panel1", dict(panel=1.0, stage_tension_kN=P_TOTAL_KN), N_LINES),
        ("n_lines7_panel1",
         dict(n_lines=7, panel=1.0, stage_tension_kN=P_TOTAL_KN), 7),
    )
    for name, kw, nl in cases:
        m = build_box_grillage_3d(**{**BASE, **kw})
        eids = [e.id for e in m.elements]
        nids = [n.id for n in m.nodes]
        eset = set(eids)
        act = [x for s in m.stages for x in s.activate]
        chains = [ps for s in m.stages for ps in s.prestress]
        errs, _ = validate_model_3d(m)
        check(f"wide {name}：节点/单元号唯一",
              len(set(nids)) == len(nids) and len(set(eids)) == len(eids),
              f"{len(nids)} 节点 / {len(eids)} 单元")
        check(f"wide {name}：引用完整性无硬错误", not errs, str(errs[:1])[:80])
        check(f"wide {name}：阶段激活恰好覆盖全部单元一次",
              sorted(act) == sorted(eids), f"{len(act)} / {len(eids)}")
        check(f"wide {name}：每跨每线一束且链引用完整",
              len(chains) == len(m.stages) * nl
              and all(all(e in eset for e in ps.elems) for ps in chains),
              f"{len(chains)} 束")

    if not fem_eigen_3d_available():
        check("3D 内核可用（wide live）", False, "未找到 example_3d_grillage.exe")
        return
    for name, kw, nl in cases[:2]:
        m = build_box_grillage_3d(**{**BASE, **kw})
        try:
            r = solve_3d(m, out_dir=OUT / "wide" / name, dat_name="wide.dat")
        except Exception as e:                   # noqa: BLE001 - 要打印内核报错
            check(f"wide {name} live 求解不抛异常", False,
                  f"{type(e).__name__}: {str(e)[:160]}")
            continue
        check(f"wide {name} live 求解不抛异常", True)
        p_line = P_TOTAL_KN * 1e3 / nl
        sp = r["stage_prestress"]
        check(f"wide {name} 内核回传 {len(m.stages)*nl} 束有效预加力",
              len(sp) == len(m.stages) * nl
              and all(abs(v["Pe"] - p_line) < 1e-6 for v in sp.values()),
              f"{len(sp)} 束")
        lon = longitudinal_elements(m)
        ns = [r["final_beam"][(e, "i")]["N"] for e in lon
              if (e, "i") in r["final_beam"]]
        check(f"wide {name} 成桥态纵梁轴力 = −P/n_lines",
              bool(ns) and max(abs(x + p_line) for x in ns) < 1e-6 * p_line,
              f"N∈[{min(ns, default=0):.0f}, {max(ns, default=0):.0f}] N")


def main() -> int:
    print("=" * 78)
    print("3D 梁格逐跨施工张拉入口（离线 + ModelDoc + live + 报告 + 编号容量）")
    print("=" * 78)
    offline()
    doc_chain()
    live()
    wide_numbering()
    n_pass = sum(1 for ok, _, _ in RESULTS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(RESULTS)} 通过")
    return 0 if n_pass == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
