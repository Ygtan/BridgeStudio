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
| 归档（不再投入） | `D:\AI-work\前处理\explore-x20` | X20 原型，多格式导出器的来源；接入约定见 `X20_接入约定.md` |

## 契约

前处理与求解内核**只通过 `.dat` 文件耦合**（FEM-Eigen new format），
目录通过 `fem_eigen_export.py` 的多候选自动探测定位（可用环境变量
`FEM_EIGEN_DIR` 覆盖），两边可以独立演进、独立提交。

## X20 资产接入

X20 原型只作**器官库**，不做骨架、不做数据源。三条铁律（唯一真源
`ModelSpec3D` / 唯一转换点 `to_canonical` / 只出不进）、A/B/C 三档处理分级、
统一字段·单位·坐标与分块编号规范、已知缺陷修正清单、资产登记表、禁止项，
全部收敛在 **`X20_接入约定.md`**，并由 **`verify_x20_contract.py`**（91/91）
做成可执行守卫。新增或修改 X20 资产必须先登记再实现；仓库内不得出现
归档目录的路径常量或 `bridge_fe_modeler` 导入。
第十五次起 2D 三格式导出 / `.mct` 反向导入另走平面专用平行链，正交约定见
`X20_接入约定.md` §3.4（不改 3D 唯一转换点 `to_canonical`、两条链互不冒充）。

## 能力边界矩阵

`X20_接入约定.md` 管「X20 资产怎么接」，**`能力边界矩阵.md` 管「接到之后到底有哪些能力、哪些还没有」**：
6 桥型 × 2 维度主矩阵（建造器键 / 求解入口 / 阶段·活载·预应力参数逐格对拍）、
20 项维度级能力的 `文件::符号` 依据、参数覆盖契约与刻意隐藏白名单、
3 条真实缺口 + 历次闭环记录（编号容量 / 包络云图 / PDF 记载纠正 / B 类）。由 **`verify_capability_matrix.py`**（280/280）纯离线守卫：
主矩阵与 `BUILDERS` / `SOLVER_OF` 对拍、依据符号真实存在、
`DEFAULTS ⊆ FIELDS ∪ 白名单`、额外控件带 `default`、默认值在区间内、
12 个建造器「控件渲染写回 params 后默认 `.dat` 逐位不变」、
以及新补控件「改值确实改变 `.dat`」的生效性对拍。

本轮据矩阵补齐了 16 个「有默认值、真实参与建模、界面却改不了」的参数
（材料 E/A/I、钢束 fpk、徐变 χ、冲击系数、斜拉桥离散 panel、
2D 斜拉塔基与锚点、悬索加劲梁板厚与车道折减、拱桥主梁板厚等），
`app.py` 的 float 控件支持可选 `format`（默认仍 `%.3f`）。
**默认值一个没改，`.dat` 逐位不变。**

## 融合来源（两版前处理器优缺点结论）

- **NL-FE（骨架）**：ModelDoc 统一外壳、规则+LLM 双通道、单位哨兵、
  8 个 verify 回归脚本约 150 项断言、FEM-Eigen 内核独立回归；
  界面已移除 anastruct 草算与互校，开发期仍保留 anastruct 作独立 oracle。
- **X20（器官库）**：5 种桥型建造器（含斜拉/悬索/组合体系）、BEAM/TRUSS/CABLE
  2D/3D 六种单元表达、组件分块编号规范（SKILL.md）、ANSYS/Abaqus/MIDAS/自研
  四格式导出器、严格 validate_model。

> 上述「器官库」具体怎么用，以 `X20_接入约定.md` 为准；本段只是来源摘要。
> 特别提醒：`bridge_spec.py` 里的「canonical model」是内部真源，与 X20 导出用的
> 「canonical dict」同名不同物，见契约 §2。

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

依赖见 `requirements.txt`（运行依赖：streamlit / matplotlib / pydantic / numpy；
anastruct 仅用于开发期独立校验 oracle，界面运行时不加载）。
无 DeepSeek API Key 时自动走规则解析，完全离线可用。

## 路线图

- [x] **P0** 修复 FEM-Eigen 内核路径断链（多候选自动探测）——2026-10-28
- [x] **P1** ModelDoc 3D 化（第一步）：`model_spec_3d.py`（BEAM/BEAM7/TRUSS/CABLE +
       3D 实常数 + 6 自由度边界 + 施工阶段 + to_dat 3D 导出）、`grillage_3d.py`
       （三跨连续箱梁空间梁格建造器，含横隔板与 Hambly 横向条带刚度）、
       `fem_eigen_3d.py`（对接 example_3d_grillage.exe 求解与包络解析）——
       2026-10-28，`verify_grillage_3d.py` 12/12 全绿：中跨跨中挠度 8.21mm
       vs anastruct 理论 8.11mm（1.2%），与三弯矩闭式解独立吻合
- [x] **P1** ModelDoc 3D 化（ModelSpec / 3D `.dat` / 梁格基准）：
       `model_spec_3d.py` 的 3D 实常数含 IY/IZ/J/IW（BEAM7 翘曲常数单独校验）、
       6 自由度边界，`to_dat()` 已能导出 `*STAGE` / `*LANE_3D` /
       `*INFLUENCE_3D` / `*PRESTRESS` 等 3D 段；基准为三跨连续箱梁 3D 梁格，
       对 FEM-Eigen `bridge_3d_grillage.dat` 黄金算例，`verify_grillage_3d.py`
       现行 13/13（上一条记的 12/12 是 2026-10-28 当时的项数；中跨跨中
       挠度 -8.21 mm vs anastruct -8.11 mm，1.2%），
       并与三弯矩闭式解独立吻合 —— 2026-11-10 核销
- [x] **P1** X20 资产接入统一约定：`X20_接入约定.md` 定死三条铁律与
       A/B/C 三档处理，把散在 5 个建造器编号表、两处「借 / 不借」注释、
       导出器 docstring 里的口径收口到一处；`verify_fem_eigen_link.py`
       去掉归档目录旁路，默认用例改用 FEM-Eigen 自带 `.dat`。
       `verify_x20_contract.py` 78/78 守卫唯一转换点、无旁路、字段映射与
       C 档不借理由 —— 2026-11-10。注意：这**不等于** X20 桥型全量移植，
       下一条仍是真实能力缺口。
- [x] **P1 能力口径可核对化 + 界面参数覆盖**：新增 `能力边界矩阵.md`
       与 `verify_capability_matrix.py` 232/232，把「哪些有 / 哪些没有」逐格
       钉到代码符号；据矩阵补齐 16 个只在 DEFAULTS 里、界面改不了的参数
       （`ps_beam` E/A/I/fpk/χ/1+μ、`truss_3d` 材料、`cable_stayed` panel、
       `cable_stayed_2d` 塔基与锚点、`suspension` 板厚与车道折减、
       `arch_3d` 主梁板厚），并给 `cable_stayed.panel` 写明「布索覆盖下界
       主导、小幅调大不改网格」的边界。默认 `.dat` 逐位不变 —— 2026-11-10
- [ ] **P1** X20 桥型建造器全量移植：`BUILDERS` 的 6 kind × 2 维度 12 格
       都有建造器键，但**没有「斜拉-悬索协作 / 梁拱组合」这类组合体系的
       独立建造器**；且原 7 项高级能力（分项损失、索长/Newton/`[OPT]`、
       承载力、四出口计算书、多格式导出、严格校验）仍不对齐：第九次收官时
       其中 4 项只在 3D 链路，**3 项部分接入**（`2D=P`）：截面应力只出组合内力的
       应力表、承载力 Mu/Vu 因 2D 无配筋契约显式拒绝；引用完整性中文校验
       按源码事实纠正并补齐，但 2D 仍窄于 3D；同源计算书四出口接通了
       `report_2d.py` 的 `Report` 旁路与原样复用的 HTML/DOCX/CSV/PDF
       写出器，但 2D 计算书内容仍窄于 3D、且 opt-in 默认关闭。
       第十次由 FE opt-in 补 `[PRESTRESSLOSS2D]` 段把「分项预应力损失」
       由 `N` 改 `P`（只出非阶段 `LOSSREF` 束明细，**不等于 3D 的阶段
       分项损失**），现为 3 项只在 3D 链路、**4 项部分接入**（`2D=P`）。
       均见 `能力边界矩阵.md`
       §2 备注。2D 模态 / 屈曲已按内核事实接通（opt-in）；第九次由 FE
       `--modal-detail` 追加 `[MODAL2D]` 段（`BASIS=xy`）补出周期 T / X·Y
       两向参与质量 / 节点振型，其余 2D ↔ 3D 仍不对齐（**B2 剩余 2 项**）。
       第十一次由 FE opt-in 留痕补 `[NEWTON2D]` 段，只把「内层 Newton /
       共转列式过程量」一格由 `N` 改 `P`（现为 2 项只在 3D 链路、
       **5 项部分接入**）；`[LENGTH]` / `[OPT]` 两层经只读调研确认 2D
       内核没有对应力学，两格继续标 `N`、不造占位，**B2 剩余 2 项**
       但内容收窄为「两层力学 + 多格式导出」。逐条依据见
       `能力边界矩阵.md` §4.1 / §4.2。
       **这是真实能力缺口、不是文档滞后**，与 P4 一起排期。
       内核侧尚无协作体系求解键（注册表 4 键、`detect_kind` 二选一），
       分阶段规格与验收断言见 `组合体系接入规格.md`；未实现期输入这类描述
       由 `verify_combo_system.py` 守卫显式拒绝，不会静默按单体系计算 —— 2026-11-10
       **2026-11-18 阶段二更新**：斜拉-悬索协作已由 `combo_3d.py` + 第 5 求解键
       `combo` 放行（复用悬索主体 + 塔顶斜拉索 + 目标索力，第 43 项 CTest
       36/36），注册表 5 键、`BUILDERS` 仍 12 格；梁拱/拱梁组合仍无独立建造器
       并保持显式拒绝。
- [x] **P2** 高级能力暴露（STAGE / LANE_3D / INFLUENCE_3D / 模态 / 屈曲 /
       过程量 / JTG D60 组合）：施工阶段 `*STAGE`（`TENSION=` / `L0=` 调索、
       全过程索力包络）、车道荷载与影响面 `*LANE_3D` / `*INFLUENCE_3D`
       （含横向分布与冲击系数）、模态（2026-11-08）、线性屈曲因子与
       `[LENGTH]` / `[NEWTON]` / `[OPT]` 过程量（2026-11-09~10）均已走通
       内核 → 解析 → 结果 → 报告 → 四出口；`combination.py` 按
       JTG D60-2015 表 4.1.5 / 4.1.7 给基本 / 频遇 / 准永久三组合。
       **边界**：组合只计恒载 + 汽车，多可变作用的组合值系数未做；
       3D 预应力的分项损失不含筋-混凝土协同、次内力与截面应力验算
       —— 2026-11-10 核销
- [x] **P2** 3D 预应力分项损失（JTG 3362-2018 §6.2）：3D 新增
       `*PRESTRESS_LOSS` 表 + `*STAGE ... :LOSSREF=<编号>`，与 2D **共用**
       `prestress_loss.hpp` 的键名解析与 σl1~σl6 公式；内核新增
       `stage prestress loss` 结果段（12 位有效数字）。前端贯通
       `PrestressLoss3D` → `to_dat()` → `fem_eigen_3d` 段解析 →
       `result_3d` 明细表与两条同源校核 → `report_3d` 专章 → 四出口；
       `ps_beam` 参数面板有「分项损失参数 JSON」入口，留空时 `.dat`
       逐位不变。**边界**：σl3/σl5/σl6 是后期损失，不进 σpe 与 Pe；
       σl2 只按直线筋，εcs/φ 需按附录 C 查表输入 —— 2026-11-10
- [x] **P3** 内核攻坚：共转列式非线性 + 索长迭代 → 斜拉桥成桥态 —— 2026-10-30
- [x] **P3** 悬索桥成桥态（自锚式 / 地锚式）：主缆索多边形找形（对 H 二分命中
       矢高）→ 平衡态弦长反算 L₀ → 索长迭代；两体系共用同一套内核，只差边界。
       `verify_suspension_3d.py` 61/61 全绿，含两条内核硬校验的负例
       （塔顶不共用节点、端节点悬空都会报错而不会静默算错）—— 2026-10-31
- [x] **P3** 截面应力验算：`code_material.py`（JTG 3362-2018 表 3.1.3~3.1.5、
       JTG D64-2015 表 3.2.1 的强度设计值，按板厚分档）+ `section_strength.py`
       （钢按 D64 §5.3.1 三款算正应力/剪应力/折算应力，混凝土按 3362 §7.1.5
       算 0.5fck）；荷载组合扩到含轴力 N。`verify_section_strength.py` 46/46。
       **边界**：应力验算不算承载力；混凝土按 0.5fck 只作量级判据
       —— 2026-10-31
- [x] **P3** 正截面/斜截面承载力验算：`section_strength.py` 加
       `CapacitySection` + `flexural_capacity`/`shear_capacity`（JTG 3362-2018
       §5.2.2/5.2.3/5.2.9：矩形/工形正截面单筋 + 仅配箍筋斜截面，ξb 用 2018
       修订值）；`model_spec_3d.RebarSpec3D` 挂 `RealConst3D.rebar`（不进 .dat）；
       `grillage_3d` 按全截面配筋率给混凝土箱梁填配筋。`result_3d` 的
       `_checks_capacity` 把基本组合设计内力换成 Mu/Vu 利用率，出校核条目 +
       `capacity_table` 明细表。`verify_capacity.py` 22/22（矩形/工形/斜截面
       手算对拍 + 超筋/未配筋负例 + 梁格端到端）。
       **边界**：只做普通钢筋混凝土（无预应力、无弯起）；工形/T 形压弯、
       负弯矩区与非对称配筋已由后续条目补齐 —— 2026-11-08
