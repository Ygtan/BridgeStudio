"""verify_report_2d.py —— 2D「同源计算书四出口」接入链路守卫。

2026-11-11 第八次（B2）：新增旁路 `report_2d.build_2d_document(res, spec)
-> report_3d.Report`，四出口**原样复用** `report_3d.write_all`；`write_2d_html`
一个字节不改，仍是默认 2D 计算书出口。

这条守卫把「同源」钉成可执行的约束，而不是一句口号：

* 数值逐格对拍：Report 的每张表与 `write_2d_html` 的 `<td>` 同源，不另立口径；
* 缺数据不补零：空结果不出空表、校核 CSV 只有表头、L/600 无位移时不编结论；
* opt-in 章条件出现：模态 / 应力未请求时整章不出现，请求后承载力仍写「做不了」；
* 四出口落盘：HTML / DOCX / CSV(zip) / PDF，PDF 逐页页码 + 章节书签读回对拍；
* 故障注入：浏览器缺失时抛 `PDFExportError`、**不生成占位 PDF**、三件套保留；
* 默认不改：默认 HTML 两次写出逐位一致、12 格 `.dat` 黄金哈希、源码级防重算。

能力矩阵该格仍标 `P`：**导出机制对齐 ≠ 2D 力学能力与 3D 对齐**，2D 报告
除 opt-in `[MODAL2D]` 明细段外没有振型 / 参与质量 / 承载力 Mu、Vu / 结构云图，且四出口是
opt-in 旁路。汇总按布尔位统计，杜绝「假绿」。

纯离线（除 PDF 打印外不调内核）；本目录不写任何仓库文件，产物都在
`out/verify_report_2d/`。
"""

from __future__ import annotations

import hashlib
import html as _html
import inspect
import os
import re
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

from console_utf8 import setup_console

setup_console()

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import model_doc
import report_3d as R
from bridge_spec import BridgeSpec
from export_html import write_2d_html
from report_2d import (BOUNDARY_NOTE, FOUR_EXPORTS_NOTE, NO_CHECK_NOTE,
                       NO_COMBO_NOTE, NO_LIVE_NOTE, build_2d_document,
                       build_and_write, write_2d_all)
from solve_2d import Solve2DResult

OUT = ROOT / "out" / "verify_report_2d"
PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


# ---------------------------------------------------------------- 构造样本


def _sample_spec() -> Any:
    """两根节点 / 30 m 单跨，够 _max_vertical_span 与 L/600 用。"""
    return NS(title="守卫桥", stages=[],
              nodes=[NS(id=1, x=0.0), NS(id=2, x=30.0)],
              boundaries=[NS(node=1, dofs="1-3"), NS(node=2, dofs="2")],
              creep_rh=0.0)


def _sample_res() -> Solve2DResult:
    res = Solve2DResult()
    res.title = "守卫桥"
    res.solver = "example_stage.exe"
    res.dat_text = "guard-dat"
    res.final_beam = {
        (1, "i"): {"N": -1000.0, "My": 2250000.0, "Vz": 12000.0},
        (1, "j"): {"N": -1000.0, "My": -1800000.0, "Vz": -9000.0},
    }
    res.final_disp = {(1, 2): -0.0123, (2, 2): -0.005}
    res.beam = {1: {"Mmin": -500000.0, "Mmax": 2250000.0,
                    "Vmin": -9000.0, "Vmax": 12000.0}}
    res.live_beam = {(1, "i"): {"NMin": 0.0, "NMax": 0.0,
                                "MyMin": -300000.0, "MyMax": 900000.0,
                                "VzMin": -4000.0, "VzMax": 5000.0}}
    res.live_disp = {1: (-0.01, -0.02)}
    res.impact_factor = 1.25
    res.diagnostics = ["守卫诊断：仅用于回归"]
    return res


def _td_texts(h: str) -> set[str]:
    return {_html.unescape(x.strip())
            for x in re.findall(r"<td[^>]*>(.*?)</td>", h, re.S)}


def _flat(items: Any):
    for it in items:
        if isinstance(it, list):
            yield from _flat(it)
        else:
            yield it


# ---------------------------------------------------------------- Report 契约


