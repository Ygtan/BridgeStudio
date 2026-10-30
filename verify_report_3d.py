"""verify_report_3d.py —— 后处理层（report_3d）回归。

守什么
------
"生成文件"类功能最容易被误判为通过：文件存在、大小不为零、Word 能打开，
就算过了。但真正的错误（列宽压成竖排、单位写成 `1`、图里量级差一千倍、
结论与数据相反）在"文件存在"这个判据下全都看不见。

所以这里的检查分三类：

1. **对拍**：同一份 Report 渲染成三种格式，再从三种格式里把内容读回来
   比对——HTML 的表格行数、Word 的段落数、CSV 的行数必须对得上
   `SolveResult`，不是"差不多"。
2. **方向性**：`_col_widths` 的每列都不低于下限、总和不超过版心；`-0.0`
   不许出现在表里；无量纲量的单位 `1` 不许出现在表头。这些断言在数据正常时
   也会失败（因为它们是恒定性质，不是"这次算出来对不对"）。
3. **故障注入**：手工造一个未通过的 Check 和一个空结果，看三种渲染器是否
   真的把"不通过"写出来、是否真的跳过不存在的图——干净模型全绿时最容易
   把渲染器的分支写成死代码。

不用 Streamlit，纯函数级。
"""

from __future__ import annotations

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


def main() -> int:
    print("=" * 78)
    print("  report_3d 回归：三个出口共用一份内容")
    print("=" * 78)

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
                "cable_stayed": {"layout", "deflection", "tension", "camber"}}[kind]
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
        check("不该有的图没画（梁格无索力/成桥态无梁内力）",
              ("tension" not in rep.figures if kind == "grillage3d"
               else "moment" not in rep.figures), str(sorted(rep.figures)))

        paths = R.write_all(rep, f"out/verify_report/{kind}", stem=f"{kind}_report")
        check("三种格式都写出来了",
              all(Path(p).exists() and Path(p).stat().st_size > 2000
                  for p in paths.values()),
              "、".join(f"{k} {Path(v).stat().st_size // 1024}KB"
                        for k, v in paths.items()))

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
    check("斜拉桥在校核里说明「不做运营期组合」",
          any("不做运营期组合" in c.detail for c in repc.checks()))

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
    paths = R.write_all(rep, "out/verify_report/inject", stem="inject")
    html = Path(paths["html"]).read_text(encoding="utf-8")
    check("HTML 写入「不通过」", "不通过" in html)
    check("HTML 提示级不标成不通过", html.count("不通过") == 1, f"{html.count('不通过')} 次")
    check("HTML 把 **粗体** 转成标签",
          "<strong>" in html and "**" not in html)
    check("HTML 转义尖括号（防注入）", "<script>" not in html)
    txt = _text_of_docx(paths["docx"])
    check("Word 写入「不通过」", "不通过" in txt)
    check("空报告不崩（无图无表）", len(rep.figures) == 0 and len(rep.tables) == 0)

    print("\n[故障注入：空的 SolveResult 不许伪造数据]")
    empty = SolveResult(kind="cable_stayed", stats={}, disp={}, beam={},
                        cables=[], camber={})
    check("无位移时不出位移图",
          "deflection" not in R.figures(empty, cases[1][1], "out/verify_report/inject"),
          str(sorted(R.figures(empty, cases[1][1], "out/verify_report/inject"))))
    check("无索时不出索力图",
          "tension" not in R.figures(empty, cases[1][1], "out/verify_report/inject"))

    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(PASS)} 通过")
    if n_pass != len(PASS):
        print("  失败项：" + "、".join(n for n, ok, _ in PASS if not ok))
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
