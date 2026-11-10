"""verify_envelope_3d.py —— 3D 活载包络视图守卫。

三条主线：

1. **离线口径**：用 FEM-Eigen 仓库自带的 `bridge_3d_grillage_summary.txt`
   （`live beam envelope` 全零）与手工构造的 SolveResult，把 `status()` 的
   五种分流、峰值表 / 明细表 / 场清单 / 逐端取值逐条对拍。不调求解器。
2. **live 端到端**：梁格（线弹性影响线，My/Vz）与斜拉桥（成桥态切线刚度
   影响线，N/My/Vz）各跑一次，核对云图场、结构云图 PNG、报告章节、图数
   与 HTML base64 对拍、峰值表跟章 / 明细表留文末。
3. **不静默兜底**：全零段、未定义车道、定义了车道却缺段、min>max 时，
   章节仍出、但**不出图不出表**，文案写明原因；本轮纯展示层，12 个建造器
   默认 `.dat` 的 sha256 黄金哈希逐位不变。

口径与实现见 `envelope_3d.py`；运行：`python verify_envelope_3d.py`。
"""

from __future__ import annotations

import hashlib
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import envelope_3d as E
import fem_eigen_3d
import model_doc
import report_3d as R
import view_3d as V
from cable_stayed_3d import build_cable_stayed_3d
from grillage_3d import build_box_grillage_3d
from result_3d import SolveResult
from solver_3d import solve
from verify_stage_tension_3d import DEFAULT_DAT_SHA

ROOT = Path(__file__).resolve().parent
FE_ROOT = ROOT.parent / "FEM-Eigen"
OUT = ROOT / "out" / "verify_envelope_3d"

PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def near(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol * max(abs(b), 1e-12)


def read(p: Path) -> str:
    return io.open(p, encoding="utf-8", errors="replace").read()


def empty_model(lanes=()):
    """只给 status / detail_table 用的最小模型，不写 .dat。"""
    return SimpleNamespace(lanes=list(lanes), nodes=[], elements=[])


def offline() -> None:
    print("\n[1] 离线：内核全零段解析 + 五种状态分流 + 逐端取值口径")
    p = FE_ROOT / "bridge_3d_grillage_summary.txt"
    check("FEM-Eigen 基线 summary 存在", p.is_file(), str(p))
    d = fem_eigen_3d.parse_summary(p)
    zero_lb = d.get("live_beam") or {}
    check("基线含 live beam envelope 行", len(zero_lb) > 0, str(len(zero_lb)))
    check("基线活载包络各分量全零",
          all(abs(float(v.get(c + s, 0.0))) <= E.ZERO_TOL
              for v in zero_lb.values()
              for c in E.LINEAR_COMPS for s in ("Min", "Max")))
    check("基线逐行 min ≤ max",
          all(float(v.get(c + "Min", 0.0)) <= float(v.get(c + "Max", 0.0)) + 1e-9
              for v in zero_lb.values() for c in E.LINEAR_COMPS))

    zr = SolveResult(kind="grillage3d", live_beam=zero_lb)
    check("全零段：source 为空（不冒充可用包络）", E.source(zr) == "")
    check("全零段：has_section 为真（内核确实回过段）", E.has_section(zr))
    check("全零段：components 为空", E.components(zr) == ())
    code, text = E.status(zr, empty_model(lanes=[1]))
    check("全零段：状态 = zero_section", code == "zero_section", code)
    check("全零段：文案写明不出云图 / 不用全零冒充",
          "不出结构云图" in text and "全零" in text, text[:48])
    check("全零段：不给峰值表", E.peak_table(zr) is None)
    check("全零段：不给明细表", E.detail_table(zr, empty_model()) is None)
    check("全零段：不给云图场", E.field_specs(zr) == [])
    check("全零段：没有默认着色场", E.default_field(zr) == "")
    check("全零段：view_3d.fields 不含 live_ 场",
          not any(f.key.startswith("live_") for f in V.fields(zr, empty_model())))

    nl = SolveResult(kind="grillage3d")
    code, text = E.status(nl, empty_model())
    check("未定义车道：状态 = no_lane", code == "no_lane", code)
    check("未定义车道：写明「未请求、不是漏算」",
          "未请求" in text and "不是漏算" in text)
    ml = SolveResult(kind="grillage3d")
    code, text = E.status(ml, empty_model(lanes=[1]))
    check("定义了车道却缺段：状态 = missing_section",
          code == "missing_section", code)
    check("缺段：写明不补零不猜测", "不补零" in text and "不猜测" in text)
    bad = SolveResult(kind="grillage3d", live_beam={
        (1, "i"): {"MyMin": 9e5, "MyMax": -2e5, "VzMin": 0.0, "VzMax": 0.0}})
    check("min > max 被检出", bool(E.invalid_ranges(bad)))
    code, text = E.status(bad, empty_model())
    check("min > max：状态 = invalid_range", code == "invalid_range", code)
    check("min > max：写明不出图不给表", "不出云图" in text and "明细表" in text)

    # 口径文案：三处消费方都用它，缺一句就会被读成「规范最不利包络」。
    scope = E.SCOPE_NOTE
    for kw in ("不是规范全工况最不利包络", "温度", "支座沉降", "风", "人群",
               "横向分布系数法", "角点组合偏保守"):
        check(f"SCOPE_NOTE 含口径「{kw}」", kw in scope)
    for kw in ("过程量", "不与本章活载包络相加", "不补零", "final_beam"):
        check(f"PROCESS_NOTE 含口径「{kw}」", kw in E.PROCESS_NOTE)
    for kw in ("不是规范全工况最不利包络", "不是设计值"):
        check(f"FIELD_NOTE 含口径「{kw}」", kw in E.FIELD_NOTE)
    src = read(ROOT / "envelope_3d.py")
    check("口径层不写 .dat（纯展示层）", "to_dat(" not in src)
    check("口径层不调求解器", "subprocess" not in src and "solve_3d(" not in src)

    # 逐端取值：每端先取 |min|/|max| 大者，再在两端取 |·| 大者；读数给两端。
    fake = SolveResult(kind="grillage3d", live_beam={
        (7, "i"): {"MyMin": -1e6, "MyMax": 2e5, "VzMin": 0.0, "VzMax": 0.0},
        (7, "j"): {"MyMin": 1e5, "MyMax": 9e5, "VzMin": 0.0, "VzMax": 0.0},
    })
    check("合成非零包络：source = live_beam", E.source(fake) == "live_beam")
    check("合成非零包络：只出有量的分量", E.components(fake) == ("My",))
    val, txt = E.member_value(fake, "My", 7, 1e-3)
    check("逐端取值：全局 |·| 最大者 = -1000 kN·m",
          val is not None and near(val, -1000.0), str(val))
    check("逐端读数含 i / j 两端", "i 端" in txt and "j 端" in txt, txt[:60])
    check("不存在的分量不补零", E.member_value(fake, "N", 7, 1e-3)[0] is None)
    check("不存在的单元不出值", E.member_value(fake, "My", 999, 1e-3)[0] is None)
    rows = E.band_rows(fake)
    check("明细行按 (单元, i→j) 排序",
          [r[1] for r in rows] == ["i", "j"], str(rows))
    pk = E.peak_table(fake)
    check("峰值表：每个非零分量一行", pk is not None and len(pk["rows"]) == 1)
    check("峰值表：6 列契约", pk is not None
          and pk["headers"] == ["分量", "包络 min", "包络 max", "控制 |·|",
                                "出现位置", "单位"])
    check("峰值表：控制 |·| = 1000.00",
          pk is not None and pk["rows"][0][3] == "1,000.00",
          str(pk["rows"][0][3]) if pk else "缺")
    check("峰值表：出现位置带单元/端",
          pk is not None and "单元 7" in pk["rows"][0][4])
    check("峰值表：表注带数据源与口径",
          pk is not None and "live beam envelope" in pk["note"]
          and "不是规范全工况最不利包络" in pk["note"])
    dt = E.detail_table(fake, empty_model())
    check("明细表：行数 = 内核行数", dt is not None and len(dt["rows"]) == 2)
    check("明细表：只列有的分量（线性无 NMin）",
          dt is not None and not any("NMin" in h for h in dt["headers"]),
          str(dt["headers"]) if dt else "缺")
    check("明细表：表注含三条口径",
          dt is not None and "缺的不补零" in dt["note"]
          and "不是规范全工况最不利包络" in dt["note"]
          and "过程量" in dt["note"])
    specs = E.field_specs(fake)
    check("场清单：每个非零分量一个", len(specs) == 1
          and specs[0]["key"] == "live_My")
    check("场清单：单位 / 显示换算 / 正负", specs[0]["unit"] == "kN·m"
          and specs[0]["factor"] == 1e-3 and specs[0]["symmetric"] is True)
    check("场标签写明「本模型车道」", "本模型车道" in specs[0]["label"])
    check("默认场取第一个有量分量", E.default_field(fake) == "live_My")

    # 索承体系：端标签 1/2，分量多一个 N。
    cable = SolveResult(kind="cable_stayed", girder_live={
        (11, 1): {"NMin": -2e6, "NMax": 1e6, "MyMin": 0.0, "MyMax": 0.0,
                  "VzMin": 0.0, "VzMax": 0.0},
        (11, 2): {"NMin": -1e6, "NMax": 3e6, "MyMin": 0.0, "MyMax": 0.0,
                  "VzMin": 0.0, "VzMax": 0.0},
    })
    check("索承体系：source = girder_live", E.source(cable) == "girder_live")
    check("索承体系：只出有量的 N", E.components(cable) == ("N",))
    check("索承体系：端标签为 1/2",
          [r[1] for r in E.band_rows(cable)] == [1, 2])
    check("索承体系：逐端取值按 1e-3 换算",
          near(E.member_value(cable, "N", 11, 1e-3)[0] or 0.0, 3000.0))
    cdt = E.detail_table(cable, empty_model())
    check("索承体系：明细表带 NMin/NMax",
          cdt is not None and "NMin (kN)" in cdt["headers"]
          and "NMax (kN)" in cdt["headers"])


def live() -> None:
    print("\n[2] live：梁格（线弹性影响线）+ 斜拉桥（成桥态切线刚度影响线）")
    m = build_box_grillage_3d(staged=True, lane_code="JTG_D60")
    r = solve(m, out_dir=OUT / "grillage")
    mc, _info = build_cable_stayed_3d()
    rc = solve(mc, kind="cable_stayed", out_dir=OUT / "cable")

    check("梁格：source = live_beam", E.source(r) == "live_beam")
    check("梁格：分量只有 My / Vz", E.components(r) == ("My", "Vz"),
          str(E.components(r)))
    check("梁格：status = ok", E.status(r, m)[0] == "ok")
    check("梁格：内核给了冲击系数",
          float(r.meta.get("impact_factor") or 0.0) > 1.0,
          str(r.meta.get("impact_factor")))
    check("斜拉：source = girder_live", E.source(rc) == "girder_live")
    check("斜拉：分量含 N / My / Vz",
          E.components(rc) == ("N", "My", "Vz"), str(E.components(rc)))
    check("斜拉：status = ok", E.status(rc, mc)[0] == "ok")

    fg = {f.key: f for f in V.fields(r, m)}
    fc = {f.key: f for f in V.fields(rc, mc)}
    check("梁格场：新增 live_My / live_Vz", {"live_My", "live_Vz"} <= set(fg))
    check("梁格场：不出 live_N（内核没给 N 活载包络）", "live_N" not in fg)
    check("斜拉场：新增 live_N / live_My / live_Vz",
          {"live_N", "live_My", "live_Vz"} <= set(fc))
    check("默认着色场不变（梁格 combo_My / 斜拉 T）",
          V.default_field(r, m) == "combo_My" and V.default_field(rc, mc) == "T")
    for tag, fs in (("梁格", fg), ("斜拉", fc)):
        for k, f in fs.items():
            if not k.startswith("live_"):
                continue
            check(f"{tag} 场 {k}：单位非空", bool(f.unit))
            check(f"{tag} 场 {k}：标签标明本模型车道",
                  "本模型车道" in f.label)
            check(f"{tag} 场 {k}：note 不冒充规范最不利",
                  "不是规范全工况最不利包络" in f.note)

    sc = V.build_scene(r, m, "live_My")
    check("梁格结构云图场景有值", sc.has_values and sc.vmax > 0,
          f"vmax={sc.vmax:,.1f}")
    check("云图控制构件非空", sc.control is not None)
    hovers = [mm.hover for mm in sc.members if mm.value is not None]
    check("云图悬停读数给两端 min/max",
          bool(hovers) and all("i 端" in h and "j 端" in h for h in hovers[:20]),
          hovers[0][:70] if hovers else "缺")
    pk = E.peak_table(r)
    check("云图色标上限 = 峰值表控制 |·|",
          pk is not None and near(sc.vmax,
                                  float(pk["rows"][0][3].replace(",", "")), 1e-5),
          f"{sc.vmax:,.2f} vs {pk['rows'][0][3] if pk else '缺'}")
    check("云图控制构件 = 峰值表出现位置",
          pk is not None and sc.control is not None
          and str(sc.control[0]) in pk["rows"][0][4],
          f"场景 {sc.control[0] if sc.control else '缺'} / {pk['rows'][0][4] if pk else '缺'}")
    check("斜拉结构云图 N 场有值",
          V.build_scene(rc, mc, "live_N").has_values)

    for tag, res, model, src in (("梁格", r, m, "live_beam"),
                                 ("斜拉", rc, mc, "girder_live")):
        check(f"{tag}：live 行 min ≤ max 逐条成立",
              all(float(v.get(c + "Min", 0.0)) <= float(v.get(c + "Max", 0.0)) + 1e-6
                  for v in (res.live_beam if src == "live_beam"
                            else res.girder_live).values()
                  for c in E.components(res, src)))
        dt = E.detail_table(res, model)
        pool = res.live_beam if src == "live_beam" else res.girder_live
        check(f"{tag}：明细表行数 = 内核行数",
              dt is not None and len(dt["rows"]) == len(pool),
              f"{len(dt['rows']) if dt else 0} / {len(pool)}")
        tabs = res.tables(model)
        check(f"{tag}：tables() 收录 live_envelope_table",
              "live_envelope_table" in tabs)
        check(f"{tag}：tables() 与口径层同源",
              tabs["live_envelope_table"]["rows"] == dt["rows"])
        check(f"{tag}：未定义阶段包络不相加（明细表只用活载段）",
              "过程量" in tabs["live_envelope_table"]["note"]
              and "不与本章活载包络相加" in tabs["live_envelope_table"]["note"])

    # ---- 报告：章节 / 图 / 表 / HTML 对拍 ----
    for tag, res, model, expect_band in (("梁格", r, m, True),
                                         ("斜拉", rc, mc, False)):
        rep = R.build_document(res, model, out_dir=OUT / f"report_{tag}")
        h1 = [b["text"] for b in rep.blocks if b["type"] == "h1"]
        check(f"{tag} 报告：有「3D 包络视图」章", "3D 包络视图" in h1)
        check(f"{tag} 报告：结论仍在明细表之前",
              h1.index("结论与说明") < h1.index("明细表"))
        check(f"{tag} 报告：结构云图已生成",
              R.ENVELOPE_FIGURE_KEY in rep.figures
              and rep.figures[R.ENVELOPE_FIGURE_KEY].path.exists()
              and rep.figures[R.ENVELOPE_FIGURE_KEY].path.stat().st_size > 20000)
        check(f"{tag} 报告：填充带图按数据源出",
              ("live_envelope_band" in rep.figures) == expect_band,
              str(sorted(rep.figures)))
        check(f"{tag} 报告：索承体系沿用 girder_live 不重复出图",
              expect_band or "girder_live" in rep.figures)
        figblocks = [b.get("key") for b in rep.blocks if b["type"] == "figure"]
        check(f"{tag} 报告：每张图恰好一个 block",
              len(figblocks) == len(set(figblocks))
              and set(figblocks) == set(rep.figures))
        html = R.render_html(rep)
        check(f"{tag} HTML：base64 图数 = figures 数",
              html.count("data:image/png;base64") == len(rep.figures),
              f"{html.count('data:image/png;base64')} / {len(rep.figures)}")
        p = next(b["text"] for b in rep.blocks
                 if b["type"] == "p" and "三类量严格分开" in b["text"])
        check(f"{tag} 章口径段：三类量分开", "三类量严格分开" in p)
        check(f"{tag} 章口径段：写明本次数据源",
              "live beam envelope" in p or "[GIRDER_LIVE]" in p)
        notes = " ".join(b["text"] for b in rep.blocks if b["type"] == "note")
        check(f"{tag} 章 note：不冒充规范最不利",
              "不是规范全工况最不利包络" in notes)
        check(f"{tag} 章 note：施工过程量不相加",
              "不与本章活载包络相加" in notes)
        check(f"{tag} 报告：峰值表跟章走（只出现一次）",
              sum(1 for b in rep.blocks if b["type"] == "table"
                  and b["key"] == R.ENVELOPE_PEAK_KEY) == 1)
        check(f"{tag} 报告：明细表留文末且不重复",
              sum(1 for b in rep.blocks if b["type"] == "table"
                  and b["key"] == R.ENVELOPE_DETAIL_KEY) == 1)
        fig = rep.figures[R.ENVELOPE_FIGURE_KEY]
        check(f"{tag} 云图图注：不插值 / 口径",
              "不在单元内插值" in fig.note
              and "不是规范全工况最不利包络" in fig.note)

    p = next(b["text"] for b in R.build_document(r, m, out_dir=OUT / "report_p").blocks
             if b["type"] == "p" and "三类量严格分开" in b["text"])
    check("梁格章写明 1+μ 已计入内力包络、位移不计冲击",
          "1+μ" in p and "不计冲击" in p, p[-70:])


def no_fallback() -> None:
    print("\n[3] 不静默兜底：全零 / 未定义车道时章节照出但不出图不出表")
    m = build_box_grillage_3d(staged=True, lane_code="")
    zr = SolveResult(kind="grillage3d", live_beam={
        (101, "i"): {"MyMin": 0.0, "MyMax": 0.0, "VzMin": 0.0, "VzMax": 0.0}})
    rep = R.build_document(zr, m, out_dir=OUT / "report_zero")
    h1 = [b["text"] for b in rep.blocks if b["type"] == "h1"]
    check("全零：章节仍在（不整章消失）", "3D 包络视图" in h1)
    check("全零：不生成结构云图", R.ENVELOPE_FIGURE_KEY not in rep.figures)
    check("全零：不生成填充带图", "live_envelope_band" not in rep.figures)
    check("全零：不给峰值表", R.ENVELOPE_PEAK_KEY not in rep.tables)
    check("全零：不给明细表", R.ENVELOPE_DETAIL_KEY not in rep.tables)
    p = next(b["text"] for b in rep.blocks
             if b["type"] == "p" and "三类量严格分开" in b["text"])
    check("全零：文案写明不出结构云图", "不出结构云图" in p, p[-70:])
    html = R.render_html(rep)
    check("全零：HTML 图数仍与 figures 对拍",
          html.count("data:image/png;base64") == len(rep.figures))

    nr = SolveResult(kind="grillage3d")
    rep2 = R.build_document(nr, m, out_dir=OUT / "report_nolane")
    p2 = next(b["text"] for b in rep2.blocks
              if b["type"] == "p" and "三类量严格分开" in b["text"])
    check("未定义车道：写明未请求、不是漏算",
          "未请求" in p2 and "不是漏算" in p2, p2[-70:])
    check("未定义车道：不出图不出表",
          R.ENVELOPE_FIGURE_KEY not in rep2.figures
          and R.ENVELOPE_DETAIL_KEY not in rep2.tables)

    print("\n[4] 纯展示层：12 个建造器默认 .dat 黄金哈希逐位不变")
    for (kind, dim), b in model_doc.BUILDERS.items():
        dat = model_doc.ModelDoc(kind, dict(model_doc.DEFAULTS[b]),
                                 dimension=dim).to_dat()
        h = hashlib.sha256(dat.encode()).hexdigest()[:16]
        check(f"默认 .dat 黄金哈希：{b}", h == DEFAULT_DAT_SHA[b], h)


def main() -> int:
    print("=" * 78)
    print("3D 活载包络视图（离线口径 + live 云图/报告 + 不静默兜底 + .dat 不变）")
    offline()
    live()
    no_fallback()
    n_pass = sum(1 for ok, _, _ in PASS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(PASS)} 通过")
    if n_pass != len(PASS):
        print("  失败项：" + "、".join(n for n, ok, _ in PASS if not ok))
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
