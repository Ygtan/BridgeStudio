"""verify_capacity_axial_2d.py —— 2D 压弯 / 轴压半部（第十六次）独立守卫。

背景（2026-11-12 第十六次，B2 收官后补齐 2D 承载力另一半）：
三路只读调研发现一条**过时注释造成的误判** —— `combination.py` 写
「内核的 live beam envelope 段只输出 My/Vz 包络，没有活载轴力」，
第十二次据此拒绝压弯 / 轴压。但 FE `defaultEnvelopeSpecs()` 明明为每单元
每端登记 `FORCE/N`（`fem_2d.cpp:4457`），BS `_parse_lane_envelope()` 也把
`NMin/NMax` 解析进了 `live_beam`；只是 `beam_combinations()` 按历史口径取
`q.get("N", 0.0)`（键名不匹配，恒取 0），组合 N 才退化成恒载单值。

用户四项拍板（全选推荐）：① 只做 2D 压弯 / 轴压半部（纯 BS、FE 零改动）；
② 新建 opt-in 独立口径层，不改 `beam_combinations` 默认路径与 12 角点应力；
③ 复用 3D 三链分派（5% 轴压 / 1% 偏心距）+ `RebarSpec2D.l0` 必填、缺失
整章拒绝；④ 能力矩阵仍标 `P`（`P ≠ Y`），按 D 类准则只改过时措辞。

本守卫锁住 9 组：
- [1] 新模块源码契约（接口符号、常量、docstring 口径、纯 Python 隔离）；
- [2] 纠偏证据（FE 源码 `FORCE/N` 登记与写出、BS `NMin/NMax` 解析、
  `combination.py` 注释改措辞、`beam_combinations` 数值行为逐位不变）；
- [3] 闭式 oracle（同端配对 2×2 角点、γ_G/γ_Q 组合、Nc 符号、5%/1%
  分派、与 `section_strength.check_axial / check_eccentric` 直接对拍、
  负弯矩面、不跨端不跨分量）；
- [4] 拒绝与缺数据负例（缺 l0 / 缺活载轴力包络 / 无配筋 / 非混凝土 /
  非法牌号 / 断引用 / 无基本组合 / 无成桥态 / 全归 flex / 无效应），
  一律中文状态、不摆空表、不补零；
- [5] 默认零污染（默认 `not_requested`、默认 HTML 逐字节、12 格黄金哈希、
  双层 opt-in 默认 False、FE 仓零改动、默认 diagnostics）；
- [6] 双层 opt-in 接线（solve_2d / ModelDoc.solve / app / export_html /
  report_2d / capability 自述 / 会话状态过期判断）；
- [7] 四出口同源（HTML / Report / `axial_capacity_tables()` 唯一取数口、
  状态非 ok 不出表、报告 ⑨′ 章不重排后续章号）；
- [8] 两仓文档对拍（矩阵仍 P、§4.2 计数、B2 剩余 0 项、第十六次闭环
  记录、FE 三份文档零命中、D 类历史条目保留）；
- [9] 汇总不许「假绿」（本守卫布尔位结构、断言组数、数字无关口径）。

铁律：**不重算**（规范公式只由 `section_strength` 纯函数给出，守卫不另写
一套）、**不补零**（缺项显式拒绝）、**不冒充**（2D 逐分量包络近似不等于
3D 全套承载力，仍 `P ≠ Y`）。汇总按布尔位统计：
`n_pass = sum(1 for _, ok, _ in PASS if ok)`，`n_pass == len(PASS)` 才返回 0。
"""

from __future__ import annotations

import hashlib
import inspect
import math
import sys
from pathlib import Path
from typing import Any

from console_utf8 import setup_console

setup_console()

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import capacity_2d as C2
import capacity_axial_2d as CA
import combination as CB
import model_doc as MD
import report_2d
import section_strength as SS
from export_html import write_2d_html
from model_doc import ModelDoc
from solve_2d import Solve2DResult, _fem_eigen_dir, solve_2d
from verify_capacity_2d import FINAL, REBAR, _res, _spec
from verify_stage_tension_3d import DEFAULT_DAT_SHA

FE = _fem_eigen_dir() or Path("D:/AI-work/FEM-Eigen")
OUT = ROOT / "out" / "verify_capacity_axial_2d"
PASS: list[tuple[str, bool, str]] = []

#: 配筋是 opt-in Python-only 契约；l0 本轮必填（mm）。
REBAR_L0 = {**REBAR, "l0": 6000.0}


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail else ""))


def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def near(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(a)), abs(float(b)))


def spec_(*, rebar: dict | None = None, **kw: Any):
    return _spec(rebar=dict(rebar if rebar is not None else REBAR_L0), **kw)


def live_of(nmin: float, nmax: float, mymin: float, mymax: float,
            ends: tuple[str, ...] = ("i", "j")) -> dict:
    return {(1, e): {"NMin": nmin, "NMax": nmax,
                     "MyMin": mymin, "MyMax": mymax,
                     "VzMin": 0.0, "VzMax": 0.0} for e in ends}


def final_of(ng: float, mg: float, ends: tuple[str, ...] = ("i", "j")) -> dict:
    return {(1, e): {"N": ng, "My": mg, "Vz": 0.0} for e in ends}


