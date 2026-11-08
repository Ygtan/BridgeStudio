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
- [ ] **P4** 成果层：3D 包络视图、MIDAS 双向互导（Word/DOCX 与 PDF 计算书已支持，含承载力逐项复核章）

## 质量保障闭环（铁律）

1. 改完任何 `.py` 必须重跑全部 verify 脚本再提交（当前 20 个脚本、761 项全绿）。
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

