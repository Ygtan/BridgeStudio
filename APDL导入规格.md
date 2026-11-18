# APDL 只读导入规格（a + b）

> 状态：规格先于实现，已落地。范围由用户 2026-11 的 `a+b` 选择限定；
> 2026-11-18 增补 `CE` 约束方程只读导入（用户选 ③ 收口本轮 CE 边界）。
> 本文只登记**读入**；APDL 导出能力（`spec_to_apdl` / `spec3d_to_apdl` /
> `beam_spec_to_apdl` / `write_ansys_file` / `write_ansys_2d`）保持逐字节不变。

## 1. 目标与边界

### 1.1 用户选择

| 项 | 内容 | 本次是否做 |
|---|---|---|
| a | 只读几何与拓扑：`N` / `E` / `ET` / `R` / `MP` → `ModelSpec3D` → `.dat` 试算 | ✅ |
| b | 再加荷载与边界：`D` / `F` / `SFBEAM` / `SFE` / `ACE` 等 | ✅ |
| c | 施工阶段、求解控制、多荷载步、`*DO` 循环、`NLGEOM` | ❌ 用户未选 |

### 1.2 唯一目标类型

导入的唯一输出契约是 **`ModelSpec3D`**（`model_spec_3d.py`），下游走
`to_dat()` → FEM-Eigen `.dat` 试算。**不产生 2D `ModelSpec`**：2D 是参数化模型
（总长 / 跨数 / 支座位置 / 截面形状），而 APDL 的 `BEAM3` 节点表无法无损反推
这四项，任何反推都是猜测。猜出来的参数化模型看起来能跑，实际已经不是原来那座桥。

### 1.3 默认零污染

- 不修改任何既有导出器的一个字符、一行输出；
- 冻结证据（`DEFAULT_DAT_SHA` 12 格、`dat_byte_regression` 37 格、
  CTest 38 项）一个都不刷新；
- 导入是新增入口，不覆盖当前界面 `doc`；
- 所有有损映射必须进 `ImportReport.warnings`，不许静默吞掉。

## 2. 命令分级

### 2.1 白名单：可逆读入

| 命令 | 语义 | → `ModelSpec3D` 字段 |
|---|---|---|
| `ET,id,BEAM4` | 6 自由度 Euler-Bernoulli 梁 | `ElemTypeDef3D(kind="BEAM")` |
| `ET,id,BEAM188` | Timoshenko 梁 | `kind="BEAM"`（**有损**，见 §3） |
| `ET,id,LINK180` | 空间杆/索 | `kind="TRUSS"`（**有损**，见 §3） |
| `ET,id,LINK8` / `ET,id,LINK10` | 空间杆 / 只受拉（或只受压）杆 | `kind="TRUSS"`（**有损**，见 §3） |
| `MP,EX,mid,v` | 弹性模量 Pa | `Material3D.E` |
| `MP,DENS,mid,v` | 密度 kg/m³ | `Material3D.dens` |
| `MP,PRXY,mid,v` | 泊松比 | 仅用于反算 `G`（无 `GXY` 时） |
| `MP,GXY,mid,v` | 剪切模量 Pa | `RealConst3D.G`（按 mat 传播） |
| `R,rid,AREA,IZZ,IYY,TKZ,TKY,THETA` | BEAM4 实常数 | `A / IZ / IY` |
| `RMORE,ISTRN,IXX,...` | BEAM4 附加实常数 | `ISTRN / J` |
| `R,rid,A,ISTRN` | LINK180/LINK8/LINK10 实常数：A + 初应变 | `A / ISTRN`。**APDL 用 LINK 只拉杆的初应变表达索力 F=E·A·ISTRN，ISTRN 完整保留**（见 §3） |
| `SECDATA,A,IYY,IYZ,IZZ,IW,J,...` | BEAM188 ASEC | `A / IY / IZ / IW / J` |
| `N,id,x,y,z` | 节点 | `Node3D` |
| `FILL,n1,n2,nfill,nstrt,ninc` | 等距插值节点 | 补 `Node3D`（线性插值） |
| `E,n1,n2` | 自动编号单元 | `Element3D`（号 = 当前最大 + 1） |
| `EN,eid,n1,n2` | 指定编号单元 | `Element3D` |
| `TYPE,t` / `MAT,m` / `REAL,r` | 单元三元组状态机 | `Element3D.etype` 绑定 |
| `D,node,dof,val[,dof2,val2...]` | 刚性约束 | `Boundary3D`（连续 dof 合并） |
| `D,node,ALL,val` | 全自由度约束 | `Boundary3D(dofs=(1,6))` |
| `F,node,FX\|FY\|FZ\|MX\|MY\|MZ,val` | 节点力/矩 | `NodalLoad3D` |
| `SFBEAM,elem,face,PRES,val` | 单元面压力 | `ElemLoad3D`（**有损**，见 §3） |
| `SFE,elem,face,PRES,val` | 单元面荷载 | `ElemLoad3D`（**有损**，见 §3） |
| `ACE,ax,ay,az` | 重力加速度 m/s² | `ModelSpec3D.gravity` |
| `CE,id,b,n1,dof1,c1,n2,dof2,c2,...` | 约束方程 Σcoef·u=b（**有损**：罚函数实现） | `ConstraintEquation3D`（`ces`），自由度名 UX/UY/UZ/ROTX/ROTY/ROTZ→1~6 |

