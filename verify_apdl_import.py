"""verify_apdl_import.py
=======================
APDL 只读导入（a+b）的离线守卫，对应 ``APDL导入规格.md`` §5 的 12 项验收。

锁住四件事：

1. **可逆**：`spec3d_to_apdl` 的 BEAM4 / BEAM188 两份命令流回读后，节点、
   单元、材料、实常数、边界、荷载逐字段一致；
2. **有损必告警**：BEAM188→BEAM、LINK180→TRUSS、面荷载方向假定都进报告；
3. **不可逆必拒绝**：`BEAM3`、两坐标 `N`、`*DO`、未知建模命令都进 rejected，
   且 `import_apdl_to_dat()` 显式抛错；
4. **默认零污染**：12 格默认 `.dat` 黄金哈希不刷新，导出器签名未动。

纯离线可回归：不需要 ANSYS、不需要网络。端到端试算仅在
`example_3d_grillage.exe` 可用时执行，否则打印 SKIP 而不判 FAIL。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from apdl_io import (
    ApdlImportError,
    import_apdl_to_dat,
    parse_apdl,
    read_apdl,
)
from ansys_bridge_3d import build_box_beam_3d, spec3d_to_apdl
from model_doc import BUILDERS, DEFAULTS, ModelDoc
from verify_stage_tension_3d import DEFAULT_DAT_SHA

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out" / "verify_apdl_import"
RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((bool(ok), name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def close(a: float, b: float, tol: float = 1e-8) -> bool:
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


# ---------------------------------------------------------------- [1] arch
def group_arch() -> None:
    print("\n[1] arch.ans（write_ansys_file 生成）回读")
    r = read_apdl(str(ROOT / "examples" / "arch.ans"))
    check("arch.ans 无显式拒绝", r.report.ok,
          "; ".join(r.report.rejected[:2]))
    m = r.model
    kinds = [(t.id, t.kind, t.real) for t in m.etypes]
    check("4 个 etype = 2 BEAM4 + 2 LINK180",
          len(m.etypes) == 4
          and [k for _, k, _ in kinds] == ["BEAM", "BEAM", "TRUSS", "TRUSS"],
          str(kinds))
    check("4 份实常数（梁/索各 2）", len(m.reals) == 4, str(len(m.reals)))
    check("FILL 补全节点共 36 个", len(m.nodes) == 36, str(len(m.nodes)))

    nodes = {n.id: n for n in m.nodes}
    check("FILL 插值节点 1001 坐标", nodes[1001].x == 31.25
          and nodes[1001].y == 0.0 and nodes[1001].z == 0.0,
          str((nodes[1001].x, nodes[1001].y, nodes[1001].z)))
    check("FILL 插值节点 1015 坐标", nodes[1015].x == 468.75,
          str(nodes[1015].x))
    check("显式端点 1000/1016 保留",
          nodes[1000].x == 0.0 and nodes[1016].x == 500.0)
    check("59 个单元 / 34 条节点荷载",
          len(m.elements) == 59 and len(m.loads) == 34,
          f"elems={len(m.elements)} loads={len(m.loads)}")
    check("LINK180 告警存在",
          any("LINK180" in w for w in r.report.warnings))


# ---------------------------------------------------------- [2][3] roundtrip
def group_roundtrip() -> None:
    src = build_box_beam_3d(n_elem_per_span=2)
    for element in ("BEAM4", "BEAM188"):
        print(f"\n[{'2' if element == 'BEAM4' else '3'}] "
              f"spec3d_to_apdl({element}) round-trip")
        txt = spec3d_to_apdl(src, jobname="verify", element=element)
        r = parse_apdl(txt, source=f"<{element}>")
        check(f"{element} 回读无拒绝", r.report.ok,
              "; ".join(r.report.rejected[:2]))
        m = r.model
        check(f"{element} 节点/单元/荷载/边界数量一致",
              len(m.nodes) == len(src.nodes)
              and len(m.elements) == len(src.elements)
              and len(m.loads) == len(src.loads)
              and len(m.boundaries) == len(src.boundaries),
              f"{len(m.nodes)}/{len(m.elements)}/{len(m.loads)}/{len(m.boundaries)}")

        a = {n.id: (n.x, n.y, n.z) for n in src.nodes}
        b = {n.id: (n.x, n.y, n.z) for n in m.nodes}
        check(f"{element} 节点坐标逐点一致",
              all(close(x, y) and close(u, v) and close(p, q)
                  for i, (x, u, p) in a.items()
                  for y, v, q in [b[i]]) if set(a) == set(b) else False)
        check(f"{element} 单元连接逐根一致",
              {(e.id, e.n1, e.n2) for e in m.elements}
              == {(e.id, e.n1, e.n2) for e in src.elements})
        check(f"{element} 荷载逐条一致",
              {(l.node, l.dof, l.value) for l in m.loads}
              == {(l.node, l.dof, l.value) for l in src.loads})
        check(f"{element} 边界逐条一致",
              {(x.node, x.dofs, x.value) for x in m.boundaries}
              == {(x.node, x.dofs, x.value) for x in src.boundaries})

        rc, ri = src.reals[0], m.reals[0]
        fields = (("A", rc.A, ri.A), ("IY", rc.IY, ri.IY),
                  ("IZ", rc.IZ, ri.IZ), ("J", rc.J, ri.J),
                  ("G", rc.G, ri.G))
        check(f"{element} 实常数 A/IY/IZ/J/G 一致",
              all(close(x, y) for _, x, y in fields),
              " ".join(f"{k}={x:.6g}/{y:.6g}" for k, x, y in fields))
        check(f"{element} 材料 E 一致",
              close(src.materials[0].E, m.materials[0].E))
        check(f"{element} etype.kind = BEAM",
              len(m.etypes) == 1 and m.etypes[0].kind == "BEAM")

        if element == "BEAM188":
            check("BEAM188 有 Timoshenko 告警",
                  any("Timoshenko" in w for w in r.report.warnings))
            check("KEYOPT 被忽略且告警",
                  r.report.ignored.get("KEYOPT") == 1
                  and any("KEYOPT" in w for w in r.report.warnings))


# ---------------------------------------------------------------- [LINK10]
def group_link10() -> None:
    print("\n[LINK10] BEAM4 + LINK10 混合读入（含水平只受拉杆）")
    text = """/PREP7
