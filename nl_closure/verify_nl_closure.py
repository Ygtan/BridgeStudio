"""离线守卫：13 例技术句 → 同源 dat/inp 逐字节 = 冻结证据。

纯离线：不调 FEM/ANSYS exe、不写任何文件、不依赖 ANSYS 许可。
只需 Anaconda Python（pydantic）。用于独立回归。

它锁住闭环的「前端等价性」：
  * 每例技术句经 extract_fields() 独立抽出字段；
  * 与 case_registry 中从 run_cases_*.py 反向抄录的参数交叉校验（防伪双保险）；
  * 再用技术句抽出的值调 build_xxx + 项目现成导出器，逐字节复现冻结 dat/inp。
只要这一层成立，下游 FEM/ANSYS（已由各 cases 冻结 CSV 与既有 CTest 锁定）
必然给出同样对拍结论。

完整双求解真跑闭环见 run_nl_closure.py。
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BS = HERE.parent
for p in (str(BS), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from nl_case_parser import parse_number  # noqa: E402
from case_registry import all_cases  # noqa: E402
from nl_pipeline import (extract_fields, validate, build_model,  # noqa: E402
                         export)

CHECKS: list[tuple[str, str, bool]] = []


def ck(case: str, name: str, ok: bool) -> None:
    CHECKS.append((case, name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {case:8s} {name}")


def main() -> int:
    # 中文数字/科学计数解析正确性（单元级）
    ck("CORE", "parse_number 三十=30", parse_number("三十") == 30.0)
    ck("CORE", "parse_number 零点零二五=0.025", parse_number("零点零二五") == 0.025)
    ck("CORE", "parse_number 2.06e11", parse_number("2.06e11") == 2.06e11)

    import tempfile
    with tempfile.TemporaryDirectory(prefix="nl_guard_") as td:
        work = Path(td)
        for case in all_cases():
            text = (HERE / case.desc).read_text(encoding="utf-8")
            try:
                fields = extract_fields(case, text)
            except Exception as exc:  # noqa: BLE001
                ck(case.key, f"技术句字段抽取异常: {type(exc).__name__}: {exc}", False)
                continue
            problems = validate(case, fields)
            ck(case.key, f"技术句字段 vs 冻结参数校验 ({len(fields)} 字段)",
               not problems)
            if problems:
                for pr in problems[:3]:
                    ck(case.key, f"  {pr}", False)
                continue
            try:
                model = build_model(case, fields)
                files = export(case, model, work, fields)
            except Exception as exc:  # noqa: BLE001
                ck(case.key, f"建模/导出异常: {type(exc).__name__}: {exc}", False)
                continue
            for logical, p in files.items():
                frozen = case.frozen(p.name)
                same = frozen.exists() and p.read_bytes() == frozen.read_bytes()
                ck(case.key, f"同源导出逐字节 = 冻结 {p.name}", same)

    passed = sum(1 for _, _, ok in CHECKS if ok)
    print("=" * 72)
    print(f"结果：{passed}/{len(CHECKS)} checks passed  "
          f"（{len(all_cases())} 个算例，纯离线）")
    print("=" * 72)
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
