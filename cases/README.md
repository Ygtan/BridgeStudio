# `cases/` — ANSYS 对标算例总览

本目录是 **BridgeStudio 前处理 → FEM-Eigen 计算 → ANSYS 18.0 校核** 链路的
算例归档。每个 `CBxx_*` 目录保留该算例的前处理输出（`.dat`）、同源导出的
ANSYS APDL（`.inp`）、模型快照（`.model.json`）、ANSYS 批处理输出
（`.out` / CSV）与对比报告（`.txt`），可离线复查，不需要再跑 ANSYS。

命名规则：`CB01~CB05` 为 2D 连续梁，`CB3D01~CB3D21` 为 3D 算例
（第十九次 ③a 新增 CB3D22 后为 `CB3D01~CB3D22`）；
编号按接入顺序递增，与 FEM-Eigen 侧 `tests/benchmark/` 的回归套件一一对应。
**例外**：CB3D18（主链路 `STABILITY=1` opt-in）与 CB3D20（成桥态起点
`STABASIS=COMPLETION` 的 V 形双索单自由度解析题）是**纯 FEM 侧
benchmark**，本仓库 `cases/` 下没有对应算例目录，也没有 ANSYS `.inp`
—— 它们的冻结 `.dat` 与结果文件在 FEM-Eigen
`tests/benchmark/stable_path_3d/` 与
`tests/benchmark/completion_stability_3d/`，不进上表。
**CB3D21 是 ② 阶段新建的 BS 侧真实斜拉桥算例**（不是 CB3D20 的
ANSYS 版）：它把同一个 `STABASIS=COMPLETION` 主链路放到 50 节点 /
63 单元 / 16 索的双塔三跨斜拉桥上，与 ANSYS 两段求解对拍，
目录与第 23 项 CTest 见下表。

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
| CB3D16 | `CB3D16_large_rotation_beam_column` | 共转 CR 梁**大转动卷圆**（至 2π）+ **梁柱 P-Δ 放大** vs ANSYS `BEAM188 + NLGEOM`（8 个 job） | `run_cases_beamnl.py` | `beam_nl_ansys_3d` |
| CB3D17 | `CB3D17_snap_through_arc_length` | 球面弧长法**后屈曲 / snap-through 全过程**（极限点、下降段、反向荷载）vs ANSYS `LINK180 + ARCLEN`（3 个 job） | `run_cases_snap.py` | `snap_through_ansys_3d` |
| CB3D19 | `CB3D19_frame_postbuckling_arc_length` | 共转 CR 梁**刚接框架（Williams toggle 型）后屈曲全过程** vs ANSYS `BEAM188 + ARCLEN`（2 个 job，每杆 24 单元） | `run_cases_frame.py` | `frame_postbuckling_3d` |
| CB3D21 | `CB3D21_completion_stability_vs_ansys` | **真实双塔三跨斜拉桥成桥态起点全过程稳定** vs ANSYS 两段求解（step1 恒载成桥态 / step2 增量荷载，荷载控制主 job + `ARCLEN` 交叉验证，2 个 job）；同源设计线形 + 16 根索 `L0` + `INISTATE eps`；第 23 项 CTest 冻结 2392 项黄金值 | `run_cases_completion.py` | `completion_bridge_3d` |
| CB3D22 | `CB3D22_limit_point_vs_ansys` | **③a 真实斜拉桥越过极值点 / 下降段**：与 CB3D21 同源桥只缩塔截面（`TOWER_SCALE=0.012`）、增量荷载改为塔顶 2009 节点 `+X`；FEM 球面弧长 `λ_max=1.624941`、下降段 207 步 vs ANSYS 两段 `ARCLEN λ_max=1.622748`（2 个 job，均 0 error）；按 `ΔUX` 插值 `λ` 逐点对拍；第 25 项 CTest 冻结 140 项检查 | `run_cases_limit_point.py` | `bridge_limit_point_3d` |

