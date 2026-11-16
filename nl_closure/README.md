# 自然语言 → dat/inp → FEM → ANSYS 闭环（易档 13 例）

## 这份闭环证明了什么

把对拍算例从「Python 直接建模」反向还原成**参数齐全的自然语言技术句**，
再让程序从这句中文重新生成 `.dat`/`.inp`，驱动 FEM-Eigen 与 ANSYS 双求解并
逐点对拍——这条闭环在**易档 13 个几何/荷载类算例**上**全部完整跑通**。

状态（2026-11-16）：

| 环节 | 结果 |
|---|---|
| 13 份技术句 → 字段抽取 → 与冻结参数交叉校验 | 逐字段一致（防伪双保险，非「技术句摆设」）|
| 同源导出 `.dat` / `.inp`（含 BEAM188+BEAM4 双 inp）| **逐字节** = 各算例 `cases/` 冻结证据 |
| FEM `example_mixed.exe`（2D）/ `example_3d_report.exe`（3D）真跑 | `.result` / `.report.txt` **逐字节** = 冻结 |
| ANSYS 18.0 真跑（`--case all` 默认含）| 各算例 CSV **逐字节** = 冻结 |
| FEM ↔ ANSYS 逐点对拍 | 各算例专用 `compare*` 全部 **PASS** |
| 完整闭环 `run_nl_closure.py --case all` | **13/13 例通过** |
| 离线守卫 `verify_nl_closure.py` | **44/44**（不依赖 ANSYS 许可）|

## 覆盖范围：易档 13 例（四类链路）

| 算例 | 链路 | FEM | ANSYS | compare |
|---|---|---|---|---|
| CB01 / CB02 | 2D 连续梁 | `example_mixed.exe` | BEAM3 | `compare`（闭式）|
| CB03 / CB04 / CB05 | 2D 自重/集中力/弹性支承 | `example_mixed.exe` | BEAM3(+COMBIN14) | `compare_generic` |
| CB3D01 / CB3D02 | 3D 箱梁 | `example_3d_report.exe` | BEAM188+BEAM4 双 inp | `compare_3d` |
| CB3D06 / CB3D07 / CB3D08 | 3D 真实分布荷载/自重/梯形荷载 | `example_3d_report.exe` | BEAM4 | `compare_3d_loads` |
| CB3D03 / CB3D04 / CB3D09 | 混合单元（索/拱/塔柱）| `example_3d_report.exe` | BEAM4+LINK180 | `compare_mixed` |

## 怎么跑

```bash
# Anaconda Python（本机已装齐 pydantic/anastruct 等依赖）
C:\Users\user\anaconda3\python.exe nl_closure\verify_nl_closure.py              # 离线守卫：13 例解析+逐字节
C:\Users\user\anaconda3\python.exe nl_closure\run_nl_closure.py --case all      # 完整闭环：13 例含 FEM+ANSYS 真跑
C:\Users\user\anaconda3\python.exe nl_closure\run_nl_closure.py --case CB3D02   # 单例
C:\Users\user\anaconda3\python.exe nl_closure\run_nl_closure.py --case all --no-ansys  # 跳过 ANSYS（复制冻结 CSV 对拍）
```

所有产物写入 `nl_closure/out/`（已被 `.gitignore` 忽略），**不覆盖** `cases/`
下 26 个算例的任何冻结证据。

## 文件

- `desc_cb01.txt` … `desc_cb3d09.txt` —— 13 份唯一输入：参数齐全的中文技术句。
- `case_registry.py` —— 13 例声明式注册表：链路类型、参数锚（反向抄自
  `cases/run_cases_*.py`）、求解器/导出器/compare 分派元数据。
- `nl_case_parser.py` —— 保留 CB3D01 原型的 `CaseSpec`/`parse_file` 与
  `parse_number`（中文数字/科学计数）。
- `nl_pipeline.py` —— 通用解析层：`extract_fields`（技术句独立抽字段）→
  `validate`（与注册表参数交叉校验，防伪）→ `build_model` + `export`
  （复用项目现成导出器，零重新实现）。
- `run_nl_closure.py` —— 注册表驱动的完整闭环编排，逐环节锚点检查。
- `verify_nl_closure.py` —— 离线守卫（13 例解析+逐字节），可接入 CTest。
- `_baseline_probe.py` —— 基线探针：复用各编排脚本 `CASES` 字典直接复现
  25 份 dat/inp，验证前提（参数等价可逐字节复现）。

## 边界与口径（如实声明，不得夸大）

1. **这是受控技术句解析，不是通用自然语言理解。** 每个字段都有明确中文标签
   （「跨径布置…跨」「每跨…米」「腹板厚…米」等），解析器是纯规则、离线、
   逐字段可审计的。它**不替代**项目真正的自然语言入口 `ModelDoc.from_text` /
   `nl_parser`。
2. **闭环价值在于「前端等价性」**：证明自然语言可作为前端驱动既有
   FEM/ANSYS 对拍链，且生成物与冻结证据逐字节一致。数值字段统一用阿拉伯
   数字（中文数字歧义大、不利逐位审计）。**不等于**解析器理解工程语义。
3. **防伪双保险**：技术句抽出的参数必须与注册表中「从 `run_cases_*.py`
   反向抄录」的独立参数锚逐字段相等，否则报错；建模只用技术句抽出的值。
4. **只覆盖易档 13 例。** 26 个对拍算例中，索/成桥/施工类
   （CB3D05/10/11/12/13/15/21）需补索长迭代 L0、体系、施工阶段字段；
   探针/弧长/稳定类（CB3D14/16/17/19/22/23，`.sag/.beam/.snap/.frame`）
   需专门工况句编码接 `*_probe.exe`。
5. **不改动任何既有解析链路与冻结证据**：`nl_parser.py`、`model_doc.py`、
   `ansys_bridge*.py` 源码零改动；新增文件全部在 `nl_closure/`。
