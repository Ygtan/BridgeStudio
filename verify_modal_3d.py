"""verify_modal_3d.py —— 模态 / 自振特性（内核输出 → 解析 → 结果 → 四出口）回归。

覆盖五件事：

1. **解析层纯函数**：手写 modal 段逐字段对拍（数值硬编码在断言里，不用
   被验代码反算）、未知行向前兼容、ERROR 段不静默。
2. **派生量**：主导方向、有效参与质量累计、竖向基频、振型序列排序、
   validate 的各类故障注入（非正频率、乱序、非归一、负质量、不同源）。
3. **段分派不串段**：新增 `modal` / `[MODAL]` 段不能污染 `lane impact`、
   `[U]`、`live beam envelope` 等既有段。
4. **独立 oracle**：`verify_3d_modal.dat` 是 10 m 简支梁，
   Euler-Bernoulli 闭式解 f₁ = π/(2L²)·√(EI/(ρA)) = 119.9534 Hz；
   同时与项目黄金基线 `tests/regression/baselines/verify_3d_modal.modal.txt`
   的前 6 阶逐一对比。这不是"内核自己跟自己对"，是解析解对拍。
5. **端到端**：梁格 / 斜拉桥求解 → SolveResult → 指标卡 / 校核 / 明细表 →
   报告独立章节与振型图 → HTML / DOCX / CSV / PDF 四种出口。另含无质量
   负例（ERROR=no_mass），确认算不出时不摆一张空表冒充通过。

用法：``python verify_modal_3d.py``（``--offline`` 只跑不依赖内核的判据）。
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from console_utf8 import setup_console

setup_console()
sys.path.insert(0, str(Path(__file__).parent))

import fem_eigen_3d
import fem_eigen_cable
import modal_3d
import report_3d
from fem_eigen_export import FEM_EIGEN_DIR
from result_3d import SolveResult

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def near(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


# ---------------------------------------------------------------- 手写段
_SAMPLE = """
NMODES=2 NDOF=6 BASIS=xyz
MASS X=1000 Y=1000 Z=1000
F 1 2.5 0.4 800 10 5 1
S 1 2 0.01 0.02 0.03
S 1 1 0.00 0.00 0.00
F 2 5.0 0.2 10 5 800 1
S 2 1 0.10 0.20 0.30
S 2 2 0.11 0.21 0.31
FUTURE_FIELD foo=1
""".strip().splitlines()


def offline_checks() -> None:
    print("\n[1] 解析层：手写段逐字段对拍")
    md = modal_3d.parse_modal_section(_SAMPLE)
    check("段头 / 总质量", md["nmodes"] == 2 and md["ndof"] == 6
          and md["basis"] == "xyz"
          and all(near(md["mass"][d], 1000.0) for d in "XYZ"),
          f"NMODES={md['nmodes']} NDOF={md['ndof']} mass={md['mass']}")
    check("两阶频率/周期/参与质量/归一值", len(md["modes"]) == 2
          and near(md["modes"][0]["f"], 2.5)
          and near(md["modes"][0]["T"], 0.4)
          and near(md["modes"][0]["mx"], 800.0)
          and near(md["modes"][0]["mz"], 5.0)
          and near(md["modes"][0]["mnorm"], 1.0)
          and near(md["modes"][1]["f"], 5.0)
          and near(md["modes"][1]["mz"], 800.0))
    check("振型按节点存三个平动分量", md["modes"][0]["shape"][1] == (0.0, 0.0, 0.0)
          and all(near(a, b) for a, b in
                  zip(md["modes"][0]["shape"][2], (0.01, 0.02, 0.03))),
          f"shape={md['modes'][0]['shape']}")
    check("未知行不破坏解析（向前兼容）", len(md["modes"]) == 2
          and not md["error"])

    print("\n[2] 派生量：主导方向 / 参与质量 / 振型序列")
    check("主导方向按参与质量取最大",
          modal_3d.mode_direction(md["modes"][0]) == "X"
          and modal_3d.mode_direction(md["modes"][1]) == "Z")
    zeros = {"order": 9, "f": 9.0, "T": 0.1, "mx": 0.0, "my": 0.0,
             "mz": 0.0, "mnorm": 1.0, "shape": {}}
    check("三向参与质量都近零 → 标「—」而非硬报方向",
          modal_3d.mode_direction(zeros, 1000.0) == "—")
    ratios = modal_3d.cumulative_ratio(md, "Z")
    check("累计竖向参与质量手算 = 0.005 / 0.805",
          len(ratios) == 2 and near(ratios[0], 0.005)
          and near(ratios[1], 0.805), f"{ratios}")
    check("竖向主导最低阶是第 2 阶（不是最低阶）",
          near(modal_3d.vertical_fundamental(md) or 0.0, 5.0))
    nodes = {1: (0.0, 0.0, 0.0), 2: (5.0, 0.0, 0.0)}
    xs, ws = modal_3d.mode_shape_series(md, 2, nodes, "x", 2)
    check("振型序列按 x 升序且取竖向分量",
          xs == [0.0, 5.0] and len(ws) == 2 and near(ws[0], 0.30)
          and near(ws[1], 0.31), f"x={xs} w={ws}")
    _x, vs = modal_3d.mode_shape_series(md, 2, nodes, "x", 1)
    check("同一函数可取横向分量 v", len(vs) == 2 and near(vs[0], 0.20))
    check("缺阶返回空序列（不画空坐标轴）",
          modal_3d.mode_shape_series(md, 99, nodes) == ([], []))

    print("\n[3] validate：故障注入")
    check("干净段无问题", modal_3d.validate(md, 2.5) == [])
    bad = modal_3d.parse_modal_section(
        ["NMODES=1 NDOF=3 BASIS=xyz", "ERROR=no_mass", "MASS X=0 Y=0 Z=0"])
    check("ERROR 段给出可读中文原因",
          any("质量" in s for s in modal_3d.validate(bad)),
          str(modal_3d.validate(bad)))
    check("缺段不静默",
          any("求解器未输出" in s for s in
              modal_3d.validate(modal_3d.empty_modal("no_modal_section"))))
    f_bad = md["modes"][0].copy()
    f_bad["f"] = 0.0
    check("非正频率被抓出",
          any("非正频率" in s for s in
              modal_3d.validate({**md, "modes": [f_bad, md["modes"][1]]})))
    swap = [md["modes"][1].copy(), md["modes"][0].copy()]
    check("频率乱序被抓出", any("升序" in s for s in
                              modal_3d.validate({**md, "modes": swap})))
    n_bad = md["modes"][0].copy(); n_bad["mnorm"] = 2.0
    check("振型未按质量归一被抓出",
          any("归一" in s for s in
              modal_3d.validate({**md, "modes": [n_bad, md["modes"][1]]})))
    m_bad = md["modes"][0].copy(); m_bad["mx"] = -1.0
    check("负参与质量被抓出",
          any("负的有效参与质量" in s for s in
              modal_3d.validate({**md, "modes": [m_bad, md["modes"][1]]})))
    check("与冲击系数同源时通过、不同源时抓出",
          modal_3d.validate(md, 2.5) == []
          and any("不同源" in s for s in modal_3d.validate(md, 3.7)),
          str(modal_3d.validate(md, 3.7)))

    print("\n[4] 明细表与工程提示")
    tb = modal_3d.modal_table(md)
    check("表头九列 / 两行 / 单位齐全",
          len(tb["headers"]) == 9 and len(tb["rows"]) == 2
          and "(Hz)" in tb["headers"][1] and "(t)" in tb["headers"][6],
          f"{tb['headers']}")
    check("表说明写清 K/M 来源与截断限制",
          "Kφ=λMφ" in tb["note"] and "DENS×A" in tb["note"]
          and "反应谱" in tb["note"])
    check("低占比提示不宜做反应谱", "反应谱" in modal_3d.participation_note(md))
    rich = {**md, "mass": {"X": 1000.0, "Y": 1000.0, "Z": 1000.0},
            "modes": [{**md["modes"][0], "mz": 950.0}]}
    check("高占比提示截断足够", "足够" in modal_3d.participation_note(rich))
    empty = modal_3d.empty_modal()
    check("空结构不含假数据", empty["nmodes"] == 0 and not empty["modes"])

    print("\n[5] 段分派：新增段不污染既有段")
    mixed = "\n".join([
        "stage displacement envelope", "N1 dof3 min=-0.001 max=0.002",
        "lane impact", "f1_vertical=2.62564 impact_mu=0.1 impact_factor=1.1",
        "modal", "NMODES=1 NDOF=6 BASIS=xyz",
        "MASS X=100 Y=100 Z=100", "F 1 2.62564 0.38086 1 2 97 1",
        "S 1 1 0 0 0.1", "live beam envelope",
        "E1 i MyMin=0 MyMax=1 VzMin=0 VzMax=1", ""])
    p = Path(tempfile.mkdtemp(prefix="vmodal_")) / "s.txt"
    p.write_text(mixed, encoding="utf-8")
    r = fem_eigen_3d.parse_summary(p)
    check("summary 各段各归各位",
          near(r["lane_impact"]["f1_vertical"], 2.62564)
          and r["disp"][(1, 3)] == (-0.001, 0.002)
          and len(r["live_beam"]) == 1
          and r["modal"]["nmodes"] == 1
          and near(r["modal"]["modes"][0]["f"], 2.62564),
          f"lane={r['lane_impact']} modal={r['modal']['nmodes']}")
    cm = "\n".join([
        "cable_solver v1", "converged=1 iterations=3 elapsed_ms=1.0",
        "[U]", "1 3 -0.001",
        "[MODAL]", "NMODES=1 NDOF=6 BASIS=xyz",
        "MASS X=100 Y=100 Z=100", "F 1 1.3262 0.754 0 0 100 1",
        "S 1 1 0 0 0.1", "[END]", ""])
    p2 = Path(tempfile.mkdtemp(prefix="vmodal_")) / "c.txt"
    p2.write_text(cm, encoding="utf-8")
    cr = fem_eigen_cable.parse_cable_result(p2)
    check("cable 各段各归各位",
          cr["meta"]["converged"] == 1 and near(cr["u"][(1, 3)], -0.001)
          and cr["modal"]["nmodes"] == 1
          and near(cr["modal"]["modes"][0]["f"], 1.3262))
    no_sec = Path(tempfile.mkdtemp(prefix="vmodal_")) / "old.txt"
    no_sec.write_text("cable_solver v1\n[END]\n", encoding="utf-8")
    check("旧版结果缺段被标注为 no_modal_section",
          fem_eigen_cable.parse_cable_result(no_sec)["modal"]["error"]
          == "no_modal_section")


# ---------------------------------------------------------------- 在线
def _exe(name: str) -> Path:
    p = Path(FEM_EIGEN_DIR) / "build" / name
    if not p.is_file():
        raise FileNotFoundError(f"缺少内核可执行文件：{p}")
    return p


def _run_grillage(dat: Path) -> dict:
    out = Path(tempfile.mkdtemp(prefix="vmodal_run_"))
    dst = out / "beam.dat"
    shutil.copy(dat, dst)
    proc = subprocess.run([str(_exe("example_3d_grillage.exe")), str(dst)],
                          capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"example_3d_grillage 失败：{proc.stderr[:300]}")
    return fem_eigen_3d.parse_summary(out / "beam_summary.txt")


def online_checks() -> None:
    print("\n[6] 独立 oracle：10 m 简支梁闭式解 + 黄金基线")
    dat = Path(FEM_EIGEN_DIR) / "verify_3d_modal.dat"
    r = _run_grillage(dat)
    md = r["modal"]
    check("简支梁产出 8 阶、无报错段",
          md["nmodes"] >= 6 and not md["error"] and len(md["modes"]) >= 6,
          f"NMODES={md['nmodes']} error={md['error']}")
    # f1 = π/(2L²)·√(EI/(ρA))，L=10 m，E=206 GPa，I=IY=0.4 m⁴，
    # A=0.18 m²，ρ=7850 kg/m³（值取自 verify_3d_modal.dat，不反向读代码）。
    f_exact = math.pi / (2.0 * 10.0 ** 2) * math.sqrt(
        206e9 * 0.4 / (7850.0 * 0.18))
    f1 = md["modes"][0]["f"]
    check("f₁ 与 Euler-Bernoulli 闭式解误差 < 0.5%",
          abs(f1 - f_exact) / f_exact < 0.005,
          f"FEM={f1:.4f} Hz  解析={f_exact:.4f} Hz  "
          f"误差={abs(f1 - f_exact) / f_exact * 100:.4f}%")
    baseline = (Path(FEM_EIGEN_DIR) / "tests" / "regression" / "baselines"
                / "verify_3d_modal.modal.txt")
    gold = [float(v) for v in re.findall(
        r"^f\d+\s*=\s*([\d.]+)\s*Hz",
        baseline.read_text(encoding="utf-8"), re.M)]
    got = [m["f"] for m in md["modes"][:len(gold)]]
    check("前 6 阶与黄金基线逐一吻合（0.01 Hz）",
          len(gold) >= 6 and len(got) >= 6
          and all(abs(a - b) < 0.01 for a, b in zip(got, gold)),
          f"got={[round(v, 3) for v in got]} gold={gold}")
    check("周期 = 1/f、φᵀMφ = 1、参与质量非负",
          all(near(m["T"] * m["f"], 1.0, 1e-6)
              and near(m["mnorm"], 1.0, 1e-6)
              and min(m["mx"], m["my"], m["mz"]) >= -1e-6
              for m in md["modes"]))
    check("频率严格升序且全为正",
          all(m["f"] > 0 for m in md["modes"])
          and all(md["modes"][i + 1]["f"] > md["modes"][i]["f"]
                  for i in range(len(md["modes"]) - 1)))
    check("validate 对该结果无异议", modal_3d.validate(md) == [])

    print("\n[7] 负例：无质量不静默、不摆空表")
    txt = dat.read_text(encoding="utf-8")
    nodens = txt.replace("DENS=7850", "DENS=0")
    p = Path(tempfile.mkdtemp(prefix="vmodal_")) / "nodens.dat"
    p.write_text(nodens, encoding="utf-8")
    r0 = _run_grillage(p)
    m0 = r0["modal"]
    check("内核给出 ERROR=no_mass", m0["error"] == "no_mass",
          f"error={m0['error']}")
    check("下游给出可读原因", "质量" in "".join(modal_3d.validate(m0)))
    res0 = SolveResult(kind="grillage3d", modal=m0)
    check("无模态数据时不生成 modal_table",
          "modal_table" not in res0.tables(None))
    check("指标卡明确写「未产出」而非空值",
          any(m.label == "一阶自振频率" and m.value == "未产出"
              for m in res0.metrics(None)))

    print("\n[8] 端到端：梁格 → 结果层 → 报告 → 四出口")
    import model_doc

    doc, _, _ = model_doc.ModelDoc.from_text(
        "三跨连续箱梁，边跨30米中跨50米，梁高2.5米，箱宽12米，逐跨施工")
    res = doc.solve(out_dir=tempfile.mkdtemp(prefix="vmodal_solve_"))
    rmd = res.modal
    ref = float(res.meta.get("f1_vertical") or 0.0) or None
    check("SolveResult 带完整模态", len(rmd.get("modes") or []) == 8
          and not rmd.get("error"))
    check("与冲击系数 f1_vertical 同源（validate 无异议）",
          modal_3d.validate(rmd, ref) == [], f"f1_vertical={ref}")
    tabs = res.tables(doc.model)
    check("明细表 modal_table 行数 = 阶数",
          "modal_table" in tabs and len(tabs["modal_table"]["rows"]) == 8)
    check("指标卡含一阶自振频率",
          any(m.label == "一阶自振频率" for m in res.metrics(doc.model)))
    mchecks = [c for c in res.checks(doc.model)
               if "模态" in c.name or "自振" in c.name or "参与质量" in c.name]
    check("工程校核含三条模态 info 且都通过",
          len(mchecks) == 3 and all(c.passed and c.level == "info"
                                   for c in mchecks),
          f"{[(c.name, c.level) for c in mchecks]}")

    out = Path(tempfile.mkdtemp(prefix="vmodal_report_"))
    rep = report_3d.build_document(res, doc.model, out_dir=out)
    h1 = [b["text"] for b in rep.blocks if b["type"] == "h1"]
    check("报告有独立「模态与自振特性」章", "模态与自振特性" in h1,
          f"{h1}")
    check("modal_table 在正文只出现一次（文末明细表不重复）",
          sum(1 for b in rep.blocks
              if b["type"] == "table" and b["key"] == "modal_table") == 1)
    figs = [k for k in rep.figures if k.startswith("mode_shape_")]
    check("生成了振型图且跳过纯纵向/横向阶", 1 <= len(figs) <= 3,
          f"{figs}")
    html = report_3d.render_html(rep)
    check("HTML 含章节、表头与归一值说明",
          "模态与自振特性" in html and "各阶自振频率" in html
          and "φᵀMφ" in html)
    paths = report_3d.write_all(rep, out, stem="modal", pdf=False)
    from docx import Document

    d = Document(str(paths["docx"]))
    dtext = "\n".join(p.text for p in d.paragraphs)
    dcell = "\n".join(c.text for tb in d.tables
                      for row in tb.rows for c in row.cells)
    check("DOCX 含章节与模态表",
          "模态与自振特性" in dtext and "φᵀMφ" in dcell
          and "各阶自振频率" in (dtext + dcell))
    with zipfile.ZipFile(str(paths["zip"])) as z:
        names = z.namelist()
        csv_name = next((n for n in names if "modal_table" in n), None)
        lines = (z.read(csv_name).decode("utf-8-sig").splitlines()
                 if csv_name else [])
    check("CSV 模态表逐行导出（8 行 + 表头）",
          len(lines) == len(tabs["modal_table"]["rows"]) + 1,
          f"{len(lines)} 行 / {csv_name}")
    try:
        pdf = report_3d.write_pdf(rep, out / "modal.pdf")
        from pypdf import PdfReader

        ptxt = "\n".join(p.extract_text() or "" for p in PdfReader(str(pdf)).pages)
        check("PDF 计算书含模态章与频率表",
              "模态与自振特性" in ptxt and "各阶自振频率" in ptxt)
    except Exception as e:                       # noqa: BLE001
        check("PDF 计算书含模态章与频率表", False, f"PDFExportError: {e}")

    print("\n[9] 端到端：斜拉桥成桥态切线刚度模态")
    import cable_stayed_3d

    cm, _ = cable_stayed_3d.build_cable_stayed_3d(
        main_span=100.0, side_span=40.0, tower_height=40.0, n_cable=6)
    cr = fem_eigen_cable.solve_cable_bridge(
        cm, out_dir=tempfile.mkdtemp(prefix="vmodal_cable_"))
    cmd = cr["modal"]
    cref = float(cr["meta"].get("f1_vertical") or 0.0) or None
    check("斜拉桥产出 12 阶、无报错段",
          len(cmd.get("modes") or []) == 12 and not cmd.get("error"),
          f"n={len(cmd.get('modes') or [])} error={cmd.get('error')}")
    check("f1_vertical 落在频率序列中且 validate 无异议",
          cref is not None and modal_3d.validate(cmd, cref) == [],
          f"f1_vertical={cref}")
    check("最低阶不是竖向基频（不能拿 f₁ 当冲击系数依据）",
          cref is not None and cmd["modes"][0]["f"] < cref
          and modal_3d.mode_direction(cmd["modes"][0],
                                      max(cmd["mass"].values())) != "Z",
          f"f₁={cmd['modes'][0]['f']:.4f} Hz，主导"
          f"{modal_3d.mode_direction(cmd['modes'][0], max(cmd['mass'].values()))}")
    cres = SolveResult(kind="cable_stayed", modal=cmd, meta=cr["meta"])
    check("斜拉桥结果层也出 modal_table",
          "modal_table" in cres.tables(None)
          and len(cres.tables(None)["modal_table"]["rows"]) == 12)
    check("斜拉桥指标卡与校核可用",
          any(m.label == "一阶自振频率" for m in cres.metrics(None))
          and any(c.name == "模态与冲击系数同源"
                  for c in cres._checks_modal()))


def main() -> int:
    print("=" * 78)
    print("  verify_modal_3d：模态与自振特性（内核输出 → 四出口）")
    print("=" * 78)
    offline_checks()
    if "--offline" in sys.argv:
        n = sum(1 for _, ok, _ in RESULTS if ok)
        print(f"\n  {n}/{len(RESULTS)} 通过（离线）")
        return 0 if n == len(RESULTS) else 1
    try:
        _exe("example_3d_grillage.exe")
        _exe("cable_solver.exe")
    except FileNotFoundError as e:
        check("内核可执行文件可用", False, str(e))
    else:
        online_checks()
    n = sum(1 for _, ok, _ in RESULTS if ok)
    failed = [name for name, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 78)
    print(f"  {n}/{len(RESULTS)} 通过" + (f"　失败：{failed}" if failed else ""))
    print("=" * 78)
    return 0 if n == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
