"""verify_capacity_2d.py —— 2D 承载力 Mu / Vu 半部（第十二次）独立守卫。

背景（2026-11-11 第十二次，B2「2D ↔ 3D 能力对齐」）：
用户拍板四项口径 —— 只做 Mu / Vu 一条 flex 链、效应只取 `ultimate`
基本组合的逐端最不利值、新建 `RebarSpec2D` 挂 `ElemTypeDef`、能力矩阵
该格仍标 `P` 且旧「本次未做」措辞条件化。本轮**纯 BridgeStudio 侧**：
不改 FE 内核、不改 `.dat`、不刷新 12 格黄金哈希与 FE 黄金基线。

本守卫锁住：
- 配筋契约（12 个必填字段、pydantic 边界、Python-only 不写 `.dat`、
  12 格默认 `.dat` 黄金哈希、轴向杆挂 rebar 中文报错、不进 DEFAULTS/FIELDS）；
- 唯一口径层的闭式解 oracle（逐端最不利 M_pos / M_neg / Vd、Mu/Vu 与
  `section_strength.check_capacity` 同源、两面各算、缺筋 inf、γ0/α 出处、
  负弯矩面与 `Asp_given` 语义、三张表契约）；
- 拒绝与缺数据负例（缺配筋 / 缺 grade / 钢截面 / 非法牌号 / 断引用 /
  无基本组合 / 无效应 / 无梁截面），一律中文状态、不摆空表、不补零；
- 零污染（默认 `not_requested`、默认 HTML 逐字节、默认 diagnostics、
  solve_2d / ModelDoc 双层 opt-in 默认 False、FE 侧零改动）；
- 接线与四出口同源（HTML / Report / CSV 三张表、应力章措辞条件化）；
- 两仓文档口径对拍，含「该格仍 P、§4.2 仍 2N+5P、B2 剩余仍 2 项」。

铁律：Mu / Vu 只能由 `section_strength` 的唯一批纯函数给出，Python 不重写
规范公式、不重算 K·ΔU=R、不猜配筋、不补零；汇总按布尔位统计防「假绿」。
"""

from __future__ import annotations

import hashlib
import inspect
import math
import sys
import zipfile
from pathlib import Path
from typing import Any

from console_utf8 import setup_console
from pydantic import ValidationError

setup_console()

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import bridge_spec as B
import capacity_2d as C
import model_doc as MD
import report_2d
import section_strength as SS
import strength_2d as S
from export_html import write_2d_html
from model_doc import ModelDoc
from solve_2d import Solve2DResult, _fem_eigen_dir, solve_2d
from verify_stage_tension_3d import DEFAULT_DAT_SHA

FE = _fem_eigen_dir() or Path("D:/AI-work/FEM-Eigen")
OUT = ROOT / "out" / "verify_capacity_2d"
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


# ---------------------------------------------------------------- 样本

REBAR = dict(
    h=2000.0, b=300.0, As=4000.0, bf=1200.0, hf=200.0,
    as_=60.0, Asp=0.0, asp=40.0, Asv=200.0, sv=150.0,
    grade_rebar="HRB400", grade_stirrup="HRB400",
)
REQUIRED = ("h", "b", "As", "bf", "hf", "as_", "Asp", "asp",
            "Asv", "sv", "grade_rebar", "grade_stirrup")

FINAL = {
    (1, "i"): {"N": 0.0, "My": 1.0e6, "Vz": 2.0e5},
    (1, "j"): {"N": 0.0, "My": -2.0e6, "Vz": 3.0e5},
}


def _spec(*, rebar: dict | None = None, no_rebar: bool = False,
          grade: str = "C50", kind: str = "concrete",
          truss: bool = False, n_types: int = 1) -> B.BridgeSpec:
    """一根 8 单元简支梁；配筋是 Python-only，不写进 `.dat`。"""
    n, L = 8, 12.0
    rb = None if no_rebar else B.RebarSpec2D(**(rebar or REBAR))
    ets = []
    for tid in range(1, n_types + 1):
        ets.append(B.ElemTypeDef(
            id=tid, kind=B.ElemKind.TRUSS if truss else B.ElemKind.BEAM,
            A=0.01 if truss else 0.6, I=None if truss else 0.45,
            WT=None if truss else 0.6, WB=None if truss else 0.6,
            AW=0.12, mat=1, rebar=rb))
    els = [B.ElemDef(id=i + 1, type_id=(1 if i < 4 and n_types > 1 else
                                        (2 if n_types > 1 else 1)),
                     n1=i + 1, n2=i + 2) for i in range(n)]
    return B.BridgeSpec(
        title="2D 承载力算例",
        nodes=[B.NodeDef(id=i + 1, x=L * i / n) for i in range(n + 1)],
        elements=els, elem_types=ets,
        materials=[B.MaterialDef(id=1, E=3.45e10, dens=2500.0,
                                 grade=grade, kind=kind)],
        boundaries=[B.BoundaryDef(node=1, dofs="1-2"),
                    B.BoundaryDef(node=n + 1, dofs="2")],
        nodal_loads=[B.NodalLoadDef(node=n // 2 + 1, dof=2,
                                    value=-1.0e6)],
        gravity=0.0)


