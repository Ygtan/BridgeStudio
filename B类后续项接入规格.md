# B 类后续项接入规格（组合体系之后）

> 建立：2026-11-10。本文件是 **B2 剩余项 / B3 / B4 / B5 的规格与能力边界**，不是「已完成清单」。
> 与 `组合体系接入规格.md` 分工：那份管 B1（协作体系求解键 + 建造器），这份管其后的四个项目。
> 用户本轮明确「暂不做组合体系」，故 B1 保持 `组合体系接入规格.md` 的两阶段规格与显式拒绝，不动内核。

## 0. 范围、口径与本轮实际交付

| 项 | 本轮状态 | 依据 |
| --- | --- | --- |
| B1 组合体系 | **暂缓**（用户指定） | `组合体系接入规格.md`、`verify_combo_system.py` 86/86 显式拒绝 |
| B2 2D ↔ 3D 能力对齐 | **部分实现（十五次逐次接入）**：先按源码事实接通 2D 模态 / 线性屈曲；第六次 A 档按 `P` 口径接通 2D **截面应力**半部（承载力当时显式拒绝）；第七次把「引用完整性中文校验」按源码事实由 `N` 纠正为 `P` 并补齐；第八次接通「同源计算书四出口」的**导出机制**（仍标 `P`）；第九次由 FE opt-in 补 `[MODAL2D]` 段闭环「模态周期 T / 振型 / 参与质量」（`BASIS=xy`，用户拍板方向口径，收官时 **B2 剩余 3 项**）；第十次由 FE opt-in 补 `[PRESTRESSLOSS2D]` 段闭环「2D 分项预应力损失明细」（`BASIS=2d_global`，仍标 `P` 不标 `Y`）；第十一次由 FE opt-in 留痕补 `[NEWTON2D]` 段，**只闭环三层迭代中的内层 Newton 一层**（该格 `N→P`，仍标 `P` 不标 `Y`），`[LENGTH]` / `[OPT]` 两层经只读调研确认 2D 内核没有对应力学、两格继续标 `N`、不造占位，**B2 剩余 2 项**（内容收窄为「两层力学 + 多格式导出」）；第十二次在同一格内按 opt-in `RebarSpec2D` / `capacity_2d.py` 接通 Mu / Vu 一条 flex 链（压弯 / 轴压仍缺、仍标 `P` 不标 `Y`），§4.2 与 **B2 剩余 2 项**均不变；第十三次**首次真正新造 2D 力学**、按 opt-in 补齐 `[LENGTH2D]` 平面索长闭合一层（该格 `2D=N` → `2D=P`、仍标 `P` 不标 `Y`），`[OPT]` 仍标 `N`、不造占位，第十三次收官时 §4.2 为 **1 个 `2D=N` + 6 个 `2D=P`**、**B2 剩余 2 项**（内容曾再收窄为「`[OPT]` 一层力学 + 多格式导出 / `.mct` 反向导入」）；**第十四次** `[OPT2D]` 2D 平面索力优化按 opt-in **首次新造第二层力学**接通（该格 `N→P`、仍标 `P` 不标 `Y`，只 MINMAXM + 显式索力箱、不等于 3D `[OPT]`），§4.2 现为 **0 个 `2D=N` + 7 个 `2D=P`**、**B2 剩余 1 项**（仅剩被 X20 契约锁死的多格式导出 / `.mct` 反向导入），双层 opt-in 默认全关、默认 `.dat` / `.result` / 黄金基线逐位不变，逐项评估写入 §1；**2026-11-12 第十五次** B2「2D 平行导出 / 导入链」两格按 B 档 `P` 接通（多格式导出 / `.mct` 反向导入，`2D=N` → `2D=P`、**仍 `P` 不 `Y`**），§4.2 现为 **0 个 `2D=N` + 9 个 `2D=P`**、**B2 剩余 0 项**（B2 逐次接入全部收口；两格仍 `P ≠ Y`） | `modal_2d.py`、`verify_modal_2d.py` 111/111、`strength_2d.py`、`verify_strength_2d.py` 162/162、`bridge_spec.py::validate_model`、`verify_validate_2d.py` 83/83、`report_2d.py`、`verify_report_2d.py`、`modal_2d.py::parse_modal_detail`、`verify_modal_detail_2d.py`、`prestress_loss_2d.py`、`verify_prestress_loss_2d.py`、`newton_2d.py`、`verify_newton_2d.py` |
| B3 非线性稳定全过程（第二十一次分阶段推进中） | **2026-11-16 用户明确要求「继续补 B3 五项力学」**：按 §2.5 列明的五项未覆盖项逐项 opt-in 落地——① 位移控制 / 线搜索、② 初始缺陷形态 / 幅值 / 敏感性、③ 材料非线性本构、④ 分支屈曲与分支切换、⑤ 动力 snap-through。每阶段交付「内核 opt-in（默认零污染）+ 独立闭式 / 解析锚点 + 负例守卫 + 新 CTest」，逐阶段仍标 `P` 不标 `Y`，五项全部收口且干净构建全量回归后才按 §2.6 统一推 `Y`。本行为**进行中**登记，具体逐项实测收口写入 §6 第 21 条；不得靠改文档提前打勾。此前历史（第十七次起 opt-in 接入、第十八次 N 阶段独立探针、第十九次 ①②③a③b 成桥态 / 施工阶段 / Riks 闭环）逐条保留于 §2、§6，B3 在五项力学补齐前**仍 `P` 不 `Y`**。 | §2、§2.5、§2.6、§6 第 21 条 |
| B3 非线性稳定全过程（历史） | **第十七次按 opt-in 接入（2026-11-14）**：`STABILITY=1` 把球面弧长法平衡路径跟踪接入 `cable_solver.exe` 主程序，`[STABILITY]` 段 + 独立解析锚点 + 第 20 项 CTest；默认全关、既有算例逐位不变。**第十八次（N 阶段）** 补 CB3D19 共转 CR 梁刚接框架后屈曲独立对标（`frame_probe.exe` + `BEAM188/ARCLEN` + 第 21 项 CTest），仍属独立探针、不是成桥态闭环。**第十九次（2026-11-15，a 阶段 ①）** 新增 `STABASIS=COMPLETION` 与 `*STABILITY_LOAD`，把平衡路径起点从 `readModel3D` 参考构形换成**成桥态收敛位移 `u0` + 恒定荷载 `F0`**（`R = F0 + λ·Finc − Fint(u)`），`[STABILITY] BASIS=completion_state` 挂在主链路 `[END]` 前；第 22 项 CTest 用 V 形双索单自由度体系的**独立闭式 `λ(v)`** 逐点对拍 `MODE=STATIC` / `MODE=TENSION` 两种成桥态闭环。**仍标 `P` 不标 `Y`**：① 只做 FEM 侧自洽与解析锚点，真实桥梁算例的 ANSYS 两段对标属 ②、B3 推 `Y` 属 ③。**第十九次 ②（2026-11-15）已交付**：CB3D21 真实双塔三跨斜拉桥（50 节点 / 63 单元 / 16 索）与 ANSYS 两段求解逐子步对拍（`ansys_bridge_completion.py` / `run_cases_completion.py` / 第 23 项 CTest，2392 项黄金值、`--live-ansys` 复核 `max|d|≤1e-11`）；同源设计线形 + 16 根索 `L0` + `INISTATE eps`，`RES0=7.97e-3 N` 与 `[NEWTON] RESLAST` 同量级。② 完成**仍 `P` 不 `Y`**：③ 才推 `Y`，且 `MODE=COMPLETION` 起点不同源缺陷登记为独立后续项。边界见 §2.5 / §2.6 | §2、§2.5、§2.6 |
| B4 分项预应力损失（第二十七次收口：2D=Y / 3D=Y，Y 口径=平面内完整，见 §6 第 29 条与矩阵） | **2026-11-16 用户「同意建议的顺序」，按 σl2 → σl6 逐项 opt-in 落地**。口径已拍板：σl2 代表值取**张拉端 x=0（保守）**（附录 G 给沿程分布、规范无统一代表值规定，取张拉端峰值与 G.0.2-4 `Δσ=2Δσd·lf` 闭式最干净，且 σpe 用于索力 / 承载力宁可保守）；σl6 表值由**用户在模型里显式录入经核对的规范表值**（不得拿 `code_loads.hpp` 拟合值 + 硬编码 βsc 冒充规范值）。每项交付「内核 opt-in（默认零污染）+ 独立闭式 oracle + 负例守卫 + 新 CTest」，逐项仍标 `P` 不提前打勾，两项收口后才评估能力矩阵该行。详见 §6 第 23 条。 | §3、§6 第 23 条 |
| B5 成桥态无应力曲率（第二十六次：3D 线性梁 FEM 自洽已接入，2D=N / 3D=P） | **2026-11-16 用户「同意建议的顺序」，先做 FEM 自洽深度**：在 `Element3D` / `CRElemData` 增 opt-in 单元级初始曲率自由度（默认 0 = 现行为），验证零外荷载自平衡、`L0` / `J` 不变、成桥态 `My` 解析锚点；**不接真实制造参数、不做 ANSYS 对拍**（那需先有设计给定的取值依据）。不得把「节点预抬高」写成「无应力曲率已实现」。详见 §6 第 23 条。 | §4、§6 第 23 条 |

**2026-11-12 第十六次（B2 收官后的 2D 承载力补齐）**：用户四项拍板全选推荐
——① 只做 2D 压弯 / 轴压半部（纯 BS 侧、FE 仓零改动）；② 新建 opt-in
独立口径层 `capacity_axial_2d.py`，**不改** `beam_combinations()` 默认路径与
12 角点应力；③ 复用 3D 三链分派（5% 轴压 / 1% 偏心距）+ `RebarSpec2D.l0`
必填，缺失整章拒绝、不静默取短柱；④ 矩阵仍标 `P`（`P ≠ Y`），按 D 类准则
只改过时措辞。**调研纠偏**：`combination.py` 旧注释「内核没有活载轴力」与
两仓源码矛盾 —— FE `defaultEnvelopeSpecs()` 已登记 `FORCE/N`、BS
`_parse_lane_envelope()` 已解析 `NMin/NMax`，只是组合层取 `q.get("N", 0.0)`
键名不匹配才退化成恒载单值；本轮不修组合层（那会改默认计算书数值），改由
独立层直接消费。效应按 `combination.ULTIMATE` 系数在**同一 (单元, 端)**
配对并枚举 2×2 角点，公式与表唯一来源 `section_strength.check_eccentric` /
`check_axial` / `eccentric_table` / `axial_table` /
`capacity_calculation_sheet`。独立守卫 `verify_capacity_axial_2d.py`（含纠偏
证据、闭式 oracle、13 类拒绝负例、默认零污染、四出口同源、两仓文档对拍）。
**仍 `P ≠ Y`**：2D 逐分量独立极值不等于同一车辆位置的联合最不利 (N, M)，
无 GUI 入口、不等于 3D 全套承载力。

**本轮没有做的事**（逐条可核对）：不新增 kind、不新增求解键、不新增 `*CABLE_BRIDGE` 关键字、不改任何内核数值路径、不刷新 `dat_byte_regression` 黄金基线。第九次、第十次、第十一次是用户逐次明确授权的三次破例：只在 FE 侧新增 opt-in 的 `[MODAL2D]` / `[PRESTRESSLOSS2D]` / `[NEWTON2D]` 结果段（默认关闭、不动既有四段与任何力学判据 / 数值路径），BS 侧新增解析 / 报告 / 界面接入层与守卫，默认 `.dat` / 默认 `.modal.txt` / `.result` 逐位不变。第十一次特别地**不新造力学**：`[LENGTH]` / `[OPT]` 两层 2D 内核没有对应力学，两格继续标 `N`、不造占位、不写不适用段冒充闭环。第十三次起已**首次真正新造** `[LENGTH2D]` 平面索长闭合一层（该格 `2D=N` → `2D=P`）；**第十四次起 `[OPT2D]` 2D 平面索力优化已按 opt-in 新造接入**（该格 `2D=N` → `2D=P`、仍 `P` 不 `Y`），只 MINMAXM + 显式索力箱，3D 的 SYSGUARD / MINENERGY / `J=M·D` 影响矩阵在 2D 仍没有、不等于 3D `[OPT]`。

**2026-11-14 第十七次（B3 主链路 opt-in 接入）**：用户选 a，按 §2.3 原两阶段
规格落地。FE 侧 `cable_solver.exe` 新增 `STABILITY=1` 独立 opt-in 模式
（默认全关），在 `readModel3D` 参考构形上调用 L 阶段已用 ANSYS `ARCLEN`
对标过的 `stability_path_3d.hpp` 球面弧长法，输出 `[STABILITY]` 段与
`ERROR=` 双函数；**不改** `NonlinearOptions3D` 默认值、不改成桥态默认
参数、不删 `[BUCKLING] ERROR=nonlinear_state_not_supported`。BS 侧新增
唯一解析器 `stability_3d.py` 与独立守卫。验收按 §2.4 八条逐条落实，
默认 `.dat` / `_cable.txt` / 黄金基线逐位不变，能力矩阵该行**标 `P` 不标
`Y`**（边界见 §2.5）。B1 仍暂缓、B4 / B5 仍只立规格、MIDAS 数值对拍
仍未闭环；本节不把 opt-in 探针写成「成桥态闭环已具备全过程稳定分析」。
B4 的两处公式 / 表值接入与 B5 无应力曲率的**两阶段规格尚未实现**，
不得因 B3 已 opt-in 接入而顺带把它们写成已落地。

**2026-11-14 第十八次（N 阶段：CR 梁刚接框架后屈曲独立对标）**：
用户按「先 b 后 a」选 b。FE 侧新增 `frame_probe.exe` / `frame_probe.cpp`，
把 L 阶段的 `stability_path_3d.hpp` 球面弧长法与 K 阶段的共转 CR 梁
组合，做 Williams toggle 型刚接框架（两根各 24 个 `BEAM`、端部铰支 /
顶点刚接）后屈曲全过程，与 ANSYS `BEAM188 + NLGEOM + ARCLEN`
两个 job 端到端对标；**`fem_3d_nl.hpp` / `stability_path_3d.hpp` /
`cable_solver.cpp` 一字未改**，`fem_3d_nl.hpp::solveModel3DNonlinear`
仍是荷载控制 Newton。BS 侧新增唯一数据源 `ansys_bridge_frame.py`
与编排 `cases/run_cases_frame.py`、正式算例 `CB3D19_*`；FE 侧新增
`tests/benchmark/frame_postbuckling_3d/` 与第 21 项 CTest
（12513 项黄金值、三套独立线性刚度锚点、负例与日志断言）。
**本轮只补「CR 梁后屈曲基准题」这一项独立基准，不改 B3 的 `P` 口径**：
全过程稳定分析接进成桥态闭环 / 施工阶段链路（把 `P` 推 `Y`）是后续
a 阶段，仍未实现；§2.2、§2.5、§6 的边界同步收窄但不打勾。
B1 仍暂缓、B4 / B5 仍只立规格、MIDAS 数值对拍仍未闭环。

> 2026-11-10 第六次（A 档）追加：用户选 A、B1 组合体系仍暂缓。本档只把 §1 表格里
> 「截面应力 / 承载力验算」的**应力半部已接入**（`strength_2d.py`，`verify_strength_2d.py`
> 162/162），**没有**实现 B3/B4/B5 任何力学，也没有新增 kind / 求解键 / 关键字 /
> 结果段 / 内核数值路径。`AW` / `t_plate` / `grade` / `kind` 是 Python-only 契约，
> 不写进 `.dat`；12 格默认 `.dat` 黄金哈希与 FE 黄金基线逐位不变。
> 因为 2D 没有配筋契约，**承载力 Mu/Vu 必须显式拒绝**，能力矩阵该格只能标 `P`；
> 这一格没有闭环，B2 剩余 6 项也仍未闭环。
>
> 2026-11-11 第七次追加：按 §1 的「③ Python 适配层」逐项源码复核，把
> 「引用完整性中文校验」一格从 `2D=N` 纠正为 `2D=P`：主体是**文档漂移**
> （`bridge_spec.py::validate_model` 早已存在且三入口调用），并按 3D 口径
> 补齐 2D 缺的引用项，新增 `verify_validate_2d.py` 守卫。校验只读模型、
> 不写 `.dat`，默认输出逐位不变，仍标 `P` 不标 `Y`。同时确认
> 「多格式导出 / `.mct` 反向导入」因 `X20_接入约定.md` 的 **X20 契约**
> （铁律 2 唯一转换点、§9 禁止改签名 / 另建 canonical）无法在
> **不补 0** 冒充 3D 字段的前提下接通，本轮明确不做、继续写「没有」。
> B3 / B4 / B5 仍只立规格；FE 内核零改动。

