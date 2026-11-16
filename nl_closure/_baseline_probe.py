"""基线探针：各编排脚本 CASES 参数 -> build -> 导出 -> 逐字节 = 冻结。

验证「技术句只要抽出等价参数，就能逐字节复现冻结证据」这一前提。
只读冻结证据，不写 cases/。
"""
from __future__ import annotations

import sys
from pathlib import Path

BS = Path(r"D:\AI-work\BridgeStudio")
sys.path.insert(0, str(BS))
sys.path.insert(0, str(BS / "cases"))

from ansys_bridge import spec_to_apdl  # noqa: E402
from ansys_bridge_3d import spec3d_to_apdl  # noqa: E402
from ansys_bridge_3d_loads import spec3d_loads_to_apdl  # noqa: E402
from ansys_bridge_cable import spec_mixed_to_apdl  # noqa: E402
from bridge_spec import build_beam_bridge  # noqa: E402
from ansys_bridge_3d import build_box_beam_3d  # noqa: E402
from ansys_bridge_3d_loads import build_box_beam_3d_loads  # noqa: E402
from ansys_bridge_cable import build_cable_stayed_3d, build_arch_bridge_3d  # noqa: E402
from ansys_bridge_tower import build_cable_stayed_tower_3d  # noqa: E402

CASES = BS / "cases"
results = []


def eq(name: str, produced: str, frozen: Path, enc: str = "utf-8") -> bool:
    if not frozen.exists():
        results.append((name, False, "冻结文件缺失"))
        return False
    want = frozen.read_text(encoding=enc)
    ok = produced == want
    results.append((name, ok, f"{len(produced)}B vs {len(want)}B"))
    return ok


# ---- 2D 五例 ----
import run_cases as r2d  # noqa: E402

for key in ("CB01", "CB02", "CB03", "CB04", "CB05"):
    cfg = r2d.CASES[key]
    stem = cfg["stem"]
    d = CASES / cfg["folder"]
    spec = build_beam_bridge(**cfg["kwargs"])
    if cfg.get("setup"):
        cfg["setup"](spec)
    eq(f"{key} .dat", spec.to_dat(), d / (stem + ".dat"))
    eq(f"{key} .inp", spec_to_apdl(spec, jobname=stem, title=cfg["title"]),
       d / (stem + ".inp"), enc="utf-8")

# ---- CB3D02 ----
import run_cases_3d as r3d  # noqa: E402
cfg = r3d.CASES_3D["CB3D02"]
stem = cfg["stem"]
d = CASES / cfg["folder"]
m = build_box_beam_3d(**cfg["kwargs"])
eq("CB3D02 .dat", m.to_dat(compact=False), d / (stem + ".dat"))
for elem, fn in (("BEAM188", stem + ".inp"), ("BEAM4", stem + "_b4.inp")):
    eq(f"CB3D02 .inp({elem})",
       spec3d_to_apdl(m, jobname=stem if elem == "BEAM188" else stem + "_b4",
                      element=elem, title=cfg["title"]),
       d / fn, enc="ascii")

# ---- CB3D03 / CB3D04 ----
import run_cases_cable as rcab  # noqa: E402
for key in ("CB3D03", "CB3D04"):
    cfg = rcab.CASES_CABLE[key]
    stem = cfg["stem"]
    d = CASES / cfg["folder"]
    m = rcab.BUILDERS[cfg["builder"]](**cfg["kwargs"])
    eq(f"{key} .dat", m.to_dat(compact=False), d / (stem + ".dat"))
    eq(f"{key} .inp", spec_mixed_to_apdl(m, jobname=stem, title=cfg["title"]),
       d / (stem + ".inp"), enc="ascii")

# ---- CB3D06 / CB3D07 / CB3D08 ----
import run_cases_3d_loads as rld  # noqa: E402
for key in ("CB3D06", "CB3D07", "CB3D08"):
    cfg = rld.CASES_LOADS[key]
    stem = cfg["stem"]
    d = CASES / cfg["folder"]
    m = build_box_beam_3d_loads(**cfg["kwargs"])
    eq(f"{key} .dat", m.to_dat(compact=False), d / (stem + ".dat"))
    eq(f"{key} .inp", spec3d_loads_to_apdl(m, jobname=stem, title=cfg["title"]),
       d / (stem + ".inp"), enc="ascii")

# ---- CB3D09 ----
import run_cases_tower as rtw  # noqa: E402
m = build_cable_stayed_tower_3d(**rtw.CFG)
d = CASES / "CB3D09_real_tower_3d"
stem = rtw.STEM
eq("CB3D09 .dat", m.to_dat(compact=False), d / (stem + ".dat"))
eq("CB3D09 .inp", spec_mixed_to_apdl(m, jobname=stem, title=rtw.TITLE),
   d / (stem + ".inp"), enc="ascii")

# ---- 汇总 ----
print("=" * 70)
fail = [r for r in results if not r[1]]
for name, ok, info in results:
    print(f"[{'OK  ' if ok else 'FAIL'}] {name:28s} {info}")
print("=" * 70)
print(f"基线复现：{len(results) - len(fail)}/{len(results)} 逐字节一致")
sys.exit(0 if not fail else 1)
