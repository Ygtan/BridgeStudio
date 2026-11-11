"""2D 引用完整性中文校验的可执行守卫（纯离线，不调内核求解）。

背景：`能力边界矩阵.md` §2 原把「引用完整性中文校验」记成 `2D=N`，
但 `bridge_spec.py::validate_model` 早就有中文引用校验，且被 `solve_2d`
/ `ModelDoc.validate` / 界面三处调用——这是与「2D 模态/屈曲=N」同源的
**文档漂移**，守卫原先只核对「代码依据列的符号存在」，不核对 N/Y 是否与
源码一致。本轮按 3D 覆盖面补齐 2D 侧缺的引用项，并把该格纠正为 `P`：
2D 仍是单一中文 error 列表、没有 3D 的 warnings 两级，`beam` 还走旧
`ModelSpec` 契约，所以不能标 `Y`。

验收：断引用负例逐类命中且不误报、合法模型与 12 格默认 `.dat` 逐位不变、
三个入口同源、文档口径与源码事实对拍。汇总按布尔位统计（防「假绿」）。
"""
from __future__ import annotations

import copy
import hashlib
import sys
import tempfile
from pathlib import Path

from console_utf8 import setup_console

setup_console()

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import bridge_spec as bs
import model_doc as md
import solve_2d
from verify_stage_tension_3d import DEFAULT_DAT_SHA

PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail else ""))


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def base() -> bs.BridgeSpec:
    """最小合法 2D 梁模型：所有引用完整、dof 合法、无退化单元。"""
    return bs.BridgeSpec(
        title="2D 引用完整性守卫基线",
        nodes=[bs.NodeDef(id=1, x=0.0, y=0.0),
               bs.NodeDef(id=2, x=6.0, y=0.0)],
        materials=[bs.MaterialDef(id=1)],
        elem_types=[bs.ElemTypeDef(id=1, kind=bs.ElemKind.BEAM,
                                   A=0.01, I=1e-4, mat=1)],
        elements=[bs.ElemDef(id=1, type_id=1, n1=1, n2=2)],
        boundaries=[bs.BoundaryDef(node=1, dofs="1-2"),
                    bs.BoundaryDef(node=2, dofs="2")],
        nodal_loads=[bs.NodalLoadDef(node=2, dof=2, value=-1000.0)],
    )