> 2026-11-11 第八次追加：按 §1 的「③ Python 适配层」接通
> 「同源计算书四出口」的**导出机制**——新增 `report_2d.py` 把
> `Solve2DResult` 装成 `report_3d.Report`，四出口原样复用 `write_all`，
> 不另写渲染器、不改 `BKSEC<i>Z` / 页码书签机制；`write_2d_html`
> 一个字节不改、默认 `.dat` 与 12 格黄金哈希逐位不变。
> **仍标 `P` 不标 `Y`**：2D 报告内容窄于 3D（无 figures、无振型 /
> 参与质量 / 承载力 Mu、Vu / 结构云图），四出口是 opt-in 旁路。
> `verify_report_2d.py` 守卫数值逐格同源、缺数据不补零、PDF 故障注入
> 不生成占位文件。B1 组合体系仍暂缓，B3 / B4 / B5 仍只立规格；
> 多格式导出 / `.mct` 反向导入仍被 **X20 契约**锁死，继续写「没有」。
> FE 内核零改动。

> 2026-11-11 第十次追加：**破例改 FE 内核**（用户明确授权并拍板段形态）。
> 与第九次同构但更硬：`computePrestressLosses()` 六项公式 2D / 3D 同源，
> 可 2D 入口 `fem_2d.cpp::processPrestress` 只取 `effectivePrestressRatio`
> 标量，六项当场丢弃，状态层连容器都没有。本轮 `loss` 行一字不动，另取
> 一份同源明细存进 `Model2D::prestressLossDetails`，
> `prestress_loss_output_2d.hpp::formatPrestressLossSection2D` 写
> `[PRESTRESSLOSS2D]` 段（`BASIS=2d_global` / `P<id> SIGMA_CON= SL1=..SL6=
> SPE= RATIO=`，12 位有效数字、单位 Pa，字段与 3D `stage prestress loss`
> 同源）。段形状按用户拍板：**独立段、不设 STAGE 占位**（2D 没有阶段维度，
> `P<id>` 是输入行首显式束号），不等于 3D 的阶段分项损失；只收录
> `LOSSREF` 束，缺束写 `ERROR=no_lossref_prestress` 不摆空表。
> `example_mixed.exe --loss-detail` 写 `<stem>.loss.txt`，
> `--loss-detail-only` **只取明细、不求解、不写 `.result`**，避让
> `example_stage.exe` 主路径；默认 `.result` / 控制台 / 12 格默认 `.dat`
> 逐位不变，黄金基线不刷新，σl1~σl6 / σpe / RATIO **由内核写出**，
> Python **只解析**、不得现算或反算。BS 侧补 22 键 `*PRESTRESS_LOSS` 表
> 与非阶段 `*PRESTRESS ... LOSSREF=` 束的 opt-in 契约（显式挂
> `BridgeSpec`，不进 `DEFAULTS` / `FIELDS`）、`bridge_spec.validate_model`
> 的 LOSSREF 引用中文校验、`prestress_loss_2d.py` 唯一口径层、
> HTML / 四出口同源表与界面第二层开关；双层 opt-in 默认全关，
> `verify_prestress_loss_2d.py` 守卫 FE 源码契约 / 独立闭式解 /
> 12 类负例 / 零污染 / 四出口同源。矩阵该格由 `N` 改 `P`，**标 `P` 不标 `Y`**：
> opt-in 默认关闭，且没有 3D 的阶段维度与阶段预应力写回。
> B1 组合体系仍暂缓，B3 / B4 / B5 仍只立规格；**B2 剩余 2 项**
> （三层迭代过程量 / 被 X20 契约锁死的多格式导出与 `.mct` 反向导入）
> 继续写「没有」。

三条铁律（与 `能力边界矩阵.md` §0 同源）：**不静默兜底**（做不到写「做不了」+ error/warn）、**只暴露内核已有能力**（Python 不重算 K/M、不造频率或 λ、不编规范表值）、**结论与计算书同源**（报告表与界面读同一份数据）。

## 1. B2 剩余 7 项：按源码逐项评估（不按旧文档）

原 §4.2 写「9 个 `2D=N` 都只在 3D」。以源码复核后发现**其中 2 格是文档与内核事实漂移**：`fem_2d.cpp:2344 computeNaturalFrequencies2D`、`:2415 computeBucklingFactors2D` 早就能算，`example_modal.exe`（`CMakeLists.txt:215`）会写 `<stem>.modal.txt`；BridgeStudio 只是从未读它。本轮已接通并把能力矩阵 §2 两格改 `Y`，§4.2 收窄为 **7 个** `2D=N`。
第六次 A 档又把「截面应力 / 承载力验算」一格由 `N` 改 `P`（只接通应力半部、承载力显式拒绝），
第七次又把「引用完整性中文校验」由 `N` 改 `P`，第八次把「同源计算书四出口」由 `N` 改 `P`，故第九次收官时 §4.2 为 4 个 `2D=N` + 3 个 `2D=P`；**「7 个」是上一轮的历史计数，不代表当前仍全是 `N`**。第九次闭环「模态周期 T / 振型 / 参与质量」——该项不是 §2 的一格，故 `4N+3P` 不变，只把 B2 剩余由 4 项收窄为 3 项。第十次闭环「2D 分项预应力损失明细」——该项是 §2 的一格，故当时 §4.2 为 3 个 `2D=N` + 4 个 `2D=P`，**B2 剩余 2 项**（由 3 项收窄）。第十一次只闭环「内层 Newton / 共转列式过程量」一格，故 §4.2 现为 **2 个 `2D=N` + 5 个 `2D=P`**；**B2 剩余仍 2 项**，但内容由「`[LENGTH]`·`[NEWTON]`·`[OPT]` 三层迭代过程量 / 多格式导出」收窄为「`[LENGTH]` / `[OPT]` 两层力学 / 多格式导出」。第十三次起 `[LENGTH2D]` 一层已按 opt-in 接通（该格 `2D=N` → `2D=P`）；**第十四次起 `[OPT2D]` 2D 平面索力优化按 opt-in 新造接通**（该格 `2D=N` → `2D=P`、仍 `P` 不 `Y`），只 MINMAXM + 显式索力箱、不等于 3D `[OPT]`；**2026-11-12 第十五次** B2「2D 平行导出 / 导入链」两格按 B 档接通（多格式导出 / `.mct` 反向导入，`2D=N` → `2D=P`、**仍 `P` 不 `Y`**），§4.2 现为 **0 个 `2D=N` + 9 个 `2D=P`**、**B2 剩余 0 项**（第十五次两格按 `P` 接通、仍 `P ≠ Y`；B2 逐次接入全部收口）。

> 2026-11-11 第九次追加：**破例改 FE 内核**（用户明确授权）。`ModalResult2D.modes`
> 早有全阶振型、`assembleMassMatrix2D()` 可重建同一份 M，缺的只是写出。
> FE 新增 `modal_output_2d.hpp::formatModalSection2D`（与 3D oracle 同构、
> 不求解新特征值），`example_modal.exe <dat> --modal-detail` 才追加
> `[MODAL2D]` 段；不加开关时 `.modal.txt` 逐字节不变、黄金基线不刷新。
> 用户拍板 `BASIS=xy`：只给水平 X / 竖向 Y 两向平动参与质量，
> 2D 无出平面自由度，**Z 向参与质量不存在（不是 0，不补零）**，θz 只作
> 振型展示；`T=1/f` 由内核写出，Python 只解析、不得现算。BS 侧
> `modal_2d.parse_modal_detail` 与两张同源表接进 HTML / 四出口 / 界面，
> 双层 opt-in 默认全关，`verify_modal_detail_2d.py` 守卫。

| 能力 | 2D 内核事实（源码依据） | 分类 | 落地条件 |
| --- | --- | --- | --- |
| 分项预应力损失 | `fem_2d.cpp:1188-1192 / 523-527` 能读 `*PRESTRESS_LOSS`、能算 σl1~σl6 并取 `effectivePrestressRatio`；**第十次已闭环**：`prestress_loss_output_2d.hpp` 新增 `[PRESTRESSLOSS2D]` 段（此前六项算完当场丢弃，3D 才写 `stage prestress loss`） | ① 契约 + 公式已有 / 明细段缺 → **已补段** | FE `example_mixed.exe --loss-detail[-only]` 写 `<stem>.loss.txt`，`BASIS=2d_global`、**不设 STAGE 占位**、不等于 3D 的阶段分项损失；σl1~σl6 / σpe / RATIO 由内核写出，`prestress_loss_2d.py` 只解析、不现算、不补零。BS 侧 22 键 `*PRESTRESS_LOSS` 表与非阶段 `LOSSREF` 束显式挂 `BridgeSpec`，双层 opt-in 默认全关；`verify_prestress_loss_2d.py` 守卫独立闭式解 / 12 类负例 / 12 格默认 `.dat` 哈希 / 四出口同源。矩阵标 `P` 不标 `Y` |
| 截面应力 / 承载力验算 | `fem_2d.cpp:3831-3846` 有「单元应力 (Pa)」段；`fem_eigen_export.py:341` 段头已登记却无消费分支；`section_strength.py::check` 是纯规范校核（不重算力学）。**复核补充**：该段是内核用 WT/WB 从内力换算的第二套 σ，且 `example_stage.cpp` 的阶段结果文件根本没有它；2D `BridgeSpec` 也没有配筋契约 | ① 接入层缺口（**应力已接入**）/ 承载力 Mu/Vu 已于第十二次按 `RebarSpec2D` opt-in 接入，压弯 / 轴压仍缺 | 应力半部由 `strength_2d.py` 接入：唯一效应源 `final_beam` + 三种组合，与 `write_2d_html` / 界面同源，**不消费「单元应力」段**；N 单值 × My/Vz 两侧 × 三种组合共 12 个角点枚举，缺 AW / WT / WB / grade / kind / 板厚整章 `refused`。承载力 Mu/Vu 由独立的 `capacity_2d.py` 出表（配筋契约 `RebarSpec2D` 挂 `ElemTypeDef.rebar`，Python-only、几何与配筋全部必填、缺一整章 `refused`）：效应只取 `ultimate` 基本组合逐端最不利 M_pos / M_neg / Vd，复用 `section_strength.check_capacity` 与三张同源表，**不用 12 角点**；压弯 / 轴压不做（2D 组合轴力 N 只有恒载单值、无活载轴力包络），故矩阵仍标 `P` 不标 `Y`；`verify_capacity_2d.py` 独立守卫 |
| 索长迭代 `[LENGTH]` / Newton `[NEWTON]` / `[OPT]` | **第十一次只读调研纠偏**：2D 内核全文无三个段头、也没有 `std::cout` 过程量打印（「只打控制台」是 3D CHANGELOG 历史描述被误套）；2D 有方案C Newton-Raphson，`NonlinearDiag` 留汇总与全量残差历史，但 `dU` / `alpha` / `lamTarget` / `Fint` / `Fext` 是局部变量丢弃。`[LENGTH]` 索长 / 几何闭合迭代与 `[OPT]` 带约束索力优化**连输入契约都没有、内核根本没算** | ② 内层 Newton「算了没留痕」**已补段**；`[LENGTH2D]` 一层已于第十三次按 `P` 接入、`[OPT2D]` 一层已于**第十四次**按 `P` 接入（仍 `P` 不 `Y`） | **第十一次只闭环内层 Newton 一层**：FE 新增 `NewtonStep2D` / `NewtonTrace2D` 留痕（判据 / 步长 / 线搜索 / 收敛一字不改）+ `[NEWTON2D]` 段（`BASIS=2d_global`、`setprecision(10)`、不写 `REFACT` / `TOLF` / `TOLD` / 阶段占位），`newton_2d.py` 只解析、不重算、不补零，矩阵该格标 `P` 不标 `Y`。**第十三次 `[LENGTH2D]` 一层按 `P` 接入；第十四次 `[OPT2D]` 一层按 `P` 接入（仍标 `P` 不 `Y`、只 MINMAXM + 显式索力箱、不等于 3D `[OPT]`）**：下料长度 `Lu` 由内核 `fem_2d.cpp::iterateCableLength2D()` 迭代求出、`length_2d.py` 只解析、不重算、不补零；Python 不得自己造目标索力 T*（不猜 T*）、不得从位移反推过程量；2D 版是无垂度线性杆 + 中心有限差分 J + LM 阻尼最小二乘，不等于 3D 的垂度 / Ernst / 共转成桥态 / 闭式影响矩阵 / 三层迭代 |
| 模态周期 T / 振型 / 参与质量 | **第九次已闭环**：FE 新增 `modal_output_2d.hpp::formatModalSection2D`，`example_modal.exe --modal-detail` 追加 `[MODAL2D]`（`BASIS=xy`）；`ModalResult2D.modes` 早已算出振型、`assembleMassMatrix2D` 重建同一份 M | ② 内核算了没写出 → **已补段** | `T=1/f` 由内核写出，Python 只解析；2D 无出平面自由度，Z 向参与质量不存在（不是 0）；`verify_modal_detail_2d.py` 守卫 FE 源码契约 / 12 类负例 / 零污染 / 四出口同源 |
| 3D 活载包络结构云图 | `solve_2d.py:194-222` 已接 2D 活载包络**数值**；云图是 `view_3d.py` / `envelope_3d.py` 的空间展示层 | ③ 维度不适用 | 矩阵须写「数值 Y / 3D 云图不适用」，不是移植 |
| 同源计算书四出口（§2 标 `P`） | `Solve2DResult` 无 `tables()` / `metrics()` / `report()`；`write_2d_html` 是默认 2D 计算书 | ③ Python 适配层（**第八次已接通导出机制**） | `report_2d.py::build_2d_document` 装成 `report_3d.Report`，`write_2d_all` 原样复用四写出器；`verify_report_2d.py` 守卫。内容仍窄于 3D，故标 `P` |
| 多格式导出 / `.mct` 反向导入 | `export_formats.py::to_canonical` 只吃
`ModelSpec3D`；`mct_io.read_mct*` 只产 `ModelSpec3D`（均为 3D 链，一字未动）。
**第十五次**：另建 `export_2d_formats.py` 的 `plane_frame_2d/v1` 平行 2D 中间
表示（**不是 canonical dict**） | ③ Python 适配层（**第十五次按 B 档平行链
接通，仍 `P` 不 `Y`**） | `X20_接入约定.md` 铁律 2 把 `to_canonical` 定义为
`ModelSpec3D → canonical dict` 的**唯一转换点**，§9 禁止改签名 / 另建
canonical；canonical schema 必带 `z`、6 dof 与 `IZZ/IYY/IXX`，「不映射」在
现有 schema 无处安放，硬接只能补 0 冒充 3D 字段——原结论是「除非用户授权
改契约并走 §8 的 B 档登记流程，否则保持 `2D=N`」；**已于第十五次按此流程
授权并登记资产 `B5`（B 档）+ 新增 §3.4「2D 平行链的正交约定」**：两条链
正交、互不冒充，2D 不改 3D 唯一转换点、不补 0、显式 `dimension="2D"` +
拒绝非平面（`dimension_not_plane`）、z=0 与平面外 dof 固定逐条进 `warnings`；
仍 `P ≠ Y` |
| 引用完整性中文校验 | `bridge_spec.py::validate_model` 早有中文引用校验
（重复 id、单元→类型 / 节点、边界→节点、荷载→节点、缺 I / A、转角 dof
无刚度），`solve_2d` / `ModelDoc.validate` / `app.py` 三处调用；FE 2D 内核
也有中文报错 | ① **主体是文档漂移，已按 `P` 口径纠正并补齐** | 本轮补
材料号引用、重复材料 / 阶段 id、阶段 ACTIVE / DEACTIVE / 荷载 / 单元荷载 /
预应力引用、车道节点引用、dof 与边界自由度范围、零长度单元。仍是单一中文
error 列表，没有 3D 的 `(errors, warnings)` 两级与 LOSSREF / 成桥态工况，
`beam` 走旧 `ModelSpec` 契约；**标 `P` 不标 `Y`** |

**验收口径**：① 类接通时须有独立 oracle 与「结论同源」断言；② 类在 FE 补段前一律保持 `2D=N`，不得在矩阵里提前打勾；③ 类写进矩阵备注，不算力学缺口。守卫 `verify_capability_matrix.py` 已加「§2 两格标 Y / §4.2 收窄为 7 个 / README 同步」三条防漂移断言。
① 类里已接入的「截面应力」另按 `P` 口径验收：独立闭式解 oracle（混凝土 §7.1.5、钢 D64 §5.3.1）、
缺参整章拒绝负例、默认 `.dat` / 默认 HTML 逐位不变、界面与计算书同源、以及
「不消费内核单元应力段」的源码级断言，全部由 `verify_strength_2d.py` 锁住。
**没有完整配筋契约与压弯 / 轴压链就不许把 `P` 涂成 `Y`**（第十二次只接通
Mu / Vu 一条 flex 链，仍标 `P`）。第十二次另按 `P` 口径验收：Mu / Vu 闭式解
oracle、`ultimate` 逐端最不利效应同源、`γ0 / α1 / α3` 出处、缺配筋整章拒绝、
默认 `.dat` / 默认 HTML 逐位不变、应力章与承载力章措辞不矛盾、四出口同源，
全部由 `verify_capacity_2d.py` 锁住。

## 2. B3 非线性稳定全过程（荷载–位移极值点）

### 2.1 现状（源码为准）

