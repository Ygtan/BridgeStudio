"""2D `.dat` 紧凑导出唯一口径层（第十七次，纯 BridgeStudio 侧 / FE 仓零改动）。

背景
----
FEM-Eigen 的 2D 内核 ``fem_2d.hpp::detail::expandLines``（553–635 行）早已支持
``*FILL`` / ``*DO … *ENDDO``（``input_format.md`` §2），但 ``BridgeSpec.to_dat()``
一直逐行写出节点与单元；3D 的 ``ModelSpec3D.to_dat(compact=True)`` 有压缩器，
两条链按 ``X20_接入约定.md`` §3.4 正交，不能把 3D 压缩器搬来 import。本模块是
2D 自己的实现，只借「编号规律可见」这个策略，不借代码。

与 3D 压缩器的三处不同（不是照抄）
----------------------------------
1. 2D 只有 x/y，且 ``to_dat()`` 写 ``%.6f``（6 位小数）；3D 写 ``%.10g`` 与 x/y/z。
2. 内核 ``*FILL`` 的中间点是 ``setprecision(15)`` **线性插值后重新写出**，不是
   逐行文本的原样复现：0→10 m 分 3 段时插值给 ``3.3333333333333335``，
   逐行版是 ``3.333333``，差 3.3e-7 m。若沿用 3D 的 ``1e-9`` 等距容差，
   紧凑版会真实改动节点坐标。因此本模块对每个候选 ``*FILL`` 段做
   **可逆性自检**：用 :func:`expand_lines`（按 fem_2d.hpp 语义写的 Python
   参考展开器）把片段展开，解析出的节点字典必须与逐行版逐字段完全相等，
   否则整段退回逐行。压不动就逐行，绝不静默近似。
3. 2D 的 ``substituteLoopVar`` 只认 ``VAR`` / ``k*VAR`` / ``VAR±m`` /
   ``k*VAR±m``，不支持括号、除法、嵌套循环（``input_format.md`` §2.2）。
   :func:`expr_2d` 只生成这四种形式，并对每个候选 ``*DO`` 段同样做展开自检，
   展开出的 ``*EN`` 行必须与逐行版逐字相同。

契约
----
* 默认关闭：``BridgeSpec.to_dat(compact=False)``；默认 ``.dat`` / ``.result`` /
  默认 HTML 逐位不变，12 格 ``DEFAULT_DAT_SHA`` 与 FE 37 个黄金基线都不刷新。
* 本模块只做**文本层压缩**，不做任何力学近似；紧凑版与逐行版必须是同一个模型。
* 不 import ``model_spec_3d`` / ``export_formats`` / ``to_canonical`` / ``mct_io``，
  不依赖 X20 归档目录（§3.4、§9）。3D 唯一转换点签名一字不动。
"""

from __future__ import annotations

SOURCE = "BridgeSpec.nodes/elements → *FILL / *DO（fem_2d.hpp::expandLines）"
BASIS = "2d_dat_compact_optin"

#: 等距判据的容差（与 3D 同源，随坐标量级放大）。它只是**切分**依据，
#: 是否真的能压由可逆性自检决定，容差本身不构成等价性判据。
FILL_TOL = 1e-9
MIN_FILL_POINTS = 3
MIN_LOOP_ITEMS = 3

DIAGNOSTIC = (
    "2D .dat 采用紧凑写法（opt-in）：连续等距节点段写 *FILL、规则单元写 "
    "*DO…*ENDDO，由 FE 内核 preprocess 展开；每个宏都先做可逆性自检，"
    "展开后的模型与逐行版逐字段相同，不做力学近似。默认关闭时 .dat 逐位不变。")

BOUNDARY_NOTE = (
    "2D 紧凑导出只是文本层压缩，不是新力学能力：内核 preprocess 原样展开 "
    "*FILL / *DO 后与逐行版是同一个模型；不做垂度、共转、Ernst 或任何近似。"
    "默认关闭，能力矩阵仍按原口径标注，不等于 3D 的 compact 链。")

# (id, x, y) / (id, n1, n2)：只吃纯元组，不依赖 BridgeSpec，避免循环 import。
NodeLine = tuple[int, float, float]
ElemLine = tuple[int, int, int]


# ---------------------------------------------------------------- 写出格式

def node_line(n: NodeLine) -> str:
    """与 ``BridgeSpec.to_dat()`` 的逐行节点写法逐字一致。"""
    return f"{n[0]} {n[1]:.6f} {n[2]:.6f}"