ET,1,BEAM4
ET,2,LINK10
MP,EX,1,2.06e11
MP,PRXY,1,0.3
R,1,1,1,1,1,1,0
RMORE,0,1,0,0,0,0
R,2,0.01,0
N,1,0,0,0
N,2,1,0,0
N,3,2,0,-1
N,4,3,0,-1
TYPE,1
MAT,1
REAL,1
E,1,2
TYPE,2
MAT,1
REAL,2
E,2,3
E,3,4
D,1,ALL,0
FINISH""".replace("R,2,0.01,0\n", "R,2,0.01,0.0005\n")
    r = parse_apdl(text, source="<BEAM4+LINK10>")
    check("BEAM4 + LINK10 混合流无拒绝", r.report.ok,
          "; ".join(r.report.rejected[:3]))
    m = r.model
    check("LINK10 映射为 TRUSS",
          [(t.id, t.kind) for t in m.etypes] == [(1, "BEAM"), (2, "TRUSS")],
          str([(t.id, t.kind) for t in m.etypes]))
    check("LINK10 单元斜杆与水平杆都读入",
          {(e.id, e.n1, e.n2, e.etype) for e in m.elements}
          == {(1, 1, 2, 1), (2, 2, 3, 2), (3, 3, 4, 2)},
          str([(e.id, e.n1, e.n2, e.etype) for e in m.elements]))
    check("LINK10 水平杆未被 CABLE 等高规则拒绝",
          any(e.n1 == 3 and e.n2 == 4 for e in m.elements))
    rc = next((x for x in m.reals if x.id == 2), None)
    check("LINK10 实常数 A / ISTRN 读入",
          rc is not None and close(rc.A, 0.01) and close(rc.ISTRN, 0.0005),
          f"A={rc.A:g} ISTRN={rc.ISTRN:g}" if rc else "无实常数 2")
    check("LINK10 tension-only 语义有告警",
          any("LINK10" in w and "只受拉" in w for w in r.report.warnings))
    # APDL 中 LINK10 用初应变表达索力 F=E·A·ISTRN；告警必须明确"索力不丢"，
    # 不再误导用户改 kind=CABLE，且逐根登记索力台账供核对。
    check("LINK10 告警明确索力不丢（F=E·A·ISTRN）",
          any("LINK10" in w and "ISTRN" in w and "不丢" in w
              for w in r.report.warnings))
    check("LINK10 告警不再误导改 kind=CABLE",
          not any("改 kind=CABLE" in w or "改 kind = CABLE" in w
                  for w in r.report.warnings))
    cfin = r.report.cable_forces
    check("LINK10 索力台账登记两根杆（斜杆+水平杆）",
          len(cfin) == 2, str(cfin))
    # E=2.06e11, A=0.01, ISTRN=0.0005 → F=2.06e11*0.01*0.0005=1.03e6 N
    _expF = 2.06e11 * 0.01 * 0.0005
    check("LINK10 索力数值 F=E·A·ISTRN 正确",
          all(close(v[3], _expF) and close(v[0], 0.01)
              and close(v[1], 0.0005) and close(v[2], 2.06e11)
              for v in cfin.values()), str(cfin))
    # 台账只统计非零 ISTRN 的杆；BEAM4（ET=1）不应进入。
    check("索力台账不含 BEAM 单元",
          all(next((t.kind for t in m.etypes if t.id == e.etype), None) == "TRUSS"
              for e in m.elements if e.id in cfin))


# ---------------------------------------------------------------- [4][5] 拒绝
def group_reject() -> None:
    print("\n[4][5] 显式拒绝边界")
    rb = read_apdl(str(ROOT / "examples" / "beam.ans"))
    check("BEAM3 进 rejected",
          any("BEAM3" in x for x in rb.report.rejected))
    check("两坐标 N 进 rejected",
          any("两坐标" in x for x in rb.report.rejected))
    try:
        import_apdl_to_dat(str(ROOT / "examples" / "beam.ans"))
        check("beam.ans 导入显式抛错", False, "未抛错")
    except ApdlImportError as exc:
        check("beam.ans 导入显式抛错", "BEAM3" in str(exc))

    rd = parse_apdl("""/PREP7
