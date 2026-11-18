"""verify_dual_plane_3d.py
=========================
双索面 3D opt-in 的离线守卫。

为什么单独一套
--------------
双索面是 2026-11-18 新增的 **opt-in 几何开关**，默认单索面必须逐字节不变，
且"仅双索面"不能触发 3D。这套守卫锁住四件事：

1. 解析口径：仅写"双索面"仍是 2D、不建第二索面；三维 + 双索面才镜像；
2. 几何契约：两索面落在 y=±B/2、横梁连接左右、荷载分半总量守恒、
   横梁随主梁段逐根激活、悬索桥 *.dat 追加平面段；
3. 报告口径：报告层从网格几何判定索面，双索面正面写明，不再无条件
   声称"单索面平面模型"；
4. 端到端：三桥型（斜拉 / 悬索 / 拱）双索面都能求解，双主缆 H 一致。

默认零污染锚点：12 格 DEFAULT_DAT_SHA 必须原样通过（不刷新）。

纯离线可回归：不需要网络；端到端部分需要 cable_solver.exe，缺失时明确失败。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fem_eigen_cable import cable_solver_available
from model_doc import BUILDERS, DEFAULTS, ModelDoc
from report_3d import _cover_facts
from result_3d import cable_plane_count
from solver_3d import solve
from verify_stage_tension_3d import DEFAULT_DAT_SHA

OUT = Path("out/verify_dual_plane")
RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((bool(ok), name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def _text(name: str) -> str:
    return Path("examples", name).read_text(encoding="utf-8").strip()


CS = "三维空间双索面斜拉桥。" + _text("cable_stayed_400m.txt")
SUSP = "三维空间双索面悬索桥。" + _text("suspension_1000m.txt")
ARCH = "三维空间双索面拱桥。" + _text("arch_500m.txt")
SINGLE_CS = _text("cable_stayed_400m.txt")


def _doc(txt: str) -> ModelDoc:
    doc, _, _ = ModelDoc.from_text(txt)
    assert not doc.validate(), f"模型校验未通过：{doc.validate()}"
    return doc


def _elem_kinds(m) -> dict[int, str]:
    return {t.id: str(getattr(t, "kind", "")) for t in m.etypes}


def _n_cable(m) -> int:
    kinds = _elem_kinds(m)
    return sum(1 for e in m.elements if kinds.get(e.etype) == "CABLE")


def _node_load_sum(m) -> float:
    return sum(float(ld.value) for ld in m.loads)


# ---------------------------------------------------------------- [1] 解析层

def stage_parse() -> None:
    print("\n[1] 解析层：双索面是 opt-in 几何开关，不是维度开关")
    only = _doc(SINGLE_CS + "，双索面")
    check("仅写双索面不触发 3D", only.dimension == "2d", only.dimension)
    check("仅双索面不建第二索面（几何仍单面）",
          cable_plane_count(only.model) == 1)

    for tag, txt in (("斜拉", CS), ("悬索", SUSP), ("拱", ARCH)):
        doc = _doc(txt)
        check(f"{tag}：三维 + 双索面 → 3D",
              doc.dimension == "3d", doc.dimension)
        check(f"{tag}：cable_planes=2 且几何判为双索面",
              int(doc.params.get("cable_planes") or 0) == 2
              and cable_plane_count(doc.model) == 2)


# ---------------------------------------------------------------- [2] 几何

def stage_geometry() -> None:
    print("\n[2] 镜像几何：y=±B/2 / 横梁 / 荷载分半 / 阶段激活")
    for tag, txt in (("斜拉", CS), ("悬索", SUSP), ("拱", ARCH)):
        doc = _doc(txt)
        m, info = doc.model, doc.info
        B = float(info["bridge_width"])
        ys = sorted({round(float(n.y), 6) for n in m.nodes})
        check(f"[{tag}] 两片索面 y=±B/2",
              len(ys) == 2 and abs(ys[0] + B / 2) < 1e-6
              and abs(ys[1] - B / 2) < 1e-6,
              f"y={ys}  B={B:g}")

        cross = list(info["cross_beam_ids"])
        nd = {n.id: n for n in m.nodes}
        ok_cross = bool(cross) and all(
            abs(nd[e.n1].y + nd[e.n2].y) < 1e-6
            and abs(abs(nd[e.n1].y) - B / 2) < 1e-6
            for e in m.elements if e.id in set(cross))
        check(f"[{tag}] 横梁逐根连接左右索面（{len(cross)} 根）", ok_cross)

        single = _doc("三维空间斜拉桥。" + SINGLE_CS) if tag == "斜拉" else None
        if single is not None:
            check("[斜拉] 节点/单元数量 = 双倍 + 横梁",
                  len(m.nodes) == 2 * len(single.model.nodes)
                  and len(m.elements) == 2 * len(single.model.elements) + len(cross),
                  f"{len(m.nodes)}/{len(m.elements)} vs "
                  f"{len(single.model.nodes)}/{len(single.model.elements)}+{len(cross)}")
            check("[斜拉] 索单元数量翻倍、荷载总量守恒",
                  _n_cable(m) == 2 * _n_cable(single.model)
                  and abs(_node_load_sum(m) - _node_load_sum(single.model))
                  < 1e-6 * max(1.0, abs(_node_load_sum(single.model))),
                  f"cables {_n_cable(m)} / {_n_cable(single.model)}")

        if m.stages:
            cross_set = set(cross)
            first: dict[int, int] = {}
            for st in m.stages:
                for eid in st.activate:
                    if eid in cross_set:
                        first.setdefault(eid, st.id)
            check(f"[{tag}] 横梁随主梁段逐根激活（非首阶段一次性全激活）",
                  len(set(first.values())) > 1,
                  f"横梁分布在 {len(set(first.values()))} 个阶段")

    # 悬索附加段：*.dat 显式登记第二索面
    dat = _doc(SUSP).to_dat()
    for kw in ("*SUSP_BRIDGE_PLANE_2", "*MAINCABLE_2", "*HANGER_2"):
        check(f"[悬索] .dat 含 {kw}", kw in dat)
    cb = _doc(SUSP).model.cable_bridge
    check("[悬索] 附加平面登记完整",
          len(cb.planes) == 1
          and len(cb.planes[0].main_cable) == len(cb.main_cable)
          and len(cb.planes[0].hangers) > 0,
          f"plane 数 {len(cb.planes)}")


# ---------------------------------------------------------------- [3] 报告层

def stage_report() -> None:
    print("\n[3] 报告层：从网格读体系，双索面正面写明")
    dual = _doc(CS).model
    single = _doc(SINGLE_CS).model
    check("cable_plane_count 判定双/单索面",
          cable_plane_count(dual) == 2 and cable_plane_count(single) == 1)
    facts = dict(_cover_facts(dual))
    check("_cover_facts 输出双索面说明",
          "双索面" in facts.get("索面", ""), facts.get("索面", ""))
    src = Path("report_docx.py").read_text(encoding="utf-8")
    check("Word 报告两处 collect 均登记 cable_planes",
          src.count('"cable_planes": cable_plane_count(model)') >= 2)
    check("Word 封面按索面分支", 'data.get("cable_planes")' in src)
    n_branch = src.count('int(d.get("cable_planes") or 1) == 2')
    check("正文多处按索面分支（非无条件单索面）",
          n_branch >= 4, f"分支数 {n_branch}")


# ---------------------------------------------------------------- [4] 默认零污染

def stage_baseline() -> None:
    print("\n[4] 默认零污染：12 格 DEFAULT_DAT_SHA 不刷新")
    import hashlib as _h
    for (kind, dim), b in BUILDERS.items():
        dat = ModelDoc(kind, dict(DEFAULTS[b]), dimension=dim).to_dat()
        h = _h.sha256(dat.encode()).hexdigest()[:16]
        check(f"默认 .dat 黄金哈希：{b}", h == DEFAULT_DAT_SHA[b],
              f"{h} / {DEFAULT_DAT_SHA[b]}")


# ---------------------------------------------------------------- [5] 端到端

def stage_solve() -> None:
    print("\n[5] 端到端：三桥型双索面求解")
    try:
        r_cs = solve(_doc(CS).model, out_dir=OUT / "cs", dat_name="dual_cs.dat")
        r_su = solve(_doc(SUSP).model, out_dir=OUT / "susp", dat_name="dual_susp.dat")
        r_ar = solve(_doc(ARCH).model, out_dir=OUT / "arch", dat_name="dual_arch.dat")
    except Exception as e:  # noqa: BLE001 - 守卫要显示失败原因
        check("三桥型双索面求解", False, str(e).splitlines()[-1][:200])
        return

    check("[斜拉] 双索面收敛", r_cs.converged)
    md = r_su.meta
    check("[悬索] 双主缆收敛", r_su.converged)
    check("[悬索] susp_plane_count=2", int(md.get("susp_plane_count") or 0) == 2)
    h1 = float(md.get("plane_1_H_kN") or 0.0)
    h2 = float(md.get("plane_2_H_kN") or 0.0)
    check("[悬索] 两平面 H 一致", h1 > 0 and abs(h1 - h2) / h1 < 1e-6,
          f"H1={h1:.1f} H2={h2:.1f} kN")
    expected = 2 * (int(md.get("main_cable_segments") or 0)
                    + int(md.get("hanger_count") or 0))
    check("[悬索] 全部索登记（两平面）",
          int(md.get("cables") or 0) == expected,
          f"cables={md.get('cables')} 期望 {expected}")
    check("[拱] 双拱肋收敛（横梁逐根激活未致奇异）", r_ar.converged)


def main() -> int:
    print("=" * 70)
    print("  双索面 3D opt-in 回归（BridgeStudio ↔ FEM-Eigen）")
    print("=" * 70)
    stage_parse()
    stage_geometry()
    stage_report()
    stage_baseline()
    if cable_solver_available():
        stage_solve()
    else:
        check("cable_solver.exe 可用", False, "无法做端到端回归")

    n = sum(1 for ok, _, _ in RESULTS if ok)
    print("\n" + "=" * 70)
    print(f"  {n}/{len(RESULTS)} 通过")
    if n < len(RESULTS):
        for ok, name, detail in RESULTS:
            if not ok:
                print(f"    · {name}  {detail}")
    return 0 if n == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
