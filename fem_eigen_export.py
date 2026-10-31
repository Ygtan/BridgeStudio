"""
fem_eigen_export.py
===================
把 NL-FE 的内部模型 `ModelSpec` 导出为 **FEM-Eigen 的 `.dat`（new format）**，
并可调用 FEM-Eigen 求解、读回 `.result` 数值结果与 `.svg` 可视化。

这一步补上了整个原型最关键的缺口 —— **「前处理 → 交接给计算模块」**：
自然语言描述建好的梁模型，不再只做本地草算，而是产出一份工业级 C++ 求解器
能直接读取、求解并出图的计算数据文件。

--------------------------------------------------------------------------
字段映射表（ModelSpec  →  FEM-Eigen .dat new format）
--------------------------------------------------------------------------
| ModelSpec             | .dat 段/字段                  | 说明                          |
|-----------------------|------------------------------|-------------------------------|
| `name`                | `*TITLE`                     | 模型标题                      |
| —（梁单元固定）        | `*ET` / `1 BEAM`             | 单元类型表，当前只导出 BEAM    |
| `E`                   | `*MP` → `E=`                 | 弹性模量，Pa                  |
| `rho`                 | `*MP` → `DENS=`              | 密度 kg/m³（自重用）          |
| `material_name`       | `*MP` → `ALPX=`              | 线膨胀系数：混凝土 1e-5，否则 1.2e-5 |
| `section` → `A`       | `*R` → `A=`                  | 截面积 m²                     |
| `section` → `I`       | `*R` → `I=`                  | 惯性矩 m⁴                     |
| `section` → `W`       | `*R` → `WT=` / `WB=`         | 上下缘抗弯模量 m³（应力图用）  |
| `section.h`           | `*R` → `H=`                  | 截面高度 m（温度梯度用）       |
| `length` + 关键坐标    | `*NODE`                      | 节点坐标（x 沿梁轴，y=0）      |
| 相邻节点               | `*EN,i,i,i+1`                | 梁单元（配合 `*TYPE/*MAT/*REAL`） |
| `supports[].position` | `*BOUNDARY`                  | 见下方支座映射                |
| `supports[].type`     | `*BOUNDARY` 的 dof 区间       | pinned→`1-2`；roller→`2`；fixed→`1-3` |
| `loads[]`(udl)        | `*ELEMENT_LOAD` `GY q q`     | kN/m → N/m（×1000，向下为负）  |
| `loads[]`(point)      | `*CLOAD` `2 F`               | kN → N（×1000，向下为负）      |
| `self_weight` 开关     | `*GRAVITY` `0 -9.81`/`0 0.0` | **必须显式写出**，见下方注意    |

自由度约定（FEM-Eigen 二维）：`1`=u(x)  `2`=v(y)  `3`=θ(转角)，y 轴向上。

⚠️ 注意（实测踩坑）：`fem_2d.hpp:109` 中 `gx=0, gy=-9.81` 是**默认值**。
只要 `*MP` 里给了 `DENS`，即使完全不写 `*GRAVITY`，求解器也会自动计入自重
（`autoLineMass = dens*A`，见 fem_2d.cpp:348-350）。
（内核早年把 2D 与 IO 放在同一个 `fem_io.hpp` 里，后来拆成
`fem_2d.hpp` / `fem_2d.cpp`；这里跟的是拆分后的位置。）
因此本模块**总是显式输出 `*GRAVITY`**：`self_weight=True` 给 `0 -9.81`，
`False` 给 `0 0.0`。否则导出的模型会凭空多出一份自重，弯矩/挠度偏大。

--------------------------------------------------------------------------
用法
--------------------------------------------------------------------------
    # 1) 只导出 .dat
    python fem_eigen_export.py --demo -o out/my_beam.dat

    # 2) 导出 + 调 FEM-Eigen 求解 + 出图 + 与理论解比对
    python fem_eigen_export.py --demo --solve --check

依赖：仅需 Python 标准库 + 本项目内的 model_spec / section_lib。
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from model_spec import ModelSpec, Support
from section_lib import section_properties

# ---------------------------------------------------------------- 路径配置
# FEM-Eigen 内核目录自动探测（按优先级）。它搬过两次家：
#   D:\AI-work\FEM-Eigen （2026-10-28 起的主家，P3 内核攻坚在此进行）
#   C:\Users\Thinkpad\WorkBuddy AI\2026-10-06-17-19-31\FEM-Eigen （旧家，
#     位于 WorkBuddy 会话目录内，已冻结为历史快照，仅作兜底）
# 优先级：环境变量 FEM_EIGEN_DIR > 逐一探测候选目录。
# 判定标准：目录下存在 build/example_mixed.exe（或任一求解器候选）。
import os as _os

_FEM_EIGEN_DIR_CANDIDATES = [
    Path(_os.environ.get("FEM_EIGEN_DIR", "")) if _os.environ.get("FEM_EIGEN_DIR") else None,
    Path(r"D:\AI-work\FEM-Eigen"),
    Path(r"C:\Users\Thinkpad\WorkBuddy AI\2026-10-06-17-19-31\FEM-Eigen"),
    Path(r"D:\AI-work\前处理\FEM-Eigen"),
]


def _detect_fem_eigen_dir() -> Path | None:
    """在候选目录里找出真正有求解器的那个。"""
    for cand in _FEM_EIGEN_DIR_CANDIDATES:
        if cand is None:
            continue
        for sub in ("build", "build_make", "build_local"):
            if (cand / sub / "example_mixed.exe").is_file():
                return cand
    return None


FEM_EIGEN_DIR = _detect_fem_eigen_dir()

# 输出数值 .result 的求解器（同时也会打印节点位移/单元内力）
EXE_RESULT_CANDIDATES = [
    (FEM_EIGEN_DIR / "build" / "example_mixed.exe") if FEM_EIGEN_DIR else None,
    (FEM_EIGEN_DIR / "build_make" / "example_mixed.exe") if FEM_EIGEN_DIR else None,
    (FEM_EIGEN_DIR / "build_local" / "example_mixed.exe") if FEM_EIGEN_DIR else None,
]
# 输出 .svg 可视化的求解器
EXE_VIZ_CANDIDATES = [
    (FEM_EIGEN_DIR / "build" / "example_viz.exe") if FEM_EIGEN_DIR else None,
    (FEM_EIGEN_DIR / "build_make" / "example_viz.exe") if FEM_EIGEN_DIR else None,
    (FEM_EIGEN_DIR / "build_local" / "example_viz.exe") if FEM_EIGEN_DIR else None,
]

# 支座类型 → 被约束的自由度（FEM-Eigen 二维：1=u, 2=v, 3=θ）
SUPPORT_DOFS = {
    "pinned": "1-2",   # 铰支：约束平动，转角自由
    "roller": "2",     # 滚动支座：仅约束竖向
    "fixed": "1-3",    # 固支：平动 + 转角全约束
}


def _fmt(v: float) -> str:
    """数值格式化：保留足够有效位，避免科学计数法歧义。"""
    return f"{v:.10g}"


def _find_exe(candidates, user_exe: str | None, what: str) -> Path | None:
    if user_exe:
        p = Path(user_exe)
        return p if p.is_file() else None
    for c in candidates:
        if c is not None and c.is_file():
            return c
    return None


def fem_eigen_available() -> bool:
    """FEM-Eigen 是否可用（有任一可执行文件即可）。

    界面与 ModelDoc.capability() 都问这一个函数，避免两处各判一次、
    判出不同结果。
    """
    return any(
        p is not None and p.is_file()
        for p in (EXE_RESULT_CANDIDATES + EXE_VIZ_CANDIDATES)
    )


# ---------------------------------------------------------------- 网格生成
def build_mesh(spec: ModelSpec, target_len: float = 0.5, max_elems: int = 200):
    """
    生成节点坐标表。

    保证支座位置、集中力位置、分布荷载起止点都**精确落在节点上**，
    否则荷载会被就近取整、引入不应有的误差。

    返回 (xs, index_of)：
      xs        —— 节点 x 坐标列表（升序，含首尾）
      index_of  —— 关键坐标 → 节点号(1 起) 的映射
    """
    L = spec.length
    tl = max(L / max_elems, target_len)

    # ① 收集必须成为节点的关键坐标
    crit = {0.0, L}
    for s in spec.supports:
        crit.add(min(max(s.position, 0.0), L))
    for ld in spec.loads:
        if ld.type == "point" and ld.position is not None:
            crit.add(min(max(ld.position, 0.0), L))
        elif ld.type == "udl":
            crit.add(min(max(ld.start if ld.start is not None else 0.0, 0.0), L))
            crit.add(min(max(ld.end if ld.end is not None else L, 0.0), L))
    crit = sorted(crit)

    # ② 逐段细分
    xs = [crit[0]]
    for x0, x1 in zip(crit[:-1], crit[1:]):
        dx = x1 - x0
        if dx <= 1e-12:
            continue
        n = max(1, math.ceil(dx / tl))
        for k in range(1, n + 1):
            xs.append(x0 + dx * k / n)

    # ③ 关键坐标 → 节点号
    index_of = {}
    for c in crit:
        # 找最接近的节点（关键坐标本就是节点，这里只做浮点容错）
        nid = min(range(len(xs)), key=lambda i: abs(xs[i] - c)) + 1
        index_of[round(c, 9)] = nid
    return xs, index_of


# ---------------------------------------------------------------- 生成 .dat
def spec_to_dat(
    spec: ModelSpec,
    target_len: float = 0.5,
    max_elems: int = 200,
    self_weight: bool = False,
    alpx: float | None = None,
) -> str:
    """把 ModelSpec 转成 FEM-Eigen `.dat`（new format）文本。"""
    L = spec.length
    xs, index_of = build_mesh(spec, target_len, max_elems)
    n_node = len(xs)
    n_elem = n_node - 1

    props = section_properties(spec.section)
    A, Iy, W = props["A"], props["I"], props["W"]

    # 线膨胀系数：混凝土 1e-5，钢材 1.2e-5
    if alpx is None:
        alpx = 1.0e-5 if "混凝土" in spec.material_name else 1.2e-5

    out: list[str] = []
    out.append("# 由 fe_nl_preprocessor（自然语言有限元前处理）自动生成")
    out.append("# 目标格式：FEM-Eigen new format，见 FEM-Eigen/input_format.md")
    out.append(f"# 模型描述：{spec.summary().replace(chr(10), ' | ')}")
    out.append("*TITLE")
    out.append(spec.name)
    out.append("")

    # --- 单元类型 ---
    out.append("! 单元类型")
    out.append("*ET")
    out.append("1 BEAM")
    out.append("")

    # --- 材料 ---
    out.append("! 材料")
    out.append("*MP")
    out.append(f"1 E={_fmt(spec.E)} ALPX={_fmt(alpx)} DENS={_fmt(spec.rho)}")
    out.append("")

    # --- 实常数（截面） ---
    out.append("! 实常数（由参数化截面自动换算）")
    out.append("*R")
    out.append(
        f"1 A={_fmt(A)} I={_fmt(Iy)} WT={_fmt(W)} WB={_fmt(W)} H={_fmt(spec.section.h)}"
    )
    out.append("")

    # --- 节点 ---
    out.append("! 节点（x 沿梁轴，y 竖向）")
    out.append("*NODE")
    for i, x in enumerate(xs, start=1):
        out.append(f"{i} {x:.6f} 0.000000")
    out.append("")

    # --- 单元 ---
    out.append("! 单元")
    out.append("*TYPE,1")
    out.append("*MAT,1")
    out.append("*REAL,1")
    for i in range(1, n_elem + 1):
        out.append(f"*EN,{i},{i},{i+1}")
    out.append("")

    # --- 支座 ---
    out.append("! 支座约束")
    out.append("*BOUNDARY")
    for s in spec.supports:
        pos = min(max(s.position, 0.0), L)
        nid = index_of.get(round(pos, 9))
        if nid is None:
            nid = min(range(n_node), key=lambda i: abs(xs[i] - pos)) + 1
        out.append(f"{nid} {SUPPORT_DOFS[s.type]} 0.0")
    out.append("")

    # --- 荷载：集中力 ---
    pts = [ld for ld in spec.loads if ld.type == "point"]
    if pts:
        out.append("! 节点集中荷载（kN → N，向下为负）")
        out.append("*CLOAD")
        for ld in pts:
            pos = min(max(ld.position or 0.0, 0.0), L)
            nid = index_of.get(round(pos, 9))
            if nid is None:
                nid = min(range(n_node), key=lambda i: abs(xs[i] - pos)) + 1
            out.append(f"{nid} 2 {_fmt(-abs(ld.value) * 1000.0)}")
        out.append("")

    # --- 荷载：分布荷载 ---
    udls = [ld for ld in spec.loads if ld.type == "udl"]
    if udls:
        out.append("! 单元分布荷载（kN/m → N/m，向下为负）")
        out.append("*ELEMENT_LOAD")
        for ld in udls:
            s = min(max(ld.start if ld.start is not None else 0.0, 0.0), L)
            e = min(max(ld.end if ld.end is not None else L, 0.0), L)
            if e <= s:
                continue
            i0 = index_of.get(round(s, 9))
            i1 = index_of.get(round(e, 9))
            if i0 is None or i1 is None:
                continue
            e_start, e_end = i0, i1 - 1   # 单元 j 连接节点 j 与 j+1
            if e_end < e_start:
                continue
            rng = f"{e_start}" if e_start == e_end else f"{e_start}-{e_end}"
            q = -abs(ld.value) * 1000.0
            out.append(f"{rng} GY {_fmt(q)} {_fmt(q)}")
        out.append("")

    # --- 自重 ---
    # 注意（踩过的坑）：FEM-Eigen 的 gx/gy 默认值是 (0, -9.81)，见 fem_2d.hpp:109。
    # 也就是说只要 *MP 里给了 DENS，即使不写 *GRAVITY 也会自动计入自重。
    # 因此这里**必须显式写出** *GRAVITY：要自重就给 -9.81，不要就显式给 0.0。
    gy = -9.81 if self_weight else 0.0
    out.append("! 自重开关（必须显式给出：默认 gy=-9.81 会自动计自重）")
    out.append("*GRAVITY")
    out.append(f"0 {_fmt(gy)}")
    out.append("")

    out.append("*END")
    return "\n".join(out) + "\n"


def export_dat(spec: ModelSpec, path: str | Path, **kw) -> Path:
    """写出 .dat 文件，返回路径。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(spec_to_dat(spec, **kw), encoding="utf-8")
    return p