> CE 说明：ANSYS 的 `CE` 没有罚系数概念。导入用 FEM-Eigen 的罚函数实现，
> 默认 `PENALTY=1e12`（与 3D `*CE_PARAMETER` 一致），结果在罚容差内满足
> `Σcoef·u=b`。重复 id 只保留第一条并告警；引用未定义节点进 `rejected`；
> 未知自由度名进 `rejected`。这些都在 `ImportReport` 中登记，绝不静默吞掉。

### 2.2 灰名单：忽略但必须报告

| 命令/段 | 处理 | 报告 |
|---|---|---|
| `/PREP7` `/BATCH` `/TITLE` `/CLEAR` `FINISH` | 段控制 | `ignored` 计数 |
| `/SOLU` 起至文件尾（含 `SOLVE` / `ANTYPE` / `/POST1` / `*DIM` / `*GET` / `*CFOPEN` / `*VWRITE` / `*CFCLOS` / `ETABLE` / `SET` / `/EXIT`） | 求解与后处理，用户未选 c | `ignored`，并提示“求解控制未导入，需自行施加” |
| `KEYOPT,1,1,0` | BEAM188 翘曲自由度选项 | `ignored` + 告警：导入后按 6 自由度 BEAM 处理 |
| `SECTYPE` / `SECNUM` | ASEC 声明/调用 | `ignored`（`SECDATA` 已读入） |
| `MP,ALPX,...` | 线膨胀系数 | `ignored` + 告警：`ModelSpec3D` 无 α 字段 |

### 2.3 黑名单：显式拒绝

遇到即进 `ImportReport.rejected`，`import_apdl_to_dat()` 抛 `ApdlImportError`；
**绝不生成半吊子模型**。

| 情形 | 拒绝理由 |
|---|---|
| `ET` 给出 `BEAM3` / `PLANE42` / `SOLID45` 等非空间梁/杆单元 | 2D/实体无法映射到 `ModelSpec3D` |
| `N,id,x,y`（仅两坐标） | 2D 节点，无法补出 z 而不猜测 |
| `*DO` / `*ENDDO` 出现在建模段 | 用户未选 c；且导出器不生成它，拒绝不影响 round-trip |
| 未知 APDL 建模命令（`K` / `L` / `V` / `*SET` / `CM` …） | 静默忽略会丢几何，必须显式拒绝 |
| `EN` 引用了未定义节点、`TYPE/MAT/REAL` 引用了未定义编号 | 引用断链 |
| `SFE` / `SFBEAM` 的面号非整数或强度非数字 | 避免把错误方向写进 `.dat` |

