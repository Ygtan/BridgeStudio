"""verify_buckling_3d.py —— 线性屈曲因子 + PDF 归档增强（页码/书签）回归。

覆盖六件事：

1. **解析层纯函数**：手写 ``buckling`` / ``[BUCKLING]`` 段逐字段对拍
   （数值硬编码在断言里，不用被验代码反算）、未知行向前兼容、ERROR 段。
2. **派生量与自检**：λ₁、Pcr = λ·P_ref、状态说明、明细表；validate 的
   各类故障注入（乱序/非正 λ/不同源/STATE 与数据矛盾/参考荷载为零）。
3. **段分派不串段**：新增段不能污染 ``modal`` / ``[MODAL]`` / 既有段。
4. **独立 oracle**：20 段悬臂柱（E=206 GPa、Iy=0.4、L=100 m、轴压 1 MN），
   Euler 闭式解 λ₁ = π²EI/(4L²)/P_ref = 20.33138507，与内核输出对拍。
   不是"内核自己跟自己对"。
5. **端到端**：梁格（no_compression 不摆空表）/ 桁架（λ 表 + 图 + 章）/
   斜拉桥（ERROR 段如实标注不适用）→ 结果层 → 四出口。
6. **PDF 归档增强**：逐页页码、章节书签与元数据、目标页顺序、
   ``navigation=False`` 显式降级对照、锚点丢失故障注入必须抛
   ``PDFExportError`` 且不覆盖目标文件（不静默）。

用法：``python verify_buckling_3d.py``（``--offline`` 只跑不依赖内核的判据）。
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from console_utf8 import setup_console

setup_console()
sys.path.insert(0, str(Path(__file__).parent))

import buckling_3d
import fem_eigen_3d
import fem_eigen_cable
import report_3d as R
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
NMODES=3 NDOF=126 BASIS=linear_elastic
REF NLOAD=1 PREF=1000000 SUMFZ=0 NBEAM=20 NAXIAL=0 KGNZ=488 NFREE=120 COMPRESSION=1 CHOLESKY=1 STATUS=ok
FUTURE_FIELD foo=1
L 1 20.33138614 20331.38614
L 2 30.49707921 30497.07921
L 3 182.9832466 182983.2466
""".strip().splitlines()

_SUMMARY = """
stage displacement envelope
N1 dof1 min=0 max=0
modal
NMODES=1 NDOF=6 BASIS=xyz
MASS X=1000 Y=1000 Z=1000
F 1 2.5 0.4 800 10 5 1
buckling
NMODES=2 NDOF=126 BASIS=linear_elastic
REF NLOAD=1 PREF=1000000 SUMFZ=0 NBEAM=20 NAXIAL=0 KGNZ=488 NFREE=120 COMPRESSION=1 CHOLESKY=1 STATUS=ok
L 1 20.33138614 20331.38614
L 2 30.49707921 30497.07921
""".strip()

_CABLE = """
[CABLE_SOLVER]
converged=1 nodes=2 elements=1
[MODAL]
NMODES=1 NDOF=6 BASIS=xyz
MASS X=1000 Y=1000 Z=1000
F 1 2.5 0.4 800 10 5 1
[BUCKLING]
NMODES=0 NDOF=0 BASIS=linear_elastic
ERROR=nonlinear_state_not_supported
[END]
""".strip()