def elem_line(e: ElemLine) -> str:
    """与 ``BridgeSpec.to_dat()`` 的逐行单元写法逐字一致。"""
    return f"*EN,{e[0]},{e[1]},{e[2]}"


def _written(v: float) -> float:
    """内核真正读到的坐标：``%.6f`` 文本再 ``>> double``。"""
    return float(f"{v:.6f}")


# ---------------------------------------------------------------- 内核语义模拟

def _is_ident(c: str) -> bool:
    return c.isalnum() or c == "_"


def substitute_loop_var(line: str, var: str, value: int) -> str:
    """按 ``fem_2d.hpp::substituteLoopVar`` 逐字符模拟变量替换。

    只支持 VAR、VAR±m、k*VAR、k*VAR±m；其余文本原样保留。大小写不敏感。
    """
    low, vlow = line.lower(), var.lower()
    out = ""
    i = 0
    while i < len(line):
        replaced = False
        if low.startswith(vlow, i):
            left_ok = i == 0 or not _is_ident(line[i - 1])
            j0 = i + len(vlow)
            right_ok = j0 == len(line) or not _is_ident(line[j0])
            if left_ok and right_ok:
                coeff, start = 1, i
                if i >= 2 and line[i - 1] == "*":
                    d = i - 1
                    while d > 0 and line[d - 1].isdigit():
                        d -= 1
                    if d < i - 1 and (d == 0 or not _is_ident(line[d - 1])):
                        coeff = int(line[d:i - 1])
                        start = d
                offset, j = 0, j0
                if j < len(line) and line[j] in "+-":
                    q = j + 1
                    if q < len(line) and line[q].isdigit():
                        while q < len(line) and line[q].isdigit():
                            q += 1
                        m = int(line[j + 1:q])
                        offset = m if line[j] == "+" else -m
                        j = q
                if start < i:
                    # 系数 k* 已经逐字符写进 out，先撤销。
                    out = out[:len(out) - (i - start)]
                out += str(coeff * value + offset)
                i = j
                replaced = True
        if not replaced:
            out += line[i]
            i += 1
    return out


def expand_lines(text: str) -> list[str]:
    """按 ``fem_2d.hpp::detail::expandLines / preprocess`` 展开宏。

    这是**守卫用参考实现**，不是求解器：只复现 2D 预处理这一步的文本展开。
    与 C++ 一致的三点：``*DO`` 的 end 包含且 step 可省略；``*FILL`` 端点必须
    已显式写出、中间点按 ``(id-a)/(b-a)`` 线性插值并以 ``setprecision(15)``
    写出；展开出来的行不再二次参与 ``nodeCoord`` 收集（故循环体内不能有宏）。
    """
    lines = text.splitlines()
    out: list[str] = []
    node_coord: dict[int, tuple[float, float]] = {}
    in_node = False
    idx = 0
    while idx < len(lines):
        raw = lines[idx]
        t = raw.strip()
        up = t.upper()
        if up.startswith("*DO") and (len(t) == 3 or t[3].isspace()):
            parts = t.split()
            var = parts[1]
            start, endv = int(parts[2]), int(parts[3])
            step = int(parts[4]) if len(parts) > 4 else 1
            if step == 0:
                raise ValueError("*DO 步长不能为 0")
            body_start, body_end, depth = idx + 1, idx + 1, 1
            while body_end < len(lines):
                bu = lines[body_end].strip().upper()
                if bu.startswith("*DO") and (len(bu) == 3 or bu[3].isspace()):
                    depth += 1
                elif bu == "*ENDDO":
                    depth -= 1
                if depth == 0:
                    break
                body_end += 1
            if depth != 0:
                raise ValueError("*DO 缺少匹配的 *ENDDO")
            v = start
            while (v <= endv) if step > 0 else (v >= endv):
                for k in range(body_start, body_end):
                    out.append(substitute_loop_var(lines[k], var, v))
                v += step
            idx = body_end + 1
        elif up.startswith("*FILL") and (len(t) == 5 or t[5].isspace()):
            parts = t.split()
            a, b = int(parts[1]), int(parts[2])
            if a not in node_coord or b not in node_coord:
                raise ValueError("*FILL 端点节点不存在")
            if b <= a:
                raise ValueError("*FILL 要求第二个节点号大于第一个")
            x1, y1 = node_coord[a]
            x2, y2 = node_coord[b]
            for nid in range(a + 1, b):
                f = (nid - a) / (b - a)
                out.append(f"{nid} {x1 + f * (x2 - x1):.15g} "
                           f"{y1 + f * (y2 - y1):.15g}")
            idx += 1
        else:
            if t and t[0] == "*":
                in_node = (up == "*NODE")
            elif in_node and t and t[0] not in "#!":
                parts = t.split()
                if len(parts) >= 3:
                    try:
                        node_coord[int(parts[0])] = (float(parts[1]),
                                                     float(parts[2]))
                    except ValueError:
                        pass
            out.append(raw)
            idx += 1
    return out


