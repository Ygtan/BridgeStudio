"""verify_suspension_3d.py
=========================
端到端回归：悬索桥（自锚式 / 地锚式）成桥态 BridgeStudio ↔ cable_solver。

为什么要单独一套校验
--------------------
悬索桥的错法跟斜拉桥不一样，而且**绝大多数不报错**：

- 斜拉桥算错，通常索力偏离目标、位移夸张，一眼能看出来；
- 悬索桥算错，往往是"体系错了但数学全对"：主缆成了锚到锚的自由索、
  H 被支座吃掉、边跨竖向分力把梁端拔起来……位移倾角都在正常范围，
  只有索力掉到百分之一。

所以这里校验的重点不是"收敛没收敛"，而是**结构关系有没有建立对**：

1. 主缆与桥塔必须共用同一个节点（坐标重合但号不同 = 主缆脱开塔）；
2. 塔处加劲梁必须有竖向支座（否则塔位梁节点把桥面荷载"扛"到邻跨）；
3. 自锚式的梁轴力必须 ≈ −H，地锚式必须 ≈ 0（体系自洽唯一硬判据）；
4. 竖向平衡用 Σ支座反力 = Σ竖向荷载，**不用 ΣT·ez**（后者在悬索桥里
   各项上下交替，求和结果取决于从哪端起算，能到恒载的几倍）。

校验清单
--------
[1] 建模与导出：节点编号复用、支座、.dat 三段结构
[2] 成桥态求解（两体系）：矢高、索力、平衡、体系自洽、下料长度
[3] 索长迭代稳定性：重复求解一致、L0 与浅垂度闭式对拍
[4] 参数扫描：跨径 / 矢跨比 / 边跨自动
[5] 负例：内核硬校验真的会拦住错模型（不是写在注释里好看）

理论基准
--------
索多边形：  H·dz/dx = V(x)，V(x) = V0 − S(x)，V0 = (M − H·Δz)/L
均布近似：  H ≈ qL²/(8f)          （仅作量级对照，真值由找形给出）
下料长度：  L0 = (L* + s)/(1 + T/EA)，s = w²Lh³/(24T²)
边跨平衡：  V = H·(Δz/L) − qL/2 = 0  ⇒  L = √(2HΔz/q)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fem_eigen_cable import cable_solver_available, find_cable_solver
from solver_3d import detect_kind, solve
from suspension_3d import build_suspension_3d

OUT = Path("out/verify_susp")
RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((bool(ok), name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


# ---------------------------------------------------------------- 小工具


def _el(model, eid: int):
    return next(e for e in model.elements if e.id == eid)


def _nd(model, nid: int):
    return next(n for n in model.nodes if n.id == nid)


def _beam_nodes(model) -> set[int]:
    """挂了梁单元（BEAM/BEAM7）的节点号。索与桁架不算——判"主缆有没有
    真正搭在塔上"，要的是塔柱这个梁单元，而不是另一根索。"""
    kinds = {t.id: str(getattr(t, "kind", "")).upper() for t in model.etypes}
    out: set[int] = set()
    for e in model.elements:
        if kinds.get(e.etype, "") in ("BEAM", "BEAM7"):
            out.add(e.n1)
            out.add(e.n2)
    return out


def _support_dofs(model, nid: int) -> set[int]:
    out: set[int] = set()
    for b in model.boundaries:
        if b.node == nid:
            out |= set(b.dofs)
    return out


# ---------------------------------------------------------------- [1] 建模


