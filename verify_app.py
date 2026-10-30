"""界面回归测试（Streamlit AppTest，无头运行）。

C 方案把①的梁与③的桥合并成一个入口，最容易出现的回归不是崩溃，而是
"看起来在、其实数据没换"——所以断言要落在 session_state 的 doc.kind 上，
而不是只断言"页面没报错"。

接入三维之后又多一条：三维区块是**同一条管线的另一个分支**，回归必须证明
"二维区块不出现、三维区块出现、结果进的是同一个 SolveResult"。
"""

from __future__ import annotations

import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<34} {detail}")


def _errors(at: AppTest) -> list[str]:
    return [f"{e.value}" for e in at.exception] if at.exception else []


def _subs(at: AppTest) -> str:
    return " ".join(s.value for s in at.subheader)


def main() -> int:
    print("=" * 78)
    print("界面回归（AppTest）")
    print("=" * 78)

    at = AppTest.from_file("app.py", default_timeout=180).run()

    print("\n[首次加载]")
    check("无异常", not _errors(at), _errors(at)[:1])
    _marks = "①②③④⑤"
    check("区块①②③④⑤齐全",
          all(any(m in s for s in [s.value for s in at.subheader]) for m in _marks),
          _subs(at)[:60])
    check("默认模型是单梁", at.session_state["doc"].kind == "beam",
          at.session_state["doc"].summary()["title"])
    check("默认不显示三维区块", "模型与求解" not in _subs(at))

    print("\n[点示例：拱桥]")
    at.button(key="ex3").click().run()
    check("无异常", not _errors(at), _errors(at)[:1])
    check("类型切换为 arch", at.session_state["doc"].kind == "arch",
          at.session_state["doc"].summary()["lines"][0][:36])
    _info = " ".join(str(i.value) for i in at.info)
    check("④提示走FEM", "FEM-Eigen" in _info, "桥型无草算")

    print("\n[改参数：吊杆数 9 → 15]")
    _doc = at.session_state["doc"]
    _n0 = len(_doc.model.nodes)
    at.number_input(key=f"parch{at.session_state['doc_version']}_n_hanger").set_value(15).run()
    check("无异常", not _errors(at), _errors(at)[:1])
    _n1 = len(at.session_state["doc"].model.nodes)
    check("拓扑按新吊杆数重建", _n1 > _n0, f"节点 {_n0} → {_n1}")

    print("\n[切到桁架：人工指定类型]")
    at.selectbox(key="kind_hint").select("桁架桥").run()
    at.text_area(key="nl_input").set_value("跨度80米桁架桥，10个节间，桁高7米").run()
    next(b for b in at.button if b.label == "解析并更新模型").click().run()
    check("无异常", not _errors(at), _errors(at)[:1])
    check("类型为 truss", at.session_state["doc"].kind == "truss",
          at.session_state["doc"].summary()["lines"][0][:36])
    check("跨度识别为 80", abs(at.session_state["doc"].params["span"] - 80) < 1e-6,
          f"span={at.session_state['doc'].params['span']}")

    print("\n[切回单梁：荷载编辑不串值]")
    at.selectbox(key="kind_hint").select("自动识别").run()
    at.text_area(key="nl_input").set_value(
        "一座跨度30米的简支钢梁，矩形截面高1.2米宽0.6米，承受50kN/m均布荷载").run()
    next(b for b in at.button if b.label == "解析并更新模型").click().run()
    check("无异常", not _errors(at), _errors(at)[:1])
    check("类型回到 beam", at.session_state["doc"].kind == "beam", "")
    check("荷载值 50 未被污染",
          abs(at.session_state["doc"].params["loads"][0]["value"] - 50.0) < 1e-6,
          f"value={at.session_state['doc'].params['loads'][0]['value']}")
    check("⑤二维求解按钮存在", bool([b for b in at.button if "FEM-Eigen" in b.label]))

    # ============================================================ 三维
    print("\n[三维 · 梁格]")
    at.button(key="ex5").click().run()
    check("无异常", not _errors(at), _errors(at)[:1])
    check("类型为 grillage3d", at.session_state["doc"].kind == "grillage3d", _subs(at)[:50])
    check("出现三维区块", "模型与求解 · 三维梁格" in _subs(at), _subs(at)[:80])
    check("二维区块⑤不再出现", "FEM-Eigen 二维求解" not in _subs(at))

    _btn = next((b for b in at.button if b.key == "r3d_solve"), None)
    check("三维求解按钮存在", _btn is not None)
    if _btn is not None:
        _btn.click().run()
        check("求解无异常", not _errors(at), _errors(at)[:1])
        _r = at.session_state.get("r3d_result")
        check("结果是 SolveResult", type(_r).__name__ == "SolveResult",
              type(_r).__name__)
        if _r is not None:
            check("内核为梁格", _r.solver == "example_3d_grillage.exe", _r.solver)
            check("222 单元 / 3 阶段",
                  _r.stats.get("elements") == 222 and _r.stats.get("stages") == 3,
                  f"{_r.stats.get('elements')} / {_r.stats.get('stages')}")
            check("活载包络 3 阶段", len(_r.live) == 3, str(sorted(_r.live)))
            _mvd = _r.max_vertical_deflection()
            check("中跨跨中挠度合理", bool(_mvd) and -0.03 < _mvd[1] < -0.001,
                  f"{_mvd[1] * 1000:.2f} mm（节点 {_mvd[0]}）" if _mvd else "—")
            # 这条守的是"summary 只输出 Mz"那一版真 bug
            _mb = _r.max_bending()
            check("竖向弯矩 My 非零", bool(_mb) and _mb[1] > 1e6,
                  f"{_mb[1] / 1e3:.1f} kN·m（单元 {_mb[0]}）" if _mb else "—")
            _bad = [c.name for c in _r.checks(at.session_state["doc"].model)
                    if not c.passed]
            check("工程校核全通过", not _bad, "、".join(_bad))
            check("明细表含梁内力包络", "beam_envelope" in _r.tables(
                at.session_state["doc"].model))

            # ---- 荷载组合块：数据在、界面在、数字同源 ----
            _cb = _r.combinations()
            check("组合可用且含活载项",
                  len(_cb) == 3 and all(c.has_live for c in _cb),
                  f"{len(_cb)} 组 / has_live={_cb[0].has_live if _cb else '—'}")
            _w = _cb[0].worst("My") if _cb else None
            check("基本组合控制弯矩为负（跨中下缘受拉）",
                  _w is not None and _w[1] < 0,
                  f"{_w[1] / 1e3:,.0f} kN·m（单元 {_w[0][0]}·{_w[0][1]} 端）"
                  if _w else "—")
            _md = " ".join(str(m.value) for m in at.markdown)
            check("界面出现荷载组合块",
                  "荷载组合（JTG D60-2015）" in _md, _md[:40])
            _labs = [m.label for m in at.metric]
            check("指标卡含基本组合控制弯矩", "基本组合控制弯矩" in _labs,
                  "、".join(_labs))
            # 组合表已经单独成块，明细表页签里不该再重复一遍
            _tb = _r.tables(at.session_state["doc"].model)
            check("组合表齐全（系数/汇总/逐截面）",
                  {"combo_coef", "combo_summary", "combo_ultimate"} <= set(_tb),
                  "、".join(k for k in _tb if k.startswith("combo")))

            # ---- 三维交互视图：控件在、图在、场景与结果同源 ----
            _fp = _r.fingerprint
            _fld = next((s for s in at.selectbox
                         if s.key == f"v3d_field_{_fp}"), None)
            check("界面出现三维交互视图", "三维交互视图" in _md)
            check("着色场选择器存在", _fld is not None,
                  "、".join(str(x) for x in (_fld.options if _fld else [])))
            check("梁格给 6 个场（几何/位移两向/My/Vz/组合）",
                  _fld is not None and len(_fld.options) == 6,
                  f"{len(_fld.options) if _fld else 0} 个")
            check("变形开关存在",
                  any(c.key == f"v3d_deform_{_fp}" for c in at.checkbox))
            _pc = at.get("plotly_chart")
            check("三维图已渲染", len(_pc) == 1, f"{len(_pc)} 张")
            _sc = at.session_state.get("v3d_scene")
            check("场景已缓存且与结果同源",
                  bool(_sc) and len(_sc[1].members) == _r.stats.get("elements"),
                  f"{len(_sc[1].members)} 构件" if _sc else "—")

            if _fld is not None:
                _fld.select("combo_My").run()
                check("切换着色场无异常", not _errors(at), _errors(at)[:1])
                _sc2 = at.session_state.get("v3d_scene")
                check("切到基本组合后场景换场",
                      bool(_sc2) and _sc2[1].field.key == "combo_My",
                      _sc2[1].field.key if _sc2 else "—")
                _cw = _cb[0].worst("My") if _cb else None
                check("切场后色标与组合值一致",
                      bool(_sc2) and _cw is not None
                      and abs(_sc2[1].vmax - abs(_cw[1]) / 1e3) < 1e-6,
                      f"{_sc2[1].vmax:,.1f} kN·m" if _sc2 else "—")
                check("说明里写明放大倍数（不藏参数）",
                      any("放大绘制" in n for n in _sc2[1].notes),
                      "；".join(n[:18] for n in _sc2[1].notes[:2]) if _sc2 else "—")

            # 可旋转视图单独发出去的那份 html
            _hb = next((b for b in at.button if b.key == f"v3d_html_{_fp}"), None)
            check("生成可旋转视图按钮存在", _hb is not None)
            if _hb is not None:
                _hb.click().run()
                _hp = at.session_state.get("v3d_html_path")
                check("可旋转视图已落盘且内联引擎",
                      bool(_hp) and Path(_hp).exists()
                      and Path(_hp).stat().st_size > 1_000_000,
                      f"{Path(_hp).stat().st_size / 1024 / 1024:.1f} MB"
                      if _hp and Path(_hp).exists() else "—")

        # 报告：三出口一次落盘（缺任一份界面上就是死按钮）
        _gb = next((b for b in at.button if b.key == "r3d_report_btn"), None)
        check("报告按钮存在（梁格）", _gb is not None)
        if _gb is not None:
            _gb.click().run()
            check("生成报告无异常", not _errors(at), _errors(at)[:1])
            _got = at.session_state.get("r3d_report")
            _paths = (_got or {}).get("paths", {})
            _ok = bool(_paths) and all(Path(p).exists() and Path(p).stat().st_size > 2000
                                       for p in _paths.values())
            check("梁格报告三份已落盘", _ok,
                  "、".join(f"{Path(p).name}" for p in _paths.values()) or "—")
            if _ok:
                import zipfile as _zip
                from docx import Document as _Doc
                _d = _Doc(str(_paths["docx"]))
                _h = [x.text for x in _d.paragraphs if x.style.name == "Heading 1"]
                check("梁格报告章节齐全", len(_h) >= 5,
                      f"{len(_h)} 章：" + "、".join(_h))
                _html = Path(_paths["html"]).read_text(encoding="utf-8")
                check("HTML 图片内嵌（单文件可发）",
                      "data:image/png;base64" in _html
                      and 'src="http' not in _html)
                _zn = set(_zip.ZipFile(_paths["zip"]).namelist())
                check("CSV 包含指标与校核",
                      {"00_指标.csv", "01_校核.csv"} <= _zn, str(sorted(_zn))[:80])

        print("\n[梁格：改参数后结果过期]")
        _ver = at.session_state["doc_version"]
        at.number_input(key=f"pgrillage3d{_ver}_sp2").set_value(60.0).run()
        check("无异常", not _errors(at), _errors(at)[:1])
        _warn = " ".join(str(w.value) for w in at.warning)
        check("提示结果已过期并隐藏", "已修改" in _warn or "上一次求解" in _warn,
              _warn[:50] or "无警告")

    print("\n[三维 · 斜拉桥]")
    at.button(key="ex6").click().run()
    check("无异常", not _errors(at), _errors(at)[:1])
    check("类型为 cable_stayed", at.session_state["doc"].kind == "cable_stayed",
          _subs(at)[:50])
    check("出现斜拉桥区块", "模型与求解 · 斜拉桥成桥态" in _subs(at), _subs(at)[:80])

    _btn = next((b for b in at.button if b.key == "r3d_solve"), None)
    check("斜拉桥求解按钮存在", _btn is not None)
    if _btn is not None:
        _btn.click().run()
        check("求解无异常", not _errors(at), _errors(at)[:1])
        _r = at.session_state.get("r3d_result")
        check("结果是 SolveResult 且 kind 正确",
              _r is not None and _r.kind == "cable_stayed",
              getattr(_r, "kind", "—"))
        if _r is not None:
            _md = _r.meta
            check("收敛", bool(_r.converged),
                  f"{_r.iterations} 轮 / {_r.elapsed_ms:.0f} ms")
            check("闭合误差 < 1e-6 m", _md.get("closure_err", 1) < 1e-6,
                  f"{_md.get('closure_err'):.1e}")
            check("索数 = 48", len(_r.cables) == 48, f"{len(_r.cables)}")
            check("索承担桥面恒载 > 50%", _md.get("deck_share_pct", 0) > 50.0,
                  f"{_md.get('deck_share_pct'):.1f}%")
            _bad = [c.name for c in _r.checks(at.session_state["doc"].model)
                    if not c.passed]
            check("工程校核全通过", not _bad, "、".join(_bad))
            _cam = [n for n in _r.camber if 1000 < n < 2000]
            check("预抬高含梁节点", len(_cam) > 40, f"{len(_cam)} 个节点")
            check("明细表含索表与制造线形",
                  {"cable_table", "camber_table"} <= set(_r.tables(
                      at.session_state["doc"].model)),
                  str(list(_r.tables(at.session_state["doc"].model))))

            # 换模型后控件 key 变（带指纹），旧的"基本组合"不会越界
            # 注意 options 里是**格式化后的标签**，不是 key
            _fld = next((s for s in at.selectbox
                         if s.key == f"v3d_field_{_r.fingerprint}"), None)
            _want = {f.label for f in __import__("view_3d").fields(
                _r, at.session_state["doc"].model)}
            check("斜拉桥也出交互视图且场随结果换",
                  _fld is not None
                  and {str(x) for x in _fld.options} == _want,
                  "、".join(str(x) for x in (_fld.options if _fld else [])))
            check("斜拉桥不提供梁内力场（成桥态内核不出）",
                  _fld is not None and not any(
                      "My" in str(x) for x in _fld.options),
                  str(len(_fld.options)) + " 个场" if _fld else "—")
            _sc3 = at.session_state.get("v3d_scene")
            check("斜拉桥场景仍可建（136 构件）",
                  bool(_sc3) and len(_sc3[1].members) == 136,
                  f"{len(_sc3[1].members)} 构件" if _sc3 else "—")

        # 计算书：点按钮 → 落盘 → 可下载（缺任一步界面上就是个死按钮）
        _rb = next((b for b in at.button if b.key == "cb_report_btn"), None)
        check("计算书按钮存在", _rb is not None)
        if _rb is not None:
            _rb.click().run()
            _rp = at.session_state.get("cb_report_path")
            check("计算书已生成", bool(_rp) and Path(_rp).exists(), str(_rp or "—"))
            if _rp and Path(_rp).exists():
                from docx import Document as _Doc

                _doc = _Doc(str(_rp))
                _heads = [p.text for p in _doc.paragraphs
                          if p.style.name == "Heading 1"]
                check("计算书章节齐全", len(_heads) >= 7,
                      f"{len(_heads)} 章：" + "、".join(h[:6] for h in _heads))
                check("计算书含关键表", len(_doc.tables) >= 10,
                      f"{len(_doc.tables)} 张表")

        # 三方复核：点按钮 → 三份文件落盘
        _eb = next((b for b in at.button if b.key == "cb_export_btn"), None)
        check("三方复核按钮存在", _eb is not None)
        if _eb is not None:
            _eb.click().run()
            _ex = at.session_state.get("cb_export")
            _paths = (_ex or {}).get("paths", {})
            _ok = bool(_paths) and all(Path(p).exists() and Path(p).stat().st_size > 500
                                       for p in _paths.values())
            check("ANSYS/Abaqus/MIDAS 三份已落盘", _ok,
                  "、".join(Path(p).name for p in _paths.values()) or "—")
            if _ok:
                _ans = Path(_paths["ansys"]).read_text(encoding="utf-8")
                _inp = Path(_paths["abaqus"]).read_text(encoding="utf-8")
                check("ANSYS 有单元且有 FILL 压缩",
                      _ans.count("\nEN,") > 100 and "FILL," in _ans,
                      f"{_ans.count(chr(10) + 'EN,')} 个单元")
                check("Abaqus 截面带方向向量", "0., 0., -1." in _inp)
                check("导出无告警堆积", len((_ex or {}).get("warnings", [])) <= 2,
                      f"{len((_ex or {}).get('warnings', []))} 条")

    print("\n[从三维切回单梁：两条链路不串味]")
    at.button(key="ex0").click().run()
    check("无异常", not _errors(at), _errors(at)[:1])
    check("类型回到 beam", at.session_state["doc"].kind == "beam", _subs(at)[:50])
    check("⑤二维区块回来了", "FEM-Eigen 二维求解" in _subs(at), _subs(at)[:80])
    check("三维区块消失", "模型与求解" not in _subs(at))

    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(PASS)} 通过")
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