def negatives() -> None:
    print("\n[1] 断引用负例：逐类命中，且只新增那一条（不误报）")
    check("基线模型校验为空", base().validate_model() == [],
          str(base().validate_model()))
    cases: list[tuple[str, object, str]] = [
        ("重复材料 id",
         lambda s: s.materials.append(bs.MaterialDef(id=1)),
         "存在重复材料 id"),
        ("单元类型引用不存在的材料",
         lambda s: setattr(s.elem_types[0], "mat", 99),
         "单元类型 1 引用了不存在的材料 99"),
        ("重复施工阶段 id",
         lambda s: setattr(s, "stages",
                           [bs.Stage2D(id=1), bs.Stage2D(id=1)]),
         "存在重复施工阶段 id"),
        ("阶段 ACTIVE 断引用",
         lambda s: setattr(s, "stages", [bs.Stage2D(id=1, activate=[99])]),
         "施工阶段 1 ACTIVE 引用了不存在的单元 99"),
        ("阶段 DEACTIVE 断引用",
         lambda s: setattr(s, "stages", [bs.Stage2D(id=1, deactivate=[98])]),
         "施工阶段 1 DEACTIVE 引用了不存在的单元 98"),
        ("阶段荷载节点断引用",
         lambda s: setattr(s, "stages",
                           [bs.Stage2D(id=1,
                                       loads=[bs.StageLoadDef(node=97)])]),
         "施工阶段 1 荷载引用了不存在的节点 97"),
        ("阶段荷载 dof 越界",
         lambda s: setattr(s, "stages",
                           [bs.Stage2D(id=1,
                                       loads=[bs.StageLoadDef(node=2, dof=6)])]),
         "施工阶段 1 荷载 dof=6 越界"),
        ("阶段单元荷载断引用",
         lambda s: setattr(s, "stages",
                           [bs.Stage2D(id=1,
                                       elem_loads=[bs.StageElemLoadDef(elems=[96])])]),
         "施工阶段 1 单元荷载引用了不存在的单元 96"),
        ("阶段预应力断引用",
         lambda s: setattr(s, "stages",
                           [bs.Stage2D(id=1,
                                       prestresses=[bs.StagePrestressDef(elems=[95])])]),
         "施工阶段 1 预应力引用了不存在的单元 95"),
        ("车道节点断引用",
         lambda s: setattr(s, "lanes", [bs.Lane2D(nodes=[94])]),
         "车道荷载引用了不存在的节点 94"),
        ("节点荷载 dof 越界",
         lambda s: setattr(s, "nodal_loads",
                           [bs.NodalLoadDef(node=2, dof=6)]),
         "节点荷载 dof=6 越界"),
        ("边界 dofs 非法字符串",
         lambda s: setattr(s.boundaries[0], "dofs", "abc"),
         "边界约束 dofs='abc' 非法或越界"),
        ("边界 dofs 越界到 3D 自由度",
         lambda s: setattr(s.boundaries[0], "dofs", "1-6"),
         "边界约束 dofs='1-6' 非法或越界"),
        ("零长度单元",
         lambda s: setattr(s.nodes[1], "x", 0.0),
         "单元 1 两端节点坐标重合（零长度单元）"),
    ]
    for name, mutate, expect in cases:
        s = base()
        mutate(s)  # type: ignore[operator]
        errs = s.validate_model()
        check(f"负例命中：{name}", any(expect in e for e in errs),
              str(errs)[:90])
        check(f"负例只报一条：{name}", len(errs) == 1, str(len(errs)))

    s = base()
    setattr(s.elem_types[0], "mat", 99)
    s.stages = [bs.Stage2D(
        id=1, activate=[99], deactivate=[98],
        loads=[bs.StageLoadDef(node=97)],
        elem_loads=[bs.StageElemLoadDef(elems=[96])],
        prestresses=[bs.StagePrestressDef(elems=[95])])]
    s.lanes = [bs.Lane2D(nodes=[94])]
    errs = s.validate_model()
    check("多类断引用一次全报出（不短路、不吞错）", len(errs) >= 7,
          f"{len(errs)} 条")
    check("中文报错不含 Markdown 残留",
          all("**" not in e for e in errs))


def positives() -> None:
    print("\n[2] 正例：合法引用不误报、默认 .dat 逐位不变")
    for (kind, dim), b in md.BUILDERS.items():
        dat = md.ModelDoc(kind, dict(md.DEFAULTS[b]), dimension=dim).to_dat()
        h = hashlib.sha256(dat.encode()).hexdigest()[:16]
        check(f"默认 .dat 黄金哈希：{b}", h == DEFAULT_DAT_SHA[b], h)

    for (kind, dim), b in md.BUILDERS.items():
        if dim != "2d":
            continue
        s = md.ModelDoc(kind, dict(md.DEFAULTS[b]), dimension=dim).model
        if isinstance(s, bs.BridgeSpec):
            check(f"{b} 默认模型通过补齐后的校验", s.validate_model() == [],
                  str(s.validate_model())[:60])

    variants = [
        ("beam_bridge", {"staged": True}),
        ("beam_bridge", {"lane": True}),
        ("beam_bridge", {"staged": True, "lane": True, "n_spans": 3,
                         "stage_tension_kN": 1000.0,
                         "stage_tension_loss": 0.85}),
        ("cable_stayed_2d", {"staged": True}),
        ("cable_stayed_2d", {"staged": True, "lane": True}),
        ("suspension_2d", {"lane": True}),
    ]
    for b, mod in variants:
        p = dict(md.DEFAULTS[b])
        p.update(mod)
        kind, dim = next((k, d) for (k, d), bb in md.BUILDERS.items()
                         if bb == b and d == "2d")
        s = md.ModelDoc(kind, p, dimension=dim).model
        errs = s.validate_model() if isinstance(s, bs.BridgeSpec) else ["非 BridgeSpec"]
        check(f"{b} {list(mod)[0]} 变体合法引用不误报", errs == [],
              str(errs)[:60])

    beam = md.ModelDoc("beam", dict(md.DEFAULTS["beam"]),
                       dimension="2d").model
    check("beam 走旧 ModelSpec 契约（不属 BridgeSpec 校验链）",
          not isinstance(beam, bs.BridgeSpec), type(beam).__name__)