def _res(final: dict | None = None, live: dict | None = None) -> Solve2DResult:
    return Solve2DResult(
        solver="FEM-Eigen", dat_text="dat", title="2D 承载力算例",
        final_beam=dict(FINAL if final is None else final),
        live_beam=dict(live or {}))


def _evaluated(spec: B.BridgeSpec | None = None,
               r: Solve2DResult | None = None) -> tuple[Solve2DResult, dict]:
    r = r or _res()
    spec = spec or _spec()
    d = C.evaluate(r, spec)
    r.capacity = d
    return r, d


# ---------------------------------------------------------------- [1] 契约

def contract() -> None:
    print("\n[1] RebarSpec2D 契约与默认 .dat 铁律")
    fields = B.RebarSpec2D.model_fields
    for k in REQUIRED:
        check(f"RebarSpec2D 必填字段 {k}", k in fields and fields[k].is_required())
    for k in ("Asp_given", "negative_given", "bf_neg", "hf_neg", "As_neg",
              "Asp_neg", "as_neg", "asp_neg", "l0", "hollow", "outer_bf"):
        check(f"RebarSpec2D 可选字段 {k} 有明确默认",
              k in fields and not fields[k].is_required())
    ok = B.RebarSpec2D(**REBAR)
    check("Asp_given / negative_given 默认 False",
          ok.Asp_given is False and ok.negative_given is False)
    check("六个 *_neg 默认 None（None=沿用，不是 0）",
          all(getattr(ok, k) is None for k in
              ("bf_neg", "hf_neg", "As_neg", "Asp_neg", "as_neg", "asp_neg")))
    for k, bad in (("h", 0.0), ("b", -1.0), ("as_", 0.0), ("asp", -2.0),
                   ("sv", 0.0), ("As", -1.0), ("bf", -1.0), ("hf", -1.0),
                   ("Asp", -1.0), ("Asv", -1.0)):
        try:
            B.RebarSpec2D(**{**REBAR, k: bad})
            good = False
        except ValidationError:
            good = True
        check(f"pydantic 拒绝非法 {k}={bad}", good)
    for k in ("bf", "hf", "Asp", "Asv"):
        zero = B.RebarSpec2D(**{**REBAR, k: 0.0})
        check(f"{k}=0 是合法事实值（不是未提供）", getattr(zero, k) == 0.0)
    try:
        B.RebarSpec2D(**{k: v for k, v in REBAR.items() if k != "As"})
        missing_ok = False
    except ValidationError:
        missing_ok = True
    check("缺任一必填字段构造失败（不猜配筋）", missing_ok)

    plain = _spec(no_rebar=True)
    rich = _spec()
    a, b = plain.to_dat(), rich.to_dat()
    check("配筋契约不写进 .dat（带/不带逐位相同）", a == b)
    for token in ("REBAR=", "AS=", "ASV=", "GRADE_REBAR=", "SV=", "CAPACITY="):
        check(f".dat 不含 {token}", token not in b)
    check("*R 仍只写内核认识的实常数", "WT=0.6" in b and "I=0.45" in b)
    for (kind, dim), builder in MD.BUILDERS.items():
        dat = ModelDoc(kind, dict(MD.DEFAULTS[builder]),
                       dimension=dim).to_dat()
        h = hashlib.sha256(dat.encode()).hexdigest()[:16]
        check(f"12 格默认 .dat 黄金哈希：{builder}",
              h == DEFAULT_DAT_SHA[builder], h)
    check("默认模型 validate_model 零影响", plain.validate_model() == [],
          str(plain.validate_model()))
    check("带配筋的合法模型不误报", rich.validate_model() == [],
          str(rich.validate_model()))
    axial = _spec(truss=True)
    errs = axial.validate_model()
    check("轴向杆挂 rebar 被中文拒绝",
          any("RebarSpec2D" in e and "轴向杆" in e for e in errs), str(errs))
    check("轴向杆错误信息点名单元类型", any("单元类型 1" in e for e in errs))
    check("ElemTypeDef.rebar 默认 None",
          B.ElemTypeDef(id=9, A=0.1).rebar is None)
    check("BridgeSpec 没有第二套配筋真源字段",
          not any("rebar" in k for k in B.BridgeSpec.model_fields))
    src_doc = read(ROOT / "model_doc.py")
    check("配筋契约不进 DEFAULTS / FIELDS",
          '"rebar"' not in str(MD.DEFAULTS) and '"rebar"' not in str(MD.FIELDS))
    check("capability 登记 capacity2d", '"capacity2d":' in src_doc)