def stage1(model_build) -> None:
    print("\n[1] 建模与导出")
    for sys_name in ("EARTH", "SELF"):
        m, info = model_build(sys_name)
        tag = "地锚" if sys_name == "EARTH" else "自锚"
        x_of = {n.id: n.x for n in m.nodes}
        z_of = {n.id: n.z for n in m.nodes}
        beams = _beam_nodes(m)
        cb = m.cable_bridge

        # --- 体系判别 ---
        check(f"[{tag}] detect_kind = suspension", detect_kind(m) == "suspension",
              detect_kind(m))

        # --- .dat 三段结构 ---
        dat = m.to_dat()
        for kw in ("*SUSP_BRIDGE", f"SYSTEM={sys_name}", "*MAINCABLE", "*HANGER",
                   "TOWER1=", "TOWER2=", "SAG=", "DECKE0=", "DECKE1="):
            if kw not in dat:
                check(f"[{tag}] .dat 含 {kw}", False)
                break
        else:
            check(f"[{tag}] .dat 含 *SUSP_BRIDGE/*MAINCABLE/*HANGER 三段", True,
                  f"{len(cb.main_cable)} 段主缆 / {len(cb.hangers)} 根吊索")

        # --- 主缆与桥塔共用节点：全篇最贵的一条 ---
        #
        # 坐标重合但节点号不同，主缆就成了"锚到锚的自由索"：塔不参与受力，
        # 求解收敛、位移正常，只有索力掉到百分之一。这种错没有异常可抓，
        # 只能用结构关系判：主缆链经过塔位时，用的必须是**同时挂着梁单元**
        # 的那个节点号。
        t1, t2 = info["tower_nodes"]
        bad_tower = [t for t in (t1, t2) if t not in beams]
        check(f"[{tag}] 塔顶节点挂着梁单元（主缆与塔共用）", not bad_tower,
              f"TOWER={t1},{t2}；缺梁单元 {bad_tower}" if bad_tower
              else f"{t1}/{t2} 同时属于塔柱与主缆")

        # 反向：主缆链里不得出现"与塔顶坐标重合但号不同"的自建节点
        chain_nodes = []
        for eid in cb.main_cable:
            e = _el(m, eid)
            if not chain_nodes:
                chain_nodes.append(e.n1)
            chain_nodes.append(e.n2)
        dup = [(n, t) for n in chain_nodes for t in (t1, t2)
               if n != t and abs(x_of[n] - x_of[t]) < 1e-6
               and abs(z_of[n] - z_of[t]) < 1e-6]
        check(f"[{tag}] 主缆链无「坐标重合但号不同」的塔顶节点", not dup,
              f"重复 {dup[:2]}" if dup else f"{len(chain_nodes)} 个链节点")

        # --- 塔处加劲梁竖向支座 ---
        #
        # 少了它，塔位梁节点承担的桥面荷载只能靠相邻跨"扛过去"，
        # 凭空造出上万 kN·m 的弯矩——数值上完全合法，力学上是假的。
        for t in (t1, t2):
            xb = x_of[t]
            deck_at = [n.id for n in m.nodes
                       if 1000 < n.id < 2000 and abs(x_of[n.id] - xb) < 1e-6]
            ok = bool(deck_at) and all(3 in _support_dofs(m, n) for n in deck_at)
            check(f"[{tag}] 塔位（x={xb:.0f}）加劲梁有竖向支座", ok,
                  f"节点 {deck_at} 约束 dof {[_support_dofs(m, n) for n in deck_at]}")

        # --- 主缆端节点：必须锚得住 ---
        ends = (chain_nodes[0], chain_nodes[-1])
        if sys_name == "SELF":
            ok = all(1000 < n < 2000 for n in ends)
            check(f"[{tag}] 主缆两端锚在加劲梁端节点", ok, f"{ends}")
            # 梁端纵向必须自由，纵向约束放跨中
            free = all(1 not in _support_dofs(m, n) for n in ends)
            check(f"[{tag}] 梁端纵向自由（H 才进得了梁）", free,
                  f"梁端约束 {[_support_dofs(m, n) for n in ends]}")
        else:
            ok = all(n not in beams for n in ends)
            check(f"[{tag}] 主缆两端锚在锚碇（非梁节点）", ok, f"{ends}")

        # --- 平面外自由度：主缆自建节点必须约束 dof 2 ---
        own = [n.id for n in m.nodes if 2000 < n.id < 3000]
        unconstrained = [n for n in own if 2 not in _support_dofs(m, n)]
        check(f"[{tag}] 主缆自建节点约束平面外 dof 2", not unconstrained,
              f"{len(own)} 个节点，缺约束 {unconstrained[:3]}"
              if unconstrained else f"{len(own)} 个")

        # --- 单元/节点计数与 info 自洽 ---
        check(f"[{tag}] 节点/单元计数与 info 一致",
              len(m.nodes) == info["n_nodes"] and len(m.elements) == info["n_elements"],
              f"{len(m.nodes)}/{info['n_nodes']} 节点，"
              f"{len(m.elements)}/{info['n_elements']} 单元")


