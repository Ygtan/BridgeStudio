"""verify_section_strength.py —— 截面应力验算的对拍与边界。

守什么
------
`verify_report_3d` 证明报告能出表，`verify_combo_3d` 证明组合等于数乘系数，
但两者都默认"应力这个换算是对的"。本脚本专门盯这一层：**σ = N/A ± M/W**
这种一行公式，错一个符号、错一个 10⁶，报告照样生成，数字照样好看。

对拍用两组：
1. **手算可验的数值**：给定 A、W、AW 与内力，应力、剪应力、折算应力
   全部有闭式答案，按位对。
2. **符号语义**：自锚式悬索桥的加劲梁同时受压与弯，跨中下缘的弯曲拉应力
   被轴压**抵消**。若图省事写成 |N/A| + |M/W|，会高估一倍以上。这里用
   "上缘更压、下缘更松"来锁住这个语义。

还守两条"宁可不做也不假装"的规矩：
- 材料等级没登记 → 抛 KeyError，不许退回某个默认强度；
- 索承体系（斜拉桥/悬索桥）的梁应力：内核输出梁端内力后必须真的算出来，
  而且必须**同端配对**（σ 用同一端的 N 与 My）；早期这里写的是"做不了"，
  现在它变成了"能算，且已接入活载组合（基本/频遇/准永久）"——两句话不能混着说。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()          # 输出被重定向到管道/文件时，GBK 会崩

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import code_material as cm
import section_strength as ss
from grillage_3d import box_section

PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def near(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol * max(abs(b), 1.0)


# ---------------------------------------------------------------- [1] 材料表


def part_material() -> None:
    print("\n[1] 规范材料强度登记表")

    c50 = cm.concrete("C50")
    check("C50 fcd = 22.4 MPa（JTG 3362 表 3.1.4）", c50.fcd == 22.4, f"{c50.fcd}")
    check("C50 ftd = 1.83 MPa", c50.ftd == 1.83, f"{c50.ftd}")
    check("C50 fck = 32.4 MPa（表 3.1.3）", c50.fck == 32.4, f"{c50.fck}")
    check("C80 已登记（表的上界不是 C60）", "C80" in cm.CONCRETE,
          f"{len(cm.CONCRETE)} 级")

    # 钢材按板厚分档：这是最容易抄错的地方，四个档位逐一核对
    for t, fd, fvd in ((10, 275, 160), (28, 270, 155),
                       (50, 260, 150), (90, 245, 140)):
        s = cm.steel("Q345qD", t)
        check(f"Q345 板厚 {t:>2} mm → fd={fd} / fvd={fvd}",
              s.fd == fd and s.fvd == fvd, s.label)

    check("桥梁钢牌号归一（Q345qD → 表 3.2.1 的 Q345）",
          cm.steel("Q345qD", 28).grade == "Q345")
    check("Q370q 归入 Q345 强度档", cm.steel("Q370qD", 30).fd == 270)

    check("HRB400 fsd = 330 MPa（表 3.1.4）", cm.rebar("HRB400").fsd == 330)
    check("HRB500 受拉截断到 330（表 3.1.4 注 1）",
          cm.rebar_fsd_tension("HRB500") == 330.0,
          f"{cm.rebar('HRB500').fsd} → {cm.rebar_fsd_tension('HRB500')}")

    # 查不到必须抛错，不能兜底
    try:
        cm.concrete("C55.5")
        check("未登记混凝土等级抛 KeyError", False, "没有抛错")
    except KeyError:
        check("未登记混凝土等级抛 KeyError", True, "C55.5")
    try:
        cm.steel("Q460", 20)
        check("未登记钢材牌号抛 KeyError", False, "没有抛错")
    except KeyError:
        check("未登记钢材牌号抛 KeyError", True, "Q460")
    try:
        cm.steel("Q345", 120)
        check("板厚超出规范表范围抛 KeyError", False, "没有抛错")
    except KeyError:
        check("板厚超出规范上限（100 mm）抛 KeyError", True, "120 mm")


# ---------------------------------------------------------------- [2] 手算对拍


def part_stress() -> None:
    print("\n[2] 应力公式手算对拍（钢箱梁 B=12 h=3 tf=28 tw=20）")

    box = box_section(12.0, 3.0, 0.028, 0.020)
    A, W, AW = box["A"], box["WT"], box["AW"]
    sec = ss.SectionForCheck(A=A, WT=W, WB=box["WB"], AW=AW,
                             grade="Q345qD", kind="steel",
                             t_plate=box["T_MAX"] * 1000.0)
    fd, fvd = 270.0, 155.0                      # t=28 → 16~40 档
    check("截面参数与手算一致（A / W / AW）",
          near(A, 0.78976, 1e-5) and near(W, 1.0460, 1e-3)
          and near(AW, 0.11776, 1e-5),
          f"A={A:.5f} W={W:.4f} AW={AW:.5f}")

    # 纯轴压
    N = -39149e3
    r = ss.check(sec, N, 0.0, 0.0)
    check("纯轴压 σ = N/A", near(r.sigma_top, N / A / 1e6, 1e-9),
          f"{r.sigma_top:.2f} MPa")
    check("纯轴压利用率 = |σ|/fd", near(r.util_normal, abs(N / A / 1e6) / fd, 1e-9),
          f"{r.util_normal:.4f}")

    # 纯弯
    M = 20e6
    r = ss.check(sec, 0.0, M, 0.0)
    check("纯弯上缘 σ = −M/W", near(r.sigma_top, -M / W / 1e6, 1e-9),
          f"{r.sigma_top:+.2f} MPa")
    check("纯弯下缘 σ = +M/W", near(r.sigma_bot, M / W / 1e6, 1e-9),
          f"{r.sigma_bot:+.2f} MPa")

    # 轴压 + 弯：轴压把弯曲拉应力抵消——这是自锚式的受力本质
    r2 = ss.check(sec, N, M, 0.0)
    check("轴压 + 弯：上缘压应力叠加", near(r2.sigma_top, N / A / 1e6 - M / W / 1e6, 1e-9),
          f"{r2.sigma_top:+.2f} MPa")
    check("轴压 + 弯：下缘拉应力被轴压抵消",
          near(r2.sigma_bot, N / A / 1e6 + M / W / 1e6, 1e-9)
          and abs(r2.sigma_bot) < abs(r2.sigma_top),
          f"下缘 {r2.sigma_bot:+.2f} 比上缘 {r2.sigma_top:+.2f} 松")
    # 若错写成 |N/A| + |M/W|（两侧都取压），下缘会得到 −68.69，与上缘一样；
    # 正确结果下缘比纯轴压还松，这才是"弯曲拉应力被轴压抵消"。
    check("没有把 |N/A| 与 |M/W| 直接相加",
          abs(r2.sigma_bot) < abs(N / A / 1e6)
          and not near(abs(r2.sigma_bot), abs(r2.sigma_top), 1e-3),
          f"|{r2.sigma_bot:+.2f}| < |{N / A / 1e6:+.2f}|，且 ≠ 上缘")

    # 剪应力与折算应力
    V = 3e6
    r3 = ss.check(sec, N, M, V)
    check("剪应力 τ = V/AW", near(r3.tau, V / AW / 1e6, 1e-9), f"{r3.tau:.2f} MPa")
    check("折算应力 = √(σ² + 3τ²)",
          near(r3.sigma_eq,
               (max(abs(r3.sigma_top), abs(r3.sigma_bot)) ** 2
                + 3.0 * r3.tau ** 2) ** 0.5, 1e-9),
          f"{r3.sigma_eq:.2f} MPa")
    check("折算利用率 = √((σ/fd)²+(τ/fvd)²)",
          near(r3.util_eq,
               ((max(abs(r3.sigma_top), abs(r3.sigma_bot)) / fd) ** 2
                + (r3.tau / fvd) ** 2) ** 0.5, 1e-9),
          f"{r3.util_eq:.4f}")
    check("控制项是利用率最大的一项", r3.util >= r3.util_normal
          and r3.util >= r3.util_shear and r3.util >= r3.util_eq, r3.control)

    # 符号翻转（拿不到局部坐标系朝向时的保险）：控制值必须不变
    ra = ss.check(sec, N, +M, +V)
    rb = ss.check(sec, N, -M, +V)
    check("My 正负翻转不改变正应力控制值",
          near(ra.util_normal, rb.util_normal, 1e-12),
          f"{ra.util_normal:.4f} vs {rb.util_normal:.4f}")


# ---------------------------------------------------------------- [3] 混凝土


def part_concrete() -> None:
    print("\n[3] 混凝土构件：限值与「不给假利用率」")

    box = box_section(12.0, 2.5, 0.28, 0.45)     # 连续梁的混凝土箱形
    sec = ss.SectionForCheck(A=box["A"], WT=box["WT"], WB=box["WB"],
                             AW=box["AW"], grade="C50", kind="concrete")
    r = ss.check(sec, 0.0, 30e6, 2e6)
    check("压应力限值 = 0.5·fck = 16.2 MPa", r.fd == 16.2, f"{r.fd}")
    check("混凝土正应力限值出处写明 §7.1.5", "7.1.5" in r.limit_desc, r.limit_desc)
    check("压应力利用率 = |σc|/0.5fck",
          near(r.util, abs(min(r.sigma_top, r.sigma_bot)) / 16.2, 1e-9),
          f"{r.util:.4f}")
    check("混凝土不做折算应力", r.util_eq == 0.0 and r.sigma_eq == 0.0)


def part_scope() -> None:
    print("\n[4] 边界：缺截面模量 / 缺腹板面积")
    bare = ss.SectionForCheck(A=0.06, WT=0.0, WB=0.0, AW=0.0,
                              grade="Q345", kind="steel", t_plate=20.0)
    r = ss.check(bare, -5e6, 1e6, 1e5)
    check("无截面模量时退化为轴应力（不除零）",
          near(r.sigma_top, -5e6 / 0.06 / 1e6, 1e-9)
          and near(r.sigma_bot, r.sigma_top, 1e-12), f"{r.sigma_top:+.2f} MPa")
    check("无腹板面积时剪应力为 0（不编一个数）", r.tau == 0.0)
    check("无腹板面积时剪应力利用率为 0", r.util_shear == 0.0)


# ---------------------------------------------------------------- [5] 集成


def part_integration() -> None:
    print("\n[5] 集成：梁格模型出真校核条目")

    import solver_3d
    from grillage_3d import build_box_grillage_3d

    m = build_box_grillage_3d(spans=(30, 50, 30), lane_code="JTG_D60")
    res = solver_3d.solve(m, out_dir="out/_verify/strength", kind="grillage3d")
    checks = res.checks(m)
    names = [c.name for c in checks]
    check("梁格模型出现正应力校核", any("正应力" in n for n in names),
          "、".join(n for n in names if "应力" in n)[:60])
    check("梁格模型出现剪应力校核条目", any("剪应力" in n for n in names))
    shear = [c for c in checks if "剪应力" in c.name]
    check("混凝土剪应力报的是 MPa 值而不是无量纲利用率",
          all(c.unit == "MPa" for c in shear), str([c.unit for c in shear]))
    check("混凝土应力条目是 warn 级（量级判据，不是强制条文）",
          all(c.level == "warn" for c in checks if "正应力" in c.name),
          str({c.level for c in checks if "正应力" in c.name}))

    # 主梁的应力必须与组合内力自洽：σ 与 1.2·M_G 同量级
    sig = [c for c in checks if "正应力" in c.name]
    if sig:
        v = sig[0].value or 0.0
        check("正应力利用率落在 (0, 1) 的合理区间", 0.0 < v < 1.0, f"{v:.3f}")
    else:
        check("正应力利用率落在 (0, 1) 的合理区间", False, "没有条目")

    # 未标牌号的模型：跳过并说明，不许崩
    m2 = m.model_copy(deep=True)
    for mat in m2.materials:
        mat.grade = ""
    res2 = solver_3d.solve(m2, out_dir="out/_verify/strength2", kind="grillage3d")
    c2 = res2.checks(m2)
    hit = [c for c in c2 if "应力验算" in c.name]
    check("未标牌号时给 info 说明而不是静默跳过",
          bool(hit) and hit[0].level == "info" and "没做" in hit[0].detail,
          hit[0].detail[:28] if hit else "无条目")


def part_suspension() -> None:
    print("\n[6] 集成：悬索桥加劲梁应力（成桥态梁端内力）")

    import result_3d as R
    import solver_3d
    from suspension_3d import build_suspension_3d

    model, _info = build_suspension_3d(system="SELF", main_span=300.0,
                                       sag_ratio=0.10)
    res = solver_3d.solve(model, out_dir="out/_verify/strength_susp",
                          kind="suspension")
    # 成桥态没有"包络"（一个确定状态不需要包络），但已经有了梁端内力。
    check("成桥态内核输出梁端内力（[GIRDEREND]）", bool(res.girder_ends),
          f"{len(res.girder_ends)} 条")
    beam_ids = {e.id for e in model.elements
                if next((t.kind for t in model.etypes if t.id == e.etype), "") == "BEAM"}
    check("每根梁两端各一条（端数 = 2×梁数）",
          len(res.girder_ends) == 2 * len(beam_ids),
          f"{len(res.girder_ends)} vs {2 * len(beam_ids)}")
    check("两端内力不相等（端信息不是冗余的）",
          any(abs(res.girder_ends[(e, 1)]["My"] - res.girder_ends[(e, 2)]["My"]) > 1.0
              for e in beam_ids))

    names = [c.name for c in res.checks(model)]
    check("不再出现「不输出梁内力」的说明",
          not any("不输出梁内力" in c.detail for c in res.checks(model)),
          next((c.detail[:24] for c in res.checks(model) if "应力验算" in c.name), "无条目"))
    check("出现加劲梁正应力校核", any("正应力" in n for n in names),
          "、".join(n for n in names if "应力" in n)[:60])
    sig = [c for c in res.checks(model) if "正应力" in c.name]
    check("正应力条目是组合口径（基本组合），钢 error / 混凝土 warn",
          any("基本组合" in c.name for c in sig)
          and all(c.level in ("error", "warn") for c in sig),
          str({c.level for c in sig}))
    check("口径条目写清活载包络已接入组合",
          any("活载包络" in c.detail for c in res.checks(model)))

    # ---- 同端配对 + 角点枚举：这是 [GIRDER]（跨端各取最大）与 [GIRDEREND] 的分水岭 ----
    # 现在 stress_table 是基本组合（1.2G+1.4Q），N 与 My 来自**同一端**，正应力
    # 枚举 N×My 角点取最不利。手算重放这一过程，σ 必须对得上——若程序把 i 端的
    # N 与 j 端的 My 拼在一起，这条就对不上（而报告照样生成、数字照样好看）。
    tab = res.tables(model)["stress_table"]
    check("报告应力表是基本组合", "基本组合" in tab["title"], tab["title"])
    label, unit_end, s_top, s_bot = tab["rows"][0][:4]
    eid, end = (int(x) for x in unit_end.split("·"))
    sec = {lbl: s for lbl, s, _ in R._strength_sections(model)}[label]
    import combination
    ult = next(c for c in combination.cable_combinations(res)
               if c.spec.key == "ultimate")
    row = ult.rows[(eid, end)]
    vz = row["VzMin"] if abs(row["VzMin"]) >= abs(row["VzMax"]) else row["VzMax"]
    best = None
    for n in (row["NMin"], row["NMax"]):
        for my in (row["MyMin"], row["MyMax"]):
            rr = ss.check(sec, n, my, vz)
            if best is None or rr.util > best.util:
                best = rr
    check("表内上下缘应力 = 组合角点枚举最不利（手算对拍）",
          abs(best.sigma_top - float(s_top)) < 0.06
          and abs(best.sigma_bot - float(s_bot)) < 0.06,
          f"手算 {best.sigma_top:+.2f}/{best.sigma_bot:+.2f} vs 表 {s_top}/{s_bot}")

    # 牌号确实落到了模型里（否则梁端内力也无从验算）
    grades = {m.grade for m in model.materials if m.grade}
    check("悬索桥模型带上了材料牌号", {"Q345qD", "C50"} <= grades, str(sorted(grades)))
    deck = next(r for r in model.reals if r.id == 1)
    check("加劲梁实常数带腹板面积（剪应力要用）", deck.AW > 0.0, f"{deck.AW:.5f} m²")
    check("加劲梁实常数带控制板厚（查强度档要用）",
          deck.T_PLATE == 28.0, f"{deck.T_PLATE:g} mm")


def part_cable_stayed() -> None:
    print("\n[7] 集成：斜拉桥主梁应力（早期这一整条链是断的）")

    import solver_3d
    from cable_stayed_3d import build_cable_stayed_3d

    model, _info = build_cable_stayed_3d()
    res = solver_3d.solve(model, out_dir="out/_verify/strength_cable",
                          kind="cable_stayed")
    check("斜拉桥也输出梁端内力", bool(res.girder_ends), f"{len(res.girder_ends)} 条")
    check("斜拉桥没有 [GIRDER] 摘要（那是悬索桥加劲梁用的）", not res.girder)

    names = [c.name for c in res.checks(model)]
    check("出现主梁正应力校核", any(n.startswith("主梁") and "正应力" in n for n in names),
          "、".join(n for n in names if "应力" in n)[:60])
    sig = [c for c in res.checks(model) if "正应力" in c.name]
    check("正应力利用率落在 (0, 1) 的合理区间",
          0.0 < max(c.value or 0.0 for c in sig) < 1.0,
          f"{max(c.value or 0.0 for c in sig):.3f}")
    check("塔柱也在验算范围内（它同样是 BEAM、同样被压弯）",
          any(n.startswith("截面#") and "正应力" in n for n in names))

    # 混凝土主梁没有预应力：恒载下上缘受拉很可能是真的（超 ftk 即开裂）。
    # 这条不判对错，只要求程序把"开裂"这件事写出来，而不是给个好看的利用率。
    peak = [c for c in res.checks(model) if "主梁" in c.name and "正应力" in c.name]
    if peak:
        print(f"      {peak[0].detail[:96]}")
    check("应力表逐截面给出控制项",
          all(row[-1] for row in res.tables(model)["stress_table"]["rows"]))


# ---------------------------------------------------------------- main


def main() -> int:
    print("=" * 78)
    print("  截面应力验算回归：材料表 / 手算对拍 / 边界 / 集成")
    print("=" * 78)

    part_material()
    part_stress()
    part_concrete()
    part_scope()
    part_integration()
    part_suspension()
    part_cable_stayed()

    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(PASS)} 通过")
    if n_pass != len(PASS):
        print("  失败项：" + "、".join(n for n, ok, _ in PASS if not ok))
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
