"""example_cable_stayed_nl.py
==============================
双塔三跨斜拉桥 **悬臂施工 + 张拉** 算例 —— 自然语言入口。

与 example_suspension_1000m_nl.py 同一思路：把一句中文描述交给
`ModelDoc.from_text()`，走「意图分类 → 规则解析 → 建造器展开 → 内核求解
→ 报告」全链路，证明斜拉桥的**施工过程**也能用一句话完整描述并被程序识别。

这里重点演示的是**施工阶段**，不是成桥态：程序从「悬臂施工 / 从塔向两侧
逐段张拉 / 二次调索」这几个词里推断出要生成施工阶段序列（*STAGE），再走
线性阶段内核逐阶段求解，输出每一根索在每一个阶段的索力过程。

运行
----
    python example_cable_stayed_nl.py

自然语言描述（即下面 DESC 这一句，可直接粘贴到界面输入框）：
    主跨 400 米的双塔三跨斜拉桥，边跨 160 米，塔高 120 米，每塔每侧 10 对索，
    主梁高 3 米，桥宽 24 米，桥面恒载 180 kN/m，二期铺装 50 kN/m，
    单索面积 10000 平方毫米，悬臂施工，从塔向两侧逐段张拉，合拢后二次调索

产出（out/cs_nl/）
    nl.dat / nl_summary.txt / report.html
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from model_doc import ModelDoc
from report_3d import build_document, write_html

OUT = Path(__file__).parent / "out" / "cs_nl"

# 自然语言描述的唯一真源在 examples/cable_stayed_400m.txt（与 run_nl.py 共用），
# 这里从文件读，避免脚本与输入文件两处漂移。
DESC = (Path(__file__).parent / "examples" / "cable_stayed_400m.txt").read_text(
    encoding="utf-8").strip()


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
    print("=" * 74)
    print(f"算例：{m.title}   （来源：{source}）")
    print(f"跨径布置  {info['x_supports'][1]:.1f} + {info['x_supports'][2]-info['x_supports'][1]:.1f} "
          f"+ {info['x_supports'][1]:.1f} m　塔高 {doc.params['Ht']:.0f} m")
    print(f"拉索  {info['n_cable_actual']} 根（每塔每侧 {doc.params['n']} 对），"
          f"单索面积 {doc.params['A']:.0f} mm²")
    print(f"恒载  一期 {doc.params['q']-doc.params['q2_deck']:.0f} + 二期 "
          f"{doc.params['q2_deck']:.0f} = {doc.params['q']:.0f} kN/m")
    print(f"施工阶段模拟 = {doc.params['staged']}，二次调索 = {doc.params['retune']}")
    print(f"施工阶段数 = {info['n_stages']}（塔+0号块 → 悬臂张拉 ×{doc.params['n']} → "
          f"合拢 → 二期铺装+二次调索）")
    print("=" * 74)

    res = doc.solve(out_dir=OUT, dat_name="nl.dat")
    stages = sorted({s for s, _ in res.stage_cables})

    print(f"\n求解：{res.solver}，收敛 = {res.converged}，"
          f"参与记录的施工阶段 {len(stages)} 个")

    # 施工阶段序列（每阶段张拉几根、命中目标）
    print("\n悬臂施工 + 张拉过程：")
    for s in stages:
        devs = [abs(res.stage_cables[(s, e)]["dev"]) for e in info["cable_ids"]
                if (s, e) in res.stage_cables and res.stage_cables[(s, e)]["target"] > 0]
        n_tension = len(devs)
        mx = max(devs) if devs else 0.0
        tag = "张拉" if n_tension else "（无新张拉）"
        print(f"  阶段 {s:>2}  {tag:<8} {n_tension:>2} 根，最大 |dev| = {mx:.1e}")

    # 挑一根先张的索看全过程：张拉到目标 → 被后张的索带偏 → 二次调索拉回
    eid = info["cable_ids"][0]
    print(f"\n先张索 {eid} 的全过程（一次张拉 → 被带偏 → 二次调索闭环）：")
    for s in stages:
        if (s, eid) in res.stage_cables:
            v = res.stage_cables[(s, eid)]
            print(f"  阶段 {s:>2}: T = {v['T']/1e3:>7,.1f} kN  "
                  f"相对设计目标偏离 {v['devDes']*100:>5.1f}%")

    # 校核
    print("\n施工阶段校核：")
    for c in res.checks(m):
        if "索" in c.name or "阶段" in c.name:
            print(f"  {'PASS' if c.passed else 'FAIL'}  {c.name}: {c.detail[:70]}")

    # ---- 报告 ----
    rep = build_document(res, m, out_dir=OUT)
    html_path = write_html(rep, OUT / "report.html")
    print(f"\n已产出：")
    print(f"  HTML 报告   {html_path}")
    print(f"  原始输出    {res.result_path}")


if __name__ == "__main__":
    main()