# ---------------------------------------------------------------- [2] 求解


def stage2(model_build) -> dict:
    print("\n[2] 成桥态求解（两体系）")
    got: dict[str, object] = {}
    for sys_name in ("EARTH", "SELF"):
        m, info = model_build(sys_name)
        tag = "地锚" if sys_name == "EARTH" else "自锚"
        res = solve(m, out_dir=OUT, dat_name=f"v_{sys_name}.dat")
        md = res.meta
        got[sys_name] = (m, info, res)

        check(f"[{tag}] 收敛", res.converged,
              f"{res.iterations} 轮 / {res.elapsed_ms:.0f} ms")

        # 矢高命中
        sag, sag_t = md.get("sag_m", 0.0), md.get("sag_target_m", 0.0)
        er = abs(sag - sag_t) / max(sag_t, 1e-9)
        check(f"[{tag}] 找形矢高命中（<1e-3）", er < 1e-3,
              f"{sag:.4f} / {sag_t:.4f} m（{er:.1e}）")

        # 索力达到找形目标
        terr = float(md.get("tension_err") or 1.0)
        check(f"[{tag}] 索力达到目标（<2%）", terr < 0.02, f"{terr * 100:.2f}%")

        # 竖向平衡：Σ支座反力 = Σ竖向荷载
        bal = abs(float(md.get("balance_pct") or 0.0))
        check(f"[{tag}] 竖向平衡 |偏差| < 1e-3 %", bal < 1e-3,
              f"{md.get('support_v_sum_kN', 0):,.0f} / "
              f"{md.get('applied_v_kN', 0):,.0f} kN（{bal:.1e}%）")

        # H 与手算均布近似对照（量级校验，不是精度校验）
        H_est = info["H_estimate"] / 1e3
        H = float(md.get("H_kN") or 0.0)
        ratio = H / max(H_est, 1e-9)
        check(f"[{tag}] H 与 qL²/(8f) 同量级（0.88~1.20）", 0.88 <= ratio <= 1.20,
              f"内核 {H:,.0f} / 手算 {H_est:,.0f} kN = {ratio:.3f}")

        # 吊索力均匀
        ht = [float(h["T"]) for h in res.hangers if float(h["T"]) > 0]
        spread = max(ht) / min(ht) if len(ht) >= 2 else 1.0
        check(f"[{tag}] 吊索力均匀（≤1.30）", spread <= 1.30,
              f"{min(ht) / 1e3:.1f}~{max(ht) / 1e3:.1f} kN = {spread:.2f}")

        # 体系自洽：这一条最能拦住"纵向约束放错位置"
        N, Hk = float(md.get("girder_N_max_kN") or 0.0), max(H, 1e-9)
        if sys_name == "SELF":
            rel = abs(abs(N) - Hk) / Hk
            check("[自锚] 梁轴力 ≈ −H（偏差 <10%）", rel < 0.10,
                  f"N {N:,.0f} / H {Hk:,.0f} kN（{rel * 100:.1f}%）")
        else:
            rel = abs(N) / Hk
            check("[地锚] 梁轴力 ≈ 0（<5% H）", rel < 0.05,
                  f"N {N:,.0f} kN = {rel * 100:.1f}% H")

        # 下料长度为正且有限（L0 由索长迭代反算，任何一段为 0 都说明模型有洞）
        bad_l0 = [r["elem"] for r in res.main_cable if float(r["L0"]) <= 0.0]
        check(f"[{tag}] 主缆下料长度为正且有限", not bad_l0,
              f"{len(res.main_cable)} 段，"
              f"L0 {min(float(r['L0']) for r in res.main_cable):.3f}~"
              f"{max(float(r['L0']) for r in res.main_cable):.3f} m")

        # 校核项：全部通过（info 级不算不通过）
        ng = [c for c in res.checks(m) if not c.passed]
        check(f"[{tag}] 全部校核通过", not ng,
              "、".join(f"{c.name}" for c in ng[:3]) if ng else "无 NG 项")

        # 计算书所需数据齐全
        tb = res.tables(m)
        need = ["main_cable_table", "hanger_table", "girder_table", "reaction_table"]
        miss = [k for k in need if k not in tb]
        check(f"[{tag}] 明细表齐全", not miss, f"缺 {miss}" if miss else
              f"{len(tb)} 张（含主缆/吊索/加劲梁/支反力）")
    return got


