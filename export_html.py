"""
export_html.py —— 把当前模型 + 求解结果导出成一份**自包含 HTML 报告**
========================================================================
Streamlit 界面需要跑服务才能看；这份 HTML 是静态文件，**双击即可用浏览器打开**，
也方便直接发给同事或存档。所有图形（matplotlib PNG / FEM-Eigen SVG）都以内嵌
base64 或内联 SVG 的形式写进文件，不依赖任何外部资源、不需要联网。

用法：
    python export_html.py                          # 用内置示例模型
    python export_html.py --text "跨度30米简支钢梁，矩形截面高1.2米宽0.6米，20kN/m"
    python export_html.py --no-fem                 # 只做本地草算，不调 FEM-Eigen
    python export_html.py -o my_report.html
"""

from __future__ import annotations

import base64
import html
import io
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

from fe_solver import solve
from viz import draw_deformed, draw_moment, draw_model, draw_section_fig

DEMO_TEXT = "一座跨度30米的简支钢梁，矩形截面高1.2米宽0.6米，承受20kN/m均布荷载"

SVG_LABELS = {
    "model": "模型图",
    "disp": "位移图",
    "moment": "弯矩图",
    "shear": "剪力图",
    "axial": "轴力图",
    "stress_top": "上缘应力",
    "stress_bottom": "下缘应力",
}


def _svg_key(p: Path) -> str:
    stem = p.stem
    for k in SVG_LABELS:
        if stem.endswith("_" + k):
            return k
    return stem.split("_")[-1]


def _fig_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def _inline_svg(p: Path) -> str:
    """内联 SVG：去掉 XML 声明后直接嵌进 HTML，可被浏览器缩放且不失真。"""
    txt = p.read_text(encoding="utf-8", errors="replace")
    txt = txt.replace('<?xml version="1.0" encoding="UTF-8"?>', "").strip()
    i = txt.find("<svg")
    return txt[i:] if i >= 0 else ""


CSS = """
:root{--bd:#d0d7de;--mut:#57606a;--bg:#f6f8fa}
body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
     margin:0;padding:32px;color:#1f2328;background:#fff;line-height:1.6}
h1{font-size:22px;margin:0 0 4px;font-weight:600}
h2{font-size:16px;margin:28px 0 12px;font-weight:600;
   border-bottom:1px solid var(--bd);padding-bottom:6px}
.sub{color:var(--mut);font-size:13px;margin-bottom:20px}
table{border-collapse:collapse;font-size:13px;width:100%;max-width:760px}
th,td{border:1px solid var(--bd);padding:6px 10px;text-align:left}
th{background:var(--bg);font-weight:600;width:150px}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}
.card{border:1px solid var(--bd);border-radius:8px;padding:12px 16px;min-width:150px}
.card .k{font-size:12px;color:var(--mut)}
.card .v{font-size:19px;font-weight:600;margin-top:2px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}
figure{margin:0;border:1px solid var(--bd);border-radius:8px;padding:8px;background:#fff}
figure svg,figure img{width:100%;height:auto;display:block}
figcaption{font-size:12px;color:var(--mut);text-align:center;padding-top:6px}
pre{background:#f6f8fa;border:1px solid var(--bd);border-radius:8px;
    padding:12px;overflow:auto;font-size:12px;max-height:340px}
.note{background:#fff8c5;border:1px solid #d4a72c;border-radius:8px;
      padding:10px 14px;font-size:13px;margin:10px 0}
"""


def write_calculation_html(res, model, out_path: str | Path, *,
                           project: str = "") -> Path:
    """三维计算书的 HTML。内容就是 report_3d 那份 Report，不另写一套结论。"""
    from report_3d import build_document, write_html

    out = Path(out_path)
    rep = build_document(res, model, project=project, out_dir=out.parent)
    return write_html(rep, out)