## 3. 有损映射：必须告警

| 源 | 目标 | 损失与处置 |
|---|---|---|
| `BEAM188` | `BEAM` | ANSYS 是 Timoshenko + 线性形函数；FEM-Eigen 3D 是 Euler-Bernoulli 三次梁。导入后 `.dat` 的位移/内力会与 BEAM188 不同（实测单单元悬臂 0.859 倍、细分后 1.0678 倍）。**不修正数值，只告警**：这正是两套内核的本征差异，强行抹平会破坏物理。 |
| `LINK180` / `LINK8` | `TRUSS` | 线性内核对 TRUSS/CABLE 的刚度装配相同；导入后失去“索”语义（`W` / `L0` / 只受拉）。若用初应变 ISTRN 表达索力（F=E·A·ISTRN），**ISTRN 完整保留、索力不丢**；只是 W/L0 垂度与只受拉判定有损。若原意需垂度，用户须改 `kind=CABLE` 并补 `W`。 |
| `LINK10` | `TRUSS` | **APDL 中 LINK10 是只拉单元，索力用初应变 ISTRN 表达：F=E·A·ISTRN。** R 的 ISTRN 被完整读入 `RealConst3D.ISTRN` → `.dat`，内核按等效节点荷载施加（`fem_3d.hpp: P=Eeff·A·istrn`），**索力不丢**，`ImportReport.cable_forces` 逐根登记 A/ISTRN/E/F 供核对。映射 `TRUSS` 而非 `CABLE` 的理由：线性内核两者轴向刚度相同，而 `validate_model_3d` 对 CABLE 有「两端等高即硬拒绝」约束，LINK10 常用于水平系杆/张紧杆，映射 CABLE 会把合法水平杆误拒。**真正有损仅两点**：(1) 只受拉单向判定——线性内核按双向轴力杆，受压不退出；(2) W/L0 垂度几何非线性——需要时补 W 走 nonlinear cable_solver。不再提示用户改 `kind=CABLE`（那是错误建议）。 |
| `SFBEAM` / `SFE` | `ElemLoad3D(dir="GZ")` | 面号 → 全局方向依赖单元局部坐标，APDL 命令流未给方向向量。统一按 GZ 记录并告警“面号未换算，须核对方向”；强度按线荷载 1:1 使用，未乘梁宽。 |
| `KEYOPT,1,1,0` | 忽略 | BEAM188 第 7 翘曲自由度在 6 自由度梁中无处安放；按 6 自由度处理。 |
| `MP,ALPX` | 忽略 | `ModelSpec3D` 温度荷载走 `*TEMPERATURE` 直接给温变，不存 α。 |
| `RMORE` 的 `SHEARZ/SHEARY/SPIN/ADDMAS` | 部分读取 | `ADDMAS` 读入 `RealConst3D.ADDMASS`；前三项为 BEAM4 剪切开关，EB 内核不使用，忽略。 |

## 4. 字段反算规则（保证同源）

1. **G 的传播**：`MP,GXY` 挂在材料上，而 `ModelSpec3D.G` 挂在实常数上。
   解析结束后按 `etype → mat, real` 把 G 写到对应 `RealConst3D.G`；
   只有 `PRXY` 时按 `G = E / (2(1+ν))` 反算；两者都没有则 `G=0` 并告警。
2. **`R` 的字段解释**由引用它的 `ET.kind` 决定，不由列位置猜：
   - BEAM：`R[1]=A, R[2]=IZ, R[3]=IY, RMORE[1]=ISTRN, RMORE[2]=J`
   - TRUSS：`R[1]=A, R[2]=ISTRN`（或 `RMORE[1]`）
   - BEAM188 ASEC：优先取 `SECDATA`，不读 `R`。
