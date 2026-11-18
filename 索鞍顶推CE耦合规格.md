# 索鞍顶推 CE 耦合方程接入规格

> 状态：**已实现并收口**（口径 C + 范围 c，2026-11-18）。
> 触发：用户 2026-11 `索鞍顶推用ce耦合方程`；用户确认 `c，c`（口径 C + 全链路）。
> 硬约束：默认零污染 / opt-in / 冻结证据逐位一致 / CTest 与守卫离线可回归 / 未授权不提交。
>
> **实现落点**：FEM-Eigen `fem_3d.hpp`（线性）与 `fem_3d_nl.hpp`（非线性）CE 装配、
> `cable_solver.cpp` 找形校验 opt-in 豁免；BridgeStudio `model_spec_3d.py` CE 契约、
> `suspension_3d.py` 顶推建造器、`model_doc.py` 自然语言识别；
> 守卫 `verify_saddle_ce.py` 30/30；第 39 项 CTest `ce_3d_constraint_equations`。
> 端到端实测（100 m 地锚式、Δ=20 mm）：S−T=0.0199999、C−S≈0、C−T=0.02，收敛。

## 1. 需求背景

### 1.1 索鞍顶推的工程含义

悬索桥（及带索鞍的斜拉桥）在加劲梁架设过程中，主缆水平分力 H 随架设进度变化，
塔顶索鞍两侧水平力不平衡，使塔身承受弯矩。为控制塔的受力与偏位，沿塔顶纵向
**顶推索鞍**（jacking the saddle）一个位移 Δ，使主缆相对塔顶滑移，塔身回正。
顶推通常分级进行，每级 Δ_i 对应一个施工阶段。

### 1.2 为什么用 CE 耦合方程

顶推的本质是「索鞍节点」与「塔顶节点 / 主缆端节点」之间施加**相对位移约束**：

```
Σ coef_i · u_(node_i, dof_i) = constant
```

CE（Constraint Equation，ANSYS 称为约束方程）正好表达这种线性位移耦合，
并且可以通过改变 constant 实现分级顶推。它比「直接给节点强制位移」更一般：
既能约束相对位移，又能保留其余自由度由平衡方程决定。

## 2. 现状调研（只读结论）

| 位置 | 现状 | 对 CE 的含义 |
|---|---|---|
| `FEM-Eigen/fem_2d.hpp` | 已有 `ConstraintEquationTerm` / `ConstraintEquation` / `ces` / `cePenalty=1e12` | 2D 语义可直接照搬到 3D（dof 1~3 → 1~6） |
| `FEM-Eigen/fem_2d.cpp` | 已有 `*CE` 解析 + 罚函数装配（约 984、1814、2092 行） | 3D 可复用同一装配公式 |
| `FEM-Eigen/input_format.md §7.10` | 已有 `*CE_PARAMETER PENALTY=1e12` 与 `*CE` 文档 | 3D `.dat` 格式与之对齐 |
| `FEM-Eigen/bridge_tied_arch_80m_apdl.inp` | 已有 ANSYS `CE,301,0.0,1,UX,1.0,101,UX,-1.0` 样例 | APDL CE 语法参考现成 |
| `FEM-Eigen/fem_3d.hpp` | **无任何** CE / MPC 契约 | 需新增数据结构与装配 |
| `BridgeStudio/model_spec_3d.py` | **无** CE 字段 | 需新增数据模型与 `.dat` 输出 |
| `BridgeStudio/APDL导入规格.md §7` | 明确「不读耦合、约束方程」 | 需按用户新要求解除 |
| `BridgeStudio/dual_plane_3d.py` | 双索面镜像不涉及索鞍 CE | 默认零污染，镜像不感知 CE |

## 3. CE 数学定义

### 3.1 通式

对第 k 条 CE：

```
c1·u(n1,d1) + c2·u(n2,d2) + ... + cn·u(nn,dn) = b
```

其中 `u(node,dof)` 为节点 node 的第 dof 个自由度位移（3D 中 dof ∈ 1~6：
UX/UY/UZ/ROTX/ROTY/ROTZ），`b` 为常数项（顶推量即写在这里）。

