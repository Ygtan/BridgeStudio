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
#: 桥型 kind 只有 6 种；维度是正交的第二维。建造器键比 kind 多几个三维变体：
#: grillage3d（梁桥三维梁格）、arch_3d（拱桥三维施工）。
#: cable_stayed / suspension 的 2d 暂时回退到三维建造器，档三补 2d 简化后替换。
BUILDERS: dict[tuple[str, str], str] = {
    ("beam", "2d"): "beam",
    ("arch", "2d"): "arch",
    ("arch", "3d"): "arch_3d",
    ("truss", "2d"): "truss",
    ("beam_bridge", "2d"): "beam_bridge",
    ("beam_bridge", "3d"): "grillage3d",
    ("cable_stayed", "2d"): "cable_stayed",
    ("cable_stayed", "3d"): "cable_stayed",
    ("suspension", "2d"): "suspension",
    ("suspension", "3d"): "suspension",
}

#: (kind, dimension) -> 标称内核键。只给报告/能力自述用；真正求解走
#: `detect_kind(model)`（悬臂施工斜拉桥摘掉 cable_bridge 会落到 grillage3d）。
SOLVER_OF: dict[tuple[str, str], str] = {
    ("beam", "2d"): "2d",
    ("arch", "2d"): "2d",
    ("arch", "3d"): "grillage3d",
    ("truss", "2d"): "2d",
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

DEFAULTS: dict[str, dict[str, Any]] = {
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
    "beam_bridge": {
        "span": 40.0, "n_spans": 1, "udl": 20.0, "point": 0.0,
        "h": 2.0, "b": 8.0, "shape": "box",
        "material": "钢材 Q345", "self_weight": True,
        # 档二：2D 施工阶段 + 车道活载（走 solve_2d 链，进 example_stage/example_influence）
        "staged": False,          # 逐跨施工（每跨一个阶段 + 二期铺装阶段）
        "q2_kN": 0.0,             # 二期铺装 (kN/m)，staged 时合拢后全桥施加
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
    r"三维|空间|梁格|空间梁格|空间效应|空间刚架|偏载|扭转|横向分布|双索面|横梁|3d",
    re.I)


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
    # 斜拉桥/悬索桥：档三补 2d 简化建造器后改默认 2d；此前只有三维实现。
    if kind in ("cable_stayed", "suspension"):
        return "3d"
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
            # 斜拉/悬索桥档三前只有三维建造器，默认 3d；其余默认 2d。
            dimension = "3d" if kind in ("cable_stayed", "suspension") else "2d"
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
        elif bk == "grillage3d":
            self._model = self._build_grillage3d()
        elif bk == "cable_stayed":
            self._model = self._build_cable_stayed()
        elif bk == "suspension":
            self._model = self._build_suspension()
        elif bk == "arch_3d":
            self._model = self._build_arch_3d()
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
        """标称内核键。真正的内核由 `detect_kind(model)` 依模型内容定。"""
        return SOLVER_OF.get((self.kind, self.dimension), "2d")

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
            lane=bool(p.get("lane", False)),
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
            staged=bool(p.get("staged", False)),
            retune=bool(p.get("retune", True)),
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
        try:
            self.build()
        except Exception as e:                       # 参数缺失/类型错
            return [f"模型参数不完整或非法：{e}"]

        if self.kind == "beam":
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
                from solver_3d import capabilities

                ok3d = bool(capabilities().get(self.solver_kind))
            except Exception:
                ok3d = False
            return {
                "anastruct": False,
                "fem_eigen": ok3d,          # 三维内核是否可用
                "matplotlib": False,
                "cross_check": False,
                "solve3d": ok3d,
                "cable": self.kind == "cable_stayed",
                "nonlinear": self.kind == "cable_stayed",
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
            "cable": False,
            "nonlinear": False,
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


def extract_3d_params(text: str, kind: str) -> tuple[dict[str, Any], list[str]]:
    """三维结构的规则解析：只抽参数、不建造。

    与 `bridge_spec.extract_bridge_params` 同一套做法：抓不到的用工程默认值，
    并在 `notes` 里逐条列出"替你默认了什么"。三维参数名与二维不同
    （sp1/Lm/Ht 而不是 span/rise），所以不能复用那张正则表。

    一个刻意的取舍：**不做"两者择一"的猜测**。斜拉桥说"跨度 200 米"，
    按主跨理解是对的；说"边跨 80、中跨 200" 也能对上。但如果一句话同时
    出现"主跨 200 边跨 80"，正则各取各的，不需要判断谁是谁。
    """
    from bridge_spec import _grab
    from units_guard import guard_bridge_span, guard_units

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

    if kind == "cable_stayed":
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
                         "→ 二期铺装+二次调索」阶段序列")
        else:
            p["staged"] = d["staged"]
        # 二期恒载（铺装+护栏）：单独给出才拆分 q1=q−q2，否则二期=0 全算一期。
        m2 = re.search(r"二期(?:恒载|铺装|荷载|桥面)?\s*"
                       r"([0-9]+(?:\.[0-9]+)?)\s*(?:kN/m|kN|kn/m|KN/m)", text)
        if m2:
            p["q2_deck"] = float(m2.group(1))
            notes.append(f"二期恒载 {p['q2_deck']:g} kN/m（一期 = 桥面恒载 − 二期）")
        else:
            p["q2_deck"] = d["q2_deck"]
        p["retune"] = d["retune"]
    elif kind == "suspension":
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
    elif kind == "arch_3d":
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

    guard_units(p, kind, notes)     # 单位误写在这一层就纠正
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
