"""易档 13 个对拍算例的统一注册表（技术句 → 同源导出 → 双求解对拍）。

设计边界（如实声明）：
  * 每例的建模参数来自其 cases/run_cases_*.py 的 CFG，反向还原为受控中文技术句。
  * 注册表只声明「参数 + 链路元数据」；实际导出/求解/对拍全部复用项目现有函数，
    不重新实现任何求解器，也不覆盖 cases/ 下冻结证据。
  * 技术句是参数齐全的受控描述，不是自由口语理解；不替代 model_doc/nl_parser。

四类链路：
  A 2d_box    : build_beam_bridge        → spec_to_apdl          → example_mixed.exe      → compare / compare_generic
  B box3d     : build_box_beam_3d        → spec3d_to_apdl        → example_3d_report.exe  → compare_3d
  C loads3d   : build_box_beam_3d_loads  → spec3d_loads_to_apdl  → example_3d_report.exe  → compare_3d_loads
  D mixed     : build_cable/arch/tower   → spec_mixed_to_apdl    → example_3d_report.exe  → compare_mixed
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

BS = Path(r"D:\AI-work\BridgeStudio")
CASES_DIR = BS / "cases"

FEM_MIXED = Path(r"D:\AI-work\FEM-Eigen\build\example_mixed.exe")
FEM_3D = Path(r"D:\AI-work\FEM-Eigen\build\example_3d_report.exe")
ANSYS = Path(r"C:\Program Files\ANSYS Inc\v180\ANSYS\bin\winx64\ANSYS180.exe")


@dataclass
class CaseDef:
    key: str
    folder: str
    stem: str
    chain: str                      # 2d_box / box3d / loads3d / mixed
    desc: str                       # 技术句文件名
    title_en: str                   # ANSYS /TITLE（英文，导出时传入）
    title_cn: str                   # 中文标题（dat 首行 / model.title）
    params: dict = field(default_factory=dict)
    # 2D 专用
    n_spans_compare: int = 0        # compare() 的 n_spans（仅 CB01/CB02）
    use_generic_compare: bool = False
    has_spring: bool = False
    # 混合单元专用
    mixed_builder: str = ""
    tower: bool = False
    # 3D 箱梁专用
    dual_inp: bool = False          # 是否生成 _b4.inp（BEAM4 严格基准）

    @property
    def case_dir(self) -> Path:
        return CASES_DIR / self.folder

    def frozen(self, name: str) -> Path:
        return self.case_dir / name


REGISTRY: dict[str, CaseDef] = {
    # ---------------- A 类：2D 连续梁 ----------------
    "CB01": CaseDef(
        key="CB01", folder="CB01_two_span_continuous_beam", stem="cb01_2span",
        chain="2d_box", desc="desc_cb01.txt",
        title_en="CB01 two-span continuous beam 2x20m UDL 20kN/m",
        title_cn="40m 2跨连续梁桥",
        params=dict(span=40.0, n_spans=2, n_elem=20, shape="rectangular",
                    h=1.2, b=0.6, udl_kN=20.0, gravity=0.0),
        n_spans_compare=2,
    ),
    "CB02": CaseDef(
        key="CB02", folder="CB02_three_span_continuous_beam", stem="cb02_3span",
        chain="2d_box", desc="desc_cb02.txt",
        title_en="CB02 three-span continuous box beam 3x30m UDL 20kN/m",
        title_cn="90m 3跨连续梁桥",
        params=dict(span=90.0, n_spans=3, n_elem=30, shape="box",
                    h=2.0, b=8.0, udl_kN=20.0, gravity=0.0),
        n_spans_compare=3,
    ),
    "CB03": CaseDef(
        key="CB03", folder="CB03_selfweight", stem="cb03_selfweight",
        chain="2d_box", desc="desc_cb03.txt",
        title_en="CB03 two-span beam UDL 20kN/m + self weight (DENS + ACEL)",
        title_cn="40m 2跨连续梁桥",
        params=dict(span=40.0, n_spans=2, n_elem=20, shape="rectangular",
                    h=1.2, b=0.6, udl_kN=20.0, gravity=-9.81),
        use_generic_compare=True,
    ),
    "CB04": CaseDef(
        key="CB04", folder="CB04_point_loads", stem="cb04_point_loads",
        chain="2d_box", desc="desc_cb04.txt",
        title_en="CB04 two-span beam with two 200kN point loads at span midpoints",
        title_cn="40m 2跨连续梁桥",
        params=dict(span=40.0, n_spans=2, n_elem=20, shape="rectangular",
                    h=1.2, b=0.6, udl_kN=0.0, gravity=0.0),
        use_generic_compare=True,
    ),
    "CB05": CaseDef(
        key="CB05", folder="CB05_elastic_support", stem="cb05_elastic_support",
        chain="2d_box", desc="desc_cb05.txt",
        title_en="CB05 two-span beam with elastic mid support K=2e6 N/m",
        title_cn="40m 2跨连续梁桥",
        params=dict(span=40.0, n_spans=2, n_elem=20, shape="rectangular",
                    h=1.2, b=0.6, udl_kN=20.0, gravity=0.0),
        use_generic_compare=True, has_spring=True,
    ),

    # ---------------- B 类：3D 箱梁（BEAM188 + BEAM4）----------------
    "CB3D01": CaseDef(
        key="CB3D01", folder="CB3D01_three_span_box_beam_3d", stem="cb3d01_3span",
        chain="box3d", desc="desc_cb3d01.txt",
        title_en="CB3D01 three-span box beam 3D 3x30m nodalized UDL",
        title_cn="三跨连续箱梁 3D 单梁线 均布荷载",
        params=dict(spans=(30.0, 30.0, 30.0), n_elem_per_span=10, B=8.0, h=2.0,
                    tw=0.02, tf=0.025, E=2.06e11, nu=0.2, udl_kN=20.0),
        dual_inp=True,
    ),
    "CB3D02": CaseDef(
        key="CB3D02", folder="CB3D02_space_effects", stem="cb3d02_space",
        chain="box3d", desc="desc_cb3d02.txt",
        title_en="CB3D02 three-span box beam 3D lateral force + torsion",
        title_cn="三跨连续箱梁 3D 空间效应（横向力 + 扭矩）",
        params=dict(spans=(30.0, 30.0, 30.0), n_elem_per_span=10, B=8.0, h=2.0,
                    tw=0.02, tf=0.025, E=2.06e11, nu=0.2, udl_kN=10.0,
                    extra_loads=((16, 2, -100000.0), (16, 4, 50000.0))),
        dual_inp=True,
    ),

    # ---------------- C 类：3D 真实分布荷载 ----------------
    "CB3D06": CaseDef(
        key="CB3D06", folder="CB3D06_distributed_udl", stem="cb3d06_udl",
        chain="loads3d", desc="desc_cb3d06.txt",
        title_en="CB3D06 three-span box beam 3D REAL *ELEMENT_LOAD q=20kN/m",
        title_cn="3D box beam with real distributed loads",
        params=dict(q_kN=20.0),
    ),
    "CB3D07": CaseDef(
        key="CB3D07", folder="CB3D07_distributed_selfweight", stem="cb3d07_selfweight",
        chain="loads3d", desc="desc_cb3d07.txt",
        title_en="CB3D07 3D distributed UDL + DENS selfweight + SELF_WEIGHT + point load",
        title_cn="3D box beam with real distributed loads",
        params=dict(q_kN=20.0, dens=2500.0, gravity=(0.0, 0.0, -9.81),
                    self_weights=(dict(who="ALL", rule="MASS", value=100.0),),
                    extra_loads=((16, 3, -100000.0),)),
    ),
    "CB3D08": CaseDef(
        key="CB3D08", folder="CB3D08_trapezoidal_loads", stem="cb3d08_trapezoid",
        chain="loads3d", desc="desc_cb3d08.txt",
        title_en="CB3D08 3D trapezoidal *ELEMENT_LOAD q1=20 q2=40 kN/m",
        title_cn="3D box beam with real distributed loads",
        params=dict(q_kN=20.0, q2_kN=40.0),
    ),

    # ---------------- D 类：混合单元（BEAM + CABLE/TRUSS）----------------
    "CB3D03": CaseDef(
        key="CB3D03", folder="CB3D03_cable_stayed_3d", stem="cb3d03_cable_stayed",
        chain="mixed", desc="desc_cb3d03.txt",
        title_en="CB3D03 single-pylon cable-stayed bridge 3D (BEAM + CABLE)",
        title_cn="独塔斜拉桥 3D 混合单元（BEAM 主梁 + CABLE 斜拉索）",
        mixed_builder="build_cable_stayed_3d",
        params=dict(span=60.0, n_elem=12, B=8.0, h=2.0, tw=0.02, tf=0.025,
                    E_deck=3.45e10, nu=0.2, tower_z=18.0, cable_area=0.004,
                    E_cable=1.95e11, udl_kN=20.0,
                    title="独塔斜拉桥 3D 混合单元（BEAM 主梁 + CABLE 斜拉索）"),
    ),
    "CB3D04": CaseDef(
        key="CB3D04", folder="CB3D04_arch_bridge_3d", stem="cb3d04_arch_bridge",
        chain="mixed", desc="desc_cb3d04.txt",
        title_en="CB3D04 parabolic arch bridge 3D (BEAM rib/deck + TRUSS hangers)",
        title_cn="抛物线拱桥 3D 混合单元（BEAM 拱肋/主梁 + TRUSS 吊杆）",
        mixed_builder="build_arch_bridge_3d",
        params=dict(span=60.0, n_elem=12, rise=12.0, B=8.0, h=2.0,
                    tw=0.02, tf=0.025, E_deck=3.45e10, nu=0.2,
                    hanger_area=0.002, E_hanger=1.95e11, udl_kN=20.0,
                    title="抛物线拱桥 3D 混合单元（BEAM 拱肋/主梁 + TRUSS 吊杆）"),
    ),
    "CB3D09": CaseDef(
        key="CB3D09", folder="CB3D09_real_tower_3d", stem="cb3d09_real_tower",
        chain="mixed", desc="desc_cb3d09.txt",
        title_en="CB3D09 single-pylon cable-stayed bridge 3D with real vertical tower (BEAM + CABLE)",
        title_cn="独塔斜拉桥 3D 真实塔柱（BEAM 主梁/塔柱 + CABLE 斜拉索）",
        mixed_builder="build_cable_stayed_tower_3d", tower=True,
        params=dict(span=60.0, n_elem=12, n_tower_seg=6, tower_z=18.0,
                    B=8.0, h=2.0, tw=0.02, tf=0.025, E_deck=3.45e10, nu=0.2,
                    tower_b=3.0, tower_h=3.0, cable_area=0.004, E_cable=1.95e11,
                    udl_kN=20.0,
                    title="独塔斜拉桥 3D 真实塔柱（BEAM 主梁/塔柱 + CABLE 斜拉索）"),
    ),
}

# 稳定顺序：2D → 3D 箱梁 → 分布荷载 → 混合单元
ORDER = ["CB01", "CB02", "CB03", "CB04", "CB05",
         "CB3D01", "CB3D02", "CB3D06", "CB3D07", "CB3D08",
         "CB3D03", "CB3D04", "CB3D09"]


def get(key: str) -> CaseDef:
    return REGISTRY[key.upper()]


def all_cases() -> list[CaseDef]:
    return [REGISTRY[k] for k in ORDER]