CB3D13 除全桥 APDL 外还归档 5 份阶段 APDL（`*_stage1..5.inp`）与对应
ANSYS CSV；三层验证口径见 `ansys_bridge_erection.py` 模块头与
FEM-Eigen `tests/benchmark/suspension_erection_3d/README.md`。
CB3D14/15 的口径与实测数字见 `ansys_bridge_sag.py` 模块头及
FEM-Eigen `tests/benchmark/cable_sag_ansys_3d/README.md`。
CB3D16 是首个同时启用 `BEAM188` 与 `NLGEOM` 的算例，另有 BEAM188
剪切锁定 / 剪切柔度的实测诊断，见 `ansys_bridge_beamnl.py` 模块头及
FEM-Eigen `tests/benchmark/beam_nl_ansys_3d/README.md`。
CB3D17 是首个启用 `ARCLEN` 的算例：参考荷载必须大于解析极限荷载，
否则 ANSYS 在 λ=1 就停；`KEYOPT(2)=1` 与对数应变解析严格同口径，
`=0` 只作应变度量对照。见 `ansys_bridge_snap.py` 模块头及
FEM-Eigen `tests/benchmark/snap_through_ansys_3d/README.md`。
CB3D19 是首个把**球面弧长法与共转 CR 梁框架**组合起来的算例：
`BEAM188` 用 `KEYOPT(3)=2` + `MP,GXY = G×1000` 退化到 EB 口径，
`IY=IZ` 消除局部轴约定差；极限点取**第一个 λ 局部极大**（框架翻越后
强硬化，全局最大落在终点），判据区间限定 `0 ≤ v ≤ VMAX=0.95`。
独立锚点不是桁架闭式 `P(v)`，而是三套线性刚架刚度实现的 `k0` 对拍。
见 `ansys_bridge_frame.py` 模块头及
FEM-Eigen `tests/benchmark/frame_postbuckling_3d/README.md`。

> CB3D14 是**单元级本构探针**：`.sag` 参数文件与 7 份 APDL 都从
> `ansys_bridge_sag.py::SAG_CFG` 同源生成，FEM 侧由 `cable_sag_probe.exe`
> 直接调用内核闭式；不走全桥 `ModelSpec3D.to_dat()`。`.model.json` 是可
> 追溯的参数快照，不参与求解。
>
> CB3D16 同理是**梁单元级探针**：`.beam` 参数文件与 8 份 APDL 都从
> `ansys_bridge_beamnl.py::BNL_GROUPS / BNL_CASES` 同源生成，FEM 侧由
> `beam_nl_probe.exe` 直接调 `fem_3d_nl.hpp::solveModel3DNonlinear`；
> 也不走 `ModelSpec3D.to_dat()`。
>
> CB3D17 是**独立路径跟踪探针**：`.snap` 参数文件与 3 份 APDL 都从
> `ansys_bridge_snap.py::SNAP_CFG / SNAP_*_CASES` 同源生成，FEM 侧由
> `snap_probe.exe` 调 `stability_path_3d.hpp::solveArcLengthPath3D`。
> 该求解器是**新文件**，不改 `solveModel3DNonlinear` 主链路；B 类规格
> 「主链路未接入非线性稳定全过程」的如实边界仍然成立。
>
> CB3D19 同样是**独立路径跟踪探针**：`.frame` 参数文件与 2 份 APDL
> 都从 `ansys_bridge_frame.py::FRAME_CFG / FRAME_*_CASES` 同源生成，
> FEM 侧由 `frame_probe.exe` 调同一个
> `stability_path_3d.hpp::solveArcLengthPath3D`，力学组合了
> `fem_3d_nl.hpp` 的共转 CR 梁内力 / 一致切线；两个内核头文件
> 一字未改。它**仍不是** `cable_solver.exe` 成桥态闭环 / 施工阶段
> 链路，能力矩阵 B3 继续标 `P` 不标 `Y`，边界见
> `B类后续项接入规格.md` §2.5。

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

## CB3D18：主链路 `[STABILITY]` opt-in（2026-11-14，M 阶段）