- `FEM-Eigen/CHANGELOG.md:302-311`：写明 `cable_solver.cpp` 只写 `[BUCKLING] ERROR=nonlinear_state_not_supported`；「真实需求是非线性稳定全过程（荷载–位移极值点）分析，**本版本未覆盖，不拿线弹性 λ 冒充**」；λ 是参考荷载放大倍数，**不是规范稳定安全系数**，JTG 3362 无 λ 合格限值。
- `cable_solver.cpp:1822-1823` 无条件写该 ERROR 段（链路性质判定，不是运行期状态机）；`input_format_3d.md:435`、`USER_MANUAL.md:425-426`、`BridgeStudio/buckling_3d.py:59-80` 与 `开发使用手册.md:911` 同口径。
- `fem_3d_nl.hpp:800 solveModel3DNonlinear` 是**荷载控制** Newton：`:952-954` `Ftarget = lam·Fext`，`:994-995` 残差/位移判据，`:1013` 迭代用尽直接抛错。全库检索 `弧长 / Riks / 位移控制 / 极值点 / 荷载-位移` 零命中（「弧长」只指索的几何弧长）。
- 可复用组件：一致切线刚度（`:342/:405/:704-732`）、残差装配（`:956-962`）、符号分解（`:965-973`）、过程留痕 `NewtonTrace3D`（`:756-776`）、「正常段 + ERROR 段」写出框架（`buckling_output_3d.hpp`、`newton_output_3d.hpp`）。
- **第十七次变更（2026-11-14）**：以上 §2.1 是第十次立规格时的源码快照，
  **历史事实保留不删**。L 阶段（CB3D17）已新增独立
  `stability_path_3d.hpp` / `snap_probe.cpp` 并用 ANSYS `ARCLEN` 完成
  1686 项黄金值对标；第十七次把同一内核以 `STABILITY=1` opt-in 接入
  `cable_solver.exe` 主程序。`fem_3d_nl.hpp::solveModel3DNonlinear`
  **仍是荷载控制 Newton**、仍是 `Ftarget = lam·Fext`，`NonlinearOptions3D`
  默认值一字未动；`[BUCKLING]` ERROR 段仍无条件写出。

### 2.2 缺什么（第十九次 ② 后的再收窄）

**已补齐**：球面弧长法的路径跟踪控制、λ 作为未知量的增广系统与
Crisfield 球面约束方程、越过极值点的下降段、LU 对角量级比
（含约束自由度置 1，**不是奇异判据**，与 CB3D17 同源）、
**独立解析锚点**（von Mises 两杆浅桁架闭式 `P(v)` 极值点 + ANSYS
`ARCLEN` 对标）、逐平衡点过程留痕与 `ERROR=` 双函数。

**第十八次（CB3D19）新增、但只在独立探针层**：CR 梁后屈曲基准题
（Williams toggle 型刚接框架）已用 `frame_probe.exe` 把同一球面弧长法
与共转 CR 梁组合，与 ANSYS `BEAM188 + NLGEOM + ARCLEN` 端到端对标，
第 21 项 CTest 冻结 12513 项黄金值；独立锚点改用初始切线刚度 `k0`
（三套线性刚架刚度实现对拍，梁框架没有桁架 `P(v)` 闭式解）。
**它仍不是** `cable_solver.exe` 成桥态闭环 / 施工阶段链路。

**第十九次 ①（a 阶段，2026-11-15）新增、且已进主链路**：
`STABASIS=COMPLETION` 让 `cable_solver.exe` **不提前 return**，先走完
找形 / 索长迭代 / 内层 Newton 得到成桥态收敛位移 `u0` 与最终 `m`，
再以 `R(u,λ) = F0 + λ·Finc − Fint(u)`、`u(0)=u0` 做球面弧长路径跟踪，
`[STABILITY] BASIS=completion_state` 挂在主链路结果文件 `[END]` 前；
终止判据改成**相对 `u0` 的控制位移增量**，段内 `CTRL0` 给出起点绝对
位移，`[U]` / `[NEWTON]` / `[LENGTH]` / `[BUCKLING]` 段一律保留。
`Finc` 由新段 `*STABILITY_LOAD` 提供、不进 `m.loads`，故成桥态恒载
`F0` 不会被增量荷载污染。第 22 项 CTest 用 V 形双索单自由度体系，
其 `N(v)=EA(L(v)−L0)/L0`、`P(v)=2N(v)(H+v)/L(v)`、
`λ(v)=[P(v)−F0]/Pinc` 是**不读求解器的闭式解**，逐点对拍
`MODE=STATIC`（显式 `L0`）与 `MODE=TENSION`（索长迭代命中 `T*`）
两种成桥态闭环，并验证 `CTRL0 ≡ [U]` 与默认零污染。

**第十九次 ②（a 阶段，2026-11-15）新增、且已与商业软件对拍**：
`ansys_bridge_completion.py` / `run_cases_completion.py` 把
`STABASIS=COMPLETION` 放到真实双塔三跨斜拉桥（CB3D21，50 节点 /
63 单元 / 16 索），同一份 `ModelSpec3D(mode=STATIC, 设计线形)` 同时
生成 FEM `.dat` 与 ANSYS 两段 `.inp`；16 根索 `L0 = L_design/(1+EPS0)`
与 `INISTATE eps ≡ EPS0` 严格同源。ANSYS step1 `TIME=1` 求成桥态，
step2 荷载控制主 job 用 `λ=(TIME−1)·LAMMAX` 对 FEM `[STABILITY]`
逐子步插值对拍（28 子步 `worst|ΔUZ|=1.85e-5 m`），`ARCLEN` job
只作终点交叉验证（`|d|=6.9e-8 m`）。第 23 项 CTest 冻结 2392 项
黄金值，`--live-ansys` 临时目录复跑两 job 的 CSV `max|d|≤1e-11`。
起点 `RES0=7.97e-3 N` 与 `[NEWTON] RESLAST=5.96e-3 N` 同量级。
**这不是**参考构形单段 `ARCLEN`：两段求解的 step1 必须真实建成桥态，
不得拿参考构形 `ARCLEN` 冒充。

**仍缺**（见 §2.5 / §2.6）：位移控制 / Riks / 分支屈曲与分支切换、初始缺陷
形态与幅值 / 缺陷敏感性、材料非线性本构、**CR 梁后屈曲接进主链路
成桥态闭环与施工阶段链路**、施工阶段链路接入、
**`MODE=COMPLETION` 索长迭代改写 `L0` 导致 `u0` 与 `m` 不同源的修复**
（② 用 `MODE=STATIC` + 显式 `L0` 绕开，CB3D10 实测 `RES0=3.15 MN`，
属独立后续项）、**越过极限点 / 下降段的真实桥梁算例**
（CB3D21 路径单调硬化、无极限点；该能力仍只有 L / N 阶段独立基准题
与 FEM 球面弧长内核）。
这些仍是实质算法新增或对标新增，不是把已有段改名。

### 2.3 两阶段规格（第十七次已实现，2026-11-14）

第十七次按本节原两阶段规格落地，**只做 opt-in，默认全关**：

1. **内核（FE）**：`*CABLE_BRIDGE` 增 `STABILITY=1` / `PATHMETHOD=SPHERE` /
   `CTRLNODE=` / `CTRLDOF=` / `CTRLTARGET=` / `DS0=` / `DSMIN=` / `DSMAX=` /
   `PSI=` / `MAXPATHSTEP=` / `STABITER=` / `STABTOL=` / `STRAIN=` 共 13 个键，
   全部默认关闭（`STABILITY=0`）。**`STABTOL` / `STABITER` 必须带 `STAB`
   前缀**：`*CABLE_BRIDGE` 既有 `TOL`（几何闭合容差 1e-8）与 `MAXITER`
   （外层迭代上限 20）已被成桥态闭环占用，直接复用同名键会悄悄改掉
   既有算例的默认参数，违反「默认逐位不变」。开启时 `cable_solver.exe` 在
   `readModel3D` 的**参考构形**上分流，调用
   `stability_path_3d.hpp::solveArcLengthPath3D`（L 阶段已用
   ANSYS `ARCLEN` 对标过的球面弧长法），结果写 `<stem>_stability.txt`，
   `[STABILITY]` 段置于 `[END]` 前。段内逐平衡点写
   `I STEP= LAMBDA= CTRL= RES= ITER= DS= PIVOT= CONVERGED=`，
   汇总行写 `KIND/STATUS/BASIS/NSTEP/CONVERGED/…`；失败走
   `stabilityErrorSection3D` 的 `ERROR=<code>` 双函数
   （`zero_reference_load` / `empty_dof_map` / `invalid_control_dof` /
   `arc_length_step_collapsed` / `max_steps_reached` / `solver_exception` /
   `unsupported_path_method` / `no_equilibrium_point`，共 8 类内核码；
   BS 侧另有 `no_stability_section` 表示根本没这段）。
   新段**不改** `NonlinearOptions3D` 默认值与
   `cable_solver.cpp` 成桥态默认参数，故 `[NEWTON]` 与全部既有非线性算例
   不受影响。**`[BUCKLING] ERROR=nonlinear_state_not_supported` 原样保留**：
   线性屈曲对共转成桥态不适用是独立的物理结论，spec 原文要求「实现并验证前
   必须保留」，实现后仍不删除，避免把两种 λ 混为一谈。
2. **BridgeStudio**：新增唯一解析器 `stability_3d.py`（仿 `buckling_3d.py`，
   **只解析不重算** λ / 路径 / 极值点）、`ERROR_TEXT` 中文说明、能力矩阵行与
   独立守卫 `verify_stability_3d.py`；未产出时章节照出但写中文「做不了」，
   **不摆空表、不补 0**。

### 2.4 禁止与验收断言（第十七次逐条落实）

- 禁止（继续生效）：拿线弹性 λ 冒充全过程极值点；给「规范稳定安全系数合格
  限值」编数；编造初始缺陷幅值 / 缺陷敏感性系数；把过程量写成合格判定；
  删掉 §2.1 的如实边界说明。**不拿线弹性 λ 冒充**仍是 `[BUCKLING]` 与
  `[STABILITY]` 两章共同口径。
- 验收逐条：① `STABILITY=0` 时 12 格默认 `.dat` 黄金哈希、37 个
  `dat_byte_regression` 用例与全部既有 `_cable.txt` 逐位不变（第 20 项
  CTest 有「默认零污染」断言）；② 每个收敛平衡点残差
  `|R| ≤ TOL·|Fref|`，段内逐点写 `RES=` 由 `compare_stable_path_3d.py`
  判定；③ 独立锚点用 von Mises 两杆浅桁架闭式
  `L(v)=√(B²+(H−v)²)`、`P(v)=−2N(H−v)/L`，极值点由黄金分割求
  `dP/dv=0`，阈值与推导写进 `benchmark/stable_path_3d/README.md`，
  且**与 L 阶段 `snap_probe` / ANSYS `ARCLEN` 同一组解析值对拍**；
  ④ 缺参数 / 未收敛 / 未产出写 `ERROR=`，负例进 CTest；
  ⑤ `[BUCKLING]` ERROR 段仍在 `cable_solver.cpp` 与默认输出中；
  ⑥ BS 报告章与 `[STABILITY]` 段同源，λ 与全过程极值点分列；
  ⑦ 独立锚点是解析闭式而非只加黄金基线（第 20 项 CTest + `test_3d_nl.cpp`
  原有 CR 锚点）；⑧ 能力矩阵该行**标 `P` 不标 `Y`**，理由见 §2.5。

### 2.5 如实边界（B3 已于 2026-11-16 第二十二次收口为 `Y`；以下为各阶段历史边界，保留不删）

- `STABILITY=1` 是 `cable_solver.exe` 的**独立 opt-in 模式**，在参考构形上
  跟踪平衡路径；**不等于**成桥态闭环 / 索长迭代 / 施工阶段链路已具备
  全过程稳定分析。既有索承体系闭环默认路径一字未动。
- 默认实现**球面弧长法**（`PATHMETHOD=SPHERE`），载荷控制仍由
  `fem_3d_nl.hpp::solveModel3DNonlinear` 承担。**第二十次补记**：
  `PATHMETHOD=RIKS`（Wempner–Riks 正交轨迹，每步校正增量与上一步
  收敛增量正交，`dλ` 由线性方程唯一确定）已在 `STABASIS=STAGE`
  入口 opt-in，并由第 27 项 CTest 以闭式 `λ(v)` + SPHERE/RIKS
  双方法交叉闭环；REFERENCE / COMPLETION 入口仍拒绝 RIKS，
  与 ANSYS `ARCLEN`（球面型）不同源。位移控制、线搜索、
  分支屈曲（bifurcation）与分支切换、初始缺陷形态 / 幅值 /
  缺陷敏感性、材料非线性本构、动力 snap-through 均**未覆盖**。
- 内核默认工程应变 `N=EA(L−L0)/L0`，`STRAIN=1` 才切对数应变
  `N=EA·ln(L/L0)`（与 ANSYS `LINK180 KEYOPT(2)=1` 同口径）；两者都是
  共转 + 解析一致切线，**不等于**材料非线性。
- λ 仍是参考荷载放大倍数，**不是规范稳定安全系数**；JTG 3362 无 λ 合格
  限值，§2.4 禁止给它编数。极限点处的
  `One indefinite tangent matrix is detected` 是物理标志不是求解失败。
- **第十八次补记（CB3D19）**：CR 梁后屈曲基准题已从「未对标」推进为
  「**独立探针层已对标**」（`frame_probe.exe` + `BEAM188/ARCLEN` +
  12513 项黄金值 + 第 21 项 CTest）。但该探针走 `*.frame` 参数文件，
  不经 `readModel3D()` 成桥态闭环，`cable_solver.cpp` /
  `fem_3d_nl.hpp` / `stability_path_3d.hpp` 一字未改；B3 的能力判据
  是「主链路成桥态闭环 / 施工阶段链路已具备全过程稳定分析」，
  本轮没有触及，结论不变。
- **第十九次 ① 补记（`completion_state` 基准）**：`STABASIS=COMPLETION`
  已把路径起点换到成桥态，但这是**新的独立 opt-in 分支**，默认
  `STABASIS=REFERENCE`（第十七次口径）与 `STABILITY=0` 的输出逐位不变；
  它只覆盖 `cable_solver.exe` 的 `MODE=STATIC` / `TENSION` 成桥态闭环在
  **FEM 自洽 + 独立闭式锚点**层面的正确性，没有 ANSYS 真实桥梁两段对标、
  没有施工阶段链路、没有 CR 梁后屈曲的主链路版本。`BASIS=completion_state`
  与 `BASIS=reference_configuration` 必须在段里分列，解析器不得把两者
  混成同一个口径（新守卫 `verify_completion_stability_3d.py` 锁住）。
- `P ≠ Y` 的结论：主链路默认关闭、`REFERENCE` 基准只有轴向 / CR 杆系
  基准题有独立解析锚点、`COMPLETION` 基准（第十九次 ①）只有 FEM 自洽
  与单自由度闭式解析锚点、Williams toggle 型 CR 梁后屈曲**只在独立探针
  层对标**、施工阶段链路仍未闭环。
  **第十九次 ② 补记**：真实斜拉桥的 ANSYS 两段对标（CB3D21 /
  第 23 项 CTest）已落地，但它是**单调硬化路径、无极限点**，
  且 `MODE=COMPLETION` 起点不同源缺陷未修；分支屈曲 / 初始缺陷 /
  材料非线性 / 施工阶段链路仍未覆盖。
  把 `P` 涂成 `Y` 需要按 §2.6 三阶段全部走完；① / ② 完成**不等于**
  `Y`，③ 走完前不得把 `P` 涂成 `Y`。
- **第十九次 ② 补记（真实桥梁两段对标）**：`ansys_bridge_completion.py`
  以 `MODE=STATIC` + 设计线形 + 显式 `L0` 为唯一数据源，FEM `.dat`
  与 ANSYS `.inp` 共用 16 根索 `L0`，
  `INISTATE,DEFINE,...,EPEL,X,eps ≡ EPS0`（偏差 `1.83e-16`）；
  两个 ANSYS job 都恰有 2 个 `SOLVE`（step1 成桥态恒载 + step2
  增量荷载）、2 条 `F,1016,FZ`、16 条 `INISTATE`。
  **不用反力求和**（50 个 `F` / 34 个约束节点上 `ΣRF,FZ` 随加载
  反而下降），主 job 用 `λ=(TIME−1)·LAMMAX`，`ARCLEN` job 的
  `TIME` 不是 λ、只比终点。`BASIS=completion_state` 与
  `BASIS=reference_configuration` 继续分列，不得拿参考构形单段
  `ARCLEN` 冒充成桥态两段。② 完成**仍 `P` 不 `Y`**。
