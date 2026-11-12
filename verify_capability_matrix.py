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
    check("§4.2 写 4 个 N + 3 个 P 且保留历史 7 个计数",
          "4 个" in text and "3 个 `2D=P`" in text and "7 个" in text)
    st_src = read(ROOT / "strength_2d.py")
    check("2D 应力口径层不消费内核应力段",
          "不解析内核「单元应力 (Pa)」段" in st_src
          and "parse_result" not in st_src)
    check("2D 应力口径层写明承载力本次未做",
          "承载力验算（Mu / Vu / 压弯 / 轴压）本次未做" in st_src)
    check("README 同步 3 项部分接入",
          "3 项部分接入" in read(ROOT / "README.md"))
    check("能力矩阵登记第六次闭环记录", "第六次闭环记录" in text)
    val_rows = [ln for ln in text.splitlines()
                if ln.startswith("| 引用完整性中文校验 |")]
    check("§2 引用完整性按源码纠正为 2D=P（不虚报 Y）",
          len(val_rows) == 1 and "| P | Y |" in val_rows[0], str(val_rows))
    check("§2 登记 2D 校验真实符号且写明覆盖窄于 3D",
          "bridge_spec.py::validate_model" in text
          and "2D 仍窄于 3D" in text)
    check("能力矩阵登记第七次闭环记录", "第七次闭环记录" in text)
    check("能力矩阵登记第九次闭环记录", "第九次闭环记录" in text)
    # 2026-11-11 第十次：2D 分项损失明细按 FE opt-in 补段闭环，仍标 P。
    loss_rows = [ln for ln in text.splitlines()
                 if ln.startswith("| 分项预应力损失")]
    check("§2 分项损失格标 2D=P（不虚报 Y）",
          len(loss_rows) == 1 and "| P | Y |" in loss_rows[0], str(loss_rows))
    check("§2 登记 [PRESTRESSLOSS2D] / BASIS=2d_global 防漂移",
          "[PRESTRESSLOSS2D]" in text and "BASIS=2d_global" in text)
    check("§2 不设 STAGE 占位且不等于 3D 阶段维度",
          "不设 STAGE 占位" in text and "不等于 3D 的阶段" in text)
    check("能力矩阵登记第十次闭环记录", "第十次闭环记录" in text)
    check("§4.2 收窄为 3 个 2D=N + 4 个 2D=P",
          "3 个 `2D=N`" in text and "4 个 `2D=P`" in text)
    # 2026-11-11 第十一次只闭环三层迭代中的内层 Newton 一层；第十三次按
    # opt-in 首次真正新造 [LENGTH2D] 平面索长闭合一层，索长格 N→P（仍 P 不 Y），
    # [OPT] 带约束目标索力优化仍没有对应力学、仍标 N、不造占位。
    nt_rows = [ln for ln in text.splitlines()
               if ln.startswith("| 内层 Newton")]
    check("§2 内层 Newton 格标 2D=P（不虚报 Y）",
          len(nt_rows) == 1 and "| P | Y |" in nt_rows[0], str(nt_rows))
    lg_rows = [ln for ln in text.splitlines()
               if ln.startswith("| 索长迭代")]
    op_rows = [ln for ln in text.splitlines()
               if ln.startswith("| 目标索力优化")]
    check("§2 索长迭代 / 几何闭合格按第十三次标 2D=P（仍 P 不 Y）",
          len(lg_rows) == 1 and "| P | Y |" in lg_rows[0], str(lg_rows))
    check("§2 目标索力优化 [OPT] 格第十四次标 2D=P（仍 P 不 Y）",
          len(op_rows) == 1 and "| P | Y |" in op_rows[0], str(op_rows))
    check("§2 登记 [NEWTON2D] / BASIS=2d_global 防漂移",
          "[NEWTON2D]" in text and "BASIS=2d_global" in text)
    check("历史段落仍登记 §2 写 [LENGTH2D] 已接通、[OPT] 仍缺、不造占位（第十三次口径）",
          "[LENGTH2D]" in text and "[OPT]" in text
          and "不造占位" in text and "没有对应力学" in text)
    check("§2 写明 2D 不写 REFACT / TOLF / TOLD",
          "不写 REFACT" in text and "不拆 TOLF/TOLD" in text)
    check("§2 写明过程量不构成规范判定",
          "不构成规范合格判定" in text or "不是规范合格判定" in text)
    check("能力矩阵登记第十一次闭环记录", "第十一次闭环记录" in text)
    check("历史段落仍登记 §4.2 为 1 个 2D=N + 6 个 2D=P（第十三次口径）",
          "1 个 `2D=N`" in text and "6 个 `2D=P`" in text)
    check("历史段落仍登记 §4.2 写 B2 剩余 2 项且内容收窄（第十三次口径）",
          "B2 剩余 2 项" in text and "内容已收窄" in text)
    # 2026-11-11 第十二次：Mu / Vu 半部按 opt-in 接通，但该格仍标 P。
    check("能力矩阵登记第十二次闭环记录", "第十二次闭环记录" in text)
    check("§2 该格仍标 P 且依据列登记 capacity_2d",
          "| 截面应力 / 承载力验算 | P | Y |" in text
          and "capacity_2d.py::evaluate" in text
          and "bridge_spec.py::RebarSpec2D" in text)
    check("历史段落仍登记 §4.2 计数 1 个 2D=N + 6 个 2D=P、B2 剩余仍 2 项（第十三次口径）",
          "1 个 `2D=N`" in text and "6 个 `2D=P`" in text
          and "B2 剩余仍 2 项" in text)
    check("矩阵写明压弯 / 轴压不做与无活载轴力包络",
          "压弯 / 轴压" in text and "无活载轴力包络" in text)
    check("独立守卫 verify_capacity_2d.py 在位且手册登记",
          (ROOT / "verify_capacity_2d.py").is_file()
          and "verify_capacity_2d.py" in read(ROOT / "开发使用手册.md"))
    # 2026-11-11 第十三次：[LENGTH2D] 平面索长闭合一层按 opt-in 首次真正
    # 新造 2D 力学接通，索长格 2D=N → 2D=P（仍 P 不 Y）；[OPT] 仍 N、不造占位。
    check("能力矩阵登记第十三次闭环记录", "第十三次闭环记录" in text)
    check("§2 索长格依据列登记 length_2d.py / LengthSpec2D 真实符号",
          "length_2d.py::parse_length_txt" in text
          and "bridge_spec.py::LengthSpec2D" in text
          and "verify_length_2d.py::main" in text)
    check("§2 [OPT] 格第十四次标 | P | Y |（仍不虚报 Y、不造占位）",
          "| 目标索力优化 `[OPT]` + 体系守卫 | P | Y |" in text
          and "不造占位" in text)
    check("矩阵写明 2D 是无垂度线性杆 + 有限差分 J + LM、不等于 3D 三层迭代",
          "无垂度线性杆" in text and "中心有限差分 Jacobian" in text
          and "LM 阻尼最小二乘" in text and "J=M·D" in text
          and "不等于 3D" in text and "三层迭代" in text)
    check("独立守卫 verify_length_2d.py 在位且开发手册登记",
          (ROOT / "verify_length_2d.py").is_file()
          and "verify_length_2d.py" in read(ROOT / "开发使用手册.md"))
    check("矩阵写明该格仍标 P 不标 Y",
          "仍标 `P` 不标 `Y`" in text)
    check("历史段落仍登记 §4.2 为 1 个 2D=N + 6 个 2D=P 且 B2 剩余仍 2 项（第十三次口径）",
          "1 个 `2D=N`" in text and "6 个 `2D=P`" in text
          and "B2 剩余仍 2 项" in text)
    check("历史段落仍登记 B2 剩余 2 项内容再收窄为 [OPT] 一层 + 多格式导出 / .mct",
          "内容再收窄为" in text and "`[OPT]` 一层力学" in text
          and "`.mct` 反向导入" in text)
    check("历史段落仍登记 [OPT] 仍没有对应力学、不造占位、不写不适用段（第十三次口径）",
          "仍没有对应力学" in text and "不造占位" in text
          and "[OPT]" in text)
    check("§2 依据列登记 2D Newton 真实符号",
          "newton_2d.py::parse_newton_txt" in text
          and "solve_2d.py::newton_tables" in text)
    nt_src = read(ROOT / "newton_2d.py")
    s2d_new = read(ROOT / "solve_2d.py")
    check("2D Newton 口径层第十四次写明不重算 / [LENGTH2D] / [OPT2D] 独立 opt-in 链",
          "不重算" in nt_src and "[LENGTH2D]" in nt_src
          and "第十四次" in nt_src and "[OPT2D]" in nt_src
          and "第十四次" in s2d_new and "[OPT2D]" in s2d_new
          and "opt_detail" in s2d_new)
    check("§2 写明 [MODAL2D] / BASIS=xy / Z 向不存在",
          "[MODAL2D]" in text and "BASIS=xy" in text
          and "不存在（不是 0）" in text)
    check("§2 依据列登记 parse_modal_detail 与 FE 新符号",
          "modal_2d.py::parse_modal_detail" in text
          and "formatModalSection2D" in text)
    check("防漂移：FE 默认回归不带开关、黄金基线不刷新",
          "--modal-detail" in text and "黄金基线不刷新" in text)
    bs_src = read(ROOT / "bridge_spec.py")
    s2d_src = read(ROOT / "solve_2d.py")
    check("防漂移：2D 校验符号真实存在且求解入口调用",
          "def validate_model(self)" in bs_src
          and "errs = spec.validate_model()" in s2d_src)
    # 2026-11-11 第八次：2D 同源计算书四出口接通「导出机制」，仍标 P。
    rpt_rows = [ln for ln in text.splitlines()
                if ln.startswith("| 同源计算书四出口")]
    check("§2 四出口格标 2D=P（导出机制对齐不虚报 Y）",
          len(rpt_rows) == 1 and "| P | Y |" in rpt_rows[0], str(rpt_rows))
    check("§2 登记 report_2d 两个真实依据符号",
          "report_2d.py::build_2d_document" in text
          and "report_2d.py::write_2d_all" in text)
    check("§2 写明导出机制对齐但内容仍窄、默认出口不变",
          "导出机制" in text and "不标 `Y`" in text
          and "opt-in" in text and "一个字节不改" in text)
    check("能力矩阵登记第八次闭环记录", "第八次闭环记录" in text)
    rp_src = read(ROOT / "report_2d.py")
    check("防漂移：report_2d 真实存在且原样复用 write_all",
          "def build_2d_document(res" in rp_src
          and "return R.write_all(rep, out_dir, stem=stem, pdf=pdf)" in rp_src)
    check("防漂移：默认 write_2d_html 不接 Report 旁路",
          "build_2d_document" not in read(ROOT / "export_html.py")
          and "def write_2d_html" in read(ROOT / "export_html.py"))
    src = read(Path(__file__))
    # 拼接构造，避免「禁用词字面量本身出现在源码里」让自检自相矛盾。
    for banned in ("sub" + "process", "os." + "system", "solve_" + "3d(",
                   "write_" + "text(", "explore-" + "x20"):
        check(f"守卫自身不含 {banned}", banned not in src)


