"""X20 多格式导出器（ANSYS / Abaqus / MIDAS）在 BridgeStudio 侧的落点。

上游 X20 的 ``bridge_fe_modeler.py`` 以 canonical dict 作为输入；BridgeStudio 的
唯一数据源是 ``ModelSpec3D``。两者字段同构但命名不同，所以这里只做两件事：

    to_canonical(model: ModelSpec3D) -> dict     一层薄映射（唯一转换点）
    write_{ansys,abaqus,midas}_file(model: dict, path: str) -> None
                                                 签名与 X20 完全一致

为什么不改签名直接吃 ``ModelSpec3D``：X20 那两千行是验证过的资产，改签名要逐行
把 ``model["nodes"]`` 改成 ``model.nodes``，改错一个键名不会报错、只会写出错数据；
适配器集中在一处，错了只错一个地方。附带好处：这份适配器同时就是两套数据模型的
对照表——以后要查「B 版的 IY 对应 X20 的 IYY 还是 IZZ」，看 ``_REAL_FIELDS`` 即可。

三处「完全合法但算错」的映射，逐一交代（这也是本文件存在的理由）：

1) **惯矩方向**。X20 的 BEAM3D 用 ANSYS 命名（AREA/IZZ/IYY/IXX），FEM-Eigen 用
   IY/IZ/J。两者都以「绕哪根轴」定义，所以按同名映射：IY→IYY、IZ→IZZ、J→IXX。
   千万不要按「竖向/横向」去对——FEM-Eigen 的桥在 X-Z 平面、竖向是 Z，而 ANSYS
   沿 X 的梁竖向是 Y；一旦按语义错配，整座桥的抗弯刚度张冠李戴，文件却完全合法，
   打开也看不出异常。

2) **泊松比**。``ModelSpec3D`` 不存 ν，只存 G（在实常数里）。必须用 ν = E/(2G) − 1
   反算，才能保证导出的剪切刚度与原模型一致；随手填 0.3 会让 G 变成 E/2.6，
   混凝土的 G 差 20% 以上。

3) **线膨胀系数**。``ModelSpec3D`` 没有这个字段（FEM-Eigen 的温度荷载走
   ``*TEMPERATURE`` 直接给温变，不走 α）。导出填 0——这会让想「用 α 复核温度
   次内力」的人得不到原模型的温度效应。这是已知限制，不装作有。

另外，X20 原版三处明确缺陷在此修正（详见各函数内的「X20 原版」注释）：
    · ANSYS 节点力 dof 3 被映射成 MZ（弯矩），应为 FZ；
    · ANSYS 文件尾部硬编码 ``ACEL,0,10,0`` + ``ANTYPE,4`` + ``SOLVE``（调试残留，
      会覆盖前面写的重力并用瞬态求解器静力模型）；
    · Abaqus 节点力 dof 3 被映射成 6（UR3），应为 3；BEAM3D 截面缺方向向量，
      且 I11/I22 按语义对调（局部 1 轴指向 -Z，I11 应是 IZZ）。
"""

from __future__ import annotations

import math
import os
from typing import Any

from model_spec_3d import ModelSpec3D

__all__ = [
    "to_canonical",
    "write_ansys_file",
    "write_abaqus_file",
    "write_midas_file",
    "export_all",
    "ANSYS_ELEMENT_TYPES",
    "ABAQUS_ELEMENT_TYPES",
    "MIDAS_ELEMENT_TYPES",
]

ANSYS_ELEMENT_TYPES: dict[str, str] = {
    "BEAM3D": "BEAM4",
    "TRUSS3D": "LINK180",
    "CABLE3D": "LINK180",
}

ABAQUS_ELEMENT_TYPES: dict[str, str] = {
    "BEAM3D": "B31",
    "TRUSS3D": "T3D2",
    "CABLE3D": "T3D2",
}

MIDAS_ELEMENT_TYPES: dict[str, str] = {
    "BEAM3D": "BEAM",
    "TRUSS3D": "TRUSS",
    "CABLE3D": "TENSTR",
}

#: ModelSpec3D 的 ElemKind3D → X20 的 canonical kind。
#: BEAM7 收敛到 BEAM3D：X20/ANSYS BEAM4 都只有 6 个自由度，第 7 自由度（翘曲）
#: 无处安放，IW 会在导出时丢失——这是格式能力限制，不是映射遗漏。
_KIND_MAP = {
    "BEAM": "BEAM3D",
    "BEAM7": "BEAM3D",
    "TRUSS": "TRUSS3D",
    "CABLE": "CABLE3D",
}