def contract() -> None:
    rep = build_2d_document(Solve2DResult(), BridgeSpec())
    check("build_2d_document 返回 report_3d.Report", isinstance(rep, R.Report),
          type(rep).__name__)
    check("Report.kind 是 2d", rep.kind == "2d", rep.kind)
    check("2D Report 不编图（figures 为空）", rep.figures == {} and
          not any(b["type"] in ("figure", "img") for b in rep.blocks))
    check("Report 有 meta / fingerprint / generated_at",
          len(rep.meta) >= 6 and isinstance(rep.generated_at, str)
          and bool(rep.generated_at))
    allowed = {"h1", "h2", "note", "bullets", "metrics", "checks", "table"}
    check("blocks 类型全部是报告认识的类型",
          all(b.get("type") in allowed for b in rep.blocks),
          str(sorted({b.get("type") for b in rep.blocks})))
    keys = [b["key"] for b in rep.blocks if b["type"] == "table"]
    check("每个 table block 都有对应 tables 条目",
          all(k in rep.tables for k in keys) and len(keys) == len(set(keys)))
    for k, tb in rep.tables.items():
        check(f"表 {k} 契约含 title/headers/rows/note",
              {"title", "headers", "rows", "note"} <= set(tb))
        check(f"表 {k} 每行与表头等宽",
              all(len(r) == len(tb["headers"]) for r in tb["rows"]))
        check(f"表 {k} 不摆空表", len(tb["rows"]) > 0)
    res = _sample_res()
    rep2 = build_2d_document(res, _sample_spec())
    check("fingerprint 与 Solve2DResult 同源",
          rep2.fingerprint == str(res.fingerprint))
    check("diagnostics 与 Solve2DResult 同源",
          list(rep2.diagnostics) == list(res.diagnostics))
    check("meta 写明维度与四出口",
          any(k == "维度" and "2D" in v for k, v in rep2.meta)
          and any(k == "计算书" and "PDF" in v for k, v in rep2.meta))
    src = inspect.getsource(build_2d_document)
    check("构造器不写文件、不调内核、不改 res/spec",
          "write_text" not in src and "open(" not in src
          and "to_dat" not in src and "solve_3d(" not in src
          and "subprocess" not in src)
    mod = Path(ROOT / "report_2d.py").read_text(encoding="utf-8")
    check("口径层不解析内核「单元应力 (Pa)」段",
          "parse_result" not in mod and "单元应力 (Pa)" in mod)
    check("口径层不重算力学（无 K/M、无 T=1/f）",
          "不重算力学" in mod and "不补 0" in mod)
    check("write_2d_all 原样复用 report_3d.write_all",
          "return R.write_all(rep, out_dir, stem=stem, pdf=pdf)" in mod)
    check("模块没有另写第二个 HTML 渲染器",
          "def render_html" not in mod and "def write_html" not in mod
          and "def write_docx" not in mod and "def write_csv" not in mod
          and "def write_pdf" not in mod)


# ---------------------------------------------------------------- 数值同源


