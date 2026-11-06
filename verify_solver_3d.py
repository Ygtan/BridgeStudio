"""verify_solver_3d.py —— 三维接入层回归（注册表 / SolveResult / 3D ModelDoc）。

重点不是"能不能跑通"，而是三条容易假通过的地方：

1. **内核选择**是否真的由模型自述决定（而不是界面写死）；把 cable_bridge
   摘掉，同一个模型必须换到线性内核上去。
2. **校核是否真的会失败**：干净模型 0 错 0 警告，很容易被误当成"规则生效"。
   所以每一条校核都要故障注入一次，确认它会变红。
3. **结果过期判定**是否用 .dat 文本而不是某个自增计数器——改了参数但没求解，
   界面上必须看不到旧结果。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()          # 输出被重定向到管道/文件时，GBK 会崩（见模块说明）

import json
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).parent))

from cable_stayed_3d import build_cable_stayed_3d  # noqa: E402
from grillage_3d import build_box_grillage_3d  # noqa: E402
from model_doc import ModelDoc, extract_3d_params  # noqa: E402
from result_3d import SolveResult  # noqa: E402
from solver_3d import (  # noqa: E402
    SolverUnavailable,
    capabilities,
    detect_kind,
    require,
    solve,
    solver_keys,
)

PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    PASS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<38} {detail}")
    return ok


def main() -> int:
    print("=" * 78)
    print("三维接入层回归")
    print("=" * 78)

    # ---------------------------------------------------------- ① 注册表
    print("\n[① 注册表]")
    keys = solver_keys()
    # 三个内核：梁格、斜拉桥成桥态、悬索桥成桥态。后两个共用同一个 exe，
    # 但 adapt 与出口不同，在注册表里是两个键——"同一个 exe"不等于"同一个
    # 内核"，混成一个键会让 detect_kind 的分支无处落地。
    check("注册表含三个内核",
          set(keys) == {"grillage3d", "cable_stayed", "suspension"}, str(keys))
    caps = capabilities()
    check("capabilities 与注册表同键", set(caps) == set(keys), str(caps))
    for k in keys:
        spec = require(k)                   # 不可用会抛，这里就是"可用性断言"
        check(f"{k} 元数据齐全",
              bool(spec.exe_name) and spec.build_hint.startswith("cd ") and spec.label,
              f"{spec.exe_name} / {spec.label}")

    try:
        require("nope")
        check("未知内核键抛错", False, "没有抛异常")
    except SolverUnavailable as e:
        check("未知内核键抛错并列出已注册", "已注册的有" in str(e), str(e)[:44])

    # ---------------------------------------------------------- ② 内核选择
    print("\n[② 内核由模型自述决定]")
    # 带活载 + 逐跨施工：这是界面默认（ModelDoc.DEFAULTS 里 staged/lane 均为真），
    # 之前用建造器裸默认（无活载）测，活载包络这条链就没被覆盖过。
    m_g = build_box_grillage_3d(staged=True, lane_code="JTG_D60")
    m_c, _ = build_cable_stayed_3d()
    check("梁格模型 → grillage3d", detect_kind(m_g) == "grillage3d", detect_kind(m_g))
    check("斜拉桥模型 → cable_stayed", detect_kind(m_c) == "cable_stayed", detect_kind(m_c))
    # 故障注入：摘掉 cable_bridge，同一个模型必须落到线性内核
    m_lin = m_c.model_copy(deep=True)
    m_lin.cable_bridge = None
    check("摘掉 cable_bridge → 落到线性内核",
          detect_kind(m_lin) == "grillage3d", detect_kind(m_lin))

    # ---------------------------------------------------------- ③ 梁格结果
    print("\n[③ 梁格：SolveResult 契约]")
    r_g = solve(m_g, out_dir="out/verify_3d/grillage")
    check("kind / solver / 指纹", r_g.kind == "grillage3d"
          and r_g.solver == "example_3d_grillage.exe" and len(r_g.fingerprint) == 12,
          f"{r_g.fingerprint}")
    check("位移包络键为 (node,dof) 元组",
          all(isinstance(k, tuple) and len(k) == 2 for k in r_g.disp), f"{len(r_g.disp)} 条")
    check("梁内力六分量齐全",
          bool(r_g.beam) and {"MyMin", "MyMax", "Tmin", "VzMin"} <= set(
              next(iter(r_g.beam.values()))),
          f"{len(r_g.beam)} 个单元")
    # 这是本轮发现的真 bug：summary 只输出 Mz 时，竖向受弯主梁的弯矩全是 0
    mmax = r_g.max_bending()
    check("最大竖向弯矩 My > 0（不能只输出 Mz）",
          bool(mmax and mmax[1] > 1e3), f"{mmax[1] / 1e3:.1f} kN·m" if mmax else "—")
    _tables = r_g.tables(m_g)
    check("明细表含梁内力包络", "beam_envelope" in _tables, str(list(_tables)))
    # 活载包络是这本软件唯一"活载相关"的结果：算完必须有出口，
    # 否则它就是躺在 res.live 里的一串没人看的数字。
    check("活载包络有指标", any("活载" in x.label for x in r_g.metrics(m_g)),
          str([x.label for x in r_g.metrics(m_g)]))
    check("明细表含活载包络表", "live_envelope" in _tables, str(list(_tables)))
    check("活载包络表行数 = 阶段数",
          len(_tables.get("live_envelope", {}).get("rows", [])) == len(r_g.live),
          f"{len(_tables.get('live_envelope', {}).get('rows', []))}/{len(r_g.live)}")
    _lw = r_g.live_worst()
    check("活载包络最不利值非零", bool(_lw and abs(_lw[1]) > 1e-9),
          f"阶段 {_lw[0]} {_lw[1] * 1000:.2f} mm" if _lw else "—")
    check("明细表含控制断面位移表", "control_disp" in _tables, str(list(_tables)))
    _cd = _tables.get("control_disp", {}).get("rows", [])
    check("控制断面 = 支座 + 跨中（不是全节点）",
          0 < len(_cd) < len(m_g.nodes),
          f"{len(_cd)} 行 / {len(m_g.nodes)} 节点")
    # 轴力为零是结果不是缺失：必须有一条说明，否则读者会去追问轴力图
    check("轴力为零有显式说明",
          any("轴力" in c.name for c in r_g.checks(m_g)),
          next((c.detail for c in r_g.checks(m_g) if "轴力" in c.name), "无"))
    check("指标卡非空且都有值", all(x.value for x in r_g.metrics(m_g)),
          f"{len(r_g.metrics(m_g))} 张")
    _bad = [c.name for c in r_g.checks(m_g) if not c.passed]
    check("干净梁格模型校核全通过", not _bad, "、".join(_bad))

    # ---------------------------------------------------------- ④ 过期判定
    print("\n[④ 结果过期判定]")
    check("同一模型不算过期", not r_g.is_stale(m_g.to_dat()))
    m_changed = build_box_grillage_3d(spans=(30.0, 60.0, 30.0))
    check("改了跨径就算过期", r_g.is_stale(m_changed.to_dat()))

    # ---------------------------------------------------------- ⑤ 斜拉桥结果
    print("\n[⑤ 斜拉桥：SolveResult 契约]")
    r_c = solve(m_c, out_dir="out/verify_3d/cable")
    check("收敛", r_c.converged is True, f"{r_c.iterations} 轮 / {r_c.elapsed_ms:.0f} ms")
    check("位移包络退化为 (v,v)", all(lo == hi for lo, hi in r_c.disp.values()),
          f"{len(r_c.disp)} 条")
    check("索数 48", len(r_c.cables) == 48, f"{len(r_c.cables)}")
    check("明细表含索表与制造线形",
          {"cable_table", "camber_table"} <= set(r_c.tables(m_c)),
          str(list(r_c.tables(m_c))))
    # 逐根安全系数：只给最不利值不够，施工方要核对"哪几根偏紧"
    _ct = r_c.tables(m_c)["cable_table"]
    check("索表含安全系数列", any("安全系数" in h for h in _ct["headers"]),
          str(_ct["headers"]))
    _sf = r_c.cable_safety(m_c)
    check("最不利索安全系数可算", bool(_sf and _sf[1] > 1.0),
          f"索 {_sf[0]} κ={_sf[1]:.2f}" if _sf else "—")
    check("安全系数进指标卡",
          any("安全系数" in x.label for x in r_c.metrics(m_c)),
          str([x.label for x in r_c.metrics(m_c)]))
    _sfcol = _ct["headers"].index(next(h for h in _ct["headers"] if "安全系数" in h))
    _kmin = min(r[_sfcol] for r in _ct["rows"])
    check("逐根安全系数最小值与最不利一致",
          abs(float(_kmin) - _sf[1]) < 0.02, f"{_kmin} vs {_sf[1]:.2f}")
    _bad = [c.name for c in r_c.checks(m_c) if not c.passed]
    check("成桥态校核全通过", not _bad, "、".join(_bad))
    _legacy = r_c.as_legacy_dict()
    check("as_legacy_dict 能被旧消费方吃",
          {"meta", "cables", "camber", "erected", "u", "converged"} <= set(_legacy)
          and _legacy["u"] and isinstance(_legacy["dat"], Path),
          f"u {len(_legacy['u'])} 条")
    check("to_dict 可 JSON 序列化", bool(json.dumps(r_c.to_dict(), ensure_ascii=False)))

    # 线性内核收到 CABLE：必须明说"按 TRUSS 处理"
    r_lin = solve(m_lin, out_dir="out/verify_3d/cable_as_truss")
    check("线性内核收 CABLE 会如实告警",
          any("TRUSS" in d for d in r_lin.diagnostics),
          r_lin.diagnostics[-1][:40] if r_lin.diagnostics else "无")

    # ---------------------------------------------------------- ⑥ 校核会失败
    print("\n[⑥ 校核的故障注入（不通过才算真的在检查）]")
    cases: list[tuple[str, Callable[[SolveResult], None]]] = [
        ("几何闭合误差超差", lambda r: r.meta.__setitem__("closure_err", 1e-3)),
        ("索力出现负值", lambda r: r.meta.__setitem__("tension_min_kN", -5.0)),
        ("索承担恒载不足", lambda r: r.meta.__setitem__("deck_share_pct", 42.0)),
        ("竖向不平衡", lambda r: r.meta.__setitem__("support_share_kN", 900.0)),
        ("索力跳变", lambda r: r.cables[3].__setitem__("T", r.cables[4]["T"] * 5.0)),
        ("未收敛", lambda r: setattr(r, "converged", False)),
    ]
    for name, mutate in cases:
        import copy

        broken = copy.deepcopy(r_c)
        mutate(broken)
        bad = [c for c in broken.checks(m_c) if not c.passed]
        check(f"注入「{name}」后有校核变红", bool(bad),
              bad[0].name if bad else "没有一条变红")

    # 安全系数：把索面积压到 1200 mm²，安全系数应掉到 2.5 以下
    import copy

    thin = copy.deepcopy(r_c)
    thin.cables = copy.deepcopy(r_c.cables)
    for c in thin.cables:
        c["T"] = 4000e3
    m_thin = m_c.model_copy(deep=True)
    m_thin.reals[2].A = 1200 * 1e-6
    sf = [c for c in thin.checks(m_thin) if "安全系数" in c.name]
    check("索面积过小时安全系数校核失败",
          bool(sf) and not sf[0].passed, sf[0].detail[:44] if sf else "无此项")

    # ---------------------------------------------------------- ⑦ ModelDoc 3D
    print("\n[⑦ ModelDoc 的三维类型]")
    for txt, want in (
        ("双索面空间斜拉桥，主跨200米，边跨80米", ("cable_stayed", "3d")),
        ("双塔三跨斜拉桥，主跨200米，边跨80米", ("cable_stayed", "2d")),
        ("三跨连续箱梁，边跨30米中跨50米", ("beam_bridge", "3d")),
        ("空间梁格法分析三跨连续箱梁", ("beam_bridge", "3d")),
    ):
        d, src, _ = ModelDoc.from_text(txt)
        check(f"识别「{txt[:12]}…」", (d.kind, d.dimension) == want,
              f"{d.kind}×{d.dimension} / {src}")

    doc = ModelDoc("cable_stayed", dimension="3d")
    doc.build()
    check("doc.validate() 干净", not doc.validate(), str(doc.validate())[:40])
    check("三维 capability 自述",
          doc.capability()["solve3d"] and doc.capability()["cable"]
          and not doc.capability()["matplotlib"], str(doc.capability()))
    check("summary 反映真实规模", "CABLE 48" in " ".join(doc.summary()["lines"]),
          doc.summary()["lines"][2] if len(doc.summary()["lines"]) > 2 else "—")
    check("info 派生量可从外壳取",
          doc.info.get("n_cable_actual") == 48 and len(doc.info.get("x_supports", [])) == 4,
          f"{doc.info.get('n_cable_actual')} 根 / {doc.info.get('x_supports')}")

    # 参数层面的荒谬必须在参数层面被拦（模型自洽拦不住）
    bad_docs = [
        ("边跨接近主跨", {"Ls": 190.0}),
        ("塔高过矮", {"Ht": 18.0}),
        ("索数过少", {"n": 2}),
        ("单索面积写成 cm²", {"A": 60.0}),
    ]
    for label, patch in bad_docs:
        dd = ModelDoc("cable_stayed", patch, dimension="3d")
        errs = dd.validate()
        check(f"拦下「{label}」", bool(errs), errs[0][:40] if errs else "未拦下")

    dg = ModelDoc("grillage3d", {"sp1": 50.0, "sp2": 20.0})
    check("拦下「中跨不足边跨一半」", bool(dg.validate()),
          dg.validate()[0][:40] if dg.validate() else "未拦下")

    # 端到端：doc.solve() 直接返回 SolveResult
    r_doc = doc.solve(out_dir="out/verify_3d/doc_solve")
    check("doc.solve() 返回 SolveResult", isinstance(r_doc, SolveResult), r_doc.kind)
    doc.params["Lm"] = 240.0
    doc.build()
    check("改参数后 doc.solve 的旧结果算过期", r_doc.is_stale(doc.to_dat()))

    # ---------------------------------------------------------- ⑧ 单位哨兵
    print("\n[⑧ 三维参数的单位哨兵]")
    p, notes = extract_3d_params("主跨200000米斜拉桥", "cable_stayed")
    check("主跨 mm 误写被纠正", abs(p["Lm"] - 200.0) < 1e-9, f"Lm={p['Lm']:g}")
    check("纠正在派生默认值之前（提示不自相矛盾）",
          not any("0.4×主跨 = 80000" in n for n in notes),
          [n for n in notes if "边跨" in n][:1])
    p2, _ = extract_3d_params("边跨30米中跨50000米", "grillage3d")
    check("中跨 mm 误写被纠正", abs(p2["sp2"] - 50.0) < 1e-9, f"sp2={p2['sp2']:g}")

    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(PASS)} 通过")
    return 0 if n_pass == len(PASS) else 1

if __name__ == "__main__":
    sys.exit(main())