def chains() -> None:
    print("\n[3] 三个入口同源：断引用在写 .dat 之前被中文拦下")
    bad = base()
    setattr(bad.elem_types[0], "mat", 99)
    expect = "单元类型 1 引用了不存在的材料 99"
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        raised = ""
        try:
            solve_2d.solve_2d(bad, out_dir=d)
        except ValueError as ex:
            raised = str(ex)
        check("solve_2d 抛 ValueError 且带同一句中文原因",
              "模型校验未通过" in raised and expect in raised,
              raised[:70].replace("\n", " "))
        check("断引用时不写出 .dat 文件", not list(d.glob("*.dat")),
              str(list(d.glob("*.dat"))))
    mdoc = read(ROOT / "model_doc.py")
    app = read(ROOT / "app.py")
    s2d = read(ROOT / "solve_2d.py")
    check("solve_2d 校验在写 .dat 之前",
          "errs = spec.validate_model()" in s2d
          and s2d.index("errs = spec.validate_model()")
          < s2d.index("dat_path.write_text"))
    check("ModelDoc.validate 复用 2D 校验", "validate_model()" in mdoc)
    check("界面求解前中文展示校验结果",
          "doc.validate()" in app and "st.error" in app)


def source_contract() -> None:
    print("\n[4] 源码与文档口径对拍（防止这一格再漂回 N）")
    src = read(ROOT / "bridge_spec.py")
    for kw in ("引用了不存在的材料", "ACTIVE 引用了不存在的单元",
               "DEACTIVE 引用了不存在的单元", "预应力引用了不存在的单元",
               "车道荷载引用了不存在的节点", "零长度单元",
               "重复施工阶段 id", "边界约束 dofs"):
        check(f"bridge_spec 含校验：{kw}", kw in src)
    check("校验只读模型、不写 .dat 字段",
          "to_dat()" in src and "这里只读模型、不写任何字段" in src)
    mat = read(ROOT / "能力边界矩阵.md")
    rows = [ln for ln in mat.splitlines()
            if ln.startswith("| 引用完整性中文校验 |")]
    check("§2 该格按源码纠正为 2D=P（不虚报 Y）",
          len(rows) == 1 and "| P | Y |" in rows[0], str(rows))
    check("§2 登记 2D 侧真实符号", "bridge_spec.py::validate_model" in mat)
    check("§2 写明 2D 仍窄于 3D 的边界", "2D 仍窄于 3D" in mat)
    check("§4.2 计数改为 5 个 N + 2 个 P",
          "5 个" in mat and "2 个 `2D=P`" in mat)
    check("能力矩阵登记第七次闭环记录", "第七次闭环记录" in mat)
    spec = read(ROOT / "B类后续项接入规格.md")
    check("规格写明这一格主体是文档漂移", "文档漂移" in spec)
    check("规格写明多格式导出被 X20 契约锁死",
          "X20 契约" in spec and "唯一转换点" in spec)
    check("规格写明 2D 校验标 P 不标 Y", "标 `P` 不标 `Y`" in spec)
    check("README 同步 2 项部分接入",
          "2 项部分接入" in read(ROOT / "README.md"))
    for f in ("使用说明.md", "开发使用手册.md"):
        check(f"{f} 登记新守卫", "verify_validate_2d.py" in read(ROOT / f))
    g = read(Path(__file__))
    check("守卫汇总按布尔位统计（防假绿）",
          "PASS.append((name, bool(ok), detail))" in g
          and "sum(1 for _, ok, _ in PASS if ok)" in g)
    for banned in ("sub" + "process", "os." + "system"):
        check(f"守卫自身不含 {banned}", banned not in g)


def main() -> int:
    print("=" * 78)
    print("2D 引用完整性中文校验守卫（纯离线）")
    negatives()
    positives()
    chains()
    source_contract()
    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(PASS)} 通过")
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
