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

Kind = Literal["beam", "arch", "truss", "beam_bridge"]

KIND_LABELS: dict[str, str] = {
    "beam": "单梁",
    "arch": "拱桥",
    "truss": "桁架桥",
    "beam_bridge": "梁桥",
}

KIND_HINTS: dict[str, str] = {
    "单梁": "beam",
    "拱桥": "arch",
    "桁架桥": "truss",
    "梁桥": "beam_bridge",
}

# 材料名 → (E, ρ)
MATERIALS: dict[str, tuple[float, float]] = {
    "钢材 Q345": (2.06e11, 7850.0),
    "混凝土 C50": (3.45e10, 2500.0),
}
MATERIAL_NAMES = list(MATERIALS)

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
        [{"key": "self_weight", "label": "计入自重", "type": "bool"}],
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


def classify_text(text: str, hint: str | None = None) -> str:
    """判断一句话属于哪种结构。

    hint 来自界面的"结构类型"提示（"自动识别" 或具体桥型）；给了具体类型
    就完全跳过猜测——用户比自己写的分类器可靠。

    顺序有讲究：先「拱」再「桁架」再「桥」，最后才落到单梁。
    因为「拱桥」「桁架桥」都含「桥」，而「简支梁桥」含「梁」。
    """
    if hint and hint in KIND_HINTS:
        return KIND_HINTS[hint]
    t = text.strip()
    if re.search(r"拱|矢高|矢跨比", t):
        return "arch"
    if re.search(r"桁架|truss", t, re.I):
        return "truss"
    if "桥" in t:
        return "beam_bridge"
    return "beam"


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

    def __init__(self, kind: str, params: dict[str, Any] | None = None):
        if kind not in DEFAULTS:
            raise ValueError(f"未知结构类型：{kind}")
        self.kind: str = kind
        merged = json.loads(json.dumps(DEFAULTS[kind]))   # 深拷贝
        if params:
            merged.update(params)
        self.params: dict[str, Any] = merged
        self._model: Any = None

    # ------------------------------------------------------------ 构建

    def build(self) -> "ModelDoc":
        """params → 展开模型。幂等，可反复调用。"""
        if self.kind == "beam":
            self._model = self._build_beam()
        elif self.kind == "arch":
            self._model = self._build_arch()
        elif self.kind == "truss":
            self._model = self._build_truss()
        else:
            self._model = self._build_beam_bridge()
        return self

    @property
    def model(self) -> Any:
        if self._model is None:
            self.build()
        return self._model

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
        )

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
        else:
            errs = self._bridge_param_checks() + self._model.validate_model()
        return errs

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

        fem = fem_eigen_available()
        is_beam = self.kind == "beam"
        return {
            "anastruct": is_beam,          # anastruct 只有梁单元
            "fem_eigen": fem,
            "matplotlib": is_beam,         # 截面/变形/弯矩图只对梁画
            # 互校的前提：梁（两边都能算）且不计自重（anastruct 无重力项）
            "cross_check": is_beam and fem and not self.params.get("self_weight", False),
        }

    # ------------------------------------------------------------ 展示

    def summary(self) -> dict[str, Any]:
        """给界面渲染的摘要：标题 + 若干行文本。"""
        self.build()
        if self.kind == "beam":
            lines = self._model.summary().splitlines()
            return {"title": KIND_LABELS[self.kind], "lines": lines}
        spec = self._model
        lines = spec.summary().splitlines()
        return {"title": spec.title, "lines": lines}

    def param_rows(self) -> list[list[Field]]:
        """当前类型下要显示的控件行（已按 when 过滤）。"""
        rows = []
        for row in FIELDS[self.kind]:
            keep = [f for f in row if f.get("when") is None or f["when"](self.params)]
            if keep:
                rows.append(keep)
        return rows

    # ------------------------------------------------------------ 序列化

    def to_dict(self) -> dict[str, Any]:
        return {"version": 1, "kind": self.kind, "params": self.params}

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> "ModelDoc":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(kind=d["kind"], params=d.get("params", {}))

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
    ) -> tuple["ModelDoc", str, Diag]:
        """统一解析入口：先分类，再走对应管线。

        返回值与 `nl_parser.parse_nl` 同构（doc, source, diag），界面不必分叉。
        """
        if prev is not None and not isinstance(prev, ModelDoc):
            # 最常见的误用：把 LLMConfig 当成第二个位置参数传进来
            raise TypeError(
                "prev 必须是 ModelDoc 或 None；LLM 配置请用 llm_config= 传入")
        kind = classify_text(text, hint)

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
