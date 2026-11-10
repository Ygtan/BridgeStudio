"""verify_strength_2d.py —— 2D 截面应力验算（P 口径）接入链路守卫。

`能力边界矩阵.md` §2 把「截面应力 / 承载力验算」的 2D 格从 `N` 改成 `P`：
本轮接通的是**应力**这一半（成桥态内力 + JTG D60 组合 → `section_strength.check`），
**承载力 Mu/Vu 因 2D 无配筋契约一律显式拒绝**。守卫锁住这个不对称，防止
下一次有人把 `P` 悄悄涂成 `Y`。

离线：混凝土 / 钢两套闭式解 oracle、γ₀ 口径、limit、八类缺参拒绝负例、
状态文案、表契约、默认路径源码级断言、新字段不写进 `.dat` 的逐位对拍。
live（`example_mixed.exe` 存在时）：默认与开启对照、`.dat` 逐位不变、
HTML 计算书同源章节、缺 AW 时整章中文拒绝。

铁律：不解析内核「单元应力 (Pa)」段（第二套 σ）、不静默兜底（缺参整章拒绝）、
结论与计算书同源（HTML / 界面都读 `res.strength_tables()`）、汇总按布尔位统计。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

from console_utf8 import setup_console

setup_console()

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import section_strength
import strength_2d
from bridge_spec import (BoundaryDef, BridgeSpec, ElemDef, ElemKind,
                         ElemTypeDef, MaterialDef, NodeDef, NodalLoadDef)
from export_html import write_2d_html
from solve_2d import Solve2DResult, solve_2d, solve_2d_available

OUT = ROOT / "out" / "verify_strength_2d"
PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


# ---------------------------------------------------------------- 构造样本


def _spec(*, grade: str = "C50", kind: str = "concrete", AW: float = 0.12,
          WT: float = 0.6, WB: float = 0.6, t_plate: float = 0.0,
          truss: bool = False) -> BridgeSpec:
    """一根 8 单元简支梁。验算参数只存在于 Python 侧，不写进 `.dat`。"""
    n, L = 8, 12.0
    et = ElemTypeDef(
        id=1, kind=ElemKind.TRUSS if truss else ElemKind.BEAM,
        A=0.01 if truss else 0.6, I=None if truss else 0.45,
        WT=None if truss else WT, WB=None if truss else WB,
        AW=AW, t_plate=t_plate)
    return BridgeSpec(
        title="2D 截面应力算例",
        nodes=[NodeDef(id=i + 1, x=L * i / n) for i in range(n + 1)],
        elements=[ElemDef(id=i + 1, type_id=1, n1=i + 1, n2=i + 2)
                  for i in range(n)],
        elem_types=[et],
        materials=[MaterialDef(id=1, E=3.45e10, dens=2500.0,
                               grade=grade, kind=kind)],
        boundaries=[BoundaryDef(node=1, dofs="1-2"),
                    BoundaryDef(node=n + 1, dofs="2")],
        nodal_loads=[NodalLoadDef(node=n // 2 + 1, dof=2, value=-1.0e6)],
        gravity=0.0,
    )


def _manual_res() -> Solve2DResult:
    """只填一个截面的成桥态内力，便于闭式解逐位对拍。

    N=-2.0 MN、My=+1.5 MN·m、Vz=-0.30 MN；无活载 → 三种组合里
    基本组合 γ_G=1.2 控制：N=-2.4e6、My=1.8e6、Vz=-3.6e5。
    A=0.6、WT=WB=0.6、AW=0.12、γ₀=1.0 ⇒
        σ_top = -4.0 - 3.0 = -7.0 MPa
        σ_bot = -4.0 + 3.0 = -1.0 MPa
        τ     = 3.0 MPa
    """
    r = Solve2DResult()
    r.final_beam = {(1, "i"): {"N": -2.0e6, "My": 1.5e6, "Vz": -3.0e5}}
    return r


# ---------------------------------------------------------------- 离线 oracle


def offline_concrete_oracle() -> None:
    spec = _spec()
    s = strength_2d.evaluate(_manual_res(), spec)
    check("混凝土样本状态 ok", s["status"] == "ok", s["status"])
    check("混凝土样本出且只出 stress_table",
          list(s["tables"]) == ["stress_table"], str(list(s["tables"])))
    check("不出承载力表（不拿应力冒充 Mu/Vu）",
          "capacity_table" not in s["tables"] and "Mu" not in str(s["tables"].keys()))
    t = s["tables"]["stress_table"]
    check("表契约四键齐全",
          all(k in t for k in ("title", "headers", "rows", "note")))
    check("表头与 section_strength.result_table 同源",
          t["headers"] == section_strength.result_table([])["headers"])
    check("标题写明 2D 与组合最不利角点",
          "2D" in t["title"] and "组合最不利角点" in t["title"])
    row = t["rows"][0]
    check("σ_top 对拍闭式解 -7.0 MPa", row[1] == "-7.0", str(row[1]))
    check("σ_bot 对拍闭式解 -1.0 MPa", row[2] == "-1.0", str(row[2]))
    check("τ 对拍闭式解 3.0 MPa", row[3] == "3.0", str(row[3]))
    check("混凝土不做折算应力（0.0）", row[4] == "0.0", str(row[4]))
    check("利用率 = 7.0 / (0.5·32.4) = 0.432",
          row[5] == "0.432", str(row[5]))
    check("控制项写明受压区混凝土压应力",
          "受压区混凝土" in row[6], row[6])
    check("控制截面注明组合与角点",
          "基本组合" in row[0] and "My" in row[0] and "Vz" in row[0], row[0])
    check("枚举 12 个角点（3 组合 × My 两侧 × Vz 两侧）",
          s["cases"] == 12, str(s["cases"]))
    check("has_live=False 如实写明",
          s["has_live"] is False and "只有恒载项" in s["note"])
    for kw in ("不解析内核", "轴力 N 只有单值、仅含恒载", "γ₀=1",
               "JTG 3362-2018 §7.1.5", "量级判据"):
        check(f"表注写明边界：{kw}", kw in t["note"])
    check("表注写明承载力本次未做（P 口径）",
          "承载力验算（Mu / Vu / 压弯 / 轴压）本次未做" in t["note"]
          and "不编一个配筋出来" in t["note"])
    check("表注写明能力矩阵只能标 P", "只能标 P" in t["note"])


def offline_steel_oracle() -> None:
    spec = _spec(grade="Q345", kind="steel", t_plate=20.0)
    s = strength_2d.evaluate(_manual_res(), spec)
    check("钢材样本状态 ok", s["status"] == "ok", s["status"])
    row = s["tables"]["stress_table"]["rows"][0]
    # Q345、t=20 mm → JTG D64-2015 表 3.2.1 第二档 fd=270、fvd=155。
    check("钢材 σ_top 同为 -7.0（截面几何不变）",
          row[1] == "-7.0", str(row[1]))
    check("钢材 τ 同为 3.0", row[3] == "3.0", str(row[3]))
    check("折算应力 √(7²+3·3²)=8.7 MPa", row[4] == "8.7", str(row[4]))
    check("利用率 = hypot(7/270, 3/155) = 0.032",
          row[5] == "0.032", str(row[5]))
    check("控制项为上缘折算应力（D64 §5.3.1 第 4 款）",
          row[6] == "上缘折算应力", row[6])
    check("表注写明钢材三款验算",
          "JTG D64-2015 §5.3.1" in s["tables"]["stress_table"]["note"])


def offline_gamma_limit() -> None:
    spec = _spec()
    s = strength_2d.evaluate(_manual_res(), spec, gamma0=1.1)
    row = s["tables"]["stress_table"]["rows"][0]
    check("γ₀=1.1 时 σ_top 按效应侧放大到 -7.7",
          row[1] == "-7.7", str(row[1]))
    check("γ₀=1.1 时利用率 = 7.7/16.2 = 0.475",
          row[5] == "0.475", str(row[5]))
    check("γ₀ 写在表注", "γ₀=1.1" in s["tables"]["stress_table"]["note"])
    s3 = strength_2d.evaluate(_manual_res(), spec, limit=3)
    check("limit=3 时只列 3 行", len(s3["tables"]["stress_table"]["rows"]) == 3)
    check("limit 写进状态文案", "前 3 个" in s3["note"])


# ---------------------------------------------------------------- 显式拒绝


def offline_refusals() -> None:
    r = _manual_res()
    cases = [
        ("材料等级 grade 缺失", _spec(grade=""), "材料未显式给出强度等级"),
        ("构件类型 kind 缺失", _spec(kind=""), "构件类型 kind"),
        ("kind 非法", _spec(kind="wood"), "构件类型 kind"),
        ("混凝土等级查表失败", _spec(grade="C999"), "表 3.1.3"),
        ("腹板面积 AW 缺失", _spec(AW=0.0), "AW 必须 > 0"),
        ("WT 缺失", _spec(WT=0.0), "WT、WB 必须都 > 0"),
        ("钢材板厚缺失", _spec(grade="Q345", kind="steel", t_plate=0.0),
         "板厚 t_plate"),
        ("钢材牌号查表失败", _spec(grade="Q999", kind="steel", t_plate=20.0),
         "表 3.2.1"),
    ]
    for name, spec, want in cases:
        s = strength_2d.evaluate(r, spec)
        check(f"拒绝负例：{name} → refused",
              s["status"] == "refused", s["status"])
        check(f"拒绝负例：{name} 不出表", s["tables"] == {})
        check(f"拒绝负例：{name} 指明原因 {want}",
              any(want in x for x in s["reasons"]), str(s["reasons"])[:120])
        check(f"拒绝负例：{name} 中文说明写明整章拒绝",
              "整章拒绝" in s["note"] and "不补零" in s["note"])
        check(f"拒绝负例：{name} 仍写明承载力做不了",
              "承载力验算（Mu / Vu / 压弯 / 轴压）本次未做" in s["note"])

    s = strength_2d.evaluate(r, _spec(truss=True))
    check("纯轴向杆模型 → no_beam_section",
          s["status"] == "no_beam_section", s["status"])
    check("纯轴向杆模型不出表", s["tables"] == {})
    check("纯轴向杆模型写明 TRUSS/CABLE/STRUT 不验算",
          "TRUSS" in s["note"] and "不做截面应力验算" in s["note"])
    check("skipped_axial 计数为 1", s["skipped_axial"] == 1,
          str(s["skipped_axial"]))

    empty = Solve2DResult()
    s = strength_2d.evaluate(empty, _spec())
    check("无成桥态内力 → unavailable", s["status"] == "unavailable",
          s["status"])
    check("unavailable 不出表", s["tables"] == {})
    check("unavailable 写明做不了", "做不了" in s["note"])
    s = strength_2d.evaluate(r, None)
    check("无 spec → unavailable", s["status"] == "unavailable")


def offline_status() -> None:
    for st in ("not_requested", "unavailable", "refused", "no_beam_section",
               "ok"):
        s = {"status": st, "reasons": ["示例原因"], "limit": 8,
             "cases": 12, "gamma0": 1.0, "has_live": False}
        check(f"状态 {st} 中文文案不为空",
              len(strength_2d.status_text(s)) >= 10)
    check("not_requested 写明默认关闭且不写进 .dat",
          "默认关闭" in strength_2d.status_text({"status": "not_requested"})
          and "不写进 .dat" in strength_2d.status_text({"status": "not_requested"}))
    check("CAPACITY_NOTE 写明不编配筋",
          "不编一个配筋出来" in strength_2d.CAPACITY_NOTE)
    check("CAPACITY_NOTE 写明只能标 P",
          "只能标 P" in strength_2d.CAPACITY_NOTE)
    check("CAPACITY_NOTE 不得出现 Markdown 星号（HTML 会印出来）",
          "**" not in strength_2d.CAPACITY_NOTE)
    check("表注不得出现 Markdown 星号",
          "**" not in strength_2d.STRESS_NOTE_EXTRA)


# ---------------------------------------------------------------- 默认路径


def offline_defaults() -> None:
    import model_doc

    r = Solve2DResult()
    check("solve_2d strength 默认 False",
          inspect.signature(solve_2d).parameters["strength"].default is False)
    check("ModelDoc.solve strength_2d 默认 False",
          inspect.signature(model_doc.ModelDoc.solve).parameters["strength_2d"].default
          is False)
    check("Solve2DResult 默认 not_requested",
          r.strength["status"] == "not_requested")
    check("默认结果 strength_tables() 为空", r.strength_tables() == {})
    check("默认结果 diagnostics 为空", r.diagnostics == [])

    src = (ROOT / "strength_2d.py").read_text(encoding="utf-8")
    src_solve = (ROOT / "solve_2d.py").read_text(encoding="utf-8")
    src_html = (ROOT / "export_html.py").read_text(encoding="utf-8")
    src_app = (ROOT / "app.py").read_text(encoding="utf-8")
    src_doc = (ROOT / "model_doc.py").read_text(encoding="utf-8")
    check("口径层不消费内核应力段（无 parse_result / stress 分支）",
          "parse_result" not in src and 'section == "stress"' not in src)
    check("口径层写明唯一效应源 final_beam",
          "final_beam" in src and "beam_combinations" in src)
    check("口径层不重算截面模量、不从 name 猜牌号",
          "不从 `MaterialDef.name`" in src and "不从材料 name 猜" in src)
    check("solve_2d 把 strength 传给 evaluate",
          "strength_2d.evaluate(res, spec)" in src_solve)
    check("solve_2d 默认路径不追加 strength 诊断",
          "if strength:" in src_solve and "strength_2d.status_text" in src_solve)
    check("ModelDoc.solve 把开关传给 2D 链",
          "strength=bool(strength_2d)" in src_doc)
    check("capability() 自述 strength2d 可用性", '"strength2d":' in src_doc)
    check("HTML 默认 not_requested 不出章节",
          '_s.get("status") != "not_requested"' in src_html)
    check("HTML 章节与界面读同一份表格",
          "res.strength_tables()" in src_html and "strength_tables" in src_app)
    check("界面有 opt-in 开关且默认关",
          'key="strength2d_opt"' in src_app and "value=False" in src_app)
    check("界面开关纳入结果过期判断",
          'bool(_s2d.get("strength2d", False)) != _strength2d_on' in src_app)
    check("界面把开关传给 doc.solve",
          "strength_2d=_strength2d_on" in src_app)
    check("界面与 HTML 都渲染承载力拒绝说明",
          "CAPACITY_NOTE" in src_app and "CAPACITY_NOTE" in src_html)

    # 默认 HTML 逐位不变：空结果不出章，且没有 Markdown 星号印进计算书。
    spec = _spec()
    h0 = write_2d_html(Solve2DResult(), spec, OUT / "default.html").read_text(
        encoding="utf-8")
    check("默认 HTML 不出 2D 截面应力验算章",
          "2D 截面应力验算" not in h0)
    check("默认 HTML 不出现承载力说明",
          "承载力验算（Mu / Vu" not in h0)
    check("默认 HTML 无 Markdown 星号残留", "**" not in h0)


def offline_dat_invariant() -> None:
    """新字段是 Python-only：带与不带的 spec 生成**同一份** .dat。"""
    from verify_stage_tension_3d import DEFAULT_DAT_SHA
    import hashlib

    plain = _spec(grade="", kind="", AW=0.0)
    rich = _spec(grade="Q345", kind="steel", AW=0.12, t_plate=20.0)
    a, b = plain.to_dat(), rich.to_dat()
    check("验算参数不写进 .dat（带/不带逐位相同）", a == b)
    for token in ("AW=", "TPLATE=", "GRADE=", "KIND=", "REBAR="):
        check(f".dat 不含 {token}", token not in b)
    check("*R 仍只写内核认识的实常数", "WT=0.6" in b and "WB=0.6" in b)
    for (kind, dim), builder in __import__("model_doc").BUILDERS.items():
        import model_doc

        dat = model_doc.ModelDoc(kind, dict(model_doc.DEFAULTS[builder]),
                                 dimension=dim).to_dat()
        h = hashlib.sha256(dat.encode()).hexdigest()[:16]
        check(f"12 格默认 .dat 黄金哈希：{builder}",
              h == DEFAULT_DAT_SHA[builder], h)


def offline_docs() -> None:
    mat = (ROOT / "能力边界矩阵.md").read_text(encoding="utf-8")
    line = [ln for ln in mat.splitlines()
            if ln.startswith("| 截面应力 / 承载力验算 |")]
    check("能力矩阵该格 2D 标 P（不是 Y、不是 N）",
          len(line) == 1 and "| P | Y |" in line[0], str(line))
    check("能力矩阵登记 2D 依据符号",
          "strength_2d.py::evaluate" in mat
          and "solve_2d.py::strength_tables" in mat)
    check("能力矩阵写明 P 口径边界",
          "2D=P" in mat and "配筋契约" in mat)
    check("§4.2 写 6 个 N + 1 个 P 且仍标已收窄",
          "6 个" in mat and "1 个 `2D=P`" in mat and "已收窄" in mat)
    check("能力矩阵登记第六次闭环记录", "第六次闭环记录" in mat)
    rd = (ROOT / "README.md").read_text(encoding="utf-8")
    check("README 同步 P 口径", "1 项部分接入" in rd and "截面应力" in rd)
    guide = (ROOT / "开发使用手册.md").read_text(encoding="utf-8")
    check("开发手册登记 2D 应力守卫", "verify_strength_2d.py" in guide)
    check("使用说明登记 2D 应力口径层",
          "strength_2d.py" in (ROOT / "使用说明.md").read_text(encoding="utf-8"))
    spec = (ROOT / "B类后续项接入规格.md").read_text(encoding="utf-8")
    check("B 类规格写明承载力需新配筋契约",
          "配筋契约" in spec and "RebarSpec2D" in spec)
    check("B 类规格写明应力部分已接入", "已接入" in spec
          and "strength_2d.py" in spec)


# ---------------------------------------------------------------- live 链


def live_chain() -> None:
    if not solve_2d_available():
        print("  [live 跳过] 2D 静态内核未编译")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    spec = _spec()
    r0 = solve_2d(spec, out_dir=OUT / "plain", dat_name="plain.dat")
    r1 = solve_2d(spec, out_dir=OUT / "strength", dat_name="strength.dat",
                  strength=True)
    check("默认求解不请求应力验算",
          r0.strength["status"] == "not_requested")
    check("默认 diagnostics 不多写应力说明",
          len(r1.diagnostics) == len(r0.diagnostics) + 1,
          f"{len(r0.diagnostics)} → {len(r1.diagnostics)}")
    check("默认结果 strength_tables() 为空", r0.strength_tables() == {})
    check("开启后状态 ok", r1.strength["status"] == "ok",
          r1.strength["status"])
    check("开启后出 stress_table", "stress_table" in r1.strength_tables())
    check("开关不改 .dat 逐位", r0.dat_text == r1.dat_text)
    h0 = write_2d_html(r0, spec, OUT / "plain.html").read_text(encoding="utf-8")
    h1 = write_2d_html(r1, spec, OUT / "strength.html").read_text(encoding="utf-8")
    check("默认 HTML 不出应力章", "2D 截面应力验算" not in h0)
    check("开启后 HTML 出应力章", "2D 截面应力验算" in h1)
    check("开启后 HTML 出应力表", "2D 控制截面应力验算" in h1)
    check("开启后 HTML 写明承载力本次未做",
          "承载力验算（Mu / Vu / 压弯 / 轴压）本次未做" in h1)
    check("HTML 表数值与结果同源",
          r1.strength_tables()["stress_table"]["rows"][0][1] in h1)
    check("HTML 无 Markdown 星号残留", "**" not in h1)

    # 缺 AW：整章显式拒绝，不摆空表、不补零、不影响静力结果。
    bare = _spec(AW=0.0)
    rb = solve_2d(bare, out_dir=OUT / "refused", dat_name="refused.dat",
                  strength=True)
    check("缺 AW → refused", rb.strength["status"] == "refused",
          rb.strength["status"])
    check("缺 AW 时 strength_tables() 为空", rb.strength_tables() == {})
    check("缺 AW 时成桥态内力仍可用", bool(rb.final_beam))
    hb = write_2d_html(rb, bare, OUT / "refused.html").read_text(encoding="utf-8")
    check("拒绝时 HTML 仍出章节", "2D 截面应力验算" in hb)
    check("拒绝时 HTML 写明整章拒绝原因", "整章拒绝" in hb and "AW" in hb)
    check("拒绝时 HTML 不摆空表", "<h3>2D 控制截面应力验算" not in hb)
    check("拒绝时 HTML 仍写明承载力做不了",
          "承载力验算（Mu / Vu / 压弯 / 轴压）本次未做" in hb)
    # 带验算参数与不带验算参数的 .dat 逐位一致（live 侧复核）。
    check("live：验算参数不写进 .dat", r1.dat_text == rb.dat_text)


def main() -> int:
    print("=" * 78)
    print("2D 截面应力验算（P 口径）接入链路守卫")
    print("=" * 78)
    offline_concrete_oracle()
    offline_steel_oracle()
    offline_gamma_limit()
    offline_refusals()
    offline_status()
    offline_defaults()
    offline_dat_invariant()
    offline_docs()
    live_chain()
    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(PASS)} 通过")
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
