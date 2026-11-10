"""verify_combo_system.py
========================
把《组合体系接入规格.md》从「写在文档里的判断」变成跑得出来的断言。

背景：内核只有 4 个求解键，``detect_kind`` 在斜拉/悬索之间二选一，
没有协作体系成桥态找形。这类描述以前会被 ``_classify_kind`` 按「悬索优先」
静默建成单体系，给出收敛但力学错误的结果。本脚本锁住：

1. 规格文档六章齐全、§2 的代码依据真实存在；
2. 组合体系描述 → ``params['combo_system']`` + notes + ``validate()`` 拒绝
   + ``solve()`` 抛错，四处文案同源（``model_doc.COMBO_SYSTEM_MSG``）；
3. 正常语料（含斜拉/悬索并列、examples/*.txt）不误伤；
4. 规格未实现前不许出现「假的第 5 个求解键」；
5. 12 个建造器默认 ``.dat`` 逐位不变，哨兵只影响校验不影响几何；
6. 契约 / 矩阵 / README 都指向规格文档，且不靠删断言假装缺口已补。

纯离线：不调求解器、不写 ``.dat``、不依赖归档目录。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import model_doc as md
import model_spec_3d
import solver_3d

ROOT = Path(__file__).parent
SPEC = ROOT / "组合体系接入规格.md"
RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((ok, name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


SECTIONS = (
    "## 1. 结论先行",
    "## 2. 内核侧现状",
    "## 3. 阶段一：内核侧规格",
    "## 4. 阶段二：Python 侧规格",
    "## 5. 未实现期的程序行为",
    "## 6. 验收断言",
)

COMBO_TEXTS = (
    "斜拉-悬索协作体系桥，主跨 1200 m，空间三维",
    "斜拉+悬索组合体系，主跨 900 m",
    "组合体系桥梁，主跨 800 m",
    "梁拱组合桥，主跨 300 m",
    "拱梁组合结构，主跨 260 m",
    "斜拉悬索协作，主跨 1000 m",
    "协作体系斜拉桥，主跨 600 m",
)

NORMAL_TEXTS = (
    "三跨连续箱梁桥，边跨 30 m，中跨 40 m，空间梁格",
    "斜拉桥，主跨 400 m，塔高 100 m，空间三维",
    "悬索桥，主跨 1000 m，矢跨比 1/10",
    "斜拉桥与悬索桥的方案对比，主跨 800 m",
    "拱桥，主跨 100 m，矢高 20 m",
    "预应力混凝土简支梁，跨度 30 m",
)


def spec_doc() -> None:
    print("\n[1] 规格文档：六章、状态与代码依据")
    check("组合体系接入规格.md 存在", SPEC.is_file(), SPEC.name)
    text = SPEC.read_text(encoding="utf-8") if SPEC.is_file() else ""
    for i, sec in enumerate(SECTIONS, 1):
        check(f"规格含 §{i}：{sec[3:]}", sec in text)
    check("规格写明未实现", "**未实现**" in text)
    check("规格写明未实现期显式拒绝",
          "不得降级成斜拉 / 悬索单体系结果" in text)
    check("规格登记阶段一后的额外断言（不许提前写成跳过）",
          "阶段一完成后的额外断言" in text and "dat_byte_regression" in text)

    reg_src = inspect.getsource(solver_3d._registry)
    det_src = inspect.getsource(solver_3d.detect_kind)
    check("§2 依据：_registry 四个键真实存在",
          all(k in reg_src for k in ("grillage3d", "cable_stayed",
                                     "suspension", "stage_nl")))
    check("§2 依据：detect_kind 按 cable_bridge/system 二选一",
          "cable_bridge" in det_src and "system" in det_src)
    check("§2 依据：ModelSpec3D 只有 cable_bridge 字段",
          "cable_bridge" in model_spec_3d.ModelSpec3D.model_fields
          and "combo_cable_bridge" not in model_spec_3d.ModelSpec3D.model_fields)
    check("§2 依据：_check_refs 存在且校验 main_cable/hangers",
          "main_cable" in inspect.getsource(
              model_spec_3d.ModelSpec3D._check_refs))
    fe_src = read("fem_eigen_3d.py")
    check("§2 依据：结果段登记表存在（新段必须登记）",
          "sections = (" in fe_src and "stage prestress loss" in fe_src
          and "段必须登记" in fe_src)


def guard_paths() -> None:
    print("\n[2] 拒绝路径：解析 / 校验 / 求解三处同源")
    msg = md.COMBO_SYSTEM_MSG
    check("文案唯一真源含内核键与规格引用",
          "detect_kind" in msg and "组合体系接入规格.md" in msg and
          "第 5 个求解键" in msg)
    check("from_text 是薄包装且注入哨兵",
          "_parse_text" in inspect.getsource(md.ModelDoc.from_text)
          and "combo_system" in inspect.getsource(md.ModelDoc.from_text))
    check("validate / solve 都引用同一文案",
          all("COMBO_SYSTEM_MSG" in inspect.getsource(getattr(md.ModelDoc, f))
              for f in ("validate", "solve")))

    for i, text in enumerate(COMBO_TEXTS, 1):
        doc, _src, diag = md.ModelDoc.from_text(text)
        check(f"组合措辞 {i} 触发哨兵", doc.params.get("combo_system") is True)
        check(f"组合措辞 {i} notes 含文案", msg in diag.notes)
        check(f"组合措辞 {i} validate 直接拒绝",
              doc.validate() == [msg], str(doc.validate())[:80])
        try:
            doc.solve()
            raised = False
        except ValueError as e:
            raised = str(e) == msg
        check(f"组合措辞 {i} solve 抛 ValueError(同源文案)", raised)


def no_false_positive() -> None:
    print("\n[3] 正常语料不误伤（含并列措辞与 examples）")
    msg = md.COMBO_SYSTEM_MSG
    texts = list(NORMAL_TEXTS)
    for p in sorted((ROOT / "examples").glob("*.txt")):
        texts.append((p.name, p.read_text(encoding="utf-8")))
    for item in texts:
        name, text = item if isinstance(item, tuple) else (item[:24], item)
        doc, _src, diag = md.ModelDoc.from_text(text)
        check(f"{name} 不触发组合哨兵",
              doc.params.get("combo_system") is not True and msg not in diag.notes)
        errs = doc.validate()
        check(f"{name} validate 不含组合拒绝文案", msg not in errs,
              str(errs[:1])[:80])


def kernel_honesty() -> None:
    print("\n[4] 规格未实现前：内核不许出现假的第 5 个键")
    keys = set(solver_3d._registry())
    check("注册表恰好 4 键", keys == {"grillage3d", "cable_stayed",
                                    "suspension", "stage_nl"}, str(sorted(keys)))
    check("detect_kind 不产生协作键",
          "cooperative" not in inspect.getsource(solver_3d.detect_kind)
          and not any("combo" in k for k in keys))
    check("BUILDERS 仍是 6 kind × 2 维度 12 格",
          len(md.BUILDERS) == 12 and len(md.KIND_LABELS) == 6,
          f"BUILDERS={len(md.BUILDERS)} KIND={len(md.KIND_LABELS)}")


def dat_invariant() -> None:
    print("\n[5] 哨兵只影响校验：12 格默认 .dat 逐位不变")
    for (kind, dim), b in md.BUILDERS.items():
        try:
            a = md.ModelDoc(kind, dict(md.DEFAULTS[b]), dimension=dim).to_dat()
            c = md.ModelDoc(kind, {**md.DEFAULTS[b], "combo_system": True},
                            dimension=dim).to_dat()
            check(f"{b} 加哨兵后 .dat 逐位不变", a == c, f"{len(a)} 字节")
        except Exception as e:                       # noqa: BLE001 - 诊断用
            check(f"{b} 加哨兵后 .dat 逐位不变", False,
                  f"{type(e).__name__}: {e}")


def docs_synced() -> None:
    print("\n[6] 契约 / 矩阵 / README 指向规格，且不删断言假装已补")
    for name in ("X20_接入约定.md", "能力边界矩阵.md", "README.md"):
        check(f"{name} 指向组合体系接入规格.md",
              "组合体系接入规格.md" in read(name))
    check("契约 B2 仍如实标「部分」",
          "部分：X20「组合体系」" in read("X20_接入约定.md"))
    check("矩阵 §4.1 缺口关键词保留",
          "组合体系桥型没有独立建造器" in read("能力边界矩阵.md"))
    check("矩阵守卫仍对拍该缺口关键词（不许删断言）",
          "组合体系桥型没有独立建造器" in read("verify_capability_matrix.py"))
    check("X20 守卫仍对拍 B2 部分落地",
          "B2 如实标注部分落地" in read("verify_x20_contract.py"))


def main() -> int:
    print("=" * 78)
    print("组合体系接入规格：能力诚实 / 显式拒绝 / 默认 .dat 不变")
    print("=" * 78)
    spec_doc()
    guard_paths()
    no_false_positive()
    kernel_honesty()
    dat_invariant()
    docs_synced()
    n_pass = sum(1 for ok, _, _ in RESULTS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(RESULTS)} 通过")
    return 0 if n_pass == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
