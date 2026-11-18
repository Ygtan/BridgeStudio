"""block_numbering.py —— 紧凑分块编号分配器。

契约见 ``X20_接入约定.md`` §5.1（2026-11-18 全量切换，替换原「建造器
固定大段」方案）：

- 同一模型内，节点与单元各自独立按组件组顺序编号；
- 第一组从 1 起编；
- 后续组按本组数量判定类型：
  - 数量 ≥ 50（主要构件）：起点 = ``(已用最大号 // 100 + 1) * 100 + 1``；
  - 数量 < 50（非主要构件）：起点 = ``(已用最大号 // 10 + 1) * 10 + 1``；
- 组内从起点连续编号；「已用最大号」是此前所有组分配过的最大编号，
  保证单调递增、不回退。

典型序列：
- 首组 1~120（≥50，主要）→ 下一主要组 201~350；
- 已用到 412，下一非主要组（10 个）→ 421~430。

**这是唯一分配入口**：建造器不得再散写 ``1000+i`` / ``3000+j`` /
``9000+t`` 之类的大段常量。节点与单元各自持有一个实例，互不影响。

编号只是标签，不进刚度矩阵与荷载向量；改号段只改变 `.dat` 字节，
不改变任何数值结果（见 FEM-Eigen/CHANGELOG.md 数值依据）。
"""

from __future__ import annotations


class BlockNumbering:
    """按 §5.1 规则为组件组分配紧凑编号。

    用法：:

        nn = BlockNumbering()
        deck_ids = nn.allocate(n_deck + 1)   # 主梁节点
        arch_ids = nn.allocate(n_arch + 1)   # 拱肋节点
        top_ids = nn.allocate(2)             # 塔顶锚点

    每次 ``allocate`` 返回本组的新编号列表（升序、连续），并更新内部
    ``max_id``。各组之间不重叠、单调递增。
    """

    #: 主要构件的最小数量门槛（§5.1：数量 < 50 为非主要构件）
    MAJOR_MIN = 50

    def __init__(self) -> None:
        self._max = 0
        self._groups: list[tuple[int, int, int]] = []   # (数量, 起点, 终点)

    @property
    def max_id(self) -> int:
        """到目前为止已分配的最大编号（0 表示尚未分配）。"""
        return self._max

    @property
    def groups(self) -> list[tuple[int, int, int]]:
        """各组摘要：(数量, 起点, 终点)，供守卫/调试使用。"""
        return list(self._groups)

    def allocate(self, count: int, *, major: bool | None = None) -> list[int]:
        """为一组 ``count`` 个构件分配新编号。

        ``major`` 默认按数量判定（≥50 为主要构件）；显式传入可覆盖，
        供「主梁这类语义主要构件即使节点少也希望按百位跳转」的场景使用。
        """
        if count < 0:
            raise ValueError(f"组件数量不能为负：{count}")
        if count == 0:
            return []

        is_major = (count >= self.MAJOR_MIN) if major is None else major
        if self._max == 0:
            start = 1
        elif is_major:
            start = (self._max // 100 + 1) * 100 + 1
        else:
            start = (self._max // 10 + 1) * 10 + 1

        ids = list(range(start, start + count))
        self._max = ids[-1]
        self._groups.append((count, start, self._max))
        return ids
