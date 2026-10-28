"""bridge_llm.py —— 桥梁描述的自然语言解析层（只出参数，不出网格）
====================================================================
定位
----
`llm_parser.py` 只认 `ModelSpec`（单梁）。桥梁参数（span/rise/n_hanger…）
不在那个 Schema 里，所以桥梁侧长期只有规则解析——`ModelDoc.from_text()`
里那句注释「桥梁侧目前只有规则解析」就是这个缺口。本模块把它补上。

**借自 explore-x20 的部分**：分条 Rules 的 prompt 写法、缺失值给工程经验值、
单位换算要显式要求、上下文修改的用法。

**刻意不借的部分**：X20 让 LLM 直接输出完整节点表 / 单元表 / 实常数。
那样做桥型覆盖更广，但代价是拓扑被冻死（改一个数要重新问一遍模型）、
结果不可复现、几何正确性全靠模型算术、而且没法离线。
本项目铁律是「参数是唯一真源，几何由确定性代码 build() 展开」，
所以这里的 prompt 第 0 条就写死：**不要输出节点表**。

数据流
------
    中文描述 ──LLM──▶ BridgeParams(JSON) ──哨兵──▶ fill_defaults ──▶ params
                                                                      │
                                                    ModelDoc.build() ─┘─▶ 节点/单元
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from llm_parser import LLMConfig, call_chat, extract_json
from units_guard import guard_units

BRIDGE_KINDS = ("arch", "truss", "beam_bridge")
KIND_NAMES = {"arch": "拱桥", "truss": "桁架桥", "beam_bridge": "梁桥"}

MATERIAL_NAMES = ("钢材 Q345", "混凝土 C50")


# ---------------------------------------------------------------- 参数模型
class BridgeParams(BaseModel):
    """LLM 要输出的参数（**不含节点/单元**）。

    字段全部可缺省：缺的由 `fill_defaults()` 按工程经验补齐并记 notes，
    这与「绝不假装听懂了」的原则一致——补齐什么，界面就显示什么。
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    kind: Literal["arch", "truss", "beam_bridge"]
    span: float | None = None

    # 拱桥
    rise: float | None = None
    n_hanger: int | None = None
    hanger_kind: Literal["TRUSS", "CABLE"] | None = None

    # 桁架桥
    n_panel: int | None = None
    depth: float | None = None
    node_load: float | None = None

    # 梁桥
    n_spans: int | None = None
    h: float | None = None
    b: float | None = None

    # 共用
    udl: float | None = None
    point: float | None = None
    material: str | None = None

    @field_validator("hanger_kind", mode="before")
    @classmethod
    def _upper(cls, v: Any) -> Any:
        return v.upper() if isinstance(v, str) else v


@dataclass
class BridgeLLMResult:
    ok: bool = False
    kind: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    raw: str = ""
    data: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)
    error: str = ""


# ---------------------------------------------------------------- Schema
def _strip_titles(node: Any) -> Any:
    """去掉 Schema 里的 title/description，压缩 prompt 体积（中文规则已写全）。"""
    if isinstance(node, dict):
        return {k: _strip_titles(v) for k, v in node.items()
                if k not in ("title", "description")}
    if isinstance(node, list):
        return [_strip_titles(x) for x in node]
    return node


def build_bridge_schema_text() -> str:
    """由 BridgeParams 自动生成紧凑 Schema——与代码永远同步，不会写歪。"""
    return json.dumps(_strip_titles(BridgeParams.model_json_schema()), ensure_ascii=False)


