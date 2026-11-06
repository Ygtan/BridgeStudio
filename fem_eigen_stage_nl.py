"""调用 example_3d_stage_nl.exe，读回非线性施工阶段的全量平衡结果。

与 example_3d_grillage 的线性增量结果不是同一套数，不能相加，也不能互相替换。
悬臂斜拉的界面仍走线性内核。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from fem_eigen_export import FEM_EIGEN_DIR
from result_3d import SolveResult


def find_stage_nl_exe() -> Path:
    if FEM_EIGEN_DIR is None:
        raise FileNotFoundError("未设置 FEM_EIGEN_DIR")
    path = FEM_EIGEN_DIR / "build" / "example_3d_stage_nl.exe"
    if not path.is_file():
        raise FileNotFoundError(
            f"未找到 {path}。请在 FEM-Eigen 里编译 example_3d_stage_nl")
    return path


def parse_nlstage(text: str) -> list[dict]:
    """每个阶段：converged、位移 {(node, dof): u}、索力 {elem: {T, L0}}。"""
    stages: list[dict] = []
    cur: dict | None = None
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("STAGE "):
            parts = line.split()
            cur = {
                "id": int(parts[1]),
                "converged": "converged=1" in line,
                "u": {},
                "cables": {},
                "beams": {},
            }
            stages.append(cur)
            section = ""
            continue
        if line == "[U]":
            section = "U"
            continue
        if line == "[CABLE]":
            section = "CABLE"
            continue
        if line == "[BEAM]":
            section = "BEAM"
            continue
        if line == "[ENDSTAGE]":
            section = ""
            continue
        if cur is None:
            continue
        bits = line.split()
        if section == "U" and len(bits) >= 3:
            cur["u"][(int(bits[0]), int(bits[1]))] = float(bits[2])
        elif section == "CABLE" and len(bits) >= 3:
            cur["cables"][int(bits[0])] = {"T": float(bits[1]), "L0": float(bits[2])}
        elif section == "BEAM" and len(bits) >= 8:
            eid, end = int(bits[0]), int(bits[1])
            cur["beams"].setdefault(eid, {})[end] = {
                "N": float(bits[2]), "Vy": float(bits[3]), "Vz": float(bits[4]),
                "T": float(bits[5]), "My": float(bits[6]), "Mz": float(bits[7]),
            }
    return stages


def parse_nl_live(text: str) -> tuple[dict, dict, dict]:
    """无索模型在阶段结果后面附带的活载包络。格式与线性内核相同。"""
    lane_impact: dict = {}
    live_beam: dict = {}
    live: dict = {}
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if line == "lane impact":
            section = "impact"
            continue
        if line == "live beam envelope":
            section = "beam"
            continue
        if line == "stage live envelope":
            section = "disp"
            continue
        if line.startswith("STAGE ") or line.startswith("["):
            section = ""
            continue
        if section == "impact":
            for key, val in _kv(line).items():
                lane_impact[key] = val
        elif section == "beam":
            bits = line.split(None, 2)
            if len(bits) >= 3 and bits[0].startswith("E") and bits[1] in ("i", "j"):
                live_beam[(int(bits[0][1:]), bits[1])] = _kv(bits[2])
        elif section == "disp" and line.startswith("STAGE"):
            # 已在上面被 STAGE 前缀清掉；位移行是 STAGE<id> max= min=
            pass
        elif section == "disp":
            pass
    # 位移行以 STAGE 开头，上面的前缀判断会把它清掉。再扫一遍。
    import re
    for raw in text.splitlines():
        m = re.match(r"STAGE(\d+) max=(\S+) min=(\S+)", raw.strip())
        if m and "converged=" not in raw:
            live[int(m.group(1))] = (float(m.group(2)), float(m.group(3)))
    return lane_impact, live_beam, live


def _kv(text: str) -> dict[str, float]:
    import re
    out: dict[str, float] = {}
    for key, raw in re.findall(r"([A-Za-z][A-Za-z0-9_]*)=(\S+)", text):
        try:
            out[key] = float(raw)
        except ValueError:
            continue
    return out


def solve_stages_nonlinear(model, out_dir: str | Path,
                           dat_name: str = "stage_nl.dat",
                           timeout: int = 180) -> list[dict]:
    """写出 .dat 并求解。返回各阶段的全量平衡结果。"""
    exe = find_stage_nl_exe()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    dat = (out / dat_name).resolve()
    dat.write_text(model.to_dat(compact=False), encoding="utf-8")
    proc = subprocess.run([str(exe), str(dat)], capture_output=True, text=True,
                          cwd=str(out.resolve()), timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(
            f"example_3d_stage_nl 失败 (code={proc.returncode}):\n"
            f"{proc.stdout}\n{proc.stderr}")
    result = Path(str(dat) + ".nlstage.txt")
    if not result.is_file():
        raise RuntimeError(f"未找到结果文件 {result}\n{proc.stdout}")
    return parse_nlstage(result.read_text(encoding="utf-8"))


def as_solve_result(model, rows: list[dict], *, dat_path: Path) -> SolveResult:
    """把非线性阶段结果收成界面用的 SolveResult。

    梁端内力取最后一个阶段。位移包络是各阶段全量位移的最小和最大，
    不是线性增量的包络。无索时活载包络与线性内核相同；有索时不输出。
    """
    dat_path = Path(dat_path)
    text = dat_path.read_text(encoding="utf-8") if dat_path.is_file() else ""
    env: dict[tuple[int, int], tuple[float, float]] = {}
    for row in rows:
        for key, u in row["u"].items():
            lo, hi = env.get(key, (u, u))
            env[key] = (min(lo, u), max(hi, u))
    final = dict(rows[-1]["u"]) if rows else {}
    girder_ends: dict[tuple[int, int], dict[str, float]] = {}
    final_beam: dict[tuple[int, str], dict[str, float]] = {}
    if rows:
        for eid, ends in rows[-1].get("beams", {}).items():
            for end, name in ((1, "i"), (2, "j")):
                src = ends.get(end)
                if not src:
                    continue
                force = {"N": src["N"], "My": src["My"], "Vz": src["Vz"]}
                girder_ends[(eid, end)] = dict(force)
                final_beam[(eid, name)] = {
                    "N": src["N"], "My": src["My"], "Vz": src["Vz"],
                    "Nmin": src["N"], "Nmax": src["N"],
                    "MyMin": src["My"], "MyMax": src["My"],
                    "VzMin": src["Vz"], "VzMax": src["Vz"],
                }
    tdes: dict[int, float] = {}
    stage_cables: dict[tuple[int, int], dict[str, float]] = {}
    for stage, row in zip(model.stages, rows):
        for eid, target in stage.tensions.items():
            tdes[int(eid)] = float(target)
        for eid, cab in row["cables"].items():
            T = float(cab["T"])
            des = tdes.get(int(eid), 0.0)
            stage_cables[(int(stage.id), int(eid))] = {
                "T": T,
                "L0": float(cab["L0"]),
                "target": float(stage.tensions.get(eid, des)),
                "dev": abs(T - des) / des if des else 0.0,
                "Tdes": des,
                "devDes": abs(T - des) / des if des else 0.0,
            }
    sibling = Path(str(dat_path) + ".nlstage.txt")
    nl_text = sibling.read_text(encoding="utf-8", errors="replace") if sibling.is_file() else ""
    lane_impact, live_beam, live = parse_nl_live(nl_text)
    has_cable = any(getattr(t, "kind", "") == "CABLE"
                    for t in getattr(model, "etypes", []) or [])
    stats = model.stats() if hasattr(model, "stats") else {}
    meta = {}
    if lane_impact.get("f1_vertical"):
        meta = {
            "f1_vertical": float(lane_impact["f1_vertical"]),
            "impact_mu": float(lane_impact.get("impact_mu") or 0.0),
            "impact_factor": float(lane_impact.get("impact_factor") or 1.0),
        }
    res = SolveResult(
        kind="grillage3d",
        title=str(getattr(model, "title", "") or ""),
        solver="example_3d_stage_nl.exe",
        dat_text=text,
        dat_path=str(dat_path),
        ok=all(r["converged"] for r in rows) if rows else False,
        converged=all(r["converged"] for r in rows) if rows else False,
        stats=dict(stats),
        disp=env,
        final_disp=final,
        stage_cables=stage_cables,
        girder_ends=girder_ends,
        final_beam=final_beam,
        live_beam=live_beam,
        live=live,
        meta=meta,
    )
    note = (
        "本结果来自非线性施工阶段：每个阶段在当前已激活结构上做全量平衡，"
        "索力由无应力索长迭代命中。位移不能按线性阶段的增量相加。"
        "梁端内力取最后一个阶段的全量平衡，不是各阶段增量之和。"
        "预应力按等效荷载留在张拉之后的每个阶段里，本结果不单独列出束力。"
    )
    tangent_live = "basis=tangent" in nl_text
    if live_beam and tangent_live:
        note += "末阶段活载弯矩在收敛构形的切线刚度上计算。有索时不用参考几何上的线弹性影响线。"
    elif live_beam:
        note += "无索单元的活载包络与线性阶段内核相同，弯矩和挠度乘同一套 1+μ。"
    elif live or meta:
        note += "末阶段活载在收敛构形的切线刚度上计算，冲击系数用同一套切线的竖向基频。"
    elif has_cable:
        note += "有索的非线性阶段还不输出活载包络：那需要成桥态切线刚度，不能用参考几何上的线弹性影响线。"
    else:
        note += "本模型没有车道，没有活载包络。"
    res.diagnostics.append(note)
    return res


def solve_staged_result(model, out_dir: str | Path,
                        dat_name: str = "stage_nl.dat",
                        timeout: int = 600) -> SolveResult:
    rows = solve_stages_nonlinear(model, out_dir, dat_name=dat_name, timeout=timeout)
    dat = (Path(out_dir) / dat_name).resolve()
    return as_solve_result(model, rows, dat_path=dat)
