"""自然语言 → 结构化模型 (ModelSpec)。

两层实现：
1. 规则解析（默认，无需任何 API key 即可运行）——覆盖本 demo 范围：
   跨度、跨数、支座、4 种截面及尺寸、均布/集中荷载、材料。
2. 可选 LLM 解析：若配置了 DEEPSEEK_API_KEY，自动调用 DeepSeek（OpenAI 兼容）
   做结构化抽取，失败则回退到规则解析。

另外支持"自然语言迭代修改"：在已有 spec 基础上用中文微调尺寸/荷载。
"""

from __future__ import annotations

import re

from model_spec import Load, ModelSpec, Section, Support

# ---------- 数值与单位 ----------
# 单位匹配元组：长单位放前面，避免 "m" 误匹配 "mm"
UNIT = r"(毫米|mm|厘米|cm|米|m|M)?"

def _to_m(val: float, unit: str | None) -> float:
    unit = (unit or "").lower()
    if "mm" in unit or "毫" in unit:
        return val / 1000.0
    if "cm" in unit or "厘" in unit:
        return val / 100.0
    return val  # 默认按 m


# ---------- 各字段抽取 ----------
def extract_length(text: str) -> float | None:
    for p in [
        r"(?:跨度|跨径|总长|总长度|长度|桥长|梁长|长)[\s:：为约]*?([0-9]+(?:\.[0-9]+)?)\s*(毫米|mm|厘米|cm|米|m|M)?",
    ]:
        m = re.search(p, text)
        if m:
            return _to_m(float(m.group(1)), m.group(2))
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(毫米|mm|厘米|cm|米|m|M)", text)
    if m:
        return _to_m(float(m.group(1)), m.group(2))
    return None


def extract_spans(text: str) -> int:
    m = re.search(r"([0-9]+)\s*跨", text)
    if m:
        return max(1, min(6, int(m.group(1))))
    for ch, n in {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}.items():
        if ch + "跨" in text:
            return n
    if "连续" in text:
        return 2
    return 1


def extract_section(text: str) -> dict:
    shape = "rectangular"
    if re.search(r"工字|工形|工字钢", text):
        shape = "i_beam"
    elif re.search(r"箱|箱梁|箱形", text):
        shape = "box"
    elif re.search(r"圆|圆柱", text):
        shape = "circular"
    elif re.search(r"矩形|长方", text):
        shape = "rectangular"

    sec: dict = {"shape": shape}
    if shape == "circular":
        m = re.search(r"直径[\s:：为]*?([0-9]+(?:\.[0-9]+)?)\s*(毫米|mm|厘米|cm|米|m|M)?", text)
        d = _to_m(float(m.group(1)), m.group(2)) if m else 1.0
        sec["h"], sec["b"] = d, d
        return sec

    h = b = None
    mh = re.search(r"(?:高|高度|截面高|深|深度)[\s:：为]*?([0-9]+(?:\.[0-9]+)?)\s*(毫米|mm|厘米|cm|米|m|M)?", text)
    if mh:
        h = _to_m(float(mh.group(1)), mh.group(2))
    mh2 = re.search(r"h[\s:：=]*?([0-9]+(?:\.[0-9]+)?)\s*(毫米|mm|厘米|cm|米|m|M)?", text, re.I)
    if h is None and mh2:
        h = _to_m(float(mh2.group(1)), mh2.group(2))

    mb = re.search(r"(?:宽|宽度|截面宽|b)[\s:：=]*?([0-9]+(?:\.[0-9]+)?)\s*(毫米|mm|厘米|cm|米|m|M)?", text, re.I)
    if mb:
        b = _to_m(float(mb.group(1)), mb.group(2))

    sec["h"] = h if h else 1.0
    sec["b"] = b if b else 0.5
    if shape in ("i_beam", "box"):
        mtw = re.search(r"(?:腹板|web|tw)[\s:：=]*?([0-9]+(?:\.[0-9]+)?)\s*(毫米|mm|厘米|cm|米|m|M)?", text, re.I)
        if mtw:
            sec["tw"] = _to_m(float(mtw.group(1)), mtw.group(2))
        mtf = re.search(r"(?:翼缘|flange|tf)[\s:：=]*?([0-9]+(?:\.[0-9]+)?)\s*(毫米|mm|厘米|cm|米|m|M)?", text, re.I)
        if mtf:
            sec["tf"] = _to_m(float(mtf.group(1)), mtf.group(2))
    return sec


