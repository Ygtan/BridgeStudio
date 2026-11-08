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

组件分块编号（X20 SKILL.md 规范）
---------------------------------
- 节点：纵梁线 i（0 起）编号 (i+1)*1000 + 节点序号
- 单元：纵梁线 i 编号 (i+1)*100 + 序号；横梁编号 500 起

单位：m / N / Pa。
"""

from __future__ import annotations

import math

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
    RealConst3D,
    RebarSpec3D,
    Stage3D,
)


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
                stirrup_dia: float, stirrup_sv: float) -> RebarSpec3D | None:
    """按全截面配筋率估一条纵梁的配筋，供承载力验算用。

    梁格把单箱切成 n_lines 条纵梁，截面属性（A/IY/AW）各 1/n_lines，配筋
    也各 1/n_lines——这样单条纵梁的「内力 Md ↔ 截面 ↔ 配筋」三者自洽。
    箱梁顶板受压时近似为工形：bf=箱宽/n_lines、hf=顶板厚、b=两道腹板宽之和
    /n_lines。``rebar_ratio <= 0`` 表示不配筋（返回 None，验算跳过）。
    """
    if rebar_ratio <= 0.0:
        return None
    As = rebar_ratio * box["A"] * 1e6 / n_lines        # m² → mm²
    b = 2.0 * tw * 1e3 / n_lines                       # 腹板宽 mm（两道均分）
    bf = B * 1e3 / n_lines                             # 受压翼缘宽 mm
    hf = tf * 1e3
    hh = h * 1e3
    as_mm = as_ * 1e3
    Asv = 0.0
    if stirrup_dia > 0.0:
        # 两道腹板 × 双肢箍，均分到单条纵梁
        Asv = 4.0 * (math.pi * stirrup_dia ** 2 / 4.0) / n_lines
    return RebarSpec3D(h=hh, b=b, bf=bf, hf=hf, As=As, as_=as_mm,
                       Asv=Asv, sv=stirrup_sv)


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
    lane_code: str = "",                 # "JTG_D60"=中线车道自动荷载 + 跨中影响线
    rebar_ratio: float = 0.012,          # 纵向受拉钢筋配筋率（按全截面 A，验算用）
    as_: float = 0.10,                   # 受拉钢筋合力点至受拉边缘距离 m
    stirrup_dia: float = 12.0,           # 箍筋直径 mm（双肢，0=不配箍筋）
    stirrup_sv: float = 150.0,           # 箍筋间距 mm
    title: str = "三跨连续箱梁 3D 梁格",
) -> ModelSpec3D:
    """生成三跨连续箱梁空间梁格模型。"""
    if n_lines < 3 or n_lines % 2 == 0:
        raise ValueError("n_lines 必须为 ≥3 的奇数（含中梁线，对称）")

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

    # ---- 节点（组件分块：纵梁线 i 节点编号 (i+1)*1000+j）----
    nodes = []
    node_id = lambda line, i: (line + 1) * 1000 + i
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
                                      rebar_ratio, as_, stirrup_dia, stirrup_sv)),
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

    # ---- 单元：纵梁（分块编号 101/201/301…）+ 横梁（501…）----
    elements = []
    for li in range(n_lines):
        base = (li + 1) * 100
        for i in range(len(xs) - 1):
            elements.append(Element3D(id=base + i + 1, etype=1,
                                      n1=node_id(li, i), n2=node_id(li, i + 1)))
    eid_cross = 500
    support_set = set(support_idx)
    for i in range(len(xs)):
        et = 3 if i in support_set else 2    # 支承断面用横隔板刚度
        for li in range(n_lines - 1):
            elements.append(Element3D(id=eid_cross, etype=et,
                                      n1=node_id(li, i), n2=node_id(li + 1, i)))
            eid_cross += 1

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
            for e in elements:
                if e.id < 500:                    # 纵梁：按段中点定位
                    li = e.id // 100 - 1
                    i = e.id - (li + 1) * 100 - 1
                    xm = (xs[i] + xs[i + 1]) / 2
                else:                             # 横梁：按两端节点 x 定位
                    n1 = next(n for n in nodes if n.id == e.n1)
                    xm = n1.x
                if span_of_x(xm) == k:
                    act.append(e.id)
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
            stages.append(Stage3D(id=k + 1, activate=act, loads=loads_k))
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