_DOF_NAMES = ("UX", "UY", "UZ", "ROTX", "ROTY", "ROTZ")

#: B 版实常数 → X20 fields 的对照表（同名映射，见模块 docstring 第 1 条）。
_REAL_FIELDS_BEAM = {
    "AREA": "A",
    "IZZ": "IZ",
    "IYY": "IY",
    "IXX": "J",
    "ISTRN": "ISTRN",
    "ADDMAS": "ADDMASS",
    "WT": "WT",
    "WB": "WB",
}
_REAL_FIELDS_TRUSS = {
    "A_TRUSS": "A",
    "ISTRN": "ISTRN",
    "ADDMAS": "ADDMASS",
}


def _num(value: Any) -> str:
    """数值格式化。10 位有效数字，与 model_spec_3d.fmt 保持一致。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "0"
    if v == 0.0:
        return "0"
    return f"{v:.10g}"


def _poisson(E: float, G: float) -> tuple[float, bool]:
    """由 G 反算泊松比：ν = E/(2G) − 1。

    返回 (ν, 是否由 G 反算)。当 G 缺失或反算结果不在 (0.01, 0.49) 内时退化为
    0.3（钢/混凝土常规值）并告知调用方——退化的代价是剪切刚度不再与原模型一致，
    调用方应当据此告警。
    """
    if E > 0.0 and G > 0.0:
        nu = E / (2.0 * G) - 1.0
        if 0.01 < nu < 0.49:
            return nu, True
    return 0.3, False


def to_canonical(model: ModelSpec3D) -> tuple[dict[str, Any], list[str]]:
    """``ModelSpec3D`` → X20 canonical dict。

    返回 ``(canonical, warnings)``。warnings 是「导出成功但信息有损」的清单，
    调用方应当把它们显示给用户——静默丢信息比导出失败更难查。
    """
    warnings: list[str] = []

    # ---- 材料：ν 由实常数的 G 反算（见 docstring 第 2 条）
    # G 挂在实常数上，实常数经单元类型挂到材料上：material → etype → real。
    reals_by_id = {r.id: r for r in model.reals}
    real_ids_by_mat: dict[int, list[int]] = {}
    for t in model.etypes:
        real_ids_by_mat.setdefault(t.mat, [])
        if t.real not in real_ids_by_mat[t.mat]:
            real_ids_by_mat[t.mat].append(t.real)

    materials_out: list[dict[str, Any]] = []
    for m in sorted(model.materials, key=lambda m: m.id):
        G = 0.0
        for rid in real_ids_by_mat.get(m.id, []):
            r = reals_by_id.get(rid)
            if r is not None and r.G > 0:
                G = r.G
                break
        nu, from_g = _poisson(m.E, G)
        # ν 只对梁的剪切刚度有意义。只用 TRUSS/CABLE 的材料（索、吊杆）即使
        # G 缺失也不影响导出结果的力学等价性——为它们保留同一条告警，只会让
        # 真正的告警被淹没。
        kinds_of_mat = {_KIND_MAP.get(t.kind, "BEAM3D")
                        for t in model.etypes if t.mat == m.id}
        if not from_g and "BEAM3D" in kinds_of_mat:
            warnings.append(
                f"材料 {m.id} 的 G 缺失或异常，泊松比按 0.3 导出——"
                f"导出模型的剪切刚度将与原模型不一致")
        materials_out.append({
            "id": m.id,
            "name": f"MAT{m.id}",
            "elastic_modulus": m.E,
            "poisson_ratio": nu,
            "thermal_expansion": 0.0,   # 见 docstring 第 3 条
            "density": m.dens,
        })

    # ---- 实常数：按「被哪种 kind 引用」归类
    # X20 的 fields 是按 kind 定义字段集的（BEAM3D 与 TRUSS3D 字段名根本不同），
    # 一份 rc 表达不了两种 kind。若同一 real 同时被梁与索引用，必须拆成两条并
    # 让各自的 etype 指向不同 id——否则索的 A=0.006 m² 会长到梁的 AREA 上。
    kinds_by_real: dict[int, set[str]] = {}
    for t in model.etypes:
        kinds_by_real.setdefault(t.real, set()).add(_KIND_MAP.get(t.kind, "BEAM3D"))

    next_rc_id = max((r.id for r in model.reals), default=0) + 1
    rc_out: list[dict[str, Any]] = []
    #: (real_id, canonical_kind) → 实际使用的 rc id
    rc_id_by_key: dict[tuple[int, str], int] = {}

    for r in sorted(model.reals, key=lambda r: r.id):
        kinds = sorted(kinds_by_real.get(r.id) or {"BEAM3D"})
        for idx, kind in enumerate(kinds):
            if kind == "BEAM3D":
                table = _REAL_FIELDS_BEAM
            else:
                table = _REAL_FIELDS_TRUSS
            fields = {k: float(getattr(r, attr)) for k, attr in table.items()}
            if kind == "BEAM3D":
                # TKZ/TKY 是 ANSYS BEAM4 的剪切变形常数（1.0 = 不计剪切变形）。
                # X20 原版拿 TKY 当截面高度 H 用，是错的；这里按 ANSYS 语义给 1.0。
                fields["TKZ"] = 1.0
                fields["TKY"] = 1.0
                fields["THETA"] = 0.0
                fields["SHEARZ"] = 0.0
                fields["SHEARY"] = 0.0
                fields["SPIN"] = 0.0
                if r.IW:
                    warnings.append(
                        f"实常数 {r.id} 的翘曲常数 IW={r.IW:g} 无法导出——"
                        f"BEAM4/B31 均为 6 自由度梁")
            # 单一 kind 时保留原 id（便于与 .dat 对照）；被多种 kind 共用时，
            # 第一种沿用原 id，其余另取新号——同一份 rc 不能同时是梁和索。
            if idx == 0:
                rid = r.id
            else:
                rid = next_rc_id
                next_rc_id += 1
            rc_id_by_key[(r.id, kind)] = rid
            rc_out.append({"id": rid, "kind": kind, "fields": fields})

    # ---- 单元类型
    et_out: list[dict[str, Any]] = []
    for t in sorted(model.etypes, key=lambda t: t.id):
        ck = _KIND_MAP.get(t.kind, "BEAM3D")
        et_out.append({
            "id": t.id,
            "kind": ck,
            "material_id": t.mat,
            "real_id": rc_id_by_key.get((t.real, ck), t.real),
        })

    # ---- 节点 / 单元
    nodes_out = [
        {"id": n.id, "x": n.x, "y": n.y, "z": n.z}
        for n in sorted(model.nodes, key=lambda n: n.id)
    ]
    elements_out = [
        {"id": e.id, "element_type_id": e.etype, "start_node": e.n1, "end_node": e.n2}
        for e in sorted(model.elements, key=lambda e: e.id)
    ]

    # ---- 边界条件：ModelSpec3D 用闭区间 [a, b]，canonical 用自由度名列表
    bc_out: list[dict[str, Any]] = []
    for i, b in enumerate(model.boundaries, start=1):
        a, c = b.dofs
        dofs = [_DOF_NAMES[d - 1] for d in range(a, c + 1) if 1 <= d <= 6]
        bc_out.append({"id": i, "node_id": b.node, "dofs": dofs,
                       "value": b.value})
        if b.k:
            warnings.append(
                f"节点 {b.node} 的弹性支承 k={b.k:g} N/m 无法导出——"
                f"X20 的 canonical 模型只有刚性约束")

    # ---- 荷载：ModelSpec3D 只有节点力；无重力对象、无梁上分布荷载
    cloads = [{"id": i, "node_id": ld.node, "dof": ld.dof, "value": ld.value}
              for i, ld in enumerate(model.loads, start=1)]

    canonical = {
        "bridge": {"name": model.title, "type": "BEAM3D"},
        "materials": materials_out,
        "element_types": et_out,
        "real_constants": rc_out,
        "nodes": nodes_out,
        "elements": elements_out,
        "boundary_conditions": bc_out,
        "loads": {
            "gravity": None,
            "cloads": cloads,
            "element_loads": [],
            "uniform_temperature": None,
            "element_temperatures": [],
            "self_weight": False,
        },
    }

    if model.loads:
        warnings.append(
            "canonical 模型只承载节点力：ModelSpec3D 里若有梁上分布荷载/重力，"
            "导出后会丢失（当前实现的桥面恒载是换算成节点力的，故本项通常为空）")
    return canonical, warnings


def _ensure_parent(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


# ---------------------------------------------------------------- ANSYS APDL
def write_ansys_file(model: dict[str, Any], path: str) -> None:
    """写 ANSYS Mechanical APDL 输入文件（仅建模，不求解）。

    与 X20 原版的差异：
      · 节点力 dof 映射补全为 FX/FY/FZ/MX/MY/MZ（原版 dof 3 → MZ，把竖向力写成了
        绕 Z 的弯矩）；
      · 删除原版尾部硬编码的 ``ACEL,0,10,0`` / ``ANTYPE,4`` / ``TIME`` / ``SOLVE``。
        那是调试残留：它会覆盖前面写好的重力，并用瞬态求解器解静力模型；
      · 等间距节点用 APDL 原生 ``FILL`` 压缩（与 .dat 的 *FILL 同源）。
    """
    from model_spec_3d import _compress_node_lines, Node3D  # 局部导入避免循环引用

    _ensure_parent(path)
    ansys_dof = {1: "FX", 2: "FY", 3: "FZ", 4: "MX", 5: "MY", 6: "MZ"}

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("FINISH\n/CLEAR,NOSTART\n/PREP7\n")
        f.write("! Generated by BridgeStudio / export_formats.py\n")
        f.write("! 仅生成模型；求解控制（重力、荷载步、求解器）请在 ANSYS 中自行设置。\n\n")

        f.write("! Materials (EX, PRXY, ALPX, DENS)\n")
        for m in model["materials"]:
            f.write(f"MP,EX,{m['id']},{_num(m['elastic_modulus'])}\n")
            f.write(f"MP,PRXY,{m['id']},{_num(m['poisson_ratio'])}\n")
            f.write(f"MP,ALPX,{m['id']},{_num(m['thermal_expansion'])}\n")
            f.write(f"MP,DENS,{m['id']},{_num(m['density'])}\n\n")

        f.write("! Element types\n")
        for et in model["element_types"]:
            f.write(f"ET,{et['id']},{ANSYS_ELEMENT_TYPES[et['kind']]}\n")
        f.write("\n")

        f.write("! Real constants\n")
        for rc in model["real_constants"]:
            v = rc["fields"]
            if rc["kind"] == "BEAM3D":
                f.write(f"R,{rc['id']}," + ",".join(
                    _num(v[k]) for k in ("AREA", "IZZ", "IYY", "TKZ", "TKY", "THETA")) + "\n")
                f.write("RMORE," + ",".join(
                    _num(v[k]) for k in ("ISTRN", "IXX", "SHEARZ", "SHEARY", "SPIN", "ADDMAS")) + "\n")
            else:
                f.write(f"R,{rc['id']},{_num(v['A_TRUSS'])},{_num(v['ISTRN'])}\n")
        f.write("\n")

        f.write("! Nodes\n")
        nodes = [Node3D(**n) for n in model["nodes"]]
        for line in _compress_node_lines(nodes):
            if line.startswith("*FILL"):
                # APDL 的 FILL：FILL, NODE1, NODE2, NFILL, NSTRT, NINC
                _, a, b = line.split()
                a, b = int(a), int(b)
                f.write(f"FILL,{a},{b},{b - a - 1},{a + 1},1\n")
            else:
                nid, x, y, z = line.split()
                f.write(f"N,{nid},{x},{y},{z}\n")
        f.write("\n")

        f.write("! Elements\n")
        et_by_id = {et["id"]: et for et in model["element_types"]}
        active = (None, None, None)
        for e in model["elements"]:
            et = et_by_id[e["element_type_id"]]
            cur = (et["id"], et["material_id"], et["real_id"])
            if cur != active:
                f.write(f"TYPE,{cur[0]}\nMAT,{cur[1]}\nREAL,{cur[2]}\n")
                active = cur
            f.write(f"EN,{e['id']},{e['start_node']},{e['end_node']}\n")

        if model["boundary_conditions"]:
            f.write("\n! Boundary conditions\n")
            for bc in model["boundary_conditions"]:
                for dof in bc["dofs"]:
                    f.write(f"D,{bc['node_id']},{dof},{_num(bc['value'])}\n")

        cl = model["loads"]["cloads"]
        if cl:
            f.write("\n! Nodal loads\n")
            for ld in cl:
                f.write(f"F,{ld['node_id']},{ansys_dof.get(int(ld['dof']), 'FZ')},"
                        f"{_num(ld['value'])}\n")

        f.write("\nFINISH\n")


# ---------------------------------------------------------------- Abaqus
def write_abaqus_file(model: dict[str, Any], path: str) -> None:
    """写 Abaqus 输入文件（.inp）。

    与 X20 原版的差异：
      · 节点力 dof 3 映射为 3（U3），原版写成 6（UR3，绕 3 轴转角）；
      · BEAM3D 截面显式给方向向量 ``0., 0., -1.``（局部 1 轴指 -Z），并把
        I11/I22 修正为 IZZ/IYY——原版按「竖向/横向」语义给了 IYY/IZZ，
        在局部 1 轴指向 -Z 时正好对调；
      · 重力方向补足三分量。
    """
    _ensure_parent(path)
    et_by_id = {et["id"]: et for et in model["element_types"]}
    rc_by_id = {rc["id"]: rc for rc in model["real_constants"]}

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("*Heading\n")
        f.write("** Generated by BridgeStudio / export_formats.py\n")
        f.write("** Units: N, m, kg, Pa\n\n")

        f.write("** Materials\n")
        for m in model["materials"]:
            f.write(f"*Material, name=MAT{m['id']}\n")
            f.write("*Elastic\n")
            f.write(f"{_num(m['elastic_modulus'])}, {_num(m['poisson_ratio'])}\n")
            f.write("*Expansion\n{0}\n".format(_num(m["thermal_expansion"])))
            f.write("*Density\n{0}\n".format(_num(m["density"])))
        f.write("\n")

        f.write("*Part, name=Bridge\n")
        f.write("*Node\n")
        for n in model["nodes"]:
            f.write(f"{n['id']}, {_num(n['x'])}, {_num(n['y'])}, {_num(n['z'])}\n")

        for et in model["element_types"]:
            f.write(f"*Element, type={ABAQUS_ELEMENT_TYPES[et['kind']]}, elset=ET{et['id']}\n")
            for e in model["elements"]:
                if e["element_type_id"] == et["id"]:
                    f.write(f"{e['id']}, {e['start_node']}, {e['end_node']}\n")

        for et in model["element_types"]:
            rc = rc_by_id[et["real_id"]]
            if et["kind"] == "BEAM3D":
                v = rc["fields"]
                f.write(f"*Beam Section, elset=ET{et['id']}, "
                        f"material=MAT{et['material_id']}, section=GENERAL\n")
                f.write(f"{_num(v['AREA'])}, {_num(v['IZZ'])}, 0., "
                        f"{_num(v['IYY'])}, {_num(v['IXX'])}\n")
                f.write("0., 0., -1.\n")
            else:
                f.write(f"*Solid Section, elset=ET{et['id']}, "
                        f"material=MAT{et['material_id']}\n")
                f.write(f"{_num(rc['fields']['A_TRUSS'])}\n")
        f.write("*End Part\n\n")

        f.write("*Assembly, name=Assembly\n")
        f.write("*Instance, name=Bridge-1, part=Bridge\n*End Instance\n*End Assembly\n\n")

        if model["boundary_conditions"]:
            f.write("*Boundary\n")
            for bc in model["boundary_conditions"]:
                for dof in bc["dofs"]:
                    num = _DOF_NAMES.index(dof) + 1
                    f.write(f"{bc['node_id']}, {num}, {num}, {_num(bc['value'])}\n")

        g = model["loads"].get("gravity")
        if g:
            mag = math.hypot(g["gx"], g["gy"])
            if mag > 0:
                # 全体单元的重力：canonical 模型没有 elset 汇总名，逐个 elset 写。
                f.write("*Dload\n")
                for et in model["element_types"]:
                    f.write(f"ET{et['id']}, GRAV, {_num(mag)}, "
                            f"{_num(g['gx'] / mag)}, {_num(g['gy'] / mag)}, 0.\n")

        cl = model["loads"]["cloads"]
        if cl:
            f.write("*Cload\n")
            for ld in cl:
                f.write(f"{ld['node_id']}, {int(ld['dof'])}, {_num(ld['value'])}\n")

        t = model["loads"].get("uniform_temperature")
        if t is not None:
            f.write("*Temperature\n")
            for n in model["nodes"]:
                f.write(f"{n['id']}, {_num(t)}\n")


# ---------------------------------------------------------------- MIDAS Civil
def write_midas_file(model: dict[str, Any], path: str) -> None:
    """写 MIDAS Civil MCT 命令文件。

    MIDAS 的截面命名（IYY 绕 y 轴）与 FEM-Eigen 同义，故 IY→iyy、IZ→izz 直接
    同名搬用；这一点与 ANSYS 相反，不要混用两套对应关系。
    """
    _ensure_parent(path)
    gravity = 9.806
    rc_by_id = {rc["id"]: rc for rc in model["real_constants"]}

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("*VERSION\n8.8.5\n")
        f.write("*UNIT\nN, M\n")
        f.write("*STRUCTYPE\n0, 0, 9.806, 0, NO, NO\n")

        f.write("*MATERIAL\n")
        for m in model["materials"]:
            f.write(f"{m['id']}, USER, MAT{m['id']}, 0, 0, 2, "
                    f"{_num(m['elastic_modulus'])}, {_num(m['poisson_ratio'])}, "
                    f"{_num(m['thermal_expansion'])}, "
                    f"{_num(m['density'] * gravity)}\n")

        f.write("*SECTION\n")
        for rc in model["real_constants"]:
            v = rc["fields"]
            sid = rc["id"]
            if rc["kind"] == "BEAM3D":
                f.write(f"{sid}, VALUE, SEC{sid}, CC, SB, Built, 0, 0, 0, 0, 0, 0\n")
                f.write(f"{_num(v['AREA'])}, {_num(v['AREA'])}, {_num(v['AREA'])}, "
                        f"{_num(v['IXX'])}, {_num(v['IYY'])}, {_num(v['IZZ'])}\n")
            else:
                f.write(f"{sid}, VALUE, SEC{sid}, CC, SR, Built, 0, 0, 0, 0, 0, 0\n")
                f.write(f"{_num(v['A_TRUSS'])}, 0, 0, 0, 0, 0\n")
            f.write("0, 0, 0, 0, 0, 0, 0, 0, 0, 0\n")
            f.write("0, 0, 0, 0, 0, 0, 0, 0\n")

        f.write("*NODE\n")
        for n in model["nodes"]:
            f.write(f"{n['id']}, {_num(n['x'])}, {_num(n['y'])}, {_num(n['z'])}\n")

        f.write("*ELEMENT\n")
        et_by_id = {et["id"]: et for et in model["element_types"]}
        for e in model["elements"]:
            et = et_by_id[e["element_type_id"]]
            kind = MIDAS_ELEMENT_TYPES[et["kind"]]
            if kind == "TENSTR":
                f.write(f"{e['id']}, TENSTR, {et['material_id']}, {et['real_id']}, "
                        f"{e['start_node']}, {e['end_node']}, 0, 1, 0\n")
            else:
                f.write(f"{e['id']}, {kind}, {et['material_id']}, {et['real_id']}, "
                        f"{e['start_node']}, {e['end_node']}, 0, 0, 0\n")

        if model["boundary_conditions"]:
            f.write("*CONSTRAINT\n")
            for bc in model["boundary_conditions"]:
                code = "".join("1" if d in bc["dofs"] else "0" for d in _DOF_NAMES)
                f.write(f"{bc['node_id']}, {code}, \n")

        f.write("*ENDDATA\n")


def export_all(model: ModelSpec3D, out_dir: str, stem: str = "model") -> dict[str, Any]:
    """一次导出三种格式，返回 {格式: 路径} 与 warnings。"""
    canonical, warnings = to_canonical(model)
    os.makedirs(out_dir, exist_ok=True)
    paths = {
        "ansys": os.path.join(out_dir, f"{stem}.ans"),
        "abaqus": os.path.join(out_dir, f"{stem}.inp"),
        "midas": os.path.join(out_dir, f"{stem}.mct"),
    }
    write_ansys_file(canonical, paths["ansys"])
    write_abaqus_file(canonical, paths["abaqus"])
    write_midas_file(canonical, paths["midas"])
    return {"paths": paths, "warnings": warnings, "canonical": canonical}