def extract_supports(text: str, length: float) -> list[Support] | None:
    t = text
    if "两端固支" in t or "双固支" in t:
        return [Support(position=0, type="fixed"), Support(position=length, type="fixed")]
    if ("一端固定一端铰支" in t or "一端固支一端简支" in t or "左固右铰" in t
            or ("固定" in t and "铰" in t)):
        return [Support(position=0, type="fixed"), Support(position=length, type="pinned")]
    if "固支" in t or "固定端" in t or "固定" in t:
        return [Support(position=0, type="fixed"), Support(position=length, type="pinned")]
    if "简支" in t or "铰支" in t or "铰" in t:
        return [Support(position=0, type="pinned"), Support(position=length, type="pinned")]
    return None


def extract_loads(text: str, length: float) -> list[Load]:
    loads: list[Load] = []
    # 均布荷载：必须带 /m
    for m in re.finditer(r"([0-9]+(?:\.[0-9]+)?)\s*(?:kN/m|千牛/米|kn/m|KN/m)", text, re.I):
        loads.append(Load(type="udl", value=float(m.group(1)), start=None, end=None))
    # 集中力：裸 kN（不带 /m）
    for m in re.finditer(r"([0-9]+(?:\.[0-9]+)?)\s*(?:kN|千牛|kn|KN)(?!/m)", text):
        val = float(m.group(1))
        pos = None
        pm = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*米\s*处", text)
        if pm:
            pos = float(pm.group(1))
        if "跨中" in text:
            pos = length / 2.0
        loads.append(Load(type="point", value=val, position=pos))
    return loads


def extract_material(text: str):
    if re.search(r"混凝土|砼|C50|C40|C30|桥面板", text):
        return ("混凝土 C50", 3.45e10, 2500.0)
    return ("钢材 Q345", 2.06e11, 7850.0)


# ---------- 组合成完整 spec ----------
def parse_full(text: str, notes: list[str] | None = None) -> ModelSpec:
    """一句话 → ModelSpec。

    `notes` 为可选出参：规则解析也会做单位哨兵（"梁高1200"这类裸数字），
    触发换算时把提示写进 `notes` 交给界面，而不是静默改数。
    """
    from units_guard import guard_spec

    length = extract_length(text) or 10.0
    n_spans = extract_spans(text)
    sec = Section(**extract_section(text))
    mat = extract_material(text)
    spec = ModelSpec(
        name="自然语言模型",
        length=length,
        n_spans=n_spans,
        section=sec,
        material_name=mat[0],
        E=mat[1],
        rho=mat[2],
    )
    sup = extract_supports(text, length)
    if sup is not None:
        spec.supports = sup
    spec.loads = extract_loads(text, length)
    return guard_spec(spec, notes if notes is not None else [])


# ---------- 自然语言迭代修改 ----------
def _get_field(obj, field):
    if isinstance(field, tuple):
        return getattr(getattr(obj, field[0]), field[1])
    return getattr(obj, field)


def _set_field(obj, field, val):
    if isinstance(field, tuple):
        setattr(getattr(obj, field[0]), field[1], val)
    else:
        setattr(obj, field, val)


def _apply_dim(t: str, kw: str, target, field):
    m = re.search(
        kw + r"[\s:：为约]*?(改为|设为|变为|加大到|减小到|增加到|减少到|加|减)?\s*"
        r"([0-9]+(?:\.[0-9]+)?)\s*(毫米|mm|厘米|cm|米|m|M)?",
        t,
    )
    if not m:
        return
    val = _to_m(float(m.group(2)), m.group(3))
    verb = m.group(1) or ""
    cur = _get_field(target, field)
    if "到" in verb or verb in ("改为", "设为", "变为"):
        new = val
    elif "减" in verb:
        new = cur - val
    elif "加" in verb:
        new = cur + val
    else:
        new = val
    _set_field(target, field, max(new, 1e-4))


