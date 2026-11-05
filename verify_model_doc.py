"""ModelDoc（C 方案）回归验证。

判据不是"能跑通"，而是：
1. 梁路径与改造前逐位一致（不引入数值偏移）；
2. 桥路径的 .dat 与直接调建造器**逐字节相同**；
3. 改参数后拓扑真的重建（C 方案的核心承诺，不是把模型冻住）；
4. 工程文件可存可读，往返不丢参数；
5. capability() 如实反映能力边界（尤其"计入自重就不能互校"）。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()          # 输出被重定向到管道/文件时，GBK 会崩（见模块说明）

import sys
import tempfile
from pathlib import Path

from bridge_spec import ElemKind, build_arch_bridge, build_truss_bridge
from fe_solver import solve
from fem_eigen_export import spec_to_dat
from model_doc import ModelDoc, classify_text
from nl_parser import parse_nl

PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<28} {detail}")


BEAM_TXT = "一座跨度30米的简支钢梁，矩形截面高1.2米宽0.6米，承受20kN/m均布荷载"


def t_beam_equivalent():
    """梁路径：ModelDoc 与旧管线（parse_nl + ModelSpec）结果必须一致。"""
    doc, src, _ = ModelDoc.from_text(BEAM_TXT)
    spec_old, _, _ = parse_nl(BEAM_TXT)

    r_doc = solve(doc.model)
    r_old = solve(spec_old)
    dm = abs(r_doc["max_moment"] - r_old["max_moment"])
    dv = abs(r_doc["max_deflection"] - r_old["max_deflection"])
    check("梁：内力与旧管线一致", dm < 1e-6 and dv < 1e-12,
          f"ΔM={dm:.2e} Δv={dv:.2e}")

    dat_doc = doc.to_dat()
    dat_old = spec_to_dat(spec_old, target_len=0.5, self_weight=False)
    check("梁：.dat 与旧管线一致", dat_doc == dat_old,
          f"{len(dat_doc)} 字符")


def t_bridge_dat_identical():
    """桥路径：从 params 重建出的 .dat 必须与直接建造器逐字节相同。"""
    doc, _, _ = ModelDoc.from_text("跨度100米的拱桥，矢高20米，9对吊杆，20kN/m均布荷载")
    direct = build_arch_bridge(span=100.0, rise=20.0, n_hanger=9,
                               udl_kN=20.0, point_kN=0.0,
                               hanger_kind=ElemKind.TRUSS, gravity=-9.81)
    check("拱桥：.dat 与建造器一致", doc.to_dat() == direct.to_dat(),
          f"节点 {len(doc.model.nodes)} 单元 {len(doc.model.elements)}")

    doc_t, _, _ = ModelDoc.from_text("跨度60米桁架桥，8个节间，桁高6米")
    # 文本未给均布荷载 → 解析侧按 q=20 kN/m 折算节点荷载：20 × (60/8)
    direct_t = build_truss_bridge(span=60.0, n_panel=8, depth=6.0,
                                  node_load_kN=20.0 * 60.0 / 8.0)
    check("桁架：.dat 与建造器一致", doc_t.to_dat() == direct_t.to_dat(),
          f"节点 {len(doc_t.model.nodes)}")


def t_topology_rebuild():
    """C 方案的核心承诺：改参数 → 拓扑重建，不是把模型冻住。"""
    doc, _, _ = ModelDoc.from_text("跨度100米的拱桥，矢高20米，9对吊杆")
    n_before = len(doc.model.nodes)
    doc.params["n_hanger"] = 15
    doc.build()
    n_after = len(doc.model.nodes)
    check("拱桥：改吊杆数拓扑重建", n_after > n_before, f"{n_before} → {n_after} 节点")

    # 梁：改跨度后支座必须跟着走（旧代码里支座位置会留在原地）
    b, _, _ = ModelDoc.from_text(BEAM_TXT)
    b.params["length"] = 60.0
    b.build()
    xs = sorted(round(s.position, 6) for s in b.model.supports)
    check("梁：改跨度支座跟随", xs == [0.0, 60.0], f"支座位置 {xs}")


def t_json_roundtrip():
    for txt in (BEAM_TXT, "跨度100米的拱桥，矢高20米，9对吊杆，20kN/m"):
        doc, _, _ = ModelDoc.from_text(txt)
        with tempfile.TemporaryDirectory() as td:
            p = doc.save(Path(td) / "proj.json")
            size = p.stat().st_size
            back = ModelDoc.load(p)
        same = back.kind == doc.kind and back.to_dat() == doc.to_dat()
        check(f"工程文件往返（{doc.kind}）", same, f"proj.json {size}B")


def t_validate():
    doc, _, _ = ModelDoc.from_text(BEAM_TXT)
    errs = doc.validate()
    check("梁：合法模型无误报", errs == [], "；".join(errs) or "无")

    doc.params["h"] = 0.0
    check("梁：h=0 被拦下", any("截面高度" in e for e in doc.validate()))

    doc2, _, _ = ModelDoc.from_text(BEAM_TXT)
    doc2.params["h"] = "abc"          # type: ignore[assignment]
    errs2 = doc2.validate()
    check("梁：非法类型不抛异常", bool(errs2), errs2[0][:34] if errs2 else "")

    for txt, label in (("跨度100米的拱桥，矢高20米", "拱桥"),
                       ("跨度60米桁架桥，8个节间", "桁架"),
                       ("跨度90米三跨连续梁桥", "梁桥")):
        d, _, _ = ModelDoc.from_text(txt)
        check(f"{label}：合法模型无误报", d.validate() == [],
              "；".join(d.validate()) or "无")


def t_capability():
    beam, _, _ = ModelDoc.from_text(BEAM_TXT)
    cap = beam.capability()
    check("能力：梁可草算+可互校",
          cap["anastruct"] and cap["cross_check"], str(cap))

    beam.params["self_weight"] = True
    beam.build()
    check("能力：计自重后关掉互校",
          beam.capability()["cross_check"] is False, "anastruct 无重力项")

    arch, _, _ = ModelDoc.from_text("跨度100米的拱桥，矢高20米")
    cap_a = arch.capability()
    check("能力：桥只能走 FEM-Eigen",
          cap_a["anastruct"] is False and cap_a["fem_eigen"] is True, str(cap_a))


def t_classify():
    cases = [
        (BEAM_TXT, ("beam", "2d")),
        ("跨度100米的拱桥", ("arch", "2d")),
        ("60米桁架桥", ("truss", "2d")),
        ("30米简支梁桥", ("beam_bridge", "2d")),
    ]
    ok = all(classify_text(t) == k for t, k in cases)
    check("意图分类", ok, "、".join(f"{k[0]}×{k[1]}" for _, k in cases))
    check("意图分类：人工提示优先", classify_text(BEAM_TXT, "拱桥") == ("arch", "2d"))


def t_beam_bridge_staged():
    """档二：2D 梁桥的自然语言施工阶段 + 车道活载解析。"""
    doc, src, _ = ModelDoc.from_text(
        "三跨连续梁桥总长90米，逐跨施工，一期恒载20kN/m，二期铺装10kN/m，双向四车道")
    check("梁桥：施工/车道/二期解析",
          doc.kind == "beam_bridge" and doc.dimension == "2d"
          and doc.params["staged"] is True and doc.params["lane"] is True
          and doc.params["q2_kN"] == 10.0 and doc.params["n_spans"] == 3,
          f"staged={doc.params['staged']} lane={doc.params['lane']} "
          f"q2={doc.params['q2_kN']} ns={doc.params['n_spans']}")
    spec = doc.model
    check("梁桥：施工序列 + 车道生成",
          len(spec.stages) == 4 and len(spec.lanes) == 1,
          f"阶段{len(spec.stages)} 车道{len(spec.lanes)}")


def main() -> int:
    print("=" * 74)
    print("ModelDoc（C 方案）验证")
    print("=" * 74)
    for fn in (t_beam_equivalent, t_bridge_dat_identical, t_topology_rebuild,
               t_json_roundtrip, t_validate, t_capability, t_classify,
               t_beam_bridge_staged):
        print(f"\n[{fn.__name__}]")
        fn()

    n_pass = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 74)
    print(f"  {n_pass}/{len(PASS)} 通过")
    return 0 if n_pass == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