def case_a():
    """轴心受压 + 归 flex 的混合案例（M 全 0）。"""
    final = final_of(-1.0e6, 0.0)
    live = live_of(-0.5e6, 0.5e6, 0.0, 0.0)
    return _res(final, live), spec_()


def case_b():
    """偏心受压 + 归 flex 的混合案例（M 两侧非零）。"""
    final = final_of(-1.0e6, 2.0e5)
    live = live_of(-0.5e6, 0.5e6, -1.0e5, 3.0e5)
    return _res(final, live), spec_()


# ---------------------------------------------------------------- [1] 源码契约

def contract() -> None:
    print("\n[1] 新模块源码契约（唯一口径层 / 纯 Python 隔离）")
    src = read(ROOT / "capacity_axial_2d.py")
    for token in ("def evaluate(", "def tables(", "def status_text(",
                  "SOURCE =", "BASIS =", "GAMMA0 = 1.0",
                  "AXIAL_FRACTION = 0.05", "ECCENTRICITY_FRACTION = 0.01",
                  "BOUNDARY_NOTE", "TABLE_NOTE_EXTRA", "STATUS_TEXT"):
        check(f"接口符号 {token}", token in src)
    for token in ("import combination", "import section_strength",
                  "from capacity_2d import _capacity_section",
                  "section_strength.check_eccentric",
                  "section_strength.check_axial",
                  "section_strength.eccentric_table",
                  "section_strength.axial_table",
                  "capacity_calculation_sheet",
                  "mirror_section_for_negative",
                  "combination.ULTIMATE"):
        check(f"只复用现成公式与契约 {token}", token in src)
    for token in ("不调 FE 内核", "不改 `.dat`", "不改默认 HTML",
                  "不写回 `BeamCombo.rows`", "同端配对",
                  "逐分量独立极值", "不静默", "P ≠ Y"):
        check(f"docstring 如实口径 {token}", token in src)
    check("SOURCE 指向 final_beam + live_beam + rebar",
          "final_beam" in CA.SOURCE and "live_beam" in CA.SOURCE
          and "rebar" in CA.SOURCE)
    check("BASIS 标 2d_beam_axial_ultimate",
          CA.BASIS == "2d_beam_axial_ultimate")
    check("γ0 与 3D 同源 =1.0", CA.GAMMA0 == 1.0)
    check("5% / 1% 阈值与 3D 同源",
          CA.AXIAL_FRACTION == 0.05 and CA.ECCENTRICITY_FRACTION == 0.01)
    check("阈值不写进模型字段（只在口径层）",
          "AXIAL_FRACTION" not in read(ROOT / "bridge_spec.py")
          and "ECCENTRICITY_FRACTION" not in read(ROOT / "bridge_spec.py"))
    sig = inspect.signature(CA.evaluate)
    check("evaluate 签名 (res, spec, gamma0)",
          list(sig.parameters) == ["res", "spec", "gamma0"])
    check("evaluate 默认 gamma0 = GAMMA0",
          sig.parameters["gamma0"].default == CA.GAMMA0)
    check("tables 签名只有一个参数",
          list(inspect.signature(CA.tables).parameters) == ["d"])
    for bad in ("subprocess", "open(", "write_text", "to_dat(",
                "combination.beam_combinations(", "check_capacity("):
        check(f"口径层不碰 {bad}", bad not in src)
    check("口径层不 import solve_2d（无循环依赖）",
          "import solve_2d" not in src)
    check("三链分派只在本层用一次 5%",
          src.count("AXIAL_FRACTION * n_axial") == 1)
    check("l0 缺失走 reasons 而非静默 φ=1",
          "不静默取短柱" in src and "sec.l0" in src)
    check("边界说明明写不等于 3D 全套承载力",
          "仍标 P" in CA.BOUNDARY_NOTE and "近似" in CA.BOUNDARY_NOTE)
    for st in ("not_requested", "unavailable", "no_ultimate",
               "no_axial_live", "no_beam_section", "no_rebar_section",
               "no_compression", "no_effect", "refused"):
        check(f"状态文案覆盖 {st}", st in src)
    check("状态非 ok 一律 tables() == {}",
          '!= "ok"' in src and "return {}" in src)
    check("默认 HTML 占位符与 capacity_html 同一行（不多一个换行）",
          "{capacity_html}{axial_html}" in read(ROOT / "export_html.py"))


# ---------------------------------------------------------------- [2] 纠偏证据

