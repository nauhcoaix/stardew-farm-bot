"""白名单 7：地形寻路与探索候选计算（纯函数，无记忆对象依赖）。

* :mod:`grid` — 格网合并、未知邻格
* :mod:`pathing` — 四邻域 BFS 与路径还原
* :mod:`candidates` — 前沿/交互候选与候选集合说明
* :mod:`regions` — 地区连接链
"""

from .candidates import (
    INTERACTIVE_KINDS,
    candidates,
    frontier_candidates,
    interaction_candidates,
    placement_candidates,
    visited_cells,
)
from .grid import Cell, build_grid, tile_key, unknown_neighbors
from .pathing import manhattan, path_to, reachable
from .regions import region_route

__all__ = [
    "Cell",
    "INTERACTIVE_KINDS",
    "build_grid",
    "candidates",
    "frontier_candidates",
    "interaction_candidates",
    "manhattan",
    "path_to",
    "placement_candidates",
    "reachable",
    "region_route",
    "tile_key",
    "unknown_neighbors",
    "visited_cells",
]
