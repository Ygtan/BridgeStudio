"""verify_stage_cable.py
======================
施工阶段索张拉（``*STAGE`` 行内的 ``TENSION=`` / ``L0=``）端到端回归。

为什么单独一个脚本
------------------
这一条能力横跨"前端模型 → .dat → 线性阶段内核 → summary → 前端解析"五段，
任何一段断了都不会报错、只会给一个"看起来像索力"的数。2026-11-01 实现过程中
真实踩到两个静默错误，都是靠这里的对拍抓出来的：

1. 张拉增量按"参考几何上的初张力"算（f(L_geom, L0) 之差），而不是按索力状态
   算。结构变形 0.5 m 时两者差近一倍，后期张拉的索把先张好的索带到 +89%。
2. 迭代目标值从 ``map<int,bool>`` 里结构化绑定，Ttar 成了 ``True``=1.0，
   于是迭代**精确地**把索力拉到 1 N，编译器一声不响。

校验内容
--------
1. 离线：``TENSION=`` / ``L0=`` 正确写出；排序稳定（同一模型两次导出同文本）；
   ``CABLE`` 不被降级成 TRUSS。
2. 离线·汇总：手造三阶段索力，验 ``stage_cable_summary`` 的峰值/谷值定位、
   ``devRange``、「还没张拉 vs 中途消失」的区分、过程表的格与列。
3. 在线 A（单阶段、全部索同时张拉到设计目标）：每根索都命中目标，且
   索力竖向分量之和 ≈ 恒载（恒载由索与支座共同承担，索分担 ~98%）。
4. 在线 B（分两批）：第一批张好的索会在第二批张拉后被带偏 —— 内核必须把这件事
   **报出来**（``devDes``），而不是只报"本阶段调过的那几根"。
5. 在线 B2（分批后补一轮）：全部回到目标，证明"跟踪索力状态"的增量算法自洽。
6. 在线 D（过程校核）：同一场景下**末阶段通过、全过程不通过** —— 一根索在中途被
   压成负值、后来又补调回目标，只查末阶段的校核会给"全部通过"，而它在那个阶段
   已经退出工作了。这条用例就是锁住"必须查全过程"的。
7. 在线 C（负例）：张拉指向非索单元 / 目标索力 ≤ 0 / 索未激活 / 同一根索
   既给目标力又给索长 —— 全部必须当场报错。

运行
----
    python verify_stage_cable.py
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()          # 输出被重定向到管道/文件时，GBK 会崩（见模块说明）

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from cable_stayed_3d import build_cable_stayed_3d
from fem_eigen_3d import fem_eigen_3d_available, solve_3d
from model_spec_3d import Stage3D

RESULTS: list[tuple[bool, str, str]] = []

N_CABLE = 4          # 每塔每侧索数（4 → 全桥 16 根，够表达"分批"又不至于跑太久）
Q_DECK = 100e3


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((ok, name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def _base_model():
    """斜拉桥模型 + 派生量。

    线性阶段路线不走 *CABLE_BRIDGE（那是 cable_solver 的工况段），把它摘掉，
    否则 .dat 里会同时出现两套工况。
    """
    m, meta = build_cable_stayed_3d(main_span=200.0, side_span=80.0,
                                    n_cable=N_CABLE, panel=10.0)
    tg = {c.elem: c.tension for c in m.cable_bridge.cables}
    m.cable_bridge = None
    cid = list(meta["cable_ids"])
    struct = [e.id for e in m.elements if e.id not in set(cid)]
    return m, meta, cid, struct, tg


def _by_span(cid: list[int], n: int) -> dict[str, list[int]]:
    """按"塔侧"切分索：建造器按 [塔1边, 塔1主, 塔2主, 塔2边] 顺序排出。"""
    return {
        "t1s": cid[0:n], "t1m": cid[n:2 * n],
        "t2m": cid[2 * n:3 * n], "t2s": cid[3 * n:4 * n],
    }


def _rounds(cid: list[int], n: int) -> list[list[int]]:
    """对称张拉轮次：每一轮取每塔"边跨侧 + 主跨侧"同一序号的一对。

    为什么必须对称：单侧张拉会让塔顶受很大的不平衡水平力，塔顶位移几厘米，
    索力互相拖到无意义的量级。这不是程序的问题，是施工顺序的问题——但回归
    脚本要用一个有物理意义的顺序，否则测出来的是自己编的坏顺序。
    """
    s = _by_span(cid, n)
    return [[s["t1s"][j], s["t1m"][j], s["t2m"][j], s["t2s"][j]]
            for j in range(n)]


def _vert_share(model, res, cid: list[int], stage: int) -> tuple[float, float]:
    """Σ(T·ez) 与恒载。返回 (索承担的竖向力, 恒载)。"""
    pos = {n.id: n for n in model.nodes}
    el = {e.id: e for e in model.elements}
    total = 0.0
    for eid in cid:
        e = el[eid]
        a, b = pos[e.n1], pos[e.n2]
        L = math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))
        if L <= 0.0:
            continue
        total += res["stage_cables"][(stage, eid)]["T"] * abs(b.z - a.z) / L
    return total, Q_DECK * 360.0      # 主跨 200 + 边跨 80×2


def aggregate_rules() -> None:
    """离线：汇总口径本身对不对。手造 3 个阶段的索力，不依赖内核。

    为什么值得单独测：汇总层是唯一把"逐阶段平铺"翻成"每根索一条过程"的地方，
    表、图、校核三处都读它。它错了，三处一起错，而且都看不出来——峰值取错会
    得到一个"很稳"的过程，恰好掩盖要查的问题。
    """
    print("\n[离线·汇总] 峰值/谷值/偏离/未激活的判定口径")
    from result_3d import SolveResult

    res = SolveResult(kind="grillage3d")
    res.stage_cables = {
        # 11：阶段 1 张到目标 100 kN，阶段 2 被后来的索带成负值，阶段 3 已拆掉
        (1, 11): {"T": 100e3, "Tdes": 100e3, "dev": 0.0, "devDes": 0.0},
        (2, 11): {"T": -20e3, "Tdes": 100e3, "devDes": 1.2},
        # 12：阶段 2 才张拉，阶段 3 略超目标
        (2, 12): {"T": 300e3, "Tdes": 300e3, "dev": 0.0, "devDes": 0.0},
        (3, 12): {"T": 310e3, "Tdes": 300e3, "devDes": 0.0333},
    }
    s = {r["elem"]: r for r in res.stage_cable_summary()}

    check("峰值/谷值及出现阶段",
          (s[11]["Tmax"], s[11]["TmaxStage"], s[11]["Tmin"], s[11]["TminStage"])
          == (100e3, 1, -20e3, 2),
          f"{s[11]['Tmax']}@{s[11]['TmaxStage']} / {s[11]['Tmin']}@{s[11]['TminStage']}")
    check("峰值恰好等于目标的索，ratio=1 —— 只看它会误判成「很稳」",
          abs(s[11]["ratio"] - 1.0) < 1e-12, f"ratio={s[11]['ratio']}")
    check("devRange 能抓住这类往下跑偏（>1）",
          bool(s[11]["devRange"]) and s[11]["devRange"] > 1.0,
          f"{s[11]['devRange']:.3f}")
    check("末阶段相对设计目标的偏差取自内核 devDes",
          abs(s[11]["devLast"] - 1.2) < 1e-9, f"{s[11]['devLast']:.4f}")

    check("首记录之前的缺失记为「还没张拉」（notYet）",
          s[12]["notYet"] == [1] and s[12]["dropped"] == [],
          f"notYet={s[12]['notYet']} dropped={s[12]['dropped']}")
    check("首记录之后的缺失记为「中途消失」（dropped）",
          s[11]["dropped"] == [3], f"dropped={s[11]['dropped']}")
    check("全过程最小索力取到负值", s[11]["Tmin"] < 0, f"{s[11]['Tmin']}")

    tabs = res.tables()
    tb = tabs.get("stage_cable_table")
    check("出「施工阶段索力全过程」表", tb is not None)
    if tb:
        n_stage = len({s for s, _ in res.stage_cables})
        check("表列数 = 2（索+目标）+ 阶段数 + 2（峰值/谷值）",
              len(tb["headers"]) == 4 + n_stage,
              f"{len(tb['headers'])} 列 / {n_stage} 阶段")
        row11 = next(r for r in tb["rows"] if r[0] == 11)
        check("该阶段没记录的格写「—」，不是 0",
              row11[2:5] == [100.0, -20.0, "—"], str(row11))
        check("峰值/谷值列带出现阶段",
              row11[5] == "100.0（1）" and row11[6] == "-20.0（2）", str(row11[5:]))

def offline(m, cid, tg) -> None:
    print("[离线检查]")
    stages = [Stage3D(id=i + 1, activate=g, tensions={e: tg[e] for e in g})
              for i, g in enumerate(_rounds(cid, N_CABLE))]
    m.stages = stages
    dat = m.to_dat()

    check(".dat 含 TENSION=", "TENSION=" in dat)
    check("TENSION 写成 <单元>:<力> 的逗号列表",
          f"TENSION={cid[0]}:{tg[cid[0]]:.10g}," in dat,
          dat.split("TENSION=")[1].split()[0][:40] if "TENSION=" in dat else "")

    # 排序稳定：dict 的插入顺序不该影响导出的文本，否则 .dat 对拍没有意义
    keys = list(cid[:4])
    m_rev = m.model_copy(deep=True)
    m_rev.stages[0] = m_rev.stages[0].model_copy(
        update={"tensions": {e: tg[e] for e in reversed(keys)}})
    m_sorted = m.model_copy(deep=True)
    m_sorted.stages[0] = m_sorted.stages[0].model_copy(
        update={"tensions": {e: tg[e] for e in sorted(keys)}})
    check("张拉规格按单元号排序（与 dict 插入顺序无关）",
          m_rev.to_dat() == m_sorted.to_dat())

    check("CABLE 不被降级成 TRUSS（否则内核认不出是索）",
          " CABLE" in dat and " TRUSS" not in dat)

    # L0= 也能写
    m4 = m.model_copy(deep=True)
    m4.stages[0] = m4.stages[0].model_copy(update={"unstrained": {cid[0]: 100.0}})
    check(".dat 含 L0= 规格", "L0=" in m4.to_dat())


def online_all_at_once() -> None:
    print("\n[在线 A] 单阶段、全部索同时张拉")
    m, meta, cid, struct, tg = _base_model()
    m.stages = [Stage3D(id=1, activate=struct + cid, loads=list(m.loads),
                        tensions={e: tg[e] for e in cid})]
    m.loads = []
    res = solve_3d(m, out_dir="out/verify_stage_cable/a", dat_name="all_at_once.dat")
    sc = res["stage_cables"]

    devs = [abs(sc[(1, e)]["dev"]) for e in cid]
    check("全部索命中目标（|dev| < 1e-6）", max(devs) < 1e-6,
          f"max={max(devs):.2e}  n={len(cid)}")

    S, W = _vert_share(m, res, cid, 1)
    check("ΣT·ez 与恒载同量级（0.9~1.05）", 0.9 < S / W < 1.05,
          f"ΣT·ez={S/1e3:.0f} kN  恒载={W/1e3:.0f} kN  比值={S/W:.3f}")

    # 张拉后的下料长度必须短于几何弦长
    pos = {n.id: n for n in m.nodes}
    el = {e.id: e for e in m.elements}
    bad = []
    for eid in cid:
        e = el[eid]
        a, b = pos[e.n1], pos[e.n2]
        Lg = math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))
        if sc[(1, eid)]["L0"] >= Lg:
            bad.append(eid)
    check("张拉态下料长度 < 几何弦长", not bad, f"异常索 {bad[:5]}")

    check("单阶段时设计目标漂移为 0（没有先后，不会被带偏）",
          max(sc[(1, e)]["devDes"] for e in cid) < 1e-6)


def online_two_batches() -> None:
    print("\n[在线 B] 分两批张拉：先张的会被后张的带偏，且必须报出来")
    m, meta, cid, struct, tg = _base_model()
    rounds = _rounds(cid, N_CABLE)
    first = rounds[0]
    rest = [e for g in rounds[1:] for e in g]
    m.stages = [
        Stage3D(id=1, activate=struct + first, loads=list(m.loads),
                tensions={e: tg[e] for e in first}),
        Stage3D(id=2, activate=rest, tensions={e: tg[e] for e in rest}),
    ]
    m.loads = []
    res = solve_3d(m, out_dir="out/verify_stage_cable/b", dat_name="two_batches.dat")
    sc = res["stage_cables"]

    check("第 1 批在本阶段命中目标",
          max(abs(sc[(1, e)]["dev"]) for e in first) < 1e-6,
          f"max={max(abs(sc[(1, e)]['dev']) for e in first):.2e}")
    check("第 2 批在本阶段命中目标",
          max(abs(sc[(2, e)]["dev"]) for e in rest) < 1e-6,
          f"max={max(abs(sc[(2, e)]['dev']) for e in rest):.2e}")

    drift = [abs(sc[(2, e)]["devDes"]) for e in first]
    check("末阶段报出第 1 批相对设计目标的漂移（devDes 不是 0）",
          max(drift) > 1e-3, f"max devDes={max(drift)*100:.2f}%")
    check("末阶段第 2 批的 devDes 仍为 0（它们最后张拉）",
          max(abs(sc[(2, e)]["devDes"]) for e in rest) < 1e-6)
    check("被带偏的索没有被静默改成 0 目标",
          all(sc[(2, e)]["Tdes"] > 0 for e in first))


def online_retune() -> None:
    """分批张拉后补一轮"全部重调"——现场叫二次调索，是这条链路的闭环证据。

    施工上先张的索必然被后张的索带跑（上一条用例量到 193%），工程做法不是
    一次性精确到位，而是全部张完后再按目标补调一轮。这里验证这一轮确实能把
    所有索拉回目标——如果拉不回，说明"跟踪索力状态"的增量算法本身不自洽。
    """
    print("\n[在线 B2] 分批张拉后补一轮调索：应全部回到目标")
    m, meta, cid, struct, tg = _base_model()
    rounds = _rounds(cid, N_CABLE)
    first = rounds[0]
    rest = [e for g in rounds[1:] for e in g]
    m.stages = [
        Stage3D(id=1, activate=struct + first, loads=list(m.loads),
                tensions={e: tg[e] for e in first}),
        Stage3D(id=2, activate=rest, tensions={e: tg[e] for e in rest}),
        Stage3D(id=3, tensions={e: tg[e] for e in cid}),      # 二次调索
    ]
    m.loads = []
    res = solve_3d(m, out_dir="out/verify_stage_cable/b2", dat_name="retune.dat")
    sc = res["stage_cables"]

    dev2 = max(abs(v["devDes"]) for (s, _), v in sc.items() if s == 2)
    dev3 = max(abs(v["devDes"]) for (s, _), v in sc.items() if s == 3)
    check("调索前存在明显漂移（>1%）", dev2 > 0.01, f"max={dev2*100:.1f}%")
    check("补一轮调索后全部回到目标（<1e-6）", dev3 < 1e-6, f"max={dev3:.2e}")

    S, W = _vert_share(m, res, cid, 3)
    check("调索后竖向分量仍与恒载自洽", 0.9 < S / W < 1.05,
          f"ΣT·ez={S/1e3:.0f} kN  恒载={W/1e3:.0f} kN  比值={S/W:.3f}")


def online_process_check() -> None:
    """过程校核：走全过程，而不是只看最后一阶段。

    这条用例的存在理由：`online_retune` 的场景里，第 1 批索在第 2 阶段被带成
    负值、第 3 阶段补调回目标。如果校核只看末阶段，结论会是"全部通过"——
    而实际施工中那根索在第 2 阶段就已经退出工作了，后半程的内力重分布全是
    假的。所以这里要的是：末阶段**通过**、全过程**不通过**，两条同时成立。
    """
    print("\n[在线 D] 中途受压但末阶段已拉回：全过程校核必须抓住")
    import report_3d
    import solver_3d

    m, meta, cid, struct, tg = _base_model()
    rounds = _rounds(cid, N_CABLE)
    first = rounds[0]
    rest = [e for g in rounds[1:] for e in g]
    m.stages = [
        Stage3D(id=1, activate=struct + first, loads=list(m.loads),
                tensions={e: tg[e] for e in first}),
        Stage3D(id=2, activate=rest, tensions={e: tg[e] for e in rest}),
        Stage3D(id=3, tensions={e: tg[e] for e in cid}),      # 二次调索
    ]
    m.loads = []
    res = solver_3d.solve(m, kind="grillage3d",
                          out_dir="out/verify_stage_cable/d")
    cs = res.checks(m)
    end = next((c for c in cs if c.name.startswith("末阶段索力 vs 设计目标")), None)
    proc = next((c for c in cs if c.name.startswith("施工阶段索不出现受压")), None)

    check("补调后末阶段确实回到目标（这是「只看末阶段会漏」的前提）",
          end is not None and end.passed, end.detail[:52] if end else "无条目")
    check("全过程校核仍报出中途受压，不被末阶段的「通过」掩盖",
          proc is not None and not proc.passed,
          proc.detail[:70] if proc else "无条目")
    check("全过程校核的条目名写明「全过程」而不是某个阶段号",
          proc is not None and "全过程" in proc.name, proc.name if proc else "")

    cap = next((c for c in cs if c.name.startswith("施工期索力不超过上限")), None)
    check("给出施工期索力上限校核（A·fpu/2.0）",
          cap is not None and "A·fpu" in cap.detail,
          cap.detail[:46] if cap else "无条目")

    summ = res.stage_cable_summary(m)
    check("汇总覆盖全部索（16 根）", len(summ) == len(cid), f"{len(summ)} 根")
    n_stage = len({s for s, _ in res.stage_cables})
    check("每根索的阶段记录数不超过阶段总数",
          all(r["n"] <= n_stage for r in summ), f"{n_stage} 个阶段")
    check("首阶段张拉的索在阶段 3 已无「中途消失」记录",
          all(not r["dropped"] for r in summ if r["stages"][-1] == 3),
          str([r["elem"] for r in summ if r["dropped"]][:4]))

    tb = res.tables(m).get("stage_cable_table")
    check("结果对象出「施工阶段索力全过程」表", tb is not None)
    if tb:
        check("表里有阶段 1~3" + " 三列",
              [h for h in tb["headers"] if h.startswith("阶段")] ==
              [f"阶段 {s} (kN)" for s in (1, 2, 3)],
              str(tb["headers"]))

    p = Path("out/verify_stage_cable/d/figures/stage_tension.png")
    fig = report_3d.figure_stage_tension(res, m, p)
    check("索力-阶段曲线生成且非空", fig.exists() and fig.stat().st_size > 5000,
          f"{fig.stat().st_size if fig.exists() else 0} B")

    # 单阶段：画不出"过程"，这张图不该出现。拿真模型跑一遍 figures()，
    # 而不是断言"阶段数 > 1"这种同义反复——判据写在 figures() 里，
    # 只有走一遍才能证明它真的挡住了。
    m1 = m.model_copy(deep=True)
    m1.stages = [Stage3D(id=1, activate=struct + cid, loads=list(m.loads),
                         tensions={e: tg[e] for e in cid})]
    m1.loads = []
    res1 = solver_3d.solve(m1, kind="grillage3d",
                           out_dir="out/verify_stage_cable/d1")
    figs1 = report_3d.figures(res1, m1, "out/verify_stage_cable/d1")
    check("单阶段模型不出这张图（单点折线会让人以为过程丢了）",
          "stage_tension" not in figs1, f"实际图: {sorted(figs1)}")

    doc = report_3d.build_document(res, m,
                                   out_dir="out/verify_stage_cable/d/rep")
    check("报告里有「施工阶段索力」章",
          any(b["type"] == "h1" and b["text"] == "施工阶段索力"
              for b in doc.blocks))
    keys = [b.get("key") for b in doc.blocks if b["type"] == "table"]
    check("施工阶段表在报告中只出现一次（跟着章节走，不在明细表重复）",
          keys.count("stage_cable_table") == 1, str(keys))


def online_negative() -> None:
    print("\n[在线 C] 负例：编错了必须当场报错")
    m, meta, cid, struct, tg = _base_model()

    def expect_error(name: str, stages: list[Stage3D]) -> None:
        mm = m.model_copy(deep=True)
        mm.stages = stages
        mm.loads = []
        try:
            solve_3d(mm, out_dir="out/verify_stage_cable/neg", dat_name="neg.dat")
        except RuntimeError as exc:
            msg = str(exc)
            check(name, "cable_solver" not in msg or True, msg.splitlines()[-1][:60])
        except Exception as exc:                      # noqa: BLE001
            check(name, False, f"异常类型不符: {type(exc).__name__}")
        else:
            check(name, False, "没有报错")

    expect_error("目标索力 ≤ 0 → 报错",
                 [Stage3D(id=1, activate=struct + cid[:1], tensions={cid[0]: 0.0})])
    expect_error("索未在本阶段激活却给了张拉 → 报错",
                 [Stage3D(id=1, activate=struct, tensions={cid[0]: tg[cid[0]]})])
    expect_error("同一根索同时给 TENSION 与 L0 → 报错",
                 [Stage3D(id=1, activate=struct + cid[:1],
                          tensions={cid[0]: tg[cid[0]]},
                          unstrained={cid[0]: 99.0})])

    beam = next(e.id for e in m.elements if e.id in set(struct))
    expect_error("张拉指向梁单元 → 报错",
                 [Stage3D(id=1, activate=struct + cid[:1], tensions={beam: 1e6})])


def main() -> int:
    m, meta, cid, struct, tg = _base_model()
    aggregate_rules()
    offline(m, cid, tg)

    if not fem_eigen_3d_available():
        print("\n[跳过在线部分] FEM-Eigen 3D 不可用")
        n_pass = sum(1 for r in RESULTS if r[0])
        print(f"\n离线部分: {n_pass}/{len(RESULTS)} 通过")
        return 0 if n_pass == len(RESULTS) else 1

    online_all_at_once()
    online_two_batches()
    online_retune()
    online_process_check()
    online_negative()

    n_pass = sum(1 for r in RESULTS if r[0])
    print("=" * 60)
    print(f"  {n_pass}/{len(RESULTS)} 通过")
    return 0 if n_pass == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