def correction() -> None:
    print("\n[2] 调研纠偏：2D 本来就有活载轴力包络，BS 组合层没消费")
    fem2d = read(FE / "fem_2d.cpp")
    inf = read(FE / "example_influence.cpp")
    s2d = read(ROOT / "solve_2d.py")
    comb = read(ROOT / "combination.py")
    check("FE defaultEnvelopeSpecs 为每端登记 FORCE/N",
          'req.component = "N"' in fem2d
          and '"_N" + std::to_string(end)' in fem2d)
    check("FE 影响线响应支持 FORCE/N",
          'spec.kind == "FORCE"' in fem2d
          and 'spec.component == "N"' in fem2d)
    check("FE 写出 E<id>_N<end> 包络标签",
          '"E" + std::to_string(e.id) + "_N"' in fem2d)
    check("FE writeEnvelopeFile 写 label/max/min",
          "writeEnvelopeFile" in inf and '"label\\tmax\\tmin' in inf)
    check("BS _parse_lane_envelope 收 N 标签",
          'r"^E(\\d+)_([NVM])([12])$"' in s2d)
    check("BS 把活载轴力解析成 NMin/NMax",
          'slot["NMin"], slot["NMax"] = lo, hi' in s2d)
    check("BS 在车道求解后读入 _envelope_lane1.txt",
          '"_envelope_lane1.txt"' in s2d and "res.live_beam = _parse_lane_envelope" in s2d)
    check("过时注释已改成如实口径（本层没消费，不是内核没算）",
          "本层没消费" in comb and "不是「内核没算」" in comb)
    check("combination 注释点名 FORCE/N 与 _envelope_lane1.txt",
          "`FORCE/N`" in comb and "`_envelope_lane1.txt`" in comb)
    check("combination 注释点名 q.get(\"N\", 0.0) 键名不匹配",
          '`q.get("N", 0.0)`' in comb and "键名不匹配" in comb)
    check("combination 注释说明 has_axial_live 恒 False 是本层口径",
          "不代表内核有没有算活载轴力" in comb)
    check("beam_combinations 数值路径一字未改（仍取 q.get(\"N\")）",
          'q.get("N", 0.0)' in comb)
    check("beam_combinations 的 has_axial_live 判定一字未改",
          'any("N" in (v or {}) for v in live.values())' in comb)
    check("capacity_2d 不再写「没有活载轴力包络」",
          "没有活载轴力包络" not in read(ROOT / "capacity_2d.py"))
    check("strength_2d 不再写「无活载轴力包络」",
          "无活载轴力包络" not in read(ROOT / "strength_2d.py"))
    check("solve_2d 不再写「压弯 / 轴压在 2D 仍不做」",
          "压弯 / 轴压在 2D 仍不做" not in s2d)
    check("bridge_spec.l0 注释改为 capacity_axial_2d 必填",
          "capacity_axial_2d 必填" in read(ROOT / "bridge_spec.py"))
    # 数值行为逐位不变：组合结果仍是恒载单值 N
    res, sp = case_a()
    combos = CB.beam_combinations(res)
    ult = next(c for c in combos if c.spec.key == "ultimate")
    gg = CB.ULTIMATE.gamma_g
    check("组合层仍产出三种组合", len(combos) == 3)
    check("组合层 has_axial_live 仍为 False", ult.has_axial_live is False)
    check("组合层 has_live 仍为 True", ult.has_live is True)
    for key in sorted(ult.rows):
        check(f"组合层 {key} 的 N 仍是 γ_G·N_G（活载项 0）",
              near(ult.rows[key]["N"], gg * (-1.0e6)))
    for key in sorted(ult.rows):
        check(f"组合层 {key} 不含 NMin/NMax 键",
              "NMin" not in ult.rows[key] and "NMax" not in ult.rows[key])
    check("新层不写回 BeamCombo.rows",
          'rows[key]' not in read(ROOT / "capacity_axial_2d.py"))
    check("FE 三份文档零命中新模块名",
          all("capacity_axial_2d" not in read(FE / n)
              for n in ("CHANGELOG.md", "USER_MANUAL.md", "input_format_3d.md")))
    check("FE 源码零命中新模块名",
          "capacity_axial_2d" not in fem2d and "capacity_axial_2d" not in inf)


# ---------------------------------------------------------------- [3] 闭式 oracle