ET,1,BEAM4
MP,EX,1,2.06e11
R,1,1,1,1,1,1,0
RMORE,0,1,0,0,0,0
N,1,0,0,0
*DO,I,2,3
N,I,I,0,0
*ENDDO
TYPE,1
MAT,1
REAL,1
E,1,2
D,1,ALL,0
FINISH""")
    check("建模段 *DO 进 rejected",
          any("*DO" in x for x in rd.report.rejected),
          str(rd.report.rejected[:1]))

    ru = parse_apdl("""/PREP7
ET,1,SOLID45
ET,2,BEAM4
MP,EX,1,2.06e11
R,1,1,1,1,1,1,0
RMORE,0,1,0,0,0,0
N,1,0,0,0
N,2,1,0,0
TYPE,2
MAT,1
REAL,1
E,1,2
D,1,ALL,0
K,1,0,0,0
FINISH""")
    check("未知/不可映射单元进 rejected",
          any("SOLID45" in x for x in ru.report.rejected)
          and any("未知命令 K" in x for x in ru.report.rejected),
          str(ru.report.rejected[:2]))


# ---------------------------------------------------------------- [6][7][8][9]
def group_loads_bc() -> None:
    print("\n[6][7][8][9] 忽略段、约束合并、面荷载、重力")
    text = """/PREP7
