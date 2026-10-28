"""梁截面参数化库：几何 → 截面属性 (A, I) 与二维截面示意图。

覆盖 4 种截面：矩形 rectangular / 工字形 i_beam / 箱形 box / 圆形 circular。
弯曲绕强轴（竖向变形），I 为对水平中心轴的惯性矩。
"""

from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Circle
from model_spec import Section


def section_properties(sec: Section) -> dict:
    """返回截面属性：面积 A(m^2)、惯性矩 I(m^4)、抗弯模量 W(m^3)。"""
    h, b = sec.h, sec.b
    if sec.shape == "rectangular":
        A = b * h
        I = b * h**3 / 12.0
    elif sec.shape == "circular":
        r = h / 2.0
        A = 3.141592653589793 * r * r
        I = 3.141592653589793 * r**4 / 4.0
    elif sec.shape == "i_beam":
        tw, tf = sec.tw or b * 0.08, sec.tf or h * 0.1
        hw = h - 2 * tf
        A = 2 * b * tf + tw * hw
        I = b * h**3 / 12.0 - (b - tw) * hw**3 / 12.0
    elif sec.shape == "box":
        tw, tf = sec.tw or b * 0.08, sec.tf or h * 0.1
        hw = h - 2 * tf
        bw = b - 2 * tw
        A = b * h - bw * hw
        I = b * h**3 / 12.0 - bw * hw**3 / 12.0
    else:
        A, I = 1.0, 1.0
    W = I / (h / 2.0) if h > 0 else 0.0
    return {"A": A, "I": I, "W": W}


def draw_section(sec: Section, ax=None) -> plt.Axes:
    """在给定 axes 上画截面示意图（竖向为 h，水平为 b）。"""
    if ax is None:
        fig, ax = plt.subplots(figsize=(3, 4))
    h, b = sec.h, sec.b
    # 坐标系：以截面中心为原点，x 水平, y 竖向
    if sec.shape == "rectangular":
        ax.add_patch(Polygon([(-b/2, -h/2), (b/2, -h/2), (b/2, h/2), (-b/2, h/2)],
                             closed=True, facecolor="#b5d4f4", edgecolor="#185fa5"))
    elif sec.shape == "circular":
        ax.add_patch(Circle((0, 0), h/2, facecolor="#b5d4f4", edgecolor="#185fa5"))
    elif sec.shape == "i_beam":
        tw, tf = sec.tw or b * 0.08, sec.tf or h * 0.1
        hw = h - 2 * tf
        # 上翼缘
        ax.add_patch(Polygon([(-b/2, h/2-tf), (b/2, h/2-tf), (b/2, h/2), (-b/2, h/2)],
                             closed=True, facecolor="#b5d4f4", edgecolor="#185fa5"))
        # 下翼缘
        ax.add_patch(Polygon([(-b/2, -h/2), (b/2, -h/2), (b/2, -h/2+tf), (-b/2, -h/2+tf)],
                             closed=True, facecolor="#b5d4f4", edgecolor="#185fa5"))
        # 腹板
        ax.add_patch(Polygon([(-tw/2, -hw/2), (tw/2, -hw/2), (tw/2, hw/2), (-tw/2, hw/2)],
                             closed=True, facecolor="#b5d4f4", edgecolor="#185fa5"))
    elif sec.shape == "box":
        tw, tf = sec.tw or b * 0.08, sec.tf or h * 0.1
        # 外框
        ax.add_patch(Polygon([(-b/2, -h/2), (b/2, -h/2), (b/2, h/2), (-b/2, h/2)],
                             closed=True, facecolor="#b5d4f4", edgecolor="#185fa5"))
        # 内孔（用背景色挖空）
        ax.add_patch(Polygon([(-b/2+tw, -h/2+tf), (b/2-tw, -h/2+tf),
                              (b/2-tw, h/2-tf), (-b/2+tw, h/2-tf)],
                             closed=True, facecolor="white", edgecolor="#185fa5"))
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_title(f"{sec.shape}  截面", fontsize=11)
    ax.set_xlabel("b (m)", fontsize=9)
    ax.set_ylabel("h (m)", fontsize=9)
    ax.grid(True, alpha=0.2)
    return ax