3. **约束合并**：同一节点的多个 dof 先按 value 分组，再把连续 dof 压成
   `Boundary3D.dofs=(a,b)`；不连续的拆成多条。这与 `.dat` 的 `*BOUNDARY`
   写法一致，round-trip 可逐字段比对。
4. **节点插值**：`FILL` 按 ANSYS 语义在 `n1`/`n2` 之间线性插值，`nfill`
   个新节点从 `nstrt` 起以 `ninc` 递增；端点坐标必须已定义，否则拒绝。

## 5. 验收口径（守卫 `verify_apdl_import.py`）

| # | 断言 |
|---|---|
| 1 | `examples/arch.ans`（`write_ansys_file` 生成）→ 模型含 4 个 etype、2 个 BEAM4 + 2 个 LINK180 实常数，`FILL` 补全节点，坐标逐点一致 |
| 2 | `spec3d_to_apdl(..., element="BEAM4")` 生成的 APDL → 回读后节点/单元/材料/实常数/边界/荷载逐字段相等 |
| 3 | `spec3d_to_apdl(..., element="BEAM188")` → 回读标记为 BEAM 且有 Timoshenko 告警 |
| 4 | `examples/beam.ans`（`ET,BEAM3`）→ 进 rejected，不产出模型 |
| 5 | 含 `*DO` 的命令流 → 进 rejected |
| 6 | `/SOLU` 之后的 `SOLVE` / `*VWRITE` → 进 ignored，不影响建模结果 |
| 7 | `D,node,ALL,0` 与多 dof 对 → 合并/拆分正确 |
| 8 | `SFBEAM` → `ElemLoad3D` 且有方向告警 |
| 9 | `ACE` → `gravity` 三分量一致 |
| 10 | 回读模型 `to_dat()` 能被 FEM-Eigen 3D 求解器试算（或至少通过 `validate_model_3d`） |
| 11 | 默认 12 格哈希、37 格 dat 回归不刷新；所有既有守卫不回归 |
| 12 | 独立应用包同步后，用自带 solver 对回读 `.dat` 试算通过 |
| 13 | `CE,301,0.05,3,UX,1,1,UX,-1` → `model.ces` 一条、b=0.05、dof=1、`.dat` 写出 `301 0.05 3 1 1 1 1 -1`；告警含罚函数 PENALTY 说明；未知自由度/重复 id 正确处理 |

## 6. 界面行为

- 位置：`ui_3d.py` 三维互导区，紧邻既有 MIDAS `.mct` 只读导入面板；
- 控件：`st.file_uploader("导入 ANSYS APDL（只读，不覆盖当前模型）", type=["ans","inp","txt"])`；
- 导入后展示三段报告：**已读入命令 / 已忽略命令 / 显式拒绝 + 告警**；
- LINK 初应变索力以「索力台账」展开（F=E·A·ISTRN）；APDL CE 以
  「约束方程台账」展开（Σcoef·u=b，罚函数实现）；
- 提供“导出 FEM-Eigen `.dat`”按钮，导出后可试算；
- 绝不写回 `st.session_state.doc`；拒绝时只显示原因，不给半吊子模型。

## 7. 不承诺的能力

- 不读 ANSYS 的几何建模链（`K`/`L`/`A`/`V` + 布尔）；
- 不读接触、MPC 耦合（如 `CP` / `CE` 中 ANSYS 特有的自由度集缩写语法）；
  普通 `CE,id,b,node,dof,coef,...` 约束方程已按 §2.1 读入，但不是求解控制的
  `*DO` 参数化 CE，也不含阶段相关 constant（分级顶推见
  `索鞍顶推CE耦合规格.md`）；
- 不读施工阶段、荷载步、`*DO` 参数化循环、求解控制；
- 不保证任意第三方 ANSYS 命令流都能导入——**承诺范围严格限于本仓库四个
  导出器实际生成的 APDL 子集**，以及 §2.1 白名单中语义明确的命令。