- [x] **P3** 桥塔压弯 / 轴心受压承载力：`section_strength` 加
       `eccentric_capacity`（§5.3.4 矩形对称配筋，大偏心 σs=fsd / 小偏心按
       §5.1.5 平截面二分迭代）、`eta_second_order`（§5.3.9 二阶效应）、
       `check_axial`（§5.3.1 轴心受压 0.9·φ·(fcd·A+f'sd·A's)）；`cable_stayed_3d`
       /`suspension_3d` 塔柱对称配筋接 `RebarSpec3D`；`result_3d._checks_capacity`
       按轴力+偏心距三分派（轴心受压 / 偏心受压 / 受弯），索承体系也接上承载力
       验算。`verify_capacity.py` 42/42（含大/小偏心、η、轴心受压手算对拍 +
       斜拉桥塔柱端到端）。
       **边界**：非对称配筋与工形/T 形/箱形压弯见后续条目 —— 2026-11-08
- [x] **P3** 桥塔稳定系数 φ：`section_strength.stability_factor` 按长细比
       查 JTG 3362-2018 表 5.3.1（线性插值）——矩形用 l0/b、箱形用 l0/i
       （最小回转半径）；`check_axial`/`check_eccentric` 从 `sec.l0` 取计算
       长度；`cable_stayed_3d`/`suspension_3d` 塔柱默认 l0=2×塔高（附录 E，
       一端固定一端自由，最保守，可调 `tower_k`）。`verify_capacity.py` 63/63
       （含 φ 表值对拍 + 箱形对拍）。—— 2026-11-08
- [x] **P3** 箱形塔柱截面（§5.3.5 工形压弯 + l0/i 回转半径）：`CapacitySection`
       加 `hollow` 标志 + 毛面积/最小回转半径（空心矩形外减内）；`eccentric_capacity`
       扩到工形/箱形（第一类 x≤hf 按 bf 宽矩形、第二类 x>hf 按工形，受压区面积
       在 x=hf 处连续）；`cable_stayed_3d`/`suspension_3d` 加 `tower_wall`（>0
       用空心箱形塔柱，等效工形 bf=外宽、hf=壁厚、b=两片腹板厚之和）。箱形
       回转半径大于实心，故 φ 更大、更抗屈曲。—— 2026-11-08
- [x] **P3** 承载力计算书（逐项复核）：把 x、ξ、ξb、σs、η、φ、λ、Ac、e0、e'
       等中间量连同 JTG 3362-2018 / JTG D60-2015 条文号写进报告。
       `section_strength` 各纯函数只增加返回键（不改数值），拆出
       `eta_second_order_detail` / `reverse_failure_detail` /
       `axial_capacity_detail` 让结论与计算书共用同一份公式；新增 `CalcCase` +
       `capacity_calculation_sheet`（六列：部位 / 验算链 / 计算量 / 数值 / 单位 /
       条文出处）。`result_3d._capacity_cases` 让工程校核与 `tables()` 共用一份
       分派结果，`report_3d` 新增独立「承载力计算书」章（汇总表 + 逐项复核表，
       不落进文末明细表章）；梁格与索承体系（塔柱 φ/λ）都出表，HTML / DOCX /
       CSV 三种出口同源（PDF 出口见下一条）。`verify_capacity.py` 130/130。—— 2026-11-08
- [x] **P3** 模态 / 自振特性全量暴露：内核 `computeNaturalFrequencies3D` 与
       `cable_solver` 的 `modalFromTangent` 本来就在算模态（只为冲击系数留下
       一个 `f1_vertical`）。新增 `FEM-Eigen/modal_output_3d.hpp` 把同一份
       `ModalResult3D` 写成 `modal` / `[MODAL]` 段（前 8 / 12 阶频率、周期、
       X/Y/Z 有效参与质量、φᵀMφ、节点三平动振型）；Python 侧 `modal_3d.py`
       统一解析，`fem_eigen_3d.py` / `fem_eigen_cable.py` 共用一份正则，
       `SolveResult.modal` → 指标卡 / 三条 info 校核 / `modal_table` → 报告独立
       「模态与自振特性」章与振型图 → HTML / DOCX / CSV / PDF 四出口。
       线性内核用成桥态构型 + 线弹性刚度，索承体系用**成桥态一致切线刚度**，
       与 `f1_vertical` 出自同一次特征求解，不另造一套频率。
       `verify_modal_3d.py` 54/54（含 10 m 简支梁 Euler-Bernoulli 闭式解
       f₁=119.9534 Hz 对拍误差 0.0007%、黄金基线前 6 阶、无质量负例、
       斜拉桥切线刚度端到端）；全套 27 个 verify 脚本 rc=0。—— 2026-11-08
- [x] **P3** PDF 计算书出口：`report_3d.write_pdf` 把 `render_html()` 的**同一份
       自包含 HTML** 交给本机无头 Edge / Chrome 按 A4 打印，不另写一套排版，
       也不引 reportlab 之类第三方 PDF 库——中文、ξ/φ/η/σs、200+ 行明细表的
       断行与插图缩放因此与 HTML 完全一致。加强 `@media print`：`@page A4`、
       `thead` 逐页重复、按 `tr` 分页（整表 `break-inside:avoid` 会在长表上
       留大片空白）、图与标题不跨页。`_pdf_browser()` 按环境变量
       `BRIDGESTUDIO_PDF_BROWSER` → PATH → 常见安装位置查找；找不到或打印
       失败抛 `PDFExportError`，界面显式告警并保留 Word/HTML/CSV 三份，
       **不生成占位 PDF**。先打到临时文件、校验 `%PDF-` 头与大小后才复制，
       避免目标路径上的旧 PDF 被误当新产物。`write_all(pdf=True)` 默认含
       PDF，界面 `ui_3d._report_block` 四按钮下载。`verify_report_3d.py`
       125/125（含 PDF 逐页文本、条文号、插图、空白页与故障注入）、
       `verify_capacity.py` 131/131。—— 2026-11-08
- [x] **P3** 线性屈曲因子全量暴露：内核 `computeBucklingResult3D` 本来就在解
       `(K+λ·Kg)φ=0`（此前只在 `example_3d_modal` / `modal_bench` 打印到控制台）。
       新增 `FEM-Eigen/buckling_output_3d.hpp` 写 `buckling` / `[BUCKLING]` 段
       （前 6 阶 λ、Pcr=λ·P_ref、Kg 非零元数/受压标志/自由自由度等诊断）；
       `finalStageBucklingModel3D` 用成桥态构型 + 成桥态恒载合力作参考荷载
       （阶段增量按节点/自由度累加）。几何刚度补齐 TRUSS/CABLE 的横向投影式
       （原来只装 BEAM，空间桁架桥 Kg 恒为 0、永远算不出 λ）。Python 侧
       `buckling_3d.py` 统一解析 → `SolveResult.buckling` → 指标卡 / info 校核 /
       `buckling_table` → 报告独立「线性屈曲与稳定」章与 λ 柱状图 → 四出口。
       **口径**：λ 是荷载整体放大倍数，不是 JTG 3362 §5.3 的稳定系数 φ，
       规范没有 λ 的合格限值；`STATUS=no_compression`（梁格只受竖向荷载、无压杆）
       是物理预期，不摆空表；索承体系成桥态为几何非线性、切线刚度已含几何刚度，
       内核写 `ERROR=nonlinear_state_not_supported`，报告如实标注「本次未产出」。
       `verify_buckling_3d.py`（含 20 段悬臂柱 Euler 闭式解 λ₁=20.33138507 对拍，
       误差 5×10⁻⁸）。—— 2026-11-09
- [x] **P3** PDF 归档增强（页码 / 章节书签）：`write_pdf` 在 Chromium 打印后
       加一层 pypdf 后处理——页脚叠 `N / 总页数`（Helvetica 标准字体，
       不嵌中文字体）、按 `render_html` 打印的 4px 白色锚点 `BKSEC<块号>Z`
       精确定位章节页并写 `/Outlines`（h1 一级 / h2 二级）、补齐标题与结果
       指纹等元数据。**Chromium 仍是唯一排版器**，pypdf 不改正文一个字。
       锚点丢失 / 书签为空 / pypdf 缺失一律抛 `PDFExportError`，界面保留
       Word/HTML/CSV，不交付"假归档版"；`navigation=False` 为显式降级对照。
       `verify_buckling_3d.py` + `verify_report_3d.py` 逐页对拍页码、书签标题与
       目标页顺序。—— 2026-11-09
- [x] **P3** 阶段二 A「暴露索长迭代过程量」：内核 `fem_3d_nl.hpp` 的两类外层
       迭代（影响矩阵 `J = M·D` 的 Newton 求下料 L0、Woodbury 低秩 Newton +
       回溯线搜索求制造线形 ΔX）原本只把每轮误差打到控制台，结果文件里只有
       4 个汇总标量。新增 `FEM-Eigen/cable_length_output_3d.hpp` 把同一份
       `IterationTrace3D` 写成 `[LENGTH]` 段（逐轮 `I` 行、逐索 `E` 行、完整
       `NJ`+`J`、`SENS` 诊断），**数值口径一字未改**；Python 侧
       `cable_length_3d.py` 唯一解析 → `SolveResult.cable_length` → 指标卡 /
       info 校核 / 逐轮表 / 逐索表 → 报告独立「索长迭代与成桥态几何闭合」章 +
       双轴收敛图 → 四出口同源。**口径**：`KIND=cable_length`（TENSION，
       判据是最大相对索力偏差）与 `KIND=erection_geometry`（COMPLETION，
       判据是 max|ΔX+u|，单位 m）控制误差单位不同、不能画同一根轴；
       `geomErr` 只是结果量，实测索力收敛到 3e-8 而弦长偏差稳定在 0.15 m，
       即「索力命中 ≠ 线形闭合」；STATIC / 悬索桥找形本无外层迭代，内核写
       ERROR 段、报告写明原因，不摆空表冒充「0 轮收敛」。过程量**不构成
       规范意义上的合格判定**。`verify_cable_length_3d.py` 58/58：手写段逐
       字段对拍 + 10 类故障注入 + 段分派不串段 + 独立 oracle（J 对角全负、
       误差单调下降、两段同源）+ 三模式实跑 + 四出口 PDF 读回。—— 2026-11-09
- [x] **P3** 阶段二 A 续「暴露内层共转列式 Newton 过程量」：上一轮只补了
       **外层**迭代，`solveModel3DNonlinear` 的荷载步 / 逐次 Newton 残差仍只
       打在 verbose 控制台。内核新增 `NewtonStep3D` / `NewtonTrace3D` 与
       `FEM-Eigen/newton_output_3d.hpp`，写 `[NEWTON]` 段（逐迭代 `S` 行：
       荷载步、步内迭代、λ、|R|、|du|、|Ftarget|、重建分解、本步收敛）；
       `cable_solver` 输出最终成桥态那一段，`example_3d_stage_nl` 每阶段
       各输出一段（`STAGE=<id>`）。Python 侧 `newton_3d.py` 唯一解析 →
       `SolveResult.newton`（bundle，支持多阶段）→ 指标卡 / info 校核 /
       逐荷载步表 / 逐迭代表 / 阶段汇总表 → 报告独立「内层 Newton 与共转
       列式求解」章 + 残差收敛图 → 四出口同源。**口径**：收敛判据是
       `|R| ≤ tolForce·max(|Ftarget|,1e-12)` **或** `|du| ≤ tolDisp·max(1,|u|)`，
       每个荷载步都要满足；换步时 |Ftarget| 变大、残差允许跳升，不是一根
       单调收敛曲线；规范没有 Newton 迭代轮数限值，不判合格。
       顺带核销 `TODO(P3-2b)`（影响矩阵复用同一次分解早已实现，只是注释
       过时），并对 `TODO(P3-3b)` 做了取证：实测 COMPLETION 六轮全走
       Newton 步、零次 FALLBACK，收敛比约 0.025，故未盲改成桥态数值路径，
       把完整敏感度的实现规格与验收 oracle 写进 `USER_MANUAL.md` §9.8。
       `verify_newton_3d.py` 52/52（离线 34 + live 18）。—— 2026-11-09
- [x] **P3** 阶段二 B「架构口径收口」：`example_3d_stage_nl.exe` 正式注册进
       `solver_3d.py` 的 `_registry()`（第四个键 `stage_nl`），`SolverSpec`
       加 `result_key` 让结果对象仍表现为 `grillage3d`、不破坏界面缓存与
       报告分支；`fem_eigen_stage_nl.solve_staged_result` 拆成两段式
       `run` / `adapt`，去掉 `model_doc.solve()` 的硬编码旁路（4 处）；
       `ui_3d.py` 的求解器名从注册表取，不再写死 exe 名。修正
       `example_cable_stayed_nl.py` 头部注释与实际非线性链路的矛盾，
       并给 `nl_steps` / `newton` / `max_iter` / `mint` 补界面参数入口
       （默认值下 `.dat` 一字不变）。`verify_stage_nl.py` 14/14、
       `verify_solver_3d.py` 67/67，两条链路的 `.dat` / 索力 / 位移 /
       梁端内力逐项一致。—— 2026-11-09
