"""verify_stage_tension_2d.py
=============================
第五类工程性老坑 ②：界面「逐跨施工」补齐张拉入口的端到端回归。

以前 `beam_bridge` 的 `staged=True` 只拆阶段 + 二期铺装，阶段预应力
（`*STAGE_PRESTRESS`）内核早就支持，BridgeStudio 却既没有字段、也不写
`.dat`、界面更没有控件，用户只能手写 `.dat` 或调 API。本脚本锁住这条链：

1. 离线：默认口径逐位不变；P>0 时每跨阶段写一条 `*STAGE_PRESTRESS`；
   非法参数（未开 staged / 单跨 / loss 越界）硬报错，不静默忽略。
2. ModelDoc：DEFAULTS / FIELDS / `param_rows()` / `validate()` / `to_dat()`
   走的是同一条链，控件只在开了逐跨施工时出现。
3. live：`example_stage.exe` 能解带阶段预应力的 `.dat`；无外荷载时三跨
   成桥态轴力 = −P·loss（每跨一束张拉，等效荷载法的自平衡力对），
   端部偏心束产生非零弯矩。中间跨两端都不锁 dof1，内核若仍抛错会在这里挂掉。

理论口径：等效荷载法把 Pe=P·loss 换成梁组轴向缩短与端部偏心弯矩，
不是 TENDON 的筋-混凝土协同，也不含 JTG 3362 分项损失。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bridge_spec import build_beam_bridge
from model_doc import DEFAULTS, ModelDoc
from solve_2d import solve_2d, solve_2d_available

OUT = Path(__file__).parent / "out" / "verify_stage_tension_2d"
RESULTS: list[tuple[bool, str, str]] = []

# 无外荷载：让成桥态内力只由预应力产生，oracle 才能写成干净闭式解。
BASE = dict(span=90.0, n_spans=3, udl_kN=0.0, gravity=0.0)
PE = 1000.0e3           # 1000 kN/跨


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((ok, name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def _prestress_block(dat: str) -> list[str]:
    """取 `*STAGE_PRESTRESS` 段的数据行（不含段头/注释/空行）。"""
    if "*STAGE_PRESTRESS\n" not in dat:
        return []
    tail = dat.split("*STAGE_PRESTRESS\n", 1)[1]
    block = tail.split("\n\n", 1)[0]
    return [l.strip() for l in block.splitlines()
            if l.strip() and not l.strip().startswith(("!", "*"))]


def offline() -> None:
    print("\n[1] 离线：默认口径、段格式与非法参数")
    plain = build_beam_bridge(**BASE, staged=True)
    default = build_beam_bridge(**BASE, staged=True,
                                stage_tension_kN=0.0,
                                stage_tension_e=0.0,
                                stage_tension_loss=1.0)
    check("默认张拉力 0 → .dat 逐位不变",
          plain.to_dat() == default.to_dat())
    check("默认不写 *STAGE_PRESTRESS",
          "*STAGE_PRESTRESS" not in default.to_dat())

    spec = build_beam_bridge(**BASE, staged=True,
                             stage_tension_kN=1000.0,
                             stage_tension_e=0.4,
                             stage_tension_loss=0.85)
    rows = _prestress_block(spec.to_dat())
    check("3 跨各写一条阶段预应力", len(rows) == 3, f"{len(rows)} 行")
    check("阶段号 1/2/3 逐跨递增",
          [r.split()[0] for r in rows] == ["1", "2", "3"],
          " ".join(r.split()[0] for r in rows))
    check("每跨 ELEM 为连续单元段",
          all(f"ELEM={a}" in r for r, a in zip(rows, ["1-30", "31-60", "61-90"])),
          " / ".join(r.split()[1] for r in rows))
    check("P 换算成 N 且写 LOSS",
          all("P=1e+06" in r and "LOSS=0.85" in r for r in rows),
          rows[0][:60] if rows else "—")
    check("端部偏心 E1=E2=e 写出",
          all("E1=0.4" in r and "E2=0.4" in r and "F=0" in r for r in rows))
    check("每阶段恰好挂一条 prestress",
          len(spec.stages) == 3 and all(len(s.prestresses) == 1 for s in spec.stages))
    check("有效系数进数据类",
          all(abs(s.prestresses[0].loss - 0.85) < 1e-12 for s in spec.stages))

    bad = [
        (dict(staged=False, n_spans=3, stage_tension_kN=1000.0), "未开逐跨施工"),
        (dict(staged=True, n_spans=1, stage_tension_kN=1000.0), "单跨"),
        (dict(staged=True, n_spans=3, stage_tension_kN=1000.0,
              stage_tension_loss=1.5), "loss>1"),
        (dict(staged=True, n_spans=3, stage_tension_kN=1000.0,
              stage_tension_loss=0.0), "loss=0"),
    ]
    for kw, label in bad:
        try:
            build_beam_bridge(**{**BASE, **kw})
            check(f"非法参数硬报错：{label}", False, "静默通过了")
        except ValueError as e:
            check(f"非法参数硬报错：{label}", True, str(e)[:36])


def doc_chain() -> None:
    print("\n[2] ModelDoc：默认参数、控件显隐、参数校验、.dat 同源")
    d = DEFAULTS["beam_bridge"]
    check("DEFAULTS 三个张拉键且默认不改口径",
          d.get("stage_tension_kN") == 0.0
          and d.get("stage_tension_e") == 0.0
          and d.get("stage_tension_loss") == 1.0)

    doc = ModelDoc("beam_bridge", dimension="2d")
    keys = lambda x: [f["key"] for row in x.param_rows() for f in row]
    check("未开逐跨施工时控件隐藏",
          "stage_tension_kN" not in keys(doc))
    doc.params["staged"] = True
    doc.params["n_spans"] = 3
    check("开了逐跨施工后三个控件齐全",
          all(k in keys(doc) for k in ("stage_tension_kN", "stage_tension_e",
                                       "stage_tension_loss")))

    doc.params.update(stage_tension_kN=1000.0, stage_tension_loss=0.85,
                      stage_tension_e=0.4)
    check("validate 通过", not doc.validate(), str(doc.validate())[:60])
    check("ModelDoc.to_dat 写出同一段",
          len(_prestress_block(doc.to_dat())) == 3)

    doc.params["h"] = 2.0
    doc.params["stage_tension_e"] = 2.0
    errs = doc.validate()
    check("偏心超出截面半高被拦下",
          any("钢束偏心" in e for e in errs), "；".join(errs)[:60])
    doc.params["stage_tension_e"] = 0.4
    doc.params["stage_tension_loss"] = 1.5
    check("loss>1 被拦下",
          any("总损失系数" in e for e in doc.validate()),
          "；".join(doc.validate())[:60])
    doc.params["stage_tension_loss"] = 0.85
    doc.params["staged"] = False
    check("未开 staged 却设张拉力被拦下",
          any("逐跨张拉" in e for e in doc.validate()),
          "；".join(doc.validate())[:60])


def live() -> None:
    print("\n[3] live：example_stage.exe 求解阶段预应力")
    if not solve_2d_available():
        check("2D 施工内核可用", False, "未找到 example_stage.exe")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        r0 = solve_2d(build_beam_bridge(**BASE, staged=True),
                      out_dir=OUT / "no_tension", dat_name="no_tension.dat")
        r1 = solve_2d(build_beam_bridge(**BASE, staged=True,
                                        stage_tension_kN=1000.0),
                      out_dir=OUT / "tension", dat_name="tension.dat")
    except Exception as e:                       # noqa: BLE001 - 要打印内核报错
        check("live 求解不抛异常", False, f"{type(e).__name__}: {str(e)[:160]}")
        return
    check("live 求解不抛异常", True)
    check("3 个施工阶段全部解出",
          r1.stats.get("stages_solved") == 3, str(r1.stats.get("stages_solved")))

    n0 = [v["N"] for (e, end), v in r0.final_beam.items() if end == "i"]
    n1 = [v["N"] for (e, end), v in r1.final_beam.items() if end == "i"]
    check("无张拉对照：成桥态轴力≈0",
          bool(n0) and max(abs(x) for x in n0) < 1.0,
          f"max|N|={max((abs(x) for x in n0), default=0):.3g}")
    check("逐跨张拉：三跨成桥态轴力≈−Pe",
          bool(n1) and max(abs(x + PE) for x in n1) < 1e-3 * PE,
          f"N∈[{min(n1):.3g}, {max(n1):.3g}]" if n1 else "—")

    try:
        r2 = solve_2d(build_beam_bridge(**BASE, staged=True,
                                        stage_tension_kN=1000.0,
                                        stage_tension_loss=0.85,
                                        stage_tension_e=0.4),
                      out_dir=OUT / "tension_loss", dat_name="tension_loss.dat")
    except Exception as e:                       # noqa: BLE001
        check("loss/e 组合 live 求解不抛异常", False, str(e)[:120])
        return
    n2 = [v["N"] for (e, end), v in r2.final_beam.items() if end == "i"]
    m2 = [abs(v["My"]) for v in r2.final_beam.values()]
    check("loss=0.85 → 轴力≈−0.85Pe",
          bool(n2) and max(abs(x + 0.85 * PE) for x in n2) < 1e-3 * PE,
          f"N∈[{min(n2):.3g}, {max(n2):.3g}]" if n2 else "—")
    check("偏心束 e=0.4 m 产生非零端弯矩",
          bool(m2) and max(m2) > 1.0e3,
          f"max|My|={max(m2, default=0):.3g} N·m")


def main() -> int:
    print("=" * 78)
    print("逐跨施工张拉入口（离线 + live）")
    print("=" * 78)
    offline()
    doc_chain()
    live()
    n_pass = sum(1 for ok, _, _ in RESULTS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(RESULTS)} 通过")
    return 0 if n_pass == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
