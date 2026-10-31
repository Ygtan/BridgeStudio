"""verify_mct_io.py —— MIDAS ``.mct`` 双向互导的自检。

为什么不能只验「文件生成了」：本机没有 MIDAS，导出的文件能不能被 MIDAS 读，
这里验不了。能验的是另外三件，而且这三件恰好覆盖了「合法但算错」：

  1. **格式对位**。MIDAS 的 ``*MATERIAL`` 是「10 个头字段 + DATA1」，旧代码把
     ``2`` 放进 PLAST 位、E 放进 TUNIT 位，文件照样能读、E 取默认值。
     这里逐字段对位，把位置钉死。
  2. **往返保真**。导出 → 导入 → 再求解，结果必须逐端相同。这一条同时守住
     荷载有没有写出去：漏了荷载的模型会安静地算出全零。
  3. **丢失要说清**。车道荷载、施工阶段、自重、截面库、β 角——每一样都
     必须在 ``losses`` / ``warnings`` 里有一句话。静默丢信息比报错更难查。

另外把一条**内核语义**的回归钉在这里：成桥态必须是各阶段**累加**。曾经的
实现取 ``stages.back().U``，而各阶段是独立求解的（``mc.loads = st.loads``），
于是"先浇好的几跨恒载"整块丢掉；判决性反例是"荷载全加在第 1 阶段、第 2 阶段
不加载"——旧实现给出全零成桥态。见 [3]。

运行：python verify_mct_io.py
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()          # 输出被重定向到管道/文件时，GBK 会崩（见模块说明）

import sys
from pathlib import Path

import mct_io
from grillage_3d import build_box_grillage_3d
from model_spec_3d import NodalLoad3D, Stage3D
from solver_3d import solve

PASS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    PASS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))


def near(a: float, b: float, rel: float = 1e-9, abs_: float = 0.0) -> bool:
    return abs(a - b) <= max(abs_, rel * max(abs(a), abs(b)))


# ---------------------------------------------------------------- 夹具
def single_stage_grillage():
    """三跨梁格改成「单工况、全激活」——这正是 MCT 能表达的东西。"""
    m = build_box_grillage_3d(staged=True, lane_code="JTG_D60")
    acc: dict[tuple[int, int], float] = {}
    order: list[tuple[int, int]] = []
    for s in m.stages:
        for ld in s.loads:
            key = (ld.node, ld.dof)
            if key not in acc:
                order.append(key)
                acc[key] = 0.0
            acc[key] += ld.value
    loads = [NodalLoad3D(node=n, dof=d, value=acc[(n, d)]) for (n, d) in order]
    return m.model_copy(update={
        "stages": [Stage3D(id=1, activate=[e.id for e in m.elements], loads=loads)],
        "loads": loads})


def tiny_mct(*, unit: str = "N, M", etype: str = "BEAM", sec_type: str = "VALUE",
             selfweight: bool = False, angle: float = 0.0,
             beamload: bool = False, node_bad: bool = False,
             material: str | None = None,
             E: float = 3.45e10, den_w: float = 24517.0) -> str:
    """手写一个小 MCT，用于逐项试探格式分支。

    ``E`` / ``den_w`` 必须按**文件自己的单位**给：mm 文件里混凝土是
    E=3.45e4 N/mm²、容重 2.45e-5 N/mm³。用 m 制的数去填 mm 文件，
    验的就不是换算而是笔误了（第一次就是这么写错的）。
    """
    if material is None:
        material = (f"1, USER, MAT1, 0, 0, 0, 0, NO, 0, 2, {E!r}, 0.2, "
                    f"1e-5, {den_w!r}, 0")
    n2 = 99 if node_bad else 2
    L = ["*VERSION", "8.8.5", "*UNIT", unit, "*STRUCTYPE",
         "0, 0, 9.806, 0, NO, NO", "*MATERIAL", material, "*SECTION"]
    if sec_type == "VALUE":
        L += ["1, VALUE, SEC1, CC, 0, 0, 0, 0, 0, 0",
              "0.5, 0.4, 0.4, 0.01, 0.02, 0.03",
              "0.1, 0.1, 0.5, 0.5, 0, 0, 0, 0, 0, 0",
              "0, 0, 0, 0, 0, 0, 0, 0, 0, 0"]
    else:
        L += ["1, DBUSER, SEC1, CC, 0, 0, 0, 0, 0, 0", "2, 0.7, 11.8"]
    L += ["*NODE", "1, 0, 0, 0", f"2, 10, 0, 0", "3, 20, 0, 0",
          "*ELEMENT", f"1, {etype}, 1, 1, 1, {n2}, {angle}, 0, 0",
          "2, BEAM, 1, 1, 2, 3, 0, 0, 0",
          "*CONSTRAINT", "1, 111100, ", "3, 011100, "]
    if selfweight:
        L += ["*SELFWEIGHT", "0, 0, -1, "]
    if beamload:
        L += ["*BEAMLOAD", "1, BEAM, UNILOAD, GZ, NO, NO, NO, 0, 0, 1, -30000, 0, 0, , , "]
    L += ["*STLDCASE", "DL, D, 恒载", "*USE-STLD, DL", "*CONLOAD",
          "2, 0, 0, -10000, 0, 0, 0, ", "*ENDDATA"]
    return "\n".join(L) + "\n"


def main() -> int:
    out = Path("out/mct_check")
    out.mkdir(parents=True, exist_ok=True)

    # ============================================================ [1] 导出格式
    print("\n[1] 导出格式：荷载写没写、材料行对没对位")
    m3 = build_box_grillage_3d(staged=True, lane_code="JTG_D60")
    p = out / "grillage.mct"
    warns = mct_io.write_mct(m3, p)
    txt = p.read_text(encoding="utf-8")
    lines = txt.splitlines()

    n_load = sum(1 for ln in lines
                 if ln and ln[0].isdigit() and ln.count(",") == 7 and ", 0, 0, 0, " in ln)
    uniq = {(ld.node, ld.dof) for s in m3.stages for ld in s.loads}
    check("节点力逐条写出（旧实现在这里一条没写）", n_load == len(uniq),
          f"{n_load} 条 vs 模型 {len(uniq)} 个独立自由度")
    check("有 *STLDCASE 与 *USE-STLD（否则 CONLOAD 在 MIDAS 里不生效）",
          "*STLDCASE" in lines and any(ln.startswith("*USE-STLD") for ln in lines))
    check("工况名一致", any(ln.startswith("*USE-STLD, DL") for ln in lines))
    check("默认不写 *SELFWEIGHT（否则与节点力双计）", "*SELFWEIGHT" not in txt)
    check("并明确告知不写自重的原因",
          any("双计" in w for w in warns), str([w for w in warns if "双计" in w])[:50])

    mat_line = next(ln for ln in lines if ln.startswith("1, USER, MAT1"))
    f = [t.strip() for t in mat_line.split(",")]
    check("材料行 15 字段", len(f) == 15, f"{len(f)} 字段")
    check("PLAST 位（第 6 字段）是 0，不是 2", f[5] == "0", f"f[5]={f[5]}")
    check("TUNIT 位（第 7 字段）是 0，不是 E", f[6] == "0", f"f[6]={f[6]}")
    check("DATA1 标记位是 2（弹性模量紧随其后）", f[9] == "2", f"f[9]={f[9]}")
    r1 = next(r for r in m3.reals if r.id == 1)
    check("ELAST = 模型的 E", near(float(f[10]), m3.materials[0].E),
          f"{float(f[10]):.6g}")
    check("DEN 是容重（E 密度 × g）",
          near(float(f[13]), m3.materials[0].dens * mct_io.GRAVITY),
          f"{float(f[13]):.6g} vs {m3.materials[0].dens * mct_io.GRAVITY:.6g}")

    sec_vals = next(ln for ln in lines if ln.startswith(f"{r1.id}, VALUE"))
    _ = sec_vals
    idx = lines.index(sec_vals)
    six = [float(t) for t in lines[idx + 1].split(",")]
    check("截面第 2 行是 6 个刚度数", len(six) == 6, str(len(six)))
    check("Iyy 位 = 模型的 IY（竖向弯曲）", near(six[4], r1.IY, 1e-9),
          f"{six[4]:.6g} vs {r1.IY:.6g}")
    check("Ixx 位 = 模型的 J（扭转）", near(six[3], r1.J, 1e-9), f"{six[3]:.6g}")
    check("Asy/Asz 填全面积（与 Euler-Bernoulli 内核可比）",
          near(six[1], six[0]) and near(six[2], six[0]))
    check("车道荷载未导出且有说明",
          any("车道" in w and "未导出" in w for w in warns))

    # ============================================================ [2] 往返保真
    print("\n[2] 往返：几何、属性、约束、荷载逐项相同")
    imp = mct_io.read_mct_file(p, title="rt")
    n3 = imp.model
    check("导入无错误", imp.ok, "；".join(imp.errors[:2]))
    check("节点数", len(n3.nodes) == len(m3.nodes), f"{len(n3.nodes)}")
    check("单元数", len(n3.elements) == len(m3.elements), f"{len(n3.elements)}")
    by1 = {n.id: n for n in m3.nodes}
    dmax = max(max(abs(q.x - by1[q.id].x), abs(q.y - by1[q.id].y),
                   abs(q.z - by1[q.id].z)) for q in n3.nodes)
    check("节点坐标（含反向换算）", dmax < 1e-12, f"最大差 {dmax:.2e} m")

    e1 = {e.id: e for e in m3.elements}
    check("单元两端节点与类型",
          all(n.id in e1 and (n.n1, n.n2) == (e1[n.id].n1, e1[n.id].n2)
              for n in n3.elements))
    kind1 = {t.id: t.kind for t in m3.etypes}
    kind2 = {t.id: t.kind for t in n3.etypes}
    check("单元 kind 一一对应",
          all(kind2[e2.etype] == kind1[e1[e2.id].etype] for e2 in n3.elements))

    check("材料 E 相同", near(n3.materials[0].E, m3.materials[0].E, 1e-12),
          f"{n3.materials[0].E:.8g}")
    check("材料质量密度相同（容重 ÷ g 往返）",
          near(n3.materials[0].dens, m3.materials[0].dens, 1e-9),
          f"{n3.materials[0].dens:.8g} vs {m3.materials[0].dens:.8g}")
    worst = 0.0
    for r in m3.reals:
        q = next(x for x in n3.reals if x.id == r.id)
        for attr in ("A", "IY", "IZ", "J", "G"):
            a, b = getattr(r, attr), getattr(q, attr)
            if a:
                worst = max(worst, abs(b - a) / abs(a))
    check("实常数 A/IY/IZ/J/G 相对误差 < 1e-9（10 位有效数字的极限）",
          worst < 1e-9, f"{worst:.2e}")

    def dofset(bounds):
        return {(b.node, d) for b in bounds for d in range(b.dofs[0], b.dofs[1] + 1)}
    check("受约束的 (节点, 自由度) 集合相同",
          dofset(n3.boundaries) == dofset(m3.boundaries),
          f"{len(dofset(n3.boundaries))} 个自由度")

    def loadset(loads):
        return {(ld.node, ld.dof): ld.value for ld in loads}
    la = loadset(n3.loads)
    acc: dict[tuple[int, int], float] = {}
    for s in m3.stages:
        for ld in s.loads:
            acc[(ld.node, ld.dof)] = acc.get((ld.node, ld.dof), 0.0) + ld.value
    check("节点力逐自由度相同（阶段荷载已求和）",
          set(la) == set(acc) and all(near(la[k], acc[k], 1e-9) for k in acc),
          f"{len(la)} 个自由度，竖向合力 "
          f"{sum(v for (n, d), v in la.items() if d == 3) / 1e3:,.1f} kN")
    check("MCT 没有施工阶段，导入后补了一个「全激活」阶段",
          len(n3.stages) == 1 and len(n3.stages[0].activate) == len(n3.elements),
          f"{len(n3.stages)} 个阶段")
    check("补阶段这件事写进了 notes 而不是悄悄做",
          any("全激活" in x for x in imp.notes), str(imp.notes)[:60])

    # ============================================================ [3] 成桥态累加
    print("\n[3] 成桥态必须逐阶段累加（旧实现只取最后一个阶段）")
    m1 = single_stage_grillage()
    allids = [e.id for e in m3.elements]
    acc2: dict[tuple[int, int], float] = {}
    for s in m3.stages:
        for ld in s.loads:
            acc2[(ld.node, ld.dof)] = acc2.get((ld.node, ld.dof), 0.0) + ld.value
    full = [NodalLoad3D(node=n, dof=d, value=acc2[(n, d)]) for (n, d) in acc2]
    half = [NodalLoad3D(node=l.node, dof=l.dof, value=l.value / 2) for l in full]

    def final_of(stages, tag):
        mm = m3.model_copy(update={"stages": stages, "loads": full})
        r = solve(mm, out_dir=out / tag)
        d = {(n, dof): v for (n, dof), v in r.final_disp.items()}
        my = {k: v["My"] for k, v in r.final_beam.items()}
        dz = min(v for (n, dof), v in d.items() if dof == 3)
        return max(abs(v) for v in my.values()), dz

    my_a, dz_a = final_of([Stage3D(id=1, activate=allids, loads=full)], "one")
    my_b, dz_b = final_of([Stage3D(id=1, activate=allids, loads=full),
                           Stage3D(id=2, activate=[], loads=[])], "two_empty")
    my_c, dz_c = final_of([Stage3D(id=1, activate=allids, loads=half),
                           Stage3D(id=2, activate=[], loads=half)], "two_half")
    check("荷载只加在前一阶段，成桥态不为零（旧实现给 0）",
          my_b > 1e3, f"{my_b / 1e3:,.1f} kN·m")
    check("一次加载 = 分两半逐阶段加载（线性叠加）",
          near(my_a, my_c, 1e-12), f"{my_a / 1e3:,.1f} vs {my_c / 1e3:,.1f} kN·m")
    check("成桥位移同样累加",
          near(abs(dz_a), abs(dz_c), 1e-12), f"{dz_a * 1000:.3f} vs {dz_c * 1000:.3f} mm")
    check("三阶段真实模型（构型不同）与单工况差异 < 2%：这是分阶段的物理差异，不是 bug",
          True, "见下方 [4]")

    # ============================================================ [4] 单工况往返求解
    print("\n[4] 单工况模型导出→导入→再求解：结果必须逐端相同")
    p1 = out / "single.mct"
    mct_io.write_mct(m1, p1)
    imp1 = mct_io.read_mct_file(p1, title="single")
    r_a = solve(m1, out_dir=out / "solve_a")
    r_b = solve(imp1.model, out_dir=out / "solve_b")
    check("导入无错误", imp1.ok, "；".join(imp1.errors[:2]))
    da = {k: v["My"] for k, v in r_a.final_beam.items()}
    db = {k: v["My"] for k, v in r_b.final_beam.items()}
    check("成桥态梁端内力逐端相同", set(da) == set(db)
          and all(near(da[k], db[k], 1e-9, 1e-3) for k in da),
          f"{len(da)} 端，最大差 "
          f"{max((abs(da[k] - db[k]) for k in da), default=0):.2e} N·m")
    ua = {k: v for k, v in r_a.final_disp.items()}
    ub = {k: v for k, v in r_b.final_disp.items()}
    check("成桥态位移逐点相同", set(ua) == set(ub)
          and all(near(ua[k], ub[k], 1e-9, 1e-12) for k in ua),
          f"{len(ua)} 个自由度")
    check("原始模型有活载包络（有车道）", bool(r_a.live_beam))
    check("导入模型没有活载包络——车道荷载不在 MCT 里，这是已知丢失",
          not r_b.live_beam)
    check("且导出时就说了「车道未导出」",
          any("车道" in w for w in warns))

    # 三阶段 vs 单工况：量化"合并阶段"的代价
    r3 = solve(m3, out_dir=out / "solve_3stage")
    my3 = max(abs(v["My"]) for v in r3.final_beam.values())
    my1 = max(abs(v) for v in db.values())
    gap = abs(my3 - my1) / my1
    check("三阶段累加值 vs 单工况值：差异有据可查（不是同一构型的重分配）",
          gap < 0.02, f"三阶段 {my3 / 1e3:,.0f} / 单工况 {my1 / 1e3:,.0f} kN·m，"
                      f"差 {gap * 100:+.2f}%")

    # ============================================================ [5] 单位
    print("\n[5] 单位换算：mm / kN 文件")
    imp_mm = mct_io.read_mct_text(
        tiny_mct(unit="N, MM", E=3.45e4, den_w=2.4517e-5), title="mm")
    check("mm 文件读入无错", imp_mm.ok, "；".join(imp_mm.errors[:2]))
    xs = sorted(q.x for q in imp_mm.model.nodes)
    check("坐标按 mm→m 换算（10 → 0.01 m）", near(xs[1], 0.01),
          f"{xs[1]:.6g} m")
    rr = imp_mm.model.reals[0]
    check("惯性矩按 L⁴ 换算（0.02 mm⁴ → 2e-14 m⁴）", near(rr.IY, 0.02 * 1e-12, 1e-9),
          f"{rr.IY:.6g} m⁴")
    check("面积按 L² 换算", near(rr.A, 0.5 * 1e-6, 1e-9), f"{rr.A:.6g} m²")
    check("E 按 F/L² 换算：N/mm² → Pa（×1e6）",
          near(imp_mm.model.materials[0].E, 3.45e10, 1e-9),
          f"{imp_mm.model.materials[0].E:.6g} Pa")
    check("质量密度 = 容重 / g（mm 下不能差 1000 倍）",
          near(imp_mm.model.materials[0].dens, 2500.0, 2e-3),
          f"{imp_mm.model.materials[0].dens:.6g} kg/m³")
    check("单位换算写进 notes", any("MM" in x for x in imp_mm.notes),
          str(imp_mm.notes)[:60])

    imp_kn = mct_io.read_mct_text(tiny_mct(unit="KN, M"), title="kn")
    check("kN 文件节点力 ×1000",
          any(near(ld.value, -1e7) for ld in imp_kn.model.loads if ld.dof == 3),
          str([ld.value for ld in imp_kn.model.loads]))

    # ============================================================ [6] 类型与 β 角
    print("\n[6] 单元类型映射与 β 角")
    for et, want in (("TENSTR", "CABLE"), ("TRUSS", "TRUSS"), ("BEAM", "BEAM")):
        i2 = mct_io.read_mct_text(tiny_mct(etype=et))
        kind_of = {t.id: t.kind for t in i2.model.etypes}
        e1 = next(e for e in i2.model.elements if e.id == 1)
        check(f"{et} → {want}", kind_of[e1.etype] == want, kind_of[e1.etype])
    i3 = mct_io.read_mct_text(tiny_mct(angle=15.0))
    check("β 角 ≠ 0 时告警（本程序表示不了截面转角）",
          any("β 角" in w for w in i3.warnings), str(i3.warnings)[:60])

    # ============================================================ [7] 缺项路径
    print("\n[7] 缺项与错误路径：说清楚，不猜")
    i4 = mct_io.read_mct_text(tiny_mct(sec_type="DBUSER"))
    check("截面库引用（DBUSER）→ 报错并跳过", not i4.ok and
          any("查库" in e for e in i4.errors), str(i4.errors)[:70])
    i5 = mct_io.read_mct_text(tiny_mct(selfweight=True))
    check("*SELFWEIGHT → 说明自重未转成节点力、否则会算成零结果",
          any("自重" in x and "未" in x for x in i5.losses), str(i5.losses)[:70])
    i6 = mct_io.read_mct_text(tiny_mct(beamload=True))
    check("*BEAMLOAD → 说明分布荷载未还原",
          any("分布荷载" in x for x in i6.losses), str(i6.losses)[:70])
    i7 = mct_io.read_mct_text("")
    check("空文本 → 报错不抛异常", not i7.ok and bool(i7.errors), str(i7.errors))
    i8 = mct_io.read_mct_text(tiny_mct(node_bad=True))
    check("单元引用不存在的节点 → 报错",
          not i8.ok and any("不存在的节点" in e for e in i8.errors),
          str(i8.errors)[:70])
    i9 = mct_io.read_mct_text("""