- [x] **P3** 阶段二 D「真正的带约束成桥目标索力优化」：内核新增
       `cable_opt_3d.hpp`，用已有 `Ld` / `solveMulti` / `beamEndForcesAt`
       组装弯矩影响矩阵，解带索力箱约束 + 逐塔 `ΣT·cos=0` 软约束的二次型
       （`MINMAXM` 用 IRLS、`MINENERGY` 用弯曲应变能），T\* 写回后仍走既有
       索长/几何闭合命中路径。`OPTIMIZE=1` 显式开启，默认全关、
       既有算例逐位不变。前端 `cable_opt_3d.py` 唯一解析 `[OPT]` 段 →
       `SolveResult.cable_opt` → 指标卡 / info 校核 / 逐轮表 / 逐索表 →
       报告独立「带约束成桥目标索力优化」章 → 四出口同源。
       **口径**：线性化弯矩目标、成桥态 `[GIRDEREND]` 实际 `max|My|`、
       无量纲归一化目标三者分列，禁止互相相减冒充改善量；
       `APPLIED=0`（守卫拦下）时 T\* 未写回、只是过程量；塔顶平衡是软约束
       且与 min max|M| 有实测权衡，未采纳 / 未收敛都不冒充通过。
       `verify_cable_opt_3d.py` 82/82。—— 2026-11-09
- [x] **P3** 阶段二 D 补「成桥体系守卫」：原示例 `MINENERGY+OPTBALANCE=0`
       数学上收敛（`NITER=196`、成桥态弯矩降 26.5 %），却把边跨索压到
       `TMIN`，索承担桥面恒载 99.31 % → 89.04 %、同跨相邻索力比
       1.08 → 31.98，计算书工程校核表出现两处不通过。内核在**写回 T\*
       之前**按与计算书同一口径预估三条体系判据（索承担恒载 > 90 %、
       支座不上拔、同跨相邻比 ≤ 1.5），不合格写
       `STATUS=system_check_rejected` / `APPLIED=0`、**不写回 T\***、
       `REJECT` 列原因码；成桥态求解后回填 `SYSCHECK_FINAL`
       （`APPLIED=0` 时属原方案）。新增 `SYSGUARD/SYSDECKMIN/
       SYSDECKMAX/SYSADJMAX`，默认开启，`SYSGUARD=0` 仅供诊断。
       前端新增体系守卫表与中文结论，`verify_cable_opt_3d.py` 119/119
       （离线 72 / live 47）；**当时**全套 32 脚本 1384 项断言、内核 ctest 7/7。
       实测原示例被拦下后工程校核表两处不通过消失，`MINMAXM+MINT=0.30`
       三条全过正常放行。—— 2026-11-10
- [x] **P3** 负弯矩区双向验算 + 非对称配筋（JTG 3362 §5.2.2/5.2.3、
       §5.3.4-4/5）：`CapacitySection` 加 `FaceSpec negative`（底板宽厚与顶板
       纵筋可单独登记）和 `Asp_given`（显式受压筋，可为 0=单筋）；
       `mirror_section_for_negative` 把截面上下翻转后复用 §5.2 公式；
       `check_capacity` 对正/负弯矩各算一次 Mu、取利用率大者控制，新增
       `capacity_faces_table` 两面留痕，未登记负弯矩面给「对称近似」告警；
       `eccentric_capacity` 支持非对称配筋，`reverse_failure_util` 按
       §5.3.4-4/5 补小偏心反向破坏；`grillage_3d` 加 `rebar_ratio_neg`
       单独配支点顶板纵筋。`result_3d._checks_capacity` 不再取 |My| 丢掉符号，
       压弯链也按两个弯矩方向取不利面。`verify_capacity.py` 104/104。
       **边界**：工形/箱形非对称配筋的反向破坏（含受压较小边翼缘项）
       未覆盖，结果里如实标注。—— 2026-11-08
- [x] **P3** 索承体系梁应力：`cable_solver` 新增 `[GIRDEREND]`（每端一行，两种桥型
       都输出），斜拉桥第一次有主梁内力；`result_3d._checks_strength_cable` 用
       `_strength_sections + section_strength` 做正应力/剪应力/折算应力，报告增
       「控制截面应力」表。`verify_section_strength.py` 59/59（含同端配对的手算对拍）。
       **口径**：成桥态恒载标准值 + 活载包络，按 JTG D60 系数组合（基本/频遇/准永久），
       正应力枚举 N×My 角点取最不利（包络法偏保守）；钢超限 error、混凝土 warn。
       —— 2026-11-01 组合接入
- [x] **P3** 索承体系梁内力图（`girder` 键）：把 `[GIRDEREND]` 的每端内力拼成
       纵向梁分段，画 My/Vz/N 三联（沿桥长）。**边界**：这是成桥态单一状态、
       **不是活载包络**（活载包络另见下一条目），图注写死；桥塔按几何筛除（同框会压平主梁）；
       两端连线即真弯矩图——分布荷载已节点化、单元内弯矩严格线性（实测 ΔM/Vz=−5 m）。
       `verify_report_3d.py` 91/91 —— 2026-10-31
- [x] **P3** 施工阶段调索（`*STAGE` 的 `TENSION=` / `L0=`）：内核在阶段内迭代命中目标
       索力（`ΔP = N目标 − N当前`，按索力状态而非参考几何算），跨阶段继承索力状态与
       按节点累积的位移；前端 `Stage3D.tensions/unstrained` → `.dat` → 结果解析 →
       两条校核（索无受压、末阶段索力 vs 设计目标）。`verify_stage_cable.py` 21/21。
       **边界**：线弹性调索；分批张拉时先张的索会被带跑（分两批实测 193.7%），内核输出
       `devDes` 量化而不静默，二次调索可拉回（实测 1e-15）—— 2026-10-31
- [x] **P3** 施工期索力全过程与包络：`SolveResult.stage_cable_summary()` 把逐阶段
       平铺压成"每根索一条过程"（峰值/谷值及阶段、`ratio`、`devRange`、`notYet`/`dropped`）；
       表「施工阶段索力全过程」+ 图「施工阶段索力过程」（线的起点 = 张拉阶段，不补零）+
       四条校核（全过程无受压、峰值 ≤ A·fpu/2.0、末阶段 vs 目标、全程峰值 vs 目标）。
       **口径**：索力可跨阶段取极值但不能累加；"被带成负值"的索峰值恰好等于目标
       （`ratio=1.00`），排序与高亮用 `devRange` 才抓得住；校核必须走全过程，只查末阶段
       会漏掉"中途压负、后来补调回来"。`verify_stage_cable.py` 45/45 —— 2026-11-01
- [x] **P3** 施工阶段预应力（`*STAGE` 的 `PRESTRESS=`，直线筋 + 曲线筋等效荷载法）：
       直线束翻译成两端轴压 `Pe` + 两端偏心弯矩 `Pe·e`（`Pe=P·loss`）；曲线筋再叠加
       两端锚固切线竖向力 `4Pe·S/L` + 跨内均布 `q=−8Pe·S/L²` 节点化。按增量法在张拉
       阶段施加；前端 `Stage3D.prestress`（`Prestress3D`）→ `.dat` → 结果解析
       （`stage_prestress`）→ 校核（有效预加力为正、阶段末合计）。
       **边界**：直线筋**不产生上拱**；上拱来自曲线筋（`S<0` → 跨中筋更低 → 向上
       均布力），闭式解 `−Pe·S·L²/(12EI)` 已锁，但依赖跨内单元细分（单元内弯矩仍
       线性）；应力判据（σ≤0.75fpk）没做——缺束面积 `Ap` 输入，不下假判据。
       `verify_stage_prestress.py` 8/8 —— 2026-10-31（曲线筋 2026-11-01）
- [x] **P3** 索承体系活载梁内力包络（`girder_live` 键）：`cable_solver` 新增
       `[GIRDER_LIVE]`（N/My/Vz 包络），影响线在**成桥态切线刚度**上算（张紧索切线
       刚度远大于弹性刚度，不能用线弹性装配），恒载由非线性精确算入、活载增量用
       切线刚度求位移 + 线性弹性刚度提取梁端内力（P-Δ 一阶略去）。前端显式
       `*LANE_3D` 优先、空则自动识别主梁；活载包络三联图（填充带 + 灰虚线恒载对照）。
       **边界**：横向布载用横向分布系数法（车道数 × 多车道横向折减 × 偏载增大，
       默认 3×0.78×1.15=2.691，偏载系数为经验值，严格做法需梁格横向影响面）。
       汽车冲击按 JTG D60 计入（成桥态切线刚度求竖向一阶弯曲基频 f1→μ，活载放大
       1+μ）。
       组合应力验算已接入（基本/频遇/准永久，正应力枚举 N×My 角点取最不利、
       包络法偏保守）。
       `verify_report_3d.py` 92/92、`verify_section_strength.py` 59/59 —— 2026-11-01
- [x] **P3** 大跨钢箱拱桥施工（`arch_3d`）：`arch_3d.py` 参数化建造器，下承式单肋平面
       模型（拱轴线二次抛物线、无铰拱拱脚固结、塔顶锚点简化为固定边界），施工序列
       18 阶段：拱脚段 → 对称吊装 + 斜拉扣索张拉（`TENSION=` 目标 = 段自重 / 竖向方向
       余弦，截断到 0.7·A·fpu）→ 拱顶合拢 → 拆扣索（`DEACTIVE`）→ 吊杆主梁 → 二期铺装。
       自然语言识别：`arch_3d` 关键字（拱肋/分段吊装/扣索/合拢）先于「斜拉」判（
       「斜拉扣索」是拱肋吊装的临时扣索、不是斜拉桥）；`model_doc` 加 `arch_3d` kind、
       `units_guard` 登记单位阈值、`result_3d._checks_stage_cable` 识别被 `DEACTIVE` 的
       临时索（「末阶段 vs 目标」与「受压」降级为提示——合拢后卸载到松弛是正常施工物理）。
       `verify_arch_3d.py` 21/21、`example_arch_500m_nl.py` 端到端 —— 2026-11-04
- [x] **P3.5** 自然语言输入文件：三个算例（悬索/斜拉/拱）的描述统一落到 `examples/*.txt`
      （唯一真源），`run_nl.py` 作命令行统一入口（`--list` / 跑全部 / 跑单个），三个
      `example_*_nl.py` 的 `DESC` 改为从 `.txt` 读 —— 2026-11-05
- [x] **第五类工程性老坑 ①②③**：① 2D `NONLINEAR` 端部释放（静态凝聚按完整 6 自由度投影 + 一阶几何刚度；顺带修 `condenseBeamGlobal()` 只散射 nf 个自由度的同源错位，无释放路径逐位不变）；② 2D 逐跨施工张拉入口；③ AppTest 环境性超时守卫。逐跨张拉：`beam_bridge` 勾选「逐跨施工」后多出**逐跨张拉力 / 钢束偏心 / 总损失系数**三个控件，每阶段写一条 `*STAGE_PRESTRESS`；内核 `EQUIV` 补「梁组两端 `dof1` 都自由 → 自平衡轴力对 `+Pe/-Pe`」分支，连续梁中间跨终于能张拉，外围结构不背附加轴力。默认张拉力 0，既有 `.dat` 逐位不变；`loss` 是总损失系数，不是 JTG 3362 分项损失，也不做 TENDON 筋-混凝土协同。③ 的超时阈值可用 `BRIDGESTUDIO_APP_TIMEOUT` 配置，只对明确的墙钟超时单次重试并给诊断，真实功能异常原样抛出、断言一条不放宽。`verify_stage_tension_2d.py` 27/27（live 三跨成桥态轴力精确 = −P·loss）、`test_2d` 129/129、`ctest` 7/7。—— 2026-11-10
- [x] **P2 真缺口收口：3D 分项预应力损失**：内核 2D/3D 共用
      `parsePrestressLossLine()` 与 `computePrestressLosses()`；3D
      `resolvePrestressLosses3D()` 把 `σpe/σcon` 写回 `loss`，`LOSSREF` 与
      `LOSS` 互斥，引用缺失、有效比越界、未解析就求解全部硬报错。
      `example_3d_grillage` 新增 `stage prestress loss` 段（仅 LOSSREF
      路径有，12 位有效数字；`stage prestress force` 段逐位不变）。
      BridgeStudio 在 `fem_eigen_3d.sections` 登记新段防串段，新增
      `PrestressLoss3D` 契约、`prestress_loss_table` 明细表、两条同源校核
      与报告专章；`ps_beam` 界面用一行 JSON 表达 22 个规范参数，留空即
      回到原 `loss` 路径。`test_3d` 88 → 115/115（[27] 27 项）、
      `verify_stage_prestress.py` 64 → 88/88（新增 24 项）、`ctest` 7/7、
      `dat_byte_regression` 37/37 无需刷新基线。—— 2026-11-10
- [x] **P2 真缺口收口：3D 梁格逐跨施工张拉**：内核 3D 的
      `*STAGE ... PRESTRESS=`（等效荷载法）、`stage prestress force` 段与
      结果校核本来就齐，缺的是建造器不生成束、界面没有控件。现
      `grillage_3d.build_box_grillage_3d()` 增 `stage_tension_kN/e/loss/s`，
      逐跨每阶段给每条纵梁线各挂一束首尾相接的开链，**全宽张拉力按纵梁线
      均分**（每束 P=P_total/n_lines，全截面轴压合计仍为 P·loss）；
      `ps_beam` 的 `LOSSREF` 分项损失路径不变。界面勾选「逐跨施工」后多
      四个控件、就地写明口径；`ModelDoc.to_dat()` 顺带改为按维度分流
      （3D ps_beam 原先会掉进 2D `spec_to_dat` 并抛 `AttributeError`）。
      结果层新增 `stage_prestress_table`、报告「施工阶段预应力」专章与
      四出口同源；默认张拉力 0 时 `.dat` 逐位不变。
      `verify_stage_tension_3d.py` **84/84**（live 无外荷载时每条纵梁成桥态
      轴力精确 = −P·loss，loss / e / s 各自对拍；编号容量放开后另加
      12 格默认 `.dat` 黄金哈希与 wide 号段 live 闭式解断言）—— 2026-11-10