# ---------------------------------------------------------------- 结果解析
def parse_result(path: str | Path) -> dict:
    """解析 FEM-Eigen 写出的 `.result` 数值结果文件。"""
    txt = Path(path).read_text(encoding="utf-8", errors="replace")
    res: dict = {"nodes": {}, "elements": {}, "reactions": {}}

    section = None
    for raw in txt.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "节点位移" in line:
            section = "disp"; continue
        if "单元内力" in line:
            section = "force"; continue
        if "支座反力" in line:
            section = "reac"; continue
        if "单元应力" in line:
            section = "stress"; continue
        parts = line.split()
        # 跳过表头行
        if parts and not re.match(r"^-?[\d.eE+]+$", parts[0]):
            continue
        try:
            if section == "disp" and len(parts) >= 4:
                res["nodes"][int(float(parts[0]))] = {
                    "u": float(parts[1]), "v": float(parts[2]), "theta": float(parts[3])
                }
            elif section == "force" and len(parts) >= 8:
                res["elements"][int(float(parts[0]))] = {
                    "type": parts[1],
                    "N1": float(parts[2]), "N2": float(parts[3]),
                    "V1": float(parts[4]), "M1": float(parts[5]),
                    "V2": float(parts[6]), "M2": float(parts[7]),
                }
            elif section == "reac" and len(parts) >= 3:
                # 同一节点会有多个自由度的反力，必须用 (节点, dof) 做键，
                # 否则后一行会覆盖前一行（早期版本在这里丢过数据）。
                res["reactions"][(int(float(parts[0])), int(float(parts[1])))] = {
                    "node": int(float(parts[0])),
                    "dof": int(float(parts[1])),
                    "value": float(parts[2]),
                }
        except (ValueError, IndexError):
            continue
    return res