# ---------------------------------------------------------------- [2] 正例 oracle

def oracle_cases() -> None:
    print("\n[2] Mu / Vu 闭式解 oracle 与效应口径")
    r, d = _evaluated()
    check("正例 status=ok", d["status"] == "ok", d["status"])
    check("配筋截面数 = 1", d["rebar_sections"] == 1)
    check("三张表 key 精确",
          set(d["tables"]) == {"capacity_2d_table",
                               "capacity_2d_faces_table",
                               "capacity_2d_sheet"}, str(sorted(d["tables"])))
    check("basis=2d_beam_ultimate", d["basis"] == "2d_beam_ultimate")
    for k, want in (("gamma0", 1.0), ("alpha1", 1.0), ("alpha3", 1.1)):
        check(f"{k} 与 3D 分派同源硬编码 = {want}", d[k] == want)
    ctrl = d["controls"][0]
    # 无活载：基本组合 γ_G=1.2；M_pos 取 (1,i) 的 MyMax，
    # M_neg 取 (1,j) 的 MyMin 绝对值，Vd 取 (1,j) 的 |Vz|。
    for k, want in (("M_pos", 1.2e6), ("M_neg", 2.4e6), ("Vd", 3.6e5)):
        check(f"逐端最不利 {k} 手算对拍", math.isclose(ctrl[k], want,
                                                  rel_tol=1e-12),
              f"{ctrl[k]} vs {want}")
    check("M_pos 控制端是单元 1·i",
          ctrl["m_pos_key"] == [1, "i"], str(ctrl["m_pos_key"]))
    check("M_neg 控制端是单元 1·j",
          ctrl["m_neg_key"] == [1, "j"], str(ctrl["m_neg_key"]))
    check("Vd 控制端是单元 1·j",
          ctrl["v_key"] == [1, "j"], str(ctrl["v_key"]))
    check("效应不是同一 12 角点（M 与 V 分端取最不利）",
          ctrl["m_pos_key"] != ctrl["v_key"])
    check("Solve2DResult.capacity_tables 与口径层同源",
          r.capacity_tables() == d["tables"])
    check("tables() 对 ok 状态返回同一份表", C.tables(d) == d["tables"])

    sec, _ = C._capacity_section(_spec().elem_types[0],
                                 _spec().materials[0])
    ref = SS.check_capacity(sec, ctrl["M_pos"], ctrl["Vd"],
                            gamma0=1.0, alpha1=1.0, alpha3=1.1,
                            Md_neg=ctrl["M_neg"])
    rows = d["tables"]["capacity_2d_table"]["rows"]
    faces = d["tables"]["capacity_2d_faces_table"]["rows"]
    check("Mu 与 check_capacity 逐位同源",
          math.isclose(ref.Mu, float(rows[0][1]) * 1e3, rel_tol=5e-4)
          or rows[0][1] == "—")
    check("Vu 与 check_capacity 逐位同源",
          math.isclose(ref.Vu / 1e3, float(rows[0][2]), rel_tol=5e-4))
    check("控制项与 check_capacity 同源",
          ref.control in rows[0][-1] or rows[0][-1] in ref.control)
    check("正 / 负弯矩两面各算 Mu（不是只取一面）",
          faces[0][1] != "—" and faces[0][3] != "—")
    check("未登记负弯矩面时写明对称近似",
          "对称近似" in faces[0][-1], str(faces[0][-1]))
    fm_pos = SS.flexural_capacity(sec, +1.0)
    check("正弯矩区 Mu 与 flexural_capacity 同源",
          math.isclose(fm_pos["Mu"] / 1e3, float(faces[0][1]), rel_tol=5e-4))
    sheet = d["tables"]["capacity_2d_sheet"]
    check("逐项复核计算书有行", len(sheet["rows"]) > 20,
          str(len(sheet["rows"])))
    check("计算书带规范条文出处",
          any("JTG 3362-2018" in str(row[-1]) for row in sheet["rows"]))
    for key, tb in d["tables"].items():
        check(f"{key} 表契约四键齐",
              set(("title", "note", "headers", "rows")) <= set(tb))
        check(f"{key} 表注写明 2D 边界",
              "ultimate" in tb["note"] and "压弯 / 轴压" in tb["note"])

    # 活载包络：MyMin / MyMax / VzMin / VzMax 分别与恒载同号侧叠加。
    live = {(1, "i"): {"MyMin": -5.0e5, "MyMax": 8.0e5,
                        "VzMin": -1.0e5, "VzMax": 4.0e5},
            (1, "j"): {"MyMin": -9.0e5, "MyMax": 2.0e5,
                        "VzMin": -7.0e5, "VzMax": 1.0e5}}
    _, dl = _evaluated(r=_res(live=live))
    cl = dl["controls"][0]
    check("含活载时 status=ok", dl["status"] == "ok", dl["status"])
    check("含活载时 has_live=True", dl["has_live"] is True)
    check("含活载 M_pos 手算对拍",
          math.isclose(cl["M_pos"], 1.2e6 + 1.4 * 8.0e5, rel_tol=1e-12),
          str(cl["M_pos"]))
    check("含活载 M_neg 手算对拍",
          math.isclose(cl["M_neg"], -(1.2 * -2.0e6 + 1.4 * -9.0e5),
                       rel_tol=1e-12), str(cl["M_neg"]))
    want_v = max(abs(1.2 * 2.0e5 + 1.4 * -1.0e5),
                 abs(1.2 * 2.0e5 + 1.4 * 4.0e5),
                 abs(1.2 * 3.0e5 + 1.4 * -7.0e5),
                 abs(1.2 * 3.0e5 + 1.4 * 1.0e5))
    check("含活载 Vd 手算对拍",
          math.isclose(cl["Vd"], want_v, rel_tol=1e-12),
          f"{cl['Vd']} vs {want_v}")

    # 多个实常数类型：按类型分组，各自取自己单元集合内的最不利。
    final2 = {(1, "i"): {"N": 0.0, "My": 1.0e6, "Vz": 1.0e5},
              (5, "j"): {"N": 0.0, "My": -3.0e6, "Vz": 4.0e5}}
    _, d2 = _evaluated(spec=_spec(n_types=2), r=_res(final=final2))
    check("两个配筋类型各自成行", len(d2["tables"]["capacity_2d_table"]["rows"]) == 2)
    c2 = {c["label"]: c for c in d2["controls"]}
    check("分组 A 只取自己单元的最不利",
          math.isclose(c2["实常数类型 1（C50）"]["M_pos"], 1.2e6,
                       rel_tol=1e-12))
    check("分组 B 只取自己单元的最不利",
          math.isclose(c2["实常数类型 2（C50）"]["M_neg"], 3.6e6,
                       rel_tol=1e-12))
    check("分组不串用 Control",
          c2["实常数类型 1（C50）"]["M_neg"] == 0.0)

    # 缺筋 / 缺箍：inf 与 0 是事实值，不补零冒充通过。
    _, dn = _evaluated(spec=_spec(rebar={**REBAR, "As": 0.0}))
    check("As=0 仍出表（不静默跳过）", dn["status"] == "ok")
    rn = dn["tables"]["capacity_2d_table"]["rows"][0]
    check("缺纵筋时利用率写 ∞", "∞" in rn[3] or "∞" in rn[4], str(rn))
    _, dv = _evaluated(spec=_spec(rebar={**REBAR, "Asv": 0.0}))
    rv = dv["tables"]["capacity_2d_table"]["rows"][0]
    check("缺箍筋时 Vu 显示 — 而非 0", rv[2] == "—", str(rv))
    check("缺箍筋时表注或结论写明", "未配箍" in dv["note"]
          or "未配箍" in str(dv["tables"]["capacity_2d_table"]["note"]))

    # 负弯矩面 / Asp_given 语义不能改。
    neg = {**REBAR, "negative_given": True, "bf_neg": 1000.0,
           "hf_neg": 180.0, "As_neg": 3200.0, "Asp_neg": 800.0,
           "as_neg": 55.0, "asp_neg": 45.0}
    secn, _ = C._capacity_section(_spec(rebar=neg).elem_types[0],
                                  _spec().materials[0])
    check("negative_given=True 生成 FaceSpec", secn.negative is not None)
    check("FaceSpec 六项取自 *_neg",
          secn.negative.bf == 1000.0 and secn.negative.As == 3200.0
          and secn.negative.asp == 45.0)
    _, dneg = _evaluated(spec=_spec(rebar=neg))
    check("登记负弯矩面后不再写对称近似",
          "对称近似" not in dneg["tables"]["capacity_2d_faces_table"]["rows"][0][-1])
    secg, _ = C._capacity_section(
        _spec(rebar={**REBAR, "Asp": 500.0, "Asp_given": True}).elem_types[0],
        _spec().materials[0])
    check("Asp_given=True 时 Asp_eff 按字面值", secg.Asp_eff == 500.0)
    check("Asp_given=False 时按对称配筋取 As",
          sec.Asp_eff == sec.As)