- [x] **P1 组合体系：先定规格 + 显式拒绝，不静默降级**：内核只有
      `grillage3d / cable_stayed / suspension / stage_nl` 4 键，`detect_kind()`
      在斜拉/悬索间二选一，**没有协作体系成桥态找形**；Python 侧硬拼模型
      会给出收敛但力学错误的结果。新增 `组合体系接入规格.md`（内核/Python
      两阶段规格、编号段登记、验收断言），`ModelDoc.from_text()` 识别
      「斜拉-悬索协作 / 梁拱组合」写 `combo_system` 哨兵，`validate()` 与
      `solve()` 用同源中文文案拒绝。`verify_combo_system.py` **86/86**
      锁住拒绝路径、正常语料不误伤、注册表仍是 4 键、12 格默认 `.dat`
      逐位不变。契约 §7 `B2` 仍如实标「部分」—— 2026-11-10
      **2026-11-18 阶段二**：`combo_3d.py` 落地，§3.7 口径参数（悬索段占比 /
      边跨比 / 塔高比 / 索面数 / 分界节点）入库，斜拉-悬索协作走第 5 键
      `combo` 完成协作成桥态找形（`r_tower` 命中），第 43 项 CTest 36/36。
- [x] **P1 全部建造器切换紧凑分块编号，默认 `.dat` 哈希已刷新**：唯一分配入口
      `block_numbering.BlockNumbering.allocate()`；节点与单元各自按组件组顺序
      分配，首组从 1 起，后续组数量 ≥50 按百位 +1 起、<50 按十位 +1 起，组内
      连续（规则与组顺序见 `X20_接入约定.md` §5.1/§5.2）。阶段激活与张拉链按
      元素元数据查表，不再从单元号反解。12 格默认 `.dat` 的 sha256 黄金哈希
      已在 `verify_stage_tension_3d.py::DEFAULT_DAT_SHA` 按新字节刷新；
      wide 的唯一性/阶段覆盖/张拉链/live `N = −P·loss/n_lines` 全绿。**只改编号
      标签、不改力学输出行，`dat_byte_regression` 37/37 基线不受影响**；能力矩阵
      §4 缺口由 5 条减为 4 条并留闭环记录 —— 2026-11-18
- [x] **P4 前半：3D 活载包络结构云图**（纯展示层，零内核改动、零 `.dat` 改动）。
      新增 `envelope_3d.py` 作两套内核口径的唯一真源：线性梁格读
      `live_beam`（My/Vz，`live beam envelope`），索承体系读 `girder_live`
      （N/My/Vz，`[GIRDER_LIVE]` 成桥态切线刚度影响线）；`view_3d.fields()`
      按**实际有量的分量**出 `live_*` 着色场（界面可旋转、悬停给两端 min/max），
      `report_3d` 新增「3D 包络视图」章出同场景轴测云图 + 填充带 + 峰值表，
      `result_3d.tables()` 出逐单元 · 逐端明细表。`status()` 五种分流
      （ok / 全零段 / 未定义车道 / 定义了车道却缺段 / min>max）都**章节照出、
      不出图不出表**，不补零、不静默兜底。图注、表注、章节说明统一写明
      「**不是规范全工况最不利包络**（未枚举温度/沉降/风/人群与多可变作用组合）、
      施工阶段过程量不相加、缺分量不补零」。`verify_envelope_3d.py` 守卫
      （离线口径 + live 两类模型云图/报告 + 不静默兜底 + 12 格默认 `.dat`
      黄金哈希逐位不变）。能力矩阵 §4.3 收窄为「MIDAS 数值对拍未做」，
      §2 增加该能力行 —— 2026-11-10
- [ ] **P4** 成果层：**3D 包络视图已闭环**（结构云图 / 沿桥长填充带 / 峰值表 / 逐端明细表，见下条）；**MIDAS 数值对拍仍未做**——本机无 MIDAS，`.mct` 往返自检不等于数值对拍。Word/DOCX 与 PDF 计算书已支持，含承载力逐项复核章。两条边界的逐条说明见 `使用说明.md` 第十一章。

### 2D 模态明细（`BASIS=xy`，第九次）

FE 2D 内核本来就算出了全阶振型与一致质量矩阵，只是 `example_modal.exe`
只写频率 / λ / μ。第九次由 `modal_output_2d.hpp::formatModalSection2D`
把同一份结果写成 opt-in 的 `[MODAL2D]` 段：`--modal-detail` 才追加，
不加开关时 `.modal.txt` 与默认 `.dat` 逐字节不变、回归黄金基线不刷新。
`modal_2d.py::parse_modal_detail` 只解析，出「周期 / 参与质量」与
「节点振型（全阶）」两张表，同步进 `write_2d_html` / 四出口 `Report` /
界面 ⑥；CLI `solve_2d(modal=True, modal_detail=True)` 与界面第二层开关
都是 opt-in 默认关闭。`verify_modal_detail_2d.py` 守卫 FE 源码契约、
12 类负例、零污染、12 格黄金哈希与四出口同源。**用户拍板的方向口径**：
2D 每节点 3 dof（水平 u / 竖向 v / 转角 θz），没有出平面自由度，
Z 向参与质量**不存在（不是 0，不补零）**，θz 只作振型展示；
`T=1/f` 由内核写出，Python 不得现算。参与质量是模态截断判据，
不是规范合格判定。

### 2D 分项预应力损失明细（`[PRESTRESSLOSS2D]`，第十次）

FE 的 `prestress_loss.hpp::computePrestressLosses` 六项公式 2D / 3D 同源，
但 2D 入口原先只取 `effectivePrestressRatio` 一个标量，六项算完当场丢弃。
第十次（用户授权破例改 FE 内核）**不新算力学**：`loss` 行一字不动，另取
一份同源明细，由 `prestress_loss_output_2d.hpp` 写成 opt-in 的
`[PRESTRESSLOSS2D]` 段（`BASIS=2d_global`、`P<id> SIGMA_CON= SL1=..SL6=
SPE= RATIO=`，12 位有效数字、单位 Pa，字段与 3D `stage prestress loss`
同源）。`example_mixed.exe <dat> --loss-detail` 写 `<stem>.loss.txt`；
`--loss-detail-only` **只取明细、不求解、不写 `.result`**，避让
`example_stage.exe` 施工阶段主路径。默认 `.result` / 控制台 / 12 格默认
`.dat` 逐位不变，FE 回归黄金基线不刷新；只收录 `LOSSREF` 束，缺束写
`ERROR=no_lossref_prestress` 不摆空表。**2D 没有阶段维度**：不设 `STAGE`
占位、不造阶段号，`P<id>` 是输入行首显式束号，不等于 3D 的阶段分项损失。
BS 侧 `bridge_spec.py` 补 22 键 `*PRESTRESS_LOSS` 表与非阶段
`*PRESTRESS ... LOSSREF=` 束（显式挂 `BridgeSpec`，不进 `DEFAULTS` /
`FIELDS`）、`prestress_loss_2d.py` 唯一口径层只解析不现算不补零；
`solve_2d(loss_detail=True)` / `ModelDoc.solve(loss_2d_detail=True)` /
界面第二层开关 `loss2ddetail_opt` 双层 opt-in 默认全关，HTML 与四出口
`Report` 用同一张表。`verify_prestress_loss_2d.py` 守卫 FE 源码契约、
独立闭式解、12 类负例、默认 HTML 零污染、12 格黄金哈希与四出口同源。
矩阵该格 `2D=P` 不是 `Y`；**B2 剩余 2 项**（三层迭代过程量 / 被 X20
契约锁死的多格式导出与 `.mct` 反向导入）继续写「没有」。

### 2D 内层 Newton 逐迭代过程量（`[NEWTON2D]`，第十一次）

只读调研先纠偏：2D 内核全文无 `[OPT]` / `[LENGTH]` / `[NEWTON]` 三个段头，
也没有 `std::cout` 过程量打印（「只打控制台」是 3D CHANGELOG 历史描述被
误套）。2D 有方案C 几何非线性 Newton-Raphson，但逐迭代的 `dU` / `alpha` /
`lamTarget` / `Fint` / `Fext` 全是局部变量用完即丢。**第十一次只闭环内层
Newton 一层**：FE `fem_2d.hpp` 新增 `NewtonStep2D` / `NewtonTrace2D` 挂进
`ModelResult`，`fem_2d.cpp` 循环里只 `push_back` 留痕，残差判据 / 位移
增量判据 / 线搜索 / 收敛与荷载步细分**一字未改**；
`newton_output_2d.hpp` 写 opt-in 的 `[NEWTON2D]` 段
（`BASIS=2d_global`、头部 10 键、逐迭代 `S` 行、`setprecision(10)` 与 3D
`[NEWTON]` 同源）。字段按 2D 事实取舍：直接求解故**不写 `REFACT`**，
只有一个 tol 故**写 `TOL` 不拆 `TOLF`/`TOLD`**，没有阶段维度故**不设
阶段占位**；`|du|=0` 是「残差已达标、本步未解增量」的事实值，不是补零。
`example_mixed.exe <dat> --newton-detail` 把段写进 `<stem>.newton.txt`；
`--newton-detail-only` 求解后只写明细、不写 `.result`（过程量必须求解后
才有，与 `--loss-detail-only` 的「不求解」不同），避让施工阶段主路径。
默认 `.result` / 控制台 / 12 格默认 `.dat` 逐位不变，FE 黄金基线不刷新；
线性模型写 `ERROR=no_newton_solution`，不摆空表。BS 侧 `newton_2d.py`
唯一口径层只解析、不重算 `K·ΔU=R`、不补零；`solve_2d(newton_detail=True)`
/ `ModelDoc.solve(newton_2d_detail=True)` / 界面 `newton2ddetail_opt`
双层 opt-in 默认全关且只对显式 `*ANALYSIS TYPE=NONLINEAR` 生效，HTML 与
四出口 `Report` 用同两张表。`verify_newton_2d.py` 守卫 FE 源码契约、
端到端 oracle、20 类负例、默认 HTML 零污染、12 格黄金哈希与四出口同源。
矩阵该格 `2D=P` 不是 `Y`；**第十三次已按 opt-in 首次真正新造 2D 力学、
接通 `[LENGTH2D]` 平面索长闭合一层（该格 `N→P`、仍 `P` 不 `Y`）；第十四次
起 `[OPT2D]` 2D 平面索力优化已按 opt-in 新造接入**（该格 `N→P`、仍 `P` 不
`Y`），只 MINMAXM + 显式索力箱，3D 的 SYSGUARD / MINENERGY / `J=M·D`
影响矩阵在 2D 仍没有、不等于 3D `[OPT]`；2D 版不等于
3D 的垂度 / Ernst / 共转成桥态 / 闭式影响矩阵，本项不等于 3D 三层迭代
已移植。过程量只说明数值如何收敛，不是规范合格判定。

## 质量保障闭环（铁律）

1. 改完任何 `.py` 必须重跑全部 verify 脚本再提交（当前 **54 个脚本全绿**，其中 50 个带汇总输出的脚本合计 **5443 项断言**；`verify_app.py` 109/109、`verify_cable_opt_3d.py` 119/119、`verify_cable_length_3d.py` 58/58、`verify_newton_3d.py` 52/52、`verify_report_3d.py` 149/149、`verify_view_3d.py` 69/69、`verify_envelope_3d.py` 152/152、`verify_capacity_2d.py` 271/271、`verify_capacity_axial_2d.py` 382/382、`verify_compact_dat_2d.py` 201/201、`verify_strength_2d.py` 168/168、`verify_modal_2d.py` 111/111、`verify_validate_2d.py` 83/83、`verify_report_2d.py` 150/150、`verify_modal_detail_2d.py` 133/133、`verify_b_class_spec.py` 234/234、`verify_prestress_loss_2d.py` 308/308、`verify_newton_2d.py` 251/251、`verify_length_2d.py` 267/267、`verify_opt_2d.py` 286/286、`verify_2d_export_import.py` 264/264、`verify_stage_tension_3d.py` 84/84、`verify_stage_tension_2d.py` 27/27、`verify_stage_prestress.py` 88/88、`verify_combo_system.py` 86/86、`verify_x20_contract.py` 92/92、`verify_capability_matrix.py` 286/286、`verify_curvature_3d.py` 51/51，内核侧另有 `ctest` 37/37 与 `dat_byte_regression` 37/37 黄金基线）。
2. 任何数值新功能必须先有理论解或交叉校验（anastruct / MIDAS / 黄金基线）。
3. 内核目录只在 `fem_eigen_export.py` 一处探测，其他模块一律复用。

### B 类后续项的口径文档

