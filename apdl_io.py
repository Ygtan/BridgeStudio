"""apdl_io.py
============
ANSYS APDL 建模命令流 **只读导入** → ``ModelSpec3D`` → FEM-Eigen ``.dat``。

范围严格限定于 ``APDL导入规格.md`` 的 a+b：

- a：几何与拓扑（`N` / `FILL` / `E` / `EN` / `ET` / `MP` / `R` / `RMORE` /
  `SECDATA` / `TYPE` / `MAT` / `REAL`）；
- b：荷载与边界（`D` / `F` / `SFBEAM` / `SFE` / `ACE`）；
- b 补充：约束方程 `CE`（Σcoef·u=b，罚函数实现，默认 PENALTY=1e12）。

不做施工阶段、求解控制、`*DO` 参数化循环。`/SOLU` 之后的一切忽略并计数。

三条铁律
--------

1. **默认零污染**：本模块只新增读入入口，不碰任何导出器、不刷新任何冻结哈希。
2. **有损必告警**：BEAM188→BEAM、LINK180→TRUSS、面荷载方向假定、α 丢失、
   翘曲自由度丢失，全部进 :class:`ImportReport.warnings`，绝不静默吞掉。
3. **不可逆必拒绝**：`BEAM3`、两坐标 `N`、建模段 `*DO`、未知建模命令、
   引用断链，全部进 :class:`ImportReport.rejected`，由
   :meth:`ApdlImportResult.raise_if_rejected` 显式失败，不给半吊子模型。

为什么只输出 ``ModelSpec3D``：2D ``ModelSpec`` 是参数化模型（总长/跨数/支座
位置/截面形状），APDL 的 `BEAM3` 节点表无法无损反推这四项，猜出来的模型看起来
能跑、实际已经不是原来那座桥。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from model_spec_3d import (
    Boundary3D,
    ConstraintEquation3D,
    ConstraintEquationTerm3D,
    ElemLoad3D,
    Element3D,
    ElemTypeDef3D,
    Material3D,
    ModelSpec3D,
    Node3D,
    NodalLoad3D,
    RealConst3D,
    validate_model_3d,
)

__all__ = [
    "ApdlImportError",
    "ImportReport",
    "ApdlImportResult",
    "parse_apdl",
    "read_apdl",
    "import_apdl_to_dat",
]


class ApdlImportError(ValueError):
    """APDL 含显式拒绝的命令或引用断链，无法生成可信模型。"""


# ANSYS DOF / 荷载分量 → ModelSpec3D 的 1..6
_DOF = {"UX": 1, "UY": 2, "UZ": 3, "ROTX": 4, "ROTY": 5, "ROTZ": 6}
_LOAD = {"FX": 1, "FY": 2, "FZ": 3, "MX": 4, "MY": 5, "MZ": 6}

# 可映射到 ModelSpec3D 的空间单元
_ET_KIND = {
    "BEAM4": "BEAM",
    "BEAM188": "BEAM",
    "LINK180": "TRUSS",
    "LINK8": "TRUSS",
    # LINK10 是 ANSYS 只受拉单元；APDL 里**索力就是用初应变 ISTRN 表达的**：
    # F = E·A·ISTRN。ISTRN 被完整读入 RealConst3D.ISTRN → .dat，内核把它转成
    # 等效节点荷载（fem_3d.hpp: P = Eeff·A·istrn），所以索力不丢。
    # 映射 TRUSS 而非 CABLE：线性内核下两者轴向刚度相同，但 CABLE 在
    # validate_model_3d 有「两端等高即硬拒绝」，会把合法的 LINK10 水平系杆/
    # 张紧杆误拒。真正丢失的只有「只受拉单向判定」和「W/L0 垂度几何非线性」，
    # 这两点靠告警显式交代，不误导用户去改 kind=CABLE。
    "LINK10": "TRUSS",
}
# 明确知道不能映射的单元（给具体理由，不落到"未知命令"的笼统提示）
_ET_REJECT = {
    "BEAM3": "平面梁（2D ModelSpec 无法由节点表反推）",
    "PLANE42": "平面实体单元，不是空间梁/杆",
    "PLANE182": "平面实体单元，不是空间梁/杆",
    "SOLID45": "三维实体单元， ModelSpec3D 只支持梁/杆/索",
    "SOLID185": "三维实体单元， ModelSpec3D 只支持梁/杆/索",
    "SHELL63": "壳单元， ModelSpec3D 只支持梁/杆/索",
    "SHELL181": "壳单元， ModelSpec3D 只支持梁/杆/索",
    "MASS21": "质量单元， ModelSpec3D 无对应类型",
}

# 段控制：永远忽略，不改变建模状态（FINISH 不切回 pre，因为导出器是
# FINISH 之后紧跟 /SOLU；若切回会把 SOLVE 误判为建模命令）
_SEGMENT = {"/PREP7", "/BATCH", "/TITLE", "/CLEAR", "FINISH"}

# 求解/后处理命令：出现在建模段时忽略（*DO 例外，规格要求拒绝）
_SOLU_CMDS = {
    "SOLVE", "ANTYPE", "TIME", "NSUBST", "KBC", "NLGEOM", "OUTRES",
    "ARCLEN", "SET", "ETABLE", "*DIM", "*DO", "*ENDDO", "*GET",
    "*CFOPEN", "*VWRITE", "*CFCLOS", "/POST1", "/EXIT", "/SOLU",
    "CNVTOL", "NEQIT", "PRESOLVE", "EQSLV",
}


# ---------------------------------------------------------------- 报告
@dataclass
class ImportReport:
    """一次导入的可审计记录。界面直接展示这三段，不需要 try/except 猜。"""

    source: str = ""
    title: str = ""
    read: dict[str, int] = field(default_factory=dict)
    ignored: dict[str, int] = field(default_factory=dict)
    rejected: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # LINK10/LINK180/LINK8 的初应变索力台账：{单元号: (A, ISTRN, E, F=E*A*ISTRN)}。
    # APDL 用 LINK 只拉杆表达索力、且索力就藏在初应变里，这里把它显式算出来，
    # 让使用者一眼核对“索力没丢”。仅统计有非零 ISTRN 的杆单元，默认空。
    cable_forces: dict[int, tuple[float, float, float, float]] = field(
        default_factory=dict)
    _warn_seen: set[str] = field(default_factory=set, repr=False)

    def bump(self, bucket: dict[str, int], cmd: str) -> None:
        bucket[cmd] = bucket.get(cmd, 0) + 1

    def reject(self, lineno: int, cmd: str, reason: str) -> None:
        self.rejected.append(f"第 {lineno} 行 {cmd or '(空命令)'}: {reason}")

    def warn(self, msg: str) -> None:
        if msg not in self._warn_seen:
            self._warn_seen.add(msg)
            self.warnings.append(msg)

    @property
    def ok(self) -> bool:
        return not self.rejected

    def text(self) -> str:
        """人可读摘要：已读入 / 已忽略 / 拒绝 / 告警。"""
        def fmt_counts(d: dict[str, int]) -> str:
            return "、".join(f"{k}×{v}" for k, v in sorted(d.items())) or "（无）"

        lines = [
            f"APDL 导入报告：{self.source or '<文本>'}",
            f"模型标题：{self.title or '（未标注）'}",
            "",
            f"[已读入] {sum(self.read.values())} 条：{fmt_counts(self.read)}",
            f"[已忽略] {sum(self.ignored.values())} 条：{fmt_counts(self.ignored)}",
            f"[显式拒绝] {len(self.rejected)} 条",
        ]
        lines += [f"  ✗ {r}" for r in self.rejected]
        lines.append(f"[告警] {len(self.warnings)} 条")
        lines += [f"  ! {w}" for w in self.warnings]
        return "\n".join(lines) + "\n"


@dataclass
class ApdlImportResult:
    """导入结果。``model`` 在 rejected 非空时**不重要**，调用方必须先看报告。"""

    model: ModelSpec3D
    report: ImportReport

    def raise_if_rejected(self) -> None:
        if self.report.rejected:
            raise ApdlImportError(
                "APDL 含无法导入的命令，未生成模型：\n"
                + "\n".join("  - " + r for r in self.report.rejected))

    def to_dat(self, compact: bool = True) -> str:
        return self.model.to_dat(compact=compact)


# ---------------------------------------------------------------- 工具
def _split(line: str) -> tuple[str, list[str]]:
    """去 `!` 注释后按逗号切分；命令名大写，参数保留原样。"""
    s = line.split("!", 1)[0].strip()
    if not s:
        return "", []
    toks = [t.strip() for t in s.split(",")]
    return toks[0].upper(), toks[1:]


def _f(tok: str) -> float | None:
    try:
        return float(tok)
    except (TypeError, ValueError):
        return None


def _i(tok: str) -> int | None:
    try:
        return int(tok)
    except (TypeError, ValueError):
        return None


def _pair_list(args: list[str], names: dict[str, int], report: ImportReport,
               lineno: int, cmd: str) -> list[tuple[int, float]]:
    """解析 `D,node,UX,0,UY,0` 这种 (dof, value) 交替序列。"""
    out: list[tuple[int, float]] = []
    i = 0
    while i < len(args):
        key = args[i].upper()
        if key == "ALL":
            dofs = [1, 2, 3, 4, 5, 6]
            i += 1
            val_tok = args[i] if i < len(args) else "0"
            i += 1
        elif key in names:
            dofs = [names[key]]
            i += 1
            val_tok = args[i] if i < len(args) else "0"
            i += 1
        else:
            report.reject(lineno, cmd, f"未知自由度/分量 {key!r}")
            break
        val = _f(val_tok)
        if val is None:
            report.reject(lineno, cmd, f"自由度 {key} 的值不是数字：{val_tok!r}")
            break
        out.extend((d, val) for d in dofs)
    return out


def _pair_ce(args: list[str], report: ImportReport, lineno: int,
             cmd: str) -> tuple[list[tuple[int, int, float]], bool]:
    """解析 `CE,id,b,n1,UX,c1,n2,UY,c2,...` 的 (node, dof, coef) 三元组序列。

    返回 (terms, ok)。ANSYS 自由度名经 :data:`_DOF` 映射到 1~6。任一项
    非法都写进 report.rejected 并把 ok 置 False，调用方据此丢弃整条 CE。
    """
    out: list[tuple[int, int, float]] = []
    i = 0
    ok = True
    while i < len(args):
        node = _i(args[i]); i += 1
        if i >= len(args):
            report.reject(lineno, cmd, "CE 项缺少自由度名")
            ok = False
            break
        dof_name = args[i].upper(); i += 1
        if i >= len(args):
            report.reject(lineno, cmd, "CE 项缺少系数")
            ok = False
            break
        coef = _f(args[i]); i += 1
        if node is None:
            report.reject(lineno, cmd, f"CE 项节点号非法：{args[i-3]!r}")
            ok = False
            continue
        if dof_name not in _DOF:
            report.reject(lineno, cmd, f"CE 自由度 {dof_name!r} 不支持（应 UX/UY/UZ/ROTX/ROTY/ROTZ）")
            ok = False
            continue
        if coef is None:
            report.reject(lineno, cmd, f"CE 系数不是数字：{args[i-1]!r}")
            ok = False
            continue
        out.append((node, _DOF[dof_name], coef))
    if not out:
        ok = False
    return out, ok


def _merge_bounds(pairs: list[tuple[int, int, float]]) -> list[Boundary3D]:
    """同节点同值 → 连续 dof 合并成一条 Boundary3D（与 .dat 写法一致）。"""
    by_node_value: dict[tuple[int, float], list[int]] = {}
    for node, dof, value in pairs:
        by_node_value.setdefault((node, value), []).append(dof)
    out: list[Boundary3D] = []
    for (node, value), dofs in sorted(by_node_value.items()):
        ds = sorted(set(dofs))
        start = prev = ds[0]
        for d in ds[1:] + [-1]:
            if d != prev + 1:
                out.append(Boundary3D(node=node, dofs=(start, prev), value=value))
                start = d
            prev = d
    return out


# ---------------------------------------------------------------- 主解析
def parse_apdl(text: str, *, source: str = "<text>") -> ApdlImportResult:
    """把 APDL 建模文本解析成 :class:`ApdlImportResult`。

    不抛异常（除内部 bug）；所有问题进 ``report.rejected``，由调用方决定
    是否 :meth:`ApdlImportResult.raise_if_rejected`。
    """
    rep = ImportReport(source=source)

    # ---- 原始收集（命令阶段只做语法解析，语义绑定留到 post） ----
    mat_raw: dict[int, dict[str, float]] = {}
    rc_raw: dict[int, list[float]] = {}
    rmore_raw: dict[int, list[float]] = {}
    sec_raw: dict[int, list[float]] = {}
    et_raw: dict[int, dict[str, Any]] = {}
    nodes: dict[int, Node3D] = {}
    # eid -> {"t": etid|None, "m": mid|None, "r": rid|None, "s": secid|None,
    #         "n1": int, "n2": int}
    elems: dict[int, dict[str, Any]] = {}
    fills: list[tuple[int, int, int, int, int]] = []
    bounds_raw: list[tuple[int, int, float]] = []
    loads_raw: list[tuple[int, int, float]] = []
    elem_loads_raw: list[tuple[int, float]] = []
    gravity: tuple[float, float, float] = (0.0, 0.0, 0.0)
    # CE,id,b,n1,dof1,c1,n2,dof2,c2,... → (id, b, [(node,dof,coef),...])
    ce_raw: list[tuple[int, float, list[tuple[int, int, float]]]] = []

    cur: dict[str, int | None] = {"type": None, "mat": None,
                                  "real": None, "sec": None}
    last_rc: int | None = None
    cur_sec: int | None = None
    phase = "prep7"          # prep7 | solu；初始即建模，便于读裸命令流

    for lineno, raw in enumerate(text.splitlines(), start=1):
        cmd, a = _split(raw)
        if not cmd:
            continue

        # --- 段切换 ---
        if cmd == "/PREP7":
            phase = "prep7"
            rep.bump(rep.ignored, cmd)
            continue
        if cmd == "/SOLU":
            phase = "solu"
            rep.bump(rep.ignored, cmd)
            continue
        if cmd in _SEGMENT:
            rep.bump(rep.ignored, cmd)
            continue
        if cmd.startswith("/"):
            rep.bump(rep.ignored, cmd)
            continue
        if phase == "solu":
            rep.bump(rep.ignored, cmd)
            continue
        if cmd in _SOLU_CMDS:
            if cmd in ("*DO", "*ENDDO"):
                rep.reject(lineno, cmd, "建模段不支持 *DO 参数化循环（用户未选 c）")
            else:
                rep.bump(rep.ignored, cmd)
            continue

        # --- ET：单元类型 ---
        if cmd == "ET":
            etid = _i(a[0]) if a else None
            name = a[1].upper() if len(a) > 1 else ""
            if etid is None or not name:
                rep.reject(lineno, cmd, "ET 需要 id 与单元名")
            elif name in _ET_REJECT:
                rep.reject(lineno, cmd,
                           f"单元 {name} 不能映射：{_ET_REJECT[name]}")
            elif name not in _ET_KIND:
                rep.reject(lineno, cmd, f"未知空间单元类型 {name!r}")
            else:
                et_raw[etid] = {"name": name, "kind": _ET_KIND[name]}
                rep.bump(rep.read, cmd)
                if name == "BEAM188":
                    rep.warn(
                        "BEAM188 按 6 自由度 Euler-Bernoulli BEAM 导入："
                        "FEM-Eigen 3D 与 ANSYS Timoshenko 形函数不同，"
                        "位移/端力会有本征差异，不做数值抹平。")
                elif name == "LINK10":
                    rep.warn(
                        "LINK10 是 APDL 只受拉单元，索力用初应变 ISTRN 表达"
                        "（F=E·A·ISTRN）；R 的 ISTRN 已完整导入 RealConst3D.ISTRN，"
                        "内核按等效节点荷载施加，**索力不丢**。映射为 TRUSS 不是"
                        " CABLE：线性内核两者轴向刚度相同，而 CABLE 的「两端等高"
                        "即硬拒绝」会把合法的水平系杆/张紧杆误拒。真正有损的只有"
                        "两点：(1) 只受拉单向判定——线性内核按双向轴力杆，受压时不退出；"
                        "(2) W/L0 垂度几何非线性——需要时请补 W 并走 nonlinear "
                        "cable_solver。索力台账见导入报告中的 cable_forces。")
                elif name in ("LINK180", "LINK8"):
                    rep.warn(
                        f"{name} 按 TRUSS 导入；若用初应变 ISTRN 表达索力"
                        "（F=E·A·ISTRN），ISTRN 已完整导入、内核按等效节点荷载"
                        "施加，索力不丢（见 cable_forces）。有损项：只受拉判定、"
                        "W/L0 垂度几何非线性（需要时补 W 走 nonlinear cable_solver）。")
            continue

        # --- MP：材料 ---
        if cmd == "MP":
            if len(a) < 3:
                rep.reject(lineno, cmd, "MP 需要 prop, mat, value")
                continue
            prop = a[0].upper()
            mid = _i(a[1])
            val = _f(a[2])
            if mid is None or val is None:
                rep.reject(lineno, cmd, "MP 的 mat/value 非法")
            elif prop == "ALPX":
                rep.bump(rep.ignored, cmd)
                if val:
                    rep.warn(
                        f"材料 {mid} 的线膨胀系数 ALPX={val:g} 未导入："
                        "ModelSpec3D 温度荷载走 *TEMPERATURE 直接给温变。")
            elif prop in ("EX", "DENS", "PRXY", "GXY"):
                mat_raw.setdefault(mid, {})[prop] = val
                rep.bump(rep.read, cmd)
            else:
                rep.bump(rep.ignored, cmd)
                rep.warn(f"MP,{prop} 未导入（{prop} 不在 a+b 范围）")
            continue

        # --- R / RMORE：实常数 ---
        if cmd == "R":
            rid = _i(a[0]) if a else None
            vals = [_f(t) for t in a[1:]]
            if rid is None or any(v is None for v in vals):
                rep.reject(lineno, cmd, "R 的 id/字段非法")
            else:
                rc_raw[rid] = [float(v) for v in vals]   # type: ignore[arg-type]
                last_rc = rid
                rep.bump(rep.read, cmd)
            continue
        if cmd == "RMORE":
            if last_rc is None:
                rep.reject(lineno, cmd, "RMORE 前没有 R 命令")
                continue
            vals = [_f(t) for t in a]
            if any(v is None for v in vals):
                rep.reject(lineno, cmd, "RMORE 字段非法")
            else:
                rmore_raw[last_rc] = [float(v) for v in vals]  # type: ignore[arg-type]
                rep.bump(rep.read, cmd)
            continue

        # --- SECTYPE / SECDATA / SECNUM：BEAM188 ASEC ---
        if cmd == "SECTYPE":
            cur_sec = _i(a[0]) if a else None
            cur["sec"] = cur_sec
            rep.bump(rep.ignored, cmd)
            continue
        if cmd == "SECDATA":
            secid = cur_sec if cur_sec is not None else 1
            vals = [_f(t) for t in a]
            if any(v is None for v in vals):
                rep.reject(lineno, cmd, "SECDATA 字段非法")
            else:
                sec_raw[secid] = [float(v) for v in vals]  # type: ignore[arg-type]
                cur["sec"] = secid
                rep.bump(rep.read, cmd)
            continue
        if cmd == "SECNUM":
            cur_sec = _i(a[0]) if a else None
            cur["sec"] = cur_sec
            rep.bump(rep.ignored, cmd)
            continue
        if cmd == "KEYOPT":
            rep.bump(rep.ignored, cmd)
            rep.warn("KEYOPT 未导入：翘曲/截面选项在 6 自由度梁中无处安放，按默认处理。")
            continue

        # --- TYPE / MAT / REAL：三元组状态机 ---
        if cmd in ("TYPE", "MAT", "REAL"):
            key = cmd.lower()
            v = _i(a[0]) if a else None
            if v is None:
                rep.reject(lineno, cmd, f"{cmd} 的编号非法")
            else:
                cur[key] = v
                rep.bump(rep.read, cmd)
            continue

        # --- N / FILL：节点 ---
        if cmd == "N":
            nid = _i(a[0]) if a else None
            if nid is None or len(a) < 3:
                rep.reject(lineno, cmd, "N 需要 id 与三维坐标")
                continue
            if len(a) < 4:
                rep.reject(lineno, cmd,
                           f"节点 {nid} 只有两坐标（平面模型）："
                           "2D ModelSpec 无法由 APDL 节点表无损反推")
                continue
            x, y, z = _f(a[1]), _f(a[2]), _f(a[3])
            if None in (x, y, z):
                rep.reject(lineno, cmd, f"节点 {nid} 坐标非法")
            elif nid in nodes:
                rep.reject(lineno, cmd, f"节点 {nid} 重复定义")
            else:
                nodes[nid] = Node3D(id=nid, x=float(x), y=float(y), z=float(z))  # type: ignore[arg-type]
                rep.bump(rep.read, cmd)
            continue
        if cmd == "FILL":
            if len(a) < 4:
                rep.reject(lineno, cmd, "FILL 需要 n1,n2,nfill[,nstrt,ninc]")
                continue
            n1, n2, nf = _i(a[0]), _i(a[1]), _i(a[2])
            ns = _i(a[3]) if len(a) > 3 else (n1 + 1 if n1 is not None else None)
            ni = _i(a[4]) if len(a) > 4 else 1
            if None in (n1, n2, nf, ns, ni) or nf <= 0 or ni <= 0:  # type: ignore[operator]
                rep.reject(lineno, cmd, "FILL 参数非法")
            else:
                fills.append((int(n1), int(n2), int(nf), int(ns), int(ni)))  # type: ignore[arg-type]
                rep.bump(rep.read, cmd)
            continue

        # --- E / EN：单元 ---
        if cmd in ("E", "EN"):
            if cmd == "E":
                if len(a) < 2:
                    rep.reject(lineno, cmd, "E 需要 n1,n2")
                    continue
                eid = max(elems, default=0) + 1
                n1, n2 = _i(a[0]), _i(a[1])
            else:
                if len(a) < 3:
                    rep.reject(lineno, cmd, "EN 需要 eid,n1,n2")
                    continue
                eid, n1, n2 = _i(a[0]), _i(a[1]), _i(a[2])
            if None in (eid, n1, n2):
                rep.reject(lineno, cmd, "单元编号/端点非法")
            elif eid in elems:
                rep.reject(lineno, cmd, f"单元 {eid} 重复定义")
            elif cur["type"] is None:
                rep.reject(lineno, cmd, f"单元 {eid} 定义前没有 TYPE 命令")
            else:
                elems[int(eid)] = {  # type: ignore[arg-type]
                    "t": cur["type"], "m": cur["mat"], "r": cur["real"],
                    "s": cur["sec"], "n1": int(n1), "n2": int(n2),  # type: ignore[arg-type]
                }
                rep.bump(rep.read, cmd)
            continue

        # --- D / F：边界与节点荷载 ---
        if cmd == "D":
            if not a:
                rep.reject(lineno, cmd, "D 缺少节点号")
                continue
            node = _i(a[0])
            if node is None:
                rep.reject(lineno, cmd, "D 的节点号非法")
                continue
            for dof, val in _pair_list(a[1:], _DOF, rep, lineno, cmd):
                bounds_raw.append((node, dof, val))
            rep.bump(rep.read, cmd)
            continue
        if cmd == "F":
            if not a:
                rep.reject(lineno, cmd, "F 缺少节点号")
                continue
            node = _i(a[0])
            if node is None:
                rep.reject(lineno, cmd, "F 的节点号非法")
                continue
            for dof, val in _pair_list(a[1:], _LOAD, rep, lineno, cmd):
                loads_raw.append((node, dof, val))
            rep.bump(rep.read, cmd)
            continue

        # --- SFBEAM / SFE：面荷载（有损） ---
        if cmd in ("SFBEAM", "SFE"):
            if len(a) < 4:
                rep.reject(lineno, cmd, "需要 elem,face,PRES,value")
                continue
            eid, face = _i(a[0]), _i(a[1])
            val = _f(a[3])
            if eid is None or face is None or val is None:
                rep.reject(lineno, cmd, "面荷载参数非法")
            else:
                elem_loads_raw.append((eid, val))
                rep.bump(rep.read, cmd)
                rep.warn(
                    f"{cmd} 单元 {eid} 面 {face} 按 GZ 线荷载读入："
                    "面号到全局方向的换算依赖单元局部坐标，命令流未给方向向量；"
                    "强度按 1:1 使用（未乘梁宽）。请核对方向与量纲。")
            continue

        # --- CE：约束方程（Σ coef·u(node,dof) = b；罚函数实现）---
        if cmd == "CE":
            if len(a) < 3:
                rep.reject(lineno, cmd, "CE 至少需要 id, b 与一项 node,dof,coef")
                continue
            ce_id = _i(a[0])
            b = _f(a[1])
            if ce_id is None or b is None:
                rep.reject(lineno, cmd, "CE 的 id 或常数 b 非法")
                continue
            terms, ok = _pair_ce(a[2:], rep, lineno, cmd)
            if ok:
                ce_raw.append((ce_id, b, terms))
                rep.bump(rep.read, cmd)
            continue

        # --- ACE：重力 ---
        if cmd == "ACE":
            vals = [_f(t) for t in a[:3]]
            if len(vals) < 3 or any(v is None for v in vals):
                rep.reject(lineno, cmd, "ACE 需要 ax,ay,az 三个分量")
            else:
                gravity = (float(vals[0]), float(vals[1]), float(vals[2]))  # type: ignore[arg-type]
                rep.bump(rep.read, cmd)
            continue

        # --- 建模段未知命令：显式拒绝 ---
        rep.reject(lineno, cmd,
                   f"建模段未知命令 {cmd}（不在 a+b 白名单/忽略清单中）")

    # ---------------------------------------------------------- post 绑定
    # 1) FILL：端点必须已定义，生成的节点号不得撞车
    for n1, n2, nf, ns, ni in fills:
        if n1 not in nodes or n2 not in nodes:
            rep.rejected.append(f"FILL 端点 {n1}/{n2} 未定义，无法插值")
            continue
        pa, pb = nodes[n1], nodes[n2]
        for k in range(nf):
            nid = ns + k * ni
            t = (k + 1) / (nf + 1)
            if nid in nodes:
                rep.rejected.append(f"FILL 生成的节点 {nid} 与已有节点冲突")
                continue
            nodes[nid] = Node3D(
                id=nid,
                x=pa.x + t * (pb.x - pa.x),
                y=pa.y + t * (pb.y - pa.y),
                z=pa.z + t * (pb.z - pa.z),
            )

    # 2) 单元三元组：同一 etype 只允许一组 (mat, real, sec)
    et_bind: dict[int, dict[str, int | None]] = {}
    sec_by_real: dict[int, int | None] = {}
    mat_by_real: dict[int, int | None] = {}
    for eid, e in elems.items():
        b = et_bind.setdefault(e["t"], {"m": e["m"], "r": e["r"], "s": e["s"]})
        if (b["m"], b["r"], b["s"]) != (e["m"], e["r"], e["s"]):
            rep.rejected.append(
                f"单元 {eid} 的 TYPE={e['t']} 使用了不同的 MAT/REAL 组合 "
                f"({e['m']},{e['r']},{e['s']}) ≠ ({b['m']},{b['r']},{b['s']})；"
                "ElemTypeDef3D 只能绑定一组")
        rid = int(e["r"] or 1)
        sec_by_real.setdefault(rid, e["s"])
        mat_by_real.setdefault(rid, e["m"])

    # 3) 材料
    materials: list[Material3D] = []
    mat_g: dict[int, float] = {}
    for mid in sorted(mat_raw):
        d = mat_raw[mid]
        E = d.get("EX")
        if E is None or E <= 0:
            rep.rejected.append(f"材料 {mid} 缺少有效的 EX（弹性模量）")
            continue
        nu = d.get("PRXY")
        gxy = d.get("GXY")
        if gxy and gxy > 0:
            G = gxy
        elif nu is not None and -1.0 < nu < 0.5:
            G = E / (2.0 * (1.0 + nu))
        else:
            G = 0.0
            rep.warn(f"材料 {mid} 无 GXY 也无合法 PRXY，剪切模量记为 0")
        mat_g[mid] = G
        materials.append(Material3D(
            id=mid, E=E, dens=d.get("DENS", 0.0), grade=""))

    # 4) 实常数：字段解释由引用它的 et.kind 决定，不靠列位置猜
    reals: list[RealConst3D] = []
    kinds_by_real: dict[int, set[str]] = {}
    for tid, t in et_raw.items():
        rid = et_bind.get(tid, {}).get("r") or 1
        kinds_by_real.setdefault(int(rid), set()).add(t["kind"])

    for rid in sorted(kinds_by_real):
        kinds = kinds_by_real[rid]
        if len(kinds) > 1:
            rep.rejected.append(
                f"实常数 {rid} 同时被 {sorted(kinds)} 引用；"
                "梁与索不能共用一份实常数")
            continue
        kind = next(iter(kinds))
        rvals = rc_raw.get(rid, [])
        more = rmore_raw.get(rid, [])
        A = rvals[0] if len(rvals) > 0 else 0.0
        ISTRN = 0.0
        IY = IZ = J = IW = ADDMASS = 0.0

        if kind == "BEAM":
            sec_id = sec_by_real.get(rid)
            if sec_id is not None and sec_id in sec_raw:
                # SECDATA: A, IYY, IYZ, IZZ, IW, J, CGY, CGZ, SHY, SHZ
                s = sec_raw[sec_id]
                A = s[0] if len(s) > 0 else A
                IY = s[1] if len(s) > 1 else 0.0
                IZ = s[3] if len(s) > 3 else 0.0
                IW = s[4] if len(s) > 4 else 0.0
                J = s[5] if len(s) > 5 else 0.0
                ISTRN = more[0] if more else 0.0
            else:
                # BEAM4: R,A,IZZ,IYY,TKZ,TKY,THETA ; RMORE,ISTRN,IXX,...
                if len(rvals) < 3:
                    rep.rejected.append(
                        f"实常数 {rid} 的 BEAM4 R 字段不足 3 个（A,IZZ,IYY）")
                    continue
                A, IZ, IY = rvals[0], rvals[1], rvals[2]
                ISTRN = more[0] if len(more) > 0 else 0.0
                J = more[1] if len(more) > 1 else 0.0
                ADDMASS = more[5] if len(more) > 5 else 0.0
            if J <= 0:
                rep.rejected.append(
                    f"实常数 {rid} 的扭转常数 J=IXX 缺失或非正（{J:g}），"
                    "FEM-Eigen 3D 梁需非零扭转刚度")
                continue
            if IY <= 0:
                rep.rejected.append(f"实常数 {rid} 的竖向惯性矩 IY 非正（{IY:g}）")
                continue
        else:
            # LINK180: R,A,ISTRN（或 ISTRN 在 RMORE 首位）
            ISTRN = (rvals[1] if len(rvals) > 1
                     else (more[0] if more else 0.0))

        # G 按 etype 绑定的材料传播到实常数
        mid = mat_by_real.get(rid)
        G = mat_g.get(mid, 0.0) if mid is not None else 0.0
        if kind == "BEAM" and G <= 0:
            rep.warn(f"实常数 {rid} 的剪切模量 G=0（材料未给 GXY/PRXY）")

        reals.append(RealConst3D(
            id=rid, A=A, IY=IY, IZ=IZ, J=J, G=G, IW=IW,
            ISTRN=ISTRN, ADDMASS=ADDMASS))

    # 5) 单元类型
    etypes: list[ElemTypeDef3D] = []
    for tid in sorted(et_raw):
        t = et_raw[tid]
        b = et_bind.get(tid, {})
        mid = b.get("m")
        rid = b.get("r")
        if mid is None or not any(m.id == mid for m in materials):
            rep.rejected.append(f"单元类型 {tid} 未绑定有效材料 MAT={mid}")
            continue
        if not any(r.id == (rid or 1) for r in reals):
            rep.rejected.append(f"单元类型 {tid} 未绑定有效实常数 REAL={rid}")
            continue
        etypes.append(ElemTypeDef3D(
            id=tid, kind=t["kind"], mat=int(mid), real=int(rid or 1)))

    # 6) 单元 / 边界 / 荷载
    node_ids = set(nodes)
    elements: list[Element3D] = []
    for eid in sorted(elems):
        e = elems[eid]
        if e["n1"] not in node_ids or e["n2"] not in node_ids:
            rep.rejected.append(
                f"单元 {eid} 引用了未定义的节点 {e['n1']}/{e['n2']}")
        elif any(t.id == e["t"] for t in etypes):
            elements.append(Element3D(
                id=eid, etype=int(e["t"]), n1=e["n1"], n2=e["n2"]))
        else:
            rep.rejected.append(f"单元 {eid} 引用了未被接受的 TYPE={e['t']}")

    elem_ids = {e.id for e in elements}
    for node, _dof, _v in bounds_raw:
        if node not in node_ids:
            rep.rejected.append(f"约束引用了未定义的节点 {node}")
    for node, _dof, _v in loads_raw:
        if node not in node_ids:
            rep.rejected.append(f"节点荷载引用了未定义的节点 {node}")
    for eid, _v in elem_loads_raw:
        if eid not in elem_ids:
            rep.rejected.append(f"面荷载引用了未定义的单元 {eid}")
    # CE 引用节点必须存在；重复 id 只保留第一条并告警。
    ces: list[ConstraintEquation3D] = []
    ce_ids_seen: set[int] = set()
    for cid, cb, cterms in ce_raw:
        bad = [n for n, _d, _c in cterms if n not in node_ids]
        if bad:
            rep.rejected.append(f"CE {cid} 引用了未定义的节点 {sorted(set(bad))}")
            continue
        if cid in ce_ids_seen:
            rep.warn(f"CE 编号 {cid} 重复，只保留第一条；请检查 APDL 编号")
            continue
        ce_ids_seen.add(cid)
        ces.append(ConstraintEquation3D(
            id=cid,
            constant=cb,
            terms=[ConstraintEquationTerm3D(node=n, dof=d, coef=c)
                   for n, d, c in cterms],
        ))
    if ces:
        rep.warn(
            f"已读入 {len(ces)} 条 APDL CE（约束方程），用罚函数实现"
            "（PENALTY 默认 1e12，可由 *CE_PARAMETER 调整）："
            "结果在罚容差内满足 Σcoef·u=b。APDL 的 CE 无罚系数概念，"
            "罚值过大会影响条件数、过小会放宽约束，请按结构刚度量级核对。")

    # 悬空引用已从各列表剔除（上面已记入 rejected），避免 pydantic 抛异常；
    # parse_apdl 的契约是"始终返回报告，由调用方显式拒绝"。
    boundaries = _merge_bounds(
        [(n, d, v) for n, d, v in bounds_raw if n in node_ids])
    loads = [NodalLoad3D(node=n, dof=d, value=v)
             for n, d, v in loads_raw if n in node_ids]
    elem_loads = [ElemLoad3D(e_from=e, e_to=e, dir="GZ", q1=v, q2=v)
                  for e, v in elem_loads_raw if e in elem_ids]

    try:
        model = ModelSpec3D(
            title=rep.title or "APDL 导入模型",
            nodes=sorted(nodes.values(), key=lambda n: n.id),
            materials=materials,
            reals=reals,
            etypes=etypes,
            elements=elements,
            boundaries=boundaries,
            loads=loads,
            elem_loads=elem_loads,
            gravity=gravity,
            ces=ces,
        )
    except ValidationError as exc:
        # 兜底：即使契约层仍有残留问题，也返回空模型 + 完整报告，
        # 让界面能显示"为什么不能导入"，而不是抛一个堆栈给用户。
        rep.rejected.append(f"模型契约校验失败：{exc}")
        model = ModelSpec3D(title=rep.title or "APDL 导入模型")

    errs, warns = validate_model_3d(model)
    rep.rejected.extend(f"模型校验：{e}" for e in errs)
    for w in warns:
        rep.warn("模型校验：" + w)

    # LINK 只拉杆的索力台账：APDL 的 LINK10 用初应变 ISTRN 表达索力
    # F = E·A·ISTRN。这里把每个桁架/索单元（有非零 ISTRN）的索力反算出来，
    # 让使用者一眼核对“索力没丢”。仅登记、不改模型，默认零污染。
    if model.etypes and model.reals and model.materials:
        _etype_by_id = {t.id: t for t in model.etypes}
        _real_by_id = {r.id: r for r in model.reals}
        _mat_by_id = {mm.id: mm for mm in model.materials}
        for el in model.elements:
            et = _etype_by_id.get(el.etype)
            if et is None or et.kind not in ("TRUSS", "CABLE"):
                continue
            rconst = _real_by_id.get(et.real)
            mat = _mat_by_id.get(et.mat)
            if rconst is None or mat is None:
                continue
            if not rconst.ISTRN:
                continue
            rep.cable_forces[el.id] = (
                rconst.A, rconst.ISTRN, mat.E, mat.E * rconst.A * rconst.ISTRN)

    return ApdlImportResult(model=model, report=rep)


def read_apdl(path: str | Path) -> ApdlImportResult:
    """从文件读入 APDL。编码错误按 replace 处理（注释里的中文不影响解析）。"""
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    result = parse_apdl(text, source=str(p))
    if not result.report.title:
        # /TITLE,xxx - BEAM4 里的标题（去掉单元后缀）
        for line in text.splitlines()[:20]:
            cmd, a = _split(line)
            if cmd == "/TITLE" and a:
                result.report.title = a[0].split(" - ")[0].strip()
                result.model.title = result.report.title
                break
    return result


def import_apdl_to_dat(
    path: str | Path,
    out_path: str | Path | None = None,
    *,
    compact: bool = True,
) -> tuple[ModelSpec3D, ImportReport, str]:
    """一步到位：读 APDL → 显式拒绝检查 → 写 ``.dat`` → 返回三者。

    ``out_path`` 为 ``None`` 时不落盘，只返回 ``dat`` 文本（界面预览用）。
    """
    result = read_apdl(path)
    result.raise_if_rejected()
    dat = result.to_dat(compact=compact)
    if out_path is not None:
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(dat, encoding="utf-8")
    return result.model, result.report, dat