# ---------------------------------------------------------------- 调用求解器
def run_fem_eigen(
    dat_path: str | Path,
    workdir: str | Path | None = None,
    exe_result: str | None = None,
    exe_viz: str | None = None,
    make_svg: bool = True,
    timeout: float | None = 120.0,
) -> dict:
    """
    把 .dat 交给 FEM-Eigen：跑数值求解（.result）+ 可选出图（.svg）。

    返回 {'result': Path|None, 'svgs': [Path,...], 'stdout': str, 'ok': bool,
          'timed_out': bool}

    **超时**：两个子进程都带 `timeout`（默认 120 s，与 fem_eigen_3d 的
    `solve_3d` 取同一个值）。没有超时的时候，内核一旦挂起——大规模模型、
    加载步过大、或死循环——界面会永远停在"求解中…"，只能杀进程。这里
    沿用本函数"不抛异常、用 ok 标志说话"的既有契约：超时不抛，而是把
    原因写进 stdout 并置 `timed_out=True`，界面照常显示日志。
    传 `timeout=None` 可关掉限制（批处理里跑超大模型时用）。
    """
    dat_path = Path(dat_path)
    tmp = None
    if workdir is None:
        tmp = tempfile.mkdtemp(prefix="fem_eigen_")
        workdir = tmp
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    work_dat = workdir / dat_path.name
    if work_dat.resolve() != dat_path.resolve():
        shutil.copy2(dat_path, work_dat)

    info: dict = {"result": None, "svgs": [], "stdout": "", "ok": False,
                  "timed_out": False}
    logs = []

    def _run(exe: Path, label: str):
        """跑一个子进程；超时返回 None 并把原因记进 logs。"""
        try:
            return subprocess.run([str(exe), work_dat.name], cwd=str(workdir),
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=timeout)
        except subprocess.TimeoutExpired as e:
            info["timed_out"] = True
            tail = e.stdout or ""
            if isinstance(tail, bytes):
                tail = tail.decode("utf-8", "replace")
            logs.append(
                f"[超时] {label}超过 {timeout:g} s 未返回，已终止。\n"
                f"  模型规模过大、加载步过长或内核挂起都会这样；\n"
                f"  可调大 timeout 后重试（界面默认 120 s）。\n"
                + (tail[-400:] if tail else ""))
            return None

    # ① 数值求解 → .result
    exe_r = _find_exe(EXE_RESULT_CANDIDATES, exe_result, "result")
    if exe_r:
        p = _run(exe_r, "数值求解")
        if p is not None:
            logs.append(p.stdout or "")
            if (workdir / f"{dat_path.stem}.result").is_file():
                info["result"] = workdir / f"{dat_path.stem}.result"

    # ② 可视化 → .svg（求解超时就不必再等一遍出图）
    if make_svg and not info["timed_out"]:
        exe_v = _find_exe(EXE_VIZ_CANDIDATES, exe_viz, "viz")
        if exe_v:
            p = _run(exe_v, "出图")
            if p is not None:
                logs.append(p.stdout or "")
                info["svgs"] = sorted(workdir.glob(f"{dat_path.stem}_*.svg"))

    info["stdout"] = "\n".join(logs)
    info["ok"] = info["result"] is not None or bool(info["svgs"])
    info["workdir"] = workdir
    return info


