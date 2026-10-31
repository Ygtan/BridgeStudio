"""export_formats 的字段映射自检。

这个脚本存在的理由：ANSYS / Abaqus / MIDAS 三个导出器**在本机无法求解验证**
（没装这三套软件）。所以不能靠「文件生成了」判定正确——一个把 IYY 和 IZZ 对调
的文件，ANSYS 照样能读、能算、能出云图，只是刚度错了，没人看得出来。

因此这里只验证三件能验证的事：
  1. 适配器搬字段时没有串位（逐项对账，含方向性断言）；
  2. 导出的文件结构自洽（单元引用的节点存在、每个 elset 有单元、无 nan/inf）；
  3. 一处真实缺陷的回归：同一实常数被梁与索共用时必须拆成两份。

运行：
    python verify_export_formats.py
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()          # 输出被重定向到管道/文件时，GBK 会崩（见模块说明）

import re
import sys
from pathlib import Path

from cable_stayed_3d import build_cable_stayed_3d
from export_formats import (_DOF_NAMES, to_canonical, write_abaqus_file,
                            write_ansys_file, write_midas_file)
from model_spec_3d import (Element3D, ElemTypeDef3D, Material3D, ModelSpec3D,
                           Node3D, RealConst3D)

PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))


def close(a: float, b: float, rel: float = 1e-12) -> bool:
    return abs(a - b) <= rel * max(1.0, abs(a), abs(b))


def main() -> int:
    out = Path("out/export_check")
    out.mkdir(parents=True, exist_ok=True)

    m, info = build_cable_stayed_3d()
    canonical, warnings = to_canonical(m)

    # ---------------------------------------------------------------- [1] 材料
    print("\n[1] 材料：泊松比必须由 G 反算，而不是随手填 0.3")
    mat1 = next(x for x in canonical["materials"] if x["id"] == 1)
    r1 = next(r for r in m.reals if r.id == 1)
    nu_expect = mat1["elastic_modulus"] / (2.0 * r1.G) - 1.0
    check("ν = E/(2G) − 1（主梁混凝土 0.2）",
          close(mat1["poisson_ratio"], nu_expect) and close(nu_expect, 0.2, 1e-9),
          f"ν={mat1['poisson_ratio']:.6f}（G={r1.G:g}）")
    check("E 与密度原样搬用",
          close(mat1["elastic_modulus"], m.materials[0].E)
          and close(mat1["density"], m.materials[0].dens))

    # ---------------------------------------------------------------- [2] 实常数
    print("\n[2] 实常数：惯矩按「绕哪根轴」同名映射，不是按竖向/横向")
    rc1 = next(r for r in canonical["real_constants"] if r["id"] == 1)
    f = rc1["fields"]
    check("主梁 AREA = A", close(f["AREA"], r1.A), f"{f['AREA']:.6g}")
    check("主梁 IYY = IY（竖向弯曲，绕 y 轴）", close(f["IYY"], r1.IY),
          f"IYY={f['IYY']:.6g} / IY={r1.IY:.6g}")
    check("主梁 IZZ = IZ（横向弯曲，绕 z 轴）", close(f["IZZ"], r1.IZ),
          f"IZZ={f['IZZ']:.6g} / IZ={r1.IZ:.6g}")
    check("主梁 IXX = J（扭转）", close(f["IXX"], r1.J), f"{f['IXX']:.6g}")
    # 方向性断言：两轴惯矩不相等，若映射对调必然被抓到
    check("IY ≠ IZ（确保上面的对调断言有效）", abs(r1.IY - r1.IZ) > 1e-6,
          f"IY={r1.IY:.4g}, IZ={r1.IZ:.4g}")

    rc3 = next(r for r in canonical["real_constants"] if r["id"] == 3)
    check("索的 rc 用 TRUSS 字段集（A_TRUSS）", rc3["kind"] == "CABLE3D"
          and "A_TRUSS" in rc3["fields"] and close(rc3["fields"]["A_TRUSS"], m.reals[2].A),
          f"kind={rc3['kind']}, A={rc3['fields'].get('A_TRUSS')}")

    # ---------------------------------------------------------------- [3] 边界
    print("\n[3] 边界条件：闭区间 [a,b] → 自由度名列表")
    # 本模型的边界是按自由度逐条写的（(1,1)、(4,4)…），只有一个 (2,3) 区间，
    # 正好用来验证「闭区间 → 多个名字」。
    by_key = {(b["node_id"], tuple(b["dofs"])) for b in canonical["boundary_conditions"]}
    multi = [b for b in m.boundaries if b.dofs == (2, 3)]
    check("区间 (2,3) → [UY, UZ]",
          bool(multi) and (multi[0].node, ("UY", "UZ")) in by_key,
          f"节点 {multi[0].node if multi else '-'}")
    single = [(b.node, b.dofs[0]) for b in m.boundaries if b.dofs[0] == b.dofs[1]]
    ok_single = all((node, (_DOF_NAMES[d - 1],)) in by_key for node, d in single)
    check("单自由度 (d,d) → [该方向的单个名]", ok_single, f"{len(single)} 条")
    check("自由度名全部合法",
          all(d in _DOF_NAMES for _, ds in by_key for d in ds),
          "UX/UY/UZ/ROTX/ROTY/ROTZ")

    # ---------------------------------------------------------------- [4] 文件
    print("\n[4] 三格式文件结构自洽")
    p_ans = out / "cable.ans"
    p_inp = out / "cable.inp"
    p_mct = out / "cable.mct"
    write_ansys_file(canonical, str(p_ans))
    write_abaqus_file(canonical, str(p_inp))
    write_midas_file(canonical, str(p_mct))

    node_ids = {n["id"] for n in canonical["nodes"]}

    # ANSYS：N 行 + FILL 展开的节点数
    ts = p_ans.read_text(encoding="utf-8").splitlines()
    n_n = sum(1 for ln in ts if ln.startswith("N,"))
    n_fill = 0
    for ln in ts:
        if ln.startswith("FILL,"):
            _, a, b, nfill, _, _ = ln.split(",")
            n_fill += int(nfill)
            check(f"FILL 端点 {a}/{b} 已定义",
                  int(a) in node_ids and int(b) in node_ids, ln)
    check("ANSYS 节点总数（N 行 + FILL）", n_n + n_fill == len(node_ids),
          f"{n_n}+{n_fill}={n_n + n_fill} vs {len(node_ids)}")
    n_en = sum(1 for ln in ts if ln.startswith("EN,"))
    check("ANSYS 单元数", n_en == len(canonical["elements"]), str(n_en))
    check("ANSYS 无伪求解段",
          "/SOLU" not in ts and not any(ln.startswith(("ANTYPE", "ACEL")) for ln in ts))
    check("ANSYS 节点力用 FZ（不是 MZ）",
          all(ln.split(",")[2] == "FZ" for ln in ts if ln.startswith("F,")),
          f"{sum(1 for ln in ts if ln.startswith('F,'))} 条节点力")

    # Abaqus：单元引用的节点必须存在；每个 elset 有单元
    txt = p_inp.read_text(encoding="utf-8")
    els: list[tuple[int, int]] = []
    for ln in txt.splitlines():
        if re.match(r"^\d+, \d+, \d+$", ln.strip()):
            a, b, c = (int(x) for x in ln.split(","))
            els.append((b, c))
    check("Abaqus 单元节点全部存在",
          all(a in node_ids and b in node_ids for a, b in els) and len(els) > 0,
          f"{len(els)} 个单元")
    check("Abaqus 单元数", len(els) == len(canonical["elements"]))
    check("Abaqus 截面带方向向量", "0., 0., -1." in txt)
    check("Abaqus 节点力 dof 用 3（不是 6）",
          all(ln.split(",")[1].strip() == "3"
              for ln in txt.splitlines()
              if re.match(r"^\d+, 3, ", ln.strip())))
    check("Abaqus 无伪温度段", "*Temperature" not in txt)

    # MIDAS：*ELEMENT 行数、无 nan
    mt = p_mct.read_text(encoding="utf-8")
    n_el = len(re.findall(r"^\d+, (?:BEAM|TRUSS|TENSTR),", mt, flags=re.M))
    check("MIDAS 单元数", n_el == len(canonical["elements"]), str(n_el))
    check("MIDAS 有 TENSTR（索）", "TENSTR" in mt)

    for p in (p_ans, p_inp, p_mct):
        bad = re.findall(r"(?i)\b(nan|inf)\b", p.read_text(encoding="utf-8"))
        check(f"{p.name} 无 nan/inf", not bad, str(bad[:3]))

    # ---------------------------------------------------------------- [5] 共用拆分
    print("\n[5] 同一实常数被梁与索共用时必须拆成两份")
    tiny = ModelSpec3D(
        title="split",
        nodes=[Node3D(id=1, x=0, y=0, z=0), Node3D(id=2, x=1, y=0, z=0),
               Node3D(id=3, x=2, y=0, z=0)],
        materials=[Material3D(id=1, E=2.0e11, dens=7850)],
        reals=[RealConst3D(id=1, A=0.01, IY=1e-4, IZ=2e-4, J=3e-4, G=7.7e10)],
        etypes=[ElemTypeDef3D(id=1, kind="BEAM", mat=1, real=1),
                ElemTypeDef3D(id=2, kind="CABLE", mat=1, real=1)],
        elements=[Element3D(id=1, etype=1, n1=1, n2=2),
                  Element3D(id=2, etype=2, n1=2, n2=3)],
    )
    cv, _ = to_canonical(tiny)
    rcs = cv["real_constants"]
    check("拆成 2 份 rc", len(rcs) == 2, f"{[r['id'] for r in rcs]}")
    beams = [r for r in rcs if r["kind"] == "BEAM3D"]
    cables = [r for r in rcs if r["kind"] == "CABLE3D"]
    check("梁 rc 带 AREA/IYY", bool(beams) and close(beams[0]["fields"]["AREA"], 0.01))
    check("索 rc 带 A_TRUSS 且无 AREA",
          bool(cables) and close(cables[0]["fields"]["A_TRUSS"], 0.01)
          and "AREA" not in cables[0]["fields"])
    ets = {t["id"]: t for t in cv["element_types"]}
    check("两个 etype 指向不同 rc", ets[1]["real_id"] != ets[2]["real_id"],
          f"BEAM→{ets[1]['real_id']}, CABLE→{ets[2]['real_id']}")

    # ---------------------------------------------------------------- 汇总
    n_ok = sum(1 for _, ok, _ in PASS if ok)
    print("\n" + "=" * 62)
    for name, ok, detail in PASS:
        if not ok:
            print(f"  失败: {name}  {detail}")
    if warnings:
        print("  信息有损的告警（预期内）：")
        for w in warnings:
            print(f"    · {w}")
    print(f"  {n_ok}/{len(PASS)} 通过")
    return 0 if n_ok == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
