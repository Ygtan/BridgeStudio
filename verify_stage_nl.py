"""非线性施工阶段的文件接口，对拍悬臂梁闭式解和目标索力。

模型与 FEM-Eigen test_3d_nl 的 testNonlinearStageCable 相同：
10 m 悬臂，端部力 -5000 N，第二阶段竖向索张拉到 20 kN。
界面在「悬臂施工 + 非线性施工阶段」或 3D 大跨自动判定时走 `stage_nl`
注册表内核（就是本脚本测的 exe）；未打开该开关的悬臂斜拉仍走线性阶段。
本脚本只测文件接口，不替界面决定走哪条链路。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import sys
from pathlib import Path

from fem_eigen_stage_nl import (  # noqa: E402
    find_stage_nl_exe, solve_staged_result, solve_stages_nonlinear,
)
from model_spec_3d import (
    Boundary3D, Element3D, ElemTypeDef3D, Material3D, ModelSpec3D,
    NodalLoad3D, Node3D, RealConst3D, Stage3D,
)

PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<28} {detail}")


def cantilever() -> ModelSpec3D:
    return ModelSpec3D(
        title="非线性施工阶段悬臂索",
        nodes=[
            Node3D(id=1, x=0, y=0, z=0),
            Node3D(id=2, x=10, y=0, z=0),
            Node3D(id=3, x=10, y=0, z=8),
        ],
        materials=[
            Material3D(id=1, E=3.0e10),
            Material3D(id=2, E=1.95e11),
        ],
        reals=[
            RealConst3D(id=1, A=0.5, IY=1.0e-3, IZ=1.0e-3, J=1.0e-3, G=1.2e10),
            RealConst3D(id=2, A=2.0e-4),
        ],
        etypes=[
            ElemTypeDef3D(id=1, kind="BEAM", mat=1, real=1),
            ElemTypeDef3D(id=2, kind="CABLE", mat=2, real=2),
        ],
        elements=[
            Element3D(id=1, etype=1, n1=1, n2=2),
            Element3D(id=2, etype=2, n1=2, n2=3),
        ],
        boundaries=[
            Boundary3D(node=1, dofs=(1, 6)),
            Boundary3D(node=3, dofs=(1, 6)),
        ],
        stages=[
            Stage3D(id=1, activate=[1],
                    loads=[NodalLoad3D(node=2, dof=3, value=-5000.0)]),
            Stage3D(id=2, activate=[2], tensions={2: 20000.0}),
        ],
    )


def main() -> int:
    print("=" * 74)
    print("非线性施工阶段文件接口")
    print("=" * 74)
    try:
        find_stage_nl_exe()
    except FileNotFoundError as e:
        print(e)
        return 1

    stages = solve_stages_nonlinear(
        cantilever(), Path("out") / "verify_stage_nl", dat_name="cantilever.dat")
    check("两个阶段", len(stages) == 2 and all(s["converged"] for s in stages),
          f"{len(stages)} 阶段")
    uz1 = stages[0]["u"].get((2, 3))
    uz2 = stages[1]["u"].get((2, 3))
    euler = -5000.0 * 10.0 ** 3 / (3.0 * 3.0e10 * 1.0e-3)
    check("阶段 1 挠度接近欧拉解",
          uz1 is not None and abs(uz1 / euler - 1.0) < 0.02,
          f"uz1={uz1} euler={euler:.5f}")
    cable = stages[1]["cables"].get(2, {})
    T = cable.get("T")
    check("阶段 2 索力命中 20 kN",
          T is not None and abs(T / 20000.0 - 1.0) < 0.01,
          f"T={T}")
    check("索把梁端往上拉",
          uz1 is not None and uz2 is not None and uz2 > uz1,
          f"uz1={uz1} uz2={uz2}")
    ends = stages[0]["beams"].get(1, {})
    mys = [abs(ends.get(1, {}).get("My", 0.0)), abs(ends.get(2, {}).get("My", 0.0))]
    vzs = [abs(ends.get(1, {}).get("Vz", 0.0)), abs(ends.get(2, {}).get("Vz", 0.0))]
    check("阶段 1 固端弯矩为 PL",
          ends and abs(max(mys) - 50000.0) / 50000.0 < 0.02 and min(mys) < 500.0,
          f"My={mys}")
    check("阶段 1 剪力为端部力",
          ends and all(abs(v - 5000.0) / 5000.0 < 0.02 for v in vzs),
          f"Vz={vzs}")

    # ---- 注册表链路：stage_nl 正式登记后，界面与脚本必须同源 ----
    from solver_3d import require, solve as solve_registered

    spec = require("stage_nl")
    check("stage_nl 已注册且可用", spec.available(), spec.exe_name)
    check("stage_nl 结果 kind 仍是 grillage3d",
          spec.result_key == "grillage3d", spec.result_key)
    m = cantilever()
    reg = solve_registered(m, kind="stage_nl",
                           out_dir=Path("out") / "verify_stage_nl",
                           dat_name="registry.dat")
    legacy = solve_staged_result(
        m, out_dir=Path("out") / "verify_stage_nl", dat_name="legacy.dat")
    check("注册表链路 solver 名来自 spec",
          reg.solver == spec.exe_name and reg.kind == "grillage3d",
          f"{reg.solver} / {reg.kind}")
    check("两条链路 .dat 文本一致",
          reg.dat_text == legacy.dat_text and bool(reg.dat_text))
    check("两条链路阶段索力一致",
          reg.stage_cables == legacy.stage_cables and bool(reg.stage_cables))
    check("两条链路位移包络一致", reg.disp == legacy.disp and bool(reg.disp))
    check("两条链路梁端内力一致",
          reg.girder_ends == legacy.girder_ends and bool(reg.girder_ends))
    check("注册表链路带出两阶段内层 Newton",
          len(reg.newton_traces()) == 2, str(len(reg.newton_traces())))

    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print(f"\n  {n_pass}/{len(PASS)} 通过")
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
