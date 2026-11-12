"""export_2d_formats.py —— 2D 平行导出 / 导入链（平面刚架），B 档，第十五次 2026-11-12。

本模块实现与 3D 链 **正交、平行、互不冒充** 的第二条链（契约 §3.4 / §7 B5）：

    3D : ModelSpec3D → canonical dict → ANSYS / Abaqus / MIDAS        （export_formats.py）
    2D : BridgeSpec  → plane_frame_2d/v1 → ANSYS / Abaqus / MIDAS(2D)  （本模块）

本模块的中间表示 ``plane_frame_2d/v1`` **不是 X20 canonical dict**，不是内部真源，
不回流到建造器 / 求解 / 报告 / 回归；它只由 ``to_plane(BridgeSpec)`` 产出、只被本
模块的三个写出器消费。铁律 2 的「唯一转换点」作用域只覆盖 3D 那条链，
``to_canonical`` 与三个 3D 写出器签名一字未改。

物理隔离（契约 §3.4 / §9）：本模块不加载 3D 导出器模块、不定义同名转换函数，
不 import ``ModelSpec3D`` / ``MctImport`` / ``write_mct*`` / ``read_mct*``，不 import
3D schema；唯一真源仍是 ``BridgeSpec``（契约铁律 1 明确允许）。为避免 ``mct_io``
（它 import ``model_spec_3d`` 的 3D 数据类）带来的环引用与概念污染，这里**自行实现
最小等价的文本切段与单位换算纯逻辑**：只借格式解析纯函数的思路，不借 3D schema；
换算系数与 ``mct_io`` 逐项对拍，见下方 ``_FORCE_TO_N`` / ``_LEN_TO_M`` 常量表。

维度语义：中间表示显式 ``dimension="2D"``，只有 x/y、平面 3 dof（UX/UY/ROTZ）、
单一截面惯矩 I；**不写** z / 6 dof / IYY·IZZ·IXX 等 3D 专有字段，缺项标「不适用」
而不是补 0。MIDAS 语法必填的数值位（节点 XYZ 的 Z、6 位约束码、截面 6 个数值、
*CONLOAD 6 分量）按格式需要写，但都是**格式占位**，逐条进 warnings，明确
「这是 MIDAS 格式占位，不是 2D 模型字段；导入侧绝不反解成 2D 字段」。

``read_mct_2d*`` 只接受平面模型：逐节点检查 z 全 0、检查 6 位码的平面外位
（UZ/ROTX/ROTY，第 3/4/5 位）全部固定，否则 errors 写 ``dimension_not_plane`` 并令
``spec=None``，绝不静默投影丢信息。导入的是用户提供的 .mct，不是归档目录产物，
不违反铁律 3「只出不进」。成功时构造真实 ``BridgeSpec`` 并验证 ``to_dat()`` 能出文本。

组合体系（B1）仍显式拒绝：本模块与 ``model_doc.COMBO_SYSTEM_MSG`` 无关，不碰它。

CLI 用法（无 GUI 入口，app.py 零改动；模块 API + CLI 即本轮交付面）：
    python export_2d_formats.py export <model.json> <out_dir>
        # 从 JSON 用 BridgeSpec.model_validate_json 还原规范模型，再导出三格式
    python export_2d_formats.py import <in.mct> [out.json]
        # 反向导入到 BridgeSpec，打印 summary()，可选把 spec 写成 JSON
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bridge_spec import (BridgeSpec, BoundaryDef, ElemDef, ElemKind,
                         ElemTypeDef, MaterialDef, NodalLoadDef, NodeDef)

__all__ = [
    "PLANE_SCHEMA", "DIMENSION", "PLANE_DOF_NAMES", "NOT_APPLICABLE",
    "to_plane", "write_ansys_2d", "write_abaqus_2d", "write_midas_2d",
    "export_2d_all", "PlaneMctImport",
    "read_mct_2d_text", "read_mct_2d_file", "read_mct_2d",
]

# ---------------------------------------------------------------- 契约常量
PLANE_SCHEMA = "plane_frame_2d/v1"
DIMENSION = "2D"
PLANE_DOF_NAMES = ("UX", "UY", "ROTZ")       # 平面 3 dof：1=u(x) 2=v(y) 3=θ
NOT_APPLICABLE = "不适用"

# 单位换算：与 mct_io._FORCE_TO_N / _LEN_TO_M 逐项对拍（力→N、长度→m）。
# 支持 *UNIT 的 N,MM / KN,M / KGF,CM 等组合。
_FORCE_TO_N = {"N": 1.0, "KN": 1e3, "KGF": 9.80665, "TONF": 9.80665e3,
               "LBF": 4.4482216153, "KIP": 4.4482216153e3}
_LEN_TO_M = {"MM": 1e-3, "CM": 1e-2, "M": 1.0, "IN": 0.0254, "FT": 0.3048}
_GRAVITY = 9.806   # 与 mct_io.GRAVITY 一致；容重(N/m³) → 质量密度(kg/m³)

_DOF_NUM_TO_NAME = {1: "UX", 2: "UY", 3: "ROTZ"}
_ABAQUS_DOF = {1: 1, 2: 2, 3: 6}          # 2D ROTZ(dof 3) → Abaqus UR3(dof 6)
_KIND_TO_MIDAS_2D = {"BEAM": "BEAM", "TRUSS": "TRUSS",
                     "CABLE": "TENSTR", "STRUT": "TRUSS"}
_KIND_FROM_MIDAS_2D = {"BEAM": "BEAM", "TRUSS": "TRUSS", "TENSTR": "CABLE"}
_SEC_TYPES_2D = {"VALUE", "DBUSER", "DB", "PSC", "SRC", "COMBINED", "TAPERED",
                 "COMPOSITE-B", "COMPOSITE-T", "CONSTRUCT", "USER"}
_MIDAS_CODE_ORDER = ("UX", "UY", "UZ", "ROTX", "ROTY", "ROTZ")

# 读到的标准段（不丢信息）。
_BENIGN_2D = {"VERSION", "UNIT", "STRUCTYPE", "ENDDATA", "NODE", "ELEMENT",
              "MATERIAL", "SECTION", "CONSTRAINT", "CONLOAD", "SELFWEIGHT",
              "GROUP", "MATL-COLOR", "SECT-COLOR", "VIEW", "POST-SETTING"}
# 读到但**搬不动**的段 → 一句话说清丢了什么（进 losses）。
_LOSSY_2D = {
    "STLDCASE": "荷载工况定义未还原（2D 平行链只导单工况全激活模型）",
    "USE-STLD": "当前荷载工况未还原",
    "SELFWEIGHT": "自重未转节点力（self_weight 固定 False，避免与节点力双计）",
    "BEAMLOAD": "梁单元分布荷载未还原（只还原节点力）",
    "BEAMLOAD-COM": "梁单元组合荷载未还原",
    "MOVLOAD": "移动荷载（车道）未还原",
    "MVLOAD": "移动荷载（车道）未还原",
    "STAGE": "施工阶段未还原（合并为单工况）",
    "STAGEANAL": "施工阶段分析设置未还原",
    "PRESTRESS": "预应力钢束未还原",
    "TDN-PROFILE": "钢束形状未还原",
    "TEMP": "温度荷载未还原",
    "TEMPERATURE": "温度荷载未还原",
    "ELASTICLINK": "弹性连接未还原",
    "RIGIDLINK": "刚性连接未还原",
    "NODALELASTIC": "节点弹性支承未还原",
    "SPRING": "弹簧单元未还原",
}


# ---------------------------------------------------------------- 纯工具
def _num(v: Any) -> str:
    """数值格式化：10 位有效数字，-0.0 洗成 0（往返相对误差 ~1e-10）。"""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "0"
    if x == 0.0:
        return "0"
    if not math.isfinite(x):
        raise ValueError(f"数值非法（nan/inf）：{v!r}")
    return f"{x:.10g}"


def _f(tok: str, default: float = 0.0) -> float:
    """宽松解析一个数值字段（MCT 里空格 / 空字段很常见）。"""
    t = (tok or "").strip()
    if not t:
        return default
    try:
        return float(t)
    except ValueError:
        return default


def _is_int(tok: str) -> bool:
    try:
        int(tok)
        return True
    except (TypeError, ValueError):
        return False


def _decode(data: bytes | str) -> str:
    """bytes/str → str。与 mct_io._decode 等价的最小实现（只借纯函数思路）。"""
    if isinstance(data, str):
        return data
    for enc in ("utf-8-sig", "utf-8", "gbk", "cp949"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


@dataclass
class _Sec:
    """一个 ``*段``：段名 + 段头参数 + 数据行（已去注释、去空行）。"""
    name: str
    arg: str
    lines: list[str]
    line_no: int


def _split(text: str) -> list[_Sec]:
    """按 ``*段名`` 切块，数据行归上一段；行内 ``;`` 注释一律切掉。"""
    out: list[_Sec] = []
    cur: _Sec | None = None
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        if line.startswith("*"):
            name, _, arg = line[1:].partition(",")
            cur = _Sec(name.strip().upper(), arg.strip(), [], i)
            out.append(cur)
        elif cur is not None:
            cur.lines.append(line)
    return out


def _parse_unit_2d(secs: list[_Sec]
                   ) -> tuple[tuple[str, str], float, float, list[str]]:
    """``*UNIT`` → (原样单位, 力→N, 长度→m, 告警)；换算系数见常量表。"""
    warns: list[str] = []
    fu, lu = "N", "M"
    for s in secs:
        if s.name != "UNIT":
            continue
        toks: list[str] = []
        if s.arg:
            toks += [t.strip().upper() for t in s.arg.split(",") if t.strip()]
        for ln in s.lines:
            toks += [t.strip().upper() for t in ln.split(",") if t.strip()]
        if toks:
            fu = toks[0]
        if len(toks) > 1:
            lu = toks[1]
        break
    if fu not in _FORCE_TO_N:
        warns.append(f"未识别的力单位 {fu}，按 N 处理（若原文件是 kN，结果会差 1000 倍）")
        fu = "N"
    if lu not in _LEN_TO_M:
        warns.append(f"未识别的长度单位 {lu}，按 m 处理（若原文件是 mm，几何会差 1000 倍）")
        lu = "M"
    warns.append(
        f"*UNIT 单位换算：{fu}, {lu} → m / N / Pa（长度 ×{_LEN_TO_M[lu]:g}、"
        f"力 ×{_FORCE_TO_N[fu]:g}）；E 按 力/长度²、I 按 长度⁴、坐标按长度换算。")
    return (fu, lu), _FORCE_TO_N[fu], _LEN_TO_M[lu], warns


def _parse_gravity_2d(secs: list[_Sec], len_to_m: float) -> float:
    """``*STRUCTYPE`` 第 3 字段给重力加速度；没有就用 _GRAVITY。"""
    for s in secs:
        if s.name != "STRUCTYPE":
            continue
        for ln in s.lines:
            f = [t.strip() for t in ln.split(",")]
            if len(f) >= 3:
                g = _f(f[2])
                if g > 0:
                    return g if g < 100.0 else g * len_to_m
        break
    return _GRAVITY


def _is_sec_header_2d(line: str) -> bool:
    f = [t.strip() for t in line.split(",")]
    return len(f) >= 3 and _is_int(f[0]) and f[1].upper() in _SEC_TYPES_2D


def _skip_to_header_2d(lines: list[str], i: int) -> int:
    while i < len(lines) and not _is_sec_header_2d(lines[i]):
        i += 1
    return i


def _validate_plane(plane: dict[str, Any]) -> None:
    """三个写出器只吃 plane dict，校验 schema / dimension，否则中文 ValueError。"""
    if not isinstance(plane, dict):
        raise ValueError("2D 写出器只吃 plane dict（plane_frame_2d/v1），拒绝其它输入")
    if plane.get("schema") != PLANE_SCHEMA:
        raise ValueError(
            f"schema 不是 {PLANE_SCHEMA}，拒绝写出：这不是 2D 平行链中间表示")
    if plane.get("dimension") != DIMENSION:
        raise ValueError("dimension 不是 2D，拒绝写出：本写出器只处理平面刚架")


def _format_placeholder_warnings() -> list[str]:
    """MIDAS 语法必填位逐条登记（导出侧写进文件注释 + export_2d_all.warnings，
    导入侧复用同一组措辞说明「不反解」）。"""
    return [
        "MIDAS 格式占位：*NODE 的 Z 列按语法写 0；z 在 2D 平面刚架中" + NOT_APPLICABLE
        + "，这是 MIDAS 格式占位，不是 2D 模型字段；导入侧绝不反解成 2D 字段。",
        "MIDAS 格式占位：*CONSTRAINT 的 6 位约束码按语法写全，平面外位"
        "（UZ/ROTX/ROTY，第 3/4/5 位）固定为 1 是平面几何的格式要求；" + NOT_APPLICABLE
        + "，导入侧绝不反解成 2D 边界字段。",
        "MIDAS 格式占位：*SECTION 第 2 行 6 个数值（AREA, ASy, ASz, Ixx, Iyy, Izz）"
        "按语法写全，只有 AREA 与单一惯矩 I（落在 Iyy 位）是 2D 字段，其余为格式占位；"
        "IYY/IZZ/IXX 在 2D " + NOT_APPLICABLE + "，导入侧绝不反解。",
        "MIDAS 格式占位：*CONLOAD 的 6 个分量（FX,FY,FZ,MX,MY,MZ）按语法写全，"
        "平面外分量 FZ/MX/MY 恒为 0 是格式占位；2D 只有 3 dof，" + NOT_APPLICABLE
        + "，导入侧绝不反解。",
    ]


def _inherent_losses_2d() -> list[str]:
    """2D 平行链「一切带不走的信息」如实登记，绝不静默丢。"""
    return [
        "车道荷载 / 影响线：2D 平行链不还原移动荷载与影响线，" + NOT_APPLICABLE
        + "（*.mct 的 *MOVLOAD 与本链结构数据不是同一类）。",
        "成桥态 / 施工阶段 / 阶段激活-钝化：本链只导单工况全激活模型，" + NOT_APPLICABLE + "。",
        "自重（*SELFWEIGHT）：self_weight 固定 False，自重不转节点力，" + NOT_APPLICABLE + "。",
        "预应力钢束 / 温度荷载：2D 平行链不还原，" + NOT_APPLICABLE + "。",
        "β 角（单元截面转角 ANGLE）/ iSUB 细分：平面梁局部轴唯一确定，" + NOT_APPLICABLE + "。",
        "DBUSER / PSC / SRC 截面库引用：只接受 VALUE 数值截面，" + NOT_APPLICABLE + "。",
        "分布荷载（*BEAMLOAD 等）：只还原节点力，" + NOT_APPLICABLE + "。",
        "MIDAS 6 位约束码 / 6 分量荷载 / 截面 6 数值等格式占位：只反解 2D 真实字段，"
        "其余格式占位" + NOT_APPLICABLE + "，绝不反解成 2D 字段。",
    ]


def _abaqus_dof_warnings() -> list[str]:
    return [
        "Abaqus 平面梁 dof 口径差异：2D 平面刚架 ROTZ（dof 3）在 Abaqus B21/B23 中"
        "是 UR3（dof 6）。本写出器按 Abaqus 口径把 θz 写成 6 并在文件注释注明；"
        "dof 3 的 3D 语义在 2D 平面梁中" + NOT_APPLICABLE + "，该口径差异已记 losses。",
    ]


# ---------------------------------------------------------------- BridgeSpec → 2D 中间表示
def to_plane(spec: BridgeSpec) -> tuple[dict[str, Any], list[str]]:
    """BridgeSpec → 2D 平面刚架中间表示（不是 X20 canonical dict）。
    返回 (plane, warnings)；warnings 必须显示给用户，绝不静默丢信息。"""
    warnings: list[str] = []
    plane: dict[str, Any] = {
        "schema": PLANE_SCHEMA,
        "dimension": DIMENSION,
        "bridge": {"name": spec.title or spec.note or "bridge",
                   "type": "PLANE_FRAME_2D"},
        "materials": [],
        "section_types": [],
        "nodes": [],
        "elements": [],
        "boundary_conditions": [],
        "loads": {"nodal_loads": [], "element_loads": [], "self_weight": False},
    }

    warnings.append(
        "2D 平面刚架中间表示（plane_frame_2d/v1）显式 dimension=2D，只含 x/y、"
        "平面 3 dof（UX/UY/ROTZ）、单一惯矩 I；z / 6 dof / IYY·IZZ·IXX 等 3D 专有"
        "字段" + NOT_APPLICABLE + "（不写、不补 0、不填 None 占位）。")

    # ---- 材料：只写 id / name / E / 密度；不写 poisson / thermal 等 3D 字段
    for m in spec.materials:
        plane["materials"].append({
            "id": m.id, "name": m.name,
            "elastic_modulus": m.E, "density": m.dens,
        })
    warnings.append("材料只导出 E / 密度；泊松比 / 线膨胀系数等 3D 专有字段在 2D "
                    + NOT_APPLICABLE + "（不写、不补 0）。")

    # ---- 截面：单一惯矩 I，绝不写 IYY/IZZ/IXX/IY/IZ/J 任何键
    for t in spec.elem_types:
        kind = t.kind.value if hasattr(t.kind, "value") else str(t.kind)
        item: dict[str, Any] = {"id": t.id, "kind": kind, "area": t.A}
        if t.I is not None:
            item["inertia"] = t.I
        else:
            warnings.append(
                f"单元类型 {t.id}（{kind}）缺单一惯矩 I：2D 模型字段缺失，"
                f"该截面 inertia " + NOT_APPLICABLE + "（不补 0、不写键）")
        plane["section_types"].append(item)
    warnings.append("截面只写 area 与单一惯矩 inertia（来自 ElemTypeDef.I）；"
                    "IYY/IZZ/IXX 在 2D " + NOT_APPLICABLE + "，不写这些键。")

    # ---- 节点：只有 x / y，绝不写 z 键
    for n in spec.nodes:
        plane["nodes"].append({"id": n.id, "x": n.x, "y": n.y})
    warnings.append("节点只写 x / y；z 在 2D " + NOT_APPLICABLE + "，不写 z 键、不补 0。")

    # ---- 单元
    type_by_id = {t.id: t for t in spec.elem_types}
    for e in spec.elements:
        t = type_by_id.get(e.type_id)
        if t is None:
            warnings.append(
                f"单元 {e.id} 引用了不存在的截面类型 {e.type_id}：material_id / kind "
                + NOT_APPLICABLE + "（不补 0、不写键）")
            plane["elements"].append({
                "id": e.id, "section_type_id": e.type_id,
                "start_node": e.n1, "end_node": e.n2,
            })
            continue
        kind = t.kind.value if hasattr(t.kind, "value") else str(t.kind)
        plane["elements"].append({
            "id": e.id, "section_type_id": e.type_id, "material_id": t.mat,
            "start_node": e.n1, "end_node": e.n2, "kind": kind,
        })

    # ---- 边界：dofs 只用 UX/UY/ROTZ 三个名字，绝不写 6 dof
    for i, b in enumerate(spec.boundaries, 1):
        nums: set[int] = set()
        for part in re.split(r"[,\s]+", (b.dofs or "").strip()):
            if not part:
                continue
            if "-" in part:
                nn = part.split("-")
                try:
                    nums.update(range(int(nn[0]), int(nn[-1]) + 1))
                except ValueError:
                    pass
            else:
                try:
                    nums.add(int(part))
                except ValueError:
                    pass
        dofs = [_DOF_NUM_TO_NAME[d] for d in sorted(nums) if d in _DOF_NUM_TO_NAME]
        plane["boundary_conditions"].append({
            "id": i, "node_id": b.node, "dofs": dofs,
        })
        bad = [d for d in nums if d not in _DOF_NUM_TO_NAME]
        if bad:
            warnings.append(
                f"边界节点 {b.node} 的 dofs='{b.dofs}' 含 2D 之外的自由度 {bad}："
                + NOT_APPLICABLE + "（不写、不补 0）")
    warnings.append("边界约束只写平面 3 dof（UX/UY/ROTZ）；6 dof 在 2D "
                    + NOT_APPLICABLE + "，不写。")

    # ---- 荷载：只有节点力；self_weight 固定 False
    for i, ld in enumerate(spec.nodal_loads, 1):
        plane["loads"]["nodal_loads"].append({
            "id": i, "node_id": ld.node, "dof": ld.dof, "value": ld.value,
        })
    if spec.elem_loads:
        warnings.append(
            f"{len(spec.elem_loads)} 条单元均布荷载（*ELEMENT_LOAD）是分布荷载，"
            "2D 平行链只还原节点力，" + NOT_APPLICABLE + "、带不走（进 losses）。")
    if spec.gravity:
        warnings.append(
            f"BridgeSpec.gravity={spec.gravity:g}（计自重）：2D 中间表示 self_weight "
            f"固定 False，自重" + NOT_APPLICABLE + "、不会写入导出文件，需在目标软件"
            "另行施加（避免与节点力双计）。")
    if spec.lanes:
        warnings.append(f"{len(spec.lanes)} 条车道荷载 / 影响线：2D 平行链带不走，"
                        + NOT_APPLICABLE + "（进 losses）。")
    if spec.stages:
        warnings.append(f"{len(spec.stages)} 个施工阶段：2D 平行链只导单工况，"
                        + NOT_APPLICABLE + "（进 losses）。")
    if spec.prestresses or spec.prestress_losses:
        warnings.append("预应力束 / 分项损失：2D 平行链不还原，" + NOT_APPLICABLE
                        + "（进 losses）。")
    return plane, warnings


# ---------------------------------------------------------------- ANSYS (2D)
def write_ansys_2d(plane: dict[str, Any], path: str | os.PathLike[str]) -> None:
    """写 ANSYS APDL 平面刚架文件（BEAM3，3 dof/节点）。"""
    _validate_plane(plane)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    L: list[str] = []
    A = L.append
    A("FINISH")
    A("/CLEAR,NOSTART")
    A("/PREP7")
    A("! DIMENSION=2D  |  PLANE_FRAME_2D  |  schema=plane_frame_2d/v1")
    A("! 这是 2D 平面刚架（BEAM3，平面 3 dof/节点），不是 3D 模型")
    A("! z / 6 dof / IYY·IZZ·IXX 在 2D " + NOT_APPLICABLE + "；实常数必填数值位是"
      " ANSYS 格式占位，不是 2D 模型字段")
    A("! Generated by BridgeStudio / export_2d_formats.py (2D 平行链)")
    A("")
    A("! Materials (EX, DENS) —— 只给 E / 密度")
    for m in plane["materials"]:
        A(f"MP,EX,{m['id']},{_num(m['elastic_modulus'])}")
        A(f"MP,DENS,{m['id']},{_num(m['density'])}")
    A("")
    A("! Element type: 2D planar beam BEAM3")
    A("ET,1,BEAM3")
    A("")
    A("! Real constants: R,id,AREA,IZZ(=单一惯矩 I),HEIGHT,... （其余为格式占位）")
    for s in plane["section_types"]:
        A(f"R,{s['id']},{_num(s['area'])},{_num(s.get('inertia', 0.0))},1,0,0,0")
    A("")
    A("! Nodes: N,id,x,y （只有 x/y，无 z）")
    for n in plane["nodes"]:
        A(f"N,{n['id']},{_num(n['x'])},{_num(n['y'])}")
    A("")
    A("! Elements: EN,id,n1,n2")
    sec = {s["id"]: s for s in plane["section_types"]}
    for e in plane["elements"]:
        sid = e.get("section_type_id")
        if sid in sec:
            A(f"REAL,{sid}")
        A(f"EN,{e['id']},{e['start_node']},{e['end_node']}")
    A("")
    if plane["boundary_conditions"]:
        A("! Boundary conditions: D,node,UX|UY|ROTZ （平面 3 dof）")
        for bc in plane["boundary_conditions"]:
            for d in bc["dofs"]:
                A(f"D,{bc['node_id']},{d},0")
    nl = plane["loads"]["nodal_loads"]
    if nl:
        A("")
        A("! Nodal loads: F,node,FX|FY|MZ （dof 只到 3；ROTZ 对应 MZ）")
        label = {"1": "FX", "2": "FY", "3": "MZ"}
        for ld in nl:
            A(f"F,{ld['node_id']},{label.get(str(int(ld['dof'])), 'FX')},"
              f"{_num(ld['value'])}")
    A("")
    A("FINISH")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")


# ---------------------------------------------------------------- Abaqus (2D)
def write_abaqus_2d(plane: dict[str, Any], path: str | os.PathLike[str]) -> None:
    """写 Abaqus 平面梁 .inp（B21）：*Node 两坐标、单一惯矩、平面 3 dof。"""
    _validate_plane(plane)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    L: list[str] = []
    A = L.append
    A("*Heading")
    A("** DIMENSION=2D  |  PLANE_FRAME_2D  |  schema=plane_frame_2d/v1")
    A("** 这是 2D 平面刚架（Abaqus B21），不是 3D 模型")
    A("** z / 6 dof / IYY·IZZ·IXX 在 2D " + NOT_APPLICABLE)
    A("** dof 口径：Abaqus 平面梁 U1=1, U2=2, UR3=6；2D 的 ROTZ(dof 3) → 6")
    A("** *Beam Section 只给面积与单一惯矩 I，其余截面常数为格式占位 0")
    A("** Generated by BridgeStudio / export_2d_formats.py (2D 平行链)")
    A("")
    for m in plane["materials"]:
        A(f"*Material, name=MAT{m['id']}")
        A("*Elastic")
        A(f"{_num(m['elastic_modulus'])}, 0.0")
        A("*Density")
        A(f"{_num(m['density'])}")
    A("")
    A("*Part, name=PlaneFrame2D")
    A("*Node")
    for n in plane["nodes"]:
        A(f"{n['id']}, {_num(n['x'])}, {_num(n['y'])}")
    A("")
    # 每个 section_type 一个 elset + *Element + *Beam Section
    sec_ids = [s["id"] for s in plane["section_types"]]
    for sid in sec_ids:
        group = [e for e in plane["elements"] if e.get("section_type_id") == sid]
        A(f"*Element, type=B21, elset=SEC{sid}")
        for e in group:
            A(f"{e['id']}, {e['start_node']}, {e['end_node']}")
    for s in plane["section_types"]:
        sid = s["id"]
        mids = [e.get("material_id") for e in plane["elements"]
                if e.get("section_type_id") == sid]
        mid = mids[0] if mids else (plane["materials"][0]["id"]
                                    if plane["materials"] else 1)
        A(f"*Beam Section, elset=SEC{sid}, material=MAT{mid}, section=GENERAL")
        A(f"{_num(s['area'])}, {_num(s.get('inertia', 0.0))}, 0., 0., 0.")
        A("0., 0., -1.")
    A("*End Part")
    A("")
    A("*Assembly, name=Assembly")
    A("*Instance, name=PlaneFrame2D-1, part=PlaneFrame2D")
    A("*End Instance")
    A("*End Assembly")
    A("")
    if plane["boundary_conditions"]:
        A("*Boundary")
        for bc in plane["boundary_conditions"]:
            for name in bc["dofs"]:
                num = _ABAQUS_DOF[{"UX": 1, "UY": 2, "ROTZ": 3}[name]]
                A(f"{bc['node_id']}, {num}, {num}, 0.")
    nl = plane["loads"]["nodal_loads"]
    if nl:
        A("*Cload")
        for ld in nl:
            num = _ABAQUS_DOF.get(int(ld["dof"]), 1)
            A(f"{ld['node_id']}, {num}, {_num(ld['value'])}")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")


# ---------------------------------------------------------------- MIDAS (2D)
def write_midas_2d(plane: dict[str, Any], path: str | os.PathLike[str]) -> None:
    """写 MIDAS Civil .mct（平面刚架）。语法必填位都是格式占位，逐条写进注释与 warnings。"""
    _validate_plane(plane)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    L: list[str] = []
    A = L.append
    A("; DIMENSION=2D  |  PLANE_FRAME_2D  |  schema=plane_frame_2d/v1  |  不是 3D 文件")
    A("; 这是 2D 平面刚架，平面 3 dof（UX/UY/ROTZ）、单一惯矩 I")
    for w in _format_placeholder_warnings():
        A("; WARN " + w)
    A("*VERSION")
    A("8.8.5")
    A("*UNIT")
    A("N, M")
    A("*STRUCTYPE")
    A(f"0, 0, {_num(_GRAVITY)}, 0, NO, NO")
    A("")
    A("*MATERIAL")
    A("; iMAT, TYPE, MNAME, SPHEAT, HEATCO, PLAST, TUNIT, bMASS, DAMPRATIO,"
      " 2, ELAST, POISN, THERMAL, DEN(容重 N/m^3), MASS")
    A("; POISN/THERMAL=0 是 MIDAS 格式占位，2D " + NOT_APPLICABLE)
    for m in plane["materials"]:
        A(f"{m['id']}, USER, {m['name']}, 0, 0, 0, 0, NO, 0, 2, "
          f"{_num(m['elastic_modulus'])}, 0, 0, "
          f"{_num(m['density'] * _GRAVITY)}, 0")
    A("")
    A("*SECTION")
    A("; iSEC, TYPE, SNAME, ... ; 2nd line: AREA, ASy, ASz, Ixx, Iyy, Izz")
    A("; 单一惯矩 I 落 Iyy 位；其余 5 个数值为 MIDAS 格式占位，2D " + NOT_APPLICABLE)
    for s in plane["section_types"]:
        A(f"{s['id']}, VALUE, SEC{s['id']}, CC, 0, 0, 0, 0, 0, 0")
        A(f"{_num(s['area'])}, 0, 0, 0, {_num(s.get('inertia', 0.0))}, 0")
        A("0, 0, 0, 0, 0, 0, 0, 0, 0, 0")
        A("0, 0, 0, 0, 0, 0, 0, 0, 0, 0")
    A("")
    A("*NODE")
    for n in plane["nodes"]:
        A(f"{n['id']}, {_num(n['x'])}, {_num(n['y'])}, 0")
    A("")
    A("*ELEMENT")
    for e in plane["elements"]:
        kind = _KIND_TO_MIDAS_2D.get(e.get("kind"), "BEAM")
        A(f"{e['id']}, {kind}, {e.get('material_id', 1)}, {e['section_type_id']}, "
          f"{e['start_node']}, {e['end_node']}, 0, 0, 0")
    A("")
    if plane["boundary_conditions"]:
        A("*CONSTRAINT")
        for bc in plane["boundary_conditions"]:
            d = set(bc["dofs"])
            code = "".join([
                "1" if "UX" in d else "0",
                "1" if "UY" in d else "0",
                "1",            # 第 3 位 UZ：平面外固定（格式占位）
                "1",            # 第 4 位 ROTX：平面外固定（格式占位）
                "1",            # 第 5 位 ROTY：平面外固定（格式占位）
                "1" if "ROTZ" in d else "0",
            ])
            A(f"{bc['node_id']}, {code}, ")
    nl = plane["loads"]["nodal_loads"]
    if nl:
        A("")
        A("*STLDCASE")
        A("DL, D, 2D 平面刚架导出的荷载工况")
        A("*USE-STLD, DL")
        A("*CONLOAD")
        A("; NODE, FX, FY, FZ, MX, MY, MZ, GROUP （FZ/MX/MY 为格式占位 0）")
        for ld in nl:
            comps = ["0", "0", "0", "0", "0", "0"]
            dof = int(ld["dof"])
            if dof == 1:
                comps[0] = _num(ld["value"])     # FX
            elif dof == 2:
                comps[1] = _num(ld["value"])     # FY
            elif dof == 3:
                comps[5] = _num(ld["value"])     # MZ（ROTZ）
            A(f"{ld['node_id']}, " + ", ".join(comps) + ", ")
    A("")
    A("*ENDDATA")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")


def export_2d_all(spec: BridgeSpec, out_dir: str | os.PathLike[str],
                  stem: str = "model2d") -> dict[str, Any]:
    """返回 {"paths": {...}, "warnings": [...], "plane": {...}}；paths 键 ansys/abaqus/midas。"""
    plane, warnings = to_plane(spec)
    warnings = list(warnings)
    warnings += _format_placeholder_warnings()
    warnings += _abaqus_dof_warnings()
    out_dir = os.fspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    paths = {
        "ansys": os.path.join(out_dir, f"{stem}.ans"),
        "abaqus": os.path.join(out_dir, f"{stem}.inp"),
        "midas": os.path.join(out_dir, f"{stem}.mct"),
    }
    write_ansys_2d(plane, paths["ansys"])
    write_abaqus_2d(plane, paths["abaqus"])
    write_midas_2d(plane, paths["midas"])
    return {"paths": paths, "warnings": warnings, "plane": plane}


# ---------------------------------------------------------------- .mct → BridgeSpec
@dataclass
class PlaneMctImport:
    spec: BridgeSpec | None
    errors: list[str]
    warnings: list[str]
    losses: list[str]
    source: str = ""

    def summary(self) -> dict[str, Any]:
        ok = self.spec is not None and not self.errors
        nodes = self.spec.nodes if self.spec is not None else []
        elems = self.spec.elements if self.spec is not None else []
        return {
            "ok": ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "losses": list(self.losses),
            "n_nodes": len(nodes),
            "n_elements": len(elems),
            "dimension": DIMENSION,
        }


def read_mct_2d_text(text: str, *, title: str = "", source: str = "") -> PlaneMctImport:
    """解析 MCT 文本 → 真实 BridgeSpec。**不抛异常**：问题都进 errors / warnings / losses。

    只接受平面模型：z 全 0 且平面外约束一致，否则 ``dimension_not_plane`` 并 spec=None。
    """
    errors: list[str] = []
    warnings: list[str] = list(_format_placeholder_warnings())
    losses: list[str] = list(_inherent_losses_2d())

    secs = _split(_decode(text))
    if not secs:
        errors.append("文件里没有找到任何 *段（不是 MIDAS MCT 文本？）")
        return PlaneMctImport(None, errors, warnings, losses, source)

    (fu, lu), force_to_n, len_to_m, unit_warns = _parse_unit_2d(secs)
    warnings += unit_warns
    gravity = _parse_gravity_2d(secs, len_to_m)

    # ---- 未识别段 / 有损段登记（一切带不走的信息进 losses）
    counts: dict[str, int] = {}
    for s in secs:
        counts[s.name] = counts.get(s.name, 0) + 1
        if s.name not in _BENIGN_2D and s.name not in _LOSSY_2D:
            losses.append(f"未识别段 *{s.name}（{len(s.lines)} 行）已跳过，"
                          + NOT_APPLICABLE)
    for name, msg in _LOSSY_2D.items():
        if counts.get(name):
            losses.append(f"*{name}：{msg}，" + NOT_APPLICABLE)

    # ---- 材料（只反解 E / 密度；POISN/THERMAL 是格式占位，不反解）
    materials: dict[int, MaterialDef] = {}
    for s in secs:
        if s.name != "MATERIAL":
            continue
        for ln in s.lines:
            f = [t.strip() for t in ln.split(",")]
            if len(f) < 15 or f[9] != "2":
                warnings.append(f"材料行无法识别（非标准 USER 15 字段），已跳过："
                                f"{ln[:60]}")
                continue
            try:
                mid = int(f[0])
            except ValueError:
                continue
            name = f[2]
            E = _f(f[10]) * force_to_n / (len_to_m ** 2)
            den_w = _f(f[13]) * force_to_n / (len_to_m ** 3)
            dens = den_w / gravity if gravity else 0.0
            materials[mid] = MaterialDef(id=mid, name=name, E=E, dens=dens)
        break

    # ---- 截面：只接受 VALUE；DBUSER/PSC 等 3D 专有类型 warnings 并跳过
    sections: dict[int, tuple[float, float]] = {}
    for s in secs:
        if s.name != "SECTION":
            continue
        lines = s.lines
        i = 0
        while i < len(lines):
            if not _is_sec_header_2d(lines[i]):
                i += 1
                continue
            head = [t.strip() for t in lines[i].split(",")]
            i += 1
            sid = int(head[0])
            typ = head[1].upper()
            if typ != "VALUE":
                warnings.append(
                    f"截面 {sid}（{head[2]}）类型是 {typ}：截面库引用 / 3D 专有类型，"
                    f"拿不到 A / 单一惯矩，" + NOT_APPLICABLE + "，已跳过（不静默）")
                i = _skip_to_header_2d(lines, i)
                continue
            if i >= len(lines):
                errors.append(f"截面 {sid} 缺截面特性行")
                break
            vals = [_f(t) for t in lines[i].split(",")]
            i += 1
            if len(vals) < 6:
                errors.append(f"截面 {sid} 特性行只有 {len(vals)} 个数，至少要 6 个")
                i = _skip_to_header_2d(lines, i)
                continue
            A = vals[0] * (len_to_m ** 2)
            I = vals[4] * (len_to_m ** 4)      # 单一惯矩 I 在 Iyy 位（第 5 个）
            sections[sid] = (A, I)
            i = _skip_to_header_2d(lines, i)
        break

    # ---- 节点（逐点检查 z；任一非 0 → dimension_not_plane，spec=None）
    nodes: list[NodeDef] = []
    seen: set[int] = set()
    for s in secs:
        if s.name != "NODE":
            continue
        for ln in s.lines:
            f = [t.strip() for t in ln.split(",")]
            if len(f) < 3:
                continue
            try:
                nid = int(f[0])
            except ValueError:
                continue
            x = _f(f[1]) * len_to_m
            y = _f(f[2]) * len_to_m
            z = (_f(f[3]) if len(f) > 3 else 0.0) * len_to_m
            if abs(z) > 1e-9:
                errors.append(
                    f"dimension_not_plane: 节点 {nid} 的 z={z:g} 非 0"
                    "（2D 平面刚架不接受非零平面外坐标，绝不静默投影丢信息）")
                return PlaneMctImport(None, errors, warnings, losses, source)
            if nid in seen:
                errors.append(f"节点 {nid} 重复定义")
                continue
            seen.add(nid)
            nodes.append(NodeDef(id=nid, x=x, y=y))
        break

    # ---- 单元（只接受能映射到 2D ElemTypeDef 的类型；3D 专有类型 warnings 并跳过）
    elem_rows: list[tuple[int, str, int, int, int, int]] = []
    for s in secs:
        if s.name != "ELEMENT":
            continue
        for ln in s.lines:
            f = [t.strip() for t in ln.split(",")]
            if len(f) < 6:
                continue
            try:
                eid, mat, sec = int(f[0]), int(f[2]), int(f[3])
                n1, n2 = int(f[4]), int(f[5])
            except ValueError:
                continue
            typ = f[1].upper()
            kind = _KIND_FROM_MIDAS_2D.get(typ)
            if kind is None:
                warnings.append(
                    f"单元 {eid} 的 MIDAS 类型 {typ} 是 3D 专有 / 非平面类型，"
                    + NOT_APPLICABLE + "，已跳过（不静默）")
                continue
            if len(f) > 6 and abs(_f(f[6])) > 1e-9:
                losses.append(f"单元 {eid} 的 β 角（ANGLE≠0）" + NOT_APPLICABLE
                              + "：2D 平面梁局部轴唯一确定，不还原")
            elem_rows.append((eid, kind, mat, sec, n1, n2))
        break

    # ---- 约束（6 位码平面外位必须固定；否则 dimension_not_plane，spec=None）
    boundaries: list[BoundaryDef] = []
    for s in secs:
        if s.name != "CONSTRAINT":
            continue
        for ln in s.lines:
            f = [t.strip() for t in ln.split(",")]
            if len(f) < 2:
                continue
            try:
                nid = int(f[0])
            except ValueError:
                continue
            code = f[1].replace(" ", "").ljust(6, "0")[:6]
            if code[2] != "1" or code[3] != "1" or code[4] != "1":
                errors.append(
                    f"dimension_not_plane: 节点 {nid} 的 *CONSTRAINT 6 位码 "
                    f"{code} 的平面外自由度（UZ/ROTX/ROTY，第 3/4/5 位）未固定，"
                    "不是平面模型，绝不静默投影丢信息")
                return PlaneMctImport(None, errors, warnings, losses, source)
            ds = []
            if code[0] == "1":
                ds.append(1)      # UX
            if code[1] == "1":
                ds.append(2)      # UY
            if code[5] == "1":
                ds.append(3)      # ROTZ
            if ds:
                boundaries.append(BoundaryDef(node=nid, dofs=",".join(map(str, ds))))
        break

    # ---- 荷载（*CONLOAD 6 分量；平面外分量是格式占位，不反解成 2D 字段）
    nodal_loads: list[NodalLoadDef] = []
    for s in secs:
        if s.name != "CONLOAD":
            continue
        for ln in s.lines:
            f = [t.strip() for t in ln.split(",")]
            if len(f) < 2:
                continue
            try:
                nid = int(f[0])
            except ValueError:
                continue
            vals = [_f(f[k]) if k < len(f) else 0.0 for k in range(1, 7)]
            fx, fy, fz, mx, my, mz = vals
            for nm, v in (("FZ", fz), ("MX", mx), ("MY", my)):
                if abs(v) > 0:
                    warnings.append(
                        f"节点 {nid} 的 {nm}={v:g} 是平面外分量（MIDAS 6 分量"
                        "格式占位），2D " + NOT_APPLICABLE + "，不还原")
            if fx:
                nodal_loads.append(NodalLoadDef(node=nid, dof=1, value=fx * force_to_n))
            if fy:
                nodal_loads.append(NodalLoadDef(node=nid, dof=2, value=fy * force_to_n))
            if mz:
                nodal_loads.append(NodalLoadDef(node=nid, dof=3, value=mz * force_to_n))
        break

    # ---- 组装 BridgeSpec
    if not nodes:
        errors.append("没有读到任何节点（*NODE 段缺失或格式不符）")
    if not materials:
        errors.append("没有读到任何材料（*MATERIAL 段缺失或格式不符）")
    mat_ids = set(materials)
    sec_ids = set(sections)
    node_ids = {n.id for n in nodes}
    etypes: list[ElemTypeDef] = []
    elements: list[ElemDef] = []
    key_to_tid: dict[tuple[str, int, int], int] = {}
    for eid, kind, mat, sec, n1, n2 in sorted(elem_rows):
        if mat not in mat_ids:
            errors.append(f"单元 {eid} 引用了不存在的材料 {mat}")
            continue
        if sec not in sec_ids:
            errors.append(f"单元 {eid} 引用了不存在的截面 {sec}")
            continue
        if n1 not in node_ids or n2 not in node_ids:
            errors.append(f"单元 {eid} 引用了不存在的节点 {n1}/{n2}")
            continue
        key = (kind, mat, sec)
        if key not in key_to_tid:
            tid = len(etypes) + 1
            key_to_tid[key] = tid
            A, I = sections[sec]
            etypes.append(ElemTypeDef(id=tid, kind=ElemKind(kind), A=A, I=I, mat=mat))
        elements.append(ElemDef(id=eid, type_id=key_to_tid[key], n1=n1, n2=n2))
    if not elements:
        errors.append("没有读到任何可用的 2D 单元")
    if not boundaries:
        errors.append("没有读到任何 2D 边界约束（结构会是机构）")

    if errors:
        return PlaneMctImport(None, errors, warnings, losses, source)

    try:
        spec = BridgeSpec(
            title=title or "imported_2d_mct",
            nodes=nodes, elements=elements, elem_types=etypes,
            materials=list(materials.values()), boundaries=boundaries,
            nodal_loads=nodal_loads, gravity=0.0,
        )
        _ = spec.to_dat()          # 验证能生成文本（不写文件）
        verrs = spec.validate_model()
        if verrs:
            errors.extend(verrs)
            return PlaneMctImport(None, errors, warnings, losses, source)
    except Exception as exc:                          # noqa: BLE001
        errors.append(f"构造 BridgeSpec / to_dat() 失败：{exc}")
        return PlaneMctImport(None, errors, warnings, losses, source)

    return PlaneMctImport(spec, errors, warnings, losses, source)


def read_mct_2d_file(path, *, title: str = "") -> PlaneMctImport:
    p = Path(path)
    return read_mct_2d_text(_decode(p.read_bytes()), title=title or p.stem,
                            source=str(p))


def read_mct_2d(source, *, title: str = "") -> PlaneMctImport:
    """从文本 / 字节 / 路径导入。文本里出现 ``*`` 就按文本处理。"""
    if isinstance(source, bytes):
        return read_mct_2d_text(_decode(source), title=title, source="<bytes>")
    if isinstance(source, (Path, os.PathLike)):
        return read_mct_2d_file(source, title=title)
    s = str(source)
    if "*" in s and "\n" in s:
        return read_mct_2d_text(s, title=title, source="<text>")
    return read_mct_2d_file(s, title=title)


# ---------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("export_2d_formats —— 2D 平行导出 / 导入链（平面刚架，B 档）")
        print("用法：")
        print("  python export_2d_formats.py export <model.json> <out_dir>")
        print("  python export_2d_formats.py import <in.mct> [out.json]")
        return 0

    cmd = argv[0]
    if cmd == "export":
        if len(argv) < 3:
            print("缺少 <model.json> 或 <out_dir>")
            return 2
        spec = BridgeSpec.model_validate_json(
            Path(argv[1]).read_text(encoding="utf-8"))
        res = export_2d_all(spec, argv[2])
        print(json.dumps(res["paths"], ensure_ascii=False, indent=2))
        for w in res["warnings"]:
            print(f"  · {w}")
        return 0

    if cmd == "import":
        if len(argv) < 2:
            print("缺少 <in.mct> 路径")
            return 2
        imp = read_mct_2d_file(argv[1])
        print(json.dumps(imp.summary(), ensure_ascii=False, indent=2))
        if len(argv) >= 3 and imp.spec is not None:
            Path(argv[2]).write_text(
                json.dumps(imp.spec.model_dump(mode="json"),
                           ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"已写出 {argv[2]}")
        return 0 if imp.summary()["ok"] else 1

    print(f"未知子命令 {cmd!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