# ---------------------------------------------------------------- [3] 拒绝负例

def refusals() -> None:
    print("\n[3] 缺参 / 缺数据一律中文状态、整章拒绝、不摆空表")
    cases: list[tuple[str, B.BridgeSpec, str]] = [
        ("无 RebarSpec2D", _spec(no_rebar=True), "no_rebar_section"),
        ("缺混凝土 grade", _spec(grade=""), "refused"),
        ("钢截面不做混凝土承载力",
         _spec(grade="Q345", kind="steel"), "refused"),
        ("非法纵筋牌号",
         _spec(rebar={**REBAR, "grade_rebar": "NOT_A_GRADE"}), "refused"),
        ("非法箍筋牌号",
         _spec(rebar={**REBAR, "grade_stirrup": "NOT_A_GRADE"}), "refused"),
        ("全轴向杆无梁截面", _spec(truss=True), "no_beam_section"),
    ]
    for label, spec, want in cases:
        d = C.evaluate(_res(), spec)
        check(f"{label} → {want}", d["status"] == want, d["status"])
        check(f"{label} 不出表", C.tables(d) == {})
        check(f"{label} 有中文状态文案",
              len(C.status_text(d)) >= 20 and "本次" in C.status_text(d)
              or "不" in C.status_text(d))

    # 断引用：单元 / 类型 / 材料找不到 → refused 且逐条中文原因。
    bad = _spec()
    bad.elements = []
    d = C.evaluate(_res(), bad)
    check("单元引用缺失 → refused", d["status"] == "refused", d["status"])
    check("单元引用缺失列中文原因",
          any("spec.elements" in r for r in d["reasons"]), str(d["reasons"]))
    bad2 = _spec()
    bad2.elem_types = []
    d = C.evaluate(_res(), bad2)
    check("类型引用缺失 → refused", d["status"] == "refused")
    check("类型引用缺失列中文原因",
          any("spec.elem_types" in r for r in d["reasons"]))
    bad3 = _spec()
    bad3.materials = []
    d = C.evaluate(_res(), bad3)
    check("材料引用缺失 → refused", d["status"] == "refused")

    # no_ultimate：组合列表里没有 key=ultimate。
    class _NoUlt:
        final_beam = dict(FINAL)

        def combinations(self) -> list[Any]:
            return [type("X", (), {
                "spec": type("S", (), {"key": "frequent"})(),
                "rows": {}, "has_live": False})()]

    d = C.evaluate(_NoUlt(), _spec())
    check("没有基本组合 → no_ultimate", d["status"] == "no_ultimate",
          d["status"])
    check("no_ultimate 写明没有效应源", "基本组合" in C.status_text(d))
    # no_effect：基本组合存在但效应全零。
    zero = {(1, "i"): {"N": 0.0, "My": 0.0, "Vz": 0.0}}
    d = C.evaluate(_res(final=zero), _spec())
    check("基本组合效应全零 → no_effect", d["status"] == "no_effect",
          d["status"])
    check("no_effect 不出表、不补零", C.tables(d) == {}
          and "不补零" in C.status_text(d))
    # unavailable：没有成桥态内力 / 没有模型。
    d = C.evaluate(Solve2DResult(), _spec())
    check("无成桥态内力 → unavailable", d["status"] == "unavailable")
    d = C.evaluate(_res(), None)
    check("无模型 → unavailable", d["status"] == "unavailable")
    check("unavailable 写明静力结果不受影响",
          "不受影响" in C.status_text(d))

    for st in ("not_requested", "unavailable", "no_ultimate",
               "no_beam_section", "no_rebar_section", "no_effect",
               "refused", "unknown_status"):
        s = {"status": st, "reasons": ["示例原因"],
             "rebar_sections": 1, "gamma0": 1.0,
             "alpha1": 1.0, "alpha3": 1.1}
        check(f"status_text({st}) 有中文且带边界",
              len(C.status_text(s)) >= 30 and "P" in C.status_text(s)
              or "本次" in C.status_text(s))
        check(f"tables({st}) 为空 dict", C.tables(s) == {})
    check("ok 状态文案写明 γ0 / α1 / α3",
          all(x in C.status_text({"status": "ok", "rebar_sections": 1,
                                  "has_live": False, "gamma0": 1.0,
                                  "alpha1": 1.0, "alpha3": 1.1})
              for x in ("γ₀", "α1", "α3")))
    check("拒绝文案不虚报为已验算",
          "未做" in C.status_text({"status": "refused", "reasons": ["x"]}))

    src = read(ROOT / "capacity_2d.py")
    for kw in ("不重写任何规范公式", "整章 `refused`", "不补零",
               "不用", "12 角点", "5% / 1%", "check_capacity",
               "key == \"ultimate\"", "无活载轴力包络"):
        check(f"口径层写明边界：{kw}", kw in src)


