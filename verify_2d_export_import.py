"""verify_2d_export_import.py —— 2D 平行导出 / 导入链（B2 第十五次）独立守卫。

背景（2026-11-12 第十五次，B2「2D 平行导出 / 导入链」）：
按契约 §3.4 的正交约定，``export_2d_formats.py`` 另建一条与 3D 链平行、
互不冒充的第二条链：``BridgeSpec → plane_frame_2d/v1 → ANSYS / Abaqus /
MIDAS(2D)``，以及 ``.mct → BridgeSpec`` 的反向导入。中间表示不是 X20
canonical dict，不回流 3D 唯一转换点；缺项标「不适用」而不是补 0；MIDAS
语法必填数值位逐条登记为「格式占位」，导入侧绝不反解；非平面模型显式
``dimension_not_plane`` + ``spec=None``，绝不静默投影。

本守卫锁住 10 组（A~J）：
- A 新模块源码契约（接口符号、物理隔离用 AST + tokenize 精确判定、docstring
  口径、CLI 两个子命令、三个中文 helpers）；
- B ``to_plane()`` 正例（真实构造 BridgeSpec、9 顶层键恰好齐全、逐节点/
  截面/边界/荷载结构、数值对拍、不补零、写出器拒绝负例）；
- C 三格式写出（实跑写 out/ 临时目录、格式关键字、格式占位、确定性逐字节）；
- D 往返 oracle（真数值：5 节点 / 4 单元 / E / dens / A / I + warnings / losses）；
- E 维度拒绝负例（z 非 0 / 6 位码平面外位未固定 / 空串 / 无 *NODE / 全注释）；
- F 单位换算 oracle（与 mct_io._parse_unit 逐项对拍 + KN,M 荷载 -100kN→-100000N）；
- G 解析负例（18 类显式拒绝 + 段外行 / 非 UTF-8 bytes 两条健壮性）；
- H 默认零污染与 3D 链路未动（12 格 DEFAULT_DAT_SHA / 3D 签名 / 默认 HTML
  逐字节 / 四个业务文件零改动 / git diff --stat）；
- I 契约与两仓文档对拍（第十五次当前口径 / D 类历史仍在 / 数字无关口径 /
  FE 三份零命中）；
- J 汇总统计不许「假绿」（本守卫自身布尔位结构 / plane_chain() / 登记表）。

铁律：**不重算**（三格式文本与往返数值只由被测模块产生，Python 不另拼
ANSYS/Abaqus/MCT 行、不由位移反推截面/惯矩）、**不补零**（缺项标不适用、
维度不符显式拒绝）、**不冒充**（平行链不碰 3D canonical）。
汇总按布尔位统计防「假绿」：``n_pass = sum(1 for _, ok, _ in PASS if ok)``，
``n_pass == len(PASS)`` 才返回 0。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import ast
import dataclasses
import hashlib
import inspect
import io
import math
import os
import re
import shutil
import subprocess
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import export_2d_formats as E
import export_formats
import mct_io
import verify_x20_contract as VX
from bridge_spec import BridgeSpec, build_beam_bridge
from export_html import write_2d_html
from model_doc import BUILDERS, DEFAULTS, ModelDoc
from solve_2d import Solve2DResult
from verify_stage_tension_3d import DEFAULT_DAT_SHA

FE = Path("D:/AI-work/FEM-Eigen")
OUT = ROOT / "out" / "verify_2d_export_import"
PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, bool(ok), str(detail)))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail else ""))


def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ---------------------------------------------------------------- 公共夹具

def spec_and_plane():
    """B2 正例唯一夹具：真实构造 30m / 4 单元 / 100kN 节点力的 2D 平面梁。"""
    spec = build_beam_bridge(span=30, n_elem=4, point_kN=100)
    plane, warnings = E.to_plane(spec)
    return spec, plane, warnings


def mct_text(stem: str) -> str:
    """实跑 write_midas_2d() 写 out/ 临时文件，返回其文本（只由被测模块产出）。"""
    _, plane, _ = spec_and_plane()
    p = OUT / (stem + ".mct")
    E.write_midas_2d(plane, p)
    return p.read_text(encoding="utf-8")


def section_data_lines(text: str, header: str) -> list[str]:
    """取 *段头 之后的数据行（去注释 / 空行），遇下一个 *段头收尾。"""
    out: list[str] = []
    in_sec = False
    for ln in text.splitlines():
        if ln.startswith(header):
            in_sec = True
            continue
        if in_sec and ln.startswith("*"):
            break
        if in_sec and ln.strip() and not ln.startswith(("**", ";")):
            out.append(ln.strip())
    return out


def all_keys(o) -> set[str]:
    """结构化收集嵌套 dict 的全部键（不把说明文字当键）。"""
    keys: set[str] = set()
    if isinstance(o, dict):
        keys |= set(o.keys())
        for v in o.values():
            keys |= all_keys(v)
    elif isinstance(o, (list, tuple)):
        for v in o:
            keys |= all_keys(v)
    return keys


# KN,M 单位的最小 MCT：FY=-100 kN → 导入后 value=-100000.0 N。
MINI_MCT = """*UNIT
KN, M
*MATERIAL
1, USER, M, 0, 0, 0, 0, NO, 0, 2, 206000000, 0, 0, 76977.1, 0
*SECTION
1, VALUE, S, CC, 0, 0, 0, 0, 0, 0
0.478, 0, 0, 0, 0.4147995833, 0
0, 0, 0, 0, 0, 0, 0, 0, 0, 0
0, 0, 0, 0, 0, 0, 0, 0, 0, 0
*NODE
1, 0, 0, 0
2, 7.5, 0, 0
*ELEMENT
1, BEAM, 1, 1, 1, 2, 0, 0, 0
*CONSTRAINT
1, 111110, 
2, 111110, 
*STLDCASE
DL, D, x
*USE-STLD, DL
*CONLOAD
2, 0, -100, 0, 0, 0, 0, 
*ENDDATA
"""


# ---------------------------------------------------------------- [A] 源码契约

def group_a() -> None:
    print("\n[A] 新模块源码契约（读文本，不重算）")
    path = ROOT / "export_2d_formats.py"
    src = read(path)
    check("export_2d_formats.py 存在且非空", bool(src), f"{len(src)}B")

    # 13 个接口符号 / 常量逐个断言（DIMENSION 用精确赋值字面量）。
    symbols = (
        "PLANE_SCHEMA", 'DIMENSION = "2D"', "PLANE_DOF_NAMES",
        "to_plane", "write_ansys_2d", "write_abaqus_2d", "write_midas_2d",
        "export_2d_all", "PlaneMctImport", "read_mct_2d_text",
        "read_mct_2d_file", "read_mct_2d", "summary",
    )
    for s in symbols:
        check(f"A 接口符号在位：{s}", s in src)

    # ---- 物理隔离：只拦「可执行依赖」，说明文字里的 canonical / mct_io 不误伤。
    forbidden_lits = ("from export_formats import", "import export_formats",
                      "from mct_io import", "import mct_io",
                      "def to_canonical(", "write_mct_from_canonical",
                      "read_mct_text")
    check("源码无 3D 链可执行 import / 同名转换函数串",
          not any(lit in src for lit in forbidden_lits),
          str([lit for lit in forbidden_lits if lit in src]))

    tree = ast.parse(src)
    imports: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imports += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            imports.append(n.module or "")
    banned_mods = {"export_formats", "mct_io", "model_spec_3d",
                   "bridge_fe_modeler"}
    check("AST import 模块名不含 3D 链模块",
          not any(m.split(".")[0] in banned_mods for m in imports),
          str(sorted(set(imports))))

    # tokenize 去掉字符串 / 注释 / f-string 内容，只留代码 token；
    # 精确按 token 相等判定，避免 PlaneMctImport 里的 "MctImport" 子串误伤。
    skip = {tokenize.STRING, tokenize.COMMENT}
    for extra in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):
        if hasattr(tokenize, extra):
            skip.add(getattr(tokenize, extra))
    code_tokens = [t.string for t in tokenize.generate_tokens(io.StringIO(src).readline)
                   if t.type not in skip]
    banned_ids = {"canonical", "export_formats", "to_canonical", "mct_io",
                  "model_spec_3d", "ModelSpec3D", "MctImport",
                  "write_mct_from_canonical", "read_mct_text",
                  "bridge_fe_modeler"}
    bad = sorted(set(t for t in code_tokens if t in banned_ids))
    check("tokenize 精确判定：3D 标识符未用作代码 token", not bad, str(bad))

    # ---- docstring 口径（module docstring 4 条 + 全文「不补 0」1 条）。
    doc0 = src.split('"""')[1] if '"""' in src else ""
    for kw in ("不是 X20 canonical dict", "正交", "格式占位",
               "dimension_not_plane"):
        check(f"模块 docstring 口径：{kw}", kw in doc0)
    check("源码说明文字写明「不补 0」", "不补 0" in src)

    # ---- CLI 两子命令 + main。
    check("CLI 有 def main(", "def main(" in src)
    check("CLI 有 export 子命令", 'if cmd == "export"' in src)
    check("CLI 有 import 子命令", 'if cmd == "import"' in src)

    # ---- 三个 helpers 返回非空中文列表。
    helpers = (
        ("_format_placeholder_warnings", E._format_placeholder_warnings, 4),
        ("_inherent_losses_2d", E._inherent_losses_2d, 8),
        ("_abaqus_dof_warnings", E._abaqus_dof_warnings, 1),
    )
    for name, fn, want in helpers:
        v = fn()
        check(f"{name}() 返回非空中文列表",
              isinstance(v, list) and len(v) > 0
              and all(any("\u4e00" <= c <= "\u9fff" for c in s) for s in v))
        check(f"{name}() 恰 {want} 条", len(v) == want, str(len(v)))


# ---------------------------------------------------------------- [B] to_plane 正例

def group_b() -> None:
    print("\n[B] to_plane() 正例（真实构造 BridgeSpec）")
    spec, plane, warnings = spec_and_plane()
    check("to_plane 返回 (plane, warnings) 且 warnings 非空",
          isinstance(plane, dict) and isinstance(warnings, list)
          and len(warnings) > 0, str(len(warnings)))

    top = {"schema", "dimension", "bridge", "materials", "section_types",
           "nodes", "elements", "boundary_conditions", "loads"}
    check("9 个顶层键恰好齐全（多一少一都不通过）", set(plane) == top,
          str(sorted(set(plane) ^ top)))
    check("schema == plane_frame_2d/v1", plane["schema"] == "plane_frame_2d/v1")
    check("dimension == 2D", plane["dimension"] == "2D")
    check("bridge[\"type\"] == PLANE_FRAME_2D",
          plane["bridge"]["type"] == "PLANE_FRAME_2D")

    # 逐节点只有 id/x/y；逐截面只有 id/kind/area/inertia。
    check("逐节点键恰为 id/x/y（无 z）",
          all(set(nd) == {"id", "x", "y"} for nd in plane["nodes"]))
    check("逐截面键恰为 id/kind/area/inertia（无任何 3D 惯矩键）",
          all(set(sec) == {"id", "kind", "area", "inertia"}
              for sec in plane["section_types"]))
    # 逐边界 dofs <= UX/UY/ROTZ；逐荷载 dof 是 int 且 <= 3。
    check("逐边界 dofs 只含平面 3 dof（UX/UY/ROTZ）",
          all(set(bc["dofs"]) <= {"UX", "UY", "ROTZ"}
              for bc in plane["boundary_conditions"]))
    check("逐荷载 dof 是 int 且 <= 3",
          all(isinstance(ld["dof"], int) and ld["dof"] <= 3
              for ld in plane["loads"]["nodal_loads"]))

    # 数值对拍：直接来自 BridgeSpec 数据类，不经重算。
    check("area == ElemTypeDef.A",
          plane["section_types"][0]["area"] == spec.elem_types[0].A)
    check("inertia == ElemTypeDef.I",
          plane["section_types"][0]["inertia"] == spec.elem_types[0].I)
    check("节点坐标 == NodeDef.x/y",
          all(nd["x"] == sn.x and nd["y"] == sn.y
              for nd, sn in zip(plane["nodes"], spec.nodes)))
    check("elastic_modulus == MaterialDef.E",
          plane["materials"][0]["elastic_modulus"] == spec.materials[0].E)

    # 不补零：结构化键集合判定，绝不用全文子串误伤说明文字。
    keys = all_keys(plane)
    check("plane 结构化键里没有 IYY/IZZ/IXX/IY/IZ/J/z 等 3D 专有键",
          not any(k in keys for k in ("IYY", "IZZ", "IXX", "IY", "IZ", "J", "z")),
          str(sorted(keys)))

    # ---- 写出器拒绝负例：中文 ValueError 且含「2D」。
    for label, bad_plane in (
        ("dimension=3D", dict(plane, dimension="3D")),
        ("schema 不对", dict(plane, schema="x")),
        ("非 dict", "x"),
    ):
        for fn in (E.write_ansys_2d, E.write_abaqus_2d, E.write_midas_2d):
            try:
                fn(bad_plane, OUT / "neg.tmp")
                ok, msg = False, ""
            except ValueError as ex:
                ok, msg = True, str(ex)
            except Exception:
                ok, msg = False, ""
            check(f"写出器 {fn.__name__} 拒绝 {label}（中文 ValueError 含 2D）",
                  ok and "2D" in msg, msg[:60])

    # 缺顶层键也应拒绝（_validate_plane 通过后访问缺键抛 KeyError）。
    try:
        E.write_midas_2d({"schema": E.PLANE_SCHEMA, "dimension": "2D"},
                         OUT / "neg2.tmp")
        rejected = False
    except (ValueError, KeyError):
        rejected = True
    except Exception:
        rejected = False
    check("plane 缺顶层键被显式拒绝（不静默兜底）", rejected)


# ---------------------------------------------------------------- [C] 三格式写出

def group_c() -> None:
    print("\n[C] 三格式写出（实跑写 out/ 临时目录）")
    spec, plane, _ = spec_and_plane()
    res = E.export_2d_all(spec, OUT, stem="c")
    paths = res["paths"]
    check("export_2d_all 返回 paths 三键 ansys/abaqus/midas",
          set(paths) == {"ansys", "abaqus", "midas"}, str(sorted(paths)))
    check("export_2d_all warnings 非空",
          isinstance(res["warnings"], list) and len(res["warnings"]) > 0,
          str(len(res["warnings"])))
    check("export_2d_all.plane 与 to_plane 一致", res["plane"] == plane)
    for k, p in paths.items():
        q = Path(p)
        check(f"{k} 文件存在 / 非空 / 可读",
              q.is_file() and q.stat().st_size > 0 and bool(read(q)))

    ans = read(Path(paths["ansys"]))
    aba = read(Path(paths["abaqus"]))
    mct = read(Path(paths["midas"]))

    check("ANSYS 含 DIMENSION=2D", "DIMENSION=2D" in ans)
    check("ANSYS 用 BEAM3", "ET,1,BEAM3" in ans)
    check("ANSYS 含 MP,EX 材料行", "MP,EX," in ans)
    n_lines = [ln for ln in ans.splitlines() if ln.startswith("N,")]
    check("ANSYS N 行只有两坐标（N,id,x,y）",
          len(n_lines) == 5 and all(len(ln.split(",")) == 4 for ln in n_lines))
    check("ANSYS 含 R, 实常数行", any(ln.startswith("R,") for ln in ans.splitlines()))
    check("ANSYS 不含 3D 单元 BEAM4 / BEAM188",
          "BEAM4" not in ans and "BEAM188" not in ans)
    check("ANSYS 含「! z / 6 dof / IYY·IZZ·IXX 在 2D 不适用」说明",
          "! z / 6 dof / IYY·IZZ·IXX 在 2D 不适用" in ans)

    check("Abaqus 含 DIMENSION=2D", "DIMENSION=2D" in aba)
    check("Abaqus 用 B21", "type=B21" in aba)
    aba_nodes = section_data_lines(aba, "*Node")
    check("Abaqus *Node 行只有两坐标（id,x,y）",
          len(aba_nodes) == 5
          and all(len(ln.split(",")) == 3 for ln in aba_nodes))
    check("Abaqus 含 *Beam Section", "*Beam Section" in aba)
    check("Abaqus 含 ROTZ(dof 3) → 6 口径说明", "ROTZ(dof 3) → 6" in aba)
    check("Abaqus 不含 3D 单元 B31", "B31" not in aba)

    check("MIDAS 含 DIMENSION=2D", "DIMENSION=2D" in mct)
    check("MIDAS 含 *UNIT/*NODE/*ELEMENT/*MATERIAL/*SECTION/*CONSTRAINT/*CONLOAD",
          all(sec in mct for sec in ("*UNIT", "*NODE", "*ELEMENT",
                                     "*MATERIAL", "*SECTION", "*CONSTRAINT",
                                     "*CONLOAD")))
    warn_lines = [ln for ln in mct.splitlines()
                  if ln.startswith("; WARN MIDAS 格式占位：")]
    check("MIDAS 恰好 4 条 ; WARN MIDAS 格式占位：且每条含三关键词",
          len(warn_lines) == 4
          and all(all(k in ln for k in ("格式占位", "不适用", "导入侧绝不反解"))
                  for ln in warn_lines), str(len(warn_lines)))
    node_rows = section_data_lines(mct, "*NODE")
    check("MIDAS *NODE 的 Z 列恒 0",
          len(node_rows) == 5
          and all(len(r.split(",")) >= 4 and r.split(",")[3].strip() == "0"
                  for r in node_rows))
    con_rows = section_data_lines(mct, "*CONSTRAINT")
    codes = [r.split(",")[1].strip() for r in con_rows if len(r.split(",")) >= 2]
    check("MIDAS *CONSTRAINT 6 位码平面外位（第 3/4/5 位）恒 1",
          len(codes) >= 1 and all(len(c) == 6 and c[2:5] == "111"
                                  for c in codes), str(codes))

    # 三个写出器各自单独调用，与 export_2d_all 对应文件逐字节一致。
    plane2, _ = E.to_plane(spec)
    for fn, key in ((E.write_ansys_2d, "ansys"),
                    (E.write_abaqus_2d, "abaqus"),
                    (E.write_midas_2d, "midas")):
        q = OUT / ("single_" + Path(paths[key]).name)
        fn(plane2, q)
        check(f"{fn.__name__} 单独产出与 export_2d_all 逐字节一致",
              q.read_bytes() == Path(paths[key]).read_bytes())


# ---------------------------------------------------------------- [D] 往返 oracle

def group_d() -> None:
    print("\n[D] 往返 oracle（write_midas_2d → read_mct_2d_text，给数值）")
    spec, _, _ = spec_and_plane()
    mct = mct_text("d")
    imp = E.read_mct_2d_text(mct, title="roundtrip", source="<d>")
    s = imp.summary()
    check("summary()[\"ok\"] is True", s["ok"] is True)
    check("spec is not None", imp.spec is not None)
    check("errors == []", imp.errors == [])
    check("dimension == 2D", s["dimension"] == "2D")
    check("spec.to_dat() 生成非空文本",
          imp.spec is not None and len(imp.spec.to_dat()) > 0)
    check("往返节点数 5", s["n_nodes"] == 5, str(s["n_nodes"]))
    check("往返单元数 4", s["n_elements"] == 4, str(s["n_elements"]))

    rs = imp.spec
    xs = [n.x for n in rs.nodes]
    ys = [n.y for n in rs.nodes]
    want_x = (0.0, 7.5, 15.0, 22.5, 30.0)
    check("往返节点 x == 0/7.5/15/22.5/30（rel 1e-9）",
          len(xs) == 5
          and all(math.isclose(g, w, rel_tol=1e-9) for g, w in zip(xs, want_x)),
          str(xs))
    check("往返节点 y 全 0（rel 1e-9）",
          all(math.isclose(v, 0.0, rel_tol=1e-9, abs_tol=1e-12) for v in ys))
    mat = rs.materials[0]
    sec = rs.elem_types[0]
    check("往返材料 E == 2.06e11（rel 1e-9）",
          math.isclose(mat.E, 2.06e11, rel_tol=1e-9), str(mat.E))
    check("往返密度 dens ≈ 7850（rel 1e-9）",
          math.isclose(mat.dens, 7850.0, rel_tol=1e-9), str(mat.dens))
    check("往返截面 A ≈ 0.478（rel 1e-9）",
          math.isclose(sec.A, 0.478, rel_tol=1e-9), str(sec.A))
    check("往返截面 I ≈ 0.4147995833（rel 1e-9）",
          math.isclose(sec.I, 0.4147995833, rel_tol=1e-9), str(sec.I))
    check("往返 I 相对原始 spec 的误差 < 1e-9（写出 10 位有效数字）",
          math.isclose(sec.I, spec.elem_types[0].I, rel_tol=1e-9),
          str(abs(sec.I - spec.elem_types[0].I) / spec.elem_types[0].I))

    check("往返 warnings 非空", len(imp.warnings) > 0, str(len(imp.warnings)))
    wtxt = " ".join(imp.warnings)
    for k in ("格式占位", "不适用", "单位换算"):
        check(f"往返 warnings 含「{k}」", k in wtxt)

    check("往返 losses >= 10 条", len(imp.losses) >= 10, str(len(imp.losses)))
    ltxt = " ".join(imp.losses)
    check("losses 覆盖车道/影响线/成桥态/施工阶段/自重/预应力/β 角/DBUSER/分布荷载",
          all(k in ltxt for k in ("车道", "影响线", "成桥态", "施工阶段",
                                  "自重", "预应力", "β 角", "DBUSER", "分布荷载")))
    check("losses 覆盖 *STLDCASE / *USE-STLD 关键字",
          "*STLDCASE" in ltxt and "*USE-STLD" in ltxt)

    check("PlaneMctImport 是 dataclass",
          dataclasses.is_dataclass(E.PlaneMctImport))
    check("summary() 返回 7 个键齐全",
          set(s) == {"ok", "errors", "warnings", "losses", "n_nodes",
                     "n_elements", "dimension"}, str(sorted(s)))


# ---------------------------------------------------------------- [E] 维度拒绝负例

def group_e() -> None:
    print("\n[E] 维度拒绝负例（不静默兜底 / 不投影成 2D）")
    mct = mct_text("e")

    def dim_bad(name: str, text: str, want_kw: str) -> None:
        try:
            r = E.read_mct_2d_text(text)
            raised = False
        except Exception:
            r, raised = None, True
        errs = " ".join(r.errors) if r is not None else ""
        check(f"E 负例 {name}：不抛异常且 spec is None / ok False",
              (not raised) and r is not None and r.spec is None
              and r.summary()["ok"] is False)
        check(f"E 负例 {name}：errors 含 dimension_not_plane",
              "dimension_not_plane" in errs, errs[:90])
        check(f"E 负例 {name}：错误文本含「{want_kw}」", want_kw in errs)

    dim_bad("节点 1 z=0.5", mct.replace("1, 0, 0, 0\n", "1, 0, 0, 0.5\n", 1),
            "节点 1")
    dim_bad("约束码 111110→110110", mct.replace("111110", "110110"),
            "平面外自由度")
    dim_bad("多节点 z 非 0",
            mct.replace("1, 0, 0, 0\n", "1, 0, 0, 0.5\n", 1)
               .replace("2, 7.5, 0, 0\n", "2, 7.5, 0, 0.5\n", 1), "节点 1")
    dim_bad("节点 z 为负", mct.replace("1, 0, 0, 0\n", "1, 0, 0, -0.5\n", 1),
            "节点 1")
    dim_bad("约束码全 0", mct.replace("111110", "000000"), "平面外自由度")
    dim_bad("约束码第 4 位为 0", mct.replace("111110", "111010"),
            "平面外自由度")
    dim_bad("约束码含非 01 字符", mct.replace("111110", "11X110"),
            "平面外自由度")

    # 空串 / 无 *NODE / 全注释：都不抛异常、errors 非空、spec is None。
    no_node_lines: list[str] = []
    skip = False
    for ln in mct.splitlines():
        if ln.startswith("*NODE"):
            skip = True
            continue
        if skip and ln.startswith("*"):
            skip = False
        if skip:
            continue
        no_node_lines.append(ln)
    for label, text in (("空串", ""),
                        ("无 *NODE 段", "\n".join(no_node_lines)),
                        ("全注释文本", "; a\n; b\n; c\n")):
        try:
            r = E.read_mct_2d_text(text)
            raised = False
        except Exception:
            r, raised = None, True
        check(f"E 负例 {label}：不抛异常 / spec is None / errors 非空",
              (not raised) and r is not None and r.spec is None
              and len(r.errors) > 0,
              "" if r is None else " ".join(r.errors)[:80])


# ---------------------------------------------------------------- [F] 单位换算 oracle

def group_f() -> None:
    print("\n[F] 单位换算 oracle（与 mct_io._parse_unit 对拍）")
    for unit_txt, want_unit, want_force, want_len in (
        ("N, MM", ("N", "MM"), 1.0, 1e-3),
        ("KN, M", ("KN", "M"), 1e3, 1.0),
        ("KGF, CM", ("KGF", "CM"), 9.80665, 1e-2),
    ):
        secs = E._split("*UNIT\n" + unit_txt + "\n")
        r2 = E._parse_unit_2d(secs)
        r3 = mct_io._parse_unit(secs)
        check(f"F {unit_txt}：单位元组与 mct_io 相等", r2[0] == r3[0] == want_unit)
        check(f"F {unit_txt}：力系数与 mct_io 相等（={want_force:g}）",
              math.isclose(r2[1], r3[1], rel_tol=1e-12)
              and math.isclose(r2[1], want_force, rel_tol=1e-12))
        check(f"F {unit_txt}：长度系数与 mct_io 相等（={want_len:g}）",
              math.isclose(r2[2], r3[2], rel_tol=1e-12)
              and math.isclose(r2[2], want_len, rel_tol=1e-12))

    imp = E.read_mct_2d_text(MINI_MCT, title="unit")
    check("*UNIT=KN,M + FY=-100kN 最小 MCT 导入成功",
          imp.summary()["ok"] is True and imp.spec is not None,
          " ".join(imp.errors)[:80])
    loads = imp.spec.nodal_loads if imp.spec is not None else []
    check("FY=-100kN 换算为 value == -100000.0 N（rel 1e-9）",
          len(loads) == 1 and loads[0].dof == 2
          and math.isclose(loads[0].value, -100000.0, rel_tol=1e-9),
          str([(ld.dof, ld.value) for ld in loads]))
    check("单位换算 warnings 里有「单位换算」说明",
          any("单位换算" in w for w in imp.warnings))


# ---------------------------------------------------------------- [G] 解析负例

def group_g() -> None:
    print("\n[G] 解析负例（18 类 spec is None / errors 非空 / 不抛异常 + 2 健壮性）")
    mct = mct_text("g")
    mat_line = ("1, USER, 钢材 Q345, 0, 0, 0, 0, NO, 0, 2, "
                "2.06e+11, 0, 0, 76977.1, 0")

    def drop_section(text: str, header: str) -> str:
        out, skip = [], False
        for ln in text.splitlines():
            if ln.startswith(header):
                skip = True
                continue
            if skip and ln.startswith("*"):
                skip = False
            if skip:
                continue
            out.append(ln)
        return "\n".join(out)

    def bad(name: str, text: str) -> None:
        try:
            r = E.read_mct_2d_text(text)
            raised = False
        except Exception:
            r, raised = None, True
        check(f"G 负例 {name}：不抛异常", not raised)
        check(f"G 负例 {name}：spec is None",
              r is not None and r.spec is None)
        check(f"G 负例 {name}：errors 非空",
              r is not None and len(r.errors) > 0,
              "" if r is None else " ".join(r.errors)[:70])

    bad("空串", "")
    bad("只有注释", "; a\n; b\n")
    bad("无段头 [OTHER]", "[OTHER]\nX=1\nY=2\n")
    bad("*UNIT 非法（残缺模型）", "; c\n*UNIT\nXYZ, ABC\n")
    bad("节点行非数值（id 非法）",
        mct.replace("1, 0, 0, 0\n", "abc, 0, 0, 0\n", 1)
           .replace("2, 7.5, 0, 0\n", "def, 7.5, 0, 0\n", 1)
           .replace("3, 15, 0, 0\n", "ghi, 15, 0, 0\n", 1)
           .replace("4, 22.5, 0, 0\n", "jkl, 22.5, 0, 0\n", 1)
           .replace("5, 30, 0, 0\n", "mno, 30, 0, 0\n", 1))
    bad("节点行缺列",
        mct.replace("1, 0, 0, 0\n", "1, 0\n", 1)
           .replace("2, 7.5, 0, 0\n", "2, 7.5\n", 1)
           .replace("3, 15, 0, 0\n", "3, 15\n", 1)
           .replace("4, 22.5, 0, 0\n", "4, 22.5\n", 1)
           .replace("5, 30, 0, 0\n", "5, 30\n", 1))
    bad("单元引用不存在节点",
        mct.replace(", 1, 2, 0, 0, 0", ", 1, 99999, 0, 0, 0"))
    bad("单元类型 3D 专有（PLATE）",
        mct.replace(", BEAM, 1, 1,", ", PLATE, 1, 1,"))
    bad("*SECTION 用 DBUSER",
        mct.replace("1, VALUE, SEC1", "1, DBUSER, SEC1"))
    bad("截面非数值（特性行只 3 个非数值 token）",
        mct.replace("0.478, 0, 0, 0, 0.4147995833, 0", "abc, def, ghi"))
    bad("约束行非 6 位（短码）", mct.replace("111110", "11"))
    bad("约束码含非 01 字符", mct.replace("111110", "11X110"))
    bad("荷载引用不存在节点（dof 越界语义）",
        mct.replace("3, 0, -100000, 0, 0, 0, 0, ",
                    "99999, 0, -100000, 0, 0, 0, 0, "))
    bad("材料缺列（非标准 USER 15 字段）", mct.replace(mat_line, "1, USER, M"))
    bad("重复节点 id",
        mct.replace("2, 7.5, 0, 0\n", "1, 7.5, 0, 0\n", 1))
    bad("重复单元 id", mct.replace("2, BEAM, 1, 1,", "1, BEAM, 1, 1,"))
    bad("缺 *CONSTRAINT 段（无 2D 边界约束）", drop_section(mct, "*CONSTRAINT"))
    bad("缺 *MATERIAL 段（无材料）", drop_section(mct, "*MATERIAL"))

    # 两条如实健壮性：段外行不串段、非 UTF-8 bytes 也走 read_mct_2d。
    check("段外 [OTHER] 行不串段（解析仍 ok）",
          E.read_mct_2d_text(mct + "\n[OTHER]\nX 1 2 3\n").summary()["ok"] is True)
    check("文本编码非 UTF-8 的 bytes 也走 read_mct_2d",
          E.read_mct_2d(mct.encode("gbk")).summary()["ok"] is True)


# ---------------------------------------------------------------- [H] 零污染 / 3D 未动

def group_h() -> None:
    print("\n[H] 默认零污染与 3D 链路未动")
    check("DEFAULT_DAT_SHA 仍是 12 项", len(DEFAULT_DAT_SHA) == 12,
          str(len(DEFAULT_DAT_SHA)))
    for (kind, dim), builder in BUILDERS.items():
        dat = ModelDoc(kind, dict(DEFAULTS[builder]), dimension=dim).to_dat()
        h16 = hashlib.sha256(dat.encode()).hexdigest()[:16]
        check(f"12 格默认 .dat 黄金哈希：{builder}",
              h16 == DEFAULT_DAT_SHA[builder], h16)

    ef = read(ROOT / "export_formats.py")
    check("export_formats.py 仍含未改的 to_canonical 签名",
          "def to_canonical(model: ModelSpec3D) -> tuple[dict[str, Any], list[str]]:" in ef)
    for fn in ("write_ansys_file", "write_abaqus_file", "write_midas_file"):
        check(f"3D {fn} 签名仍是 (model, path)",
              tuple(inspect.signature(getattr(export_formats, fn)).parameters)
              == ("model", "path"))
    mct_src = read(ROOT / "mct_io.py")
    check("mct_io.py 仍含 write_mct / read_mct_text / MctImport",
          all(k in mct_src for k in ("def write_mct(", "def read_mct_text(",
                                     "class MctImport")))
    check("verify_x20_contract.py ASSET_REGISTRY 13 项且含 B5:B / B6:B",
          len(VX.ASSET_REGISTRY) == 13
          and VX.ASSET_REGISTRY.get("B5") == "B"
          and VX.ASSET_REGISTRY.get("B6") == "B")
    check("verify_x20_contract.py 九章 SECTIONS 结构未变",
          len(VX.SECTIONS) == 9
          and all(s.startswith("## %d." % i) for i, s in enumerate(VX.SECTIONS, 1)))

    # 默认 HTML 逐字节 + 不含本轮任何字样。
    r0 = Solve2DResult()
    sp0 = BridgeSpec()
    a = write_2d_html(r0, sp0, OUT / "d0.html").read_bytes()
    b = write_2d_html(r0, sp0, OUT / "d1.html").read_bytes()
    check("默认 2D HTML 两次写出逐字节一致", a == b, f"{len(a)}B")
    html0 = a.decode("utf-8", errors="replace")
    for k in ("plane_frame_2d/v1", "2D 平行导出", "export_2d_formats"):
        check(f"默认 HTML 不含「{k}」", k not in html0)

    # 第十五次：四个业务文件零引用平行链。第十六次起这些文件因 opt-in
    # 2D 压弯 / 轴压链接入而有改动，故不再要求「零改动」，改为锁住更本质
    # 的契约：对它们的任何改动都不得回流第十五次平行链 / 3D 唯一转换点。
    biz = ("bridge_spec.py", "solve_2d.py", "app.py", "export_html.py")
    for f in biz:
        check(f"{f} 源码无 export_2d_formats 引用",
              "export_2d_formats" not in read(ROOT / f))
    r = subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--"] + list(biz),
        capture_output=True, text=True)
    dtxt = r.stdout
    check("四个业务文件的改动不触碰第十五次平行链 / 3D 唯一转换点",
          r.returncode == 0
          and not any(t in dtxt for t in
                      ("export_2d_formats", "to_canonical", "mct_io",
                       "plane_frame_2d/v1", "PlaneMctImport")),
          repr(dtxt[:80]))


# ---------------------------------------------------------------- [I] 契约与两仓文档

def group_i() -> None:
    print("\n[I] 契约与两仓文档对拍（第十五次口径 / 数字无关）")
    contract = read(ROOT / "X20_接入约定.md")
    mat = read(ROOT / "能力边界矩阵.md")
    spec_doc = read(ROOT / "B类后续项接入规格.md")
    rd = read(ROOT / "README.md")
    use = read(ROOT / "使用说明.md")
    guide = read(ROOT / "开发使用手册.md")

    # ---- 契约 §3.4 / B5 / §9 / 九章 / §7 对拍。
    check("契约新增 §3.4 2D 平行链的正交约定",
          "### 3.4 2D 平行链的正交约定（B 档，2026-11-12 第十五次）" in contract)
    check("契约 §3.4 字面量（schema/维度/拒绝/格式占位/铁律 3/组合体系）",
          all(k in contract for k in ("plane_frame_2d/v1", "dimension=2D",
                                      "dimension_not_plane", "格式占位",
                                      "不违反铁律 3", "组合体系", "正交")))
    b5_rows = [ln for ln in contract.splitlines()
               if ln.strip().startswith("|") and "`B5`" in ln]
    b5_cells = ([c.strip() for c in b5_rows[0].strip().strip("|").split("|")]
                if b5_rows else [])
    check("契约 §7 B5 行档位为 B",
          len(b5_rows) == 1 and len(b5_cells) >= 7 and b5_cells[3] == "B",
          b5_cells[3] if len(b5_cells) >= 7 else "行缺失")
    sec9 = contract.split("## 9. 禁止项")[1] if "## 9. 禁止项" in contract else ""
    n_forbid = len([ln for ln in sec9.splitlines() if ln.startswith("- ")])
    check("契约 §9 新增 2D 禁止项且禁止项不少于 7 条",
          "把 2D 平面刚架中间表示" in contract and n_forbid >= 7,
          f"{n_forbid} 条")
    check("契约九章结构不变（§1–§9 齐全、无新增顶层章节）",
          all(s in contract for s in VX.SECTIONS)
          and len(re.findall(r"(?m)^## \d+\.", contract)) == 9)
    rows: dict[str, list[str]] = {}
    for ln in contract.splitlines():
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) >= 7 and re.fullmatch(r"`[ABC]\d`", cells[0]):
            rows[cells[0][1:-1]] = cells
    check("契约 §7 与 ASSET_REGISTRY 逐项对拍（含档位、按登记表长度）",
          len(rows) == len(VX.ASSET_REGISTRY) and set(rows) == set(VX.ASSET_REGISTRY)
          and all(rows[k][3] == g for k, g in VX.ASSET_REGISTRY.items()),
          str(sorted(set(rows) ^ set(VX.ASSET_REGISTRY))))

    # ---- 矩阵 §2 两格精确表格行 + 代码依据列。
    r_export = [ln for ln in mat.splitlines()
                if ln.startswith("| ANSYS / Abaqus / MIDAS 多格式导出")]
    check("矩阵 §2 多格式导出格 | P | Y |",
          len(r_export) == 1 and "| P | Y |" in r_export[0], str(r_export))
    r_import = [ln for ln in mat.splitlines()
                if ln.startswith("| `.mct` 反向导入")]
    check("矩阵 §2 .mct 反向导入格 | P | Y |",
          len(r_import) == 1 and "| P | Y |" in r_import[0], str(r_import))
    check("矩阵代码依据列含 ::to_plane / ::read_mct_2d",
          "export_2d_formats.py::to_plane" in mat and "::read_mct_2d" in mat)
    check("矩阵 §4.2 现为 0 个 2D=N + 9 个 2D=P / B2 剩余 0 项",
          "0 个 `2D=N` + 9 个 `2D=P`" in mat and "B2 剩余 0 项" in mat)
    check("矩阵有第十五次闭环记录段", "第十五次闭环记录" in mat)
    check("矩阵如实边界 P ≠ Y / 不等于 3D / 无 GUI 入口",
          "P ≠ Y" in mat and "不等于 3D" in mat and "无 GUI 入口" in mat)
    check("矩阵如实写明 MIDAS 数值对拍仍未做（非数值对拍）",
          "MIDAS 数值对拍" in mat
          and ("仍未做" in mat or "仍未完成" in mat))
    cloud = [ln for ln in mat.splitlines()
             if ln.startswith("| 3D 活载包络结构云图")]
    check("矩阵云图格仍 | N | Y |（P4 成果层，未计入 9P）",
          len(cloud) == 1 and "| N | Y |" in cloud[0], str(cloud))
    check("矩阵 D 类历史 1N+6P / B2 剩余 2 项仍在",
          "1 个 `2D=N` + 6 个 `2D=P`" in mat and "B2 剩余 2 项" in mat)
    check("矩阵 D 类历史 B2 剩余 1 项 / 仅剩被 X20 仍在",
          "B2 剩余 1 项" in mat and "仅剩被 X20" in mat)

    # ---- B 类规格 §0 / §6 第 13 条 / 未闭环清单 / 带日期历史。
    check("B 类规格 §0 部分实现（十五次逐次接入）",
          "部分实现（十五次逐次接入）" in spec_doc)
    check("B 类规格 §6 第 13 条第十五次",
          "13. 第十五次" in spec_doc)
    check("B 类规格未闭环清单 B2 剩余 0 项 / plane_frame_2d/v1 / 仍 P ≠ Y",
          "B2 剩余 0 项" in spec_doc and "plane_frame_2d/v1" in spec_doc
          and "仍 `P ≠ Y`" in spec_doc)
    hist = "\n".join(spec_doc.splitlines()[:100])
    check("B 类规格带日期历史段仍有「仍被 X20 契约锁死 / 继续写没有」",
          "仍被 **X20 契约**锁死" in hist and "继续写「没有」" in hist
          and bool(re.search(r"20\d\d-\d\d-\d\d", hist)))
    check("B 类规格 D 类历史 1N+6P 仍在",
          "1 个 `2D=N` + 6 个 `2D=P`" in spec_doc)

    # ---- README / 使用说明 / 开发手册（统计数字不写死）。
    check("README 新增第十五次 2D 平行导出 / 导入链章节",
          "### 2D 平行导出 / 导入链（2026-11-12，第十五次）" in rd)
    check("README 第十四次 [OPT2D] 历史章节标题仍在",
          "### 2D 平面索力优化 `[OPT2D]`" in rd)
    check("README 质量保障段存在（不写死脚本数 / 断言数）",
          "## 质量保障闭环（铁律）" in rd)
    check("README 质量保障段按数字无关口径登记",
          "项断言" in rd and "个脚本" in rd)
    check("使用说明新增 ### 8.5", "### 8.5" in use)
    check("开发手册新增 ### 8.x 与登记表 12 项",
          "### 8.x" in guide and "登记表 12 项" in guide)

    # ---- FE 三份文档：本轮零改动（X20 / 多格式导出 / .mct 零命中）。
    for name in ("CHANGELOG.md", "USER_MANUAL.md", "input_format.md"):
        t = read(FE / name)
        check(f"FE {name} 仍不含 X20 / 多格式导出 / .mct 相关段落",
              bool(t) and all(k not in t for k in
                              ("X20", "多格式导出", ".mct", "plane_frame_2d",
                               "export_2d_formats", "第十五次", "反向导入")))
    fe_log = read(FE / "CHANGELOG.md")
    check("FE CHANGELOG ## 2026-11-12 与第十三次历史句仍在",
          "## 2026-11-12" in fe_log and "第十三次" in fe_log
          and "仍不做" in fe_log)


# ---------------------------------------------------------------- [J] 汇总不许假绿

def group_j() -> None:
    print("\n[J] 汇总统计不许「假绿」")
    self_src = read(ROOT / "verify_2d_export_import.py")
    check("本守卫用 PASS.append((name, bool(ok), str(detail))) 按布尔位登记",
          "PASS.append((name, bool(ok), str(detail)))" in self_src)
    check("本守卫用 sum(1 for _, ok, _ in PASS if ok) 汇总",
          "sum(1 for _, ok, _ in PASS if ok)" in self_src)
    vxsrc = read(ROOT / "verify_x20_contract.py")
    check("verify_x20_contract.py::plane_chain() 存在",
          "def plane_chain(" in vxsrc and hasattr(VX, "plane_chain"))
    body = vxsrc.split("def plane_chain(")[1].split("def main(")[0]
    check("plane_chain() 走 check() 逐条登记", "check(" in body)
    check("ASSET_REGISTRY 13 项且含 B5:B / B6:B",
          len(VX.ASSET_REGISTRY) == 13
          and VX.ASSET_REGISTRY.get("B5") == "B"
          and VX.ASSET_REGISTRY.get("B6") == "B")
    check("verify_x20_contract.py 末尾仍打印 N/N 通过（按布尔位统计）",
          "通过" in vxsrc and "{n_pass}/{len(RESULTS)}" in vxsrc
          and "sum(1 for ok, _, _ in RESULTS if ok)" in vxsrc)
    check("仓库根不残留 .mct / .ans / .inp 临时文件",
          not list(ROOT.glob("*.mct")) and not list(ROOT.glob("*.ans"))
          and not list(ROOT.glob("*.inp")))


def main() -> int:
    print("=" * 78)
    print("2D 平行导出 / 导入链守卫（B2 第十五次，纯离线 + out/ 临时实跑）")
    print("=" * 78)
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        group_a()
        group_b()
        group_c()
        group_d()
        group_e()
        group_f()
        group_g()
        group_h()
        group_i()
        group_j()
    finally:
        shutil.rmtree(OUT, ignore_errors=True)
    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(PASS)} 通过")
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
