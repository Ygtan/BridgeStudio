"""能力边界矩阵一致性守卫（纯离线）。

`能力边界矩阵.md` 是能力口径的唯一可核对来源；本脚本把里面每一格
拉回代码对拍，防止「文档写有但代码没有」「代码有但文档漏记」两类漂移。

四类检查：
  1. §1 主矩阵 12 格 ↔ `model_doc.BUILDERS` / `SOLVER_OF` / `DEFAULTS`；
  2. §2 维度级能力的 `文件::符号` 依据真实存在；
  3. §3 参数覆盖契约：DEFAULTS ⊆ FIELDS ∪ 白名单、额外控件带 default、
     默认值落在区间内、带 format 的控件 printf 往返不丢精度；
  4. 控件渲染写回 params 后，12 个建造器的默认 `.dat` 逐位不变。

不调求解器、不写 `.dat`、不依赖归档原型目录。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import model_doc as md

DOC = ROOT / "能力边界矩阵.md"
RESULTS: list[tuple[bool, str, str]] = []
MARKS = ("<!-- MATRIX:MAIN -->", "<!-- MATRIX:CAP -->",
         "<!-- MATRIX:WHITELIST -->", "<!-- MATRIX:COVERAGE -->",
         "<!-- MATRIX:EFFECT -->")


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((bool(ok), name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def table(mark: str) -> tuple[list[str], list[list[str]]]:
    """取标记后紧邻的第一张 Markdown 表：返回 (表头, 数据行)。"""
    text = read(DOC)
    if mark not in text:
        return [], []
    rows: list[list[str]] = []
    started = False
    for ln in text.split(mark, 1)[1].splitlines():
        s = ln.strip()
        if s.startswith("|"):
            started = True
            rows.append([c.strip() for c in s.strip("|").split("|")])
        elif started and s:
            break
    if len(rows) < 2:
        return (rows[0] if rows else []), []
    return rows[0], rows[2:]


def field_keys(builder: str) -> set[str]:
    return {f["key"] for row in md.FIELDS[builder] for f in row}


def doc_structure() -> None:
    text = read(DOC)
    check("矩阵文档存在", DOC.is_file())
    for i in range(7):
        check(f"§{i} 章节存在", f"## {i}." in text)
    for m in MARKS:
        check(f"表标记 {m[5:-4].strip()}", m in text)
    check("文档写明不静默兜底", "不静默兜底" in text)
    check("文档写明默认 .dat 纪律", "默认路径纪律" in text or "逐位不变" in text)


def main_matrix() -> dict[str, tuple[str, str]]:
    hdr, rows = table(MARKS[0])
    check("§1 表头 9 列", len(hdr) == 9, str(len(hdr)))
    check("§1 主矩阵 12 行", len(rows) == 12, str(len(rows)))
    out: dict[str, tuple[str, str]] = {}
    seen: set[tuple[str, str]] = set()
    for r in rows:
        if len(r) != 9:
            check("§1 行列数", False, str(r))
            continue
        kind, dim, builder, module, solver, stage, lane, ps, cover = r
        key = (kind, dim)
        out[builder] = key
        seen.add(key)
        ok = (key in md.BUILDERS
              and md.BUILDERS.get(key) == builder
              and md.SOLVER_OF.get(key) == solver
              and (ROOT / module).is_file())
        check(f"§1 {kind}/{dim} 对拍 BUILDERS/SOLVER_OF/模块", ok,
              f"{builder} → {solver} @ {module}")
        for label, cell in (("阶段", stage), ("活载", lane), ("预应力", ps)):
            if cell == "—":
                continue
            check(f"§1 {builder} {label}参数 {cell} 真在 DEFAULTS",
                  builder in md.DEFAULTS and cell in md.DEFAULTS[builder])
        check(f"§1 {builder} 参数覆盖标 Y", cover == "Y", cover)
    check("§1 12 格恰好覆盖 BUILDERS 全部键", seen == set(md.BUILDERS),
          f"{len(seen)}/{len(md.BUILDERS)}")
    return out


def dimension_caps() -> None:
    hdr, rows = table(MARKS[1])
    check("§2 表头 4 列", len(hdr) == 4, str(len(hdr)))
    check("§2 能力行不少于 15", len(rows) >= 15, str(len(rows)))
    ref_re = re.compile(r"`([^`]+)`")
    for r in rows:
        if len(r) != 4:
            check("§2 行列数", False, str(r))
            continue
        cap, d2, d3, refs = r
        check(f"§2 {cap} 记号合法",
              d2 in ("Y", "P", "N") and d3 in ("Y", "P", "N"),
              f"2D={d2} 3D={d3}")
        frag = ref_re.findall(refs)
        bad: list[str] = []
        for f in frag:
            if "::" not in f:
                bad.append(f + "(缺 ::)")
                continue
            file, sym = f.split("::", 1)
            p = ROOT / file
            if not p.is_file():
                bad.append(f + "(文件缺)")
            elif sym not in read(p):
                bad.append(f + "(符号缺)")
        check(f"§2 {cap} 代码依据可核对", bool(frag) and not bad,
              ", ".join(bad) if bad else f"{len(frag)} 处")


def whitelist() -> dict[str, set[str]]:
    hdr, rows = table(MARKS[2])
    check("§3 白名单表头 3 列", len(hdr) == 3, str(len(hdr)))
    out: dict[str, set[str]] = {}
    for r in rows:
        if len(r) != 3:
            check("§3 白名单行列数", False, str(r))
            continue
        b, k, why = r
        ok = (b in md.DEFAULTS and k in md.DEFAULTS[b]
              and k not in field_keys(b) and len(why) >= 8)
        check(f"§3 白名单 {b}.{k} 成立且带理由", ok, why[:24])
        out.setdefault(b, set()).add(k)
    return out


def coverage(white: dict[str, set[str]]) -> None:
    for b, defs in md.DEFAULTS.items():
        fk = field_keys(b)
        hidden = set(defs) - fk
        check(f"§3 {b} DEFAULTS ⊆ FIELDS ∪ 白名单",
              hidden == white.get(b, set()),
              f"隐藏 {sorted(hidden)}")
        extra = [f for row in md.FIELDS[b] for f in row if f["key"] not in defs]
        check(f"§3 {b} 额外控件都带 default",
              all("default" in f for f in extra),
              f"{len(extra)} 个")
        bad_range = []
        bad_round = []
        for row in md.FIELDS[b]:
            for f in row:
                k = f["key"]
                if k not in defs or f["type"] not in ("float", "int"):
                    continue
                v = defs[k]
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    continue
                if not (f["min"] <= v <= f["max"]):
                    bad_range.append(k)
                if "format" in f and f["type"] == "float":
                    try:
                        v2 = float(f["format"] % float(v))
                    except (TypeError, ValueError):
                        v2 = float("nan")
                    if not math.isclose(float(v), v2, rel_tol=1e-9, abs_tol=1e-15):
                        bad_round.append(k)
        check(f"§3 {b} 默认值都在控件区间内", not bad_range, str(bad_range))
        check(f"§3 {b} 带 format 控件 printf 往返不丢精度", not bad_round,
              str(bad_round))
    hdr, rows = table(MARKS[3])
    check("§3 覆盖统计表头 4 列", len(hdr) == 4, str(len(hdr)))
    check("§3 覆盖统计 12 行", len(rows) == 12, str(len(rows)))
    for r in rows:
        if len(r) != 4:
            check("§3 覆盖统计行列数", False, str(r))
            continue
        b, nd, nf, nw = r
        real = (len(md.DEFAULTS[b]), len(field_keys(b)), len(white.get(b, set())))
        check(f"§3 {b} 覆盖数字对拍", (int(nd), int(nf), int(nw)) == real,
              f"文档 {nd}/{nf}/{nw} 实测 {real}")


def rendered_params(builder: str) -> dict:
    """复刻 app._render_field 的写回行为（含 choice/bool/text/float 的往返）。"""
    p = dict(md.DEFAULTS[builder])
    for row in md.FIELDS[builder]:
        for f in row:
            k, t = f["key"], f["type"]
            # param_rows() 的 when 过滤必须复刻：beam 的 tw/tf 只在工字形 /
            # 箱形截面显示，默认矩形截面下它们是 None，不能被顶到 min。
            w = f.get("when")
            if w is not None and not w(p):
                continue
            raw = p.get(k)
            if t == "float":
                p[k] = float(raw) if raw is not None else float(
                    f.get("default", f["min"]))
            elif t == "int":
                p[k] = int(raw) if raw is not None else int(
                    f.get("default", f["min"]))
            elif t == "choice":
                opts = f["choices"]
                p[k] = opts[opts.index(raw)] if raw in opts else opts[0]
            elif t == "bool":
                p[k] = bool(raw)
            elif t == "text":
                p[k] = str(raw or f.get("default", "") or "")
    return p


def dat_invariant(builder_map: dict[str, tuple[str, str]]) -> None:
    for b, (kind, dim) in builder_map.items():
        try:
            a = md.ModelDoc(kind, dict(md.DEFAULTS[b]), dimension=dim).to_dat()
            c = md.ModelDoc(kind, rendered_params(b), dimension=dim).to_dat()
            check(f"§3 {b} 控件渲染后默认 .dat 逐位不变", a == c,
                  f"{len(a)} 字节")
        except Exception as e:                       # noqa: BLE001 - 诊断用
            check(f"§3 {b} 控件渲染后默认 .dat 逐位不变", False,
                  f"{type(e).__name__}: {e}")


def live_effect(builder_map: dict[str, tuple[str, str]]) -> None:
    """§3.1：新补控件不是摆设——改值必须真的影响模型 .dat。"""
    hdr, rows = table(MARKS[4])
    check("§3.1 生效性表头 4 列", len(hdr) == 4, str(len(hdr)))
    check("§3.1 生效性 6 行", len(rows) == 6, str(len(rows)))
    for r in rows:
        if len(r) != 4:
            check("§3.1 生效性行列数", False, str(r))
            continue
        b, key, raw, why = r
        try:
            val: object = float(raw)
        except ValueError:
            val = raw
        ok = (b in md.DEFAULTS and key in md.DEFAULTS[b]
              and key in field_keys(b) and val != md.DEFAULTS[b][key])
        if ok:
            kind, dim = builder_map[b]
            p0 = dict(md.DEFAULTS[b])
            p1 = dict(p0)
            p1[key] = val
            try:
                ok = (md.ModelDoc(kind, p0, dimension=dim).to_dat()
                      != md.ModelDoc(kind, p1, dimension=dim).to_dat())
            except Exception as e:                   # noqa: BLE001 - 诊断用
                ok = False
                why = f"{type(e).__name__}: {e}"
        check(f"§3.1 {b}.{key} 改值后 .dat 确实变化", ok, why[:28])


def gaps_and_process() -> None:
    text = read(DOC)
    for i, kw in enumerate(("组合体系桥型没有独立建造器", "2D ↔ 3D 高级能力不对齐",
                            "P4 成果层"), 1):
        check(f"§4 缺口 {i} 有明确条目", kw in text)
    check("§4 记录界面 PDF 记载纠正（不是删断言假装没发生过）",
          "界面 PDF 默认关闭" in text and "纠正错误记载" in text
          and "4 条减为 3 条" in text and "write_pdf" in text)
    ui_src = read(ROOT / "ui_3d.py")
    check("界面 PDF 走三件套 + 单独打印且失败有中文降级",
          "write_all(rep, _out, stem=_stem, pdf=False)" in ui_src
          and 'paths["pdf"] = write_pdf(' in ui_src
          and "PDF 计算书未生成" in ui_src and "pdf_error" in ui_src)
    check("§4 记录编号容量闭环（不靠删断言假装没发生过）",
          "梁格编号容量限制" in text and "缺口由 5 条减为 4 条" in text
          and "DEFAULT_DAT_SHA" in text and "wide_numbering()" in text)
    check("§2 登记 3D 活载包络结构云图能力",
          "3D 活载包络结构云图" in text)
    check("§4 记录 3D 包络云图闭环且 MIDAS 对拍仍欠",
          "结构云图已闭环" in text and "verify_envelope_3d.py" in text
          and "MIDAS 数值对拍" in text and "仍未完成" in text)
    check("§5 写明新增桥型/能力/参数流程", "## 5. 维护流程" in text
          and "新增 / 修改桥型" in text and "白名单写理由" in text)
    check("§3.1 写明有控件≠能改 .dat 的边界",
          "有控件 ≠ 一定能改变" in text and "约束压制" in text)
    check("§6 明确路线图对应且仍是未完成",
          "仍未完成" in text and "P1 X20" in text and "P4 成果层" in text)
    check("§6 写明契约 ≠ 全量移植", "不等于" in text and "全量移植" in text)
    # 2026-11-10：2D 模态 / 屈曲两格按 FEM-Eigen 源码事实接通，防止文档漂移回 N。
    cap_rows = [ln for ln in text.splitlines()
                if ln.startswith("| 模态与自振特性 |")
                or ln.startswith("| 线性屈曲因子 |")]
    check("§2 模态 / 屈曲 2D 按内核事实标 Y",
          len(cap_rows) == 2 and all("| Y | Y |" in ln for ln in cap_rows))
    check("§4.2 收窄为 7 个 2D=N（不是删缺口）",
          "7 个" in text and "已收窄" in text
          and "2D ↔ 3D 高级能力不对齐" in text)
    s2d = read(ROOT / "solve_2d.py")
    m2d = read(ROOT / "modal_2d.py")
    check("2D 模态接入链路源码级对拍",
          "example_modal.exe" in s2d and "parse_modal_txt" in m2d
          and "不重算 K/M" in m2d and "status_text" in m2d)
    check("README 同步 7 项且不再写 9 项",
          "7 项高级能力" in read(ROOT / "README.md")
          and "9 项高级能力" not in read(ROOT / "README.md"))
    # 2026-11-10 第六次：2D 截面应力按 P 口径接通，承载力仍显式拒绝。
    st_rows = [ln for ln in text.splitlines()
               if ln.startswith("| 截面应力 / 承载力验算 |")]
    check("§2 截面应力 / 承载力 2D 标 P（不虚报为 Y）",
          len(st_rows) == 1 and "| P | Y |" in st_rows[0], str(st_rows))
    check("§2 登记 2D 应力的真实依据符号",
          "strength_2d.py::evaluate" in text
          and "solve_2d.py::strength_tables" in text)
    check("§4.2 写 6 个 N + 1 个 P 且保留历史 7 个计数",
          "6 个" in text and "1 个 `2D=P`" in text and "7 个" in text)
    st_src = read(ROOT / "strength_2d.py")
    check("2D 应力口径层不消费内核应力段",
          "不解析内核「单元应力 (Pa)」段" in st_src
          and "parse_result" not in st_src)
    check("2D 应力口径层写明承载力本次未做",
          "承载力验算（Mu / Vu / 压弯 / 轴压）本次未做" in st_src)
    check("README 同步 1 项部分接入",
          "1 项部分接入" in read(ROOT / "README.md"))
    check("能力矩阵登记第六次闭环记录", "第六次闭环记录" in text)
    src = read(Path(__file__))
    # 拼接构造，避免「禁用词字面量本身出现在源码里」让自检自相矛盾。
    for banned in ("sub" + "process", "os." + "system", "solve_" + "3d(",
                   "write_" + "text(", "explore-" + "x20"):
        check(f"守卫自身不含 {banned}", banned not in src)


def main() -> int:
    print("=" * 78)
    doc_structure()
    builder_map = main_matrix()
    dimension_caps()
    white = whitelist()
    coverage(white)
    dat_invariant(builder_map)
    live_effect(builder_map)
    gaps_and_process()
    n_pass = sum(1 for ok, _, _ in RESULTS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(RESULTS)} 通过")
    return 0 if n_pass == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