- **第十九次 ③a 补记（起点同源 / 施工阶段共存 / 越过极值点）**：
  ② 登记的 `MODE=COMPLETION` 起点不同源缺陷已实质修复——
  `fem_3d_nl.hpp` 的 `CableLengthResult3D` / `ErectionGeometryResult3D` /
  `CompletionStateResult3D` 增加 `Model3D basisModel`，
  `cable_solver.cpp` 只在 `STABASIS=COMPLETION`（opt-in）且
  `MODE=TENSION/COMPLETION` 时把与 `u0` 同源的最终模型交给
  `[STABILITY]` 装配，并加自由度结构一致性检查；CB3D10 `RES0`
  由 `3.1492e6 N` 降到 `7.4001e-3 N`、首步 λ 由负转正
  （第 24 项 CTest，110 项检查）。施工阶段链路核对结论是
  CB3D13 上 `[STABILITY]` 与 5 个 `[ERECTION*]` 段**并列输出、互不污染**，
  `[STABILITY]` 起点仍是成桥态而非某个施工阶段中间态。
  ③a 另补 CB3D22 真实斜拉桥**越过极值点 / 下降段**算例
  （`ansys_bridge_limit_point.py` / `cases/run_cases_limit_point.py` /
  第 25 项 CTest，140 项检查）：FEM `λ_max=1.624941`、下降段 207 步，
  ANSYS `ARCLEN λ_max=1.622748`，两 job 0 error。
  **上述三项都是成桥态起点的路径跟踪能力，不改变 §2.5 其余边界**：
  位移控制 / Riks、分支屈曲与分支切换、初始缺陷形态 / 幅值 / 敏感性、
  材料非线性本构、动力 snap-through、施工阶段中间态全过程稳定
  **仍未覆盖**；CB3D22 的细塔是稳定敏感研究构型，不是实际设计尺寸。
  故 ③a 完成**仍 `P` 不 `Y`**。
- **第二十次 ③b 补记（施工阶段中间态起点 opt-in）**：③a 登记的
  「施工阶段中间态全过程稳定未覆盖」已推进为「**主链路有一个
  opt-in 研究构型算例**」——`fem_3d_nl.hpp::NLStageResult3D` 增加
  `Model3D model` 并在 `solveModel3DStagesNonlinear()` 每阶段写
  `row.model = mc`，`cable_solver.cpp` 新增 `STABASIS=STAGE` +
  `STAGEID=<id>` 独立 opt-in，以指定阶段的共转列式全量平衡态
  `u_stage` 为弧长路径起点，`F0` 为该阶段累计恒定荷载、`Finc`
  仍只来自 `*STABILITY_LOAD`；`[STAGES]` / `STAGE=` /
  `BASIS=stage_state` 与另两种基准分列，未知阶段写
  `ERROR=stage_not_found` 不降级。CB3D23（4 节点 / 3 杆 `TRUSS`
  浅拱，3 个施工阶段、取阶段 2）与 ANSYS 18.0 两段求解对拍：
  `STAGE=2 NELEM=3 NDOF=12`、`RES0/|F0|=6.34e-13`、
  FEM `NPOINT=134` / `λ_end=2.57577583`、两 job 0 error、
  按 `|ΔUY|` 插值 `λ` 的 `worst|Δλ|≈0.0051`；第 26 项 CTest
  214 项检查。两侧同源加 `UX` 导向约束消除一阶运动机构，轴向
  应变取约 `2e-4` 避开 `LINK180` 默认 Almansi 应变与 FEM 共转
  工程应变的系统性差异。**如实边界**：`STAGE` 仍是独立 opt-in
  （默认 `REFERENCE` / `STABILITY=0` 输出逐位不变）；CB3D23 是
  研究构型，**不是真实桥梁施工阶段**，不覆盖 CR 梁 / 索的真实
  施工工序；位移控制 / Riks、分支屈曲与分支切换、初始缺陷、
  材料非线性本构、动力 snap-through **仍未覆盖**。因此 §2.6 的
  「确认 §2.5 全部边界收口」未满足，③b 完成**仍 `P` 不 `Y`**，
  不得靠一个 opt-in 算例提前打勾。

### 2.6 a 阶段三阶段验收口径（什么才算把 B3 推成 `Y`）

用户 2026-11-15 选定 **a1**：按三阶段逐阶段交付，每阶段结束都只写
「本阶段做了什么、还差什么」，不得靠改文档提前打勾。

| 阶段 | 交付 | 结束时 B3 |
|---|---|---|
| ① | 规格/守卫先行 + `stability_path_3d.hpp` 支持非零初态 / 恒定荷载 / 增量荷载（默认零污染）+ `cable_solver.exe` `STABASIS=COMPLETION` opt-in 与 `*STABILITY_LOAD` + 第 22 项 CTest（FEM 自洽 / 独立闭式 `λ(v)` 锚点 / 默认零污染） | **`P`（不变）** |
| ② | `BridgeStudio` 同源导出 + 真实斜拉 / 悬索桥算例 + ANSYS「成桥态等效初应变 + 两段求解」对标，冻结黄金值并接入第 23 项 CTest | **`P`（不变）** |
| ③ | 施工阶段链路接入核对、文档 / 能力矩阵 / 三守卫全量复核、干净构建全量回归，确认 §2.5 全部边界收口后才把 `P` 改 `Y` | **`Y`** |

四条硬口径：① `STABASIS=COMPLETION` 必须**先走完成桥态闭环**
（找形 / 索长迭代 / `[NEWTON]`），不得拿参考构形 `u=0` 冒充 `u0`；
② `F0` 必须是成桥态恒定荷载、`Finc` 必须是 `*STABILITY_LOAD` 的
增量荷载，二者不得合并成「一个 λ 放大全部荷载」；
③ `BASIS=completion_state` 的 λ 是**增量荷载**放大倍数，与
`BASIS=reference_configuration` 的 λ（总参考荷载放大倍数）**不是同一个
量**，报告与计算书必须分列、不得互相冒充；
④ ANSYS 对标必须是「先建成桥态、再在成桥态上做 `ARCLEN`」的两段口径，
单段 `ARCLEN` 只对应参考构形基准，不能拿来验收 `completion_state`。

**② 阶段实测收口（2026-11-15）**：CB3D21 交付第 23 项 CTest，
`RES0/|F0| = 2.696e-9`、`CTRL0 ≡ [U](1016,3)`（差 `3.06e-14`）、
16 根索 `L0*(1+EPS0) ≡ 设计弦长`（偏差 `0`）、`INISTATE eps`
偏差 `1.83e-16`、28 子步 `worst|ΔUZ| = 1.85e-5 m`、
`ARCLEN` 与荷载控制终点 `|d| = 6.9e-8 m`、两个 ANSYS job
均 0 error、`--live-ansys` 复跑 `max|d| ≤ 1e-11`。四条硬口径
全部满足，但 ③ 的施工阶段链路核对与 §2.5 全部边界收口
**尚未完成**，故 B3 仍 `P` 不 `Y`。② 发现的 `MODE=COMPLETION`
起点不同源缺陷登记为独立后续项，不得因 ② 完成而略过。

**③a 阶段实测收口（2026-11-15，用户选 `3a`）**：先做实质技术收口，
再评估是否具备推 `Y` 条件。三件交付：

| 项 | 交付物 | 实测 |
|---|---|---|
| 起点同源修复 | `fem_3d_nl.hpp` 三个结果结构 `basisModel` + `cable_solver.cpp` 同源模型分流 + 自由度一致性检查 + 第 24 项 CTest | CB3D10 `RES0/|F0|` 从 `1.06` 降到 `2.502e-9`；`MODE=COMPLETION` 与 `MODE=STATIC` 两条独立闭环逐点 `worst|dλ|=1.081e-6`；110 项检查 |
| 施工阶段链路核对 | 第 24 项 CTest 的 CB3D13 case | 5 个 `[ERECTION*]` 段逐位一致、`RES0/|F0|=1.112e-9`、26 个平衡点；结论是**共存**，不是每阶段中间态可分析 |
| 真实桥梁越过极限点 | `ansys_bridge_limit_point.py` / `run_cases_limit_point.py` / BS 算例 `CB3D22_limit_point_vs_ansys` / 第 25 项 CTest | FEM `λ_max=1.624941 @ dUX=0.200411`、下降段 207 步；ANSYS `λ_max=1.622748 @ dUX=0.201182`；极值点 `dλ=1.349e-3`、`dUX=3.850e-3`；140 项检查 |

全量回归：干净 Ninja 构建 `_d_build` 显式锁定
`mingw64/bin/g++.exe`（与冻结证据同工具链），
`ctest` **25/25**、`run_tests.ps1 -BuildDir _d_build` `ALL TESTS PASSED`。
③a 期间发现并登记：历史冻结证据用 mingw64、③a 新证据最初用
`ucrt64` 编译，两套 CRT 的末位浮点不同；第 24 项冻结证据已用规范
工具链重新生成，第 25 项改用 `tests/benchmark/replay_tolerance.py`
的结构化跨工具链比较。

**B3 结论：仍 `P` 不 `Y`**。§2.6 规定 ③ 必须「确认 §2.5 全部边界
收口后才把 `P` 改 `Y`」，而 §2.5 列明的位移控制 / Riks、分支屈曲与
分支切换、初始缺陷、材料非线性、动力 snap-through、**施工阶段中间态
全过程稳定**，③a 一项也未实现；③a 只收口了「成桥态起点同源」
「施工阶段段共存」「真实桥梁越过极值点」三处。故 ③a 完成
**不等于 ③ 完成**，能力矩阵该行继续 `P`。要推 `Y` 只有两条路：
补齐上述力学，或由用户重新拍板修改 §2.5 / §2.6 的验收口径；
**不得靠改文档提前打勾**。

**③b 阶段实测收口（2026-11-15，用户 `按建议做`）**：按上一条的两条路
之外，先把 §2.5 中「施工阶段中间态全过程稳定」这一项从「完全未覆盖」
推进到「主链路有 opt-in 研究构型算例」，其余边界不动。

| 项 | 交付物 | 实测 |
|---|---|---|
| 同源阶段模型 | `fem_3d_nl.hpp::NLStageResult3D::model`（每阶段 `row.model = mc`）+ `stability_output_3d.hpp::StabilityStateHeader3D` 的 `stageId/stageElems/stageDofs` | 阶段 1 `NELEM=2` → 阶段 2 `NELEM=3 NDOF=12`，`[STAGES]` 3 阶段全收敛 |
| 主链路 opt-in | `cable_solver.cpp::runStageStabilityMode()` + `STABASIS=STAGE` / `STAGEID` | `BASIS=stage_state`、`RES0/|F0|=6.34e-13`、`CTRL0=-0.000952173872` 非零、负例 `ERROR=stage_not_found` 不降级 |
| 同源 ANSYS 对标 | `ansys_stage_stability.py` / `cases/run_cases_stage_state.py` / `CB3D23_stage_state_vs_ansys/`（15 份证据） | FEM `NPOINT=134` / `λ_end=2.57577583`；`st_load` 27 点 / `st_arc` 25 点，两 job 0 error，按 `|ΔUY|` 插值 λ 的 `worst|Δλ|≈0.0051`、共同区间覆盖 99.7%；BS 报告 72 checks / 0 failed |
| 第 26 项 CTest | `tests/benchmark/stage_stability_3d/` + `CMakeLists.txt` + `run_tests.ps1` | 214 项检查，5 层判据，当前二进制重放两个 `.dat` 逐字节一致；干净构建 `ctest` **26/26**、`run_tests.ps1 -BuildDir _d_build` `ALL TESTS PASSED` |
| 第 27 项 CTest（第二十次 Riks 闭环） | `tests/benchmark/riks_stage_stability_3d/` + `CMakeLists.txt` + `run_tests.ps1` | 63 项守卫 + 3109 checks 黄金值 + 20 criteria；闭式逐点 `worst|Δλ|=4.6e-14`、SPHERE/RIKS 双方法 `1.47e-4`、CB3D23 终点与第 26 项锚点一致；8 份冻结证据逐字节重放；干净构建 `ctest` **27/27** |
| 依赖补齐 / 守卫适配 / UI 兼容 | `anastruct 1.7.1` / `python-docx 1.2.0` / `pypdf 7.1.0`（清华镜像）；`verify_app.py` 的 `_ssget()` 等价适配 Streamlit 1.45.1 `SafeSessionState`；`verify_opt_2d.py` 过时位置断言改为「小节仍完整在档」；`ui_3d.py` 外层嵌套 `expander` 改 `container` | 53 个 BS 守卫由 44/53 提升到 **53/53 全绿**；4 个 ③b 关键守卫 100/100、185/185、282/282、30/30；`verify_app.py` 109/109。后两项均为**业务 UI / 测试代码与新版 Streamlit 的兼容性适配**，与 ③b 力学无关、不涉及任何功能判据或 B3 结论 |

**③b 完成后的 B3 结论：仍 `P` 不 `Y`**。理由：① `STAGE` 是独立
opt-in，默认 `REFERENCE` / `STABILITY=0` 输出逐位不变；② CB3D23
是 4 节点 3 杆 `TRUSS` 浅拱研究构型，两侧同源加 `UX` 导向约束、
轴向应变约 `2e-4`，**不是真实桥梁施工阶段**，也不覆盖 CR 梁 / 索
的真实施工工序；③ §2.5 的位移控制、分支屈曲与分支切换、初始缺陷、
材料非线性、动力 snap-through 五项**仍未实现**（Riks 部分已由
第二十次第 27 项 CTest 收口，但仍是独立 opt-in 研究构型，不改本结论）。
按 §2.6 的「确认 §2.5 全部边界收口」口径，③ 仍未走完，
能力矩阵该行继续 `P`。**不得靠一个 opt-in 算例提前打勾**。

**第二十一次分阶段推进（2026-11-16，用户「继续补 B3 五项力学」）**：用户
明确要求补齐 §2.5 列明尚未覆盖的五项力学，按「逐项 opt-in、默认零污染、
每项独立锚点 + 负例守卫 + 新 CTest、逐阶段仍 `P`」推进，次序与口径：

| 力学 | 内核挂点（均在 opt-in 分支，默认逐位不变） | 验收深度 |
|---|---|---|
| ① 位移控制 / 线搜索 | `stability_path_3d.hpp` 增控制模式（控制 DOF 位移增量驱动、λ 为未知量）+ Armijo 回溯（移植 2D `fem_2d.cpp` 口径） | 闭式 `λ(v)` 锚点 + SPHERE 交叉 + 默认零污染 + 负例守卫 |
| ② 初始缺陷形态 / 幅值 / 敏感性 | 由线性屈曲模态（独立 `computeBucklingModeShape3D`，不改只返回 factors 的 `computeBucklingResult3D`）生成缺陷场，按幅值 / 模态号 opt-in 施加 | **已落地（任务 #161）**：缺陷敏感性趋势锚点（幅值→λ_max 单调 / 对称破缺）+ 独立闭式 `λ_cr` / `P(v)` + 负例 + 默认零污染；第 29 项 CTest 35/35，仍 `P` |
| ③ 材料非线性本构 | 把 `section.hpp` 的 `materialStress` / `sectionResponse` 接进 `fem_3d_nl.hpp::crElementInternal` 纤维积分；`Element3D` opt-in `matKind` + `.dat` `*R MATKIND/MATFY/MATFT`，Gauss 纤维截面（弹性段与 Kl12 逐位对齐） | **已落地（任务 #162）**：单轴应力-应力平台 + 矩形塑性铰逐阶段屈服 `My<M3<Mp` + 混凝土受拉开裂退出 + 弹性段纤维 vs Kl12 逐位对齐 + 独立 Python（numpy Gauss）锚点 + 负例 + 默认零污染；第 30 项 CTest 491/491，仍 `P`（`ctest` 30/30） |
| ④ 分支屈曲与分支切换 | `stability_path_3d.hpp::ArcLengthOptions3D` 新增 `bifurcate/bifPivot/bifMode/bifAmp/bifEigTol`（默认全关）；用已逐点输出但此前未使用的 `pivotRatio` 做轻量入口、当前一致切线自由子块最小特征值 `\|μ_min\|/max\|diag(Kff)\|<1e-3` 确认临界点，沿 `computeBucklingModeShape3D` 模态施加位移扰动切换分支 | **已落地（任务 #163）**：高拱 von Mises 反对称分岔 `L³=L0·h²` 独立闭式锚点（先于对称极值点 `L³=B²·L0`）+ FEM 切换点 `v=0.44630/λ=25.9567` 对齐闭式 `0.44735/26.0152` + 分支承载力降 68% / 模态幅值 0.44→1.50 / 对称破缺 + 对称模态 / 零幅值 / 模态越界三类负例 + 默认零污染；第 31 项 CTest 42/42，仍 `P`（`ctest` 31/31） |
| ⑤ 动力 snap-through | **独立新模块** `dynamic_newmark_3d.hpp` + `dyn_probe.cpp`（不改静力链）：一致质量复用 `assembleMassMatrix3D` / 行和集中质量、Newmark-β 隐式积分 + 每步 Newton、Rayleigh 阻尼、阶跃 / 爬升 / 半正弦荷载 | **已落地（任务 #164）**：浅拱 von Mises `P=0.85·P_cr`（低于静力极限）阶跃下 `v_max≈1.361`、`t_snap≈0.0039 s`；一致/集中质量闭式 `m=2·rhoA·L0/3` / `rhoA·L0` 各自对齐独立 SDOF RK4（步长 1/5，位移 <2e-3 m、速度 <5 m/s）+ 荷载时程 / 阻尼 / 质量 / dt 加密 / 能量外功 + 零质量 / 零步长 / 非法 β / `DYN=0` 负例 + 默认零污染；第 32 项 CTest 88/88，仍 `P`（`ctest` 32/32） |

