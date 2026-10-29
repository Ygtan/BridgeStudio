"""ui_3d.py —— 三维区块的界面（⑥）。

为什么从 app.py 里搬出来
------------------------
改造前 ⑥ 梁格 与 ⑦ 斜拉桥 是两段各 150 行的手写控件：两套 exe 名、两套
session_state 键、两套"参数变了作废结果"的判断，第四张图还得再抄一遍。
结果是"加一个内核"= 复制 150 行 + 改 20 处字符串。

接入之后这里只剩三件事：

    参数由 doc.param_rows() 给 → 求解由 doc.solve() 做 → 展示由 SolveResult 出

界面不再知道内核叫什么、exe 在哪、输出文件分几段。换内核只改 solver_3d.py。

一处刻意的保留：斜拉桥的"计算书"与"第三方导出"两个按钮仍是专属的——
它们消费的是 cable 特有的下料索长/制造线形，梁格没有这些量。
但触发它们的数据来源已经是 SolveResult，不再是各自的 session_state 字典。
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import streamlit as st

from model_doc import KIND_LABELS, ModelDoc
from result_3d import SolveResult, support_x

#: 结果缓存键（带模型指纹，避免"换模型后显示上一个模型的结果"）
SS_RESULT = "r3d_result"


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


def _tables_block(res: SolveResult, model: Any) -> None:
    """明细表：SolveResult.tables() 出数据，这里只负责摆。"""
    tabs = res.tables(model)
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
            st.download_button(
                f"下载 {tb['title']} (.csv)",
                data=_csv_bytes(tb["headers"], tb["rows"]),
                file_name=f"{n}.csv", mime="text/csv", key=f"dl_{n}")


# ---------------------------------------------------------------- 图


def _plot_grillage(res: SolveResult, model: ModelDoc) -> None:
    """中梁线（或全部梁节点）的竖向包络挠度沿桥分布。"""
    x_of = {n.id: n.x for n in model.model.nodes}
    pts = sorted(
        ((x_of[nid], lo, hi) for (nid, dof), (lo, hi) in res.disp.items()
         if dof == 3 and nid in x_of),
        key=lambda t: t[0])
    if not pts:
        st.caption("结果中没有竖向位移，跳过曲线。")
        return
    xs = [p[0] for p in pts]
    lo = [p[1] * 1000 for p in pts]
    hi = [p[2] * 1000 for p in pts]
    fig, ax = plt.subplots(figsize=(10, 3.2))
    ax.plot(xs, lo, "-", lw=1.2, color="#185FA5", label="包络 min")
    ax.plot(xs, hi, "--", lw=0.9, color="#8FA8C8", label="包络 max")
    for sx in support_x(model.model):
        ax.axvline(x=sx, color="#A32D2D", ls="--", lw=0.8, alpha=0.7)
    ax.set_xlabel("顺桥向 x (m)　（红虚线 = 支承断面）")
    ax.set_ylabel("竖向位移 (mm)")
    ax.set_title(f"{res.title} · 阶段位移包络（向下为负）")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    st.pyplot(fig)
    plt.close(fig)


def _plot_cable(res: SolveResult, model: ModelDoc) -> None:
    """索力与制造线形预抬高沿桥纵向的双联图。"""
    x_of = {n.id: n.x for n in model.model.nodes}
    sup = support_x(model.model)

    if res.cables:
        cs = sorted(res.cables, key=lambda c: x_of.get(c["anchor"], 0.0))
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
        ax1.plot([x_of.get(c["anchor"], 0.0) for c in cs],
                 [c["T"] / 1e3 for c in cs], "o-", ms=3, color="#185FA5", lw=1.2)
        for sx in sup:
            ax1.axvline(x=sx, color="#A32D2D", ls="--", lw=0.8, alpha=0.6)
        ax1.set_ylabel("成桥索力 (kN)")
        ax1.set_title("成桥态 · 索力与制造线形预抬高")
        ax1.grid(alpha=0.3)

        deck = sorted((n for n in res.camber if 1000 < n < 2000),
                      key=lambda n: x_of.get(n, 0.0)) or sorted(res.camber)
        ax2.plot([x_of.get(n, 0.0) for n in deck],
                 [res.camber[n][2] * 1000 for n in deck],
                 "o-", ms=3, color="#2E7D32", lw=1.2)
        ax2.axhline(y=0.0, color="#666666", lw=0.8)
        for sx in sup:
            ax2.axvline(x=sx, color="#A32D2D", ls="--", lw=0.8, alpha=0.6)
        ax2.set_xlabel("顺桥向 x (m)　（红虚线 = 支承断面）")
        ax2.set_ylabel("预抬高 (mm)")
        ax2.grid(alpha=0.3)
        st.pyplot(fig)
        plt.close(fig)


# ---------------------------------------------------------------- 斜拉桥专属


def _cable_extras(doc: ModelDoc, res: SolveResult) -> None:
    """计算书 + 三方导出。数据全部取自 SolveResult。"""
    st.markdown("**计算书与第三方复核**")
    c1, c2 = st.columns([1.2, 2.0])

    if c1.button("生成计算书 (.docx)", key="cb_report_btn"):
        with st.spinner("排版计算书（表格、插图、目录）…"):
            try:
                from report_docx import build_cable_report

                p = dict(doc.params)
                out = build_cable_report(
                    model=doc.model, info=doc.info,
                    res=res.as_legacy_dict(), params=p,
                    out_path="out/cable/report.docx",
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

    c3, c4 = st.columns([1.2, 2.0])
    if c3.button("导出 ANSYS / Abaqus / MIDAS", key="cb_export_btn"):
        try:
            from export_formats import export_all

            st.session_state["cb_export"] = export_all(
                doc.model, out_dir="out/cable/export", stem="cable")
        except Exception as e:                      # noqa: BLE001
            st.session_state.pop("cb_export", None)
            st.error(f"导出失败：{e}")
    _ex = st.session_state.get("cb_export")
    if _ex:
        _cols = c4.columns(3)
        for _i, (_fmt, _lab) in enumerate((("ansys", "ANSYS (.ans)"),
                                           ("abaqus", "Abaqus (.inp)"),
                                           ("midas", "MIDAS (.mct)"))):
            _ep = Path(_ex["paths"][_fmt])
            if _ep.exists():
                _cols[_i].download_button(_lab, data=_ep.read_bytes(),
                                          file_name=_ep.name, key=f"cb_ex_{_fmt}")
        for _w in _ex.get("warnings", [])[:4]:
            st.caption(f"· {_w}")
        st.caption("导出的是同一套节点/单元/实常数；惯矩按「绕哪根轴」同名映射，"
                   "泊松比由 G 反算。三格式均只建模、不含求解控制。")
    else:
        c4.caption("导出后可在第三方软件里独立建算，核对索力与线形。")


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
    _m = res.metrics(doc.model)
    if _m:
        _cols = st.columns(len(_m))
        for _c, _mt in zip(_cols, _m):
            _c.metric(_mt.label, _mt.value + (f" {_mt.unit}" if _mt.unit else ""))
            if _mt.hint:
                _c.caption(_mt.hint)

    # ---- 校核 ----
    _checks_block(res, doc.model)

    # ---- 图 ----
    if res.kind == "cable_stayed":
        _plot_cable(res, doc)
    else:
        _plot_grillage(res, doc)

    # ---- 表 ----
    _tables_block(res, doc.model)

    # ---- 专属出口 ----
    if res.kind == "cable_stayed":
        _cable_extras(doc, res)

    with st.expander(f"内核原始输出（{res.solver}）"):
        for d in res.diagnostics:
            st.caption(f"· {d}")
        st.text(f"指纹 {res.fingerprint}　｜　{res.dat_path}")
        if res.stdout:
            st.code(res.stdout[-1500:], language="text")
        st.code(res.dat_text[:4000] + ("\n…（已截断）" if len(res.dat_text) > 4000 else ""),
                language="text")
