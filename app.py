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


# ================================================================ ⑥ 三维梁格
# 三跨连续箱梁空间梁格（ModelSpec3D）：逐跨施工阶段 + JTG D60 车道活载。
# 与①~⑤的 2D 链路相互独立——3D 内核（example_3d_grillage.exe）输出的是
# 阶段位移/内力包络与活载包络，不是 7 张 SVG，因此单独成块。
try:
    from fem_eigen_3d import fem_eigen_3d_available, solve_3d
    from grillage_3d import build_box_grillage_3d
    _G3D_OK = fem_eigen_3d_available()
except Exception:
    _G3D_OK = False

st.divider()
st.subheader("⑥ 三维梁格 · 三跨连续箱梁（施工阶段 + 活载）")

if not _G3D_OK:
    st.info(
        "三维求解器不可用：需要 FEM-Eigen 的 `build/example_3d_grillage.exe`。"
        "可用环境变量 `FEM_EIGEN_DIR` 指定内核目录。"
    )
else:
    _g = st.session_state.get("g3d_params", {})
    _c1, _c2, _c3, _c4, _c5 = st.columns(5)
    with _c1:
        _sp1 = st.number_input("边跨 (m)", 10.0, 60.0, float(_g.get("sp1", 30.0)), 5.0,
                               key="g3d_sp1")
        _sp2 = st.number_input("中跨 (m)", 20.0, 120.0, float(_g.get("sp2", 50.0)), 5.0,
                               key="g3d_sp2")
    with _c2:
        _Bw = st.number_input("箱宽 B (m)", 6.0, 20.0, float(_g.get("B", 12.0)), 0.5,
                              key="g3d_B")
        _hg = st.number_input("梁高 h (m)", 1.2, 5.0, float(_g.get("h", 2.5)), 0.1,
                              key="g3d_h")
    with _c3:
        _tf = st.number_input("顶/底板厚 (m)", 0.15, 0.6, float(_g.get("tf", 0.28)),
                              0.01, key="g3d_tf")
        _tw = st.number_input("腹板厚 (m)", 0.2, 0.8, float(_g.get("tw", 0.45)), 0.05,
                              key="g3d_tw")
    with _c4:
        _qd = st.number_input("桥面均布 q (kN/m)", 20.0, 500.0,
                              float(_g.get("q", 100.0)), 10.0, key="g3d_q")
        _panel = st.number_input("横梁间距 (m)", 1.0, 5.0, float(_g.get("panel", 2.5)),
                                 0.5, key="g3d_panel")
    with _c5:
        _staged = st.checkbox("逐跨施工（3 阶段）", value=True, key="g3d_staged")
        _lane = st.checkbox("JTG D60 车道活载", value=True, key="g3d_lane")

    _params_now = dict(sp1=_sp1, sp2=_sp2, B=_Bw, h=_hg, tf=_tf, tw=_tw,
                       q=_qd, panel=_panel, staged=_staged, lane=_lane)
    if _params_now != _g:
        st.session_state["g3d_params"] = _params_now
        st.session_state.pop("g3d_result", None)      # 参数变 → 结果过期

    if st.button("构建梁格并求解（FEM-Eigen 3D）", key="g3d_solve"):
        with st.spinner("构建 → 导出 .dat → example_3d_grillage.exe 求解 …"):
            try:
                _m3 = build_box_grillage_3d(
                    spans=(_sp1, _sp2, _sp1), B=_Bw, h=_hg, tf=_tf, tw=_tw,
                    n_lines=3, panel=_panel, q_deck=_qd * 1000.0,
                    staged=_staged,
                    lane_code="JTG_D60" if _lane else "")
                _r3 = solve_3d(_m3, out_dir="out/g3d", dat_name="grillage.dat")
                st.session_state["g3d_result"] = _r3
                st.session_state["g3d_stats"] = _m3.stats()
                st.session_state["g3d_dat"] = _m3.to_dat()
                # 支座/跨中节点索引：按中梁线（2001..）的实际节点 x 推
                _cl = sorted(n.id for n in _m3.nodes if 2000 < n.id < 3000)
                _clx = [next(n.x for n in _m3.nodes if n.id == i) for i in _cl]
                _sup_x = [0.0, _sp1, _sp1 + _sp2, 2 * _sp1 + _sp2]
                _sup_nodes = [min(range(len(_clx)),
                                  key=lambda k: abs(_clx[k] - sx)) for sx in _sup_x]
                _mid_i = (_sup_nodes[1] + _sup_nodes[2]) // 2
                st.session_state["g3d_meta"] = {
                    "mid_node": 2000 + _mid_i,
                    "sup_nodes": _sup_nodes,
                    "n_line_nodes": len(_cl),
                }
            except Exception as _e:
                st.error(f"三维求解失败：{_e}")

    _r3 = st.session_state.get("g3d_result")
    if _r3:
        _stats = st.session_state.get("g3d_stats", {})
        _meta = st.session_state.get("g3d_meta", {})
        _live = _r3.get("live", {})

        _m1, _m2, _m3c, _m4 = st.columns(4)
        _m1.metric("节点 / 单元",
                   f"{_stats.get('nodes', 0)} / {_stats.get('elements', 0)}")
        _m1.metric("施工阶段", f"{_stats.get('stages', 0)}")
        _dw = _r3["disp"].get((_meta.get("mid_node", 2022), 3), (0, 0))
        _m2.metric("中跨跨中包络挠度", f"{_dw[0] * 1000:.2f} mm")
        if _live:
            _lv = _live[max(_live)][1]
            _m3c.metric("成桥活载包络（影响点）", f"{_lv * 1000:.2f} mm")
        _m4.metric("梁内力包络单元数", f"{len(_r3['beam'])}")

        # ---- 中梁线包络挠度曲线（节点索引取自 meta，适配任意跨径）----
        _xx, _yy = [], []
        for _nid in range(2001, 2001 + _meta.get("n_line_nodes", 45)):
            _w = _r3["disp"].get((_nid, 3))
            if _w is None:
                continue
            _xx.append(_nid - 2000)
            _yy.append(_w[0] * 1000)
        if _yy:
            fig, ax = plt.subplots(figsize=(10, 3.2))
            ax.plot(_xx, _yy, "-o", ms=3, color="#185FA5", lw=1.2)
            for _si in _meta.get("sup_nodes", []):
                ax.axvline(x=_si, color="#A32D2D", ls="--", lw=0.8)
            ax.set_xlabel("中梁线节点序号")
            ax.set_ylabel("包络挠度 min (mm)")
            ax.set_title("三跨连续箱梁 · 中梁线位移包络（竖向，向下为负）")
            ax.grid(alpha=0.3)
            st.pyplot(fig)

        if _live:
            st.markdown("**活载包络（JTG D60，影响点 = 中跨跨中）**："
                        + "　".join(
                            f"阶段{k}: {v[1]*1000:.2f} mm"
                            for k, v in sorted(_live.items())))

        with st.expander("查看 3D .dat（fem_3d new format）"):
            st.code(st.session_state.get("g3d_dat", ""), language="text")
    else:
        st.caption("调整参数后点击求解；结果随参数自动过期。")


