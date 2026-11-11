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
全部收敛在 **`X20_接入约定.md`**，并由 **`verify_x20_contract.py`**（78/78）
做成可执行守卫。新增或修改 X20 资产必须先登记再实现；仓库内不得出现
归档目录的路径常量或 `bridge_fe_modeler` 导入。

## 能力边界矩阵

`X20_接入约定.md` 管「X20 资产怎么接」，**`能力边界矩阵.md` 管「接到之后到底有哪些能力、哪些还没有」**：
6 桥型 × 2 维度主矩阵（建造器键 / 求解入口 / 阶段·活载·预应力参数逐格对拍）、
20 项维度级能力的 `文件::符号` 依据、参数覆盖契约与刻意隐藏白名单、
3 条真实缺口 + 历次闭环记录（编号容量 / 包络云图 / PDF 记载纠正 / B 类）。由 **`verify_capability_matrix.py`**（223/223）纯离线守卫：
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
  8 个 verify 回归脚本约 150 项断言、anastruct 交叉校验。
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
       与 `verify_capability_matrix.py` 223/223，把「哪些有 / 哪些没有」逐格
       钉到代码符号；据矩阵补齐 16 个只在 DEFAULTS 里、界面改不了的参数
       （`ps_beam` E/A/I/fpk/χ/1+μ、`truss_3d` 材料、`cable_stayed` panel、
       `cable_stayed_2d` 塔基与锚点、`suspension` 板厚与车道折减、
       `arch_3d` 主梁板厚），并给 `cable_stayed.panel` 写明「布索覆盖下界
       主导、小幅调大不改网格」的边界。默认 `.dat` 逐位不变 —— 2026-11-10
- [ ] **P1** X20 桥型建造器全量移植：`BUILDERS` 的 6 kind × 2 维度 12 格
       都有建造器键，但**没有「斜拉-悬索协作 / 梁拱组合」这类组合体系的
       独立建造器**；且原 7 项高级能力（分项损失、索长/Newton/`[OPT]`、
       承载力、四出口计算书、多格式导出、严格校验）仍不对齐：其中 4 项
       只在 3D 链路，**3 项部分接入**（`2D=P`）：截面应力只出组合内力的
       应力表、承载力 Mu/Vu 因 2D 无配筋契约显式拒绝；引用完整性中文校验
       按源码事实纠正并补齐，但 2D 仍窄于 3D；同源计算书四出口接通了
       `report_2d.py` 的 `Report` 旁路与原样复用的 HTML/DOCX/CSV/PDF
       写出器，但 2D 计算书内容仍窄于 3D、且 opt-in 默认关闭。
       均见 `能力边界矩阵.md`
       §2 备注。2D 模态 / 屈曲已按内核事实接通
       （opt-in，只有频率与 λ），其余 2D ↔ 3D 仍不对齐。
       逐条依据见 `能力边界矩阵.md` §4.1 / §4.2。
       **这是真实能力缺口、不是文档滞后**，与 P4 一起排期。
       内核侧尚无协作体系求解键（注册表 4 键、`detect_kind` 二选一），
       分阶段规格与验收断言见 `组合体系接入规格.md`；未实现期输入这类描述
       由 `verify_combo_system.py` 守卫显式拒绝，不会静默按单体系计算 —— 2026-11-10
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
- [x] **P1 梁格编号容量放开，默认 `.dat` 逐位不变**：`grillage_3d.numbering_plan()`
      按 `n_lines` / 总梁段数自动选 legacy（默认容量，黄金基线原样）或
      wide（`S=max(10000, 段数+2)`，三段不重叠硬校验）号段；阶段激活与
      张拉链改按元素元数据查表，不再 `e.id < 500` 反解。顺带校正契约 §5
      与模块头陈旧的斜拉塔单元号段（200/300 → 800/900）。12 格默认 `.dat`
      的 sha256 黄金哈希在 `verify_stage_tension_3d.py::DEFAULT_DAT_SHA`；
      wide 的节点/单元唯一、阶段覆盖、张拉链引用与 live
      `N = −P·loss/n_lines` 全绿。**未改任何默认输出行，`dat_byte_regression`
      37/37 基线无需刷新**；能力矩阵 §4 缺口由 5 条减为 4 条并留闭环记录
      —— 2026-11-10
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

## 质量保障闭环（铁律）

1. 改完任何 `.py` 必须重跑全部 verify 脚本再提交（当前 **43 个脚本全绿**，其中 38 个带汇总输出的脚本合计 **2678 项断言**；`verify_app.py` 109/109、`verify_cable_opt_3d.py` 119/119、`verify_cable_length_3d.py` 58/58、`verify_newton_3d.py` 52/52、`verify_report_3d.py` 149/149、`verify_view_3d.py` 69/69、`verify_envelope_3d.py` 152/152、`verify_strength_2d.py` 162/162、`verify_modal_2d.py` 111/111、`verify_validate_2d.py` 83/83、`verify_report_2d.py` 134/134、`verify_b_class_spec.py` 112/112、`verify_stage_tension_3d.py` 84/84、`verify_stage_tension_2d.py` 27/27、`verify_stage_prestress.py` 88/88、`verify_combo_system.py` 86/86、`verify_x20_contract.py` 78/78、`verify_capability_matrix.py` 223/223，内核侧另有 `ctest` 7/7 与 `dat_byte_regression` 37/37 黄金基线）。
2. 任何数值新功能必须先有理论解或交叉校验（anastruct / MIDAS / 黄金基线）。
3. 内核目录只在 `fem_eigen_export.py` 一处探测，其他模块一律复用。

### B 类后续项的口径文档

2D 模态 / 线性屈曲已按内核事实接通（opt-in，见 `能力边界矩阵.md` §2 备注）；
B2 的「截面应力」半部随后按 `P` 口径接入（`strength_2d.py`，承载力仍显式拒绝），
B2 剩余 6 项、组合体系（B1，用户本轮暂缓）、B3 非线性稳定全过程、B4 预应力
损失两处规范口径、B5 无应力曲率的**现状、两阶段规格与验收断言**统一写在
`B类后续项接入规格.md`，由 `verify_b_class_spec.py` 纯离线守卫。这些项目
**只立规格、未实现力学**；未实现期 `[BUCKLING] ERROR=nonlinear_state_not_supported`
与「σl2 只按直线筋 / εcs·φ 需查表输入」等如实边界一律保留，不虚报能力。

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
**承载力 Mu/Vu 本次未做**：2D 没有配筋契约，`RebarSpec3D` 是 3D 的，
新增 opt-in 的 `RebarSpec2D` 之前不出 Mu/Vu 表、不拿应力利用率冒充承载力。
默认关闭（`strength_2d=False`），默认 `.dat` / 默认 HTML 逐位不变；
`verify_strength_2d.py` 162/162 含混凝土 / 钢闭式解 oracle 与缺参拒绝负例。

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