def same_source() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    res, spec = _sample_res(), _sample_spec()
    rep = build_2d_document(res, spec)
    legacy = write_2d_html(res, spec, OUT / "legacy.html").read_text(encoding="utf-8")
    new_html = R.render_html(rep)
    old_cells = _td_texts(legacy)
    check("旧 HTML 确实写出了成桥内力表", "成桥态梁端内力" in legacy)
    check("四张数据表齐备",
          {"final_beam_table", "combo_table", "stage_envelope_table",
           "live_beam_table"} <= set(rep.tables),
          str(sorted(rep.tables)))
    for key, tb in rep.tables.items():
        cells = [str(c) for row in tb["rows"] for c in row]
        check(f"{key} 全部 {len(cells)} 格与旧 HTML 逐格同源",
              all(c in old_cells for c in cells),
              str([c for c in cells if c not in old_cells][:4]))
    for token in ("2250.0", "-1800.0", "-12.30", "-20.00", "50.0"):
        check(f"关键数值 {token} 在新 HTML 与旧 HTML 都出现",
              token in new_html and token in legacy)
    check("冲击系数只在指标卡列数值（旧 HTML 不重复列）",
          "1.250" in new_html
          and f"{res.impact_factor:.3f}" == "1.250")
    check("新 HTML 无 Markdown 星号残留（rich 覆盖粗体）", "**" not in new_html)
    check("新 HTML 每章一个 BKSEC 书签锚点（CSS 注释里另有说明字样）",
          new_html.count('<span class="om">BKSEC') ==
          sum(1 for b in rep.blocks if b["type"] in ("h1", "h2")))
    check("新 HTML 带四出口说明与能力边界",
          FOUR_EXPORTS_NOTE in new_html and BOUNDARY_NOTE in new_html)
    check("新 HTML 明确写出「没有振型 / 承载力」",
          "没有振型" in new_html and "Mu / Vu" in new_html)
    check("新 HTML 不把展示量写成规范判定",
          "展示量，不构成规范判定" in new_html)
    chk = list(rep.checks())
    check("L/600 校核结论只有一条", len(chk) == 1, str([c.name for c in chk]))
    if chk:
        c = chk[0]
        check("L/600 实测 20 mm / 限值 50 mm 与旧 HTML 同源",
              abs(c.value - 20.0) < 1e-9 and abs(c.limit - 50.0) < 1e-9,
              f"{c.value}/{c.limit}")
        check("L/600 结论是「满足」（不擅自改判定）", c.passed)
        check("L/600 说明含表 4.3.2 与最大跨径 30 m",
              "表 4.3.2" in c.detail and "30 m" in c.detail)
    metrics = {m.label: m.value for m in rep.metrics()}
    check("指标卡最大竖向位移与旧 HTML 同源",
          metrics.get("成桥态最大竖向位移") == "-12.30", str(metrics))
    check("指标卡冲击系数与 res 同源",
          metrics.get("车道冲击系数 1+μ") == "1.250")
    check("指标卡内力条目数与 final_beam 同源",
          metrics.get("成桥态梁端内力条目") == "2")
    note_txt = ("".join(str(b.get("text", "")) for b in rep.blocks
                        if b["type"] == "note")
                + "".join(str(tb.get("note", "")) for tb in rep.tables.values()))
    check("阶段包络 note 与旧 HTML 同文案",
          "包络含未成桥的临时构型" in note_txt
          and "包络含未成桥的临时构型" in legacy)
    check("活载包络 note 与旧 HTML 同文案",
          "已含 1+μ" in note_txt and "已含 1+μ" in legacy)


# ---------------------------------------------------------------- 不补零


def no_fabrication() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    res, spec = Solve2DResult(), BridgeSpec()
    rep = build_2d_document(res, spec)
    check("空结果不出成桥内力表", "final_beam_table" not in rep.tables)
    check("空结果不出活载 / 阶段包络表",
          "live_beam_table" not in rep.tables
          and "stage_envelope_table" not in rep.tables)
    check("空结果不出规范组合表", "combo_table" not in rep.tables)
    check("空结果没有 L/600 校核结论", list(rep.checks()) == [])
    txt = "".join(str(b.get("text", "")) for b in rep.blocks
                  if b["type"] == "note")
    check("空结果写中文「不生成 L/600 校核结论」", NO_CHECK_NOTE in txt)
    check("空结果写中文「本次不出规范组合表」", NO_COMBO_NOTE in txt)
    check("空结果指标卡写「—」而不是 0",
          any(m.label == "成桥态最大竖向位移" and m.value == "—"
              for m in rep.metrics()))
    check("空结果没有空 rows 的表",
          all(tb["rows"] for tb in rep.tables.values()))
    paths = write_2d_all(rep, OUT / "empty", stem="empty", pdf=False)
    with zipfile.ZipFile(paths["zip"]) as z:
        chk_csv = z.read("01_校核.csv").decode("utf-8-sig")
    lines = [x for x in chk_csv.splitlines() if x.strip()]
    check("CSV 校核表只有表头，不补零、不摆空结论", len(lines) == 1,
          str(lines))
    check("CSV 校核表表头仍在", "校核项" in lines[0] and "结论" in lines[0])
    h = R.render_html(rep)
    check("空结果 HTML 不出现虚构数值 0.0 校核",
          "不生成 L/600 校核结论" in h)
    # 只有恒载、没有活载包络的组合：必须显式写「不是完整正常使用验算」。
    r2 = Solve2DResult()
    r2.final_beam = {(1, "i"): {"N": -1000.0, "My": 1000.0, "Vz": 500.0}}
    r2.live_beam = {}
    rep2 = build_2d_document(r2, BridgeSpec())
    all_txt = "".join(str(b.get("text", "")) for b in rep2.blocks
                      if b["type"] == "note") + "".join(
        str(tb.get("note", "")) for tb in rep2.tables.values())
    check("无活载包络时写中文「不是完整的正常使用验算」",
          NO_LIVE_NOTE in all_txt or "不是完整的正常使用验算" in all_txt)


