"""solve_2d.py —— 二维桥梁求解链：BridgeSpec → 2D 内核 → 规范组合-ready 结果。

档二的核心落点
--------------
维度正交化后，二维桥型（梁桥/拱桥/桁架）不再只有 `example_mixed.exe` 的一次性
静力。本模块把 2D 内核已经具备、但前处理一直没接上的三条能力串成一条链：

    施工阶段（example_stage.exe）   → 成桥态累计内力 + 施工包络
    车道活载（example_influence.exe）→ JTG D60 影响线最不利布载包络
    combination.beam_combinations   → 基本/频遇/准永久三种组合

返回 `Solve2DResult`。它刻意**不**复用三维 `SolveResult`：后者拖着索、找形、
预拱度一整排字段，二维用不上；这里只实现 `combination.beam_combinations` 需要
的鸭子属性（`final_beam` / `live_beam`），其余字段按 2D 语义自持。

三条铁律（与 combination.py、solver_3d.py 同源）
------------------------------------------------
1. **成桥态取最后一个施工阶段的累计内力**。`example_stage.exe` 走增量正装
   （`solveModel2DStages`，累计荷载 = 各阶段增量），每个阶段的 `forces` 都是
   对**当前累计构型**求的力——所以最后阶段的 `forces` 就是 Σ 各阶段增量的
   成桥态，与三维 `final_beam` 语义一致，可直接和活载包络配对组合。
2. **按 (单元, 端) 组合，不按单元**。两端弯矩异号，先外包再相加会把 i 端的
   恒载和 j 端的活载凑到一起。
3. **缺项就说缺**。无车道时 `live_beam` 为空，`beam_combinations` 只出恒载项
   并标 `has_live=False`，界面/报告必须明说。

第四条链（**opt-in，默认关闭**）：`example_modal.exe` → `<stem>.modal.txt`
→ `modal_2d` 解析，把 2D 内核本来就在算的自振频率与线性屈曲因子接进结果/报告。
只暴露内核输出：不重算 K/M、不造周期 T / 振型 / 参与质量，屈曲段缺失如实写明。

坐标约定
--------
2D 内核自由度：1=u(x)、2=v(y)、3=θ。竖向位移是 dof 2（不是三维的 dof 3）。
内力字段映射到 combination.py 的命名：2D 的 M（绕 z 的面外弯矩）→ "My"、
2D 的 V（沿 y 的竖向剪力）→ "Vz"——两者是同一物理量（引起竖向挠度的弯矩/
竖向剪力），只是坐标命名不同。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import combination
import modal_2d


class SolverUnavailable2D(RuntimeError):
    """2D 内核不可用。消息里必须带"怎么装回来"，否则等于没说。"""


# ---------------------------------------------------------------- 内核定位

_BUILD_DIRS = ("build", "build_make", "build_local")


def _fem_eigen_dir() -> Path | None:
    from fem_eigen_export import FEM_EIGEN_DIR

    return FEM_EIGEN_DIR


def _find_exe(name: str) -> Path | None:
    """在 FEM-Eigen 的 build 目录里找 2D 内核可执行文件。"""
    root = _fem_eigen_dir()
    if root is None:
        return None
    for sub in _BUILD_DIRS:
        p = root / sub / name
        if p.is_file():
            return p
    return None


def stage_solver_available() -> bool:
    return _find_exe("example_stage.exe") is not None


def influence_solver_available() -> bool:
    return _find_exe("example_influence.exe") is not None


def static_solver_available() -> bool:
    return _find_exe("example_mixed.exe") is not None


def modal_solver_available() -> bool:
    """2D 自振 / 屈曲内核是否已编译（example_modal.exe）。"""
    return _find_exe("example_modal.exe") is not None


def solve_2d_available() -> bool:
    """2D 桥梁求解链是否可用（施工内核 + 静态内核至少其一）。"""
    return static_solver_available() or stage_solver_available()


# ---------------------------------------------------------------- 结果

@dataclass
class Solve2DResult:
    """一次二维桥梁求解的全部产出。

    组合侧只认 `final_beam` / `live_beam` 两个鸭子属性（见 combination.py 的
    `beam_combinations`），键 `(单元, "i"/"j")`、值 `{"N","My","Vz"}` 与
    `{"NMin","NMax","MyMin","MyMax","VzMin","VzMax"}`。其余字段供报告/界面。
    """

    kind: str = "2d"
    title: str = ""
    solver: str = ""
    dat_text: str = ""
    dat_path: str = ""
    stats: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)

    # 成桥态（最后施工阶段累计内力）：{(单元, "i"/"j"): {"N","My","Vz"}}（N / N·m）
    final_beam: dict[tuple[int, str], dict[str, float]] = field(default_factory=dict)
    # 成桥态位移：{(节点, dof): 值 m}，竖向 = dof 2
    final_disp: dict[tuple[int, int], float] = field(default_factory=dict)
    # 施工阶段内力包络：{单元: {Nmin,Nmax,Mmin,Mmax,Vmin,Vmax}}（N / N·m）
    beam: dict[int, dict[str, float]] = field(default_factory=dict)
    # 车道活载内力包络：{(单元, "i"/"j"): {NMin,NMax,MyMin,MyMax,VzMin,VzMax}}
    live_beam: dict[tuple[int, str], dict[str, float]] = field(default_factory=dict)
    # 车道节点活载位移包络：{节点: (max, min)}，竖向，单位 m。不计冲击
    # （表 4.3.2 挠度限值针对汽车荷载本身，冲击只放大内力包络）。
    live_disp: dict[int, tuple[float, float]] = field(default_factory=dict)
    # 自动冲击的放大系数（只作用于内力包络）。未自动计算时为 1。
    impact_factor: float = 1.0
    # 2D 自振 / 线性屈曲（opt-in）：modal_2d.parse_modal_txt 的结果。
    # 默认 "not_requested"；不重算 K/M、缺段不补零。
    modal: dict[str, Any] = field(
        default_factory=lambda: {"status": "not_requested",
                                 "source": modal_2d.SOURCE})

    def modal_tables(self) -> dict[str, dict[str, Any]]:
        """报告 / 界面同源的两张表；无可用结果时为空 dict。"""
        out: dict[str, dict[str, Any]] = {}
        t = modal_2d.modal_table(self.modal)
        if t:
            out["modal_table"] = t
        b = modal_2d.buckling_table(self.modal)
        if b:
            out["buckling_table"] = b
        return out

    @property
    def fingerprint(self) -> str:
        import hashlib

        return hashlib.sha1(self.dat_text.encode("utf-8")).hexdigest()[:12]

    def combinations(self) -> list[combination.BeamCombo]:
        """JTG D60 基本/频遇/准永久三种组合。无成桥态内力时返回空表。"""
        return combination.beam_combinations(self)

    def max_vertical_deflection(self) -> tuple[int, float] | None:
        """竖向（dof=2）挠度绝对值最大者 → (节点号, 值 m)。负号保留原向。"""
        best: tuple[int, float] | None = None
        for (nid, dof), u in self.final_disp.items():
            if dof != 2:
                continue
            if best is None or abs(u) > abs(best[1]):
                best = (nid, u)
        return best


# ---------------------------------------------------------------- 解析

def _parse_stages(txt: str) -> list[dict[str, Any]]:
    """解析 example_stage.exe 的 `_stages.txt`。

    返回 ``[{stage, disp: {node: {u,v,theta}}, forces: {elem: {type,N1,N2,V1,M1,V2,M2}}}]``。
    每个阶段的 `forces` 是**累计内力**（增量正装，见模块铁律）。
    """
    stages: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    section: str | None = None
    for raw in txt.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^STAGE\s+(\d+)$", line)
        if m:
            cur = {"stage": int(m.group(1)), "disp": {}, "forces": {}}
            stages.append(cur)
            section = None
            continue
        if cur is None:
            continue
        if "节点位移" in line:
            section = "disp"
            continue
        if "单元内力" in line:
            section = "force"
            continue
        parts = line.split()
        if not parts or not re.match(r"^-?[\d.eE+]+$", parts[0]):
            continue
        try:
            if section == "disp" and len(parts) >= 4:
                cur["disp"][int(float(parts[0]))] = {
                    "u": float(parts[1]), "v": float(parts[2]),
                    "theta": float(parts[3])}
            elif section == "force" and len(parts) >= 8:
                cur["forces"][int(float(parts[0]))] = {
                    "type": parts[1],
                    "N1": float(parts[2]), "N2": float(parts[3]),
                    "V1": float(parts[4]), "M1": float(parts[5]),
                    "V2": float(parts[6]), "M2": float(parts[7])}
        except (ValueError, IndexError):
            continue
    return stages


def _parse_lane_envelope(txt: str) -> dict[tuple[int, str], dict[str, float]]:
    """解析 example_influence.exe 的 `_envelope_lane1.txt`。

    标签格式 `E<单元>_<N|V|M><端>`（`defaultEnvelopeSpecs` 生成），另有应力标签
    `S`/`TOP`/`BOT` 一律忽略。输出 ``{(单元, "i"/"j"): {NMin,NMax,MyMin,MyMax,VzMin,VzMax}}``。
    """
    out: dict[tuple[int, str], dict[str, float]] = {}
    for raw in txt.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("label"):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        m = re.match(r"^E(\d+)_([NVM])([12])$", parts[0])
        if not m:
            continue
        eid = int(m.group(1))
        comp = m.group(2)
        end = "i" if m.group(3) == "1" else "j"
        hi, lo = float(parts[1]), float(parts[2])
        slot = out.setdefault((eid, end), {})
        if comp == "N":
            slot["NMin"], slot["NMax"] = lo, hi
        elif comp == "V":
            slot["VzMin"], slot["VzMax"] = lo, hi
        elif comp == "M":
            slot["MyMin"], slot["MyMax"] = lo, hi
    return out


def _parse_lane_disp(txt: str) -> dict[int, tuple[float, float]]:
    """车道节点竖向位移包络。标签 D<节点>_2，值为 (max, min)，单位 m。"""
    out: dict[int, tuple[float, float]] = {}
    for raw in txt.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("label"):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        m = re.match(r"^D(\d+)_2$", parts[0])
        if not m:
            continue
        out[int(m.group(1))] = (float(parts[1]), float(parts[2]))
    return out


def _max_vertical_span(spec: Any) -> float:
    """相邻竖向支座的最大间距（m）。JTG D60 表 4.3.2 的分母用这一跨，不用总长。"""
    from bridge_spec import _dof_set

    xy = {n.id: float(n.x) for n in getattr(spec, "nodes", []) or []}
    xs = sorted({
        xy[b.node] for b in getattr(spec, "boundaries", []) or []
        if b.node in xy and 2 in _dof_set(b.dofs)
    })
    if len(xs) < 2:
        return 0.0
    return max(b - a for a, b in zip(xs, xs[1:]))


def _spec_has_cable(spec: Any) -> bool:
    for t in getattr(spec, "elem_types", []) or []:
        kind = getattr(t, "kind", "")
        name = getattr(kind, "value", kind)
        if str(name).upper() == "CABLE":
            return True
    return False


def _forces_to_final(forces: dict[int, dict[str, float]]) -> dict[tuple[int, str], dict[str, float]]:
    """单元内力表 → 成桥态 `final_beam`（(单元, "i"/"j") → {"N","My","Vz"}）。"""
    out: dict[tuple[int, str], dict[str, float]] = {}
    for eid, f in forces.items():
        out[(eid, "i")] = {"N": f["N1"], "My": f["M1"], "Vz": f["V1"]}
        out[(eid, "j")] = {"N": f["N2"], "My": f["M2"], "Vz": f["V2"]}
    return out


def _stage_envelope(stages: list[dict[str, Any]]) -> dict[int, dict[str, float]]:
    """跨阶段内力包络 {单元: {Nmin,Nmax,Mmin,Mmax,Vmin,Vmax}}。"""
    env: dict[int, dict[str, float]] = {}
    for s in stages:
        for eid, f in s["forces"].items():
            slot = env.setdefault(eid, {
                "Nmin": 1e300, "Nmax": -1e300,
                "Mmin": 1e300, "Mmax": -1e300,
                "Vmin": 1e300, "Vmax": -1e300})
            slot["Nmin"] = min(slot["Nmin"], f["N1"], f["N2"])
            slot["Nmax"] = max(slot["Nmax"], f["N1"], f["N2"])
            slot["Vmin"] = min(slot["Vmin"], f["V1"], f["V2"])
            slot["Vmax"] = max(slot["Vmax"], f["V1"], f["V2"])
            slot["Mmin"] = min(slot["Mmin"], f["M1"], f["M2"])
            slot["Mmax"] = max(slot["Mmax"], f["M1"], f["M2"])
    return env


# ---------------------------------------------------------------- 调用内核

def _run(exe: Path, dat_name: str, workdir: Path, timeout: float) -> str:
    """跑一个内核；超时抛 SolverUnavailable2D，返回 stdout。"""
    try:
        p = subprocess.run([str(exe), dat_name], cwd=str(workdir),
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise SolverUnavailable2D(
            f"内核 {exe.name} 超过 {timeout:g} s 未返回。模型规模过大或内核挂起，"
            f"可调大 timeout 重试。") from e
    if p.returncode != 0:
        raise SolverUnavailable2D(
            f"内核 {exe.name} 返回码 {p.returncode}：\n{p.stdout}\n{p.stderr}")
    return p.stdout or ""


# ---------------------------------------------------------------- 求解入口

def solve_2d(
    spec: Any,
    out_dir: str | Path = "out/solve2d",
    dat_name: str | None = None,
    timeout: float = 120.0,
    modal: bool = False,
) -> Solve2DResult:
    """求解一个 BridgeSpec，返回 `Solve2DResult`。

    `modal=True` 时额外跑 `example_modal.exe`（读同一份 .dat）并把
    2D 自振 / 屈曲结果放进 `res.modal`。默认 False：不调该 exe、不写
    任何新 diagnostics、HTML 计算书也一字不多，默认路径完全不变。

    用法::

        spec = build_beam_bridge(span=30, n_spans=3, staged=True, lane=True)
        res = solve_2d(spec)
        [c for c in res.combinations()]     # 三种规范组合
    """
    errs = spec.validate_model()
    if errs:
        raise ValueError("模型校验未通过：\n- " + "\n- ".join(errs))

    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    dat_name = dat_name or "model.dat"
    if not dat_name.lower().endswith(".dat"):
        dat_name += ".dat"
    dat_path = out / dat_name
    dat_path.write_text(spec.to_dat(), encoding="utf-8")

    res = Solve2DResult(
        title=getattr(spec, "title", "") or "未命名",
        dat_text=dat_path.read_text(encoding="utf-8", errors="replace"),
        dat_path=str(dat_path),
        stats={"nodes": len(spec.nodes), "elements": len(spec.elements),
               "stages": len(spec.stages), "lanes": len(spec.lanes)},
    )

    # ① 恒载/施工阶段 → 成桥态
    if spec.stages:
        exe = _find_exe("example_stage.exe")
        if exe is None:
            raise SolverUnavailable2D(
                "未找到 example_stage.exe，无法做施工阶段正装。请先编译：\n"
                "  cd D:/AI-work/FEM-Eigen && cmake --build build --target example_stage")
        res.solver = exe.name
        stdout = _run(exe, dat_name, out, timeout)
        stages_path = out / (dat_path.stem + "_stages.txt")
        if not stages_path.is_file():
            raise SolverUnavailable2D(
                f"内核未写出施工阶段结果 {stages_path.name}。\n{stdout}")
        stages = _parse_stages(stages_path.read_text(encoding="utf-8", errors="replace"))
        if not stages:
            raise SolverUnavailable2D("施工阶段结果为空，无法解析。")
        res.beam = _stage_envelope(stages)
        last = stages[-1]
        res.final_disp = {}
        for n, d in last["disp"].items():
            res.final_disp[(int(n), 1)] = float(d["u"])
            res.final_disp[(int(n), 2)] = float(d["v"])
        res.final_beam = _forces_to_final(last["forces"])
        res.stats["stages_solved"] = len(stages)
        creep_note = (
            "二维施工阶段按加载龄期之后的徐变折减模量，老化系数 χ=0.8。"
            "静定梁的位移约放大到 1+χφ，弯矩不随徐变放大。"
            "混凝土收缩按同一组湿度从加载龄期起算，正值缩短，只缩短梁轴。"
            "拉索和钢杆不按这条混凝土曲线徐变或收缩。"
            if float(getattr(spec, "creep_rh", 0.0) or 0.0) > 0.0
            else "这条二维正装不按龄期计算徐变和收缩。")
        res.diagnostics.append(
            f"施工阶段正装（{len(stages)} 个阶段，增量法）：成桥态取最后一个阶段的"
            "累计内力，与三维 final_beam 语义一致，可和活载包络配对组合。"
            + creep_note)
    else:
        exe = _find_exe("example_mixed.exe")
        if exe is None:
            raise SolverUnavailable2D(
                "未找到 example_mixed.exe，无法做一次性静力。请先编译：\n"
                "  cd D:/AI-work/FEM-Eigen && cmake --build build --target example_mixed")
        res.solver = exe.name
        stdout = _run(exe, dat_name, out, timeout)
        result_path = out / (dat_path.stem + ".result")
        if not result_path.is_file():
            raise SolverUnavailable2D(
                f"内核未写出结果 {result_path.name}。\n{stdout}")
        from fem_eigen_export import parse_result

        r = parse_result(result_path)
        res.final_beam = _forces_to_final({
            int(e): {"N1": v["N1"], "N2": v["N2"], "M1": v["M1"], "M2": v["M2"],
                     "V1": v["V1"], "V2": v["V2"]}
            for e, v in r["elements"].items()})
        res.final_disp = {(int(n), 2): float(v["v"]) for n, v in r["nodes"].items()}
        res.diagnostics.append("一次性静力（无施工阶段）：成桥态即当前模型的全荷载解。")

    # ② 车道活载 → 活载内力包络
    if spec.lanes:
        exe = _find_exe("example_influence.exe")
        if exe is None:
            # 车道不可算时诚实降级：留空 live_beam，组合只出恒载项。
            res.diagnostics.append(
                "模型定义了车道荷载，但未找到 example_influence.exe，"
                "无活载内力包络，组合只出恒载项。")
        else:
            stdout = _run(exe, dat_name, out, timeout)
            env_path = out / (dat_path.stem + "_envelope_lane1.txt")
            if env_path.is_file():
                env_text = env_path.read_text(encoding="utf-8", errors="replace")
                res.live_beam = _parse_lane_envelope(env_text)
                res.live_disp = _parse_lane_disp(env_text)
                res.diagnostics.append(
                    "车道活载按 JTG D60 影响线最不利布载取包络（成桥构型上线性叠加）。")
                hit = re.search(
                    r"f1_vertical=(\S+)\s+impact_mu=(\S+)\s+impact_factor=(\S+)",
                    env_text)
                if hit:
                    res.impact_factor = float(hit.group(3))
                    # 留一份独立的 f1 参照：opt-in 模态结果必须与它同源，
                    # 禁止报告里出现两套频率（modal_2d.consistency）。
                    res.stats["f1_vertical"] = float(hit.group(1))
                    res.stats["impact_mu"] = float(hit.group(2))
                    res.diagnostics.append(
                        "汽车冲击按竖向基频计入："
                        f"f1={float(hit.group(1)):.2f} Hz，"
                        f"μ={float(hit.group(2)):.3f}，"
                        f"活载放大 {res.impact_factor:.3f}。")
                if _spec_has_cable(spec):
                    res.diagnostics.append(
                        "索承活载走线性影响线：索按线弹性杆一次装配，逐点回代。"
                        "这不是成桥切线刚度，不含 Ernst 与几何刚度。")
            else:
                res.diagnostics.append(
                    f"example_influence.exe 未写出活载包络，组合只出恒载项。\n{stdout}")
    if not res.live_beam:
        res.diagnostics.append(
            "模型未定义车道荷载或内核未产出活载包络，无活载内力，"
            "荷载组合只出恒载项（has_live=False）。")
    if res.live_disp:
        node, (_hi, lo) = min(res.live_disp.items(), key=lambda kv: kv[1][1])
        span = _max_vertical_span(spec)
        line = (
            f"车道活载竖向位移（不计冲击）：节点 {node} "
            f"{lo * 1000:.2f} mm。"
        )
        if span > 0.0:
            lim = span / 600.0
            line += (
                f"JTG D60 表 4.3.2 限值 L/600 = {lim * 1000:.1f} mm"
                f"（最大跨径 {span:.0f} m），"
                + ("满足。" if abs(lo) <= lim else "超出。")
            )
        res.diagnostics.append(line)
    else:
        res.diagnostics.append(
            "没有车道位移包络，不能做 JTG D60 表 4.3.2 的活载挠度限值。")

    # ③ 2D 自振 / 线性屈曲（opt-in）：只读 example_modal.exe 写的
    #    <stem>.modal.txt，不重算 K/M、不改 .dat。缺 exe / 缺段 / 运行失败
    #    一律写中文状态，不摆空表、不补零、不影响①②的结果。
    if modal:
        exe = _find_exe("example_modal.exe")
        if exe is None:
            res.modal = {"status": "unavailable", "source": modal_2d.SOURCE}
        else:
            try:
                stdout = _run(exe, dat_name, out, timeout)
                modal_path = out / (dat_path.stem + ".modal.txt")
                if not modal_path.is_file():
                    res.modal = {"status": "no_output",
                                 "source": modal_2d.SOURCE,
                                 "stdout": (stdout or "")[-500:]}
                else:
                    res.modal = modal_2d.parse_modal_txt(
                        modal_path.read_text(encoding="utf-8",
                                             errors="replace"))
            except SolverUnavailable2D as e:
                res.modal = {"status": "solver_error",
                             "source": modal_2d.SOURCE, "reason": str(e)}
        res.modal["consistency"] = modal_2d.consistency(
            res.modal, res.stats.get("f1_vertical"))
        res.diagnostics.append(modal_2d.status_text(res.modal))
        res.diagnostics.append(res.modal["consistency"]["note"])
    return res


# ---------------------------------------------------------------- CLI 自检
if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from console_utf8 import setup_console

    setup_console()

    if not solve_2d_available():
        print("2D 内核不可用，跳过自检")
        raise SystemExit(0)

    from bridge_spec import build_beam_bridge

    spec = build_beam_bridge(span=30.0, n_spans=3, udl_kN=20.0,
                             staged=True, q2_kN=10.0, lane=True)
    res = solve_2d(spec)
    print(f"[2D] {res.title}  内核 {res.solver}  指纹 {res.fingerprint}")
    print(f"     节点 {res.stats['nodes']} / 单元 {res.stats['elements']} / "
          f"阶段 {res.stats['stages_solved']}")
    print(f"     成桥态截面 {len(res.final_beam)} 个 / 活载截面 {len(res.live_beam)} 个")
    w = res.max_vertical_deflection()
    print(f"     最大竖向挠度：节点 {w[0]} = {w[1] * 1000:.3f} mm")
    for c in res.combinations():
        m = c.worst("My")
        print(f"     {c.spec.name}: 控制弯矩 {m[1] / 1e3:.1f} kN·m "
              f"（单元 {m[0][0]}·{m[0][1]} 端，has_live={c.has_live}）")
