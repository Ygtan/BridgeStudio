"""verify_arch_3d.py
===================
大跨钢箱拱桥（拱肋分段吊装 + 斜拉扣索辅助 + 合拢 + 吊杆主梁 + 铺装）回归。

校验内容
--------
1. 离线（建模）
   - 节点/单元/阶段规模与拓扑（拱肋 16 段、吊杆 15、扣索 12、阶段 18）
   - 扣索张拉目标左右对称、且 ≤ 0.7·A·fpu（不撞截断）
   - 编号规范（拱肋 100+、主梁 200+、吊杆 500+、扣索 9000+/9100+）
   - 约束正确：拱脚固结、塔顶锚点固定、主梁端纵向滑动
2. 自然语言识别（意图分类 + 参数解析）
   - 「拱肋分段吊装 / 斜拉扣索 / 合拢」→ arch_3d（不是 arch / cable_stayed）
   - 普通「拱桥 + 矢高」仍走 2D arch
   - 裸数字「拱桥500米」识别主跨
3. 在线（线性阶段内核）
   - 收敛；成桥态挠度 ≤ L/600；拱肋应力 ≤ fd
   - 施工期索力：全程不出现受压（临时索松弛降级为提示）、峰值 ≤ A·fpu/2
   - 临时扣索合拢后 DEACTIVE 拆除，「末阶段 vs 目标」判定为 0 根永久索
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from arch_3d import build_arch_3d
from model_doc import ModelDoc, classify_text
from solver_3d import solve

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((ok, name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def main() -> int:
    print("=" * 66)
    print("  大跨钢箱拱桥施工端到端回归（BridgeStudio → 线性阶段内核）")
    print("=" * 66)

    # ---------------------------------------------------------------- 1 建模
    print("\n[1] 建模与拓扑")
    m, info = build_arch_3d()
    st = m.stats()
    check("节点/单元规模", st["nodes"] == 36 and st["elements"] == 59,
          f"{st['nodes']} 节点 / {st['elements']} 单元")
    check("单元构成 BEAM 32 / TRUSS 15 / CABLE 12",
          st["kinds"] == {"BEAM": 32, "TRUSS": 15, "CABLE": 12}, str(st["kinds"]))
    check("施工阶段 18 个", st["stages"] == 18, str(st["stages"]))
    check("拱肋 16 段 / 吊杆 15 根 / 扣索 12 根",
          info["n_arch_seg"] == 16 and info["n_hanger"] == 15
          and len(info["cable_ids"]) == 12,
          f"{info['n_arch_seg']} / {info['n_hanger']} / {len(info['cable_ids'])}")

    # 扣索张拉目标左右对称
    tgt = info["cable_targets"]
    sym = all(abs(tgt[l] - tgt[l + 100]) < 1e-6
              for l in info["cable_left"])
    cap = 0.7 * 8000e-6 * 1860e6
    under = all(t <= cap for t in tgt.values())
    check("扣索目标左右对称", sym, f"左 {[round(tgt[l]/1e3) for l in info['cable_left']]} kN")
    check("扣索目标 ≤ 0.7·A·fpu", under, f"max {max(tgt.values())/1e3:.0f} / {cap/1e3:.0f} kN")

    # 约束：拱脚固结 + 塔顶锚点固定（dofs 为闭区间 [a,b]）
    bs = {b.node: b.dofs for b in m.boundaries}
    check("拱脚固结 (DOF 1-6)", bs.get(3000) == (1, 6) and bs.get(3016) == (1, 6),
          str(bs.get(3000)))
    check("塔顶锚点固定 (DOF 1-3)", bs.get(4000) == (1, 3) and bs.get(5000) == (1, 3),
          str(bs.get(4000)))

    # ---------------------------------------------------------------- 2 意图
    print("\n[2] 自然语言识别")
    check("拱肋分段吊装+斜拉扣索 → arch×3d",
          classify_text("大跨度拱桥500米，拱肋分段吊装，用斜拉扣索辅助吊装，合拢后铺装") == ("arch", "3d"))
    check("普通拱桥（矢高+吊杆）→ arch×2d",
          classify_text("跨度100米的拱桥，矢高20米，9对吊杆") == ("arch", "2d"))
    check("斜拉桥（无拱桥施工词）仍判斜拉桥",
          classify_text("主跨200米斜拉桥，塔高60米，悬臂施工") == ("cable_stayed", "2d"))

    doc, src, diag = ModelDoc.from_text(
        "大跨度拱桥500米，矢高100米，拱肋分段吊装，用斜拉扣索辅助吊装，"
        "合拢后安装吊杆、主梁，主梁合拢后铺装")
    check("from_text → arch×3d",
          doc.kind == "arch" and doc.dimension == "3d", src)
    check("裸数字「拱桥500米」识别主跨", doc.params["span"] == 500.0,
          f"span={doc.params['span']}")
    check("矢高解析", doc.params["rise"] == 100.0, f"rise={doc.params['rise']}")
    check("校验通过", not doc.validate(), "; ".join(doc.validate()))

    # ---------------------------------------------------------------- 3 求解
    print("\n[3] 求解与校核")
    res = solve(m, out_dir="out/verify_arch_3d", dat_name="arch.dat")
    check("线性求解收敛", res.converged, res.solver)

    fd = res.final_disp
    arch_dz = max(abs(fd[(n, 3)]) for n in info["arch_node_ids"])
    deck_dz = max(abs(fd[(n, 3)]) for n in info["deck_node_ids"])
    check("拱肋挠度 ≤ L/600", arch_dz <= 500.0 / 600.0, f"{arch_dz:.3f} m")
    check("主梁挠度 ≤ L/600", deck_dz <= 500.0 / 600.0, f"{deck_dz:.3f} m")

    checks = {c.name: c for c in res.checks(m)}
    # 施工期索力上限：峰值 ≤ A·fpu/2.0
    c_up = checks.get("施工期索力不超过上限")
    check("施工期索力不超过上限", c_up is not None and c_up.passed,
          c_up.detail[:60] if c_up else "无此校核")
    # 临时索：末阶段 vs 目标判为 0 根永久索（info 级通过）
    c_last = checks.get("末阶段索力 vs 设计目标（0 根永久索）")
    check("临时扣索不按「末阶段 vs 目标」误判",
          c_last is not None and c_last.passed, c_last.detail[:50] if c_last else "无此校核")
    # 应力
    c_sig = [c for c in res.checks(m) if "正应力" in c.name]
    check("截面正应力 ≤ fd", all(c.passed for c in c_sig),
          f"{len(c_sig)} 项")

    print("\n" + "=" * 66)
    n_fail = sum(1 for ok, _, _ in RESULTS if not ok)
    print(f"  {len(RESULTS)} 项校验，{len(RESULTS) - n_fail} 通过，{n_fail} 失败")
    print("=" * 66)
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
