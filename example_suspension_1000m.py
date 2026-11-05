"""example_suspension_1000m.py
================================
主跨 1000 m 地锚式悬索桥算例（6 车道，从跨中对称吊装主梁 → 合拢 → 二期铺装）。

为什么单独一个算例脚本
----------------------
这是"自然语言 → 有限元"链路里悬索桥的**基准算例**：一份参数自洽、可复现、
含施工阶段的完整算例。参数按 JTG/T D65-05-2015 拟定，主缆线形由恒载找形确定，
施工阶段按"给定主缆下料长度 L0 的逐阶段找形"模拟——铺装态矢高必须精确重现
成桥矢高，这是本算例正确性的自洽判据。

运行
----
    python example_suspension_1000m.py

产出（out/susp1000/）
    susp1000.dat            求解器输入
    susp1000_cable.txt      求解器原始输出
    report.html             自包含 HTML 报告（含施工阶段章节）
    计算书.docx             悬索桥成桥态 + 施工阶段计算书

单元单位：m / N / Pa。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from suspension_3d import build_suspension_3d
from solver_3d import solve
from report_3d import build_document, render_html, write_html
from report_docx import build_suspension_report

OUT = Path(__file__).parent / "out" / "susp1000"

# ---------------------------------------------------------------- 结构参数
# 主跨 1000 m、矢跨比 1/10、6 车道钢箱梁加劲梁、双塔三跨地锚式。
#
# 加劲梁（6 车道钢箱梁）
#   B = 33.5 m：6×3.75 m 车道 + 中央分隔带 + 两侧硬路肩/检修道
#   h = 3.5 m：L/286，钢箱梁常规高跨比区间
#
# 主缆（每索面一根，全桥两根，这里按单索面平面模型计）
#   A = 280000 mm²（0.28 m²），高强钢丝 1770 MPa 级
#
# 恒载拆分（施工阶段用）
#   一期 Q1 = 100 kN/m（钢箱梁自重）
#   二期 Q2 =  60 kN/m（桥面铺装 + 护栏）
#   合计    = 160 kN/m
#
# 车道（JTG D60-2015 表 4.3.1-5 多车道横向折减）
#   6 车道 × 折减 0.55 × 偏载增大 1.15 = 横向分布系数 m = 3.795

PARAMS = dict(
    system="EARTH",
    main_span=1000.0,
    sag_ratio=0.10,           # 矢跨比 1/10 → 矢高 100 m
    panel=16.0,               # 吊索间距 16 m
    B=33.5,                   # 6 车道桥宽
    h=3.5,                    # 钢箱梁高
    tf=0.032, tw=0.022,       # 顶/底板厚（按主跨 1000 m 适当加厚）
    tower_b=4.0, tower_h=5.0, # 桥塔矩形截面（实心）
    n_tower_seg=8,
    main_area_mm2=280000.0,   # 单根主缆
    hanger_area_mm2=4000.0,   # 单根吊索（吊索安全系数 ≥ 2.5）
    q_deck=160e3,             # 桥面总恒载 N/m
    q2_deck=60e3,             # 二期恒载 → 一期 = 100 kN/m
    erection=True,            # 生成施工阶段模拟方案
    erection_panels=2,        # 每次对称吊装 2 对（= 4 段）
    lane_count=6,             # 6 车道
    lane_reduction=0.55,      # JTG D60 6 车道横向折减
    eccentricity=1.15,        # 偏载增大
)


def main() -> None:
    m, info = build_suspension_3d(**PARAMS)

    print("=" * 72)
    print(f"算例：{m.title}")
    print(f"跨径布置  {info['side_span']:.1f} + {info['main_span']:.1f} "
          f"+ {info['side_span']:.1f} m　矢跨比 1/{1 / info['sag_ratio']:.0f}")
    print(f"恒载  一期 {info['deck_load_q1']/1e3:.0f} + 二期 "
          f"{info['deck_load_q2']/1e3:.0f} = {info['deck_load']/1e3:.0f} kN/m")
    print(f"车道  {PARAMS['lane_count']} 车道，横向分布系数 m = "
          f"{info['transverse_distribution']:.3f}")
    print(f"规模  {info['n_nodes']} 节点 / {info['n_elements']} 单元 / "
          f"吊索 {info['n_hanger']} 根")
    print("=" * 72)

    res = solve(m, out_dir=OUT, dat_name="susp1000.dat")
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

    # ---- 报告与计算书 ----
    rep = build_document(res, m, out_dir=OUT)
    html_path = write_html(rep, OUT / "report.html")
    docx_path = build_suspension_report(
        model=m, info=info, res=res, params=dict(PARAMS),
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