def fourteenth() -> None:
    """第十四次：[OPT2D] 2D 平面索力优化按 opt-in 新造接通，矩阵该格 N→P
    （仍 P 不 Y），§4.2 变 0N+7P、B2 剩余 1 项；`0 个 2D=N` 断言只落在
    确实含该串的文档上，历史串仍按 D 类准则保留。"""
    text = read(DOC)
    check("第十四次：矩阵 §2 [OPT] 格 | P | Y | 且 P ≠ Y、不等于 3D [OPT]",
          "| 目标索力优化 `[OPT]` + 体系守卫 | P | Y |" in text
          and "P ≠ Y" in text and "不等于 3D `[OPT]`" in text)
    check("历史段落仍登记第十四次矩阵 §4.2 0N+7P、B2 剩余 1 项与第十四次闭环记录",
          "0 个 `2D=N` + 7 个 `2D=P`" in text
          and "B2 剩余 1 项" in text
          and "第十四次闭环记录" in text
          and "第十四次起 `[OPT2D]`" in text)
    check("第十四次：矩阵仍标 P 不标 Y、只 MINMAXM + 显式索力箱 TMIN/TMAX、不做 MINENERGY",
          "仍标 `P` 不标 `Y`" in text and "MINMAXM" in text
          and "TMIN" in text and "TMAX" in text
          and "不做 `MINENERGY`" in text)
    check("第十四次：矩阵如实边界 2D 无 SYSGUARD / DECKLOAD / J=M·D 闭式影响矩阵、不等于 3D 三层迭代",
          "SYSGUARD" in text and "DECKLOAD" in text and "J=M·D" in text
          and "三层迭代" in text and "不等于 3D" in text)
    check("第十四次：矩阵写明 APPLIED=1 只是建议值、命中 T* 属 [LENGTH2D] 职责",
          "APPLIED=1" in text
          and "命中 `T*` 属 `[LENGTH2D]` 职责" in text)
    check("第十四次：矩阵写明默认 .dat / .result / HTML 逐位不变、DEFAULT_DAT_SHA 不刷新",
          "默认 `.dat` / 默认 `.result` / 默认 HTML 逐位不变" in text
          and "DEFAULT_DAT_SHA" in text and "不刷新" in text)
    rd = read(ROOT / "README.md")
    use = read(ROOT / "使用说明.md")
    guide = read(ROOT / "开发使用手册.md")
    check("第十五次：README 新章、开发手册 0N+9P、使用说明 B2 剩余 0 项",
          "### 2D 平行导出 / 导入链（2026-11-12，第十五次）" in rd
          and "0 个 `2D=N` + 9 个 `2D=P`" in guide
          and "B2 剩余 0 项" in guide and "B2 剩余 0 项" in use
          and "0 个 `2D=N`" not in use)
    o = read(ROOT / "opt_2d.py")
    g = read(ROOT / "verify_opt_2d.py")
    check("第十四次：opt_2d.py 含 BOUNDARY_NOTE / parse_opt_txt / 四张表键与如实边界",
          "BOUNDARY_NOTE" in o and "parse_opt_txt" in o
          and all(k in o for k in ("opt_2d_summary_table",
                                   "opt_2d_iter_table",
                                   "opt_2d_cable_table",
                                   "opt_2d_moment_table"))
          and "APPLIED=1 只表示建议值被采纳" in o
          and "命中 T* 属 [LENGTH2D] 职责" in o)
    check("第十四次：verify_opt_2d.py 在位、汇总按布尔位统计、12 格 DEFAULT_DAT_SHA 不刷新",
          (ROOT / "verify_opt_2d.py").is_file()
          and "PASS.append((name, bool(ok), str(detail)))" in g
          and "sum(1 for _, ok, _ in PASS if ok)" in g
          and "12 格 DEFAULT_DAT_SHA" in g and "不刷新" in g)
    bs = read(ROOT / "bridge_spec.py")
    s2d = read(ROOT / "solve_2d.py")
    mdpy = read(ROOT / "model_doc.py")
    app = read(ROOT / "app.py")
    ehtml = read(ROOT / "export_html.py")
    rpt = read(ROOT / "report_2d.py")
    check("第十四次：BS 源码符号 OptSpec2D / opt_detail / opt_2d_detail / opt2ddetail_opt / opt_html / ⑪",
          "class OptSpec2D" in bs and "opt_detail" in s2d
          and "opt_2d_detail" in mdpy and "opt2ddetail_opt" in app
          and "opt_html" in ehtml and "⑪" in rpt)
    from solve_2d import _fem_eigen_dir
    root = _fem_eigen_dir()
    if root:
        cl = read(root / "CHANGELOG.md")
        ifmt = read(root / "input_format.md")
        um = read(root / "USER_MANUAL.md")
        check("第十四次：FE 三文档登记 ## 2026-11-12 / *CABLE_OPT2D / 二维 opt-in 专章",
              "## 2026-11-12" in cl and "[OPT2D]" in cl
              and "### 6.6 `*CABLE_OPT2D`" in ifmt
              and "#### 二维 opt-in `[OPT2D]`" in um)
        check("第十四次：FE CHANGELOG 第十三次历史句仍保留（不删历史条目）",
              "`[OPT]` 带约束目标索力优化在 2D" in cl
              and "仍不做" in cl)