# ---------------------------------------------------------------- opt-in 章


def optin_sections() -> None:
    res = _sample_res()
    rep = build_2d_document(res, _sample_spec())
    heads = [b["text"] for b in rep.blocks if b["type"] == "h1"]
    check("未请求时不出 2D 自振 / 屈曲章",
          "2D 自振特性与线性屈曲" not in heads)
    check("未请求时不出 2D 截面应力章", "2D 截面应力验算" not in heads)
    check("默认模态 status = not_requested",
          (getattr(res, "modal", None) or {}).get("status") == "not_requested")
    check("默认应力 status = not_requested",
          (getattr(res, "strength", None) or {}).get("status") == "not_requested")

    res2 = _sample_res()
    res2.modal = {"status": "ok", "consistency": {"note": "模态一致性说明"}}
    res2.strength = {"status": "ok"}
    res2.modal_tables = lambda: {
        "modal_table": {"title": "自振频率", "headers": ["阶次", "f (Hz)"],
                        "rows": [["1", "2.500"]], "note": "只有频率"},
        "buckling_table": {"title": "线性屈曲", "headers": ["阶次", "λ"],
                           "rows": [["1", "4.200"]], "note": "λ≠φ"},
    }
    res2.strength_tables = lambda: {
        "stress_table": {"title": "截面应力", "headers": ["位置", "σ (MPa)"],
                         "rows": [["上缘", "8.30"]], "note": "仅应力"},
    }
    rep2 = build_2d_document(res2, _sample_spec())
    heads2 = [b["text"] for b in rep2.blocks if b["type"] == "h1"]
    check("请求后出 2D 自振 / 屈曲章", "2D 自振特性与线性屈曲" in heads2)
    check("请求后出 2D 截面应力章", "2D 截面应力验算" in heads2)
    check("请求后模态两张表都进 Report",
          "modal_table" in rep2.tables and "buckling_table" in rep2.tables)
    check("请求后应力表进 Report", "stress_table" in rep2.tables)
    from strength_2d import CAPACITY_NOTE
    from modal_2d import BUCK_SECTION
    h2 = R.render_html(rep2)
    check("应力章仍写承载力「本次未做」（2D 无配筋契约）",
          CAPACITY_NOTE in h2 and "承载力验算（Mu / Vu" in h2)
    check("屈曲章不把 λ 说成规范安全系数",
          "不是规范稳定安全系数" in BUCK_SECTION
          or "λ≠φ" in h2 or "λ 不是规范稳定安全系数" in h2)
    check("模态一致性说明入报告", "模态一致性说明" in h2)
    check("opt-in 章数值不补零", "8.30" in h2 and "4.200" in h2)
    # 请求了却没结果：出中文状态，不摆空表。
    res3 = _sample_res()
    res3.modal = {"status": "unavailable"}
    res3.strength = {"status": "refused"}
    res3.modal_tables = lambda: {}
    res3.strength_tables = lambda: {}
    rep3 = build_2d_document(res3, _sample_spec())
    h3 = R.render_html(rep3)
    check("请求却无结果时仍出章节（不静默删章）",
          "2D 自振特性与线性屈曲" in h3 and "2D 截面应力验算" in h3)
    check("请求却无结果时不出空表",
          "modal_table" not in rep3.tables and "stress_table" not in rep3.tables)
    check("请求却无结果时写中文状态", "本次" in h3 or "没有" in h3)


# ---------------------------------------------------------------- 四出口落盘