BRIDGE_SYSTEM_PROMPT = """你是桥梁结构有限元前处理助手，负责把用户的中文桥梁描述转成**建造参数**。

第 0 条（最重要）：
**不要输出节点表、单元表、材料库、实常数、坐标系或任何坐标数组。**
拱轴线、吊杆位置、桁架节点由程序按你给的参数精确生成。你只负责听懂这句话，
不负责画网格。输出里一旦出现 nodes / elements / coordinates 之类字段，整次结果作废。

硬性要求：
1. 只输出一个 JSON 对象，不要解释、不要 Markdown 代码围栏。
2. 严格遵守下面给出的 JSON Schema。
3. 单位统一：长度用米(m)，均布荷载 kN/m，集中力与节点荷载 kN。
   "矢高 18 米"→18；"桁高 6000"→6；"跨度 120000mm"→120；"桥宽 8 米"→8。
   用户明显在同一句里混用 mm 与 m 时，以工程合理性为准。
4. kind 判定：
   - 出现 拱 / 拱肋 / 矢高 / 矢跨比 → "arch"
   - 出现 桁架 / truss / 节间 / 桁高 → "truss"
   - 其余含"桥"（连续梁桥、简支梁桥、箱梁桥） → "beam_bridge"
5. 各桥型该填的字段，其余字段省略、不要编造：
   - arch: span, rise, n_hanger, hanger_kind, udl, point, material
   - truss: span, n_panel, depth, node_load, material
   - beam_bridge: span, n_spans, h, b, udl, point, material
6. 用户没给的数值，按下面经验值补齐（不要留 null）：
   - rise 未给 → 按矢跨比 1/5 取 span/5；用户说了"矢跨比 1/6"就按 1/6 算
   - depth 未给 → span/8；h 未给 → span/20；b 未给 → 6
   - n_hanger 未给 → 9；n_panel 未给 → 8；n_spans 未给 → 1
7. 桁架的节点荷载：node_load = udl × span / n_panel，单位 kN。
   用户直接说"每个节点 100kN"时，node_load 直接写 100，不要再折算。
8. 吊杆单元：用户说"索 / 拉索 / 缆"→ "CABLE"，否则 "TRUSS"。
9. 材料：说"混凝土"或 C 系列强度等级 → "混凝土 C50"，否则 "钢材 Q345"。
10. 荷载只给数值，不考虑方向（向下的正值）。
11. 桥型判定与用户明确选择不一致时，以用户选择为准（调用方会覆盖 kind）。

JSON Schema：
{schema}
"""