def fifteenth() -> None:
    """第十五次：B2「2D 平行导出 / 导入链」两格按 B 档平行链接通（仍 P 不 Y），
    矩阵 §2 两格标 P、依据列登记 2D 真实符号，§4.2 变 0N+9P、B2 剩余 0 项；
    逐条核对矩阵新口径、历史串保留、如实边界与 2D 源码正交，按布尔位统计。"""
    text = read(DOC)
    e2d = read(ROOT / "export_2d_formats.py")
    check("第十五次：矩阵 §2 多格式导出格 | P | Y |、依据列含 export_2d_formats.py::to_plane",
          "| ANSYS / Abaqus / MIDAS 多格式导出 | P | Y |" in text
          and "export_2d_formats.py::to_plane" in text)
    check("第十五次：矩阵 §2 .mct 反向导入格 | P | Y |、依据列含 export_2d_formats.py::read_mct_2d",
          "| `.mct` 反向导入 | P | Y |" in text
          and "export_2d_formats.py::read_mct_2d" in text)
    check("第十五次：矩阵 §4.2 0 个 2D=N + 9 个 2D=P 与 B2 剩余 0 项",
          "0 个 `2D=N` + 9 个 `2D=P`" in text
          and "B2 剩余 0 项" in text)
    check("第十五次：矩阵有第十五次闭环记录段",
          "第十五次闭环记录" in text)
    check("第十五次：矩阵 3D 活载包络云图格仍 | N | Y |（P4 成果层未计入 9P）",
          "| 3D 活载包络结构云图 / 逐端明细表 | N | Y |" in text)
    check("第十五次：矩阵历史串 1 个 2D=N + 6 个 2D=P / B2 剩余 2 项 / B2 剩余 1 项 / 仅剩被 X20 仍在",
          all(k in text for k in (
              "1 个 `2D=N` + 6 个 `2D=P`", "B2 剩余 2 项",
              "B2 剩余 1 项", "仅剩被 X20")))
    check("第十五次：矩阵如实边界 P ≠ Y / 不等于 3D [OPT] / MIDAS 数值对拍仍未做 / 无 GUI 入口",
          all(k in text for k in (
              "P ≠ Y", "不等于 3D `[OPT]`",
              "MIDAS 数值对拍仍未做", "无 GUI 入口")))
    check("第十五次：矩阵写明 DEFAULT_DAT_SHA 不刷新 / 逐位不变",
          "DEFAULT_DAT_SHA" in text and "不刷新" in text
          and "逐位不变" in text)
    check("第十五次：export_2d_formats.py 存在且含 PLANE_SCHEMA / DIMENSION = 2D / to_plane / read_mct_2d / PlaneMctImport",
          (ROOT / "export_2d_formats.py").is_file()
          and all(k in e2d for k in (
              "PLANE_SCHEMA", 'DIMENSION = "2D"', "to_plane",
              "read_mct_2d", "PlaneMctImport")))
    check("第十五次：export_2d_formats.py 不含 from export_formats import / import mct_io（3D 正交）",
          "from export_formats import" not in e2d
          and "import mct_io" not in e2d)


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
    fourteenth()
    fifteenth()
    n_pass = sum(1 for ok, _, _ in RESULTS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(RESULTS)} 通过")
    return 0 if n_pass == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