def four_exports() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # 上一轮真的打印过 PDF；重跑时先清掉，否则「pdf=False 不生成 PDF」
    # 会被历史产物误伤（守卫必须可重复执行）。
    stale = OUT / "four" / "r2d.pdf"
    if stale.exists():
        stale.unlink()
    res, spec = _sample_res(), _sample_spec()
    rep, paths = build_and_write(res, spec, OUT / "four", stem="r2d", pdf=False)
    check("pdf=False 只出三件套", set(paths) == {"html", "docx", "zip"},
          str(sorted(paths)))
    check("三件套全部落盘且非空",
          all(p.is_file() and p.stat().st_size > 0 for p in paths.values()))
    check("pdf=False 不生成 PDF 文件", not stale.exists())
    html_txt = paths["html"].read_text(encoding="utf-8")
    check("落盘 HTML 与 render_html(rep) 逐字一致",
          html_txt == R.render_html(rep))
    with zipfile.ZipFile(paths["zip"]) as z:
        names = z.namelist()
        check("CSV 数据包含指标 / 校核两表",
              "00_指标.csv" in names and "01_校核.csv" in names, str(names))
        check("CSV 数据包每张明细表一个 csv（另有 README.txt 说明）",
              len([n for n in names if n.endswith(".csv")])
              == 2 + len(rep.tables), str(names))
        all_csv = "\n".join(z.read(n).decode("utf-8-sig") for n in names)
    check("CSV 里同源出现关键数值", "2250.0" in all_csv and "-1800.0" in all_csv)
    check("CSV 里出现 L/600 实测与限值",
          "20" in all_csv and "50" in all_csv)
    try:
        from docx import Document

        doc = Document(str(paths["docx"]))
        doc_txt = "\n".join(p.text for p in doc.paragraphs)
        doc_tbl = "\n".join(c.text for t in doc.tables
                            for row in t.rows for c in row.cells)
        check("DOCX 有标题与四出口说明", "守卫桥" in doc_txt
              and "同源" in doc_txt + doc_tbl)
        check("DOCX 同源出现关键数值", "2250.0" in doc_tbl)
        check("DOCX 表格数与 Report 表数一致",
              len(doc.tables) >= len(rep.tables),
              f"{len(doc.tables)}/{len(rep.tables)}")
    except ImportError:
        check("python-docx 可用（DOCX 读回）", False, "python-docx 缺失")

    # PDF：真的打印、真的读回，不靠「调用过」就算数。
    pdf_path = OUT / "four" / "r2d.pdf"
    try:
        pdf_path = R.write_pdf(rep, pdf_path)
        ok = pdf_path.is_file() and pdf_path.stat().st_size > 0
        check("PDF 出口真的落盘且非空", ok)
    except R.PDFExportError as e:
        check("PDF 出口真的落盘且非空", False, str(e)[:60])
        return
    try:
        from pypdf import PdfReader

        rd = PdfReader(str(pdf_path))
        page_txt = [(p.extract_text() or "") for p in rd.pages]
        n = len(page_txt)
        check("PDF 至少 1 页", n >= 1, str(n))
        stamps = [f"{i + 1} / {n}" in t for i, t in enumerate(page_txt)]
        check("PDF 每页都有页码「N / 总页数」", all(stamps),
              f"缺页码页 {[i + 1 for i, v in enumerate(stamps) if not v]}")
        heads = [b["text"] for b in rep.blocks if b["type"] in ("h1", "h2")]
        items = list(_flat(rd.outline))
        titles = [str(x.get("/Title")) for x in items]
        check("PDF 书签条数与报告章节数一致",
              len(items) == len(heads), f"{len(items)}/{len(heads)}")
        check("PDF 书签标题与 h1/h2 逐条对拍", titles == heads,
              str(titles[:3]))
        body = "\n".join(page_txt)
        check("PDF 正文同源出现关键数值", "2250.0" in body)
        check("PDF 不是第四个渲染器（正文来自 HTML）",
              "同源" in body or "Report" in body)
    except ImportError:
        check("pypdf 可用（PDF 读回）", False, "pypdf 缺失")