### 3.2 罚函数装配（与 2D 完全一致）

势能泛函：

```
Π_CE = ½ · p · (Σ ci·ui − b)²
```

对 uk 求导得到残差与刚度贡献：

```
∂Π/∂uk = p·ck·(Σ ci·ui − b)
⇒ K[k][j] += p·ck·cj       （对 CE 内所有项两两配对）
⇒ F[k]     += p·ck·b        （移到等式右端）
```

`p = cePenalty`，默认 1e12，仅在存在 CE 时启用。

### 3.3 索鞍顶推的方程形式（**已确认采用口径 C**）

用户确认 `c，c`：口径 C + 范围 c（全链路）。三种候选如下，实线为选定项：

| 方案 | CE 方程 | 语义 |
|---|---|---|
| A 直接顶推 | `UX(S) − UX(T) = Δ` | 索鞍节点 S 相对塔顶节点 T 顶推 Δ |
| B 缆鞍固结 | `UX(C) − UX(S) = 0` | 主缆端节点 C 随索鞍一起移动（无滑移） |
| **C 组合（选定）** | **A + B 两条 CE** | **索鞍相对塔顶顶推，主缆端随索鞍移动** |

分级顶推时，把 `b = Δ_i` 写入第 i 阶段的 CE constant。

**节点映射（建造器实现）**：每塔新建两个节点，原塔顶节点保留为主缆端 C：
- `T` = 塔顶**结构**节点：塔身最顶单元改连它，连 BEAM，6 自由度；
- `S` = 索鞍**孤立**节点：坐标与塔顶重合，不连单元，靠 CE 罚刚度获得自由度（3 平动）；
- `C` = 主缆端节点（原塔顶 `i_top1/i_top2`）：主缆链仍连它，只连 CABLE，3 平动自由度。

每塔 6 条 CE：CE1 `UX(S)−UX(T)=Δ`（顶推）、CE2 `UX(C)−UX(S)=0`（缆鞍固结）、
CE3/4 `UY/UZ(S)−UY/UZ(T)=0`、CE5/6 `UY/UZ(C)−UY/UZ(T)=0`（后四条把主缆竖向/
横向力经索鞍传给塔，同时让孤立节点 S 获得刚度）。净效果 `UX(C)−UX(T)=Δ`。

**为何塔顶要拆成 T/C 两个节点**：顶推的本质是主缆端相对塔身纵向滑移 Δ，
若沿用"塔顶节点直接复用主缆节点"，则主缆与塔纵向完全固结、无法表达相对位移。
拆开后主缆链塔位节点 C 不再有梁单元，会触发悬索找形"主缆必须与塔共用节点"
的防错校验——但该校验的本意（防止塔不受力）在 CE 耦合下仍满足（塔经 CE 受力），
故 `cable_solver.cpp` 在 `m.ces` 非空时豁免此校验（空 CE 时判断完全不变）。

## 4. 实现方案

### 4.1 FEM-Eigen 3D 内核（`fem_3d.hpp`）

1. 在 `Model3D` 内新增（与 2D 同构，dof 扩到 1~6）：
   ```cpp
   struct ConstraintEquationTerm {
       int node = 0; int dof = 0; double coef = 0.0;
   };
   struct ConstraintEquation {
       int id = 0; double constant = 0.0;
       std::vector<ConstraintEquationTerm> terms;
   };
   std::vector<ConstraintEquation> ces;
   double cePenalty = 1e12;
   ```
2. 在 `assembleModel3D()` 的 `a.F0 = F` 之后、Dirichlet 之前，按 §3.2 把 CE 罚项
   叠加到 K 与 F（同时更新 `a.K0` / `a.F0`，保证支座反力一致）。
3. 在 `parseModel3D`（约 3380 行附近）新增 `*CE_PARAMETER` / `*CE` 解析，
   dof 校验改为 1~6。
4. `ces` 为空时整段早退 —— **这是默认零污染的关键**。

### 4.2 `.dat` 格式（`input_format.md` 增 3D 章节）