def oracle() -> None:
    print("\n[3] 闭式 oracle：同端配对 / 三链分派 / 与 pure function 对拍")
    ult_spec = CB.ULTIMATE
    gg, gq = ult_spec.gamma_g, ult_spec.gamma_q
    sp = spec_()
    sec, why = C2._capacity_section(sp.elem_types[0], sp.materials[0])
    check("配筋转换与 capacity_2d 共用且成功", sec is not None and why == "")
    fcd = __import__("code_material").concrete(sec.concrete_grade).fcd
    n_axial = fcd * sec.b * sec.h
    thr = CA.AXIAL_FRACTION * n_axial

    # --- 案例 A：M=0 → 轴心受压 / 归 flex
    res, sp = case_a()
    d = CA.evaluate(res, sp)
    check("案例 A status=ok", d["status"] == "ok")
    check("案例 A has_axial_live=True", d["has_axial_live"] is True)
    check("案例 A has_live=True", d["has_live"] is True)
    check("案例 A 8 个角点（两端各 2×2）",
          d["angle_count"] == 8, str(d["angle_count"]))
    check("角点数 = 已判定角点 + 归 flex 角点",
          d["angle_count"] == len(d["controls"]) + d["flex_angles"])
    exp_n = {}
    for lab, nq in (("NMin", -0.5e6), ("NMax", 0.5e6)):
        n = gg * (-1.0e6) + gq * nq
        exp_n[lab] = (n, max(-n, 0.0))
    check("闭式 Nc(NMin)=1.9e6", near(exp_n["NMin"][1], 1.9e6))
    check("闭式 Nc(NMax)=0.5e6", near(exp_n["NMax"][1], 0.5e6))
    check("闭式阈值小于 1.9e6 且大于 0.5e6（分派可分离）",
          exp_n["NMax"][1] < thr < exp_n["NMin"][1],
          f"thr={thr:.6g}")
    check("案例 A 轴压角点 4 个", d["axial_angles"] == 4)
    check("案例 A 压弯角点 0 个", d["eccentric_angles"] == 0)
    check("案例 A 归 flex 角点 4 个", d["flex_angles"] == 4)
    for c in d["controls"]:
        lab = c["angle"].split("·")[0]
        check(f"角点 {c['angle']} N 与闭式一致",
              near(c["N"], exp_n[lab][0]))
        check(f"角点 {c['angle']} Nc 与闭式一致",
              near(c["Nc"], exp_n[lab][1]))
        check(f"角点 {c['angle']} chain=axial", c["chain"] == "axial")
        r = SS.check_axial(sec, c["Nc"], gamma0=CA.GAMMA0, l0=sec.l0)
        check(f"角点 {c['angle']} util 与 check_axial 对拍",
              near(c["util"], r.util))
        check(f"角点 {c['angle']} e0=0", c["e0"] == 0.0)
        check(f"角点 {c['angle']} 同端 key=(1, i/j)",
              c["key"][0] == 1 and c["key"][1] in ("i", "j"))
    check("案例 A 只出轴压表与角点表、计算书",
          "capacity_axial_2d_axial_table" in d["tables"]
          and "capacity_axial_2d_angles_table" in d["tables"]
          and "capacity_axial_2d_sheet" in d["tables"])
    check("案例 A 不摆空的压弯表",
          "capacity_axial_2d_eccentric_table" not in d["tables"])
    tb = d["tables"]["capacity_axial_2d_axial_table"]
    check("轴压表契约 headers/rows/note/title",
          all(k in tb for k in ("headers", "rows", "note", "title")))
    check("轴压表行数 = 4", len(tb["rows"]) == 4)
    check("轴压表表头由 section_strength 给出",
          tb["headers"] == SS.axial_table([])["headers"])
    at = d["tables"]["capacity_axial_2d_angles_table"]
    check("角点表 8 列契约", len(at["headers"]) == 8)
    check("角点表含归 flex 行",
          any("归 flex 链" in str(row[-1]) for row in at["rows"]))
    check("角点表表注写明逐分量独立极值不冒充同一工况",
          "不保证来自同一" in at["note"])
    sheet = d["tables"]["capacity_axial_2d_sheet"]
    check("计算书表头由 section_strength 给出",
          sheet["headers"] == SS.capacity_calculation_sheet([])["headers"])
    check("计算书有实际行", len(sheet["rows"]) > 0)
    check("计算书按链取最不利 3 个（不放大冒充逐项复核）",
          "最不利的 3 个截面" in sheet["note"])
    check("表注含 γ0、5% / 1%、l0、RebarSpec2D",
          all(t in CA.TABLE_NOTE_EXTRA
              for t in ("γ₀=1.0", "5%", "1%", "l0", "RebarSpec2D")))

    # --- 案例 B：M 两侧非零 → 偏心受压 / 归 flex
    res2, sp2 = case_b()
    d2 = CA.evaluate(res2, sp2)
    check("案例 B status=ok", d2["status"] == "ok")
    check("案例 B 8 个角点（两端各 2×2）", d2["angle_count"] == 8)
    check("案例 B 压弯角点 4 个", d2["eccentric_angles"] == 4)
    check("案例 B 轴压角点 0 个", d2["axial_angles"] == 0)
    check("案例 B 归 flex 角点 4 个", d2["flex_angles"] == 4)
    exp_m = {}
    for lab, mq in (("MyMin", -1.0e5), ("MyMax", 3.0e5)):
        exp_m[lab] = gg * 2.0e5 + gq * mq
    check("闭式 M(MyMin)=1.0e5", near(exp_m["MyMin"], 1.0e5))
    check("闭式 M(MyMax)=6.6e5", near(exp_m["MyMax"], 6.6e5))
    for c in d2["controls"]:
        mlab = c["angle"].split("·")[1]
        check(f"案例 B 角点 {c['angle']} M 与闭式一致",
              near(c["M"], exp_m[mlab]))
        check(f"案例 B 角点 {c['angle']} chain=eccentric",
              c["chain"] == "eccentric")
        check(f"案例 B 角点 {c['angle']} e0 = Md/Nc",
              near(c["e0"], abs(c["M"]) / c["Nc"]))
        check(f"案例 B 角点 {c['angle']} e0 > 1% 截面高",
              c["e0"] >= CA.ECCENTRICITY_FRACTION * (sec.h / 1000.0))
        r = SS.check_eccentric(sec, c["Nc"], c["M"],
                               gamma0=CA.GAMMA0, l0=sec.l0)
        check(f"案例 B 角点 {c['angle']} util 与 check_eccentric 对拍",
              near(c["util"], r.util))
    check("案例 B 只出压弯表、不出空轴压表",
          "capacity_axial_2d_eccentric_table" in d2["tables"]
          and "capacity_axial_2d_axial_table" not in d2["tables"])
    check("压弯表表头由 section_strength 给出",
          d2["tables"]["capacity_axial_2d_eccentric_table"]["headers"]
          == SS.eccentric_table([])["headers"])

    # --- 同端配对：不跨端、不跨分量
    fin = {(1, "i"): {"N": -2.0e6, "My": 0.0, "Vz": 0.0},
           (1, "j"): {"N": 0.0, "My": 1.0e6, "Vz": 0.0}}
    liv = {(1, "i"): {"NMin": -0.5e6, "NMax": 0.5e6,
                      "MyMin": 0.0, "MyMax": 0.0,
                      "VzMin": 0.0, "VzMax": 0.0},
           (1, "j"): {"NMin": 0.0, "NMax": 0.0,
                      "MyMin": 0.0, "MyMax": 0.0,
                      "VzMin": 0.0, "VzMax": 0.0}}
    d3 = CA.evaluate(_res(fin, liv), spec_())
    check("跨端陷阱案例 status=ok", d3["status"] == "ok")
    for c in d3["controls"]:
        if c["key"][1] == "i":
            check(f"i 端角点 {c['angle']} 不拿 j 端弯矩",
                  abs(c["M"]) < 1e-12)
            check(f"i 端角点 {c['angle']} 保留自身轴压",
                  c["Nc"] > 1.0e6)
        else:
            check(f"j 端角点 {c['angle']} 不拿 i 端轴压",
                  c["Nc"] < 1e-12)
    check("角点表每行注明 单元 与 端",
          all("单元 1·" in str(row[0]) for row in
              d3["tables"]["capacity_axial_2d_angles_table"]["rows"]))

    # --- 负弯矩面
    fin4 = final_of(-1.5e6, -4.0e5)
    liv4 = live_of(-0.5e6, 0.5e6, -3.0e5, 1.0e5)
    d4 = CA.evaluate(_res(fin4, liv4), spec_())
    check("负弯矩案例 status=ok", d4["status"] == "ok")
    check("存在负弯矩角点",
          any(c["M"] < 0 for c in d4["controls"]))
    check("负弯矩角点备注留痕",
          any("负弯矩面" in str(row[-1]) for row in
              d4["tables"]["capacity_axial_2d_angles_table"]["rows"]))


