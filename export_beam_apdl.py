"""单梁 ModelSpec -> ANSYS APDL (.ans) 同源导出。

设计边界（与现有两条导出链正交，互不冒充）：
- 只覆盖旧契约链 `ModelSpec`（2D 单梁 / 连续梁）；
- `export_2d_formats.py` 只接受 `BridgeSpec`（平面专用平行链）；
- `export_formats.py` 只接受 `ModelSpec3D`（三维全桥型）。
三者输入契约不同，各自只导出自己能表达的几何，不伪造对方格式。

口径：
- 网格节点与截面属性复用 `fem_eigen_export.build_mesh` / `section_properties`，
  与 FEM-Eigen `.dat` 严格同源，避免“APDL 和 .dat 各算一套”；
- 单位：几何 m，力 N，弹性模量 Pa；荷载 kN / kN/m -> N / N/m，向下为负；
- 只写建模命令流（/PREP7 ... FINISH），不含求解控制，由使用方自行加
  /SOLU + SOLVE + FINISH + /POST1。
"""

from __future__ import annotations

from pathlib import Path

from fem_eigen_export import build_mesh, _fmt
from model_spec import ModelSpec
from section_lib import section_properties

__all__ = ["beam_spec_to_apdl", "export_beam_apdl"]


def _support_node(model: ModelSpec, spec_support, xs, index_of) -> int:
    pos = min(max(spec_support.position, 0.0), model.length)
    nid = index_of.get(round(pos, 9))
    if nid is None:
        nid = min(range(len(xs)), key=lambda i: abs(xs[i] - pos)) + 1
    return nid


def beam_spec_to_apdl(model: ModelSpec) -> str:
    """把单梁 ModelSpec 转成 ANSYS APDL 命令流文本。"""
    xs, index_of = build_mesh(model)
    n_node = len(xs)
    n_elem = n_node - 1
    props = section_properties(model.section)
    A, I = props["A"], props["I"]
    L = model.length

    out: list[str] = [
        "! ---------------------------------------------------------------",
        "! BridgeStudio -> ANSYS Mechanical APDL 命令流（单梁 ModelSpec）",
        f"! 模型：{model.name} | L={L:g} m | {model.n_spans} 跨",
        "! 单位：m / N / Pa；荷载 kN、kN/m -> N、N/m（向下为负）",
        "! 只含建模信息，求解控制请按需在 FINISH 后自行添加。",
        "! ---------------------------------------------------------------",
        "/PREP7",
        "ET,1,BEAM3",
        f"MP,EX,1,{_fmt(model.E)}",
        "MP,PRXY,1,0.3",
        f"MP,DENS,1,{_fmt(model.rho)}",
        f"R,1,{_fmt(A)},{_fmt(I)},{_fmt(model.section.h)}",
        "",
        "! 节点（x 沿梁轴，y 竖向）",
    ]
    for i, x in enumerate(xs, start=1):
        out.append(f"N,{i},{x:.9f},0")

    out.append("")
    out.append("! 单元")
    for i in range(1, n_elem + 1):
        out.append(f"E,{i},{i + 1}")

    out.append("")
    out.append("! 支座（pinned: UX+UY；roller: UY；fixed: UX+UY+ROTZ）")
    for s in model.supports:
        nid = _support_node(model, s, xs, index_of)
        if s.type in ("pinned", "fixed"):
            out.append(f"D,{nid},UX,0")
        out.append(f"D,{nid},UY,0")
        if s.type == "fixed":
            out.append(f"D,{nid},ROTZ,0")

    pts = [ld for ld in model.loads if ld.type == "point"]
    if pts:
        out.append("")
        out.append("! 节点集中力（kN -> N，向下为负）")
        for ld in pts:
            pos = min(max(ld.position or 0.0, 0.0), L)
            nid = index_of.get(round(pos, 9))
            if nid is None:
                nid = min(range(n_node), key=lambda i: abs(xs[i] - pos)) + 1
            out.append(f"F,{nid},FY,{_fmt(-abs(ld.value) * 1000.0)}")

    udls = [ld for ld in model.loads if ld.type == "udl"]
    if udls:
        out.append("")
        out.append("! 单元均布荷载 SFBEAM（kN/m -> N/m，向下为负）")
        for ld in udls:
            s = min(max(ld.start if ld.start is not None else 0.0, 0.0), L)
            e = min(max(ld.end if ld.end is not None else L, 0.0), L)
            if e <= s:
                continue
            i0 = index_of.get(round(s, 9))
            i1 = index_of.get(round(e, 9))
            if i0 is None or i1 is None:
                continue
            for elem in range(i0, i1):
                out.append(f"SFBEAM,{elem},1,PRES,{_fmt(-abs(ld.value) * 1000.0)}")

    out.append("")
    out.append("FINISH")
    out.append("! 如需在 ANSYS 内直接求解，接：/SOLU / SOLVE / FINISH / /POST1")
    return "\n".join(out) + "\n"


def export_beam_apdl(model: ModelSpec, path: str | Path) -> Path:
    """把单梁 ModelSpec 导出为 .ans（APDL）文件并返回路径。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(beam_spec_to_apdl(model), encoding="utf-8")
    return p