```
*CE_PARAMETER PENALTY=1e12
*CE
<id> <b> <node1> <dof1> <coef1> <node2> <dof2> <coef2> ...
```

与 2D 同语法，仅 dof 上限从 3 改为 6。

### 4.3 BridgeStudio 模型契约（`model_spec_3d.py`）

```python
class ConstraintEquationTerm3D(BaseModel):
    node: int
    dof: int          # 1~6，Field(ge=1, le=6)
    coef: float

class ConstraintEquation3D(BaseModel):
    id: int
    constant: float = 0.0
    terms: list[ConstraintEquationTerm3D]

class ModelSpec3D:
    ces: list[ConstraintEquation3D] = Field(default_factory=list)
    ce_penalty: float = 1e12
```

`to_dat()` 在末尾追加 `*CE_PARAMETER` / `*CE` 段；`ces` 为空时不输出任何字符
（既有 37 格 dat 逐字节不变）。

### 4.4 APDL 只读导入（**可选**）

`apdl_io.py` 增加 `CE` 命令解析：`CE,id,b,n1,dof1,c1,n2,dof2,c2,...`。
APDL 自由度名 `UX/UY/UZ/ROTX/ROTY/ROTZ` → 数字 1~6。
导入结果写入 `ModelSpec3D.ces`，并在 `ImportReport.read` 计数、告警中登记
「CE 用罚函数实现，结果依赖 PENALTY」。

### 4.5 索鞍顶推专用建造器（**可选**）

在建造器中根据用户在自然语言里写明的「索鞍顶推 Δ / 分级顶推」自动生成 §3.3
的 CE（方案 A/B/C 待定），绑定到索鞍/塔顶/主缆端节点。默认不触发。

### 4.6 独立包同步

`BridgeStudio-App/solver/` 的 `fem_3d.hpp` 与核心 `.py` 同步；守卫增加独立包一致性断言。

## 5. 默认零污染保证

- `ces` 为空 → 内核装配早退、`.dat` 无新增段、12 格默认哈希与 37 格 dat 回归不变；
- 既有 38 项 CTest 全部逐位不变；新增 CE benchmark 作为第 39 项；
- APDL 导入 CE 是新增能力，不覆盖 `st.session_state.doc`；
- 所有有损映射（罚函数依赖性）进 `ImportReport.warnings`。

## 6. 验收口径（守卫与 CTest）

| # | 断言 |
|---|---|
| 1 | 2D `*CE` 既有算例逐位不变（回归） |
| 2 | 3D 空 `ces` 模型 `.dat` 与改造前逐字节一致 |
| 3 | 单条 CE `UX(a)−UX(b)=0` → a、b 水平位移相等（罚函数容差内） |
| 4 | 顶推 CE `UX(S)−UX(T)=Δ` → 相对位移精确命中 Δ |
| 5 | CE 反力贡献进入支座反力（K0/F0 同步） |
| 6 | dof 越界（0 或 7）被拒绝并给出行号 |
| 7 | APDL `CE,...` 回读 → `ModelSpec3D.ces`，告警含 PENALTY 说明 |
| 8 | 三桥型双索面 3D 算例（默认无 CE）全部不回归 |
| 9 | 38 项既有 CTest + 新增第 39 项全绿 |
| 10 | 独立包同步后自带 solver 对 CE 算例试算通过 |

## 7. 实现路径选项（**已选 c**）

| 选项 | 范围 | 说明 |
|---|---|---|
| a | 仅规格 + APDL `CE` 读入为「已读但告警，不试算」 | 最小改动，内核零风险；但顶推不能真正求解 |
| b | a + FEM-Eigen 3D CE 内核 + `ModelSpec3D` + `.dat` | 顶推可求解；新增第 39 项 CTest；不含专用建造器 |
| **c（已选）** | b + 索鞍顶推专用建造器 + 自然语言识别 + 独立包同步 | 全链路；已按口径 C 实现并收口 |