def parse_nodes(lines: list[str]) -> dict[int, tuple[float, float]]:
    """从展开后的普通行里取 ``*NODE`` 段（模型级，不是文本级）。"""
    d: dict[int, tuple[float, float]] = {}
    inside = False
    for raw in lines:
        t = raw.strip()
        if not t:
            continue
        if t.startswith("*"):
            inside = (t.upper().split(",")[0] == "*NODE")
            continue
        if inside and t[0] not in "#!":
            p = t.split()
            if len(p) >= 3:
                try:
                    d[int(p[0])] = (float(p[1]), float(p[2]))
                except ValueError:
                    pass
    return d


def parse_elements(lines: list[str]) -> list[ElemLine]:
    """从展开后的普通行里取所有 ``*EN,``（按 id 排序，顺序不作为线索）。"""
    out: list[ElemLine] = []
    for raw in lines:
        t = raw.strip()
        if t.upper().startswith("*EN,"):
            p = t[4:].split(",")
            if len(p) >= 3:
                try:
                    out.append((int(p[0]), int(p[1]), int(p[2])))
                except ValueError:
                    pass
    return sorted(out)


def equivalent(full_text: str, compact_text: str) -> tuple[bool, str]:
    """紧凑版与逐行版是否解析出**同一个模型**。

    两份文本都先过参考展开器（逐行版没有宏，展开等于自身），再比节点字典与
    单元列表。文本层面必然不同（``*DO``/``*ENDDO`` 消失、``*FILL`` 变成
    15 位精度的数值行），所以只做模型级对拍，不做文本对拍。
    """
    f_lines = expand_lines(full_text)
    c_lines = expand_lines(compact_text)
    fn, cn = parse_nodes(f_lines), parse_nodes(c_lines)
    fe, ce = parse_elements(f_lines), parse_elements(c_lines)
    if fn != cn:
        bad = [k for k in set(fn) | set(cn) if fn.get(k) != cn.get(k)]
        return False, f"节点不等价：{len(bad)} 个，例 {bad[:3]}"
    if fe != ce:
        bad = [e for e in fe if e not in ce]
        return False, f"单元不等价：逐行 {len(fe)} / 紧凑 {len(ce)}，例 {bad[:3]}"
    return True, f"{len(fn)} 节点 / {len(fe)} 单元逐字段相同"


# ---------------------------------------------------------------- 可逆性自检

def fill_reversible(seg: list[NodeLine]) -> bool:
    """候选 ``*FILL`` 段是否可逆：展开后节点字典必须与逐行版完全相同。

    这是本模块与 3D 压缩器最重要的差别。``0→10 m`` 分 3 段这类会产生
    3.3e-7 m 漂移的段在这里返回 False，整段退回逐行。
    """
    a, b = seg[0][0], seg[-1][0]
    frag = "\n".join(["*NODE", node_line(seg[0]), node_line(seg[-1]),
                      f"*FILL {a} {b}"])
    got = parse_nodes(expand_lines(frag))
    want = parse_nodes(["*NODE"] + [node_line(n) for n in seg])
    return got == want


def loop_reversible(seg: list[ElemLine], e1: str, e2: str) -> bool:
    """候选 ``*DO`` 段是否可逆：展开出的 ``*EN`` 行必须与逐行版逐字相同。"""
    first, last = seg[0][0], seg[-1][0]
    frag = "\n".join([f"*DO I {first} {last}",
                      f"*EN,I,{e1},{e2}", "*ENDDO"])
    return parse_elements(expand_lines(frag)) == [tuple(t) for t in seg]


def _step_close(a: NodeLine, b: NodeLine, c: NodeLine) -> bool:
    """三步同长判据（用内核实际读到的 ``%.6f`` 值，容差随量级放大）。"""
    ax, bx, cx = _written(a[1]), _written(b[1]), _written(c[1])
    ay, by, cy = _written(a[2]), _written(b[2]), _written(c[2])
    scale = max(1.0, abs(ax), abs(bx), abs(cx), abs(ay), abs(by), abs(cy))
    tol = FILL_TOL * scale
    return (abs((bx - ax) - (cx - bx)) <= tol
            and abs((by - ay) - (cy - by)) <= tol)