# ---------------------------------------------------------------- [4] 拒绝负例

def refusals() -> None:
    print("\n[4] 拒绝与缺数据负例（不摆空表、不补零、中文状态）")
    cases: list[tuple[str, dict, dict, dict, str]] = []

    def mk(name, *, final=None, live=None, rebar=None, grade="C50",
           kind="concrete", truss=False, no_rebar=False, expected=""):
        f = dict(final if final is not None else final_of(-1.0e6, 0.0))
        l = dict(live if live is not None else live_of(-0.5e6, 0.5e6, 0.0, 0.0))
        rb = dict(rebar if rebar is not None else REBAR_L0)
        cases.append((name, f, l,
                      {"rebar": rb, "grade": grade, "kind": kind,
                       "truss": truss, "no_rebar": no_rebar}, expected))

    mk("缺 l0", rebar={**REBAR, "l0": 0.0}, expected="refused")
    mk("l0 为负", rebar={**REBAR, "l0": -1.0}, expected="refused")
    mk("缺活载轴力包络（live 空）", live={}, expected="no_axial_live")
    mk("活载只有 My/Vz 没有 NMin/NMax",
       live={(1, "i"): {"MyMin": 0.0, "MyMax": 0.0,
                        "VzMin": 0.0, "VzMax": 0.0}},
       expected="no_axial_live")
    mk("全无配筋契约", no_rebar=True, expected="no_rebar_section")
    mk("材料 grade 为空", grade="", expected="refused")
    mk("材料是钢截面（Q345）", grade="Q345", kind="steel",
       expected="refused")
    mk("非法混凝土牌号", grade="C999", expected="refused")
    mk("非法钢筋牌号", rebar={**REBAR_L0, "grade_rebar": "XYZ"},
       expected="refused")
    mk("轴向杆（无梁截面）", truss=True, expected="no_beam_section")
    mk("全部角点轴压不显著",
       final=final_of(0.0, 0.0), live=live_of(0.0, 0.0, 1.0e5, 2.0e5),
       expected="no_compression")
    mk("全零效应",
       final=final_of(0.0, 0.0), live=live_of(0.0, 0.0, 0.0, 0.0),
       expected="no_effect")
    mk("活载缺 My 两侧（不补零）",
       live={(1, "i"): {"NMin": -0.5e6, "NMax": 0.5e6},
             (1, "j"): {"NMin": -0.5e6, "NMax": 0.5e6}},
       expected="no_effect")

    for name, f, l, kw, expected in cases:
        rebar = kw.pop("rebar")
        no_rebar = kw.pop("no_rebar")
        sp = _spec(rebar=rebar, grade=kw["grade"], kind=kw["kind"],
                   truss=kw["truss"], no_rebar=no_rebar)
        d = CA.evaluate(_res(f, l), sp)
        check(f"{name} → {expected}", d["status"] == expected, d["status"])
        check(f"{name} 不摆空表", CA.tables(d) == {})
        check(f"{name} 中文说明非空", len(str(d["note"])) > 30)
        check(f"{name} 说明含边界口径", "P" in CA.status_text(d)
              or "不出表" in str(d["note"])
              or "拒绝" in str(d["note"]))
        check(f"{name} 不产生 controls", d["controls"] == [])
    check("拒绝负例覆盖 13 类", len(cases) == 13)

    # 断引用 / 无 ultimate / 无 final / spec=None
    sp = spec_()
    bad = _res({(99, "i"): {"N": -1.0e6, "My": 0.0, "Vz": 0.0}},
               {(99, "i"): {"NMin": -0.5e6, "NMax": 0.5e6,
                            "MyMin": 0.0, "MyMax": 0.0}})
    d = CA.evaluate(bad, sp)
    check("单元引用不存在 → refused", d["status"] == "refused")
    check("断引用中文点名单元号", any("99" in str(x) for x in d["reasons"]))
    r = _res(final_of(-1.0e6, 0.0), live_of(-0.5e6, 0.5e6, 0.0, 0.0))
    r.combinations = lambda: []                       # type: ignore[method-assign]
    d = CA.evaluate(r, sp)
    check("无基本组合 → unavailable", d["status"] == "unavailable")
    check("无基本组合不摆空表", CA.tables(d) == {})
    r2 = _res({}, {})
    d = CA.evaluate(r2, sp)
    check("无成桥态内力 → unavailable", d["status"] == "unavailable")
    d = CA.evaluate(_res(final_of(-1.0e6, 0.0),
                         live_of(-0.5e6, 0.5e6, 0.0, 0.0)), None)
    check("spec=None → unavailable", d["status"] == "unavailable")
    # 只给 NMin 不给 NMax：不补零，角点数减半
    l_one = {(1, "i"): {"NMin": -0.5e6, "MyMin": 0.0, "MyMax": 0.0},
             (1, "j"): {"NMin": -0.5e6, "MyMin": 0.0, "MyMax": 0.0}}
    d = CA.evaluate(_res(final_of(-1.0e6, 0.0), l_one), spec_())
    check("只有 NMin 时仍 status=ok（实测键存在才用）",
          d["status"] == "ok", d["status"])
    check("只有 NMin 不补出 NMax 角点", d["angle_count"] == 4)
    check("只有 NMin 的每个角点标签都不含 NMax",
          all("NMax" not in c["angle"] for c in d["controls"]))
    check("l0 缺失时 reasons 明写不静默取短柱",
          any("不静默取短柱" in x for x in
              CA.evaluate(_res(final_of(-1.0e6, 0.0),
                               live_of(-0.5e6, 0.5e6, 0.0, 0.0)),
                          _spec(rebar=REBAR))["reasons"]))
    check("status_text(ok) 含链计数与边界说明",
          "压弯" in CA.status_text(CA.evaluate(*case_a())))