四条硬口径沿用 §2.6：① 每项都必须 opt-in、默认关闭、`STABILITY=0` / 无新键时
既有输出逐位不变（27 项 CTest 冻结不得漂移）；② 每项都必须有**独立解析 /
闭式锚点**，不得只用「程序跑通」当验收；③ 每项都必须有负例守卫（非法参数
不静默）；④ 五项全部收口且干净构建全量回归后才把能力矩阵该行 `P→Y`，
任何单项完成都**不提前打勾**。任务 #159–#166 逐项跟踪，实测收口写入 §6。

## 3. B4 预应力损失两处规范口径

### 3.1 σl2 曲线筋反摩擦（附录 G）

**现状**：`prestress_loss.hpp:84` 无条件 `σl2 = ΣΔl/l·Ep`，连「是否曲线筋」的判断都没有；22 个键里没有矢高 / 曲率 / 反摩擦影响长度 / 反向摩擦系数字段。`USER_MANUAL.md:241`、`input_format.md:295-296`、`input_format_3d.md:257-259`、`model_spec_3d.py:375-376`、`report_3d.py:2053-2055` 都是**如实能力边界，不得删**。

**条文依据**（本轮查到规范全文，不是自造公式）：JTG 3362-2018 附录 G「预应力曲线钢筋由锚具变形、钢筋回缩和接缝压缩引起的考虑反向摩擦后的预应力损失简化计算」，条文 G.0.1 / G.0.2-1~5 / 表 G.0.2；全文见 <https://jtg-3362.readthedocs.io/zh/latest/FLG.html>（条文说明 <https://jtg-3362.readthedocs.io/zh/latest/twsm/tFLG.html>），另有贵州省《桥梁锚下预应力检测技术规程》附录 C.2.3 同款公式佐证。

- G.0.1：后张法受弯构件应计算反向摩擦后的损失；反向摩擦系数可假定与正向相同。
- G.0.2-2：`Δσd = (σcon − σl)/l`，σl 为扣除沿途摩擦损失后的锚固端应力，即 `Δσd = σl1(l)/l`。
- G.0.2-1：`lf = sqrt(ΣΔl·Ep / Δσd)`（规范单位：ΣΔl 与 l 用 mm、Ep 与应力用 MPa、Δσd 为 MPa/mm；实现必须与本内核 SI 单位口径换算一致，不得混用）。
- `lf ≤ l`：G.0.2-4 `Δσ = 2·Δσd·lf`，G.0.2-3 `σl2(x) = Δσ·(lf−x)/lf`；`x ≥ lf` 处不受回缩影响。
- `lf > l`：G.0.2-5 `σl2′(x′) = Δσ′ − 2x′Δσd`，其中 `Δσ′` 由「等腰梯形面积 `A = ΣΔl·Ep`」试算求得（条文说明明确给出积分协调式 `ΣΔl = Δσd·lf²/Ep`）。

**待用户拍板的口径（本轮不实现的原因）**：附录 G 给的是 σl2 **沿程分布**，而本内核 `σpe = σcon − σl1 − σl2 − σl4` 与 `Pe = P·(σpe/σcon)` 需要一个**代表值**：取张拉端最不利 `x=0`、锚固端、还是跨中代表截面，规范没有统一规定，属设计口径选择。在用户选定前把某个 x 写死进内核，等于用程序口径替设计取值，违反「不静默兜底」。

**实现规格（口径选定后执行）**：`PrestressLossInput` 增 opt-in 开关与 `L2_X` / 曲线筋几何来源键（默认全关、22 键表行逐位不变）；`computePrestressLosses()` 分流：未开启走原式；开启但参数不全**显式 throw**，不回退直线式；2D / 3D 共用同一函数（唯一真源不变）；结果段只在新路径追加 `LF=` / `DSL2=` 等字段，旧行不动；`σl3/σl5/σl6 不进 σpe 与 Pe` 的口径不变。验收：直线筋旧算例逐位不变、`lf≤l` 与 `lf>l` 两支独立闭式解 oracle、单位换算对拍、缺参负例、2D/3D 同参同值、`stage prestress force` 6 位有效数字不变、报告表与 `SL2/SPE/RATIO/Pe` 同源。

### 3.2 σl6 收缩徐变随龄期自动算（附录 C）

**现状**：`EPS`/`PHI` 由用户按附录 C 查表输入（`prestress_loss.hpp:152-153`、`:99-100`）；`code_loads.hpp:32-82` 有 `jtg3362NominalCreep()` / `jtg3362ShrinkageNominal()` / 龄期发展公式，但**服务结构整体时变分析，与 σl6 的输入链完全不相交**；`:35` 与 `:57` 明确自认「表 C.2.2 的 32.4/fck 折减、表 C.1.2 的 C50 及以上折减是查表用的，这里不重复乘」，且 `βsc` 硬编码 5.0。

**不能自动算的理由**：附录 C 的**规范表格数据本身缺席**（表 C.1.2 水泥类型 βsc 与强度折减、表 C.2.2 的 32.4/fck 折减、RH / 理论厚度 h / fcm / 加载龄期 t0 / 干燥开始龄期 ts / 计算龄期 t）。若把 `code_loads.hpp` 的简化拟合 + 硬编码 βsc 直接灌进 `epsCs/phi`，等于拿非规范表值冒充规范值，同时违反「不凭空编数值」「不静默兜底」「结论与计算书同源」。
**表值缺席时不得自动算、不得凭空编数值**：程序只能显式拒绝或等用户按规范
查表给出，不存在「先拿拟合值算一个再说」的中间档。

**规格（只做防护，不做计算）**：列出必需输入清单，要求规范表值随模型给出（用户录入或经核对的表数据文件）；实现「开启自动算但缺任一表值 → 显式 throw，**绝不调用 `jtg3362NominalCreep()` 等公式值兜底**」；表值齐备后才允许把 φ(t,t0)、εcs(t,ts) 接到 `EPS/PHI`，并在结果段写明表值来源；σl6 仍不进 σpe / Pe。另记一处现存隐患（`EPS/PHI` 不给时 σl6 静默算 0）：任何实现必须区分「显式声明 σl6 不参与」与「缺值报错」两条路径，且**不得改变既有 22 键算例的输出**。验收：未开启时 22 键表行与 `SL1~SL6/SPE/RATIO` 逐位不变、开启缺表值显式拒绝、结果段不出现任何自动填入的 εcs/φ、报告引用同一组规范表值。

## 4. B5 成桥态「无应力曲率」

**现状**：`fem_3d_nl.hpp:1367-1368` 注明「成桥态梁内应有的弯矩由『无应力曲率』承担，属无应力状态法的后续扩展，不影响索长迭代本身」；`fem_3d.hpp:243 Element3D` 只有 `L0`，`fem_3d_nl.hpp:502 CRElemData` 也只有 `L0`；当前无应力状态法的两件交付物是 `L0`（下料索长）与 `Xm`（制造 / 安装线形，`:1596-1615`），靠**移动节点坐标**令参考构形无应力，没有单元级初始曲率 / 初应变。两侧文档对「无应力曲率」零匹配。

**影响环节**：成桥态梁 / 塔恒载弯矩与内力定解 → `[GIRDEREND]` 的 `My`（`cable_solver.cpp:88-92`）→ `[OPT]` 的 `MPEAK_FINAL` 与预抬高 / 制造线形的完备性；**不影响** `T(L0)` 求根、影响矩阵 `J=M·D` 与 L0 下料数值。

**规格（研究性扩展，本轮不实现）**：需要先有单元级初始曲率 / 初应变自由度、共转列式参考构形与切线的一致性、设计给定的取值依据（无应力曲率不是算出来的，是制造参数）、下料 / 施工实现，以及独立解析锚点。未实现期不得把「节点预抬高」写成「无应力曲率已实现」，也不得删除 §4 的如实边界。验收：单元初始曲率在零外荷载下自平衡、`L0` 与 `J` 不因引入该自由度而改变、成桥态 `My` 与解析解对拍、默认路径逐位不变。

### 4.1 本轮实现范围（第二十六次，FEM 自洽深度）

按用户 2026-11-16「先做 FEM 自洽深度」与 2026-11-17「先做 B5 然后一起收口」：只做**单元级初始曲率的 FEM 自洽**，**不接真实制造参数、不做 ANSYS 对拍**。

- **自由度载体**：`Element3D` 增 `k0y` / `k0z`（无应力曲率，1/m，默认 0 = 现行为）。输入侧只在 `*EN` 单元行接受 `K0Y=` / `K0Z=`，不进 `*R` 表、不改任何既有行；`K0Y/K0Z` 出现在非 `BEAM` 单元、或值非有限 → 显式 `throw`。
- **力学等价**：常曲率 κ0 的固端力 `f_eq = EI·κ0·[θ₁:−1, θ₂:+1]`（局部），经 `Tᵀ` 转全局后并入 `elementEquivalentLoad3D()`。该函数同时供「组装 `a.F0`」与「端内力 `fl − T·fe`」使用，因此**一处接入两端自洽**：平衡方程含初曲率荷载，端内力自动变成 `M = EI(κ − κ0)`。曲率模态用 `Eeff = E/(1+χφ)`（与刚度一致），保证零外荷载时 `K·u0 = f_eq` 精确成立。
- **解析锚点**：悬臂自由端 `u0 = (v₂ = κ0L²/2, θ₂ = κ0L)`、端内力恒零（自平衡）；两端固结 `U = 0`、截面弯矩 `M = −EI·κ0`（常数）；`κ0y` 侧同式，符号按 `θy = −dw/dx` 取反。
- **不变量**：几何不被改写（不移动节点），故 `L0`、`J`、`Xm` 与默认路径**逐位不变**；`κ0 = 0` 时 `fe` 恒零、`elementEquivalentLoad3D` 早退、旧 `.report.txt` / 冻结基线逐字节不变。
- **输出**：opt-in 独立文件 `<stem>.curvature.txt`（`example_3d_report --curvature-detail`，`[UNSTRESSED_CURVATURE]` 段，逐单元 `EID K0Y= K0Z= EIY= EIZ= M0Y= M0Z=`），默认不写、不污染既有解析器。
- **未覆盖（如实登记，不冒充）**：共转列式 `CRElemData` 的初始曲率（需在其局部变形里减 `d0`，风险高，另立条目）；真实制造参数（设计给定的 κ0 依据）；成桥态 `My` 的全桥 ANSYS 对拍；徐变折减下 κ0 与 `χφ` 的耦合口径；**3D 计算书章节接线**（`example_3d_grillage.exe` 走 `<stem>_summary.txt`、`solver_3d` 注册表改造面大，本轮 BS 侧只提供 `curvature_3d.py` 解析口径层与守卫，不假装已进计算书）。


## 5. 待决口径（非缺陷，本轮不动）

`[OPT]` 的 `STATUS=not_converged` 时仍可能 `APPLIED=1` 写回目标索力 T\*（`cable_opt_3d.hpp:640`、`cable_solver.cpp:1017`），代码写明是**有意行为**。若产品要求「未收敛一律不得采纳」，需改这段判定并同步 `verify_cable_opt_3d.py` 守卫、结果段口径与文档；这是口径选择而不是 bug，等用户明确后再动。

## 6. 守卫与验收

`verify_b_class_spec.py`（纯离线）逐条锁住：

1. 本文件 §0–§6 齐全，且明写「本轮未实现 B3/B4/B5 力学」「不新增求解键 / 关键字 / 结果段 / 数值路径」；
2. FE 源码依据对拍（CHANGELOG / `cable_solver.cpp` / `fem_3d_nl.hpp` / `prestress_loss.hpp` / `code_loads.hpp` / `example_modal.cpp`）与 BS 侧如实边界对拍（`buckling_3d.py` / `report_3d.py` / `model_spec_3d.py` / 开发手册），**禁止删边界说明**；
3. 附录 G 条文号与来源登记齐全，σl2 的「代表值口径待决」与 σl6 的「表值缺席不得自动算」两条禁止在位；
4. `[BUCKLING] ERROR=nonlinear_state_not_supported` 仍在 `cable_solver.cpp` 与 `input_format_3d.md`；`solver_3d._registry()` 仍 4 键；
5. 默认 `.dat` 逐位不变：12 格 `DEFAULT_DAT_SHA` 黄金哈希 + `PrestressLoss3D` 22 键表行不变；
6. B2 已接通部分不虚报：能力矩阵 §2 两格 `Y`、§4.2 收窄为 7 个、README 同步「7 项」、§4 仍保留组合体系与 MIDAS 两条真缺口；
7. 规格文档与能力矩阵 / README / 使用说明 / 开发手册的登记计数同步。
8. 第十次 `[PRESTRESSLOSS2D]` 明细闭环不虚报：矩阵该格 `2D=P` 不是 `Y`、
   `BASIS=2d_global` / 不设 `STAGE` 占位 / 缺束 `ERROR=` 在位、
   `verify_prestress_loss_2d.py` 汇总按布尔位统计、开发手册登记该守卫、
   B2 剩余项由 3 项如实收窄为 2 项。
9. 第十一次 `[NEWTON2D]` 只闭环内层 Newton 不虚报：矩阵该格 `2D=P` 不是 `Y`、
   `[LENGTH]` / `[OPT]` 两格仍 `2D=N`、写明「没有对应力学 / 不造占位」、
   §4.2 为 2 个 `2D=N` + 5 个 `2D=P`、`verify_newton_2d.py` 汇总按布尔位
   统计、开发手册登记该守卫、FE 内核判据 / 步长 / 线搜索 / 收敛零改动、
   B2 剩余仍 2 项但内容如实收窄（内层 Newton 已按 `P` 闭环 ≠ 三层全闭环）。
10. 第十二次 `RebarSpec2D` / `capacity_2d.py` 只闭环 Mu / Vu 不虚报：矩阵该格
   仍 `| P | Y |`、`§4.2` 仍 2 个 `2D=N` + 5 个 `2D=P`、**B2 剩余仍 2 项**、
   写明「压弯 / 轴压不做，不照搬 3D 的 5% / 1% 阈值」、效应只取 `ultimate`
   基本组合逐端最不利、双层 opt-in 默认全关、FE 内核与默认 `.dat` 零改动、
   `verify_capacity_2d.py` 汇总按布尔位统计、三份文档登记该守卫。
11. 第十三次 `[LENGTH2D]` 一层不虚报：矩阵索长格 `2D=P` 不是 `Y`、
    `[OPT]` 格仍 `2D=N`、§4.2 为 **1 个 `2D=N` + 6 个 `2D=P`**、
    **B2 剩余仍 2 项**（内容再收窄为「`[OPT]` 一层力学 + 多格式导出 /
    `.mct` 反向导入」）、写明「无垂度线性杆本构 / 闭式初值 / 中心有限差分
    Jacobian + LM 阻尼最小二乘 / `tension_rel` 唯一判据」与「不等于 3D 的
    垂度 / Ernst 等效模量 / 共转成桥态 / 闭式影响矩阵 `J=M·D` /
    `[OPT]`→`[LENGTH]`→`[NEWTON]` 三层迭代」、双层 opt-in 默认全关、
    默认 `.dat` / `.result` / 默认 HTML 逐位不变、37 个 `dat_byte_regression`
    黄金基线不刷新、`verify_length_2d.py` 汇总按布尔位统计、开发手册登记
    该守卫、`to_dat()` 一个字节不多写。
12. 第十四次 `[OPT2D]` 2D 平面索力优化不虚报：矩阵该格 `2D=P` 不是 `Y`、
    只 `MINMAXM` + 显式索力箱 `TMIN`/`TMAX`（`BND_SRC=USER`）、不做
    `MINENERGY`、不猜 3D 的 `1.5×RIGID`、无 SYSGUARD 三判据、不造占位；
    段内明写「不是 3D 的 `J=M·D` 闭式影响矩阵」、不等于 3D
    `[OPT]`→`[LENGTH]`→`[NEWTON]` 三层迭代；`APPLIED=1` 只表示建议
    `T*` 被采纳、`[OPT2D]` 不写回 `.dat` / `.result`、`Nfinal ≠ T*`；
    §4.2 为 **0 个 `2D=N` + 7 个 `2D=P`**、**B2 剩余 1 项**（仅剩被
    X20 契约锁死的多格式导出 / `.mct` 反向导入）；双层 opt-in 默认全关、
    默认 `.dat` / `.result` / 默认 HTML 逐位不变、37 个黄金基线不刷新、
    `verify_opt_2d.py` 守卫 FE 源码契约 / 独立 oracle / 零污染 / 四出口同源。
13. 第十五次 `export_2d_formats.py` 2D 平行链不虚报：矩阵两格 `2D=P`
    不是 `Y`；`plane_frame_2d/v1` 是平行 2D 中间表示、**不是 canonical dict**；
    3D 的 `to_canonical(model: ModelSpec3D)` 与三个写出器签名一字未动、
    3D 唯一转换点不经过；显式 `dimension="2D"` + 拒绝非平面
    （`errors` 写 `dimension_not_plane` 且 `spec=None`、绝不静默投影丢信息）；
    不写 z / 6dof / `IYY`·`IZZ`·`IXX`、缺项标「不适用」不补 0，z=0 与
    平面外 dof 固定等格式占位逐条进 `warnings`；2D 只有模块 API + CLI、
    **无 GUI 入口**；MIDAS 数值对拍本机无 MIDAS、仍未做；有损项（车道 /
    影响线 / 成桥态 / 施工阶段 / 自重 / 预应力 / β 角 / DBUSER / 分布荷载）
    只登记不还原；默认 `.dat` / 默认 `.result` / 默认 HTML 逐位不变、
    12 格 `DEFAULT_DAT_SHA` 与 FE 37 个黄金基线不刷新；FE 仓本轮零改动、
    零文档改动。独立守卫 `verify_2d_export_import.py`（264/264，
    覆盖契约正交 / 三格式 / 往返 oracle / 维度拒绝 / 零污染）；BS 侧闭环不等于 FE 内核已实现 3D 等价能力。