# ---------------------------------------------------------------- [3] 迭代


def stage3(got: dict) -> None:
    print("\n[3] 索长迭代稳定性与闭式对拍")
    m, info, res = got["SELF"]
    md = res.meta
    cb = m.cable_bridge

    # 重复求解：索长迭代是确定性迭代，两次结果必须逐位一致
    res2 = solve(m, out_dir=OUT, dat_name="v_SELF_rep.dat")
    dH = abs(float(res2.meta.get("H_kN", 0)) - float(md.get("H_kN", 0)))
    tmax1 = max(float(r["T"]) for r in res.main_cable)
    tmax2 = max(float(r["T"]) for r in res2.main_cable)
    check("重复求解 H 一致（<1e-6 kN）", dH < 1e-6, f"ΔH = {dH:.2e} kN")
    check("重复求解最大索力一致（<1e-3 kN）", abs(tmax1 - tmax2) < 1e-3,
          f"ΔT = {abs(tmax1 - tmax2):.2e} kN")

    # L0 与浅垂度闭式对拍：L0 = (L* + s)/(1 + T/EA)
    #
    # L* 必须取内核输出的**平衡态弦长**（[MAINCABLE] 第 5 列），不是设计弦长。
    # 两者差一个弹性缩短量，自锚式能到 150 mm 以上；用设计弦长反算会让达到的
    # 索力系统性偏低 1~6%，差额全变成梁的恒载弯矩。这条以前吃过亏，所以
    # 内核现在把弦长直接印出来，校核不必再猜。
    rc_main = next(r for r in m.reals if r.id == 3)          # 实常数 3 = 主缆
    EA = rc_main.A * next(mm for mm in m.materials if mm.id == 3).E
    w = rc_main.W
    worst, worst_seg, no_chord = 0.0, 0, 0
    for r in res.main_cable:
        ch = float(r.get("chord") or 0.0)
        T = float(r["T"])
        if ch <= 0:
            no_chord += 1
            continue
        Lh = ch                                          # 等价水平投影的下界
        sag = w * w * Lh ** 3 / (24 * T * T) if T > 0 else 0.0
        l0 = (ch + sag) / (1 + T / EA)
        dev = abs(l0 - float(r["L0"])) / max(float(r["L0"]), 1e-9)
        if dev > worst:
            worst, worst_seg = dev, r["elem"]
    check("L0 vs 浅垂度闭式（<1e-4）", worst < 1e-4 and not no_chord,
          f"max 相对偏差 {worst:.2e}（单元 {worst_seg}）"
          + (f"，{no_chord} 段缺弦长" if no_chord else ""))

    # 平衡弦长 ≠ 设计弦长。两个来源叠加：①找形把主缆从建造器给的初值挪到
    # 真线形（边跨最明显，锚点处必须水平）；②自锚式梁被 H 压短，主缆跨度
    # 随之缩短。这条判据的作用是确认内核真的输出了平衡弦长——若它偷懒回传
    # 设计弦长，上面那条 L0 校核会"照样通过"，但数值是错的。
    design = {}
    for eid in cb.main_cable:
        e = _el(m, eid)
        a, b = _nd(m, e.n1), _nd(m, e.n2)
        design[eid] = ((b.x - a.x) ** 2 + (b.y - a.y) ** 2 + (b.z - a.z) ** 2) ** 0.5
    dmax = max(abs(float(r.get("chord") or 0.0) - design[r["elem"]])
               for r in res.main_cable)
    check("内核输出的是平衡弦长（非设计弦长）", dmax > 1e-3,
          f"最大差 {dmax * 1000:.1f} mm（找形线形 + 梁体压缩）")

    # 自锚式梁体缩短量级：Δ ≈ H·L/EA（**梁的** EA，不是主缆的）
    rc_gir = next(r for r in m.reals if r.id == 1)          # 实常数 1 = 加劲梁
    EA_gir = rc_gir.A * next(mm for mm in m.materials if mm.id == 1).E
    deck = sorted((n for n in m.nodes if 1000 < n.id < 2000), key=lambda n: n.x)
    ua = res.disp.get((deck[0].id, 1), (0.0, 0.0))[0]
    ub = res.disp.get((deck[-1].id, 1), (0.0, 0.0))[0]
    short_mm = abs(ua - ub) * 1000
    est = float(md.get("H_kN", 0)) * 1e3 * info["total_length"] / EA_gir * 1000
    check("自锚式梁体缩短量级与 H·L/EA 相符（0.5~2 倍）",
          0.5 <= short_mm / max(est, 1e-9) <= 2.0,
          f"实测 {short_mm:.1f} mm / 估算 {est:.1f} mm")

    # 边跨主缆在锚点处必须水平：平衡边跨的定义就是 V(L)=0，而平衡边跨又
    # 是自锚式唯一能用的边跨（梁端支座不能承拉）。所以主缆在梁端不是"斜着
    # 上去的直线"，而是顶点落在锚点的抛物线——建造器的初始值差得能有几米，
    # 图件与设计线形必须取 [CABLESHAPE]，不能取模型坐标。
    shp = sorted(res.cable_shape.items(), key=lambda kv: kv[1][0]) \
        if res.cable_shape else []
    if len(shp) >= 3:
        (_, (x0, z0)), (_, (x1, z1)) = shp[0], shp[1]
        slope = (z1 - z0) / max(x1 - x0, 1e-9)
        check("找形线形：边跨主缆在锚点处水平（V≈0）", abs(slope) < 0.05,
              f"锚点段 dz/dx = {slope:.4f}（x {x0:.1f}→{x1:.1f}）")
    else:
        check("内核输出找形线形 [CABLESHAPE]", False, "缺失：图件将退回模型初值")

    # 平衡边跨 = √(2HΔz/q)：只对自锚式有约束力
    Lbal = info["side_span_balanced"]
    check("自锚式边跨自动取平衡边跨（±2%）",
          abs(info["side_span"] - Lbal) / max(Lbal, 1e-9) < 0.02,
          f"边跨 {info['side_span']:.1f} m / 平衡 {Lbal:.1f} m")


