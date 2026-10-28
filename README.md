# BridgeStudio — 自然语言驱动的桥梁设计与施工计算软件

> 目标：结合 FEM-Eigen C++ 求解内核，构建一套覆盖「自然语言建模 → 有限元求解 →
> 规范设计验算 → 成果输出」全流程的桥梁设计、施工计算软件。
> 追求两个极致：**处理问题的深度**（非线性成桥态、施工全过程、规范全自动验算）
> 与**速度**（稀疏模态/屈曲求解 O(n·k)、参数即时重算、自然语言分钟级建模）。

## 项目组成

| 部分 | 位置 | 说明 |
|---|---|---|
| BridgeStudio（本仓库） | `D:\AI-work\BridgeStudio` | 前处理 + 界面 + 规范验算 + 成果输出 |
| FEM-Eigen 求解内核 | `C:\Users\Thinkpad\WorkBuddy AI\2026-10-06-17-19-31\FEM-Eigen` | C++/Eigen，git 管理，独立演进 |
| 归档（不再投入） | `D:\AI-work\前处理\fe_nl_preprocessor` | NL-FE 原型，本项目的前身 |
| 归档（不再投入） | `D:\AI-work\前处理\explore-x20` | X20 原型，多格式导出器的来源 |

## 契约

前处理与求解内核**只通过 `.dat` 文件耦合**（FEM-Eigen new format），
目录通过 `fem_eigen_export.py` 的多候选自动探测定位（可用环境变量
`FEM_EIGEN_DIR` 覆盖），两边可以独立演进、独立提交。

## 融合来源（两版前处理器优缺点结论）

- **NL-FE（骨架）**：ModelDoc 统一外壳、规则+LLM 双通道、单位哨兵、
  8 个 verify 回归脚本约 150 项断言、anastruct 交叉校验。
- **X20（器官库）**：5 种桥型建造器（含斜拉/悬索/组合体系）、BEAM/TRUSS/CABLE
  2D/3D 六种单元表达、组件分块编号规范（SKILL.md）、ANSYS/Abaqus/MIDAS/自研
  四格式导出器、严格 validate_model。

## 架构（五层）

```
① 自然语言层    规则解析 + DeepSeek 双通道（继承 NL-FE）
② 参数化建模层  组件分块编号 + 梁/拱/桁架/斜拉/悬索建造器（移植 X20）
③ 统一模型层    ModelDoc 3D：BEAM/BEAM7/TRUSS/CABLE + 施工阶段 + 预应力体系
④ 求解层        FEM-Eigen：静力/模态/屈曲/影响面/车道/施工阶段 → 阶段二非线性
⑤ 规范+成果层   JTG D60 组合、3362 预应力、3363 验算 → 计算书/包络图/多格式互导
```

## 快速开始

```bat
:: 双击 run_app.bat（自动清端口 → 启动 → 开浏览器）
:: 或命令行：
cd /d D:\AI-work\BridgeStudio
C:\Users\Thinkpad\.workbuddy\binaries\python\envs\default\Scripts\streamlit.exe run app.py
```

依赖见 `requirements.txt`（streamlit / anastruct / matplotlib / pydantic / numpy）。
无 DeepSeek API Key 时自动走规则解析，完全离线可用。

## 路线图

- [x] **P0** 修复 FEM-Eigen 内核路径断链（多候选自动探测）——2026-10-28
- [ ] **P1** ModelDoc 3D 化：3D ModelSpec（IY/IZ/J/IW 截面）、X20 桥型建造器移植、
       3D .dat 导出；基准 = 三跨连续箱梁 3D 梁格（对照 FEM-Eigen 黄金基线
       `bridge_3d_grillage`）
- [ ] **P2** 高级能力全量暴露：施工阶段（STAGE）、预应力（PRESTRESS_LOSS）、
       车道荷载/影响面（LANE_3D/INFLUENCE_3D）、模态/屈曲；JTG D60 荷载组合引擎
- [ ] **P3** 内核攻坚：共转列式非线性 + 索长迭代 → 斜拉桥/悬索桥成桥态
       （在 FEM-Eigen 仓库实施，本仓库负责暴露与可视化）
- [ ] **P4** 成果层：Word/PDF 计算书、3D 包络视图、MIDAS 双向互导

## 质量保障闭环（铁律）

1. 改完任何 `.py` 必须重跑全部 verify 脚本再提交（当前 19+7+6+17 项全绿）。
2. 任何数值新功能必须先有理论解或交叉校验（anastruct / MIDAS / 黄金基线）。
3. 内核目录只在 `fem_eigen_export.py` 一处探测，其他模块一律复用。
