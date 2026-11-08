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

    # ============================================================ 偏心受压（压弯）
    print("\n[5] 矩形偏心受压（对称配筋）手算对拍")
    # b=400 h=600 as=a's=40 → h0=560；C30 fcd=13.8；HRB400 fsd=f'sd=330、
    # Es=2.0e5；As=A's=4Φ25=1963.5；ξb=0.53
    sec_e = ss.CapacitySection(h=600, b=400, As=4 * math.pi * 25 ** 2 / 4,
                               as_=40, asp=40, concrete_grade="C30")
    As_e = 4 * math.pi * 25 ** 2 / 4
    # 大偏心：N=800kN，对称配筋（fsd=fsd_c）力平衡 → x=N/(fcd·b)
    cap_e1 = ss.eccentric_capacity(sec_e, 800e3)
    x1 = 800e3 / (13.8 * 400)
    rhs1 = 13.8 * 400 * x1 * (560 - x1 / 2) + 330 * As_e * (560 - 40)
    Mu1 = (rhs1 - 800e3 * (300 - 40)) / 1e3              # N·m
    check("大偏心受压区高度 x 与手算一致", near(cap_e1["x"], x1, 1e-3),
          f"x={cap_e1['x']:.1f}mm vs {x1:.1f}")
    check("大偏心 σs=fsd=330", near(cap_e1["sigma_s"], 330.0, 1e-9),
          f"σs={cap_e1['sigma_s']:.0f}")
    check("大偏心 Mu 与手算一致", near(cap_e1["Mu"], Mu1, 1e-3),
          f"Mu={cap_e1['Mu']/1e3:.1f}kN·m vs {Mu1/1e3:.1f}")
    check("判为大偏心受压", cap_e1["mode"] == "大偏心受压"
          and not cap_e1["over_reinforced"], cap_e1["mode"])

    # 小偏心：N=2500kN，力平衡 5520x − 660·As·448/x = 556139.95（二次方程闭式解）
    cap_e2 = ss.eccentric_capacity(sec_e, 2500e3)
    a2, b2, c2 = 5520.0, -(2500e3 - 330 * As_e - 660 * As_e), -660 * As_e * 448
    x2 = (-b2 + math.sqrt(b2 * b2 - 4 * a2 * c2)) / (2 * a2)
    sig2 = 0.0033 * 2.0e5 * (0.8 * 560 / x2 - 1)
    rhs2 = 13.8 * 400 * x2 * (560 - x2 / 2) + 330 * As_e * (560 - 40)
    Mu2 = (rhs2 - 2500e3 * (300 - 40)) / 1e3
    check("小偏心受压区高度 x（二分迭代）", near(cap_e2["x"], x2, 1e-3),
          f"x={cap_e2['x']:.1f}mm vs {x2:.1f}")
    check("小偏心 σs 按 §5.1.5 平截面", near(cap_e2["sigma_s"], sig2, 1e-3),
          f"σs={cap_e2['sigma_s']:.1f} vs {sig2:.1f}")
    check("小偏心 Mu 与手算一致", near(cap_e2["Mu"], Mu2, 1e-3),
          f"Mu={cap_e2['Mu']/1e3:.1f}kN·m vs {Mu2/1e3:.1f}")
    check("判为小偏心受压", cap_e2["mode"] == "小偏心受压"
          and cap_e2["over_reinforced"], cap_e2["mode"])

    # ============================================================ 二阶效应 η
    print("\n[6] 偏心距增大系数 η（§5.3.9）")
    # 墩柱算例：l0=19.6m、h=2.2m、e0=274mm、C40 → η≈1.26
    sec_eta = ss.CapacitySection(h=2200, b=2200, As=91608.8, as_=70, asp=70,
                                 concrete_grade="C40")
    eta = ss.eta_second_order(sec_eta, 20400e3, 20400e3 * 0.274, 19600)
    check("η 与墩柱算例一致（≈1.26）", near(eta, 1.26, 0.02), f"η={eta:.3f}")
    check("短柱（l0/i≤17.5）η=1",
          ss.eta_second_order(sec_eta, 20400e3, 20400e3 * 0.274, 5000) == 1.0, "")
    check("未给 l0 时 η=1",
          ss.eta_second_order(sec_eta, 20400e3, 20400e3 * 0.274, None) == 1.0, "")

    # ============================================================ 压弯边界
    print("\n[7] 压弯边界：集成验算 / 轴压不足 / 未配筋 / 轴拉 / 工形")
    r_ec = ss.check_eccentric(sec_e, 800e3, 400e3, gamma0=1.1)
    # check_eccentric 用 γ0·N=880kN 求 Mu，再算 util=η·γ0·|M|/Mu（η=1）
    Nd_ec = 1.1 * 800e3
    x_ec = Nd_ec / (13.8 * 400)
    rhs_ec = 13.8 * 400 * x_ec * (560 - x_ec / 2) + 330 * As_e * (560 - 40)
    Mu_ec = (rhs_ec - Nd_ec * (300 - 40)) / 1e3
    check("check_eccentric 大偏心利用率", near(r_ec.util, 1.1 * 400e3 / Mu_ec, 1e-3),
          f"util={r_ec.util:.3f}")
    r_ov = ss.check_eccentric(sec_e, 8000e3, 100e3)
    check("轴压超载 → util=∞ 且标注", math.isinf(r_ov.util), r_ov.note)
    r_nr = ss.check_eccentric(
        ss.CapacitySection(h=600, b=400, As=0, concrete_grade="C30"), 1000e3, 100e3)
    check("未配筋 → 不可判定", r_nr.mode == "未配筋", r_nr.note)
    r_nt = ss.check_eccentric(sec_e, -500e3, 100e3)
    check("轴拉 → 非受压不适用", r_nt.mode == "非受压", r_nt.note)
    sec_i = ss.CapacitySection(h=2500, b=900, bf=12000, hf=280, As=40000,
                               as_=100, concrete_grade="C50")
    cap_i = ss.eccentric_capacity(sec_i, 1000e3)
    check("工形截面压弯明确标注不支持", "不支持" in cap_i["mode"], cap_i["mode"])

    # ============================================================ 轴心受压
    print("\n[8] 轴心受压（§5.3.1）+ 桥塔端到端")
    # Nu = 0.9·φ·(fcd·A + f'sd·A's)，A=b·h，对称配筋 A's = As + Asp = 2·As
    nu_hand = 0.9 * 1.0 * (13.8 * 400 * 600 + 330 * (As_e * 2))
    r_ax = ss.check_axial(sec_e, 2000e3, gamma0=1.0)
    check("轴心受压 Nu 与手算一致", near(r_ax.Nu, nu_hand, 1e-3),
          f"Nu={r_ax.Nu/1e3:.0f}kN vs {nu_hand/1e3:.0f}")
    check("轴心受压利用率正确", near(r_ax.util, 2000e3 / nu_hand, 1e-3),
          f"util={r_ax.util:.3f}")
    # 端到端：斜拉桥塔柱（混凝土 C50 对称配筋）→ 自动走轴心受压
    import cable_stayed_3d
    from solver_3d import solve
    m_cb, _ = cable_stayed_3d.build_cable_stayed_3d(
        main_span=100.0, side_span=40.0, tower_height=40.0, n_cable=6)
    res_cb = solve(m_cb, out_dir=tempfile.mkdtemp(), dat_name="cable.dat")
    chk_cb = res_cb.checks(m_cb)
    ax_cb = next((c for c in chk_cb if "轴心受压" in c.name), None)
    check("斜拉桥塔柱轴心受压条目出现", ax_cb is not None, "")
    check("塔柱轴心受压通过（截面富余大）", ax_cb is not None and ax_cb.ok,
          ax_cb.detail[:60] if ax_cb else "—")

    # ============================================================ 端到端
    print("\n[9] 端到端：梁格建造器 → 求解 → 承载力校核")
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
