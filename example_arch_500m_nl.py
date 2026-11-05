"""example_arch_500m_nl.py
===========================
大跨度钢箱拱桥 **拱肋分段吊装 + 斜拉扣索辅助 + 合拢 + 吊杆主梁 + 铺装**
算例 —— 自然语言入口。

与 example_cable_stayed_nl.py / example_suspension_1000m_nl.py 同一思路：
把一句中文描述交给 `ModelDoc.from_text()`，走「意图分类 → 规则解析 →
建造器展开 → 内核求解 → 报告」全链路，证明大跨拱桥的**整条施工链**
也能用一句话完整描述并被程序识别。

这里重点演示的是**施工阶段**：程序从「拱肋分段吊装 / 斜拉扣索 / 合拢 /
吊杆主梁 / 铺装」这几个词里推断出要生成施工阶段序列（*STAGE），再走线性
阶段内核逐阶段求解，输出每一根临时扣索在每一个阶段的索力过程、以及
合拢拆索后主梁/拱肋的内力与挠度。

运行
----
    python example_arch_500m_nl.py

自然语言描述（即下面 DESC 这一句，可直接粘贴到界面输入框）：
    大跨度拱桥 500 米，矢高 100 米，拱肋分段吊装，用斜拉扣索辅助吊装，
    合拢后安装吊杆、主梁，主梁合拢后铺装

产出（out/arch_nl/）
    nl.dat / nl_summary.txt / report.html
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from model_doc import KIND_LABELS, ModelDoc
from report_3d import build_document, write_html

OUT = Path(__file__).parent / "out" / "arch_nl"

# 自然语言描述的唯一真源在 examples/arch_500m.txt（与 run_nl.py 共用），
# 这里从文件读，避免脚本与输入文件两处漂移。
DESC = (Path(__file__).parent / "examples" / "arch_500m.txt").read_text(
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
    print(f"跨径  {info['span']:.0f} m　矢高 {info['rise']:.0f} m　"
          f"矢跨比 1/{info['sag_ratio']:.1f}")
    print(f"拱肋  {info['n_arch_seg']} 段（半跨 {info['n_seg_half']} 段吊装），"
          f"吊杆 {info['n_hanger']} 根，临时扣索 {len(info['cable_ids'])} 根")
    print(f"恒载  一期 {doc.params['q_deck']:.0f} + 二期 "
          f"{doc.params['q2_deck']:.0f} = {doc.params['q_deck'] + doc.params['q2_deck']:.0f} kN/m")
    print(f"施工阶段 {info['n_stages']} 个：拱脚段 → 对称吊装+扣索张拉 ×"
          f"{info['n_seg_half'] - 2} → 拱顶合拢 → 拆扣索 → 吊杆主梁 → 二期铺装")
    print("=" * 74)

    res = doc.solve(out_dir=OUT, dat_name="nl.dat")
    stages = sorted({s for s, _ in res.stage_cables})

    print(f"\n求解：{res.solver}，收敛 = {res.converged}，"
          f"参与记录的施工阶段 {len(stages)} 个")

    # 临时扣索张拉目标（左右对称）与全过程峰值
    print("\n临时扣索（斜拉扣挂法）张拉目标与全过程：")
    left = info["cable_left"]
    for e in left:
        tgt = info["cable_targets"][e]
        vals = [(s, res.stage_cables[(s, e)]["T"])
                for s in stages if (s, e) in res.stage_cables]
        tmax = max(v for _, v in vals)
        tmin = min(v for _, v in vals)
        last = vals[-1][1]
        print(f"  左扣索 E{e:>5}: 目标 {tgt/1e3:6.0f} kN　"
              f"峰值 {tmax/1e3:6.0f}　末阶段(拆索前) {last/1e3:6.0f}　"
              f"最小 {tmin/1e3:6.0f} kN")

    # 先张扣索的卸载过程（拱圈逐渐闭合后扣索退出工作）
    eid = info["cable_left"][0]
    print(f"\n先张扣索 E{eid} 的全过程（张拉 → 被后续吊装卸载，符合施工物理）：")
    for s in stages:
        if (s, eid) in res.stage_cables:
            v = res.stage_cables[(s, eid)]
            print(f"  阶段 {s:>2}: T = {v['T']/1e3:>7,.1f} kN")

    # 成桥态位移与内力
    fd = res.final_disp
    arch_dz = max(abs(fd[(n, 3)]) for n in info["arch_node_ids"])
    deck_dz = max(abs(fd[(n, 3)]) for n in info["deck_node_ids"])
    fb = res.final_beam
    Nmax = max(abs(v["N"]) for (e, d), v in fb.items() if 100 <= e <= 199)
    Mmax = max(abs(v["My"]) for (e, d), v in fb.items() if 100 <= e <= 199)
    print(f"\n成桥态（拆索 + 吊杆主梁 + 铺装后）：")
    print(f"  拱肋最大 |挠度| = {arch_dz:.3f} m（{info['span']/arch_dz:.0f} 分之一跨）")
    print(f"  主梁最大 |挠度| = {deck_dz:.3f} m")
    print(f"  拱肋轴力峰值 = {Nmax/1e6:.0f} MN，弯矩峰值 = {Mmax/1e6:.0f} MN·m")

    # 校核
    print("\n工程校核：")
    for c in res.checks(m):
        if "索" in c.name or "阶段" in c.name or "挠度" in c.name or "应力" in c.name:
            print(f"  {'PASS' if c.passed else 'FAIL'}  {c.name}: {c.detail[:72]}")

    # ---- 报告 ----
    rep = build_document(res, m, out_dir=OUT,
                         kind_label=KIND_LABELS[doc.kind])
    html_path = write_html(rep, OUT / "report.html")
    print(f"\n已产出：")
    print(f"  HTML 报告   {html_path}")
    print(f"  原始输出    {res.result_path}")


if __name__ == "__main__":
    main()