2D 模态 / 线性屈曲已按内核事实接通（opt-in，见 `能力边界矩阵.md` §2 备注）；
B2 的「截面应力」半部随后按 `P` 口径接入（`strength_2d.py`，承载力当时显式拒绝；第十二次另由 opt-in `RebarSpec2D` 接通 Mu / Vu 半部，见下文），
第十次由 FE opt-in 补 `[PRESTRESSLOSS2D]` 段闭环「2D 分项预应力损失
明细」后曾 **B2 剩余 2 项**；第十一次只闭环三层迭代中的内层 Newton 一层
（`[NEWTON2D]`，该格 `2D=P`），当时 `[LENGTH]` / `[OPT]` 两层 2D 内核
没有对应力学、两格仍标 `N`；第十三次首次真正新造 2D 力学、按 opt-in
接通 `[LENGTH2D]` 平面索长闭合一层（该格 `N→P`、仍 `P` 不 `Y`），
第十三次收官时 `[OPT]` 仍标 `N`、**B2 剩余 2 项**（内容曾再收窄为
「`[OPT]` 一层力学 + 多格式导出 / `.mct` 反向导入」）；**第十四次起
`[OPT2D]` 2D 平面索力优化按 opt-in 首次新造第二层力学接通**（该格
`N→P`、仍 `P` 不 `Y`），§4.2 现为 **0 个 `2D=N` + 9 个 `2D=P`**、
**B2 剩余 0 项**——第十五次 B2「2D 平行导出 / 导入链」的多格式导出 / `.mct` 反向导入已按 B 档平行链接通（两格 `N→P`、仍 `P` 不 `Y`），B2 逐次接入全部收口；
B2 已收口的这两格（`P ≠ Y` 的如实边界、见下文新增节）与组合体系（B1，用户本轮暂缓）、
B3 非线性稳定全过程、B4 预应力损失两处规范口径、B5 无应力曲率的
**现状、两阶段规格与验收断言**统一写在
`B类后续项接入规格.md`，由 `verify_b_class_spec.py` 纯离线守卫。这些项目里
B3 已于 2026-11-14 第十七次按 `STABILITY=1` opt-in 接入主链路球面弧长法
（`[STABILITY]` 段 + 解析锚点 + 第 20 项 CTest，默认全关、**仍 `P` 不 `Y`**，
边界见 `B类后续项接入规格.md` §2.5）；**2026-11-14 第十八次（N 阶段）**
另由 `frame_probe.exe` 完成 CB3D19 共转 CR 梁刚接框架后屈曲全过程
与 ANSYS `BEAM188 + ARCLEN` 的**独立探针层**对标（第 21 项 CTest、
12513 项黄金值、三套线性刚度锚点），`fem_3d_nl.hpp` /
`stability_path_3d.hpp` / `cable_solver.cpp` 零改动，故 B3 在**第十八次
收官时**继续 `P ≠ Y`、不等于成桥态闭环已具备全过程稳定分析；
**2026-11-15 第十九次（a 阶段 ①）** 新增 `STABASIS=COMPLETION` 与
`*STABILITY_LOAD`，把平衡路径起点换成成桥态收敛位移 `u0` + 恒定荷载
`F0`（`R = F0 + λ·Finc − Fint(u)`），`[STABILITY] BASIS=completion_state`
挂在主链路结果文件 `[END]` 前；第 22 项 CTest 用 V 形双索单自由度
体系的独立闭式 `λ(v)` 逐点对拍 `MODE=STATIC` / `TENSION` 两种成桥态
闭环。**B3 仍 `P` 不 `Y`**：① 只有 FEM 自洽与解析锚点，真实桥梁
ANSYS 两段对标属 ②、B3 推 `Y` 属 ③，验收表见 `B类后续项接入规格.md`
§2.6；**2026-11-15 第十九次 ②** 已交付 CB3D21 真实双塔三跨斜拉桥
与 ANSYS 两段求解逐子步对标（`ansys_bridge_completion.py` /
`cases/run_cases_completion.py` / 第 23 项 CTest，2392 项黄金值、
28 子步 `worst|ΔUZ|=1.85e-5 m`、`--live-ansys max|d|≤1e-11`），
同一份设计线形 + 16 根索 `L0` + `INISTATE eps` 同源，
`RES0` 与 `[NEWTON] RESLAST` 同量级；② 完成**仍 `P` 不 `Y`**
（路径单调硬化无极限点、`MODE=COMPLETION` 起点不同源缺陷未修、
施工阶段链路未接入，③ 才推 `Y`，见规格 §2.5 / §2.6）；
**2026-11-15 第十九次 ③a** 已修 `MODE=COMPLETION` 起点不同源缺陷
（`basisModel` 回传同源模型，CB3D10 `RES0/|F0|` 由 `1.06` 降到
`2.502e-9`，第 24 项 CTest）、核对 CB3D13 施工阶段五段与
`[STABILITY]` **并列输出互不污染**、并补 CB3D22 真实斜拉桥
**越过极值点 / 下降段**算例（FEM `λ_max=1.624941`、
下降段 207 步 vs ANSYS 两段 `ARCLEN λ_max=1.622748`，
第 25 项 CTest）；干净构建全量 `ctest` 25/25。
**③a 完成仍 `P` 不 `Y`**：§2.5 的分支屈曲 / 初始缺陷 /
材料非线性 / 施工阶段中间态稳定等边界未收口，见规格 §2.5 / §2.6；
**2026-11-15 第二十次 ③b** 按「按建议做」继续 §2.5 收口，
把施工阶段中间态从「完全未覆盖」推进为「主链路有 opt-in
研究构型算例」：`fem_3d_nl.hpp::NLStageResult3D` 回传同源
`Model3D model`，`cable_solver.cpp` 新增 `STABASIS=STAGE` +
`STAGEID=<id>` 独立 opt-in（`BASIS=stage_state`、`[STAGES]` /
`STAGE=` 留痕、未知阶段 `ERROR=stage_not_found` 不降级）；
`ansys_stage_stability.py` / `cases/run_cases_stage_state.py`
同源导出 CB3D23（4 节点 3 杆 `TRUSS` 浅拱、3 阶段取阶段 2），
`STAGE=2 NELEM=3 NDOF=12`、`RES0/|F0|=6.34e-13`、
FEM `NPOINT=134` / `λ_end=2.57577583`、两个 ANSYS job 0 error、
按 `|ΔUY|` 插值 λ `worst|Δλ|≈0.0051`；第 26 项 CTest
214 项检查。第二十次 Riks 闭环新增第 27 项
`riks_stage_stability_3d_benchmark`（63 项守卫、3109 checks）：
`PATHMETHOD=RIKS` 仅 STAGE 起点 opt-in（Wempner–Riks 正交轨迹），
闭式逐点 `4.6e-14`、双方法交叉 `1.47e-4`，与 ANSYS `ARCLEN`
不同源。干净构建 `ctest` **27/27**、
`run_tests.ps1 -BuildDir _d_build` `ALL TESTS PASSED`。
**仍 `P` 不 `Y`**：`STAGE` / `RIKS` 都是独立 opt-in、算例是研究构型
不是真实桥梁施工阶段，位移控制 / 线搜索、分支屈曲、初始缺陷、
材料非线性、动力 snap-through 仍未闭环，见规格 §2.5 / §2.6；
B4 / B5 仍**只立规格、未实现力学**；
B2 的 `[LENGTH2D]` 一层自第十三次
起、2D 平面索力优化 `[OPT2D]` 一层自第十四次起已按 opt-in 真正新造 2D
力学接通（两格都 `N→P`、仍 `P` 不 `Y`），`[OPT2D]` 只 MINMAXM + 显式
索力箱、不等于 3D `[OPT]`；未实现期 `[BUCKLING] ERROR=nonlinear_state_not_supported`
与「σl2 只按直线筋 / εcs·φ 需查表输入」等如实边界一律保留，不虚报能力。

**2026-11-16 第二十二次收口：B3 由 `P` 推 `Y`**（任务 #165）。§2.6 三阶段全部走完：①/② 成桥态起点同源 + CB3D21 真实斜拉桥 ANSYS 两段对标（第 22/23 项 CTest）、③a/③b 施工阶段中间态 opt-in（CB3D23 / 第 24—27 项 CTest）、第二十一次补齐 §2.5 五项力学（① 位移控制 / 线搜索 25/25、② 初始缺陷 35/35、③ 材料非线性 491/491、④ 分岔切换 42/42、⑤ 动力 snap-through 88/88，第 28—32 项 CTest）。干净构建全量回归 `ctest` **32/32**、`run_tests.ps1 -BuildDir _d_build` `ALL TESTS PASSED`。能力矩阵「非线性稳定全过程」3D 格由 `P` 改 `Y`，三守卫同步全绿。**`Y` 的边界**：全部能力 opt-in 默认关闭、`STABILITY=0` 既有输出逐位不变；五项力学多在 `snap_probe` / `mat_nl_probe` / `dyn_probe` 独立探针层、不进 `cable_solver` 默认求解链；与 ANSYS 瞬态 / 动力不同源，λ 不是 JTG 稳定安全系数；B4 / B5、四出口内容补齐、MIDAS 数值对拍、B2 各格 `P ≠ Y` 均不受影响。逐项实测见 `B类后续项接入规格.md` §6 第二十二次记录。

**2026-11-16 第二十三次：B4 σl2 附录 G 反摩擦 opt-in 收口（任务 #167–#170）**。用户「同意建议的顺序」后按 σl2 → σl6 → B5 推进，本轮仅 σl2：FE 内核 `prestress_loss.hpp` 增 opt-in `L2ANTI` / `L2REPX`（默认 `0/0`），`L2ANTI=1` 按附录 G、`x=0` 张拉端峰值代表值计算（`lf≤l` / `lf>l` 两支闭式，缺参与非 x=0 代表值显式拒绝）；2D 明细段仅在启用时追加 `LF=` / `DSL2D=` / `DSL2PEAK=` / `BRANCH=`；BS 侧 22 键扩到 24 键且默认两键不写、12 格 `.dat` 哈希不变。新增第 33 项 CTest `prestress_loss_anti_2d_benchmark`（117/117 checks：独立闭式 oracle、两支交叉 / `lf=l` 连续、两个负例、默认逐位零污染、2D/3D 同参同值；3D 只读探针 `loss_probe_3d.exe` 不自带力学）。守卫 `verify_prestress_loss_2d.py` 219/219、`verify_b_class_spec.py` 211/211。**边界**：只给代表截面峰值、不输出 `σl2(x)` 沿程分布；σl6 仍需用户录入规范表值、不按龄期自动发展；不跑 ANSYS、B4 整项与能力矩阵均不推 `Y`。`lf>l` 分支 σl5 可能为负（JTG 6.2.6-1 数学行为）已如实登记。上文「σl2 只按直线筋」是历史边界文字，保留不删；默认路径仍逐位不变。

**2026-11-16 第二十三次：B4 σl6 附录 C 显式表值 opt-in 收口（任务 #172–#176）**。用户「同意建议的顺序」后按 σl2 → σl6 → B5 推进，本轮仅 σl6，沿用 opt-in 默认零污染：FE 内核 `prestress_loss.hpp` 增 opt-in `C2D` / `C_SKIP`（默认 `0/0`）与 7 个附录 C 表值键 `EPS0` / `PHI0` / `C_RH` / `C_H` / `C_T0` / `C_TS` / `C_T`；默认 ECS/PHI 直给路径逐位不变，`C_SKIP=1` 显式不参与（`C_SOURCE=2`），`C2D=1` 按表 C.1.2 / C.2.2 用户核对表值 + 解析发展公式（βs/βH/βc，RH 取区间代表值 55/80）算 εcs/φ，**不查表、不调用 `code_loads.hpp` 拟合值兜底**，缺任一表值 / RH 越界 / 龄期倒挂 / 两开关冲突均显式拒绝。2D 明细段与 3D `loss_probe_3d.exe` 仅在启用时追加 `C_SOURCE=` / `ECS_DEV=` / `PHI_DEV=` / `BETAS=` / `BETAS0=` / `BETAC=` / `BETAH=` / `RHREP=`。新增第 34 项 CTest `prestress_loss_creep_2d_benchmark`（142/142 checks：独立闭式 oracle、βH 两区间切换、φ=φ0·βc / εcs=εcs0(βs−βs0) 交叉、四负例、默认零污染、C_SKIP 区分、2D/3D 同参同值；σl6≈45.4MPa 与规范算例书吻合）。**边界**：表值由用户录入、程序不查表不拟合；只给代表截面标量、不输出沿程分布；不跑 ANSYS、B4 整项与能力矩阵均不推 `Y`。上文「σl6 需用户录入、不按龄期自动发展」是 σl2 阶段历史边界文字，保留不删；默认路径仍逐位不变。

### 2D 截面应力验算的 P 口径（2026-11-10）

`能力边界矩阵.md` §2 的「截面应力 / 承载力验算」2D 格从 `N` 改成 **`P`**：
只接通**应力**半部。`strength_2d.py::evaluate` 的效应侧只取
`Solve2DResult.final_beam` 经 `combination.beam_combinations` 的三种组合内力，
**不解析内核「单元应力 (Pa)」段**（那是同一批内力经 WT/WB 换算的第二套 σ，
且施工阶段结果文件没有该段），按 N 单值 × My/Vz 两侧 × 三种组合共 12 个角点
枚举取利用率最大者，交 `section_strength.check` / `result_table` 出表。
`ElemTypeDef.AW` / `t_plate` 与 `MaterialDef.grade` / `kind` 是 opt-in
Python-only 契约，不写进 `.dat`；缺任一项整章 `refused` 并逐条列原因
（缺 AW 让 τ 静默取 0、缺板厚让 `steel(grade, 0)` 落最薄板档偏不安全）。
承载力 Mu / Vu 见下一节：第十二次按 opt-in 的 `RebarSpec2D` 配筋契约接通；
未请求或缺配筋时整章拒绝，不拿应力利用率冒充承载力。
默认关闭（`strength_2d=False`），默认 `.dat` / 默认 HTML 逐位不变；
`verify_strength_2d.py` 168/168 含混凝土 / 钢闭式解 oracle 与缺参拒绝负例。

