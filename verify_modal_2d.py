"""verify_modal_2d.py —— 2D 自振 / 线性屈曲接入链路守卫。

背景：`能力边界矩阵.md` 原先把「模态与自振特性」「线性屈曲因子」的 2D 格
记成 `N`，但源码事实是 FEM-Eigen 的 `fem_2d.cpp::computeNaturalFrequencies2D`
/ `computeBucklingFactors2D` 早就能算，`example_modal.exe` 会写 `.modal.txt`，
缺的只是 BridgeStudio 的解析与报告链路。本守卫锁住这层接入：

离线：黄金 `.modal.txt` oracle、手写带 λ 段样本、故障注入（段缺失 / 字段不全 /
f1 自相矛盾 / 屈曲段空 / 3D 段串入）、f1 同源对拍、表格契约、默认路径源码级断言。
live（`example_modal.exe` 存在时）：`solve_2d` 默认与开启对照、`.dat` 逐位不变、
HTML 计算书同源章节、f1 与 `f1_vertical` 一致、Euler 支柱 λ 闭式解。

铁律：不重算 K/M、不自己造频率或 λ；周期 T / 振型 / 参与质量只能来自
FE opt-in 的 `[MODAL2D]` 段（见 `verify_modal_detail_2d.py`）；缺段不补零、
不静默兜底。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import inspect
import math
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import modal_2d
from export_html import write_2d_html
from solve_2d import (Solve2DResult, modal_solver_available, solve_2d,
                      solve_2d_available)

OUT = ROOT / "out" / "verify_modal_2d"
RESULTS: list[tuple[bool, str, str]] = []

GOLD_TXT = """== 自振频率 ==
f1 = 128.199 Hz
f2 = 146.913 Hz
f3 = 387.766 Hz
f4 = 587.712 Hz
f5 = 656.896 Hz
f6 = 942.016 Hz
基频 f1 = 128.199 Hz，冲击系数 μ = 0.45
"""
GOLD_F = [128.199, 146.913, 387.766, 587.712, 656.896, 942.016]

BUCK_TXT = GOLD_TXT + """== 屈曲荷载因子（Pcr = λ·P_ref）==
λ1 = 1.2198e10
λ2 = 4.8792e10
λ3 = 1.0978e11
"""


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((bool(ok), name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def offline_gold() -> None:
    m = modal_2d.parse_modal_txt(GOLD_TXT)
    check("黄金 .modal.txt 状态 ok", m["status"] == "ok", m["status"])
    check("解析出 6 阶频率", len(m["frequencies"]) == 6,
          str(len(m["frequencies"])))
    for i, want in enumerate(GOLD_F, 1):
        check(f"第 {i} 阶频率 = {want:g} Hz",
              abs(m["frequencies"][i - 1] - want) < 1e-9,
              f"{m['frequencies'][i - 1]:g}")
    check("基频 f1 = 128.199 Hz", abs(m["f1"] - 128.199) < 1e-9, f"{m['f1']:g}")
    check("冲击系数 μ = 0.45", abs(m["mu"] - 0.45) < 1e-12, f"{m['mu']:g}")
    check("黄金样本没有屈曲段", not m["has_buckling_section"])
    check("黄金样本 λ 为空（不补零）", m["buckling"] == [])
    check("黄金样本不出屈曲表", modal_2d.buckling_table(m) is None)
    t = modal_2d.modal_table(m)
    check("频率表 6 行", t is not None and len(t["rows"]) == 6)
    check("频率表表头 2 列", t is not None and t["headers"] == ["阶", "频率 f (Hz)"])
    check("频率表注明缺字段不补零", t is not None and "不补零" in t["note"])
    txt = modal_2d.status_text(m)
    check("无屈曲段写明「未写出」", "未写出屈曲荷载因子段" in txt)
    check("无屈曲段写明不推算 λ", "不补零、不推算 λ" in txt)
    # 内核自带算例的黄金文件若在场，逐字对拍（纯读，不写）。
    from solve_2d import _fem_eigen_dir

    fe = _fem_eigen_dir()
    gold = (fe / "verify_modal.modal.txt") if fe else None
    if gold and gold.is_file():
        raw = gold.read_text(encoding="utf-8", errors="replace")
        check("FE 自带 verify_modal.modal.txt 与内置 oracle 同源",
              raw.strip() == GOLD_TXT.strip())
        check("解析 FE 黄金文件得同一组频率",
              modal_2d.parse_modal_txt(raw)["frequencies"] == GOLD_F)


def offline_buckling() -> None:
    m = modal_2d.parse_modal_txt(BUCK_TXT)
    check("带 λ 段样本状态 ok", m["status"] == "ok", m["status"])
    check("解析出 3 阶 λ", len(m["buckling"]) == 3, str(len(m["buckling"])))
    for i, want in enumerate((1.2198e10, 4.8792e10, 1.0978e11), 1):
        check(f"λ{i} 数值逐字段对拍", abs(m["buckling"][i - 1] - want) < 1e4,
              f"{m['buckling'][i - 1]:g}")
    check("λ 段不影响频率表 6 行",
          len(modal_2d.modal_table(m)["rows"]) == 6)
    t = modal_2d.buckling_table(m)
    check("屈曲表 3 行", t is not None and len(t["rows"]) == 3)
    check("屈曲表表头 2 列", t is not None
          and t["headers"] == ["阶", "λ（Pcr = λ·P_ref）"])
    check("屈曲表写明不是规范稳定安全系数",
          t is not None and "不是规范稳定安全系数" in t["note"])
    check("屈曲表写明 λ ≠ §5.3 的 φ",
          t is not None and "§5.3 的稳定系数 φ" in t["note"])
    # 段头有、λ 行没有：不许拿空表冒充。
    empty_sec = modal_2d.parse_modal_txt(GOLD_TXT + modal_2d.BUCK_SECTION + "\n")
    check("屈曲段头存在但无 λ 行时不算有屈曲",
          not empty_sec["has_buckling_section"] and empty_sec["status"] == "ok")
    check("屈曲段头存在但无 λ 行时中文写明",
          "未写出屈曲荷载因子段" in modal_2d.status_text(empty_sec))
    # 3D 的 F/S 段格式串入 2D 文件不得污染解析。
    mixed = modal_2d.parse_modal_txt(
        GOLD_TXT + "F 1 3.0 0.333 0.1 0.2 0.3\nS 1 2 0.5\n"
        + modal_2d.BUCK_SECTION + "\nλ1 = 7.5\n")
    check("3D 的 F/S 行不污染 2D 频率", len(mixed["frequencies"]) == 6)
    check("混入 3D 行后 λ 仍按 2D 段解析",
          mixed["buckling"] == [7.5], str(mixed["buckling"]))
    latin = modal_2d.parse_modal_txt(
        GOLD_TXT + "== 屈曲荷载因子 ==\nl1 = 2.5\n")
    check("拉丁字母 l<i> 也按内核段解析", latin["buckling"] == [2.5],
          str(latin["buckling"]))


def offline_failures() -> None:
    cases = [
        ("空文本", "", "no_frequency_section"),
        ("只有频率段头", modal_2d.FREQ_SECTION + "\n", "no_frequencies"),
        ("缺基频 μ 行", "== 自振频率 ==\nf1 = 3.0 Hz\n", "incomplete_base_line"),
        ("基频与 f1 行不一致",
         "== 自振频率 ==\nf1 = 3.0 Hz\n基频 f1 = 9.0 Hz，冲击系数 μ = 0.2\n",
         "inconsistent_f1"),
        ("其它段头冒充频率",
         "== 随便一段 ==\nf1 = 3.0 Hz\n", "no_frequency_section"),
    ]
    for name, txt, want in cases:
        m = modal_2d.parse_modal_txt(txt)
        check(f"故障注入：{name} → {want}", m["status"] == want, m["status"])
        check(f"故障注入：{name} 不出表",
              modal_2d.modal_table(m) is None
              and modal_2d.buckling_table(m) is None)
        check(f"故障注入：{name} 有中文说明",
              bool(modal_2d.status_text(m).strip()))
    bad = modal_2d.parse_modal_txt(
        "== 自振频率 ==\nf1 = 3.0 Hz\n基频 f1 = 9.0 Hz，冲击系数 μ = 0.2\n")
    check("f1 矛盾时 f1_matches_section=False", not bad["f1_matches_section"])
    for st in ("not_requested", "unavailable", "no_output", "solver_error",
               "no_frequency_section", "no_frequencies",
               "incomplete_base_line", "inconsistent_f1"):
        m = {"status": st}
        check(f"状态 {st} 中文文案不为空",
              len(modal_2d.status_text(m)) >= 8)
        check(f"状态 {st} 两张表都不出",
              modal_2d.modal_table(m) is None
              and modal_2d.buckling_table(m) is None)
    check("not_requested 写明默认关闭",
          "默认关闭" in modal_2d.status_text({"status": "not_requested"}))
    check("unavailable 写明怎么编译回来",
          "cmake --build build --target example_modal"
          in modal_2d.status_text({"status": "unavailable"}))


def offline_consistency() -> None:
    m = modal_2d.parse_modal_txt(GOLD_TXT)
    c = modal_2d.consistency(m, 128.199)
    check("f1 与影响线同源 → ok", c["status"] == "ok", c["status"])
    check("同源文案写明「同源一致」", "同源一致" in c["note"])
    c = modal_2d.consistency(m, 999.0)
    check("两套频率不一致 → mismatch", c["status"] == "mismatch")
    check("mismatch 禁止同时引用", "禁止同时引用" in c["note"])
    c = modal_2d.consistency(m, None)
    check("无影响线 → no_reference", c["status"] == "no_reference")
    check("no_reference 不拿冲击系数反推频率",
          "不拿冲击系数反推频率" in c["note"])
    c = modal_2d.consistency({"status": "unavailable"}, 128.199)
    check("无模态结果 → not_available", c["status"] == "not_available")
    c = modal_2d.consistency(m, 128.199 * 1.0001)
    check("6 位有效数字级微小差仍判同源", c["status"] == "ok")


def offline_defaults() -> None:
    import model_doc

    src_solve = (ROOT / "solve_2d.py").read_text(encoding="utf-8")
    src_html = (ROOT / "export_html.py").read_text(encoding="utf-8")
    src_doc = (ROOT / "model_doc.py").read_text(encoding="utf-8")
    src_app = (ROOT / "app.py").read_text(encoding="utf-8")
    check("solve_2d modal 默认 False",
          inspect.signature(solve_2d).parameters["modal"].default is False)
    check("ModelDoc.solve modal_2d 默认 False",
          inspect.signature(model_doc.ModelDoc.solve).parameters["modal_2d"].default
          is False)
    r = Solve2DResult()
    check("Solve2DResult 默认 not_requested",
          r.modal["status"] == "not_requested")
    check("默认结果 modal_tables() 为空", r.modal_tables() == {})
    check("solve_2d 登记 modal_solver_available",
          "def modal_solver_available" in src_solve)
    check("solve_2d 不重算周期/振型（源码级）",
          "不重算 K/M" in src_solve and "T = 1/f" in
          (ROOT / "modal_2d.py").read_text(encoding="utf-8"))
    check("solve_2d 把开关传给 exe 的结果解析",
          "parse_modal_txt" in src_solve and "modal_2d.consistency" in src_solve)
    check("ModelDoc.solve 把开关传给 2D 链",
          "modal=bool(modal_2d)" in src_doc)
    check("capability() 自述 modal2d 可用性",
          '"modal2d":' in src_doc)
    check("HTML 默认 not_requested 不出章节",
          '_m.get("status") != "not_requested"' in src_html)
    check("HTML 章节与界面读同一份表格",
          "res.modal_tables()" in src_html and "modal_tables" in src_app)
    check("界面有 opt-in 开关",
          'key="modal2d_opt"' in src_app and "value=False" in src_app)
    check("界面开关纳入结果过期判断",
          'bool(_s2d.get("modal2d", False)) != _modal2d_on' in src_app)
    mat = (ROOT / "能力边界矩阵.md").read_text(encoding="utf-8")
    cap = [ln for ln in mat.splitlines()
           if "模态与自振特性 |" in ln or "线性屈曲因子 |" in ln]
    check("能力矩阵两行 2D 都改 Y",
          len(cap) == 2 and all("| Y | Y |" in ln for ln in cap),
          str(cap))
    check("能力矩阵登记 2D 依据符号",
          "modal_2d.py::parse_modal_txt" in mat
          and "solve_2d.py::modal_solver_available" in mat)


def _buckling_spec():
    """简支柱 + 参考轴压 1 N（test_2d.cpp::testBuckling 的同款锚点）。"""
    from bridge_spec import (BoundaryDef, BridgeSpec, ElemDef, ElemKind,
                             ElemTypeDef, MaterialDef, NodeDef, NodalLoadDef)

    n, L, E, A, I = 16, 10.0, 2.06e11, 0.18, 0.6
    return BridgeSpec(
        title="2D 欧拉支柱",
        nodes=[NodeDef(id=i + 1, x=L * i / n) for i in range(n + 1)],
        elements=[ElemDef(id=i + 1, type_id=1, n1=i + 1, n2=i + 2)
                  for i in range(n)],
        elem_types=[ElemTypeDef(id=1, kind=ElemKind.BEAM, A=A, I=I,
                                WT=0.4, WB=0.4)],
        materials=[MaterialDef(id=1, E=E, dens=7850.0)],
        boundaries=[BoundaryDef(node=1, dofs="1-2"),
                    BoundaryDef(node=n + 1, dofs="2")],
        nodal_loads=[NodalLoadDef(node=n + 1, dof=1, value=-1.0)],
        gravity=0.0,
    ), (math.pi ** 2 * E * I / L ** 2)


def live_chain() -> None:
    if not solve_2d_available() or not modal_solver_available():
        print("  [live 跳过] 2D 内核或 example_modal.exe 未编译")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    from bridge_spec import build_beam_bridge

    spec = build_beam_bridge(span=30.0, n_spans=1, udl_kN=20.0,
                             gravity=0.0, lane=True)
    r0 = solve_2d(spec, out_dir=OUT / "plain", dat_name="plain.dat")
    r1 = solve_2d(spec, out_dir=OUT / "modal", dat_name="modal.dat", modal=True)
    check("默认求解不请求模态", r0.modal["status"] == "not_requested")
    check("默认 diagnostics 不多写模态说明",
          len(r1.diagnostics) == len(r0.diagnostics) + 2,
          f"{len(r0.diagnostics)} → {len(r1.diagnostics)}")
    check("开启后状态 ok", r1.modal["status"] == "ok", r1.modal["status"])
    check("开启后频率非空且 f1>0",
          bool(r1.modal["frequencies"]) and r1.modal["f1"] > 0,
          f"f1={r1.modal.get('f1'):g}")
    check("f1 与 f1_vertical 同源一致",
          r1.modal["consistency"]["status"] == "ok",
          f"{r1.stats.get('f1_vertical')}")
    check("开启后 μ 与影响线冲击系数同源",
          r1.stats.get("impact_mu") is not None
          and abs(r1.modal["mu"] - r1.stats["impact_mu"]) < 1e-6)
    check("开关不改 .dat 逐位", r0.dat_text == r1.dat_text)
    check("开启后出频率表", "modal_table" in r1.modal_tables())
    h0 = write_2d_html(r0, spec, OUT / "plain.html").read_text(encoding="utf-8")
    h1 = write_2d_html(r1, spec, OUT / "modal.html").read_text(encoding="utf-8")
    check("默认 HTML 不出模态章（逐位不变口径）",
          "2D 自振特性与线性屈曲" not in h0)
    check("开启后 HTML 出模态章", "2D 自振特性与线性屈曲" in h1)
    check("HTML 频率表与结果同源",
          f"{r1.modal['frequencies'][0]:.6g}" in h1)
    check("HTML 写明 2D 缺字段不补零", "缺的字段不补零" in h1)
    check("HTML 无 Markdown 星号残留", "**" not in h1)

    # λ 段 live：内核真的写出屈曲段，且 λ1 对上 Euler 闭式解。
    bs, want = _buckling_spec()
    rb = solve_2d(bs, out_dir=OUT / "buck", dat_name="buck.dat", modal=True)
    check("欧拉支柱算例写出 λ 段",
          rb.modal.get("has_buckling_section", False)
          and bool(rb.modal.get("buckling")),
          str(rb.modal.get("status")))
    if rb.modal.get("buckling"):
        got = rb.modal["buckling"][0]
        rel = abs(got - want) / want
        check("λ1 对拍 Euler Pcr=π²EI/L²（1%）", rel < 0.01,
              f"λ1={got:.6g} Pcr={want:.6g} 误差 {rel * 100:.3f}%")
        t = modal_2d.buckling_table(rb.modal)
        check("live 屈曲表带 λ 口径说明",
              t is not None and "稳定承载力的上限" in t["note"])
    hb = write_2d_html(rb, bs, OUT / "buck.html").read_text(encoding="utf-8")
    check("λ 算例 HTML 出屈曲表", "Pcr = λ·P_ref" in hb)
    check("λ 算例 HTML 不冒充规范安全系数",
          "不是规范稳定安全系数" in hb)
    # 缺 exe 的路径由 status_text 锁住（不真的删 exe）。
    check("缺 exe 时中文写明做不了",
          "做不了" in modal_2d.status_text({"status": "unavailable"}))


def main() -> int:
    print("=" * 78)
    print("2D 自振 / 线性屈曲接入链路守卫")
    print("=" * 78)
    offline_gold()
    offline_buckling()
    offline_failures()
    offline_consistency()
    offline_defaults()
    live_chain()
    n_pass = sum(1 for ok, _, _ in RESULTS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(RESULTS)} 通过")
    return 0 if n_pass == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