# ================================================================ ⑦ 斜拉桥成桥态
# P3 内核的能力出口：给定跨度/索数/定索力方法 → 下料索长 + 制造线形（预抬高）。
# 与⑥梁格的区别不只是模型，而是求解类型：⑥是线性阶段+活载包络，
# ⑦是几何非线性成桥态闭环（索垂度、索长反算、几何闭合）。
try:
    from cable_stayed_3d import build_cable_stayed_3d, cable_break_load
    from fem_eigen_cable import cable_solver_available, solve_cable_bridge
    _CB_OK = cable_solver_available()
except Exception:
    _CB_OK = False

st.divider()
st.subheader("⑦ 斜拉桥成桥态 · 下料索长与制造线形")

if not _CB_OK:
    st.info(
        "成桥态求解器不可用：需要 FEM-Eigen 的 `build/cable_solver.exe`。\n\n"
        "编译：`cd D:/AI-work/FEM-Eigen && cmake --build build --target cable_solver`"
    )
else:
    _cbc = st.columns(5)
    with _cbc[0]:
        _cb_Lm = st.number_input("主跨 (m)", 100.0, 600.0, 200.0, 10.0, key="cb_Lm")
        _cb_Ls = st.number_input("边跨 (m)", 30.0, 300.0, 80.0, 5.0, key="cb_Ls")
    with _cbc[1]:
        _cb_Ht = st.number_input("塔高 (m)", 20.0, 200.0, 60.0, 5.0, key="cb_Ht")
        _cb_n = int(st.number_input("每塔每侧索数", 3, 30, 12, 1, key="cb_n"))
    with _cbc[2]:
        _cb_h = st.number_input("主梁高 (m)", 1.2, 6.0, 2.5, 0.1, key="cb_h")
        _cb_B = st.number_input("主梁宽 (m)", 6.0, 30.0, 12.0, 0.5, key="cb_B")
    with _cbc[3]:
        _cb_q = st.number_input("桥面恒载 (kN/m)", 20.0, 500.0, 100.0, 10.0,
                                key="cb_q")
        _cb_A = st.number_input("单索面积 (mm²)", 1000.0, 20000.0, 6000.0, 500.0,
                                key="cb_A")
    with _cbc[4]:
        _cb_method = st.selectbox(
            "定索力方法", ["按恒载竖向分配", "刚性支承连续梁法", "最小弯曲能量法"],
            key="cb_method",
            help="斜拉桥默认用第一项：ΣT·ez 精确等于桥面恒载，且分布随索倾角平滑。"
                 "后两项由内核计算，适合无塔处竖向支座的纯索承体系。")
        _cb_fpu = st.number_input("索破断强度 (MPa)", 1200.0, 2200.0, 1860.0, 10.0,
                                  key="cb_fpu")

    _method_key = {"按恒载竖向分配": "DECK", "刚性支承连续梁法": "RIGID",
                   "最小弯曲能量法": "MINENERGY"}[_cb_method]
    _cb_now = dict(Lm=_cb_Lm, Ls=_cb_Ls, Ht=_cb_Ht, n=_cb_n, h=_cb_h, B=_cb_B,
                   q=_cb_q, A=_cb_A, method=_method_key, fpu=_cb_fpu)
    if _cb_now != st.session_state.get("cb_params"):
        st.session_state["cb_params"] = _cb_now
        st.session_state.pop("cb_result", None)     # 参数变 → 结果过期

    if st.button("求解成桥态（非线性闭环）", key="cb_solve", type="primary"):
        with st.spinner("建桥 → 导出 .dat → cable_solver 几何非线性成桥态 …"):
            try:
                _cbm, _cbinfo = build_cable_stayed_3d(
                    main_span=_cb_Lm, side_span=_cb_Ls, tower_height=_cb_Ht,
                    n_cable=_cb_n, h=_cb_h, B=_cb_B,
                    q_deck=_cb_q * 1e3,
                    cable_area_mm2=_cb_A, method=_method_key,
                )
                _cbres = solve_cable_bridge(_cbm, out_dir="out/cable",
                                            dat_name="cable.dat")
                st.session_state["cb_result"] = _cbres
                st.session_state["cb_model"] = _cbm
                st.session_state["cb_info"] = _cbinfo
                st.session_state["cb_dat"] = _cbm.to_dat()
            except Exception as _e:
                st.session_state["cb_result"] = None
                st.error(f"成桥态求解失败：{_e}")

    _cb = st.session_state.get("cb_result")
    if _cb:
        _md = _cb["meta"]
        _cbm = st.session_state.get("cb_model")
        _cbinfo = st.session_state.get("cb_info", {})
        _x_of = {n.id: n.x for n in _cbm.nodes}

        _m1, _m2, _m3, _m4, _m5 = st.columns(5)
        _m1.metric("收敛", "是" if _cb["converged"] else "否")
        _m2.metric("几何迭代", f"{_md.get('iterations', 0)} 轮 / {_md.get('elapsed_ms', 0):.0f} ms")
        _m3.metric("闭合误差", f"{_md.get('closure_err', 0) * 1000:.2e} mm")
        _m4.metric("索力范围", f"{_md.get('tension_min_kN', 0):.0f} ~ "
                               f"{_md.get('tension_max_kN', 0):.0f} kN")
        _m5.metric("索承担桥面恒载", f"{_md.get('deck_share_pct', 0):.1f} %")
        st.caption(
            f"模型 {_md.get('nodes')} 节点 / {_md.get('elements')} 单元 / "
            f"{_md.get('cables')} 根索（{_md.get('dofs')} 自由度）　｜　"
            f"支座承担 {_md.get('support_share_kN', 0):.0f} kN　｜　"
            f"索自重 {_cbinfo.get('cable_w', 0):.0f} N/m（垂度已计入）"
        )

        # ---- 索力 / 预抬高 沿桥纵向分布 ----
        _cs = sorted(_cb["cables"], key=lambda c: _x_of.get(c["anchor"], 0.0))
        _xs = [_x_of.get(c["anchor"], 0.0) for c in _cs]
        _ts = [c["T"] / 1000.0 for c in _cs]
        _deck_nodes = sorted((n for n in _cb["camber"] if 1000 < n < 2000),
                             key=lambda n: _x_of.get(n, 0.0))
        _dxs = [_x_of.get(n, 0.0) for n in _deck_nodes]
        _dzs = [_cb["camber"][n][2] * 1000.0 for n in _deck_nodes]

        _fig, (_ax1, _ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
        _ax1.plot(_xs, _ts, "o-", ms=3, color="#185FA5", lw=1.2)
        for _sx in _cbinfo.get("x_supports", []):
            _ax1.axvline(x=_sx, color="#A32D2D", ls="--", lw=0.8, alpha=0.6)
        _ax1.set_ylabel("成桥索力 (kN)")
        _ax1.set_title("斜拉桥成桥态 · 索力与制造线形预抬高")
        _ax1.grid(alpha=0.3)

        _ax2.plot(_dxs, _dzs, "o-", ms=3, color="#2E7D32", lw=1.2)
        _ax2.axhline(y=0.0, color="#666666", lw=0.8)
        for _sx in _cbinfo.get("x_supports", []):
            _ax2.axvline(x=_sx, color="#A32D2D", ls="--", lw=0.8, alpha=0.6)
        _ax2.set_xlabel("顺桥向 x (m)　（红虚线=支承断面）")
        _ax2.set_ylabel("预抬高 (mm)")
        _ax2.grid(alpha=0.3)
        st.pyplot(_fig)
        plt.close(_fig)

        # ---- 下料索长表 ----
        st.markdown("**下料索长表（施工下料 / 挂索依据）**")
        _fpu = _cb_fpu * 1e6
        _bl = cable_break_load(_cb_A, _fpu)
        _rows = []
        for c in _cs:
            _sig = c["T"] / (_cb_A * 1e-6)
            _rows.append({
                "索单元": c["elem"],
                "梁侧锚点": c["anchor"],
                "锚点x(m)": round(_x_of.get(c["anchor"], 0.0), 2),
                "设计弦长(m)": round(c["Ltarget"], 3),
                "下料索长L0(m)": round(c["L0"], 3),
                "成桥弦长(m)": round(c["chord"], 3),
                "成桥索力(kN)": round(c["T"] / 1e3, 1),
                "索应力(MPa)": round(_sig / 1e6, 0),
                "安全系数": round(_bl / max(c["T"], 1e-9), 2),
            })
        st.dataframe(_rows, use_container_width=True, hide_index=True)

        _tmax = max(c["T"] for c in _cb["cables"])
        _sf_min = _bl / _tmax
        if _sf_min < 2.0:
            st.warning(
                f"最大索力 {_tmax/1e3:.0f} kN，安全系数仅 {_sf_min:.2f}"
                f"（< 2.0）。请加大单索面积或减少索数。")

        # ---- 预抬高表 ----
        st.markdown("**制造线形（预抬高）表**")
        _crow = [{"节点": n, "x(m)": round(_x_of.get(n, 0.0), 2),
                  "预抬高(mm)": round(_cb["camber"][n][2] * 1000, 1),
                  "制造标高(m)": round(_cb["erected"].get(n, (0, 0, 0))[2], 4)}
                 for n in _deck_nodes]
        st.dataframe(_crow, use_container_width=True, hide_index=True)
        _zmax = max(abs(v) for v in _dzs) if _dzs else 0.0
        st.caption(
            f"预抬高最大绝对值 {_zmax:.1f} mm（约 L/{_cb_Lm*1000/max(_zmax,1e-9):.0f}）。"
            "**负值表示该点成桥会上抬，制造时要压低**——量级来自给定索力下"
            "结构的真实变形，不是算法噪声。")

        # ---- 下载 ----
        _dl1, _dl2 = st.columns(2)
        with _dl1:
            st.download_button(
                "下载下料索长表 (.csv)",
                data="\n".join(
                    ["索单元,梁侧锚点,锚点x_m,设计弦长_m,下料索长L0_m,成桥弦长_m,"
                     "成桥索力_kN,索应力_MPa,安全系数"]
                    + [",".join(str(r[k]) for k in r) for r in _rows]),
                file_name="cable_length_table.csv", mime="text/csv",
                key="cb_dl1")
        with _dl2:
            st.download_button(
                "下载制造线形表 (.csv)",
                data="\n".join(
                    ["节点,x_m,预抬高_mm,制造标高_m"]
                    + [f"{r['节点']},{r['x(m)']},{r['预抬高(mm)']},{r['制造标高(m)']}"
                       for r in _crow]),
                file_name="camber_table.csv", mime="text/csv", key="cb_dl2")

        # ---- 计算书（P4 成果层：把上面三张表落成可交付的 Word）----
        st.markdown("**计算书（Word，含索力表 / 下料索长表 / 制造线形表）**")
        _rc1, _rc2 = st.columns([1.1, 2.0])
        if _rc1.button("生成计算书 (.docx)", key="cb_report_btn"):
            with st.spinner("排版计算书（表格、插图、目录）…"):
                try:
                    from report_docx import build_cable_report
                    _p = dict(st.session_state.get("cb_params", {}))
                    _p["Lm"], _p["Ls"], _p["Ht"] = _cb_Lm, _cb_Ls, _cb_Ht
                    _p["n"], _p["h"], _p["B"] = _cb_n, _cb_h, _cb_B
                    _p["q"], _p["A"] = _cb_q, _cb_A
                    _p["method"], _p["fpu"] = _method_key, _cb_fpu
                    _out = build_cable_report(
                        model=_cbm, info=_cbinfo, res=_cb, params=_p,
                        out_path="out/cable/report.docx",
                        project="双塔三跨斜拉桥")
                    st.session_state["cb_report_path"] = str(_out)
                except Exception as _e:                     # noqa: BLE001
                    st.session_state.pop("cb_report_path", None)
                    st.error(f"计算书生成失败：{_e}")
        _rp = st.session_state.get("cb_report_path")
        if _rp and Path(_rp).exists():
            _rc2.download_button(
                "下载计算书 (.docx)", data=Path(_rp).read_bytes(),
                file_name="斜拉桥成桥态计算书.docx",
                mime=("application/vnd.openxmlformats-officedocument"
                      ".wordprocessingml.document"),
                key="cb_dl3")
            _rc2.caption(f"已生成 {Path(_rp).stat().st_size / 1024:.0f} KB。"
                         "数值全部来自本次求解结果，不含二次力学计算。")
        else:
            _rc2.caption("点击生成后可直接下载。计算书内的表格数据由求解结果"
                         "直接生成，与界面显示逐项一致。")

        # ---- 第三方复核：导出 ANSYS / Abaqus / MIDAS ----
        # 斜拉桥最怕的是「只有你自己的软件能算出这个下料索长」，所以成品必须能
        # 交给别人用别的软件复核。三个导出器吃的是同一个 canonical 模型（见
        # export_formats.to_canonical），字段映射只在一处定义。
        st.markdown("**第三方复核（导出给 ANSYS / Abaqus / MIDAS 核对）**")
        _ex1, _ex2 = st.columns([1.1, 2.0])
        if _ex1.button("导出三种格式", key="cb_export_btn"):
            try:
                from export_formats import export_all
                _res = export_all(_cbm, out_dir="out/cable/export", stem="cable")
                st.session_state["cb_export"] = _res
            except Exception as _e:                         # noqa: BLE001
                st.session_state.pop("cb_export", None)
                st.error(f"导出失败：{_e}")
        _exr = st.session_state.get("cb_export")
        if _exr:
            _ec = _ex2.columns(3)
            for _i, (_fmt, _lab) in enumerate((("ansys", "ANSYS (.ans)"),
                                               ("abaqus", "Abaqus (.inp)"),
                                               ("midas", "MIDAS (.mct)"))):
                _ep = Path(_exr["paths"][_fmt])
                if _ep.exists():
                    _ec[_i].download_button(_lab, data=_ep.read_bytes(),
                                            file_name=_ep.name,
                                            key=f"cb_ex_{_fmt}")
            for _w in _exr["warnings"]:
                st.caption(f"· {_w}")
            st.caption("导出的是**同一套节点/单元/实常数**：惯矩按「绕哪根轴」同名映射"
                       "（IY→IYY、IZ→IZZ、J→IXX），泊松比由 G 反算。"
                       "三格式均只建模、不含求解控制，荷载与边界随模型一起导出。")
        else:
            _ex2.caption("导出后可在第三方软件里独立建算，核对索力与线形。")

        with st.expander("查看导出的 .dat（含 *CABLE_BRIDGE 工况段）"):
            st.code(st.session_state.get("cb_dat", ""), language="text")
            st.code(_cb.get("stdout", ""), language="text")
    else:
        st.caption("调整参数后点求解。本区块输出的是**下料索长**与**制造线形**，"
                   "不是内力包络——两者用途不同。")
