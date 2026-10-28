"""fem_eigen_3d.py
=================
调 FEM-Eigen 3D 内核求解 ModelSpec3D，并读回 `example_3d_grillage.exe`
产出的 `<stem>_summary.txt`（阶段位移包络 + 梁内力包络 + 活载包络）。

复用 fem_eigen_export 的内核目录探测（环境变量 FEM_EIGEN_DIR 优先，
候选目录逐一检查 build/example_3d_grillage.exe）。

用法
----
    from grillage_3d import build_box_grillage_3d
    from fem_eigen_3d import solve_3d
    m = build_box_grillage_3d()
    res = solve_3d(m, out_dir="out/grillage")
    res["disp"][(node, dof)] = (min, max)
    res["beam"][elem_id] = dict(Nmin=..., Nmax=..., MzMin=..., MzMax=...)
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from fem_eigen_export import FEM_EIGEN_DIR

EXE_3D_CANDIDATES = [
    (FEM_EIGEN_DIR / "build" / "example_3d_grillage.exe") if FEM_EIGEN_DIR else None,
]


def fem_eigen_3d_available() -> bool:
    return any(p is not None and p.is_file() for p in EXE_3D_CANDIDATES)


def _find_exe() -> Path:
    for p in EXE_3D_CANDIDATES:
        if p is not None and p.is_file():
            return p
    raise FileNotFoundError(
        "未找到 FEM-Eigen 3D 求解器 example_3d_grillage.exe，"
        f"期望目录: {FEM_EIGEN_DIR}（可用环境变量 FEM_EIGEN_DIR 指定）"
    )


def parse_summary(path: Path) -> dict:
    """解析 <stem>_summary.txt。

    返回 {"disp": {(node,dof): (min,max)},
          "beam": {elem: {"Nmin":..,"Nmax":..,"MzMin":..,"MzMax":..}},
          "live": {stage: (max,min)}}
    """
    disp: dict = {}
    beam: dict = {}
    live: dict = {}
    section = None
    re_disp = re.compile(r"^N(\d+) dof(\d+) min=(\S+) max=(\S+)$")
    re_beam = re.compile(
        r"^E(\d+) Nmin=(\S+) Nmax=(\S+) MzMin=(\S+) MzMax=(\S+)$")
    re_live = re.compile(r"^STAGE(\d+) max=(\S+) min=(\S+)$")
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(("stage displacement", "beam force", "stage live")):
            section = line
            continue
        if section and section.startswith("stage displacement"):
            m = re_disp.match(line)
            if m:
                disp[(int(m.group(1)), int(m.group(2)))] = (
                    float(m.group(3)), float(m.group(4)))
        elif section and section.startswith("beam force"):
            m = re_beam.match(line)
            if m:
                beam[int(m.group(1))] = {
                    "Nmin": float(m.group(2)), "Nmax": float(m.group(3)),
                    "MzMin": float(m.group(4)), "MzMax": float(m.group(5)),
                }
        elif section and section.startswith("stage live"):
            m = re_live.match(line)
            if m:
                live[int(m.group(1))] = (float(m.group(2)), float(m.group(3)))
    return {"disp": disp, "beam": beam, "live": live}


def solve_3d(model, out_dir: str | Path, dat_name: str = "model_3d.dat") -> dict:
    """导出 .dat → 调 example_3d_grillage.exe → 读回 summary。

    返回 {"dat": 路径, "summary": 路径, "disp":…, "beam":…, "live":…}。
    """
    exe = _find_exe()
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    dat = (out / dat_name).resolve()
    dat.write_text(model.to_dat(), encoding="utf-8")

    # exe 以 dat 路径为基准写 <stem>_summary.txt；传绝对路径保证落位
    proc = subprocess.run(
        [str(exe), str(dat)], capture_output=True, text=True,
        cwd=str(out), timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(
            f"FEM-Eigen 3D 求解失败 (code={proc.returncode}):\n"
            f"{proc.stdout}\n{proc.stderr}")

    summary = dat.with_name(dat.stem + "_summary.txt")
    if not summary.is_file():
        raise RuntimeError(f"求解器未产出 summary: {summary}")
    parsed = parse_summary(summary)
    parsed["dat"] = dat
    parsed["summary"] = summary
    return parsed


# ---------------------------------------------------------------- CLI 自检
if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from grillage_3d import build_box_grillage_3d

    m = build_box_grillage_3d()
    print("模型规模:", m.stats())
    res = solve_3d(m, out_dir="out/p1_selfcheck", dat_name="grillage.dat")
    print("dat     :", res["dat"])
    print("summary :", res["summary"])
    nd = len(res["disp"])
    print(f"位移包络条目: {nd}，梁内力包络单元数: {len(res['beam'])}")
