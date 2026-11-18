"""verify_viz_labels.py —— SVG 编号显示开关 / 弯矩受拉侧独立守卫。

背景（前处理界面整改第 2、3 项）：
- ``example_viz.cpp`` 生成的 7 张 SVG 原来无条件绘制节点编号、不绘制单元
  编号；现新增 ``--node-labels=1|0`` / ``--elem-labels=1|0`` 两个 CLI 开关，
  默认仍是「显示节点号、不显示单元号」，旧调用 ``example_viz.exe m.dat``
  完全兼容。
- 弯矩图原先把正弯矩画在局部 x 轴上方，与结构力学「画在受拉侧」的习惯
  相反；现只把弯矩图的法向翻转（``flipNormal=true``），正弯矩下缘受拉
  画在梁下方，剪力/轴力/应力图方向不变。

本守卫锁住两组：
A 源码契约：CLI 开关存在、默认值不变、只有弯矩图翻法向；
B 实跑契约：旧调用仍出图、两个开关确实生效、弯矩偏移在梁下方。

**不重算**：不自己拼 .dat，直接调用 ``fem_eigen_export.export_dat``；
不自己拼 SVG，直接读 exe 写出的文件。exe 未编译时只跑 A 组并明确
SKIP，不把缺失当通过。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FEM_ROOT = ROOT.parent / "FEM-Eigen"
SRC = FEM_ROOT / "example_viz.cpp"
EXE = FEM_ROOT / "build" / "example_viz.exe"

PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<32} {detail}")


def source_contract() -> None:
    """A 组：源码契约（不需要 exe，离线可跑）。"""
    print("\n[A 源码契约]")
    check("example_viz.cpp 存在", SRC.is_file(), str(SRC))
    if not SRC.is_file():
        return
    txt = SRC.read_text(encoding="utf-8", errors="replace")

    check("节点编号 CLI 开关", "--node-labels=" in txt)
    check("单元编号 CLI 开关", "--elem-labels=" in txt)
    check("节点号默认显示（旧行为）", "bool showNodeLabels = true" in txt)
    check("单元号默认关闭（不污染）", "bool showElemLabels = false" in txt)
    check("节点编号受开关控制", txt.count("if (showNodeLabels)") >= 2,
          f"{txt.count('if (showNodeLabels)')} 处")
    check("单元编号受开关控制", txt.count("if (showElemLabels)") >= 2,
          f"{txt.count('if (showElemLabels)')} 处")

    check("偏移图使用统一法向符号", "perp(s.p1, s.p2) * normalSign" in txt)
    moment = re.search(r'writeDiagramFigure\(base \+ "_moment\.svg"[\s\S]*?\);', txt)
    check("弯矩图 flipNormal=true",
          bool(moment) and re.search(r",\s*true\s*\)", moment.group(0)) is not None)
    others = re.findall(
        r'writeDiagramFigure\(base \+ "_(?:model|shear|axial|stress_top|stress_bottom)\.svg"'
        r'[\s\S]*?\);', txt)
    check("其余 5 张图不翻法向", len(others) == 5
          and all(re.search(r",\s*true\s*\)", c) is None for c in others),
          f"{len(others)} 处调用")


def exe_contract() -> None:
    """B 组：真实出图（需要已编译的 example_viz.exe）。"""
    print("\n[B 实跑契约]")
    if not EXE.is_file():
        print(f"  SKIP  实跑：{EXE} 未编译（A 组源码契约仍已验证）")
        return

    from fem_eigen_export import _demo_spec, export_dat

    with tempfile.TemporaryDirectory(prefix="viz_labels_") as td:
        work = Path(td)
        dat = export_dat(_demo_spec(), work / "m.dat")

        def run(*flags: str) -> dict[str, str]:
            subprocess.run([str(EXE), dat.name, *flags], cwd=str(work),
                           capture_output=True, text=True, timeout=120, check=True)
            return {p.name: p.read_text(encoding="utf-8", errors="replace")
                    for p in work.glob("m_*.svg")}

        old = run()                                  # 旧调用：一个额外参数都不传
        new = run("--node-labels=0", "--elem-labels=1")

        check("旧调用仍出图（向后兼容）", len(old) >= 5, f"{len(old)} 张 SVG")
        model0 = old.get("m_model.svg", "")
        model1 = new.get("m_model.svg", "")
        check("默认显示节点号", 'font-size="11"' in model0)
        check("默认不显示单元号", "E1</text>" not in model0)
        check("切换后隐藏节点号", 'font-size="11"' not in model1)
        check("切换后显示单元号", "E1</text>" in model1)

        polys = re.findall(r'<polygon points="([^"]+)"', old.get("m_moment.svg", ""))
        ds = []
        for poly in polys:
            pts = [tuple(map(float, t.split(","))) for t in poly.split()]
            if len(pts) >= 2:
                ds.append(pts[1][1] - pts[0][1])     # SVG 屏幕 y：大 = 下方
        down = sum(d > 1e-6 for d in ds)
        check("弯矩画在受拉侧（梁下方）", bool(ds) and down >= len(ds) - 1,
              f"向下 {down}/{len(ds)} 段")


def main() -> int:
    print("=" * 78)
    print("SVG 编号开关 / 弯矩受拉侧守卫（纯离线 + 可选用 exe 实跑）")
    print("=" * 78)
    source_contract()
    exe_contract()
    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(PASS)} 通过")
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
