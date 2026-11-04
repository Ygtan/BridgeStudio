"""ui_3d.py —— 三维区块的界面（⑥）。

为什么从 app.py 里搬出来
------------------------
改造前 ⑥ 梁格 与 ⑦ 斜拉桥 是两段各 150 行的手写控件：两套 exe 名、两套
session_state 键、两套"参数变了作废结果"的判断，第四张图还得再抄一遍。
结果是"加一个内核"= 复制 150 行 + 改 20 处字符串。

接入之后这里只剩三件事：

    参数由 doc.param_rows() 给 → 求解由 doc.solve() 做 → 展示由 SolveResult 出

界面不再知道内核叫什么、exe 在哪、输出文件分几段。换内核只改 solver_3d.py。

图不再由界面自己画：`report_3d.figures()` 出 PNG，界面只负责摆。这样
"屏幕上看到的图"与"报告里的图"是同一个文件，不是各画一张再对齐。

导出也是一处：`report_3d` 的 Word / HTML / CSV 三个出口吃同一份 `Report`。
斜拉桥另保留"详细计算书（7 章带公式推导）"与"三方格式导出"两个专属按钮，
因为下料索长与制造线形是 cable 独有的量。
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

import streamlit as st

from model_doc import KIND_LABELS, ModelDoc
from report_3d import build_document, figures as build_figures, write_all
from result_3d import SolveResult

#: 结果缓存键（带模型指纹，避免"换模型后显示上一个模型的结果"）
SS_RESULT = "r3d_result"
SS_FIGURES = "r3d_figures"
SS_REPORT = "r3d_report"


# ---------------------------------------------------------------- 小工具


def _csv_bytes(headers: list[str], rows: list[list[Any]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    w.writerows(rows)
    # Excel 需要 BOM 才认 UTF-8
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def _checks_block(res: SolveResult, model: Any) -> None:
    """校核清单：一条一行，不通过的红字。"""
    checks = res.checks(model)
    bad = [c for c in checks if not c.passed]
    if not checks:
        st.caption("本次求解没有产出可判定的校核项。")
        return
    head = f"工程校核：{len(checks) - len(bad)}/{len(checks)} 通过"
    (st.error if bad else st.success)(head)
    for c in checks:
        icon = "✅" if c.passed else ("⚠️" if c.level == "warn" else "❌")
        st.markdown(f"{icon} **{c.name}** — {c.detail}")


def _tables_block(res: SolveResult, model: Any, skip: tuple[str, ...] = ()) -> None:
    """明细表：SolveResult.tables() 出数据，这里只负责摆。

    `skip` 用来让已在别处展示过的表不重复出现（组合表在组合块里给）。
    """
    tabs = {k: v for k, v in res.tables(model).items() if k not in skip}
    if not tabs:
        st.caption("本次结果没有可导出的明细表。")
        return
    names = list(tabs)
    _t = st.tabs([tabs[n]["title"] for n in names])
    for _tab, n in zip(_t, names):
        tb = tabs[n]
        with _tab:
            st.dataframe(
                [dict(zip(tb["headers"], r)) for r in tb["rows"]],
                use_container_width=True, hide_index=True)
            if tb.get("note"):
                st.caption(tb["note"])
            st.download_button(
                f"下载 {tb['title']} (.csv)",
                data=_csv_bytes(tb["headers"], tb["rows"]),
                file_name=f"{n}.csv", mime="text/csv", key=f"dl_{n}")


def _combination_block(res: SolveResult, model: Any) -> None:
    """荷载组合块：三种组合的控制值 + 系数出处 + 逐截面明细。

    数据直接取自 `res.tables()` 里那三张表——与 Word/HTML 报告同一份 dict，
    界面与报告不会各算一遍。呈现上分三层：控制值（一屏看完）、系数（复核
    依据）、逐截面（配筋用，收进展开器）。
    """
    tabs = res.tables(model)
    summ = tabs.get("combo_summary")
    if summ is None:
        return
    st.markdown("**荷载组合（JTG D60-2015）**")
    combos = res.combinations()
    if combos and not combos[0].has_live:
        st.warning("模型未定义车道荷载（`*LANE_3D`），下列组合值只有恒载项乘"
                   "系数，**不是组合值**——补齐车道定义前不得用于设计。")
    st.dataframe([dict(zip(summ["headers"], r)) for r in summ["rows"]],
                 use_container_width=True, hide_index=True)
    st.caption(summ["note"])
    st.caption("恒载项取成桥态单值（各阶段累加，非施工阶段包络），"
               "组合按单元 × i/j 端进行："
               "阶段包络含未浇筑悬臂的临时内力，与运营期活载不属同一构型；"
               "两端弯矩异号，先外包再相加会把一端恒载配另一端活载。")
    with st.expander("组合系数与依据 / 基本组合逐截面结果"):
        coef = tabs.get("combo_coef")
        if coef:
            st.dataframe([dict(zip(coef["headers"], r)) for r in coef["rows"]],
                         use_container_width=True, hide_index=True)
            st.caption(coef["note"])
        ult = tabs.get("combo_ultimate")
        if ult:
            st.markdown(f"**{ult['title']}**")
            st.dataframe([dict(zip(ult["headers"], r)) for r in ult["rows"]],
                         use_container_width=True, hide_index=True)
            st.caption(ult["note"])
            st.download_button(
                "下载基本组合逐截面 (.csv)",
                data=_csv_bytes(ult["headers"], ult["rows"]),
                file_name="combo_ultimate.csv", mime="text/csv",
                key="dl_combo_ultimate")


# ---------------------------------------------------------------- 图


def _figures_block(doc: ModelDoc, res: SolveResult) -> None:
    """图来自 report_3d（与 Word / HTML 报告同一批 PNG）。

    按结果指纹缓存：Streamlit 每次交互都会重跑脚本，重新生成 5 张 matplotlib
    图要 1 s 左右，而结果没变时图也不会变。
    """
    key = (res.fingerprint, res.kind)
    cached = st.session_state.get(SS_FIGURES)
    if not cached or cached[0] != key:
        figs = build_figures(res, doc.model, Path("out") / doc.kind / "report")
        st.session_state[SS_FIGURES] = (key, figs)
        cached = st.session_state[SS_FIGURES]
    figs = cached[1]
    if not figs:
        st.caption("本次结果没有可绘制的曲线（内核未输出位移或内力）。")
        return
    for f in figs.values():
        st.image(str(f.path), use_container_width=True)
        st.caption(f"**{f.caption}**" + (f"　{f.note}" if f.note else ""))


# ---------------------------------------------------------------- 三维交互视图


#: 变形放大倍数的可选档位。用离散档位而不是 1–2000 的连续滑杆：连续滑杆上
#: "×317" 这种数字没有意义，也复现不了（换个浏览器就是别的值）。
_SCALES = (1, 2, 5, 10, 20, 50, 100, 200, 300, 500, 1000)


def _scene_block(doc: ModelDoc, res: SolveResult) -> None:
    """可旋转的三维视图（plotly）。几何与着色场由 view_3d 装配，这里只摆控件。

    为什么界面上要这个、报告里却是 PNG：屏幕上能转是"看形态"的刚需——梁格
    的横向分配、斜拉桥两排索的空间关系，一个固定视角讲不清楚；而 Word 里
    印不出交互，只能用同一份场景投影一张轴测图。两边同源，不各画一套。
    """
    import view_3d as V

    try:                                   # plotly 只是这一块的依赖
        import plotly.graph_objects  # noqa: F401
    except ImportError:
        st.info("未安装 plotly，三维交互视图不可用（`pip install plotly`）。"
                "指标、校核、组合、图、表、报告都不受影响。")
        return

    fs = V.fields(res, doc.model)
    if not fs:
        return
    labels = {f.key: f.label for f in fs}
    st.markdown("**三维交互视图**")
    st.caption("拖动旋转、滚轮缩放、悬停读每个单元的值。"
               "同一份几何在报告里印成轴测图（图 2）。")

    # 控件 key 带结果指纹：换模型后旧的选择（比如"成桥索力"）在新模型里
    # 不存在，selectbox 会抛 "value not in options"。
    fp = res.fingerprint
    c1, c2, c3, c4 = st.columns([2.4, 1.0, 1.1, 1.5])
    fk = c1.selectbox("着色场", [f.key for f in fs],
                      format_func=lambda k: labels[k], key=f"v3d_field_{fp}")
    deform = c2.checkbox("显示变形", value=True, key=f"v3d_deform_{fp}",
                         help="按放大倍数绘制挠曲形态；放大倍数会写在图注里")
    prop = c3.selectbox("比例", ["自动压缩", "真实比例"], key=f"v3d_prop_{fp}",
                        help="自动压缩把横向/竖向放大到看得见的比例；"
                             "真实比例横纵一致，长桥会是一条线")
    suggest = V.deform_scale_suggest(res, doc.model)
    scale = suggest
    if deform:
        opts = list(_SCALES)
        if suggest not in opts:                    # 建议值不在档位里就补进去
            opts = sorted(opts + [suggest])
        scale = c4.select_slider("变形放大 ×", options=opts, value=suggest,
                                 key=f"v3d_scale_{fp}_{fk}")

    cache_key = (fp, fk, bool(deform), float(scale), prop)
    got = st.session_state.get("v3d_scene")
    if not got or got[0] != cache_key:
        scene = V.build_scene(res, doc.model, fk, deform=deform, scale=scale,
                             true_proportion=(prop == "真实比例"))
        fig = V.to_plotly(scene, height=560)
        st.session_state["v3d_scene"] = (cache_key, scene, fig)
        got = st.session_state["v3d_scene"]
    scene, fig = got[1], got[2]

    st.plotly_chart(fig, width="stretch", key=f"v3d_chart_{fp}")
    for n in scene.notes:
        st.caption(f"· {n.replace('**', '')}")

    # 单独发出去的 html：审图的人不一定装了这个程序，截图只能看一个角度。
    c5, c6 = st.columns([1.2, 2.4])
    if c5.button("生成可旋转视图 (.html)", key=f"v3d_html_{fp}",
                 help="内联 plotly.js 的单文件，约 4 MB，双击即可打开"):
        try:
            p = V.write_html(scene, Path("out") / doc.kind / "report" / "view_3d.html",
                             title=f"{res.title or doc.kind_label} · {labels[fk]}")
            st.session_state["v3d_html_path"] = str(p)
        except Exception as e:                     # noqa: BLE001
            st.session_state.pop("v3d_html_path", None)
            st.error(f"生成失败：{e}")
    _hp = st.session_state.get("v3d_html_path")
    if _hp and Path(_hp).exists():
        c6.download_button(
            "下载三维视图 (.html)", data=Path(_hp).read_bytes(),
            file_name="view_3d.html", mime="text/html", key="v3d_dl_html")
        c6.caption(f"{Path(_hp).stat().st_size / 1024 / 1024:.1f} MB，"
                   "已内联渲染引擎，离线可开。")
    else:
        c6.caption("导出的是**当前这个着色场与视角参数**下的场景。")


# ---------------------------------------------------------------- MIDAS 互导


def _interop_block(doc: ModelDoc) -> None:
    """MIDAS ``.mct`` 双向互导：导出当前模型 / 导入别人的模型试算。

    为什么不把导入的模型直接塞回 ``doc``：``ModelDoc`` 是"参数 → 展开模型"
    的产物，而 .mct 里没有"跨径布置"这种参数，只有节点和单元。硬塞进去会让
    ``doc.params`` 与实际模型对不上（改一个参数重算，模型就变回原样）。
    所以导入的结果走独立的"试算"面板：能算、能看、能导出 .dat，但不冒充
    参数化模型。
    """
    import mct_io

    st.markdown("**与 MIDAS Civil 互导（.mct）**")
    c1, c2 = st.columns([1.0, 1.7])

    # ---- 导出 ----
    if c1.button("导出当前模型 (.mct)", key="mi_export_btn",
                 help="节点、单元、材料、截面、约束与荷载工况；车道荷载与施工阶段"
                      "不在 MCT 的范围内，会逐条列出"):
        try:
            p = Path("out") / doc.kind / "export" / f"{doc.kind}.mct"
            warns = mct_io.write_mct(doc.model, p)
            st.session_state["mi_export"] = {"path": str(p), "warnings": warns}
        except Exception as e:                      # noqa: BLE001
            st.session_state.pop("mi_export", None)
            c1.error(f"导出失败：{e}")
    got = st.session_state.get("mi_export")
    if got and Path(got["path"]).exists():
        _p = Path(got["path"])
        c1.download_button("下载 .mct", data=_p.read_bytes(), file_name=_p.name,
                           mime="text/plain", key="mi_dl")
        with c1.expander(f"导出说明（{len(got['warnings'])} 条，含丢失项）"):
            for w in got["warnings"]:
                st.caption(f"· {w}")
    else:
        c1.caption("MCT 是单工况模型：施工阶段会被合并为一条荷载工况。")

    # ---- 导入 ----
    up = c2.file_uploader("导入 MIDAS .mct（只读，不覆盖当前模型）",
                          type=["mct", "txt"], key="mi_upload")
    if up is None:
        c2.caption("导入后可看「读进来什么、丢了什么」，并直接写出 FEM-Eigen "
                   ".dat 或就地试算一次。")
        return

    imp = mct_io.read_mct(up.getvalue(), title=up.name.rsplit(".", 1)[0])
    sm = imp.summary()
    c2.caption(f"{sm['nodes']} 节点 / {sm['elements']} 单元 / "
               f"{sm['materials']} 材料 / {sm['reals']} 截面 / "
               f"{sm['boundaries']} 约束 / {sm['loads']} 节点力　"
               f"｜　单位 {sm['unit'][0]}, {sm['unit'][1]}　"
               f"｜　竖向合力 {sm['load_sum_z'] / 1e3:,.1f} kN")

    for e in imp.errors[:5]:
        c2.error(e)
    if len(imp.errors) > 5:
        c2.caption(f"…另有 {len(imp.errors) - 5} 条错误")
    if imp.notes:
        for x in imp.notes:
            c2.info(x)
    if imp.warnings:
        with c2.expander(f"告警（{len(imp.warnings)} 条：能算，但与原模型不完全一致）"):
            for w in imp.warnings:
                st.caption(f"· {w}")
    if imp.losses:
        with c2.expander(f"带不走的（{len(imp.losses)} 条：文件里有，本程序没有）"):
            for x in imp.losses:
                st.caption(f"· {x}")

    if not imp.ok:
        return

    _errs, _warns = imp.lint()
    if _errs:
        c2.error("模型校验不通过：" + "；".join(_errs[:3]))
        return
    if _warns:
        with c2.expander(f"模型校验提示（{len(_warns)} 条）"):
            for w in _warns:
                st.caption(f"· {w}")

    _c1, _c2 = c2.columns(2)
    _c1.download_button("写出 FEM-Eigen .dat", data=imp.model.to_dat().encode("utf-8"),
                        file_name=f"{up.name.rsplit('.', 1)[0]}.dat",
                        mime="text/plain", key="mi_dl_dat")
    if _c2.button("用本内核试算（恒载静力）", key="mi_solve_btn"):
        from solver_3d import solve as _solve

        with st.spinner("导入模型 → 导出 .dat → 调用内核 …"):
            try:
                st.session_state["mi_result"] = _solve(
                    imp.model, kind="grillage3d", out_dir="out/interop")
            except Exception as e:                  # noqa: BLE001
                st.session_state.pop("mi_result", None)
                c2.error(f"试算失败：{e}")
    _r = st.session_state.get("mi_result")
    if _r is not None and _r.final_beam and _r.final_disp:
        _mymax = max(abs(v.get("My", 0.0)) for v in _r.final_beam.values())
        _dz = min(v for (_n, d), v in _r.final_disp.items() if d == 3) if any(
            d == 3 for (_n, d) in _r.final_disp) else 0.0
        _k = c2.columns(3)
        _k[0].metric("控制弯矩 |My|", f"{_mymax / 1e3:,.0f} kN·m")
        _k[1].metric("成桥挠度", f"{_dz * 1000:.2f} mm")
        _k[2].metric("自由度", str(_r.stats.get("dofs", "—")))
        c2.caption("试算只算恒载（车道荷载不在 MCT 里），结果不参与本页的校核与组合。")


# ---------------------------------------------------------------- 报告导出


def _report_block(doc: ModelDoc, res: SolveResult) -> None:
    """Word / HTML / CSV 三出口，同一份 Report。"""
    st.markdown("**报告与数据导出**")
    c1, c2 = st.columns([1.1, 2.2])
    if c1.button("生成报告", key="r3d_report_btn",
                 help="同一份内容同时产出 Word、HTML 与 CSV 数据包"):
        with st.spinner("排版报告（表格、插图、结论）…"):
            try:
                rep = build_document(res, doc.model, project=res.title,
                                     out_dir=Path("out") / doc.kind / "report",
                                     kind_label=KIND_LABELS.get(doc.kind))
                paths = write_all(rep, Path("out") / doc.kind / "report",
                                  stem=f"{doc.kind}_report")
                st.session_state[SS_REPORT] = {
                    "fingerprint": res.fingerprint, "paths": paths,
                    "n_blocks": len(rep.blocks)}
            except Exception as e:                  # noqa: BLE001
                st.session_state.pop(SS_REPORT, None)
                st.error(f"报告生成失败：{e}")

    got = st.session_state.get(SS_REPORT)
    if not got:
        c2.caption("报告含：计算概要、工程校核、结果图、结论、明细表。"
                   "数值与界面逐项同源（同一个 SolveResult）。")
        return
    if got["fingerprint"] != res.fingerprint:
        c2.warning("报告是上一次求解的结果，模型已变，请重新生成。")
        return
    names = {"docx": ("Word (.docx)", "application/vnd.openxmlformats-"
                                      "officedocument.wordprocessingml.document"),
             "html": ("HTML (.html)", "text/html"),
             "zip": ("数据表 (.zip)", "application/zip")}
    cols = c2.columns(3)
    for i, (fmt, (label, mime)) in enumerate(names.items()):
        p = Path(got["paths"][fmt])
        if p.exists():
            cols[i].download_button(label, data=p.read_bytes(), file_name=p.name,
                                    mime=mime, key=f"r3d_dl_{fmt}")


# ------------------------------------------------------------ 索承体系共用出口


def _export_block(doc: ModelDoc, res: SolveResult, *, out_dir: str,
                  stem: str, key: str) -> None:
    """三方复核导出（ANSYS / Abaqus / MIDAS）。

    斜拉桥与悬索桥共用这一段，但 `out_dir` 与 `stem` **必须各给各的**：
    悬索桥若沿用 `out/cable/export` + `stem="cable"`，两种桥型互相覆盖，
    而且导出的悬索桥模型在第三方软件里叫 "cable"——打开的人无从判断这是
    哪一座桥、哪一次求解。两个参数是必填而不是默认值，就是为了让调用点
    必须显式表态。

    `key` 是 widget key 前缀：同一次会话里只会有一种桥型渲染，但 key 撞了
    会在切换桥型时留下旧状态（Streamlit 按 key 复用 widget），所以分开。
    """
    c3, c4 = st.columns([1.2, 2.0])
    if c3.button("导出 ANSYS / Abaqus / MIDAS", key=f"{key}_export_btn"):
        try:
            from export_formats import export_all

            st.session_state[f"{key}_export"] = export_all(
                doc.model, out_dir=out_dir, stem=stem)
        except Exception as e:                      # noqa: BLE001
            st.session_state.pop(f"{key}_export", None)
            st.error(f"导出失败：{e}")
    _ex = st.session_state.get(f"{key}_export")
    if _ex:
        _cols = c4.columns(3)
        for _i, (_fmt, _lab) in enumerate((("ansys", "ANSYS (.ans)"),
                                           ("abaqus", "Abaqus (.inp)"),
                                           ("midas", "MIDAS (.mct)"))):
            _ep = Path(_ex["paths"][_fmt])
            if _ep.exists():
                _cols[_i].download_button(_lab, data=_ep.read_bytes(),
                                          file_name=_ep.name,
                                          key=f"{key}_ex_{_fmt}")
        for _w in _ex.get("warnings", [])[:4]:
            st.caption(f"· {_w}")
        st.caption("导出的是同一套节点/单元/实常数；惯矩按「绕哪根轴」同名映射，"
                   "泊松比由 G 反算。三格式均只建模、不含求解控制。")
    else:
        c4.caption("导出后可在第三方软件里独立建算，核对索力与线形。")


def _cable_extras(doc: ModelDoc, res: SolveResult) -> None:
    """斜拉桥：计算书 + 三方导出。数据全部取自 SolveResult。"""
    st.markdown("**详细计算书（7 章，含公式推导）与第三方复核**")
    c1, c2 = st.columns([1.2, 2.0])

    if c1.button("生成详细计算书 (.docx)", key="cb_report_btn"):
        with st.spinner("排版计算书（表格、插图、目录）…"):
            try:
                from report_docx import build_cable_report

                p = dict(doc.params)
                # 文件名带结果指纹：一是"这份计算书对应哪次求解"一目了然，
                # 二是避免覆盖 Word 正打开着的同名文件（实测直接
                # PermissionError，界面上只剩一句"生成失败"）。
                out = build_cable_report(
                    model=doc.model, info=doc.info,
                    res=res.as_legacy_dict(), params=p,
                    out_path=f"out/cable/report_{res.fingerprint}.docx",
                    project=res.title or "双塔三跨斜拉桥")
                st.session_state["cb_report_path"] = str(out)
            except Exception as e:                  # noqa: BLE001
                st.session_state.pop("cb_report_path", None)
                st.error(f"计算书生成失败：{e}")
    _rp = st.session_state.get("cb_report_path")
    if _rp and Path(_rp).exists():
        c2.download_button(
            "下载计算书 (.docx)", data=Path(_rp).read_bytes(),
            file_name="斜拉桥成桥态计算书.docx",
            mime=("application/vnd.openxmlformats-officedocument"
                  ".wordprocessingml.document"), key="cb_dl_docx")
        c2.caption(f"已生成 {Path(_rp).stat().st_size / 1024:.0f} KB。"
                   "数值全部来自本次求解结果，不含二次力学计算。")
    else:
        c2.caption("计算书内的表格与界面显示逐项一致：同一个 SolveResult。")

    _export_block(doc, res, out_dir="out/cable/export", stem="cable", key="cb")


def _susp_extras(doc: ModelDoc, res: SolveResult) -> None:
    """悬索桥：计算书 + 三方导出。

    与斜拉桥分开，不是因为排版不同，而是因为**内容不同**：悬索桥要讲的是
    主缆找形（H 由矢高反求，不是人为给定）、两种体系只差边界条件、以及
    平衡边跨这个自锚式独有的几何约束。这些东西塞进斜拉桥那 7 章里会互相
    打架，所以 report_docx 里本来就是两套 `_sec_*`。

    导出段则与斜拉桥共用（`_export_block`）——那里没有力学差异，
    只有目录与文件名的差别。
    """
    st.markdown("**详细计算书（含找形推导、施工阶段与体系校核）与第三方复核**")
    c1, c2 = st.columns([1.2, 2.0])
    if c1.button("生成详细计算书 (.docx)", key="susp_report_btn"):
        with st.spinner("排版计算书（表格、插图、目录）…"):
            try:
                from report_docx import build_suspension_report

                out = build_suspension_report(
                    model=doc.model, info=doc.info, res=res,
                    params=dict(doc.params),
                    out_path=f"out/susp/report_{res.fingerprint}.docx",
                    project=res.title or res.kind_label,
                    fig_dir=f"out/susp/figs_{res.fingerprint}")
                st.session_state["susp_report_path"] = str(out)
            except Exception as e:                  # noqa: BLE001
                st.session_state.pop("susp_report_path", None)
                st.error(f"计算书生成失败：{e}")
    _rp = st.session_state.get("susp_report_path")
    if _rp and Path(_rp).exists():
        c2.download_button(
            "下载计算书 (.docx)", data=Path(_rp).read_bytes(),
            file_name="悬索桥成桥态计算书.docx",
            mime=("application/vnd.openxmlformats-officedocument"
                  ".wordprocessingml.document"), key="susp_dl_docx")
        c2.caption(f"已生成 {Path(_rp).stat().st_size / 1024:.0f} KB。")
    else:
        c2.caption("计算书与界面显示同源：主缆/吊索/加劲梁三张表直接取 "
                   "SolveResult，不做二次力学计算。")

    # 悬索桥的导出与斜拉桥共用同一段代码，但目录与文件名分开：
    # out/susp/export/susp.ans，不与 out/cable/export/cable.* 互相覆盖。
    _export_block(doc, res, out_dir="out/susp/export", stem="susp", key="susp")


# ---------------------------------------------------------------- 主入口


def render(doc: ModelDoc) -> None:
    """渲染三维求解区块。调用前 ① ② 已把参数写进 doc.params。"""
    cap = doc.capability()
    solver_name = "三维内核"
    try:
        from solver_3d import solvers

        spec = solvers().get(doc.kind)
        solver_name = spec.exe_name if spec else solver_name
    except Exception:                               # noqa: BLE001
        pass

    st.divider()
    st.subheader(f"③④ 模型与求解 · {KIND_LABELS[doc.kind]}")

    if not cap["fem_eigen"]:
        st.warning(
            f"未找到 {KIND_LABELS[doc.kind]} 对应的三维内核，本区块不可用。\n\n"
            f"可用环境变量 `FEM_EIGEN_DIR` 指向内核目录，然后编译对应目标。")
        st.text("\n".join(doc.summary()["lines"]))
        return

    # ---- 模型摘要 ----
    st.markdown("**模型**")
    st.text("\n".join(doc.summary()["lines"]))
    with st.expander("查看结构化参数（params / 派生量）"):
        st.json(doc.to_dict())
        st.json(doc.info or {})

    hints = doc.hints_3d()
    if hints:
        with st.expander(f"模型校验提示（{len(hints)} 条，不影响求解）"):
            for h in hints[:15]:
                st.caption(f"· {h}")

    # ---- 互导（放在求解之前：导入别人的模型本身就是一条"取模型"的路）----
    with st.expander("与 MIDAS 互导（.mct）：导出当前模型 / 导入 MIDAS 模型试算"):
        _interop_block(doc)

    # ---- 求解 ----
    st.caption(
        f"求解器：`{solver_name}`　｜　"
        f"工况：{'几何非线性成桥态（索垂度 + 索长反算）' if cap['nonlinear'] else '线性阶段 + 活载包络'}"
        f"　｜　能力自述：{', '.join(k for k, v in cap.items() if v) or '—'}")

    if st.button(f"求解（{KIND_LABELS[doc.kind]}）", type="primary", key="r3d_solve"):
        with st.spinner("构建模型 → 导出 .dat → 调用内核 …"):
            try:
                _res = doc.solve(out_dir=f"out/{doc.kind}")
                st.session_state[SS_RESULT] = _res
            except Exception as e:                  # noqa: BLE001
                st.session_state.pop(SS_RESULT, None)
                st.error(f"求解失败：{e}")

    res: SolveResult | None = st.session_state.get(SS_RESULT)
    if res is None:
        st.caption("调好参数后点求解。结果随参数自动过期（按 .dat 文本指纹判定）。")
        return

    # 换过模型类型就不能拿旧结果凑数
    if res.kind != doc.kind:
        st.info(f"当前模型是{KIND_LABELS[doc.kind]}，缓存结果来自"
                f"{res.kind_label}，已隐藏。请重新求解。")
        return
    if res.is_stale(doc.to_dat()):
        st.warning("模型参数已修改，结果来自上一次求解，已隐藏。请重新点求解。")
        return

    # ---- 指标卡 ----
    # 超过 5 张就换行：梁格现在有 7 张（含组合控制弯矩），一线排 7 列时
    # 每列不足 130 px，"基本组合控制弯矩"会被折成三行。
    _m = res.metrics(doc.model)
    if _m:
        per = 5
        for _s in range(0, len(_m), per):
            _chunk = _m[_s:_s + per]
            _cols = st.columns(per)
            for _c, _mt in zip(_cols, _chunk):
                _c.metric(_mt.label, _mt.value + (f" {_mt.unit}" if _mt.unit else ""))
                if _mt.hint:
                    _c.caption(_mt.hint)

    # ---- 校核 ----
    _checks_block(res, doc.model)

    # ---- 荷载组合（梁格才有；斜拉桥内核不出梁内力）----
    _combination_block(res, doc.model)

    # ---- 三维交互视图（报告里的轴测图出自同一份场景）----
    _scene_block(doc, res)

    # ---- 图（与报告同一批 PNG）----
    _figures_block(doc, res)

    # ---- 表 ----
    _tables_block(res, doc.model,
                  skip=("combo_coef", "combo_summary", "combo_ultimate"))

    # ---- 报告导出 ----
    _report_block(doc, res)

    # ---- 索承体系专属出口 ----
    if res.kind == "cable_stayed":
        _cable_extras(doc, res)
    elif res.kind == "suspension":
        _susp_extras(doc, res)

    with st.expander(f"内核原始输出（{res.solver}）"):
        for d in res.diagnostics:
            st.caption(f"· {d}")
        st.text(f"指纹 {res.fingerprint}　｜　{res.dat_path}")
        if res.stdout:
            st.code(res.stdout[-1500:], language="text")
        st.code(res.dat_text[:4000] + ("\n…（已截断）" if len(res.dat_text) > 4000 else ""),
                language="text")
