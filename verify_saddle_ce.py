"""verify_saddle_ce.py
=====================
索鞍顶推 CE 耦合（口径 C）的离线守卫，对应《索鞍顶推CE耦合规格.md》验收口径。

锁住五件事：
1. 默认零污染：suspension_3d 不加顶推量时 ces 为空、.dat 无 *CE 段、
   默认 .dat 12 格黄金哈希不刷新；
2. 顶推模型结构：每塔一条 CE1(顶推)+CE2(缆鞍固结)+4 条固结 CE，
   constant 符号/量值正确，引用节点存在，validate 无 error；
3. .dat 输出：*CE_PARAMETER + 12 条 *CE，dof 1~6、格式正确；
4. 自然语言识别：「索鞍顶推 50mm」→ saddle_jack_dx=0.05，Delta>0 才触发；
5. 独立包一致性：BridgeStudio-App/app 下核心 .py 逐字节一致（若目录存在）；
6. 分级顶推 stage：jack_stages 差分为 δ_i、每阶段覆盖两塔顶推 CE、拒绝路径
   与默认零污染，自然语言「分级顶推」识别。

纯离线可回归：不需要 ANSYS/网络。内核端到端试算因需成桥态找形+非线性，
交给 FEM-Eigen 侧 CTest（第 39 项）覆盖，本守卫只校验模型契约。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from model_doc import BUILDERS, DEFAULTS, ModelDoc, extract_3d_params
from model_spec_3d import validate_model_3d
from suspension_3d import build_suspension_3d
from verify_stage_tension_3d import DEFAULT_DAT_SHA

ROOT = Path(__file__).resolve().parent
APP_ROOT = ROOT.parent / "BridgeStudio-App"
RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((bool(ok), name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def close(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def sha12(dat: str) -> str:
    return hashlib.sha256(dat.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- [1] 默认零污染
def group_default() -> None:
    print("\n[1] 默认零污染")
    m, meta = build_suspension_3d(system="EARTH", main_span=300.0, title="ce_default")
    check("默认无 CE：ces 为空", len(m.ces) == 0)
    check("默认无 CE：meta n_ce=0", meta["n_ce"] == 0)
    dat = m.to_dat()
    check("默认 .dat 无 *CE_PARAMETER", "*CE_PARAMETER" not in dat)
    check("默认 .dat 无 *CE 段", "\n*CE\n" not in dat)
    # 全部 12 类默认 .dat 16 格哈希逐位不变（CE 字段对默认模型零污染）
    all_ok = True
    for (kind, dim), b in BUILDERS.items():
        dat = ModelDoc(kind, dict(DEFAULTS[b]), dimension=dim).to_dat()
        if sha12(dat) != DEFAULT_DAT_SHA[b]:
            all_ok = False
            check(f"默认哈希 {b} 不刷新", False, f"{sha12(dat)} / {DEFAULT_DAT_SHA[b]}")
    check("12 类默认 .dat 哈希逐位不变", all_ok)
    errs, _ = validate_model_3d(m)
    check("默认模型校验无 error", not errs, str(errs[:3]))


# ---------------------------------------------------------------- [2] 顶推结构
def group_structure() -> None:
    print("\n[2] 顶推模型结构（口径 C）")
    Delta = 0.05
    m, meta = build_suspension_3d(system="EARTH", main_span=300.0,
                                  saddle_jack_dx=Delta, title="ce_jack")
    check("顶推触发：n_ce=12（两塔各 6 条）", meta["n_ce"] == 12 and len(m.ces) == 12,
          f"n_ce={meta['n_ce']} len={len(m.ces)}")
    check("索鞍/塔顶节点各 2 个", len(meta["saddle_nodes"]) == 2
          and len(meta["saddle_tower_top_nodes"]) == 2)
    check("saddle_jack_on=True", meta["saddle_jack_on"] is True)

    # 编号 1..12 唯一
    ids = [ce.id for ce in m.ces]
    check("CE 编号 1..12 唯一", sorted(ids) == list(range(1, 13)), str(ids))

    # 每塔两条顶推 CE 的 constant=Delta，其余为 0
    consts = [ce.constant for ce in m.ces]
    n_delta = sum(1 for c in consts if close(c, Delta))
    n_zero = sum(1 for c in consts if close(c, 0.0))
    check("两条顶推 CE constant=Δ，其余 10 条=0", n_delta == 2 and n_zero == 10,
          f"delta={n_delta} zero={n_zero}")

    # CE1 结构：S 系数 +1、T 系数 -1、dof=1
    ce1 = m.ces[0]
    ok1 = (len(ce1.terms) == 2
           and any(t.dof == 1 and close(t.coef, 1.0) for t in ce1.terms)
           and any(t.dof == 1 and close(t.coef, -1.0) for t in ce1.terms))
    check("CE1=UX(S)−UX(T)=Δ 结构正确", ok1)
    # CE2 结构：C 系数 +1、S 系数 -1
    ce2 = m.ces[1]
    ok2 = (len(ce2.terms) == 2
           and any(t.dof == 1 and close(t.coef, 1.0) for t in ce2.terms)
           and any(t.dof == 1 and close(t.coef, -1.0) for t in ce2.terms))
    check("CE2=UX(C)−UX(S)=0 结构正确", ok2)
    # 固结 CE：每塔后 4 条（索引 2~5、8~11）均为 dof 2/3；
    # 顶推/缆鞍 CE（索引 0,1,6,7）才是 dof 1。
    fix_idx = [2, 3, 4, 5, 8, 9, 10, 11]
    okc = all(all(t.dof in (2, 3) for t in m.ces[i].terms) for i in fix_idx)
    check("固结 CE 均为 UY/UZ（dof 2/3）", okc)
    push_idx = [0, 1, 6, 7]
    okp = all(all(t.dof == 1 for t in m.ces[i].terms) for i in push_idx)
    check("顶推/缆鞍 CE 均为 UX（dof 1）", okp)

    # 引用节点都存在
    node_ids = {n.id for n in m.nodes}
    check("所有 CE 引用节点存在",
          all(t.node in node_ids for ce in m.ces for t in ce.terms))
    errs, _ = validate_model_3d(m)
    check("顶推模型校验无 error", not errs, str(errs[:5]))


# ---------------------------------------------------------------- [3] .dat 输出
def group_dat() -> None:
    print("\n[3] .dat 输出")
    m, _ = build_suspension_3d(system="EARTH", main_span=300.0,
                               saddle_jack_dx=0.05, title="ce_dat")
    dat = m.to_dat()
    check("含 *CE_PARAMETER", "*CE_PARAMETER" in dat)
    check("含 PENALTY=1e12", "PENALTY=1e+12" in dat or "PENALTY=1e12" in dat)
    check("*CE 段数=12", dat.count("\n*CE\n") == 12)
    # 段位于最后一个 *END（精确换行匹配，避开 *ENDDO）之前
    last_end = dat.rfind("\n*END")
    check("CE 段在 *END 之前",
          dat.find("*CE_PARAMETER") < last_end)
    # 每条 CE 行格式：id b node dof coef ...（偶数个后续字段 = 3×项数）
    block = dat[dat.find("*CE_PARAMETER"):last_end]
    lines = [ln for ln in block.splitlines() if ln and not ln.startswith("*")
             and not ln.startswith("PENALTY")]
    okfmt = all(len(ln.split()) >= 7 and (len(ln.split()) - 2) % 3 == 0
                for ln in lines)
    check("CE 行格式 <id> <b> <n> <dof> <coef>... 正确", okfmt,
          f"n_lines={len(lines)}")


# ---------------------------------------------------------------- [4] 自然语言
def group_nl() -> None:
    print("\n[4] 自然语言识别")
    cases = [
        ("地锚式悬索桥 主跨 300m 索鞍顶推 50mm", 0.05),
        ("悬索桥 主跨 300m 索鞍顶推量 5cm", 0.05),
        ("悬索桥 主跨 300m 索鞍顶推 0.05m", 0.05),
    ]
    for text, expect in cases:
        p, notes = extract_3d_params(text, "suspension")
        got = p.get("saddle_jack_dx")
        check(f"识别「{text.split('索鞍')[1]}」→ {expect} m",
              got is not None and close(got, expect), f"got={got}")

    # 无顶推词 → 不触发
    p, _ = extract_3d_params("地锚式悬索桥 主跨 300m 矢跨比 1/10", "suspension")
    check("无「索鞍顶推」不生成 saddle_jack_dx",
          "saddle_jack_dx" not in p or p["saddle_jack_dx"] == 0.0)
    # 只有「顶推施工」没有索鞍 → 不触发
    p, _ = extract_3d_params("悬索桥 主跨 300m 顶推施工法", "suspension")
    check("仅「顶推」不触发（须带索鞍）",
          "saddle_jack_dx" not in p or p["saddle_jack_dx"] == 0.0)


# ---------------------------------------------------------------- [5] 独立包
# ------------------------------------------------------------ [6] 分级顶推
def group_stage_jack() -> None:
    print("\n[6] 分级顶推 stage（opt-in）")
    m, meta = build_suspension_3d(
        system="EARTH", main_span=300.0, panel=30.0, n_tower_seg=3,
        jack_stages=[0.01, 0.02, 0.03], title="ce_stage")
    dat = m.to_dat()
    errs, _ = validate_model_3d(m)

    check("jack_stages 生成 3 个 stage", len(m.stages) == 3,
          f"got={len(m.stages)}")
    check("stage 差分 δ 口径：Δ=[.01,.02,.03] → δ=[.01,.01,.01]",
          all(close(d, 0.01) for d in meta["jack_stage_deltas"]),
          f"got={meta['jack_stage_deltas']}")
    check("每 stage 覆盖两塔顶推 CE（id=1,7）",
          all(set(s.ce_constants) == {1, 7} for s in m.stages))
    check("首个 stage 激活全部单元，后续不增删",
          bool(m.stages[0].activate) and all(not s.activate and not s.deactivate
                                             for s in m.stages[1:]))
    check("stage CE= 按 id 排序输出（可复现）",
          dat.count("CE=1:0.01,7:0.01") == 3)
    check("分级顶推模型 validate 无 error", not errs, f"n={len(errs)}")

    # 拒绝路径：非递增 / 负值 / 少于两级 / 与 saddle_jack_dx 不一致
    for bad, tag in [
        ([0.02, 0.01], "非递增"),
        ([0.01, -0.01], "负值"),
        ([0.01], "少于两级"),
    ]:
        threw = False
        try:
            build_suspension_3d(main_span=300.0, jack_stages=bad)
        except ValueError:
            threw = True
        check(f"jack_stages {tag} 被拒绝", threw)
    threw = False
    try:
        build_suspension_3d(main_span=300.0, saddle_jack_dx=0.05,
                            jack_stages=[0.01, 0.02])
    except ValueError:
        threw = True
    check("saddle_jack_dx 与 jack_stages 末值不一致被拒绝", threw)

    # 默认零污染：不传 jack_stages 时无 stage、.dat 无 *STAGE
    m0, _ = build_suspension_3d(main_span=300.0, panel=30.0, n_tower_seg=3)
    check("无 jack_stages → 无 stage", not m0.stages)
    check("无 jack_stages → .dat 无 *STAGE 段", "*STAGE" not in m0.to_dat())

    # 自然语言 → jack_stages
    p, _ = extract_3d_params("地锚式悬索桥 主跨 300m 分级顶推 10、20、30 mm",
                             "suspension")
    js = p.get("jack_stages")
    check("自然语言「分级顶推 10、20、30 mm」→ [0.01,0.02,0.03]",
          js is not None and len(js) == 3 and all(close(a, b) for a, b in
          zip(js, [0.01, 0.02, 0.03])), f"got={js}")


def group_app() -> None:
    print("\n[5] 独立包一致性")
    if not APP_ROOT.exists():
        print("  SKIP  独立包目录不存在")
        return
    for name in ("suspension_3d.py", "model_doc.py", "model_spec_3d.py"):
        src = ROOT / name
        dst = APP_ROOT / "app" / name
        ok = dst.exists() and src.read_bytes() == dst.read_bytes()
        check(f"独立包 {name} 逐字节一致", ok,
              "" if ok else ("" if dst.exists() else "缺失"))


def main() -> int:
    group_default()
    group_structure()
    group_dat()
    group_nl()
    group_stage_jack()
    group_app()
    passed = sum(1 for ok, _, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n==== {passed}/{total} 通过 ====")
    for ok, name, detail in RESULTS:
        if not ok:
            print(f"  FAIL  {name}  {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