### 2D 承载力 Mu / Vu 的 opt-in 半部（2026-11-11，第十二次）

纯 BridgeStudio 侧闭环，**不改 FE 内核、不改 `to_dat()`**。新增
`bridge_spec.py::RebarSpec2D`（挂在 `ElemTypeDef.rebar`，Python-only，
几何与配筋全部必填）与 `capacity_2d.py`（唯一口径层）：

- **只走 flex 链**：正截面抗弯 Mu（JTG 3362-2018 §5.2.2 / §5.2.3）+
  斜截面抗剪 Vu（§5.2.9，仅配箍筋）；My 正 / 负两面各算 Mu、缺筋 `util=∞`。
  本章不设 3D 的 5% / 1% 分派阈值（`BeamCombo.rows` 的 N 按历史口径只有
  恒载单值）；压弯 / 轴心受压由**2026-11-12 第十六次**独立 opt-in 章
  `capacity_axial_2d.py` 接通（直接消费内核活载轴力包络 `NMin/NMax`），
  两章不共用效应源、不互相覆盖。
- **效应只取 `ultimate` 基本组合的逐端最不利** M_pos / M_neg / Vd，
  与 3D `result_3d._capacity_cases` 同源；不用 2D 应力的 12 角点相乘。
  `γ0=1.0 / α1=1.0 / α3=1.1` 与 3D 分派同源硬编码，不猜参数。
- **公式与三张表唯一来源** `section_strength.check_capacity` /
  `capacity_table` / `capacity_faces_table` / `capacity_calculation_sheet`，
  不重写规范公式；缺配筋 / 缺混凝土 grade 整章 `refused`，不补零、不编配筋。
- 链路 `solve_2d(capacity=)` → `ModelDoc.solve(capacity_2d=)` → 界面
  `capacity2d_opt` + 显式配筋 JSON → HTML 与四出口同源章，双层 opt-in
  默认全关；`strength_2d.capacity_note_for()` 让应力章按 `res.capacity`
  状态挑措辞，不与新章自相矛盾。`verify_capacity_2d.py` 独立守卫。

能力矩阵「截面应力 / 承载力验算」2D **仍标 `P`、不标 `Y`**：2D 逐分量
独立极值不等于同一工况的联合最不利 (N, M)，预应力、受扭、Mz / Vy 与
3D 全套承载力仍未覆盖；§4.2 计数与
**B2 剩余 2 项**当时均不变；第十三次 `[LENGTH2D]` 一层按 opt-in 首次真正
新造 2D 力学接通后，第十三次收官时 §4.2 为 **1 个 `2D=N` + 6 个 `2D=P`**、
**B2 剩余 2 项**；**第十四次起 `[OPT2D]` 2D 平面索力优化按 opt-in 新造
接通**（该格 `N→P`、仍 `P` 不 `Y`），**2026-11-12 第十五次**起 B2「2D 平行导出 / 导入链」两格按 B 档平行链接通（`2D=N` → `2D=P`、**仍 `P` 不 `Y`**），§4.2 现为 **0 个 `2D=N` + 9 个 `2D=P`**、**B2 剩余 0 项**（第十五次两格按 `P` 接通，仍 `P ≠ Y`；B2 逐次接入全部收口）。

### 2D 平面索长闭合 `[LENGTH2D]`（2026-11-11，第十三次）

B2 里**首次真正新造 2D 力学**：此前 2D 连索长输入契约都没有。用户拍板只做
`[LENGTH2D]` **一层**（无垂度线性杆本构 `N=EA·((L-Lu)/Lu+istrn)`、闭式
初值 `Lu0=L*/(1+T*/EA)`、中心有限差分 Jacobian + LM 阻尼最小二乘、唯一
判据 `max|N/T*-1| <= TOLT` 即 `ERRKIND=tension_rel`），`[OPT]` 带约束目标
索力优化**仍不做、仍标 `N`、不造占位**。FE 侧新增 `*CABLE_LENGTH2D`
段（`LENGTH2D=1 TOLT= MAXLENITER=` + 逐行 `<CABLE单元号> <T*_N>`）、
`Model2D::Element::Lu` 独立无应力下料长度（`Lu<=0` 时本构逐位不变）、
`fem_2d.cpp::iterateCableLength2D()` 与
`cable_length_output_2d.hpp::formatCableLengthSection2D()`；
`example_mixed.exe --length-detail[-only]` 写 `<stem>.length.txt` 的
`[LENGTH2D]` 段（`--length-detail-only` 求解后返回、不写 `.result`），
无契约写 `KIND=none STATUS=not_applicable` + `ERROR=no_length_request`
不摆空表；`[LENGTH2D]` 段按 2D 事实取舍，不写 sag / Ernst / SENS /
NJ / J / geomErr / STAGE 占位。BS 侧 `length_2d.py` 唯一口径层只解析、
不重算、不补零（三张表），`bridge_spec.py::LengthSpec2D` +
`BridgeSpec.length_2d` 默认 None、`to_dat()` 一个字节不多写、
12 格 `DEFAULT_DAT_SHA` 不变；`solve_2d(length_detail=)` /
`Solve2DResult.length` / `length_tables()` /
`ModelDoc.solve(length_2d_detail=)` / `capability()["length2d"]` /
界面 `length2ddetail_opt` + `length2d_cfg` 双层 opt-in 默认全关，
`export_html.py` 新章、`report_2d.py` 新章 ⑩（诊断顺延 ⑪）。
闭式解 oracle：`verify_length_2d.dat` 两端固定单根 CABLE + 非零 `ISTRN`
使闭式初值不直接命中，真实迭代 3 轮收敛到 `3.83e-13`、
`Lu=9.952229299 m = 10/(1+T*/EA−istrn)`、`N=T*=1e6 N`；不进
`compare_regression.py` 的 `CASES`、不刷新基线。`ctest` 7/7（含
`dat_byte_regression` 37/37），默认不带开关时 `.result` / 控制台 /
37 个黄金基线逐位不变。该格 `2D=N` → `2D=P`、**仍 `P` 不 `Y`**（opt-in
默认全关，不等于 3D 的垂度 / Ernst / 共转成桥态 / 闭式影响矩阵 /
`[OPT]`→`[LENGTH]`→`[NEWTON]` 三层迭代），`[OPT]` 仍 N，§4.2 变
**1 个 `2D=N` + 6 个 `2D=P`**，**B2 剩余仍 2 项**。

### 2D 平面索力优化 `[OPT2D]`（2026-11-12，第十四次）

B2 里**首次新造第二层力学**（FE `631c1d1`、BS `222b7d3`）：2D 平面索力
优化按 opt-in 接通。用户拍板只做 `MINMAXM`——最小化**用户显式登记的
BEAM 单元端弯矩集合**的 `max|M|`，内核不自动识别「主梁」、不补零；约束
只有**显式索力箱** `TMIN` / `TMAX`（必填、`0<TMIN<TMAX`、
`BND_SRC=USER`，缺一写 `ERROR=missing_tension_bounds`），**绝不猜 3D 的
`1.5×RIGID`**；不做 `MINENERGY`（2D 无裸结构弯曲应变能柔度 `Fm` /
影响矩阵），2D 无 DECKLOAD / 塔顶 `ΣT·cos` / SYSGUARD 三判据、不造占位。
变量是无量纲名义索力 `x=T/Tscale`，经
`Lu(x)=Lgeom/(1+Tscale·x/EA−istrn)` 落到第十三次无垂度线性杆本构，
**中心有限差分 Jacobian + IRLS(p=2/4/8/16) + 投影梯度**求解——
**不是 3D 的 `J=M·D` 闭式影响矩阵**，也**不等于 3D
`[OPT]`→`[LENGTH]`→`[NEWTON]` 三层迭代**。`APPLIED=1` 只表示建议
`T*` 被采纳，命中 `T*` 属 `[LENGTH2D]` 职责；`[OPT2D]` 只定目标、
**不写回 `.dat` / `.result`**，`T` 行只是过程量不能当设计索力；峰值不降写
`objective_not_improved` / `APPLIED=0`；`Nfinal ≠ T*`（结构柔性使
`Lu(T)` 闭式只是近似）。

FE 侧新增 `*CABLE_OPT2D` 契约（`OPT2D=1 TMIN= TMAX= TOL= MAXITER= IRLS=
ROUNDTOL=` 参数行 + 逐索 `T0` 行 + 逐个 `M` 登记行）、
`fem_2d.cpp::iterateCableOpt2D()`、`<stem>.opt.txt` 的 `[OPT2D]` 段
（`I` / `T` / `M` 行与 `STATUS` / `CONVERGED` / `CONVBY` / `APPLIED` /
`REJECT`），`example_mixed.exe --opt-detail` / `--opt-detail-only`；无契约写
`ERROR=no_opt_request` 不摆空表。BS 侧 `opt_2d.py` 唯一口径层（14 态 +
4 张表 `opt_2d_summary_table` / `opt_2d_iter_table` / `opt_2d_cable_table` /
`opt_2d_moment_table`）、`OptSpec2D` + `BridgeSpec.opt_2d`、
`solve_2d(opt_detail=)` ⑧ 分支、`ModelDoc.solve(opt_2d_detail=)`、
`capability()["opt2d"]`、界面开关 `opt2ddetail_opt` / `opt2d_cfg`、
`export_html.py` 的 `opt_html`、`report_2d.py` 章 ⑪（诊断顺延 ⑫），
双层 opt-in 默认全关。两个 oracle：正例 `verify_opt_2d.dat`
`T*=TMIN=200000`、`MPEAK0=1304347.826`、`MPEAK=260869.5652`、
`APPLIED=1`、`CONVERGED=1`；负例 `verify_opt_2d_negative.dat`
`objective_not_improved` / `APPLIED=0`。编译 rc=0、`ctest` 7/7
（37/37 黄金基线未刷新）、三条零污染证据通过；默认 `.dat` / 默认
`.result` / 默认 HTML 逐位不变，12 格 `DEFAULT_DAT_SHA` 与 37 个黄金基线
都不刷新。该格 `2D=N` → `2D=P`、**仍 `P` 不 `Y`**：2D 版缺 SYSGUARD /
MINENERGY / 影响矩阵 / 垂度 / Ernst / 共转成桥态 / 三维制造线形，
**不等于 3D `[OPT]`**；**2026-11-12 第十五次**起 B2「2D 平行导出 / 导入链」两格按 B 档平行链接通（`2D=N` → `2D=P`、**仍 `P` 不 `Y`**），§4.2 现为 **0 个 `2D=N` + 9 个 `2D=P`**、**B2 剩余 0 项**（第十五次两格按 `P` 接通，仍 `P ≠ Y`；B2 逐次接入全部收口）。

### 2D 平行导出 / 导入链（2026-11-12，第十五次）

B2 最后两格（ANSYS / Abaqus / MIDAS 多格式导出、`.mct` 反向导入）按
`X20_接入约定.md` §3.4 的 **B 档平行链**接通（§8「先登记 §7 再实现」新增
资产 `B5`，`ASSET_REGISTRY` 由 11 项变 12 项、§1–§9 九章结构不变），
两格 `2D=N` → `2D=P`、**仍 `P ≠ Y`**，B2 剩余 0 项。两条链**正交、互不冒充**：

- **契约形态**：新建 `export_2d_formats.py` 定义 `plane_frame_2d/v1`
  **平行 2D 中间表示**（**不叫 canonical dict**）；3D 的
  `export_formats.to_canonical(model: ModelSpec3D)` 与三个写出器签名
  **一字不动**、3D 唯一转换点不经过。
- **接口 / CLI**：导出 `to_plane` → `write_ansys_2d` / `write_abaqus_2d` /
  `write_midas_2d` / `export_2d_all`（`.ans` / `.inp` / `.mct`）；导入
  `read_mct_2d` / `read_mct_2d_text` / `PlaneMctImport` 读回 `BridgeSpec`。
  只有模块 API + CLI，**无 GUI 入口**（界面按钮仍只走 3D）。
- **维度语义**：显式 `dimension="2D"`，只有 x/y、平面 3 dof `UX/UY/ROTZ`、
  单一惯矩 `I`；**不写 z / 6dof / `IYY`·`IZZ`·`IXX`，缺项标「不适用」
  而不是补 0**。写 `.mct` 时 z=0（平面几何事实）与平面外 dof 固定（格式要求）
  逐条进 `warnings`；导入只接受 z 全 0 且平面外约束一致的平面模型，否则
  `errors` 写 `dimension_not_plane` 且 `spec=None`，**绝不静默投影丢信息**。
- **有损项只登记不还原**：车道 / 影响线 / 成桥态 / 施工阶段 / 自重 /
  预应力 / β 角 / DBUSER / 分布荷载一律进 `warnings`、不补 0、不猜值。
- **仍 `P ≠ Y`**：2D 版格式覆盖窄于 3D、无 GUI 入口、MIDAS 数值对拍仍未做
  （本机无 MIDAS）；3D `.mct` 反向导入是 `mct_io.MctImport` 的完整双向互导，
  2D 只是平面专用平行链，不等于 3D。
- **默认零污染 / FE 仓零改动**：本轮不改 `export_formats.py` / `mct_io.py` /
  `bridge_spec.py` / `app.py` / `solve_2d.py` / `export_html.py`，默认 `.dat` /
  默认 `.result` / 默认 HTML 逐位不变，12 格 `DEFAULT_DAT_SHA` 与 FE 37 个
  黄金基线不刷新；B1 仍暂缓、B3 / B4 / B5 仍只立规格、MIDAS 数值对拍未闭环。
  FE 仓本轮零改动、零文档改动，BS 侧闭环不等于 FE 内核已实现 3D 等价能力。
  独立守卫 `verify_2d_export_import.py`（264/264，覆盖契约正交 / 三格式写出 / .mct 往返 oracle / 维度拒绝 / 默认零污染 / 两仓文档对拍）。

