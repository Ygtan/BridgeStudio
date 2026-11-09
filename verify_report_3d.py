"""verify_report_3d.py —— 后处理层（report_3d）回归。

守什么
------
"生成文件"类功能最容易被误判为通过：文件存在、大小不为零、Word 能打开，
就算过了。但真正的错误（列宽压成竖排、单位写成 `1`、图里量级差一千倍、
结论与数据相反）在"文件存在"这个判据下全都看不见。

所以这里的检查分三类：

1. **对拍**：同一份 Report 渲染成四种格式，再从各格式里把内容读回来
   比对——HTML 的表格行数、Word 的段落数、CSV 的行数、PDF 的可提取文本
   必须对得上 `SolveResult`，不是"差不多"。
2. **方向性**：`_col_widths` 的每列都不低于下限、总和不超过版心；`-0.0`
   不许出现在表里；无量纲量的单位 `1` 不许出现在表头。这些断言在数据正常时
   也会失败（因为它们是恒定性质，不是"这次算出来对不对"）。
3. **故障注入**：手工造一个未通过的 Check 和一个空结果，看各出口是否
   真的把"不通过"写出来、是否真的跳过不存在的图；再注入一个不存在的
   浏览器路径，看 PDF 出口是否明确报错而不是交付空壳文件。

不用 Streamlit，纯函数级。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()          # 输出被重定向到管道/文件时，GBK 会崩（见模块说明）

import os
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cable_stayed_3d import build_cable_stayed_3d  # noqa: E402
from grillage_3d import build_box_grillage_3d  # noqa: E402
from result_3d import Check, Metric, SolveResult  # noqa: E402
from solver_3d import solve  # noqa: E402

import report_3d as R  # noqa: E402

PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def _text_of_docx(path: Path) -> str:
    from docx import Document

    d = Document(str(path))
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:
        for row in t.rows:
            for c in row.cells:
                parts.append(c.text)
    return "\n".join(parts)


def _text_of_pdf(path: Path) -> tuple[list[str], list[int]]:
    """(逐页可提取文本, 逐页图片数)。pypdf 仅用于回归时把内容读回来对拍。"""
    from pypdf import PdfReader

    r = PdfReader(str(path))
    return ([(p.extract_text() or "") for p in r.pages],
            [len(p.images or []) for p in r.pages])


def main() -> int:
    print("=" * 78)
    print("  report_3d 回归：四个出口共用一份内容（PDF 由 HTML 打印）")
    print("=" * 78)

    # PDF 依赖本机浏览器。没有就明确 FAIL，不把判据悄悄"跳过"。
    try:
        browser = R._pdf_browser()
        has_pdf = True
    except R.PDFExportError as e:
        has_pdf = False
        check("本机有 PDF 打印浏览器", False, str(e))
    else:
        check("本机有 PDF 打印浏览器", True, str(browser))

    # ---------------------------------------------------------- 单元级
    print("\n[列宽与单位这类恒定性质]")
    w = R._col_widths(["单元", "Nmin (kN)", "Nmax (kN)"],
                      [[str(i), "-0.0", "328.8"] for i in range(101, 222)],
                      15.5)
    check("列宽总和不超过版心", sum(w) <= 15.5 + 1e-6, f"{sum(w):.2f} cm")
    check("每列不低于下限 1.1 cm", min(w) >= 1.09, f"min={min(w):.2f} cm")
    check("列宽足够放下 3 位数（不留竖排隐患）",
          w[0] >= 0.162 * 3 + 0.44 - 1e-9, f"{w[0]:.2f} cm")

    w2 = R._col_widths(["项目", "内容"], [("桥型", "三维梁格")], 15.5)
    check("少列时按内容摊满版心", abs(sum(w2) - 15.5) < 0.02, f"{sum(w2):.2f} cm")

    check("无量纲单位不外露", R._unit("1") == "" and R._unit("%") == "%",
          f"{R._unit('1')!r} / {R._unit('%')!r}")

    from result_3d import _r
    check("-0.0 归一成 0.0", _r(-0.0, 1) == 0.0 and str(_r(-0.0, 1)) == "0.0",
          str(_r(-0.0, 1)))

    # ---------------------------------------------------------- 两个真实模型
    cases = [("grillage3d", build_box_grillage_3d()),
             ("cable_stayed", build_cable_stayed_3d()[0])]
    reps = {}
    for kind, model in cases:
        print(f"\n[{kind}]")
        res = solve(model, kind=kind, out_dir=f"out/verify_report/{kind}")
        rep = R.build_document(res, model, out_dir=f"out/verify_report/{kind}")
        reps[kind] = (rep, res, model)

        kinds = [b["type"] for b in rep.blocks]
        h1s0 = [b["text"] for b in rep.blocks if b["type"] == "h1"]
        # "结论在明细表之前"要按**章**判，不能按"第一个 table 块"判：
        # 荷载组合章的系数表本来就在结论之前，按块判会把这个正常布局判成
        # 错误（曾经就是这么误报的）。
        check("章节顺序：结论在明细表章之前",
              h1s0.index("结论与说明") < h1s0.index("明细表")
              if ("明细表" in h1s0 and "结论与说明" in h1s0) else True,
              " > ".join(h1s0))
        check("块结构完整（概要/校核/图/结论）",
              all(t in kinds for t in ("h1", "p", "metrics", "checks",
                                       "figure", "bullets")),
              f"{len(rep.blocks)} 块")

        # 图：每张都真的画出来了，且不是空白底片
        check("图全部落盘", all(f.path.exists() and f.path.stat().st_size > 8000
                                for f in rep.figures.values()),
              "、".join(f"{k} {f.path.stat().st_size // 1024}KB"
                        for k, f in rep.figures.items()))
        want = {"grillage3d": {"layout", "deflection", "moment"},
                "cable_stayed": {"layout", "deflection", "tension", "camber",
                                 "girder", "girder_live"}}[kind]
        check("该有的图都有", want <= set(rep.figures), str(sorted(rep.figures)))
        # 轴测图：与界面里的可旋转视图同一份场景，报告里必须有一张
        check("轴测图已生成且不是空白",
              "axon" in rep.figures and rep.figures["axon"].path.exists()
              and rep.figures["axon"].path.stat().st_size > 20000,
              f"{rep.figures['axon'].path.stat().st_size // 1024} KB"
              if "axon" in rep.figures else "缺")
        _fk = [b.get("key") for b in rep.blocks if b["type"] == "figure"]
        check("轴测图排在简图之后、曲线之前",
              "axon" in _fk and _fk.index("axon") > _fk.index("layout")
              and (_fk.index("axon") < _fk.index("deflection")
                   if "deflection" in _fk else True),
              " > ".join(str(k) for k in _fk[:4]))
        check("轴测图图注点明是轴测/未放大变形",
              "轴测" in rep.figures["axon"].caption
              and "未放大变形" in rep.figures["axon"].note,
              rep.figures["axon"].caption)
        check("不该有的图没画（梁格无索力/成桥态无梁格式弯矩包络）",
              ("tension" not in rep.figures if kind == "grillage3d"
               else "moment" not in rep.figures), str(sorted(rep.figures)))
        if kind == "cable_stayed":
            check("成桥态梁内力图走 girder（不是梁格 moment）且图注写明非包络",
                  "girder" in rep.figures
                  and "不是活载包络" in rep.figures["girder"].note,
                  rep.figures["girder"].caption if "girder" in rep.figures else "缺")
            check("活载梁内力包络图存在且图注写明切线刚度口径",
                  "girder_live" in rep.figures
                  and "切线刚度" in rep.figures["girder_live"].note,
                  rep.figures["girder_live"].caption
                  if "girder_live" in rep.figures else "缺")

        paths = R.write_all(rep, f"out/verify_report/{kind}",
                            stem=f"{kind}_report", pdf=has_pdf)
        check("HTML / Word / CSV 三件套都写出来了",
              all(Path(paths[k]).exists() and Path(paths[k]).stat().st_size > 2000
                  for k in ("html", "docx", "zip")),
              "、".join(f"{k} {Path(v).stat().st_size // 1024}KB"
                        for k, v in paths.items()))
        check("PDF 计算书已生成且不是空壳",
              "pdf" in paths
              and Path(paths["pdf"]).stat().st_size > 10000
              and Path(paths["pdf"]).read_bytes()[:5] == b"%PDF-",
              f"{Path(paths['pdf']).stat().st_size // 1024} KB"
              if "pdf" in paths else "缺浏览器，PDF 出口未运行（见首条判据）")

        # ---- PDF 对拍：内容从 PDF 里读回来，与同一份 Report 比对 ----
        if "pdf" in paths:
            page_txt, page_imgs = _text_of_pdf(Path(paths["pdf"]))
            pdf_txt = "\n".join(page_txt)
            check("PDF 是多页计算书（不是一页摘要）",
                  len(page_txt) >= 3 and len(pdf_txt) > 3000,
                  f"{len(page_txt)} 页 / {len(pdf_txt)} 字")
            check("PDF 无空白页（纯图页算有内容）",
                  all(t.strip() or n for t, n in zip(page_txt, page_imgs)),
                  f"空白页 {[i + 1 for i, (t, n) in enumerate(zip(page_txt, page_imgs)) if not t.strip() and not n]}")
            h1s_pdf = [b["text"] for b in rep.blocks if b["type"] == "h1"]
            check("PDF 含全部一级章节（与 blocks 一致）",
                  all(t in pdf_txt for t in h1s_pdf),
                  f"{len(h1s_pdf)} 章")
            first_metric = rep.metrics()[0].value
            check("PDF 数值与 HTML 同源（首个指标卡数值）",
                  first_metric in pdf_txt, f"{first_metric}")
            check("PDF 嵌入了结果图", sum(page_imgs) >= 1,
                  f"{sum(page_imgs)} 张 / 报告 {len(rep.figures)} 张")
            if "capacity_calculation_sheet" in rep.tables:
                sheet = rep.tables["capacity_calculation_sheet"]
                clauses = {str(r[5]) for r in sheet["rows"] if len(r) >= 6}
                hit = sum(1 for c in clauses if c and c in pdf_txt)
                check("PDF 承载力计算书含中间量与条文号",
                      "承载力计算书" in pdf_txt and hit >= 3,
                      f"条文命中 {hit}/{len(clauses)}")

            # ---- 归档增强：页码 / 章节书签 / 元数据 ----
            # write_all 走的是 write_pdf 默认 navigation=True，这里把成品
            # 读回来对拍，确认不是"打印成功就自称归档版"。
            from pypdf import PdfReader

            def _flat(items):
                for it in items:
                    if isinstance(it, list):
                        yield from _flat(it)
                    else:
                        yield it

            rd = PdfReader(str(paths["pdf"]))
            n_pdf = len(page_txt)
            stamps = [f"{i + 1} / {n_pdf}" in txt for i, txt in enumerate(page_txt)]
            check("PDF 每页都有页码「N / 总页数」", all(stamps),
                  f"缺页码页 {[i + 1 for i, v in enumerate(stamps) if not v]}")
            heads = [b["text"] for b in rep.blocks
                     if b["type"] in ("h1", "h2")]
            items = list(_flat(rd.outline))
            titles = [str(x.get("/Title")) for x in items]
            check("PDF 书签条数与报告章节数一致",
                  len(items) == len(heads) and titles == heads,
                  f"{len(items)} 条 / {len(heads)} 章；前 3 {titles[:3]}")
            dests = [rd.get_destination_page_number(x) for x in items]
            check("书签目标页有效且按章节顺序不倒退",
                  all(0 <= d < n_pdf for d in dests)
                  and all(dests[i + 1] >= dests[i]
                          for i in range(len(dests) - 1)),
                  f"{dests}")
            check("PDF 元数据写入结果指纹与中文标题",
                  rep.fingerprint in str((rd.metadata or {}).get("/Keywords", ""))
                  and str((rd.metadata or {}).get("/Title", "")) == rep.title)

        # ---- HTML 对拍：表格行数、指标、校核结论 ----
        html = Path(paths["html"]).read_text(encoding="utf-8")
        check("HTML 无外链资源", "src=\"http" not in html and "<script src" not in html)
        check("HTML 图片内嵌", html.count("data:image/png;base64") == len(rep.figures),
              f"{html.count('data:image/png;base64')} 张")
        rows_html = sum(len(tb["rows"]) for tb in rep.tables.values())
        check("HTML 表格行数与结果一致",
              html.count("<tr>") >= rows_html + len(rep.checks()),
              f"tr={html.count('<tr>')} vs 明细 {rows_html} + 校核 {len(rep.checks())}")
        bad = [c for c in rep.checks() if not c.passed]
        check("HTML 结论标记与 checks 一致（不通过数）",
              html.count("不通过") == len(bad), f"{html.count('不通过')} vs {len(bad)}")
        first = rep.metrics()[0]
        check("HTML 含首张指标卡的数值", first.value in html, f"{first.label}={first.value}")

        # ---- Word 对拍：标题层级、表格张数、关键数字 ----
        from docx import Document

        d = Document(str(paths["docx"]))
        heads = [p.text for p in d.paragraphs if p.style.name == "Heading 1"]
        check("Word 一级标题 = 内容里的 h1 数",
              len(heads) == kinds.count("h1"), f"{len(heads)} vs {kinds.count('h1')}")
        check("Word 无「表 0-」编号",
              "表 0-" not in _text_of_docx(paths["docx"]), "首表不编号")
        txt = _text_of_docx(paths["docx"])
        # 只抓"整个单元格就是 -0.0"的情况：-0.04 是合法数值，不能当缺陷
        _neg0 = [t for t in re.split(r"[\s\u3000]+", txt)
                 if re.fullmatch(r"-0\.0+", t)]
        check("Word 未把 -0.0 写进表格（整格）", not _neg0,
              f"{len(_neg0)} 处：{_neg0[:3]}")
        check("Word 含校核结论文字",
              ("不通过" in txt if bad else "通过" in txt), f"{len(bad)} 项未通过")
        exp_tables = (1 + kinds.count("metrics") + kinds.count("checks")
                      + kinds.count("table"))
        check("Word 表格张数对得上（信息/指标/校核/明细）",
              len(d.tables) == exp_tables, f"{len(d.tables)} vs {exp_tables}")

        # ---- CSV 对拍 ----
        zf = zipfile.ZipFile(paths["zip"])
        names = set(zf.namelist())
        check("CSV 含指标/校核/说明",
              {"00_指标.csv", "01_校核.csv", "README.txt"} <= names, str(sorted(names)))
        for i, key in enumerate(rep.tables, start=2):
            fn = f"{i:02d}_{key}.csv"
            check(f"CSV 有明细表 {key}", fn in names)
            if fn in names:
                body = zf.read(fn).decode("utf-8-sig").strip().splitlines()
                check(f"CSV {key} 行数与结果一致",
                      len(body) == len(rep.tables[key]["rows"]) + 1,
                      f"{len(body) - 1} 行 vs {len(rep.tables[key]['rows'])}")
        check("CSV 带 BOM（Excel 中文列名不乱码）",
              zf.read("00_指标.csv").startswith(b"\xef\xbb\xbf"))

        # ---- 组合章的位置：不许混进明细表 ----
        h1s = [b["text"] for b in rep.blocks if b["type"] == "h1"]
        if "荷载组合" in h1s:
            check("荷载组合章在结论之前、明细表之前",
                  h1s.index("荷载组合") < h1s.index("结论与说明")
                  < h1s.index("明细表"),
                  " > ".join(h1s))
            # 组合控制的**汇总**必须在正文，222 行明细才归附表
            i_tab = next((i for i, b in enumerate(rep.blocks)
                          if b["type"] == "h1" and b["text"] == "明细表"), len(rep.blocks))
            tail = rep.blocks[i_tab:]
            check("组合汇总表未落进明细表章",
                  all(b.get("key") not in ("combo_coef", "combo_summary")
                      for b in tail),
                  "、".join(str(b.get("key")) for b in tail if b["type"] == "table"))

    # ---------------------------------------------------------- 荷载组合
    print("\n[荷载组合：正常路径]")
    # 单独起一个带车道荷载的模型：`cases` 里那个没有 LANE_3D，走的是
    # "缺项就说缺" 的分支，两条路径都得守。
    ml = build_box_grillage_3d(staged=True, lane_code="JTG_D60")
    resl = solve(ml, out_dir="out/verify_report/combo")
    repl = R.build_document(resl, ml, out_dir="out/verify_report/combo")
    tabsl = repl.tables
    check("三张组合表齐全", {"combo_coef", "combo_summary", "combo_ultimate"}
          <= set(tabsl), str(sorted(set(tabsl) & {"combo_coef", "combo_summary",
                                                  "combo_ultimate"})))
    check("组合明细按截面（单元×端）出，不是按单元",
          len(resl.combinations()[0].rows) == len(resl.final_beam)
          and all(k[1] in ("i", "j") for k in resl.combinations()[0].rows),
          f"{len(resl.combinations()[0].rows)} 行 / final_beam "
          f"{len(resl.final_beam)} 行")

    # 独立复算：不调 combination 的函数，照条文式子手算一遍再对比。
    # 这是本层唯一能证明"系数真的乘上去了"的检查——只比结构不看数值，
    # 把 1.4 写成 1.0 也照样通过。
    ult = next(c for c in resl.combinations() if c.spec.key == "ultimate")
    freq = next(c for c in resl.combinations() if c.spec.key == "frequent")
    key = ult.worst("My")[0]
    g_my = resl.final_beam[key]["My"]
    q = resl.live_beam[key]
    ok_ult = all(abs(ult.rows[key][f"My{s}"] - (1.2 * g_my + 1.4 * q[f"My{s}"])) < 1e-6
                 for s in ("Min", "Max"))
    ok_freq = all(abs(freq.rows[key][f"My{s}"] - (g_my + 0.7 * q[f"My{s}"])) < 1e-6
                  for s in ("Min", "Max"))
    check("基本组合 = 1.2·S_G + 1.4·S_Q（控制截面复算）", ok_ult,
          f"单元{key[0]}·{key[1]}端 G={g_my / 1e3:,.1f} "
          f"Q={q['MyMin'] / 1e3:,.1f}~{q['MyMax'] / 1e3:,.1f} kN·m")
    check("频遇组合 = S_G + 0.7·S_Q", ok_freq)

    # 按端组合的意义：梁两端弯矩异号，若先外包再相加，i 端恒载会配上
    # j 端活载。用"同一单元两端的组合值之差"来验：必须等于各自的
    # (1.2·G + 1.4·Q)，而不是同一个数。
    e_multi = next((e for (e, _d) in ult.rows
                    if (e, "i") in ult.rows and (e, "j") in ult.rows
                    and abs(ult.rows[(e, "i")]["MyMin"] - ult.rows[(e, "j")]["MyMin"]) > 1.0),
                   None)
    check("同一单元两端组合值不同（确实按端组合）", e_multi is not None,
          f"单元 {e_multi}")

    # 三个组合的单调关系：γ 递减，组合值 |My| 也应递减。
    def _peak(c):
        w = c.worst("My")
        return abs(w[1]) / 1e3 if w else 0.0
    quasi = next(c for c in resl.combinations() if c.spec.key == "quasi")
    check("基本 > 频遇 > 准永久（控制弯矩递减）",
          _peak(ult) > _peak(freq) > _peak(quasi),
          f"{_peak(ult):,.0f} / {_peak(freq):,.0f} / {_peak(quasi):,.0f} kN·m")
    check("汇总表控制值与 worst() 一致",
          f"{_peak(ult):,.0f}" in tabsl["combo_summary"]["rows"][0][2],
          tabsl["combo_summary"]["rows"][0][2])

    # 位移组合：三项必须由同一对 (sg, sq) 组合出来
    dc = resl.disp_combinations()
    check("位移组合从成桥态取 S_G（不取阶段包络）",
          len(dc) == 3 and abs(dc[0].value - (1.2 * dc[0].sg + 1.4 * dc[0].sq)) < 1e-12,
          f"S_G={dc[0].sg * 1000:.2f} S_Q={dc[0].sq * 1000:.2f} mm" if dc else "无")

    # ---- 缺项路径：没有车道荷载就必须说"这不是组合值" ----
    rep0 = reps["grillage3d"][0]
    html0 = R.render_html(rep0)
    check("无车道荷载时报告明说「不是组合值」", "不是组合值" in html0)
    check("无车道荷载时系数表仍在（读者要知道缺了哪一项）",
          "combo_coef" in rep0.tables)
    check("无车道荷载时汇总表标「只有恒载项」",
          any("只有恒载" in str(t.get("note", ""))
              for k, t in rep0.tables.items() if k.startswith("combo")),
          "combo_*.note")

    # ---- 斜拉桥：不许凭空给组合值 ----
    repc = reps["cable_stayed"][0]
    check("斜拉桥不出组合表", not any(k.startswith("combo") for k in repc.tables),
          str(sorted(repc.tables)))
    check("斜拉桥在校核里已接入活载组合应力验算",
          any("已接入活载组合" in c.detail for c in repc.checks())
          and any("基本组合 1.2G+1.4Q" in c.name for c in repc.checks()))

    # ---------------------------------------------------------- 故障注入
    print("\n[故障注入：渲染器必须把「不通过」写出来]")
    fake = SolveResult(kind="grillage3d", title="注入用例", solver="fake.exe",
                       stats={"nodes": 2, "elements": 1, "dofs": 6},
                       converged=False, iterations=40,
                       disp={(1, 3): (-0.5, -0.5)},
                       diagnostics=["本内核不输出索力"])
    rep = R.Report(title="注入用例", subtitle="三维梁格", kind="grillage3d",
                   blocks=[
                       {"type": "h1", "text": "工程校核"},
                       {"type": "checks", "items": [
                           Check("挠度限值", False, "实测 500.0 mm > 183.3 mm",
                                 value=0.5, limit=0.1833, unit="m"),
                           Check("预抬高量级", True, "0.2 m", level="info")]},
                       {"type": "h1", "text": "结论与说明"},
                       {"type": "bullets", "items": ["**1 项未通过**"]}],
                   meta=[("桥型", "三维梁格")])
    # 故障注入用例只要三件套：不重复启动浏览器，也顺带守住 pdf=False 开关
    paths = R.write_all(rep, "out/verify_report/inject", stem="inject",
                        pdf=False)
    check("pdf=False 显式关掉 PDF 出口", "pdf" not in paths, str(sorted(paths)))
    html = Path(paths["html"]).read_text(encoding="utf-8")
    check("HTML 写入「不通过」", "不通过" in html)
    check("HTML 提示级不标成不通过", html.count("不通过") == 1, f"{html.count('不通过')} 次")
    check("HTML 把 **粗体** 转成标签",
          "<strong>" in html and "**" not in html)
    check("HTML 转义尖括号（防注入）", "<script>" not in html)
    txt = _text_of_docx(paths["docx"])
    check("Word 写入「不通过」", "不通过" in txt)
    check("空报告不崩（无图无表）", len(rep.figures) == 0 and len(rep.tables) == 0)

    print("\n[故障注入：PDF 出口不静默兜底]")
    _saved = os.environ.get(R.PDF_BROWSER_ENV)
    os.environ[R.PDF_BROWSER_ENV] = "D:/__no_such_browser__/nope.exe"
    try:
        try:
            R._pdf_browser()
            _said = ""
        except R.PDFExportError as e:
            _said = str(e)
        check("指定了不存在的浏览器 → 明确报错并给出环境变量名",
              "不存在" in _said and R.PDF_BROWSER_ENV in _said, _said[:80])
        target = Path("out/verify_report/inject/should_not_exist.pdf")
        target.write_bytes(b"OLD-PDF-STUB")
        try:
            R.write_pdf(rep, target)
            _failed = False
        except R.PDFExportError:
            _failed = True
        check("PDF 打印失败时抛错、且旧文件不被误当新产物",
              _failed and target.read_bytes() == b"OLD-PDF-STUB")
    finally:
        if _saved is None:
            os.environ.pop(R.PDF_BROWSER_ENV, None)
        else:
            os.environ[R.PDF_BROWSER_ENV] = _saved
    try:
        check("故障注入后浏览器配置已复原", R._pdf_browser().is_file())
    except R.PDFExportError as e:
        check("故障注入后浏览器配置已复原", False, str(e)[:80])

    print("\n[故障注入：空的 SolveResult 不许伪造数据]")
    empty = SolveResult(kind="cable_stayed", stats={}, disp={}, beam={},
                        cables=[], camber={})
    check("无位移时不出位移图",
          "deflection" not in R.figures(empty, cases[1][1], "out/verify_report/inject"),
          str(sorted(R.figures(empty, cases[1][1], "out/verify_report/inject"))))
    check("无索时不出索力图",
          "tension" not in R.figures(empty, cases[1][1], "out/verify_report/inject"))
    check("无梁端内力时不出成桥态梁内力图",
          "girder" not in R.figures(empty, cases[1][1], "out/verify_report/inject"))

    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(PASS)} 通过")
    if n_pass != len(PASS):
        print("  失败项：" + "、".join(n for n, ok, _ in PASS if not ok))
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
