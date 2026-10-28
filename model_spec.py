"""结构化有限元模型定义（单一数据源）。

所有自然语言解析、截面参数化、求解与可视化都围绕 `ModelSpec` 这个
Pydantic 模型展开。它是整个软件的"真相来源"（single source of truth）。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

# 截面形状
SectionShape = Literal["rectangular", "i_beam", "box", "circular"]
# 支座类型：pinned=铰支, fixed=固支, roller=滚动支座(仅竖向约束)
SupportType = Literal["pinned", "fixed", "roller"]
# 荷载类型：udl=均布荷载, point=集中力
LoadType = Literal["udl", "point"]


class Support(BaseModel):
    """支座：用沿梁轴线的坐标 position(m) 定位。"""

    position: float = Field(..., description="沿梁轴线坐标，单位 m")
    type: SupportType = "pinned"

    def to_zh(self) -> str:
        name = {"pinned": "铰支", "fixed": "固支", "roller": "滚动"}[self.type]
        return f"{self.position:g}m 处{name}座"


class Load(BaseModel):
    """荷载。udl 用 start/end 定义作用区间(m)；point 用 position 定位(m)。"""

    type: LoadType
    value: float = Field(..., description="udl: kN/m；point: kN")
    start: Optional[float] = None
    end: Optional[float] = None
    position: Optional[float] = None

    def to_zh(self) -> str:
        if self.type == "udl":
            s = f"{self.start:g}" if self.start is not None else "0"
            e = f"{self.end:g}" if self.end is not None else "L"
            return f"均布荷载 {self.value:g} kN/m（{s}~{e}m）"
        return f"集中力 {self.value:g} kN（{self.position:g}m 处）"


class Section(BaseModel):
    """梁截面。h=高度/直径(m)，b=宽度(m)；i_beam/box 用 tw,tf 定义腹板/翼缘厚(m)。"""

    shape: SectionShape = "rectangular"
    h: float = Field(..., description="截面高度(或圆截面直径)，单位 m")
    b: float = Field(..., description="截面宽度，单位 m")
    tw: Optional[float] = None  # 腹板厚
    tf: Optional[float] = None  # 翼缘厚

    @model_validator(mode="after")
    def _fill_thickness(self) -> "Section":
        if self.shape in ("i_beam", "box"):
            if self.tw is None:
                self.tw = max(0.01, self.b * 0.08)
            if self.tf is None:
                self.tf = max(0.01, self.h * 0.1)
        return self

    def to_zh(self) -> str:
        shape_name = {
            "rectangular": "矩形",
            "i_beam": "工字形",
            "box": "箱形",
            "circular": "圆形",
        }[self.shape]
        if self.shape == "circular":
            return f"{shape_name}截面，直径 {self.h*1000:g} mm"
        if self.shape in ("i_beam", "box"):
            return (
                f"{shape_name}截面 h={self.h*1000:g}mm, b={self.b*1000:g}mm, "
                f"tw={self.tw*1000:g}mm, tf={self.tf*1000:g}mm"
            )
        return f"{shape_name}截面 h={self.h*1000:g}mm, b={self.b*1000:g}mm"


class ModelSpec(BaseModel):
    """完整的有限元前处理模型描述。"""

    name: str = "未命名模型"
    length: float = Field(..., description="梁总长，单位 m", gt=0)
    n_spans: int = Field(1, ge=1, le=6)
    supports: list[Support] = Field(default_factory=list)
    loads: list[Load] = Field(default_factory=list)
    section: Section
    material_name: str = "钢材 Q345"
    E: float = Field(2.06e11, description="弹性模量 Pa")
    rho: float = Field(7850.0, description="密度 kg/m^3")

    @model_validator(mode="after")
    def _check_consistency(self) -> "ModelSpec":
        # 默认支座：单跨两端简支；多跨在等分点布置铰支（连续梁）
        if not self.supports:
            if self.n_spans <= 1:
                self.supports = [
                    Support(position=0.0, type="pinned"),
                    Support(position=self.length, type="pinned"),
                ]
            else:
                self.supports = [
                    Support(position=self.length * i / self.n_spans, type="pinned")
                    for i in range(self.n_spans + 1)
                ]
        # 校验荷载位置落在 [0, length] 内
        for ld in self.loads:
            if ld.type == "point" and ld.position is not None:
                ld.position = min(max(ld.position, 0.0), self.length)
        return self

    def summary(self) -> str:
        lines = [
            f"模型：{self.name} | 总长 {self.length:g} m | {self.n_spans} 跨",
            f"材料：{self.material_name} (E={self.E/1e9:g} GPa)",
            f"截面：{self.section.to_zh()}",
            "支座：" + "；".join(s.to_zh() for s in self.supports),
            "荷载：" + ("；".join(l.to_zh() for l in self.loads) or "无"),
        ]
        return "\n".join(lines)