> **2026-11-17 补齐（建模后 APDL 导出入口）**：上面“无 GUI 入口，界面按钮仍只走
> 3D”是第十五次当时的历史事实，现已改变——三维侧 `ui_3d.py::_export_block`
> 提升为全桥型通用出口（梁格 / 桁架 / 拱桥 / 斜拉 / 悬索建模求解后都能导出 ANSYS
> `.ans` APDL / Abaqus / MIDAS），且导出块已前移到求解结果顶部（指标卡之前），
> 不再藏在报告与 `.dat` 原文之后。二维侧 `app.py` 的导出按钮前移到 ③ 模型区
> 最上方（模型摘要 / JSON 结构文件之前）：`BridgeSpec` 桥型调 `export_2d_all`
> 出三格式；`ModelSpec` 单梁调新增 `export_beam_apdl.py::export_beam_apdl`
> 出 `.ans`（与 FEM-Eigen `.dat` 共用同一网格与截面口径，只建模不含求解控制）。
> 三条链按输入契约正交、互不冒充。`2D=P` 口径不变（格式覆盖窄、有损项只登记、
> MIDAS 数值对拍未做），只补界面入口、不改任何默认 `.dat`。界面守卫
> `verify_app.py` 121/121。

> **2026-11-18 补齐（APDL 只读导入，用户选 a+b）**：此前只有「导出 APDL」与
> 「读 ANSYS CSV 结果」，不能把 APDL 命令流读回来。新增 `apdl_io.py`，按
> **a. 几何拓扑 + b. 荷载边界**只读导入：`N`/`FILL`/`E`/`EN`/`ET`/`MP`/`R`/
> `RMORE`/`SECDATA`/`TYPE`/`MAT`/`REAL` + `D`/`F`/`SFBEAM`/`SFE`/`ACE`，
> 统一输出 `ModelSpec3D` → FEM-Eigen `.dat` 试算。入口在 `ui_3d.py` 互导区，
> 与 MIDAS `.mct` 面板并列，导入后展示「已读入 / 已忽略 / 显式拒绝 + 告警」
> 三段报告，**不覆盖当前 doc**。三条硬边界：`BEAM3`、两坐标 `N`、建模段
> `*DO` 与未知命令显式拒绝（不产出半吊子模型）；`BEAM188→BEAM`、
> `LINK180→TRUSS`、面荷载方向假定逐条告警；`/SOLU` 之后的求解控制忽略。
> 默认 12 格哈希与既有黄金基线一个不刷新，四个导出器一字未动。规格见
> `APDL导入规格.md`，守卫 `verify_apdl_import.py` 63/63、`verify_app.py` 125/125。

### 2D 压弯 / 轴压半部（2026-11-12，第十六次）

B2 收官后补齐第十二次留下的承载力另一半，**纯 BridgeStudio 侧、FE 仓
零改动、零文档改动**。用户四项拍板全选推荐，并在动手前先做三路只读调研：

- **一条调研纠偏**：`combination.py` 旧注释「内核的 live beam envelope 段
  只输出 My/Vz 包络，没有活载轴力」与两仓源码矛盾。FE
  `defaultEnvelopeSpecs()` 已为每单元每端登记 `FORCE/N` 并写出
  `E<id>_N1/N2`（`fem_2d.cpp:4457`），BS `_parse_lane_envelope()` 已解析成
  `NMin/NMax`（`solve_2d.py:362-363`）；只是 `beam_combinations()` 按历史
  口径取 `q.get("N", 0.0)`（键名不匹配，恒取 0），组合 N 才退化成恒载
  单值。第十二次据此拒绝压弯 / 轴压，属**过时注释造成的误判**。
- **不修组合层**：那是 `strength_2d` 12 角点应力的效应源，改它就等于改
  默认计算书数值。新建 opt-in 独立口径层 `capacity_axial_2d.py`，直接读
  `final_beam` + `live_beam`，按 `combination.ULTIMATE` 系数在**同一
  (单元, 端)** 配对并枚举 2×2 角点；不写回 `BeamCombo.rows`、不跨端、
  不跨分量。**逐分量独立极值不冒充同一车辆位置的联合最不利 (N, M)**。
- **三链分派与 3D 同源**：`Nc = max(-N_comb, 0)`、`n_axial = fcd·b·h`，
  `Nc > 5%·n_axial` 才进本章，再按 `e0 = Md/Nc < 1%·截面高` 分轴心受压
  （§5.3.1）/ 偏心受压（§5.3.4）；不显著的角点归 flex 链、本章不出表。
  公式与表唯一来源 `section_strength.check_eccentric` / `check_axial` /
  `eccentric_table` / `axial_table` / `capacity_calculation_sheet`，
  不重写规范公式、不重算 `K·ΔU=R`。
- **不静默兜底**：`RebarSpec2D.l0`（mm）必填，缺失整章 `refused`、不静默
  取短柱 `φ=1` / `η=1`；内核没有活载轴力包络整章 `no_axial_live`，不拿
  恒载单值冒充完整判定。状态非 `ok` 一律 `tables() == {}`。
- **接线与同源**：`solve_2d(axial_capacity=)` →
  `ModelDoc.solve(axial_capacity_2d=)` → 界面 `axialcapacity2d_opt`（复用
  同一份配筋 JSON）→ `res.axial_capacity_tables()` 唯一取数口 → HTML 与四
  出口 ⑨′ 章（不重排后续章号）。双层 opt-in 默认全关，默认 `.dat` /
  `.result` / HTML 逐位不变，12 格 `DEFAULT_DAT_SHA` 与 FE 37 个黄金基线
  不刷新。独立守卫 `verify_capacity_axial_2d.py`，含纠偏证据、闭式 oracle、
  13 类拒绝负例、默认零污染、四出口同源与两仓文档对拍。
- **仍 `P ≠ Y`**：矩阵该格 2D 仍 `P`、§4.2 仍 **0 个 `2D=N` + 9 个
  `2D=P`**、**B2 剩余 0 项**；2D 逐分量包络近似、无 GUI 入口、不等于 3D
  全套承载力。B1 仍暂缓、B3 / B4 / B5 仍只立规格、MIDAS 数值对拍未闭环。

### 2D `.dat` 紧凑写法 `*FILL` / `*DO`（2026-11-12，第十七次）

用户指出「节点和单元有规律增加时，X20 里的 `*FILL` / `*DO` 语法在 2D 出口没有保留」。
调研结论：**3D 主链一直在**（`ModelSpec3D.to_dat(compact=True)` 默认开），FE 2D 内核
`fem_2d.hpp::expandLines` 也一直能读回；真正缺的是 `BridgeSpec.to_dat()` 主链与
`fem_eigen_export.spec_to_dat` 等旁路。本轮按四项拍板（节点 `*FILL` + 单元 `*DO`、
严格可逆自检、求解与展示统一、只做 `BridgeSpec` 主链）补上 **opt-in 默认关闭**的
2D 紧凑导出。

- **独立 2D 压缩器**：`compact_dat_2d.py` 不 import 3D 压缩器 / `export_formats` /
  `to_canonical`（`X20_接入约定.md` §3.4 正交约定、§7 新增 `B6`）。2D 只有 x/y 且写
  `%.6f`；2D 内核 `substituteLoopVar` 只认 `VAR` / `k*VAR` / `VAR±m` / `k*VAR±m`，
  不支持括号、除法与嵌套循环。
- **严格可逆性自检（本轮新发现）**：内核 `*FILL` 用 `setprecision(15)` 线性插值后重新写出
  中间点，逐行版却是 `%.6f`；`0→10 m` 分 3 段时插值给 `3.3333333333333335`、逐行版是
  `3.333333`，差 3.3e-7 m，属**真实几何漂移**而非格式差异。因此每个候选宏都先用
  参考展开器 `expand_lines()`（按 `fem_2d.hpp` 语义）展开，节点字典 / 单元列表必须与
  逐行版逐字段相同才允许压；不合格整段退回逐行，绝不静默近似。`arch` 拱肋曲线点、
  `0→10` 分 3 段都属于「压不动」。
- **默认零污染**：`BridgeSpec.to_dat(compact=False)` → `ModelDoc.to_dat(compact_2d=)`
  / `solve(compact_2d_dat=)` → `solve_2d(compact_dat=)` → 界面 `compact2ddat_opt`
  与结果区 `.dat` 下载。3D 分支保持 `to_dat()` 原样调用（原 `compact=True` 不受影响），
  单梁 `spec_to_dat` 本轮**不覆盖**。默认 `.dat` / `.result` / 默认 HTML 逐位不变，
  12 格 `DEFAULT_DAT_SHA` 与 FE 37 个黄金基线不刷新。
- **真跑内核对拍**：同一模型逐行版 / 紧凑版分别交 `example_mixed.exe`，`.result`
  逐字节相同，`final_beam` / `final_disp` 完全相同；5 个 2D 桥型主链实测
  `beam_bridge` 121→47 行、`suspension_2d` 413→226 行，全部模型级等价。紧凑开关
  会在 `diagnostics` 如实登记口径（四出口同源），`res.fingerprint` 按写盘文本计算，
  两版不同属预期。
- **如实边界**：这只是**文本层压缩、不是新力学能力**，不改索长、共转、垂度、Ernst
  或任何判定；能力矩阵仍按原口径、`P ≠ Y`，B2 剩余 0 项不变。独立守卫
  `verify_compact_dat_2d.py`。

### 2D 引用完整性校验（`P` 口径）

`bridge_spec.py::validate_model` 早有中文引用校验，能力矩阵这一格原记
`2D=N` 属文档漂移。2026-11-11 按 3D 覆盖面补齐材料号引用、重复材料 / 阶段
id、施工阶段 ACTIVE / DEACTIVE / 荷载 / 单元荷载 / 预应力引用、车道节点引用、
dof 与边界自由度范围、零长度单元；仍是单一中文 error 列表，没有 3D 的
warnings 两级，`beam` 走旧 `ModelSpec` 契约，故标 `P` 不标 `Y`。
`verify_validate_2d.py` 守卫逐类断引用负例、合法模型不误报、12 格默认
`.dat` 黄金哈希与三入口同源；校验只读模型，默认 `.dat` 逐位不变。

### 2D 同源计算书四出口（`P` 口径）

`report_2d.py::build_2d_document(res, spec)` 把同一份 `Solve2DResult`
装成 `report_3d.Report`，`write_2d_all` 原样复用 3D 的四个写出器
（**没有第二个渲染器**），PDF 由 HTML 打印并补页码 / 章节书签。数值与
`write_2d_html` 逐格同源（N→kN 除 1e3、位移 m→mm 乘 1e3），不重算力学、
不解析内核「单元应力 (Pa)」段、不补零。CLI `--four-exports` 与界面
`report2d_opt` 都是 **opt-in 默认关闭**；PDF 失败抛 `PDFExportError` 并
只降级这一个出口，中文告警后保留另外三份，绝不生成占位 PDF。
`verify_report_2d.py` 守卫四出口落盘、CSV / DOCX / PDF 读回、页码书签
对拍、缺数据不补零、opt-in 章条件出现、12 格默认 `.dat` 黄金哈希与
默认 HTML 逐字节不变。**能力矩阵仍标 `P` 不标 `Y`**：2D 计算书没有
figures、没有振型 / 参与质量 / 承载力 Mu、Vu / 结构云图，默认 2D 出口
仍是单份 `write_2d_html`；导出机制对齐不等于 2D 力学能力对齐。

### 回归守卫自身的「假绿」修复（2026-11-10）

`verify_envelope_3d.py` 的汇总曾写成 `sum(1 for ok, _, _ in PASS if ok)`，
而 `PASS` 元组是 `(名称, 布尔, 细节)` —— 非空名称恒为真，2 条真实失败长期
被报成「152/152 通过」。本轮改为按布尔位统计，并把这 2 条断言按实际口径
修正（峰值表「控制 |·|」按绝对值选取但**保留 min/max 原符号**；云图色标
上限与该值的绝对值对拍），`envelope_3d.py` 表注补上这条口径。
`verify_b_class_spec.py` 加源码级断言锁住元组顺序，防止再退化。

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

### 第二十七次：2D 阶段有效预加力段 + B4 收口推 Y（2026-11-17，任务 #196–#200）

B4 收口的最后一项可补缺口：3D 早有 `stage prestress force` 段，2D 一直没有。
力学上 2D 一直在算 `Pe = ps.P * loss`（`processPrestress`，`loss` 已含 `LOSSREF`
解析出的 `σpe/σcon`，并经 `cumulativeLoads` 跨阶段累积），缺的只是**结果段**。

- **FE**：`PrestressStageForce2D{id, P, loss, Pe, sigmaCon}` 与 `Pe` 同一次计算填一次
  （不重算）；`example_stage.exe --stage-prestress-force` 写独立
  `<stem>.stage.force.txt`（`[PRESTRESSFORCE2D]` / `BASIS=2d_stage` /
  `STAGE<n> P<id> SIGMA_CON= P= LOSS= PE=`），默认不写。
- **BS**：`prestress_loss_2d.py` 增 `parse_stage_force_txt` / `stage_force_table` /
  `force_status_text`（只解析、不重算、PE≠P·LOSS 显式拒绝）；
  `solve_2d(stage_force_detail=)`、`report_2d` 新增「2D 阶段有效预加力明细」章、
  `model_doc.solve(stage_force_2d_detail=)` 透传；两个阶段开关互不依赖。