def offline_checks() -> None:
    print("\n[1] 解析层：手写段逐字段对拍")
    bk = buckling_3d.parse_buckling_section(_SAMPLE)
    check("段头 / 参考荷载 / 诊断字段",
          bk["nmodes"] == 3 and bk["ndof"] == 126
          and bk["basis"] == "linear_elastic" and bk["status"] == "ok"
          and near(bk["ref"]["pref_kN"], 1000.0)
          and near(bk["ref"]["sumFz_kN"], 0.0)
          and bk["ref"]["nbeam"] == 20 and bk["ref"]["naxial"] == 0
          and bk["ref"]["kgnz"] == 488 and bk["ref"]["nfree"] == 120
          and bk["ref"]["compression"] and bk["ref"]["cholesky"]
          and bk["ref"]["nload"] == 1)
    check("三阶 λ / Pcr 按字面解析",
          [m["order"] for m in bk["modes"]] == [1, 2, 3]
          and near(bk["modes"][0]["lambda"], 20.33138614)
          and near(bk["modes"][0]["pcr_kN"], 20331.38614)
          and near(bk["modes"][2]["lambda"], 182.9832466))
    check("未知行不破坏解析（向前兼容）",
          len(bk["modes"]) == 3 and not bk["error"])
    check("ERROR 段不静默",
          buckling_3d.parse_buckling_section(
              ["NMODES=0 NDOF=0 BASIS=linear_elastic",
               "ERROR=nonlinear_state_not_supported"])["error"]
          == "nonlinear_state_not_supported")

    print("\n[2] 派生量：λ₁ / 状态 / 明细表")
    check("λ₁ 与临界荷载说明", near(buckling_3d.first_factor(bk), 20.33138614)
          and "Pcr" in buckling_3d.load_note(bk)
          and "上限估计" in buckling_3d.load_note(bk))
    tb = buckling_3d.buckling_table(bk)
    check("明细表 3 行、表头与口径说明齐全",
          len(tb["rows"]) == 3 and tb["headers"][1] == "屈曲因子 λ"
          and "λ 是荷载整体放大倍数" in tb["note"]
          and "JTG 3362" in tb["note"],
          f"{len(tb['rows'])} 行")
    check("手写段 validate 无异议", buckling_3d.validate(bk) == [],
          str(buckling_3d.validate(bk)))

    print("\n[3] validate 故障注入（不静默）")
    cases = [
        ("λ 非正", lambda d: d["modes"][0].update({"lambda": -1.0})),
        ("λ 乱序", lambda d: d["modes"][0].update({"lambda": 999.0})),
        ("Pcr 与 λ·P_ref 不同源", lambda d: d["modes"][0].update(pcr_kN=1.0)),
        ("声明阶数与 L 行数不符", lambda d: d.update(nmodes=7)),
        ("STATUS 与数据矛盾", lambda d: d.update(status="no_compression")),
        ("参考荷载为零", lambda d: d["ref"].update(pref_kN=0.0)),
    ]
    for label, mutate in cases:
        import copy
        d = copy.deepcopy(bk)
        mutate(d)
        check(f"抓到「{label}」", bool(buckling_3d.validate(d)))
    check("no_compression 不是求解失败（不算问题）",
          buckling_3d.validate({
              **buckling_3d.empty_buckling(), "status": "no_compression",
              "ref": {**buckling_3d.empty_buckling()["ref"], "pref_kN": 1000.0},
          }) == [])
    check("error 段给出可读中文原因",
          "几何非线性" in "".join(buckling_3d.validate(
              buckling_3d.parse_buckling_section(
                  ["ERROR=nonlinear_state_not_supported"]))))

    print("\n[4] 段分派：新段不污染旧段")
    d = Path(tempfile.mkdtemp(prefix="vbk_seg_")) / "x_summary.txt"
    d.write_text(_SUMMARY, encoding="utf-8")
    parsed = fem_eigen_3d.parse_summary(d)
    check("summary：modal 段只 1 阶、buckling 段 2 阶",
          len(parsed["modal"]["modes"]) == 1
          and len(parsed["buckling"]["modes"]) == 2
          and not parsed["modal"]["error"]
          and not parsed["buckling"]["error"],
          f"modal={len(parsed['modal']['modes'])} "
          f"buckling={len(parsed['buckling']['modes'])}")
    c = Path(tempfile.mkdtemp(prefix="vbk_seg_")) / "x_cable.txt"
    c.write_text(_CABLE, encoding="utf-8")
    cp = fem_eigen_cable.parse_cable_result(c)
    check("cable：[BUCKLING] 独立成段、[MODAL] 未串段",
          cp["buckling"]["error"] == "nonlinear_state_not_supported"
          and len(cp["modal"]["modes"]) == 1 and not cp["modal"]["error"])
    no_bk = Path(tempfile.mkdtemp(prefix="vbk_seg_")) / "old_summary.txt"
    no_bk.write_text("modal\nNMODES=0 NDOF=0 BASIS=xyz\nERROR=no_mass\n",
                     encoding="utf-8")
    check("旧内核没有屈曲段时标 no_buckling_section",
          fem_eigen_3d.parse_summary(no_bk)["buckling"]["error"]
          == "no_buckling_section")

    print("\n[5] 结果层：指标卡 / 校核 / 明细表")
    s = SolveResult(kind="grillage3d", buckling=bk)
    check("有 λ 时出指标卡、两条 info 校核、明细表",
          any(m.label == "一阶屈曲因子 λ₁" and m.value.startswith("20.")
              for m in s.metrics(None))
          and len(s._checks_buckling()) == 2
          and "buckling_table" in s.tables(None))
    no_c = buckling_3d.empty_buckling()
    no_c["status"] = "no_compression"
    s2 = SolveResult(kind="grillage3d", buckling=no_c)
    check("no_compression：指标卡写「未产出」、不给空表",
          any(m.label == "一阶屈曲因子 λ₁" and m.value == "未产出"
              for m in s2.metrics(None))
          and "buckling_table" not in s2.tables(None)
          and "物理预期" in s2._checks_buckling()[0].detail)
    err = buckling_3d.empty_buckling("nonlinear_state_not_supported")
    s3 = SolveResult(kind="cable_stayed", buckling=err)
    check("索承体系 ERROR：info 级写明不适用、不误判为求解失败",
          s3._checks_buckling()[0].level == "info"
          and s3._checks_buckling()[0].passed
          and "不适用" in s3._checks_buckling()[0].detail)
    lam_low = buckling_3d.parse_buckling_section([
        "NMODES=1 NDOF=6 BASIS=linear_elastic",
        "REF NLOAD=1 PREF=1000000 SUMFZ=0 NBEAM=1 NAXIAL=0 KGNZ=6 NFREE=5 COMPRESSION=1 CHOLESKY=1 STATUS=ok",
        "L 1 0.8 800"])
    s4 = SolveResult(kind="grillage3d", buckling=lam_low)
    check("λ₁ ≤ 1 时给出明确不通过（数学分界，非规范限值）",
          any(c.name == "参考荷载与临界荷载" and not c.passed
              for c in s4._checks_buckling()))


