"""example_suspension_1000m_nl.py
===================================
主跨 1000 m 地锚式悬索桥算例 —— **自然语言入口**。

与 example_suspension_1000m.py 是同一个算例，区别只在一行：这里不是直接调
`build_suspension_3d(**PARAMS)`，而是把一句中文描述交给 `ModelDoc.from_text()`
走「意图分类 → 规则解析 → 建造器展开」链路，证明同一个算例可以**用一句话**
完整描述并被程序识别、求解、出报告。

运行
----
    python example_suspension_1000m_nl.py

自然语言描述（即下面 DESC 这一句，可直接粘贴到界面输入框）：
    主跨 1000 米的地锚式悬索桥，6 车道，矢跨比 1/10，加劲梁高 3.5 米，
    桥宽 33.5 米，桥面恒载 160 kN/m，二期铺装 60 kN/m，吊索间距 16 米，
    主缆面积 280000 mm²，吊索面积 3000 mm²，从跨中对称吊装主梁，
    主梁合拢后再铺装

产出（out/susp1000_nl/）
    nl.dat / nl_cable.txt / report.html / 计算书.docx
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from model_doc import ModelDoc
from report_3d import build_document, write_html
from report_docx import build_suspension_report

OUT = Path(__file__).parent / "out" / "susp1000_nl"

# 自然语言描述（唯一真源）：一句话，不写任何节点/单元。
DESC = (
    "主跨 1000 米的地锚式悬索桥，6 车道，矢跨比 1/10，加劲梁高 3.5 米，"
    "桥宽 33.5 米，桥面恒载 160 kN/m，二期铺装 60 kN/m，吊索间距 16 米，"
    "主缆面积 280000 mm²，吊索面积 3000 mm²，从跨中对称吊装主梁，"
    "主梁合拢后再铺装"
)


def main() -> None:
    doc, source, diag = ModelDoc.from_text(DESC)
    if diag.notes:
        print("解析提示（默认值）：")
        for n in diag.notes:
            print(f"  · {n}")

    errs = doc.validate()
    if errs:
        print("校验失败：")
        for e in errs:
            print(f"  ✗ {e}")
        return

    m = doc.model
    info = doc.info
    print("=" * 72)
    print(f"算例：{m.title}   （来源：{source}）")
    print(f"跨径布置  {info['side_span']:.1f} + {info['main_span']:.1f} "
          f"+ {info['side_span']:.1f} m　矢跨比 1/{1 / info['sag_ratio']:.0f}")
    print(f"恒载  一期 {info['deck_load_q1']/1e3:.0f} + 二期 "
          f"{info['deck_load_q2']/1e3:.0f} = {info['deck_load']/1e3:.0f} kN/m")
    print(f"车道  {doc.params['lane_count']} 车道，横向分布系数 m = "
          f"{info['transverse_distribution']:.3f}")
    print(f"施工阶段模拟 = {doc.params['erection']}，每次对称吊装 "
          f"{doc.params['erection_panels']} 对")
    print("=" * 72)

    res = doc.solve(out_dir=OUT, dat_name="nl.dat")
    md = res.meta
    print(f"\n成桥态：H = {md.get('H_kN', 0):,.0f} kN，"
          f"矢高 {md.get('sag_m', 0):.2f} m，"
          f"主缆 Tmax = {md.get('tension_max_kN', 0):,.0f} kN，"
          f"吊索 {md.get('hanger_T_min_kN', 0):,.0f}~"
          f"{md.get('hanger_T_max_kN', 0):,.0f} kN，收敛 = {res.converged}")

    if res.erection:
        print("\n施工阶段（从跨中对称吊装 → 合拢 → 铺装）：")
        for r in res.erection:
            print(f"  阶段 {r['stage']:>2}  {r['name']:<3}  "
                  f"H = {r['H']/1e3:>8,.0f} kN  矢高 {r['sag']:>7.2f} m  "
                  f"跨中高程 {r['midz']:>6.2f} m")

    # ---- 报告与计算书（params 用报告期望的键名，side_span 取建造器真值）----
    report_params = {
        "system": doc.params["system"],
        "side_span": info["side_span"],
        "B": doc.params["B"], "h": doc.params["h"],
        "q": doc.params["q"],
        "A": doc.params["A"], "Ah": doc.params["Ah"],
        "lane_count": doc.params["lane_count"],
    }
    rep = build_document(res, m, out_dir=OUT)
    html_path = write_html(rep, OUT / "report.html")
    docx_path = build_suspension_report(
        model=m, info=info, res=res, params=report_params,
        out_path=OUT / "计算书.docx",
        project=m.title,
        fig_dir=OUT / "figs",
    )
    print(f"\n已产出：")
    print(f"  HTML 报告   {html_path}")
    print(f"  Word 计算书 {docx_path}")
    print(f"  原始输出    {res.result_path}")


if __name__ == "__main__":
    main()