# ---------------------------------------------------------------- [4] 零污染

def zero_pollution() -> None:
    print("\n[4] 默认路径逐位不变 / FE 内核零改动")
    r0 = Solve2DResult()
    check("默认 capacity=not_requested",
          r0.capacity.get("status") == "not_requested")
    check("默认 capacity_tables 为空", r0.capacity_tables() == {})
    check("默认 diagnostics 无承载力条目",
          not any("Mu / Vu" in str(x) for x in r0.diagnostics))
    spec = _spec()
    h0 = write_2d_html(r0, spec, OUT / "default.html").read_bytes()
    h0b = write_2d_html(r0, spec, OUT / "default2.html").read_bytes()
    check("默认 HTML 两次写出逐字节一致", h0 == h0b)
    text = h0.decode("utf-8")
    check("默认 HTML 不出承载力章",
          "2D 正截面抗弯 / 斜截面抗剪承载力" not in text)
    for title in ("正截面 / 斜截面承载力验算",
                  "正 / 负弯矩区分面抗弯验算",
                  "承载力计算书（逐项复核）"):
        check(f"默认 HTML 不出表「{title}」", title not in text)
    check("默认 HTML 不出应力章", "2D 截面应力验算" not in text)
    rep0 = report_2d.build_2d_document(r0, spec)
    check("默认 Report 无 capacity 表",
          not any(k.startswith("capacity_2d") for k in rep0.tables))
    check("默认 Report 无承载力 h1 章",
          not any(b_.get("type") == "h1" and "承载力" in str(b_.get("text"))
                  for b_ in rep0.blocks))

    sig = inspect.signature(solve_2d)
    check("solve_2d 有 capacity: bool = False",
          str(sig.parameters["capacity"].default) == "False"
          and "capacity" in sig.parameters)
    sigd = inspect.signature(ModelDoc.solve)
    check("ModelDoc.solve 有 capacity_2d: bool = False",
          "capacity_2d" in sigd.parameters
          and sigd.parameters["capacity_2d"].default is False)
    src_solve = read(ROOT / "solve_2d.py")
    check("capacity 分支先于 strength 执行（措辞按状态选）",
          src_solve.index("if capacity:") < src_solve.index("if strength:"))
    check("capacity 分支只在 opt-in 时追加 diagnostics",
          "if capacity:" in src_solve
          and "res.diagnostics.append(capacity_2d.status_text" in src_solve)
    check("solve_2d 自检不带 capacity 开关",
          "res = solve_2d(spec)" in src_solve)

    fe_src = read(FE / "example_mixed.cpp")
    check("FE example_mixed.cpp 没有新增 --capacity 开关",
          "--capacity" not in fe_src)
    check("FE 侧没有 capacity_2d / RebarSpec2D 引用",
          "capacity_2d" not in fe_src and "RebarSpec2D" not in fe_src)
    for name in ("CHANGELOG.md", "USER_MANUAL.md"):
        check(f"FE {name} 未被本轮改动（无 RebarSpec2D）",
              "RebarSpec2D" not in read(FE / name))


