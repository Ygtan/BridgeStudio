"""
verify_fem_eigen_link.py
========================
验证「X20 自然语言建模 → .dat(new format) → FEM-Eigen 求解 → .svg 可视化」链路是否打通。

用途
----
1. 取 explore-x20 输出的一份 .dat 计算数据文件；
2. 调用 FEM-Eigen 的 example_viz.exe 求解并生成 SVG 可视化；
3. 读回生成的 SVG，校验其为合法 XML、包含 <svg> 根节点，并统计内容；
4. 输出链路通断结论，供 NL-FE(Streamlit 前处理) 对接 FEM-Eigen 时使用。

用法
----
    python verify_fem_eigen_link.py                       # 用内置默认用例（拱桥 100m）
    python verify_fem_eigen_link.py <某个.dat>            # 指定 .dat
    python verify_fem_eigen_link.py --exe <exe路径> <dat> # 指定求解器可执行文件

依赖
----
仅需 Python 标准库（subprocess / xml / pathlib）。不调用任何 API，完全离线。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()          # 输出被重定向到管道/文件时，GBK 会崩（见模块说明）

import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

# ---------------------------------------------------------------- 路径配置
# FEM-Eigen 根目录（C++ 有限元求解器，读取 new-format .dat 并输出 SVG）
# 目录搬过家，统一走 fem_eigen_export 的多候选自动探测，避免两处硬编码漂移。
try:
    from fem_eigen_export import FEM_EIGEN_DIR as _DETECTED_DIR, EXE_VIZ_CANDIDATES as _DETECTED_VIZ
except Exception:
    _DETECTED_DIR = None
    _DETECTED_VIZ = []

FEM_EIGEN_DIR = _DETECTED_DIR

# 可视化求解器可执行文件：读 <base>.dat → 写 <base>_model.svg 等一组 SVG
# 构建产物在多个 build 目录里都有，按优先级依次探测
EXE_CANDIDATES = [p for p in _DETECTED_VIZ if p is not None] or [
    FEM_EIGEN_DIR / "build" / "example_viz.exe",
    FEM_EIGEN_DIR / "build_make" / "example_viz.exe",
    FEM_EIGEN_DIR / "build_local" / "example_viz.exe",
] if FEM_EIGEN_DIR else []

# explore-x20 的输出目录（自然语言建模产出的 .dat）
X20_OUTPUTS = Path(
    r"D:\AI-work\前处理\explore-x20\outputs"
)

# example_viz 固定产出的这组图形（见 example_viz.cpp: 213-271）
EXPECTED_SUFFIXES = [
    "_model.svg",          # 模型几何（节点/单元线框）
    "_disp.svg",           # 位移图（放大）
    "_moment.svg",         # 弯矩图
    "_shear.svg",          # 剪力图
    "_axial.svg",          # 轴力图
    "_stress_top.svg",     # 上缘应力图
    "_stress_bottom.svg",  # 下缘应力图
]


def find_exe(user_exe: str | None = None) -> Path:
    """定位 example_viz.exe。用户显式给出则优先用用户的。"""
    if user_exe:
        p = Path(user_exe)
        if not p.is_file():
            sys.exit(f"[FAIL] 指定的可执行文件不存在: {p}")
        return p
    for c in EXE_CANDIDATES:
        if c.is_file():
            return c
    sys.exit(
        "[FAIL] 未找到 example_viz.exe。请先编译 FEM-Eigen：\n"
        f"  cmake -S {FEM_EIGEN_DIR} -B {FEM_EIGEN_DIR / 'build'} -G Ninja\n"
        f"  cmake --build {FEM_EIGEN_DIR / 'build'}"
    )


def default_dat() -> Path:
    """默认的测试用 .dat：优先 X20 的拱桥样例，退回 FEM-Eigen 自带的同名文件。"""
    for p in (X20_OUTPUTS / "arch_bridge_100m.dat",
              FEM_EIGEN_DIR / "arch_bridge_100m.dat"):
        if p.is_file():
            return p
    sys.exit("[FAIL] 找不到默认测试用的 .dat 文件")


def validate_svg(path: Path) -> tuple[bool, str, int]:
    """校验 SVG：可解析为 XML、根节点是 svg、并返回图元数量。"""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        return False, f"XML 解析失败: {e}", 0
    if not root.tag.endswith("svg"):
        return False, f"根节点不是 <svg>：{root.tag}", 0
    # 统计所有子元素（line/text/rect/path...）作为“画了多少东西”的粗略指标
    n_elements = sum(1 for _ in root.iter()) - 1
    title = root.find(".//{http://www.w3.org/2000/svg}text")
    title_txt = (title.text or "") if title is not None else ""
    return True, title_txt, n_elements


def run_link(dat_path: Path, exe: Path, workdir: Path) -> int:
    """
    执行一次完整链路：复制 .dat 到临时目录 → 求解器生成 SVG → 校验 SVG。
    返回退出码（0=成功）。
    """
    dat_path = Path(dat_path)
    if not dat_path.is_file():
        print(f"[FAIL] .dat 不存在: {dat_path}")
        return 1

    base = dat_path.stem
    print("=" * 68)
    print("链路验证：X20 .dat  →  FEM-Eigen 求解  →  SVG 可视化")
    print("=" * 68)
    print(f"输入 .dat : {dat_path}")
    print(f"求解器    : {exe}")
    print(f"工作目录  : {workdir}\n")

    # --- ① 把 .dat 放进临时目录，避免污染源文件所在目录 ---
    target_dat = workdir / dat_path.name
    shutil.copy2(dat_path, target_dat)

    # --- ② 调用 FEM-Eigen 求解并出图 ---
    print("① 调用求解器 ...")
    proc = subprocess.run(
        [str(exe), target_dat.name],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.stdout.strip():
        print(proc.stdout.rstrip())
    if proc.returncode != 0:
        print(f"[FAIL] 求解器返回码 {proc.returncode}")
        if proc.stderr.strip():
            print(proc.stderr.rstrip()[:2000])
        return proc.returncode
    print("   求解完成\n")

    # --- ③ 读回并校验生成的 SVG ---
    print("② 校验生成的 SVG 可视化文件：")
    print(f"   {'文件':<32}{'大小':>9}{'图元数':>9}  标题")
    print("   " + "-" * 64)
    n_ok = 0
    produced = []
    for suf in EXPECTED_SUFFIXES:
        svg = workdir / f"{base}{suf}"
        if not svg.is_file():
            print(f"   {base + suf:<32}{'缺失':>9}")
            continue
        ok, title, n_el = validate_svg(svg)
        size = svg.stat().st_size
        if ok:
            n_ok += 1
            produced.append(svg)
            print(f"   {base + suf:<32}{size:>8}B{n_el:>9}  {title}")
        else:
            print(f"   {base + suf:<32}{size:>8}B{'—':>9}  [无效] {title}")

    # --- ④ 结论 ---
    print("\n" + "=" * 68)
    if n_ok == len(EXPECTED_SUFFIXES):
        print(f"✅ 链路打通：{n_ok}/{len(EXPECTED_SUFFIXES)} 个 SVG 全部生成且合法")
        print(f"   输出目录：{workdir}")
        print("\n   这就是「前处理交接计算模块」的实证：")
        print("   X20 的 .dat(new format) 无需任何格式转换，")
        print("   可被 FEM-Eigen 直接求解并产出可视化图形。")
        return 0
    print(f"⚠️ 链路部分可用：{n_ok}/{len(EXPECTED_SUFFIXES)} 个 SVG 生成成功")
    return 1


def main() -> int:
    args = [a for a in sys.argv[1:]]
    exe_arg = None
    if "--exe" in args:
        i = args.index("--exe")
        exe_arg = args[i + 1]
        del args[i:i + 2]

    exe = find_exe(exe_arg)
    dat = Path(args[0]) if args else default_dat()

    print(f"FEM-Eigen 目录: {FEM_EIGEN_DIR}")
    print(f"默认用例 .dat : {dat}\n")

    with tempfile.TemporaryDirectory(prefix="fem_link_") as tmp:
        return run_link(dat, exe, Path(tmp))


if __name__ == "__main__":
    raise SystemExit(main())