# ---------------------------------------------------------------- [4] 扫描


def stage4() -> None:
    print("\n[4] 参数扫描")
    cases = [
        dict(system="EARTH", main_span=200.0, sag_ratio=0.10),
        dict(system="SELF", main_span=200.0, sag_ratio=0.10),
        dict(system="EARTH", main_span=300.0, sag_ratio=1 / 8),
        dict(system="EARTH", main_span=300.0, sag_ratio=1 / 12),
        dict(system="SELF", main_span=400.0, sag_ratio=0.09, side_span=140.0),
    ]
    for c in cases:
        label = (f"{c['system']} L={c['main_span']:.0f} "
                 f"1/{1 / c['sag_ratio']:.0f}")
        try:
            m, info = build_suspension_3d(**c)
            r = solve(m, out_dir=OUT,
                      dat_name=f"s_{c['system']}_{int(c['main_span'])}_"
                               f"{int(1 / c['sag_ratio'])}.dat")
            ok = (r.converged
                  and abs(float(r.meta.get("sag_m", 0))
                          - float(r.meta.get("sag_target_m", 1))) < 1e-3
                  and abs(float(r.meta.get("balance_pct") or 0)) < 1e-3)
            check(f"{label} 收敛且平衡", ok,
                  f"{r.iterations} 轮，H={r.meta.get('H_kN', 0):,.0f} kN，"
                  f"T={r.meta.get('tension_max_kN', 0):,.0f} kN，"
                  f"平衡 {r.meta.get('balance_pct', 0):+.1e}%")
        except Exception as exc:                            # noqa: BLE001
            check(f"{label} 收敛且平衡", False, str(exc)[:90])


# ---------------------------------------------------------------- [5] 负例