def consecutive_runs(ids: list[int]) -> list[tuple[int, int]]:
    """升序整数序列切成连续段（闭区间）。"""
    out: list[tuple[int, int]] = []
    if not ids:
        return out
    start = prev = ids[0]
    for x in ids[1:]:
        if x == prev + 1:
            prev = x
            continue
        out.append((start, prev))
        start = prev = x
    out.append((start, prev))
    return out


# ---------------------------------------------------------------- 节点压缩

def compress_nodes(nodes: list[NodeLine]) -> list[str]:
    """连续 id 且严格等距、且通过 :func:`fill_reversible` 的段写 ``*FILL``。

    连续 id 段内再按步长变化切分（边跨/主跨步长不同时各自压缩）；相邻子段
    共用转折点，只写一次。任何一段自检不过，该段逐行写出，不影响其它段。
    """
    ns = sorted(nodes, key=lambda n: n[0])
    lines: list[str] = []
    i = 0
    while i < len(ns):
        group = [ns[i]]
        j = i + 1
        while j < len(ns) and ns[j][0] == group[-1][0] + 1:
            group.append(ns[j])
            j += 1
        cuts = [0]
        for k in range(1, len(group) - 1):
            if not _step_close(group[k - 1], group[k], group[k + 1]):
                cuts.append(k)
        cuts.append(len(group) - 1)
        prev_id: int | None = None
        for a, b in zip(cuts, cuts[1:]):
            seg = group[a:b + 1]
            if len(seg) >= MIN_FILL_POINTS and fill_reversible(seg):
                if seg[0][0] != prev_id:
                    lines.append(node_line(seg[0]))
                lines.append(node_line(seg[-1]))
                lines.append(f"*FILL {seg[0][0]} {seg[-1][0]}")
                prev_id = seg[-1][0]
            else:
                for g in seg:
                    if g[0] != prev_id:
                        lines.append(node_line(g))
                    prev_id = g[0]
        i = j
    return lines


# ---------------------------------------------------------------- 单元压缩

def fit_linear(values: list[int], ids: list[int]) -> tuple[int, int] | None:
    """拟合 ``values = a*id + b``（a ≥ 0），要求每点严格命中。"""
    if len(values) < 2:
        return None
    a = values[1] - values[0]
    if a < 0:
        return None
    b = values[0] - a * ids[0]
    if all(v == a * i + b for v, i in zip(values, ids)):
        return a, b
    return None


def expr_2d(coeff: int, const: int) -> str | None:
    """生成 2D 内核认得的索引表达式；超出白名单返回 None（不压）。"""
    if coeff < 0:
        return None
    if coeff == 0:
        # 常数节点号必须为正；负数不是合法表达式，也不该拿去解析。
        return str(const) if const > 0 else None
    body = "I" if coeff == 1 else f"{coeff}*I"
    if const == 0:
        return body
    return f"{body}+{const}" if const > 0 else f"{body}-{abs(const)}"


def compress_elements(items: list[ElemLine]) -> list[str]:
    """连续 id 且两端节点号可拟合成 ``a*id+b`` 的段写 ``*DO``。

    贪心取**最长可拟合**的连续 id 段（与 X20 导出器策略一致，见
    ``X20_接入约定.md`` §7 A0；只借策略，不改任何签名）。每个候选段都做
    :func:`loop_reversible` 展开自检，不过就退回单行。
    """
    loops = sorted(items, key=lambda t: t[0])
    out: list[str] = []
    k = 0
    while k < len(loops):
        best: tuple[int, int, str, str] | None = None
        best_end = k
        for m in range(k + MIN_LOOP_ITEMS, len(loops) + 1):
            seg = loops[k:m]
            ids = [t[0] for t in seg]
            if any(ids[p + 1] != ids[p] + 1 for p in range(len(ids) - 1)):
                break
            f1 = fit_linear([t[1] for t in seg], ids)
            f2 = fit_linear([t[2] for t in seg], ids)
            if f1 is None or f2 is None:
                continue
            e1, e2 = expr_2d(*f1), expr_2d(*f2)
            if e1 is None or e2 is None or not loop_reversible(seg, e1, e2):
                continue
            best = (ids[0], ids[-1], e1, e2)
            best_end = m
        if best is not None:
            out.append(f"*DO I {best[0]} {best[1]}")
            out.append(f"*EN,I,{best[2]},{best[3]}")
            out.append("*ENDDO")
            k = best_end
        else:
            out.append(elem_line(loops[k]))
            k += 1
    return out
