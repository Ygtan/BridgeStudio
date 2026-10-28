"""自然语言驱动有限元前处理 · Streamlit 原型（统一 ModelDoc 入口）。

C 方案改造后的界面结构：**一条输入，四种结构**。

    ① 自然语言输入（单梁 / 拱桥 / 桁架桥 / 梁桥，自动识别）
    ② 参数面板（按当前类型的字段元数据自动渲染）
    ③ 模型（梁：截面图 + 模型图；桥：节点单元简图）
    ④ 计算结果（梁：anastruct 草算；桥：指向⑤）
    ⑤ FEM-Eigen 求解与可视化（两种类型共用，7 张 SVG）

与改造前的关键差别：界面不再直接持有 ModelSpec / BridgeSpec，只持有
`ModelDoc`；"能不能草算""有没有互校"由 `doc.capability()` 自述，
不再靠 `if 是桥` 分支。

运行：streamlit run app.py
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import matplotlib.pyplot as plt
import streamlit as st

from fe_solver import solve
from llm_parser import LLMConfig, probe
from model_doc import KIND_LABELS, ModelDoc
from viz import (
    draw_bridge_geometry,
    draw_deformed,
    draw_model,
    draw_moment,
    draw_section_fig,
)

# ---------------------------------------------------------------- FEM-Eigen
try:
    from fem_eigen_export import (
        EXE_RESULT_CANDIDATES,
        EXE_VIZ_CANDIDATES,
        FEM_EIGEN_DIR,
        fem_eigen_available,
        parse_result,
        run_fem_eigen,
    )

    _FEM_EXES = [p for p in (EXE_RESULT_CANDIDATES + EXE_VIZ_CANDIDATES) if p.is_file()]
    FEM_AVAILABLE = fem_eigen_available()
except Exception:          # 模块缺失/路径不存在都不该让界面崩掉
    FEM_EIGEN_DIR = None
    _FEM_EXES = []
    FEM_AVAILABLE = False

SVG_LABELS = {
    "model": "模型图",
    "disp": "位移图",
    "moment": "弯矩图",
    "shear": "剪力图",
    "axial": "轴力图",
    "stress_top": "上缘应力",
    "stress_bottom": "下缘应力",
}


def _svg_key(path: str | Path) -> str:
    """从文件名反推图种。

    文件名是 `<dat_stem>_<key>.svg`，而 key 本身可能含下划线
    （stress_top / stress_bottom），所以不能用 split('_')[-1]，
    必须按键名做**后缀匹配**。
    """
    stem = Path(path).stem
    for key in SVG_LABELS:
        if stem.endswith("_" + key):
            return key
    return stem.split("_")[-1]


def _svg_html(path: str | Path) -> str:
    """SVG 内联为 base64 <img>：Streamlit 不能直接渲染裸 SVG。"""
    b64 = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return (
        f'<img src="data:image/svg+xml;base64,{b64}" '
        f'style="width:100%;background:#ffffff;border:1px solid #d0d7de;border-radius:6px"/>'
    )


EXAMPLES: list[tuple[str, str]] = [
    ("梁 · 简支钢梁", "一座跨度30米的简支钢梁，矩形截面高1.2米宽0.6米，承受20kN/m均布荷载"),
    ("梁 · 三跨连续", "三跨连续梁总长60米，混凝土箱梁截面高2米宽4米，跨中施加50kN集中力"),
    ("梁 · 两端固支", "两端固支的工字钢梁，跨度12米，截面高0.8米宽0.3米腹板20mm翼缘15mm，均布荷载15kN/m"),
    ("桥 · 拱桥", "跨度100米的下承式拱桥，矢高20米，9对吊杆，桥面承受20kN/m均布荷载"),
    ("桥 · 桁架", "跨度60米桁架桥，8个节间，桁高6米，每个下弦节点100kN"),
]

KIND_OPTIONS = ["自动识别", "单梁", "拱桥", "桁架桥", "梁桥"]

st.set_page_config(page_title="NL-FE 前处理原型", layout="wide")
st.title("自然语言驱动有限元前处理 · 原型")
st.caption(
    "一条输入 → 自动识别 单梁 / 拱桥 / 桁架桥 / 梁桥 → 统一 ModelDoc "
    "→ .dat → FEM-Eigen 求解与出图"
)

# ---------------------------------------------------------------- 侧栏：LLM
st.sidebar.markdown("### LLM 解析设置")
_force_rule = st.sidebar.checkbox(
    "强制使用规则解析（离线）", value=False,
    help="勾选后完全不调用 LLM。适合离线演示或节省额度。"
         "桥梁解析目前本身就是规则实现。",
)

_env_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY") or ""
_ui_key = st.sidebar.text_input(
    "API Key", value="", type="password",
    placeholder="留空则用环境变量 DEEPSEEK_API_KEY",
    help="只保存在当前会话内存中，不会写入磁盘。",
)
_ui_base = st.sidebar.text_input(
    "Base URL", value=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
)
_ui_model = st.sidebar.text_input(
    "模型", value=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
)

llm_config = None
if not _force_rule:
    _key = _ui_key.strip() or _env_key
    if _key:
        llm_config = LLMConfig(api_key=_key, base_url=_ui_base.strip(),
                               model=_ui_model.strip())
        st.sidebar.success(f"LLM 已启用：{_ui_model.strip()}")
    else:
        st.sidebar.info("未配置 API Key，使用内置规则解析（离线可用）")
else:
    st.sidebar.info("已强制使用规则解析")

if llm_config is not None and st.sidebar.button("测试 LLM 连通性"):
    with st.spinner("连接中…"):
        _ok, _msg = probe(llm_config)
    (st.sidebar.success if _ok else st.sidebar.error)(
        f"{'连通' if _ok else '失败'}：{_msg}")

st.sidebar.markdown("---")
st.sidebar.caption(
    f"FEM-Eigen：{'可用' if FEM_AVAILABLE else '未找到'}\n\n"
    f"`{FEM_EIGEN_DIR if FEM_EIGEN_DIR else '—'}`"
)

# ---------------------------------------------------------------- 会话状态
if "doc" not in st.session_state:
    st.session_state.doc = ModelDoc.from_text(EXAMPLES[0][1])[0]
if "source" not in st.session_state:
    st.session_state.source = "rule"
if "diag" not in st.session_state:
    st.session_state.diag = None
if "doc_version" not in st.session_state:
    # 每次整体替换模型（点示例 / 重新解析 / 增删荷载）就 +1，
    # 让所有带 key 的控件换一批 key。
    # Streamlit 的 widget 状态按 key 持久化——沿用旧 key 会把上一个模型的
    # 数值回填到新模型上（实测过：示例2 的 50kN 被旧模型的 20 顶掉）。
    st.session_state.doc_version = 0


def _load_text(text: str, hint: str) -> None:
    """解析文本并整体替换当前模型。"""
    with st.spinner("解析中…"):
        new_doc, source, diag = ModelDoc.from_text(
            text, prev=st.session_state.doc, llm_config=llm_config, hint=hint,
        )
    st.session_state.doc = new_doc
    st.session_state.source = source
    st.session_state.diag = diag
    st.session_state.pop("fe_result", None)      # 模型换了，旧结果作废
    st.session_state.doc_version += 1


# ---------------------------------------------------------------- ① 输入
st.subheader("① 自然语言输入")

hint = st.selectbox(
    "结构类型", KIND_OPTIONS, key="kind_hint",
    help="拿不准就留「自动识别」：先按关键词判断梁/拱/桁架/梁桥，再走对应解析。"
         "指定类型时完全跳过判断——你的选择比关键词可靠。",
)

# 示例按钮必须在 text_area「之前」：若在 text_area 实例化之后再赋值
# session_state.nl_input，widget 状态会在下一次运行把它覆盖掉。
st.write("示例（点击直接建模；也可改写下方输入框后点「解析并更新模型」）：")
_ec = st.columns(len(EXAMPLES))
_clicked = None
for _i, (_label, _txt) in enumerate(EXAMPLES):
    if _ec[_i].button(_label, key=f"ex{_i}", use_container_width=True):
        _clicked = _txt

if "nl_input" not in st.session_state:
    st.session_state.nl_input = ""

if _clicked is not None:
    st.session_state.nl_input = _clicked
    _load_text(_clicked, hint)
    st.rerun()

_nl_text = st.text_area(
    "用中文描述你想建立的结构：", key="nl_input", height=80,
    placeholder="例如：跨度30米的简支钢梁，矩形截面高1.2米宽0.6米，承受20kN/m均布荷载；"
                "或：跨度100米的拱桥，矢高20米，9对吊杆，桥面20kN/m",
)

if st.button("解析并更新模型", type="primary"):
    if _nl_text.strip():
        _load_text(_nl_text.strip(), hint)
        st.rerun()
    else:
        st.warning("请先输入一句描述。")

# 解析完成后统一取最新 doc（上面的 rerun 会回到这里）
doc: ModelDoc = st.session_state.doc
ver: int = st.session_state.doc_version

st.caption(
    f"识别结果：**{KIND_LABELS[doc.kind]}**　｜　解析来源：**{st.session_state.source}**"
)

_diag = st.session_state.get("diag")
if _diag is not None:
    if not _diag.ok:
        st.warning(f"LLM 解析未采用，已回退规则解析。原因：{_diag.error}")
    # 注意：回退时也会有 notes（例如规则路径的单位哨兵提示），
    # 所以这里不能写成 elif，否则回退后的提示会被吞掉。
    if _diag.notes or _diag.raw:
        with st.expander("解析诊断（替你默认了什么）", expanded=bool(_diag.notes)):
            if _diag.notes:
                st.markdown("**以下信息未在描述中给出，已按工程默认值处理（请确认）：**")
                for _n in _diag.notes:
                    st.markdown(f"- {_n}")
            else:
                st.caption("全部关键字段均从描述中识别。")
            if _diag.raw:
                st.markdown("**LLM 原始输出**")
                st.code(_diag.raw, language="json")

# ---------------------------------------------------------------- ② 参数
st.subheader("② 参数面板（改动即时重算）")
_p = doc.params


def _render_field(col, field: dict, params: dict, prefix: str) -> None:
    """按字段元数据渲染一个控件，并把值写回 params。

    控件类型由元数据决定，四种结构共用这一段代码——这就是把"每类模型写一套
    面板"换成"一份元数据驱动"的收益。
    """
    key = field["key"]
    wkey = f"{prefix}_{key}"
    ftype = field["type"]

    if ftype == "float":
        raw = params.get(key)
        val = float(raw) if raw is not None else float(field.get("default", field["min"]))
        params[key] = col.number_input(
            field["label"], value=val, min_value=float(field["min"]),
            max_value=float(field["max"]), step=float(field["step"]),
            format="%.3f", key=wkey,
        )
    elif ftype == "int":
        params[key] = int(col.number_input(
            field["label"], value=int(params.get(key, field["min"])),
            min_value=int(field["min"]), max_value=int(field["max"]),
            step=int(field["step"]), key=wkey,
        ))
    elif ftype == "choice":
        opts = field["choices"]
        fmt = field.get("fmt", {})
        cur = params.get(key)
        idx = opts.index(cur) if cur in opts else 0
        params[key] = col.selectbox(
            field["label"], opts, index=idx, key=wkey,
            format_func=lambda v, _f=fmt: _f.get(v, v),
        )
    elif ftype == "bool":
        params[key] = col.checkbox(field["label"], value=bool(params.get(key, False)),
                                   key=wkey)


_prefix = f"p{doc.kind}{ver}"
for _row in doc.param_rows():
    _cols = st.columns(len(_row))
    for _col, _f in zip(_cols, _row):
        _render_field(_col, _f, _p, _prefix)

# --- 荷载编辑（仅单梁：桥型的荷载已折进参数里）---
if doc.kind == "beam":
    st.markdown("**荷载**")
    for _i, _ld in enumerate(_p["loads"]):
        _c = st.columns([2, 2, 2, 1])
        _c[0].write(f"荷载{_i + 1}：{'均布' if _ld['type'] == 'udl' else '集中'}")
        _ld["value"] = _c[1].number_input(
            "值", value=float(_ld["value"]), step=1.0,
            key=f"ldv{ver}_{_i}", format="%.2f",
        )
        if _ld["type"] == "point":
            _pos = _ld.get("position")
            _ld["position"] = _c[2].number_input(
                "位置(m)",
                value=float(_pos) if _pos is not None else float(_p["length"]) / 2,
                step=0.5, key=f"ldp{ver}_{_i}", format="%.2f",
            )
        if _c[3].button("删除", key=f"lld{ver}_{_i}"):
            _p["loads"].pop(_i)
            st.session_state.doc_version += 1
            st.rerun()

    _ab1, _ab2, _ab3 = st.columns([1, 1, 2])
    if _ab1.button("＋ 均布 20 kN/m"):
        _p["loads"].append({"type": "udl", "value": 20.0, "start": None,
                            "end": None, "position": None})
        st.session_state.doc_version += 1
        st.rerun()
    if _ab2.button("＋ 跨中集中 50 kN"):
        _p["loads"].append({"type": "point", "value": 50.0, "start": None,
                            "end": None, "position": float(_p["length"]) / 2})
        st.session_state.doc_version += 1
        st.rerun()

    with _ab3:
        st.download_button(
            "下载工程文件 (.json)",
            data=json.dumps(doc.to_dict(), ensure_ascii=False, indent=2),
            file_name=f"{doc.kind}.json", mime="application/json",
            help="参数是纯 JSON：存下来就能原样复现这个模型。",
        )

# --- 校验：参数问题必须在求解前以中文说清楚 ---
doc.build()
_errs = doc.validate()
if _errs:
    for _e in _errs:
        st.error(_e)
    st.stop()

_cap = doc.capability()

# 单梁：anastruct 草算（便宜，每次 rerun 都算）；桥型没有这个能力
_ana = None
if _cap["anastruct"]:
    try:
        _ana = solve(doc.model)
    except Exception as _e:
        st.error(f"anastruct 求算失败：{_e}")

# ---------------------------------------------------------------- ③ 模型
st.subheader("③ 模型")
if _cap["matplotlib"]:
    _scol, _mcol = st.columns([1, 3])
    with _scol:
        _fig = draw_section_fig(doc.model)
        st.pyplot(_fig)
        plt.close(_fig)
        if _ana is not None:
            st.markdown("**截面属性**")
            st.write(f"面积 A = {_ana['props']['A']:.4f} m²")
            st.write(f"惯性矩 I = {_ana['props']['I']:.5e} m⁴")
    with _mcol:
        _fig = draw_model(doc.model)
        st.pyplot(_fig)
        plt.close(_fig)
else:
    _fig = draw_bridge_geometry(doc.model)
    st.pyplot(_fig)
    plt.close(_fig)

st.text("\n".join(doc.summary()["lines"]))
with st.expander("查看结构化模型（ModelDoc → params）"):
    st.json(doc.to_dict())

# ---------------------------------------------------------------- ④ 结果
st.subheader("④ 计算结果")
if _ana is not None:
    _spec = doc.model
    _total = sum(
        (ld.value * (ld.end - ld.start if ld.end is not None else _spec.length))
        if ld.type == "udl" else ld.value
        for ld in _spec.loads
    )
    # 支座对梁的力 = -Fy
    _reac = -sum(v.get("Fy", 0) for v in _ana["reactions"].values()) / 1000.0

    _k1, _k2, _k3, _k4 = st.columns(4)
    _k1.metric("最大挠度", f"{_ana['max_deflection'] * 1000:.2f} mm")
    _k2.metric("最大弯矩", f"{_ana['max_moment'] / 1000:.1f} kN·m")
    _k3.metric("总竖向荷载", f"{_total:.1f} kN")
    _k4.metric("支座反力合计", f"{_reac:.1f} kN")
    st.caption(
        f"平衡校验：荷载合计 {_total:.1f} kN ≈ 反力合计 {_reac:.1f} kN"
        f"（差异 {abs(_total - _reac):.2f} kN）"
    )

    _d1, _d2 = st.columns(2)
    with _d1:
        _fig = draw_deformed(_spec, _ana)
        st.pyplot(_fig)
        plt.close(_fig)
    with _d2:
        _fig = draw_moment(_spec, _ana)
        st.pyplot(_fig)
        plt.close(_fig)
else:
    st.info(
        "**桥型模型只走 FEM-Eigen**（anastruct 没有索/桁架单元），"
        "请在下方 ⑤ 求解后查看结果。"
    )

# ---------------------------------------------------------------- ⑤ FEM-Eigen
st.subheader("⑤ FEM-Eigen 求解与可视化")

if not _cap["fem_eigen"]:
    st.warning(
        "未找到 FEM-Eigen 可执行文件，本环节不可用。\n\n"
        f"期望在 `{FEM_EIGEN_DIR}` 下存在 `build/example_mixed.exe` 或 "
        "`build/example_viz.exe`。"
    )
else:
    st.caption(f"求解器：`{_FEM_EXES[0].name}`　｜　目录：`{_FEM_EXES[0].parent}`　"
               f"｜　自重：**{'计入' if _p.get('self_weight') else '不计'}**（在②中切换）")

    _dat_now = doc.to_dat()

    if st.button("导出 .dat 并调用 FEM-Eigen 求解", type="primary"):
        _outdir = Path("out/gui_run")
        _outdir.mkdir(parents=True, exist_ok=True)
        _dat_path = _outdir / "gui_model.dat"
        _dat_path.write_text(_dat_now, encoding="utf-8")
        with st.spinner("FEM-Eigen 求解中…"):
            try:
                _info = run_fem_eigen(_dat_path, workdir=_outdir)
                st.session_state.fe_result = {
                    "dat_text": _dat_now,
                    "dat_path": str(_dat_path),
                    "result": str(_info["result"]) if _info["result"] else None,
                    "svgs": [str(x) for x in _info["svgs"]],
                    "stdout": _info["stdout"],
                    "ok": _info["ok"],
                }
            except Exception as _e:
                st.session_state.fe_result = None
                st.error(f"FEM-Eigen 求解失败：{_e}")

    _fr = st.session_state.get("fe_result")
    _stale = bool(_fr) and _fr.get("dat_text") != _dat_now

    if _fr and _stale:
        st.warning("模型参数已修改，下方结果来自上一次求解，已隐藏。请重新点上方按钮。")

    if _fr and not _stale and not _fr["ok"]:
        st.error("FEM-Eigen 未产出任何结果，请查看下方日志。")
        st.code(_fr["stdout"][-2000:] or "（无输出）", language="text")

    if _fr and not _stale and _fr["ok"]:
        # --- 与 anastruct 交叉比对（仅单梁且不计自重）---
        # anastruct 侧没有重力项：勾了自重还去比，会得到几百个百分点的
        # "假误差"（实测 277%），毫无意义。capability() 已经把这个条件算好了。
        if _cap["cross_check"] and _fr["result"] and _ana is not None:
            try:
                _rr = parse_result(_fr["result"])
                _v_fem = min((n["v"] for n in _rr["nodes"].values()), default=0.0)
                _m_fem = max((max(abs(e["M1"]), abs(e["M2"]))
                              for e in _rr["elements"].values()), default=0.0)
                # FEM-Eigen 反力以 +y 向上为正，与向下荷载直接平衡，不取负
                _r_fem = sum(v["value"] for v in _rr["reactions"].values()
                             if v["dof"] == 2)
                _m_ana = abs(_ana["max_moment"])
                _v_ana = _ana["max_deflection"]
                _dm = abs(_m_fem - _m_ana) / _m_ana * 100 if _m_ana else 0.0
                _dv = abs(_v_fem - _v_ana) / abs(_v_ana) * 100 if _v_ana else 0.0

                _c1, _c2, _c3, _c4 = st.columns(4)
                _c1.metric("FEM 最大挠度", f"{_v_fem * 1000:.3f} mm", f"草算差 {_dv:.4f}%")
                _c2.metric("FEM 最大弯矩", f"{_m_fem / 1000:.2f} kN·m", f"草算差 {_dm:.4f}%")
                _c3.metric("FEM 反力合计", f"{_r_fem / 1000:.2f} kN")
                _c4.metric("与草算最大差异", f"{max(_dm, _dv):.4f} %")
                st.caption(
                    "两个独立求解器（Python/anastruct 与 C++/Eigen）在同一模型上互校，"
                    "差异主要来自网格密度，通常 < 0.05%。"
                )
            except Exception as _e:
                st.warning(f"结果解析失败（SVG 仍可用）：{_e}")
        elif not _cap["cross_check"]:
            # 桥型：只有一个内核，别让人误以为也校验过了
            try:
                _rr = parse_result(_fr["result"]) if _fr["result"] else None
                if _rr:
                    _v_fem = min((n["v"] for n in _rr["nodes"].values()), default=0.0)
                    _m_fem = max((max(abs(e["M1"]), abs(e["M2"]))
                                  for e in _rr["elements"].values()), default=0.0)
                    _r_fem = sum(v["value"] for v in _rr["reactions"].values()
                                 if v["dof"] == 2)
                    _c1, _c2, _c3, _c4 = st.columns(4)
                    _c1.metric("最大挠度", f"{_v_fem * 1000:.3f} mm")
                    _c2.metric("最大弯矩", f"{_m_fem / 1000:.2f} kN·m")
                    _c3.metric("支反力合计", f"{_r_fem / 1000:.2f} kN")
                    _c4.metric("单元数", f"{len(_rr['elements'])}")
                    st.caption("本模型只走 FEM-Eigen 单内核，无交叉校验（如实标注）。")
            except Exception as _e:
                st.warning(f"结果解析失败（SVG 仍可用）：{_e}")

        # --- SVG ---
        st.markdown("**FEM-Eigen 生成的可视化**")
        if _fr["svgs"]:
            _named = [(SVG_LABELS.get(_svg_key(x), _svg_key(x)), x) for x in _fr["svgs"]]
            _named.sort(key=lambda t: list(SVG_LABELS).index(_svg_key(t[1]))
                        if _svg_key(t[1]) in SVG_LABELS else 99)
            _tabs = st.tabs([lab for lab, _ in _named])
            for _tab, (_lab, _f) in zip(_tabs, _named):
                with _tab:
                    st.markdown(_svg_html(_f), unsafe_allow_html=True)
                    st.caption(Path(_f).name)
        else:
            st.info("本次未生成 SVG（可能只跑了数值求解器）。")

        with st.expander("查看导出的 .dat（FEM-Eigen new format）"):
            st.code(_fr["dat_text"], language="text")
            st.code(_fr["stdout"][-1500:] or "（无输出）", language="text")
