"""solver_3d.py —— 三维内核注册表：**一个入口，三个内核，一种返回**。

为什么需要注册表
----------------
接入之前，界面里"用哪个内核"是硬编码的：⑤ 写死 `example_mixed.exe`，
⑥ 写死 `example_3d_grillage.exe`，⑦ 写死 `cable_solver.exe`。于是每加一个
内核就要在界面上再抄一遍"导出 dat → 起进程 → 解析分段文本 → 攒字典"这套
脚手架，连错误提示都要各写一份（"未找到 xxx.exe，请编译 …"）。

现在这张表是唯一的映射：

    模型自身的能力 ──► 内核键 ──► SolverSpec ──► SolveResult

**内核键不靠界面猜，靠模型自述。** `detect_kind()` 只看一条判据：
模型带了 `cable_bridge` 工况段，就说明它要的是几何非线性成桥态求解；
否则就是线性阶段 + 活载包络。这与 `to_dat()` 里的 CABLE 导出分叉是同一条
判据——模型已经说了自己是谁，界面没必要再问一遍。

三条铁律
--------
1. **不可用就直说**：内核缺失时抛 `SolverUnavailable`，消息里带编译命令，
   不返回一个字段空空的 SolveResult 让人以为"算完了但没结果"。
2. **dat 从盘上回读**：`dat_text` 是求解器真正读到的字节，不是 Python 又
   算了一遍的结果。这样"结果过期判定"和"求解实际输入"永远是同一份文本。
3. **内核没算的就不装**：成桥态内核不输出梁内力，`beam` 就留空，并在
   `diagnostics` 里写明原因。空字典是诚实的，假数据不是。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from result_3d import KIND_LABELS, SolveResult


class SolverUnavailable(RuntimeError):
    """内核不可用。消息里必须带"怎么装回来"，否则等于没说。"""


@dataclass(frozen=True)
class SolverSpec:
    """一个内核的全部元数据 + 两个钩子。

    `run`    : (model, out_dir, dat_name) -> 内核原始解析字典
    `adapt`  : (model, raw, res) -> None，把原始字典填进 SolveResult
    """

    key: str
    exe_name: str
    available: Callable[[], bool]
    find: Callable[[], Path]
    run: Callable[[Any, Path, str], dict]
    adapt: Callable[[Any, dict, SolveResult], None]
    build_hint: str

    @property
    def label(self) -> str:
        return KIND_LABELS.get(self.key, self.key)


# ---------------------------------------------------------------- 适配器


def _dofs(model: Any) -> int:
    """模型自由度：6×节点数 减 被约束的自由度个数。"""
    try:
        return 6 * len(model.nodes) - sum(len(b.dofs) for b in model.boundaries)
    except AttributeError:
        return 0


def _adapt_grillage(model: Any, raw: dict, res: SolveResult) -> None:
    res.disp = dict(raw.get("disp") or {})
    res.beam = {int(k): dict(v) for k, v in (raw.get("beam") or {}).items()}
    res.live = {int(k): tuple(v) for k, v in (raw.get("live") or {}).items()}
    res.final_beam = {(int(e), str(d)): dict(v)
                      for (e, d), v in (raw.get("final_beam") or {}).items()}
    res.final_disp = {(int(n), int(d)): float(u)
                      for (n, d), u in (raw.get("final_disp") or {}).items()}
    res.live_beam = {(int(e), str(d)): dict(v)
                     for (e, d), v in (raw.get("live_beam") or {}).items()}
    res.stage_cables = {(int(s), int(e)): dict(v)
                        for (s, e), v in (raw.get("stage_cables") or {}).items()}
    st = model.stats() if hasattr(model, "stats") else {}
    res.stats = {**st, "dofs": _dofs(model)}
    res.converged = True                       # 线性求解，无迭代
    res.iterations = None
    res.result_path = str(raw.get("summary") or "")
    res.diagnostics.append(
        "线性阶段分析：不考虑几何非线性、索垂度与只受拉。"
        "本内核输出阶段位移/内力包络与活载包络。")
    if res.final_beam:
        res.diagnostics.append(
            "荷载组合取「成桥态」单值（各施工阶段内力累加），不取阶段包络："
            "阶段包络里含未浇筑完成的悬臂临时内力，与运营期活载不属同一构型。")
    if not res.live_beam:
        res.diagnostics.append(
            "模型未定义车道荷载（LANE_3D），无活载内力，荷载组合只出恒载项。")
    if any(t.kind == "CABLE" for t in getattr(model, "etypes", [])):
        res.diagnostics.append(
            "模型含 CABLE 单元，但线性内核把它们当 TRUSS 处理（可压）。"
            "要按只受拉 + 垂度算成桥态，请走 cable_stayed 内核。")



def _adapt_cable(model: Any, raw: dict, res: SolveResult) -> None:
    meta = dict(raw.get("meta") or {})
    res.meta = meta
    res.converged = bool(raw.get("converged"))
    res.iterations = int(meta.get("iterations", 0)) if meta.get("iterations") is not None else None
    res.elapsed_ms = float(meta["elapsed_ms"]) if meta.get("elapsed_ms") is not None else None
    res.stats = {
        "nodes": int(meta.get("nodes", 0)),
        "elements": int(meta.get("elements", 0)),
        "dofs": int(meta.get("dofs", 0)),
        "cables": int(meta.get("cables", 0)),
        "stages": 0,
    }
    res.cables = list(raw.get("cables") or [])
    res.camber = dict(raw.get("camber") or {})
    res.erected = dict(raw.get("erected") or {})
    # 梁端内力：索承体系的应力验算靠它。旧内核没有这一段，缺失时留空，
    # 由 result_3d 明说"没做"而不是报一个假的利用率。
    res.girder_ends = {
        (int(g["elem"]), int(g["end"])):
            {"N": float(g["N"]), "My": float(g["My"]), "Vz": float(g["Vz"])}
        for g in (raw.get("girder_ends") or [])}
    # 成桥态是"一个确定状态"，但对外仍按包络给：一个状态的包络就是 (v, v)。
    res.disp = {(int(n), int(d)): (float(v), float(v))
                for (n, d), v in (raw.get("u") or {}).items()}
    res.result_path = str(raw.get("result") or "")
    res.stdout = str(raw.get("stdout") or "")
    res.diagnostics.append(
        "几何非线性成桥态：共转列式 + 索垂度 + 索长反算，输出下料索长与制造线形。")
    res.diagnostics.append(
        "梁端内力（[GIRDEREND]）是成桥态恒载标准值，用于截面应力验算；"
        "本内核不含活载，故没有梁内力包络——包络须走 grillage3d 内核。")


def _adapt_suspension(model: Any, raw: dict, res: SolveResult) -> None:
    """悬索桥成桥态：复用斜拉桥的适配，再补上悬索桥专有的四组数据。

    复用是有意的：`.dat` 是同一个、`cable_solver.exe` 是同一个、成桥态语义
    也一样（下料长度 + 制造线形 + 成桥位移）。真正的差别只在**多出来什么**：
    主缆是一条连续索，所以要分段给出索力链；吊索是恒载的直接承担者；加劲梁
    在恒载下本该几乎不受力——这三张表就是悬索桥计算书的主干。
    """
    _adapt_cable(model, raw, res)
    res.main_cable = list(raw.get("main_cable") or [])
    res.hangers = list(raw.get("hangers") or [])
    res.girder = list(raw.get("girder") or [])
    res.reaction = dict(raw.get("reaction") or {})
    res.cable_shape = {int(k): tuple(v)
                       for k, v in (raw.get("cable_shape") or {}).items()}
    sys_name = str(res.meta.get("susp_system") or "EARTH").upper()
    res.diagnostics.append(
        "体系：" + ("地锚式（主缆锚在锚碇，H 传给地基，梁端纵向固定）"
                    if sys_name == "EARTH" else
                    "自锚式（主缆锚在加劲梁端，H 压进梁，纵向约束放跨中）")
        + "；主缆线形由恒载找形确定，不是人为给定。")
    res.diagnostics.append(
        "加劲梁走单梁线（平面模型），主缆与吊索节点的平面外自由度按平面问题"
        "处理；双索面与横撑不在本版范围。")
    res.diagnostics.append(
        "塔处加劲梁竖向支座按支座反力计（不细分塔梁传力路径）：塔柱轴力若需"
        "计入该反力，请在 [REACTION] 里取塔位梁节点的竖向反力叠加。")


# ---------------------------------------------------------------- 注册表


def _registry() -> dict[str, SolverSpec]:
    """延迟构建：内核模块导入失败不该让整个界面崩掉。"""
    from fem_eigen_3d import fem_eigen_3d_available, solve_3d
    from fem_eigen_cable import cable_solver_available, find_cable_solver, solve_cable_bridge

    def _find_grillage() -> Path:
        from fem_eigen_3d import _find_exe
        return _find_exe()

    return {
        "grillage3d": SolverSpec(
            key="grillage3d",
            exe_name="example_3d_grillage.exe",
            available=fem_eigen_3d_available,
            find=_find_grillage,
            run=lambda m, d, n: solve_3d(m, out_dir=d, dat_name=n),
            adapt=_adapt_grillage,
            build_hint="cd D:/AI-work/FEM-Eigen && cmake --build build "
                       "--target example_3d_grillage",
        ),
        "cable_stayed": SolverSpec(
            key="cable_stayed",
            exe_name="cable_solver.exe",
            available=cable_solver_available,
            find=find_cable_solver,
            run=lambda m, d, n: solve_cable_bridge(m, out_dir=d, dat_name=n),
            adapt=_adapt_cable,
            build_hint="cd D:/AI-work/FEM-Eigen && cmake --build build --target cable_solver",
        ),
        # 悬索桥与斜拉桥共用同一个 exe：成桥态的力学内核完全一样（共转列式
        # 非线性 + 索垂度 + 索长反算），差别在**前面多一步找形**。分成两个内核
        # 键不是为了分程序，而是因为后处理、指标、校核、计算书章节结构全不同，
        # 用 kind 分流比在消费方写 if 干净。
        "suspension": SolverSpec(
            key="suspension",
            exe_name="cable_solver.exe",
            available=cable_solver_available,
            find=find_cable_solver,
            run=lambda m, d, n: solve_cable_bridge(m, out_dir=d, dat_name=n),
            adapt=_adapt_suspension,
            build_hint="cd D:/AI-work/FEM-Eigen && cmake --build build --target cable_solver",
        ),
    }


def solvers() -> dict[str, SolverSpec]:
    try:
        return _registry()
    except Exception:                      # 缺少内核模块/目录不存在
        return {}


def solver_keys() -> list[str]:
    return list(solvers())


def detect_kind(model: Any) -> str:
    """模型 → 内核键。判据与 `ModelSpec3D.to_dat()` 的 CABLE 分叉完全一致。

    悬索桥与斜拉桥都有 `cable_bridge`，唯一的结构性区别是悬索桥声明了体系
    （`system` 非空 = 主缆是一条连续索、线形由找形定）。用体系字段而不是
    "有没有 main_cable"来判，是因为 main_cable 为空时 also 说明不了问题——
    那是一个**该报错**的模型，不该被默默当成斜拉桥去算。
    """
    cb = getattr(model, "cable_bridge", None)
    if cb is None:
        return "grillage3d"
    return "suspension" if str(getattr(cb, "system", "") or "") else "cable_stayed"


def capabilities() -> dict[str, bool]:
    """内核键 → 是否可用。界面据此决定显示/禁用，不在界面里写死 exe 名。"""
    return {k: bool(s.available()) for k, s in solvers().items()}


def require(kind: str) -> SolverSpec:
    reg = solvers()
    if not reg:
        raise SolverUnavailable(
            "未找到 FEM-Eigen 三维内核模块。请设置环境变量 FEM_EIGEN_DIR 指向内核目录，"
            "并先编译目标：" + "；".join(
                s.build_hint for s in _fallback_hints().values()))
    spec = reg.get(kind)
    if spec is None:
        raise SolverUnavailable(
            f"未知内核键 {kind!r}，已注册的有：{', '.join(reg)}")
    if not spec.available():
        raise SolverUnavailable(
            f"内核 {spec.exe_name} 不可用（{spec.label}）。请先编译：\n"
            f"  {spec.build_hint}")
    return spec


def _fallback_hints() -> dict[str, SolverSpec]:
    """模块都导不进来时，至少把各内核的编译命令说清楚。

    键必须与真注册表一致（`grillage3d` / `cable_stayed` / `suspension`）：
    少一个键，用户在那种情况下会看到"未知内核键"而不是"请先编译"，
    提示就白写了。
    """
    def _no() -> bool:
        return False

    def _nofind() -> Path:
        raise FileNotFoundError

    def _norun(m: Any, d: Path, n: str) -> dict:
        raise FileNotFoundError

    def _noad(m: Any, r: dict, s: SolveResult) -> None:
        return None

    return {
        k: SolverSpec(key=k, exe_name=e, available=_no, find=_nofind,
                      run=_norun, adapt=_noad, build_hint=h)
        for k, e, h in (
            ("grillage3d", "example_3d_grillage.exe",
             "cd D:/AI-work/FEM-Eigen && cmake --build build --target example_3d_grillage"),
            ("cable_stayed", "cable_solver.exe",
             "cd D:/AI-work/FEM-Eigen && cmake --build build --target cable_solver"),
            # 悬索桥与斜拉桥共用 cable_solver.exe，但同样是独立的一个键：
            # adapt 与出口不同，键合并会让 detect_kind 的分支无处落地。
            ("suspension", "cable_solver.exe",
             "cd D:/AI-work/FEM-Eigen && cmake --build build --target cable_solver"),
        )
    }


# ---------------------------------------------------------------- 唯一入口


def solve(
    model: Any,
    kind: str | None = None,
    out_dir: str | Path = "out/solve3d",
    dat_name: str | None = None,
) -> SolveResult:
    """求解一个 ModelSpec3D，返回统一的 SolveResult。

    用法::

        res = solve(model)                    # 内核由模型自述决定
        res.metrics()                         # 指标卡
        [c for c in res.checks(model) if not c.passed]   # 不通过的校核
        res.tables(model)["cable_table"]      # 明细表
    """
    k = kind or detect_kind(model)
    spec = require(k)

    dat_name = dat_name or f"{k}.dat"
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    raw = spec.run(model, out, dat_name)
    dat = Path(raw["dat"]).resolve()
    if not dat.is_file():
        raise RuntimeError(f"内核未写出 .dat：{dat}")

    res = SolveResult(
        kind=k,
        title=str(getattr(model, "title", "") or ""),
        solver=spec.exe_name,
        dat_text=dat.read_text(encoding="utf-8", errors="replace"),
        dat_path=str(dat),
    )
    spec.adapt(model, raw, res)
    if not res.stats.get("nodes"):
        res.stats.update({k2: v for k2, v in (model.stats() or {}).items()
                          if k2 not in res.stats})
        res.stats["dofs"] = res.stats.get("dofs") or _dofs(model)
    return res


# ---------------------------------------------------------------- CLI 自检
if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent))

    print("已注册内核：")
    for k, s in solvers().items():
        print(f"  {k:<14} {s.label:<12} {'可用' if s.available() else '不可用'}"
              f"  ({s.exe_name})")

    if not capabilities().get("grillage3d"):
        print("\n梁格内核不可用，跳过自检")
        raise SystemExit(0)

    from grillage_3d import build_box_grillage_3d

    m = build_box_grillage_3d()
    r = solve(m, out_dir="out/p4_selfcheck", dat_name="grillage.dat")
    print(f"\n[{r.kind_label}] {r.solver}  指纹 {r.fingerprint}")
    for mt in r.metrics(m):
        print(f"  {mt.label}: {mt.value} {mt.unit}  {mt.hint}")
    for c in r.checks(m):
        print(f"  {'OK ' if c.passed else 'NG '} {c.name}: {c.detail}")
    print("  表:", list(r.tables(m)))