def write_2d_html(res, spec, out_path: str | Path) -> Path:
    """二维桥梁 HTML。只列求解结果里已有的成桥内力、挠度和规范组合。"""
    title = str(getattr(spec, "title", "") or getattr(res, "title", "") or "二维桥梁")
    rows = []
    for (eid, end), f in sorted(res.final_beam.items()):
        rows.append(
            "<tr>"
            f"<td>{eid}</td><td>{html.escape(str(end))}</td>"
            f"<td>{f.get('N', 0.0) / 1e3:.1f}</td>"
            f"<td>{f.get('My', 0.0) / 1e3:.1f}</td>"
            f"<td>{f.get('Vz', 0.0) / 1e3:.1f}</td>"
            "</tr>"
        )
    defl = res.max_vertical_deflection()
    defl_txt = "—" if defl is None else f"节点 {defl[0]}　{defl[1] * 1000:.2f} mm"
    live_v = ""
    disp = getattr(res, "live_disp", None) or {}
    if disp:
        node, pair = min(disp.items(), key=lambda kv: kv[1][1])
        factor = float(getattr(res, "impact_factor", 1.0) or 1.0)
        if factor <= 0.0:
            factor = 1.0
        bare = pair[1] / factor
        from solve_2d import _max_vertical_span
        span = _max_vertical_span(spec)
        extra = f"不计冲击 {bare * 1000:.2f} mm。"
        if span > 0.0:
            lim = span / 600.0
            extra += (
                f"表 4.3.2 限值 L/600 = {lim * 1000:.1f} mm"
                f"（最大跨径 {span:.0f} m），"
                + ("满足。" if abs(bare) <= lim else "超出。")
            )
        live_v = (
            "<h2>车道活载竖向位移</h2>"
            f"<p>向下最大：节点 {node}　{pair[1] * 1000:.2f} mm（含冲击）。"
            f"{html.escape(extra)}</p>"
        )
    diags = "".join(f"<li>{html.escape(str(d))}</li>" for d in res.diagnostics)
    age_rows = []
    for s in getattr(spec, "stages", []) or []:
        t = float(getattr(s, "time", 0.0) or 0.0)
        if t > 0.0:
            age_rows.append(f"<tr><td>{s.id}</td><td>{t:g}</td></tr>")
    age_html = ""
    if age_rows:
        rh = float(getattr(spec, "creep_rh", 0.0) or 0.0)
        if rh > 0.0:
            from model_doc import jtg3362_nominal_creep, jtg3362_shrinkage_nominal
            h = float(getattr(spec, "creep_h", 0.0) or 0.0)
            fcm = float(spec.creep_fcm)
            t0 = float(getattr(spec, "creep_t0", 7.0) or 7.0)
            if h > 0.0:
                phi0, beta = jtg3362_nominal_creep(rh, h, fcm, t0)
                eps = jtg3362_shrinkage_nominal(rh, fcm)
                thick = (f"名义徐变系数 φ0={phi0:.3f}，βH={beta:.1f}，"
                         f"名义收缩系数 {eps:.3e}。")
            else:
                hs = [float(getattr(t, "h_th_mm", 0.0) or 0.0)
                      for t in getattr(spec, "elem_types", []) or []]
                hs = [x for x in hs if x > 0.0]
                thick = ("未写统一理论厚度，各混凝土截面按外轮廓 h=2A/u："
                         + "、".join(f"{x:.1f} mm" for x in hs) + "。")
            age_note = (
                "混凝土梁和桥塔按 JTG 3362 从这些龄期徐变、收缩。"
                + thick
                + "拉索保持原弹性模量。上面的成桥挠度已经含这段徐变。"
            )
            if getattr(spec, "lanes", None):
                age_note += "车道活载仍按未徐变的弹性模量，不乘 1+χφ。"
        else:
            age_note = "这些龄期写在阶段上，但本次没有按规范曲线计算徐变和收缩。"
        age_html = (
            "<h2>施工阶段龄期</h2>"
            f"<div class=\"note\">{html.escape(age_note)}</div>"
            "<table><tr><th>阶段</th><th>末龄期 (天)</th></tr>"
            + "".join(age_rows) + "</table>"
        )
    env_rows = []
    for eid, f in sorted(getattr(res, "beam", {}).items()):
        env_rows.append(
            "<tr>"
            f"<td>{eid}</td>"
            f"<td>{f.get('Mmin', 0.0) / 1e3:.1f}</td>"
            f"<td>{f.get('Mmax', 0.0) / 1e3:.1f}</td>"
            f"<td>{f.get('Vmin', 0.0) / 1e3:.1f}</td>"
            f"<td>{f.get('Vmax', 0.0) / 1e3:.1f}</td>"
            "</tr>"
        )
    live_rows = []
    for (eid, end), f in sorted(
            (getattr(res, "live_beam", None) or {}).items(),
            key=lambda kv: (kv[0][0], kv[0][1])):
        live_rows.append(
            "<tr>"
            f"<td>{eid}</td><td>{html.escape(str(end))}</td>"
            f"<td>{f.get('NMin', 0.0) / 1e3:.1f}</td>"
            f"<td>{f.get('NMax', 0.0) / 1e3:.1f}</td>"
            f"<td>{f.get('MyMin', 0.0) / 1e3:.1f}</td>"
            f"<td>{f.get('MyMax', 0.0) / 1e3:.1f}</td>"
            f"<td>{f.get('VzMin', 0.0) / 1e3:.1f}</td>"
            f"<td>{f.get('VzMax', 0.0) / 1e3:.1f}</td>"
            "</tr>"
        )
    live_html = ""
    if live_rows:
        live_html = (
            "<h2>车道活载内力包络</h2>"
            "<div class=\"note\">这是车道影响线最不利布载的内力，已含 1+μ。"
            "下面的规范组合已经用过这份包络，不要再加一次。</div>"
            "<table><tr><th>单元</th><th>端</th>"
            "<th>N min (kN)</th><th>N max (kN)</th>"
            "<th>My min (kN·m)</th><th>My max (kN·m)</th>"
            "<th>Vz min (kN)</th><th>Vz max (kN)</th></tr>"
            + "".join(live_rows) + "</table>"
        )
    env_html = ""
    if env_rows:
        env_html = (
            "<h2>施工阶段内力包络</h2>"
            "<div class=\"note\">包络含未成桥的临时构型，例如边跨还是简支时的跨中弯矩。"
            "不能把这份极值与运营期活载相加。</div>"
            "<table><tr><th>单元</th><th>My min (kN·m)</th><th>My max (kN·m)</th>"
            "<th>Vz min (kN)</th><th>Vz max (kN)</th></tr>"
            + "".join(env_rows) + "</table>"
        )
    combo_rows = []
    live_note = ""
    for c in res.combinations():
        worst = c.worst("My")
        if worst is None:
            continue
        (eid, end), val = worst
        combo_rows.append(
            "<tr>"
            f"<td>{html.escape(c.spec.name)}</td>"
            f"<td>{html.escape(c.spec.expr)}</td>"
            f"<td>{eid}·{html.escape(end)}</td>"
            f"<td>{val / 1e3:.1f}</td>"
            "</tr>"
        )
        if not c.has_live:
            live_note = ("<div class=\"note\">没有车道活载包络，组合里只有恒载项。"
                         "这不是完整的正常使用验算。</div>")
    body = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<title>{html.escape(title)}</title>