用户 `c，c` = 口径 C + 范围 c。实现结果：内核（线性+非线性）、`ModelSpec3D`、
`.dat`、专用建造器、自然语言识别、独立包同步、第 39 项 CTest、守卫 30/30 全部落地。
**2026-11-18 收口补充（用户选 ③）**：§4.4 的 APDL `CE` 只读导入已落地——
`apdl_io.py` 解析 `CE,id,b,node,dof,coef,...`（自由度名 UX/UY/UZ/ROTX/ROTY/
ROTZ→1~6），写入 `ModelSpec3D.ces`，告警登记罚函数 PENALTY 依赖；UI 增
「约束方程台账」；`verify_apdl_import.py` 由 74/74 增至 89/89（新增 15 条
CE 断言）；独立包 `apdl_io.py`/`ui_3d.py` 已同步。`APDL导入规格.md` §2.1/
§5-#13 同步解除「不读耦合方程」的旧表述。

仍未包含项：**分级顶推 stage**（每级 Δ_i 写入第 i 阶段 constant）——调研确认
内核 `solveModel3DStages` / `solveModel3DStagesNonlinear` 构造 `mc` 时不复制
`m.ces`，CE 当前完全不进 stage 路径，且悬索建造器不生成 stages。要做真正的
分级顶推需内核级改动（stage 路径注入 CE + 每阶段 constant 可变 + 建造器生成
顶推阶段 + 求解链接入）。该周期已按用户指令立项，规格见 §8。

---

## 8. 分级顶推 stage（阶段二，opt-in）

> 状态：**规格已定，实现中**。触发：用户 2026-11-18 选「2、3」中的第 2 项。
> 目标：把成桥态单次顶推 Δ 扩展为**逐施工阶段顶推**——第 i 阶段顶推量 Δ_i
> 写入该阶段 CE1 的 constant，塔顶两侧水平力随架设进度逐级平衡。

### 8.1 现状调研（逐条代码依据）

| 位置 | 现状 | 对分级顶推的含义 |
|---|---|---|
| `fem_3d.hpp::solveModel3DStages`（1450） | `mc.nodes/constraints/loads` 复制，**不复制 `mc.ces`**；每阶段解**增量**（`mc.loads = st.loads`） | 需复制 CE + 明确 constant 口径 |
| `fem_3d_nl.hpp::solveModel3DStagesNonlinear`（2780） | 同样不复制 `mc.ces`；末尾调 `solveModel3DNonlinear(mc,...)`（2970） | 该函数内部已装配 CE（1008~1125），只要 `mc.ces` 就位 |
| `fem_3d.hpp::constrainDisconnected3D`（1423） | 把所有孤立节点逐自由度硬约束 | **风险**：索鞍孤立节点 S 若无单元连接会被硬约束，CE 失效 |
| `fem_3d_nl.hpp`（2008 注释） | 「不能直接调 `constrainDisconnected3D`——它会把有弹簧支承的节点也硬约束」 | 佐证 CE 引用节点必须豁免硬约束 |
| `fem_3d_nl.hpp::2530~2611 solveCompletionState3D` | 成桥态单次顶推：`m.ces` 由主链路读入，CE 装配在 `solveModel3DNonlinear` 内 | 成桥态已验证 S−T=Δ，机制可照搬 |
| `model_spec_3d.py::Stage3D`（478） | 有 `id/activate/deactivate/loads/tensions/unstrained/prestress/creep_dphi/time`，无 CE 字段 | 需加每阶段 CE constant 覆盖字段 |
| `suspension_3d.py`（105/185/485） | `saddle_jack_dx` 生成每塔 6 条 CE、constant=Δ；**不生成 stages** | 需加生成顶推阶段序列 |
| `fem_3d.hpp::readModel3D`（3463 `*CE`） | CE 用 `<id> <b> <node> <dof> <coef>...` | 每阶段常量覆盖用新键表达 |

### 8.2 CE constant 口径（**关键决策**）

`fem_3d.hpp` 阶段求解是**增量法**（见 1434 注释：`mc.loads = st.loads` 不是累计），
每一步解的是「本阶段荷载在当前构型上的增量」。因此：

