"""verify_curvature_3d.py
=========================
B5 第二十六次：3D 成桥态无应力曲率的 Python 口径层与两侧同源回归。

本脚本纯离线，锁住四件事：

1. **口径常量**：`SECTION` / `BASIS` / `SOURCE` 与 FEM-Eigen 的
   `[UNSTRESSED_CURVATURE]`、`BASIS=unstressed_curvature_fem_only`、
   `--curvature-detail` 严格一致；
2. **只解析不重算**：`curvature_3d.py` 不引求解器、不从位移/内力反推 κ0，
   `M0 = EI·κ0` 只作同批数字互核；缺段/缺字段/不一致一律中文状态且不出表；
3. **正负例**：标准段 → `ok`；BASIS 错、`ERROR=no_curvature_elements`、
   NELEM 缺/不符、缺字段、M0 与 EIκ0 不一致、乱行 → 各自显式状态；
4. **文档同源**：规格 §4.1 与 §6 第 28 条在位；FE 侧 `fem_3d.hpp` /
   `example_3d_report.cpp` 的关键字与段头未漂移。

运行：``python verify_curvature_3d.py``（退出码 0 = 全绿）。
"""

from __future__ import annotations

from console_utf8 import setup_console

setup_console()

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import curvature_3d as cv  # noqa: E402

FE_ROOT = HERE.parent / "FEM-Eigen"
RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((bool(ok), name, detail))


def _txt(basis: str = cv.BASIS, nelem_line: str = "NELEM=1",
         rows: str | None = None,
         error_line: str | None = None) -> str:
    body = [cv.SECTION, f"BASIS={basis}"]
    if error_line:
        body.append(f"ERROR={error_line}")
    else:
        body.append(nelem_line)
        if rows is None:
            rows = "1 K0Y=0 K0Z=0.001 EIY=3.45e10 EIZ=3.45e10 M0Y=0 M0Z=3.45e7"
        body.append(rows)
    return "\n".join(body) + "\n"


