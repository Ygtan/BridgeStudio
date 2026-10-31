"""verify_bridge_llm.py —— 桥梁 LLM 解析层离线验证
====================================================
用本地假端点冒充 OpenAI 兼容接口，全程不联网、不消耗任何额度。

验证目标（不只是"没报错"，而是断言行为）：
  ① 拱桥/桁架桥/梁桥三种 kind 都能解析出正确参数
  ② Markdown 围栏能被剥离
  ③ 非法 JSON → ok=False 且带原因（由上层回退规则）
  ④ 缺字段 → 用工程经验值补齐，并逐条记 notes
  ⑤ 单位误写（跨度 120000、矢高 24000、udl 30000）被哨兵纠正
  ⑥ kind 冲突时以调用方指定为准，并记 note
  ⑦ kind 非法 → 拒绝
  ⑧ LLM 越界输出节点表 → 参数里不许混进 nodes/elements
  ⑨ 失败回退：ModelDoc.from_text 仍能建出模型，source 标注回退
  ⑩ 与规则解析的结果可比（同一句话的关键参数一致）
  ⑪ 无 key 时 source 为 rule(桥梁)
  ⑫ 规则路径同样过哨兵（"跨度120000的拱桥" → 120 m + 提示）

运行：python verify_bridge_llm.py
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()          # 输出被重定向到管道/文件时，GBK 会崩（见模块说明）

import http.server
import json
import threading

from bridge_llm import (
    BRIDGE_SYSTEM_PROMPT,
    build_bridge_schema_text,
    parse_bridge_with_llm,
)
from llm_parser import LLMConfig
from model_doc import ModelDoc

# ---------------------------------------------------------------- 假端点用例
CASES: dict[str, str] = {
    # ① 三型正常输出
    "c1arch": json.dumps({
        "kind": "arch", "span": 100, "rise": 20, "n_hanger": 11,
        "hanger_kind": "CABLE", "udl": 30, "point": 0,
        "material": "钢材 Q345",
    }),
    "c2truss": json.dumps({
        "kind": "truss", "span": 60, "n_panel": 8, "depth": 7.5,
        "node_load": 100, "material": "钢材 Q345",
    }),
    "c3beam": json.dumps({
        "kind": "beam_bridge", "span": 40, "n_spans": 2, "h": 2.0, "b": 8.0,
        "udl": 25, "material": "混凝土 C50",
    }),
    # ② 围栏
    "c4fenced": "```json\n" + json.dumps({
        "kind": "arch", "span": 80, "rise": 16, "n_hanger": 9, "udl": 20,
    }) + "\n```",
    # ③ 非法 JSON
    "c5bad": "抱歉，我不太确定你想建什么桥。",
    # ④ 缺字段
    "c6missing": json.dumps({"kind": "arch", "span": 100}),
    # ⑤ 单位误写
    "c7mm": json.dumps({
        "kind": "arch", "span": 120000, "rise": 24000,
        "n_hanger": 9, "udl": 30000,
    }),
    # ⑥ kind 冲突（LLM 说桁架，调用方要拱桥）
    "c8conflict": json.dumps({
        "kind": "truss", "span": 60, "n_panel": 8, "depth": 7.5,
    }),
    # ⑦ kind 非法
    "c9badkind": json.dumps({"kind": "斜拉桥", "span": 200}),
    # ⑧ 越界输出节点表
    "c10mesh": json.dumps({
        "kind": "arch", "span": 100, "rise": 20, "n_hanger": 9,
        "nodes": [{"id": 1, "x": 0, "y": 0}],
        "elements": [{"id": 1, "n1": 1, "n2": 2}],
        "coordinates": [[0, 0], [100, 0]],
    }),
    # ⑩ 与规则解析对齐用
    "c11parity": json.dumps({
        "kind": "arch", "span": 100, "rise": 20, "n_hanger": 11,
        "hanger_kind": "TRUSS", "udl": 30,
    }),
}


def start_fake_server() -> tuple[http.server.HTTPServer, int]:
    state = {"case": "c1arch"}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):        # 静音
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n).decode("utf-8"))
            last = body["messages"][-1]["content"]
            for k in CASES:
                if k in last:
                    state["case"] = k
                    break
            out = json.dumps(
                {"choices": [{"message": {"content": CASES[state["case"]]}}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


fails = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global fails
    if not cond:
        fails += 1
    print(f"  [{'PASS' if cond else 'FAIL'}] {name} {extra}")


def main() -> int:
    srv, port = start_fake_server()
    cfg = LLMConfig(api_key="test", base_url=f"http://127.0.0.1:{port}/v1", model="fake")

    print("桥梁 LLM 解析层验证（本地假端点，不联网）")
    print("-" * 70)

    # ① 三型
    r = parse_bridge_with_llm("c1arch 跨度100米中承式拱桥，矢高20米，11对吊杆", cfg)
    check("①a 拱桥解析", r.ok and r.kind == "arch" and r.params["span"] == 100.0
          and r.params["rise"] == 20.0 and r.params["n_hanger"] == 11
          and r.params["hanger_kind"] == "CABLE" and r.params["udl"] == 30.0,
          f"→ {r.params}" if r.ok else f"→ {r.error}")

    r = parse_bridge_with_llm("c2truss 跨度60米桁架桥，8个节间，桁高7.5米", cfg)
    check("①b 桁架桥解析", r.ok and r.kind == "truss" and r.params["n_panel"] == 8
          and r.params["depth"] == 7.5 and r.params["node_load"] == 100.0,
          f"→ {r.params}" if r.ok else f"→ {r.error}")

    r = parse_bridge_with_llm("c3beam 两跨连续梁桥，每跨40米", cfg)
    check("①c 梁桥解析", r.ok and r.kind == "beam_bridge" and r.params["n_spans"] == 2
          and r.params["b"] == 8.0 and r.params["material"] == "混凝土 C50",
          f"→ {r.params}" if r.ok else f"→ {r.error}")

    # ② 围栏
    r = parse_bridge_with_llm("c4fenced 跨度80米拱桥", cfg)
    check("② Markdown 围栏可剥离", r.ok and r.params["span"] == 80.0,
          f"→ span={r.params.get('span')}")

    # ③ 非法 JSON
    r = parse_bridge_with_llm("c5bad 随便一句", cfg)
    check("③ 非法 JSON 被拒绝", (not r.ok) and "JSON" in r.error, f"→ {r.error[:36]}")

    # ④ 缺字段
    r = parse_bridge_with_llm("c6missing 跨度100米拱桥", cfg)
    check("④ 缺字段按经验值补齐并提示",
          r.ok and r.params["rise"] == 20.0 and r.params["n_hanger"] == 9
          and len(r.notes) >= 2,
          f"→ rise={r.params.get('rise')} nh={r.params.get('n_hanger')} "
          f"提示{len(r.notes)}条")

    # ⑤ 单位哨兵
    r = parse_bridge_with_llm("c7mm 跨度120000mm的拱桥", cfg)
    ok5 = (r.ok and r.params["span"] == 120.0 and r.params["rise"] == 24.0
           and r.params["udl"] == 30.0
           and any("疑为" in n for n in r.notes))
    check("⑤ 单位哨兵（跨度/矢高/荷载）", ok5,
          f"→ span={r.params.get('span')} rise={r.params.get('rise')} "
          f"udl={r.params.get('udl')}")

    # ⑥ kind 冲突
    r = parse_bridge_with_llm("c8conflict 跨度60米桁架桥", cfg, kind="arch")
    ok6 = (r.ok and r.kind == "arch" and any("按你选的" in n for n in r.notes)
           and r.params["rise"] > 0)
    check("⑥ kind 冲突以调用方为准并提示", ok6,
          f"→ kind={r.kind} {r.notes[:1]}")

    # ⑦ kind 非法
    r = parse_bridge_with_llm("c9badkind 斜拉桥200米", cfg)
    check("⑦ kind 非法被拒绝", (not r.ok) and "kind" in r.error, f"→ {r.error[:36]}")

    # ⑧ 越界输出节点表
    r = parse_bridge_with_llm("c10mesh 跨度100米拱桥", cfg)
    ok8 = (r.ok and "nodes" not in r.params and "elements" not in r.params
           and "coordinates" not in r.params)
    check("⑧ 越界输出节点表被丢弃", ok8, f"→ {sorted(r.params)}")

    # ⑨ 失败回退（端到端）
    doc, src, diag = ModelDoc.from_text("c5bad 跨度60米桁架桥", llm_config=cfg)
    ok9 = (doc.kind == "truss" and "桥梁" in src and "回退" in src
           and not doc.validate())
    check("⑨ LLM 失败 → 回退规则仍能建模", ok9, f"→ {src} kind={doc.kind}")

    # ⑩ 与规则解析对齐
    text = "c11parity 跨度100米拱桥，矢高20米，11对吊杆，30kN/m"
    r = parse_bridge_with_llm(text, cfg)
    from bridge_spec import extract_bridge_params

    rule_kind, rule_p, _ = extract_bridge_params(text)
    same = (r.ok and rule_kind == "arch"
            and abs(r.params["span"] - rule_p["span"]) < 1e-9
            and abs(r.params["rise"] - rule_p["rise"]) < 1e-9
            and r.params["n_hanger"] == rule_p["n_hanger"]
            and abs(r.params["udl"] - rule_p["udl"]) < 1e-9)
    check("⑩ LLM 与规则解析结果可比", same,
          f"→ LLM {r.params.get('span')}/{r.params.get('rise')}/"
          f"{r.params.get('n_hanger')} vs 规则 {rule_p.get('span')}/"
          f"{rule_p.get('rise')}/{rule_p.get('n_hanger')}")

    # ⑪ 无 key
    doc, src, diag = ModelDoc.from_text("跨度120米的拱桥，矢高24米，9对吊杆")
    check("⑪ 无 key 时走规则", "rule" in src and "桥梁" in src, f"→ {src}")

    # ⑫ 规则路径也过哨兵
    doc, src, diag = ModelDoc.from_text("跨度120000的拱桥")
    ok12 = (doc.params["span"] == 120.0 and diag is not None
            and any("疑为" in n for n in diag.notes))
    check("⑫ 规则路径单位哨兵生效", ok12,
          f"→ span={doc.params['span']} 提示={diag.notes[:1] if diag else None}")

    # ⑬ prompt 与 schema 的硬约束
    schema = build_bridge_schema_text()
    check("⑬ schema 不含 nodes/elements",
          "nodes" not in schema and "elements" not in schema,
          f"→ {len(schema)} 字符")
    check("⑭ prompt 明确禁止输出网格",
          "不要输出节点表" in BRIDGE_SYSTEM_PROMPT,
          "→ 第 0 条")

    # ⑮ LLM 结果可直接求解
    doc, src, diag = ModelDoc.from_text("c1arch 跨度100米拱桥，矢高20米，11对吊杆",
                                        llm_config=cfg)
    ok15 = (src.startswith("llm") and not doc.validate()
            and "*NODE" in doc.to_dat() and len(doc.summary()["lines"]) > 0)
    check("⑮ LLM 参数 → build → .dat 全链路", ok15,
          f"→ {src} 节点{len(doc.model.nodes)} 单元{len(doc.model.elements)}")

    srv.shutdown()
    print("-" * 70)
    print(f"结果：{'全部通过' if fails == 0 else f'{fails} 项失败'}")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