def stage5() -> None:
    print("\n[5] 负例：内核硬校验")
    exe = find_cable_solver()
    m, info = build_suspension_3d(system="EARTH")
    dat_txt = m.to_dat()
    OUT.mkdir(parents=True, exist_ok=True)

    # --- 5a 塔顶节点改成"不挂梁单元的主缆节点" ---
    #
    # 这正是最初把主缆做成"锚到锚自由索"的那一类错：TOWER1 指到一个只在
    # 主缆链上出现的节点。求解照样能收敛，所以必须在入口拦住。
    own = next(n.id for n in m.nodes if 2000 < n.id < 3000)
    bad = dat_txt.replace(f"TOWER1={info['tower_nodes'][0]}", f"TOWER1={own}", 1)
    p = (OUT / "neg_tower.dat").resolve()
    p.write_text(bad, encoding="utf-8")
    r = subprocess.run([str(exe), str(p)], capture_output=True, text=True,
                       cwd=str(OUT), timeout=300)
    msg = (r.stdout or "") + (r.stderr or "")
    check("塔顶节点无梁单元 → 内核报错",
          r.returncode not in (0, 3) and "共用同一个节点" in msg,
          f"rc={r.returncode}，{msg.strip().splitlines()[-1][:70] if msg.strip() else '无输出'}")

    # --- 5b 主缆端节点既不约束、也不接梁 ---
    ends = (info["anchor_nodes"][0], info["anchor_nodes"][1])
    # 把锚碇节点改成一个凭空多出来的节点：去掉 5001 的约束行即可
    lines = [ln for ln in dat_txt.splitlines()
             if not (ln.strip().startswith("5001") and "BOUND" not in ln.upper())]
    bad2 = "\n".join(lines)
    # 精确做法：删掉 *BOUNDARY 段里 node=5001 的行
    out_lines, sec = [], ""
    for ln in dat_txt.splitlines():
        s = ln.strip()
        if s.startswith("*"):
            sec = s.upper()
        if sec.startswith("*BOUND") and s and s.split()[0] in (str(ends[0]), str(ends[1])):
            continue
        out_lines.append(ln)
    p2 = (OUT / "neg_anchor.dat").resolve()
    p2.write_text("\n".join(out_lines), encoding="utf-8")
    r2 = subprocess.run([str(exe), str(p2)], capture_output=True, text=True,
                        cwd=str(OUT), timeout=300)
    msg2 = (r2.stdout or "") + (r2.stderr or "")
    check("主缆端节点既不约束也不接梁 → 内核报错",
          r2.returncode not in (0, 3),
          f"rc={r2.returncode}，{msg2.strip().splitlines()[-1][:70] if msg2.strip() else '无输出'}")

    # --- 5c 正确模型仍能跑通（负例不能把正例一起拦掉）---
    p3 = (OUT / "neg_ok.dat").resolve()
    p3.write_text(dat_txt, encoding="utf-8")
    r3 = subprocess.run([str(exe), str(p3)], capture_output=True, text=True,
                        cwd=str(OUT), timeout=300)
    check("同一份 .dat 未改动时可正常求解", r3.returncode == 0,
          f"rc={r3.returncode}")


# ---------------------------------------------------------------- main


def main() -> int:
    print("=" * 66)
    print("  悬索桥成桥态端到端回归（BridgeStudio → cable_solver）")
    print("=" * 66)
    if not cable_solver_available():
        print("  cable_solver.exe 不可用，无法回归。")
        print("  编译： cd D:/AI-work/FEM-Eigen && cmake --build build --target cable_solver")
        return 1

    def build(sys_name: str):
        return build_suspension_3d(system=sys_name, main_span=300.0,
                                   sag_ratio=0.10, title=f"{sys_name} 回归算例")

    stage1(build)
    got = stage2(build)
    stage3(got)
    stage4()
    stage5()

    n = sum(1 for r in RESULTS if r[0])
    print("=" * 66)
    print(f"  {n}/{len(RESULTS)} 通过")
    if n < len(RESULTS):
        print("  未通过：")
        for ok, name, detail in RESULTS:
            if not ok:
                print(f"    · {name}  {detail}")
    return 0 if n == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