# ---------------------------------------------------------------- [5] 零污染

def zero_pollution() -> None:
    print("\n[5] 默认路径逐位不变 / FE 仓零改动")
    r0 = Solve2DResult()
    check("默认 axial_capacity=not_requested",
          r0.axial_capacity.get("status") == "not_requested")
    check("默认 axial_capacity_tables 为空", r0.axial_capacity_tables() == {})
    check("默认 diagnostics 无压弯条目",
          not any("压弯" in str(x) for x in r0.diagnostics))
    check("默认 capacity 仍 not_requested（不改旧链）",
          r0.capacity.get("status") == "not_requested")
    sp = spec_()
    h0 = write_2d_html(r0, sp, OUT / "default.html").read_bytes()
    h0b = write_2d_html(r0, sp, OUT / "default2.html").read_bytes()
    check("默认 HTML 两次写出逐字节一致", h0 == h0b)
    text = h0.decode("utf-8")
    check("默认 HTML 不出压弯 / 轴压章",
          "2D 偏心受压（压弯）/ 轴心受压承载力" not in text)
    for title in ("轴心受压正截面承载力验算",
                  "偏心受压（压弯）正截面承载力验算",
                  "2D 压弯 / 轴压角点分派明细"):
        check(f"默认 HTML 不出表「{title}」", title not in text)
    rep0 = report_2d.build_2d_document(r0, sp)
    check("默认 Report 无 axial 表",
          not any(k.startswith("capacity_axial_2d") for k in rep0.tables))
    check("默认 Report 无压弯 h1 章",
          not any(b.get("type") == "h1" and "偏心受压" in str(b.get("text"))
                  for b in rep0.blocks))
    sig = inspect.signature(solve_2d)
    check("solve_2d 新参数默认 False",
          sig.parameters["axial_capacity"].default is False)
    check("ModelDoc.solve 新参数默认 False",
          inspect.signature(ModelDoc.solve).parameters[
              "axial_capacity_2d"].default is False)
    check("solve_2d 旧参数默认值未变",
          sig.parameters["capacity"].default is False
          and sig.parameters["strength"].default is False)
    for (kind, dim), builder in MD.BUILDERS.items():
        dat = ModelDoc(kind, dict(MD.DEFAULTS[builder]),
                       dimension=dim).to_dat()
        h = hashlib.sha256(dat.encode()).hexdigest()[:16]
        check(f"12 格默认 .dat 黄金哈希：{builder}",
              h == DEFAULT_DAT_SHA[builder], h)
    check("配筋契约仍不写进 .dat（带/不带逐位相同）",
          _spec(no_rebar=True).to_dat() == _spec(rebar=REBAR_L0).to_dat())
    check("FE 仓三份文档不含新章名",
          all("第十六次" not in read(FE / n)
              for n in ("CHANGELOG.md", "USER_MANUAL.md", "input_format_3d.md")))
    check("FE 源码不含 AXIAL_FRACTION / PlaneMct 之类新符号",
          "AXIAL_FRACTION" not in read(FE / "fem_2d.cpp"))
    check("默认 HTML 模板只新增一个同行占位符",
          read(ROOT / "export_html.py").count("{axial_html}") == 1)


