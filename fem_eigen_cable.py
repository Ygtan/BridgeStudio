"""fem_eigen_cable.py
====================
调 FEM-Eigen 的 `cable_solver.exe` 求解斜拉桥成桥态，并读回
`<stem>_cable.txt`（下料索长 / 成桥索力 / 预抬高 / 制造线形 / 成桥位移）。

为什么要一个独立 exe
--------------------
成桥态用到共转列式几何非线性、索垂度（弹性悬链线 + Ernst）、索长影响矩阵、
最小弯曲能量法——这些都在 C++ 内核里。Python 只做两件事：把参数拼成 .dat，
把结果解析成表。任何"在这儿也顺便算一下柔和度"的做法都会变成第二套力学。

结果字典
--------
::

    res["meta"]    = {mode, tension_method, nodes, elements, cables, dofs,
                      converged, iterations, elapsed_ms, closure_err,
                      tension_err, tension_min, tension_max, tension_sum,
                      vertical_sum, total_load, deck_load, deck_share_pct,
                      support_share}
    res["cables"]  = [{elem, anchor, T, L0, chord, Ltarget, dL}, …]
    res["camber"]  = {node: (dx, dy, dz)}     制造线形相对设计线形的偏移 m
    res["erected"] = {node: (x, y, z)}        制造线形绝对坐标
    res["u"]       = {(node, dof): 值}        成桥位移（dof 1..6）
    res["dat"], res["result"], res["stdout"]

用法
----
    from cable_stayed_3d import build_cable_stayed_3d
    from fem_eigen_cable import solve_cable_bridge
    m, meta = build_cable_stayed_3d(main_span=200, n_cable=12)
    res = solve_cable_bridge(m, out_dir="out/cable")
    res["cables"][0]["L0"]        # 下料索长
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from fem_eigen_export import FEM_EIGEN_DIR

EXE_CABLE_CANDIDATES = [
    (FEM_EIGEN_DIR / "build" / "cable_solver.exe") if FEM_EIGEN_DIR else None,
    (FEM_EIGEN_DIR / "build_make" / "cable_solver.exe") if FEM_EIGEN_DIR else None,
]

#: 返回码：0 收敛 / 3 未收敛但有结果 / 其他 失败
RC_CONVERGED = 0
RC_NOT_CONVERGED = 3


def cable_solver_available() -> bool:
    return any(p is not None and p.is_file() for p in EXE_CABLE_CANDIDATES)


def find_cable_solver() -> Path:
    for p in EXE_CABLE_CANDIDATES:
        if p is not None and p.is_file():
            return p
    raise FileNotFoundError(
        "未找到 FEM-Eigen 的 cable_solver.exe。"
        f"期望目录: {FEM_EIGEN_DIR}（可用环境变量 FEM_EIGEN_DIR 指定），"
        "并在该目录执行 cmake --build build --target cable_solver"
    )


def _kv(line: str) -> tuple[str, str] | None:
    if "=" not in line:
        return None
    k, v = line.split("=", 1)
    return k.strip(), v.strip()


def parse_cable_result(path: str | Path) -> dict:
    """解析 cable_solver 输出的 <stem>_cable.txt。"""
    txt = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    meta: dict = {}
    cables: list[dict] = []
    camber: dict[int, tuple[float, float, float]] = {}
    erected: dict[int, tuple[float, float, float]] = {}
    u: dict[tuple[int, int], float] = {}
    sec = ""
    for raw in txt:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("["):
            sec = line.strip("[]").upper()
            continue
        if sec in ("", "CABLE_SOLVER"):
            if line.startswith("cable_solver"):
                continue
            # 头部一行可能有多个 kv：converged=1 iterations=6 elapsed_ms=155.7
            toks = line.split()
            if toks and toks[0] == "model":
                toks = toks[1:]
            for tok in toks:
                kv = _kv(tok)
                if not kv:
                    continue
                k, v = kv
                try:
                    meta[k] = float(v) if ("." in v or "e" in v.lower()) else int(v)
                except ValueError:
                    meta[k] = v
            continue
        parts = line.split()
        if sec == "CABLE" and len(parts) >= 7:
            cables.append({
                "elem": int(parts[0]), "anchor": int(parts[1]),
                "T": float(parts[2]), "L0": float(parts[3]),
                "chord": float(parts[4]), "Ltarget": float(parts[5]),
                "dL": float(parts[6]),
            })
        elif sec == "CAMBER" and len(parts) >= 4:
            camber[int(parts[0])] = (float(parts[1]), float(parts[2]), float(parts[3]))
        elif sec == "ERECTED" and len(parts) >= 4:
            erected[int(parts[0])] = (float(parts[1]), float(parts[2]), float(parts[3]))
        elif sec == "U" and len(parts) >= 3:
            u[(int(parts[0]), int(parts[1]))] = float(parts[2])
    return {"meta": meta, "cables": cables, "camber": camber,
            "erected": erected, "u": u}


def solve_cable_bridge(model, out_dir: str | Path,
                       dat_name: str = "cable.dat", timeout: int = 600) -> dict:
    """导出 .dat → 跑 cable_solver → 解析 <stem>_cable.txt。"""
    exe = find_cable_solver()
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    dat = (out / dat_name).resolve()
    dat.write_text(model.to_dat(), encoding="utf-8")

    proc = subprocess.run([str(exe), str(dat)], capture_output=True, text=True,
                          cwd=str(out), timeout=timeout)
    result = dat.with_name(dat.stem + "_cable.txt")
    if proc.returncode not in (RC_CONVERGED, RC_NOT_CONVERGED):
        raise RuntimeError(
            f"cable_solver 失败 (code={proc.returncode}):\n"
            f"{proc.stdout}\n{proc.stderr}")
    if not result.is_file():
        raise RuntimeError(f"cable_solver 未产出结果文件: {result}\n{proc.stdout}")

    res = parse_cable_result(result)
    res["dat"] = dat
    res["result"] = result
    res["stdout"] = proc.stdout
    res["returncode"] = proc.returncode
    res["converged"] = bool(res["meta"].get("converged", 0))
    return res


# ---------------------------------------------------------------- CLI 自检
if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from cable_stayed_3d import build_cable_stayed_3d

    if not cable_solver_available():
        print("cable_solver.exe 不可用，请先编译内核：")
        print("  cd D:/AI-work/FEM-Eigen && cmake --build build --target cable_solver")
        raise SystemExit(1)

    m, info = build_cable_stayed_3d()
    print("模型:", m.stats(), "索数:", info["n_cable_actual"])
    r = solve_cable_bridge(m, out_dir="out/p3_selfcheck", dat_name="cable.dat")
    md = r["meta"]
    print(f"收敛={r['converged']} 迭代={md.get('iterations')} 耗时={md.get('elapsed_ms')} ms")
    print(f"闭合误差 {md.get('closure_err'):.2e} m，索力 "
          f"{md.get('tension_min_kN'):.0f}~{md.get('tension_max_kN'):.0f} kN")
    print(f"索承担桥面恒载 {md.get('deck_share_pct'):.1f}%")
    print("下料索长前 3 根:", [round(c["L0"], 3) for c in r["cables"][:3]])