<style>{CSS}</style></head><body>
<h1>{html.escape(title)}</h1>
<div class="sub">二维桥梁 · {html.escape(res.solver or "FEM-Eigen")} · 指纹 {html.escape(res.fingerprint)}</div>
<h2>最大竖向位移</h2>
<p>{html.escape(defl_txt)}</p>
{age_html}
{live_v}
<h2>成桥态梁端内力</h2>
<table>
<tr><th>单元</th><th>端</th><th>N (kN)</th><th>My (kN·m)</th><th>Vz (kN)</th></tr>
{''.join(rows)}
</table>
{env_html}
{live_html}
<h2>规范组合（控制弯矩）</h2>
{live_note}
<table>
<tr><th>组合</th><th>表达式</th><th>截面</th><th>My (kN·m)</th></tr>
{''.join(combo_rows)}
</table>
{"<h2>诊断</h2><ul>" + diags + "</ul>" if diags else ""}
</body></html>"""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    return out


def export_calculation_book(doc, out_path: str | Path) -> Path:
    """ModelDoc 三维模型：求解后导出与计算书相同的 HTML。"""
    doc.build()
    res = doc.solve(out_dir=Path(out_path).parent / "solve")
    title = str(getattr(doc.model, "title", "") or "")
    return write_calculation_html(res, doc.model, out_path, project=title)


def build_report(
    spec,
    use_fem: bool = True,
    out_path: str | Path = "nl_fe_report.html",
    self_weight: bool = False,
) -> Path:
    result = solve(spec)

    total_load = sum(
        (ld.value * (ld.end - ld.start if ld.end is not None else spec.length))
        if ld.type == "udl" else ld.value
        for ld in spec.loads
    )
    sum_reac = -sum(v.get("Fy", 0) for v in result["reactions"].values()) / 1000.0

    # --- FEM-Eigen ---
    dat_text = ""
    svg_parts: list[tuple[str, str]] = []
    fem_metrics = ""
    fem_note = ""
    if use_fem:
        try:
            from fem_eigen_export import export_dat, parse_result, run_fem_eigen

            outdir = Path("out/html_run")
            outdir.mkdir(parents=True, exist_ok=True)
            dat = export_dat(spec, outdir / "model.dat",
                             self_weight=self_weight)
            dat_text = dat.read_text(encoding="utf-8")
            info = run_fem_eigen(dat, workdir=outdir)
            if info["ok"]:
                for p in info["svgs"]:
                    svg_parts.append((SVG_LABELS.get(_svg_key(p), _svg_key(p)), _inline_svg(p)))
                if info["result"]:
                    rr = parse_result(info["result"])
                    v_f = min((n["v"] for n in rr["nodes"].values()), default=0.0)
                    m_f = max((max(abs(e["M1"]), abs(e["M2"]))
                               for e in rr["elements"].values()), default=0.0)
                    r_f = sum(v["value"] for v in rr["reactions"].values() if v["dof"] == 2)
                    if not self_weight:
                        dv = abs(v_f - result["max_deflection"]) / abs(result["max_deflection"]) * 100 \
                            if result["max_deflection"] else 0.0
                        dm = abs(m_f - abs(result["max_moment"])) / abs(result["max_moment"]) * 100 \
                            if result["max_moment"] else 0.0
                        fem_metrics = (
                            f'<div class="cards">'
                            f'<div class="card"><div class="k">FEM-Eigen 最大挠度</div>'
                            f'<div class="v">{v_f*1000:.3f} mm</div></div>'
                            f'<div class="card"><div class="k">FEM-Eigen 最大弯矩</div>'
                            f'<div class="v">{m_f/1000:.2f} kN·m</div></div>'
                            f'<div class="card"><div class="k">FEM-Eigen 反力合计</div>'
                            f'<div class="v">{r_f/1000:.2f} kN</div></div>'
                            f'<div class="card"><div class="k">与草算最大差异</div>'
                            f'<div class="v">{max(dv, dm):.4f} %</div></div></div>'
                        )
                    else:
                        fem_note = (
                            '<div class="note">已计入自重：本地 anastruct 草算内核不含重力项，'
                            '两者不可比，故未做交叉比对。请以 FEM-Eigen 结果为准。</div>'
                        )
            else:
                fem_note = ("<div class=\"note\">未找到 FEM-Eigen 可执行文件，"
                            "本节只有本地草算结果。</div>")
        except Exception as e:
            fem_note = f'<div class="note">FEM-Eigen 调用失败：{html.escape(str(e))}</div>'

    p = result["props"]
    figs = [
        ("截面", draw_section_fig(spec)),
        ("模型", draw_model(spec)),
        ("变形", draw_deformed(spec, result)),
        ("弯矩", draw_moment(spec, result)),
    ]
    fig_html = "".join(
        f'<figure><img src="data:image/png;base64,{_fig_b64(f)}" alt="{n}"/>'
        f'<figcaption>{n}</figcaption></figure>'
        for n, f in figs
    )
    svg_html = "".join(
        f'<figure>{s}<figcaption>FEM-Eigen · {n}</figcaption></figure>'
        for n, s in svg_parts
    )

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>NL-FE 模型报告 · {html.escape(spec.name)}</title>
<style>{CSS}</style></head><body>
<h1>自然语言有限元前处理 · 模型报告</h1>
<div class="sub">生成时间 {datetime.now():%Y-%m-%d %H:%M} ｜
求解内核：anastruct（草算）{" ＋ FEM-Eigen（交付级）" if use_fem else ""}</div>

<h2>① 模型参数</h2>
<pre>{html.escape(spec.summary())}</pre>
<table>
<tr><th>截面面积 A</th><td>{p['A']:.4f} m²</td></tr>
<tr><th>惯性矩 I</th><td>{p['I']:.5e} m⁴</td></tr>
<tr><th>弹性模量 E</th><td>{spec.E/1e9:g} GPa</td></tr>
<tr><th>材料</th><td>{html.escape(spec.material_name)}</td></tr>
</table>

<h2>② 草算结果（anastruct）</h2>
<div class="cards">
<div class="card"><div class="k">最大挠度</div><div class="v">{result['max_deflection']*1000:.2f} mm</div></div>
<div class="card"><div class="k">最大弯矩</div><div class="v">{result['max_moment']/1000:.1f} kN·m</div></div>
<div class="card"><div class="k">总竖向荷载</div><div class="v">{total_load:.1f} kN</div></div>
<div class="card"><div class="k">支座反力合计</div><div class="v">{sum_reac:.1f} kN</div></div>
</div>
<div class="sub">平衡校验：荷载 {total_load:.1f} kN ≈ 反力 {sum_reac:.1f} kN
（差异 {abs(total_load-sum_reac):.2f} kN）</div>
<div class="grid">{fig_html}</div>

<h2>③ FEM-Eigen 求解与可视化</h2>
{fem_note}{fem_metrics}
<div class="grid">{svg_html or '<div class="sub">（无 SVG）</div>'}</div>
{f'<h2>④ 导出的 .dat（FEM-Eigen new format）</h2><pre>{html.escape(dat_text)}</pre>' if dat_text else ''}
</body></html>"""

    out = Path(out_path)
    out.write_text(doc, encoding="utf-8")
    return out