# ---------------------------------------------------------------- 自检
def _demo_spec() -> ModelSpec:
    """内置自检用例：30 m 简支钢梁，矩形截面 1.2×0.6 m，20 kN/m 均布荷载。"""
    return ModelSpec(
        name="demo_30m_simple_beam",
        length=30.0,
        n_spans=1,
        section={"shape": "rectangular", "h": 1.2, "b": 0.6},
        material_name="钢材 Q345",
        E=2.06e11,
        rho=7850.0,
        supports=[{"position": 0.0, "type": "pinned"},
                  {"position": 30.0, "type": "roller"}],
        loads=[{"type": "udl", "value": 20.0, "start": None, "end": None}],
    )


def _theory(spec: ModelSpec) -> dict:
    """简支梁均布荷载理论解：M_max = wL²/8，v_max = 5wL⁴/(384EI)。"""
    w = 20.0e3          # N/m
    L = spec.length
    pr = section_properties(spec.section)
    EI = spec.E * pr["I"]
    return {
        "M_max": w * L**2 / 8.0,
        "v_max": -5.0 * w * L**4 / (384.0 * EI),
        "R": w * L / 2.0,
    }


def main() -> int:
    # 命令行入口才改编码：本模块被 app.py / ui 导入时不碰全局 stdout。
    from console_utf8 import setup_console

    setup_console()
    import argparse
    ap = argparse.ArgumentParser(description="导出 FEM-Eigen .dat 并可选求解校验")
    ap.add_argument("--demo", action="store_true", help="使用内置 30m 简支梁用例")
    ap.add_argument("-o", "--output", default="out/model.dat", help=".dat 输出路径")
    ap.add_argument("--solve", action="store_true", help="调用 FEM-Eigen 求解")
    ap.add_argument("--check", action="store_true", help="与理论解比对")
    ap.add_argument("--self-weight", action="store_true", help="计入自重")
    args = ap.parse_args()

    spec = _demo_spec()
    out = export_dat(spec, args.output, self_weight=args.self_weight)
    print(f"已导出 .dat：{out}")
    print("-" * 60)
    print(out.read_text(encoding="utf-8")[:600])
    print("  ...（略）")

    if not args.solve:
        return 0

    info = run_fem_eigen(out)
    print("-" * 60)
    if not info["ok"]:
        print("[FAIL] FEM-Eigen 未产出结果")
        return 1
    print(f"数值结果: {info['result']}")
    print(f"可视化   : {len(info['svgs'])} 个 SVG")

    if not args.check or not info["result"]:
        return 0

    r = parse_result(info["result"])
    th = _theory(spec)
    v_max = min((n["v"] for n in r["nodes"].values()), default=0.0)
    m_max = max((abs(e["M1"]) for e in r["elements"].values()), default=0.0)
    # FEM-Eigen 的支座反力以 +y 向上为正，与外荷载（向下为负）直接平衡，
    # 因此这里**不要再取负号**（取负会得到 -600kN 而误判为不平衡）。
    sum_r = sum(v["value"] for v in r["reactions"].values() if v["dof"] == 2)

    print("-" * 60)
    print("与理论解比对（简支梁 + 均布荷载）：")
    print(f"  跨中挠度  FEM={v_max*1000:10.4f} mm   理论={th['v_max']*1000:10.4f} mm"
          f"   相对误差 {abs(v_max-th['v_max'])/abs(th['v_max'])*100:6.3f}%")
    print(f"  最大弯矩  FEM={m_max/1000:10.3f} kN·m 理论={th['M_max']/1000:10.3f} kN·m"
          f"   相对误差 {abs(m_max-th['M_max'])/th['M_max']*100:6.3f}%")
    print(f"  支反力合  FEM={sum_r/1000:10.3f} kN   理论={th['R']*2/1000:10.3f} kN"
          f"   相对误差 {abs(sum_r-2*th['R'])/(2*th['R'])*100:6.3f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