# ---------------------------------------------------------------- [6] 接线

def wiring() -> None:
    print("\n[6] 双层 opt-in 接线与能力自述")
    expect = {
        "solve_2d.py": ("import capacity_axial_2d",
                        "res.axial_capacity = capacity_axial_2d.evaluate(res, spec)",
                        "capacity_axial_2d.status_text(res.axial_capacity)",
                        "def axial_capacity_tables(self)",
                        "axial_capacity: bool = False",
                        "if axial_capacity:"),
        "model_doc.py": ("axial_capacity_2d: bool = False",
                         "axial_capacity=bool(axial_capacity_2d)",
                         '"axialcapacity2d":'),
        "app.py": ('key="axialcapacity2d_opt"',
                   "axial_capacity_2d=_axial2d_on",
                   '"axialcapacity2d": _axial2d_on',
                   'bool(_s2d.get("axialcapacity2d", False)) != _axial2d_on'),
        "export_html.py": ("axial_html", "res.axial_capacity_tables()",
                           "{capacity_html}{axial_html}",
                           "AXIAL_BOUNDARY_NOTE"),
        "report_2d.py": ("import capacity_axial_2d", "AXIAL_BOUNDARY_NOTE",
                         "res.axial_capacity_tables()", "⑨′",
                         "2D 偏心受压（压弯）/ 轴心受压承载力"),
        "capacity_axial_2d.py": ("def evaluate(", "def tables(",
                                 "def status_text("),
    }
    for fname, tokens in expect.items():
        src = read(ROOT / fname)
        for token in tokens:
            check(f"{fname} 含 {token[:44]}", token in src)
    check("capability() 源码自述 axialcapacity2d",
          '"axialcapacity2d":' in read(ROOT / "model_doc.py"))
    check("capability() 仍保留 capacity2d 格",
          '"capacity2d":' in read(ROOT / "model_doc.py"))
    check("app 新开关默认 value=False",
          'key="axialcapacity2d_opt"' in read(ROOT / "app.py")
          and "value=False, key=\"axialcapacity2d_opt\"" in read(ROOT / "app.py"))
    check("app 新开关复用同一份配筋 JSON（不另建第二套）",
          "复用上方 2D 配筋 JSON" in read(ROOT / "app.py"))
    check("report_2d ⑨′ 章不重排后续章号",
          "⑩ 2D 平面索长闭合" in read(ROOT / "report_2d.py")
          and "⑪ 2D 平面索力优化" in read(ROOT / "report_2d.py")
          and "⑫ 诊断与能力边界" in read(ROOT / "report_2d.py"))
    check("AXIAL_BOUNDARY_NOTE 与口径层同源",
          report_2d.AXIAL_BOUNDARY_NOTE == CA.BOUNDARY_NOTE)


# ---------------------------------------------------------------- [7] 四出口同源

def exports() -> None:
    print("\n[7] 四出口同源 / 状态非 ok 不出表")
    res, sp = case_a()
    res.axial_capacity = CA.evaluate(res, sp)
    d = res.axial_capacity
    check("唯一取数口与口径层一致",
          res.axial_capacity_tables() == CA.tables(d))
    html = write_2d_html(res, sp, OUT / "on.html").read_text(encoding="utf-8")
    check("开启态 HTML 出压弯 / 轴压章",
          "2D 偏心受压（压弯）/ 轴心受压承载力" in html)
    for key, tb in d["tables"].items():
        check(f"HTML 出表「{tb['title']}」", tb["title"] in html)
        check(f"表 {key} 契约 headers/rows/note/title",
              all(k in tb for k in ("headers", "rows", "note", "title")))
        check(f"表 {key} 行数与列数自洽",
              all(len(row) == len(tb["headers"]) for row in tb["rows"]))
        check(f"表 {key} 表注带 2D 口径",
              "2D 压弯 / 轴压口径" in tb["note"])
    rep = report_2d.build_2d_document(res, sp)
    for key in d["tables"]:
        check(f"Report 收表 {key}", key in rep.tables)
        check(f"Report 表 {key} 与 HTML 同源",
              rep.tables[key]["rows"] == d["tables"][key]["rows"])
    check("Report 有 ⑨′ h1 章",
          any(b.get("type") == "h1"
              and "偏心受压" in str(b.get("text")) for b in rep.blocks))
    check("Report 章带 AXIAL_BOUNDARY_NOTE",
          any("逐分量独立极值" in str(b.get("text"))
              for b in rep.blocks if b.get("type") == "note"))
    # 拒绝态：HTML 只给中文说明，不摆空表
    bad, spb = case_a()
    spb = _spec(rebar=REBAR)                    # 缺 l0
    bad.axial_capacity = CA.evaluate(bad, spb)
    hbad = write_2d_html(bad, spb, OUT / "refused.html").read_text("utf-8")
    check("拒绝态 status", bad.axial_capacity["status"] == "refused")
    check("拒绝态 HTML 出中文说明", "本次未做 2D 压弯 / 轴压验算" in hbad)
    check("拒绝态 HTML 不摆空表",
          "轴心受压正截面承载力验算" not in hbad
          and "2D 压弯 / 轴压角点分派明细" not in hbad)
    repb = report_2d.build_2d_document(bad, spb)
    check("拒绝态 Report 无 axial 表",
          not any(k.startswith("capacity_axial_2d") for k in repb.tables))
    check("拒绝态 Report 仍出 ⑨′ 章（章节照出、内容只给原因）",
          any(b.get("type") == "h1" and "偏心受压" in str(b.get("text"))
              for b in repb.blocks))