def main() -> int:
    import argparse

    from model_doc import ModelDoc

    ap = argparse.ArgumentParser(description="导出自包含 HTML 模型报告")
    ap.add_argument("--text", default=DEMO_TEXT, help="自然语言描述")
    ap.add_argument("-o", "--output", default="nl_fe_report.html")
    ap.add_argument("--no-fem", action="store_true", help="不调用 FEM-Eigen（仅单梁）")
    ap.add_argument("--self-weight", action="store_true", help="计入自重（仅单梁）")
    args = ap.parse_args()

    doc, source, _diag = ModelDoc.from_text(args.text)
    print(f"解析来源：{source}")
    if doc.is_3d:
        print(doc.model.title if hasattr(doc.model, "title") else doc.kind)
        out = export_calculation_book(doc, args.output)
    elif doc.kind == "beam":
        print(doc.model.summary())
        out = build_report(doc.model, use_fem=not args.no_fem,
                           out_path=args.output, self_weight=args.self_weight)
    else:
        print(getattr(doc.model, "title", doc.kind))
        res = doc.solve(out_dir=Path(args.output).parent / "solve2d")
        out = write_2d_html(res, doc.model, args.output)
    print(f"\n已生成：{out.resolve()}")
    print(f"大小：{out.stat().st_size/1024:.1f} KB（双击即可用浏览器打开）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
