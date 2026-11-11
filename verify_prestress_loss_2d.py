"""verify_prestress_loss_2d.py —— 2D 分项预应力损失明细（第十次）独立守卫。

背景（2026-11-11 第十次，B2「分项预应力损失明细」）：
FEM-Eigen 的 `prestress_loss.hpp::computePrestressLosses` 六项公式 2D/3D
同源，但 2D 的 `fem_2d.cpp::processPrestress` 只取 `effectivePrestressRatio`
标量，六项当场丢弃；2D 数据模型、结果写出层都没有承载。本轮用户授权
破例改 FE 内核并拍板段形态：保留 `loss` 行一字不动，另取一份同源明细，
由 `example_mixed.exe --loss-detail[-only]` 写出 `[PRESTRESSLOSS2D]` 段；
BridgeStudio 只解析、不重算；2D 输入契约同步补 opt-in 的非阶段 LOSSREF 束。

本守卫锁住：
- FE 源码契约（段格式 / BASIS=2d_global / 不写 STAGE 占位 / 数值路径零改动 /
  默认回归用例不带开关 / 黄金基线不含新段）；
- FE 端到端数值对拍（独立闭式解 oracle、RATIO 同源、默认 .result 逐位不变、
  --loss-detail-only 不求解不写 .result）；
- BS 契约（22 键参数表、LOSSREF 束、引用完整性中文校验、12 格默认 .dat 哈希）；
- 解析层正例与 12 类负例，一律显式中文状态、不摆空表、不补零；
- 零污染（默认开关全关、默认 HTML 不出章、diagnostics 无新条目）；
- 接线与四出口同源（HTML / CSV 与表字典同一份数字）；
- 两仓文档口径对拍。

铁律：σl1~σl6 / σpe / RATIO 只能来自内核段，Python 不现算、不反算、
不补零；汇总按布尔位统计防「假绿」。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import hashlib
import inspect
import math
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import prestress_loss_2d as L
import report_2d
from bridge_spec import Prestress2D, PrestressLoss2D, build_beam_bridge
from export_html import write_2d_html
from model_doc import BUILDERS, DEFAULTS, ModelDoc
from solve_2d import Solve2DResult, _fem_eigen_dir, solve_2d
from verify_stage_tension_3d import DEFAULT_DAT_SHA

FE = _fem_eigen_dir() or Path("D:/AI-work/FEM-Eigen")
OUT = ROOT / "out" / "verify_prestress_loss_2d"
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


# ---------------------------------------------------------------- 样本与 oracle

ROW_OK = (
    "P1 SIGMA_CON=1395000000 SL1=87790888.5885 SL2=117000000 "
    "SL3=40000000 SL4=45217391.3043 SL5=20645991.0476 "
    "SL6=108364004.044 SPE=1144991720.11 RATIO=0.820782595059")

CFG = {
    "sigma_con": 1395e6, "Ep": 1.95e11, "Ec": 3.45e10, "fpk": 1860e6,
    "mu": 0.25, "k": 0.0015, "theta": 0.2, "x": 10.0,
    "delta_l": 0.006, "l": 10.0, "t1": 15.0, "t2": 35.0,
    "sigma_pc4": 8e6, "sigma_pc6": 8e6, "psi": 1.0, "zeta": 0.3,
    "eps_cs": 2e-4, "phi": 2.0, "rho": 0.005, "rho_ps": 1.0,
    "post": True, "rebar": False,
}


def oracle(cfg: dict) -> dict[str, float]:
    """守卫内部的独立闭式解：不调用内核、不写回结果，不构成产品口径。

    与 `prestress_loss.hpp::computePrestressLosses` 同式的第二份实现，只用于
    对拍内核写出的段，避免守卫变成「拿内核自己的输出证明内核正确」。
    """
    alpha = cfg["Ep"] / cfg["Ec"] if cfg["Ec"] > 0 else 0.0
    sl1 = cfg["sigma_con"] * (1.0 - math.exp(
        -(cfg["mu"] * cfg["theta"] + cfg["k"] * cfg["x"])))
    sl2 = cfg["delta_l"] / cfg["l"] * cfg["Ep"]
    sl3 = 2.0 * (cfg["t2"] - cfg["t1"]) * 1.0e6
    sl4 = alpha * cfg["sigma_pc4"]
    spe = (cfg["sigma_con"] - sl1 - sl2 - sl4 if cfg["post"]
           else cfg["sigma_con"] - sl2)
    sl5 = cfg["psi"] * cfg["zeta"] * (0.52 * spe / cfg["fpk"] - 0.26) * spe
    sl6 = 0.9 * (cfg["Ep"] * cfg["eps_cs"] + alpha * cfg["sigma_pc6"] * cfg["phi"]) \
        / (1.0 + 15.0 * cfg["rho"] * cfg["rho_ps"])
    return {"SL1": sl1, "SL2": sl2, "SL3": sl3, "SL4": sl4, "SL5": sl5,
            "SL6": sl6, "SPE": spe, "RATIO": spe / cfg["sigma_con"]}


def loss_text(n: int = 1, basis: str = "2d_global", rows: list[str] | None = None,
              error: str | None = None, header: bool = True,
              note: bool = True) -> str:
    lines = [L.SECTION]
    if error:
        lines.append("ERROR=" + error)
    if header:
        lines.append(f"NPRESTRESS={n} BASIS={basis}")
    if note:
        lines.append("NOTE=" + L.NOTE)
    lines.extend(rows if rows is not None else [ROW_OK])
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- FE 源码契约

def fe_sources() -> None:
    print("\n[1] FE 源码契约：段格式、数值路径零改动、默认基线不刷新")
    hpp = read(FE / "prestress_loss_output_2d.hpp")
    check("FE 新增 prestress_loss_output_2d.hpp", bool(hpp))
    check("段名 [PRESTRESSLOSS2D]", "[PRESTRESSLOSS2D]" in hpp)
    check("BASIS=2d_global", "BASIS=2d_global" in hpp)
    check("段头写 NPRESTRESS", "NPRESTRESS=" in hpp)
    for key in L.FIELDS:
        check(f"段字段 {key}", (key + "=") in hpp)
    check("本段用 12 位有效数字", "setprecision(12)" in hpp)
    check("不写 STAGE 占位（不凭空造阶段号）", '"STAGE"' not in hpp)
    check("不重算力学（段层不调 computePrestressLosses）",
          "computePrestressLosses(" not in hpp)
    check("无 LOSSREF 束写 ERROR 不摆空表",
          "ERROR=no_lossref_prestress" in hpp
          and "m.prestressLossDetails.empty()" in hpp)
    check("NOTE 写明 2D 没有阶段维度", "2D 没有阶段维度" in hpp)
    check("NOTE 写明 σl3/σl5/σl6 不进 σpe", "σl3/σl5/σl6" in hpp and "不进 σpe" in hpp)
    check("NOTE 写明 σl2 仅直线筋", "只按直线筋" in hpp)

    cpp = read(FE / "fem_2d.cpp")
    check("数值路径零改动：loss 行仍走 effectivePrestressRatio",
          "loss = effectivePrestressRatio(lit->second);" in cpp)
    check("明细只取同源 computePrestressLosses",
          "const PrestressLossResult detail = computePrestressLosses(lit->second);" in cpp)
    check("明细存入 Model2D 容器", "m.prestressLossDetails.push_back(" in cpp)
    check("processPrestress 每次重建明细，不累加",
          "m.prestressLossDetails.clear();" in cpp)

    hdr = read(FE / "fem_2d.hpp")
    check("Model2D 有 prestressLossDetails 容器", "prestressLossDetails" in hdr)
    check("fem_2d.hpp 显式 include tuple", "#include <tuple>" in hdr)

    exe_src = read(FE / "example_mixed.cpp")
    check("CLI 有 --loss-detail", '"--loss-detail"' in exe_src)
    check("CLI 有 --loss-detail-only", '"--loss-detail-only"' in exe_src)
    check("写出 <stem>.loss.txt", '".loss.txt"' in exe_src)
    check("--loss-detail-only 在求解前 return，不写 .result",
          "if (lossDetailOnly) return 0;" in exe_src
          and exe_src.find("if (lossDetailOnly) return 0;")
          < exe_src.find("writeResult(outPath"))
    check("默认 .result 仍由 writeResult 写出",
          "writeResult(outPath, model, r, forces);" in exe_src)

    reg = read(FE / "tests" / "regression" / "compare_regression.py")
    check("默认回归用例 verify_prestress_loss.dat 不带新开关",
          '("example_mixed.exe", "verify_prestress_loss.dat", [],' in reg)
    gold = read(FE / "tests" / "regression" / "baselines" / "verify_prestress_loss.result")
    check("黄金基线不含 [PRESTRESSLOSS2D]", "[PRESTRESSLOSS2D]" not in gold and bool(gold))


# ---------------------------------------------------------------- FE 端到端

def fe_runtime() -> None:
    print("\n[2] FE 端到端：独立闭式解对拍 + 默认输出零污染")
    exe = FE / "build" / "example_mixed.exe"
    check("example_mixed.exe 已编译", exe.is_file(), str(exe))
    src = FE / "verify_prestress_loss.dat"
    if not (exe.is_file() and src.is_file()):
        return
    OUT.mkdir(parents=True, exist_ok=True)
    dat = OUT / "loss_probe.dat"
    shutil.copyfile(src, dat)
    check("夹具 .dat 含 LOSSREF=1", "LOSSREF=1" in read(dat))

    def run(args: list[str]):
        return subprocess.run([str(exe), dat.name] + args, cwd=str(OUT),
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=90)

    result_path = OUT / "loss_probe.result"
    loss_path = OUT / "loss_probe.loss.txt"
    # 清理上一次守卫留下的 opt-in 产物，避免把历史文件误判为默认输出
    loss_path.unlink(missing_ok=True)
    base = run([])
    check("默认运行 rc=0", base.returncode == 0, (base.stderr or "")[-120:])
    check("默认运行不产生 .loss.txt", not loss_path.is_file())
    default_bytes = result_path.read_bytes() if result_path.is_file() else b""
    check("默认 .result 非空", bool(default_bytes))
    gold = (FE / "tests" / "regression" / "baselines"
            / "verify_prestress_loss.result").read_bytes()
    check("默认 .result 与黄金基线规范化后一致（CRLF/LF 差异除外）",
          default_bytes.replace(b"\r\n", b"\n") == gold.replace(b"\r\n", b"\n"))

    detail = run(["--loss-detail"])
    check("--loss-detail rc=0", detail.returncode == 0)
    check("--loss-detail 时 .result 逐位不变",
          result_path.is_file() and result_path.read_bytes() == default_bytes)
    check("--loss-detail 写出 .loss.txt", loss_path.is_file())
    txt = read(loss_path)
    check(".loss.txt 含段头", L.SECTION in txt)
    check("段内 12 位精度的 RATIO 与 3D 官方示例一致",
          "RATIO=0.820782595059" in txt)
    check("段内不写 STAGE 占位（数据行无 STAGE<n>）",
          not any(re.match(r"^STAGE\d+", line.strip())
                  for line in txt.splitlines()))

    d = L.parse_loss_txt(txt)
    check("内核段可解析且状态 ok", d.get("status") == "ok", str(d.get("status")))
    check("BASIS=2d_global", d.get("basis") == L.BASIS)
    check("NPRESTRESS 与行数一致", d.get("np") == len(d.get("rows") or []))
    row = (d.get("rows") or [{}])[0]
    check("SIGMA_CON 来自内核输入", row.get("SIGMA_CON") == 1395e6)
    want = oracle(CFG)
    for key, expect in want.items():
        got = float(row.get(key, 0.0))
        check(f"内核 {key} 与独立闭式解一致",
              abs(got - expect) <= 1e-6 * max(1.0, abs(expect)),
              f"{got:.6g} / {expect:.6g}")
    check("RATIO 与段内 SPE/SIGMA_CON 同源",
          abs(row.get("RATIO", 0.0)
              - row.get("SPE", 0.0) / row.get("SIGMA_CON", 1.0)) < 1e-9)
    check("明细表由段生成", L.loss_table(d) is not None)

    # --loss-detail-only：只取明细、不求解、不覆盖 .result
    result_path.unlink(missing_ok=True)
    loss_path.unlink(missing_ok=True)
    only = run(["--loss-detail-only"])
    check("--loss-detail-only rc=0", only.returncode == 0)
    check("--loss-detail-only 不写 .result", not result_path.is_file())
    check("--loss-detail-only 写出 .loss.txt", loss_path.is_file())
    check("两种开关写出的明细逐位一致", read(loss_path) == txt)


# ---------------------------------------------------------------- BS 契约

def bs_contract() -> None:
    print("\n[3] BS 输入契约：22 键表、LOSSREF 束、引用校验、12 格哈希")
    src = read(ROOT / "bridge_spec.py")
    check("PrestressLoss2D 契约在位", "class PrestressLoss2D" in src)
    check("Prestress2D 契约在位", "class Prestress2D" in src)
    keys = ("SIGMA_CON", "EP", "EC", "FPK", "MU", "K", "THETA", "X",
            "DELTA_L", "L", "T1", "T2", "SIGMA_PC4", "SIGMA_PC6", "PSI",
            "ZETA", "ECS", "PHI", "RHO", "RHOPS", "POST", "REBAR")
    for key in keys:
        check(f"22 键参数表含 {key}", f'"{key}"' in src)
    tendon_cls = src.split("class Prestress2D")[1].split("class Stage2D")[0]
    check("束只给 LOSSREF，不造总损失系数",
          "LOSSREF={self.loss_ref}" in tendon_cls and "LOSS=" not in tendon_cls)
    check("BridgeSpec 挂两张 opt-in 表", "prestress_losses: dict[int, PrestressLoss2D]" in src
          and "prestresses: list[Prestress2D]" in src)
    check("to_dat 默认不写新段（if 包住）",
          "if self.prestress_losses:" in src and "if self.prestresses:" in src)
    check("拒绝注释按第十次如实更新", "第十次起 2D 有了 opt-in" in src)
    check("validate_model 补 LOSSREF 引用校验",
          "引用了不存在的 *PRESTRESS_LOSS 表" in src
          and "引用了不存在的单元 {eid}" in src)

    default = build_beam_bridge(span=10.0, n_spans=1, udl_kN=0.0, staged=False)
    dat0 = default.to_dat()
    check("默认 .dat 不含 *PRESTRESS_LOSS", "*PRESTRESS_LOSS" not in dat0)
    check("默认 .dat 不含非阶段 *PRESTRESS 束", "\n*PRESTRESS\n" not in dat0)
    check("默认模型 validate 无错误", not default.validate_model())

    tb = PrestressLoss2D(**CFG)
    eids = [e.id for e in default.elements][:4]
    default.prestress_losses = {1: tb}
    default.prestresses = [Prestress2D(id=1, elems=eids, P=1500e3,
                                       F=0.2, loss_ref=1)]
    dat1 = default.to_dat()
    check("opt-in .dat 写入 *PRESTRESS_LOSS 表", "*PRESTRESS_LOSS" in dat1)
    check("opt-in .dat 写入 LOSSREF 束", "LOSSREF=1" in dat1)
    check("opt-in 前缀与默认逐位一致", dat1.startswith(dat0.split("! 自重开关")[0]))
    check("opt-in 模型 validate 无错误", not default.validate_model())

    bad = build_beam_bridge(span=10.0, n_spans=1, udl_kN=0.0, staged=False)
    bad.prestress_losses = {1: tb}
    bad.prestresses = [Prestress2D(id=1, elems=eids, P=1500e3, loss_ref=9)]
    check("断引用：表编号不存在给中文错误",
          any("不存在的 *PRESTRESS_LOSS 表 9" in e for e in bad.validate_model()))
    bad.prestresses = [Prestress2D(id=1, elems=[], P=1500e3, loss_ref=1)]
    check("断引用：空 ELEM 列表给中文错误",
          any("ELEM 列表为空" in e for e in bad.validate_model()))
    bad.prestresses = [Prestress2D(id=1, elems=[9999], P=1500e3, loss_ref=1)]
    check("断引用：单元不存在给中文错误",
          any("不存在的单元 9999" in e for e in bad.validate_model()))

    for (kind, dim), b in BUILDERS.items():
        dat = ModelDoc(kind, dict(DEFAULTS[b]), dimension=dim).to_dat()
        h = hashlib.sha256(dat.encode()).hexdigest()[:16]
        check(f"12 格默认 .dat 黄金哈希：{b}", h == DEFAULT_DAT_SHA[b],
              f"{h} / {DEFAULT_DAT_SHA[b]}")


# ---------------------------------------------------------------- 解析层

def parser_cases() -> None:
    print("\n[4] 解析层：正例、12 类负例、不重算、不补零")
    ok = L.parse_loss_txt(loss_text())
    check("正例状态 ok", ok.get("status") == "ok", str(ok.get("status")))
    check("正例解析出 1 束", len(ok.get("rows") or []) == 1)
    check("正例字段齐全", all(k in (ok["rows"][0]) for k in L.FIELDS))
    t = L.loss_table(ok)
    check("表字典契约 title/headers/rows/note", bool(t)
          and all(k in t for k in ("title", "headers", "rows", "note")))
    check("表头 10 列", len(t["headers"]) == 10)
    check("表值 MPa 换算自 Pa", t["rows"][0][1] == "1395.00"
          and t["rows"][0][2] == "87.79")
    check("note 写明 σl3/σl5/σl6 不进 σpe", "σl3/σl5/σl6" in t["note"])
    check("note 写明 2D 没有 3D 阶段维度", "没有 3D 的阶段维度" in t["note"])
    check("状态文案是中文", "2D 分项预应力损失明细" in L.status_text(ok))

    bad_cases = [
        ("空文本", "", "no_section"),
        ("无段头", "NPRESTRESS=1 BASIS=2d_global\n" + ROW_OK, "no_section"),
        ("内核 ERROR", loss_text(error="no_lossref_prestress", rows=[]), "kernel_error"),
        ("缺 NPRESTRESS 头", loss_text(header=False), "bad_header"),
        ("坏 BASIS", loss_text(basis="xyz"), "bad_basis"),
        ("缺字段", loss_text(rows=[ROW_OK.replace(" SL6=108364004.044", "")]), "bad_row"),
        ("非数值字段", loss_text(rows=[ROW_OK.replace("SL3=40000000", "SL3=abc")]), "bad_row"),
        ("无法识别行", loss_text(rows=[ROW_OK, "JUNK 1 2 3"]), "bad_row"),
        ("行数不符", loss_text(n=2), "row_count_mismatch"),
        ("NPRESTRESS=0 且无行", loss_text(n=0, rows=[]), "row_count_mismatch"),
        ("RATIO 与 SPE/SIGMA_CON 不一致",
         loss_text(rows=[ROW_OK.replace("RATIO=0.820782595059", "RATIO=0.5")]),
         "inconsistent_ratio"),
        ("重复 NOTE 行不污染数据行", loss_text(rows=["NOTE=" + L.NOTE, ROW_OK]), "ok"),
    ]
    for name, text, status in bad_cases:
        d = L.parse_loss_txt(text)
        check(f"负例「{name}」→ {status}", d.get("status") == status,
              str(d.get("status")))
        if status != "ok":
            check(f"负例「{name}」不出表", L.loss_table(d) is None)
            check(f"负例「{name}」有中文状态且无 Markdown 粗体",
                  "**" not in L.status_text(d) and "本次" in L.status_text(d)
                  or "解析拒绝" in L.status_text(d) or "不摆空表" in L.status_text(d))
    tail = L.parse_loss_txt(loss_text() + "[OTHER]\nP2 JUNK\n")
    check("段后下一段正确截断，不把别人的行收进来",
          tail.get("status") == "ok" and len(tail.get("rows") or []) == 1)

    src = read(ROOT / "prestress_loss_2d.py")
    check("口径层不调用 computePrestressLosses", "computePrestressLosses(" not in src)
    check("口径层不写力学公式（无 math.exp / def compute）",
          "math.exp" not in src and "def compute" not in src)
    check("口径层不解析内核 3D 段名", "stage prestress loss" not in src
          or "不等于 3D" in src)
    check("STATUS_TEXT 覆盖所有状态且无 Markdown 粗体",
          len(L.STATUS_TEXT) >= 12 and all("**" not in v for v in L.STATUS_TEXT.values()))
    check("汇总按布尔位统计（防假绿）",
          "PASS.append((name, bool(ok), detail))" in read(Path(__file__))
          and "sum(1 for _, ok, _ in PASS if ok)" in read(Path(__file__)))


# ---------------------------------------------------------------- 零污染与接线

def wiring_and_zero_pollution() -> None:
    print("\n[5] 双层 opt-in 默认值、零污染、报告与界面接线")
    sig = inspect.signature(solve_2d)
    check("solve_2d 有 loss_detail 且默认 False",
          "loss_detail" in sig.parameters
          and sig.parameters["loss_detail"].default is False)
    check("Solve2DResult 默认 not_requested",
          Solve2DResult().prestress_loss.get("status") == "not_requested")
    check("Solve2DResult 默认无表", not Solve2DResult().prestress_loss_tables())

    s2 = read(ROOT / "solve_2d.py")
    check("solve_2d 只给明细 exe 传 --loss-detail-only",
          '"--loss-detail-only"' in s2 and "--modal-detail" in s2)
    check("solve_2d 无 LOSSREF 束时写 no_model 中文状态", '"no_model"' in s2)
    check("solve_2d 读 .loss.txt 而非 .result", '".loss.txt"' in s2)
    check("solve_2d 先清残留再读", "loss_path.unlink()" in s2)
    check("solve_2d 写中文状态到 diagnostics", "loss2d.status_text" in s2)

    md = read(ROOT / "model_doc.py")
    check("ModelDoc.solve 透传 loss_2d_detail（默认 False）",
          "loss_2d_detail: bool = False" in md
          and "loss_detail=bool(loss_2d_detail)" in md)
    app = read(ROOT / "app.py")
    check("界面有第二层 opt-in 开关且默认关",
          "loss2ddetail_opt" in app and "value=False, key=\"loss2ddetail_opt\"" in app)
    check("界面把开关传给 doc.solve", "loss_2d_detail=_loss2d_on" in app)
    check("界面 stale 判断含该开关与参数",
          '"loss2ddetail"' in app and '"loss2dcfg"' in app)
    check("界面参数显式挂 BridgeSpec，不进 DEFAULTS",
          "doc.model.prestress_losses = {1:" in app
          and "loss2ddetail" not in read(ROOT / "model_doc.py"))
    html = read(ROOT / "export_html.py")
    check("write_2d_html 有 opt-in 明细章且默认不出现",
          'loss_html = ""' in html and "{loss_html}" in html
          and '_l.get("status") != "not_requested"' in html)
    rp = read(ROOT / "report_2d.py")
    check("report_2d 有 opt-in 章与边界注",
          '"2D 分项预应力损失明细"' in rp and "LOSS_BOUNDARY_NOTE" in rp)
    check("report_2d 不另写渲染器", "return R.write_all(rep, out_dir" in rp)

    # 默认路径零污染：不请求时不出章、无新 diagnostics、无 .loss.txt
    spec = build_beam_bridge(span=10.0, n_spans=1, udl_kN=0.0, staged=False)
    res = solve_2d(spec, out_dir=OUT / "default", dat_name="m.dat")
    check("默认求解 status=not_requested",
          res.prestress_loss.get("status") == "not_requested")
    check("默认求解不写 .loss.txt", not (OUT / "default" / "m.loss.txt").is_file())
    check("默认求解 diagnostics 无分项损失条目",
          not any("分项预应力损失" in d for d in res.diagnostics))
    h0 = write_2d_html(res, spec, OUT / "default.html").read_text(encoding="utf-8")
    check("默认 HTML 无分项损失章", "2D 分项预应力损失明细" not in h0)
    h0b = write_2d_html(res, spec, OUT / "default2.html").read_text(encoding="utf-8")
    check("默认 HTML 可重复且逐位不变", h0 == h0b)
    rep0 = report_2d.build_2d_document(res, spec)
    check("默认四出口 Report 无分项损失表",
          not any("prestress_loss" in k for k in rep0.tables))
    check("默认四出口 Report 无分项损失章",
          not any("2D 分项预应力损失明细" in str(b.get("text", ""))
                  for b in rep0.blocks))


# ---------------------------------------------------------------- 四出口同源

def exports() -> None:
    print("\n[6] 四出口同源：同一份表字典进 Report / HTML / CSV")
    d = L.parse_loss_txt(loss_text())
    res = Solve2DResult()
    res.prestress_loss = d
    spec = build_beam_bridge(span=10.0, n_spans=1, udl_kN=0.0, staged=False)
    rep = report_2d.build_2d_document(res, spec)
    check("Report 挂 prestress_loss_table", "prestress_loss_table" in rep.tables)
    tb = rep.tables["prestress_loss_table"]
    check("Report 表与口径层表逐格同源", tb == L.loss_table(d))
    check("Report 有对应 h1 章",
          any(b.get("type") == "h1" and "2D 分项预应力损失明细" in str(b.get("text"))
              for b in rep.blocks))
    check("Report 有 LOSS_BOUNDARY_NOTE",
          any("不设 STAGE 占位" in str(b.get("text", "")) for b in rep.blocks))
    OUT.mkdir(parents=True, exist_ok=True)
    paths = report_2d.write_2d_all(rep, OUT / "exports", stem="loss2d", pdf=False)
    check("三件套写出成功（不生成占位 PDF）",
          all(k in paths for k in ("html", "docx", "zip")) and "pdf" not in paths)
    html = read(paths["html"])
    check("四出口 HTML 含表标题", "2D 分项预应力损失" in html)
    check("四出口 HTML 含 σcon 与 RATIO 数值",
          "1395.00" in html and "0.8208" in html)
    with zipfile.ZipFile(paths["zip"]) as z:
        names = z.namelist()
        csv_name = next((n for n in names if "prestress_loss_table" in n), None)
        check("CSV 数据包含分项损失表", csv_name is not None, str(names))
        if csv_name:
            csv = z.read(csv_name).decode("utf-8")
            check("CSV 与表字典同源（表头）", all(h in csv for h in tb["headers"][:3]))
            check("CSV 与表字典同源（数值）", "1395.00" in csv and "0.8208" in csv)
    h = write_2d_html(res, spec, OUT / "optin.html").read_text(encoding="utf-8")
    check("默认 HTML 出口也有 opt-in 章", "2D 分项预应力损失明细" in h)
    check("默认 HTML 出口的表与 Report 同源", "1395.00" in h and "0.8208" in h)


# ---------------------------------------------------------------- 文档对拍

def docs() -> None:
    print("\n[7] 两仓文档口径对拍")
    fe_log = read(FE / "CHANGELOG.md")
    fe_man = read(FE / "USER_MANUAL.md")
    check("FE CHANGELOG 登记 [PRESTRESSLOSS2D] 第十次",
          "[PRESTRESSLOSS2D]" in fe_log and "第十次" in fe_log)
    check("FE USER_MANUAL 登记 --loss-detail-only",
          "--loss-detail-only" in fe_man)
    check("FE 文档写明默认输出逐位不变",
          "逐位不变" in fe_log or "逐位不变" in fe_man)

    mat = read(ROOT / "能力边界矩阵.md")
    spec = read(ROOT / "B类后续项接入规格.md")
    rd = read(ROOT / "README.md")
    use = read(ROOT / "使用说明.md")
    guide = read(ROOT / "开发使用手册.md")
    check("能力矩阵登记第十次闭环", "第十次" in mat and "[PRESTRESSLOSS2D]" in mat)
    check("能力矩阵该格改 2D=P 不虚报 Y", "分项预应力损失" in mat and "2D=P" in mat)
    check("能力矩阵写明 B2 剩余 2 项", "B2 剩余 2 项" in mat)
    check("B 类规格登记第十次与段名",
          "第十次" in spec and "[PRESTRESSLOSS2D]" in spec)
    check("B 类规格写明 B2 剩余 2 项", "B2 剩余 2 项" in spec)
    for name, text in (("README", rd), ("使用说明", use), ("开发手册", guide)):
        check(f"{name} 登记 verify_prestress_loss_2d.py",
              "verify_prestress_loss_2d.py" in text)
    check("README 登记新守卫计数", "verify_prestress_loss_2d.py" in rd and "第十次" in rd)
    check("开发手册回归清单含新守卫", "verify_prestress_loss_2d.py" in guide)
    check("矩阵守卫有第十次防漂移断言",
          "[PRESTRESSLOSS2D]" in read(ROOT / "verify_capability_matrix.py"))
    check("B 类规格守卫有第十次断言",
          "[PRESTRESSLOSS2D]" in read(ROOT / "verify_b_class_spec.py"))


def main() -> int:
    print("=" * 78)
    print("2D 分项预应力损失明细段守卫（B2 第十次，纯离线 + 本地内核实跑）")
    print("=" * 78)
    fe_sources()
    fe_runtime()
    bs_contract()
    parser_cases()
    wiring_and_zero_pollution()
    exports()
    docs()
    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(PASS)} 通过")
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