- **CE constant 采用「增量口径」**：第 i 阶段 `*STAGE` 里的 `CE=` 写的是
  **本阶段新增顶推量 δ_i = Δ_i − Δ_(i−1)**，不是累计 Δ_i。
- 原因：CE 约束 `Σci·ui = b` 里的 `u` 是本阶段求解的位移向量（增量 `dU`），
  常量写 δ_i 才使累计滑移 `Σ δ = Δ_i`；若写累计 Δ_i，塔顶会逐级过顶推。
- 建造器负责把用户给的「分级顶推 Δ_1, Δ_2, …, Δ_n（累计目标）」差分成本阶段
  δ_i 写进各 `*STAGE`。用户看到的仍是累计顶推量，差分是内部实现细节。

### 8.3 内核最小切口（三处必须成对）

1. **`Stage3D` 新增每阶段常量覆盖字段**：`std::vector<std::pair<int,double>>
   ceConstants`（CE id → 本阶段 constant 覆盖），默认空 = 不覆盖、零污染。
   `*STAGE` 解析新键 `CE=<id>:<b>,<id>:<b>;...`（`;` 分隔多组，`:` 分 id 与值）。
2. **两个 stage 求解器复制 CE 并应用覆盖**：`solveModel3DStages` 与
   `solveModel3DStagesNonlinear` 构造 `mc` 时 `mc.ces = m.ces`；对本阶段
   `ceConstants` 中每个 id 覆盖对应 CE 的 constant。覆盖只作用于 `mc` 副本，
   `m.ces` 原值不变。
3. **`constrainDisconnected3D` 对 CE 引用节点豁免**：当 `m.ces` 非空时，
   凡被任一 CE 的 term 引用的节点，不加入硬约束——让 S 靠 CE 罚刚度获得自由度。
   线性与非线性路径各一处；`m.ces` 为空时豁免逻辑完全不触发，既有算例逐位不变。

### 8.4 BridgeStudio 切口

- `model_spec_3d.py::Stage3D` 新增 `ce_constants: dict[int, float]`，`to_dat()`
  在 `CE=` 键非空时追加（按 id 排序，保证可复现）；`validate_model_3d()` 校验
  覆盖的 id 必须在 `model.ces` 中存在，否则报错（防静默丢弃）。
- `suspension_3d.py::build_suspension_3d` 新增 opt-in 参数
  `jack_stages: list[float] | None = None`（累计顶推量序列）。缺省 `None` 时不生成
  stages、行为和现在完全一致；给值时：CE 按 §3.3 生成（constant 用首个 Δ），
  并为每个 Δ_i 生成一个 `*STAGE`，`CE=` 写差分 δ_i，其余恒载阶段照旧。
- `model_doc.py` 自然语言识别「分级顶推 / 逐阶段顶推 + 多个 Δ」→ `jack_stages`。

### 8.5 默认零污染

- `jack_stages=None` 且 `saddle_jack_dx=0`：不生成 CE、不生成 stage、`.dat` 逐位不变；
- `m.ces` 为空：两个 stage 求解器 `mc.ces` 为空，CE 段早退、`constrainDisconnected3D`
  豁免不触发——既有施工阶段算例（CB3D13 等）与 12+37 格冻结证据不变；
- 覆盖字段为空：stage CE constant 沿用 `*CE` 原值，行为等价单次顶推。

### 8.6 验收断言（第 41 项 CTest + verify_saddle_ce.py）

1. 分级顶推 `.dat` 重放：第 i 阶段末 `UX(C)−UX(T)` 累计值命中 Δ_i（容差同 §6-#4）；
2. 每阶段 constant 增量口径：Δ=[0.01,0.02,0.03] → 三阶段 δ=[0.01,0.01,0.01]；
3. 无 `CE=` 覆盖：stage CE 沿用 `*CE` 原常量；
4. `constrainDisconnected3D` 豁免：S 节点未被硬约束、CE 命中；
5. 覆盖 id 不存在 / `jack_stages` 非递增 → 显式拒绝；
6. 默认零污染：无顶推/无 CE 的 stage 算例结果与改造前逐字节一致；
7. 线性与非线性两条 stage 路径各自命中分级顶推量。
