# BridgeStudio — 自然语言驱动的桥梁设计与施工计算软件

> 目标：结合 FEM-Eigen C++ 求解内核，构建一套覆盖「自然语言建模 → 有限元求解 →
> 规范设计验算 → 成果输出」全流程的桥梁设计、施工计算软件。
> 追求两个极致：**处理问题的深度**（非线性成桥态、施工全过程、规范全自动验算）
> 与**速度**（稀疏模态/屈曲求解 O(n·k)、参数即时重算、自然语言分钟级建模）。

## 项目组成

| 部分 | 位置 | 说明 |
|---|---|---|
| BridgeStudio（本仓库） | `D:\AI-work\BridgeStudio` | 前处理 + 界面 + 规范验算 + 成果输出 |
| FEM-Eigen 求解内核 | `D:\AI-work\FEM-Eigen` | C++/Eigen，git 管理，独立演进 |
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
- [x] **P1** ModelDoc 3D 化（第一步）：`model_spec_3d.py`（BEAM/BEAM7/TRUSS/CABLE +
       3D 实常数 + 6 自由度边界 + 施工阶段 + to_dat 3D 导出）、`grillage_3d.py`
       （三跨连续箱梁空间梁格建造器，含横隔板与 Hambly 横向条带刚度）、
       `fem_eigen_3d.py`（对接 example_3d_grillage.exe 求解与包络解析）——
       2026-10-28，`verify_grillage_3d.py` 12/12 全绿：中跨跨中挠度 8.21mm
       vs anastruct 理论 8.11mm（1.2%），与三弯矩闭式解独立吻合
- [ ] **P1** ModelDoc 3D 化：3D ModelSpec（IY/IZ/J/IW 截面）、X20 桥型建造器移植、
       3D .dat 导出；基准 = 三跨连续箱梁 3D 梁格（对照 FEM-Eigen 黄金基线
       `bridge_3d_grillage`）
- [ ] **P2** 高级能力全量暴露：施工阶段（STAGE）、预应力（PRESTRESS_LOSS）、
       车道荷载/影响面（LANE_3D/INFLUENCE_3D）、模态/屈曲；JTG D60 荷载组合引擎
- [x] **P3** 内核攻坚：共转列式非线性 + 索长迭代 → 斜拉桥成桥态 —— 2026-10-30
- [x] **P3** 悬索桥成桥态（自锚式 / 地锚式）：主缆索多边形找形（对 H 二分命中
       矢高）→ 平衡态弦长反算 L₀ → 索长迭代；两体系共用同一套内核，只差边界。
       `verify_suspension_3d.py` 61/61 全绿，含两条内核硬校验的负例
       （塔顶不共用节点、端节点悬空都会报错而不会静默算错）—— 2026-10-31
- [x] **P3** 截面应力验算：`code_material.py`（JTG 3362-2018 表 3.1.3~3.1.5、
       JTG D64-2015 表 3.2.1 的强度设计值，按板厚分档）+ `section_strength.py`
       （钢按 D64 §5.3.1 三款算正应力/剪应力/折算应力，混凝土按 3362 §7.1.5
       算 0.5fck）；荷载组合扩到含轴力 N。`verify_section_strength.py` 46/46。
       **边界**：只做应力、不做承载力（不建钢筋）；混凝土按 0.5fck 只作量级判据
       —— 2026-10-31
- [x] **P3** 索承体系梁应力：`cable_solver` 新增 `[GIRDEREND]`（每端一行，两种桥型
       都输出），斜拉桥第一次有主梁内力；`result_3d._checks_strength_cable` 用
       `_strength_sections + section_strength` 做正应力/剪应力/折算应力，报告增
       「控制截面应力」表。`verify_section_strength.py` 59/59（含同端配对的手算对拍）。
       **口径**：成桥态恒载**标准值**，无活载、无组合系数——利用率 <1 不等于设计通过，
       条目一律 warn；恒载单项就 >1 才 error。**边界**：仍不出梁内力包络与弯矩图
       —— 2026-10-31
- [x] **P3** 施工阶段调索（`*STAGE` 的 `TENSION=` / `L0=`）：内核在阶段内迭代命中目标
       索力（`ΔP = N目标 − N当前`，按索力状态而非参考几何算），跨阶段继承索力状态与
       按节点累积的位移；前端 `Stage3D.tensions/unstrained` → `.dat` → 结果解析 →
       两条校核（索无受压、末阶段索力 vs 设计目标）。`verify_stage_cable.py` 21/21。
       **边界**：线弹性调索；分批张拉时先张的索会被带跑（分两批实测 193.7%），内核输出
       `devDes` 量化而不静默，二次调索可拉回（实测 1e-15）—— 2026-10-31
- [ ] **P4** 成果层：Word/PDF 计算书、3D 包络视图、MIDAS 双向互导

## 质量保障闭环（铁律）

1. 改完任何 `.py` 必须重跑全部 verify 脚本再提交（当前 18 个脚本、692 项全绿）。
2. 任何数值新功能必须先有理论解或交叉校验（anastruct / MIDAS / 黄金基线）。
3. 内核目录只在 `fem_eigen_export.py` 一处探测，其他模块一律复用。

### 跑 verify 时的控制台编码

所有 verify 脚本入口都调了 `console_utf8.setup_console()`，把 stdout/stderr
强制成 UTF-8。**这不是装饰**：脚本里带 `✅`、`↔`、`mm²` 的 `²`，而 Windows
默认控制台是 cp936——一旦把输出重定向到管道或文件（`> log.txt`、CI 收集日志、
编辑器的"运行"按钮），`print` 就抛 `UnicodeEncodeError`，脚本跑到一半崩掉、
退出码 1，看起来像"用例失败"，其实是"输出失败"。

因此：

- 用 `python verify_x.py | tee log.txt` 或 `> log.txt` 都是安全的；
- 若在 cmd.exe 里直接看，建议先 `chcp 65001`（或改用 Windows Terminal）；
- **不要**在脚本里删掉那两行 setup——删了就又回到"偶尔崩在 emoji 上"。