def modify(text: str, spec: ModelSpec, notes: list[str] | None = None) -> ModelSpec:
    from units_guard import guard_spec

    s = spec.model_copy(deep=True)
    t = text
    _apply_dim(t, r"(?:跨度|总长|长度|梁长)", s, "length")
    _apply_dim(t, r"(?:截面高|截面高度|梁高|高度|高)", s, ("section", "h"))
    _apply_dim(t, r"(?:截面宽|截面宽度|梁宽|宽度|宽)", s, ("section", "b"))
    if re.search(r"添加|加|施加", t) and re.search(r"集中力|集中荷载|点荷载|kN|千牛", t):
        pm = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:kN|千牛|kn|KN)(?!/m)", t)
        if pm:
            val = float(pm.group(1))
            pos = None
            pmm = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*米\s*处", t)
            if pmm:
                pos = float(pmm.group(1))
            if "跨中" in t:
                pos = s.length / 2.0
            s.loads.append(Load(type="point", value=val, position=pos))
    if re.search(r"去掉|删除|移除", t) and re.search(r"集中力|荷载", t):
        if "集中" in t:
            s.loads = [l for l in s.loads if l.type != "point"]
        else:
            s.loads = []
    return guard_spec(s, notes if notes is not None else [])


# ---------- 统一入口 ----------
def parse_nl(text: str, prev: ModelSpec | None = None, llm_config=None):
    """
    自然语言 → ModelSpec。

    返回 (spec, source, diag)：
      source ∈ {'llm', 'llm(mod)', 'rule', 'rule(mod)', 'rule(llm失败回退)'}
      diag    —— LLMResult | None，携带原始输出、失败原因、默认值提示（供界面显示）

    LLM 与规则的配合策略：
      - 配了 key → 优先 LLM（含上下文修改），**失败一律回退规则解析**，不静默吞错；
      - 没配 key 或 LLM 不可用 → 规则解析（离线可用）。
    """
    from llm_parser import LLMConfig, LLMResult, parse_with_llm

    diag: LLMResult | None = None

    # 兼容旧的 dict 形式配置
    if isinstance(llm_config, dict):
        llm_config = LLMConfig(
            api_key=llm_config.get("api_key", ""),
            base_url=llm_config.get("base_url", "https://api.deepseek.com"),
            model=llm_config.get("model", "deepseek-chat"),
        )

    if llm_config is not None and getattr(llm_config, "api_key", ""):
        # 有 prev 且句子像"修改"，就把当前模型一起发给 LLM 做上下文修改
        is_mod = bool(re.search(r"改|加大|减小|增加|减少|添加|去掉|删除|设为|变为|调整", text))
        diag = parse_with_llm(text, llm_config, prev=prev if (prev is not None and is_mod) else None)
        if diag.ok and diag.spec is not None:
            return diag.spec, ("llm(mod)" if is_mod and prev is not None else "llm"), diag

    # 回退：规则解析（含迭代修改）。规则路径同样过单位哨兵，
    # 触发的换算提示要带出去——否则用户看到跨度变成 120 m 却不知道为什么。
    notes: list[str] = []
    if prev is not None and re.search(r"改|加大|减小|增加|减少|添加|去掉|删除|设为|变为|调整", text):
        try:
            spec = modify(text, prev, notes)
            return spec, ("rule(llm失败回退)" if diag else "rule(mod)"), _rule_diag(diag, notes)
        except Exception:
            pass
    spec = parse_full(text, notes)
    return spec, ("rule(llm失败回退)" if diag else "rule"), _rule_diag(diag, notes)


def _rule_diag(llm_diag, notes: list[str]):
    """规则路径的诊断对象：保留 LLM 失败原因，并带上哨兵提示。

    原先规则路径返回 `diag=None`，界面拿不到"替你改了什么"，只能靠猜。
    这里用与 LLM 诊断同构的 `Diag`（ok/notes/error/raw 四个字段），
    界面代码不必分叉。
    """
    if not notes:
        return llm_diag
    from model_doc import Diag

    if llm_diag is None:
        return Diag(ok=True, notes=notes)
    return Diag(ok=False, notes=notes, error=llm_diag.error, raw=llm_diag.raw)