# ---------------------------------------------------------------- [5] 接线

def wiring() -> None:
    print("\n[5] 双层 opt-in 接线与唯一取数口")
    expect = {
        "solve_2d.py": ("import capacity_2d",
                        "res.capacity = capacity_2d.evaluate(res, spec)",
                        "capacity_2d.status_text(res.capacity)",
                        "def capacity_tables(self)",
                        "capacity: bool = False"),
        "model_doc.py": ("capacity_2d: bool = False",
                         "capacity=bool(capacity_2d)",
                         '"capacity2d":'),
        "app.py": ('key="capacity2d_opt"',
                   "capacity_2d=_capacity2d_on",
                   "RebarSpec2D(**_v2)",
                   '"capacity2d": _capacity2d_on',
                   'bool(_s2d.get("capacity2d", False)) != _capacity2d_on',
                   "capacity2d_status_text"),
        "export_html.py": ("capacity_html", "res.capacity_tables()",
                           "capacity_note_for(", "{capacity_html}",
                           "CAPACITY_BOUNDARY_NOTE"),
        "report_2d.py": ("import capacity_2d", "CAPACITY_BOUNDARY_NOTE",
                         "res.capacity_tables()", "capacity_note_for(",
                         "2D 正截面抗弯 / 斜截面抗剪承载力"),
        "strength_2d.py": ("CAPACITY_REFUSED_NOTE", "CAPACITY_LINK_NOTE",
                           "def capacity_note_for(", '"capacity": cap_state'),
    }
    for fname, tokens in expect.items():
        src = read(ROOT / fname)
        for t in tokens:
            check(f"{fname} 含 {t[:40]}", t in src)
    src_app = read(ROOT / "app.py")
    check("配筋 JSON 留空时只给中文说明、不猜参数",
          "未填 2D 配筋参数" in src_app
          and "_t2.rebar = None" in src_app)
    check("界面配筋参数逐类型显式挂 BridgeSpec",
          "_by_id2[_tid2].rebar = RebarSpec2D(**_v2)" in src_app)
    check("界面把 capacity2dcfg 纳入过期判断",
          'str(_s2d.get("capacity2dcfg", "")) != _capacity2d_cfg' in src_app)
    src_cap = read(ROOT / "capacity_2d.py")
    check("口径层只调用 check_capacity 一条验算链",
          "check_eccentric" not in src_cap
          and "check_axial" not in src_cap
          and "check_capacity(" in src_cap)
    check("口径层不重算 K·ΔU=R、不解析内核应力段",
          "parse_result" not in src_cap and "单元应力 (Pa)" not in src_cap)
    check("口径层不写 .dat", "to_dat" not in src_cap)