# ---------------------------------------------------------------- [8] 两仓文档

def docs() -> None:
    print("\n[8] 两仓文档口径对拍（D 类准则 / 仍 P≠Y）")
    mat = read(ROOT / "能力边界矩阵.md")
    spec_doc = read(ROOT / "B类后续项接入规格.md")
    readme = read(ROOT / "README.md")
    manual = read(ROOT / "开发使用手册.md")
    usage = read(ROOT / "使用说明.md")
    head = "\n".join(mat.splitlines()[:110])
    check("矩阵 §2 该格仍 | P | Y |（不虚报）",
          "| 截面应力 / 承载力验算 | **P** | Y |" in mat
          or "| 截面应力 / 承载力验算 | P | Y |" in mat)
    check("矩阵 §2 备注不再写「无活载轴力包络」",
          "无活载轴力包络" not in head)
    check("矩阵 §4.2 仍 0 个 2D=N + 9 个 2D=P",
          "0 个 `2D=N` + 9 个 `2D=P`" in mat)
    check("矩阵 B2 剩余 0 项", "B2 剩余 0 项" in mat)
    check("矩阵登记第十六次闭环", "第十六次" in mat)
    check("矩阵登记 capacity_axial_2d.py", "capacity_axial_2d.py" in mat)
    check("矩阵写明仍 P≠Y", "P≠Y" in mat or "P ≠ Y" in mat)
    check("矩阵保留第十二次历史条目（D 类）",
          "第十二次" in mat and "压弯 / 轴压不做" in mat)
    for name, txt in (("B 类规格", spec_doc), ("README", readme),
                      ("开发手册", manual), ("使用说明", usage)):
        check(f"{name} 登记第十六次", "第十六次" in txt)
        check(f"{name} 登记 capacity_axial_2d.py",
              "capacity_axial_2d.py" in txt)
        check(f"{name} 登记 verify_capacity_axial_2d.py",
              "verify_capacity_axial_2d.py" in txt)
    check("B 类规格仍写 B2 剩余 0 项", "B2 剩余 0 项" in spec_doc)
    check("B 类规格仍如实列 B3/B4/B5 未实现",
          "B3" in spec_doc and "B4" in spec_doc and "B5" in spec_doc
          and "只立规格" in spec_doc)
    check("B 类规格仍写 MIDAS 数值对拍未闭环",
          "MIDAS 数值对拍" in spec_doc)
    check("README 仍写 P ≠ Y（不虚报）",
          "P ≠ Y" in readme or "P≠Y" in readme)
    check("FE CHANGELOG 无第十六次条目（本轮 FE 零改动）",
          "第十六次" not in read(FE / "CHANGELOG.md"))
    check("FE USER_MANUAL 无第十六次条目",
          "第十六次" not in read(FE / "USER_MANUAL.md"))
    check("FE input_format_3d 无第十六次条目",
          "第十六次" not in read(FE / "input_format_3d.md"))


# ---------------------------------------------------------------- [9] 不许假绿

def no_fake_green() -> None:
    print("\n[9] 汇总不许「假绿」")
    src = read(ROOT / "verify_capacity_axial_2d.py")
    check("check() 按布尔位记录",
          "PASS.append((name, bool(ok), detail))" in src)
    check("汇总按布尔位统计",
          "sum(1 for _, ok, _ in PASS if ok)" in src)
    check("只有全绿才返回 0", "n_pass == len(PASS)" in src)
    check("断言组数 9 组", src.count("    print(\"\\n[") == 9)
    check("没有常量 True 断言", "check(" not in src.split("def check(")[0])
    for tok in ("case_a()", "case_b()", "CA.evaluate", "CA.tables",
                "SS.check_axial", "SS.check_eccentric",
                "CB.beam_combinations", "write_2d_html",
                "report_2d.build_2d_document"):
        check(f"守卫确实调用 {tok}", tok in src)
    main_src = src.rsplit("def main() -> int:", 1)[1]
    check("main() 汇总行用 len(PASS) 而非写死总数",
          "{n_pass}/{len(PASS)} 通过" in main_src
          and "4524" not in main_src)


def main() -> int:
    print("=" * 78)
    print("2D 压弯 / 轴压半部守卫（第十六次，纯 BridgeStudio 侧 / FE 零改动）")
    print("=" * 78)
    OUT.mkdir(parents=True, exist_ok=True)
    contract()
    correction()
    oracle()
    refusals()
    zero_pollution()
    wiring()
    exports()
    docs()
    no_fake_green()
    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(PASS)} 通过")
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
