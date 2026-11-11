"""verify_modal_detail_2d.py —— 2D 模态明细段（opt-in）链路守卫。

背景（2026-11-11 第九次，B2「模态周期 T / 振型 / 参与质量」）：
FE 2D 内核 `computeNaturalFrequencies2D` 本来就算出了全阶振型、也装配了
一致质量矩阵 M，只是 `example_modal.exe` 只写频率 / λ / μ。本轮由 FE 侧
`example_modal.exe <dat> --modal-detail` 追加 `[MODAL2D]` 段写出周期、
X/Y 两向有效参与质量与节点振型，BridgeStudio 只做解析与同源展示。

本守卫锁住：
- FE 源码契约（新段格式、BASIS=xy、不写 Z 向零值、ERROR 不静默、默认
  回归用例不带开关、黄金基线不含新段）；
- BS 解析层正例与 12 类负例（缺段 / kernel ERROR / 头缺失 / 3D BASIS /
  缺 MASS / 无 F / F 数不符 / 无 S / T<=0 / 两套频率 / 3D F 行 / 后续段）；
- 零污染与默认路径逐位不变（既有频率解析、12 格 .dat 黄金哈希、
  write_2d_html 默认字节、三个 opt-in 开关默认值）；
- 报告同源（HTML 章、四出口 Report 与 CSV 数据包）；
- 两仓文档口径对拍。

铁律：周期 T 与参与质量只能来自内核段，Python 侧不现算、不反算、
不重算 K/M、不补零冒充 3D 的 Z 向；汇总按布尔位统计防「假绿」。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import hashlib
import inspect
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import modal_2d
import model_doc
import report_2d
from bridge_spec import build_beam_bridge
from export_html import write_2d_html
from solve_2d import Solve2DResult, _fem_eigen_dir, _run, solve_2d
from verify_stage_tension_3d import DEFAULT_DAT_SHA

OUT = ROOT / "out" / "verify_modal_detail_2d"
PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail else ""))


def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ---------------------------------------------------------------- 黄金样本
# 频率段与 verify_modal_2d.py 的 GOLD_TXT 逐字一致（setprecision(6)）。
GOLD_FREQ = """== 自振频率 ==
f1 = 128.199 Hz
f2 = 146.913 Hz
f3 = 387.766 Hz
f4 = 587.712 Hz
f5 = 656.896 Hz
f6 = 942.016 Hz
基频 f1 = 128.199 Hz，冲击系数 μ = 0.45
"""
GOLD_F = [128.199, 146.913, 387.766, 587.712, 656.896, 942.016]

# F 行取自 FEM-Eigen example_modal.exe verify_modal.dat --modal-detail 的
# 真实输出（setprecision(10)）。T、mx、my、mnorm 都是内核原值，不是 Python
# 现算；守卫里只把它们当字符串夹具。
GOLD_F_ROWS = [
    "F 1 128.1991627 0.007800362957 11453.27864 2.939290543e-26 1",
    "F 2 146.9132482 0.006806738072 2.63961109e-26 11453.345 1",
    "F 3 387.7664708 0.002578871757 1271.970016 1.078179671e-27 1",
    "F 4 587.7119222 0.001701513892 1.84624846e-27 1.650012284e-26 1",
    "F 5 656.8958281 0.001522311388 456.3304032 3.45728573e-29 1",
    "F 6 942.0158274 0.001061553289 229.9968634 5.347085657e-19 1",
]
NOTE_ROW = ("NOTE=2D 无出平面自由度，Z 向参与质量不存在（不是 0）；theta 为转角"
            "自由度，只作振型展示、不设参与质量；T=1/f 由内核写出，Python 侧"
            "不得现算。")


def _detail(f_rows: list[str] | None = None, *, basis: str = "xy",
            nmodes: int = 6, ndof: int = 9,
            mass: str = "MASS X=14130 Y=14130", header: bool = True,
            note: bool = True, shapes: bool = True, nodes: int = 3,
            error: str | None = None, tail: str = "") -> str:
    """拼一份 [MODAL2D] 样本；默认即正例，参数用于逐类造负例。"""
    rows = list(GOLD_F_ROWS if f_rows is None else f_rows)
    lines = [modal_2d.DETAIL_SECTION]
    if header:
        lines.append(f"NMODES={nmodes} NDOF={ndof} BASIS={basis}")
    if note:
        lines.append(NOTE_ROW)
    if error:
        lines.append(f"ERROR={error}")
    if mass:
        lines.append(mass)
    lines.extend(rows)
    if shapes:
        for i in range(1, len(rows) + 1):
            for n in range(1, nodes + 1):
                lines.append(f"S {i} {n} {-0.001 * n * i:.10g} "
                             f"{1e-18 * n:.10g} {2e-18 * i:.10g}")
    if tail:
        lines.append(tail)
    return "\n".join(lines) + "\n"


GOLD = GOLD_FREQ + "\n" + _detail()
BASE = modal_2d.parse_modal_txt(GOLD)


# ---------------------------------------------------------------- [1] FE 契约

def fe_contract() -> None:
    print("\n[1] FE 源码契约：段格式 / opt-in / 基线不刷新")
    fe = _fem_eigen_dir()
    check("_fem_eigen_dir 定位到 FEM-Eigen 源码目录",
          fe is not None and Path(fe).is_dir(), str(fe))
    if fe is None:
        return
    fe = Path(fe)
    hp = read(fe / "modal_output_2d.hpp")
    check("modal_output_2d.hpp 存在且非空", bool(hp))
    for kw in ("[MODAL2D]", "BASIS=xy", "MASS X=", "formatModalSection2D",
               "assembleMassMatrix2D", "setprecision(10)",
               "ERROR=no_modes", "ERROR=missing_mode_vectors",
               "ERROR=mode_size_mismatch", "Γ_d", "T=1/f"):
        check(f"头文件登记：{kw}", kw in hp)
    check("头文件不写 3D 的 Z 向 MASS 字段（不补 0 冒充）",
          " Z=" not in hp and "BASIS=xyz" not in hp)
    check("头文件只做向量内积、不求解特征值",
          "computeNaturalFrequencies2D" in hp
          and "GeneralizedSelfAdjointEigenSolver" not in hp)
    em = read(fe / "example_modal.cpp")
    for kw in ('#include "modal_output_2d.hpp"', "--modal-detail",
               "formatModalSection2D(model, modal)", "if (detail) out <<"):
        check(f"example_modal.cpp 登记：{kw}", kw in em)
    check("开关只扫描 argv[2..]，path 仍取 argv[1]",
          "for (int a = 2; a < argc; ++a)" in em
          and "if (argc > 1) path = argv[1];" in em)
    reg = read(fe / "tests" / "regression" / "compare_regression.py")
    check("回归用例 example_modal 的 extra_args 仍为空（默认不刷新基线）",
          '("example_modal.exe", "verify_modal.dat", [],' in reg)
    gold = read(fe / "tests" / "regression" / "baselines" /
                "verify_modal.modal.txt")
    check("黄金基线 verify_modal.modal.txt 不含 [MODAL2D]",
          bool(gold) and "[MODAL2D]" not in gold)
    check("FE CHANGELOG 登记 --modal-detail",
          "--modal-detail" in read(fe / "CHANGELOG.md"))
    check("FE USER_MANUAL 登记 BASIS=xy 与不设 Z",
          "BASIS=xy" in read(fe / "USER_MANUAL.md")
          and "不设 Z 向参与质量" in read(fe / "USER_MANUAL.md"))


# ---------------------------------------------------------------- [2] 正例

def positives() -> None:
    print("\n[2] 正例：真实 F 行解析、口径与两张表")
    d = modal_2d.parse_modal_detail(GOLD, frequencies=BASE["frequencies"])
    check("正例 status=ok（6 位 / 10 位打印精度差按 5e-6 容差）",
          d["status"] == "ok", d["status"])
    check("BASIS=xy 且没有 Z 向", d["basis"] == "xy" and "z" not in d["basis"])
    check("NMODES / NDOF 如实登记", d["nmodes"] == 6 and d["ndof"] == 9)
    check("MASS X/Y 两向总质量", d["mass_x"] == 14130.0
          and d["mass_y"] == 14130.0)
    check("6 阶 F 行全部解析", len(d["modes"]) == 6)
    check("每阶 3 个节点振型", len(d["shapes"]) == 6
          and all(len(v) == 3 for v in d["shapes"].values()))
    check("kernel_note 非空且写明 Z 不存在", bool(d["kernel_note"])
          and "不存在" in d["kernel_note"])
    check("F 行频率与频率段同源（容差内）",
          all(abs(d["modes"][i]["f"] - GOLD_F[i])
              <= 5e-6 * max(1.0, GOLD_F[i]) for i in range(6)))
    check("段内 T·f≈1（周期是内核写出的）",
          all(abs(r["T"] * r["f"] - 1.0) < 1e-8 for r in d["modes"]))
    check("mnorm 均为 1（M-正交归一）",
          all(abs(r["mnorm"] - 1.0) < 1e-9 for r in d["modes"]))
    check("Σmx 与 Σmy 不超过各自总质量",
          sum(r["mx"] for r in d["modes"]) <= d["mass_x"] + 1e-6
          and sum(r["my"] for r in d["modes"]) <= d["mass_y"] + 1e-6)
    txt = modal_2d.detail_status_text(d)
    check("ok 中文文案不含 Markdown 星号", "**" not in txt and bool(txt))
    t1 = modal_2d.detail_table(d)
    check("明细表存在且 6 阶 + 总质量行 7 行",
          t1 is not None and len(t1["rows"]) == 7, str(t1 and len(t1["rows"])))
    check("明细表 6 列且无 mz / Z 列", t1 is not None
          and len(t1["headers"]) == 6
          and all("mz" not in h and "Z" not in h for h in t1["headers"]))
    check("明细表 note 含 BASIS=xy / 不补零 / 不是规范判定",
          t1 is not None and "BASIS=xy" in t1["note"]
          and "不补零" in t1["note"] and "不是规范合格判定" in t1["note"])
    t2 = modal_2d.shape_table(d)
    check("振型表 18 行（6 阶 × 3 节点），不挑阶不插值",
          t2 is not None and len(t2["rows"]) == 18)
    check("振型表 5 列含 θz，且注明只作展示",
          t2 is not None and len(t2["headers"]) == 5
          and "θz" in t2["headers"] and "不设参与质量" in t2["note"])


# ---------------------------------------------------------------- [3] 负例

def negatives() -> None:
    print("\n[3] 12 类负例：每类显式报错且两张表都不出")
    cases: list[tuple[str, str, str]] = [
        ("无 [MODAL2D] 段", GOLD_FREQ, "no_detail_section"),
        ("内核写 ERROR", GOLD_FREQ + _detail(error="no_modes"), "kernel_error"),
        ("缺 NMODES 头行", GOLD_FREQ + _detail(header=False), "bad_header"),
        ("3D 的 BASIS=xyz 混入", GOLD_FREQ + _detail(basis="xyz"), "bad_basis"),
        ("缺 MASS X/Y 行", GOLD_FREQ + _detail(mass=""), "missing_mass"),
        ("声明阶数却无 F 行",
         GOLD_FREQ + _detail(f_rows=[], nmodes=0), "no_modes"),
        ("F 行数与 NMODES 不符",
         GOLD_FREQ + _detail(nmodes=3), "incomplete_mode_row"),
        ("缺 S 振型行", GOLD_FREQ + _detail(shapes=False), "missing_shapes"),
        ("T<=0 的阶", GOLD_FREQ + _detail(
            f_rows=[GOLD_F_ROWS[0].replace("0.007800362957", "0")]
            + GOLD_F_ROWS[1:]), "invalid_period"),
        ("两套频率不一致", GOLD_FREQ + _detail(
            f_rows=["F 1 999.0 0.001 1 1 1"] + GOLD_F_ROWS[1:]),
         "inconsistent_f"),
        ("3D 的 8-token F 行不得当 2D 明细", GOLD_FREQ + _detail(
            f_rows=["F 1 3.0 0.333 0.1 0.2 0.3 1"], nmodes=1), "no_modes"),
    ]
    for name, txt, want in cases:
        d = modal_2d.parse_modal_detail(txt, frequencies=BASE["frequencies"])
        check(f"{name} → {want}", d["status"] == want, d["status"])
        check(f"{name}：明细表 / 振型表都返回 None",
              modal_2d.detail_table(d) is None
              and modal_2d.shape_table(d) is None)
        st = modal_2d.detail_status_text(d)
        check(f"{name}：中文写明后果且无 Markdown 残留",
              bool(st) and "**" not in st
              and any(k in st for k in ("不补", "不出", "不可用",
                                        "不得", "禁止", "做不了")))
    # 后续 == 段不得污染明细段
    d = modal_2d.parse_modal_detail(
        GOLD + "== 其他 ==\nF 9 999 1 1 1 1\n",
        frequencies=BASE["frequencies"])
    check("后续 == 段不污染（仍是 6 阶 / status=ok）",
          d["status"] == "ok" and len(d["modes"]) == 6, d["status"])
    # 所有状态都有中文文案
    states = set(modal_2d.DETAIL_STATUS_TEXT)
    check("每个状态都有唯一中文文案且无 Markdown 残留",
          len(states) >= 10
          and all("**" not in v for v in modal_2d.DETAIL_STATUS_TEXT.values()))


# ---------------------------------------------------------------- [4] 默认不变

def defaults() -> None:
    print("\n[4] 零污染 / 默认路径逐位不变")
    b2 = modal_2d.parse_modal_txt(GOLD)
    check("追加 [MODAL2D] 后频率解析仍为 6 阶且逐值不变",
          b2["frequencies"] == GOLD_F, str(b2["frequencies"]))
    check("追加 [MODAL2D] 后 λ 仍为空、status 仍 ok",
          b2["buckling"] == [] and b2["status"] == "ok")
    check("追加 [MODAL2D] 后不产生屈曲段", not b2["has_buckling_section"])
    check("3D F/S 行仍不污染 2D 频率（既有守卫口径）",
          len(modal_2d.parse_modal_txt(
              GOLD_FREQ + "F 1 3.0 0.333 0.1 0.2 0.3\nS 1 2 0.5\n"
          )["frequencies"]) == 6)
    r = Solve2DResult()
    check("Solve2DResult 默认 modal_tables() 为空", r.modal_tables() == {})
    det = modal_2d.parse_modal_detail(GOLD, frequencies=BASE["frequencies"])
    r.modal = modal_2d.parse_modal_txt(GOLD)
    r.modal["detail"] = det
    tabs = r.modal_tables()
    check("置入明细后出 3 张同源表",
          sorted(tabs) == ["modal_detail_table", "modal_shape_table",
                           "modal_table"], str(sorted(tabs)))
    for (kind, dim), b in model_doc.BUILDERS.items():
        dat = model_doc.ModelDoc(
            kind, dict(model_doc.DEFAULTS[b]), dimension=dim).to_dat()
        h = hashlib.sha256(dat.encode()).hexdigest()[:16]
        check(f"默认 .dat 黄金哈希：{b}", h == DEFAULT_DAT_SHA[b], h)
    spec = build_beam_bridge(span=30.0, n_spans=3)
    OUT.mkdir(parents=True, exist_ok=True)
    r0 = Solve2DResult()
    h_a = write_2d_html(r0, spec, OUT / "default_a.html").read_bytes()
    h_b = write_2d_html(r0, spec, OUT / "default_b.html").read_bytes()
    check("默认 2D HTML 两次写出逐字节一致", h_a == h_b)
    check("默认 2D HTML 不含模态明细章与 BASIS=xy",
          b"2D \xe6\xa8\xa1\xe6\x80\x81\xe6\x98\x8e\xe7\xbb\x86" not in h_a
          and b"BASIS=xy" not in h_a)
    h1 = write_2d_html(r, spec, OUT / "detail.html").read_text(encoding="utf-8")
    check("opt-in HTML 含两张新表标题与 BASIS=xy 口径",
          "2D 模态明细" in h1 and "2D 节点振型" in h1
          and "BASIS=xy" in h1)
    sig = inspect.signature(solve_2d)
    check("solve_2d 新增参数默认 False",
          sig.parameters["modal_detail"].default is False)
    check("_run 的 extra_args 默认 None（默认命令行逐位不变）",
          inspect.signature(_run).parameters["extra_args"].default is None)
    check("ModelDoc.solve 新增参数默认 False",
          inspect.signature(model_doc.ModelDoc.solve)
          .parameters["modal_2d_detail"].default is False)
    s2d = read(ROOT / "solve_2d.py")
    check("solve_2d 只在 opt-in 时追加 --modal-detail",
          '["--modal-detail"] if modal_detail else None' in s2d)
    check("solve_2d 同一份文本解析并传频率对拍",
          "parse_modal_detail(" in s2d and "frequencies=" in s2d)
    check("solve_2d 单独开明细会写中文说明，不静默",
          "modal=False" in s2d and "本次不做模态明细" in s2d)
    app = read(ROOT / "app.py")
    for kw in ("modal2ddetail_opt", "modal_2d_detail=_modal2d_detail_on",
               "modal2ddetail", "detail_status_text"):
        check(f"界面接线：{kw}", kw in app)
    check("界面明细开关叠在 2D 自振开关之上",
          "if _modal2d_on:" in app and "_modal2d_detail_on = False" in app)
    mdoc = read(ROOT / "model_doc.py")
    check("ModelDoc.solve 把第二层开关透传到 solve_2d",
          "modal_detail=bool(modal_2d_detail)" in mdoc
          and "modal=bool(modal_2d)" in mdoc)


# ---------------------------------------------------------------- [5] 报告同源

def reports() -> None:
    print("\n[5] 四出口同源与 CSV 数据包")
    det = modal_2d.parse_modal_detail(GOLD, frequencies=BASE["frequencies"])
    r = Solve2DResult()
    r.modal = modal_2d.parse_modal_txt(GOLD)
    r.modal["detail"] = det
    spec = build_beam_bridge(span=30.0, n_spans=3)
    rep = report_2d.build_2d_document(r, spec)
    for key in ("modal_detail_table", "modal_shape_table"):
        check(f"Report.tables 含 {key}", key in rep.tables)
    check("Report 含 DETAIL_BOUNDARY_NOTE",
          any(b.get("text") == report_2d.DETAIL_BOUNDARY_NOTE
              for b in rep.blocks))
    check("Report 仍含既有 BOUNDARY_NOTE 且保留「没有振型」措辞",
          any(report_2d.BOUNDARY_NOTE in str(b) for b in rep.blocks)
          and "没有振型" in report_2d.BOUNDARY_NOTE)
    rep0 = report_2d.build_2d_document(Solve2DResult(), spec)
    check("默认 Report 不含两张明细表",
          "modal_detail_table" not in rep0.tables
          and "modal_shape_table" not in rep0.tables)
    four = OUT / "four"
    shutil.rmtree(four, ignore_errors=True)
    paths = report_2d.write_2d_all(rep, four, stem="r2d", pdf=False)
    check("pdf=False 只出三件套", set(paths) == {"html", "docx", "zip"},
          str(sorted(paths)))
    check("三件套全部落盘且非空",
          all(p.is_file() and p.stat().st_size > 0 for p in paths.values()))
    with zipfile.ZipFile(paths["zip"]) as z:
        names = z.namelist()
    check("CSV 数据包含明细表与振型表 csv",
          any("modal_detail" in n for n in names)
          and any("modal_shape" in n for n in names), str(names))
    html = paths["html"].read_text(encoding="utf-8")
    check("四出口 HTML 与单份 HTML 同样含两张新表标题",
          "2D 模态明细" in html and "2D 节点振型" in html)
    eh = read(ROOT / "export_html.py")
    check("export_html 的 2D 章遍历两张新表",
          '"modal_detail_table"' in eh and '"modal_shape_table"' in eh)
    rp = read(ROOT / "report_2d.py")
    check("report_2d 的 ⑤ 章遍历两张新表",
          '"modal_detail_table"' in rp and '"modal_shape_table"' in rp)


# ---------------------------------------------------------------- [6] 文档口径

def documents() -> None:
    print("\n[6] 两仓文档口径对拍")
    mat = read(ROOT / "能力边界矩阵.md")
    spec = read(ROOT / "B类后续项接入规格.md")
    rd = read(ROOT / "README.md")
    use = read(ROOT / "使用说明.md")
    guide = read(ROOT / "开发使用手册.md")
    cm = read(ROOT / "verify_capability_matrix.py")
    bs = read(ROOT / "verify_b_class_spec.py")
    for kw in ("[MODAL2D]", "BASIS=xy", "第九次"):
        check(f"能力矩阵登记：{kw}", kw in mat)
    check("能力矩阵写明 Z 向参与质量不存在（不是 0）",
          "不存在（不是 0）" in mat or "不存在，不是 0" in mat)
    for kw in ("第九次", "[MODAL2D]"):
        check(f"B 类规格登记：{kw}", kw in spec)
    check("B 类规格把该行标为已闭环", "已闭环" in spec)
    check("B 类规格 B2 剩余由 4 项收窄为 3 项",
          "剩余 3 项" in spec or "剩余 3" in spec)
    check("README 登记新守卫", "verify_modal_detail_2d.py" in rd)
    check("使用说明文件地图登记 parse_modal_detail",
          "parse_modal_detail" in use)
    check("开发手册回归清单登记新守卫",
          "verify_modal_detail_2d.py" in guide)
    check("能力矩阵守卫有防漂移断言",
          "[MODAL2D]" in cm or "modal_detail" in cm)
    check("B 类规格守卫有防漂移断言",
          "modal_detail" in bs or "[MODAL2D]" in bs)


def main() -> int:
    print("=" * 78)
    print("2D 模态明细段（opt-in）链路守卫")
    print("=" * 78)
    fe_contract()
    positives()
    negatives()
    defaults()
    reports()
    documents()
    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(PASS)} 通过")
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
