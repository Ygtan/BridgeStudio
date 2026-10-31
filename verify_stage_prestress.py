"""verify_stage_prestress.py
============================
P3 端到端回归：施工阶段预应力（直线筋等效荷载法）的 BridgeStudio ↔ 内核链。

校验内容
--------
1. 离线（建模与导出）
   - PRESTRESS= 正确写出：连续单元压缩成 a-b、loss=1 时不写 :loss、
     loss≠1 时写。
2. 在线（FEM-Eigen 3D）
   - 内核输出 stage prestress force 段，Pe = P·loss 解析正确。
   - SolveResult.stage_prestress 承接、checks 出「有效预加力为正」与合计。

理论基准（内核 test_3d [25] 已锁，这里只验链路）：
   直线筋（常偏心）只给轴压 + 锚固端偏心弯矩，不产生上拱——上拱来自曲线筋。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fem_eigen_3d import fem_eigen_3d_available, solve_3d
from model_spec_3d import (Boundary3D, Element3D, ElemTypeDef3D, Material3D,
                           ModelSpec3D, Node3D, Prestress3D, RealConst3D,
                           Stage3D)

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((ok, name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def _beam_model(loss: float = 0.85) -> ModelSpec3D:
    """单根 10 m 简支梁（沿 x，竖向弯曲绕 y）+ 一根直线束在下缘（e<0）。"""
    m = ModelSpec3D(title="prestress_probe")
    m.nodes = [Node3D(id=1, x=0.0, y=0.0, z=0.0),
               Node3D(id=2, x=10.0, y=0.0, z=0.0)]
    m.materials = [Material3D(id=1, E=2.0e11)]
    m.reals = [RealConst3D(id=1, A=0.01, IY=0.01, IZ=0.01, J=1e-4, G=8.0e10)]
    m.etypes = [ElemTypeDef3D(id=1, kind="BEAM", mat=1, real=1)]
    m.elements = [Element3D(id=1, etype=1, n1=1, n2=2)]
    m.boundaries = [
        Boundary3D(node=1, dofs=(1, 3), value=0.0),
        Boundary3D(node=1, dofs=(4, 4), value=0.0),
        Boundary3D(node=1, dofs=(6, 6), value=0.0),
        Boundary3D(node=2, dofs=(2, 3), value=0.0),
        Boundary3D(node=2, dofs=(4, 4), value=0.0),
        Boundary3D(node=2, dofs=(6, 6), value=0.0),
    ]
    m.stages = [Stage3D(id=1, activate=[1],
                        prestress=[Prestress3D(elems=[1], P=1.0e6, e=-0.5,
                                               loss=loss)])]
    return m


def offline() -> None:
    print("[离线] PRESTRESS= 写出")
    text = _beam_model(loss=0.85).to_dat()
    check("PRESTRESS= 含 P:e:loss（loss≠1 时写）",
          "PRESTRESS=1:1000000:-0.5:0.85" in text, "")
    text1 = _beam_model(loss=1.0).to_dat()
    check("loss=1 时省略 :loss（缺省值不外露）",
          "PRESTRESS=1:1000000:-0.5" in text1
          and ":-0.5:1" not in text1, "")

    # 多单元连续压缩成 a-b
    m = _beam_model()
    m.nodes += [Node3D(id=3, x=20.0, y=0.0, z=0.0),
                Node3D(id=4, x=30.0, y=0.0, z=0.0)]
    m.elements += [Element3D(id=2, etype=1, n1=2, n2=3),
                   Element3D(id=3, etype=1, n1=3, n2=4)]
    m.stages = [Stage3D(id=1, activate=[1, 2, 3],
                        prestress=[Prestress3D(elems=[1, 2, 3], P=1.0e6,
                                               e=-0.5, loss=1.0)])]
    text3 = m.to_dat()
    check("连续单元压缩成 1-3", "PRESTRESS=1-3:1000000:-0.5" in text3, "")


def online() -> None:
    print("[在线] FEM-Eigen 3D")
    raw = solve_3d(_beam_model(), out_dir="out/verify_prestress",
                   dat_name="prestress.dat")
    sp = raw.get("stage_prestress", {})
    check("内核输出 stage prestress force 段", bool(sp),
          str(sp))
    check("Pe = P·loss = 850 kN", sp.get((1, 1), {}).get("Pe", 0.0) == 850000.0,
          f"{sp.get((1, 1), {})}")

    import solver_3d
    from result_3d import SolveResult
    m = _beam_model()
    res = solver_3d.solve(m, out_dir="out/verify_prestress", kind="grillage3d")
    check("SolveResult.stage_prestress 承接",
          res.stage_prestress == {(1, 1): {"Pe": 850000.0}},
          str(res.stage_prestress))
    names = [c.name for c in res.checks(m)]
    check("checks 出「有效预加力为正」",
          any("有效预加力为正" in n for n in names), "")
    check("checks 出「有效预加力合计」",
          any("有效预加力合计" in n for n in names), "")


def main() -> int:
    offline()
    if "--offline" in sys.argv:
        n = sum(1 for r in RESULTS if r[0])
        print(f"\n离线部分: {n}/{len(RESULTS)} 通过")
        return 0 if n == len(RESULTS) else 1
    if not fem_eigen_3d_available():
        print("\n[跳过在线部分] FEM-Eigen 3D 不可用")
        return 1
    online()
    n = sum(1 for r in RESULTS if r[0])
    print("=" * 60)
    print(f"  {n}/{len(RESULTS)} 通过")
    return 0 if n == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