本阶段**不在 `BridgeStudio/cases/` 建算例目录**：FEM-Eigen 的
`cable_solver.exe` 新增 `STABILITY=1` 独立 opt-in 模式，`.dat` +
冻结 `[STABILITY]` 输出 + 3311 项黄金值 + 第 20 项 CTest 全部归档在
`FEM-Eigen/tests/benchmark/stable_path_3d/`。BridgeStudio 侧只新增
唯一解析器 `stability_3d.py`、独立守卫 `verify_stability_3d.py`，
以及能力矩阵 / B 类规格 / README / 使用说明 / 开发手册的登记，
**不产生新的 ANSYS APDL 或 `.inp`**（ANSYS `ARCLEN` 对标证据已在
CB3D17 归档，主链路与之逐值同源，无需重跑 ANSYS）。
能力矩阵该行标 `P` 不标 `Y`，边界见 `B类后续项接入规格.md` §2.5。

## CB3D19：CR 梁刚接框架后屈曲独立对标（2026-11-14，N 阶段 / 第十八次）

CB3D19 与 CB3D18 不同，**在 `cases/` 下建正式算例目录**：两根各 24 个
共转 `BEAM` 单元组成 Williams toggle 型刚接框架（端部铰支、顶点刚接），
顶点 `-Y` 参考荷载，用球面弧长法跟踪完整荷载–位移路径，
与 ANSYS `BEAM188 + NLGEOM + ARCLEN` 两个 job 对标。`.frame` /
`model.json` / FEM CSV / 对比报告 / manifest / run_log / 2 份 APDL /
`.out` / CSV 全部归档在 `CB3D19_frame_postbuckling_arc_length/`，
并同步冻结到 FEM-Eigen `tests/benchmark/frame_postbuckling_3d/cases/`。

- **同源唯一数据源**：`ansys_bridge_frame.py::FRAME_CFG` 同时生成
  `*.frame`（FEM）与 `*.inp`（ANSYS）；`linear_frame_stiffness()` 是
  BridgeStudio 侧第二套纯 Python 线性刚架刚度锚点。
- **第 21 项 CTest**：12513 项黄金值、4 个字符串、37 条 criteria、
  6 个 case；`compare_frame_postbuckling_3d.py` 离线重跑
  `frame_probe.exe` 逐值复现，`--live-ansys` 只写临时目录。
- **仍 `P` 不 `Y`**：这是独立 `frame_probe.exe` 探针，不是成桥态
  闭环 / 施工阶段链路已具备全过程稳定分析；把两者接通属于后续
  a 阶段，边界见 `B类后续项接入规格.md` §2.5。

CB3D21（2026-11-15，a 阶段 ②）是首个**真实桥梁尺度**的
`BASIS=completion_state` 算例。`ansys_bridge_completion.py` 以
`COMPLETION_CFG + EPS0=8e-4 + PINC=1e6 + LAMMAX=4.55` 为唯一数据源，
从同一份 `ModelSpec3D(mode=STATIC, 设计线形)` 同时生成 FEM `.dat`
与 ANSYS 两段 `.inp`：16 根索的 `L0 = L_design/(1+EPS0)` 与
`INISTATE,DEFINE,...,EPEL,X,EPS0` 严格同源，从根上消除参考构形偏差。

- **不用反力求和**：完整斜拉桥 50 个 `F` / 34 个约束节点上
  `ΣRF,FZ` 在 step2 随加载反而下降。荷载控制主 job `cs_load` 用
  `λ=(TIME−1)·LAMMAX` 与 `ΔUZ=UZ−UZ_step1` 对 FEM `[STABILITY]`
  逐点插值对拍（28 子步，`worst|ΔUZ|=1.85e-5 m`）；`cs_arc` 的
  `TIME` 是 `ARCLEN` 上界、不是 λ，只做终点位移交叉验证
  （`|d|=6.9e-8 m`）。
- **起点严格闭合**：`RES0=7.97e-3 N` 与 `[NEWTON] RESLAST=5.96e-3 N`
  同量级，`STATE CTRL0 ≡ [U](1016,3)`。必须用 `MODE=STATIC` + 显式
  `L0`：`MODE=COMPLETION` 的索长迭代会改写 `m.elements[*].L0`，
  与 `u0` 不同源（CB3D10 实测 `RES0=3.15 MN`），该缺陷登记为
  独立后续项，② 未修。
- **仍 `P` 不 `Y`**：本算例路径单调硬化、无极限点，ANSYS 荷载控制
  即可覆盖全程；「越过极限点 / 下降段 / 反向荷载」由 CB3D17 /
  CB3D19 独立基准题与 FEM 球面弧长内核证明。施工阶段链路接入、
  分支屈曲 / 初始缺陷 / 材料非线性未覆盖，B3 推 `Y` 属 ③。
  见 FEM-Eigen `tests/benchmark/completion_bridge_3d/README.md`
  与 `B类后续项接入规格.md` §2.6。

