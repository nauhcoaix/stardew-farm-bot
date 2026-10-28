"""已知地形上的四邻域寻路。

来源：``stardew_valley_dfy/bot/exploration.py`` 的 ``reachable`` / ``path_to``。
不迁移移动执行、目的地选择或固定路线表：这里只返回坐标序列。
"""

from __future__ import annotations

from collections import deque
from typing import Mapping

from .grid import Cell

NEIGHBORS = ((0, -1), (1, 0), (0, 1), (-1, 0))


def reachable(cells: Mapping[Cell, bool], start: Cell, limit: int = 20000) -> dict[Cell, Cell | None]:
    """从 ``start`` 可达的格网及其前驱；``limit`` 防止大地图展开过久。"""
    if not cells.get(start):
        cells = dict(cells)
        cells[start] = True
    prior: dict[Cell, Cell | None] = {start: None}
    queue = deque([start])
    while queue and len(prior) <= limit:
        current = queue.popleft()
        for dx, dy in NEIGHBORS:
            nxt = (current[0] + dx, current[1] + dy)
            if nxt in prior or not cells.get(nxt):
                continue
            prior[nxt] = current
            queue.append(nxt)
    return prior


def path_to(cells: Mapping[Cell, bool], start: Cell, goal: Cell, limit: int = 20000) -> list[Cell] | None:
    """``start`` 走到 ``goal`` 的坐标序列（不含起点）；不可达返回 ``None``。"""
    prior = reachable(cells, start, limit=limit)
    if goal not in prior:
        return None
    path: list[Cell] = []
    while goal != start:
        path.append(goal)
        previous = prior[goal]
        if previous is None:
            break
        goal = previous
    return list(reversed(path))


def manhattan(a: Cell, b: Cell) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])