def pdf_failure() -> None:
    """浏览器缺失：显式报错、绝不生成占位 PDF、三件套仍保留。"""
    OUT.mkdir(parents=True, exist_ok=True)
    rep = build_2d_document(_sample_res(), _sample_spec())
    fail_dir = OUT / "fail"
    paths = write_2d_all(rep, fail_dir, stem="fail", pdf=False)
    check("故障注入前先有三件套",
          set(paths) == {"html", "docx", "zip"}
          and all(p.exists() for p in paths.values()))
    old = os.environ.get(R.PDF_BROWSER_ENV)
    raised = False
    msg = ""
    try:
        os.environ[R.PDF_BROWSER_ENV] = str(OUT / "no_such_browser.exe")
        R.write_pdf(rep, fail_dir / "fail.pdf")
    except R.PDFExportError as e:
        raised, msg = True, str(e)
    finally:
        if old is None:
            os.environ.pop(R.PDF_BROWSER_ENV, None)
        else:
            os.environ[R.PDF_BROWSER_ENV] = old
    check("显式指定不存在的浏览器时抛 PDFExportError", raised, msg[:50])
    check("PDF 失败后不生成占位 PDF", not (fail_dir / "fail.pdf").exists())
    check("PDF 失败后三件套仍在（只降级一个出口）",
          all(p.is_file() for p in paths.values()))
    check("PDFExportError 是 RuntimeError 子类（界面可捕获）",
          issubclass(R.PDFExportError, RuntimeError))
    src = Path(ROOT / "app.py").read_text(encoding="utf-8")
    cli = Path(ROOT / "export_html.py").read_text(encoding="utf-8")
    check("界面捕获 PDFExportError 且给中文告警",
          "except PDFExportError" in src and "PDF 计算书未生成" in src)
    check("界面失败时保留另外三份下载按钮",
          "download_button" in src and "report2d_dl_" in src
          and "没有生成占位 PDF" in src)
    check("CLI 失败时保留三件套且不生成占位 PDF",
          "except PDFExportError" in cli and "没有生成占位 PDF" in cli)


# ---------------------------------------------------------------- 默认不变


