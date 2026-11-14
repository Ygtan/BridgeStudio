# `cases/` — ANSYS 对标算例总览

本目录是 **BridgeStudio 前处理 → FEM-Eigen 计算 → ANSYS 18.0 校核** 链路的
算例归档。每个 `CBxx_*` 目录保留该算例的前处理输出（`.dat`）、同源导出的
ANSYS APDL（`.inp`）、模型快照（`.model.json`）、ANSYS 批处理输出
（`.out` / CSV）与对比报告（`.txt`），可离线复查，不需要再跑 ANSYS。

命名规则：`CB01~CB05` 为 2D 连续梁，`CB3D01~CB3D13` 为 3D 算例；
编号按接入顺序递增，与 FEM-Eigen 侧 `tests/benchmark/` 的回归套件一一对应。

## 算例清单

| 编号 | 目录 | 内容 | 编排脚本 | FEM-Eigen 回归 |
|---|---|---|---|---|
| CB01 | `CB01_two_span_continuous_beam` | 两跨连续梁满布均布荷载，BEAM3 | `run_cases.py` | `continuous_beam` |
| CB02 | `CB02_three_span_continuous_beam` | 三跨连续梁，含闭式解三方校核 | `run_cases.py` | `continuous_beam` |
| CB03 | `CB03_selfweight` | 自重 `ACEL` / 等效线荷载 | `run_cases.py` | `continuous_beam` |
| CB04 | `CB04_point_loads` | 节点集中力 | `run_cases.py` | `continuous_beam` |
| CB05 | `CB05_elastic_support` | 弹性支承 `COMBIN14` | `run_cases.py` | `continuous_beam` |
| CB3D01 | `CB3D01_three_span_box_beam_3d` | 3D 三跨箱梁，BEAM4 基准 | `run_cases_3d.py` | `continuous_beam_3d` |
| CB3D02 | `CB3D02_space_effects` | 3D 空间效应，BEAM188 差异量化 | `run_cases_3d.py` | `continuous_beam_3d` |
| CB3D03 | `CB3D03_cable_stayed_3d` | 斜拉桥 BEAM + TRUSS 混合单元 | `run_cases_cable.py` | `cable_arch_3d` |
| CB3D04 | `CB3D04_arch_bridge_3d` | 拱桥 BEAM + TRUSS 混合单元 | `run_cases_cable.py` | `cable_arch_3d` |
| CB3D05 | `CB3D05_cable_solver_completion` | `cable_solver` 非线性成桥态自洽 | `run_cable_solver_smoke.py` | `cable_solver_smoke` |
| CB3D06 | `CB3D06_distributed_udl` | 3D 单元均布荷载 `*ELEMENT_LOAD` | `run_cases_3d_loads.py` | `element_loads_3d` |
| CB3D07 | `CB3D07_distributed_selfweight` | 3D 自重 / 附加质量 | `run_cases_3d_loads.py` | `element_loads_3d` |
| CB3D08 | `CB3D08_trapezoidal_loads` | 梯形分布荷载叠加 | `run_cases_3d_loads.py` | `element_loads_3d` |
| CB3D09 | `CB3D09_real_tower_3d` | 真实竖直塔柱（方向节点 K(+Y)） | `run_cases_tower.py` | `cable_arch_3d` |
| CB3D10 | `CB3D10_completion_vs_ansys_nl` | 非线性成桥态 vs `NLGEOM + LINK180` | `run_cases_nl.py` | `nonlinear_ansys_3d` |
| CB3D11 | `CB3D11_suspension_earth_anchored` | 地锚式悬索桥找形 | `run_cases_susp.py` | `suspension_ansys_3d` |
| CB3D12 | `CB3D12_suspension_self_anchored` | 自锚式悬索桥找形（手动 40 m 边跨） | `run_cases_susp.py` | `suspension_ansys_3d` |
| CB3D13 | `CB3D13_suspension_balanced_erection` | 自锚式**自动平衡边跨 71.414 m** + 5 个施工阶段找形 | `run_cases_erection.py` | `suspension_erection_3d` |
| CB3D14 | `CB3D14_cable_sag_ernst` | 单索垂度 / **Ernst 等效弹模** vs ANSYS 80 段 LINK180 索链（7 个 job） | `run_cases_sag.py` | `cable_sag_ansys_3d` |
| CB3D15 | `CB3D15_suspension_cable_selfweight` | 与 CB3D13 同参数但 `cable_dens=7850`（`W>0`），验证**空缆态不再退化** | `run_cases_sag.py` | `cable_sag_ansys_3d` |

CB3D13 除全桥 APDL 外还归档 5 份阶段 APDL（`*_stage1..5.inp`）与对应
ANSYS CSV；三层验证口径见 `ansys_bridge_erection.py` 模块头与
FEM-Eigen `tests/benchmark/suspension_erection_3d/README.md`。
CB3D14/15 的口径与实测数字见 `ansys_bridge_sag.py` 模块头及
FEM-Eigen `tests/benchmark/cable_sag_ansys_3d/README.md`。

> CB3D14 是**单元级本构探针**：`.sag` 参数文件与 7 份 APDL 都从
> `ansys_bridge_sag.py::SAG_CFG` 同源生成，FEM 侧由 `cable_sag_probe.exe`
> 直接调用内核闭式；不走全桥 `ModelSpec3D.to_dat()`。`.model.json` 是可
> 追溯的参数快照，不参与求解。

## 同源导出约定

- **唯一数据源**：2D 用 `BridgeSpec`，3D 用 `ModelSpec3D`；同一份模型同时
  生成 FEM-Eigen `.dat` 与 ANSYS `.inp`，不允许手工抄写第二份输入。
- **APDL 纯 ASCII**：单元类型、标题、注释一律英文；中文只出现在 Python
  报告里，避免 ANSYS 18.0 批处理写文件时编码失败。
- **ANSYS 调用**：`ANSYS180.exe -b -j <job> -i <inp> -o <out>`，
  结果由 `*CFOPEN` + `*VWRITE` 一次性写数组导出 CSV（不要在 `*DO` 里
  逐行 `*VWRITE`）。
- **关键语义差异**：FEM `*CLOAD` 累加、ANSYS `F` 覆盖，导出前必须合并
  重复 `(node, dof)`；竖直 BEAM 需用方向节点 `K(+Y)` 对齐 FEM 局部轴。

## 归档规则

`.gitignore` 放行 `.dat` / `.inp` / `.model.json` / `.csv` / `.txt` / `.out`
作为可复查证据，排除 ANSYS 每次求解重生成的二进制与数据库产物
（`.rst` `.emat` `.esav` `.full` `.mntr` `.DSP` `.err` `.log` `.ldhi`
`.rdb` `.r###` 等）以及 `__pycache__/`。

## 复现

```powershell
# 单个阶段（示例：CB3D13；需要 ANSYS 18.0 许可 + anaconda python）
C:\Users\user\anaconda3\python.exe D:\AI-work\BridgeStudio\cases\run_cases_erection.py

# 离线回归（只需 FEM-Eigen 构建产物，不需要 ANSYS）
ctest --test-dir D:\AI-work\FEM-Eigen\build --output-on-failure
powershell -ExecutionPolicy Bypass -File D:\AI-work\FEM-Eigen\run_tests.ps1
```

新增算例时：在 `cases/` 下建 `CBxx_*` 目录、写 `run_cases_*.py`，
并在 FEM-Eigen `tests/benchmark/` 建对应回归套件与 CTest 注册；
清单与总索引（`tests/benchmark/README.md`）需同步更新。
