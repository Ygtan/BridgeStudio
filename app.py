"""自然语言驱动有限元前处理 · Streamlit 原型（统一 ModelDoc 入口）。

C 方案改造后的界面结构：**一条输入，六种结构，两条内核链路**。

    ① 自然语言输入（单梁 / 拱桥 / 桁架桥 / 梁桥 / 三维梁格 / 斜拉桥）
    ② 参数面板（按当前类型的字段元数据自动渲染）
    ── 二维链路 ──────────────────────────────────────
    ③ 模型（梁：截面图 + 模型图；桥：节点单元简图）
    ④ 计算结果（梁：anastruct 草算；桥：指向⑤）
    ⑤ FEM-Eigen 二维求解与可视化（7 张 SVG）
    ── 三维链路（ui_3d.py）───────────────────────────
    ③④ 模型与求解（SolveResult：指标卡 / 工程校核 / 图 / 明细表 / 计算书）

与改造前的关键差别：
- 界面不持有 ModelSpec / ModelSpec3D / BridgeSpec，只持有 `ModelDoc`；
- "能不能草算""有没有互校""走哪个内核"由 `doc.capability()` 与
  `doc.solve()` 自述，界面里没有 `if 是梁格 / if 是斜拉桥`；
- 三维结果统一是 `SolveResult`（result_3d.py），界面/Word/CSV 共用一份。

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
from model_doc import DIM_LABELS, KIND_LABELS, ModelDoc
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
    ("三维 · 桁架", "空间桁架桥，跨度60米，8个节间，桁高6米，桥宽10米，桥面恒载100kN/m"),
    ("三维 · 梁格", "三跨连续箱梁，边跨30米中跨50米，梁高2.5米，箱宽12米，逐跨施工"),
    ("三维 · 斜拉桥", "空间单索面斜拉桥，主跨200米，边跨80米，塔高60米，每塔每侧12根索"),
    ("三维 · 地锚式悬索桥",
     "空间单索面地锚式悬索桥，主跨300米，矢跨比1/10，桥面恒载100kN/m"),
    ("三维 · 自锚式悬索桥",
     "空间单索面自锚式悬索桥，主跨300米，矢跨比1/10，桥面恒载100kN/m"),
    ("二维 · 斜拉桥", "双塔三跨斜拉桥，主跨200米，边跨80米，塔高60米，每塔每侧12根索"),
    ("二维 · 地锚式悬索桥",
     "地锚式悬索桥，主跨300米，矢跨比1/10，桥面恒载100kN/m"),
]

KIND_OPTIONS = ["自动识别", "单梁", "拱桥", "桁架桥", "梁桥", "斜拉桥", "悬索桥"]
DIM_OPTIONS = ["自动", "二维", "三维"]

st.set_page_config(page_title="NL-FE 前处理原型", layout="wide")
st.title("自然语言驱动有限元前处理 · 原型")
st.caption(
    "一条输入 → 自动识别 桥型（单梁 / 拱桥 / 桁架 / 梁桥 / 斜拉桥 / 悬索桥）"
    "× 维度（二维 / 三维）→ 统一 ModelDoc → .dat → FEM-Eigen（2D / 3D 双链路）"
    "求解与出图"
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


def _load_text(text: str, hint: str, dim_hint: str | None) -> None:
    """解析文本并整体替换当前模型。"""
    with st.spinner("解析中…"):
        new_doc, source, diag = ModelDoc.from_text(
            text, prev=st.session_state.doc, llm_config=llm_config, hint=hint,
            dim_hint=dim_hint,
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

_dim_sel = st.selectbox(
    "维度", DIM_OPTIONS, key="dim_hint",
    help="自动 = 按描述判定（默认二维；出现「三维/空间/梁格/偏载/扭转」等词转三维）。"
         "「双索面」不会生成第二索面。多数桥可简化二维，复杂空间结构选三维。",
)
_dim_hint: str | None = {"二维": "2d", "三维": "3d"}.get(_dim_sel)

# 示例按钮必须在 text_area「之前」：若在 text_area 实例化之后再赋值
# session_state.nl_input，widget 状态会在下一次运行把它覆盖掉。
st.write("示例（点击直接建模；也可改写下方输入框后点「解析并更新模型」）：")
# 示例多到 9 个，排成两行；一行 9 个按钮每个只有几十像素宽，字全被截断。
_clicked = None
_PER_ROW = 5
for _r0 in range(0, len(EXAMPLES), _PER_ROW):
    _ec = st.columns(_PER_ROW)
    for _j, (_label, _txt) in enumerate(EXAMPLES[_r0:_r0 + _PER_ROW]):
        if _ec[_j].button(_label, key=f"ex{_r0 + _j}", use_container_width=True):
            _clicked = _txt

if "nl_input" not in st.session_state:
    st.session_state.nl_input = ""

if _clicked is not None:
    st.session_state.nl_input = _clicked
    _load_text(_clicked, hint, _dim_hint)
    st.rerun()

_nl_text = st.text_area(
    "用中文描述你想建立的结构：", key="nl_input", height=80,
    placeholder="例如：跨度30米的简支钢梁，矩形截面高1.2米宽0.6米，承受20kN/m均布荷载；"
                "或：跨度100米的拱桥，矢高20米，9对吊杆，桥面20kN/m",
)

if st.button("解析并更新模型", type="primary"):
    if _nl_text.strip():
        _load_text(_nl_text.strip(), hint, _dim_hint)
        st.rerun()
    else:
        st.warning("请先输入一句描述。")

# 解析完成后统一取最新 doc（上面的 rerun 会回到这里）
doc: ModelDoc = st.session_state.doc
ver: int = st.session_state.doc_version

st.caption(
    f"识别结果：**{KIND_LABELS[doc.kind]}（{DIM_LABELS[doc.dimension]}）**"
    f"　｜　解析来源：**{st.session_state.source}**"
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
            # 默认仍是 %.3f（既有控件逐位不变）；E=2e11 / A=0.01 这类跨
            # 数量级的参数用字段自带的 %.6g，避免显示成 0.000 或长串数字，
            # 用户一碰就静默改口径。
            format=field.get("format", "%.3f"),
            key=wkey, help=field.get("help", ""),
        )
    elif ftype == "int":
        # 和 float 一样支持 `default`：新增的求解控制量（荷载步 / 迭代上限）
        # 若没有默认值就会被顶到 min 上，界面一打开就把 .dat 口径改了。
        raw = params.get(key)
        val = int(raw) if raw is not None else int(field.get("default", field["min"]))
        params[key] = int(col.number_input(
            field["label"], value=val,
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
    elif ftype == "text":
        # 一整组规范参数（分项预应力损失表）用一行 JSON 表达，比铺 22 个
        # number_input 更可读；空串 = 不启用，既有 .dat 口径逐位不变。
        params[key] = col.text_input(
            field["label"],
            value=str(params.get(key, field.get("default", "")) or ""),
            key=wkey, help=field.get("help", ""))


_prefix = f"p{doc.kind}{ver}"
for _row in doc.param_rows():
    _cols = st.columns(len(_row))
    for _col, _f in zip(_cols, _row):
        _render_field(_col, _f, _p, _prefix)

# 逐跨张拉是等效荷载法的施工阶段量，不是规范意义上的预应力设计验算。
# 口径必须就地写清，否则用户会把"总损失系数"当成 JTG 3362 的分项损失。
if (doc.builder_key == "beam_bridge" and _p.get("staged")
        and float(_p.get("stage_tension_kN") or 0.0) > 0.0):
    st.caption(
        "逐跨张拉：每个施工阶段给该跨写一条 `*STAGE_PRESTRESS`，内核强制走"
        "等效荷载法（EQUIV）。P 为张拉控制力，有效预加力 Pe = P × loss；"
        "loss 是**总损失系数**（1.0 = 不扣损失），不是 JTG 3362 的摩阻/锚固/"
        "松弛/收缩徐变分项损失。钢束按直线筋 + 端部偏心处理，不做筋-混凝土"
        "协同（TENDON）；各阶段按增量累加进成桥态，过程量不构成规范合格判定。")

# 3D 梁格逐跨张拉走的是 *STAGE 行内 PRESTRESS=，与 2D 的 *STAGE_PRESTRESS
# 不是同一个段；口径同样要就地写清，避免把「全宽张拉力」当成每根束的力，
# 或把总损失系数当成规范分项损失。
if (doc.builder_key == "grillage3d" and _p.get("staged")
        and float(_p.get("stage_tension_kN") or 0.0) > 0.0):
    st.caption(
        "3D 梁格逐跨张拉：每个施工阶段给该跨的每条纵梁线各写一条 "
        "`*STAGE ... PRESTRESS=`（等效荷载法）。「逐跨张拉力」是**全宽总量**，"
        "按纵梁线条数均分到各束；有效预加力 Pe = P × loss，loss 是总损失系数"
        "（1.0 = 不扣损失），不是 JTG 3362 的 σl1~σl6 分项损失。矢高 s=0 是"
        "直线筋，s≠0 走曲线筋（跨内上拱）。不做筋-混凝土协同、次内力与"
        "截面应力验算，过程量不构成规范合格判定。")

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

# 三维模型：一条独立但**同构**的路径——参数仍是 doc.params，求解仍是
# "导出 .dat → 内核 → 结果"，只是内核换成三维的、结果换成 SolveResult。
# 界面层不再写 if 梁格 / if 斜拉桥，只问 doc.kind 与 capability()。
if doc.is_3d:
    import ui_3d

    ui_3d.render(doc)
    st.stop()          # 三维没有 anastruct 草算与 2D 七张 SVG，后面的区块不适用

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
st.subheader("⑤ FEM-Eigen 二维求解与可视化")

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

# ---------------------------------------------------------------- ⑥ 施工阶段 + 规范组合
# 档二：二维桥型的施工阶段正装 + 车道活载 + JTG D60 组合（solve_2d 链）。
# 只有桥型（非单梁）才进这里；三维桥走 ui_3d 的 SolveResult，不经过本段。
if doc.kind != "beam" and _cap.get("solve2d"):
    st.subheader("⑥ 施工阶段与规范组合（JTG D60）")
    st.caption(
        "二维施工正装（`example_stage.exe`）→ 成桥态累计内力 + 车道活载影响线"
        "包络（`example_influence.exe`）→ 基本/频遇/准永久三种组合。"
    )
    if not (doc.params.get("staged") or doc.params.get("lane")):
        st.info("当前模型未开「逐跨施工」或「车道活载」，组合只有恒载项。可在②中勾选。")

    # 2D 自振 / 屈曲是 opt-in 求解选项（不是模型参数，不进 .dat）：
    # 只读 example_modal.exe 写出的 .modal.txt，缺 exe / 缺段如实写明。
    _modal2d_on = st.checkbox(
        "同时输出 2D 自振频率与线性屈曲（需 example_modal.exe，默认关闭）",
        value=False, key="modal2d_opt",
        help="不重算 K/M；默认只给频率与 λ。若要周期 T / 振型 / 两向参与质量，"
             "另勾下方「2D 模态明细」；内核没写出的字段一律不补。"
             "缺 exe 或缺段只在结果里写中文说明，不影响静力与活载。")
    if not _cap.get("modal2d"):
        st.caption("未编译 example_modal.exe；勾选也只会得到中文「做不了」说明。")
    # 第二层 opt-in：必须叠在 2D 自振开关之上，不单独跑内核。
    _modal2d_detail_on = False
    if _modal2d_on:
        _modal2d_detail_on = st.checkbox(
            "同时输出 2D 模态明细：周期 T / X·Y 两向参与质量 / 节点振型（默认关闭）",
            value=False, key="modal2ddetail_opt",
            help="给 example_modal.exe 追加 --modal-detail，由 FE 内核写出 "
                 "[MODAL2D] 段；Python 只解析、不现算 T、不重算 K/M。"
                 "BASIS=xy：2D 没有出平面自由度，Z 向参与质量不存在（不是 0），"
                 "θz 只作振型展示、不设参与质量。")
    else:
        st.caption("2D 模态明细需先勾选上方「2D 自振频率与线性屈曲」。")

    # 2D 截面应力验算同为 opt-in：只做应力；承载力 Mu / Vu 由下方
    # 另一个独立 opt-in 开关负责，两者边界措辞按 res.capacity 状态选择。
    # 模型未显式给 grade/kind/AW/板厚时应力章整章拒绝。
    _strength2d_on = st.checkbox(
        "同时输出 2D 截面应力验算（默认关闭，承载力另有开关）",
        value=False, key="strength2d_opt",
        help="只从成桥态内力与 JTG D60 组合取值，不解析内核「单元应力」段。"
             "需模型显式给出材料等级 grade、构件类型 kind、腹板面积 AW"
             "与钢材板厚；缺任一项会在结果里整章写明原因，不补零。")
    if not _cap.get("strength2d"):
        st.caption("2D 求解链不可用；勾选也只会得到中文「做不了」说明。")

    # 2D 分项预应力损失明细（第十次 opt-in）：参数显式挂在 BridgeSpec，
    # 不进 DEFAULTS / FIELDS；doc.build() 每次 rerun 会重建 model，故此处重挂。
    # 留空 / 非法 JSON 一律中文说明，不静默兜底、不猜参数、不补零。
    _loss2d_on = st.checkbox(
        "同时输出 2D 分项预应力损失明细（默认关闭，需填参数）",
        value=False, key="loss2ddetail_opt",
        help="[PRESTRESSLOSS2D] 段由 FE 内核写出，Python 只解析、不重算 σl1~σl6。"
             "2D 没有阶段维度，段里不设 STAGE 占位；σl3/σl5/σl6 不进 σpe 与 Pe。"
             "参数留空只会得到中文「不做」说明，不补零。")
    _loss2d_cfg = ""
    if _loss2d_on:
        _loss2d_cfg = st.text_area(
            "2D 分项损失参数 JSON（loss 表 + tendon 束；留空 = 不启用）",
            value="", key="loss2d_cfg", height=130,
            help="格式：{\"loss\": {22 键参数表}, \"tendon\": "
                 "{\"elems\": [1,2,3,4], \"P\": 1500000, \"F\": 0.2}}。"
                 "loss 对应 JTG 3362 §6.2 的 *PRESTRESS_LOSS 表；tendon 是"
                 "非阶段 *PRESTRESS 束，elems / P 必须显式给出。两者都会写进"
                 " .dat，默认留空时一个字节不多写。")
    _loss2d_supported = hasattr(doc.model, "prestress_losses")
    if _loss2d_supported:
        # build() 每次 rerun 重建 model，清空后再按本次控件值重挂。
        doc.model.prestress_losses = {}
        doc.model.prestresses = []
    if _loss2d_on and not _loss2d_supported:
        st.info("当前模型不是 BridgeSpec，2D 分项损失明细不可用；"
                "本次只给中文「不做」说明。")
    elif _loss2d_on and not (_loss2d_cfg or "").strip():
        st.info("未填 2D 分项损失参数；本次求解只给中文「不做」说明，不补零。")
    elif _loss2d_on:
        try:
            import json
            from bridge_spec import Prestress2D, PrestressLoss2D

            _cfg = json.loads(_loss2d_cfg)
            if (not isinstance(_cfg, dict)
                    or not isinstance(_cfg.get("loss"), dict)
                    or not isinstance(_cfg.get("tendon"), dict)):
                raise ValueError("JSON 必须含 loss 与 tendon 两个对象")
            _tendon = Prestress2D(**_cfg["tendon"])
            _tendon.loss_ref = 1
            doc.model.prestress_losses = {1: PrestressLoss2D(**_cfg["loss"])}
            doc.model.prestresses = [_tendon]
            _loss_errs = [e for e in doc.model.validate_model()
                          if "PRESTRESS" in e]
            if _loss_errs:
                raise ValueError("；".join(_loss_errs))
        except Exception as _le:
            doc.model.prestress_losses = {}
            doc.model.prestresses = []
            st.error(f"2D 分项损失参数无效：{_le}")

    # 2D 内层 Newton 逐迭代过程量（第十一次 opt-in）：不需要新模型参数，
    # 但必须模型本身显式声明 *ANALYSIS TYPE=NONLINEAR；线性模型只给中文
    # 「不做」，绝不为了出表而把分析类型偷偷改成非线性。
    _newton2d_on = st.checkbox(
        "同时输出 2D 内层 Newton 逐迭代过程量（默认关闭，需 NONLINEAR 模型）",
        value=False, key="newton2ddetail_opt",
        help="[NEWTON2D] 段由 FE 内核留痕写出，Python 只解析、不重算 "
             "K·ΔU=R。2D 没有 REFACT、没有 TOLF/TOLD 拆分、没有阶段占位；"
             "第十三次起 [LENGTH2D] 平面索长闭合、第十四次起 [OPT2D] 平面"
             "索力优化都由各自单独开关接入（2D 独立段）；[OPT]（3D 三层迭代 "
             "+ 体系守卫）那一层仍没有对应力学；本开关只覆盖内层 Newton。"
             "线性模型只会得到中文「不做」说明。")

    # 2D 承载力 Mu / Vu（第十二次 opt-in，纯 BS 侧）：配筋是
    # Python-only 契约，不写进 .dat、不进 DEFAULTS / FIELDS；doc.build()
    # 每次 rerun 重建 model，故先清空再按本次控件值重挂，绝不猜配筋。
    _capacity2d_on = st.checkbox(
        "同时输出 2D 正截面抗弯 / 斜截面抗剪承载力 Mu、Vu（默认关闭，需填配筋）",
        value=False, key="capacity2d_opt",
        help="只走 flex 链：效应取基本组合逐端最不利 M_pos / M_neg / Vd，"
             "复用 section_strength.check_capacity。压弯 / 轴压在 2D 仍不做"
             "（组合轴力 N 只有恒载单值、无活载轴力包络）。"
             "参数留空或模型无混凝土 grade 时整章中文拒绝，不补零。")
    _capacity2d_cfg = ""
    if _capacity2d_on:
        _capacity2d_cfg = st.text_area(
            "2D 配筋 JSON（按实常数类型 id 给 RebarSpec2D；留空 = 不启用）",
            value="", key="capacity2d_cfg", height=150,
            help='格式：{"1": {"h": 2000, "b": 300, "As": 4000, '
                 '"bf": 1200, "hf": 200, "as_": 60, "Asp": 0, '
                 '"asp": 40, "Asv": 200, "sv": 150, '
                 '"grade_rebar": "HRB400", "grade_stirrup": "HRB400"}}。'
                 '尺寸 mm、面积 mm²，几何与配筋全部必填；不写进 .dat。')
    _cap2d_supported = hasattr(doc.model, "elem_types")
    if _cap2d_supported:
        for _t2 in doc.model.elem_types:
            if hasattr(_t2, "rebar"):
                _t2.rebar = None
    if _capacity2d_on and not _cap2d_supported:
        st.info("当前模型不是 BridgeSpec，2D 承载力不可用；"
                "本次只给中文「不做」说明。")
    elif _capacity2d_on and not (_capacity2d_cfg or "").strip():
        st.info("未填 2D 配筋参数；本次求解只给中文「不做」说明，不补零。")
    elif _capacity2d_on:
        try:
            import json
            from bridge_spec import RebarSpec2D

            _cfg2 = json.loads(_capacity2d_cfg)
            if not isinstance(_cfg2, dict):
                raise ValueError("JSON 必须是 {实常数类型 id: RebarSpec2D} 对象")
            _by_id2 = {int(t.id): t for t in doc.model.elem_types}
            for _k2, _v2 in _cfg2.items():
                _tid2 = int(_k2)
                if _tid2 not in _by_id2:
                    raise ValueError(f"实常数类型 {_tid2} 不存在")
                if not isinstance(_v2, dict):
                    raise ValueError(f"类型 {_tid2} 的配筋必须是对象")
                _by_id2[_tid2].rebar = RebarSpec2D(**_v2)
            _cap2d_errs = [e for e in doc.model.validate_model()
                           if "RebarSpec2D" in e]
            if _cap2d_errs:
                raise ValueError("；".join(_cap2d_errs))
        except Exception as _ce:
            for _t2 in doc.model.elem_types:
                if hasattr(_t2, "rebar"):
                    _t2.rebar = None
            st.error(f"2D 配筋参数无效：{_ce}")

    # 2D 平面索长闭合 [LENGTH2D]（第十三次 opt-in，FE 内核新造力学）：
    # 目标索力 T* 必须由用户显式给出，程序不猜、不改分析类型、不偷写段。
    # 契约挂上后 to_dat() 才会写 *CABLE_LENGTH2D，默认路径逐位不变。
    _length2d_on = st.checkbox(
        "同时输出 2D 平面索长闭合过程量（默认关闭，需填逐索目标索力 T*）",
        value=False, key="length2ddetail_opt",
        help="[LENGTH2D] 段由 FE 内核新造的 2D 力学写出（无垂度线性杆 + "
             "中心有限差分 Jacobian + LM），Python 只解析、不重算。"
             "需模型显式挂 BridgeSpec.length_2d；[OPT]（3D 三层迭代 + 体系"
             "守卫）那一层仍不做、不造占位，第十四次另立的 [OPT2D] 平面"
             "索力优化是独立 2D 段、由单独开关接入。留空或目标单元不是 "
             "CABLE 时只给中文「不做」说明，不补零。")
    _length2d_cfg = ""
    if _length2d_on:
        _length2d_cfg = st.text_area(
            "2D 索长闭合 JSON（目标索力 T*，单位 N；留空 = 不启用）",
            value="", key="length2d_cfg", height=110,
            help='格式：{"targets": {"<CABLE 单元号>": 1000000.0}, '
                 '"tol_t": 1e-6, "max_iter": 20}。'
                 '目标索力 T* 受拉为正、必须 > 0。只有显式填写才在 .dat '
                 '写 *CABLE_LENGTH2D，默认 .dat 逐位不变。')
    if hasattr(doc.model, "length_2d"):
        doc.model.length_2d = None   # 每次 rerun 先清空，绝不沿用上次契约
    if _length2d_on and not (_length2d_cfg or "").strip():
        st.info("未填 2D 索长闭合目标索力；本次求解只给中文「不做」说明，"
                "不猜 T*、不补零。")
    elif _length2d_on and not hasattr(doc.model, "length_2d"):
        st.info("当前模型不是 BridgeSpec，2D 索长闭合不可用；"
                "本次只给中文「不做」说明。")
    elif _length2d_on:
        try:
            import json
            from bridge_spec import LengthSpec2D

            _lcfg = json.loads(_length2d_cfg)
            if not isinstance(_lcfg, dict):
                raise ValueError("JSON 必须是对象")
            _tg = _lcfg.get("targets")
            if not isinstance(_tg, dict) or not _tg:
                raise ValueError("targets 必须是非空的 {单元号: T*} 对象")
            _tg2 = {int(k): float(v) for k, v in _tg.items()}
            doc.model.length_2d = LengthSpec2D(
                targets=_tg2,
                tol_t=float(_lcfg.get("tol_t", 1e-6)),
                max_iter=int(_lcfg.get("max_iter", 20)))
            _lgerrs = [e for e in doc.model.validate_model()
                       if "2D 索长闭合" in e]
            if _lgerrs:
                raise ValueError("；".join(_lgerrs))
        except Exception as _le:
            doc.model.length_2d = None
            st.error(f"2D 索长闭合契约无效：{_le}")

    # 2D 平面索力优化 [OPT2D]（第十四次 opt-in，FE 内核新造力学）：
    # 索力箱 TMIN/TMAX、逐 CABLE 初始名义索力 T0、登记 BEAM 端弯矩都必须
    # 由用户显式给出，程序不猜、不改分析类型、不偷写段。契约挂上后
    # to_dat() 才会写 *CABLE_OPT2D，默认路径逐位不变。
    _opt2d_on = st.checkbox(
        "同时输出 2D 平面索力优化过程量（默认关闭，需填显式索力箱与梁端弯矩登记）",
        value=False, key="opt2ddetail_opt",
        help="[OPT2D] 段由 FE 内核新造的 2D 力学写出（MINMAXM + 显式索力箱 + "
             "中心有限差分 Jacobian + IRLS + 投影梯度），Python 只解析、不重算。"
             "需模型显式挂 BridgeSpec.opt_2d；min max|M| 只针对用户显式登记的 "
             "BEAM 端弯矩，不自动识别主梁。不是 3D 的 J=M·D 闭式影响矩阵，"
             "也不等于 3D [OPT]→[LENGTH]→[NEWTON] 三层迭代。留空或 TMIN/TMAX "
             "不合法时只给中文「不做」说明，不补零、不猜参数。")
    _opt2d_cfg = ""
    if _opt2d_on:
        _opt2d_cfg = st.text_area(
            "2D 索力优化 JSON（索力箱 + 逐索 T0 + 登记梁端；留空 = 不启用）",
            value="", key="opt2d_cfg", height=130,
            help='格式：{"t_min": 200000.0, "t_max": 1500000.0, '
                 '"targets": {"<CABLE 单元号>": 1000000.0}, '
                 '"moments": [[<BEAM 单元号>, <端号 1|2>]], '
                 '"tol": 1e-7, "max_iter": 120, "irls": 4, '
                 '"round_tol": 1e-5}。'
                 'TMIN/TMAX 必须显式给且 0 < TMIN < TMAX；T0 受拉为正、>0。'
                 '只有显式填写才在 .dat 写 *CABLE_OPT2D，默认 .dat 逐位不变。')
    if hasattr(doc.model, "opt_2d"):
        doc.model.opt_2d = None   # 每次 rerun 先清空，绝不沿用上次契约
    if _opt2d_on and not (_opt2d_cfg or "").strip():
        st.info("未填 2D 索力优化契约；本次求解只给中文「不做」说明，"
                "不猜索力箱 / T0 / 登记端、不补零。")
    elif _opt2d_on and not hasattr(doc.model, "opt_2d"):
        st.info("当前模型不是 BridgeSpec，2D 索力优化不可用；"
                "本次只给中文「不做」说明。")
    elif _opt2d_on:
        try:
            import json
            from bridge_spec import OptSpec2D

            _ocfg = json.loads(_opt2d_cfg)
            if not isinstance(_ocfg, dict):
                raise ValueError("JSON 必须是对象")
            if "t_min" not in _ocfg or "t_max" not in _ocfg:
                raise ValueError("必须显式给出 t_min / t_max（0 < TMIN < TMAX）")
            _otg = _ocfg.get("targets")
            if not isinstance(_otg, dict) or not _otg:
                raise ValueError("targets 必须是非空的 {CABLE 单元号: T0} 对象")
            _om = _ocfg.get("moments")
            if not isinstance(_om, list) or not _om:
                raise ValueError("moments 必须是非空的 [[BEAM 单元号, 端号]] 列表")
            _otg2 = {int(k): float(v) for k, v in _otg.items()}
            _om2 = [(int(p[0]), int(p[1])) for p in _om]
            doc.model.opt_2d = OptSpec2D(
                targets=_otg2, moments=_om2,
                t_min=float(_ocfg["t_min"]), t_max=float(_ocfg["t_max"]),
                tol=float(_ocfg.get("tol", 1e-7)),
                max_iter=int(_ocfg.get("max_iter", 120)),
                irls=int(_ocfg.get("irls", 4)),
                round_tol=float(_ocfg.get("round_tol", 1e-5)))
            _operrs = [e for e in doc.model.validate_model()
                       if "2D 索力优化" in e]
            if _operrs:
                raise ValueError("；".join(_operrs))
        except Exception as _oe:
            doc.model.opt_2d = None
            st.warning(f"2D 索力优化契约无效：{_oe}")
    _dat_now_2d = doc.to_dat()
    if st.button("运行施工阶段 + 规范组合求解", type="primary"):
        _outdir = Path("out/gui_2d")
        _outdir.mkdir(parents=True, exist_ok=True)
        with st.spinner("二维施工阶段 + 车道活载求解中…"):
            try:
                _res2d = doc.solve(out_dir=_outdir, dat_name="gui_2d.dat",
                                   modal_2d=_modal2d_on,
                                   strength_2d=_strength2d_on,
                                   modal_2d_detail=_modal2d_detail_on,
                                   loss_2d_detail=_loss2d_on,
                                   newton_2d_detail=_newton2d_on,
                                   capacity_2d=_capacity2d_on,
                                   length_2d_detail=_length2d_on,
                                   opt_2d_detail=_opt2d_on)
                st.session_state.solve2d = {"dat_text": _dat_now_2d,
                                            "modal2d": _modal2d_on,
                                            "modal2ddetail": _modal2d_detail_on,
                                            "strength2d": _strength2d_on,
                                            "loss2ddetail": _loss2d_on,
                                            "loss2dcfg": _loss2d_cfg,
                                            "newton2ddetail": _newton2d_on,
                                            "capacity2d": _capacity2d_on,
                                            "capacity2dcfg": _capacity2d_cfg,
                                            "length2ddetail": _length2d_on,
                                            "length2dcfg": _length2d_cfg,
                                            "opt2ddetail": _opt2d_on,
                                            "opt2dcfg": _opt2d_cfg,
                                            "res": _res2d}
            except Exception as _e:
                st.session_state.solve2d = None
                st.error(f"二维求解失败：{_e}")

    _s2d = st.session_state.get("solve2d")
    if _s2d and (_s2d.get("dat_text") != _dat_now_2d
                 or bool(_s2d.get("modal2d", False)) != _modal2d_on
                 or bool(_s2d.get("modal2ddetail", False)) != _modal2d_detail_on
                 or bool(_s2d.get("strength2d", False)) != _strength2d_on
                 or bool(_s2d.get("loss2ddetail", False)) != _loss2d_on
                 or str(_s2d.get("loss2dcfg", "")) != _loss2d_cfg
                 or bool(_s2d.get("newton2ddetail", False)) != _newton2d_on
                 or bool(_s2d.get("capacity2d", False)) != _capacity2d_on
                 or str(_s2d.get("capacity2dcfg", "")) != _capacity2d_cfg
                 or bool(_s2d.get("length2ddetail", False)) != _length2d_on
                 or str(_s2d.get("length2dcfg", "")) != _length2d_cfg
                 or bool(_s2d.get("opt2ddetail", False)) != _opt2d_on
                 or str(_s2d.get("opt2dcfg", "")) != _opt2d_cfg):
        st.warning("模型参数或求解开关已修改，下方结果来自上一次求解，已隐藏。请重新点上方按钮。")
        _s2d = None
    if _s2d:
        _r2d = _s2d["res"]
        _w = _r2d.max_vertical_deflection()
        _env_m = max((v.get("Mmax", 0.0) for v in _r2d.beam.values()), default=0.0)
        _c1, _c2, _c3, _c4 = st.columns(4)
        _c1.metric("成桥态最大挠度", f"{_w[1] * 1000:.2f} mm" if _w else "—")
        _c2.metric("施工阶段最大弯矩", f"{_env_m / 1e3:.1f} kN·m")
        _c3.metric("单元数", f"{_r2d.stats.get('elements', 0)}")
        _c4.metric("施工阶段数", f"{_r2d.stats.get('stages_solved', _r2d.stats.get('stages', 0))}")
        st.caption(f"内核：`{_r2d.solver}`")

        _combos = _r2d.combinations()
        if _combos:
            _rows = []
            for _c in _combos:
                _my = _c.worst("My")
                _n = _c.worst("N")
                _mycell = "—" if _my is None else f"{_my[1]/1e3:,.0f}（单元 {_my[0][0]}·{_my[0][1]}）"
                _ncell = "—" if _n is None else f"{_n[1]/1e3:,.0f}"
                _rows.append([_c.spec.name, _c.spec.expr, _mycell, _ncell,
                              "含活载" if _c.has_live else "仅恒载"])
            st.markdown(
                "| 组合 | 表达式 | 控制弯矩 My (kN·m) | 控制轴力 N (kN) | 活载 |\n"
                "|---|---|---|---|---|\n"
                + "\n".join(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} |"
                            for r in _rows))
        else:
            st.info("成桥态内力为空，无法组合（内核未产出）。")
        if _modal2d_on:
            from modal_2d import status_text

            _m2 = getattr(_r2d, "modal", None) or {}
            _mt = getattr(_r2d, "modal_tables", lambda: {})()
            st.markdown("**2D 自振特性与线性屈曲**")
            for _t in _mt.values():
                st.markdown(
                    "| " + " | ".join(_t["headers"]) + " |\n|"
                    + "---|" * len(_t["headers"]) + "\n"
                    + "\n".join("| " + " | ".join(r) + " |" for r in _t["rows"]))
                st.caption(_t["note"])
            if _m2.get("status") != "ok" or "buckling_table" not in _mt:
                st.warning(status_text(_m2))
            _c2 = _m2.get("consistency") or {}
            if _c2.get("status") == "mismatch":
                st.error(str(_c2.get("note", "")))
            elif _c2.get("note"):
                st.caption(str(_c2["note"]))
            if _modal2d_detail_on:
                from modal_2d import detail_status_text

                _d2 = _m2.get("detail") or {"status": "no_detail_section"}
                if _d2.get("status") != "ok":
                    st.warning(detail_status_text(_d2))
                else:
                    st.caption(detail_status_text(_d2))
        if _strength2d_on:
            from strength_2d import capacity_note_for
            from strength_2d import status_text as strength_status_text

            _s2 = getattr(_r2d, "strength", None) or {}
            _st = getattr(_r2d, "strength_tables", lambda: {})()
            st.markdown("**2D 截面应力验算**")
            for _t in _st.values():
                st.markdown(
                    "| " + " | ".join(_t["headers"]) + " |\n|"
                    + "---|" * len(_t["headers"]) + "\n"
                    + "\n".join("| " + " | ".join(r) + " |" for r in _t["rows"]))
                st.caption(_t["note"])
            if _s2.get("status") != "ok" or not _st:
                st.warning(strength_status_text(_s2))
            st.caption(capacity_note_for(getattr(_r2d, "capacity", None)))
        if _loss2d_on:
            from prestress_loss_2d import status_text as loss2d_status_text

            _l2 = getattr(_r2d, "prestress_loss", None) or {}
            _lt = getattr(_r2d, "prestress_loss_tables", lambda: {})()
            st.markdown("**2D 分项预应力损失明细**")
            for _t in _lt.values():
                st.markdown(
                    "| " + " | ".join(_t["headers"]) + " |\n|"
                    + "---|" * len(_t["headers"]) + "\n"
                    + "\n".join("| " + " | ".join(r) + " |" for r in _t["rows"]))
                st.caption(_t["note"])
            if _l2.get("status") != "ok":
                st.warning(loss2d_status_text(_l2))
            else:
                st.caption(loss2d_status_text(_l2))
        if _newton2d_on:
            from newton_2d import status_text as newton2d_status_text

            _nt2 = getattr(_r2d, "newton", None) or {}
            _ntt = getattr(_r2d, "newton_tables", lambda: {})()
            st.markdown("**2D 内层 Newton 逐迭代过程量**")
            for _t in _ntt.values():
                st.markdown(
                    "| " + " | ".join(_t["headers"]) + " |\n|"
                    + "---|" * len(_t["headers"]) + "\n"
                    + "\n".join("| " + " | ".join(r) + " |" for r in _t["rows"]))
                st.caption(_t["note"])
            if _nt2.get("status") != "ok":
                st.warning(newton2d_status_text(_nt2))
            else:
                st.caption(newton2d_status_text(_nt2))
        if _capacity2d_on:
            from capacity_2d import status_text as capacity2d_status_text

            _cap2 = getattr(_r2d, "capacity", None) or {}
            _capt = getattr(_r2d, "capacity_tables", lambda: {})()
            st.markdown("**2D 正截面抗弯 / 斜截面抗剪承载力**")
            for _t in _capt.values():
                st.markdown(
                    "| " + " | ".join(_t["headers"]) + " |\n|"
                    + "---|" * len(_t["headers"]) + "\n"
                    + "\n".join("| " + " | ".join(r) + " |" for r in _t["rows"]))
                st.caption(_t["note"])
            if _cap2.get("status") != "ok":
                st.warning(capacity2d_status_text(_cap2))
            else:
                st.caption(capacity2d_status_text(_cap2))
        if _length2d_on:
            from length_2d import status_text as length2d_status_text

            _lg2 = getattr(_r2d, "length", None) or {}
            _lgt = getattr(_r2d, "length_tables", lambda: {})()
            st.markdown("**2D 平面索长闭合（无应力下料长度）**")
            for _t in _lgt.values():
                st.markdown(
                    "| " + " | ".join(_t["headers"]) + " |\n|"
                    + "---|" * len(_t["headers"]) + "\n"
                    + "\n".join("| " + " | ".join(r) + " |" for r in _t["rows"]))
                st.caption(_t["note"])
            if _lg2.get("status") != "ok":
                st.warning(length2d_status_text(_lg2))
            else:
                st.caption(length2d_status_text(_lg2))
        if _opt2d_on:
            from opt_2d import status_text as opt2d_status_text

            _op2 = getattr(_r2d, "opt", None) or {}
            _optbl = getattr(_r2d, "opt_tables", lambda: {})()
            st.markdown("**2D 平面索力优化（MINMAXM · 过程量）**")
            for _t in _optbl.values():
                st.markdown(
                    "| " + " | ".join(_t["headers"]) + " |\n|"
                    + "---|" * len(_t["headers"]) + "\n"
                    + "\n".join("| " + " | ".join(r) + " |" for r in _t["rows"]))
                st.caption(_t["note"])
            if _op2.get("status") != "ok":
                st.warning(opt2d_status_text(_op2))
            else:
                st.caption(opt2d_status_text(_op2))
        for _d in _r2d.diagnostics:
            st.caption(f"· {_d}")

        # 2D 同源四出口计算书（opt-in）：默认不勾，勾选后才出现按钮。
        # 与 3D 的 _report_block 同一套降级方式：pdf=False 出三件套，
        # PDF 单独打印，失败显式中文告警且保留另外三个下载按钮。
        _report2d_on = st.checkbox(
            "生成 2D 同源计算书四出口（HTML / DOCX / CSV / PDF，默认关闭）",
            value=False, key="report2d_opt",
            help="同一份 Report 中间结构出四种格式，数值与上方结果同源；"
                 "PDF 需本机 Edge / Chrome，失败只降级 PDF 这一个出口。"
                 "2D 报告不含振型与结构云图；承载力 Mu / Vu 只在勾选"
                 "「2D 正截面抗弯 / 斜截面抗剪承载力」并填了配筋后出现，"
                 "压弯 / 轴压仍不做；平面索长闭合章只在勾选并填了逐索"
                 "目标索力 T* 后出现，第十四次新增的平面索力优化章只在"
                 "勾选并填了显式索力箱后出现；[OPT]（3D 三层迭代 + 体系"
                 "守卫）那一层仍不做。")
        if _report2d_on and st.button("生成 2D 四出口计算书",
                                      key="report2d_btn"):
            from report_2d import build_and_write
            from report_3d import PDFExportError, write_pdf

            _rdir2 = Path("out/gui_2d/report2d")
            try:
                _rep2, _paths2 = build_and_write(
                    _r2d, doc.model, _rdir2, stem="report2d", pdf=False)
                _pdf_err2 = None
                try:
                    _paths2["pdf"] = write_pdf(_rep2, _rdir2 / "report2d.pdf")
                except PermissionError:
                    _paths2["pdf"] = write_pdf(
                        _rep2, _rdir2 / f"report2d_{_r2d.fingerprint}.pdf")
                except PDFExportError as _e:
                    _pdf_err2 = str(_e)
                st.session_state["report2d"] = {
                    "fingerprint": _r2d.fingerprint,
                    "paths": {k: str(v) for k, v in _paths2.items()},
                    "pdf_error": _pdf_err2,
                }
            except Exception as _e:
                st.session_state.pop("report2d", None)
                st.error(f"2D 计算书生成失败：{_e}")
        _got2 = st.session_state.get("report2d")
        if _got2:
            if _got2.get("fingerprint") != _r2d.fingerprint:
                st.warning("2D 计算书是上一次求解的结果，模型已变，请重新生成。")
            else:
                _dl_cols = st.columns(4)
                for _i, (_k2, _lbl2) in enumerate(
                        (("html", "HTML"), ("docx", "Word"),
                         ("zip", "CSV 数据包"), ("pdf", "PDF 计算书"))):
                    _p2 = Path(_got2["paths"].get(_k2, ""))
                    if _p2.is_file():
                        _dl_cols[_i].download_button(
                            f"下载 {_lbl2}", data=_p2.read_bytes(),
                            file_name=_p2.name, key=f"report2d_dl_{_k2}")
                if _got2.get("pdf_error"):
                    st.warning(
                        f"PDF 计算书未生成：{_got2['pdf_error']}；"
                        "其余三份可下载，没有生成占位 PDF。")