ET,1,BEAM4
MP,EX,1,2.06e11
MP,PRXY,1,0.3
MP,ALPX,1,0
R,1,1,1,1,1,1,0
RMORE,0,1,0,0,0,0
N,1,0,0,0
N,2,1,0,0
TYPE,1
MAT,1
REAL,1
E,1,2
D,1,UX,0,UY,0
D,1,ROTX,0.1
D,2,ALL,0
F,2,FY,-1000,FZ,-2000
SFBEAM,1,1,PRES,-5000
ACE,0,0,-9.81
FINISH
/SOLU
ANTYPE,STATIC
SOLVE
SET,LAST
*DIM,X,ARRAY,2
*VWRITE,X(1)
FINISH""".replace("MP,ALPX,1,0\n", "")
    # 单独测 ALPX 告警（上例去掉是为了让主流程不被无关告警干扰）
    r = parse_apdl(text, source="<mixed>")
    check("混合命令流无拒绝", r.report.ok, str(r.report.rejected[:2]))
    m = r.model

    b = {(x.node, x.dofs, x.value) for x in m.boundaries}
    check("D 多 dof 对合并成 (1,2)", (1, (1, 2), 0.0) in b, str(sorted(b)))
    check("D 不同值独立成条", (1, (4, 4), 0.1) in b)
    check("D,ALL 展开成 (1,6)", (2, (1, 6), 0.0) in b)
    check("F 多分量对",
          {(l.node, l.dof, l.value) for l in m.loads}
          == {(2, 2, -1000.0), (2, 3, -2000.0)})
    check("SFBEAM → ElemLoad3D(dir=GZ)",
          len(m.elem_loads) == 1 and m.elem_loads[0].dir == "GZ"
          and m.elem_loads[0].q1 == -5000.0)
    check("SFBEAM 方向告警存在",
          any("面号" in w for w in r.report.warnings))
    check("ACE → gravity 三分量",
          m.gravity == (0.0, 0.0, -9.81), str(m.gravity))
    check("/SOLU 之后求解控制全忽略",
          all(r.report.ignored.get(k) for k in ("/SOLU", "SOLVE", "SET",
                                                 "*DIM", "*VWRITE")),
          str(r.report.ignored))

    ra = parse_apdl(text.replace("/PREP7", "/PREP7\nMP,ALPX,1,0.00001"))
    check("非零 ALPX 告警但未拒绝",
          ra.report.ok and any("ALPX" in w for w in ra.report.warnings))


# ---------------------------------------------------------------- [10] 试算
def group_solve(m) -> None:
    print("\n[10] 回读模型 → FEM-Eigen 3D 试算")
    try:
        from solver_3d import capabilities, solve
    except Exception as exc:                        # pragma: no cover
        print(f"  SKIP  求解器注册表不可用：{exc}")
        return
    if not capabilities().get("grillage3d"):
        print("  SKIP  example_3d_grillage.exe 不可用（离线守卫不判 FAIL）")
        return
    try:
        res = solve(m, kind="grillage3d", out_dir=str(OUT),
                    dat_name="apdl_import.dat")
        check("回读模型求解收敛", bool(res.converged),
              getattr(res, "stats", {}))
    except Exception as exc:
        check("回读模型求解收敛", False, str(exc))


# --------------------------------------------------------------- [11] 零污染
def group_baseline() -> None:
    print("\n[11] 默认零污染锚点")
    check("DEFAULT_DAT_SHA 仍是 12 项", len(DEFAULT_DAT_SHA) == 12,
          str(len(DEFAULT_DAT_SHA)))
    for (kind, dim), builder in BUILDERS.items():
        dat = ModelDoc(kind, dict(DEFAULTS[builder]), dimension=dim).to_dat()
        h16 = hashlib.sha256(dat.encode()).hexdigest()[:16]
        check(f"12 格默认 .dat 哈希：{builder}",
              h16 == DEFAULT_DAT_SHA[builder], h16)

    src = (ROOT / "export_formats.py").read_text(encoding="utf-8")
    check("export_formats.write_ansys_file 签名未变",
          "def write_ansys_file(model: dict[str, Any], path: str) -> None:"
          in src)
    src3 = (ROOT / "ansys_bridge_3d.py").read_text(encoding="utf-8")
    check("spec3d_to_apdl 导出函数仍在",
          "def spec3d_to_apdl(" in src3)
    srcb = (ROOT / "export_beam_apdl.py").read_text(encoding="utf-8")
    check("beam_spec_to_apdl 导出函数仍在",
          "def beam_spec_to_apdl(" in srcb)


# ---------------------------------------------------------------- [CE] 约束方程回读
def group_ce() -> None:
    print("\n[CE] APDL CE（约束方程）回读")
    # 用带完整实常数的 BEAM4 命令流，附加两条 CE：
    #   CE,301,0.05,3,UX,1,1,UX,-1   → UX(3)−UX(1)=0.05（顶推量）
    #   CE,302,0,2,UY,1,1,UY,-1      → UY(2)−UY(1)=0（固结）
    text = """/PREP7