14. 第十七次 `[STABILITY]` 主链路 opt-in 不虚报：默认 `STABILITY=0` 时
    `cable_solver.cpp` 既有结果文件逐位不变；`fem_3d_nl.hpp` 仍是荷载
    控制 Newton、`NonlinearOptions3D` 默认值零改动；`[BUCKLING]`
    `ERROR=nonlinear_state_not_supported` 仍在源码与默认输出；
    `[STABILITY]` 段有 `ERROR=` 双函数与 6 类错误码、逐点残差判据、
    独立解析锚点（与 CB3D17 `snap_probe` / ANSYS `ARCLEN` 同源）；
    能力矩阵该行 `P` 不是 `Y`、§2.5 五条边界在位；
    `verify_stability_3d.py` 与第 20 项 CTest 守卫「只解析不重算 /
    缺段写中文做不了 / 不补 0 / 默认零污染」。
15. 第十八次 `frame_probe.exe` 独立 CR 梁后屈曲不虚报：
    `fem_3d_nl.hpp` / `stability_path_3d.hpp` / `cable_solver.cpp`
    零改动，力学只由新 CLI 复用内核；`.frame` 与 2 份 APDL 同源
    出自 `ansys_bridge_frame.py::FRAME_CFG`，第 21 项 CTest 有
    12513 项黄金值、三套独立线性刚度锚点 `k0`、`bad_noload`
    负例与 ANSYS 日志 `errors=0` 断言；极限点取第一个 λ 局部极大、
    判据区间 `0 ≤ v ≤ VMAX` 的理由写进
    `tests/benchmark/frame_postbuckling_3d/README.md`。
    能力矩阵该行**仍 `P` 不是 `Y`**、§2.2 / §2.5 明写
    「独立探针已对标 ≠ 成桥态闭环已接入」。
16. 第十九次 ① `STABASIS=COMPLETION` 不虚报：新增独立守卫
    `verify_completion_stability_3d.py` 逐条锁住「默认 `STABASIS=REFERENCE`
    / `STABILITY=0` 输出逐位不变」「`BASIS=completion_state` 不得写成
    `reference_configuration`」「`F0` 与 `Finc` 分列、`*STABILITY_LOAD`
    不进 `m.loads`」「`CTRL0 ≡ [U]`」「能力矩阵该行仍 `P` 不是 `Y`」
    「§2.6 三阶段表与四条硬口径在位」；第 22 项 CTest 冻结 V 形双索
    `MODE=STATIC` / `TENSION` 两组成桥态路径，用**不读求解器的闭式
    `λ(v)`** 逐点对拍，另有「无增量荷载 / 未知基准 / 默认零污染」负例；
    FE 侧 `fem_3d_nl.hpp` 与 `cable_solver.cpp` 既有默认路径零改动，
    `[BUCKLING]` `ERROR=nonlinear_state_not_supported` 仍在。
17. 第十九次 ② 真实桥梁两段对标不虚报：同一守卫的 `stage2_contract()`
    锁住「`ansys_bridge_completion.py` / `run_cases_completion.py` 在位
    且 `MODE=STATIC` + 同一份 `L0` + `INISTATE eps` 同源」「两个 ANSYS
    job 都恰有 2 个 `SOLVE`（step1 成桥态 + step2 增量荷载），主 job
    荷载控制、`ARCLEN` job 只作终点交叉验证，不得拿参考构形单段
    `ARCLEN` 冒充」「第 23 项 CTest 离线可跑、`--live-ansys` 不覆写
    冻结 CSV」「能力矩阵仍 `| N | P |`、§2.6 三阶段表在位、③ 仍待」
    「`cases/README.md` 登记 CB3D21、FE 总索引登记 23 个 CTest」；
    CB3D21 报告 `RESULT: ALL CHECKS PASSED`、两 job 0 error，
    但路径单调硬化无极限点、施工阶段链路未接入，B3 仍 `P` 不 `Y`。
18. 第十九次 ③a 不虚报：同一守卫的 `stage3_contract()` 锁住
    「`fem_3d_nl.hpp` 三个结果结构回传 `basisModel`、
    `cable_solver.cpp` 只在 opt-in 成桥态用同源模型且带
    `invalid_initial_state` 自由度检查」「第 24 / 25 项 CTest 与
    `run_tests.ps1 -BuildDir` 在位、FE 总索引 25 个 CTest、
    编号 `CB3D10_BASIS_FIX` 不与 CB3D22 复用」
    「`ansys_bridge_limit_point.py` 只缩塔实常数、不重新建模，
    两个 ANSYS job 各 2 个 `SOLVE` / 16 条 `INISTATE` / 0 error，
    `lp_load` 荷载控制、`lp_arc` 才有 `ARCLEN`，按 `ΔUX` 插值 `λ`」
    「§2.5 / §2.6 如实写明 ③a 完成仍 `P` 不 `Y`、
    不得靠改文档提前打勾」「FE 侧 `CHANGELOG.md` /
    `input_format_3d.md` / `USER_MANUAL.md` / `DEVELOPMENT_GUIDE.md`
    四份文档均登记 ③a，且 `USER_MANUAL.md` / `DEVELOPMENT_GUIDE.md`
    同样写明 B3 仍 `P` 不 `Y`」；CB3D22 报告 20 checks / 0 failed，
    干净构建 `ctest` 25/25、`run_tests.ps1` `ALL TESTS PASSED`。
    但 §2.5 的分支屈曲 / 初始缺陷 / 材料非线性 /
    施工阶段中间态全过程稳定**全部未收口**，③a 完成不等于 ③ 完成。
19. 第二十次 ③b 不虚报：同一守卫的 `stage4_contract()` 锁住
    「`NLStageResult3D::model` 每阶段回传同源模型、
    `cable_solver.cpp` 的 `runStageStabilityMode()` 用 `pick->model`
    装配 F0 / Fint / 弧长路径、未知 `STAGEID` 写
    `ERROR=stage_not_found`」「`BASIS=stage_state` 与另两种基准
    分列、`[STAGES]` 与 `STAGE=` 留痕、第 26 项 CTest 与
    `run_tests.ps1 -BuildDir` 在位、FE 总索引 26 个 CTest」
    「`ansys_stage_stability.py` / `run_cases_stage_state.py`
    同源导出，两个 ANSYS job 各 2 个 `SOLVE` / 0 error、
    `λ = TIME − 1`、按 `|ΔUY|` 插值 λ」
    「§2.5 / §2.6 如实写明 ③b 完成仍 `P` 不 `Y`、
    不得靠一个 opt-in 算例提前打勾」「FE 侧 `CHANGELOG.md` /
    `input_format_3d.md` / `USER_MANUAL.md` / `DEVELOPMENT_GUIDE.md`
    四份文档均登记 ③b 且写明 `STABASIS=STAGE`」；
    CB3D23 报告 72 checks / 0 failed，第 26 项 214 项检查，
    干净构建 `ctest` 26/26、`run_tests.ps1` `ALL TESTS PASSED`。
    但 §2.5 的位移控制 / 线搜索、分支屈曲、初始缺陷、材料非线性、
    动力 snap-through 仍未收口（Riks 部分由下面第 20 条补记收口），
    且 CB3D23 不是真实桥梁施工阶段，③b 完成不等于 ③ 完成。
    本轮另如实登记两处**与力学无关**的守卫环境适配（不得视为放宽
    判据或虚报）：`verify_app.py` 因 Streamlit 1.45.1 把
    `AppTest.session_state` 换成 `SafeSessionState`（无 `dict.get()`），
    新增 `_ssget()` 以 `in` + `[]` 严格等价替换 12 处旧写法；
    `verify_opt_2d.py` 的「CHANGELOG 前 12 行含 `## 2026-11-12`」
    是 ③a 起过时的位置断言（当日小节持续追加到顶部），改为锁
    「该日期小节仍完整在档」。两条都**不放宽任何功能判据**。
    结果：`verify_opt_2d.py` 286/286，另 4 个 ③b 关键守卫全绿
    （100/100、185/185、282/282、30/30）。
    `ui_3d.py` 的「与 MIDAS 互导」外层 `st.expander` 内嵌
    `_interop_block()` 的 `c1/c2.expander` 触发。最小修复是把外层
    改为普通 `st.container()` + 一行 `caption`，保留内层 4 个折叠块、
    全部按钮 / 下载 / 试算功能与守卫断言，仅去掉「互导整块可折叠」
    这一层包装；修复后 `verify_app.py` **109/109 通过**。
    这是业务 UI 与新版 Streamlit 的兼容性问题，不是 ③b 引入、
    不涉及任何力学判据，**不放宽断言、不改 B3 结论**。
    补齐 `anastruct` / `python-docx` / `pypdf` 并完成上述等价适配后，
    53 个 BS 守卫全部通过，4 个 ③b 关键守卫为
    100/100、185/185、282/282、30/30。
20. 第二十次 Riks 闭环不虚报：`stability_path_3d.hpp` 的 RIKS 分支把
    正交约束落在**校正增量**上（初版误落在「本步总增量」，预测点沿
    当前切线走恰是正交面法向，实测 2000 步 `CTRL` 原地不动，已如实
    记在 benchmark README），`dλ` 由线性方程唯一确定、无选根启发式；
    `cable_solver.cpp` 只在 `runStageStabilityMode` 放开 RIKS，
    REFERENCE / COMPLETION 入口仍拒绝；`stability_output_3d.hpp` 按
    pathMethod 写 `KIND=wempner_riks_orthogonal` / `PATHMETHOD=RIKS`，
    SPHERE 字符串逐位不变。第 27 项 CTest 63/63 守卫通过：闭式
    `λ(v)` 逐点 `worst=4.6e-14`（184 点）、SPHERE/RIKS 双方法
    `worst=1.47e-4`、CB3D23 终点与第 26 项冻结 SPHERE 一致、
    未知方法负例、8 份冻结证据逐字节重放；第 20 / 26 项原有冻结
    输出不变。FE 侧 `CHANGELOG.md` / `input_format_3d.md` /
    `USER_MANUAL.md` / `DEVELOPMENT_GUIDE.md` 四份文档与本规格
    §2.5 均已登记。本项目 RIKS 与 ANSYS `ARCLEN`（球面型）**不同源**，
    本项不跑 ANSYS、不代替球面型的 ANSYS 证据；B3 仍 `P` 不 `Y`。
21. 第二十一次（2026-11-16，用户「继续补 B3 五项力学」）：用户明确要求
    补齐 §2.5 列明的五项未覆盖力学。规格先行、逐项 opt-in、默认零污染，
    每阶段交付「内核 opt-in + 独立闭式 / 解析锚点 + 负例守卫 + 新 CTest」，
    逐阶段仍标 `P` 不标 `Y`，五项全部收口且干净构建全量回归后才按 §2.6
    统一推 `Y`。次序：① 位移控制 / 线搜索、② 初始缺陷形态 / 幅值 / 敏感性、
    ③ 材料非线性本构、④ 分支屈曲与分支切换、⑤ 动力 snap-through。每项
    挂点与验收深度见 §2.6「第二十一次分阶段推进」表。任务 #159–#166
    逐项跟踪；本条为**进行中**登记，各项落地后在此逐条补实测数据。任何
    单项完成都不把能力矩阵提前涂成 `Y`，不得靠改文档提前打勾。
    - **力学①实测收口（2026-11-16，任务 #160）**：`stability_path_3d.hpp`
      `ArcLengthOptions3D` 新增 opt-in `controlMode` / `ctrlDispStep` /
      `lineSearch`（默认 0/0/0，弧长 / Riks 与既有输出逐位不变）；位移控制
      把控制 DOF 位移增量锁定、λ 由线性约束唯一确定，控制方向第一步锁定
      越过极限点不翻转；Armijo 线搜索保持位移约束、不改平衡路径。
      `snap_probe.exe` 的 `*.snap` 新增 `CTRLMODE/LINESEARCH/CTRLSTEP/
      CTRLNODE/CTRLDOF`（默认全关）。第 28 项 CTest
      `displacement_control_3d_benchmark` 25/25：闭式 `P(v)` 逐点
      `worst_dP=2.2e-8 N`、线搜索 / 无线搜索同路径 `worst|Δλ|=3.9e-18`、
      细步 `λ_max=0.594204` 比粗步 `0.594016` 更接近闭式 `0.594210`、
      奇异控制 DOF 负例 `singular_control_dof`。干净构建 `ctest` **28/28**、
      `run_tests.ps1 -BuildDir _d_build` `ALL TESTS PASSED`。本项仅
      `snap_probe` 独立探针层、未接 `cable_solver` 主链路，B3 仍 `P`。
    - **力学②实测收口（2026-11-16，任务 #161）**：`stability_path_3d.hpp`
      新增独立 `computeBucklingModeShape3D`（线性屈曲模态 `(K+λKg)φ=0`，
      稠密特征问题、`φ` 最大分量固定 −1 保证符号可复现；**不改**
      `fem_3d.hpp::computeBucklingResult3D`，后者只返回 factors 且被
      `example_3d_grillage` 等多处调用，`STABILITY=0` 既有输出逐位不变）与
      `applyImperfectionToNodes`（模态按幅值加到节点坐标，TRUSS/CABLE 的
      `L0` 同步更新为无应力几何缺陷，λ=0 即平衡态）。实测最低模态对称
      竖向（线性屈曲因子 3.18）、第 2 阶反对称横向（顶点 X 偏移）。
      `snap_probe.exe` 的 `*.snap` 新增 `IMPERF/IMPMODE/IMPAMP`（默认
      0/0/0，关闭时输出逐位不变），opt-in 输出 `imp_ok/imp_lambda/
      imp_dx2/imp_dy2/imp_maxphi`。第 29 项 CTest
      `initial_imperfection_3d_benchmark` **35/35**：幅值 0→0.005→0.01→0.02
      的 `λ_max_fit` 0.59436→0.57719→0.56073→0.52831 **严格单调下降**，
      与独立闭式 `λ_cr`（缺陷后几何 `h=H−a`、`L0'=hypot(B,h)`）误差
      ≤1.5e-4；闭式 `P(v)` 逐点锚点；反对称模态对称破缺（顶点 X 偏移
      =幅值、Y 不偏移）；`IMPMODE=9` 越界负例 `imp_ok=0`；`IMPERF=0`
      与零幅值路径逐位一致且无诊断键。冻结 CSV 当前二进制逐字节重放。
      干净构建 `ctest` **29/29**、`run_tests.ps1 -BuildDir _d_build`
      `ALL TESTS PASSED`。本项仅 `snap_probe` 独立探针层、未接
      `cable_solver` 主链路、不跑 ANSYS，B3 仍 `P`。
    - **力学③实测收口（2026-11-16，任务 #162）**：`Element3D` 新增 opt-in
      `matKind`（默认 `ELASTIC`）/ `matFy` / `matFt` / `matNfy` /
      `matNfz`，`readModel3D` 的 `*R` 段解析 `MATKIND/MATFY/MATFT/
      MATNFY/MATNFZ`（键缺席保持默认、逐位走原路径）；`section.hpp` 新增
      `gaussLegendreRule`（Newton 求根）；`fem_3d_nl.hpp` 的
      `crMakeElemData` 仅在 `BEAM`+`STEEL/CONCRETE` 时构造 Gauss 纤维截面
      （离散 A/Iy/Iz 与解析矩形逐位一致，弃用中点离散 1.6% Iz 误差），
      新增 `crFiberLocalForce`（轴力/双向弯矩由 `sectionResponse` 积分、
      端剪力由梁段平衡 `q1=(q5+q11)/L`、`q2=−(q4+q10)/L` 补齐、扭转保持
      线弹性）与 `crFiberLocalTangent`（中心差分覆盖 D12/D13/D23 与
      弹塑性活动集），`crElementInternal` BEAM 分支 opt-in 接入且
      `enabled=false` 时逐字节同源。STEEL 理想弹塑性（拉压对称、屈服后
      切线 0）、CONCRETE 受拉 `Eε>ft` 开裂退出、受压线弹性。第 30 项
      CTest `material_nonlinear_3d_benchmark` **491/491**：单轴平台
      `N=fy·A`、矩形塑性铰 `My<M3<Mp=1.5My` 且塑性切线 < 弹性切线、
      混凝土受拉开裂归零、弹性段纤维 vs Kl12 `maxdq~5e-8`/`maxdK`
      相对 ~5e-8，独立 Python（numpy Gauss 点）纤维积分逐项锚点、
      当前二进制逐字节重放。干净构建 `ctest` **30/30**、
      `run_tests.ps1 -BuildDir _d_build` `ALL TESTS PASSED`。本项未接
      `cable_solver` 成桥态主链路、不跑 ANSYS，B3 仍 `P`。
    - **力学④实测收口（2026-11-16，任务 #163）**：`ArcLengthOptions3D`
      新增 opt-in `bifurcate=0 / bifPivot=0.05 / bifMode=1 / bifAmp=0 /
      bifEigTol=1e-3`；`ArcLengthPoint3D` / `ArcLengthResult3D` 新增 `bif*`
      诊断字段（默认全 0）。`solveArcLengthPath3D` 每个收敛点先用**已逐点
      输出但此前未使用**的 `pivotRatio` 做轻量入口（高拱分岔点实测
      `≈8.9e-9`），再用当前一致切线自由子块最小特征值
      `|μ_min|/max|diag(Kff)|<1e-3` 确认（实测 `≈2.9e-4`）；确认后沿
      `computeBucklingModeShape3D(m, bifMode)` 的模态施加 `bifAmp` 位移
      扰动（只改位移、不改 `L0`/荷载/求解器，多阶分岔只切换一次），
      后续弧长步从扰动构形继续跟踪分岔后分支。`snap_probe.cpp` 解析
      `BIFURC/BIFPIV/BIFMODE/BIFAMP`，opt-in 时输出 `M` 行与 `bif_*`
      诊断，默认零污染。高拱 von Mises 桁架（`B=1,H=3`）：反对称分岔
      `L³=L0·h²` 闭式有两根（`h≈0.838/2.553`），路径上先遇到较大 `h`
      的根（`v_bif≈0.44735, λ_bif≈26.0152`），先于对称极值点
      `L³=B²·L0`（`v_sym≈1.92555, λ_sym≈82.3708`）；FEM 切换点
      `v=0.44630, λ=25.9567` 对齐闭式。第 31 项 CTest
      `bifurcation_switch_3d_benchmark` **42/42**：反对称切换后
      `λ_max` 82.37→26.24、模态幅值 0.44→1.50、`v_final` +2.926→−1.501、
      不再满足对称解析；`bif_sym`（对称模态）`amp_final≈5e-17` 仍在同一
      对称路径，`bif_zero`（零幅值）/ `bif_badmode`（模态越界）检测但
      `bif_switched=0` 且与主路径去 id 后逐位一致。独立 Python 闭式求根
      锚点 + 冻结 CSV sha256 + 当前二进制逐字节重放。干净构建
      `ctest` **31/31**、`run_tests.ps1 -BuildDir _d_build`
      `ALL TESTS PASSED`。本项属 FEM 自洽深度、仅探针层，不跑 ANSYS、
      与 ANSYS `ARCLEN` 球面型不同源，B3 仍 `P`。
    - **力学⑤实测收口（2026-11-16，任务 #164）**：新建独立模块
      `dynamic_newmark_3d.hpp`（`NewmarkOptions3D` / `NewmarkPoint3D` /
      `NewmarkResult3D` / `denseMassMatrix3D` / `solveNewmarkDynamic3D`）
      与文件驱动 CLI `dyn_probe.cpp`，**不改** `snap_probe` /
      `stability_path_3d.hpp` / `fem_3d_nl.hpp` / `fem_3d.hpp` 既有路径。
      一致质量复用 `assembleMassMatrix3D`，`massMode=1` 用行和集中质量；
      Newmark-β（默认 β=0.25/γ=0.5）隐式积分，每步经典预测 + Newton 解
      `(a0·M+a1·C+Kt)Δu = Fext−Fint−M·a−C·v`，残差只统计自由自由度
      （排除支反力），Rayleigh 阻尼步起点冻结。浅拱 von Mises 两杆
      （`B=2,H=0.5`）阶跃荷载 `PREF=0.85·P_cr≈1.010157e6`（**低于静力
      极限** `P_cr≈1.18842e6`）实测 `v_max≈1.3615`、`t_snap≈0.0039 s`，
      即惯性驱动的动力跃越；一致质量 `m_c=2·rhoA·L0/3=10.788793`、
      集中质量 `m_l=rhoA·L0=16.183190` 各自对齐独立 SDOF RK4（子步长
      1/5，位移误差 <2e-3 m、速度误差 <5 m/s）。第 32 项 CTest
      `dynamic_snapthrough_3d_benchmark` **88/88**：低荷载 / 爬升 /
      半正弦不跃越、阻尼降低峰值动能与位移、质量模式改变 snap 时刻、
      dt 加密 `v_max` 差 2.2e-4、动能/应变能非负、外功 `≈P_ref·v_final`、
      初加速度 `-P_ref/m`；零质量 / 零步长 / 非法 β / `DYN=0` 四类负例
      无 `T,` 行，冻结 CSV sha256 + 当前二进制逐字节重放。干净构建
      `ctest` **32/32**、`run_tests.ps1 -BuildDir _d_build`
      `ALL TESTS PASSED`。本项属 FEM 自洽深度、独立探针层，不跑 ANSYS、
      与 ANSYS 瞬态动力学不同源，B3 仍 `P`。

