"""verify_view_3d.py —— 三维交互视图（场景层）的回归。

守什么
------
`view_3d` 把结果变成"场景"，再由两种渲染器分别画成 plotly（界面）与 PNG
（报告）。能出错的地方不是"画不出来"——画不出来会立刻报错——而是**画出来
但不对**：

1. **单位**。内核给 N / N·m / m，读的人要 kN / kN·m / mm。漏乘一次，
   色标上就写着 kN·m、刻度却是 7,408,900。
2. **取错量**。`camber` 的键是**节点号**，按单元号去查会全查空：云图整片
   灰，且一声不响（这条真踩过）。
3. **变形放大倍数与说明不一致**。图上 ×300、说明写 ×100，谁也不知道该信哪个。
4. **悬停点错位**。三维悬停按"最近点"命中，若把读数挂在单元端点上，鼠标
   指着一根梁会读到旁边那根的内力。

这些都只能靠对拍：把场景里的数与 `SolveResult` 里的数逐项对上。

怎么跑
------
    python verify_view_3d.py
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()          # 输出被重定向到管道/文件时，GBK 会崩（见模块说明）

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import view_3d as V
from cable_stayed_3d import build_cable_stayed_3d
from grillage_3d import build_box_grillage_3d
from result_3d import SolveResult
from solver_3d import solve

PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def near(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol * max(abs(b), 1e-12)


def main() -> int:
    m = build_box_grillage_3d(staged=True, lane_code="JTG_D60")
    res = solve(m, out_dir="out/verify_view_3d")
    mc, _info = build_cable_stayed_3d()
    resc = solve(mc, kind="cable_stayed", out_dir="out/verify_view_3d_cable")

    # ---------------------------------------------------------- 场的清单
    print("\n[场的清单由结果决定，不由模型类型硬编码]")
    fg = {f.key: f for f in V.fields(res, m)}
    fc = {f.key: f for f in V.fields(resc, mc)}
    # 2026-11-10：接入 3D 活载包络结构云图（见 envelope_3d.py）。
    # 梁格 live_beam 只有 My/Vz，斜拉桥 girder_live 另有 N；场清单由
    # **内核实际给出的分量**决定，不按桥型硬编码、不给全零分量。
    check("梁格场：几何/位移两方向/阶段 My·Vz/组合/活载 My·Vz",
          set(fg) == {"geometry", "w_min", "w_max", "My", "Vz", "combo_My",
                      "live_My", "live_Vz"},
          "、".join(fg))
    check("梁格不出 N 场（梁格无纵向约束，N 恒为 0）", "N" not in fg)
    check("梁格不出 live_N（内核没给活载轴力包络）", "live_N" not in fg)
    check("斜拉桥场：几何/成桥位移/活载 N·My·Vz/索力/预抬高",
          set(fc) == {"geometry", "w", "live_N", "live_My", "live_Vz",
                      "T", "camber"}, "、".join(fc))
    check("斜拉桥不出梁内力场（成桥态内核不输出梁格式包络）",
          not ({"My", "Vz", "N", "combo_My"} & set(fc)))
    for tag, fs in (("梁格", fg), ("斜拉桥", fc)):
        for k, f in fs.items():
            if k.startswith("live_"):
                check(f"{tag} 活载场 {k} 不冒充规范最不利包络",
                      "不是规范全工况最不利包络" in f.note
                      and "本模型车道" in f.label)
    check("场标签与单位非空",
          all(f.label and (f.unit or f.key == "geometry") for f in list(fg.values()) + list(fc.values())))
    check("默认着色场：梁格取基本组合", V.default_field(res, m) == "combo_My")
    check("默认着色场：斜拉桥取索力", V.default_field(resc, mc) == "T")

    # ---------------------------------------------------------- 几何
    print("\n[几何：单元数、分组、支承]")
    scg = V.build_scene(res, m, "geometry")
    check("构件数 = 单元数", len(scg.members) == len(m.elements),
          f"{len(scg.members)} / {len(m.elements)}")
    grp = {g: len(ms) for g, ms in scg.groups().items()}
    check("梁格分组：纵梁 + 横梁，无索", set(grp) == {"纵梁", "横梁"},
          str(grp))
    check("纵梁 132 根（44 单元 × 3 线）", grp.get("纵梁") == 132, str(grp))
    check("几何场不带值（不画空色标）", not scg.has_values)
    check("支承点数与报告同源",
          len(scg.supports) == len(__import__("report_3d")._support_node_positions(m)),
          f"{len(scg.supports)} 个")
    check("支承点位置一致",
          sorted(round(p[0], 6) for p in scg.supports) ==
          sorted(round(p[0], 6) for p in
                 __import__("report_3d")._support_node_positions(m)))

    scc = V.build_scene(resc, mc, "T")
    grpc = {g: len(ms) for g, ms in scc.groups().items()}
    check("斜拉桥分出「索」与「竖构件」", {"索", "竖构件", "主梁"} <= set(grpc),
          str(grpc))
    check("索 48 根", grpc.get("索") == len(resc.cables), str(grpc))

    # ---------------------------------------------------------- 单位
    print("\n[单位换算：色标上的数就是读数上的数]")
    scm = V.build_scene(res, m, "My")
    eid, my = res.max_bending()
    check("|My| 峰值按 kN·m 出（不是 N·m）", near(scm.vmax, my / 1e3, 1e-6),
          f"{scm.vmax:,.1f} kN·m vs 内核 {my / 1e3:,.1f}")
    check("有正负的场 0 居中", near(scm.vmin, -scm.vmax, 1e-12),
          f"[{scm.vmin:,.1f}, {scm.vmax:,.1f}]")
    check("控制构件 = 内核的 |My| 最大单元", scm.control[0] == eid,
          f"场景 {scm.control[0]} / 内核 {eid}")
    check("控制构件读数与色标上限一致",
          near(abs(float(scm.control[1].split("：")[1].split()[0].replace(",", ""))),
               scm.vmax, 1e-2), scm.control[1])

    scv = V.build_scene(res, m, "Vz")
    check("Vz 按 kN 出", scv.vmax > 100 and scv.vmax < 1e5,
          f"{scv.vmax:,.1f} kN")
    combos = {c.spec.key: c for c in res.combinations()}
    wc = combos["ultimate"].worst("My")
    sccb = V.build_scene(res, m, "combo_My")
    check("基本组合场 = 组合 worst(My)",
          near(sccb.vmax, abs(wc[1]) / 1e3, 1e-6),
          f"{sccb.vmax:,.1f} kN·m")
    check("组合控制构件 = worst 的单元", sccb.control[0] == wc[0][0],
          f"场景 {sccb.control[0]} / 组合 {wc[0][0]}")

    scw = V.build_scene(res, m, "w_min")
    check("位移按 mm 出", 10 < abs(scw.vmin) < 100,
          f"{scw.vmin:,.2f} mm")
    check("向下极值取到包络最小值",
          near(scw.vmin, min(lo for (_n, d), (lo, _hi) in res.disp.items() if d == 3) * 1000,
               1e-6))

    # 活载包络结构云图：与阶段包络场是两套数据，不能读成同一条曲线。
    scl = V.build_scene(res, m, "live_My")
    check("活载 My 云图有值且 0 居中",
          scl.has_values and scl.vmax > 0 and near(scl.vmin, -scl.vmax, 1e-12),
          f"[{scl.vmin:,.1f}, {scl.vmax:,.1f}] kN·m")
    check("活载 My 云图读数给 i/j 两端",
          all("i 端" in mm.hover and "j 端" in mm.hover
              for mm in scl.members if mm.value is not None))
    check("活载场与阶段包络场峰值不同（不是同一份数据）",
          not near(scl.vmax, scm.vmax, 1e-3),
          f"活载 {scl.vmax:,.1f} / 阶段 {scm.vmax:,.1f}")

    # ---------------------------------------------------------- 节点量
    print("\n[节点量：camber 的键是节点号，不是单元号]")
    sccam = V.build_scene(resc, mc, "camber")
    valued = [x for x in sccam.members if x.value is not None]
    check("预抬高有值（按单元号查会全空）", len(valued) == len(mc.elements),
          f"{len(valued)} / {len(mc.elements)}")
    check("预抬高最大值 175.9 mm",
          near(abs(sccam.vmax), 175.9, 5e-3), f"{sccam.vmax:,.1f} mm")
    check("预抬高读数标明是节点", "节点" in valued[0].hover, valued[0].hover[:40])

    scT = V.build_scene(resc, mc, "T")
    tv = [x for x in scT.members if x.value is not None]
    check("索力只落在索单元上", len(tv) == len(resc.cables), f"{len(tv)} 根")
    check("索力按 kN 出（内核给 N）",
          all(100 < x.value < 5000 for x in tv),
          f"{min(x.value for x in tv):,.0f} ~ {max(x.value for x in tv):,.0f} kN")
    t_of = {int(c["elem"]): float(c["T"]) for c in resc.cables}
    check("索力逐根对上内核",
          all(near(x.value, t_of[x.elem] / 1e3, 1e-6)
              for x in tv if x.elem in t_of))
    check("量值场从 0 起（不居中）", scT.vmin > 0, f"[{scT.vmin:,.0f}, {scT.vmax:,.0f}]")

    # ---------------------------------------------------------- 变形
    print("\n[变形：放大倍数与说明必须一致]")
    peak = V._w_peak(res)
    sug = V.deform_scale_suggest(res, m)
    sd = V.build_scene(res, m, "My", deform=True)
    nodes = {n.id: n for n in m.nodes}
    # 逐单元比：变形后的 z 与原始 z 之差 = 放大倍数 × 该节点的竖向位移
    ends = {(mem.elem, 0): mem.p0 for mem in sd.members}
    ends.update({(mem.elem, 1): mem.p1 for mem in sd.members})
    worst = 0.0
    for e in m.elements:
        for k, nid in ((0, e.n1), (1, e.n2)):
            p = ends.get((e.id, k))
            if p is None:
                continue
            w = res.disp.get((nid, 3))
            expect = (sd.deform_scale * (w[0] if abs(w[0]) >= abs(w[1]) else w[1])
                      if w else 0.0)
            worst = max(worst, abs((p[2] - nodes[nid].z) - expect))
    check("变形量 = 放大倍数 × 节点竖向位移", worst < 1e-9,
          f"最大偏差 {worst:.2e} m")
    check("建议倍数取整到 1/2/3/5 档", sug in (1, 2, 3, 5, 10, 20, 30, 50, 100,
                                              200, 300, 500, 1000), f"×{sug:,.0f}")
    check("说明里的倍数与场景一致",
          any(f"×{sd.deform_scale:,.0f}" in n for n in sd.notes),
          [n for n in sd.notes if "变形" in n][0][:50])
    check("说明里写出真实最大位移",
          any(f"{peak * 1000:.1f} mm" in n for n in sd.notes),
          f"{peak * 1000:.2f} mm")
    snd = V.build_scene(res, m, "My", deform=False)
    check("不勾变形时坐标不动",
          max(abs(mem.p0[2] - nodes[[e.n1 for e in m.elements if e.id == mem.elem][0]].z)
              for mem in snd.members) < 1e-12)
    check("变形不改着色值", near(sd.vmax, snd.vmax, 1e-12))

    # ---------------------------------------------------------- 比例
    print("\n[比例：压缩了就要说]")
    sa = V.build_scene(res, m, "My")
    check("自动比例：横向、竖向都放大到看得见",
          sa.aspect["y"] >= 0.16 and sa.aspect["z"] >= 0.10,
          str({k: round(v, 3) for k, v in sa.aspect.items()}))
    # 梁格模型所有节点 z=0（一片平面），"竖向放大"无从谈起；只提横向
    check("自动比例时写明横向被放大",
          any("横向已按最小比例放大" in n for n in sa.notes),
          sa.notes[0][:40])
    check("不把 0 尺寸的方向说成「已放大」",
          not any("竖向已" in n for n in sa.notes))
    st_ = V.build_scene(res, m, "My", true_proportion=True)
    ex, ey, ez = st_.bbox()
    # plotly 的 aspectratio 不接受 0（梁格的 z 极差为 0），故有一个 1e-3 下限
    check("真实比例：宽高比等于真实尺寸比",
          near(st_.aspect["y"] / st_.aspect["x"], ey / ex, 1e-9)
          and near(st_.aspect["z"] / st_.aspect["x"], max(ez / ex, 1e-3), 1e-9),
          str({k: round(v, 4) for k, v in st_.aspect.items()}))
    check("真实比例时改为陈述真实尺寸",
          any("按真实比例显示" in n for n in st_.notes), st_.notes[0][:40])
    scab = V.build_scene(resc, mc, "T")
    check("共面模型不提「横向放大」（y 方向没有尺寸）",
          all("横向" not in n for n in scab.notes),
          "；".join(n[:24] for n in scab.notes))

    # ---------------------------------------------------------- plotly
    print("\n[plotly：一个单元一个悬停点，一个色标]")
    fig = V.to_plotly(sd)
    lines = [t for t in fig.data if t.mode == "lines"]
    marks = [t for t in fig.data if t.mode == "markers"]
    check("线数 = 构件数（一个单元一条，不靠 None 断线）",
          len(lines) == len(sd.members), f"{len(lines)} 条")
    check("线不参与悬停", all(t.hoverinfo == "skip" for t in lines))
    # 只数**带色标**的标记点：支承点与控制点也是 marker，混进来会多出 9 个
    valued = [t for t in marks if getattr(t.marker, "colorscale", None) is not None]
    check("标记点数 = 构件数（每单元恰好一个读数点）",
          sum(len(t.x) for t in valued) == len(sd.members),
          f"{sum(len(t.x) for t in valued)} 点 / {len(sd.members)} 构件")
    check("色标只画一次",
          sum(1 for t in fig.data if getattr(t.marker, "showscale", False)) == 1)
    cb = [t for t in fig.data if getattr(t.marker, "showscale", False)][0]
    check("色标范围 = 场景范围",
          near(cb.marker.cmin, sd.vmin, 1e-12) and near(cb.marker.cmax, sd.vmax, 1e-12))
    check("三维比例用 manual（否则 plotly 会把长桥拉成方块）",
          fig.layout.scene.aspectmode == "manual")
    check("图无外链（plotly.js 随页面内联）",
          "cdn.plot.ly" not in fig.to_html(full_html=False, include_plotlyjs=False))

    # ---------------------------------------------------------- HTML 出口
    print("\n[HTML 出口：单文件、离线可开]")
    p = V.write_html(sd, "out/verify_view_3d/view.html", title="测试视图")
    html = p.read_text(encoding="utf-8")
    check("文件已写出且大于 1 MB（内联渲染引擎）",
          p.exists() and p.stat().st_size > 1_000_000,
          f"{p.stat().st_size / 1024 / 1024:.1f} MB")
    check("无外链脚本（离线环境打不开 CDN）", "<script src" not in html)
    # plotly 把中文写成 \uXXXX，直接找"控制构件"会假失败
    import json as _json
    esc = lambda t: _json.dumps(t)[1:-1]        # noqa: E731
    check("含场景数据（中文以 \\u 转义写入）",
          esc("控制构件") in html and esc("顺桥向") in html
          and html.lower().count('"type":"scatter3d"') >= len(sd.members),
          f"trace 数 {html.lower().count(chr(34) + 'type' + chr(34) + ':' + chr(34) + 'scatter3d' + chr(34))}")
    check("标题与说明写进页面", "测试视图" in html and "放大" in html)

    # ---------------------------------------------------------- 轴测 PNG
    print("\n[轴测图：报告里那张]")
    ap = V.figure_axon(sd, "out/verify_view_3d/axon.png", title="轴测")
    check("PNG 已写出且不是空白", ap.exists() and ap.stat().st_size > 20000,
          f"{ap.stat().st_size // 1024} KB")

    # ---------------------------------------------------------- 空结果
    print("\n[空结果：不崩、不留假色标]")
    empty = SolveResult(kind="grillage3d", title="空")
    fe = V.fields(empty, m)
    check("空结果只剩几何场", [f.key for f in fe] == ["geometry"], "、".join(f.key for f in fe))
    se = V.build_scene(empty, m, "geometry")
    check("空结果仍能建场景（节点/单元照画）", len(se.members) == len(m.elements),
          f"{len(se.members)} 构件")
    check("空结果 has_values=False", not se.has_values)
    check("空结果色标区间不为零宽（否则 plotly 报错）", se.vmax > se.vmin)
    sf = V.build_scene(empty, m, "不存在的场")
    check("场名不认识就退到第一个场", sf.field.key == "geometry")

    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(PASS)} 通过")
    if n_pass != len(PASS):
        print("  失败项：" + "、".join(n for n, ok, _ in PASS if not ok))
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