def main() -> int:
    # ---------------- 1. 口径常量 ----------------
    check("SECTION 与内核段头一致", cv.SECTION == "[UNSTRESSED_CURVATURE]")
    check("BASIS 与内核口径一致", cv.BASIS == "unstressed_curvature_fem_only")
    check("SOURCE 指向 --curvature-detail", "--curvature-detail" in cv.SOURCE)
    check("SOURCE 指向 .curvature.txt", ".curvature.txt" in cv.SOURCE)
    check("行必需键齐全（6 个）", cv.ROW_KEYS == ("K0Y", "K0Z", "EIY", "EIZ", "M0Y", "M0Z"))

    # ---------------- 2. 正例 ----------------
    d = cv.parse_curvature_txt(_txt())
    check("标准段解析为 ok", d["status"] == "ok", d["status"])
    check("NELEM 读出", d["nelem"] == 1)
    check("单元号读出", list(d["rows"]) == [1])
    check("K0Z 读出", d["rows"][1]["K0Z"] == 0.001)
    check("M0Z 读出", d["rows"][1]["M0Z"] == 3.45e7)

    rows2 = "\n".join([
        "1 K0Y=0 K0Z=0.001 EIY=3.45e10 EIZ=3.45e10 M0Y=0 M0Z=3.45e7",
        "2 K0Y=0.002 K0Z=0 EIY=3.45e10 EIZ=3.45e10 M0Y=6.9e7 M0Z=0",
    ])
    d2 = cv.parse_curvature_txt(_txt(nelem_line="NELEM=2", rows=rows2))
    check("两单元解析为 ok", d2["status"] == "ok", d2["status"])
    check("两单元都入表", len(d2["rows"]) == 2)

    # ---------------- 3. 负例 ----------------
    check("空输入 → no_section", cv.parse_curvature_txt(None)["status"] == "no_section")
    check("无段头 → no_section",
          cv.parse_curvature_txt("BASIS=xxx\nNELEM=0\n")["status"] == "no_section")
    check("BASIS 错 → bad_basis",
          cv.parse_curvature_txt(_txt(basis="other"))["status"] == "bad_basis")
    check("ERROR=no_curvature_elements → no_curvature",
          cv.parse_curvature_txt(
              _txt(error_line="no_curvature_elements"))["status"] == "no_curvature")
    check("缺 NELEM → bad_nelem",
          cv.parse_curvature_txt(
              f"{cv.SECTION}\nBASIS={cv.BASIS}\n"
              "1 K0Y=0 K0Z=0.001 EIY=3.45e10 EIZ=3.45e10 M0Y=0 M0Z=3.45e7\n"
          )["status"] == "bad_nelem")
    check("NELEM 与行数不符 → nelem_mismatch",
          cv.parse_curvature_txt(_txt(nelem_line="NELEM=3"))["status"]
          == "nelem_mismatch")
    check("缺 M0Z → missing_key",
          cv.parse_curvature_txt(_txt(
              rows="1 K0Y=0 K0Z=0.001 EIY=3.45e10 EIZ=3.45e10 M0Y=0"))["status"]
          == "missing_key")
    check("M0Z 与 EIZ*K0Z 不一致 → moment_mismatch",
          cv.parse_curvature_txt(_txt(
              rows="1 K0Y=0 K0Z=0.001 EIY=3.45e10 EIZ=3.45e10 M0Y=0 M0Z=1.0"))["status"]
          == "moment_mismatch")
    check("乱行（无键值）→ bad_row",
          cv.parse_curvature_txt(_txt(nelem_line="NELEM=0", rows="1 garbage"))["status"]
          == "bad_row")

    # ---------------- 4. 只解析不重算 ----------------
    src = (HERE / "curvature_3d.py").read_text(encoding="utf-8")
    check("不引求解器（无 solve_3d / solver_3d）",
          "solve_3d" not in src and "solver_3d" not in src)
    check("不引数值库（无 numpy）", "import numpy" not in src)
    check("不从位移反推 κ0（不解析位移段、不取 U）",
          "节点位移" not in src and "U[" not in src and "disp" not in src)
    check("M0 互核有显式容差 1e-9", "1e-9" in src)
    check("解析器有唯一入口 parse_curvature_txt",
          "def parse_curvature_txt" in src)

    # ---------------- 5. 状态与表格 ----------------
    for st in ("ok", "no_section", "bad_basis", "no_curvature", "bad_nelem",
               "nelem_mismatch", "bad_row", "missing_key", "moment_mismatch"):
        check(f"状态 {st} 有中文说明", st in cv._STATUS_TEXT)
    check("status_text(ok) 非空", bool(cv.status_text({"status": "ok"})))
    check("status_text(None) 给未请求", "未请求" in cv.status_text(None))
    t = cv.curvature_table(d)
    check("ok 状态出表", t is not None)
    check("表行数与 NELEM 一致", t is not None and len(t["rows"]) == 1)
    check("表带来源与口径", t is not None and t["basis"] == cv.BASIS
          and "--curvature-detail" in t["source"])
    check("表带边界说明（不 ANSYS 对拍）",
          t is not None and "ANSYS" in t["note"])
    check("非 ok 状态不出表",
          cv.curvature_table({"status": "bad_basis"}) is None)

    # ---------------- 6. 文档与 FE 同源 ----------------
    spec = (HERE / "B类后续项接入规格.md").read_text(encoding="utf-8")
    check("规格 §4.1 本轮实现范围在位", "### 4.1 本轮实现范围" in spec)
    check("规格 §4.1 含 K0Y/K0Z", "K0Y=" in spec and "K0Z=" in spec)
    check("规格 §6 第 28 条在位", "第二十六次 **B5 无应力曲率" in spec)
    check("规格如实登记未覆盖（共转列式）", "CRElemData" in spec)

    hpp = FE_ROOT / "fem_3d.hpp"
    if hpp.exists():
        t_hpp = hpp.read_text(encoding="utf-8", errors="replace")
        check("FE Element3D 含 k0y/k0z", "double k0y = 0.0, k0z = 0.0;" in t_hpp)
        check("FE 解析 K0Y=/K0Z=", '"K0Y="' in t_hpp and '"K0Z="' in t_hpp)
        rpt = FE_ROOT / "example_3d_report.cpp"
        t_rpt = rpt.read_text(encoding="utf-8", errors="replace")
        check("FE 有 --curvature-detail", '"--curvature-detail"' in t_rpt)
        check("FE 段头 [UNSTRESSED_CURVATURE] 一致",
              cv.SECTION in t_rpt)
        check("FE 无曲率写显式 ERROR",
              "ERROR=no_curvature_elements" in t_rpt)
    else:
        check("FEM-Eigen 目录可达", False, str(hpp))

    ok = sum(1 for r in RESULTS if r[0])
    for passed, name, detail in RESULTS:
        if not passed:
            print(f"  FAIL  {name}" + (f"  {detail}" if detail else ""))
    print()
    print("=" * 62)
    print(f"  {ok}/{len(RESULTS)} 通过")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