**明确未闭环（历史记录，截至第二十一次；B3 五项已于第二十二次收口，见文末）**：B3 的分支屈曲 / 初始缺陷 / 材料非线性 / 动力 snap-through
虽已在**独立探针层**
分别落地（见力学④②③实测收口），但与 CR 梁后屈曲一样，其
**主链路成桥态闭环与施工阶段链路接入**（第十八次 CB3D19 只把
独立基准题对标完，`frame_probe.exe` 不是主链路；第十九次 ① 已把
球面弧长法起点换成成桥态，但只覆盖 `STATIC` / `TENSION` 的 FEM 自洽，
**真实桥梁 ANSYS 两段对标与施工阶段链路仍属 ② / ③**）、真实桥梁全过程
稳定对标（第十七次起主链路 opt-in 球面弧长法已接入、第十八次
独立 CR 梁框架后屈曲已对标、第十九次 ① 成桥态基准已进主链路，
但按 §2.5 / §2.6 仍标 `P` 不标 `Y`）、B4 两项公式 / 表值接入、B5 无应力曲率、**B2 剩余 0 项**（第十五次 B2「2D 平行导出 / 导入链」两格按 `P` 接通，仍 `P ≠ Y`；`[OPT]` 带约束索力优化一层 2D 内核力学已于 **2026-11-12 第十四次**按 `P` 接通为 `[OPT2D]` 2D 平面索力优化，仍 `P ≠ Y`、不等于 3D `[OPT]`；`[LENGTH2D]` 已于第十三次按 `P` 接通，仍不等于 3D `[LENGTH]`，内层 Newton 已于第十一次按 `P` 闭环，不等于三层全闭环）、四出口计算书内容补齐（导出机制已闭环，仍标 `P`）、2D 分项损失明细的 3D 阶段维度（第十次只接通非阶段束明细，仍标 `P`）、MIDAS 数值对拍、组合体系建造器。任何一项真正落地前，能力矩阵与计算书都必须继续写「没有」，不得靠改写文档提前打勾；`P` 不是 `Y`，应力利用率不得冒充 Mu/Vu 承载力验算。2D 承载力的压弯 / 轴压半部已于 **2026-11-12 第十六次**按 `P` 口径接通（`capacity_axial_2d.py` / `verify_capacity_axial_2d.py`，仍 `P ≠ Y`），不再列入上述未闭环清单。

> **第二十二次收口记录（2026-11-16，任务 #165）：B3 非线性稳定全过程由 `P` 推 `Y`**。按 §2.6 三阶段验收口径逐条核对，推 `Y` 条件全部满足：
>
> | §2.6 阶段 | 交付收口 | B3 状态 |
> |---|---|---|
> | ① | `stability_path_3d.hpp` 非零初态 / 恒定荷载 / 增量荷载 + `STABASIS=COMPLETION` + 第 22 项 CTest（FEM 自洽 / 闭式 `λ(v)` / 零污染） | `P`（当时） |
> | ② | `ansys_bridge_completion.py` + CB3D21 真实双塔三跨斜拉桥 ANSYS 两段对标 + 第 23 项 CTest（2392 项黄金值、28 子步 `worst\|ΔUZ\|=1.85e-5 m`、`--live-ansys max\|d\|≤1e-11`） | `P`（当时） |
> | ③a | `MODE=COMPLETION` 起点同源修复（CB3D10 `RES0`→`2.502e-9`）+ 施工阶段段共存（CB3D13）+ CB3D22 越过极值点 / 下降段（第 24 / 25 项 CTest） | `P`（当时） |
> | ③b | `STABASIS=STAGE` + `STAGEID` 施工阶段中间态起点 opt-in（CB3D23）+ 第 26 项 CTest（214 项）+ Riks 正交弧长第 27 项 CTest（3109 checks）；`STAGE` / `RIKS` 均独立 opt-in | `P`（当时） |
> | 第二十一次 | §2.5 列明五项力学全部补齐：① 位移控制 / 线搜索（第 28 项 25/25）、② 初始缺陷（第 29 项 35/35）、③ 材料非线性（第 30 项 491/491）、④ 分岔切换（第 31 项 42/42）、⑤ 动力 snap-through（第 32 项 88/88） | `P`（单项均不提前打勾） |
> | **第二十二次（本轮）** | 五项全部收口 + 三阶段全部走完 + 干净构建全量回归：`ctest` **32/32**、`run_tests.ps1 -BuildDir _d_build` `ALL TESTS PASSED`；能力矩阵该行 `P→Y`、三守卫同步（B 类规格 / 能力矩阵 / app 全绿） | **`Y`** |
>
> **`Y` 的如实边界（不得外推）**：① **opt-in 默认全关**——五项力学新键默认关闭，`STABILITY=0` / 无新键时既有 `.dat` / `.result` / 报告逐位不变，27 项既有 CTest 冻结证据零漂移（第二十一次四条硬口径第①条）；② **独立探针层为主**——① ② ④ 在 `snap_probe.exe`（`*.snap`）、③ 在 `mat_nl_probe.exe`（`*.mat`）、⑤ 在独立模块 `dynamic_newmark_3d.hpp` + `dyn_probe.exe`（`*.dyn`），经参数文件驱动、不经过 `cable_solver` 主链路成桥态自动流程；球面弧长 / Riks / 成桥态 / 施工阶段起点才是 `cable_solver.exe` 主链路 opt-in 分支；③ **每项有独立闭式 / 解析锚点 + 负例守卫 + 冻结 CTest**（硬口径第②③条），不是「程序跑通」当验收；④ **与 ANSYS 同源性有限**——球面弧长 / 成桥态 / 施工阶段 / 极限点有 ANSYS 18.0 两段对标证据，五项力学（尤其动力 snap-through）属 FEM 自洽深度、与 ANSYS 瞬态动力学不同源，Riks 与 ANSYS `ARCLEN` 球面型不同源；⑤ **不是规范合格判定**——λ 仍是参考荷载放大倍数、不是 JTG 3362 稳定安全系数，JTG 无 λ 合格限值，§2.4 禁编数；⑥ **研究构型边界**——CB3D22 细塔 / CB3D23 三杆浅拱是稳定敏感研究构型、非实际桥梁设计尺寸或真实施工工序。
>
> 本推 `Y` 只针对 B3 非线性稳定全过程这一格。B1 组合体系仍暂缓、B4 两项公式 / 表值接入、B5 无应力曲率、四出口内容补齐、2D 分项损失 3D 阶段维度、MIDAS 数值对拍、B2 各格 `P ≠ Y` 均未因本轮改变；`P ≠ Y` 的其余边界与「应力利用率不得冒充承载力验算」纪律继续有效。

22. 第二十二次前序记录见上方「第二十二次收口记录」引用块（B3 由 `P` 推 `Y`）。

23. 第二十三次（2026-11-16，用户「同意建议的顺序」）：按 σl2 → σl6 → B5 推进 B4 / B5 三项，沿用 opt-in 默认零污染、独立解析锚点、负例守卫、新 CTest、逐项仍 `P` 的纪律，全部收口且干净构建全量回归后才评估能力矩阵相应格。用户拍板的三项口径：
    - **σl2 代表值取张拉端 x=0（保守）**：附录 G 给的是 `σl2(x)` 沿程分布，而内核 `σpe = σcon−σl1−σl2−σl4` 只能吃一个代表值，规范无统一规定；取 x=0 峰值与 G.0.2-4 `Δσ=2Δσd·lf` 闭式最干净，且 σpe 用于 `[OPT]` 索力与承载力验算、宁可保守。实现挂点：`prestress_loss.hpp::PrestressLossInput` opt-in 新键 + `computePrestressLosses()` 分流，`lf≤l` / `lf>l` 两支独立闭式 + SI 单位换算（规范 mm/MPa ↔ 内核 m/Pa），缺参显式 throw 不回退直线式；结果段只在新路径追加 `LF=` / `DSL2D=` / `DSL2PEAK=` / `BRANCH=`。验证入口 `example_mixed.exe --loss-detail-only <file>.dat` → `<file>.loss.txt`（2D/3D 共用 `parsePrestressLossLine`）。
    - **σl6 由用户在模型里显式录入经核对的规范表值**：表 C.1.2（水泥类型 βsc 与强度折减）、表 C.2.2（32.4/fck 折减）、RH / h / fcm / t0 / ts / t 等。实现挂点：opt-in `C_AUTO=1` 但缺任一表值 → 显式 throw，**绝不调用 `code_loads.hpp::jtg3362NominalCreep()` / `jtg3362ShrinkageNominal()` 拟合值兜底**；表值齐备后才把 `φ(t,t0)` / `εcs(t,ts)` 接到 `EPS` / `PHI` 并在结果段写明来源；区分「显式声明 σl6 不参与」与「缺值报错」，不改既有 22 键算例输出。
    - **B5 先做 FEM 自洽深度**：`fem_3d.hpp::Element3D` / `fem_3d_nl.hpp::CRElemData` 增 opt-in 单元级初始曲率自由度（默认 0 = 现行为）；零外荷载自平衡、`L0` 与影响矩阵 `J` 不变、成桥态 `My` 与解析解对拍；不接真实制造参数、不做 ANSYS 对拍（那需设计给定的取值依据）。不得把「节点预抬高」写成「无应力曲率已实现」。
    - 逐项实测收口写入 §6 后续条目，任务 #167–#172（σl2）、后续 σl6 / B5 任务跟踪；任何单项完成都不提前打勾。

24. 第二十三次 **σl2 阶段实测收口**（2026-11-16，任务 #167–#170）：本条只收口 σl2，**不代表 B4 整项完成、也不改能力矩阵任何格**。
    - **内核**：`prestress_loss.hpp` 增 opt-in `L2ANTI` / `L2REPX`（默认 `0/0`），`computePrestressLosses()` 分流；`lf≤l` 用 `Δσ=2Δσd·lf`、`lf>l` 用 `Δσ′=Δσd(lf²/l+l)`，`lf=l` 两支连续（独立验算 `175581777.17704442 Pa`）；无摩擦退化直线式 `BRANCH=0`；`ΣΔl≤0` / `L2REPX≠0` / `Ep≤0` 显式 throw。结果增 `lf` / `deltaSigmaD` / `deltaSigmaPeak` / `l2Branch`，2D 明细段仅 `l2Branch≥1` 时追加 `LF=` / `DSL2D=` / `DSL2PEAK=` / `BRANCH=`，默认数值行逐位不变。
    - **BS 侧**：`PrestressLoss2D` / `PrestressLoss3D` 由 22 键扩到 24 键，两个反摩擦键默认不写出，12 格默认 `.dat` 哈希与 21 键既有算例均不变；`prestress_loss_2d.py` 的九列表不承载中间量，NOTE 如实化；`report_2d.py` / `report_3d.py` / `result_3d.py` 的「只按直线筋 / 未实现」文案同步为「默认直线筋 + opt-in 附录 G x=0」。
    - **证据**：新增第 33 项 CTest `prestress_loss_anti_2d_benchmark`（`tests/benchmark/prestress_loss_anti_2d/`），7 份 `.dat`、5 份冻结证据、独立 Python 闭式 oracle、两支交叉 / `lf=l` 连续性 / 单位量纲 / 缺参与非 x=0 两个负例 / 默认逐位零污染 / 2D/3D 同参同值，**117/117 checks**；3D 只读探针 `loss_probe_3d.exe` 只复用内核，不自带力学。
    - **如实边界**：只给代表截面峰值、不输出 `σl2(x)` 沿程分布；非 x=0 代表值显式拒绝；`σl6` 仍为用户显式录入规范表值；不跑 ANSYS、不把 B4 涂成 `Y`。`lf>l` 分支的 `σl5` 在极低 σpe 下可能为负（JTG 6.2.6-1 数学行为），已写入 benchmark README，不是安全系数或规范合格判定。

