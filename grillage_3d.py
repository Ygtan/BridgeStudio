"""grillage_3d.py
================
三跨连续箱梁空间梁格建造器（P1 基准算例）。

梁格法建模思路
--------------
- 纵桥向：把单箱截面切分为 n_lines 条纵梁（含实常数 A/IY/J 各 1/n），
  沿 x 布置于箱宽 B 上（边线位于腹板处 y=±B/2）。
- 横桥向：每个横梁断面用横梁单元连接相邻纵梁线，实常数取顶板
  条带（宽 = 横梁间距 panel），保证荷载横向分布能力。
- 支座：双支座体系——每个支承断面在两侧边纵梁线约束 dof 2,3
  （横桥向 + 竖向），一个桥台 additionally 约束 dof 1（纵桥向固定支座）。
- 荷载：桥面均布 q（全宽 N/m）→ 纵梁线均摊 → 节点集中力（dof=3 向下），
  梁端节点取半段。全部挂入施工阶段 1（一次性成桥）。

组件分块编号（B1，统一约定见 X20_接入约定.md §5）
----------------------------------------------------
下表是统一编号规范在本建造器内的落地实例，不是第二份规范；同模型内不得撞号，
新增编号段须先登记契约 §5。
- 默认（legacy）编号：节点 (i+1)*1000 + 序号；纵梁单元 (i+1)*100 + 序号；
  横梁从 500 起。**默认参数下逐位不变**，不刷新 .dat 黄金基线。
- 宽编号（wide）：n_lines ≥ 5 或总梁段数 > 99 时自动切换到
  `NumberingPlan` 的大号段，段间留 stride，保证同一模型内不撞号；
  阶段激活与张拉链按元素元数据查表，不再从单元号反解，见契约 §5。

单位：m / N / Pa。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from model_spec_3d import (
    Boundary3D,
    Element3D,
    ElemTypeDef3D,
    Influence3D,
    Lane3D,
    Material3D,
    ModelSpec3D,
    Node3D,
    NodalLoad3D,
    Prestress3D,
    RealConst3D,
    RebarSpec3D,
    Stage3D,
)


@dataclass(frozen=True)
class NumberingPlan:
    """梁格编号方案：legacy 逐位不变，wide 自动扩容。

    ``n_seg`` 是每条纵梁线的梁段数（节点序号最大 = n_seg）。三段号空间
    互不重叠的条件：

    - 节点 ``(li+1)*node_stride + i``，需 ``node_stride > n_seg``；
    - 纵梁 ``(li+1)*elem_stride + i+1``，需 ``elem_stride > n_seg``；
    - 横梁 ``cross_base + seq``，需 ``cross_base > n_lines*elem_stride + n_seg``。

    legacy 常量是历史契约（``(li+1)*1000`` / ``(li+1)*100`` / ``500`` 起）：
    只有 ``n_lines <= 4`` 且 ``n_seg <= 99`` 才安全，此时纵梁最大号
    ``n_lines*100 + n_seg <= 499 < 500``。默认参数必须落在这档，否则
    会改变既有 ``.dat`` 黄金基线（契约 §9 禁止静默换号）。
    """

    legacy: bool
    node_stride: int
    elem_stride: int
    cross_base: int

    def node(self, line: int, i: int) -> int:
        return (line + 1) * self.node_stride + i

    def girder(self, line: int, i: int) -> int:
        return (line + 1) * self.elem_stride + i + 1

    def cross(self, seq: int) -> int:
        return self.cross_base + seq


def numbering_plan(n_lines: int, n_seg: int) -> NumberingPlan:
    """按容量选编号方案；返回对象是节点 / 单元编号的唯一入口。

    不做「超限就静默换号」：`legacy` 只在默认容量内使用，`wide` 的号段
    在 `X20_接入约定.md` §5 登记，且三段不重叠在这里硬校验。
    """
    if n_lines < 3 or n_seg < 1:
        raise ValueError("编号方案要求 n_lines ≥ 3 且总梁段数 ≥ 1")
    if n_lines <= 4 and n_seg <= 99:
        plan = NumberingPlan(legacy=True, node_stride=1000,
                             elem_stride=100, cross_base=500)
    else:
        stride = max(10000, n_seg + 2)
        plan = NumberingPlan(legacy=False, node_stride=stride,
                             elem_stride=stride,
                             cross_base=(n_lines + 1) * stride)
    if not (plan.node_stride > n_seg
            and plan.elem_stride > n_seg
            and plan.cross_base > n_lines * plan.elem_stride + n_seg):
        raise ValueError("梁格编号段容量不足，无法保证单元号唯一")
    return plan


def box_section(B: float, h: float, tf: float, tw: float) -> dict:
    """单箱单室截面几何特性（薄壁近似，圆角忽略）。

    返回 A / IY（竖向弯曲）/ J（Bredt 扭转）/ WT / WB。
    """
    hw = h - 2 * tf                      # 腹板净高
    A = 2 * B * tf + 2 * hw * tw
    d = h - tf                           # 上下翼缘形心距
    Iy = 2 * (B * tf ** 3 / 12 + B * tf * (d / 2) ** 2) + 2 * (tw * hw ** 3 / 12)
    # Bredt：J = 4·A0² / ∮(ds/t)
    A0 = (B - tw) * hw
    perimeter = 2 * (B - tw) / tf + 2 * hw / tw
    J = 4 * A0 * A0 / perimeter if perimeter > 0 else 0.0
    return {
        "A": A,
        "IY": Iy,
        "J": J,
        "WT": Iy / (h / 2),              # 上缘（顶板）模量
        "WB": Iy / (h / 2),              # 下缘（底板）模量
        # 下面三个是强度验算要用的，力学求解不读它们：
        "AW": 2 * hw * tw,               # 腹板总面积（两道腹板），剪应力 τ=V/AW
        "HW": hw,                        # 腹板净高
        "T_MAX": max(tf, tw),            # 控制板厚 m（查 JTG D64 表 3.2.1 的档位）
    }


def _rebar_spec(box: dict, B: float, h: float, tf: float, tw: float,
                n_lines: int, rebar_ratio: float, as_: float,
                stirrup_dia: float, stirrup_sv: float,
                rebar_ratio_neg: float | None = None) -> RebarSpec3D | None:
    """按全截面配筋率估一条纵梁的配筋，供承载力验算用。

    梁格把单箱切成 n_lines 条纵梁，截面属性（A/IY/AW）各 1/n_lines，配筋
    也各 1/n_lines——这样单条纵梁的「内力 Md ↔ 截面 ↔ 配筋」三者自洽。
    箱梁顶板受压时近似为工形：bf=箱宽/n_lines、hf=顶板厚、b=两道腹板宽之和
    /n_lines。``rebar_ratio <= 0`` 表示不配筋（返回 None，验算跳过）。

    负弯矩区（支点，底板受压 / 顶板受拉）单独登记一套配筋：
    ``rebar_ratio_neg`` 为顶板纵筋配筋率（None = 与跨中底板筋相同）。本
    建造器的单箱单室截面顶底板等宽等厚（`box_section` 里 A=2·B·tf+…），
    所以 bf_neg/hf_neg 沿用 bf/hf；若换成底板较窄的真实箱梁，把 bf_neg
    改小即可，验算会自动按倒 T 形重算 Mu，不再拿顶板翼缘冒充。
    """
    if rebar_ratio <= 0.0:
        return None
    As = rebar_ratio * box["A"] * 1e6 / n_lines        # 底板受拉纵筋 mm²
    ratio_top = rebar_ratio if rebar_ratio_neg is None else rebar_ratio_neg
    As_top = ratio_top * box["A"] * 1e6 / n_lines       # 顶板纵筋 mm²
    b = 2.0 * tw * 1e3 / n_lines                       # 腹板宽 mm（两道均分）
    bf = B * 1e3 / n_lines                             # 受压翼缘宽 mm
    hf = tf * 1e3
    hh = h * 1e3
    as_mm = as_ * 1e3
    Asv = 0.0
    if stirrup_dia > 0.0:
        # 两道腹板 × 双肢箍，均分到单条纵梁
        Asv = 4.0 * (math.pi * stirrup_dia ** 2 / 4.0) / n_lines
    return RebarSpec3D(
        h=hh, b=b, bf=bf, hf=hf, As=As, as_=as_mm,
        Asv=Asv, sv=stirrup_sv,
        negative_given=True,
        bf_neg=bf, hf_neg=hf,
        As_neg=As_top, Asp_neg=As, as_neg=as_mm,
    )


def build_box_grillage_3d(
    spans: tuple[float, float, float] = (30.0, 50.0, 30.0),
    B: float = 12.0,
    h: float = 2.5,
    tf: float = 0.28,
    tw: float = 0.45,
    n_lines: int = 3,
    panel: float = 2.5,
    E: float = 34.5e9,
    nu: float = 0.2,
    dens: float = 2500.0,
    grade: str = "C50",                  # 混凝土等级（应力验算用，不参与求解）
    q_deck: float = 100e3,               # 全宽桥面均布荷载 N/m（含自重），向下
    td: float = 0.6,                     # 支承断面横隔板厚度 m
    staged: bool = False,                # True=逐跨浇筑三阶段；False=一次成桥
    stage_tension_kN: float = 0.0,      # 逐跨张拉力 kN/跨（全宽），0=不张拉
    stage_tension_e: float = 0.0,       # 钢束端部偏心 m（形心上方为正）
    stage_tension_loss: float = 1.0,    # 总损失系数 Pe=P·loss
    stage_tension_s: float = 0.0,       # 曲线筋矢高 m（0=直线筋）
    lane_code: str = "",                 # "JTG_D60"=中线车道自动荷载 + 跨中影响线
    rebar_ratio: float = 0.012,          # 跨中底板受拉纵筋配筋率（按全截面 A，验算用）
    rebar_ratio_neg: float | None = None,  # 支点顶板受拉纵筋配筋率（None=同跨中）
    as_: float = 0.10,                   # 受拉钢筋合力点至受拉边缘距离 m
    stirrup_dia: float = 12.0,           # 箍筋直径 mm（双肢，0=不配箍筋）
    stirrup_sv: float = 150.0,           # 箍筋间距 mm
    title: str = "三跨连续箱梁 3D 梁格",
) -> ModelSpec3D:
    """生成三跨连续箱梁空间梁格模型。

    ``stage_tension_kN > 0``：逐跨施工的每个阶段给该跨张拉一束/纵梁线，
    走 3D 内核 ``*STAGE ... PRESTRESS=`` 的等效荷载法。口径：

    * 张拉力是**全宽总量**，按纵梁线均分到 ``n_lines`` 条链上
      （每束 ``P = stage_tension_kN·1000/n_lines``），这样全截面轴压合计
      仍等于 P·loss，与 2D 单梁线的 ``*STAGE_PRESTRESS`` 同量级；
    * ``e`` 为端部偏心（m，形心上方为正）、``s`` 为曲线筋矢高（m，
      0 = 直线筋）、``loss`` 是**总**损失系数，不是 JTG 3362 分项损失
      （分项要走 ``Prestress3D.loss_ref`` / ``LOSSREF=``）；
    * 必须 ``staged=True``；参数非法一律硬报错，不静默忽略，也不拿
      「一次成桥」兜底。默认全 0 时完全不写 ``PRESTRESS=``，既有 ``.dat``
      逐位不变。
    """
    if n_lines < 3 or n_lines % 2 == 0:
        raise ValueError("n_lines 必须为 ≥3 的奇数（含中梁线，对称）")
    if stage_tension_kN > 0.0:
        if not staged:
            raise ValueError("3D 梁格逐跨张拉要求 staged=True")
        # 编号容量不再在这里硬拦：`numbering_plan()` 按 n_lines / 总梁段数
        # 自动选 legacy 或 wide 号段，阶段激活与张拉链都按元素元数据查表。
        # 默认参数仍走 legacy，.dat 逐位不变；扩容走 wide，无需刷新黄金基线。
        if not 0.0 < stage_tension_loss <= 1.0:
            raise ValueError("stage_tension_loss 必须在 (0, 1]")
        if abs(stage_tension_e) > h / 2:
            raise ValueError(
                f"钢束偏心 |e|={abs(stage_tension_e):g} m 超出截面半高 {h/2:g} m")
        if abs(stage_tension_s) > h / 2:
            raise ValueError(
                f"曲线筋矢高 |s|={abs(stage_tension_s):g} m 超出截面半高 {h/2:g} m")
    elif stage_tension_kN < 0.0:
        raise ValueError("stage_tension_kN 不能为负（0 = 不张拉）")

    L_tot = sum(spans)
    G = E / (2 * (1 + nu))

    # ---- 节点离散：每跨独立等分（n=ceil(span/panel)），支承断面必为节点 ----
    xs: list[float] = [0.0]
    support_idx: list[int] = [0]
    for s in spans:
        n_seg = max(int(-(-s // panel)), 1)     # ceil(s/panel)
        for j in range(1, n_seg + 1):
            xs.append(xs[-1] + s / n_seg)
        support_idx.append(len(xs) - 1)
    dx_mean = L_tot / (len(xs) - 1)
    # 编号方案在节点/单元生成之前定死：默认参数落 legacy（与黄金基线逐位一致），
    # 超出容量自动落 wide。之后所有编号都走 plan，不再散写 `*100` / `500` 起。
    n_seg_total = len(xs) - 1
    plan = numbering_plan(n_lines, n_seg_total)

    # ---- 断面几何 ----
    box = box_section(B, h, tf, tw)
    # 普通断面横梁：闭合箱横向条带的框架刚度（Hambly 梁格法——
    # 顶/底板条带视为横向梁的上下翼缘，梁高取箱高）
    strip_w = dx_mean
    d_box = h - tf                          # 上下翼缘形心距
    cross = {
        "A": 2 * tf * strip_w,
        "IY": 2 * tf * strip_w * (d_box / 2) ** 2
              + 2 * tf ** 3 * strip_w / 12,
        "J": 2 * (tf * strip_w) ** 3 / 3,
    }
    # 支承断面横梁：实体横隔板（深梁，承担双支座间传载）
    diaph = {
        "A": h * td,
        "IY": td * h ** 3 / 12,
        "J": (2 * B + 2 * h) * td ** 3 / 3,   # 细长闭合环扭转近似（薄板条之和）
    }

    # ---- 纵梁线位置（对称，边线在腹板处）----
    ys = [-B / 2 + i * B / (n_lines - 1) for i in range(n_lines)]

    # ---- 节点（组件分块：plan.node(线号, 序号)）----
    nodes = []
    node_id = plan.node
    for li in range(n_lines):
        for i, x in enumerate(xs):
            nodes.append(Node3D(id=node_id(li, i), x=x, y=ys[li], z=0.0))

    # ---- 单元类型/材料/实常数 ----
    materials = [Material3D(id=1, E=E, dens=dens, grade=grade)]
    reals = [
        RealConst3D(id=1, A=box["A"] / n_lines, IY=box["IY"] / n_lines,
                    IZ=box["A"] / n_lines * (B / n_lines) ** 2 / 8,  # 横向弯曲粗估
                    J=box["J"] / n_lines, G=G,
                    WT=box["WT"] / n_lines, WB=box["WB"] / n_lines,
                    AW=box["AW"] / n_lines,          # 腹板也按纵梁线均分
                    rebar=_rebar_spec(box, B, h, tf, tw, n_lines,
                                      rebar_ratio, as_, stirrup_dia, stirrup_sv,
                                      rebar_ratio_neg)),
        RealConst3D(id=2, A=cross["A"], IY=cross["IY"], IZ=cross["A"] * panel ** 2 / 12,
                    J=cross["J"], G=G, AW=cross["A"] / 1.5),
        RealConst3D(id=3, A=diaph["A"], IY=diaph["IY"], IZ=diaph["A"] * B ** 2 / 12,
                    J=diaph["J"], G=G, AW=diaph["A"] / 1.5),
    ]
    etypes = [
        ElemTypeDef3D(id=1, kind="BEAM", mat=1, real=1),   # 纵梁
        ElemTypeDef3D(id=2, kind="BEAM", mat=1, real=2),   # 普通断面横梁
        ElemTypeDef3D(id=3, kind="BEAM", mat=1, real=3),   # 支承断面横隔板
    ]

    # ---- 单元：纵梁 + 横梁，编号统一走 plan，同时登记定位元数据 ----
    # 阶段激活要按「线号 + 段号 / 断面 x」定位，不能从单元号反解：
    # wide 号段不再是 (li+1)*100+序号，反解会静默指错单元。
    elements = []
    girder_meta: list[tuple[int, int, int]] = []   # (eid, 线号, 段号)
    cross_meta: list[tuple[int, float]] = []       # (eid, 断面 x)
    for li in range(n_lines):
        for i in range(len(xs) - 1):
            eid = plan.girder(li, i)
            elements.append(Element3D(id=eid, etype=1,
                                      n1=node_id(li, i), n2=node_id(li, i + 1)))
            girder_meta.append((eid, li, i))
    cross_seq = 0
    support_set = set(support_idx)
    for i in range(len(xs)):
        et = 3 if i in support_set else 2    # 支承断面用横隔板刚度
        for li in range(n_lines - 1):
            eid = plan.cross(cross_seq)
            elements.append(Element3D(id=eid, etype=et,
                                      n1=node_id(li, i), n2=node_id(li + 1, i)))
            cross_meta.append((eid, xs[i]))
            cross_seq += 1
    if len({e.id for e in elements}) != len(elements):
        raise ValueError("梁格单元编号重复：编号方案容量不足，请核对契约 §5 号段")

    # ---- 边界：双支座 + 一个纵向固定点 ----
    boundaries = []
    for i in sorted(support_idx):
        for li in (0, n_lines - 1):          # 边纵梁线 = 腹板/支座位置
            boundaries.append(Boundary3D(node=node_id(li, i), dofs=(2, 3)))
    # 纵桥向固定：起始桥台中梁线（防刚体平动，尽量少约束）
    boundaries.append(Boundary3D(node=node_id(n_lines // 2, min(support_idx)),
                                 dofs=(1, 1)))

    # ---- 荷载：q 全宽均摊到各线，节点受荷宽度取相邻段均值 ----
    q_line = q_deck / n_lines
    trib = []                              # 每个节点的受荷宽度
    for i in range(len(xs)):
        left = (xs[i] - xs[i - 1]) / 2 if i > 0 else 0.0
        right = (xs[i + 1] - xs[i]) / 2 if i < len(xs) - 1 else 0.0
        trib.append(left + right)

    # ---- 施工阶段 ----
    if staged:
        # 逐跨浇筑：阶段 k 激活第 k 跨内全部构件，荷载只挂本跨
        # （单元按跨度分段：纵梁按段中点、横梁按节点归属）
        sup_x = [xs[i] for i in support_idx]
        def span_of_x(x: float) -> int:
            for k in range(len(spans)):
                if x <= sup_x[k + 1] + 1e-6:
                    return k
            return len(spans) - 1

        stages = []
        for k in range(len(spans)):
            act = []
            # 按元数据定位（不再 `e.id < 500` 反解）；顺序仍是先纵梁后横梁，
            # 与旧实现一致 → legacy 默认 .dat 的 ACTIVE= 逐位不变。
            for eid, _li, i in girder_meta:
                xm = (xs[i] + xs[i + 1]) / 2
                if span_of_x(xm) == k:
                    act.append(eid)
            for eid, x in cross_meta:
                if span_of_x(x) == k:
                    act.append(eid)
            # 本跨受荷宽度（跨界节点只取跨内半段）
            loads_k = []
            x_lo, x_hi = sup_x[k], sup_x[k + 1]
            for li in range(n_lines):
                for i in range(len(xs)):
                    if not (x_lo - 1e-6 <= xs[i] <= x_hi + 1e-6):
                        continue
                    left = (xs[i] - xs[i - 1]) / 2 if i > 0 else 0.0
                    right = (xs[i + 1] - xs[i]) / 2 if i < len(xs) - 1 else 0.0
                    w = 0.0
                    if i > 0 and xs[i] - left / 2 >= x_lo - 1e-6:
                        w += left
                    if i < len(xs) - 1 and xs[i] + right / 2 <= x_hi + 1e-6:
                        w += right
                    if w > 0:
                        loads_k.append(NodalLoad3D(node=node_id(li, i), dof=3,
                                                   value=-q_line * w))
            # 逐跨张拉：本跨每条纵梁线各挂一束直线/曲线筋。链用
            # `plan.girder(li, i)` 取号（i 从本跨左支承断面到前一格），
            # legacy/wide 都对；这些单元在同一阶段激活、首尾相接成开链，
            # 内核的 PRESTRESS= 链校验才过。
            ps_list = []
            if stage_tension_kN > 0.0:
                act_set = set(act)
                i0, i1 = support_idx[k], support_idx[k + 1]
                for li in range(n_lines):
                    chain = [plan.girder(li, i) for i in range(i0, i1)]
                    missing = [eid for eid in chain if eid not in act_set]
                    if missing or len(chain) < 1:
                        raise ValueError(
                            f"第 {k + 1} 跨纵梁线 {li} 的张拉链未全部激活：{missing}")
                    ps_list.append(Prestress3D(
                        elems=chain,
                        P=stage_tension_kN * 1e3 / n_lines,
                        e=stage_tension_e,
                        s=stage_tension_s,
                        loss=stage_tension_loss,
                    ))
            stages.append(Stage3D(id=k + 1, activate=act,
                                  loads=loads_k, prestress=ps_list))
    else:
        loads = []
        for li in range(n_lines):
            for i in range(len(xs)):
                loads.append(NodalLoad3D(node=node_id(li, i), dof=3,
                                         value=-q_line * trib[i]))
        stages = [Stage3D(id=1, activate=[e.id for e in elements], loads=loads)]

    # ---- 车道荷载与影响线（可选）----
    influences = []
    lanes = []
    if lane_code:
        c_line = n_lines // 2
        mid_i = (support_idx[1] + support_idx[2]) // 2      # 中跨跨中
        influences.append(Influence3D(node=node_id(c_line, mid_i), dof=3))
        lanes.append(Lane3D(
            nodes=[node_id(c_line, i) for i in range(len(xs))],
            dof=3, code=lane_code))

    return ModelSpec3D(
        title=title,
        nodes=nodes,
        materials=materials,
        reals=reals,
        etypes=etypes,
        elements=elements,
        boundaries=boundaries,
        stages=stages,
        influences=influences,
        lanes=lanes,
    )