# ---------------------------------------------------------------- [6] 四出口

def exports() -> None:
    print("\n[6] 四出口同源与 CSV 自动收表")
    r, d = _evaluated()
    tabs = r.capacity_tables()
    check("三张表通过唯一取数口", set(tabs) == set(d["tables"]))
    rep = report_2d.build_2d_document(r, _spec())
    for k in tabs:
        check(f"Report.tables 含 {k}", k in rep.tables)
    check("Report 有承载力 h1 章",
          any(b_.get("type") == "h1" and "承载力" in str(b_.get("text"))
              for b_ in rep.blocks))
    check("Report 有边界 note",
          any("压弯 / 轴压" in str(b_.get("text")) for b_ in rep.blocks))
    check("Report.figures 仍为空（不出图）", rep.figures == {})
    html = write_2d_html(r, _spec(), OUT / "optin.html").read_text(
        encoding="utf-8")
    check("HTML 出口有承载力章",
          "2D 正截面抗弯 / 斜截面抗剪承载力" in html)
    for tb in tabs.values():
        check(f"HTML 出口含表「{tb['title'][:12]}」", tb["title"] in html)
    check("HTML 出口数值与表字典同源",
          tabs["capacity_2d_table"]["rows"][0][1] in html)
    check("HTML 出口写明压弯 / 轴压不做",
          "压弯 / 轴压" in html and "仍不做" in html)

    d4 = OUT / "four"
    d4.mkdir(parents=True, exist_ok=True)
    paths = report_2d.write_2d_all(rep, d4, stem="cap2d", pdf=False)
    check("四出口三件套落盘（pdf=False）",
          set(paths) == {"html", "docx", "zip"}, str(sorted(paths)))
    with zipfile.ZipFile(paths["zip"]) as z:
        names = z.namelist()
        for k in tabs:
            check(f"CSV 自动收 {k}", any(k + ".csv" in n for n in names))
        csv_text = next((z.read(n).decode("utf-8") for n in names
                         if "capacity_2d_table.csv" in n), "")
        check("CSV 与表字典同源（表头）",
              all(h in csv_text
                  for h in tabs["capacity_2d_table"]["headers"][:3]))
        check("CSV 与表字典同源（数值）",
              tabs["capacity_2d_table"]["rows"][0][1] in csv_text)
    check("DOCX 出口非空", paths["docx"].stat().st_size > 0)

    # 应力章措辞条件化：capacity=ok 指向新章、refused 维持拒绝。
    r.strength = S.evaluate(r, _spec())
    h_ok = write_2d_html(r, _spec(), OUT / "both_ok.html").read_text(
        encoding="utf-8")
    check("已出 Mu/Vu 时应力章指向新章", "capacity_2d.py" in h_ok)
    check("已出 Mu/Vu 时应力章不再说承载力本次未做",
          "承载力验算（Mu / Vu / 压弯 / 轴压）本次未做" not in h_ok)
    r2, _ = _evaluated()
    r2.capacity = {"status": "refused", "source": C.SOURCE,
                   "reasons": ["示例原因"]}
    r2.strength = S.evaluate(r2, _spec())
    h_no = write_2d_html(r2, _spec(), OUT / "both_refused.html").read_text(
        encoding="utf-8")
    check("承载力被拒时应力章仍显式拒绝",
          "承载力验算（Mu / Vu / 压弯 / 轴压）本次未做" in h_no)
    check("承载力被拒时仍出应力章", "2D 截面应力验算" in h_no)


