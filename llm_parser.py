"""
llm_parser.py —— LLM 解析层（DeepSeek / 任意 OpenAI 兼容端点）
================================================================
把中文自然语言描述解析成结构化 `ModelSpec`。

设计原则：解析结果**绝不直连模型**，必须过三道关
-------------------------------------------------
1. **Schema 约束** —— 把 ModelSpec 的 Pydantic JSON Schema 直接写进 prompt，
   并开启 `response_format=json_object`，从源头压掉散文式输出。
2. **Pydantic 校验** —— 任何不合法输出一律拒绝（`ValidationError` 就地捕获），
   失败就回退到规则解析，绝不让脏数据污染模型状态。
3. **默认值 + 追问** —— 缺字段用工程默认值补齐，同时把"缺了什么"列成
   `notes` 交给界面显式提示，避免用户以为"没说=我说对了"。

另外支持**上下文修改**：把当前 spec 一并发给 LLM，让它输出更新后的完整 spec，
从而实现"把截面高度加大到 1.5m"这类迭代改参。

依赖：仅标准库（urllib），不需要装 openai 包。

单元自检：python llm_parser.py --selftest
（内置一个本地假端点，不联网、不消耗额度即可验证整条链路）
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from model_spec import ModelSpec

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


# ---------------------------------------------------------------- 配置
@dataclass
class LLMConfig:
    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout: float = 45.0
    temperature: float = 0.0

    @classmethod
    def from_env(cls) -> "LLMConfig | None":
        """从环境变量读取；没有 key 就返回 None（调用方据此走规则解析）。"""
        import os

        key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY") or ""
        if not key:
            return None
        return cls(
            api_key=key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL),
            model=os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL),
        )


@dataclass
class LLMResult:
    """解析结果 + 诊断信息。界面据此显示"解析是否可信、缺了什么"。"""

    ok: bool = False
    spec: ModelSpec | None = None
    raw: str = ""                       # LLM 返回的原始文本
    data: dict[str, Any] | None = None  # 提取出的 JSON
    notes: list[str] = field(default_factory=list)   # 默认值/单位修正等提示
    error: str = ""                     # 失败原因


# ---------------------------------------------------------------- Schema
def _strip_titles(node: Any) -> Any:
    """去掉 schema 里的 title 字段，压缩 prompt 体积。"""
    if isinstance(node, dict):
        return {k: _strip_titles(v) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_strip_titles(x) for x in node]
    return node


def build_schema_text() -> str:
    """由 ModelSpec 自动生成紧凑 JSON Schema（与代码永远同步，不会写歪）。"""
    return json.dumps(_strip_titles(ModelSpec.model_json_schema()), ensure_ascii=False)


SYSTEM_PROMPT = """你是结构工程有限元前处理助手，负责把用户的中文描述转成严格的 JSON 模型。

硬性要求：
1. 只输出一个 JSON 对象，不要任何解释、不要 Markdown 代码围栏。
2. 必须严格遵守下面给出的 JSON Schema。
3. 单位统一：长度用米(m)，均布荷载用 kN/m，集中力用 kN，弹性模量用 Pa。
   例如"腹板厚 20mm"必须写成 0.02；"1200mm 梁高"必须写成 1.2。
4. 荷载向下为正值（数值写正号，方向由求解器按向下处理）。
5. 用户没提到的字段：能合理推断就推断，推断不了就省略该字段（不要编造）。
   例如没说支座，就省略 supports；没说荷载，就省略 loads。

支座 type 取值：pinned(铰支) / roller(滚动) / fixed(固支)。
截面 shape 取值：rectangular(矩形) / i_beam(工字) / box(箱形) / circular(圆形，h=b=直径)。
荷载 type 取值：udl(均布，用 start/end 给区间) / point(集中力，用 position 给位置)。