25. 第二十三次 **σl6 阶段实测收口**（2026-11-16，任务 #172–#176）：本条只收口 σl6，**不代表 B4 整项完成、也不改能力矩阵任何格**。口径依据用户拍板「表值由用户在模型里显式录入经核对的规范表值，绝不调用规范拟合函数兜底」，条文依据 JTG 3362-2018 附录 C（https://jtg-3362.readthedocs.io/zh/latest/FLC.html）。
    - **内核**：`prestress_loss.hpp::PrestressLossInput` 增 opt-in `C2D` / `C_SKIP`（默认 `0/0`）与 7 个表值键 `EPS0` / `PHI0` / `C_RH` / `C_H` / `C_T0` / `C_TS` / `C_T`；`computePrestressLosses()` 三路分流：默认 ECS/PHI 直给（逐位不变）、`C_SKIP=1` 显式不参与（`cSource=2`）、`C2D=1` 表值龄期（`cSource=1`）。后者按 C.1.1-5 / C.1.3 算 `εcs(t,t0)=εcs0[βs(t,ts)−βs(t0,ts)]`，按 C.2.1-1/6/7 与 C.2.3-1 算 `φ(t,t0)=φ0·βc`（βH 的 RH 取区间代表值 55/80），再代入 6.2.7-1。**缺 EPS0/PHI0/RH 越界/龄期倒挂/两开关冲突均显式 throw，绝不调用 `code_loads.hpp::jtg3362NominalCreep()` / `jtg3362ShrinkageNominal()` 兜底**。`PrestressLossResult` 增 `epsCsDev` / `phiDev` / `betaS` / `betaS0` / `betaC` / `betaH` / `cHRepresentative` / `cSource`；2D 明细段与 3D `loss_probe_3d.cpp` 仅 `cSource≥1` 时追加 `C_SOURCE=` / `ECS_DEV=` / `PHI_DEV=` / `BETAS=` / `BETAS0=` / `BETAC=` / `BETAH=` / `RHREP=`。
    - **BS 侧**：`PrestressLoss2D` / `PrestressLoss3D` 增同组 opt-in 字段（默认不写出，22/24 键默认 `.dat` 哈希与既有算例均不变）；`prestress_loss_2d.py` NOTE、`report_2d.py` / `report_3d.py` / `result_3d.py` 文案同步为「默认用户直给 + opt-in 附录 C 表值龄期 + C_SKIP 显式不参与」。`USER_MANUAL.md` / `input_format.md` / `CHANGELOG.md` 相应登记。
    - **证据**：新增第 34 项 CTest `prestress_loss_creep_2d_benchmark`（`tests/benchmark/prestress_loss_creep_2d/`），9 份 `.dat`、5 份冻结证据（2D `.loss.txt` × 4 + 3D `.csv` × 1）、独立 Python 闭式 oracle、βH 两区间代表值切换 / `φ=φ0·βc` / `εcs=εcs0(βs−βs0)` 交叉 / 4 个负例（缺表值、RH 越界、语义冲突、龄期倒挂）/ 默认逐位零污染 / C_SKIP 与缺值区分 / 2D/3D 同参同值，**142/142 checks**；规范算例书锚点 σl6≈45.4MPa（C50、RH=75%、h=320.7mm、t0=7d、ts=3d、t=97d）与内核一致。
    - **如实边界**：表值 `εcs0` / `φ0` 由用户录入、程序不查表不按龄期拟合——这是口径选择，不是能力缺陷；C50 以上 32.4/fck 折减也由用户体现在录入表值里。只给代表截面 σl6 标量、不输出 εcs(x)/φ(x) 沿程分布；不接真实制造参数、不做 ANSYS 对拍；σl6 是后期损失，不进 σpe 与 Pe；不把 B4 整项涂成 `Y`。`code_loads.hpp` 拟合函数仍服务结构整体时变分析，与 σl6 表值链不相交。
    - **全量回归（任务 #176）**：干净 Ninja 构建（显式锁 mingw64 `g++.exe`）88/88 目标、ctest **34/34**、`run_tests.ps1` 干净构建目录 `ALL TESTS PASSED`、BridgeStudio 55 个守卫脚本全绿；冻结证据零污染（既有 benchmark 参考 / CSV / `.loss.txt` / `.snap` / `.dat` 均未改写）。工具链注意：干净构建若误用 `ucrt64/bin/c++.exe`（同为 GCC 16.2 / `-O3`），9 项 benchmark 会因 libm 末位漂移在 `RES` 列差 ~1e-10 而误报「逐字节不一致」，这是 `DEVELOPMENT_GUIDE.md §7` 已登记的 CRT 差异，非 σl6 代码缺陷；冻结证据由 mingw64 产出，干净构建必须用 mingw64 对齐。
26. 第二十四次 **结构时变徐变链规范算例书锚点 benchmark**（2026-11-16，任务 #177–#181）：用户指出「fem 里面有徐变模型，核实一下」，核实确认 FEM-Eigen 确有结构整体时变链（`code_loads.hpp` 连续拟合 + `fem_2d.cpp` `creepCoefficientAt`/`shrinkageStrainAt`/`processTimeEffects` 阶段 AEMM + `fem_3d.hpp` `creepCoefficientAt3D`），此前只回归到算例、无专门 benchmark、无 ANSYS 对拍。**本条只补 FEM 侧离线 benchmark，不改 BridgeStudio 数据模型 / 守卫 / 能力矩阵任何格，σl6 表值链口径不变**。
    - **口径**：结构整体时变链走 `code_loads.hpp` 连续拟合——`φ0=φRH·βF·βT0`、`βH` 用**实际 RH** 连续值、`εcs0=εS·βRH`；与 σl6「显式表值 + RH 代表值 55/80」链刻意隔离，禁止混用。
    - **证据**：新增只读探针 `creep_probe_2d.cpp`（仅调 `readModel2D` + 拟合函数，不自带力学），暴露 `M,PHI0/BETAH/ECS0` 与逐龄期 `S,<age>,PHI/ECS/BETAS/EEFF`；新建 `tests/benchmark/creep_shrinkage_time_2d/`（7 个 `.dat`、3 份冻结 CSV、独立 Python 闭式 oracle、compare 脚本、README）。第 35 项 CTest `creep_shrinkage_time_2d_benchmark` **73/73 checks**：独立 oracle 逐键核对 `φ0/βH/εcs0` 与逐龄期 `φ/εcs/βs/E_eff=E/(1+χφ)`，RH/t0 敏感性、AEMM 单调性、四负例（缺 RH/H/FCM、旧 CURVE 口径）、当前二进制逐字节重放，**75/75 checks**。
    - **隔离硬断言**：本链 βH=803.253（RH=75% 连续）**不是** σl6 代表值 961.763；εcs0=3.674e-4（连续 βRH）**不是** σl6 表值 0.310e-3；探针源码不得硬编码 55/80。
    - **h 来源核实（用户追问）**：`*CREEP_MODEL H=` → `m.creepH`，语义是**理论厚度 h=2A/u（mm）**，不是截面梁高（梁高是 `fem_2d.hpp:260` 另一个字段）；给定 H 时 `creepCoefficientAt`/`shrinkageStrainAt`（`fem_2d.cpp:751/771`）用 `m.creepH` 覆盖传入的单元 `hTheory`，探针与阶段 AEMM 严格同源。benchmark 增 2 条守卫锁死此覆盖关系，防未来改动致分叉。
    - **如实边界**：oracle 只核 `E_eff=E/(1+χφ)` 一环，不逐键展开阶段 AEMM 等效荷载（按上一阶段轴力、`Δφ` 增量、模量从 `baseModulus` 重算而非逐级累乘）；**只覆盖显式给定全桥理论厚度 H 的路径**——不给 H、仅单元 `HTH` 时内核退到逐单元 hTheory（`fem_2d.cpp:3088/3164-3170`）、探针在 :44 拒绝该路径，未覆盖；`processTimeEffects:807` 无条件用 `e.hTheory`、靠被调函数二次覆盖才正确（分支不一致隐患，已登记）。只覆盖 2D 探针，3D `creepCoefficientAt3D()` 同源未单独展开；不做真实制造参数接入、不与 ANSYS CEB90/蠕变做数值对拍（对拍可行性另见评估）。B4 仍 `P`，不因本条涂 `Y`。

27. 第二十五次 **2D 分项预应力损失阶段维度接入**（2026-11-17，用户「既然3D 侧早已是 Y，补 2D 阶段维度：让 [PRESTRESSLOSS2D] 支持 STAGE、把 σl2/σl6 的选项映射到 2D」，任务 #182–#187）：对齐 3D 早已 `Y` 的阶段分项损失，**只补 2D 缺的阶段维度**，**不改能力矩阵任何格（B4 仍 `P`，不因本条涂 `Y`）**。核心：`*STAGE_PRESTRESS` 支持 `LOSSREF=<表号>`，阶段束用与 3D 共用的 `computePrestressLosses()` 得分项明细与有效比，σl2 `L2ANTI/L2REPX`、σl6 `C2D/C_SKIP/EPS0/PHI0/C_RH/C_H/C_T0/C_TS/C_T` 经同一张 `*PRESTRESS_LOSS` 表**自动映射**（不另立 2D 专属公式）。
    - **默认零污染硬口径**：① 既有 `BASIS=2d_global` 段、`<stem>.loss.txt`、`example_mixed --loss-detail[-only]` 全部逐位不变，Python 旧 `parse_loss_txt()` 仍只认 `BASIS=2d_global` + `P<id>`；② 阶段段用**独立 opt-in 输出文件** `<stem>.stage.loss.txt`（`example_stage.exe --stage-loss-detail`），段名同为 `[PRESTRESSLOSS2D]` 但头行 `BASIS=2d_stage`、行首 `STAGE<n> P<id>`，与旧段不同文件、互不混行；③ 无阶段 LOSSREF 束时阶段文件写 `ERROR=no_lossref_stage_prestress`，不摆零值；④ 默认路径不写阶段文件、不刷新任何冻结证据、不改既有 CTest 黄金值。
    - **字段同源**：阶段行字段顺序、单位、精度与 3D `example_3d_grillage.cpp::stage prestress loss` 完全一致——`STAGE<n> P<id> SIGMA_CON= SL1=..SL6= SPE= RATIO=`，12 位有效数字，σl2/σl6 opt-in 中间量后缀同源追加。Python 新口径层 `parse_stage_loss_txt()` 以 `(阶段号, 束号)` 为键，与 `fem_eigen_3d.py` 的 `stage_prestress_loss` 组织键一致；仍**只解析、不重算**，RATIO 一致性只作同批数字互核。
    - **负例守卫**：`LOSS` 与 `LOSSREF` 同时出现→显式拒绝；`LOSSREF` 引用不存在表号→显式拒绝；表 `SIGMA_CON≤0`→显式拒绝；分项算得 `σpe/σcon` 不在 `(0,1]`→显式拒绝；σl2 非 x=0 / σl6 缺表值 / 两开关冲突 / 龄期倒挂沿用既有 throw；Python 侧阶段段 basis 不符、行数不符、缺字段、RATIO 不一致一律给显式中文状态、不摆空表。
    - **验收**：新增第 36 项 CTest `stage_loss_2d_benchmark`（`tests/benchmark/stage_loss_2d/`），含阶段锚点 / σl2 反摩擦 / σl6 表值 / C_SKIP 算例，独立 Python 闭式 oracle、冻结证据、负例、默认逐位零污染、2D/3D 同参同值；干净构建（显式锁 mingw64 `g++.exe`）`ctest` **36/36**、`run_tests.ps1 -BuildDir _d_build` `ALL TESTS PASSED`、BridgeStudio 全守卫全绿后，才评估能力矩阵是否收口。B5 无应力曲率、B4/B5 统一收口仍未开始，本条不代表 B4 整项完成。

28. 第二十六次 **B5 无应力曲率 FEM 自洽接入**（2026-11-17，用户「先做 b5 然后一起收口」，任务 #188–#194；范围见 §4.1）：按用户此前拍板「先做 FEM 自洽深度，不接真实制造参数、不做 ANSYS 对拍」，在 3D 线性梁增 opt-in 初始曲率，**本项只做 FEM 自洽，不代表 B5 已覆盖制造参数与全桥对拍**。
    - **载体与入口**：`Element3D` 增 `k0y`/`k0z`（1/m，默认 0），仅 `*EN` 单元行接受 `K0Y=`/`K0Z=`；非 `BEAM` 单元出现、或值非有限 → 显式 `throw`；`κ0=0` 时字段不参与任何计算，`elementEquivalentLoad3D()` 早退，既有 `.report.txt` / CSV / 冻结基线逐字节不变。
    - **一处分身两处自洽**：固端力 `f_eq = EI·κ0·[θ₁:−1, θ₂:+1]`（局部，`Tᵀ` 转全局）并入 `elementEquivalentLoad3D()`——它同时供组装 `a.F0` 与端内力 `fl − T·fe`，故平衡与内力恢复自动一致，`M = EI(κ−κ0)`；曲率模态用 `Eeff = E/(1+χφ)` 与刚度同源，零外荷载 `K·u0 = f_eq` 精确成立。`θy = −dw/dx` 侧符号取反。
    - **解析锚点**：悬臂 `u0=(v₂=κ0L²/2, θ₂=κ0L)`、端内力恒零；两端固结 `U=0`、`M=−EIκ0` 常数；`L0/J/Xm` 不受影响（不移动节点）。
    - **输出**：opt-in 独立文件 `<stem>.curvature.txt`（`example_3d_report --curvature-detail`，`[UNSTRESSED_CURVATURE]` 段），默认不写，不动既有报告段与解析器。
    - **验收**：第 37 项 CTest `unstressed_curvature_3d_benchmark`（独立闭式 oracle、负例、默认逐位零污染、`L0/J` 不变、既有 3D 报告哈希不变）；干净构建 `ctest 37/37`、`run_tests.ps1 ALL TESTS PASSED`、BridgeStudio 全守卫全绿。
    - **未覆盖（如实登记）**：共转列式 `CRElemData` 初曲率；真实制造参数；成桥态 `My` 全桥 ANSYS 对拍；徐变折减与 κ0 的耦合。以上不因本条涂改。

29. 第二十七次 **B4 收口：2D 阶段有效预加力输出段 + `Y` 口径定稿**（2026-11-17，用户「继续」，任务 #196–#200）。承接第 25 条：那一条把 2D 的阶段**分项损失**补齐后，矩阵仍差一项「2D 缺 3D `stage prestress force` 结果段」，本条把它补完，并把 `Y` 的口径写成可核对的定义，之后才动矩阵。
    - **补的是什么能力**：2D 阶段每束的**有效预加力** `Pe = P·loss`。力学上 2D 一直在算（`processPrestress` 内 `const double Pe = ps.P * loss;`，`loss` 已含 `LOSSREF` 解析出的 `σpe/σcon`，并经 `processPrestress → mc.loads → cumulativeLoads` 跨阶段累积），缺的只是**结果段**，前端拿不到「分阶段看哪批束何时张拉、有效预应力相对控制应力还剩多少」。
    - **载体与默认零污染**：`Model2D::prestressForces` / `StageResult::prestressForces`（`PrestressStageForce2D{id, P, loss, Pe, sigmaCon}`）在 `processPrestress` 里与 `Pe` **同一次计算**填一次，不重算；`example_stage.exe --stage-prestress-force` 写独立 `<stem>.stage.force.txt`，段名 `[PRESTRESSFORCE2D]`、头行 `BASIS=2d_stage`、行 `STAGE<n> P<id> SIGMA_CON= P= LOSS= PE=`（12 位有效数字）。**默认不写**：不带开关不追加任何参数、不删除任何文件，既有 `.result` / `_stages.txt` / `.stage.loss.txt` 逐字节不变。
    - **与 3D 的关系**：2D 的 `PE=` 即 3D `stage prestress force` 段的 `Pe=`，同参同值；组织键 `(阶段号, 束号)` 一致。**不输出** `PE/SIGMA_CON` 这类量纲不成立的比值（力/应力 = m²），有效比就是 `LOSS` 本身；只给 `LOSS=` 的束 `SIGMA_CON` 记 0，**不拿 `P/Ap` 反造控制应力**。
    - **`Y` 的口径（2D 侧，定稿）**：`Y` = **平面内分项损失完整**——六项公式与 3D 同一真源（`prestress_loss.hpp::computePrestressLosses`）、非阶段明细段（`BASIS=2d_global`）与阶段明细段（`BASIS=2d_stage`）齐备、阶段维度齐备、阶段有效预加力段齐备、各有独立闭式 benchmark 锚定（第 33 项 117 / 第 34 项 142 / 第 36 项 269 checks）。`Y` **不等于**「2D 有三向空间量」：2D 是平面模型，没有出平面位移与三向应力/内力，这是**定义差**、不是待补缺口，任何场景不得拿 2D 的 `Y` 冒充 3D 三向结果。
    - **`Y` 仍不覆盖（2D/3D 同样没有）**：① 不接真实制造参数；② 不做分项损失 ANSYS 本构级对拍（APDL `TB,CREEP` 无 JTG `φ0/βH/χ`）；③ σl2 只给张拉端 `x=0` 代表值、不出 `σl2(x)` 沿程；④ `σl3/σl5/σl6` 不进 `σpe` 与 `Pe`（规范口径）。
    - **验收**：第 36 项 CTest 扩到 **269 checks**（新增 8 算例的 force 段冻结证据、独立 oracle `PE=P·loss`、多束多阶段编号、软负例 `ERROR=no_stage_prestress`、两个 opt-in 互不依赖、默认不写文件、FE 源码契约）；`verify_prestress_loss_2d.py` 扩 `force_contract()`；`verify_capability_matrix.py` 同时锁「`| Y | Y |`」与上面这段口径文字（只改格子不改口径必须红）。
