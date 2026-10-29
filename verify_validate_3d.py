"""validate_model_3d 的故障注入测试。

校验函数最大的风险不是漏检，而是**看起来在工作**：真实模型跑出来 0 错误 0 警告，
很容易被当成「校验通过」，实际上可能所有规则都没生效。所以这里对每个检查项
注入一个具体缺陷，断言它被点名抓到。

每一项都对应一次真实的调试经历（塔顶变刚体、索力反算除零、阶段激活不存在的单元），
不是凭空列的清单。
"""

from __future__ import annotations

import sys

from cable_stayed_3d import build_cable_stayed_3d
from model_spec_3d import (CableSpec3D, Boundary3D, Element3D, NodalLoad3D,
                           Node3D, RealConst3D, validate_model_3d)

PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))


def has(items: list[str], *words: str) -> bool:
    return any(all(w in x for w in words) for x in items)


def main() -> int:
    base, info = build_cable_stayed_3d()
    cable_ids = list(info["cable_ids"])
    deck_node = next(n.id for n in base.nodes if 1000 < n.id < 2000)
    tower_node = next(n.id for n in base.nodes if n.id == 2001)

    # 基线：干净模型不应有错误
    print("\n[0] 基线")
    e, w = validate_model_3d(base)
    check("干净斜拉桥 0 错误", not e, f"{len(e)} 错误 / {len(w)} 警告")

    def fresh():
        return base.model_copy(deep=True)

    # ---------------------------------------------------------------- [1] 引用
    print("\n[1] 引用完整性（pydantic 完全管不了的一类）")

    m = fresh()
    m.elements.append(Element3D(id=99999, etype=1, n1=deck_node, n2=88888))
    e, _ = validate_model_3d(m)
    check("单元引用不存在的节点", has(e, "不存在的节点", "88888"))

    m = fresh()
    m.etypes[0].mat = 777
    e, _ = validate_model_3d(m)
    check("单元类型引用不存在的材料", has(e, "不存在的材料", "777"))

    m = fresh()
    m.etypes[0].real = 888
    e, _ = validate_model_3d(m)
    check("单元类型引用不存在的实常数", has(e, "不存在的实常数", "888"))

    m = fresh()
    m.elements[0].etype = 55
    e, _ = validate_model_3d(m)
    check("单元引用不存在的类型", has(e, "不存在的单元类型", "55"))

    m = fresh()
    m.boundaries.append(Boundary3D(node=44444, dofs=(1, 1)))
    e, _ = validate_model_3d(m)
    check("边界引用不存在的节点", has(e, "边界条件", "44444"))

    m = fresh()
    m.loads.append(NodalLoad3D(node=55555, dof=3, value=-1.0))
    e, _ = validate_model_3d(m)
    check("节点荷载引用不存在的节点", has(e, "荷载", "55555"))

    # ---------------------------------------------------------------- [2] 几何
    print("\n[2] 几何退化（进内核才炸，且报错看不出根因）")

    m = fresh()
    # 把某根索的第二端抬到与第一端同高
    cid = cable_ids[0]
    ce = next(x for x in m.elements if x.id == cid)
    lo = min((ce.n1, ce.n2),
             key=lambda i: next(n.z for n in m.nodes if n.id == i))
    hi = ce.n2 if lo == ce.n1 else ce.n1
    him = next(n for n in m.nodes if n.id == hi)
    lom = next(n for n in m.nodes if n.id == lo)
    him.z = lom.z
    e, _ = validate_model_3d(m)
    check("索两端等高", has(e, "等高"), f"单元 {cid}")

    m = fresh()
    a = next(n for n in m.nodes if n.id == deck_node)
    b = next(n for n in m.nodes if n.id == deck_node + 1)
    b.x, b.y, b.z = a.x, a.y, a.z
    e, _ = validate_model_3d(m)
    check("单元长度为 0", has(e, "长度为 0"))

    m = fresh()
    m.elements[0].n2 = m.elements[0].n1
    e, _ = validate_model_3d(m)
    check("单元起止节点相同", has(e, "起止节点相同"))

    # ---------------------------------------------------------------- [3] 可解性
    print("\n[3] 可解性")

    m = fresh()
    m.boundaries = []
    e, _ = validate_model_3d(m)
    check("无任何约束 → 刚体位移", has(e, "刚体位移"))

    m = fresh()
    m.boundaries[0].dofs = (1, 7)
    e, _ = validate_model_3d(m)
    check("自由度区间越界", has(e, "越界"))

    m = fresh()
    m.stages = []
    m.cable_bridge.cables.append(CableSpec3D(elem=12345, anchor=0, tension=0.0))
    e, _ = validate_model_3d(m)
    check("成桥态引用非索单元", has(e, "不是索"))

    # ---------------------------------------------------------------- [4] 警告
    print("\n[4] 警告级（能算，但几乎肯定是笔误）")

    m = fresh()
    lonely = Node3D(id=66666, x=1.0, y=2.0, z=3.0)
    m.nodes.append(lonely)
    _, w = validate_model_3d(m)
    check("孤立节点", has(w, "未被任何单元引用", "66666"))

    m = fresh()
    src = m.elements[0]
    m.elements.append(Element3D(id=99998, etype=src.etype, n1=src.n1, n2=src.n2))
    _, w = validate_model_3d(m)
    check("重复单元", has(w, "重复单元"))

    m = fresh()
    m.reals[2].W = 0.0
    _, w = validate_model_3d(m)
    check("索 W=0（垂度退化）", has(w, "W=0"))

    m = fresh()
    cable_rc = m.reals[2]
    cable_rc.L0 = 1000.0     # 与任何真实索长都差得远
    _, w = validate_model_3d(m)
    check("无应力索长与几何长度严重不符", has(w, "无应力索长"))

    m = fresh()
    m.reals[0].G = 1.0e6     # E=3.45e10 / (2e6) - 1 = 16.25，超出 0~0.5
    _, w = validate_model_3d(m)
    check("G 与 E 不自洽", has(w, "不自洽"))

    m = fresh()
    dup_node = Node3D(id=base.nodes[0].id, x=9.0, y=9.0, z=9.0)
    m.nodes.append(dup_node)
    e, _ = validate_model_3d(m)
    check("节点编号重复", has(e, "编号重复"))

    # ---------------------------------------------------------------- 汇总
    n_ok = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 60)
    for name, ok, detail in PASS:
        if not ok:
            print(f"  失败: {name}  {detail}")
    print(f"  {n_ok}/{len(PASS)} 通过")
    return 0 if n_ok == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