/TITLE, CE guard
ET,1,BEAM4
MP,EX,1,2.1e11
MP,PRXY,1,0.3
R,1,0.01,1e-5,1e-5,1e-5
RMORE,0,2e-5
N,1,0,0,0
N,2,1,0,0
N,3,2,0,0
TYPE,1
MAT,1
REAL,1
E,1,2
E,2,3
D,1,UX,0,UY,0,UZ,0,ROTX,0,ROTY,0,ROTZ,0
CE,301,0.05,3,UX,1,1,UX,-1
CE,302,0,2,UY,1,1,UY,-1
"""
    r = parse_apdl(text, source="<CE guard>")
    check("含 CE 的命令流无拒绝", r.report.ok,
          "; ".join(r.report.rejected[:3]))
    check("CE 读入计数为 2", r.report.read.get("CE") == 2,
          str(r.report.read.get("CE")))
    ces = {c.id: c for c in r.model.ces}
    check("两条 CE 均进入 model.ces", len(ces) == 2, str(list(ces)))
    ce301 = ces.get(301)
    check("CE301 常数 b=0.05", ce301 is not None
          and close(ce301.constant, 0.05))
    check("CE301 项 UX(3)=+1 / UX(1)=−1",
          ce301 is not None and len(ce301.terms) == 2
          and any(t.node == 3 and t.dof == 1 and close(t.coef, 1.0)
                  for t in ce301.terms)
          and any(t.node == 1 and t.dof == 1 and close(t.coef, -1.0)
                  for t in ce301.terms))
    ce302 = ces.get(302)
    check("CE302 APDL UY 名为 dof=2",
          ce302 is not None and all(t.dof == 2 for t in ce302.terms))
    dat = r.to_dat()
    check(".dat 含 *CE_PARAMETER 与 2 条 *CE",
          "*CE_PARAMETER" in dat and dat.count("*CE\n") == 2)
    check("CE301 行格式正确（id b n dof coef ...）",
          any(line.startswith("301 0.05 3 1 1 1 1 -1")
              for line in dat.splitlines()))
    check("CE 告警含罚函数 PENALTY 说明",
          any("CE" in w and "罚函数" in w and "PENALTY" in w
              for w in r.report.warnings))
    # 未知自由度被拒绝
    bad = text.replace("CE,301,0.05,3,UX,1,1,UX,-1",
                       "CE,301,0.05,3,UX,1,1,WOBBLE,-1")
    rb = parse_apdl(bad, source="<CE bad dof>")
    check("CE 未知自由度被拒绝", not rb.report.ok
          and any("CE 自由度" in x for x in rb.report.rejected))
    # 重复编号只留一条 + 告警
    dup = text + "\nCE,301,0.09,2,UX,1,1,UX,-1\n"
    rd = parse_apdl(dup, source="<CE dup>")
    check("CE 重复编号只保留一条", len(rd.model.ces) == 2)
    check("CE 重复编号有告警",
          any("重复" in w for w in rd.report.warnings))


# ---------------------------------------------------------------- [12] 独立包
def group_app() -> None:
    print("\n[12] 独立应用包同步")
    appdir = ROOT.parent / "BridgeStudio-App" / "app"
    if not appdir.is_dir():
        print("  SKIP  未找到独立应用包（不判 FAIL）")
        return
    p = appdir / "apdl_io.py"
    check("独立包已同步 apdl_io.py", p.is_file(), str(p))
    if not p.is_file():
        return
    a = (ROOT / "apdl_io.py").read_bytes()
    b = p.read_bytes()
    check("独立包 apdl_io.py 与源一致", a == b, str(p))
    pu = appdir / "ui_3d.py"
    check("独立包已同步 ui_3d.py", pu.is_file(), str(pu))
    if pu.is_file():
        check("独立包 ui_3d.py 与源一致",
              (ROOT / "ui_3d.py").read_bytes() == pu.read_bytes(), str(pu))
        uitext = pu.read_text(encoding="utf-8")
        check("独立包 ui_3d.py 含 CE 台账展示",
              "约束方程台账" in uitext)


def main() -> int:
    print("=" * 70)
    print("  APDL 只读导入 (a+b) 回归 — 可逆 / 告警 / 拒绝 / 零污染")
    print("=" * 70)
    group_arch()
    group_roundtrip()
    group_link10()
    group_ce()
    group_reject()
    group_loads_bc()
    # 用纯 BEAM4 回读模型做端到端（arch 含 TRUSS，不是 grillage 的典型工况）
    src = build_box_beam_3d(n_elem_per_span=2)
    rt = parse_apdl(spec3d_to_apdl(src, jobname="verify", element="BEAM4"))
    group_solve(rt.model)
    group_baseline()
    group_app()

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