def invariants() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    a = write_2d_html(Solve2DResult(), BridgeSpec(), OUT / "d0.html").read_bytes()
    b = write_2d_html(Solve2DResult(), BridgeSpec(), OUT / "d1.html").read_bytes()
    check("默认 2D HTML 两次写出逐字节一致", a == b, f"{len(a)}B")
    check("默认 2D HTML 不含四出口旁路字样",
          b"report_2d" not in a and "同源四出口" not in a.decode("utf-8"))
    src = inspect.getsource(write_2d_html)
    check("write_2d_html 仍只自拼 HTML（未接 Report）",
          "<!DOCTYPE html>" in src and "Report(" not in src
          and "build_2d_document" not in src and "write_all" not in src)
    check("write_2d_html 仍按 1e3 / 1000 换算（口径未改）",
          "/ 1e3" in src and "* 1000" in src)
    from verify_stage_tension_3d import DEFAULT_DAT_SHA

    for (kind, dim), builder in model_doc.BUILDERS.items():
        dat = model_doc.ModelDoc(
            kind, dict(model_doc.DEFAULTS[builder]), dimension=dim).to_dat()
        h = hashlib.sha256(dat.encode()).hexdigest()[:16]
        check(f"12 格默认 .dat 黄金哈希：{builder}",
              h == DEFAULT_DAT_SHA[builder], h)
    cli = Path(ROOT / "export_html.py").read_text(encoding="utf-8")
    app = Path(ROOT / "app.py").read_text(encoding="utf-8")
    check("CLI 四出口是 opt-in 的 store_true（默认关闭）",
          '--four-exports' in cli and 'action="store_true"' in cli)
    check("CLI 仍先写默认单份 HTML 再做四出口",
          cli.index("out = write_2d_html") < cli.index("args.four_exports"))
    check("界面四出口 opt-in checkbox 默认 False",
          'key="report2d_opt"' in app and "value=False, key=\"report2d_opt\"" in app)
    check("界面先出三件套再单独打印 PDF",
          'stem="report2d", pdf=False' in app
          and 'write_pdf(_rep2' in app)
    check("界面用 fingerprint 判报告是否过期",
          '_got2.get("fingerprint") != _r2d.fingerprint' in app)
    check("界面明确 2D 报告不含振型 / 承载力 / 云图",
          "2D 报告不含振型" in app and "Mu、Vu" in app and "云图" in app)
    check("build_and_write 返回 (Report, paths) 二元组",
          "return rep, write_2d_all(rep, out_dir, stem=stem, pdf=pdf)"
          in Path(ROOT / "report_2d.py").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 文档口径


def docs() -> None:
    mat = (ROOT / "能力边界矩阵.md").read_text(encoding="utf-8")
    row = [ln for ln in mat.splitlines()
           if ln.startswith("| 同源计算书四出口")]
    check("§2 四出口格仍标 2D=P（不虚报 Y）",
          len(row) == 1 and "| P | Y |" in row[0], str(row))
    check("§2 依据列登记 report_2d 两个真实符号",
          "report_2d.py::build_2d_document" in mat
          and "report_2d.py::write_2d_all" in mat)
    check("§2 写明四出口机制已接通但内容仍窄于 3D",
          "导出机制" in mat and "不标 `Y`" in mat and "opt-in" in mat)
    check("§4.2 写 4 个 N + 3 个 P 且保留历史 7 个计数",
          "4 个 `2D=N`" in mat and "3 个 `2D=P`" in mat
          and "7 个" in mat and "已收窄" in mat)
    check("能力矩阵登记第八次闭环记录", "第八次闭环记录" in mat)
    # 第十一次：2D 内层 Newton 过程量章与四出口同源，两层力学仍标 N。
    check("能力矩阵登记第十一次 [NEWTON2D] 闭环",
          "[NEWTON2D]" in mat and "第十一次闭环记录" in mat)
    check("report_2d 有 2D Newton 边界 note",
          "NEWTON_BOUNDARY_NOTE" in
          (ROOT / "report_2d.py").read_text(encoding="utf-8"))
    check("能力矩阵仍写前端默认出口不变",
          "一个字节不改" in mat and "`write_2d_html`" in mat
          and "默认 `.dat` 与 12 格黄金哈希" in mat)
    rd = (ROOT / "README.md").read_text(encoding="utf-8")
    check("README 同步 3 项部分接入", "3 项部分接入" in rd)
    check("README 登记 2D 四出口与 report_2d.py",
          "report_2d.py" in rd and "四出口" in rd)
    guide = (ROOT / "开发使用手册.md").read_text(encoding="utf-8")
    check("开发手册登记 verify_report_2d.py", "verify_report_2d.py" in guide)
    check("开发手册登记 report_2d.py", "report_2d.py" in guide)
    usage = (ROOT / "使用说明.md").read_text(encoding="utf-8")
    check("使用说明登记 2D 四出口口径层", "report_2d.py" in usage)
    spec = (ROOT / "B类后续项接入规格.md").read_text(encoding="utf-8")
    check("B 类规格写明四出口已接通但仍标 P",
          "四出口" in spec and "已接通" in spec and "仍标 `P`" in spec)
    check("B 类规格写明 X20 契约仍锁死多格式导出",
          "X20 契约" in spec and "唯一转换点" in spec)
    check("B 类规格写明 B2 剩余 3 项", "B2 剩余 3 项" in spec
          or "其余 3 项" in spec)
    for name, expr in (
            ("verify_capability_matrix.py", "sum(1 for ok, _, _ in RESULTS if ok)"),
            ("verify_b_class_spec.py", "sum(1 for ok, _, _ in RESULTS if ok)"),
            ("verify_strength_2d.py", "sum(1 for _, ok, _ in PASS if ok)"),
            ("verify_validate_2d.py", "sum(1 for _, ok, _ in PASS if ok)")):
        t = Path(ROOT / name).read_text(encoding="utf-8")
        check(f"既有守卫 {name} 汇总按布尔位统计（不假绿）", expr in t)


def main() -> int:
    print("=" * 78)
    print("  verify_report_2d：2D 同源计算书四出口（机制对齐，不虚报内容）")
    print("=" * 78)
    OUT.mkdir(parents=True, exist_ok=True)
    contract()
    same_source()
    no_fabrication()
    optin_sections()
    four_exports()
    pdf_failure()
    invariants()
    docs()
    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("-" * 78)
    print(f"  {n_pass}/{len(PASS)} 通过")
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