CB3D22（2026-11-15，a 阶段 ③a）是首个**真实桥梁越过极值点 / 下降段**
的算例。`ansys_bridge_limit_point.py` 以 `COMPLETION_CFG` 原几何 /
材料 / 恒载 / 16 根索 `L0` 为唯一数据源，只把桥塔实常数
`RealConst3D(id=2)` 按 `TOWER_SCALE=0.012` 缩放，增量荷载改为塔顶
2009 节点 `+X`（`PINC=1e6 N`），用 P-Δ 软化产生 `λ_max` 与下降段。
初版曾用缩小 `tower_b/tower_h` **重新建模**，导致塔自重缩小 83 倍、
成桥态不同源、FEM 不收敛，已改回「只缩实常数」口径。

- **两段求解**：ANSYS `step1` 默认 `TIME=1` 求成桥态，
  `step2` 用 `F,2009,FX`，`λ = TIME − 1`，不靠反力求和。
  `lp_load` 荷载控制只到 `λ=1.5`（极值点前）；
  `lp_arc` 用 `ARCLEN,ON,0.005,1.0E-8` 越过极值点。
- **非单调路径对拍**：λ 在极值点后下降，不能拿 λ 当插值自变量；
  按单调量 `ΔUX = UX − UX_step1` 插值 λ 逐点对拍。FEM
  `NPOINT=553`、`λ_max=1.624941 @ dUX=0.200411`、下降段 207 步；
  ANSYS `lp_arc` `λ_max=1.622748 @ dUX=0.201182`，
  极值点 `dλ=1.349e-3`、`dUX=3.850e-3`，两 job 均 0 error。
  `One indefinite tangent matrix is detected` 是极值点物理标志，
  ANSYS job 成败以 `NUMBER OF ERROR MESSAGES ENCOUNTERED=0` 判定。
- **如实边界**：细塔是为观测极值点构造的稳定敏感研究构型，不是实际
  设计尺寸；`[STABILITY]` 起点仍是成桥态。分支屈曲 / 初始缺陷 /
  材料非线性 / 施工阶段中间态稳定仍未覆盖。
  **③a 完成仍 `P` 不 `Y`**，见
  FEM-Eigen `tests/benchmark/bridge_limit_point_3d/README.md`
  与 `B类后续项接入规格.md` §2.5 / §2.6。
- **③a 同时修复** CB3D21 登记的 `MODE=COMPLETION` 起点不同源缺陷：
  `basisModel` 回传同源模型，见 FEM-Eigen
  `tests/benchmark/completion_basis_3d/README.md`。

## 归档规则

`.gitignore` 放行 `.dat` / `.inp` / `.model.json` / `.csv` / `.txt` / `.out`
作为可复查证据，排除 ANSYS 每次求解重生成的二进制与数据库产物
（`.rst` `.emat` `.esav` `.full` `.mntr` `.DSP` `.err` `.log` `.ldhi`
`.rdb` `.r###` 等）以及 `__pycache__/`。

## 复现

```powershell
# 单个阶段（示例：CB3D13；需要 ANSYS 18.0 许可 + anaconda python）
C:\Users\user\anaconda3\python.exe D:\AI-work\BridgeStudio\cases\run_cases_erection.py

# 离线回归（只需 FEM-Eigen 构建产物，不需要 ANSYS）。
# 构建目录须用与冻结证据同工具链的 mingw64 版本（见 tests/benchmark/README.md）。
ctest --test-dir D:\AI-work\FEM-Eigen\_d_build --output-on-failure
powershell -ExecutionPolicy Bypass -File D:\AI-work\FEM-Eigen\run_tests.ps1 -BuildDir _d_build
```

新增算例时：在 `cases/` 下建 `CBxx_*` 目录、写 `run_cases_*.py`，
并在 FEM-Eigen `tests/benchmark/` 建对应回归套件与 CTest 注册；
清单与总索引（`tests/benchmark/README.md`）需同步更新。
