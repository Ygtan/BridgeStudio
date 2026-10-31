"""verify_combo_3d.py —— 荷载组合与活载内力的**解析对拍**。

守什么
------
`verify_report_3d` 已经证明"组合值等于内核数乘系数"，但它把内核给的两个
数当已知量，证明不了这两个数**本身对不对**。1.2·S_G + 1.4·S_Q 的式子写
对了、可内核的活载内力错了，那种检查照样全绿。

所以要有一个能用笔算出答案的模型：**单跨简支梁**。

    M_G = q·L²/8
    M_Q = q_k·L²/8 + P_k·L/4        （JTG D60 车道荷载，集中力置于跨中）
    M_ud = 1.2·M_G + 1.4·M_Q

这三条式子没有离散误差、没有迭代、没有反算，是混凝土课本第一章的内容。
把内核算出来的数按位对上，才能说"活载影响线把集中力也算进去了、量纲没
丢节点间距"。

为什么要专门建一个模型
----------------------
梁格算例是三跨连续的，跨中活载弯矩受相邻跨影响，没有初等解析式；横向
横梁还会把荷载分给边梁。想对答案就得把横向和连续都拿掉——单根纵梁、
两端铰支、无横梁。这 17 个节点是**测试脚手架**，不是产品算例。

踩过的坑
--------
1. 活载包络一度按 `q·Ση` 积分，漏掉节点间距权重，量纲成了 N 而不是
   N·m（差 2.5 倍，正好是横梁间距）。简支梁对拍能一眼抓出来。
2. `Boundary3D.dofs` 是**闭区间** [a,b]，不是自由度列表。约束 u,v,w,θx
   要写 (1,4)，不是 (1,2,3,4)。
3. θy 必须放开：它是竖向弯曲的转角自由度，约束了就成固端梁，跨中弯矩
   会从 qL²/8 变成 qL²/24。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()          # 输出被重定向到管道/文件时，GBK 会崩（见模块说明）

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from model_spec_3d import (Boundary3D, Element3D, ElemTypeDef3D,
                           Influence3D, Lane3D, Material3D, ModelSpec3D,
                           Node3D, NodalLoad3D, RealConst3D, Stage3D)
from solver_3d import solve

PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def near(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol * max(abs(b), 1e-12)


# ---------------------------------------------------------------- 测试脚手架


L, N, Q = 40.0, 16, 30e3                     # 跨径 m / 单元数 / 恒载 N·m⁻¹
E, B, H = 34.5e9, 0.6, 1.6                   # C50 弹性模量 / 截面宽高
A = B * H
IY = B * H ** 3 / 12.0                       # 竖向弯曲惯矩
IZ = H * B ** 3 / 12.0
J = 0.229 * B * H ** 3                       # 矩形截面扭转常数（近似）
G = E / (2 * (1 + 0.2))


def simple_beam(lane: bool = True) -> ModelSpec3D:
    """单跨简支梁：单根纵梁 + 两端铰支。竖向弯曲解析解已知。"""
    xs = [L * i / N for i in range(N + 1)]
    nodes = [Node3D(id=i + 1, x=xs[i], y=0.0, z=0.0) for i in range(N + 1)]
    els = [Element3D(id=i + 1, etype=1, n1=i + 1, n2=i + 2) for i in range(N)]
    # 节点受荷宽度 = 左右半段之和（端节点只有半段）
    trib = []
    for i in range(N + 1):
        left = (xs[i] - xs[i - 1]) / 2 if i > 0 else 0.0
        right = (xs[i + 1] - xs[i]) / 2 if i < N else 0.0
        trib.append(left + right)
    loads = [NodalLoad3D(node=i + 1, dof=3, value=-Q * trib[i]) for i in range(N + 1)]
    # dofs 是闭区间；左端 1..4(u,v,w,θx)+θz，右端 2..4+θz，θy 放开 = 铰支
    bounds = [Boundary3D(node=1, dofs=(1, 4)), Boundary3D(node=1, dofs=(6, 6)),
              Boundary3D(node=N + 1, dofs=(2, 4)), Boundary3D(node=N + 1, dofs=(6, 6))]
    return ModelSpec3D(
        title="单跨简支梁 40 m（解析对拍）", nodes=nodes,
        materials=[Material3D(id=1, E=E, dens=2500.0)],
        reals=[RealConst3D(id=1, A=A, IY=IY, IZ=IZ, J=J, G=G,
                           WT=IY / (H / 2), WB=IY / (H / 2))],
        etypes=[ElemTypeDef3D(id=1, kind="BEAM", mat=1, real=1)],
        elements=els, boundaries=bounds,
        stages=[Stage3D(id=1, activate=[e.id for e in els], loads=loads)],
        influences=[Influence3D(node=N // 2 + 1, dof=3)] if lane else [],
        lanes=[Lane3D(nodes=[i + 1 for i in range(N + 1)], dof=3, code="JTG_D60")]
        if lane else [],
    )


def main() -> int:
    print("=" * 78)
    print("  荷载组合解析对拍：单跨简支梁")
    print("=" * 78)
    print(f"  L={L:.0f} m　q={Q / 1e3:.0f} kN/m　{H}×{B} m　"
          f"I={IY:.4f} m⁴　N={N} 单元")

    qk = 10.5e3                                        # JTG D60 qk
    pk = 270e3 + (360e3 - 270e3) / 45 * (L - 5)        # L=40 → 340 kN
    m_g = Q * L * L / 8                                # 恒载跨中弯矩
    m_q = qk * L * L / 8 + pk * L / 4                  # 活载跨中弯矩（含集中力）
    defl = 5 * Q * L ** 4 / (384 * E * IY)             # 恒载跨中挠度
    defl_q = (5 * qk * L ** 4 / (384 * E * IY)         # 活载跨中挠度：
              + pk * L ** 3 / (48 * E * IY))           # 均布 + 跨中集中力

    # ---------------------------------------------------------- 恒载与活载
    print("\n[单工况：内核 vs 解析]")
    res = solve(simple_beam(), kind="grillage3d", out_dir="out/verify_combo")
    mid = (N // 2, "j")                                # 跨中单元的 j 端
    g_my = res.final_beam.get(mid, {}).get("My", 0.0)
    check("成桥态梁内力落在单元两端（不是单元中点）",
          len(res.final_beam) == 2 * N
          and all(k[1] in ("i", "j") for k in res.final_beam),
          f"{len(res.final_beam)} 行")

    # 跨中弯矩取单元两端的较大绝对值：i 端在左侧、j 端在右侧，两者都等于
    # qL²/8（等分网格时中点在单元边界上）
    m_g_got = max(abs(res.final_beam[(N // 2, s)]["My"]) for s in ("i", "j"))
    check("恒载跨中弯矩 = qL²/8", near(m_g_got, m_g, 2e-3),
          f"FEM {m_g_got / 1e3:,.1f} / 解析 {m_g / 1e3:,.1f} kN·m"
          f"（差 {abs(m_g_got - m_g) / m_g:.2%}）")

    lv = res.live_beam[mid]
    m_q_got = abs(lv["MyMin"])
    check("活载跨中弯矩 = qk·L²/8 + Pk·L/4", near(m_q_got, m_q, 5e-3),
          f"FEM {m_q_got / 1e3:,.1f} / 解析 {m_q / 1e3:,.1f} kN·m"
          f"（qk {qk / 1e3:.1f} + Pk {pk / 1e3:.0f}）")
    check("活载弯矩包络只有向下（简支梁无上拱）", abs(lv["MyMax"]) < 1.0,
          f"max={lv['MyMax']:.3g} N·m")

    # 位移：把量纲与单元长度再验一遍。活载积分若漏节点间距，弯矩会差
    # 2.5 倍；位移同理。
    u_mid = res.final_disp.get((N // 2 + 1, 3), 0.0)
    check("恒载跨中挠度 = 5qL⁴/(384EI)", near(abs(u_mid), defl, 5e-3),
          f"FEM {abs(u_mid) * 1000:.2f} / 解析 {defl * 1000:.2f} mm")

    # ---------------------------------------------------------- 组合值
    print("\n[组合：三条式子逐一对]")
    by = {c.spec.key: c for c in res.combinations()}
    check("三种组合都产出", set(by) == {"ultimate", "frequent", "quasi"}, str(sorted(by)))
    check("组合表含活载项（has_live）", all(c.has_live for c in by.values()))

    ult = abs(by["ultimate"].rows[mid]["MyMin"])
    freq = abs(by["frequent"].rows[mid]["MyMin"])
    quasi = abs(by["quasi"].rows[mid]["MyMin"])
    check("基本组合 = 1.2·M_G + 1.4·M_Q",
          near(ult, 1.2 * m_g + 1.4 * m_q, 5e-3),
          f"FEM {ult / 1e3:,.1f} / 解析 {(1.2 * m_g + 1.4 * m_q) / 1e3:,.1f} kN·m")
    check("频遇组合 = M_G + 0.7·M_Q", near(freq, m_g + 0.7 * m_q, 5e-3),
          f"{freq / 1e3:,.1f} kN·m")
    check("准永久组合 = M_G + 0.4·M_Q", near(quasi, m_g + 0.4 * m_q, 5e-3),
          f"{quasi / 1e3:,.1f} kN·m")
    check("控制截面在跨中", by["ultimate"].worst("My")[0][0] == N // 2,
          str(by["ultimate"].worst("My")[0]))

    # 活载位移：5qk·L⁴/(384EI) + Pk·L³/(48EI)。这条同时守住"影响点取的是
    # 最后一个阶段"——施工中间态的模型残缺，那里活载位移恒为 0。
    u_q = abs(res.live[max(res.live)][1]) if res.live else 0.0
    check("活载跨中挠度 = 5qk·L⁴/(384EI) + Pk·L³/(48EI)",
          near(u_q, defl_q, 5e-3),
          f"FEM {u_q * 1000:.2f} / 解析 {defl_q * 1000:.2f} mm")

    # 位移组合：1.2×恒载挠度 + 1.4×活载挠度（同一点、同方向）
    dc = {d.spec.key: d for d in res.disp_combinations()}
    u_sq = abs(dc["frequent"].sq) if dc else 0.0
    check("位移组合的活载项不为零（成桥态取到活载位移）", u_sq > 1e-6,
          f"u_Q={u_sq * 1000:.2f} mm")
    check("位移组合 = γ_G·u_G + γ_Q·u_Q（同点同向叠加）",
          bool(dc) and near(abs(dc["ultimate"].value),
                            1.2 * abs(dc["ultimate"].sg) + 1.4 * u_sq, 1e-9),
          f"u_G={dc['ultimate'].sg * 1000:.2f} u_Q={u_sq * 1000:.2f} "
          f"组合={dc['ultimate'].value * 1000:.2f} mm" if dc else "—")

    # ---------------------------------------------------------- 系数与出处
    print("\n[系数与条文号]")
    from combination import ALL, coefficient_table
    coef = coefficient_table()
    gammas = {s.key: (s.gamma_g, s.gamma_q) for s in ALL}
    check("γ_G=1.2 / γ_Q=1.4（基本组合）", gammas["ultimate"] == (1.2, 1.4),
          str(gammas["ultimate"]))
    check("ψ1=0.7 / ψ2=0.4（频遇/准永久）",
          gammas["frequent"][1] == 0.7 and gammas["quasi"][1] == 0.4,
          f"{gammas['frequent'][1]} / {gammas['quasi'][1]}")
    check("系数表逐行带条文出处",
          all(s.basis and ("4.1.5" in s.basis or "4.1.7" in s.basis) for s in ALL),
          "、".join(s.basis.split("（")[0] for s in ALL))
    check("系数表 3 行 6 列", len(coef["rows"]) == 3 and len(coef["headers"]) == 6)

    # ---------------------------------------------------------- 缺活载
    print("\n[缺车道荷载：只出恒载项，并标 has_live=False]")
    res0 = solve(simple_beam(lane=False), kind="grillage3d",
                 out_dir="out/verify_combo_nolane")
    c0 = {c.spec.key: c for c in res0.combinations()}
    check("无车道时 has_live=False", not c0["ultimate"].has_live)
    check("无车道时组合 = γ_G·S_G（不含活载）",
          near(abs(c0["ultimate"].rows[mid]["MyMin"]), 1.2 * m_g, 5e-3),
          f"{abs(c0['ultimate'].rows[mid]['MyMin']) / 1e3:,.1f} kN·m")
    check("无车道时校核给出 warn",
          any("组合里只有恒载项" in c.detail or "不是组合值" in c.detail
              for c in res0.checks(simple_beam(lane=False)) if not c.passed),
          "活载效应")

    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(PASS)} 通过")
    if n_pass != len(PASS):
        print("  失败项：" + "、".join(n for n, ok, _ in PASS if not ok))
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
