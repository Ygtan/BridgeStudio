"""受控技术句 → CaseSpec → ModelSpec3D（复用对拍算例的同源导出器）。

这是「自然语言 → dat/inp → FEM/ANSYS 对拍」闭环原型的解析层。

设计边界（如实声明，不得夸大）：
  * 这里解析的是**参数齐全的受控技术句**（见 desc_cb3d01.txt），
    每个字段都有明确中文标签，不是自由口语/通用工程语义理解。
  * 解析器不强依赖 LLM；纯规则、离线可跑、逐字段可审计。
  * 解析结果喂回 `ansys_bridge_3d.build_box_beam_3d`，因此生成的
    .dat/.inp 与冻结证据同源、可逐字节比对。

本项目真正的自然语言理解入口仍是 `model_doc.ModelDoc.from_text` /
`nl_parser`；本模块是「把对拍算例反向还原为可解析技术句」的桥，
不改动、不替代那两套解析链路。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import sys

_BRIDGESTUDIO = Path(__file__).resolve().parent.parent
if str(_BRIDGESTUDIO) not in sys.path:
    sys.path.insert(0, str(_BRIDGESTUDIO))

_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}


def _cn_int(s: str) -> int:
    s = s.strip()
    if not s:
        return 0
    if s in _CN_DIGITS:
        return _CN_DIGITS[s]
    total = 0
    cur = 0
    for ch in s:
        if ch in _CN_DIGITS:
            cur = _CN_DIGITS[ch]
        elif ch in _CN_UNIT:
            total += (cur or 1) * _CN_UNIT[ch]
            cur = 0
    return total + cur


def parse_number(s: str) -> float:
    """同时接受阿拉伯数字（含科学计数）与中文数字（含「点」小数）。"""
    s = s.strip()
    if re.fullmatch(r"[0-9.eE+\-]+", s):
        return float(s)
    if "点" in s:
        ip, fp = s.split("点", 1)
        frac = "".join(str(_CN_DIGITS[c]) for c in fp if c in _CN_DIGITS)
        return float(f"{_cn_int(ip)}.{frac}")
    return float(_cn_int(s))


@dataclass
class CaseSpec:
    case_id: str
    n_spans: int
    span_m: float
    n_elem_per_span: int
    B: float
    h: float
    tw: float
    tf: float
    E: float
    nu: float
    udl_kN: float
    title_cn: str
    title_en: str
    source_text: str = field(default="", repr=False)

    def build_kwargs(self) -> dict:
        return dict(
            spans=(self.span_m,) * self.n_spans,
            n_elem_per_span=self.n_elem_per_span,
            B=self.B, h=self.h, tw=self.tw, tf=self.tf,
            E=self.E, nu=self.nu, udl_kN=self.udl_kN,
            title=self.title_cn,
        )

    def build_model(self):
        from ansys_bridge_3d import build_box_beam_3d
        return build_box_beam_3d(**self.build_kwargs())


_NUM = r"([^，。、；\s]+?)"


def parse_case(text: str, case_id: str = "CB3D01") -> CaseSpec:
    """解析参数齐全技术句；缺字段立即报错，不静默填默认值。"""
    t = text.strip()
    pats = {
        "n_spans": re.compile(r"跨径布置" + _NUM + r"跨"),
        "span_m": re.compile(r"每跨" + _NUM + r"米"),
        "n_elem": re.compile(r"每跨划分" + _NUM + r"个"),
        "B": re.compile(r"宽" + _NUM + r"米"),
        "h": re.compile(r"高" + _NUM + r"米"),
        "tw": re.compile(r"腹板厚" + _NUM + r"米"),
        "tf": re.compile(r"翼缘厚" + _NUM + r"米"),
        "E": re.compile(r"弹性模量\s*([0-9.eE+\-]+)\s*帕"),
        "nu": re.compile(r"泊松比\s*([0-9.]+)"),
        "udl": re.compile(r"均布荷载\s*([0-9.]+)\s*千牛每米"),
        "title_cn": re.compile(r"中文标题[:：]\s*([^。\n]+)"),
        "title_en": re.compile(r"英文标题[:：]\s*([^。\n]+)"),
    }
    raw: dict[str, str] = {}
    for key, pat in pats.items():
        m = pat.search(t)
        if not m:
            raise ValueError(f"技术句缺字段 {key!r}: {t[:80]}")
        raw[key] = m.group(1).strip()

    return CaseSpec(
        case_id=case_id,
        n_spans=int(parse_number(raw["n_spans"])),
        span_m=parse_number(raw["span_m"]),
        n_elem_per_span=int(parse_number(raw["n_elem"])),
        B=parse_number(raw["B"]),
        h=parse_number(raw["h"]),
        tw=parse_number(raw["tw"]),
        tf=parse_number(raw["tf"]),
        E=parse_number(raw["E"]),
        nu=parse_number(raw["nu"]),
        udl_kN=parse_number(raw["udl"]),
        title_cn=raw["title_cn"],
        title_en=raw["title_en"],
        source_text=t,
    )


def parse_file(path: str | Path, case_id: str = "CB3D01") -> CaseSpec:
    return parse_case(Path(path).read_text(encoding="utf-8"), case_id=case_id)
