"""verify_capacity.py —— JTG 3362-2018 第 5 章承载力验算的端到端回归。

覆盖七件事：
1. 纯函数手算对拍：矩形/工形正截面抗弯 Mu、斜截面抗剪 Vu 与闭式解逐一
   对照（手算值硬编码在断言里，不用被验代码反算）。
2. 边界：超筋检测、未配筋负例（不下假结论）。
3. 偏心受压 / 轴心受压：大/小偏心、η 二阶效应、φ 稳定系数、矩形/工形/
   箱形毛面积与回转半径。
4. 负弯矩区：倒 T 形第一/二类 Mu 手算对拍、双向验算取控制、未登记负弯矩
   面给对称近似告警、缺筋判 ∞ 不可判定。
5. 非对称配筋：Asp_given/Asp_eff、单筋轴压、非对称大/小偏心力平衡、
   §5.3.4-4/5 反向破坏手算对拍、工形未覆盖如实标注。
6. 端到端：梁格建造器填配筋 → 求解 → 校核含承载力条目且通过、明细表
   capacity_table / capacity_faces_table 出现；塔柱轴压与箱形塔柱端到端。
7. 承载力计算书：纯函数中间量留痕且与结论同源、六列明细表带条文号、
   三条验算链齐全、∞ 不可判定排最前；报告独立章节 + HTML / DOCX / CSV /
   PDF 四种出口逐行导出（PDF 从 HTML 打印，用 pypdf 读回文本对拍）。

JTG 3362-2018 依据：
- §5.2.1 表 5.2.1 相对界限受压区高度 ξb（2018 修订值：HRB400=0.53）
- §5.2.2 矩形 / §5.2.3 工形（受压翼缘）正截面抗弯
- §5.2.9 仅配箍筋斜截面抗剪
- §5.3.1 轴心受压 + 表 5.3.1 稳定系数 φ；§5.3.4 偏心受压（含 5.3.4-4/5
  反向破坏）；§5.3.5 工形/箱形压弯；§5.3.9 二阶效应 η
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import math
import tempfile
from dataclasses import replace

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
    # 第一类 T 形（中和轴在翼缘内）：x1=1e6/(22.4×12000)=3.72mm<hf=280，
    # x=max(3.72,2×40)=80mm；x_big<2a's 受压钢筋不屈服，保守忽略 Asp。
    mu_i_nm = (22.4 * 12000 * 80 * (2400 - 40) - 1e6 * (1250 - 100)) / 1e3
    check("工形压弯（第一类 T 形）大偏心", cap_i["mode"] == "大偏心受压",
          cap_i["mode"])
    check("工形压弯 x 与手算一致", near(cap_i["x"], 80.0, 1e-3),
          f"x={cap_i['x']:.1f}")
    check("工形压弯 Mu 与手算一致", near(cap_i["Mu"], mu_i_nm, 1e-3),
          f"Mu={cap_i['Mu']/1e3:.0f} vs {mu_i_nm/1e3:.0f}")
    # 第二类 T 形（中和轴深入腹板）：bf=3000,hf=100,b=800,h=4500,N=15000kN
    sec_f2 = ss.CapacitySection(h=4500, b=800, As=26800, Asp=26800,
                                bf=3000, hf=100, as_=70, asp=70,
                                concrete_grade="C50")
    cap_f2 = ss.eccentric_capacity(sec_f2, 15e6)
    x_f2 = (15e6 - 22.4 * (3000 - 800) * 100) / (22.4 * 800)
    mu_f2_nm = (22.4 * (800 * x_f2 * (4430 - x_f2 / 2)
                        + 2200 * 100 * (4430 - 50))
                + 330 * 26800 * 4360 - 15e6 * 2180) / 1e3
    check("工形压弯第二类 x 与手算一致", near(cap_f2["x"], x_f2, 1e-3),
          f"x={cap_f2['x']:.1f}")
    check("工形压弯第二类 Mu 与手算一致", near(cap_f2["Mu"], mu_f2_nm, 1e-3),
          f"Mu={cap_f2['Mu']/1e3:.0f}")

    # ============================================================ 轴心受压
    print("\n[8] 轴心受压（§5.3.1）+ 桥塔端到端")
    # Nu = 0.9·φ·(fcd·A + f'sd·A's)，A=b·h，对称配筋 A's = As + Asp = 2·As
    nu_hand = 0.9 * 1.0 * (13.8 * 400 * 600 + 330 * (As_e * 2))
    r_ax = ss.check_axial(sec_e, 2000e3, gamma0=1.0)
    check("轴心受压 Nu 与手算一致", near(r_ax.Nu, nu_hand, 1e-3),
          f"Nu={r_ax.Nu/1e3:.0f}kN vs {nu_hand/1e3:.0f}")
    check("轴心受压利用率正确", near(r_ax.util, 2000e3 / nu_hand, 1e-3),
          f"util={r_ax.util:.3f}")
    # ---- 稳定系数 φ 表 5.3.1（l0/b 坐标，b 矩形短边）----
    sec_stab = ss.CapacitySection(h=600, b=400, As=1963, concrete_grade="C30")
    for l0, phi_hand in [(3200, 1.0), (8000, 0.75), (12000, 0.52),
                          (16000, 0.32), (20000, 0.19)]:
        phi, l0b = ss.stability_factor(sec_stab, l0)
        check(f"稳定系数 φ（l0/b={l0 / 400:.0f}）", near(phi, phi_hand, 1e-2),
              f"φ={phi:.3f} vs {phi_hand}")
    check("短柱（无 l0）φ=1",
          ss.stability_factor(sec_stab, None)[0] == 1.0, "")
    r_ax_long = ss.check_axial(sec_stab, 2000e3, gamma0=1.0, l0=8000)
    check("长柱轴压 φ<1", r_ax_long.phi < 1.0, f"φ={r_ax_long.phi:.3f}")
    check("长柱轴压 λ=l0/b 正确", near(r_ax_long.lam, 20.0, 1e-3),
          f"λ={r_ax_long.lam:.0f}")
    # ---- 箱形截面：毛面积 / 回转半径 / 稳定系数 l0/i ----
    sec_box = ss.CapacitySection(h=4500, b=800, As=26800, Asp=26800,
                                 bf=3000, hf=400, as_=70, asp=70,
                                 concrete_grade="C50", l0=120000, hollow=True)
    A_box = 3000 * 4500 - 2200 * 3700
    check("箱形毛面积 = 外减空心", near(sec_box.gross_area, A_box, 1e-6),
          f"A={sec_box.gross_area:.0f}")
    I_B = (4500 * 3000 ** 3 - 3700 * 2200 ** 3) / 12
    i_box = math.sqrt(I_B / A_box)
    check("箱形最小回转半径", near(sec_box.min_radius_of_gyration, i_box, 1e-3),
          f"i={sec_box.min_radius_of_gyration:.1f}")
    phi_box, lam_box = ss.stability_factor(sec_box, 120000)
    check("箱形稳定系数坐标 l0/i", near(lam_box, 120000 / i_box, 1e-3),
          f"λ={lam_box:.1f}")
    check("箱形 φ 与表值一致", near(phi_box, 0.507, 5e-3), f"φ={phi_box:.4f}")
    check("箱形比实心更抗屈曲（φ 更大）",
          phi_box > ss.stability_factor(
              ss.CapacitySection(h=4500, b=3000, As=26800, Asp=26800, as_=70,
                                 concrete_grade="C50", l0=120000), 120000)[0],
          f"{phi_box:.3f}")
    r_ax_box = ss.check_axial(sec_box, 16000e3)
    check("箱形轴压 λ 标注 l0/i", r_ax_box.lam_kind == "l0/i", r_ax_box.lam_kind)
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
    check("塔柱稳定系数已计入（φ<1）",
          ax_cb is not None and "λ=l0/b=" in ax_cb.detail
          and "φ=0." in ax_cb.detail,
          ax_cb.detail[:70] if ax_cb else "—")
    # 箱形塔柱：稳定系数走 l0/i，λ 标注 l0/i
    m_box, _ = cable_stayed_3d.build_cable_stayed_3d(
        main_span=100.0, side_span=40.0, tower_height=40.0, n_cable=6,
        tower_wall=0.4)
    res_box = solve(m_box, out_dir=tempfile.mkdtemp(), dat_name="cable_box.dat")
    chk_box = res_box.checks(m_box)
    ax_box = next((c for c in chk_box if "轴心受压" in c.name), None)
    check("箱形塔柱轴心受压条目出现", ax_box is not None, "")
    check("箱形塔柱 λ 标注 l0/i",
          ax_box is not None and "λ=l0/i=" in ax_box.detail,
          ax_box.detail[:70] if ax_box else "—")

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

    # ============================================================ 负弯矩区
    print("\n[10] 负弯矩区（受压在底板、顶板纵筋受拉）§5.2.2/§5.2.3")
    sec_bi = ss.CapacitySection(
        h=2500, b=800, As=40000, bf=12000, hf=280, as_=100,
        Asv=402.0, sv=150, concrete_grade="C50",
        negative=ss.FaceSpec(bf=2000, hf=250, As=12000, as_=80))
    sec_m, given = ss.mirror_section_for_negative(sec_bi)
    check("负弯矩面显式登记", given, "")
    check("镜像后受压翼缘换成底板宽/厚",
          sec_m.bf == 2000 and sec_m.hf == 250, f"bf={sec_m.bf} hf={sec_m.hf}")
    check("镜像后受拉筋换成顶板纵筋", sec_m.As == 12000
          and sec_m.as_ == 80 and sec_m.h0 == 2420,
          f"As={sec_m.As:.0f} h0={sec_m.h0:.0f}")
    check("镜像锁定外轮廓宽、毛面积不变",
          sec_m.outer_bf == 12000
          and near(sec_m.gross_area, sec_bi.gross_area, 1e-9),
          f"outer_bf={sec_m.outer_bf:.0f} A={sec_m.gross_area:.0f}")
    check("镜像面不再嵌套镜像（防递归）", sec_m.negative is None, "")

    # 第一类倒 T 形（中和轴在底板翼缘内）：x=fsd·As/(fcd·bf_neg)
    fm_neg = ss.flexural_capacity(sec_bi, -1.0)
    x_neg = 330 * 12000 / (22.4 * 2000)
    mu_neg_hand = 22.4 * 2000 * x_neg * (2420 - x_neg / 2) / 1e6   # kN·m
    check("负弯矩第一类 x 与手算一致", near(fm_neg["x"], x_neg, 1e-3),
          f"x={fm_neg['x']:.1f} vs {x_neg:.1f}")
    check("负弯矩第一类 Mu 与手算一致",
          near(fm_neg["Mu"] / 1e3, mu_neg_hand, 1e-3),
          f"Mu={fm_neg['Mu']/1e3:.0f} vs {mu_neg_hand:.0f} kN·m")
    check("负弯矩 Mu 标出受压面", "负弯矩区" in fm_neg["face"], fm_neg["face"])

    # 第二类倒 T 形（中和轴深入腹板）：Ac = b·x + (bf−b)·hf
    sec_bi2 = replace(sec_bi, negative=ss.FaceSpec(
        bf=2000, hf=250, As=60000, as_=80))
    fm_neg2 = ss.flexural_capacity(sec_bi2, -1.0)
    x_n2 = (330 * 60000 - 22.4 * (2000 - 800) * 250) / (22.4 * 800)
    mu_n2 = (22.4 * (800 * x_n2 * (2420 - x_n2 / 2)
                     + (2000 - 800) * 250 * (2420 - 250 / 2))) / 1e6
    check("负弯矩第二类 x 与手算一致", near(fm_neg2["x"], x_n2, 1e-3),
          f"x={fm_neg2['x']:.1f} vs {x_n2:.1f}")
    check("负弯矩第二类 Mu 与手算一致",
          near(fm_neg2["Mu"] / 1e3, mu_n2, 1e-3),
          f"Mu={fm_neg2['Mu']/1e3:.0f} vs {mu_n2:.0f} kN·m")

    # 双向验算取控制：底板窄 + 顶板少筋 → 负弯矩 Mu 显著变小
    r_bi = ss.check_capacity(sec_bi, Md=10329e3, Vd=1000e3,
                             alpha3=1.1, Md_neg=10884e3)
    check("Mu_neg 明显小于 Mu_pos（不再拿顶板翼缘冒充）",
          r_bi.Mu_neg < 0.6 * r_bi.Mu_pos,
          f"{r_bi.Mu_neg/1e3:.0f} vs {r_bi.Mu_pos/1e3:.0f} kN·m")
    check("负弯矩利用率高于正弯矩、控制区标为负弯矩",
          r_bi.util_M_neg > r_bi.util_M_pos
          and r_bi.zone.startswith("负弯矩区"), r_bi.zone)
    check("util_M 取两个弯矩方向的大者",
          near(r_bi.util_M, r_bi.util_M_neg, 1e-9),
          f"{r_bi.util_M:.4f} vs {r_bi.util_M_neg:.4f}")
    check("控制项标出弯矩方向", "正截面抗弯" in r_bi.control, r_bi.control)

    # 未登记负弯矩面：给对称近似提示，不静默
    sec_no = replace(sec_bi, negative=None)
    r_ap = ss.check_capacity(sec_no, Md=0.0, Vd=1000e3, Md_neg=10884e3)
    check("未登记负弯矩面 → Mu 两面相同", near(r_ap.Mu_neg, r_ap.Mu_pos, 1e-9), "")
    check("未登记负弯矩面 → 明确标注对称近似",
          r_ap.negative_approx and "对称近似" in r_ap.note
          and "对称近似" in ss.flexural_capacity(sec_no, -1.0)["face"],
          r_ap.note[:60])
    check("capacity_table 说明里带对称近似告警",
          "对称近似" in ss.capacity_table([("主梁", r_ap)])["note"], "")

    # 负弯矩面没配筋 → 不可判定（∞），不能静默给 0
    r_nn = ss.check_capacity(
        replace(sec_bi, negative=ss.FaceSpec(As=0.0)),
        Md=0.0, Vd=1000e3, Md_neg=100e3)
    check("负弯矩面缺筋 → util_M_neg=∞", math.isinf(r_nn.util_M_neg), "")
    check("负弯矩面缺筋 → 总利用率=∞", math.isinf(r_nn.util), "")
    check("缺筋写进说明且表里显示 ∞",
          "不可判定" in r_nn.note and "∞" in r_nn.to_row(), r_nn.note[:60])
    ft = ss.capacity_faces_table([("主梁", r_bi)])
    check("分面明细表 7 列且行对齐",
          len(ft["headers"]) == 7 and len(ft["rows"][0]) == 7, "")
    check("分面明细表备注写明两向均验算",
          "两个弯矩方向均已验算" in ft["rows"][0][-1], ft["rows"][0][-1])

    # ============================================================ 非对称配筋
    print("\n[11] 非对称配筋偏心受压 + 反向破坏（§5.3.4-4/5）")
    sec_asym = ss.CapacitySection(
        h=600, b=400, As=As_e, Asp=800.0, Asp_given=True,
        as_=40, asp=40, concrete_grade="C30")
    check("Asp_given=True → 受压筋按字面值、判为非对称",
          near(sec_asym.Asp_eff, 800.0, 1e-9) and sec_asym.asymmetric, "")
    check("Asp_given=False（默认）→ 兼容对称配筋",
          near(sec_e.Asp_eff, As_e, 1e-9) and not sec_e.asymmetric, "")
    sec_single = replace(sec_asym, Asp=0.0)
    nu_single = 0.9 * (13.8 * 400 * 600 + 330 * As_e)
    check("单筋轴压承载力只算 As（不是 2·As）",
          near(ss.axial_capacity(sec_single), nu_single, 1e-3),
          f"Nu={ss.axial_capacity(sec_single)/1e3:.0f} kN")

    # 非对称大偏心：x = (N − f'sd·A's + fsd·As)/(fcd·b)
    x_big_hand = (800e3 - 330 * 800 + 330 * As_e) / (13.8 * 400)
    cap_big = ss.eccentric_capacity(sec_asym, 800e3)
    mu_big_hand = (13.8 * 400 * x_big_hand * (560 - x_big_hand / 2)
                   + 330 * 800 * (560 - 40) - 800e3 * (300 - 40)) / 1e3
    check("非对称大偏心 x 与手算一致",
          near(cap_big["x"], x_big_hand, 1e-3)
          and cap_big["mode"] == "大偏心受压",
          f"x={cap_big['x']:.1f} vs {x_big_hand:.1f}")
    check("非对称大偏心 Mu 与手算一致",
          near(cap_big["Mu"], mu_big_hand, 1e-3),
          f"Mu={cap_big['Mu']/1e3:.1f} kN·m")

    # 非对称小偏心：力平衡残差（σs 按 §5.1.5 平截面）
    cap_small = ss.eccentric_capacity(sec_asym, 2500e3)
    sig_small = 0.0033 * 2.0e5 * (0.8 * 560 / cap_small["x"] - 1)
    res_force = (13.8 * 400 * cap_small["x"] + 330 * 800
                 - sig_small * As_e - 2500e3)
    check("非对称小偏心力平衡闭合",
          cap_small["mode"] == "小偏心受压"
          and abs(res_force) / 2500e3 < 1e-6,
          f"x={cap_small['x']:.1f} 残差={res_force:.1f} N")

    # 反向破坏：e'=h/2−e0−a'、h0'=h−a'、R=fcd·b·h·(h0'−h/2)+f'sd·As·(h0'−as)
    r_rev = ss.check_eccentric(sec_asym, 2500e3, 100e3)
    e0_r = 100e3 / 2500e3 * 1000.0
    ep_r = 300.0 - e0_r - 40.0
    h0p_r = 600.0 - 40.0
    R_r = (13.8 * 400 * 600 * (h0p_r - 300.0)
           + 330 * As_e * (h0p_r - 40.0))
    util_rev_hand = 2500e3 * ep_r / R_r
    check("反向破坏利用率与手算一致",
          near(r_rev.reverse_util, util_rev_hand, 1e-3),
          f"{r_rev.reverse_util:.4f} vs {util_rev_hand:.4f}")
    check("反向破坏控制时利用率取大者并标注",
          near(r_rev.util, util_rev_hand, 1e-3)
          and "反向破坏控制" in r_rev.mode, r_rev.mode)
    check("反向破坏不计 η", near(r_rev.eta, 1.0, 1e-9)
          and "§5.3.4-4/5" in r_rev.note, r_rev.note[:50])
    r_sym = ss.check_eccentric(sec_e, 2500e3, 100e3)
    check("对称配筋不触发反向破坏",
          r_sym.reverse_util == 0.0 and not r_sym.asymmetric, "")
    r_big2 = ss.check_eccentric(sec_asym, 800e3, 200e3)
    check("大偏心不触发反向破坏", r_big2.reverse_util == 0.0, "")
    sec_fl = replace(sec_asym, bf=3000.0, hf=100.0)
    r_fl = ss.check_eccentric(sec_fl, 10e6, 100e3)
    check("工形非对称小偏心 → 反向破坏如实标注未覆盖",
          (not r_fl.reverse_covered) and "未覆盖" in r_fl.note
          and r_fl.reverse_util == 0.0, r_fl.note[:50])

    # ============================================================ 端到端
    print("\n[12] 端到端：负弯矩面接入校核与报告明细表")
    import result_3d
    secs2 = result_3d._capacity_sections(doc.model)
    check("梁格截面带负弯矩面参数", secs2 and secs2[0][1].negative is not None, "")
    check("明细表含正/负弯矩分面表",
          "capacity_faces_table" in tb
          and len(tb["capacity_faces_table"]["rows"]) >= 1, "")
    cM2 = next((c for n, c in names.items() if "正截面抗弯" in n), None)
    check("抗弯校核标注控制区与两面 Mu",
          cM2 is not None and "负弯矩区" in cM2.detail
          and "正弯矩区 Mu" in cM2.detail,
          cM2.detail[:80] if cM2 else "—")
    check("默认模型已登记负弯矩面（无对称近似告警）",
          "未登记负弯矩面" not in tb["capacity_table"]["note"]
          and "对称近似" not in tb["capacity_table"]["note"], "")
    import grillage_3d
    box = grillage_3d.box_section(12.0, 2.5, 0.28, 0.45)
    rb = grillage_3d._rebar_spec(
        box, 12.0, 2.5, 0.28, 0.45, 3, 0.012, 0.10, 12.0, 150.0,
        rebar_ratio_neg=0.006)
    rb0 = grillage_3d._rebar_spec(
        box, 12.0, 2.5, 0.28, 0.45, 3, 0.012, 0.10, 12.0, 150.0)
    check("顶板纵筋配筋率可单独配置（减半生效）",
          rb.negative_given and near(rb.As_neg, rb.As / 2.0, 1e-9),
          f"As_neg={rb.As_neg:.0f} vs As={rb.As:.0f}")
    check("默认顶板筋与底板筋相同",
          rb0.negative_given and near(rb0.As_neg, rb0.As, 1e-9), "")
    sec_half = replace(secs2[0][1],
                       negative=replace(secs2[0][1].negative,
                                        As=secs2[0][1].negative.As / 2.0))
    fm_half = ss.flexural_capacity(sec_half, -1.0)
    check("顶板纵筋减半 → 负弯矩 Mu 下降",
          fm_half["Mu"] < ss.flexural_capacity(secs2[0][1], -1.0)["Mu"],
          f"{fm_half['Mu']/1e3:.0f} kN·m")

    # ============================================================ 计算书
    print("\n[13] 承载力计算书：中间量留痕 + 条文号 + 报告四种出口")
    # ---- 纯函数只留痕、不改数值 ----
    fm_k = ss.flexural_capacity(sec3)
    check("抗弯纯函数导出 Ac/kind/条文号",
          all(k in fm_k for k in ("Ac", "kind", "clause", "h0", "fcd", "fsd"))
          and near(fm_k["Ac"], sec3.bf * fm_k["x"], 1e-6)
          and "§5.2.3" in fm_k["clause"], f"{fm_k['kind']} Ac={fm_k['Ac']:.0f}")
    sh_k = ss.shear_capacity(sec2, alpha3=1.1)
    check("抗剪纯函数导出 α1/α2/α3/ρsv,min",
          all(k in sh_k for k in ("alpha1", "alpha2", "alpha3",
                                  "rho_sv_min", "Asv", "sv"))
          and near(sh_k["alpha3"], 1.1, 1e-9)
          and near(sh_k["Vu"], ss.shear_capacity(sec2, alpha3=1.1)["Vu"], 1e-9),
          f"α3={sh_k['alpha3']} ρsv,min={sh_k['rho_sv_min']}")
    d_eta = ss.eta_second_order_detail(sec_eta, 2500e3, 100e3, 8000)
    check("η 明细与 η 结论同源（不另写一套公式）",
          near(d_eta["eta"], ss.eta_second_order(sec_eta, 2500e3, 100e3, 8000),
               1e-12)
          and all(k in d_eta for k in ("zeta1", "zeta2", "e0",
                                       "l0_over_i", "applicable")),
          f"η={d_eta['eta']:.3f} ζ1={d_eta['zeta1']:.3f}")
    cap_k = ss.eccentric_capacity(sec_asym, 2500e3)
    check("偏心受压纯函数导出 β/εcu/Es/Ac/x_big",
          all(k in cap_k for k in ("beta", "eps_cu", "Es", "Ac", "x_big"))
          and near(cap_k["beta"], 0.80, 1e-9)
          and near(cap_k["eps_cu"], 0.0033, 1e-9)
          and near(cap_k["Es"], 2.0e5, 1e-9),
          f"β={cap_k['beta']} Es={cap_k['Es']:.0f}")
    d_rev = ss.reverse_failure_detail(
        sec_asym, 2500e3, 100e3 / 2500e3 * 1000.0)
    check("反向破坏明细 e'/h0'/利用率与手算一致",
          d_rev is not None
          and near(d_rev["e_prime"], 300 - 40 - 40, 1e-9)
          and near(d_rev["h0_prime"], 560, 1e-9)
          and near(d_rev["util"], r_rev.reverse_util, 1e-12),
          f"e'={d_rev['e_prime']:.0f} util={d_rev['util']:.4f}"
          if d_rev else "—")
    d_ax = ss.axial_capacity_detail(sec_stab, 0.75)
    check("轴压明细 Nu 与结论同源、混凝土/钢筋分项可复核",
          near(d_ax["Nu"], ss.axial_capacity(sec_stab, 0.75), 1e-12)
          and near(d_ax["concrete"] + d_ax["steel"],
                   d_ax["Nu"] / (0.9 * 0.75), 1e-6)
          and near(d_ax["As_total"], 2 * sec_stab.As, 1e-9),
          f"Nu={d_ax['Nu']/1e3:.0f} kN")

    # ---- 三条链的计算书明细表 ----
    cf = ss.CalcCase("主梁", "flex", sec2,
                     ss.check_capacity(sec2, 100e3, 100e3, Md_neg=50e3),
                     Md=100e3, Md_neg=50e3, Vd=100e3)
    ce = ss.CalcCase("桥墩", "eccentric", sec_asym,
                     ss.check_eccentric(sec_asym, 2500e3, 100e3),
                     N=2500e3, M=100e3)
    ca = ss.CalcCase("桥塔", "axial", sec_stab,
                     ss.check_axial(sec_stab, 2000e3, l0=8000),
                     N=2000e3)
    sheet = ss.capacity_calculation_sheet([cf, ce, ca])
    qs = [r[2] for r in sheet["rows"]]
    clauses = [r[5] for r in sheet["rows"]]
    check("计算书 6 列且每行对齐",
          sheet["headers"] == ["部位", "验算链", "计算量", "数值", "单位",
                               "条文 / 公式出处"]
          and all(len(r) == 6 for r in sheet["rows"]),
          f"{len(sheet['rows'])} 行")
    check("三条验算链都展开",
          {r[1] for r in sheet["rows"]}
          == {"受弯 / 抗剪", "偏心受压（压弯）", "轴心受压"}, "")
    for key, label in [("受压区高度 x", "x"), ("相对受压区高度 ξ", "ξ"),
                       ("ξb", "ξb"), ("σs", "σs"),
                       ("偏心距增大系数 η", "η"), ("稳定系数 φ", "φ"),
                       ("长细比 λ", "λ"), ("e'", "e'")]:
        hits = [(q, c) for q, c in zip(qs, clauses) if key in q]
        check(f"计算书含 {label} 且标条文号",
              bool(hits) and all(("§" in c or "表" in c) for _, c in hits),
              hits[0][1][:36] if hits else "—")
    check("关键中间量与纯函数一致（x 不被另算一遍）",
          any(q.startswith("正弯矩区") and "受压区高度 x" in q
              and v == f"{ss.flexural_capacity(sec2)['x']:,.1f}"
              for q, v in ((r[2], r[3]) for r in sheet["rows"])),
          f"x={ss.flexural_capacity(sec2)['x']:.1f}mm")
    check("计算书 note 写明条文与适用范围",
          "JTG 3362-2018" in sheet["note"]
          and "JTG D60-2015" in sheet["note"]
          and "逐项复核" in sheet["title"], "")
    cases4 = [
        ss.CalcCase(f"截面{j}", "flex", sec2,
                    ss.check_capacity(sec2, 10e3 * (j + 1), 10e3),
                    Md=10e3 * (j + 1), Vd=10e3)
        for j in range(4)]
    sheet4 = ss.capacity_calculation_sheet(cases4)
    check("每条链只展开最不利 3 个截面",
          len({r[0] for r in sheet4["rows"]}) == 3
          and "截面0" not in {r[0] for r in sheet4["rows"]}, "")
    sec_bad = ss.CapacitySection(
        h=600, b=300, As=0, Asv=2 * math.pi * 8 ** 2 / 4, sv=150,
        concrete_grade="C30")
    bad = ss.CalcCase("缺筋截面", "flex", sec_bad,
                      ss.check_capacity(sec_bad, 100e3, 100e3),
                      Md=100e3, Vd=100e3)
    sheet_inf = ss.capacity_calculation_sheet([cf, bad])
    check("不可判定（∞）截面排在最前、不静默跳过",
          sheet_inf["rows"][0][0] == "缺筋截面"
          and any("∞" in r[3] for r in sheet_inf["rows"]), "")

    # ---- 端到端：tables / HTML / DOCX / CSV ----
    import zipfile
    import report_3d
    sheets = tb.get("capacity_calculation_sheet")
    check("梁格明细表含计算书且 6 列对齐",
          sheets is not None and len(sheets["rows"]) > 0
          and all(len(r) == 6 for r in sheets["rows"]),
          f"{len(sheets['rows']) if sheets else 0} 行")
    tabs_cb = res_cb.tables(m_cb)
    sh_cb = tabs_cb.get("capacity_calculation_sheet")
    check("斜拉桥塔柱也出计算书（φ/λ 不再只在文字里）",
          "axial_table" in tabs_cb and sh_cb is not None
          and any("稳定系数 φ" in r[2] for r in sh_cb["rows"])
          and any("表 5.3.1" in r[5] for r in sh_cb["rows"]),
          f"{len(sh_cb['rows']) if sh_cb else 0} 行")
    rep = report_3d.build_document(res, doc.model,
                                   out_dir=tempfile.mkdtemp())
    h1s = [b["text"] for b in rep.blocks if b["type"] == "h1"]
    sheet_pos = next(i for i, b in enumerate(rep.blocks)
                     if b.get("key") == "capacity_calculation_sheet")
    detail_pos = next(i for i, b in enumerate(rep.blocks)
                      if b["type"] == "h1" and b["text"] == "明细表")
    check("报告有独立「承载力计算书」章、且不在明细表章",
          "承载力计算书" in h1s
          and h1s.index("承载力计算书") < h1s.index("结论与说明")
          and sheet_pos < detail_pos, " / ".join(h1s))
    html = report_3d.render_html(rep)
    check("HTML 报告含计算书表名与条文号",
          "承载力计算书（逐项复核）" in html
          and "§5.2.2" in html and "表 5.2.1" in html, "")
    out_dir = tempfile.mkdtemp()
    paths = report_3d.write_all(rep, out_dir, stem="capacity_calc")
    from docx import Document
    docx_cells = "\n".join(
        c.text for t in Document(str(paths["docx"])).tables
        for row in t.rows for c in row.cells)
    check("DOCX 报告含计算书中间量与条文号",
          "相对受压区高度" in docx_cells and "§5.2.2" in docx_cells
          and "ξb" in docx_cells, "")
    with zipfile.ZipFile(str(paths["zip"])) as zf:
        names = zf.namelist()
        csv_name = next((n for n in names
                         if "capacity_calculation_sheet" in n), None)
        n_lines = (len(zf.read(csv_name).decode("utf-8-sig").splitlines())
                   if csv_name else -1)
    check("CSV 出口逐行导出计算书",
          csv_name is not None
          and n_lines == len(tb["capacity_calculation_sheet"]["rows"]) + 1,
          f"{n_lines} 行 / 表 {len(tb['capacity_calculation_sheet']['rows'])} 行")
    from pypdf import PdfReader
    pdf_txt = "\n".join(p.extract_text() or ""
                        for p in PdfReader(str(paths["pdf"])).pages)
    check("PDF 计算书含中间量与条文号（从 PDF 读回）",
          "承载力计算书" in pdf_txt and "相对受压区高度" in pdf_txt
          and "§5.2.2" in pdf_txt and "ξb" in pdf_txt, "")

    # ============================================================ 汇总
    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(PASS)} 通过")
    print("=" * 78)
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
