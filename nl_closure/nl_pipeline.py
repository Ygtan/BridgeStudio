"""技术句解析 + 同源导出流水线（注册表驱动，13 例统一）。

核心防伪设计：
  * 技术句经 extract_fields() 独立抽出数值字段；
  * case_registry.REGISTRY[key].params 是「从 run_cases_*.py 反向抄录」的独立锚；
  * 两者必须逐项相等（validate_fields），否则报错，杜绝「技术句是摆设」。
  * 校验通过后用「技术句抽出的值」调 build_xxx，再复用项目现成导出器生成 dat/inp。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BS = HERE.parent
for p in (str(BS), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from nl_case_parser import parse_number  # noqa: E402
from case_registry import CaseDef, get  # noqa: E402

# 现有同源导出器（零改动复用）
from bridge_spec import (BoundaryDef, NodalLoadDef,  # noqa: E402
                         build_beam_bridge)
from ansys_bridge import spec_to_apdl  # noqa: E402
from ansys_bridge_3d import build_box_beam_3d, spec3d_to_apdl  # noqa: E402
from ansys_bridge_3d_loads import (build_box_beam_3d_loads,  # noqa: E402
                                   spec3d_loads_to_apdl)
from ansys_bridge_cable import (build_cable_stayed_3d,  # noqa: E402
                                build_arch_bridge_3d, spec_mixed_to_apdl)
from ansys_bridge_tower import build_cable_stayed_tower_3d  # noqa: E402

MIXED_BUILDERS = {
    "build_cable_stayed_3d": build_cable_stayed_3d,
    "build_arch_bridge_3d": build_arch_bridge_3d,
    "build_cable_stayed_tower_3d": build_cable_stayed_tower_3d,
}


class FieldError(ValueError):
    pass


def _one(pattern: str, text: str, key: str, conv=parse_number):
    m = re.search(pattern, text)
    if not m:
        raise FieldError(f"技术句缺字段 [{key}]，匹配式: {pattern}")
    return conv(m.group(1))


def _opt(pattern: str, text: str, default, conv=parse_number):
    m = re.search(pattern, text)
    return default if not m else conv(m.group(1))


def _cn_scientific(text: str) -> float:
    """兼容阿拉伯科学计数（2.06e11）与中文「二乘十的六次方」。"""
    return parse_number(text)


def extract_fields(case: CaseDef, text: str) -> dict:
    """从技术句独立抽取数值字段（不读注册表 params）。"""
    t = text.strip()
    f: dict = {}
    f["title_cn"] = _one(r"中文标题[:：]\s*([^。\n]+)", t, "中文标题", conv=str)
    f["title_en"] = _one(r"英文标题[:：]\s*([^。\n]+)", t, "英文标题", conv=str)

    c = case.chain
    if c == "2d_box":
        f["span"] = _one(r"总跨长\s*([^，,]+?)\s*米", t, "总跨长")
        f["n_spans"] = _one(r"跨径布置\s*([^、，,]+?)\s*跨", t, "跨数", conv=lambda s: int(parse_number(s)))
        f["n_elem"] = _one(r"每跨划分\s*([^个，,]+?)\s*个梁单元", t, "每跨梁单元数", conv=lambda s: int(parse_number(s)))
        shape_cn = _one(r"截面为\s*(箱形|矩形)", t, "截面形式", conv=str)
        f["shape"] = {"箱形": "box", "矩形": "rectangular"}[shape_cn]
        f["h"] = _one(r"高\s*([^米，,]+?)\s*米", t, "截面高")
        f["b"] = _one(r"宽\s*([^米，,]+?)\s*米", t, "截面宽")
        f["udl_kN"] = _opt(r"均布荷载\s*([^千牛，,]+?)\s*千牛每米", t, 0.0)
        # 自重：CB03 有「重力加速度 负九点八一」
        f["gravity"] = -9.81 if re.search(r"重力加速度\s*负九点八一", t) else 0.0
        # CB04 节点集中力：「在第X号节点和第Y号节点各施加向下集中力 -200000 牛」
        mforce = re.search(r"集中力\s*(-?[0-9.eE+\-]+)\s*牛", t)
        if mforce:
            val = float(mforce.group(1))
            nodes = re.findall(r"第\s*([^号]+?)\s*号节点", t)
            f["nodal_loads"] = tuple(
                (int(parse_number(n.strip())), 2, val) for n in nodes)
        # CB05 弹性支承
        sp = re.search(r"刚度\s*(-?[0-9.eE+\-]+)\s*牛每米", t)
        if sp:
            f["spring_k"] = parse_number(sp.group(1))
            f["spring_node"] = _one(r"第\s*([^号]+?)\s*号节点的竖向支座", t, "弹簧节点",
                                    conv=lambda s: int(parse_number(s)))

    elif c == "box3d":
        f["n_spans"] = _one(r"跨径布置\s*([^、，,]+?)\s*跨", t, "跨数", conv=lambda s: int(parse_number(s)))
        f["span_m"] = _one(r"每跨\s*([^米，,]+?)\s*米", t, "每跨跨径")
        f["n_elem_per_span"] = _one(r"每跨划分\s*([^个，,]+?)\s*个梁单元", t, "每跨单元数",
                                    conv=lambda s: int(parse_number(s)))
        shape_cn = _one(r"截面为\s*(箱形|矩形)", t, "截面形式", conv=str)
        f["shape"] = {"箱形": "box", "矩形": "rectangular"}[shape_cn]
        f["B"] = _one(r"宽\s*([^米，,]+?)\s*米", t, "截面宽")
        f["h"] = _one(r"高\s*([^米，,]+?)\s*米", t, "截面高")
        f["tw"] = _one(r"腹板厚\s*([^米，,]+?)\s*米", t, "腹板厚")
        f["tf"] = _one(r"翼缘厚\s*([^米，,]+?)\s*米", t, "翼缘厚")
        f["E"] = _one(r"弹性模量\s*([0-9.eE+\-]+)\s*帕", t, "弹性模量", conv=float)
        f["nu"] = _one(r"泊松比\s*([0-9.]+)", t, "泊松比", conv=float)
        f["udl_kN"] = _one(r"均布荷载\s*([^千牛，,]+?)\s*千牛每米", t, "均布荷载")
        # 附加节点荷载：node16 横向 -100000（dof2）、扭矩 50000（dof4）
        extra = []
        m2 = re.search(r"施加横向力\s*(-?[0-9.eE+\-]+)\s*牛（自由度二）", t)
        if m2:
            extra.append((16, 2, float(m2.group(1))))
        m4 = re.search(r"扭矩\s*(-?[0-9.eE+\-]+)\s*牛米（自由度四）", t)
        if m4:
            extra.append((16, 4, float(m4.group(1))))
        if extra:
            f["extra_loads"] = tuple(extra)

    elif c == "loads3d":
        # 几何全走默认，技术句仍陈述以便可审计
        f["span_m"] = _one(r"每跨\s*([^米，,]+?)\s*米", t, "每跨跨径")
        f["n_elem_per_span"] = _one(r"每跨划分\s*([^个，,]+?)\s*个梁单元", t, "每跨单元数",
                                    conv=lambda s: int(parse_number(s)))
        shape_cn = _one(r"截面为\s*(箱形|矩形)", t, "截面形式", conv=str)
        f["shape"] = {"箱形": "box", "矩形": "rectangular"}[shape_cn]
        f["B"] = _one(r"宽\s*([^米，,]+?)\s*米", t, "截面宽")
        f["h"] = _one(r"高\s*([^米，,]+?)\s*米", t, "截面高")
        f["tw"] = _one(r"腹板厚\s*([^米，,]+?)\s*米", t, "腹板厚")
        f["tf"] = _one(r"翼缘厚\s*([^米，,]+?)\s*米", t, "翼缘厚")
        f["E"] = _one(r"弹性模量\s*([0-9.eE+\-]+)\s*帕", t, "弹性模量", conv=float)
        f["nu"] = _one(r"泊松比\s*([0-9.]+)", t, "泊松比", conv=float)
        # q1: CB3D06「均布分布荷载 20 千牛每米」/ CB3D08「左端 20 千牛每米」
        mq = re.search(r"(?:均布分布荷载|左端)\s*(-?[0-9.eE+\-]+)\s*千牛每米", t)
        if not mq:
            raise FieldError("loads3d 缺 q1（均布分布荷载/左端 … 千牛每米）")
        f["q_kN"] = float(mq.group(1))
        f["q2_kN"] = _opt(r"右端\s*(-?[0-9.eE+\-]+)\s*千牛每米", t, None, conv=float)
        f["dens"] = _opt(r"材料密度\s*(-?[0-9.eE+\-]+)\s*千克每立方米", t, 0.0, conv=float)
        f["gravity_z"] = _opt(r"重力加速度竖向\s*(-?[0-9.eE+\-]+)", t, 0.0, conv=float)
        f["self_weight_mass"] = _opt(r"全部单元附加质量\s*(-?[0-9.eE+\-]+)", t, None, conv=float)
        # 竖向集中力（CB3D07 node16 dof3）
        if re.search(r"第十六号节点施加竖向集中力", t):
            val = _one(r"竖向集中力\s*(-?[0-9.eE+\-]+)\s*牛（自由度三）", t, "竖向集中力", conv=float)
            f["point_dof3"] = (16, 3, val)

    elif c == "mixed":
        f["span"] = _one(r"主跨\s*([^米，,]+?)\s*米", t, "主跨")
        f["n_elem"] = _one(r"(?:主梁划分|主梁)\s*([^个单元，,]+?)\s*个", t, "主梁单元数",
                           conv=lambda s: int(parse_number(s)))
        # 「宽X米、高Y米」成对出现；截面高取第一处，塔柱高取第二处（CB3D09）
        heights = re.findall(r"宽[^米，,]+?米、高\s*([^米，,]+?)\s*米", t)
        if not heights:
            raise FieldError("mixed 缺截面高（宽X米、高Y米）")
        f["B"] = _one(r"宽\s*([^米，,]+?)\s*米", t, "截面宽")
        f["h"] = parse_number(heights[0].strip())
        f["tw"] = _one(r"腹板厚\s*([^米，,]+?)\s*米", t, "腹板厚")
        f["tf"] = _one(r"翼缘厚\s*([^米，,]+?)\s*米", t, "翼缘厚")
        f["E_deck"] = _one(r"主梁弹性模量\s*([0-9.eE+\-]+)\s*帕", t, "主梁弹模", conv=float)
        f["nu"] = _one(r"泊松比\s*([0-9.]+)", t, "泊松比", conv=float)
        f["udl_kN"] = _one(r"均布荷载\s*([^千牛，,]+?)\s*千牛每米", t, "均布荷载")
        if case.key == "CB3D03":
            f["tower_z"] = _one(r"塔顶标高\s*([^米，,]+?)\s*米", t, "塔顶标高")
            f["cable_area"] = _one(r"索面积\s*([^平方米，,]+?)\s*平方米", t, "索面积")
            f["E_cable"] = _one(r"索弹性模量\s*([0-9.eE+\-]+)\s*帕", t, "索弹模", conv=float)
        elif case.key == "CB3D04":
            f["rise"] = _one(r"矢高\s*([^米，,]+?)\s*米", t, "矢高")
            f["hanger_area"] = _one(r"吊杆面积\s*([^平方米，,]+?)\s*平方米", t, "吊杆面积")
            f["E_hanger"] = _one(r"吊杆弹性模量\s*([0-9.eE+\-]+)\s*帕", t, "吊杆弹模", conv=float)
        elif case.key == "CB3D09":
            f["n_tower_seg"] = _one(r"塔柱划分\s*([^个单元，,]+?)\s*个单元", t, "塔柱分段",
                                    conv=lambda s: int(parse_number(s)))
            f["tower_z"] = _one(r"塔顶标高\s*([^米，,]+?)\s*米", t, "塔顶标高")
            f["tower_b"] = _one(r"塔柱截面宽\s*([^米，,]+?)\s*米", t, "塔柱宽")
            if len(heights) < 2:
                raise FieldError("CB3D09 缺塔柱高（第二处 宽X米、高Y米）")
            f["tower_h"] = parse_number(heights[1].strip())
            f["cable_area"] = _one(r"索面积\s*([^平方米，,]+?)\s*平方米", t, "索面积")
            f["E_cable"] = _one(r"索弹性模量\s*([0-9.eE+\-]+)\s*帕", t, "索弹模", conv=float)

    return f


def _close(a, b, tol=1e-12) -> bool:
    if isinstance(a, (tuple, list)) and isinstance(b, (tuple, list)):
        return len(a) == len(b) and all(_close(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k], tol) for k in a)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if isinstance(a, bool) or isinstance(b, bool):
            return a == b
        return abs(a - b) <= tol * max(1.0, abs(a), abs(b))
    return a == b


def build_expected(case: CaseDef) -> dict:
    """从注册表 params 生成期望字段（与 extract_fields 的键对齐）。"""
    p = case.params
    c = case.chain
    e = {"title_cn": case.title_cn, "title_en": case.title_en}
    if c == "2d_box":
        e.update(span=p["span"], n_spans=p["n_spans"], n_elem=p["n_elem"],
                 shape=p["shape"], h=p["h"], b=p["b"], udl_kN=p["udl_kN"],
                 gravity=p["gravity"])
        if case.key == "CB04":
            e["nodal_loads"] = ((6, 2, -200000.0), (16, 2, -200000.0))
        if case.key == "CB05":
            e.update(spring_k=2.0e6, spring_node=11)
    elif c == "box3d":
        e.update(n_spans=len(p["spans"]), span_m=p["spans"][0],
                 n_elem_per_span=p["n_elem_per_span"], shape="box",
                 B=p["B"], h=p["h"], tw=p["tw"], tf=p["tf"], E=p["E"], nu=p["nu"],
                 udl_kN=p["udl_kN"])
        if "extra_loads" in p:
            e["extra_loads"] = p["extra_loads"]
    elif c == "loads3d":
        e.update(span_m=30.0, n_elem_per_span=10, shape="box",
                 B=8.0, h=2.0, tw=0.02, tf=0.025, E=2.06e11, nu=0.2,
                 q_kN=p["q_kN"], q2_kN=p.get("q2_kN"),
                 dens=p.get("dens", 0.0),
                 gravity_z=p.get("gravity", (0, 0, 0))[2],
                 self_weight_mass=(p["self_weights"][0]["value"]
                                   if p.get("self_weights") else None))
        if "extra_loads" in p:
            e["point_dof3"] = p["extra_loads"][0]
    elif c == "mixed":
        e.update(span=p["span"], n_elem=p["n_elem"], B=p["B"], h=p["h"],
                 tw=p["tw"], tf=p["tf"], E_deck=p["E_deck"], nu=p["nu"],
                 udl_kN=p["udl_kN"])
        for k in ("tower_z", "cable_area", "E_cable", "rise",
                  "hanger_area", "E_hanger", "n_tower_seg", "tower_b", "tower_h"):
            if k in p:
                e[k] = p[k]
    return e


def validate(case: CaseDef, fields: dict) -> list[str]:
    """技术句抽出的字段 vs 注册表独立锚，逐项核对。"""
    exp = build_expected(case)
    problems = []
    for k, v in exp.items():
        if k not in fields:
            problems.append(f"缺字段 {k}")
        elif not _close(fields[k], v):
            problems.append(f"{k}: 技术句={fields[k]!r} 期望={v!r}")
    return problems


def build_model(case: CaseDef, fields: dict):
    """用技术句抽出的字段构造模型（注册表只提供结构，不提供数值）。"""
    c = case.chain
    f = fields
    if c == "2d_box":
        kw = dict(span=f["span"], n_spans=f["n_spans"], n_elem=f["n_elem"],
                  shape=f["shape"], h=f["h"], b=f["b"],
                  udl_kN=f["udl_kN"], gravity=f["gravity"])
        spec = build_beam_bridge(**kw)
        if "nodal_loads" in f:
            spec.nodal_loads.extend([
                NodalLoadDef(node=n, dof=d, value=v) for n, d, v in f["nodal_loads"]])
        if "spring_k" in f:
            node = f["spring_node"]
            for i, b in enumerate(spec.boundaries):
                if int(b.node) == node:
                    spec.boundaries[i] = BoundaryDef(
                        node=node, dofs=b.dofs, value=0.0, k=f["spring_k"])
                    break
            else:
                raise FieldError(f"节点 {node} 无可替换支座")
        return spec
    if c == "box3d":
        kw = dict(spans=(f["span_m"],) * f["n_spans"],
                  n_elem_per_span=f["n_elem_per_span"], B=f["B"], h=f["h"],
                  tw=f["tw"], tf=f["tf"], E=f["E"], nu=f["nu"],
                  udl_kN=f["udl_kN"], title=f["title_cn"])
        if "extra_loads" in f:
            kw["extra_loads"] = f["extra_loads"]
        return build_box_beam_3d(**kw)
    if c == "loads3d":
        kw = dict(q_kN=f["q_kN"], title=case.title_cn)
        if f.get("q2_kN") is not None:
            kw["q2_kN"] = f["q2_kN"]
        if f.get("dens", 0.0) != 0.0:
            kw["dens"] = f["dens"]
            kw["gravity"] = (0.0, 0.0, f["gravity_z"])
        if f.get("self_weight_mass") is not None:
            kw["self_weights"] = (dict(who="ALL", rule="MASS",
                                       value=f["self_weight_mass"]),)
        if "point_dof3" in f:
            kw["extra_loads"] = (f["point_dof3"],)
        return build_box_beam_3d_loads(**kw)
    if c == "mixed":
        fn = MIXED_BUILDERS[case.mixed_builder]
        kw = dict(span=f["span"], n_elem=f["n_elem"], B=f["B"], h=f["h"],
                  tw=f["tw"], tf=f["tf"], E_deck=f["E_deck"], nu=f["nu"],
                  udl_kN=f["udl_kN"], title=f["title_cn"])
        for k in ("tower_z", "cable_area", "E_cable", "rise", "hanger_area",
                  "E_hanger", "n_tower_seg", "tower_b", "tower_h"):
            if k in f:
                kw[k] = f[k]
        return fn(**kw)
    raise FieldError(f"未知链路 {c}")


def export(case: CaseDef, model, work: Path, fields: dict) -> dict[str, Path]:
    """调用项目现有同源导出器，返回 {逻辑名: 生成文件}。"""
    stem = case.stem
    out: dict[str, Path] = {}
    c = case.chain
    if c == "2d_box":
        dat = work / (stem + ".dat")
        dat.write_text(model.to_dat(), encoding="utf-8")
        inp = work / (stem + ".inp")
        inp.write_text(spec_to_apdl(model, jobname=stem, title=fields["title_en"]),
                       encoding="utf-8")
        out.update(dat=dat, inp=inp)
    elif c == "box3d":
        dat = work / (stem + ".dat")
        dat.write_text(model.to_dat(compact=False), encoding="utf-8")
        out["dat"] = dat
        for elem, suffix, job in (
            ("BEAM188", stem + ".inp", stem),
            ("BEAM4", stem + "_b4.inp", stem + "_b4"),
        ):
            p = work / suffix
            p.write_text(spec3d_to_apdl(model, jobname=job, element=elem,
                                        title=fields["title_en"]), encoding="ascii")
            out["inp_" + elem] = p
    elif c == "loads3d":
        dat = work / (stem + ".dat")
        dat.write_text(model.to_dat(compact=False), encoding="utf-8")
        inp = work / (stem + ".inp")
        inp.write_text(spec3d_loads_to_apdl(model, jobname=stem,
                                            title=fields["title_en"]), encoding="ascii")
        out.update(dat=dat, inp=inp)
    elif c == "mixed":
        dat = work / (stem + ".dat")
        dat.write_text(model.to_dat(compact=False), encoding="utf-8")
        inp = work / (stem + ".inp")
        inp.write_text(spec_mixed_to_apdl(model, jobname=stem,
                                          title=fields["title_en"]), encoding="ascii")
        out.update(dat=dat, inp=inp)
    return out


def run_case(case: CaseDef, work: Path):
    """完整解析+建模+导出：返回 (fields, model, files, problems)。"""
    text = (HERE / case.desc).read_text(encoding="utf-8")
    fields = extract_fields(case, text)
    problems = validate(case, fields)
    if problems:
        return fields, None, {}, problems
    model = build_model(case, fields)
    files = export(case, model, work, fields)
    return fields, model, files, []
