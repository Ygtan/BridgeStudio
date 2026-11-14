"""verify_b_class_spec.py —— B 类后续项接入规格守卫（纯离线）。

`B类后续项接入规格.md` 管 B2 剩余项 / B3 非线性稳定全过程 / B4 预应力损失
两处规范口径 / B5 无应力曲率。这些是研究性或未决口径的项目，本守卫不检查
「功能是否可用」（那会虚报能力），而是检查：

1. 规格章节与「本轮未实现」「不新增求解键」等声明齐全；
2. FE / BS 两侧的**如实能力边界**逐条还在（删掉就等于虚报）；
3. 附录 G 条文来源与两条禁止（σpe 代表值待决、附录 C 表值不得编）在位；
4. `[BUCKLING] ERROR=nonlinear_state_not_supported` 与 4 键注册表未被提前改掉；
5. 12 格默认 `.dat` 黄金哈希与 22 键 `*PRESTRESS_LOSS` 表行逐位不变；
6. B2 已接通的 2D 模态 / 屈曲不虚报、文档计数同步。

不调求解器、不写 `.dat`、不新增任何能力。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import model_doc
import solver_3d
from verify_stage_tension_3d import DEFAULT_DAT_SHA

SPEC = ROOT / "B类后续项接入规格.md"
RESULTS: list[tuple[bool, str, str]] = []

LOSS_KEYS = ("SIGMA_CON", "EP", "EC", "FPK", "MU", "K", "THETA", "X",
             "DELTA_L", "L", "T1", "T2", "SIGMA_PC4", "SIGMA_PC6",
             "PSI", "ZETA", "ECS", "PHI", "RHO", "RHOPS", "POST", "REBAR")


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((bool(ok), name, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""


def fe() -> Path | None:
    from solve_2d import _fem_eigen_dir

    return _fem_eigen_dir()


def structure() -> None:
    t = read(SPEC)
    check("规格文档存在", SPEC.is_file() and bool(t))
    for i in range(7):
        check(f"§{i} 章节存在", f"## {i}." in t)
    for kw in ("暂缓", "本轮未实现", "不新增求解键", "逐位不变",
               "不静默兜底", "只暴露内核已有能力", "结论与计算书同源",
               "明确未闭环", "MIDAS 数值对拍"):
        check(f"§0/§6 声明在位：{kw}", kw in t)
    check("§1 写明 9→7 的文档漂移纠正",
          "9 个" in t and "7 个" in t and "文档与内核事实漂移" in t)
    check("§1 三类分类齐全（第十四次口径）",
          "接入层缺口" in t and "维度不适用" in t
          and "第十四次" in t and "[OPT2D]" in t)
    check("§0 总表登记第十三次 [LENGTH2D] 与守卫名",
          "第十三次" in t and "[LENGTH2D]" in t
          and "verify_length_2d.py" in t)


def fe_sources() -> None:
    root = fe()
    if root is None:
        print("  [跳过] 未定位到 FEM-Eigen 源码目录")
        return
    cl = read(root / "CHANGELOG.md")
    check("CHANGELOG 明写全过程未覆盖",
          "本版本未覆盖" in cl and "不拿线弹性 λ 冒充" in cl)
    cs = read(root / "cable_solver.cpp")
    check("cable_solver 仍写 nonlinear_state_not_supported ERROR 段",
          "nonlinear_state_not_supported" in cs)
    ifmt = read(root / "input_format_3d.md")
    check("input_format_3d 仍登记该 ERROR 段",
          "nonlinear_state_not_supported" in ifmt)
    nl = read(root / "fem_3d_nl.hpp")
    check("fem_3d_nl 标注无应力曲率属后续扩展", "无应力曲率" in nl)
    check("fem_3d_nl 主求解器仍是荷载控制 Newton",
          "solveModel3DNonlinear" in nl and "Ftarget" in nl)
    check("内核没有虚报弧长法 / Riks 实现",
          "Riks" not in nl and "arc-length" not in nl)
    pl = read(root / "prestress_loss.hpp")
    check("σl2 仍是无条件直线式（本轮未改内核）",
          "r.sigmaL2 = in.sumDeltaL / in.tendonLength * in.Ep;" in pl)
    check("22 键表仍含 ECS / PHI 用户输入",
          'g("ECS", li.epsCs);' in pl and 'g("PHI", li.phi);' in pl)
    check("内核没有新增 L2_ANTI 反摩擦键", "L2_ANTI" not in pl)
    code = read(root / "code_loads.hpp")
    check("code_loads 自认规范表折减是查表用的",
          "查表用的" in code and "jtg3362NominalCreep" in code)
    um = read(root / "USER_MANUAL.md")
    check("USER_MANUAL 如实写明 σl2 反摩擦未实现",
          "σl2 曲线筋按附录 G 的反摩擦影响未实现" in um)
    check("USER_MANUAL 如实写明 σl6 不按龄期自动发展",
          "程序不自动算收缩" in um)
    em = read(root / "example_modal.cpp")
    check("2D 模态内核写 == 自振频率 == 与屈曲条件段",
          "== 自振频率 ==" in em and "computeBucklingFactors2D" in em)


def bs_boundaries() -> None:
    b3 = read(ROOT / "buckling_3d.py")
    check("buckling_3d 保留非线性状态中文说明",
          "nonlinear_state_not_supported" in b3)
    r3 = read(ROOT / "report_3d.py")
    check("report_3d 保留 σl2 直线筋边界",
          "σl2 目前只按直线筋" in r3)
    ms = read(ROOT / "model_spec_3d.py")
    check("PrestressLoss3D 保留两条规范边界",
          "σl2 目前只支持直线筋" in ms and "附录 C" in ms)
    guide = read(ROOT / "开发使用手册.md")
    check("开发手册保留「不做非线性稳定」", "不做非线性稳定" in guide)
    mat = read(ROOT / "能力边界矩阵.md")
    check("能力矩阵仍保留 3 条真缺口",
          all(k in mat for k in ("组合体系桥型没有独立建造器",
                                 "2D ↔ 3D 高级能力不对齐", "P4 成果层")))
    check("能力矩阵 §4.2 写 7 个 2D=N 且已收窄",
          "7 个" in mat and "已收窄" in mat)
    check("能力矩阵 §2 模态 / 屈曲两格 2D=Y",
          "| 模态与自振特性 | Y | Y |" in mat
          and "| 线性屈曲因子 | Y | Y |" in mat)
    rd = read(ROOT / "README.md")
    check("README 同步 7 项且不再写 9 项",
          "7 项高级能力" in rd and "9 项高级能力" not in rd)
    check("README P1 仍未打勾（不虚报全量移植）",
          "- [ ] **P1**" in rd and "真实能力缺口" in rd)
    check("README 仍写 MIDAS 数值对拍未做",
          "MIDAS 数值对拍仍未做" in rd and "本机无 MIDAS" in rd)
    # 第六次 A 档：B2「截面应力」半部接入，但该格只能标 P，不能涂成 Y。
    check("能力矩阵该格 2D 标 P（不是 Y）",
          "| 截面应力 / 承载力验算 | P | Y |" in mat)
    check("能力矩阵 §4.2 写 4 个 N + 3 个 P",
          "4 个" in mat and "3 个 `2D=P`" in mat)
    check("README 同步 3 项部分接入", "3 项部分接入" in rd)


def b2_strength() -> None:
    t = read(SPEC)
    check("规格写明承载力需 RebarSpec2D 配筋契约",
          "RebarSpec2D" in t and "配筋契约" in t)
    check("规格写明应力半部已接入",
          "已接入" in t and "strength_2d.py" in t)
    check("规格写明标 P 不标 Y", "标 `P` 不标 `Y`" in t)
    st = read(ROOT / "strength_2d.py")
    check("2D 应力层不消费内核单元应力段",
          "不解析内核「单元应力 (Pa)」段" in st and "parse_result" not in st)
    check("2D 应力层写明承载力本次未做",
          "承载力验算（Mu / Vu / 压弯 / 轴压）本次未做" in st)
    check("2D 应力守卫在位", (ROOT / "verify_strength_2d.py").is_file())
    g = read(ROOT / "verify_strength_2d.py")
    check("2D 应力守卫汇总按布尔位统计（不把名称当通过）",
          "PASS.append((name, bool(ok), detail))" in g
          and "sum(1 for _, ok, _ in PASS if ok)" in g)
    check("开发手册登记 2D 应力守卫",
          "verify_strength_2d.py" in read(ROOT / "开发使用手册.md"))


def spec_rules() -> None:
    t = read(SPEC)
    for kw in ("G.0.1", "G.0.2-1", "G.0.2-2", "G.0.2-3", "G.0.2-4",
               "G.0.2-5", "表 C.1.2", "表 C.2.2", "βsc",
               "jtg-3362.readthedocs.io"):
        check(f"附录 G / C 条文依据登记：{kw}", kw in t)
    check("σl2 写明 σpe 代表值口径待用户拍板",
          "待用户拍板的口径" in t and "属设计口径选择" in t)
    check("σl6 写明表值缺席不得自动算",
          "绝不调用" in t and "表值缺席" in t and "不得凭空编数值" in t)
    check("B3 写明禁止拿 λ 冒充 / 保留 ERROR 段",
          "不拿线弹性 λ 冒充" in t and "必须保留" in t)
    check("B5 写明预抬高不得冒充无应力曲率",
          "不得把「节点预抬高」写成「无应力曲率已实现」" in t)
    check("§5 登记 not_converged 口径选择",
          "not_converged" in t and "口径选择" in t)
    check("§6 明确 4 类未闭环项",
          all(k in t for k in ("B3", "B4", "B5", "MIDAS 数值对拍")))
    check("规格写明 2D 逐项评估按源码不按旧文档",
          "按源码逐项评估（不按旧文档）" in t)


def no_new_capability() -> None:
    check("solver_3d._registry() 仍 4 键",
          len(solver_3d._registry()) == 4, str(sorted(solver_3d._registry())))
    check("BUILDERS 仍 12 格", len(model_doc.BUILDERS) == 12,
          str(len(model_doc.BUILDERS)))
    src_combo = read(ROOT / "model_doc.py")
    check("组合体系仍显式拒绝（未偷偷放行）",
          "COMBO_SYSTEM_MSG" in src_combo)
    root = fe()
    if root:
        cs = read(root / "cable_solver.cpp")
        ifmt = read(root / "input_format_3d.md")
        # 第十七次（2026-11-14）：B3 按 §2.3 两阶段规格 opt-in 接入。
        # 守卫不再禁止 [STABILITY]，改为锁住「默认全关 / 不改主求解器 /
        # 保留 BUCKLING ERROR / 独立锚点 / 仍 P 不 Y」的不虚报口径。
        sout = read(root / "stability_output_3d.hpp")
        check("B3 opt-in：关键字与结果段已在源码登记",
              '"STABILITY"' in cs and "[STABILITY]" in ifmt
              and "stabilityErrorSection3D" in sout)
        check("B3 opt-in：默认全关且分流独立于成桥态闭环",
              "bool enabled = false;" in cs
              and "if (cfg.stability.enabled)" in cs
              and "return runStabilityMode" in cs)
        check("B3 opt-in：StabilityCfg 独立、主求解器零改动",
              "struct StabilityCfg" in cs
              and "NonlinearOptions3D nl;" in cs
              and "solveModel3DNonlinear" in read(root / "fem_3d_nl.hpp")
              and "Ftarget" in read(root / "fem_3d_nl.hpp"))
        check("B3 opt-in：[BUCKLING] ERROR 段仍保留",
              '"nonlinear_state_not_supported"' in cs)
        check("B3 opt-in：独立守卫与矩阵 P≠Y 在位",
              (ROOT / "verify_stability_3d.py").is_file()
              and "stability_3d.py" in read(ROOT / "开发使用手册.md")
              and "| 非线性稳定全过程（球面弧长法 opt-in） | N | P |"
              in read(ROOT / "能力边界矩阵.md"))
        nl = read(root / "fem_3d_nl.hpp")
        check("内核未新增第二套路径跟踪求解器（仍在独立头文件）",
              "solveEquilibriumPath3D" not in nl
              and "arc-length" not in nl and "Riks" not in nl)
    m2 = read(ROOT / "modal_2d.py")
    check("2D 模态层不重算 K/M、不补周期 T",
          "不重算 K/M" in m2 and "T = 1/f" in m2)
    check("2D 模态守卫在位", (ROOT / "verify_modal_2d.py").is_file())
    # 2026-11-10 顺带修掉的「假绿」：包络守卫曾把断言名称当布尔位统计，
    # 2 条真实 FAIL 被汇总成 152/152。锁住元组顺序，不许再退化。
    env = read(ROOT / "verify_envelope_3d.py")
    check("包络守卫汇总按布尔位统计（不把名称当通过）",
          "PASS.append((name, bool(ok), detail))" in env
          and "sum(1 for _, ok, _ in PASS if ok)" in env)


def dat_invariant() -> None:
    for (kind, dim), b in model_doc.BUILDERS.items():
        dat = model_doc.ModelDoc(kind, dict(model_doc.DEFAULTS[b]),
                                 dimension=dim).to_dat()
        h = hashlib.sha256(dat.encode()).hexdigest()[:16]
        check(f"默认 .dat 黄金哈希：{b}", h == DEFAULT_DAT_SHA[b], h)
    from model_spec_3d import PrestressLoss3D

    line = PrestressLoss3D(sigma_con=1.395e9).to_dat(1)
    check("分项损失表仍是 22 键",
          all(k + "=" in line for k in LOSS_KEYS)
          and len(line.split()) == len(LOSS_KEYS) + 1,
          f"{len(line.split()) - 1} 键")
    check("分项损失表本轮未新增反摩擦键",
          "L2_ANTI" not in line and "L2_X" not in line)


def doc_registration() -> None:
    t = read(SPEC)
    check("规格写明守卫名", "verify_b_class_spec.py" in t)
    for f in ("README.md", "使用说明.md", "开发使用手册.md"):
        txt = read(ROOT / f)
        check(f"{f} 登记 B 类规格", "B类后续项接入规格.md" in txt)
    guide = read(ROOT / "开发使用手册.md")
    check("开发手册登记新守卫脚本", "verify_b_class_spec.py" in guide)
    check("开发手册登记 2D 模态守卫", "verify_modal_2d.py" in guide)
    check("能力矩阵登记第五次闭环记录",
          "第五次闭环记录" in read(ROOT / "能力边界矩阵.md"))
    check("规格自身不含「已完成」虚报措辞",
          "尚未实现" in t or "均未实现" in t)


def b2_validation() -> None:
    t = read(SPEC)
    check("规格写明引用完整性一格主体是文档漂移", "文档漂移" in t)
    check("规格写明多格式导出被 X20 契约锁死",
          "X20 契约" in t and "唯一转换点" in t and "不补 0" in t)
    check("规格写明 2D 校验标 P 不标 Y", "标 `P` 不标 `Y`" in t)
    p = ROOT / "verify_validate_2d.py"
    check("2D 校验守卫在位", p.is_file())
    g = read(p)
    check("2D 校验守卫汇总按布尔位统计",
          "PASS.append((name, bool(ok), detail))" in g
          and "sum(1 for _, ok, _ in PASS if ok)" in g)
    check("开发手册登记 2D 校验守卫",
          "verify_validate_2d.py" in read(ROOT / "开发使用手册.md"))


def b2_report() -> None:
    """第八次：2D 同源计算书四出口接通导出机制，但规格仍写「内容窄、标 P」."""
    t = read(SPEC)
    check("规格写明四出口已接通导出机制且仍标 P",
          "四出口" in t and "已接通" in t and "仍标 `P` 不标 `Y`" in t)
    check("规格写明不另写渲染器、不改 BKSEC 书签机制",
          "不另写渲染器" in t and "BKSEC" in t)
    check("规格写明 PDF 失败不生成占位文件", "占位" in t)
    p = ROOT / "verify_report_2d.py"
    check("2D 四出口守卫在位", p.is_file())
    g = read(p)
    check("2D 四出口守卫汇总按布尔位统计",
          "PASS.append((name, bool(ok), detail))" in g
          and "sum(1 for _, ok, _ in PASS if ok)" in g)
    check("开发手册登记 2D 四出口守卫",
          "verify_report_2d.py" in read(ROOT / "开发使用手册.md"))

def b2_modal_detail() -> None:
    """第九次：FE opt-in 补 [MODAL2D] 段，规格须写明口径与如实边界."""
    t = read(SPEC)
    check("规格登记第九次 [MODAL2D] 明细闭环",
          "第九次" in t and "[MODAL2D]" in t)
    check("规格写明用户拍板 BASIS=xy / Z 向不存在",
          "BASIS=xy" in t and "不存在（不是 0）" in t)
    check("规格写明 T 由内核写出、Python 不得现算",
          "由内核写出" in t and "不得现算" in t)
    p = ROOT / "verify_modal_detail_2d.py"
    check("2D 模态明细守卫在位", p.is_file())
    g = read(p)
    check("2D 模态明细守卫汇总按布尔位统计",
          "PASS.append((name, bool(ok), detail))" in g
          and "sum(1 for _, ok, _ in PASS if ok)" in g)
    check("开发手册登记 2D 模态明细守卫",
          "verify_modal_detail_2d.py" in read(ROOT / "开发使用手册.md"))


def b2_loss_detail() -> None:
    """第十次：FE opt-in 补 [PRESTRESSLOSS2D] 段，规格须写明口径与如实边界."""
    t = read(SPEC)
    check("规格登记第十次 [PRESTRESSLOSS2D] 明细闭环",
          "第十次" in t and "[PRESTRESSLOSS2D]" in t)
    check("规格写明 BASIS=2d_global / 不设 STAGE 占位",
          "BASIS=2d_global" in t and "不设 STAGE 占位" in t)
    check("规格写明数值由内核写出、Python 只解析",
          "由内核写出" in t and "只解析" in t)
    check("规格写明该项仍标 P 不标 Y", "标 `P` 不标 `Y`" in t)
    check("规格写明 B2 剩余 2 项", "B2 剩余 2 项" in t)
    p = ROOT / "verify_prestress_loss_2d.py"
    check("2D 分项损失守卫在位", p.is_file())
    g = read(p)
    check("2D 分项损失守卫汇总按布尔位统计",
          "PASS.append((name, bool(ok), detail))" in g
          and "sum(1 for _, ok, _ in PASS if ok)" in g)
    check("开发手册登记 2D 分项损失守卫",
          "verify_prestress_loss_2d.py" in read(ROOT / "开发使用手册.md"))


def b2_newton_detail() -> None:
    """第十一次只闭环内层 Newton 一层；第十三次索长格改 P、[OPT] 仍 N，
    规格须写明如实边界."""
    t = read(SPEC)
    mat = read(ROOT / "能力边界矩阵.md")
    check("规格登记第十一次 [NEWTON2D] 明细闭环",
          "第十一次" in t and "[NEWTON2D]" in t)
    check("规格写明 BASIS=2d_global / 不写 REFACT",
          "BASIS=2d_global" in t and "不写 `REFACT`" in t)
    check("规格写明 FE 判据 / 步长 / 线搜索 / 收敛零改动",
          "一字不改" in t and "留痕" in t)
    check("规格写明该项仍标 P 不标 Y", "标 `P` 不标 `Y`" in t)
    check("历史段落仍登记 [LENGTH2D] 已接入、[OPT] 2D=N / 不造占位（第十三次口径）",
          "[LENGTH2D]" in t and "`[OPT]` 格仍 `2D=N`" in t
          and "不造占位" in t)
    check("规格写明 B2 剩余仍 2 项且内容收窄",
          "B2 剩余仍 2 项" in t and "内容" in t)
    check("矩阵索长格 2D=P、内层 Newton 格 2D=P、第十四次 `[OPT]` 格 2D=P（仍 P 不 Y）",
          "| 内层 Newton / 共转列式过程量 | P | Y |" in mat
          and "| 索长迭代 / 几何闭合过程量 | P | Y |" in mat
          and "| 目标索力优化 `[OPT]` + 体系守卫 | P | Y |" in mat)
    p = ROOT / "verify_newton_2d.py"
    check("2D Newton 守卫在位", p.is_file())
    g = read(p)
    check("2D Newton 守卫汇总按布尔位统计",
          "PASS.append((name, bool(ok), detail))" in g
          and "sum(1 for _, ok, _ in PASS if ok)" in g)
    check("开发手册登记 2D Newton 守卫",
          "verify_newton_2d.py" in read(ROOT / "开发使用手册.md"))


def b2_capacity_2d() -> None:
    """第十二次：只闭环 Mu / Vu 一条 flex 链，压弯 / 轴压仍缺，仍标 P。"""
    t = read(SPEC)
    mat = read(ROOT / "能力边界矩阵.md")
    check("规格登记第十二次 Mu / Vu 半部",
          "第十二次" in t and "Mu / Vu" in t)
    check("规格写明 RebarSpec2D 与 capacity_2d 口径层",
          "RebarSpec2D" in t and "capacity_2d.py" in t)
    check("规格写明压弯 / 轴压不做、不照搬 3D 阈值",
          "压弯 / 轴压" in t and "5% / 1%" in t)
    check("规格写明仍标 P 不标 Y", "标 `P` 不标 `Y`" in t)
    check("历史段落仍登记 §4.2 为 1N+6P、B2 剩余仍 2 项（第十三次口径）",
          "1 个 `2D=N`" in t and "B2 剩余仍 2 项" in t)
    check("历史段落仍登记矩阵该格 P、§4.2 为 1N+6P（第十三次口径）",
          "| 截面应力 / 承载力验算 | P | Y |" in mat
          and "1 个 `2D=N`" in mat and "6 个 `2D=P`" in mat)
    p = ROOT / "verify_capacity_2d.py"
    check("2D 承载力守卫在位", p.is_file())
    g = read(p)
    check("2D 承载力守卫汇总按布尔位统计",
          "PASS.append((name, bool(ok), detail))" in g
          and "sum(1 for _, ok, _ in PASS if ok)" in g)
    check("开发手册登记 2D 承载力守卫",
          "verify_capacity_2d.py" in read(ROOT / "开发使用手册.md"))


def b2_length_2d() -> None:
    """第十三次：[LENGTH2D] 平面索长闭合一层按 opt-in 首次真正新造 2D 力学，
    索长格 N→P 仍 P 不 Y；[OPT] 仍 2D=N、不造占位。规格须逐条写明如实边界."""
    t = read(SPEC)
    mat = read(ROOT / "能力边界矩阵.md")
    guide = read(ROOT / "开发使用手册.md")
    check("规格 §0 总表登记第十三次 [LENGTH2D] 一层",
          "第十三次" in t and "[LENGTH2D]" in t
          and "首次真正新造 2D 力学" in t)
    check("规格 §1 写明 [LENGTH2D] 一层已 opt-in 接通、第十四次 `[OPT2D]` 按 opt-in 新造接通",
          "第十三次起 `[LENGTH2D]` 一层已按 opt-in 接通" in t
          and "第十四次起 `[OPT2D]`" in t
          and "0 个 `2D=N` + 7 个 `2D=P`" in t)
    check("规格 §6 第 11 条登记 [LENGTH2D] 一层不虚报",
          "11. 第十三次 `[LENGTH2D]` 一层不虚报" in t)
    check("规格明确未闭环清单只剩 [OPT] 一层与多格式导出 / .mct",
          "明确未闭环" in t
          and "`[LENGTH2D]` 已于第十三次按 `P` 接通" in t
          and "`[OPT]` 带约束索力优化一层" in t
          and "`.mct` 反向导入" in t)
    check("规格登记 [LENGTH2D] / length_2d.py / verify_length_2d.py",
          "[LENGTH2D]" in t and "length_2d.py" in t
          and "verify_length_2d.py" in t)
    check("规格登记 LengthSpec2D 契约真实符号在矩阵依据列",
          "bridge_spec.py::LengthSpec2D" in mat
          and "class LengthSpec2D" in read(ROOT / "bridge_spec.py"))
    check("历史段落仍登记 [OPT] 2D=N、仍 P 不 Y（第十三次口径）",
          "`[OPT]` 格仍 `2D=N`" in t and "不是 `Y`" in t)
    check("历史段落仍登记 §4.2 为 1N+6P 且 B2 剩余仍 2 项（第十三次口径）",
          "1 个 `2D=N` + 6 个 `2D=P`" in t
          and "B2 剩余仍 2 项" in t)
    check("规格写明无垂度线性杆 / 有限差分 J + LM / 不等于 3D 三层迭代",
          "无垂度线性杆" in t and "中心有限差分" in t
          and "LM 阻尼最小二乘" in t
          and "不等于 3D 的" in t and "三层迭代" in t
          and "J=M·D" in t)
    check("规格写明双层 opt-in 默认全关、默认 .dat / .result / 默认 HTML 逐位不变",
          "双层 opt-in 默认全关" in t
          and "默认 `.dat` / `.result` / 默认 HTML 逐位不变" in t)
    p = ROOT / "verify_length_2d.py"
    check("2D 索长闭合守卫在位", p.is_file())
    g = read(p)
    check("2D 索长闭合守卫汇总按布尔位统计（不把名称当通过）",
          "PASS.append((name, bool(ok)" in g
          and "sum(1 for _, ok, _ in PASS if ok)" in g)
    check("开发手册登记 verify_length_2d.py 守卫",
          "verify_length_2d.py" in guide)


def b2_opt_2d() -> None:
    """第十四次：[OPT2D] 2D 平面索力优化按 opt-in 新造接通，矩阵该格
    N→P（仍 P 不 Y），§4.2 变 0N+7P、B2 剩余 1 项；`0 个 2D=N` 断言只落在
    确实含该串的文档上，历史串仍按 D 类准则保留。"""
    t = read(SPEC)
    mat = read(ROOT / "能力边界矩阵.md")
    rd = read(ROOT / "README.md")
    use = read(ROOT / "使用说明.md")
    guide = read(ROOT / "开发使用手册.md")

    check("第十四次：矩阵 §2 [OPT] 格 | P | Y | 且 P ≠ Y、不等于 3D [OPT]",
          "| 目标索力优化 `[OPT]` + 体系守卫 | P | Y |" in mat
          and "P ≠ Y" in mat and "不等于 3D `[OPT]`" in mat)
    check("历史段落仍登记第十四次矩阵 §4.2 0N+7P、B2 剩余 1 项与第十四次闭环记录",
          "0 个 `2D=N` + 7 个 `2D=P`" in mat
          and "B2 剩余 1 项" in mat
          and "第十四次闭环记录" in mat)
    check("历史段落仍登记第十四次 §0 十四次逐次接入与 §6 第 12 条（当前为十五次逐次接入）",
          "部分实现（十五次逐次接入）" in t and "12. 第十四次" in t
          and "13. 第十五次" in t and "十四次" in t)
    check("第十五次：未闭环清单 B2 剩余 0 项、两格按 P 接通仍 P≠Y",
          "明确未闭环" in t and "B2 剩余 0 项" in t
          and "按 `P` 接通" in t and "仍 `P ≠ Y`" in t)
    check("第十四次：README 新章 ### 2D 平面索力优化 `[OPT2D]` 在位",
          "### 2D 平面索力优化 `[OPT2D]`" in rd)
    check("第十五次：开发手册登记 0N+9P 与 B2 剩余 0 项",
          "0 个 `2D=N` + 9 个 `2D=P`" in guide
          and "B2 剩余 0 项" in guide and "第十五次" in guide)
    check("第十五次：使用说明写 B2 剩余 0 项、第十五次新章且不落 0 个 2D=N",
          "B2 剩余 0 项" in use
          and "### 8.5 2D 平行导出 / 导入链（2026-11-12，第十五次）" in use
          and "0 个 `2D=N`" not in use)

    o = read(ROOT / "opt_2d.py")
    p = ROOT / "verify_opt_2d.py"
    g = read(p)
    check("第十四次：opt_2d.py 在位且含 BOUNDARY_NOTE / parse_opt_txt / 四张表键与如实边界",
          (ROOT / "opt_2d.py").is_file()
          and "BOUNDARY_NOTE" in o and "parse_opt_txt" in o
          and all(k in o for k in ("opt_2d_summary_table",
                                   "opt_2d_iter_table",
                                   "opt_2d_cable_table",
                                   "opt_2d_moment_table"))
          and "只做 MINMAXM 目标与显式索力箱 TMIN/TMAX（BND_SRC=USER），不做 MINENERGY" in o
          and "APPLIED=1 只表示建议值被采纳" in o
          and "命中 T* 属 [LENGTH2D] 职责" in o)
    check("第十四次：verify_opt_2d.py 在位、汇总按布尔位统计、登记四张表键",
          p.is_file()
          and "PASS.append((name, bool(ok), str(detail)))" in g
          and "sum(1 for _, ok, _ in PASS if ok)" in g
          and all(k in g for k in ("opt_2d_summary_table",
                                   "opt_2d_iter_table",
                                   "opt_2d_cable_table",
                                   "opt_2d_moment_table")))
    bs = read(ROOT / "bridge_spec.py")
    s2d = read(ROOT / "solve_2d.py")
    mdpy = read(ROOT / "model_doc.py")
    app = read(ROOT / "app.py")
    ehtml = read(ROOT / "export_html.py")
    rpt = read(ROOT / "report_2d.py")
    check("第十四次：BS 源码符号 OptSpec2D / opt_detail / opt_2d_detail / opt2ddetail_opt / opt_html / ⑪",
          "class OptSpec2D" in bs
          and "opt_detail" in s2d and "opt_2d_detail" in mdpy
          and "opt2ddetail_opt" in app and "opt_html" in ehtml
          and "⑪" in rpt and "OPT_BOUNDARY_NOTE" in rpt)

    root = fe()
    if root:
        cl = read(root / "CHANGELOG.md")
        ifmt = read(root / "input_format.md")
        um = read(root / "USER_MANUAL.md")
        check("第十四次：FE 三文档登记 ## 2026-11-12 / *CABLE_OPT2D / 二维 opt-in 专章",
              "## 2026-11-12" in cl and "[OPT2D]" in cl
              and "### 6.6 `*CABLE_OPT2D`" in ifmt
              and "#### 二维 opt-in `[OPT2D]`" in um)
        check("第十四次：FE CHANGELOG 第十三次历史句仍保留（不删历史条目）",
              "`[OPT]` 带约束目标索力优化在 2D" in cl
              and "仍不做" in cl
              and "一字不改、仍为当时的事实记录" in cl)


def b2_fifteenth() -> None:
    """第十五次：B2「2D 平行导出 / 导入链」两格按 B 档平行链接通（仍 P 不 Y），
    矩阵 §4.2 变 0N+9P、B2 剩余 0 项；逐条核对第十五次新口径、X20 正交约定、
    注册表与如实能力边界，第十四次历史条目仍按 D 类准则保留。"""
    spec_doc = read(SPEC)
    x20 = read(ROOT / "X20_接入约定.md")
    vx = read(ROOT / "verify_x20_contract.py")
    check("第十五次：B 类规格 §0 部分实现（十五次逐次接入）、§6 13. 第十五次、未闭环清单 B2 剩余 0 项",
          "部分实现（十五次逐次接入）" in spec_doc
          and "13. 第十五次" in spec_doc
          and "明确未闭环" in spec_doc
          and "B2 剩余 0 项" in spec_doc)
    check("第十五次：B 类规格带日期历史段仍登记 仍被 **X20 契约**锁死 / 继续写「没有」 / 12. 第十四次",
          "仍被 **X20 契约**锁死" in spec_doc
          and "继续写「没有」" in spec_doc
          and "12. 第十四次" in spec_doc)
    check("第十五次：B 类规格 §6 第 13 条写明两格 2D=P 不是 Y、plane_frame_2d/v1 不是 canonical dict",
          "13. 第十五次" in spec_doc and "矩阵两格 `2D=P`" in spec_doc
          and "不是 canonical dict" in spec_doc
          and "plane_frame_2d/v1" in spec_doc)
    check("第十五次：B 类规格写明显式 dimension=2D + dimension_not_plane 拒绝非平面、绝不静默投影",
          'dimension="2D"' in spec_doc and "dimension_not_plane" in spec_doc
          and "spec=None" in spec_doc
          and "绝不静默投影丢信息" in spec_doc)
    check("第十五次：B 类规格写明缺项标「不适用」不补 0、格式占位逐条进 warnings、无 GUI 入口",
          "缺项标「不适用」不补 0" in spec_doc
          and "格式占位逐条进 `warnings`" in spec_doc
          and "无 GUI 入口" in spec_doc)
    check("第十五次：B 类规格写明 MIDAS 数值对拍仍未做、12 格 DEFAULT_DAT_SHA 不刷新、FE 仓零改动",
          "MIDAS 数值对拍本机无 MIDAS、仍未做" in spec_doc
          and "12 格 `DEFAULT_DAT_SHA`" in spec_doc
          and "不刷新" in spec_doc
          and "FE 仓本轮零改动" in spec_doc)
    check("第十五次：X20 契约 §3.4 / | `B5` | / plane_frame_2d/v1 / dimension_not_plane / 格式占位 / 不违反铁律 3 齐备",
          all(k in x20 for k in (
              "### 3.4 2D 平行链的正交约定", "| `B5` |",
              "plane_frame_2d/v1", "dimension_not_plane",
              "格式占位", "不违反铁律 3")))
    check("第十五次：X20 契约 §1–§9 九章齐全且 §9 新增 2D 平行链禁止项",
          all(f"## {i}." in x20 for i in range(1, 10))
          and "把 2D 平面刚架中间表示" in x20)
    check("第十五次：verify_x20_contract.py ASSET_REGISTRY 登记 B5 为 B 档且有 plane_chain()",
          "ASSET_REGISTRY" in vx and '"B5": "B"' in vx
          and "def plane_chain()" in vx and "plane_chain()" in vx)
    check("第十五次：组合体系（B1）仍显式拒绝、B5 仍 P 不 Y、无 GUI 入口",
          "组合体系（B1）仍显式拒绝" in x20
          and "仍 `P` 不 `Y`" in x20 and "无 GUI 入口" in x20)


def main() -> int:
    print("=" * 78)
    print("B 类后续项接入规格守卫（纯离线）")
    print("=" * 78)
    structure()
    fe_sources()
    bs_boundaries()
    b2_strength()
    b2_validation()
    b2_report()
    b2_modal_detail()
    b2_loss_detail()
    b2_newton_detail()
    b2_capacity_2d()
    b2_length_2d()
    b2_opt_2d()
    b2_fifteenth()
    spec_rules()
    no_new_capability()
    dat_invariant()
    doc_registration()
    n_pass = sum(1 for ok, _, _ in RESULTS if ok)
    print("\n" + "=" * 78)
    print(f"  {n_pass}/{len(RESULTS)} 通过")
    return 0 if n_pass == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
