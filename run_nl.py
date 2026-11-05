"""run_nl.py — 自然语言算例统一入口
====================================
从 ``examples/*.txt`` 读取一句中文描述，走「意图分类 → 规则解析 → 建模 →
求解 → 报告」全链路。所有算例的自然语言描述都以 ``examples/*.txt`` 为唯一真源，
不再散落在各 ``example_*.py`` 脚本里。

用法
----
    python run_nl.py                          # 依次跑 examples/ 下全部算例
    python run_nl.py --list                   # 只列出算例与描述，不求解
    python run_nl.py arch_500m                # 按文件名/关键字匹配（可省 .txt 与目录）
    python run_nl.py examples/arch_500m.txt   # 直接给路径
    python run_nl.py suspension_1000m cable_stayed_400m   # 一次跑多个

产出（out/<name>/）
    nl.dat / nl_summary.txt / report.html　（悬索桥另有 计算书.docx）
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from console_utf8 import setup_console

setup_console()

from model_doc import KIND_LABELS, ModelDoc  # noqa: E402
from report_3d import build_document, write_html  # noqa: E402

EXAMPLES_DIR = Path(__file__).parent / "examples"


def iter_examples() -> list[Path]:
    """examples/ 下所有 .txt，按文件名排序。"""
    return sorted(EXAMPLES_DIR.glob("*.txt"))


def load_desc(path: Path) -> str:
    """读自然语言文件：去掉空行，每行 strip 后拼成一句话。"""
    text = path.read_text(encoding="utf-8")
    return "".join(line.strip() for line in text.splitlines() if line.strip())


def resolve(name: str) -> Path:
    """把「arch_500m」或「examples/arch_500m.txt」解析成实际文件。"""
    s = name.strip()
    p = Path(s)
    candidates: list[Path] = []
    if p.suffix == ".txt":
        candidates = [p, EXAMPLES_DIR / p.name]
    else:
        candidates = [EXAMPLES_DIR / f"{p.stem or s}.txt"]
    for c in candidates:
        if c.exists():
            return c
    # 关键字模糊匹配
    hits = [f for f in iter_examples() if s.lower() in f.stem.lower()]
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise SystemExit(f"关键字「{name}」匹配到多个算例：{[h.stem for h in hits]}")
    raise SystemExit(f"找不到算例「{name}」。用 --list 查看可用的算例。")


def run_one(path: Path) -> dict:
    name = path.stem
    desc = load_desc(path)
    print("=" * 78)
    print(f"算例：{name}")
    print(f"自然语言：{desc}")
    print("-" * 78)

    doc, source, diag = ModelDoc.from_text(desc)
    if diag.notes:
        print("解析提示（默认值）：")
        for n in diag.notes:
            print(f"  · {n}")

    errs = doc.validate()
    if errs:
        print("校验失败：")
        for e in errs:
            print(f"  ✗ {e}")
        return {"name": name, "ok": False, "error": "；".join(errs)}

    m = doc.model
    info = doc.info
    st = m.stats()
    n_stages = len(getattr(m, "stages", []) or [])
    print(f"识别：{KIND_LABELS.get(doc.kind, doc.kind)}（来源 {source}）")
    print(f"规模：{st['nodes']} 节点 / {st['elements']} 单元 / "
          f"{st.get('dofs', '—')} 自由度 / 施工阶段 {n_stages}")

    out_dir = Path("out") / name
    res = doc.solve(out_dir=out_dir, dat_name="nl.dat")
    print(f"求解：{res.solver}，收敛 = {res.converged}")

    rep = build_document(res, m, out_dir=out_dir,
                         kind_label=KIND_LABELS.get(doc.kind))
    html = write_html(rep, out_dir / "report.html")

    checks = res.checks(m)
    errors = [c for c in checks if not c.passed and c.level == "error"]
    warns = [c for c in checks if not c.passed and c.level == "warn"]
    n_ok = len(checks) - len(errors) - len(warns)
    parts = [f"校核：{n_ok}/{len(checks)} 通过"]
    if errors:
        parts.append(f"{len(errors)} 项不合格：" + "、".join(c.name for c in errors))
    if warns:
        parts.append(f"{len(warns)} 项提示：" + "、".join(c.name for c in warns))
    print("；".join(parts))

    result: dict = {"name": name, "ok": res.converged and not errors, "html": html}

    if doc.kind == "suspension":
        from report_docx import build_suspension_report

        report_params = {
            "system": doc.params["system"],
            "side_span": info["side_span"],
            "B": doc.params["B"], "h": doc.params["h"],
            "q": doc.params["q"],
            "A": doc.params["A"], "Ah": doc.params["Ah"],
            "lane_count": doc.params["lane_count"],
        }
        docx = build_suspension_report(
            model=m, info=info, res=res, params=report_params,
            out_path=out_dir / "计算书.docx",
            project=m.title, fig_dir=out_dir / "figs",
        )
        result["docx"] = docx
        print(f"计算书：{docx}")

    print(f"HTML：{html}")
    print(f"原始输出：{res.result_path}")
    return result


def main(argv: list[str]) -> int:
    if "--list" in argv:
        print(f"自然语言算例（examples/ 目录，共 {len(iter_examples())} 个）：\n")
        for f in iter_examples():
            print(f"  {f.name:<26} {load_desc(f)}")
        return 0

    if not argv:
        paths = iter_examples()
    else:
        paths = [resolve(a) for a in argv]

    results = []
    for p in paths:
        try:
            results.append(run_one(p))
        except Exception as e:  # noqa: BLE001 —— 单例失败不影响其余算例
            print(f"✗ {p.stem} 运行失败：{e}")
            results.append({"name": p.stem, "ok": False, "error": str(e)})
        print()

    print("=" * 78)
    ok = sum(1 for r in results if r["ok"])
    print(f"完成：{ok}/{len(results)} 个算例通过")
    for r in results:
        mark = "PASS" if r["ok"] else "FAIL"
        extra = "" if r["ok"] else f"（{r.get('error', '')}）"
        print(f"  {mark}  {r['name']}{extra}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