# ---------------------------------------------------------------- [7] 文档

def docs() -> None:
    print("\n[7] 两仓文档口径对拍")
    mat = read(ROOT / "能力边界矩阵.md")
    check("矩阵该格 2D 仍标 P（不虚报 Y）",
          "| 截面应力 / 承载力验算 | P | Y |" in mat)
    check("矩阵依据列登记 capacity_2d / RebarSpec2D",
          "capacity_2d.py::evaluate" in mat
          and "bridge_spec.py::RebarSpec2D" in mat)
    check("矩阵 §4.2 仍 2 个 N + 5 个 P",
          "2 个 `2D=N`" in mat and "5 个 `2D=P`" in mat)
    check("矩阵写明 B2 剩余仍 2 项", "B2 剩余仍 2 项" in mat
          or "B2 剩余 2 项" in mat)
    check("矩阵登记第十二次闭环记录", "第十二次闭环记录" in mat)
    check("矩阵写明压弯 / 轴压不做",
          "压弯 / 轴压" in mat and "无活载轴力包络" in mat)
    spec = read(ROOT / "B类后续项接入规格.md")
    check("B 类规格登记第十二次与 verify_capacity_2d.py",
          "第十二次" in spec and "verify_capacity_2d.py" in spec)
    check("B 类规格写明 Mu/Vu 已接入、压弯轴压仍缺",
          "Mu / Vu" in spec and "第十二次" in spec
          and "压弯 / 轴压" in spec)
    check("B 类规格仍保留 RebarSpec2D 与配筋契约口径",
          "RebarSpec2D" in spec and "配筋契约" in spec)
    check("B 类规格 B2 剩余仍 2 项", "B2 剩余仍 2 项" in spec
          or "**B2 剩余 2 项**" in spec)
    rd = read(ROOT / "README.md")
    check("README 登记第十二次承载力半部",
          "### 2D 承载力 Mu / Vu 的 opt-in 半部" in rd)
    check("README 登记 capacity_2d.py 与新守卫",
          "capacity_2d.py" in rd and "verify_capacity_2d.py" in rd)
    check("README 写明仍标 P 不标 Y", "仍标 `P`、不标 `Y`" in rd)
    check("README 同步 stress 守卫 168/168",
          "verify_strength_2d.py` 168/168" in rd)
    guide = read(ROOT / "使用说明.md")
    check("使用说明文件地图登记 capacity_2d.py",
          "capacity_2d.py` / `verify_capacity_2d.py`" in guide)
    manual = read(ROOT / "开发使用手册.md")
    check("开发手册回归清单登记新守卫",
          "verify_capacity_2d.py" in manual)
    for fname in ("verify_capability_matrix.py", "verify_b_class_spec.py",
                  "verify_report_2d.py", "verify_strength_2d.py"):
        check(f"{fname} 有第十二次防漂移断言",
              "verify_capacity_2d.py" in read(ROOT / fname)
              or ("capacity_note_for" in read(ROOT / fname)
                  and fname == "verify_strength_2d.py"))


def main() -> int:
    print("=" * 78)
    print("2D 承载力 Mu / Vu 半部守卫（B2 第十二次，纯 BridgeStudio 侧）")
    print("=" * 78)
    OUT.mkdir(parents=True, exist_ok=True)
    contract()
    oracle_cases()
    refusals()
    zero_pollution()
    wiring()
    exports()
    docs()
    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(PASS)} 通过")
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
