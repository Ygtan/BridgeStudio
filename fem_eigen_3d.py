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

from buckling_3d import parse_buckling_section
from fem_eigen_export import FEM_EIGEN_DIR
from modal_3d import parse_modal_section

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
          "beam": {elem: {Nmin,Nmax,VyMin,…,MyMin,MyMax,MzMin,MzMax}},
          "live": {stage: (max,min)},
          "final_beam": {elem: {...}}   # 成桥态恒载内力（单一状态）
          "final_disp": {(node,dof): u} # 成桥态位移
          "live_beam": {elem: {MyMin,MyMax,VzMin,VzMax}}}  # 成桥态活载内力包络
          "modal": {...}                  # 各阶频率/周期/参与质量/节点振型
          "buckling": {...}               # 各阶线性屈曲荷载因子 λ / Pcr / 诊断

    梁内力**逐分量**解析（键名不写死）。旧版 summary 只输出 N/Mz，新版输出
    六个分量——竖向受弯的弯矩落在 My 上（纵向梁沿 X，绕局部 y 水平轴），
    只读 Mz 会得到"222 个单元内力全 0"。按 key=value 通用解析后，新旧格式
    都能读，缺的分量就是缺，不补零成"算过了"。

    `final_*` 与 `live_beam` 是荷载组合的输入：组合的两项必须来自**同一个
    构型**（成桥态），所以不能拿 `beam` 的阶段包络去乘 1.2——那份包络里
    混着"某跨还没浇完"的临时内力。
    """
    disp: dict = {}
    beam: dict = {}
    live: dict = {}
    final_beam: dict = {}
    final_disp: dict = {}
    live_beam: dict = {}
    lane_impact: dict = {}
    stage_cables: dict = {}
    stage_prestress: dict = {}
    modal_lines: list[str] | None = None      # None = 内核根本没写 modal 段
    buckling_lines: list[str] | None = None   # None = 内核根本没写 buckling 段

    # 段名按**完整前缀**分派，不做子串匹配："beam force envelope" 与
    # "final stage beam force" 都含 "beam force"，用子串会串段。
    # 新段必须登记在这里：不登记的段头会留在上一个 section 里，buckling
    # 的 REF/L 行会被当成 modal 段的行收进去，污染旧段。
    sections = (
        "stage displacement envelope",
        "beam force envelope",
        "final stage beam force",
        "final stage displacement",
        "stage live envelope",
        "lane impact",
        "live beam envelope",
        "stage cable tension",
        "stage prestress force",
        "modal",
        "buckling",
    )
    section = ""
    re_disp = re.compile(r"^N(\d+) dof(\d+) min=(\S+) max=(\S+)$")
    re_fdisp = re.compile(r"^N(\d+) dof(\d+) u=(\S+)$")
    re_beam_id = re.compile(r"^E(\d+)\s+(.*)$")
    re_end_id = re.compile(r"^E(\d+)\s+([ij])\s+(.*)$")
    # 键名允许字母后跟数字：L0= 这类键（无应力索长）首字母后就是数字，
    # 只写 [A-Za-z]+ 会把它整条丢掉——不报错，只是字段"凭空消失"。
    # 下划线要留着：基频行是 f1_vertical= / impact_mu=。
    re_kv = re.compile(r"([A-Za-z][A-Za-z0-9_]*)=(\S+)")
    re_live = re.compile(r"^STAGE(\d+) max=(\S+) min=(\S+)$")
    re_stage_cable = re.compile(r"^STAGE(\d+)\s+E(\d+)\s+(.*)$")
    re_stage_prestress = re.compile(r"^STAGE(\d+)\s+P(\d+)\s+(.*)$")

    def _beam_row(rest: str) -> dict[str, float]:
        vals: dict[str, float] = {}
        for key, raw in re_kv.findall(rest):
            try:
                vals[key] = float(raw)
            except ValueError:
                continue
        return vals

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        hit = next((s for s in sections if line.startswith(s)), None)
        if hit:
            section = hit
            if hit == "modal":
                modal_lines = []
            elif hit == "buckling":
                buckling_lines = []
            continue
        if section == "stage displacement envelope":
            m = re_disp.match(line)
            if m:
                disp[(int(m.group(1)), int(m.group(2)))] = (
                    float(m.group(3)), float(m.group(4)))
        elif section == "final stage displacement":
            m = re_fdisp.match(line)
            if m:
                final_disp[(int(m.group(1)), int(m.group(2)))] = float(m.group(3))
        elif section == "beam force envelope":
            m = re_beam_id.match(line)
            if m:
                vals = _beam_row(m.group(2))
                if vals:
                    beam[int(m.group(1))] = vals
        elif section in ("final stage beam force", "live beam envelope"):
            # 逐端：键为 (单元号, "i"/"j")。两端弯矩异号，外包后组合会
            # 把 i 端的恒载与 j 端的活载相加。
            m = re_end_id.match(line)
            if m:
                vals = _beam_row(m.group(3))
                if vals:
                    target = final_beam if section.startswith("final") else live_beam
                    target[(int(m.group(1)), m.group(2))] = vals
        elif section == "lane impact":
            for key, raw in re_kv.findall(line):
                try:
                    lane_impact[key] = float(raw)
                except ValueError:
                    continue
        elif section == "stage live envelope":
            m = re_live.match(line)
            if m:
                live[int(m.group(1))] = (float(m.group(2)), float(m.group(3)))
        elif section == "stage cable tension":
            # 索力是**状态量**：键为 (阶段号, 单元号)，逐阶段各一条，不累加、
            # 不做包络。施工阶段调索的成败就看 target 与 T 是否对得上。
            m = re_stage_cable.match(line)
            if m:
                vals = _beam_row(m.group(3))
                if vals:
                    stage_cables[(int(m.group(1)), int(m.group(2)))] = vals
        elif section == "stage prestress force":
            # 每阶段每束的有效预加力 Pe（束号 → P·loss）。与索力一样是
            # 状态量：键 (阶段号, 束号)，只在张拉那一阶段出现，不累加。
            m = re_stage_prestress.match(line)
            if m:
                vals = _beam_row(m.group(3))
                if vals:
                    stage_prestress[(int(m.group(1)), int(m.group(2)))] = vals
        elif section == "modal" and modal_lines is not None:
            # 模态段的行格式由 modal_3d.py 统一解析（与 cable_solver 共用），
            # 这里只负责收行——两个求解器不各写一套正则。
            modal_lines.append(line)
        elif section == "buckling" and buckling_lines is not None:
            buckling_lines.append(line)

    modal = parse_modal_section(modal_lines or [])
    if modal_lines is None:
        modal["error"] = "no_modal_section"
    buckling = parse_buckling_section(buckling_lines or [])
    if buckling_lines is None:
        buckling["error"] = "no_buckling_section"
    return {"disp": disp, "beam": beam, "live": live,
            "final_beam": final_beam, "final_disp": final_disp,
            "live_beam": live_beam, "lane_impact": lane_impact,
            "stage_cables": stage_cables,
            "stage_prestress": stage_prestress,
            "modal": modal, "buckling": buckling}


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