def _exe() -> Path:
    p = (FEM_EIGEN_DIR / "build" / "example_3d_grillage.exe") if FEM_EIGEN_DIR else None
    if p is None or not p.is_file():
        raise FileNotFoundError(f"缺 example_3d_grillage.exe：{p}")
    return p


def _cantilever(dat: Path) -> dict:
    """20 段悬臂柱 + 自由端轴压 1 MN，用内核产出 buckling 段。"""
    n, L = 20, 100.0
    lines = ["*ET", "1 BEAM", "", "*MP", "1 E=206e9 DENS=7850", "",
             "*R", "1 A=0.18 IY=0.4 IZ=0.6 J=0.3 G=7.9e10", "", "*NODE"]
    for i in range(n + 1):
        lines.append(f"{i + 1} {L * i / n:.6f} 0.0 0.0")
    lines += ["", "*TYPE,1", "*MAT,1", "*REAL,1"]
    for i in range(n):
        lines.append(f"*EN,{i + 1},{i + 1},{i + 2}")
    lines += ["", "*BOUNDARY", "1 1-6 0.0", "", "*CLOAD",
              f"{n + 1} 1 -1.0e6", "", "*END"]
    dat.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cp = subprocess.run([str(_exe()), str(dat)], capture_output=True,
                        text=True, cwd=str(dat.parent), timeout=180)
    if cp.returncode != 0:
        raise RuntimeError(f"内核失败：{cp.stdout}\n{cp.stderr}")
    return fem_eigen_3d.parse_summary(dat.with_name(dat.stem + "_summary.txt"))


