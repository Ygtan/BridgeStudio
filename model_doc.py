"""统一模型外壳 ModelDoc —— 界面与求解器之间的唯一类型（C 方案）。

为什么需要这一层
---------------
改造前存在两条互不相通的管线：

- `ModelSpec` 是**声明式**的（跨度/支座/单一截面/荷载），节点由求解器离散；
- `BridgeSpec` 是**展开式**的（直接持有 nodes/elements 表）。

两者抽象层级不同，于是 `app.py` 里到处是 `if 是桥 else 是梁`，桥梁参数
（`bp_span` 等）也散落在界面层的 `session_state` 里。本模块把"声明"与
"展开"显式分成两段，让两种结构走同一节奏：

    params（唯一可编辑、可序列化）
        │  build()
        ▼
    model（展开产物，可随时丢弃重建）
        │  to_dat()
        ▼
    .dat（唯一求解契约）

设计约束（与项目既有铁律一致）
------------------------------
1. `params` 是唯一真源；界面只写它，绝不直接改展开后的模型。
2. `build()` 必须幂等、够快（毫秒级），允许每次 rerun 重建。
3. `validate()` 用中文报错，且**不抛异常**——界面直接展示即可。
4. `to_dat()` 是唯一对外契约，不把 nodes/elements 暴露给界面。
5. 能力（能否草算 / 能否互校）由 `capability()` 自述，不在界面里写死。
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from model_spec import Load, ModelSpec, Section, Support

Kind = Literal["beam", "arch", "truss", "beam_bridge",
               "cable_stayed", "suspension"]
Dimension = Literal["2d", "3d"]

#: (kind, dimension) -> 建造器键（DEFAULTS / FIELDS 的键）。
#: 桥型 kind 只有 6 种；维度是正交的第二维。建造器键比 kind 多几个维度变体：
#: grillage3d（梁桥三维梁格）、arch_3d（拱桥三维施工）、cable_stayed_2d /
#: suspension_2d（斜拉/悬索平面索面简化，档三落地）。
BUILDERS: dict[tuple[str, str], str] = {
    ("beam", "2d"): "beam",
    ("beam", "3d"): "ps_beam",
    ("arch", "2d"): "arch",
    ("arch", "3d"): "arch_3d",
    ("truss", "2d"): "truss",
    ("truss", "3d"): "truss_3d",
    ("beam_bridge", "2d"): "beam_bridge",
    ("beam_bridge", "3d"): "grillage3d",
    ("cable_stayed", "2d"): "cable_stayed_2d",
    ("cable_stayed", "3d"): "cable_stayed",
    ("suspension", "2d"): "suspension_2d",
    ("suspension", "3d"): "suspension",
}

#: (kind, dimension) -> 标称内核键。只给报告/能力自述用；真正求解走
#: `detect_kind(model)`（悬臂施工斜拉桥摘掉 cable_bridge 会落到 grillage3d）。
SOLVER_OF: dict[tuple[str, str], str] = {
    ("beam", "2d"): "2d",
    ("beam", "3d"): "grillage3d",
    ("arch", "2d"): "2d",
    ("arch", "3d"): "grillage3d",
    ("truss", "2d"): "2d",
    ("truss", "3d"): "grillage3d",
    ("beam_bridge", "2d"): "2d",
    ("beam_bridge", "3d"): "grillage3d",
    ("cable_stayed", "2d"): "2d",
    ("cable_stayed", "3d"): "cable_stayed",
    ("suspension", "2d"): "2d",
    ("suspension", "3d"): "suspension",
}

#: 旧 8 值 kind -> (新 kind, dimension)。向后兼容 `ModelDoc("grillage3d")` 这类调用。
_LEGACY_KIND: dict[str, tuple[str, str]] = {
    "grillage3d": ("beam_bridge", "3d"),
    "arch_3d": ("arch", "3d"),
}

KIND_LABELS: dict[str, str] = {
    "beam": "单梁",
    "arch": "拱桥",
    "truss": "桁架桥",
    "beam_bridge": "梁桥",
    "cable_stayed": "斜拉桥",
    "suspension": "悬索桥",
}

DIM_LABELS: dict[str, str] = {"2d": "二维", "3d": "三维"}

KIND_HINTS: dict[str, str] = {
    "单梁": "beam",
    "拱桥": "arch",
    "桁架桥": "truss",
    "梁桥": "beam_bridge",
    "三维梁格": "beam_bridge",
    "斜拉桥": "cable_stayed",
    "悬索桥": "suspension",
    "自锚式悬索桥": "suspension",
    "地锚式悬索桥": "suspension",
    "大跨拱桥": "arch",
    "拱桥施工": "arch",
    "拱肋吊装": "arch",
}

# 材料名 → (E, ρ)
MATERIALS: dict[str, tuple[float, float]] = {
    "钢材 Q345": (2.06e11, 7850.0),
    "混凝土 C50": (3.45e10, 2500.0),
}
MATERIAL_NAMES = list(MATERIALS)

# 多车道横向布载折减系数（JTG D60-2015 表 4.3.1-5）
_LANE_REDUCTION: dict[int, float] = {
    1: 1.00, 2: 1.00, 3: 0.78, 4: 0.67,
    5: 0.60, 6: 0.55, 7: 0.52, 8: 0.50,
}

SHAPE_LABELS = {
    "rectangular": "矩形",
    "i_beam": "工字形",
    "box": "箱形",
    "circular": "圆形",
}


# ---------------------------------------------------------------- 参数默认值

# 程序口径，不是规范条文：主跨达到这个值的悬臂斜拉改走非线性施工阶段。
# 短于它的仍走线性；句子里写「线性施工」可以强制保持线性。
CABLE_STAYED_LARGE_SPAN_M = 400.0

DEFAULTS: dict[str, dict[str, Any]] = {
    "ps_beam": {
        "length": 10.0,
        "n_seg": 2,
        "P_kN": 1000.0,
        "e": 0.0,
        "s": -0.3,
        "loss": 1.0,
        "E": 2.0e11,
        "A": 0.01,
        "I": 0.01,
        "creep_phi": 0.0,
        "creep_chi": 0.8,
        "shrinkage_ecs": 0.0,
        "support": "simple",
        "Ap_mm2": 0.0,
        "fpk_MPa": 1860.0,
        "lane_q_kNpm": 0.0,
        "lane_code": "",
        "lane_impact": 1.0,
    },
    "beam": {
        "name": "未命名模型",
        "length": 30.0,
        "n_spans": 1,
        "shape": "rectangular",
        "h": 1.2,
        "b": 0.6,
        "tw": None,
        "tf": None,
        "material": "钢材 Q345",
        "supports": None,          # None = 按跨度/跨数自动生成支座
        "loads": [
            {"type": "udl", "value": 20.0, "start": None, "end": None, "position": None}
        ],
        "target_len": 0.5,
        "self_weight": False,
    },
    "arch": {
        "span": 100.0, "rise": 20.0, "n_hanger": 9,
        "udl": 20.0, "point": 0.0, "hanger_kind": "TRUSS",
        "material": "钢材 Q345", "self_weight": True,
    },
    "truss": {
        "span": 60.0, "n_panel": 8, "depth": 6.0, "node_load": 100.0,
        "material": "钢材 Q345", "self_weight": True,
    },
    # 三维空间桁架桥：两片竖向 Warren 桁架 + 上下弦横向联系（横联 + 对角撑）。
    # 纯 TRUSS 单元，cable_bridge=None，detect_kind 落到 grillage3d 线性阶段内核。
    "truss_3d": {
        "span": 60.0, "n_panel": 8, "depth": 6.0, "width": 10.0,
        "A_chord": 20000.0, "A_diag": 12000.0, "A_lat": 10000.0,   # mm²
        "q_deck": 100.0,                     # 桥面均布恒载 (kN/m，含自重)
        "material": "钢材 Q345",
    },
    "beam_bridge": {
        "span": 40.0, "n_spans": 1, "udl": 20.0, "point": 0.0,
        "h": 2.0, "b": 8.0, "shape": "box",
        "material": "钢材 Q345", "self_weight": True,
        # 档二：2D 施工阶段 + 车道活载（走 solve_2d 链，进 example_stage/example_influence）
        "staged": False,          # 逐跨施工（每跨一个阶段 + 二期铺装阶段）
        "q2_kN": 0.0,             # 二期铺装 (kN/m)，staged 时合拢后全桥施加
        # 逐跨张拉：0 = 不写 *STAGE_PRESTRESS，既有 .dat 逐位不变。
        # loss 是总损失系数（不是 JTG 3362 分项损失），1.0 = 不扣损失。
        "stage_tension_kN": 0.0,
        "stage_tension_e": 0.0,
        "stage_tension_loss": 1.0,
        "lane": False,            # JTG D60 车道活载 + 影响线最不利布载包络
    },
    # ---- 三维：参数名沿用建造器（sp1/sp2 而不是 span/n_spans）----
    # 梁格是"三跨连续箱梁"专用建造器：三跨不对称就没有意义，
    # 硬套 span+n_spans 会造出它表达不了的模型。
    "grillage3d": {
        "sp1": 30.0, "sp2": 50.0,          # 边跨 / 中跨 (m)
        "B": 12.0, "h": 2.5, "tf": 0.28, "tw": 0.45,
        "q": 100.0,                         # 全宽桥面均布 (kN/m，含自重)
        "panel": 2.5,                       # 横梁间距 / 纵向离散 (m)
        "n_lines": 3,                       # 纵梁线条数（奇数，含中梁线）
        "staged": True,                     # 逐跨浇筑三阶段
        "lane": True,                       # JTG D60 车道活载 + 影响线
    },
    "cable_stayed": {
        "Lm": 200.0, "Ls": 80.0, "Ht": 60.0,
        "n": 12,                            # 每塔每侧索数
        "h": 2.5, "B": 12.0,
        "q": 100.0,                         # 桥面恒载 (kN/m，= 一期 + 二期)
        "q2_deck": 0.0,                     # 二期恒载 (kN/m，铺装+护栏)；0 = 二期为 0
        "A": 6000.0,                        # 单索面积 (mm²)
        "method": "DECK",                   # DECK | RIGID | MINENERGY
        "fpu": 1860.0,                       # 索破断强度 (MPa)
        "tower_b": 3.0, "tower_h": 4.5,
        "panel": 8.0,
        "staged": False,                    # 生成悬臂施工 + 张拉阶段序列
        "retune": True,                     # 合拢后二次调索到成桥目标
        # 悬臂施工默认仍走线性阶段。主跨达到 CABLE_STAYED_LARGE_SPAN_M 时自动改非线性。
        "nonlinear_stages": False,
    },
    # 2D 平面斜拉桥：与三维共用同一套工程参数（Lm/Ls/Ht/n/q/A），主梁降成一条
    # 平面梁线、扇形索面压进 x-y 平面。塔梁固结体系，无 method/fpu/retune（三维
    # 定索力/破断强度/二次调索在 2D 简化里不单列）。
    "cable_stayed_2d": {
        "Lm": 200.0, "Ls": 80.0, "Ht": 60.0,
        "n": 12,                            # 每塔每侧索数
        "h": 2.5, "B": 12.0,                # 主梁钢箱
        "tf": 0.28, "tw": 0.45,             # 翼缘/腹板厚 (m)
        "q": 100.0,                         # 桥面恒载 (kN/m，= 一期 + 二期)
        "q2_deck": 0.0,                     # 二期恒载 (kN/m)；0 = 二期为 0
        "A": 6000.0,                        # 单索面积 (mm²)
        "tower_b": 3.0, "tower_h": 4.5,
        "tower_below": 8.0,                 # 塔基在桥面以下 (m)
        "anchor_low": 0.55,                 # 最低索锚点高度系数（塔高 × 此值）
        "staged": False,                    # 悬臂施工 + 张拉阶段序列
        "lane": False,                      # JTG D60 车道活载（2D 索承活载待验）
    },
    # 悬索桥的默认值刻意让边跨自动：Ls=0 时由建造器按体系定
    # 边跨（自锚式取"锚点主缆水平"的平衡边跨，地锚式取 1/3 主跨）。这不是
    # 偷懒——自锚式的边跨被 V=0 约束死，随手填一个数就会把梁端拔起来。
    "suspension": {
        "system": "EARTH",                  # EARTH 地锚式 | SELF 自锚式
        "Lm": 300.0, "Ls": 0.0,             # 0 = 自动
        "sag": 10.0,                        # 矢跨比分母（1/10）
        "clear": 5.0,                       # 跨中主缆至桥面净空 (m)
        "h": 3.0, "B": 12.0, "tf": 0.028, "tw": 0.020,
        "q": 100.0,                         # 桥面恒载 (kN/m，= 一期 + 二期)
        "q2_deck": 0.0,                     # 二期恒载 (kN/m，铺装+护栏)；0 = 二期为 0
        "A": 60000.0,                       # 单根主缆面积 (mm²)
        "Ah": 3000.0,                       # 单根吊索面积 (mm²)
        "panel": 10.0,                      # 吊索间距 (m)
        "tower_b": 3.0, "tower_h": 4.0,
        "tp": 6,                            # 每塔分段数
        "sh": True,                         # 边跨是否挂吊索
        "fpu": 1770.0,                      # 主缆破断强度 (MPa)
        "erection": False,                  # 是否生成施工阶段模拟（空缆→吊装→合拢→铺装）
        "erection_panels": 2,               # 每次对称吊装的吊点对数
        "lane_count": 3,                    # 横向设计车道数
        "lane_reduction": 0.78,             # 多车道横向折减系数（JTG D60 表 4.3.1-5）
    },
    # 2D 平面悬索桥：与三维共用工程参数（system/Lm/Ls/sag/clear/q/A/Ah），主缆按
    # 抛物线初值、恒载水平力 H=qL²/8f 以 ISTRN 预张。2D 只做成桥态一次静力，无
    # 施工阶段（erection）/车道数（lane_count）/二期拆分（q2_deck）。
    "suspension_2d": {
        "system": "EARTH",                  # EARTH 地锚式 | SELF 自锚式
        "Lm": 300.0, "Ls": 0.0,             # 0 = 自动（自锚取平衡边跨，地锚取 1/3 主跨）
        "sag": 10.0,                        # 矢跨比分母（1/10）
        "clear": 5.0,                       # 跨中主缆至桥面净空 (m)
        "panel": 10.0,                      # 吊索间距 (m)
        "sh": True,                         # 边跨是否挂吊索
        "h": 3.0, "B": 12.0,                # 加劲梁钢箱
        "tf": 0.028, "tw": 0.020,           # 翼缘/腹板厚 (m)
        "tower_b": 3.0, "tower_h": 4.0,
        "tp": 6,                            # 每塔分段数
        "q": 100.0,                         # 桥面恒载 (kN/m)
        "A": 60000.0,                       # 单根主缆面积 (mm²)
        "Ah": 3000.0,                       # 单根吊索面积 (mm²)
        "lane": False,                      # JTG D60 车道活载（2D 索承活载待验）
    },
    # 大跨钢箱拱桥（拱肋分段吊装 + 斜拉扣索辅助 + 合拢 + 吊杆主梁 + 铺装）。
    # 走 3D 线性阶段内核（cable_bridge=None），与 grillage3d 同一求解链。
    "arch_3d": {
        "span": 500.0, "rise": 100.0,       # 主跨 / 矢高 (m)，矢跨比 1/5
        "n_seg_half": 8,                    # 半跨拱肋吊装段数（全跨 2·n_seg_half 段）
        "arch_h": 7.0, "arch_b": 5.0,       # 拱肋钢箱截面 (m)
        "arch_tf": 0.05, "arch_tw": 0.045,  # 拱肋翼缘/腹板厚 (m)
        "deck_h": 3.2, "deck_b": 24.0,      # 主梁钢箱截面 (m)
        "deck_tf": 0.03, "deck_tw": 0.022,  # 主梁翼缘/腹板厚 (m)
        "hanger_area_mm2": 30000.0,         # 单根吊杆面积 (mm²，刚性吊杆)
        "cable_area_mm2": 8000.0,           # 单根临时扣索面积 (mm²)
        "cable_fpu": 1860.0,                # 扣索破断强度 (MPa)
        "q_deck": 200.0,                    # 一期恒载 (kN/m，主梁+桥面系)
        "q2_deck": 60.0,                    # 二期恒载 (kN/m，铺装+护栏)
    },
}

# ---------------------------------------------------------------- 字段元数据
# 界面按这张表自动渲染参数控件，不再为每种模型写一套控件代码。
# type: float / int / choice / bool；when(params) 为 False 时该控件不显示。

Field = dict[str, Any]
FIELDS: dict[str, list[list[Field]]] = {
    "ps_beam": [
        [
            {"key": "length", "label": "跨度 (m)", "type": "float",
             "min": 5.0, "max": 80.0, "step": 1.0},
            {"key": "support", "label": "支承", "type": "choice",
             "choices": ["simple", "cantilever"],
             "fmt": {"simple": "简支", "cantilever": "悬臂"}},
            {"key": "P_kN", "label": "张拉力 (kN)", "type": "float",
             "min": 100.0, "max": 20000.0, "step": 50.0},
            {"key": "s", "label": "矢高 (m，下缘为负)", "type": "float",
             "min": -2.0, "max": 2.0, "step": 0.05},
            {"key": "e", "label": "端部偏心 (m，下缘为负)", "type": "float",
             "min": -1.5, "max": 1.5, "step": 0.05},
            {"key": "Ap_mm2", "label": "钢束面积 (mm²，0 不校核)", "type": "float",
             "min": 0.0, "max": 20000.0, "step": 10.0},
        ],
        [
            {"key": "loss", "label": "有效系数", "type": "float",
             "min": 0.5, "max": 1.0, "step": 0.01},
            {"key": "n_seg", "label": "梁段数", "type": "int",
             "min": 2, "max": 8, "step": 1},
            {"key": "creep_phi", "label": "徐变系数 φ", "type": "float",
             "min": 0.0, "max": 5.0, "step": 0.1},
            {"key": "shrinkage_ecs", "label": "收缩应变", "type": "float",
             "min": 0.0, "max": 0.002, "step": 0.00005},
            {"key": "lane_q_kNpm", "label": "车道均布 (kN/m，向下)", "type": "float",
             "min": 0.0, "max": 50.0, "step": 0.5},
        ],
    ],
    "beam": [
        [
            {"key": "length", "label": "跨度 L (m)", "type": "float",
             "min": 1.0, "max": 300.0, "step": 0.5},
            {"key": "n_spans", "label": "跨数", "type": "int",
             "min": 1, "max": 6, "step": 1},
            {"key": "shape", "label": "截面形式", "type": "choice",
             "choices": list(SHAPE_LABELS), "fmt": SHAPE_LABELS},
            {"key": "material", "label": "材料", "type": "choice",
             "choices": MATERIAL_NAMES},
        ],
        [
            {"key": "h", "label": "高度 h (m)", "type": "float",
             "min": 0.05, "max": 10.0, "step": 0.05},
            {"key": "b", "label": "宽度 b (m)", "type": "float",
             "min": 0.05, "max": 20.0, "step": 0.05,
             "when": lambda p: p.get("shape") != "circular"},
            {"key": "tw", "label": "腹板厚 tw (m)", "type": "float",
             "min": 0.005, "max": 0.5, "step": 0.005, "default": 0.02,
             "when": lambda p: p.get("shape") in ("i_beam", "box")},
            {"key": "tf", "label": "翼缘厚 tf (m)", "type": "float",
             "min": 0.005, "max": 0.5, "step": 0.005, "default": 0.015,
             "when": lambda p: p.get("shape") in ("i_beam", "box")},
        ],
        [
            {"key": "target_len", "label": "单元目标长度 (m)", "type": "float",
             "min": 0.05, "max": 5.0, "step": 0.1},
            {"key": "self_weight", "label": "计入自重", "type": "bool"},
        ],
    ],
    "arch": [
        [
            {"key": "span", "label": "跨度 (m)", "type": "float",
             "min": 5.0, "max": 500.0, "step": 5.0},
            {"key": "rise", "label": "矢高 (m)", "type": "float",
             "min": 2.0, "max": 100.0, "step": 1.0},
            {"key": "n_hanger", "label": "吊杆对数", "type": "int",
             "min": 3, "max": 25, "step": 2},
            {"key": "udl", "label": "桥面均布荷载 (kN/m)", "type": "float",
             "min": 0.0, "max": 200.0, "step": 5.0},
        ],
        [
            {"key": "point", "label": "跨中集中力 (kN)", "type": "float",
             "min": 0.0, "max": 5000.0, "step": 50.0},
            {"key": "hanger_kind", "label": "吊杆单元类型", "type": "choice",
             "choices": ["TRUSS", "CABLE"],
             "fmt": {"TRUSS": "TRUSS 可拉可压", "CABLE": "CABLE 只受拉"}},
            {"key": "material", "label": "材料", "type": "choice",
             "choices": MATERIAL_NAMES},
            {"key": "self_weight", "label": "计入自重", "type": "bool"},
        ],
    ],
    "truss": [
        [
            {"key": "span", "label": "跨度 (m)", "type": "float",
             "min": 5.0, "max": 300.0, "step": 5.0},
            {"key": "n_panel", "label": "节间数", "type": "int",
             "min": 2, "max": 24, "step": 1},
            {"key": "depth", "label": "桁高 (m)", "type": "float",
             "min": 1.0, "max": 30.0, "step": 0.5},
            {"key": "node_load", "label": "每个下弦节点荷载 (kN)", "type": "float",
             "min": 0.0, "max": 2000.0, "step": 10.0},
        ],
        [
            {"key": "material", "label": "材料", "type": "choice",
             "choices": MATERIAL_NAMES},
            {"key": "self_weight", "label": "计入自重", "type": "bool"},
        ],
    ],
    "truss_3d": [
        [
            {"key": "span", "label": "跨度 (m)", "type": "float",
             "min": 10.0, "max": 300.0, "step": 5.0},
            {"key": "n_panel", "label": "节间数", "type": "int",
             "min": 2, "max": 24, "step": 1},
            {"key": "depth", "label": "桁高 (m)", "type": "float",
             "min": 1.0, "max": 30.0, "step": 0.5},
            {"key": "width", "label": "桥宽 / 桁架间距 (m)", "type": "float",
             "min": 4.0, "max": 40.0, "step": 1.0},
        ],
        [
            {"key": "A_chord", "label": "弦杆面积 (mm²)", "type": "float",
             "min": 1000.0, "max": 100000.0, "step": 1000.0},
            {"key": "A_diag", "label": "腹杆面积 (mm²)", "type": "float",
             "min": 500.0, "max": 50000.0, "step": 500.0},
            {"key": "A_lat", "label": "横向联系面积 (mm²)", "type": "float",
             "min": 500.0, "max": 50000.0, "step": 500.0},
            {"key": "q_deck", "label": "桥面均布恒载 (kN/m)", "type": "float",
             "min": 0.0, "max": 500.0, "step": 10.0},
        ],
    ],
    "beam_bridge": [
        [
            {"key": "span", "label": "跨度 (m)", "type": "float",
             "min": 5.0, "max": 500.0, "step": 5.0},
            {"key": "n_spans", "label": "跨数", "type": "int",
             "min": 1, "max": 6, "step": 1},
            {"key": "udl", "label": "桥面均布荷载 (kN/m)", "type": "float",
             "min": 0.0, "max": 200.0, "step": 5.0},
            {"key": "point", "label": "跨中集中力 (kN)", "type": "float",
             "min": 0.0, "max": 5000.0, "step": 50.0},
        ],
        [
            {"key": "h", "label": "梁高 h (m)", "type": "float",
             "min": 0.2, "max": 10.0, "step": 0.1},
            {"key": "b", "label": "桥宽 b (m)", "type": "float",
             "min": 0.5, "max": 40.0, "step": 0.5},
            {"key": "shape", "label": "截面形式", "type": "choice",
             "choices": ["box", "i_beam", "rectangular"], "fmt": SHAPE_LABELS},
            {"key": "material", "label": "材料", "type": "choice",
             "choices": MATERIAL_NAMES},
        ],
        [
            {"key": "self_weight", "label": "计入自重", "type": "bool"},
            {"key": "staged", "label": "逐跨施工", "type": "bool"},
            {"key": "lane", "label": "JTG D60 车道活载", "type": "bool"},
            {"key": "q2_kN", "label": "二期铺装 (kN/m)", "type": "float",
             "min": 0.0, "max": 100.0, "step": 5.0,
             "when": lambda p: p.get("staged")},
        ],
        # 逐跨张拉：每跨阶段写一条 *STAGE_PRESTRESS（等效荷载法）。
        # 只在开了逐跨施工时显示，默认 0 不写段、不改任何既有 .dat。
        [
            {"key": "stage_tension_kN", "label": "逐跨张拉力 (kN/跨)", "type": "float",
             "min": 0.0, "max": 50000.0, "step": 500.0,
             "when": lambda p: p.get("staged")},
            {"key": "stage_tension_e", "label": "钢束偏心 e (m，向下为正)", "type": "float",
             "min": -5.0, "max": 5.0, "step": 0.05,
             "when": lambda p: p.get("staged")},
            {"key": "stage_tension_loss", "label": "总损失系数 loss", "type": "float",
             "min": 0.1, "max": 1.0, "step": 0.05,
             "when": lambda p: p.get("staged")},
        ],
    ],
    "grillage3d": [
        [
            {"key": "sp1", "label": "边跨 (m)", "type": "float",
             "min": 10.0, "max": 60.0, "step": 5.0},
            {"key": "sp2", "label": "中跨 (m)", "type": "float",
             "min": 20.0, "max": 120.0, "step": 5.0},
            {"key": "B", "label": "箱宽 B (m)", "type": "float",
             "min": 6.0, "max": 20.0, "step": 0.5},
            {"key": "h", "label": "梁高 h (m)", "type": "float",
             "min": 1.2, "max": 5.0, "step": 0.1},
        ],
        [
            {"key": "tf", "label": "顶/底板厚 (m)", "type": "float",
             "min": 0.15, "max": 0.6, "step": 0.01},
            {"key": "tw", "label": "腹板厚 (m)", "type": "float",
             "min": 0.2, "max": 0.8, "step": 0.05},
            {"key": "q", "label": "桥面均布 q (kN/m)", "type": "float",
             "min": 20.0, "max": 500.0, "step": 10.0},
            {"key": "panel", "label": "纵向离散 / 横梁间距 (m)", "type": "float",
             "min": 1.0, "max": 5.0, "step": 0.5},
        ],
        [
            {"key": "n_lines", "label": "纵梁线条数（奇数）", "type": "int",
             "min": 3, "max": 9, "step": 2},
            {"key": "staged", "label": "逐跨施工（3 阶段）", "type": "bool"},
            {"key": "lane", "label": "JTG D60 车道活载", "type": "bool"},
        ],
    ],
    "cable_stayed": [
        [
            {"key": "Lm", "label": "主跨 (m)", "type": "float",
             "min": 100.0, "max": 600.0, "step": 10.0},
            {"key": "Ls", "label": "边跨 (m)", "type": "float",
             "min": 30.0, "max": 300.0, "step": 5.0},
            {"key": "Ht", "label": "塔高 (m)", "type": "float",
             "min": 20.0, "max": 200.0, "step": 5.0},
            {"key": "n", "label": "每塔每侧索数", "type": "int",
             "min": 3, "max": 30, "step": 1},
        ],
        [
            {"key": "h", "label": "主梁高 (m)", "type": "float",
             "min": 1.2, "max": 6.0, "step": 0.1},
            {"key": "B", "label": "主梁宽 (m)", "type": "float",
             "min": 6.0, "max": 30.0, "step": 0.5},
            {"key": "q", "label": "桥面恒载 (kN/m)", "type": "float",
             "min": 20.0, "max": 500.0, "step": 10.0},
            {"key": "A", "label": "单索面积 (mm²)", "type": "float",
             "min": 1000.0, "max": 20000.0, "step": 500.0},
        ],
        [
            {"key": "method", "label": "定索力方法", "type": "choice",
             "choices": ["DECK", "RIGID", "MINENERGY"],
             "fmt": {"DECK": "按恒载竖向分配", "RIGID": "刚性支承连续梁法",
                     "MINENERGY": "最小弯曲能量法"}},
            {"key": "fpu", "label": "索破断强度 (MPa)", "type": "float",
             "min": 1200.0, "max": 2200.0, "step": 10.0},
            {"key": "tower_b", "label": "塔柱宽 (m)", "type": "float",
             "min": 1.5, "max": 10.0, "step": 0.5},
            {"key": "tower_h", "label": "塔柱深 (m)", "type": "float",
             "min": 2.0, "max": 12.0, "step": 0.5},
        ],
        [
            {"key": "staged", "label": "悬臂施工 + 张拉", "type": "bool"},
            {"key": "q2_deck", "label": "二期恒载 (kN/m)", "type": "float",
             "min": 0.0, "max": 200.0, "step": 5.0,
             "when": lambda p: p.get("staged")},
            {"key": "retune", "label": "合拢后二次调索", "type": "bool",
             "when": lambda p: p.get("staged")},
            {"key": "nonlinear_stages", "label": "非线性施工阶段", "type": "bool",
             "when": lambda p: p.get("staged")},
        ],
        # 成桥态 `*CABLE_BRIDGE` 的求解控制量。以前只能写 .dat / 调 API，
        # 界面完全看不见；现在与 `CableBridge3D` 同默认值，缺省时 .dat 不变。
        # staged 模型不挂 cable_bridge，这些量不写进 .dat，故整行隐藏。
        # 1e-8 / 1e-7 这类收敛容差仍不暴露：number_input 的 %.3f 会把它们
        # 显示成 0.000，用户一碰就变成 0，属于静默改口径，宁可留 API。
        [
            {"key": "nl_steps", "label": "非线性荷载步", "type": "int",
             "min": 1, "max": 20, "step": 1, "default": 3,
             "when": lambda p: not p.get("staged")},
            {"key": "newton", "label": "Newton 迭代上限", "type": "int",
             "min": 5, "max": 100, "step": 5, "default": 30,
             "when": lambda p: not p.get("staged")},
            {"key": "max_iter", "label": "索长迭代上限", "type": "int",
             "min": 1, "max": 60, "step": 1, "default": 20,
             "when": lambda p: not p.get("staged")},
            {"key": "mint", "label": "保力下限比例", "type": "float",
             "min": 0.0, "max": 0.2, "step": 0.005, "default": 0.02,
             "when": lambda p: not p.get("staged")},
        ],
    ],
    "cable_stayed_2d": [
        [
            {"key": "Lm", "label": "主跨 (m)", "type": "float",
             "min": 100.0, "max": 600.0, "step": 10.0},
            {"key": "Ls", "label": "边跨 (m)", "type": "float",
             "min": 30.0, "max": 300.0, "step": 5.0},
            {"key": "Ht", "label": "塔高 (m)", "type": "float",
             "min": 20.0, "max": 200.0, "step": 5.0},
            {"key": "n", "label": "每塔每侧索数", "type": "int",
             "min": 3, "max": 30, "step": 1},
        ],
        [
            {"key": "h", "label": "主梁高 (m)", "type": "float",
             "min": 1.2, "max": 6.0, "step": 0.1},
            {"key": "B", "label": "主梁宽 (m)", "type": "float",
             "min": 6.0, "max": 30.0, "step": 0.5},
            {"key": "tf", "label": "顶/底板厚 (m)", "type": "float",
             "min": 0.01, "max": 1.0, "step": 0.01},
            {"key": "tw", "label": "腹板厚 (m)", "type": "float",
             "min": 0.01, "max": 1.0, "step": 0.01},
        ],
        [
            {"key": "q", "label": "桥面恒载 (kN/m)", "type": "float",
             "min": 20.0, "max": 500.0, "step": 10.0},
            {"key": "A", "label": "单索面积 (mm²)", "type": "float",
             "min": 1000.0, "max": 20000.0, "step": 500.0},
            {"key": "tower_b", "label": "塔柱宽 (m)", "type": "float",
             "min": 1.5, "max": 10.0, "step": 0.5},
            {"key": "tower_h", "label": "塔柱深 (m)", "type": "float",
             "min": 2.0, "max": 12.0, "step": 0.5},
        ],
        [
            {"key": "staged", "label": "悬臂施工 + 张拉", "type": "bool"},
            {"key": "lane", "label": "JTG D60 车道活载", "type": "bool"},
            {"key": "q2_deck", "label": "二期恒载 (kN/m)", "type": "float",
             "min": 0.0, "max": 200.0, "step": 5.0,
             "when": lambda p: p.get("staged")},
        ],
    ],
    "suspension": [
        [
            {"key": "system", "label": "体系", "type": "choice",
             "choices": ["EARTH", "SELF"],
             "fmt": {"EARTH": "地锚式（主缆锚在锚碇）",
                     "SELF": "自锚式（主缆锚在梁端）"}},
            {"key": "Lm", "label": "主跨 (m)", "type": "float",
             "min": 100.0, "max": 1500.0, "step": 10.0},
            {"key": "Ls", "label": "边跨 (m，0=自动)", "type": "float",
             "min": 0.0, "max": 500.0, "step": 5.0},
            {"key": "sag", "label": "矢跨比 1/n", "type": "float",
             "min": 7.0, "max": 14.0, "step": 0.5},
        ],
        [
            {"key": "h", "label": "加劲梁高 (m)", "type": "float",
             "min": 1.5, "max": 6.0, "step": 0.1},
            {"key": "B", "label": "加劲梁宽 (m)", "type": "float",
             "min": 6.0, "max": 30.0, "step": 0.5},
            {"key": "q", "label": "桥面恒载 (kN/m)", "type": "float",
             "min": 20.0, "max": 500.0, "step": 10.0},
            {"key": "clear", "label": "跨中净空 (m)", "type": "float",
             "min": 2.0, "max": 30.0, "step": 1.0},
        ],
        [
            {"key": "A", "label": "单根主缆面积 (mm²)", "type": "float",
             "min": 10000.0, "max": 300000.0, "step": 5000.0},
            {"key": "Ah", "label": "单根吊索面积 (mm²)", "type": "float",
             "min": 500.0, "max": 10000.0, "step": 250.0},
            {"key": "panel", "label": "吊索间距 (m)", "type": "float",
             "min": 5.0, "max": 20.0, "step": 1.0},
            {"key": "fpu", "label": "主缆破断强度 (MPa)", "type": "float",
             "min": 1400.0, "max": 2000.0, "step": 10.0},
        ],
        [
            {"key": "tower_b", "label": "塔柱宽 (m)", "type": "float",
             "min": 1.5, "max": 10.0, "step": 0.5},
            {"key": "tower_h", "label": "塔柱深 (m)", "type": "float",
             "min": 2.0, "max": 12.0, "step": 0.5},
            {"key": "tp", "label": "每塔分段", "type": "int",
             "min": 3, "max": 12, "step": 1},
            {"key": "sh", "label": "边跨挂吊索", "type": "bool"},
        ],
        [
            {"key": "lane_count", "label": "车道数", "type": "int",
             "min": 1, "max": 8, "step": 1},
            {"key": "q2_deck", "label": "二期恒载 (kN/m)", "type": "float",
             "min": 0.0, "max": 300.0, "step": 5.0},
            {"key": "erection", "label": "施工阶段模拟", "type": "bool"},
            {"key": "erection_panels", "label": "每次对称吊装对数", "type": "int",
             "min": 1, "max": 10, "step": 1,
             "when": lambda p: p.get("erection")},
        ],
    ],
    "suspension_2d": [
        [
            {"key": "system", "label": "体系", "type": "choice",
             "choices": ["EARTH", "SELF"],
             "fmt": {"EARTH": "地锚式（主缆锚在锚碇）",
                     "SELF": "自锚式（主缆锚在梁端）"}},
            {"key": "Lm", "label": "主跨 (m)", "type": "float",
             "min": 100.0, "max": 1500.0, "step": 10.0},
            {"key": "Ls", "label": "边跨 (m，0=自动)", "type": "float",
             "min": 0.0, "max": 500.0, "step": 5.0},
            {"key": "sag", "label": "矢跨比 1/n", "type": "float",
             "min": 7.0, "max": 14.0, "step": 0.5},
        ],
        [
            {"key": "h", "label": "加劲梁高 (m)", "type": "float",
             "min": 1.5, "max": 6.0, "step": 0.1},
            {"key": "B", "label": "加劲梁宽 (m)", "type": "float",
             "min": 6.0, "max": 30.0, "step": 0.5},
            {"key": "tf", "label": "顶/底板厚 (m)", "type": "float",
             "min": 0.01, "max": 0.1, "step": 0.005},
            {"key": "tw", "label": "腹板厚 (m)", "type": "float",
             "min": 0.01, "max": 0.1, "step": 0.005},
        ],
        [
            {"key": "q", "label": "桥面恒载 (kN/m)", "type": "float",
             "min": 20.0, "max": 500.0, "step": 10.0},
            {"key": "A", "label": "单根主缆面积 (mm²)", "type": "float",
             "min": 10000.0, "max": 300000.0, "step": 5000.0},
            {"key": "Ah", "label": "单根吊索面积 (mm²)", "type": "float",
             "min": 500.0, "max": 10000.0, "step": 250.0},
            {"key": "clear", "label": "跨中净空 (m)", "type": "float",
             "min": 2.0, "max": 30.0, "step": 1.0},
        ],
        [
            {"key": "panel", "label": "吊索间距 (m)", "type": "float",
             "min": 5.0, "max": 20.0, "step": 1.0},
            {"key": "tower_b", "label": "塔柱宽 (m)", "type": "float",
             "min": 1.5, "max": 10.0, "step": 0.5},
            {"key": "tower_h", "label": "塔柱深 (m)", "type": "float",
             "min": 2.0, "max": 12.0, "step": 0.5},
            {"key": "tp", "label": "每塔分段", "type": "int",
             "min": 3, "max": 12, "step": 1},
            {"key": "sh", "label": "边跨挂吊索", "type": "bool"},
        ],
        [
            {"key": "lane", "label": "JTG D60 车道活载", "type": "bool"},
        ],
    ],
    "arch_3d": [
        [
            {"key": "span", "label": "主跨 (m)", "type": "float",
             "min": 100.0, "max": 600.0, "step": 10.0},
            {"key": "rise", "label": "矢高 (m)", "type": "float",
             "min": 20.0, "max": 150.0, "step": 5.0},
            {"key": "n_seg_half", "label": "半跨吊装段数", "type": "int",
             "min": 3, "max": 16, "step": 1},
            {"key": "cable_fpu", "label": "扣索破断强度 (MPa)", "type": "float",
             "min": 1200.0, "max": 2200.0, "step": 10.0},
        ],
        [
            {"key": "arch_h", "label": "拱肋高 (m)", "type": "float",
             "min": 3.0, "max": 12.0, "step": 0.5},
            {"key": "arch_b", "label": "拱肋宽 (m)", "type": "float",
             "min": 2.0, "max": 10.0, "step": 0.5},
            {"key": "arch_tf", "label": "拱肋翼缘厚 (m)", "type": "float",
             "min": 0.02, "max": 0.12, "step": 0.005},
            {"key": "arch_tw", "label": "拱肋腹板厚 (m)", "type": "float",
             "min": 0.02, "max": 0.12, "step": 0.005},
        ],
        [
            {"key": "deck_h", "label": "主梁高 (m)", "type": "float",
             "min": 2.0, "max": 6.0, "step": 0.1},
            {"key": "deck_b", "label": "主梁宽 (m)", "type": "float",
             "min": 8.0, "max": 40.0, "step": 0.5},
            {"key": "hanger_area_mm2", "label": "吊杆面积 (mm²)", "type": "float",
             "min": 5000.0, "max": 100000.0, "step": 1000.0},
            {"key": "cable_area_mm2", "label": "扣索面积 (mm²)", "type": "float",
             "min": 1000.0, "max": 20000.0, "step": 500.0},
        ],
        [
            {"key": "q_deck", "label": "一期恒载 (kN/m)", "type": "float",
             "min": 50.0, "max": 500.0, "step": 10.0},
            {"key": "q2_deck", "label": "二期恒载 (kN/m)", "type": "float",
             "min": 0.0, "max": 200.0, "step": 5.0},
        ],
    ],
}


# ---------------------------------------------------------------- 解析诊断


@dataclass
class Diag:
    """与 llm_parser 的诊断对象同构，界面可无差别渲染。"""

    ok: bool = True
    notes: list[str] = field(default_factory=list)
    error: str | None = None
    raw: str = ""


# ---------------------------------------------------------------- 意图分类


_DIM_3D_RE = re.compile(
    r"三维|空间|梁格|空间梁格|空间效应|空间刚架|偏载|扭转|横向分布|横梁|3d",
    re.I)
_DUAL_PLANE_RE = re.compile(r"双索面|两索面|双缆面|双主缆")
_FLOATING_RE = re.compile(r"半漂浮|漂浮体系|纵向漂浮")
_FIXED_RE = re.compile(r"塔梁固结|固结体系")


def _classify_kind(text: str, hint: str | None = None) -> str:
    """判断结构类型（旧 8 值语义，含 grillage3d/arch_3d）。

    hint 来自界面的"结构类型"提示（"自动识别" 或具体桥型）；给了具体类型
    就完全跳过猜测——用户比自己写的分类器可靠。

    顺序有讲究：先「斜拉」再「拱」再「桁架」，然后「梁格」，最后才落到
    普通桥/单梁。因为「拱桥」「桁架桥」「斜拉桥」都含「桥」，「简支梁桥」
    含「梁」，而「三跨连续箱梁」这类描述里一个「桥」字都没有。
    """
    if hint:
        # 界面上的标签带分组前缀（"三维 · 斜拉桥"），而 KIND_HINTS 的键是
        # 纯名字（"斜拉桥"）。这里先剥前缀，再按最长键包含匹配。
        h = (hint.replace("三维", "").replace("二维", "")
             .replace("·", "").replace("・", "").strip())
        if hint in KIND_HINTS:
            return KIND_HINTS[hint]
        if h in KIND_HINTS:
            return KIND_HINTS[h]
        hits = [k for k in KIND_HINTS if k and k in hint]
        if hits:
            return KIND_HINTS[max(hits, key=len)]
    t = text.strip()
    if re.search(r"悬索|主缆|吊索", t):
        return "suspension"
    # 大跨拱桥施工必须先于「斜拉」判：拱肋吊装用的「斜拉扣索」含「斜拉」二字，
    # 但那是临时扣索、不是斜拉桥。判据 = 有「拱」且带施工过程词（分段吊装 /
    # 扣索 / 扣挂 / 缆索吊 / 合拢）。普通拱桥（只有矢高/吊杆）仍走 2D arch。
    if re.search(r"拱", t) and re.search(
            r"拱肋|分段吊装|吊装|扣索|扣挂|缆索吊|斜拉扣|合拢", t):
        return "arch_3d"
    if re.search(r"斜拉|拉索桥|斜拉索", t):
        return "cable_stayed"
    # 预应力梁也会写「矢高」。这不是拱。斜拉、悬索、拱桥自己的预应力仍留给原桥型。
    if re.search(r"预应力|钢束|预加力", t) and not re.search(r"拱肋|拱桥|斜拉|悬索", t):
        return "beam"
    # 「矢高」「矢跨比」在悬索桥里也用，但悬索已经先一步判掉了，
    # 落到这里的基本都是真拱桥。
    if re.search(r"拱|矢高|矢跨比", t):
        return "arch"
    if re.search(r"桁架|truss", t, re.I):
        return "truss"
    if re.search(r"梁格|连续箱梁|箱梁三维|空间梁格", t):
        return "grillage3d"
    if "桥" in t:
        return "beam_bridge"
    return "beam"


_DIM_2D_RE = re.compile(r"二维|平面|平面索面|平面杆系|平面模型|平面力学|简化", re.I)


def classify_dimension(text: str, kind: str) -> str:
    """由描述判定维度：默认 2d（大多数桥可简化二维），显式三维词转 3d。"""
    if _DIM_3D_RE.search(text):
        return "3d"
    if _DIM_2D_RE.search(text):
        return "2d"
    # 梁格、大跨拱施工本质是空间问题（其建造器只做三维）。
    if kind in ("grillage3d", "arch_3d"):
        return "3d"
    # 斜拉桥/悬索桥：档三已补 2D 平面索面建造器，默认 2d；只有显式三维词
    # （空间/偏载/扭转/横向分布…）才转 3d。「双索面」不在这份词表里：
    # 它表示第二索面，当前建造器做不出来，由 validate() 拒绝，而不是当成三维。
    return "2d"


def classify_text(text: str, hint: str | None = None) -> tuple[str, str]:
    """判断一句话属于「桥型 kind × 维度 dimension」两个正交维度。

    返回 ``(kind, dimension)``。kind ∈ 6 桥型；dimension ∈ {2d, 3d}。
    """
    raw = _classify_kind(text, hint)
    dim_src = text if hint is None else f"{text} {hint}"
    dim = classify_dimension(dim_src, raw)
    if raw in _LEGACY_KIND:
        return _LEGACY_KIND[raw]
    return raw, dim


# ---------------------------------------------------------------- 外壳


class ModelDoc:
    """参数 + 展开模型 + 能力自述的统一外壳。

    典型用法::

        doc = ModelDoc.from_text("跨度30米简支梁，均布20kN/m")[0]
        if errs := doc.validate():
            ...
        doc.to_dat()                       # 交给 FEM-Eigen
        doc.capability()["anastruct"]      # 能否本地草算
    """

    def __init__(self, kind: str, params: dict[str, Any] | None = None,
                 dimension: str | None = None):
        # 向后兼容旧 8 值 kind（grillage3d/arch_3d）→ 归一化到 (kind, dimension)
        if kind in _LEGACY_KIND:
            kind, dimension = _LEGACY_KIND[kind]
        if kind not in KIND_LABELS:
            raise ValueError(f"未知结构类型：{kind}")
        if dimension is None:
            # 档三后斜拉/悬索桥默认 2d（平面索面简化），其余同样默认 2d。
            dimension = "2d"
        if dimension not in DIM_LABELS:
            raise ValueError(f"未知维度：{dimension}")
        self.kind: str = kind
        self.dimension: str = dimension
        bk = self.builder_key
        if bk not in DEFAULTS:
            raise ValueError(
                f"结构 {KIND_LABELS[kind]}×{DIM_LABELS[dimension]} 暂不支持"
                f"（建造器 {bk} 缺失）")
        merged = json.loads(json.dumps(DEFAULTS[bk]))   # 深拷贝
        if params:
            merged.update(params)
        self.params: dict[str, Any] = merged
        self._model: Any = None
        self._info: dict[str, Any] | None = None

    # ------------------------------------------------------------ 构建

    def build(self) -> "ModelDoc":
        """params → 展开模型。幂等，可反复调用。"""
        self._info = None
        bk = self.builder_key
        if bk == "beam":
            self._model = self._build_beam()
        elif bk == "arch":
            self._model = self._build_arch()
        elif bk == "truss":
            self._model = self._build_truss()
        elif bk == "truss_3d":
            self._model = self._build_truss_3d()
        elif bk == "grillage3d":
            self._model = self._build_grillage3d()
        elif bk == "cable_stayed":
            self._model = self._build_cable_stayed()
        elif bk == "suspension":
            self._model = self._build_suspension()
        elif bk == "arch_3d":
            self._model = self._build_arch_3d()
        elif bk == "cable_stayed_2d":
            self._model = self._build_cable_stayed_2d()
        elif bk == "suspension_2d":
            self._model = self._build_suspension_2d()
        elif bk == "ps_beam":
            self._model = self._build_ps_beam()
        else:
            self._model = self._build_beam_bridge()
        return self

    @property
    def model(self) -> Any:
        if self._model is None:
            self.build()
        return self._model

    @property
    def info(self) -> dict[str, Any]:
        """建造器给的派生量（支座 x、实际索数、索重…）。三维专用。"""
        if self._model is None:
            self.build()
        return self._info or {}

    @property
    def builder_key(self) -> str:
        """建造器键（DEFAULTS/FIELDS 的键），由 kind × dimension 路由得到。"""
        return BUILDERS.get((self.kind, self.dimension), self.kind)

    @property
    def is_3d(self) -> bool:
        return self.dimension == "3d"

    @property
    def solver_kind(self) -> str:
        """实际求解内核键。三维按模型内容判定，与 `solve()` 同一条路由。

        悬臂施工的斜拉桥不挂 `cable_bridge`，走线性阶段内核；成桥斜拉和悬索
        走 `cable_solver`。界面若仍用名义键，会把线性结果藏起来，或把 exe
        名称写成几何非线性求解器。
        """
        if self.is_3d:
            from solver_3d import detect_kind

            return detect_kind(self.model)
        return SOLVER_OF.get((self.kind, self.dimension), "2d")

    def _build_ps_beam(self):
        """一跨梁加一束预应力。简支曲线束的跨中上拱、悬臂直线束的自由端挠度各对得上闭式解。"""
        from model_spec_3d import (
            Boundary3D, Element3D, ElemTypeDef3D, Material3D, ModelSpec3D,
            Lane3D, Node3D, Prestress3D, RealConst3D, Stage3D,
        )

        p = self.params
        L = float(p["length"])
        n = max(2, int(p.get("n_seg") or 2))
        E = float(p.get("E") or 2.0e11)
        A = float(p.get("A") or 0.01)
        I = float(p.get("I") or 0.01)
        nodes = [Node3D(id=i + 1, x=L * i / n, y=0.0, z=0.0) for i in range(n + 1)]
        elements = [Element3D(id=i + 1, etype=1, n1=i + 1, n2=i + 2) for i in range(n)]
        cantilever = p.get("support") == "cantilever"
        if cantilever:
            # 固端约束全部 6 个自由度。其余节点只锁出平面，竖向和绕 y 转动留着。
            bounds = [Boundary3D(node=1, dofs=(1, 6))]
            for i in range(2, n + 2):
                bounds.append(Boundary3D(node=i, dofs=(2, 2)))
                bounds.append(Boundary3D(node=i, dofs=(4, 4)))
                bounds.append(Boundary3D(node=i, dofs=(6, 6)))
        else:
            bounds = [
                Boundary3D(node=1, dofs=(1, 3)),
                Boundary3D(node=1, dofs=(4, 4)),
                Boundary3D(node=1, dofs=(6, 6)),
                Boundary3D(node=n + 1, dofs=(2, 3)),
                Boundary3D(node=n + 1, dofs=(4, 4)),
                Boundary3D(node=n + 1, dofs=(6, 6)),
            ]
            for i in range(2, n + 1):
                bounds.append(Boundary3D(node=i, dofs=(2, 2)))
                bounds.append(Boundary3D(node=i, dofs=(4, 4)))
                bounds.append(Boundary3D(node=i, dofs=(6, 6)))
        ap_mm2 = float(p.get("Ap_mm2") or 0.0)
        tendon = Prestress3D(
            elems=list(range(1, n + 1)),
            P=float(p["P_kN"]) * 1e3,
            e=float(p.get("e") or 0.0),
            s=float(p.get("s") or 0.0),
            loss=float(p.get("loss") or 1.0),
            Ap=ap_mm2 * 1e-6,
            fpk=float(p.get("fpk_MPa") or 1860.0) * 1e6,
        )
        stages = [Stage3D(id=1, activate=list(range(1, n + 1)), prestress=[tendon])]
        dphis = [float(x) for x in (p.get("creep_dphis") or [])]
        if not dphis and float(p.get("creep_dphi") or 0.0):
            dphis = [float(p["creep_dphi"])]
        for i, dphi in enumerate(dphis, start=2):
            if dphi:
                stages.append(Stage3D(id=i, creep_dphi=dphi))
        if float(p.get("creep_rh") or 0.0) > 0.0:
            t0 = float(p.get("creep_t0") or 7.0)
            stages[0].time = t0
            t_end = p.get("creep_t_end")
            if t_end:
                stages.append(Stage3D(id=len(stages) + 1, time=float(t_end)))
        lane_q = float(p.get("lane_q_kNpm") or 0.0)
        impact = float(p.get("lane_impact") or 1.0)
        lanes = []
        if p.get("lane_code") == "JTG_D60":
            # qk、Pk 由内核按跨径填写。冲击只在句子写了系数时乘上。
            lanes.append(Lane3D(
                nodes=[nd.id for nd in nodes], code="JTG_D60", dof=3,
                impact=impact))
        elif lane_q:
            # 正值向下。内核里沿 +z 为正，所以均布写成负号。
            lanes.append(Lane3D(
                nodes=[nd.id for nd in nodes], q=-lane_q * 1e3, p=0.0, dof=3,
                impact=impact))
        return ModelSpec3D(
            title="预应力悬臂梁" if cantilever else "预应力简支梁",
            nodes=nodes,
            materials=[Material3D(id=1, E=E, dens=float(p.get("dens") or 2500.0))],
            reals=[RealConst3D(id=1, A=A, IY=I, IZ=I, J=max(I * 0.01, 1e-6), G=E / 2.6)],
            etypes=[ElemTypeDef3D(id=1, kind="BEAM", mat=1, real=1)],
            elements=elements,
            boundaries=bounds,
            stages=stages,
            creep_phi=float(p.get("creep_phi") or 0.0),
            creep_chi=float(p.get("creep_chi") or 0.8),
            creep_t0=float(p.get("creep_t0") or 7.0),
            creep_rh=float(p.get("creep_rh") or 0.0),
            creep_h=float(p.get("creep_h") or 0.0),
            creep_fcm=float(p.get("creep_fcm") or 0.0),
            shrinkage_ts=float(p.get("shrinkage_ts") or 0.0),
            shrinkage_ecs=float(p.get("shrinkage_ecs") or 0.0),
            lanes=lanes,
        )

    def _build_beam(self) -> ModelSpec:
        p = self.params
        shape = p.get("shape", "rectangular")
        h = float(p["h"])
        b = h if shape == "circular" else float(p["b"])
        tw = float(p["tw"]) if p.get("tw") else None
        tf = float(p["tf"]) if p.get("tf") else None
        E, rho = MATERIALS.get(p.get("material", "钢材 Q345"), MATERIALS["钢材 Q345"])
        loads = [Load(**{k: v for k, v in ld.items() if k in
                         ("type", "value", "start", "end", "position")})
                 for ld in p.get("loads", [])]
        supports = ([Support(**s) for s in p["supports"]]
                    if p.get("supports") else [])
        return ModelSpec(
            name=p.get("name", "未命名模型"),
            length=float(p["length"]),
            n_spans=int(p["n_spans"]),
            supports=supports,
            loads=loads,
            section=Section(shape=shape, h=h, b=b, tw=tw, tf=tf),
            material_name=p.get("material", "钢材 Q345"),
            E=E,
            rho=rho,
        )

    def _gravity(self) -> float:
        return -9.81 if self.params.get("self_weight", True) else 0.0

    def _build_arch(self):
        from bridge_spec import ElemKind, build_arch_bridge

        p = self.params
        kind = ElemKind.CABLE if p.get("hanger_kind") == "CABLE" else ElemKind.TRUSS
        return build_arch_bridge(
            span=float(p["span"]), rise=float(p["rise"]),
            n_hanger=int(p["n_hanger"]),
            material=p.get("material", "钢材 Q345"),
            udl_kN=float(p.get("udl", 0.0) or 0.0),
            point_kN=float(p.get("point", 0.0) or 0.0),
            hanger_kind=kind, gravity=self._gravity(),
        )

    def _build_truss(self):
        from bridge_spec import build_truss_bridge

        p = self.params
        return build_truss_bridge(
            span=float(p["span"]), n_panel=int(p["n_panel"]),
            depth=float(p["depth"]),
            material=p.get("material", "钢材 Q345"),
            node_load_kN=float(p.get("node_load", 0.0) or 0.0),
            gravity=self._gravity(),
        )

    def _build_truss_3d(self):
        """三维空间桁架桥：两片竖向 Warren 桁架 + 上下弦横向联系。

        纯 TRUSS 单元（节点只有 3 平动自由度），`cable_bridge=None` 让
        `detect_kind` 落到 grillage3d 线性阶段内核。弦杆轴力内核不直接输出，
        由位移反算（N=EA·ΔL/L），见 verify_truss_3d.py。
        """
        from truss_3d import build_truss_3d

        p = self.params
        E, dens = MATERIALS.get(p.get("material", "钢材 Q345"), (2.06e11, 7850.0))
        model, info = build_truss_3d(
            span=float(p["span"]), n_panel=int(p["n_panel"]),
            depth=float(p["depth"]), width=float(p["width"]),
            A_chord_mm2=float(p["A_chord"]), A_diag_mm2=float(p["A_diag"]),
            A_lat_mm2=float(p["A_lat"]),
            q_deck=float(p["q_deck"]) * 1e3,
            E_steel=E, dens_steel=dens,
        )
        self._info = dict(info)
        return model

    def _build_beam_bridge(self):
        from bridge_spec import build_beam_bridge

        p = self.params
        return build_beam_bridge(
            span=float(p["span"]), n_spans=int(p["n_spans"]),
            shape=p.get("shape", "box"), h=float(p["h"]), b=float(p["b"]),
            material=p.get("material", "钢材 Q345"),
            udl_kN=float(p.get("udl", 0.0) or 0.0),
            point_kN=float(p.get("point", 0.0) or 0.0),
            gravity=self._gravity(),
            staged=bool(p.get("staged", False)),
            q2_kN=float(p.get("q2_kN", 0.0) or 0.0),
            stage_tension_kN=float(p.get("stage_tension_kN", 0.0) or 0.0),
            stage_tension_e=float(p.get("stage_tension_e", 0.0) or 0.0),
            stage_tension_loss=float(p.get("stage_tension_loss", 1.0) or 1.0),
            lane=bool(p.get("lane", False)),
            creep_rh=float(p.get("creep_rh") or 0.0),
            creep_h=float(p.get("creep_h") or 0.0),
            creep_fcm=float(p.get("creep_fcm") or 0.0),
            creep_t0=float(p.get("creep_t0") or 7.0),
            creep_t_end=float(p.get("creep_t_end") or 0.0),
            shrinkage_ts=float(p.get("shrinkage_ts") or 0.0),
        )

    def _build_grillage3d(self):
        from grillage_3d import build_box_grillage_3d

        p = self.params
        return build_box_grillage_3d(
            spans=(float(p["sp1"]), float(p["sp2"]), float(p["sp1"])),
            B=float(p["B"]), h=float(p["h"]),
            tf=float(p["tf"]), tw=float(p["tw"]),
            n_lines=int(p.get("n_lines", 3)),
            panel=float(p["panel"]),
            q_deck=float(p["q"]) * 1e3,
            staged=bool(p.get("staged", True)),
            lane_code="JTG_D60" if p.get("lane", True) else "",
        )

    def _build_cable_stayed(self):
        from cable_stayed_3d import build_cable_stayed_3d

        p = self.params
        model, info = build_cable_stayed_3d(
            main_span=float(p["Lm"]), side_span=float(p["Ls"]),
            tower_height=float(p["Ht"]), n_cable=int(p["n"]),
            panel=float(p.get("panel", 8.0)),
            B=float(p["B"]), h=float(p["h"]),
            tower_b=float(p.get("tower_b", 3.0)),
            tower_h=float(p.get("tower_h", 4.5)),
            q_deck=float(p["q"]) * 1e3,
            q2_deck=float(p.get("q2_deck", 0.0) or 0.0) * 1e3,
            cable_area_mm2=float(p["A"]),
            method=str(p.get("method", "DECK")),
            min_tension_fraction=float(p.get("mint", 0.02) or 0.02),
            nl_steps=int(p.get("nl_steps", 3) or 3),
            newton=int(p.get("newton", 30) or 30),
            max_iter=int(p.get("max_iter", 20) or 20),
            staged=bool(p.get("staged", False)),
            retune=bool(p.get("retune", True)),
            system=str(p.get("system") or "semi"),
            creep_rh=float(p.get("creep_rh") or 0.0),
            creep_h=float(p.get("creep_h") or 0.0),
            creep_fcm=float(p.get("creep_fcm") or 0.0),
            shrinkage_ts=float(p.get("shrinkage_ts") or 0.0),
            cycle_days=float(p.get("cycle_days") or 7.0),
            load_age_days=float(p.get("load_age_days") or 7.0),
        )
        # 派生量挂在外壳上：界面要支座 x 才能画红虚线，而 pydantic 契约里
        # 不该出现这种"只给界面用"的字段。
        self._info = dict(info)
        self._info["fpu"] = float(p.get("fpu", 1860.0)) * 1e6
        return model

    def _build_suspension(self):
        """悬索桥：主缆线形由恒载决定，建造器只给初值，真线形由内核找形。

        界面这一层不预设边跨：`Ls = 0` 交给建造器按体系取——自锚式取使
        锚点 V=0 的平衡边跨（√(2HΔz/q)），地锚式取 1/3 主跨。若在这里写死
        一个默认值，自锚式会被随手填的数把梁端拔起来，而算出来的位移、
        索力全都"正常"，只有梁端支反力是负的。
        """
        from suspension_3d import build_suspension_3d

        p = self.params
        n_sag = float(p.get("sag", 10.0))
        # 车道折减系数：车道数变了但折减系数仍是 3 车道默认值时，按
        # JTG D60 表 4.3.1-5 自动跟正（UI 只改车道数不会联动折减系数）。
        _lc = int(p.get("lane_count", 3))
        _lr = float(p.get("lane_reduction", 0.78))
        if _lc != 3 and abs(_lr - 0.78) < 1e-9:
            _lr = _LANE_REDUCTION.get(_lc, _lr)
        model, info = build_suspension_3d(
            system=str(p.get("system", "EARTH")).upper(),
            main_span=float(p["Lm"]),
            side_span=float(p.get("Ls", 0.0) or 0.0),
            sag_ratio=(1.0 / n_sag) if n_sag > 0 else 0.10,
            clearance=float(p.get("clear", 5.0)),
            panel=float(p.get("panel", 10.0)),
            side_hangers=bool(p.get("sh", True)),
            B=float(p["B"]), h=float(p["h"]),
            tf=float(p.get("tf", 0.028)), tw=float(p.get("tw", 0.020)),
            tower_b=float(p.get("tower_b", 3.0)),
            tower_h=float(p.get("tower_h", 4.0)),
            n_tower_seg=int(p.get("tp", 6)),
            q_deck=float(p["q"]) * 1e3,
            q2_deck=float(p.get("q2_deck", 0.0) or 0.0) * 1e3,
            main_area_mm2=float(p.get("A", 60000.0)),
            hanger_area_mm2=float(p.get("Ah", 3000.0)),
            erection=bool(p.get("erection", False)),
            erection_panels=int(p.get("erection_panels", 2)),
            lane_count=_lc,
            lane_reduction=_lr,
        )
        self._info = dict(info)
        self._info["fpu"] = float(p.get("fpu", 1770.0)) * 1e6
        return model

    def _build_cable_stayed_2d(self):
        """2D 平面斜拉桥：主梁降成一条梁线、扇形索面压进 x-y 平面。

        与三维共用工程参数（Lm/Ls/Ht/n/q/A），但走 2D 内核（NONLINEAR，
        CABLE 以 ISTRN 预张）。塔梁固结体系，悬臂施工 + 逐段张拉可选。
        """
        from bridge_spec import build_cable_stayed_2d

        p = self.params
        return build_cable_stayed_2d(
            main_span=float(p["Lm"]), side_span=float(p["Ls"]),
            tower_height=float(p["Ht"]), n_cable=int(p["n"]),
            h=float(p["h"]), B=float(p["B"]),
            tf=float(p.get("tf", 0.28)), tw=float(p.get("tw", 0.45)),
            tower_b=float(p.get("tower_b", 3.0)),
            tower_h=float(p.get("tower_h", 4.5)),
            tower_below=float(p.get("tower_below", 8.0)),
            anchor_low=float(p.get("anchor_low", 0.55)),
            q_kN=float(p["q"]),
            q2_kN=float(p.get("q2_deck", 0.0) or 0.0),
            cable_area_mm2=float(p["A"]),
            staged=bool(p.get("staged", False)),
            lane=bool(p.get("lane", False)),
            creep_rh=float(p.get("creep_rh") or 0.0),
            creep_h=float(p.get("creep_h") or 0.0),
            creep_fcm=float(p.get("creep_fcm") or 0.0),
            creep_t0=float(p.get("load_age_days") or p.get("creep_t0") or 7.0),
            creep_t_end=float(p.get("creep_t_end") or 0.0),
            shrinkage_ts=float(p.get("shrinkage_ts") or 0.0),
            load_age_days=float(p.get("load_age_days") or 7.0),
            cycle_days=float(p.get("cycle_days") or 7.0),
        )

    def _build_suspension_2d(self):
        """2D 平面悬索桥：单跨双塔，主缆按抛物线初值、恒载水平力 H=qL²/8f 以
        ISTRN 预张。只做成桥态一次静力（无施工阶段/车道数，见 DEFAULTS 说明）。
        """
        from bridge_spec import build_suspension_2d

        p = self.params
        n_sag = float(p.get("sag", 10.0))
        return build_suspension_2d(
            system=str(p.get("system", "EARTH")).upper(),
            main_span=float(p["Lm"]),
            side_span=float(p.get("Ls", 0.0) or 0.0),
            sag_ratio=(1.0 / n_sag) if n_sag > 0 else 0.10,
            clearance=float(p.get("clear", 5.0)),
            panel=float(p.get("panel", 10.0)),
            side_hangers=bool(p.get("sh", True)),
            h=float(p["h"]), B=float(p["B"]),
            tf=float(p.get("tf", 0.028)), tw=float(p.get("tw", 0.020)),
            tower_b=float(p.get("tower_b", 3.0)),
            tower_h=float(p.get("tower_h", 4.0)),
            n_tower_seg=int(p.get("tp", 6)),
            q_kN=float(p["q"]),
            main_area_mm2=float(p.get("A", 60000.0)),
            hanger_area_mm2=float(p.get("Ah", 3000.0)),
            lane=bool(p.get("lane", False)),
        )

    def _build_arch_3d(self):
        """大跨钢箱拱桥（拱肋分段吊装 + 斜拉扣索辅助 + 合拢 + 吊杆主梁 + 铺装）。

        与 cable_stayed 的 staged 模式同一条链：建造器不挂 `cable_bridge`，
        `detect_kind` 落到线性阶段内核（grillage3d），施工序列由 `*STAGE` 承载，
        临时扣索按 `TENSION=` 目标索力张拉、合拢后 `DEACTIVE` 拆除。
        """
        from arch_3d import build_arch_3d

        p = self.params
        model, info = build_arch_3d(
            span=float(p["span"]), rise=float(p["rise"]),
            n_seg_half=int(p["n_seg_half"]),
            arch_h=float(p["arch_h"]), arch_b=float(p["arch_b"]),
            arch_tf=float(p["arch_tf"]), arch_tw=float(p["arch_tw"]),
            deck_h=float(p["deck_h"]), deck_b=float(p["deck_b"]),
            deck_tf=float(p.get("deck_tf", 0.03)),
            deck_tw=float(p.get("deck_tw", 0.022)),
            hanger_area_mm2=float(p["hanger_area_mm2"]),
            cable_area_mm2=float(p["cable_area_mm2"]),
            cable_fpu=float(p.get("cable_fpu", 1860.0)) * 1e6,
            q_deck=float(p["q_deck"]) * 1e3,
            q2_deck=float(p.get("q2_deck", 0.0) or 0.0) * 1e3,
        )
        self._info = dict(info)
        self._info["fpu"] = float(p.get("cable_fpu", 1860.0)) * 1e6
        return model

    # ------------------------------------------------------------ 校验

    def validate(self) -> list[str]:
        """返回中文问题列表；空列表表示可以安全求解。**不抛异常**。"""
        errs: list[str] = []
        # 逐跨张拉的参数问题先给中文提示：建造器也会硬拦，但若等它抛出来，
        # 界面只会看到一句「模型参数不完整或非法」，看不出是哪个控件填错了。
        if self.builder_key == "beam_bridge":
            early = self._bridge_param_checks()
            if early:
                return early
        try:
            self.build()
        except Exception as e:                       # 参数缺失/类型错
            return [f"模型参数不完整或非法：{e}"]

        if self.builder_key == "ps_beam":
            p = self.params
            if float(p.get("length") or 0) <= 0:
                errs.append("跨度必须大于 0")
            if float(p.get("P_kN") or 0) <= 0:
                errs.append("张拉力必须大于 0")
            loss = float(p.get("loss") or 0)
            if not 0 < loss <= 1:
                errs.append("预应力有效系数必须在 (0, 1]")
            errs.extend(self.warnings_3d())
        elif self.kind == "beam":
            m: ModelSpec = self._model
            if m.length <= 0:
                errs.append("跨度必须大于 0")
            if m.section.h <= 0 or m.section.b <= 0:
                errs.append("截面高度/宽度必须大于 0")
            for i, ld in enumerate(m.loads, 1):
                if not math.isfinite(ld.value):
                    errs.append(f"荷载 {i} 的数值非法")
                if ld.type == "point" and ld.position is None:
                    errs.append(f"集中力 {i} 缺少作用位置")
                if ld.type == "udl" and ld.end is not None and ld.start is not None:
                    if ld.end <= ld.start:
                        errs.append(f"均布荷载 {i} 的区间无效（终点 ≤ 起点）")
            if not m.supports:
                errs.append("没有支座（结构会是机构）")
        elif self.is_3d:
            errs = self._param_checks_3d() + self.warnings_3d()
        elif self.kind in ("cable_stayed", "suspension"):
            # 2D 索承体系：参数量级检查沿用三维的 Lm/sag/A 分支，但模型自洽校验
            # 走 2D 的 validate_model（三维的 warnings_3d 引用完整性不适用）。
            errs = self._param_checks_3d() + self._model.validate_model()
        else:
            errs = self._bridge_param_checks() + self._model.validate_model()
        return errs

    def warnings_3d(self) -> list[str]:
        """三维模型的引用完整性硬错误（`validate_model_3d` 的第一返回值）。

        与 `validate()` 分开：界面要在求解前拦错误、同时把提示折叠起来，
        两件事不该挤在一个列表里靠数个数来区分。
        """
        from model_spec_3d import validate_model_3d

        errs, _ = validate_model_3d(self.model)
        return list(errs)

    def hints_3d(self) -> list[str]:
        """三维模型的"可以算但建议看一眼"提示。"""
        from model_spec_3d import validate_model_3d

        _, warns = validate_model_3d(self.model)
        return list(warns)

    def _param_checks_3d(self) -> list[str]:
        """三维参数量级检查。

        与二维桥型同样的道理：`validate_model_3d()` 查的是"节点/单元表自洽"，
        拦不住"塔高写成 6000 m"这种几何自洽但物理荒谬的参数。
        """
        p = self.params
        e: list[str] = []
        if int(p.get("cable_planes") or 1) > 1:
            e.append("当前只建立单索面。描述里的「双索面」不会生成第二索面和横梁，"
                     "请改为「单索面」后再计算。")
        if p.get("system_conflict"):
            e.append("悬臂施工与半漂浮成桥不能写在同一次计算里。"
                     "内核不能在合拢后解除塔梁临时固结。"
                     "请只保留一种体系：悬臂施工按塔梁固结贯穿全程，"
                     "或单独计算半漂浮成桥。")

        def pos(key: str, label: str) -> float:
            v = p.get(key)
            if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
                e.append(f"{label}必须为正数")
                return 0.0
            return float(v)

        if self.builder_key == "grillage3d":
            sp1 = pos("sp1", "边跨")
            sp2 = pos("sp2", "中跨")
            h = pos("h", "梁高")
            B = pos("B", "箱宽")
            tf = pos("tf", "顶/底板厚")
            tw = pos("tw", "腹板厚")
            pos("q", "桥面均布荷载")
            if sp1 and sp2 and sp2 < 0.5 * sp1:
                e.append(f"中跨 {sp2:g} m 不足边跨 {sp1:g} m 的一半，"
                         "三跨连续梁的受力与配束都不成立")
            if h and sp2 and h < sp2 / 40.0:
                e.append(f"梁高 {h:g} m 对 {sp2:g} m 中跨偏矮（< L/40），"
                         "梁格法假定平面截面保持，高跨比过小会失真")
            if tf and h and 2 * tf >= h:
                e.append(f"顶+底板厚 {2 * tf:g} m 已达梁高 {h:g} m，腹板无高度")
            if tw and B and 2 * tw >= B:
                e.append(f"两片腹板厚 {2 * tw:g} m 已达箱宽 {B:g} m")
            nl = p.get("n_lines")
            if not isinstance(nl, int) or nl < 3 or nl % 2 == 0:
                e.append(f"纵梁线条数 {nl} 必须是 ≥3 的奇数（含中梁线，对称）")
        elif self.kind == "suspension":
            Lm = pos("Lm", "主跨")
            pos("h", "加劲梁高")
            pos("B", "加劲梁宽")
            pos("q", "桥面恒载")
            A = pos("A", "单根主缆面积")
            Ah = pos("Ah", "单根吊索面积")
            pos("panel", "吊索间距")
            n = p.get("sag")
            if not isinstance(n, (int, float)) or isinstance(n, bool) or n <= 0:
                e.append("矢跨比分母必须为正数（如 10 表示 1/10）")
            elif not 7.0 <= float(n) <= 14.0:
                e.append(f"矢跨比 1/{n:g} 超出常规（1/12~1/8）。过小则 H 与塔顶"
                         "竖向反力畸高，过大则吊索过长、梁的刚度靠不上主缆")
            Ls = p.get("Ls") or 0.0
            if Ls and float(Ls) > 0 and Lm and float(Ls) > 0.8 * Lm:
                e.append(f"边跨 {float(Ls):g} m 接近主跨 {Lm:g} m；边跨通常取"
                         " 1/3 主跨（地锚式）或平衡边跨 √(2HΔz/q)（自锚式）")
            if A and A < 5000:
                e.append(f"单根主缆面积 {A:g} mm² 过小，请核对是否写成 cm²")
            if Ah and A and Ah > A:
                e.append(f"吊索面积 {Ah:g} mm² 大于主缆面积 {A:g} mm²，请核对")
            q2 = p.get("q2_deck") or 0.0
            if isinstance(q2, (int, float)) and q2 > 0 and p.get("q") and q2 > float(p["q"]):
                e.append(f"二期恒载 {q2:g} kN/m 超过总恒载 {float(p['q']):g} kN/m，"
                         "一期恒载会变负")
            if "lane_count" in p:
                lc = p.get("lane_count")
                if not isinstance(lc, int) or not 1 <= lc <= 8:
                    e.append(f"车道数 {lc} 超出范围（1~8）")
        elif self.builder_key == "arch_3d":
            span = pos("span", "主跨")
            rise = pos("rise", "矢高")
            pos("arch_h", "拱肋高")
            pos("arch_b", "拱肋宽")
            pos("arch_tf", "拱肋翼缘厚")
            pos("arch_tw", "拱肋腹板厚")
            pos("deck_h", "主梁高")
            pos("deck_b", "主梁宽")
            pos("q_deck", "一期恒载")
            nseg = p.get("n_seg_half")
            if not isinstance(nseg, int) or nseg < 3:
                e.append(f"半跨吊装段数 {nseg} 太少（≥3：拱脚段 + 吊装段 + 合拢段）")
            if span and rise and not 2.0 <= span / rise <= 8.0:
                e.append(f"矢跨比 span/rise = {span / rise:.2f} 超出合理范围 2~8，"
                         "过坦则水平推力畸高、过陡则失稳")
            if span and rise and rise > 0.35 * span:
                e.append(f"矢高 {rise:g} m 超过主跨 {span:g} m 的 0.35，矢跨比过陡")
            q2 = p.get("q2_deck") or 0.0
            if isinstance(q2, (int, float)) and q2 > 0 and p.get("q_deck") and q2 > float(p["q_deck"]):
                e.append(f"二期恒载 {q2:g} kN/m 超过一期 {float(p['q_deck']):g} kN/m，"
                         "一期恒载会变负")
        elif self.builder_key == "truss_3d":
            span = pos("span", "跨度")
            depth = pos("depth", "桁高")
            width = pos("width", "桥宽")
            pos("q_deck", "桥面均布恒载")
            pos("A_chord", "弦杆面积")
            pos("A_diag", "腹杆面积")
            pos("A_lat", "横向联系面积")
            npnl = p.get("n_panel")
            if not isinstance(npnl, int) or not 2 <= npnl <= 60:
                e.append(f"节间数 {npnl} 超出范围（2~60）")
            if span and depth and depth >= span:
                e.append(f"桁高 {depth:g} m 已达跨度 {span:g} m，桁架退化成横梁")
            if span and depth and depth < span / 20.0:
                e.append(f"桁高 {depth:g} m 对 {span:g} m 跨度偏矮（< L/20），"
                         "弦杆轴力会畸高")
            if span and width and width > span / 2.0:
                e.append(f"桥宽 {width:g} m 超过跨度 {span:g} m 的一半，"
                         "横向联系杆件过长，请核对是否把桥宽写成了跨度")
        else:
            Lm = pos("Lm", "主跨")
            Ls = pos("Ls", "边跨")
            Ht = pos("Ht", "塔高")
            pos("h", "主梁高")
            pos("B", "主梁宽")
            pos("q", "桥面恒载")
            A = pos("A", "单索面积")
            n = p.get("n")
            if not isinstance(n, int) or n < 3:
                e.append(f"每塔每侧索数 {n} 太少（≥3），无法形成扇形索面")
            if Lm and Ls and Ls > 0.8 * Lm:
                e.append(f"边跨 {Ls:g} m 接近主跨 {Lm:g} m，"
                         "边跨/主跨通常取 0.35~0.5，过大时边跨索力会主导设计")
            if Ht and Lm and Ht < 0.12 * Lm:
                e.append(f"塔高 {Ht:g} m 相对主跨 {Lm:g} m 偏矮（< L/8），"
                         "索倾角过小，竖向分力效率低")
            if A and A < 500:
                e.append(f"单索面积 {A:g} mm² 过小，请核对是否写成 cm²")
        return e


    def _bridge_param_checks(self) -> list[str]:
        """桥型的参数量级检查。

        `validate_model()` 查的是"节点/单元表是否自洽"——它拦不住
        "跨度写成 120000 m"这种模型，因为那样建出来的几何依然自洽。
        参数层面的荒谬必须在参数层面拦。
        """
        p = self.params
        errs: list[str] = []
        span = p.get("span")
        if not isinstance(span, (int, float)) or isinstance(span, bool) or span <= 0:
            return ["跨度必须是正数"]
        if span > 5000:
            errs.append(f"跨度 {span:g} m 超出合理范围（>5000 m），请检查是否把 mm 当成了 m")

        if self.kind == "arch":
            rise = p.get("rise") or 0
            if rise <= 0:
                errs.append("矢高必须大于 0")
            elif rise > span:
                errs.append(f"矢高 {rise:g} m 大于跨度 {span:g} m")
            nh = p.get("n_hanger") or 0
            if not 1 <= nh <= 40:
                errs.append(f"吊杆对数 {nh} 超出范围（1~40）")
        elif self.kind == "truss":
            depth = p.get("depth") or 0
            if depth <= 0 or depth > span:
                errs.append(f"桁高 {depth:g} m 不合理（应在 0~跨度 {span:g} m 之间）")
            npnl = p.get("n_panel") or 0
            if not 2 <= npnl <= 60:
                errs.append(f"节间数 {npnl} 超出范围（2~60）")
        else:
            h = p.get("h") or 0
            if h <= 0 or h > span:
                errs.append(f"梁高 {h:g} m 不合理（应在 0~跨度 {span:g} m 之间）")
            ns = p.get("n_spans") or 0
            if not 1 <= ns <= 8:
                errs.append(f"跨数 {ns} 超出范围（1~8）")

        # 逐跨张拉：建造器会硬拦"没开 staged / 单跨"，这里给界面中文提示，
        # 顺带把"偏心超出截面半高"这种几何自洽但物理荒谬的输入拦下来。
        if self.builder_key == "beam_bridge":
            pt = float(p.get("stage_tension_kN") or 0.0)
            if pt > 0:
                if not p.get("staged") or int(p.get("n_spans") or 1) < 2:
                    errs.append("逐跨张拉需要先开启「逐跨施工」且跨数 ≥ 2")
                loss = float(p.get("stage_tension_loss") or 0.0)
                if not 0.0 < loss <= 1.0:
                    errs.append("预应力总损失系数必须在 (0, 1]")
                h = float(p.get("h") or 0.0)
                e = float(p.get("stage_tension_e") or 0.0)
                if h > 0 and abs(e) > h / 2:
                    errs.append(
                        f"钢束偏心 |e|={abs(e):g} m 超出截面半高 {h / 2:g} m")
        return errs

    # ------------------------------------------------------------ 契约

    def to_dat(self) -> str:
        """导出 FEM-Eigen `.dat` 文本——唯一对外求解契约。"""
        if self.kind == "beam":
            from fem_eigen_export import spec_to_dat

            return spec_to_dat(
                self.model,
                target_len=float(self.params.get("target_len", 0.5)),
                self_weight=bool(self.params.get("self_weight", False)),
            )
        return self.model.to_dat()

    def capability(self) -> dict[str, bool]:
        """能力自述：界面据此决定显示哪些区块，而不是写 `if 是桥`。"""
        from fem_eigen_export import fem_eigen_available

        if self.is_3d:
            # 三维只有一条路：三维内核。没有 anastruct 草算，也没有第三方互校。
            try:
                from solver_3d import capabilities, detect_kind

                sk = detect_kind(self.model)
                ok3d = bool(capabilities().get(sk))
                nonlinear = sk in ("cable_stayed", "suspension")
            except Exception:
                ok3d = False
                nonlinear = False
            has_cable = (self.kind in ("cable_stayed", "suspension")
                         or self.builder_key == "arch_3d")
            if (self.kind == "cable_stayed" and self.params.get("staged")
                    and self.params.get("nonlinear_stages")):
                nonlinear = True
                # `stage_nl` 已正式注册进 solver_3d，能力自述走注册表，
                # 文档层不再自己拼 exe 路径（少一处硬编码补位）。
                ok3d = bool(capabilities().get("stage_nl"))
            return {
                "anastruct": False,
                "fem_eigen": ok3d,          # 实际内核是否可用
                "matplotlib": False,
                "cross_check": False,
                "solve3d": ok3d,
                "cable": has_cable,
                "nonlinear": nonlinear,
            }

        fem = fem_eigen_available()
        is_beam = self.kind == "beam"
        from solve_2d import solve_2d_available

        return {
            "anastruct": is_beam,          # anastruct 只有梁单元
            "fem_eigen": fem,
            "matplotlib": is_beam,         # 截面/变形/弯矩图只对梁画
            # 互校的前提：梁（两边都能算）且不计自重（anastruct 无重力项）
            "cross_check": is_beam and fem and not self.params.get("self_weight", False),
            "solve3d": False,
            # 二维桥型（梁桥/拱桥/桁架）走 solve_2d 链：施工阶段 + 车道 + 组合
            "solve2d": (not is_beam) and solve_2d_available(),
            "cable": self.kind in ("cable_stayed", "suspension"),
            "nonlinear": str(getattr(self.model, "analysis", "LINEAR")).upper()
                         == "NONLINEAR",
        }

    def solve(self, out_dir: str | Path | None = None, dat_name: str | None = None):
        """求解的唯一入口，按维度路由到对应内核。

        三维 → `solver_3d.solve`（SolveResult）；二维桥型 → `solve_2d.solve_2d`
        （Solve2DResult，施工阶段 + 车道活载 + 规范组合）。单梁（ModelSpec）没有
        施工阶段/车道，仍走 `fem_eigen_export.run_fem_eigen` 的一次性静力。
        """
        if self.is_3d:
            from solver_3d import solve as solve_3d_model

            out = out_dir or Path("out") / self.kind
            # 悬臂斜拉默认仍走线性阶段。显式打开非线性施工阶段（或 3D 大跨
            # 自动判定）时，改走注册表里的 `stage_nl` 内核：分派、可用性、
            # 编译提示、run/adapt 契约都与另外三个内核一致，不再有旁路。
            # `stage_nl` 的 result_kind 仍是 grillage3d，界面缓存与报告分支
            # 不会因为新键把结果藏起来。
            if (self.kind == "cable_stayed" and self.params.get("staged")
                    and self.params.get("nonlinear_stages")):
                return solve_3d_model(
                    self.model, out_dir=out, kind="stage_nl",
                    dat_name=dat_name or "stage_nl.dat")
            # 不硬传 kind：悬臂施工的斜拉桥（staged）摘掉了 cable_bridge，按
            # detect_kind 该走线性阶段内核（grillage3d），而不是成桥态内核
            # （cable_stayed）。内核键由模型自述决定，不靠 self.kind 猜。
            return solve_3d_model(self.model, out_dir=out, dat_name=dat_name)

        if self.kind == "beam":
            raise ValueError(
                f"{KIND_LABELS.get(self.kind, self.kind)}是单梁模型，"
                "无施工阶段/车道，请走 fem_eigen_export.run_fem_eigen")

        from solve_2d import solve_2d

        out = out_dir or Path("out") / "2d"
        return solve_2d(self.model, out_dir=out, dat_name=dat_name)

    # ------------------------------------------------------------ 展示

    def summary(self) -> dict[str, Any]:
        """给界面渲染的摘要：标题 + 若干行文本。"""
        self.build()
        if self.builder_key == "ps_beam":
            p = self.params
            s = float(p.get("s") or 0)
            cantilever = p.get("support") == "cantilever"
            if cantilever and not s:
                camber = "直线束作用在悬臂自由端，挠度对得上 Pe·e·L²/(2EI)。筋在下缘时自由端下挠。"
            elif cantilever:
                camber = ("两段悬臂时，自由端挠度对得上 Pe·s·L²/(4EI)。"
                          "矢高向下时自由端下挠，不是简支梁那种上拱。")
            else:
                camber = "两段离散时，跨中上拱对得上 −Pe·s·L²/(12EI)；再细分会趋近均布荷载挠度。"
            lines = [
                f"跨度 {float(p['length']):g} m，{int(p.get('n_seg') or 2)} 段，"
                + ("悬臂" if cantilever else "简支"),
                f"张拉力 {float(p['P_kN']):g} kN，有效系数 {float(p['loss']):g}",
                ("曲线束，矢高 "
                 + f"{s:g} m（负值表示跨中筋更低）"
                 if s else
                 f"直线束，端部偏心 {float(p.get('e') or 0):g} m"),
                (f"钢束面积 {float(p.get('Ap_mm2') or 0):g} mm²，"
                 f"fpk {float(p.get('fpk_MPa') or 1860):g} MPa"
                 if float(p.get("Ap_mm2") or 0) > 0 else
                 "未给钢束面积，不做张拉控制应力校核"),
                camber,
            ]
            if p.get("lane_code") == "JTG_D60" and not cantilever:
                factor = float(p.get("lane_impact") or 1.0)
                shock = (f"已乘手写冲击放大 {factor:g}。"
                         if factor != 1.0 else
                         "未写冲击系数，按竖向基频自动计算 μ。")
                lines.append(
                    "车道按 JTG D60：qk=10.5 kN/m，Pk 随跨径内插。"
                    "两段简支梁跨中 MyMin 对得上 −(qk L²/8 + Pk L/4)。" + shock)
            elif float(p.get("lane_q_kNpm") or 0) > 0 and not cantilever:
                lines.append(
                    f"车道均布 {float(p['lane_q_kNpm']):g} kN/m 向下。"
                    "两段简支梁跨中弯矩包络的 MyMin 对得上 −qL²/8。")
            elif float(p.get("lane_q_kNpm") or 0) > 0 and cantilever:
                lines.append(
                    f"车道均布 {float(p['lane_q_kNpm']):g} kN/m 向下。"
                    "两段悬臂固端 MyMax 在计入自动冲击后对得上 (1+μ)qL²/2。")
            return {"title": "预应力悬臂梁" if cantilever else "预应力简支梁",
                    "lines": lines}
        if self.kind == "beam":
            lines = self._model.summary().splitlines()
            return {"title": KIND_LABELS[self.kind], "lines": lines}
        if self.is_3d:
            return {"title": KIND_LABELS[self.kind],
                    "lines": self._summary_3d().splitlines()}
        spec = self._model
        lines = spec.summary().splitlines()
        return {"title": spec.title, "lines": lines}

    def _summary_3d(self) -> str:
        """三维摘要：规模 + 分类型单元数 + 工况，全部从模型本身统计。"""
        m = self._model
        st = m.stats()
        kinds = st.get("kinds", {})
        lines = [
            f"{m.title}",
            f"节点 {st['nodes']} / 单元 {st['elements']} / 实常数 {st['reals']} / "
            f"材料 {st['materials']}",
            "单元构成：" + "，".join(f"{k} {v}" for k, v in sorted(kinds.items())),
        ]
        if self.kind == "cable_stayed":
            info = self.info
            lines.append(
                f"拉索 {info.get('n_cable_actual', 0)} 根，索重 "
                f"{info.get('cable_w', 0):.0f} N/m，桥面恒载 "
                f"{info.get('total_deck_load', 0) / 1e3:.0f} kN")
            lines.append(
                "支承断面 x = " + "、".join(f"{x:.0f}" for x in info.get("x_supports", []))
                + " m")
            lines.append(f"定索力方法：{info.get('tension_method', '—')}"
                         "（DECK = 按恒载竖向分配）")
            sys_name = "塔梁固结" if info.get("system") == "fixed" else "半漂浮"
            lines.append(f"结构体系：{sys_name}。索面：单索面。")
            if info.get("staged"):
                if self.params.get("nonlinear_stages"):
                    lines.append(
                        "施工与成桥使用同一套塔梁固结约束。"
                        "求解走非线性施工阶段：每个阶段是全量平衡，"
                        "索力由无应力索长命中。"
                        "每段末写了龄期，混凝土按 φ(t)−φ(上一阶段) 徐变。")
                else:
                    lines.append(
                        "施工与成桥使用同一套塔梁固结约束。"
                        "本模型走线性阶段内核，不是成桥几何非线性。")
        elif self.builder_key == "arch_3d":
            info = self.info
            lines.append(
                f"主跨 {info.get('span', 0):.0f} m / 矢高 {info.get('rise', 0):.0f} m，"
                f"矢跨比 1/{info.get('sag_ratio', 0):.1f}")
            lines.append(
                f"拱肋 {info.get('n_arch_seg', 0)} 段 + 吊杆 {info.get('n_hanger', 0)} 根"
                f" + 临时扣索 {len(info.get('cable_ids', []))} 根")
            lines.append(
                f"施工阶段 {st.get('stages', 0)} 个：拱脚段 → 对称吊装+扣索张拉 → 合拢"
                " → 拆扣索 → 吊杆主梁 → 二期铺装")
        elif self.builder_key == "truss_3d":
            info = self.info
            lines.append(
                f"主跨 {info.get('span', 0):.0f} m / 桁高 {info.get('depth', 0):.0f} m"
                f" / 桥宽 {info.get('width', 0):.0f} m，{info.get('n_panel', 0)} 节间")
            lines.append(
                f"弦杆 {len(info.get('chord_elems', []))} 根 / 腹杆 "
                f"{len(info.get('diag_elems', []))} 根 / 横向联系 "
                f"{len(info.get('lat_elems', []))} 根")
            lines.append(
                "纯 TRUSS 单元，一次成桥静力，走线性阶段内核。"
                "桥面恒载已含自重；弦杆轴力由位移反算。")
        else:
            lines.append(f"施工阶段 {st.get('stages', 0)} 个，"
                         f"活载工况 {len(getattr(m, 'influences', []) or [])} 个")
        return "\n".join(lines)

    def param_rows(self) -> list[list[Field]]:
        """当前类型下要显示的控件行（已按 when 过滤）。"""
        rows = []
        for row in FIELDS[self.builder_key]:
            keep = [f for f in row if f.get("when") is None or f["when"](self.params)]
            if keep:
                rows.append(keep)
        return rows

    # ------------------------------------------------------------ 序列化

    def to_dict(self) -> dict[str, Any]:
        return {"version": 1, "kind": self.kind, "dimension": self.dimension,
                "params": self.params}

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> "ModelDoc":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(kind=d["kind"], params=d.get("params", {}),
                   dimension=d.get("dimension"))

    # ------------------------------------------------------------ 构造入口

    @classmethod
    def from_beam_spec(cls, spec: ModelSpec, **solve_opts: Any) -> "ModelDoc":
        """把解析出来的 ModelSpec 转成 doc。

        `supports` 若是「按跨度自动生成」的标准布置，就存 None，让改跨度时
        支座跟着走；只有用户/解析明确给了非标准支座时才存显式列表。
        """
        doc = cls("beam")
        doc.params.update({
            "name": spec.name,
            "length": spec.length,
            "n_spans": spec.n_spans,
            "shape": spec.section.shape,
            "h": spec.section.h,
            "b": spec.section.b,
            "tw": spec.section.tw,
            "tf": spec.section.tf,
            "material": spec.material_name,
            "supports": None if _supports_are_default(spec) else
                        [s.model_dump() for s in spec.supports],
            "loads": [ld.model_dump() for ld in spec.loads],
        })
        doc.params.update(solve_opts)
        return doc

    @classmethod
    def from_text(
        cls,
        text: str,
        prev: "ModelDoc | None" = None,
        llm_config: Any = None,
        hint: str | None = None,
        dim_hint: str | None = None,
    ) -> tuple["ModelDoc", str, Diag]:
        """统一解析入口：先分类，再走对应管线。

        返回值与 `nl_parser.parse_nl` 同构（doc, source, diag），界面不必分叉。

        `dim_hint` 是界面维度下拉（"二维"/"三维"）；给了就覆盖描述判定，
        不给则按描述关键词自动判（默认 2d）。
        """
        if prev is not None and not isinstance(prev, ModelDoc):
            # 最常见的误用：把 LLMConfig 当成第二个位置参数传进来
            raise TypeError(
                "prev 必须是 ModelDoc 或 None；LLM 配置请用 llm_config= 传入")
        kind, dimension = classify_text(text, hint)
        if dim_hint in DIM_LABELS:
            dimension = dim_hint

        if kind == "beam" and re.search(r"预应力|钢束|预加力", text):
            params, notes = extract_ps_beam_params(text)
            return cls("beam", params, dimension="3d"), "rule(预应力梁)", Diag(notes=notes)

        if kind == "beam":
            from nl_parser import parse_nl

            prev_spec = prev.model if (prev is not None and prev.kind == "beam") else None
            spec, source, diag = parse_nl(text, prev=prev_spec, llm_config=llm_config)
            # 沿用上一份的求解选项，避免解析一次就把"计入自重"重置掉
            opts = {}
            if prev is not None and prev.kind == "beam":
                opts = {k: prev.params[k] for k in ("target_len", "self_weight")
                        if k in prev.params}
            return cls.from_beam_spec(spec, **opts), source, diag

        if dimension == "3d":
            # 三维只走规则解析：参数量少、名字确定（sp1/Lm/Ht），而 LLM 侧
            # 没有对应的 Schema。加 Schema 的成本高于收益——真正需要"理解"
            # 的是二维那座桥，三维这几十个参数正则就够了。
            # 注意传建造器键（bk）：extract_3d_params 按 grillage3d/arch_3d/
            # cable_stayed/suspension 分支，不是按 6 值 kind。
            bk = BUILDERS.get((kind, dimension), kind)
            params, notes = extract_3d_params(text, bk)
            params = _keep_solver_opts(prev, kind, params)
            return cls(kind, params, dimension=dimension), "rule(三维)", Diag(notes=notes)

        if kind in ("cable_stayed", "suspension"):
            # 2D 索承体系：参数名与三维同一套（Lm/Ls/Ht/sag/A…），复用三维规则
            # 解析，但用 2D 建造器键取默认值（少了 method/fpu/erection/lane_count
            # 等三维项）。几何仍由 build() 展开，参数是唯一真源。
            bk = BUILDERS.get((kind, dimension), kind)
            params, notes = extract_3d_params(text, bk)
            params = _keep_solver_opts(prev, kind, params)
            return cls(kind, params, dimension=dimension), "rule(索承2d)", Diag(notes=notes)

        from bridge_spec import extract_bridge_params

        # 桥梁侧也走 LLM 了，但**只出参数**（bridge_llm），几何仍由 build() 算。
        # 这是与 explore-x20 的关键区别：那边让模型直接吐节点表，拓扑会被冻死。
        cfg = _as_llm_config(llm_config)
        prev_params = prev.params if (prev is not None and prev.kind == kind) else None

        if cfg is not None and getattr(cfg, "api_key", ""):
            from bridge_llm import parse_bridge_with_llm

            r = parse_bridge_with_llm(text, cfg, kind=kind, prev=prev_params)
            if r.ok:
                params = _keep_solver_opts(prev, r.kind, r.params)
                return cls(r.kind, params), "llm(桥梁)", Diag(notes=r.notes)
            _, params, notes = extract_bridge_params(text)
            notes = [f"LLM 未采用，已回退规则解析：{r.error}"] + notes
            return (cls(kind, _keep_solver_opts(prev, kind, params)),
                    "rule(桥梁)(llm失败回退)",
                    Diag(ok=False, notes=notes, error=r.error, raw=r.raw))

        bridge_kind, params, notes = extract_bridge_params(text)
        return (cls(bridge_kind, _keep_solver_opts(prev, bridge_kind, params)),
                "rule(桥梁)", Diag(notes=notes))


# ---------------------------------------------------------------- 内部工具


def _as_llm_config(cfg: Any) -> Any:
    """兼容旧的 dict 形式配置（界面早期版本传的是 dict）。"""
    if isinstance(cfg, dict):
        from llm_parser import LLMConfig

        return LLMConfig(
            api_key=cfg.get("api_key", ""),
            base_url=cfg.get("base_url", "https://api.deepseek.com"),
            model=cfg.get("model", "deepseek-chat"),
        )
    return cfg


def _keep_solver_opts(prev: "ModelDoc | None", kind: str,
                      params: dict[str, Any]) -> dict[str, Any]:
    """沿用上一份模型的求解选项，避免"解析一次就把计入自重重置掉"。

    `self_weight`/`shape` 是界面选项，不在 LLM Schema 里，也不在规则解析结果里，
    但它们在 DEFAULTS 中，每次新建 ModelDoc 都会被重置成默认值。
    """
    if prev is None or prev.kind != kind:
        return params
    out = dict(params)
    for k in ("self_weight", "shape"):
        if k in prev.params and k not in out:
            out[k] = prev.params[k]
    return out


def creep_phi_from_age(t_days: float, t0_days: float = 7.0, phi0: float = 2.0,
                       beta_h: float = 700.0, power: float = 0.3) -> float:
    """与 FEM-Eigen 二维内核 creepCoefficientAt 的简化曲线相同。

    φ = φ0 · [(t−t0)/(βH + t−t0)]^power。阶段求解用相邻龄期的差值，不在这里一次乘完。
    """
    dt = max(0.0, float(t_days) - float(t0_days))
    return float(phi0) * (dt / (beta_h + dt)) ** power


def theoretical_thickness_mm(area_m2: float, exposed_perimeter_m: float) -> float:
    """JTG 3362 的理论厚度 h=2A/u，返回 mm。u 是与大气接触的周长。"""
    if exposed_perimeter_m <= 0.0 or area_m2 <= 0.0:
        return 0.0
    return 2000.0 * float(area_m2) / float(exposed_perimeter_m)


def closed_contour_perimeter_m(width_m: float, depth_m: float) -> float:
    """闭合箱或实心矩形的外轮廓周长。箱内空腔不计入。"""
    return 2.0 * (float(width_m) + float(depth_m))


def jtg3362_nominal_creep(rh_percent: float, h_mm: float, fcm_mpa: float,
                          t0_days: float) -> tuple[float, float]:
    """JTG 3362-2018 附录 C.2.1 的名义徐变系数和 βH。与内核 jtg3362NominalCreep 相同。

    返回 (φ0, βH)。发展项仍用 creep_phi_from_age 的 0.3 次方。
    表 C.2.2 对 C50 及以上的折减是查表用的，这里不重复乘。
    """
    rh = min(99.0, max(40.0, float(rh_percent)))
    h = max(float(h_mm), 1.0)
    fcm = max(float(fcm_mpa), 1.0)
    t0 = max(float(t0_days), 0.1)
    phi_rh = 1.0 + (1.0 - rh / 100.0) / (0.46 * (h / 100.0) ** (1.0 / 3.0))
    beta_f = 5.3 / (fcm / 10.0) ** 0.5
    beta_t0 = 1.0 / (0.1 + t0 ** 0.2)
    beta_h = 150.0 * (1.0 + (1.2 * rh / 100.0) ** 18.0) * (h / 100.0) + 250.0
    if beta_h > 1500.0:
        beta_h = 1500.0
    return phi_rh * beta_f * beta_t0, beta_h


def jtg3362_shrinkage_nominal(rh_percent: float, fcm_mpa: float) -> float:
    """JTG 3362 附录 C.1 的名义收缩系数，正值缩短。与内核 jtg3362ShrinkageNominal 相同。"""
    rh = min(99.0, max(40.0, float(rh_percent)))
    fcm = max(float(fcm_mpa), 1.0)
    eps_s = (160.0 + 50.0 * (9.0 - fcm / 10.0)) * 1e-6
    beta_rh = 1.55 * (1.0 - (rh / 100.0) ** 3.0)
    return eps_s * beta_rh


def jtg3362_shrinkage(t_days: float, t_ref_days: float, ts_days: float,
                      rh_percent: float, h_mm: float, fcm_mpa: float) -> float:
    """从 t_ref 到 t 新发展的收缩应变。干燥从 ts 起算。正值缩短。"""
    def beta(t: float) -> float:
        dt = max(0.0, float(t) - float(ts_days))
        h = max(float(h_mm), 1.0)
        return (dt / (350.0 * (h / 100.0) ** 2.0 + dt)) ** 0.5

    return jtg3362_shrinkage_nominal(rh_percent, fcm_mpa) * (
        beta(t_days) - beta(t_ref_days))


def creep_phi_jtg3362(t_days: float, t0_days: float, rh_percent: float,
                      h_mm: float, fcm_mpa: float) -> float:
    """按附录 C.2.1 计算 φ(t, t0)。阶段求解仍取相邻龄期的差。"""
    phi0, beta_h = jtg3362_nominal_creep(rh_percent, h_mm, fcm_mpa, t0_days)
    return creep_phi_from_age(t_days, t0_days, phi0, beta_h, 0.3)


def extract_ps_beam_params(text: str) -> tuple[dict[str, Any], list[str]]:
    """一跨梁上的一束预应力。只抽参数，网格由 build 展开。

    矢高向下（跨中筋更低）记为负，简支梁上这是产生上拱的方向。
    两段简支梁时跨中挠度对得上 −Pe·s·L²/(12EI)。
    悬臂梁的直线束自由端挠度对得上 Pe·e·L²/(2EI)。
    两段悬臂的曲线束自由端挠度对得上 Pe·s·L²/(4EI)，矢高向下时向下。
    """
    p: dict[str, Any] = {}
    notes: list[str] = []
    if re.search(r"悬臂", text) and not re.search(r"悬臂施工", text):
        p["support"] = "cantilever"
        notes.append("按悬臂梁建模：一端固结，另一端自由。直线束筋在下缘时自由端下挠。")
    else:
        p["support"] = "simple"
    m = re.search(r"(?:跨度|梁长)\s*([0-9.]+)\s*(?:米|m)?", text)
    if not m:
        m = re.search(r"([0-9.]+)\s*(?:米|m)\s*(?:的)?(?:简支|悬臂)?梁", text)
    if m:
        length = float(m.group(1))
        if length > 1000:
            length /= 1000.0
            notes.append(f"跨度疑为毫米，已按 {length:g} m 处理")
        p["length"] = length
    else:
        p["length"] = 10.0
        notes.append("未说明跨度，默认 10 m")
    m = re.search(
        r"(?:张拉力|预加力)\s*([0-9.]+)\s*(MN|兆牛|kN|KN|千牛|N|牛)?", text, re.I)
    if m:
        val = float(m.group(1))
        unit = (m.group(2) or "").lower()
        if unit in ("mn", "兆牛"):
            p["P_kN"] = val * 1000.0
        elif unit in ("n", "牛"):
            p["P_kN"] = val / 1000.0
        else:
            p["P_kN"] = val
    else:
        p["P_kN"] = 1000.0
        notes.append("未说明张拉力，默认 1000 kN")
    m = re.search(r"矢高\s*([0-9.]+)\s*(?:米|m)?", text)
    if m:
        sag = float(m.group(1))
        if re.search(r"向上|上缘", text):
            p["s"] = sag
        else:
            p["s"] = -sag
            if p.get("support") == "cantilever":
                notes.append("矢高向下（跨中筋更低），记为负值。两段悬臂的自由端下挠。")
            else:
                notes.append("矢高向下（跨中筋更低），记为负值，产生上拱")
    elif re.search(r"直线", text):
        p["s"] = 0.0
    else:
        p["s"] = -0.3
        notes.append("未说明矢高，按曲线束默认 0.3 m 向下")
    m = re.search(r"偏心\s*([0-9.]+)\s*(?:米|m)?", text)
    if m:
        ecc = float(m.group(1))
        p["e"] = -ecc if re.search(r"下缘", text) else ecc
    else:
        p["e"] = 0.0
    p["loss"] = 1.0
    m_ap = re.search(
        r"(?:钢束面积|束面积|预应力面积)\s*([0-9.]+)\s*"
        r"(mm²|mm2|平方毫米|cm²|cm2|平方厘米|m²|m2|平方米)?",
        text, re.I)
    if m_ap:
        aval = float(m_ap.group(1))
        unit = (m_ap.group(2) or "").lower().replace("²", "2")
        if unit in ("m2", "平方米"):
            p["Ap_mm2"] = aval * 1e6
        elif unit in ("cm2", "平方厘米"):
            p["Ap_mm2"] = aval * 100.0
        elif unit in ("mm2", "平方毫米"):
            p["Ap_mm2"] = aval
        elif aval >= 1.0:
            p["Ap_mm2"] = aval
            notes.append(f"钢束面积未写单位，{aval:g} 按 mm² 计")
        else:
            p["Ap_mm2"] = aval * 1e6
            notes.append(f"钢束面积 {aval:g} 小于 1，按 m² 换算成 {p['Ap_mm2']:g} mm²")
    else:
        p["Ap_mm2"] = 0.0
    m_fpk = re.search(
        r"(?:fpk|抗拉强度标准值)\s*([0-9.]+)\s*(?:MPa|兆帕)?", text, re.I)
    if m_fpk:
        p["fpk_MPa"] = float(m_fpk.group(1))
    else:
        p["fpk_MPa"] = 1860.0
        if p["Ap_mm2"] > 0:
            notes.append("钢绞线强度未说明，张拉控制应力上限按 1860 MPa 的 0.75 倍")
    if re.search(r"公路桥涵车道|JTG\s*D60|规范车道", text, re.I):
        p["lane_code"] = "JTG_D60"
        notes.append(
            "车道按 JTG D60-2015：qk=10.5 kN/m，Pk 在 5 m 与 50 m 之间线性内插。")
    else:
        p["lane_code"] = ""
    m_lane = re.search(
        r"车道(?:均布|荷载|活载)?\s*([0-9.]+)\s*"
        r"(?:kN/m|kn/m|千牛每米|千牛/米)?",
        text, re.I)
    if m_lane:
        p["lane_q_kNpm"] = float(m_lane.group(1))
        notes.append(
            f"车道均布 {p['lane_q_kNpm']:g} kN/m，向下。"
            "活载弯矩按影响线单独包络，不加进预应力位移。")
    else:
        p["lane_q_kNpm"] = 0.0
    m_mu = re.search(r"(?:汽车)?冲击系数\s*([0-9.]+)", text)
    if m_mu:
        raw_mu = float(m_mu.group(1))
        if raw_mu > 1.0:
            p["lane_impact"] = raw_mu
            notes.append(f"冲击系数 {raw_mu:g} 大于 1，按放大倍数 1+μ 使用。")
        else:
            p["lane_impact"] = 1.0 + raw_mu
            notes.append(
                f"冲击系数 μ={raw_mu:g}，车道荷载乘 {p['lane_impact']:g}。")
    else:
        p["lane_impact"] = 1.0
    if (p["lane_code"] == "JTG_D60" or p["lane_q_kNpm"]) and p["lane_impact"] == 1.0:
        if not re.search(r"密度\s*[0-9.]+", text):
            p["dens"] = 2500.0
            notes.append("未写密度，按 2500 kg/m³ 计算竖向基频。")
        notes.append("未写冲击系数，μ 按 JTG D60 由竖向基频自动计算。")
    p["n_seg"] = 2
    p["E"] = 2.0e11
    p["A"] = 0.01
    p["I"] = 0.01
    dphis = [float(x) for x in re.findall(r"徐变增量\s*([0-9.]+)", text)]
    p["creep_dphis"] = dphis
    p["creep_dphi"] = dphis[0] if dphis else 0.0
    if dphis:
        where = "自由端挠度" if p.get("support") == "cantilever" else "上拱"
        factor = 1.0 + sum(dphis)
        seq = "、".join(f"{v:g}" for v in dphis)
        notes.append(
            f"先按弹性模量张拉，再依次施加徐变增量 Δφ={seq}。"
            f"静定梁最终{where}约为瞬时值的 {factor:g} 倍。"
            "后一次增量仍按张拉完成时的内力计算，不把上一次的徐变位移再当成应力。"
            "轴力和弯矩保持张拉完成时的值，不再把整段加载乘 1+χφ。")
    m_phi = re.search(r"徐变系数\s*([0-9.]+)", text)
    m_rh = re.search(r"相对湿度\s*([0-9.]+)\s*%?", text)
    m_h = re.search(r"理论厚度\s*([0-9.]+)\s*(毫米|mm|米|m)?", text, re.I)
    m_grade = re.search(r"混凝土\s*C\s*([0-9.]+)", text, re.I)
    m_fcm = re.search(r"(?:圆柱体强度|fcm)\s*([0-9.]+)", text, re.I)
    m_t = re.search(r"(?:计算龄期|目标龄期)\s*([0-9.]+)\s*(?:天|日)?", text)
    if not m_t:
        m_t = re.search(r"(?<!加载)(?<!张拉)龄期\s*([0-9.]+)\s*(?:天|日)?", text)
    m_t0 = re.search(r"(?:加载龄期|张拉龄期)\s*([0-9.]+)\s*(?:天|日)?", text)
    if dphis:
        p["creep_phi"] = 0.0
    elif m_phi:
        p["creep_phi"] = float(m_phi.group(1))
        notes.append(
            f"徐变按全模型一个系数 φ={p['creep_phi']:g}，老化系数 χ 默认 0.8。"
            "刚度折成 E/(1+χφ)，位移约放大到 1+χφ 倍。"
            "这不是按应力历史逐阶段累加的徐变。")
        if m_rh and m_h:
            notes.append("写了徐变系数，不再改用湿度、理论厚度的规范曲线。")
    elif m_rh and m_h:
        rh = float(m_rh.group(1))
        h = float(m_h.group(1))
        unit = (m_h.group(2) or "").lower()
        if unit in ("米", "m") or (not unit and h < 5.0):
            h *= 1000.0
            notes.append(f"理论厚度按米换算为 {h:g} mm。")
        if m_fcm:
            fcm = float(m_fcm.group(1))
        elif m_grade:
            fcu = float(m_grade.group(1))
            fcm = 0.8 * fcu + 8.0
            notes.append(f"混凝土 C{fcu:g} 的圆柱体平均强度取 fcm=0.8fcu,k+8={fcm:g} MPa。")
        else:
            fcm = 40.0
            notes.append("未写混凝土强度，圆柱体平均强度按 C40 的 fcm=40 MPa。")
        t0 = float(m_t0.group(1)) if m_t0 else 7.0
        phi0, beta_h = jtg3362_nominal_creep(rh, h, fcm, t0)
        p["creep_phi"] = 0.0
        p["creep_rh"] = rh
        p["creep_h"] = h
        p["creep_fcm"] = fcm
        p["creep_t0"] = t0
        if m_t:
            p["creep_t_end"] = float(m_t.group(1))
        ts = _drying_start_days(text)
        if ts is not None:
            p["shrinkage_ts"] = ts
        notes.append(
            f"徐变按 JTG 3362 附录 C.2.1：相对湿度 {rh:g}%、理论厚度 {h:g} mm、"
            f"fcm={fcm:g} MPa、加载龄期 {t0:g} 天，名义系数 φ0={phi0:.3f}，βH={beta_h:.1f}。"
            "这不是 φ0=2、βH=700 的简化曲线。阶段求解用相邻龄期的差值。"
            + (f"干燥从第 {ts:g} 天起，参与受力之前的收缩不进入平衡。"
               if ts is not None else "没写收缩开始龄期，干燥从加载龄期起算。"))
    elif m_t:
        t0 = float(m_t0.group(1)) if m_t0 else 7.0
        p["creep_phi"] = creep_phi_from_age(float(m_t.group(1)), t0)
        notes.append(
            f"由计算龄期 {float(m_t.group(1)):g} 天、加载龄期 {t0:g} 天，"
            f"按简化曲线得到 φ={p['creep_phi']:.3f}（φ0=2，βH=700，指数 0.3）。"
            "再按 1+χφ 放大位移。这不是逐阶段应力历史徐变。")
    else:
        p["creep_phi"] = 0.0
    m = re.search(r"老化系数\s*([0-9.]+)", text)
    p["creep_chi"] = float(m.group(1)) if m else 0.8
    m = re.search(r"收缩应变\s*([0-9.]+(?:e-?[0-9]+)?)", text, re.I)
    if m:
        p["shrinkage_ecs"] = float(m.group(1))
        notes.append(
            f"自由收缩应变 {p['shrinkage_ecs']:g}，正值表示缩短。"
            "只在单元进入模型时施加一次，轴向缩短约 ε·L，不随徐变系数或徐变增量再乘。")
    else:
        p["shrinkage_ecs"] = 0.0
    notes.append("截面暂用基准梁 E=200 GPa、I=0.01 m⁴，便于和 −Pe·s·L²/(12EI) 对拍")
    return p, notes


def _note_cable_planes(text: str, p: dict[str, Any], notes: list[str]) -> None:
    """双索面不是维度开关。当前建造器只有一个索面，写了就拒绝求解。"""
    if _DUAL_PLANE_RE.search(text):
        p["cable_planes"] = 2
        notes.append("描述写了双索面。当前建造器只生成单索面，不会生成第二索面和横梁。"
                     "请改成「单索面」后再计算。")
    else:
        p["cable_planes"] = 1


def _note_cable_system(text: str, p: dict[str, Any], notes: list[str],
                       *, is_2d: bool) -> None:
    """悬臂施工与成桥必须是同一套约束。合拢后不能改成半漂浮。"""
    wants_float = bool(_FLOATING_RE.search(text))
    wants_fixed = bool(_FIXED_RE.search(text))
    if is_2d:
        p["system"] = "fixed"
        if p.get("staged") and wants_float:
            p["system_conflict"] = True
            notes.append("二维平面模型按塔梁固结建立，不能在悬臂施工之后改成半漂浮。")
        elif wants_float:
            notes.append("二维平面斜拉桥按塔梁固结建立，不按半漂浮。"
                         "半漂浮成桥请使用三维模型，并且不要同时写悬臂施工。")
        return
    if p.get("staged") and wants_float:
        p["system"] = "fixed"
        p["system_conflict"] = True
        notes.append("悬臂施工与半漂浮成桥不能写在同一次计算里。"
                     "内核不能在合拢后解除塔梁临时固结。")
    elif p.get("staged") or wants_fixed:
        p["system"] = "fixed"
        notes.append("结构体系按塔梁固结。悬臂施工与成桥使用同一套约束，合拢后不解除。")
    else:
        p["system"] = "semi"
        if wants_float:
            notes.append("结构体系按半漂浮成桥，不进入悬臂施工阶段。")


def _drying_start_days(text: str) -> float | None:
    """收缩开始龄期。没写就返回 None，干燥从加载龄期起算。"""
    m = re.search(r"(?:收缩开始龄期|干燥开始龄期|养护结束)\s*([0-9.]+)\s*(?:天|日)?", text)
    return float(m.group(1)) if m else None


def _note_cantilever_schedule(text: str, p: dict[str, Any], notes: list[str]) -> None:
    """句子里的加载龄期和每段工期。没写就保持 7 天，不在这里再声明一遍默认值。"""
    m_age = re.search(r"(?:加载龄期|张拉龄期)\s*([0-9.]+)\s*(?:天|日)?", text)
    m_cycle = re.search(
        r"(?:每段工期|节段周期|施工周期|悬臂周期|每段)\s*([0-9.]+)\s*(?:天|日)", text)
    if not m_age and not m_cycle:
        return
    age = float(m_age.group(1)) if m_age else 7.0
    cycle = float(m_cycle.group(1)) if m_cycle else 7.0
    if m_age:
        p["load_age_days"] = age
    if m_cycle:
        p["cycle_days"] = cycle
    notes.append(
        f"悬臂第一段末龄期 {age:g} 天，之后每段加 {cycle:g} 天，含合拢和二期。"
        "这是句子里的工期，不是现场进度。合拢后不解除塔梁固结。")


def _note_cantilever_creep(text: str, p: dict[str, Any], notes: list[str]) -> None:
    """悬臂斜拉没写湿度和理论厚度时保持 φ0=2。写齐了、又没写徐变系数，才改规范曲线。"""
    m_rh = re.search(r"相对湿度\s*([0-9.]+)\s*%?", text)
    m_h = re.search(r"理论厚度\s*([0-9.]+)\s*(毫米|mm|米|m)?", text, re.I)
    if re.search(r"徐变系数", text):
        if m_rh and m_h:
            notes.append("写了徐变系数，悬臂施工仍用 φ0=2、βH=700，不改用湿度曲线。")
        return
    if not m_rh:
        if _drying_start_days(text) is not None:
            notes.append("写了收缩开始龄期，但没有湿度，不按规范收缩曲线计算。")
        return
    rh = float(m_rh.group(1))
    h = 0.0
    if m_h:
        h = float(m_h.group(1))
        unit = (m_h.group(2) or "").lower()
        if unit in ("米", "m") or (not unit and h < 5.0):
            h *= 1000.0
            notes.append(f"理论厚度按米换算为 {h:g} mm。")
    m_grade = re.search(r"混凝土\s*C\s*([0-9.]+)", text, re.I)
    m_fcm = re.search(r"(?:圆柱体强度|fcm)\s*([0-9.]+)", text, re.I)
    if m_fcm:
        fcm = float(m_fcm.group(1))
    elif m_grade:
        fcu = float(m_grade.group(1))
        fcm = 0.8 * fcu + 8.0
        notes.append(f"混凝土 C{fcu:g} 的圆柱体平均强度取 fcm=0.8fcu,k+8={fcm:g} MPa。")
    else:
        fcm = 0.8 * 50.0 + 8.0
        notes.append("未写混凝土强度，主梁按 C50，圆柱体平均强度 fcm=48 MPa。")
    t0 = float(p.get("load_age_days") or 7.0)
    p["creep_rh"] = rh
    p["creep_h"] = h
    p["creep_fcm"] = fcm
    ts = _drying_start_days(text)
    if ts is not None:
        p["shrinkage_ts"] = ts
    if h > 0.0:
        phi0, beta_h = jtg3362_nominal_creep(rh, h, fcm, t0)
        thick = (f"理论厚度 {h:g} mm，名义系数 φ0={phi0:.3f}，βH={beta_h:.1f}。")
    else:
        thick = ("未写统一理论厚度。主梁按闭合外轮廓、桥塔按实心周长，分别取 h=2A/u。"
                 "拉索不取。")
    notes.append(
        f"悬臂徐变按 JTG 3362 附录 C.2.1：相对湿度 {rh:g}%，{thick}"
        f"fcm={fcm:g} MPa、加载龄期 {t0:g} 天。"
        "这不是 φ0=2、βH=700。合拢后的塔梁固结不因这条曲线而解除。"
        + (f"干燥从第 {ts:g} 天起，参与受力之前的收缩不进入平衡。"
           if ts is not None else ""))


def extract_3d_params(text: str, kind: str) -> tuple[dict[str, Any], list[str]]:
    """三维（及 2D 索承体系）的规则解析：只抽参数、不建造。

    与 `bridge_spec.extract_bridge_params` 同一套做法：抓不到的用工程默认值，
    并在 `notes` 里逐条列出"替你默认了什么"。三维参数名与二维不同
    （sp1/Lm/Ht 而不是 span/rise），所以不能复用那张正则表。

    档三起也承接 `cable_stayed_2d` / `suspension_2d`：它们与三维共用同一套
    工程参数（Lm/Ls/Ht/sag/A…），只少了 method/fpu/erection/lane_count 等
    三维项。用 `base`（去掉 `_2d` 后缀）做分支分发，默认值读 `d = DEFAULTS[kind]`。

    一个刻意的取舍：**不做"两者择一"的猜测**。斜拉桥说"跨度 200 米"，
    按主跨理解是对的；说"边跨 80、中跨 200" 也能对上。但如果一句话同时
    出现"主跨 200 边跨 80"，正则各取各的，不需要判断谁是谁。
    """
    from bridge_spec import _grab
    from units_guard import guard_bridge_span, guard_units

    base = kind.replace("_2d", "")          # cable_stayed_2d→cable_stayed 等
    is_2d = kind.endswith("_2d")
    d = DEFAULTS[kind]
    p: dict[str, Any] = {}
    notes: list[str] = []

    def grab(key: str, *names: str) -> bool:
        v = _grab(text, *names)
        if v is None:
            return False
        p[key] = v
        return True

    def default(key: str, label: str) -> None:
        notes.append(f"未说明{label}，默认 {d[key]:g}")

    if base == "cable_stayed":
        _note_cable_planes(text, p, notes)
        if not grab("Lm", "主跨", "跨径", "主孔"):
            if grab("Lm", "跨度", "总长", "全长"):
                notes.append(f"只给了一个跨度，按主跨 {p['Lm']:g} m 处理")
            else:
                p["Lm"] = d["Lm"]
                default("Lm", "主跨")
        # 单位哨兵必须跑在**派生默认值之前**：否则先用 200000 推出"塔高 60000 m"，
        # 再回头把主跨改成 200 m，提示里就会出现"边跨按 0.4×主跨 = 80000 m"
        # 紧接着"边跨 80000 疑为 mm，已按 80 m 处理"这种自相矛盾的两行。
        p["Lm"] = guard_bridge_span(float(p["Lm"]), notes)
        if not grab("Ls", "边跨", "边孔"):
            p["Ls"] = round(float(p["Lm"]) * 0.4, 1)
            notes.append(f"未说明边跨，按经验取 0.4×主跨 = {p['Ls']:g} m")
        if not grab("Ht", "塔高", "索塔高", "塔柱高"):
            p["Ht"] = round(float(p["Lm"]) * 0.3, 1)
            notes.append(f"未说明塔高，按 0.3×主跨 = {p['Ht']:g} m")
        m = re.search(r"每塔每侧\s*(\d+)|(\d+)\s*对索|(\d+)\s*根索", text)
        if m:
            p["n"] = int(next(g for g in m.groups() if g))
        else:
            p["n"] = d["n"]
            default("n", "每塔每侧索数")
        if not grab("h", "主梁高", "梁高"):
            p["h"] = round(float(p["Lm"]) / 80.0, 2)
            notes.append(f"未说明主梁高，按 L/80 = {p['h']:g} m")
        if not grab("B", "主梁宽", "桥宽", "箱宽"):
            p["B"] = d["B"]
            default("B", "主梁宽")
        if not grab("q", "恒载", "桥面荷载", "均布荷载"):
            p["q"] = d["q"]
            default("q", "桥面恒载")
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:mm2|mm²|平方毫米)", text)
        if m:
            p["A"] = float(m.group(1))
        else:
            p["A"] = d["A"]
            default("A", "单索面积")
        # 悬臂施工 + 张拉：识别施工关键词。悬索桥的施工叫"分级找形"，斜拉桥
        # 的施工叫"悬臂施工 + 逐段张拉"，两条路在建造器里是两套 stage 序列。
        if re.search(r"悬臂施工|悬臂|悬拼|挂篮|逐段|逐节|分段施工|施工阶段"
                     r"|平衡悬臂|悬浇|从塔|向两侧", text):
            p["staged"] = True
            notes.append("识别到悬臂施工，生成「桥塔+0号块 → 悬臂逐段张拉 → 合拢 "
                         "→ 二期铺装" + ("" if is_2d else "+二次调索") + "」阶段序列")
        else:
            p["staged"] = d["staged"]
        if (not is_2d) and re.search(r"非线性施工|几何非线性施工", text):
            p["staged"] = True
            p["nonlinear_stages"] = True
            notes.append("识别到非线性施工：悬臂阶段改用共转全量平衡，"
                         "索力由无应力索长命中。")
        elif ((not is_2d) and p.get("staged")
              and float(p.get("Lm") or 0.0) >= CABLE_STAYED_LARGE_SPAN_M):
            if re.search(r"线性施工|线性阶段", text):
                p["nonlinear_stages"] = False
                notes.append(
                    f"主跨达到 {CABLE_STAYED_LARGE_SPAN_M:g} m，但写明了线性施工，"
                    "仍走线性阶段。")
            else:
                p["nonlinear_stages"] = True
                notes.append(
                    f"主跨达到 {CABLE_STAYED_LARGE_SPAN_M:g} m，悬臂施工改用非线性阶段。"
                    f"短于 {CABLE_STAYED_LARGE_SPAN_M:g} m 仍走线性阶段；"
                    "写「线性施工」可以强制保持线性。")
        _note_cable_system(text, p, notes, is_2d=is_2d)
        if p.get("staged"):
            _note_cantilever_schedule(text, p, notes)
            _note_cantilever_creep(text, p, notes)
            m_end = re.search(r"(?:计算龄期|目标龄期)\s*([0-9.]+)\s*(?:天|日)?", text)
            if is_2d and m_end and float(p.get("creep_rh") or 0.0) > 0.0:
                p["creep_t_end"] = float(m_end.group(1))
                notes.append(
                    f"二维斜拉在施工阶段之后加一段到 {p['creep_t_end']:g} 天。"
                    "混凝土梁和桥塔按这条曲线徐变、收缩，拉索保持弹性。")
        # 二期恒载（铺装+护栏）：单独给出才拆分 q1=q−q2，否则二期=0 全算一期。
        m2 = re.search(r"二期(?:恒载|铺装|荷载|桥面)?\s*"
                       r"([0-9]+(?:\.[0-9]+)?)\s*(?:kN/m|kN|kn/m|KN/m)", text)
        if m2:
            p["q2_deck"] = float(m2.group(1))
            notes.append(f"二期恒载 {p['q2_deck']:g} kN/m（一期 = 桥面恒载 − 二期）")
        else:
            p["q2_deck"] = d["q2_deck"]
        p["retune"] = d.get("retune", True)
        if is_2d and re.search(r"车道|活载|影响线|汽车荷载|JTG\s*D60", text, re.I):
            p["lane"] = True
            notes.append(
                "2D 斜拉活载按线性影响线最不利布载。索按线弹性杆，"
                "不是成桥切线刚度，不含 Ernst 与几何刚度。")
        elif is_2d:
            p["lane"] = bool(d.get("lane", False))
    elif base == "suspension":
        _note_cable_planes(text, p, notes)
        # 体系：只有两个选项，关键词足够，不必等 LLM。同时接受"锚在梁上"
        # 这类说法——用户描述体系时往往不说"自锚"三个字。
        if re.search(r"自锚|锚在梁|锚于梁|锚在加劲梁", text):
            p["system"] = "SELF"
        elif re.search(r"地锚|锚碇|隧道锚|重力锚", text):
            p["system"] = "EARTH"
        else:
            p["system"] = d["system"]
            notes.append(f"未说明体系，默认{'地锚式' if d['system'] == 'EARTH' else '自锚式'}"
                         "（自锚式请写明「自锚式」）")
        if not grab("Lm", "主跨", "跨径", "主孔"):
            if grab("Lm", "跨度", "总长", "全长"):
                notes.append(f"只给了一个跨度，按主跨 {p['Lm']:g} m 处理")
            else:
                p["Lm"] = d["Lm"]
                default("Lm", "主跨")
        p["Lm"] = guard_bridge_span(float(p["Lm"]), notes)
        # 边跨：**不给默认值**，交给建造器按体系算（自锚式取平衡边跨）。
        # 在这里随手写个 0.4L，自锚式的梁端就会被主缆拔起来。
        if not grab("Ls", "边跨", "边孔"):
            p["Ls"] = 0.0
            notes.append("未说明边跨，按体系自动取：自锚式取平衡边跨 √(2HΔz/q)"
                         "（锚点主缆水平），地锚式取 1/3 主跨")
        else:
            L_bal = 0.0
            if p.get("Lm"):
                L_bal = float(p["Lm"]) / 3.0
            if p.get("system") == "SELF" and L_bal and p["Ls"] < 0.8 * L_bal:
                notes.append(f"自锚式边跨 {p['Ls']:g} m 明显短于平衡边跨附近值，"
                             "梁端可能出现向上的支反力（需抗拔支座或压重）")
        # 矢跨比：接受「1/10」「矢跨比 0.1」「矢高 30 米」三种说法
        m = re.search(r"矢跨比\s*1\s*[/:：]\s*(\d+(?:\.\d+)?)", text)
        if m:
            p["sag"] = float(m.group(1))
        else:
            m = re.search(r"矢跨比\s*([0-9.]+)", text)
            if m and float(m.group(1)) < 1.0:
                p["sag"] = round(1.0 / max(float(m.group(1)), 1e-6), 1)
            else:
                m = re.search(r"矢高\s*([0-9.]+)\s*(?:m|米)", text)
                if m and p.get("Lm"):
                    p["sag"] = round(float(p["Lm"]) / max(float(m.group(1)), 1e-6), 1)
                else:
                    p["sag"] = d["sag"]
                    default("sag", "矢跨比 1/n")
        if not grab("h", "主梁高", "加劲梁高", "梁高"):
            p["h"] = max(2.0, round(float(p["Lm"]) / 100.0, 2))
            notes.append(f"未说明加劲梁高，按 L/100 = {p['h']:g} m")
        for key, names, label in (
            ("B", ("主梁宽", "桥宽", "加劲梁宽"), "加劲梁宽"),
            ("q", ("恒载", "桥面荷载", "均布荷载"), "桥面恒载"),
            ("panel", ("吊索间距", "吊索间距离"), "吊索间距"),
        ):
            if not grab(key, *names):
                p[key] = d[key]
                default(key, label)
        # 缆索面积单位是 mm²，不是长度——不能走 _grab 的 _to_m 换算，否则
        # 「主缆面积 280000 mm²」会被除以 1000 变成 280 m²。
        def _area(*names: str) -> float | None:
            for nm in names:
                m = re.search(nm + r"[\s:：为约]*?([0-9]+(?:\.[0-9]+)?)"
                              r"\s*(?:mm2|mm²|平方毫米|mm)", text, re.I)
                if m:
                    return float(m.group(1))
            return None

        for key, names, label in (
            ("A", ("主缆面积",), "单根主缆面积"),
            ("Ah", ("吊索面积",), "单根吊索面积"),
        ):
            v = _area(*names)
            if v is None:
                p[key] = d[key]
                default(key, label)
            else:
                p[key] = v
        if not is_2d:
            # 二期铺装：与一期拆分（一期 = q - q2），供施工阶段分级施加。
            m = re.search(r"二期(?:恒载|铺装|荷载|自重)?[\s:：为约]*"
                          r"([0-9]+(?:\.[0-9]+)?)\s*(?:kN/m|kn/m|kN·m-1|千牛/米)",
                          text, re.I)
            if m:
                p["q2_deck"] = float(m.group(1))
            else:
                p["q2_deck"] = d["q2_deck"]
            # 车道数：多车道横向折减系数按 JTG D60 表 4.3.1-5。
            m = re.search(r"(?:双向|单向)?[\s]*([一二三四五六七八1-8])\s*车道", text)
            if m:
                cn = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
                      "七": 7, "八": 8}.get(m.group(1))
                n_lane = cn if cn is not None else int(m.group(1))
                p["lane_count"] = n_lane
                p["lane_reduction"] = _LANE_REDUCTION.get(n_lane, 0.78)
            else:
                p["lane_count"] = d["lane_count"]
                p["lane_reduction"] = d["lane_reduction"]
                default("lane_count", "车道数")
            # 施工阶段：出现吊装/合拢/施工等过程描述才生成
            # （空缆 → 从跨中对称吊装 → 合拢 → 铺装）。
            if re.search(r"施工阶段|施工过程|吊装|合拢|对称吊装|从跨中|从中间", text):
                p["erection"] = True
                m = re.search(r"每次(?:对称)?吊装\s*(\d+)\s*(?:对|段|个)?", text)
                if m:
                    p["erection_panels"] = int(m.group(1))
                else:
                    p["erection_panels"] = d["erection_panels"]
            else:
                p["erection"] = d["erection"]
                p["erection_panels"] = d["erection_panels"]
        else:
            # 2D 平面悬索只做成桥态一次静力：无二期拆分 / 车道数 / 施工阶段。
            # 描述里若提了这些，如实提示"2D 简化暂不承载"，不静默忽略。
            if re.search(r"二期|铺装", text):
                notes.append("2D 悬索桥只做成桥态一次静力，二期铺装已并入桥面恒载 q")
            if re.search(r"车道|活载|影响线|汽车荷载|JTG\s*D60", text, re.I):
                p["lane"] = True
                notes.append(
                    "2D 悬索活载按线性影响线最不利布载。主缆和吊索按线弹性杆，"
                    "不是成桥切线刚度，不含 Ernst 与几何刚度。")
            if re.search(r"施工|吊装|合拢", text):
                notes.append("2D 悬索桥暂不做施工阶段模拟（分级找形走三维 erection）")
    elif base == "arch_3d":
        # 主跨：与斜拉/悬索同一套「先主跨后跨度」的优先级。
        if not grab("span", "主跨", "跨径", "主孔"):
            if not grab("span", "跨度", "总长", "全长"):
                # 「大跨度拱桥500米」里「跨度」被「拱桥」隔开，_grab 抓不到；
                # 单独抓紧贴「拱桥/拱」的裸数字。
                m = re.search(r"(?:拱桥|拱)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:米|m|M)", text)
                if m:
                    p["span"] = float(m.group(1))
                    notes.append(f"从「拱桥 {p['span']:g} 米」识别主跨")
                else:
                    p["span"] = d["span"]
                    default("span", "主跨")
            else:
                notes.append(f"只给了一个跨度，按主跨 {p['span']:g} m 处理")
        p["span"] = guard_bridge_span(float(p["span"]), notes)
        if not grab("rise", "矢高", "拱矢高"):
            p["rise"] = round(float(p["span"]) / 5.0, 1)
            notes.append(f"未说明矢高，按 L/5 = {p['rise']:g} m")
        # 半跨吊装段数：显式「半跨 8 段」「分 8 段」「8 段吊装」；缺省 8。
        m = re.search(r"半跨\s*(\d+)\s*段|拱肋\s*分\s*(\d+)\s*段|分\s*(\d+)\s*段\s*(?:吊装|拼装)", text)
        if m:
            p["n_seg_half"] = int(next(g for g in m.groups() if g))
        else:
            p["n_seg_half"] = d["n_seg_half"]
            default("n_seg_half", "半跨吊装段数")
        # 截面/荷载：拱肋、主梁截面与吊杆/扣索面积属"拟定合理参数"，
        # 大多走默认；只抽显式给出的桥宽与恒载。
        for key, names, label in (
            ("deck_b", ("桥宽", "主梁宽", "桥面宽"), "主梁宽"),
            ("q_deck", ("一期恒载", "桥面恒载", "恒载", "均布荷载"), "一期恒载"),
        ):
            if not grab(key, *names):
                p[key] = d[key]
                default(key, label)
        # 二期铺装
        m2 = re.search(r"二期(?:恒载|铺装|荷载|自重)?[\s:：为约]*"
                       r"([0-9]+(?:\.[0-9]+)?)\s*(?:kN/m|kn/m|kN·m-1|千牛/米)", text, re.I)
        if m2:
            p["q2_deck"] = float(m2.group(1))
        else:
            p["q2_deck"] = d["q2_deck"]
            default("q2_deck", "二期恒载")
        # 车道数 → 桥宽（6 车道 → 24 m 桥面；给出桥宽时以桥宽为准，不覆盖）
        m3 = re.search(r"([一二三四五六七八1-8])\s*车道", text)
        if m3 and "deck_b" not in p:
            cn = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
                  "七": 7, "八": 8}.get(m3.group(1))
            n_lane = cn if cn is not None else int(m3.group(1))
            p["deck_b"] = round(3.75 * n_lane + 3.0, 1)
            notes.append(f"{n_lane} 车道按 3.75 m/车道 + 3 m 检修道取桥宽 "
                         f"{p['deck_b']:g} m")
        # 施工关键词已由 classify_text 判到 arch_3d，这里把施工顺序写清楚。
        notes.append("识别到拱肋分段吊装 + 斜拉扣索辅助，生成「拱脚段 → 对称吊装"
                     " + 扣索张拉 → 拱顶合拢 → 拆扣索 → 吊杆主梁 → 二期铺装」"
                     "阶段序列")
    elif base == "truss_3d":
        # 主跨：与拱桥同一套「先主跨后跨度」的优先级。
        if not grab("span", "主跨", "跨径", "主孔"):
            if not grab("span", "跨度", "总长", "全长"):
                p["span"] = d["span"]
                default("span", "跨度")
            else:
                notes.append(f"只给了一个跨度，按 {p['span']:g} m 处理")
        p["span"] = guard_bridge_span(float(p["span"]), notes)
        # 桁高：空间桁架的受力与挠度都靠它，缺省取 L/10（Warren 桁常规高跨比）。
        if not grab("depth", "桁高", "桁架高", "架高", "高度"):
            p["depth"] = round(float(p["span"]) / 10.0, 1)
            notes.append(f"未说明桁高，按 L/10 = {p['depth']:g} m")
        # 桥宽（两片主桁的间距）：缺省取跨度的 1/6，不小于 4 m。
        if not grab("width", "桥宽", "桁架间距", "两片桁架间距", "宽度"):
            p["width"] = max(4.0, round(float(p["span"]) / 6.0, 1))
            notes.append(f"未说明桥宽，按 L/6 = {p['width']:g} m")
        # 节间数：显式「8 个节间 / 分 8 节间 / 节间数 8」；缺省 8。
        m = re.search(r"(?:节间数|节间)\s*(?:为|是)?\s*(\d+)|(\d+)\s*个?(?:节间|格)", text)
        if m:
            p["n_panel"] = int(next(g for g in m.groups() if g))
        else:
            p["n_panel"] = d["n_panel"]
            default("n_panel", "节间数")
        if not grab("q_deck", "桥面恒载", "恒载", "均布荷载", "桥面荷载"):
            p["q_deck"] = d["q_deck"]
            default("q_deck", "桥面均布恒载")
        # 弦/腹/横联截面面积属"拟定合理参数"，大多走默认；只抽显式 mm²。
        def _area_mm2(*names: str) -> float | None:
            for nm in names:
                mm = re.search(nm + r"[\s:：为约]*?([0-9]+(?:\.[0-9]+)?)"
                               r"\s*(?:mm2|mm²|平方毫米|mm)", text, re.I)
                if mm:
                    return float(mm.group(1))
            return None

        for key, names, label in (
            ("A_chord", ("弦杆面积", "弦杆截面积"), "弦杆面积"),
            ("A_diag", ("腹杆面积", "腹杆截面积"), "腹杆面积"),
            ("A_lat", ("横向联系面积", "横联面积", "横向连接面积"), "横向联系面积"),
        ):
            v = _area_mm2(*names)
            if v is None:
                p[key] = d[key]
                default(key, label)
            else:
                p[key] = v
        notes.append("识别到空间桁架桥，生成两片竖向 Warren 桁架 + 上下弦横向联系"
                     "（横联 + 对角撑）的纯 TRUSS 三维模型，走线性阶段内核。")
    else:
        if not grab("sp1", "边跨"):
            p["sp1"] = d["sp1"]
            default("sp1", "边跨")
        if not grab("sp2", "中跨"):
            if grab("sp2", "跨度", "主跨"):
                notes.append(f"只给了一个跨度，按中跨 {p['sp2']:g} m 处理")
            else:
                p["sp2"] = d["sp2"]
                default("sp2", "中跨")
        p["sp2"] = guard_bridge_span(float(p["sp2"]), notes)   # 派生 h 之前先纠正
        if not grab("h", "梁高"):
            p["h"] = round(float(p["sp2"]) / 20.0, 2)
            notes.append(f"未说明梁高，按中跨 L/20 = {p['h']:g} m")
        for key, names, label in (
            ("B", ("箱宽", "桥宽"), "箱宽"),
            ("tf", ("顶板厚", "底板厚", "板厚"), "顶/底板厚"),
            ("tw", ("腹板厚",), "腹板厚"),
            ("q", ("均布荷载", "桥面荷载", "恒载"), "桥面均布荷载"),
            ("panel", ("横梁间距", "节段长"), "横梁间距"),
        ):
            if not grab(key, *names):
                p[key] = d[key]
                default(key, label)

    guard_units(p, base, notes)     # 单位哨兵用归一化键（2D 复用 3D 阈值表）
    return p, notes


def _supports_are_default(spec: ModelSpec) -> bool:
    """判断支座是否就是 ModelSpec 的默认布置（单跨/多跨等分铰支）。"""
    if spec.n_spans <= 1:
        want = [(0.0, "pinned"), (spec.length, "pinned")]
    else:
        want = [(spec.length * i / spec.n_spans, "pinned")
                for i in range(spec.n_spans + 1)]
    got = [(round(s.position, 6), s.type) for s in spec.supports]
    return len(got) == len(want) and all(
        abs(g[0] - w[0]) < 1e-6 and g[1] == w[1] for g, w in zip(got, want))


if __name__ == "__main__":            # 快速自检
    for txt in [
        "跨度30米的简支钢梁，矩形截面高1.2米宽0.6米，承受20kN/m均布荷载",
        "跨度100米的拱桥，矢高20米，9对吊杆，20kN/m均布荷载",
        "跨度60米桁架桥，8个节间，桁高6米",
        "三跨连续梁桥总长90米",
    ]:
        doc, src, dg = ModelDoc.from_text(txt)
        doc.validate()
        print(f"[{doc.kind}] {src} | {doc.summary()['lines'][0]}")
        print(f"        能力：{doc.capability()}")