def build_bridge_messages(text: str, prev: dict | None = None) -> list[dict]:
    sys = BRIDGE_SYSTEM_PROMPT.replace("{schema}", build_bridge_schema_text())
    if prev is None:
        user = f"请把下面的桥梁描述解析为 JSON 参数：\n\n{text}"
    else:
        cur = json.dumps({k: v for k, v in prev.items() if k != "loads"},
                         ensure_ascii=False, indent=2)
        user = (
            f"这是当前的桥梁参数 JSON：\n```json\n{cur}\n```\n\n"
            f"请按下面的修改要求，输出**更新后的完整参数**"
            f"（未提及的字段保持原值）：\n\n{text}"
        )
    return [
        {"role": "system", "content": sys},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------- 默认值
def normalize_material(name: Any) -> str:
    """把 LLM 五花八门的材料写法收敛到两个选项。"""
    s = str(name or "")
    if "混凝土" in s or s.upper().startswith("C") and s[1:].isdigit():
        return "混凝土 C50"
    if "钢" in s or "Q" in s.upper():
        return "钢材 Q345"
    return "钢材 Q345"


def _pos(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None


def fill_defaults(kind: str, p: dict[str, Any], notes: list[str]) -> dict[str, Any]:
    """把 LLM 给的参数补成可直接交给 `ModelDoc.build()` 的完整字典。

    与 `bridge_spec.extract_bridge_params()` 的经验值保持一致，
    这样"LLM 路径"和"规则路径"对同一句话给出的模型是可比的（便于回归）。
    只输出该桥型 `DEFAULTS` 里认识的键，避免脏字段污染工程文件。
    """
    out: dict[str, Any] = {}

    span = _pos(p.get("span"))
    if span is None:
        span = 60.0
        notes.append("未说明跨度，默认 60 m")
    out["span"] = span

    udl = p.get("udl")
    udl = float(udl) if isinstance(udl, (int, float)) and not isinstance(udl, bool) else None
    point = p.get("point")
    point = float(point) if isinstance(point, (int, float)) and not isinstance(point, bool) else None
    mat = normalize_material(p.get("material"))
    if not p.get("material"):
        notes.append(f"未说明材料，按 {mat} 处理")

    if kind == "arch":
        rise = _pos(p.get("rise"))
        if rise is None:
            rise = span / 5.0
            notes.append(f"未说明矢高，按常用矢跨比 1/5 取 {rise:.1f} m")
        nh = p.get("n_hanger")
        if not isinstance(nh, int) or nh < 1:
            nh = 9
            notes.append(f"未说明吊杆数，默认 {nh} 对")
        out.update(
            rise=rise, n_hanger=nh,
            hanger_kind="CABLE" if p.get("hanger_kind") == "CABLE" else "TRUSS",
            udl=udl or 0.0, point=point or 0.0, material=mat,
        )
        if udl is None:
            notes.append("未说明均布荷载，按无外荷载处理（可手动加）")
        return out

    if kind == "truss":
        depth = _pos(p.get("depth"))
        if depth is None:
            depth = span / 8.0
            notes.append(f"未说明桁高，按 1/8 跨度取 {depth:.1f} m")
        np_ = p.get("n_panel")
        if not isinstance(np_, int) or np_ < 2:
            np_ = 8
            notes.append(f"未说明节间数，默认 {np_} 个")
        node_load = p.get("node_load")
        if isinstance(node_load, (int, float)) and not isinstance(node_load, bool):
            node_load = float(node_load)
        else:
            q = udl if udl is not None else 20.0
            node_load = q * (span / np_)
            notes.append(
                f"未说明节点荷载，按 {q:g} kN/m 折算为每节点 {node_load:.0f} kN")
        out.update(span=span, n_panel=np_, depth=depth,
                   node_load=node_load, material=mat)
        return out

    # ---- beam_bridge ----
    ns = p.get("n_spans")
    if not isinstance(ns, int) or ns < 1:
        ns = 1
    h = _pos(p.get("h"))
    if h is None:
        h = span / 20.0
        notes.append(f"未说明梁高，按 1/20 跨度取 {h:.2f} m")
    b = _pos(p.get("b"))
    if b is None:
        b = 6.0
        notes.append("未说明桥宽，默认 6 m")
    out.update(span=span, n_spans=ns, h=h, b=b,
               udl=udl or 0.0, point=point or 0.0, material=mat)
    if udl is None:
        notes.append("未说明均布荷载，按无外荷载处理（可手动加）")
    return out


# ---------------------------------------------------------------- 主入口
def parse_bridge_with_llm(
    text: str,
    cfg: LLMConfig | None,
    kind: str | None = None,
    prev: dict | None = None,
) -> BridgeLLMResult:
    """走 LLM 解析桥梁参数。任何环节失败都返回 ok=False + error，由调用方回退规则。

    `kind` 给定时以它为准（界面上的"结构类型"提示 / 关键词分类结果），
    因为用户显式选择比自己写的分类器可靠；LLM 判得不一样会记一条 note。
    """
    if cfg is None or not getattr(cfg, "api_key", ""):
        return BridgeLLMResult(ok=False, error="未配置 API Key")

    try:
        raw = call_chat(cfg, build_bridge_messages(text, prev))
    except Exception as e:
        return BridgeLLMResult(ok=False, error=f"调用失败：{type(e).__name__}: {e}")

    try:
        data = extract_json(raw)
    except Exception as e:
        return BridgeLLMResult(ok=False, raw=raw, error=f"输出不是合法 JSON：{e}")

    if not isinstance(data, dict):
        return BridgeLLMResult(ok=False, raw=raw,
                               error=f"输出不是 JSON 对象：{type(data).__name__}")

    # ---- 桥型：显式指定优先
    llm_kind = data.get("kind")
    if llm_kind not in BRIDGE_KINDS:
        return BridgeLLMResult(ok=False, raw=raw, data=data,
                               error=f"kind 非法：{llm_kind!r}（应为 arch/truss/beam_bridge）")
    notes: list[str] = []
    target = kind or llm_kind
    if target not in BRIDGE_KINDS:
        return BridgeLLMResult(ok=False, raw=raw, data=data, error=f"目标桥型非法：{target!r}")
    if kind and llm_kind != kind:
        notes.append(f"语言模型判为{KIND_NAMES[llm_kind]}，按你选的"
                     f"{KIND_NAMES[kind]}处理")

    # ---- 先过 Pydantic 把类型收敛（"120000"→120000.0），再过哨兵
    payload = {k: v for k, v in data.items() if k != "kind"}
    payload["kind"] = target
    try:
        model = BridgeParams(**payload)
    except Exception as e:
        return BridgeLLMResult(ok=False, raw=raw, data=data,
                               error=f"Schema 校验未通过：{type(e).__name__}: {str(e)[:300]}")

    clean = model.model_dump(exclude_none=True)
    guard_units(clean, target, notes)
    params = fill_defaults(target, clean, notes)
    return BridgeLLMResult(ok=True, kind=target, params=params,
                           raw=raw, data=data, notes=notes)


# ---------------------------------------------------------------- CLI
def main() -> int:
    import argparse

    from llm_parser import probe

    ap = argparse.ArgumentParser(description="桥梁参数 LLM 解析层")
    ap.add_argument("--parse", metavar="TEXT", help="解析一句桥梁描述")
    ap.add_argument("--probe", action="store_true", help="检测真实端点连通性")
    args = ap.parse_args()

    cfg = LLMConfig.from_env()
    if cfg is None:
        print("未配置 DEEPSEEK_API_KEY / LLM_API_KEY。")
        print("离线验证请用：python verify_bridge_llm.py")
        return 1

    if args.probe:
        ok, msg = probe(cfg)
        print(f"端点 {cfg.base_url} 模型 {cfg.model} → {'连通' if ok else '失败'}")
        print(msg)
        return 0 if ok else 1

    if args.parse:
        r = parse_bridge_with_llm(args.parse, cfg)
        print(f"ok={r.ok} kind={r.kind} error={r.error}")
        print(json.dumps(r.params, ensure_ascii=False, indent=2))
        for n in r.notes:
            print(f"  · 提示：{n}")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