def _flat(items):
    for it in items:
        if isinstance(it, list):
            yield from _flat(it)
        else:
            yield it


def online_checks() -> None:
    print("\n[6] 独立 oracle：Euler 悬臂柱闭式解")
    dat = Path(tempfile.mkdtemp(prefix="vbk_euler_")) / "cantilever.dat"
    p = _cantilever(dat)
    bk = p["buckling"]
    exact = math.pi ** 2 * 206e9 * 0.4 / (4 * 100.0 ** 2) / 1.0e6
    got = buckling_3d.first_factor(bk)
    check("λ₁ 与 Euler Pcr=π²EI/(4L²) 对拍（相对误差 < 1e-4）",
          got is not None and near(got, exact, 1e-4)
          and bk["status"] == "ok"
          and buckling_3d.validate(bk) == [],
          f"内核 {got:.8f} / 闭式 {exact:.8f}")
    check("参考荷载未被重复计入（P_ref = 1 MN）",
          near(bk["ref"]["pref_kN"], 1000.0)
          and bk["ref"]["nload"] == 1
          and near(bk["modes"][0]["pcr_kN"], got * 1000.0),
          f"P_ref={bk['ref']['pref_kN']} kN, NLOAD={bk['ref']['nload']}")

    print("\n[7] 端到端：梁格 / 桁架 / 斜拉桥")
    from grillage_3d import build_box_grillage_3d
    from truss_3d import build_truss_3d
    from solver_3d import solve

    gm = build_box_grillage_3d()
    gres = solve(gm, out_dir=tempfile.mkdtemp(prefix="vbk_g_"))
    check("梁格：无压杆 → STATUS=no_compression，不摆空表",
          gres.buckling.get("status") == "no_compression"
          and not gres.buckling.get("error")
          and "buckling_table" not in gres.tables(gm)
          and buckling_3d.validate(gres.buckling) == [])
    gout = Path(tempfile.mkdtemp(prefix="vbk_grep_"))
    grep = R.build_document(gres, gm, out_dir=gout)
    check("梁格报告有独立「线性屈曲与稳定」章并写明未产出",
          "线性屈曲与稳定" in [b["text"] for b in grep.blocks
                              if b["type"] == "h1"]
          and "本次没有 λ" in R.render_html(grep))
    gpaths = R.write_all(grep, gout, stem="grillage", pdf=False)
    with zipfile.ZipFile(str(gpaths["zip"])) as z:
        check("梁格 CSV 不导出空的屈曲表",
              not any("buckling_table" in n for n in z.namelist()))

    tm = build_truss_3d()[0]
    tres = solve(tm, out_dir=tempfile.mkdtemp(prefix="vbk_t_"))
    check("桁架：TRUSS 几何刚度生效、求出多阶 λ",
          tres.buckling.get("status") == "ok"
          and len(tres.buckling.get("modes") or []) >= 3
          and buckling_3d.validate(tres.buckling) == []
          and tres.buckling["ref"]["naxial"] > 0,
          f"λ₁={buckling_3d.first_factor(tres.buckling):.2f}")
    tout = Path(tempfile.mkdtemp(prefix="vbk_trep_"))
    trep = R.build_document(tres, tm, out_dir=tout)
    check("桁架报告有 λ 表与 λ 图，且各只出现一次",
          "buckling_table" in trep.tables
          and "buckling_factors" in trep.figures
          and sum(1 for b in trep.blocks
                  if b["type"] == "table" and b["key"] == "buckling_table") == 1
          and sum(1 for b in trep.blocks
                  if b["type"] == "figure" and b["key"] == "buckling_factors") == 1)
    tpaths = R.write_all(trep, tout, stem="truss", pdf=False)
    with zipfile.ZipFile(str(tpaths["zip"])) as z:
        name = next((n for n in z.namelist() if "buckling_table" in n), None)
        rows = (z.read(name).decode("utf-8-sig").splitlines()
                if name else [])
    check("CSV 屈曲表逐行导出（阶数 + 表头）",
          len(rows) == len(trep.tables["buckling_table"]["rows"]) + 1,
          f"{len(rows)} 行")

    from cable_stayed_3d import build_cable_stayed_3d
    cm, _ = build_cable_stayed_3d(main_span=100.0, side_span=40.0,
                                  tower_height=40.0, n_cable=6)
    cr = fem_eigen_cable.solve_cable_bridge(
        cm, out_dir=tempfile.mkdtemp(prefix="vbk_c_"))
    check("斜拉桥：内核写 ERROR 段、模态段不受污染",
          cr["buckling"]["error"] == "nonlinear_state_not_supported"
          and len(cr["modal"]["modes"]) == 12 and not cr["modal"]["error"])
    cres = SolveResult(kind="cable_stayed", modal=cr["modal"],
                       buckling=cr["buckling"], meta=cr["meta"])
    crep = R.build_document(cres, cm,
                            out_dir=Path(tempfile.mkdtemp(prefix="vbk_crep_")))
    chtml = R.render_html(crep)
    check("斜拉桥报告如实写「不适用 / 未产出」，不给 λ 表或 λ 图",
          "非线性" in chtml and "未产出" in chtml
          and "buckling_table" not in crep.tables
          and "buckling_factors" not in crep.figures)

    print("\n[8] PDF 归档增强：页码 / 书签 / 降级 / 故障注入")
    from pypdf import PdfReader

    out = Path(tempfile.mkdtemp(prefix="vbk_pdf_"))
    nav = R.write_pdf(trep, out / "nav.pdf")
    plain = R.write_pdf(trep, out / "plain.pdf", navigation=False)
    rd = PdfReader(str(nav))
    n = len(rd.pages)
    texts = [p.extract_text() or "" for p in rd.pages]
    check("归档版每页都有页码「N / 总页数」",
          n >= 2 and all(f"{i + 1} / {n}" in t for i, t in enumerate(texts)),
          f"{n} 页")
    heads = [b["text"] for b in trep.blocks if b["type"] in ("h1", "h2")]
    items = list(_flat(rd.outline))
    check("书签条数/标题与报告章节一致",
          len(items) == len(heads)
          and [str(x.get("/Title")) for x in items] == heads,
          f"{len(items)} 条")
    dests = [rd.get_destination_page_number(x) for x in items]
    check("书签目标页有效且不倒退",
          all(0 <= d < n for d in dests)
          and all(dests[i + 1] >= dests[i] for i in range(len(dests) - 1)),
          f"{dests}")
    check("元数据写入结果指纹",
          trep.fingerprint in str((rd.metadata or {}).get("/Keywords", "")))
    rp = PdfReader(str(plain))
    check("navigation=False 显式降级：无书签、无页码",
          not rp.outline and "1 /" not in (rp.pages[0].extract_text() or ""))

    # 锚点丢失：HTML 走非 render_html 路径时必须报错，且不能覆盖目标文件。
    saved = R.render_html
    target = out / "inject.pdf"
    target.write_bytes(b"OLD-PDF-STUB")
    said = ""
    try:
        R.render_html = lambda rep: re.sub(
            r'<span class="om">BKSEC\d+Z</span>', "", saved(rep))
        try:
            R.write_pdf(trep, target)
        except R.PDFExportError as e:
            said = str(e)
    finally:
        R.render_html = saved
    check("锚点丢失时抛 PDFExportError 且不静默交付",
          "锚点丢失" in said and "已拒绝交付" in said, said[:90])
    check("失败时不覆盖目标文件（保留旧内容）",
          target.read_bytes() == b"OLD-PDF-STUB")


def main() -> int:
    print("=" * 78)
    print("  verify_buckling_3d：屈曲因子 + PDF 页码/书签归档")
    print("=" * 78)
    offline_checks()
    if "--offline" in sys.argv:
        n = sum(1 for _, ok, _ in RESULTS if ok)
        print(f"\n  {n}/{len(RESULTS)} 通过（离线）")
        return 0 if n == len(RESULTS) else 1
    try:
        _exe()
    except (FileNotFoundError, RuntimeError) as e:
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
