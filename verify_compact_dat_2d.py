"""第十七次接入守卫：2D `.dat` opt-in 紧凑导出（*FILL / *DO）。

九组断言：

1. 新模块源码契约（独立 2D 压缩器、不 import 3D / X20 canonical、纯文本层）
2. FE 内核语法证据（fem_2d.hpp / input_format.md，2D 侧零改动）
3. 严格可逆 oracle（漂移段退回逐行、可压段、索引表达式白名单）
4. 参考展开器与 C++ 预处理语义对拍
5. 5 个 2D 建造器全模型对拍（宏生效 / 行数更少 / 解析后逐字段相同）
6. 默认零污染（12 格 DEFAULT_DAT_SHA / 3D 分支 / 单梁出口）
7. 双层 opt-in 接线（BridgeSpec → ModelDoc → solve_2d → GUI）
8. 真跑 FE 内核对拍（逐行版 / 紧凑版 .result 逐字节相同 + 四出口同源）
9. 两仓文档对拍 + 不许假绿

汇总按布尔位统计，任何一条红都 rc=1。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from console_utf8 import setup_console

import bridge_spec
import compact_dat_2d as c
import model_doc
import report_2d
import solve_2d
from verify_stage_tension_3d import DEFAULT_DAT_SHA

ROOT = Path(__file__).parent
FE_ROOT = ROOT.parent / "FEM-Eigen"
PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, bool(ok), detail))
    if not ok:
        print(f"  FAIL  {name}  {detail}", flush=True)


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def fe_read(name: str) -> str:
    return (FE_ROOT / name).read_text(encoding="utf-8")


def two_d_builders() -> list[tuple[str, str, str]]:
    """(kind, dim, builder)：2D 桥型主链，单梁 spec_to_dat 不在覆盖范围。"""
    return [(kind, dim, b) for (kind, dim), b in model_doc.BUILDERS.items()
            if dim == "2d" and kind != "beam"]


# ---------------------------------------------------------------- [1]

def group_source() -> None:
    print("\n[1] 新模块源码契约")
    src = read("compact_dat_2d.py")
    for token in ("SOURCE = ", 'BASIS = "2d_dat_compact_optin"',
                  "FILL_TOL = 1e-9", "MIN_FILL_POINTS = 3",
                  "MIN_LOOP_ITEMS = 3", "DIAGNOSTIC = ", "BOUNDARY_NOTE = ",
                  "def node_line(", "def elem_line(",
                  "def substitute_loop_var(", "def expand_lines(",
                  "def parse_nodes(", "def parse_elements(",
                  "def equivalent(", "def fill_reversible(",
                  "def loop_reversible(", "def compress_nodes(",
                  "def fit_linear(", "def expr_2d(",
                  "def compress_elements("):
        check(f"新模块含 {token.rstrip(' (= ')}", token in src)
    import_lines = [ln.strip() for ln in src.splitlines()
                    if ln.strip().startswith(("import ", "from "))]
    for banned in ("model_spec_3d", "export_formats", "to_canonical",
                   "mct_io", "bridge_fe_modeler"):
        check(f"2D 压缩器不 import {banned}",
              not any(banned in ln for ln in import_lines))
    for banned in ("subprocess", "write_text(", "shutil", "socket"):
        check(f"2D 压缩器纯文本层、不含 {banned}", banned not in src)
    check("模块写明可逆性自检与漂移数值依据",
          "3.333333" in src and "可逆性自检" in src and "setprecision(15)" in src)
    check("模块写明 2D 表达式白名单与不支持嵌套",
          "VAR±m" in src and "不支持括号" in src)


# ---------------------------------------------------------------- [2]

def group_kernel_syntax() -> None:
    print("\n[2] FE 内核语法证据（2D 侧零改动）")
    hpp = fe_read("fem_2d.hpp")
    fmt = fe_read("input_format.md")
    for token in ("substituteLoopVar", "expandLines", "preprocess",
                  "*FILL", "*ENDDO", "setprecision(15)", "nodeCoord",
                  'rfind("*DO"', "*DO 步长不能为 0",
                  "*FILL 要求第二个节点号大于第一个"):
        check(f"fem_2d.hpp 含 {token}", token in hpp)
    for token in ("## 2. 预处理宏", "### 2.1 `*FILL`", "*DO VAR start end [step]",
                  "`I+k`、`I-k`", "`k*I+m`、`k*I-m`",
                  "不支持**嵌套循环**", "循环体内不要使用 `*FILL`"):
        check(f"input_format.md 含 {token}", token in fmt)
    check("内核 *FILL 用端点线性插值且写出 15 位精度",
          "x1 + f * (x2 - x1)" in hpp or "(x1 + f * (x2 - x1))" in hpp)
    check("内核 *DO 的 end 包含、step 缺省为 1",
          "if (!(ss >> step)) step = 1" in hpp and "v <= endv" in hpp)


# ---------------------------------------------------------------- [3]

def group_reversible_oracle() -> None:
    print("\n[3] 严格可逆 oracle")
    for n, expect_fill in ((3, False), (4, True), (6, True), (8, True)):
        nodes = [(i + 1, 10.0 * i / n, 0.0) for i in range(n + 1)]
        lines = c.compress_nodes(nodes)
        has_fill = any(ln.startswith("*FILL") for ln in lines)
        full = "*NODE\n" + "\n".join(c.node_line(x) for x in nodes)
        comp = "*NODE\n" + "\n".join(lines)
        ok, msg = c.equivalent(full, comp)
        check(f"0→10 分 {n} 段：是否压 = {expect_fill}",
              has_fill == expect_fill, f"fill={has_fill} lines={lines}")
        check(f"0→10 分 {n} 段：展开后模型等价", ok, msg)
        # 整段 fill_reversible 未必等于「有没有压」：n=6 时步长切分会把
        # 连续段拆成多个子段，部分可压、部分退回逐行。逐个子段核自检更本质。
        fills = [ln for ln in lines if ln.startswith("*FILL ")]
        sub_ok = True
        for ln in fills:
            _, sa, sb = ln.split()
            a, b = int(sa), int(sb)
            seg = [x for x in nodes if a <= x[0] <= b]
            sub_ok = sub_ok and c.fill_reversible(seg)
        check(f"0→10 分 {n} 段：每个 *FILL 子段都过可逆性自检",
              sub_ok, str(fills))
    # 漂移量级：这是本轮不能照抄 3D 容差的数值依据。
    drift = abs(float(f"{10.0/3:.15g}") - float(f"{10.0/3:.6f}"))
    check("0→10 分 3 段插值漂移约 3.3e-7 m（远大于 1e-9）",
          1e-9 < drift < 1e-6, f"drift={drift:.6g}")
    check("n=4 压成首末两行 + *FILL 1 5",
          c.compress_nodes([(i + 1, 2.5 * i, 0.0) for i in range(5)])
          == ["1 0.000000 0.000000", "5 10.000000 0.000000", "*FILL 1 5"])
    # 抛物线节点：x 等距但 y 二阶差分不为零，任何 3 点子段都不等距。
    curve = [(i + 1, 5.0 * i, 0.3 * i * i) for i in range(6)]
    check("曲线 / 非等距段不压 *FILL",
          not any(ln.startswith("*FILL") for ln in c.compress_nodes(curve)))

    elem_cases = (
        ([(i, i, i + 1) for i in range(1, 9)], "*EN,I,I,I+1"),
        ([(100 + i, i, 200 + i) for i in range(1, 7)], "*EN,I,I-100,I+100"),
        ([(i, 1, 2) for i in range(1, 6)], "*EN,I,1,2"),
    )
    for items, body in elem_cases:
        lines = c.compress_elements(items)
        full = "\n".join(c.elem_line(x) for x in items)
        comp = "\n".join(lines)
        check(f"单元 {len(items)} 个压成 *DO",
              len(lines) == 3 and lines[0].startswith("*DO I ")
              and lines[1] == body and lines[2] == "*ENDDO", str(lines))
        check(f"单元 {len(items)} 个展开后逐字段相同",
              c.parse_elements(c.expand_lines(full))
              == c.parse_elements(c.expand_lines(comp)))
    odd = [(1, 1, 2), (3, 3, 4), (4, 4, 5), (5, 5, 6)]
    lines = c.compress_elements(odd)
    check("不连续 id 段先逐行、后续连续段才压",
          lines[0] == "*EN,1,1,2" and lines[1] == "*DO I 3 5", str(lines))
    check("expr_2d 只生成四种白名单形式",
          c.expr_2d(1, 0) == "I" and c.expr_2d(1, 3) == "I+3"
          and c.expr_2d(1, -3) == "I-3" and c.expr_2d(2, 0) == "2*I"
          and c.expr_2d(2, 5) == "2*I+5" and c.expr_2d(2, -5) == "2*I-5"
          and c.expr_2d(0, 7) == "7")
    check("expr_2d 拒绝负系数与负常数节点号",
          c.expr_2d(-1, 0) is None and c.expr_2d(0, 0) is None
          and c.expr_2d(0, -7) is None)
    check("fit_linear 要求每点严格命中",
          c.fit_linear([1, 2, 4], [1, 2, 3]) is None
          and c.fit_linear([1, 2, 3], [1, 2, 3]) == (1, 0)
          and c.fit_linear([5, 4, 3], [1, 2, 3]) is None)


# ---------------------------------------------------------------- [4]

def group_expander() -> None:
    print("\n[4] 参考展开器与 C++ 预处理语义对拍")
    check("substituteLoopVar 四种形式一次替换",
          c.substitute_loop_var("*EN,I,I+1,2*I-3", "I", 5) == "*EN,5,6,7")
    check("循环变量名大小写不敏感",
          c.substitute_loop_var("*EN,I,I+1,2*I-3", "i", 5) == "*EN,5,6,7")
    check("标识符中的 I 不被误替换",
          c.substitute_loop_var("*EN,I,ISTRN,I+1", "I", 4) == "*EN,4,ISTRN,5")
    check("*FILL 按端点线性插值生成中间点",
          c.expand_lines("*NODE\n1 0 0\n3 10 0\n*FILL 1 3")
          == ["*NODE", "1 0 0", "3 10 0", "2 5 0"])
    check("*DO 的 step 生效且 end 包含",
          c.parse_elements(c.expand_lines(
              "*DO I 1 5 2\n*EN,I,I,I+1\n*ENDDO"))
          == [(1, 1, 2), (3, 3, 4), (5, 5, 6)])
    for bad, label in (("*NODE\n1 0 0\n*FILL 1 3", "端点缺失"),
                       ("*NODE\n3 10 0\n1 0 0\n*FILL 3 1", "b<=a"),
                       ("*DO I 1 3\n*EN,I,I,I+1", "缺 *ENDDO"),
                       ("*DO I 1 3 0\n*EN,I,I,I+1\n*ENDDO", "step=0")):
        raised = False
        try:
            c.expand_lines(bad)
        except ValueError:
            raised = True
        check(f"内核语义：{label} 抛错", raised)
    ok, msg = c.equivalent(
        "*NODE\n1 0.000000 0.000000\n2 3.333333 0.000000\n"
        "3 6.666667 0.000000\n4 10.000000 0.000000",
        "*NODE\n1 0.000000 0.000000\n4 10.000000 0.000000\n*FILL 1 4")
    check("equivalent 能识破漂移的伪紧凑文本", not ok, msg)
    check("parse_nodes 只取 *NODE 段",
          c.parse_nodes(c.expand_lines("*NODE\n1 1 2\n*ET\n3 3 4"))
          == {1: (1.0, 2.0)})
    check("consecutive_runs 切分连续段",
          c.consecutive_runs([1, 2, 3, 7, 8, 20]) == [(1, 3), (7, 8), (20, 20)])


# ---------------------------------------------------------------- [5]

def group_full_models() -> None:
    print("\n[5] 5 个 2D 建造器全模型对拍")
    cases = two_d_builders()
    check("2D 桥型主链共 5 个建造器", len(cases) == 5, str([x[2] for x in cases]))
    for kind, dim, builder in cases:
        spec = model_doc.ModelDoc(
            kind, dict(model_doc.DEFAULTS[builder]), dimension=dim).build().model
        full = spec.to_dat()
        comp = spec.to_dat(compact=True)
        check(f"{builder}：默认逐行版无宏",
              "*FILL " not in full and "*DO I" not in full and "*ENDDO" not in full)
        check(f"{builder}：紧凑版含 *FILL 与 *DO",
              "*FILL " in comp and "*DO I" in comp and "*ENDDO" in comp)
        check(f"{builder}：紧凑版行数更少",
              len(comp.splitlines()) < len(full.splitlines()),
              f"{len(full.splitlines())}→{len(comp.splitlines())}")
        ok, msg = c.equivalent(full, comp)
        check(f"{builder}：展开后节点/单元逐字段相同", ok, msg)
        check(f"{builder}：节点与单元数不变",
              len(c.parse_nodes(c.expand_lines(comp))) == len(spec.nodes)
              and len(c.parse_elements(c.expand_lines(comp))) == len(spec.elements))
        check(f"{builder}：紧凑注释说明内核可读回",
              "紧凑写法" in comp and "可逆性自检" in comp)
    # arch 拱肋是曲线点：101..109 段绝不能被 *FILL 压掉。
    arch = model_doc.ModelDoc(
        "arch", dict(model_doc.DEFAULTS["arch"]), dimension="2d").build().model
    comp = arch.to_dat(compact=True)
    check("arch 拱肋曲线段不写 *FILL 101 109", "*FILL 101 109" not in comp)
    check("arch 拱肋坐标仍逐行写出", "101 " in comp and "109 " in comp)


# ---------------------------------------------------------------- [6]

def group_zero_pollution() -> None:
    print("\n[6] 默认零污染")
    for (kind, dim), builder in model_doc.BUILDERS.items():
        dat = model_doc.ModelDoc(
            kind, dict(model_doc.DEFAULTS[builder]), dimension=dim).to_dat()
        h = hashlib.sha256(dat.encode()).hexdigest()[:16]
        check(f"默认 .dat 黄金哈希：{builder}",
              h == DEFAULT_DAT_SHA[builder], f"{h} / {DEFAULT_DAT_SHA[builder]}")
    check("DEFAULT_DAT_SHA 仍是 12 项", len(DEFAULT_DAT_SHA) == 12,
          str(len(DEFAULT_DAT_SHA)))
    for (kind, dim), builder in model_doc.BUILDERS.items():
        if dim != "3d":
            continue
        doc = model_doc.ModelDoc(
            kind, dict(model_doc.DEFAULTS[builder]), dimension=dim)
        check(f"3D {builder}：compact_2d 开关不改变 to_dat",
              doc.to_dat() == doc.to_dat(compact_2d=True))
    beam_dat = model_doc.ModelDoc(
        "beam", dict(model_doc.DEFAULTS["beam"]), dimension="2d").to_dat()
    check("单梁 spec_to_dat 本轮未覆盖、仍逐行",
          "*FILL " not in beam_dat and "*DO I" not in beam_dat)
    src = read("fem_eigen_export.py")
    check("spec_to_dat 源码未改出宏", "*FILL" not in src and "*DO I" not in src)
    src = read("solve_2d.py")
    check("solve_2d 的 compact_dat 默认 False", "compact_dat: bool = False" in src)
    check("FE 仓 3D 紧凑链签名保持 compact=True",
          "def to_dat(self, compact: bool = True) -> str:"
          in read("model_spec_3d.py"))


# ---------------------------------------------------------------- [7]

def group_wiring() -> None:
    print("\n[7] 双层 opt-in 接线")
    bs = read("bridge_spec.py")
    md = read("model_doc.py")
    s2 = read("solve_2d.py")
    app = read("app.py")
    for token in ("import compact_dat_2d", "def to_dat(self, compact: bool = False)",
                  "compact_dat_2d.compress_nodes(", "compact_dat_2d.compress_elements(",
                  "spec.to_dat(compact="):
        check(f"BridgeSpec / solve_2d 接线含 {token}",
              token in bs or token in s2)
    check("BridgeSpec 默认分支逐行写出不变",
          'for n in self.nodes:\n                out.append(f"{n.id} {n.x:.6f} {n.y:.6f}")' in bs)
    for token in ("def to_dat(self, compact_2d: bool = False)",
                  "def solve(self, out_dir:", "compact_2d_dat: bool = False",
                  "return self.model.to_dat()", "compact=bool(compact_2d)",
                  "compact_dat=bool(compact_2d_dat)"):
        check(f"ModelDoc 接线含 {token}", token in md)
    for token in ("compact_dat: bool = False", "compact_dat_2d.DIAGNOSTIC",
                  "spec.to_dat(compact=bool(compact_dat))"):
        check(f"solve_2d 接线含 {token}", token in s2)
    for token in ("key=\"compact2ddat_opt\"", "compact_2d_dat=_compact2d_on",
                  '"compact2ddat": _compact2d_on',
                  'bool(_s2d.get("compact2ddat", False)) != _compact2d_on',
                  "下载本次写盘的 .dat"):
        check(f"GUI 接线含 {token}", token in app)
    check("默认关闭时 solve_2d 不追加紧凑口径说明",
          "if compact_dat:" in s2 and "res.diagnostics.append(" in s2)
    check("app.py 下载按钮取 res.dat_text（写盘同源）",
          'getattr(_r2d, "dat_text", "")' in app)


# ---------------------------------------------------------------- [8]

def group_kernel_run() -> None:
    print("\n[8] 真跑 FE 内核对拍")
    out = ROOT / "out" / "compact17_guard"
    spec = bridge_spec.build_beam_bridge(
        span=20.0, n_spans=1, n_elem=8, udl_kN=10.0,
        staged=False, lane=False)
    r0 = solve_2d.solve_2d(spec, out_dir=out, dat_name="full.dat")
    r1 = solve_2d.solve_2d(spec, out_dir=out, dat_name="compact.dat",
                           compact_dat=True)
    p0, p1 = out / "full.result", out / "compact.result"
    check("两份 .result 都由内核写出", p0.is_file() and p1.is_file())
    if p0.is_file() and p1.is_file():
        b0, b1 = p0.read_bytes(), p1.read_bytes()
        check("逐行版与紧凑版 .result 逐字节相同", b0 == b1,
              f"{len(b0)} / {len(b1)} 字节")
        check("两份 .result 非空", len(b0) > 0)
    check("final_beam 完全相同", r0.final_beam == r1.final_beam)
    check("final_disp 完全相同", r0.final_disp == r1.final_disp)
    check("逐行版 dat_text 无宏、紧凑版有宏",
          "*FILL " not in r0.dat_text and "*DO I" not in r0.dat_text
          and "*FILL " in r1.dat_text and "*DO I" in r1.dat_text)
    check("指纹按写盘文本计算，两版不同且如实",
          r0.fingerprint != r1.fingerprint
          and r0.fingerprint == hashlib.sha1(
              r0.dat_text.encode("utf-8")).hexdigest()[:12]
          and r1.fingerprint == hashlib.sha1(
              r1.dat_text.encode("utf-8")).hexdigest()[:12])
    check("紧凑口径只在打开时进 diagnostics",
          c.DIAGNOSTIC not in r0.diagnostics and c.DIAGNOSTIC in r1.diagnostics)
    doc0 = report_2d.build_2d_document(r0, spec)
    doc1 = report_2d.build_2d_document(r1, spec)
    check("四出口同源：Report.diagnostics 同步带上口径说明",
          c.DIAGNOSTIC not in doc0.diagnostics
          and c.DIAGNOSTIC in doc1.diagnostics)
    check("默认求解的 Report 指纹与逐行版一致",
          doc0.fingerprint == r0.fingerprint)
    check("紧凑版与逐行版解析出同一模型",
          c.equivalent(r0.dat_text, r1.dat_text)[0])


# ---------------------------------------------------------------- [9]

def group_docs() -> None:
    print("\n[9] 两仓文档对拍 + 不许假绿")
    needs = {
        "README.md": ("第十七次", "compact_dat_2d.py", "可逆性自检",
                      "*FILL", "*DO"),
        "使用说明.md": ("compact_dat_2d.py", "verify_compact_dat_2d.py"),
        "开发使用手册.md": ("第十七次", "compact_dat_2d.py",
                           "verify_compact_dat_2d.py"),
        "能力边界矩阵.md": ("第十七次", "compact_dat_2d.py",
                           "不是新力学能力", "P ≠ Y"),
        "X20_接入约定.md": ("`B6`", "compact_dat_2d.py", "第十七次"),
    }
    for name, tokens in needs.items():
        text = read(name)
        for token in tokens:
            check(f"{name} 写明 {token}", token in text)
    contract = read("verify_x20_contract.py")
    check("ASSET_REGISTRY 登记 B6 且档位 B", '"B6": "B"' in contract)
    check("X20 §7 登记表含 B6 行",
          any(ln.strip().startswith("|") and "`B6`" in ln
              for ln in read("X20_接入约定.md").splitlines()))
    check("§3.4 仍写明 2D 与 3D 正交、互不冒充",
          "正交、平行、互不冒充" in read("X20_接入约定.md"))
    check("FE 仓 input_format.md 本轮未被改出 3D 表达式",
          "不支持**嵌套循环**" in fe_read("input_format.md"))
    src = read("verify_compact_dat_2d.py")
    tail = src.rsplit("def main() -> int:", 1)[1]
    check("汇总按布尔位统计", "sum(1 for _, ok, _ in PASS if ok)" in tail)
    check("红项返回 rc=1", "return 0 if n_pass == len(PASS) else 1" in tail)
    check("不用常量真值假绿", ("or" + " True") not in tail
          and ("check(" in src and "bool(ok)" in src))
    check("断言明细随 rc 一起输出", "for n, ok, d in PASS" in tail)


def main() -> int:
    setup_console()
    print("第十七次：2D .dat opt-in 紧凑导出（*FILL / *DO）守卫")
    group_source()
    group_kernel_syntax()
    group_reversible_oracle()
    group_expander()
    group_full_models()
    group_zero_pollution()
    group_wiring()
    group_kernel_run()
    group_docs()
    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print(f"\n  红项明细（共 {len(PASS) - n_pass} 条）：")
    for n, ok, d in PASS:
        if not ok:
            print(f"    FAIL  {n}  {d[:160]}")
    print(f"  {n_pass}/{len(PASS)} 通过")
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