- **证据**：第 36 项 CTest 由 167 扩至 **269 checks**（force 段冻结证据、独立 oracle、
  多束多阶段编号、软负例、默认不写、FE 契约）；`verify_prestress_loss_2d.py`
  新增 `force_contract()`；`verify_capability_matrix.py` 286/286。
- **能力矩阵**：该格由 `2D=P` 推 `2D=Y`，并把 `Y` 口径**定稿为「平面内完整」**
  （六项公式同源 + 非阶段/阶段明细段 + 阶段维度 + 阶段有效预加力 + 独立闭式锚点）。
  `Y` **不冒充三向空间量**；真实制造参数与分项损失 ANSYS 对拍 2D/3D 同样没有。

### 第二十六次：B5 成桥态无应力曲率 FEM 自洽（2026-11-17，任务 #188–#194）

用户：「先做 b5 然后一起收口」。按此前拍板「先做 FEM 自洽深度，**不接真实
制造参数、不做 ANSYS 对拍**」，本轮只验证「给一个 κ0，FEM 是否自洽」。

- FE：`Element3D` 增 `k0y`/`k0z`（1/m，默认 0），只在 `*EN` 单元行接受
  `K0Y=`/`K0Z=`；非 `BEAM` 或值非有限 → 显式 `throw`。
  `elementEquivalentLoad3D()` 追加固端力 `f_eq(θ₁)=−EI·κ0`、`f_eq(θ₂)=+EI·κ0`
  （局部，`Tᵀ` 转全局）。**该函数同时供组装 `F0` 与端内力 `fl−T·fe`**，
  故一处接入两端自洽：`K·u = f_ext + f_eq`、`M = EI(κ−κ0)`；曲率模态用
  `Eeff` 与刚度同源，零外荷载 `K·u0 = f_eq` 精确成立；`κ0=0` 逐位不变。
- 输出：`example_3d_report.exe --curvature-detail` 写独立
  `<stem>.curvature.txt`（`[UNSTRESSED_CURVATURE]`），默认不写。
- BS：`curvature_3d.py` 唯一解析口径层（`parse_curvature_txt` /
  `curvature_table` / `status_text`），**只解析不重算**、`M0=EI·κ0` 只作同批
  数字互核。**本轮 BS 侧不进 3D 计算书**（`example_3d_grillage` 走 summary、
  `solver_3d` 注册表改造面大），已在规格 §4.1 如实登记。
- 证据：FEM-Eigen 第 37 项 CTest `unstressed_curvature_3d_benchmark`
  （111/111 checks，9 份 `.dat`、13 份冻结证据、独立闭式 oracle 自检
  1.35e-10、两个硬负例、自平衡 / 刚度不变 / J 不变强断言、默认逐位零污染）；
  `verify_curvature_3d.py` 51/51。
- 边界：未覆盖共转列式 `CRElemData` 初曲率、真实制造参数、成桥态 `My`
  全桥 ANSYS 对拍、`χφ` 与 κ0 耦合；不得把「节点预抬高 / 制造线形 `Xm`」
  写成「无应力曲率已实现」。

### 第二十五次：2D 分项预应力损失阶段维度（2026-11-17，任务 #182–#187）

用户：「既然3D 侧早已是 Y，补 2D 阶段维度：让 `[PRESTRESSLOSS2D]` 支持
STAGE、把 σl2/σl6 的选项映射到 2D」。本轮**只补 2D 缺的阶段维度**，
**能力矩阵该格仍标 `2D=P`，B4 整项与 B4/B5 收口均未宣称完成**。

- FE：`*STAGE_PRESTRESS` 支持 `LOSSREF=<表号>`（与 `LOSS` 二选一），σl2
  `L2ANTI/L2REPX` 与 σl6 `C2D/C_SKIP/EPS0/PHI0/C_RH/C_H/C_T0/C_TS/C_T`
  经同一张 `*PRESTRESS_LOSS` 表自动映射；`example_stage.exe
  --stage-loss-detail` 写独立 `<stem>.stage.loss.txt`（`BASIS=2d_stage`、
  行首 `STAGE<n> P<id>`），与旧 `<stem>.loss.txt`（`BASIS=2d_global`）
  严格隔离，默认不写、冻结证据逐位不变。
- BS：`StagePrestressDef.loss_ref`（默认 0 → 写 `LOSS=`，历史 `.dat` 逐位
  不变）+ 引用完整性校验；`prestress_loss_2d.py::parse_stage_loss_txt`
  以 `(阶段号, 束号)` 为键、只解析不重算；`solve_2d(stage_loss_detail=...)`
  与 `ModelDoc.solve(stage_loss_2d_detail=...)` 两个 opt-in 开关；报告挂
  `prestress_stage_loss_table`。旧 `parse_loss_txt()` 一字未改。
- 证据：FEM-Eigen 第 36 项 CTest `stage_loss_2d_benchmark`（269/269 checks，
  独立闭式 oracle + 冻结证据 + 硬/软负例 + 默认逐位零污染 + 2D/3D 同参同值）；
  `verify_prestress_loss_2d.py` 扩到 269/269。


### 第二十六次：索鞍顶推 CE 耦合方程（2026-11-18，口径 C + 全链路）

用户：「索鞍顶推用 ce 耦合方程」，并确认 `c，c`（口径 C + 范围 c 全链路）。
本轮在三维悬索桥上实现**主缆端相对塔顶纵向滑移 Δ** 的索鞍顶推工况，
默认 `saddle_jack_dx=0` 完全走原路径，既有冻结证据逐位不变。

- **口径 C（每塔 6 条 CE）**：新建塔顶结构节点 T（连塔身 BEAM）与索鞍孤立
  节点 S，原塔顶节点保留为主缆端 C（仍连主缆）。CE1 `UX(S)−UX(T)=Δ`
  （顶推）、CE2 `UX(C)−UX(S)=0`（缆鞍固结）、CE3~6 UY/UZ 固结（传力+给
  孤立节点刚度）。净效果 `UX(C)−UX(T)=Δ`。
- **自然语言**：`索鞍顶推 50mm / 顶推量 5cm / 0.05m` → `saddle_jack_dx=0.05`；
  仅写「顶推施工」不触发（须带「索鞍」）。
- **内核**：`fem_3d.hpp` 线性 CE 装配、`fem_3d_nl.hpp` 非线性 CE 装配
  （常数项只进内力侧，避免顶推量翻倍的 2b 缺陷）、`cable_solver.cpp`
  找形"塔位必须有梁单元"校验在 `m.ces` 非空时 opt-in 豁免；
  `input_format_3d.md` 新增 3D `*CE` 章节。
- **证据**：`verify_saddle_ce.py` 30/30；第 39 项 CTest
  `ce_3d_constraint_equations`（线性+非线性两路径、无效 dof 拒绝、
  重复系数合并、空 CE 确定性）；端到端 100 m 地锚式 Δ=20 mm 实测
  S−T=0.0199999、C−S≈0、C−T=0.02、竖向平衡 0.00%、收敛。
- **边界**：未做 APDL `CE` 读入（导入仍明确不读耦合方程）；分级顶推的
  i 阶段 constant 尚未接入 stage，当前为成桥态单次顶推；独立包已同步
  3 个核心 `.py` 与重编译的 `cable_solver.exe`。

### 第二十八次：组合体系阶段一内核规格先行（2026-11-18，用户选 A）

用户从「组合体系 / B5 五项边界 / CE 小边界」三方向中选组合体系，再从切入点
中选 **A 规格先行**。本轮只写规格契约，不动任何内核或 Python 实现：

- `组合体系接入规格.md` §3 由 5 条结论性描述细化为**实施契约**：
  - §3.1 显式体系标识：`Cfg` 新增独立 `cooperative`（默认 false），
    `*CABLE_BRIDGE SYSTEM=COOP` 置位；**严禁复用** `cfg.system` 空/非空
    或 `*CABLE` 非空推断协作；协作 `.dat` 必备 5 段，缺段显式拒绝。
  - §3.2 斜拉索目标索力保留：协作模式构造 `suspElems` 集合，只清属于它的
    `cfg.cables`，保留斜拉索目标力；非协作仍走旧 `clear()` 全量重填逐位不变；
    缺省目标力 / 单元编号冲突显式拒绝。
  - §3.3 三类残差分列 `r_main` / `r_stay` / `r_girder`：各自独立计算与报告，
    同时满足各自容差才标记收敛，不得合并成单一标量。
  - §3.4 专属输出段 `[COOPERATIVE]`（恒载份额/索力梯度/塔顶水平力平衡），
    段头必须登记进 `fem_eigen_3d.py::sections`；非协作不写该段。
  - §3.5 第 5 求解键切换条件：闭环前保持 4 键 + 哨兵拒绝，闭环后才注册
    `combo` 并成对改写守卫断言；阶段二 Python 侧方可开工。
  - §3.6 数值依据：改 `.dat`/结果段须跑 `dat_byte_regression` 37/37 或写依据；
    协作力学口径由用户确认，不得由实现者臆定。
- §6 阶段一验收清单按 §3.1~§3.5 分列（内核第 40 项 CTest + 阶段二桥型接入）。
- **守卫**：`verify_combo_system.py` 由 89 项增至 **95/95**，新增 `[7]` 契约登记组
  确认六条实施契约齐全，且内核侧确无 `cooperative` / `[COOPERATIVE]` / 第 5 键
  （防止提前实现假缺口）；哨兵拒绝、注册表 4 键、12 格默认 `.dat` 逐位不变全部保持。
- **未做（如实登记）**：阶段一内核实现、第 40 项 CTest、第 5 键注册、`combo_3d.py`
  建造器均未开工；协作力学口径待用户确认后写入 §3.6。当前工作区不含本轮提交。

**用户确认的协作体系力学口径（2026-11-18 晚，写入规格 §3.7）**：主跨中段悬索约占一半（居中），主跨两端各余约 1/4 斜拉；边跨斜拉约为主跨斜拉合计长度的一半略短（≈单侧斜拉段略短），以「塔顶水平力平衡残差 `r_tower`」判定与主跨斜拉桥基本平衡；塔高介于斜拉桥与悬索桥典型值之间（默认 ≈1/6~1/7 主跨）；双索面（`plane_count=2`）；恒载下主梁与索力平衡=合理成桥状态（`r_girder` 命中 + 主梁弯矩包络入计算书），非主梁弯矩恒为零。定性词全部实现为可配置参数 `susp_fraction`/`side_stay_ratio`/`tower_height_ratio`/`plane_count`/`coop_boundary_node`，默认取转写值；用户原话原样存档 §3.7 末尾，不被转写覆盖。**待复核**：原话"一半是双索面"按工程语境解读为"一般是双索面"，已在 §3.7 标注。守卫 `verify_combo_system.py` 96/96（契约登记增至七条）。

### 第二十九次：组合体系阶段一内核实现闭环（2026-11-18，用户选 A → 开工内核实现）

用户确认协作体系力学口径（§3.7）后指令「开工内核实现」。本轮在 FEM-Eigen 内核
完成阶段一，BridgeStudio 只做段头登记与守卫同步，**未放行第 5 键、未动 12 格
默认 `.dat`**：

- **`cable_solver.cpp`**：`Cfg` 新增独立 `bool cooperative=false`；
  `*CABLE_BRIDGE SYSTEM=COOP` 置位（与 `*SUSP_BRIDGE SYSTEM=EARTH/SELF` 独立存储、互不覆盖）。
  协作模式构造 `suspElems` 主缆/吊索集合，悬索找形后**只覆盖**这些索，`*CABLE` 里的
  斜拉索目标索力保留并一并进入既有索长闭环，无需另造联合迭代。斜拉索缺省目标力 /
  与悬索索编号冲突均显式拒绝。
- **四类残差**：`r_main`（矢高闭合）/ `r_stay`（斜拉索目标力）/ `r_girder`（相对变形
  `closure_err/span`，约 L/1000）/ `r_tower`（塔两侧水平力平衡，对应 §3.7 用户口径），
  任一超容差则 `coop_ok=0`。实测 `r_main=1.4e-16`、`r_stay=6.3e-3`、
  `r_girder=1.3e-4`、`r_tower=2.0e-2`。
- **`[COOPERATIVE]` 输出段**：仅协作时写，置于 `[END]` 前；非协作结果文件不含该段、
  既有段行序与取值逐位不变。`fem_eigen_3d.py::sections` 已登记段头。
- **文档**：`input_format_3d.md` 登记 `SYSTEM=COOP`、`[COOPERATIVE]` 段与四类残差口径；
  规格 §6 补入实测证据，状态改为「阶段一内核已闭环、阶段二 Python 侧仍未实现」。
- **第 40 项 CTest `combo_system_3d_benchmark`**：冻结 `combo_case.dat`
  （sha256 `ad55c4ea…`）重放，**32/32 通过**：协作重放收敛、斜拉索目标力保留、
  四类残差命中、`coop_ok=1`、负例拒绝、去 `SYSTEM=COOP` 默认零污染、FE/BS 源码契约齐全。
  算例仅作机制验证（斜拉索分担恒载约 0.13%），**不是真实均分协作桥**，真实比例属阶段二
  `combo_3d.py` 口径。
- **守卫**：`verify_combo_system.py` 的三条「阶段一未闭环」反证断言成对改写为
  「已闭环」正向确认，条数不减、口径不放宽，仍 **96/96**。
- **未做（如实登记）**：第 5 键注册、`combo_3d.py` 建造器、双索面协作算例、分级顶推
  stage 均未开工；当前工作区不含本轮提交。