JSON Schema：
{schema}
"""


def build_messages(text: str, prev: ModelSpec | None = None) -> list[dict]:
    sys = SYSTEM_PROMPT.replace("{schema}", build_schema_text())
    if prev is None:
        user = f"请把下面的描述解析为 JSON 模型：\n\n{text}"
    else:
        cur = json.dumps(prev.model_dump(), ensure_ascii=False, indent=2)
        user = (
            f"这是当前的模型 JSON：\n```json\n{cur}\n```\n\n"
            f"请按下面的修改要求，输出**更新后的完整 JSON 模型**"
            f"（未提及的字段保持原值）：\n\n{text}"
        )
    return [
        {"role": "system", "content": sys},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------- 调用
def call_chat(cfg: LLMConfig, messages: list[dict]) -> str:
    """
    调用 OpenAI 兼容的 /chat/completions，返回 content 字符串。

    兼容处理：部分端点不支持 response_format=json_object，
    遇到 400 就自动去掉该参数重试一次。
    """
    url = cfg.base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": cfg.model,
        "messages": messages,
        "temperature": cfg.temperature,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }

    def _post(payload: dict) -> str:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers=headers, method="POST",
        )
        with urllib.request.urlopen(req, timeout=cfg.timeout) as r:
            return json.loads(r.read().decode("utf-8"))["choices"][0]["message"]["content"]

    try:
        return _post(body)
    except urllib.error.HTTPError as e:
        if e.code == 400:
            body.pop("response_format", None)
            return _post(body)
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e


def probe(cfg: LLMConfig) -> tuple[bool, str]:
    """连通性自检：发一条极短消息，成功返回 (True, 模型名)。"""
    try:
        content = call_chat(
            cfg,
            [{"role": "user", "content": '只回复一个 JSON：{"ok":true}'}],
        )
        return True, (content.strip()[:120] or "（空响应）")
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------- JSON 提取
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)


def extract_json(content: str) -> dict:
    """从 LLM 输出里稳健地抠出 JSON 对象。"""
    txt = content.strip()
    m = _JSON_FENCE.search(txt)
    if m:
        txt = m.group(1).strip()
    # 兜底：截到首尾花括号
    if not txt.startswith("{"):
        i = txt.find("{")
        if i >= 0:
            j = txt.rfind("}")
            txt = txt[i:j + 1] if j > i else txt[i:]
    return json.loads(txt)


# ---------------------------------------------------------------- 清洗 / 哨兵
def _unit_guard(data: dict, notes: list[str]) -> None:
    """
    单位哨兵：LLM 偶尔会把 mm 直接写成数字（1200 表示 1200mm）。
    这类错误不会报错，但会让刚度矩阵荒谬——属于最危险的静默错误，必须拦。

    具体阈值与换算统一放在 `units_guard.py`，因为规则解析路径也要用同一套
    （原先这段只保护 LLM 输出，规则路径完全绕过，见该模块开头的说明）。
    """
    from units_guard import guard_units

    guard_units(data, "beam", notes)


def _collect_notes(data: dict, notes: list[str]) -> None:
    """把"用户没说、我们替他默认了什么"列出来——这是可解释性的核心。"""
    if not isinstance(data.get("length"), (int, float)):
        data["length"] = 10.0
        notes.append("未说明跨度，已按 10 m 默认")
    sec = data.get("section")
    if not isinstance(sec, dict):
        data["section"] = {"shape": "rectangular", "h": 1.0, "b": 0.5}
        notes.append("未说明截面，已按矩形 1.0×0.5 m 默认")
    else:
        if not isinstance(sec.get("h"), (int, float)):
            sec["h"] = 1.0
            notes.append("未说明截面高度，已按 1.0 m 默认")
        if not isinstance(sec.get("b"), (int, float)):
            sec["b"] = 0.5
            notes.append("未说明截面宽度，已按 0.5 m 默认")
    if not data.get("loads"):
        notes.append("未识别到任何荷载，模型将无外荷载（可手动添加）")
    if not data.get("supports"):
        notes.append("未说明支座形式，已按两端简支处理")
    if not data.get("material_name"):
        notes.append("未说明材料，已按钢材 Q345 处理")

    # 材料与 E/rho 一致性：LLM 说了混凝土却沿用钢材默认值时纠正
    name = str(data.get("material_name", ""))
    if "混凝土" in name or re.search(r"\bC\d{2}\b", name):
        if not isinstance(data.get("E"), (int, float)) or data.get("E") == 2.06e11:
            data["E"] = 3.45e10
            data["rho"] = 2500.0
            notes.append("材料为混凝土，E/ρ 已按 C50 取值（3.45e10 Pa / 2500 kg/m³）")


def _sanitize(data: dict) -> list[str]:
    notes: list[str] = []
    _unit_guard(data, notes)
    _collect_notes(data, notes)
    return notes


# ---------------------------------------------------------------- 主入口
def parse_with_llm(
    text: str,
    cfg: LLMConfig | None,
    prev: ModelSpec | None = None,
) -> LLMResult:
    """
    走 LLM 解析。任何环节失败都返回 ok=False + error，由调用方回退规则解析。
    """
    if cfg is None or not cfg.api_key:
        return LLMResult(ok=False, error="未配置 API Key")

    try:
        raw = call_chat(cfg, build_messages(text, prev))
    except Exception as e:
        return LLMResult(ok=False, error=f"调用失败：{type(e).__name__}: {e}")

    try:
        data = extract_json(raw)
    except Exception as e:
        return LLMResult(ok=False, raw=raw, error=f"输出不是合法 JSON：{e}")

    if not isinstance(data, dict):
        return LLMResult(ok=False, raw=raw, error=f"输出不是 JSON 对象：{type(data).__name__}")

    notes = _sanitize(data)

    try:
        spec = ModelSpec(**data)
    except Exception as e:
        return LLMResult(ok=False, raw=raw, data=data,
                         error=f"Schema 校验未通过：{type(e).__name__}: {str(e)[:300]}")

    return LLMResult(ok=True, spec=spec, raw=raw, data=data, notes=notes)


# ---------------------------------------------------------------- 自检
def _selftest() -> int:
    """
    本地假端点自检：起一个 http.server 冒充 OpenAI 兼容接口，
    覆盖 ①正常输出 ②带代码围栏 ③非法 JSON ④缺字段（追问）⑤单位哨兵 ⑥上下文修改。
    全程不联网、不消耗额度。
    """
    import http.server
    import threading

    cases = {
        "ok": '{"name":"LLM梁","length":30,"n_spans":1,'
              '"section":{"shape":"rectangular","h":1.2,"b":0.6},'
              '"supports":[{"position":0,"type":"pinned"},{"position":30,"type":"roller"}],'
              '"loads":[{"type":"udl","value":20,"start":null,"end":null}],'
              '"material_name":"钢材 Q345","E":2.06e11,"rho":7850}',
        "fenced": '```json\n{"name":"围栏","length":12,"section":{"shape":"i_beam","h":0.8,"b":0.3,'
                  '"tw":0.02,"tf":0.015},"supports":[{"position":0,"type":"fixed"},'
                  '{"position":12,"type":"fixed"}],"loads":[{"type":"udl","value":15}],'
                  '"material_name":"钢材 Q345"}\n```',
        "badjson": "抱歉，我无法理解这段描述。",
        "missing": '{"name":"缺字段","length":20,"section":{"shape":"box","h":2}}',
        "mm": '{"name":"单位坑","length":30000,"section":{"shape":"rectangular","h":1200,"b":600},'
              '"loads":[{"type":"udl","value":20000}],"material_name":"混凝土 C50"}',
        "modify": '{"name":"改后","length":40,"n_spans":1,'
                  '"section":{"shape":"rectangular","h":1.5,"b":0.6},'
                  '"supports":[{"position":0,"type":"pinned"},{"position":40,"type":"roller"}],'
                  '"loads":[{"type":"udl","value":20},{"type":"point","value":80,"position":20}],'
                  '"material_name":"钢材 Q345"}',
    }
    state = {"case": "ok"}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n).decode("utf-8"))
            # 用最后一条 user 消息判断用例
            last = body["messages"][-1]["content"]
            for k in cases:
                if k in last:
                    state["case"] = k
                    break
            content = cases[state["case"]]
            out = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    cfg = LLMConfig(api_key="test", base_url=f"http://127.0.0.1:{port}/v1", model="fake")
    fails = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal fails
        if not cond:
            fails += 1
        print(f"  [{'PASS' if cond else 'FAIL'}] {name} {extra}")

    print("LLM 解析层自检（本地假端点，不联网）")
    print("-" * 68)

    r = parse_with_llm("ok 一座30米简支钢梁", cfg)
    check("① 正常输出", r.ok and r.spec is not None and r.spec.length == 30.0,
          f"→ L={r.spec.length if r.spec else '-'}m, 荷载{len(r.spec.loads) if r.spec else '-'}个")

    r = parse_with_llm("fenced 两端固支工字梁", cfg)
    check("② Markdown 代码围栏", r.ok and r.spec is not None
          and r.spec.section.shape == "i_beam" and r.spec.supports[0].type == "fixed",
          f"→ {r.spec.section.shape if r.spec else '-'}")

    r = parse_with_llm("badjson 随便来一句", cfg)
    check("③ 非法 JSON → 拒绝且带原因", (not r.ok) and "JSON" in r.error, f"→ {r.error[:40]}")

    r = parse_with_llm("missing 20米箱梁", cfg)
    ok4 = r.ok and r.spec is not None and len(r.notes) >= 2
    check("④ 缺字段 → 默认值 + 追问", ok4,
          f"→ 提示 {len(r.notes)} 条：{r.notes[:2]}")

    r = parse_with_llm("mm 单位坑", cfg)
    ok5 = (r.ok and r.spec is not None and abs(r.spec.length - 30.0) < 1e-6
           and abs(r.spec.section.h - 1.2) < 1e-6 and abs(r.spec.loads[0].value - 20.0) < 1e-6
           and r.spec.E == 3.45e10)
    check("⑤ 单位哨兵(mm→m, 混凝土E)", ok5,
          f"→ L={r.spec.length if r.spec else '-'}m h={r.spec.section.h if r.spec else '-'}m "
          f"q={r.spec.loads[0].value if r.spec and r.spec.loads else '-'}kN/m")

    prev = ModelSpec(name="原", length=30.0, section={"shape": "rectangular", "h": 1.2, "b": 0.6})
    r = parse_with_llm("modify 跨度改40，截面高改1.5，跨中加80kN", cfg, prev=prev)
    ok6 = (r.ok and r.spec is not None and r.spec.length == 40.0
           and r.spec.section.h == 1.5 and any(l.type == "point" for l in r.spec.loads))
    check("⑥ 上下文修改", ok6,
          f"→ L={r.spec.length if r.spec else '-'}m h={r.spec.section.h if r.spec else '-'}m "
          f"荷载{len(r.spec.loads) if r.spec else '-'}个")

    srv.shutdown()
    print("-" * 68)
    print(f"结果：{'全部通过' if fails == 0 else f'{fails} 项失败'}")
    return 0 if fails == 0 else 1


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="LLM 解析层")
    ap.add_argument("--selftest", action="store_true", help="本地假端点自检（不联网）")
    ap.add_argument("--probe", action="store_true", help="检测真实端点连通性")
    ap.add_argument("--parse", metavar="TEXT", help="解析一句自然语言")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    cfg = LLMConfig.from_env()
    if cfg is None:
        print("未配置 DEEPSEEK_API_KEY / LLM_API_KEY。")
        print("可先跑 `python llm_parser.py --selftest` 用本地假端点验证链路。")
        return 1

    if args.probe:
        ok, msg = probe(cfg)
        print(f"端点 {cfg.base_url} 模型 {cfg.model} → {'连通' if ok else '失败'}")
        print(msg)
        return 0 if ok else 1

    if args.parse:
        r = parse_with_llm(args.parse, cfg)
        print(f"ok={r.ok}  error={r.error}")
        if r.spec:
            print(r.spec.summary())
        for n in r.notes:
            print(f"  · 提示：{n}")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