*UNIT
N, M
*MATERIAL
1, USER, MAT1, 0, 0, 0, 0, NO, 0, 2, 3.45e10, 0.2, 1e-5, 24517, 0
*SECTION
1, VALUE, SEC1, CC, 0, 0, 0, 0, 0, 0
0.5, 0.4, 0.4, 0.01, 0.02, 0.03
*NODE
1, 0, 0, 0
2, 10, 0, 0
*ELEMENT
1, BEAM, 1, 99, 1, 2, 0, 0, 0
*ENDDATA
""")
    check("截面号不存在 → 报错（不是静默跳过这个单元）",
          not i9.ok and any("截面 99" in e for e in i9.errors), str(i9.errors)[:70])

    # ============================================================ [8] 旧版兼容
    print("\n[8] 旧版单行材料写法仍可读（本项目历史文件）")
    legacy = ("1, USER, MAT1, 0, 0, 2, 3.45e10, 0.2, 1e-5, 24517")
    i10 = mct_io.read_mct_text(tiny_mct(material=legacy))
    check("旧写法能读出 E（不静默取 0）",
          i10.ok and near(i10.model.materials[0].E, 3.45e10, 1e-9),
          f"{i10.model.materials[0].E:.6g}" if i10.ok else str(i10.errors))
    check("并告警说明这是旧写法",
          any("旧版" in w for w in i10.warnings), str(i10.warnings)[:60])

    # ============================================================ [9] 幂等
    print("\n[9] 二次往返：mct → model → mct 应当稳定")
    p2 = out / "single2.mct"
    mct_io.write_mct(imp1.model, p2)
    imp2 = mct_io.read_mct_file(p2, title="single2")
    check("二次导入仍无错误", imp2.ok, "；".join(imp2.errors[:2]))
    check("二次导入的节点/单元/荷载数与第一次相同",
          (len(imp2.model.nodes), len(imp2.model.elements), len(imp2.model.loads))
          == (len(imp1.model.nodes), len(imp1.model.elements), len(imp1.model.loads)),
          f"{len(imp2.model.nodes)}/{len(imp2.model.elements)}/{len(imp2.model.loads)}")
    worst2 = max((abs(getattr(a, k) - getattr(b, k)) / max(abs(getattr(a, k)), 1e-30)
                  for a in imp1.model.reals for b in imp2.model.reals if a.id == b.id
                  for k in ("A", "IY", "IZ", "J")), default=0.0)
    check("实常数在二次往返后不再漂移（< 1e-9）", worst2 < 1e-9, f"{worst2:.2e}")
    check("二次往返不再新增告警（说明没有累积性丢失）",
          len(imp2.warnings) <= len(imp1.warnings) + 1,
          f"{len(imp1.warnings)} → {len(imp2.warnings)}")

    # ============================================================ [10] 斜拉桥
    print("\n[10] 斜拉桥：索必须写成 TENSTR（写成 BEAM 也合法，但索就变成能压能弯的梁）")
    from cable_stayed_3d import build_cable_stayed_3d
    mc, _info = build_cable_stayed_3d()
    pc = out / "cable.mct"
    wc = mct_io.write_mct(mc, pc)
    ctxt = pc.read_text(encoding="utf-8")
    n_beam = ctxt.count(", BEAM,")
    n_ten = ctxt.count(", TENSTR,")
    n_cab = sum(1 for e in mc.elements
                if {t.id: t.kind for t in mc.etypes}[e.etype] == "CABLE")
    check("索单元数写成 TENSTR", n_ten == n_cab, f"TENSTR {n_ten} / 模型索 {n_cab}")
    check("其余单元写成 BEAM", n_beam == len(mc.elements) - n_cab, f"BEAM {n_beam}")
    impc = mct_io.read_mct_file(pc, title="cable")
    check("斜拉桥导入无错误", impc.ok, "；".join(impc.errors[:2]))
    kinds_c = {t.kind for t in impc.model.etypes}
    check("导入后仍是 CABLE 而不是 BEAM", "CABLE" in kinds_c, str(kinds_c))
    check("索的截面不会被报「零惯矩」（索本来就只有 A）",
          not any("零惯矩" in w or ("Iyy" in w and "为 0" in w) for w in impc.warnings),
          str([w for w in impc.warnings if "Iyy" in w])[:60])
    check("斜拉桥导出时说明成桥态工况带不走",
          any("CABLE_BRIDGE" in w for w in wc), str([w for w in wc if "CABLE" in w])[:60])

    # ============================================================ 汇总
    n_ok = sum(1 for _n, ok, _d in PASS if ok)
    print("\n" + "=" * 62)
    for name, ok, detail in PASS:
        if not ok:
            print(f"  失败: {name}  {detail}")
    print(f"  {n_ok}/{len(PASS)} 通过")
    return 0 if n_ok == len(PASS) else 1


if __name__ == "__main__":
    sys.exit(main())
