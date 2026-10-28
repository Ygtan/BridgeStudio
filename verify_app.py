"""界面回归测试（Streamlit AppTest，无头运行）。

C 方案把①的梁与⑥的桥合并成一个入口，最容易出现的回归不是崩溃，而是
"看起来在、其实数据没换"——所以断言要落在 session_state 的 doc.kind 上，
而不是只断言"页面没报错"。
"""

from __future__ import annotations

import sys

from streamlit.testing.v1 import AppTest

PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<30} {detail}")


def _errors(at: AppTest) -> list[str]:
    return [f"{e.value}" for e in at.exception] if at.exception else []


def main() -> int:
    print("=" * 74)
    print("界面回归（AppTest）")
    print("=" * 74)

    at = AppTest.from_file("app.py", default_timeout=120).run()

    print("\n[首次加载]")
    check("无异常", not _errors(at), _errors(at)[:1])
    _subs = [s.value for s in at.subheader]
    _marks = "①②③④⑤"
    check("区块①②③④⑤齐全",
          all(any(m in s for s in _subs) for m in _marks),
          " / ".join(_subs))
    check("⑥三维梁格区块存在", any("三维梁格" in s for s in _subs),
          "新增的三维梁格独立区块")
    check("默认模型是单梁", at.session_state["doc"].kind == "beam",
          at.session_state["doc"].summary()["title"])

    print("\n[点示例：拱桥]")
    at.button(key="ex3").click().run()
    check("无异常", not _errors(at), _errors(at)[:1])
    check("类型切换为 arch", at.session_state["doc"].kind == "arch",
          at.session_state["doc"].summary()["lines"][0][:36])
    check("解析来源标注桥梁", "桥梁" in at.session_state["source"],
          at.session_state["source"])
    # 桥型没有 anastruct 能力：④ 应给出指引而不是空着
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
    # 「解析并更新模型」按钮没有 key，按 label 定位
    _parse = next(b for b in at.button if b.label == "解析并更新模型")
    _parse.click().run()
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

    print("\n[FEM 求解按钮]")
    _solve_btns = [b.label for b in at.button if "FEM-Eigen" in b.label]
    check("⑤求解按钮存在", bool(_solve_btns), str(_solve_btns))

    print("\n[⑥ 三维梁格求解]")
    _g3d_btn = next((b for b in at.button if b.key == "g3d_solve"), None)
    check("⑥求解按钮存在", _g3d_btn is not None)
    if _g3d_btn is not None:
        _g3d_btn.click().run()
        check("⑥求解无异常", not _errors(at), _errors(at)[:1])
        _r = at.session_state.get("g3d_result")
        check("⑥结果已缓存", _r is not None)
        if _r:
            _st = at.session_state.get("g3d_stats", {})
            check("⑥默认模型 222 单元", _st.get("elements") == 222,
                  f"{_st.get('elements')}")
            check("⑥三阶段施工", _st.get("stages") == 3, f"{_st.get('stages')}")
            check("⑥活载包络 3 阶段", len(_r.get("live", {})) == 3,
                  str(sorted(_r.get("live", {}))))
            _meta = at.session_state.get("g3d_meta", {})
            _dw = _r["disp"].get((_meta.get("mid_node", 2022), 3), (0, 0))
            check("⑥中跨跨中挠度合理",
                  -0.03 < _dw[0] < -0.001, f"{_dw[0]*1000:.2f} mm")

    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 74)
    print(f"  {n_pass}/{len(PASS)} 通过")
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
