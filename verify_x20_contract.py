"""verify_x20_contract.py
========================
把 ``X20_接入约定.md`` 从「写在文档里的规矩」变成「跑得出来的断言」。

X20 是已归档的资产库。以前用法散在几处：4 个建造器各写一遍编号规范、
两处各解释一遍「借 / 不借」、还有一个回归脚本直接读归档目录的 .dat。
本脚本锁住统一约定：

1. 契约文档九章齐全、三条铁律、术语澄清（两个 canonical 不同物）、
   §4 字段/单位/坐标口径、§7 资产登记表逐项可解析；
2. 铁律 2 唯一转换点：只有 ``export_formats.py`` 定义 ``to_canonical``，
   ``mct_io.py`` 只消费不自建，导出器签名保持 ``(model, path)``；
3. 铁律 3 只出不进：仓库根目录不得出现归档路径常量 / X20 模块导入，
   ``verify_fem_eigen_link.py`` 默认用例改用 FEM-Eigen 自带样例；
4. 各接触点注释统一指向契约，5 个建造器的编号表是 §5 的落地实例；
5. §4 映射表与代码常量对拍、信息有损必须走 warnings、§6 缺陷修正有回归。

不依赖归档目录、不调用求解器、不写 ``.dat``，纯离线 + 一次内存冒烟。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import inspect
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import export_formats
from cable_stayed_3d import build_cable_stayed_3d

ROOT = Path(__file__).parent
CONTRACT = ROOT / "X20_接入约定.md"
RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((ok, name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


#: 契约 §7 资产登记表：id → 档位。新增资产必须先改契约再改这里。
ASSET_REGISTRY: dict[str, str] = {
    "A0": "A", "A1": "A", "A2": "A", "A3": "A",
    "B1": "B", "B2": "B", "B3": "B", "B4": "B",
    "C1": "C", "C2": "C", "C3": "C",
}

SECTIONS = (
    "## 1. 定位与三条铁律",
    "## 2. 术语：两个 canonical 不是一回事",
    "## 3. 三档处理分级",
    "## 4. 统一数据、单位与坐标约定",
    "## 5. 统一分块编号规范",
    "## 6. 已知缺陷修正清单",
    "## 7. 资产登记表",
    "## 8. 新增 / 修改流程",
    "## 9. 禁止项",
)

#: 只拦截**可执行**的旁路；注释里说明「不依赖归档目录」是允许的（契约 §9）。
BANNED_SNIPPETS = (
    "X20_OUTPUTS =",
    r'r"D:\AI-work\前处理\explore-x20',
    "import bridge_fe_modeler",
    "from bridge_fe_modeler",
)

BUILDERS = ("grillage_3d.py", "arch_3d.py", "cable_stayed_3d.py",
            "suspension_3d.py", "truss_3d.py")

CANONICAL_KEYS = (
    "bridge", "materials", "element_types", "real_constants",
    "nodes", "elements", "boundary_conditions", "loads",
)

FIX_ASSERTIONS = (
    "ANSYS 节点力用 FZ（不是 MZ）",
    "ANSYS 无伪求解段",
    "Abaqus 节点力 dof 用 3（不是 6）",
    "Abaqus 截面带方向向量",
    "主梁 IYY = IY",
)


def contract_doc() -> None:
    print("\n[1] 契约文档：九章、铁律、术语与口径")
    check("X20_接入约定.md 存在", CONTRACT.is_file(), str(CONTRACT.name))
    text = CONTRACT.read_text(encoding="utf-8") if CONTRACT.is_file() else ""
    for i, sec in enumerate(SECTIONS, 1):
        check(f"契约含 §{i}：{sec[3:]}", sec in text)
    for law in ("唯一真源", "唯一转换点", "只出不进"):
        check(f"铁律文本：{law}", law in text)
    check("§2 澄清两个 canonical 不同物",
          "canonical dict 是「出去的格式」，不是「进来的模型」" in text
          and "同名不同物" in text)
    check("§3 三档处理分级齐全",
          all(c in text for c in ("A 直接复用", "B 重写适配", "C 刻意不借")))
    for why in ("拓扑被冻死", "结果不可复现", "无法离线"):
        check(f"C 档写明不借理由：{why}", why in text)
    check("§4 惯矩按绕轴同名映射",
          all(s in text for s in ("IY→IYY", "IZ→IZZ", "J→IXX")))
    check("§4 泊松比由 G 反算", "ν = E/(2G) − 1" in text)
    check("§4 自由度口径（FZ / UR3）", "FZ" in text and "UR3" in text)
    check("§4 信息有损必须 warnings",
          "静默丢信息比导出失败更难查" in text and "warnings" in text)
    check("§5 登记梁格 legacy/wide 双编号方案",
          all(s in text for s in ("n_lines ≥ 5", "numbering_plan()",
                                  "逐位不变", "DEFAULT_DAT_SHA")))
    check("§5 写明宽编号仍要 live 闭式解守卫",
          "wide_numbering()" in text and "−P·loss/n_lines" in text)
    check("§9 禁止项不少于 6 条",
          len([ln for ln in text.split("## 9. 禁止项")[1].splitlines()
               if ln.startswith("- ")]) >= 6)
    check("§8 写明基线纪律", "dat_byte_regression" in text)


def asset_table() -> None:
    print("\n[2] 资产登记表：逐项可解析、档位与状态齐全")
    text = CONTRACT.read_text(encoding="utf-8") if CONTRACT.is_file() else ""
    rows: dict[str, list[str]] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) >= 7 and re.fullmatch(r"`[ABC]\d`", cells[0]):
            rows[cells[0][1:-1]] = cells
    missing = sorted(set(ASSET_REGISTRY) - set(rows))
    check(f"§7 登记表覆盖 ASSET_REGISTRY 全部 {len(ASSET_REGISTRY)} 项",
          not missing and len(rows) == len(ASSET_REGISTRY),
          f"缺 {missing}" if missing else f"{len(rows)} 项")
    bad_grade = [k for k, g in ASSET_REGISTRY.items()
                 if k in rows and rows[k][3] != g]
    check("每项档位与契约一致（A/B/C）", not bad_grade, str(bad_grade))
    incomplete = [k for k, c in rows.items()
                  if not all(c[i] for i in (1, 2, 4, 5, 6))]
    check("每项都有资产 / 来源 / 落点 / 回归 / 状态", not incomplete,
          str(incomplete))
    check("B2 如实标注部分落地（组合体系待搬）",
          "B2" in rows and "部分" in rows.get("B2", [""] * 7)[6])
    for cid in ("C1", "C2", "C3"):
        check(f"{cid} 标注为明确不借",
              cid in rows and "明确不借" in rows.get(cid, [""] * 7)[6])
    check("契约声明本表与守卫对拍",
          "verify_x20_contract.py::ASSET_REGISTRY" in text)


def unique_conversion() -> None:
    print("\n[3] 铁律 2：唯一转换点、签名不变、只消费不自建")
    files = sorted(p for p in ROOT.glob("*.py")
                   if p.name != "verify_x20_contract.py")
    definers = [p.name for p in files
                if re.search(r"^def to_canonical\(", p.read_text(encoding="utf-8"),
                             re.M)]
    check("只有 export_formats.py 定义 to_canonical",
          definers == ["export_formats.py"], str(definers))
    mct = read("mct_io.py")
    check("mct_io 局部 import to_canonical（不自建）",
          "from export_formats import to_canonical" in mct
          and "def to_canonical(" not in mct)
    check("mct_io 注释指向契约铁律 2", "X20_接入约定.md 铁律 2" in mct)
    for fn in ("write_ansys_file", "write_abaqus_file", "write_midas_file"):
        sig = inspect.signature(getattr(export_formats, fn))
        check(f"{fn} 签名保持 (model, path)",
              tuple(sig.parameters) == ("model", "path"),
              str(tuple(sig.parameters)))
    ef = read("export_formats.py")
    check("export_formats 自认唯一转换点",
          "X20_接入约定.md" in ef and "唯一转换点" in ef)
    check("to_canonical 返回 (canonical, warnings)",
          "def to_canonical(model: ModelSpec3D) -> tuple[dict[str, Any], list[str]]:" in ef)
    check("§4 字段对照表集中在 export_formats.py",
          "_REAL_FIELDS_BEAM" in ef and "_KIND_MAP" in ef)


def no_bypass() -> None:
    print("\n[4] 铁律 3：仓库内不得有归档旁路")
    files = sorted(p for p in ROOT.glob("*.py")
                   if p.name != "verify_x20_contract.py")
    blobs = {p.name: p.read_text(encoding="utf-8") for p in files}
    for snippet in BANNED_SNIPPETS:
        hits = [name for name, s in blobs.items() if snippet in s]
        check(f"无可执行旁路：{snippet[:34]}", not hits, str(hits))
    link = read("verify_fem_eigen_link.py")
    check("链路回归默认用例改用内核自带 .dat",
          'FEM_EIGEN_DIR / "arch_bridge_100m.dat"' in link
          and "X20_OUTPUTS" not in link)
    check("链路回归说明 C3 收口", "§3.3" in link and "C3" in link)
    check("链路回归仍允许显式传 .dat 路径",
          "args[0]" in link or "sys.argv" in link)
    bs = read("bridge_spec.py")
    check("bridge_spec 澄清 canonical 术语冲突",
          "同名不同物" in bs and "§2" in bs)


def contact_points() -> None:
    print("\n[5] 接触点注释统一指向契约")
    for name in BUILDERS:
        s = read(name)
        head = s.split('"""')[1] if '"""' in s else ""
        check(f"{name} 编号表指向 §5",
              "X20_接入约定.md" in head and "§5" in head)
    units = [name for name in BUILDERS if "m / N / Pa" in read(name)]
    check("5 个建造器单位统一 m / N / Pa",
          len(units) == len(BUILDERS), str(units))
    ll = read("bridge_llm.py")
    check("bridge_llm 标注 B4 借 / C2 不借",
          "B4" in ll and "C2" in ll and "X20_接入约定.md" in ll)
    check("model_doc 标注 C2", "C2" in read("model_doc.py")
          and "§3.2" in read("model_doc.py"))
    check("model_spec_3d 标注 A0 策略借用",
          "§7 A0" in read("model_spec_3d.py"))
    check("export_formats 标注资产档位 A0~A3",
          "A0/A1/A2/A3" in read("export_formats.py"))


def field_mapping() -> None:
    print("\n[6] §4 映射口径与代码常量对拍")
    f = export_formats._REAL_FIELDS_BEAM
    for x20, ours in (("AREA", "A"), ("IYY", "IY"), ("IZZ", "IZ"), ("IXX", "J")):
        check(f"{x20} → {ours}（按绕轴，不按竖向/横向）",
              f.get(x20) == ours, str(f.get(x20)))
    check("BEAM7 → BEAM3D 且注明格式限制",
          export_formats._KIND_MAP.get("BEAM7") == "BEAM3D")
    for name, key, want in (("ANSYS", "BEAM3D", "BEAM4"),
                            ("ABAQUS", "BEAM3D", "B31"),
                            ("MIDAS", "CABLE3D", "TENSTR")):
        table = getattr(export_formats, f"{name}_ELEMENT_TYPES")
        check(f"{name} 单元类型表 {key}={want}", table.get(key) == want)
    nu, from_g = export_formats._poisson(3.45e10, 3.45e10 / 2.4)
    check("ν = E/(2G) − 1 = 0.2（不随手填）",
          from_g and abs(nu - 0.2) < 1e-12, f"ν={nu:.6g}")
    ef = read("export_formats.py")
    check("G 缺失兜底必须带告警",
          "泊松比按 0.3 导出" in ef and "剪切刚度将与原模型不一致" in ef)
    check("IW / 弹性支承等有损项进 warnings",
          all(k in ef for k in ("翘曲常数 IW", "只有刚性约束"))
          and ef.count("warnings.append") >= 3)
    check("线膨胀系数标为已知限制（不装作有）",
          '"thermal_expansion": 0.0' in ef and "已知限制" in CONTRACT.read_text(encoding="utf-8"))


def smoke_and_fixes() -> None:
    print("\n[7] 内存冒烟与 §6 缺陷修正可核对")
    model, _ = build_cable_stayed_3d()
    canonical, warns = export_formats.to_canonical(model)
    check("to_canonical 返回 (dict, list)",
          isinstance(canonical, dict) and isinstance(warns, list))
    lack = [k for k in CANONICAL_KEYS if k not in canonical]
    check("canonical 八个顶层键齐全", not lack, str(lack))
    check("warnings 由返回值交给调用方（不是打印后吞掉）",
          isinstance(warns, list)
          and "return canonical, warnings" in read("export_formats.py"))
    vf = read("verify_export_formats.py")
    miss = [a for a in FIX_ASSERTIONS if a not in vf]
    check("§6 五处缺陷修正均有回归断言", not miss, str(miss))
    check("契约 §6 列出 X20 原版缺陷",
          "X20 原版" in CONTRACT.read_text(encoding="utf-8"))


def main() -> int:
    print("=" * 78)
    print("X20 资产接入约定：文档 / 唯一转换点 / 无旁路 / 口径对拍")
    print("=" * 78)
    contract_doc()
    asset_table()
    unique_conversion()
    no_bypass()
    contact_points()
    field_mapping()
    smoke_and_fixes()
    n_pass = sum(1 for ok, _, _ in RESULTS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(RESULTS)} 通过")
    return 0 if n_pass == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
