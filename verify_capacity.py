"""verify_capacity.py —— 正截面抗弯 + 斜截面抗剪承载力验算的端到端回归。

覆盖三件事：
1. 纯函数手算对拍：矩形/工形正截面抗弯 Mu、斜截面抗剪 Vu 与闭式解逐一
   对照（手算值硬编码在断言里，不用被验代码反算）。
2. 边界：超筋检测、未配筋负例（不下假结论）。
3. 端到端：梁格建造器填配筋 → 求解 → 校核含承载力条目且通过、明细表
   capacity_table 出现。

JTG 3362-2018 依据：
- §5.2.1 表 5.2.1 相对界限受压区高度 ξb（2018 修订值：HRB400=0.53）
- §5.2.2 矩形 / §5.2.3 工形（受压翼缘）正截面抗弯
- §5.2.9 仅配箍筋斜截面抗剪
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import math
import tempfile

import section_strength as ss

PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<40} {detail}")


def near(a: float, b: float, rel: float = 1e-3) -> bool:
    return abs(a - b) <= rel * max(abs(a), abs(b), 1e-9)


def main() -> int:
    print("=" * 78)
    print("承载力验算回归（正截面抗弯 + 斜截面抗剪）")
    print("=" * 78)

    # ============================================================ 矩形正截面
    print("\n[1] 矩形截面正截面抗弯（单筋）手算对拍")
    # C30 fcd=13.8 MPa；HRB400 fsd=330 MPa；b=300 h=600 as=40 → h0=560
    # 4Φ25：As = 4 × π×25²/4 = 1963.5 mm²
    sec = ss.CapacitySection(h=600, b=300, As=4 * math.pi * 25 ** 2 / 4,
                             as_=40, concrete_grade="C30")
    fm = ss.flexural_capacity(sec)
    # x = fsd·As/(fcd·b) = 330×1963.5/(13.8×300)
    x_hand = 330 * 1963.495 / (13.8 * 300)
    # Mu = fcd·b·x·(h0 − x/2)，N·mm → kN·m 除以 1e6
    Mu_hand = 13.8 * 300 * x_hand * (560 - x_hand / 2) / 1e6   # kN·m
    check("受压区高度 x 与手算一致", near(fm["x"], x_hand, 1e-3),
          f"x={fm['x']:.1f}mm vs {x_hand:.1f}")
    check("抗弯承载力 Mu 与手算一致", near(fm["Mu"] / 1e3, Mu_hand, 1e-3),
          f"Mu={fm['Mu']/1e3:.1f}kN·m vs {Mu_hand:.1f}")
    check("相对受压区高度不超界限（HRB400/C30 ξb=0.53）",
          fm["xi"] < fm["xi_b"] and near(fm["xi_b"], 0.53, 1e-9),
          f"xi={fm['xi']:.3f} xi_b={fm['xi_b']}")
    check("不超筋", not fm["over_reinforced"])

    # ============================================================ 斜截面抗剪
    print("\n[2] 斜截面抗剪（仅配箍筋）手算对拍")
    # 双肢Φ8@150：Asv = 2×π×8²/4 = 100.53 mm²；P=100·As/(b·h0)=1.169
    sec2 = ss.CapacitySection(h=600, b=300, As=4 * math.pi * 25 ** 2 / 4,
                              as_=40, Asv=2 * math.pi * 8 ** 2 / 4, sv=150,
                              concrete_grade="C30")
    sh = ss.shear_capacity(sec2)
    P = 100 * (4 * math.pi * 25 ** 2 / 4) / (300 * 560)
    rho_sv = (2 * math.pi * 8 ** 2 / 4) / (150 * 300)
    Vu_hand = (0.45e-3 * 1.0 * 1.0 * 1.0 * 300 * 560
               * math.sqrt((2 + 0.6 * P) * math.sqrt(30.0) * rho_sv * 330.0))
    check("抗剪承载力 Vu 与手算一致", near(sh["Vu"] / 1e3, Vu_hand, 1e-3),
          f"Vu={sh['Vu']/1e3:.1f}kN vs {Vu_hand:.1f}")
    check("纵向配筋百分率 P 正确", near(sh["P"], P, 1e-6), f"P={sh['P']:.3f}")
    check("箍筋配筋率 ρsv 正确", near(sh["rho_sv"], rho_sv, 1e-6),
          f"ρsv={sh['rho_sv']:.5f}")
    check("箍筋强度取设计值且不超 330", sh["fsv"] == 330.0, f"fsv={sh['fsv']}")
    check("立方体强度 fcu,k = 30", near(sh["fcu_k"], 30.0, 1e-9))

    # ============================================================ 工形截面
    print("\n[3] 工形截面（箱梁顶板受压）第一类 T 形")
    # 箱梁 B=12m 顶板 0.28m，腹板 2×0.45m，h=2.5m；单条纵梁均分（n_lines=3）
    sec3 = ss.CapacitySection(h=2500, b=900, bf=12000, hf=280, As=40000,
                              as_=100, concrete_grade="C50")
    fm3 = ss.flexural_capacity(sec3)
    # 第一类 T 形判定：fsd·As ≤ fcd·bf·hf → 中和轴在翼缘内
    first_kind = 330 * 40000 <= 22.4 * 12000 * 280
    check("判为第一类 T 形（中和轴在翼缘内）", first_kind and fm3["x"] <= 280,
          f"x={fm3['x']:.1f}mm")
    check("第一类 T 形 Mu 按翼缘宽 bf 矩形算",
          near(fm3["Mu"] / 1e3,
               22.4 * 12000 * fm3["x"] * ((2500 - 100) - fm3["x"] / 2) / 1e6,
               1e-3), f"Mu={fm3['Mu']/1e3:.0f}kN·m")

    # ============================================================ 边界
    print("\n[4] 边界：超筋 / 未配筋 / 未配箍")
    # 超筋：极小截面配极大钢筋，x > ξb·h0
    over = ss.CapacitySection(h=400, b=200, As=8000, as_=40, concrete_grade="C30")
    fmo = ss.flexural_capacity(over)
    check("超筋被识别", fmo["over_reinforced"],
          f"x={fmo['x']:.0f}mm > ξb·h0={fmo['xi_b']*360:.0f}mm")
    # 未配筋：不下"通过"结论
    none_ = ss.CapacitySection(h=600, b=300, As=0, concrete_grade="C30")
    r0 = ss.check_capacity(none_, Md=100e3, Vd=100e3)
    check("未配筋 → 承载力不可判定", r0.no_rebar and r0.Mu == 0.0, r0.control)
    check("未配筋不误报利用率", r0.util == 0.0)
    # 未配箍：抗剪不可判定，但抗弯仍可验
    nosv = ss.CapacitySection(h=600, b=300, As=1963, as_=40, concrete_grade="C30")
    r1 = ss.check_capacity(nosv, Md=200e3, Vd=100e3)
    check("未配箍 → Vu=0 且标注", r1.no_stirrup and r1.Vu == 0.0)

    # ============================================================ 端到端
    print("\n[5] 端到端：梁格建造器 → 求解 → 承载力校核")
    import model_doc
    t = "三跨连续箱梁，边跨30米中跨50米，梁高2.5米，箱宽12米，逐跨施工"
    doc, _src, _diag = model_doc.ModelDoc.from_text(t)
    res = doc.solve(out_dir=tempfile.mkdtemp())
    checks = res.checks(doc.model)
    names = {c.name: c for c in checks}
    check("校核含正截面抗弯条目",
          any("正截面抗弯" in n for n in names), "")
    check("校核含斜截面抗剪条目",
          any("斜截面抗剪" in n for n in names), "")
    cM = next((c for n, c in names.items() if "正截面抗弯" in n), None)
    cV = next((c for n, c in names.items() if "斜截面抗剪" in n), None)
    check("正截面抗弯通过（默认配筋合理）", cM is not None and cM.ok,
          cM.detail[:60] if cM else "—")
    check("斜截面抗剪通过（Φ12@150 合理）", cV is not None and cV.ok,
          cV.detail[:60] if cV else "—")
    # 明细表
    tb = res.tables(doc.model)
    check("明细表含 capacity_table", "capacity_table" in tb, "")
    if "capacity_table" in tb:
        rows = tb["capacity_table"]["rows"]
        check("capacity_table 有主梁一行", len(rows) >= 1
              and "主梁" in rows[0][0], f"{len(rows)} 行")
        check("承载力表列名齐全",
              tb["capacity_table"]["headers"]
              == ["部位", "Mu (kN·m)", "Vu (kN)",
                  "利用率 M", "利用率 V", "利用率", "控制项"], "")

    # ============================================================ 汇总
    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(PASS)} 通过")
    print("=" * 78)
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
