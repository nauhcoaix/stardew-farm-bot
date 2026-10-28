"""地形格网构建：只使用调用方明确提供的观察与已知地形。

来源：``stardew_valley_dfy/bot/exploration.py`` 的 ``grid``，已去掉对
``MemoryStore`` 的依赖。通行性未知的地块不写进格网，因此不会被当作“不可通行”，
只会在前沿计算里表现为“未知邻格”。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

Cell = tuple[int, int]


def tile_key(tile: Mapping[str, Any] | None) -> Cell | None:
    """把 ``{"x": .., "y": ..}`` 转成格网坐标；不完整时返回 ``None``。"""
    if not isinstance(tile, Mapping):
        return None
    x, y = tile.get("x"), tile.get("y")
    if x is None or y is None:
        return None
    try:
        return int(x), int(y)
    except (TypeError, ValueError):
        return None


def build_grid(
    observed: Iterable[Mapping[str, Any]] = (),
    known: Iterable[Mapping[str, Any]] = (),
    *,
    extra_passable: Iterable[Cell] = (),
) -> dict[Cell, bool]:
    """合并本次观察与历史地形；本次观察优先于历史值（现场变化以现在为准）。

    ``observed`` / ``known`` 的每一项都是 ``{"tile": {"x","y"}, "passable": bool}``。
    缺少 ``passable`` 的项被忽略（表示未知，而不是禁止通行）。
    """
    cells: dict[Cell, bool] = {}
    for row in list(known) + list(observed):
        key = tile_key(row.get("tile"))
        if key is None or "passable" not in row:
            continue
        cells[key] = bool(row["passable"])
    for point in extra_passable:
        cells[(int(point[0]), int(point[1]))] = True
    return cells


def unknown_neighbors(cells: Mapping[Cell, bool], point: Cell, scope: Mapping[str, Any] | None = None) -> int:
    """四邻域里既没观察过、又落在观察范围内的格子数。"""
    width = (scope or {}).get("width")
    height = (scope or {}).get("height")
    count = 0
    for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
        nx, ny = point[0] + dx, point[1] + dy
        if (nx, ny) in cells:
            continue
        if width is not None and not 0 <= nx < int(width):
            continue
        if height is not None and not 0 <= ny < int(height):
            continue
        count += 1
    return count
