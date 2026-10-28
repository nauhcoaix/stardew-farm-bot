"""探索候选与交互候选计算。

来源：``stardew_valley_dfy/bot/exploration.py`` 的 ``candidates``，改为显式输入：
格网、起点、访问记录、观察范围、实体列表都由调用方提供，不读记忆对象。

模块不替 Agent 决定目的地，也不执行移动；候选集合必须说明截断与排序依据，
让模型知道“看到的候选不是世界的全部”。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..observation.identity import semantic, target_id
from .grid import Cell, build_grid, tile_key, unknown_neighbors
from .pathing import manhattan, reachable

#: 值得交互的实体种类（不含普通地块）
INTERACTIVE_KINDS = ("npc", "object", "action", "door", "exit", "bed", "furniture")


def _frontier_points(
    cells: Mapping[Cell, bool],
    start: Cell,
    *,
    connected: Mapping[Cell, Cell | None],
    visited: frozenset[Cell],
    scope: Mapping[str, Any] | None,
) -> list[dict]:
    points = []
    for point, passable in cells.items():
        if not passable or point == start:
            continue
        unknown = unknown_neighbors(cells, point, scope)
        if not unknown or point not in connected:
            continue
        points.append(
            {
                "id": "frontier:%s:%s" % point,
                "mode": "path",
                "x": point[0],
                "y": point[1],
                "unknown_neighbors": unknown,
                "visited": point in visited,
                "distance": manhattan(start, point),
            }
        )
    points.sort(key=lambda row: (row["visited"], -row["unknown_neighbors"], row["distance"]))
    return points


def placement_candidates(
    entities: Iterable[Mapping[str, Any]],
    player: Mapping[str, Any] | None,
    *,
    limit: int = 3,
) -> list[dict]:
    """背包满时可放箱子的相邻空格（1～2 格内、无对象/地形/动作/水源）。"""
    here = dict(player or {}).get("position") or {}
    ox, oy = here.get("x"), here.get("y")
    if not isinstance(ox, int) or not isinstance(oy, int):
        return []
    rows: list[dict] = []
    for entity in entities:
        if entity.get("kind") != "tile" or entity.get("passable") is not True:
            continue
        if entity.get("refillable") or entity.get("objName") or entity.get("terrain") or entity.get("action"):
            continue
        tile = entity.get("tile") or {}
        x, y = tile.get("x"), tile.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            continue
        distance = abs(x - ox) + abs(y - oy)
        if not 1 <= distance <= 2:
            continue
        rows.append(
            {
                "id": "placement:%d:%d" % (x, y),
                "mode": "inventory",
                "op": "place_chest",
                "tx": x,
                "ty": y,
                "tile": {"x": x, "y": y},
                "name": "可放箱子的空格",
                "distance": distance,
            }
        )
    rows.sort(key=lambda row: (row["distance"], row["ty"], row["tx"]))
    return rows[:limit]


def frontier_candidates(
    cells: Mapping[Cell, bool],
    start: Cell,
    *,
    visited: Iterable[Cell] = (),
    scope: Mapping[str, Any] | None = None,
    limit: int = 16,
    spacing: int = 4,
) -> dict:
    """可达且邻接未知地块的前沿候选；同一片边缘只保留分散的点。"""
    connected = reachable(cells, start)
    points = _frontier_points(cells, start, connected=connected, visited=frozenset(visited), scope=scope)
    selected: list[dict] = []
    for point in points:
        if all(manhattan((point["x"], point["y"]), (other["x"], other["y"])) >= spacing for other in selected):
            selected.append(point)
        if len(selected) >= limit:
            break
    return {
        "items": selected,
        "truncated": len(selected) < len(points),
        "considered": len(points),
        "order": "先未访问、再未知邻格多、再距离近；同一片边缘至少间隔 %d 格" % spacing,
        "more": "候选被截断时，换更大 limit 或用已知坐标直接指定目标",
    }


def interaction_candidates(
    entities: Iterable[Mapping[str, Any]],
    location: str | None,
    *,
    kinds: Iterable[str] = INTERACTIVE_KINDS,
    limit: int = 16,
) -> list[dict]:
    """现场可交互实体候选（NPC、对象、动作格、门、出口、床、家具）。"""
    allowed = set(kinds)
    rows = []
    for entity in entities:
        if entity.get("kind") not in allowed:
            continue
        rows.append(
            {
                "id": target_id(location, entity),
                "mode": "interaction",
                "kind": entity.get("kind"),
                "ref": entity.get("ref"),
                "tile": entity.get("tile"),
                "target": semantic(entity),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def candidates(
    *,
    observed: Iterable[Mapping[str, Any]] = (),
    known: Iterable[Mapping[str, Any]] = (),
    start: Cell,
    entities: Iterable[Mapping[str, Any]] = (),
    location: str | None = None,
    visited: Iterable[Cell] = (),
    scope: Mapping[str, Any] | None = None,
    limit: int = 16,
    spacing: int = 4,
) -> dict:
    """把地形前沿与交互对象合成一份候选集合（默认各取 ``limit`` 个）。"""
    cells = build_grid(observed, known, extra_passable=[start])
    frontier = frontier_candidates(
        cells, start, visited=visited, scope=scope, limit=limit, spacing=spacing
    )
    interactions = interaction_candidates(entities, location, limit=limit)
    items = list(frontier["items"]) + interactions
    return {
        "items": items,
        "frontier": frontier,
        "interaction_count": len(interactions),
        "truncated": frontier["truncated"] or len(items) >= 2 * limit,
        "order": frontier["order"] + "；交互对象按快照顺序",
        "more": frontier["more"],
        "scope": dict(scope or {}),
    }


def visited_cells(known: Iterable[Mapping[str, Any]]) -> set[Cell]:
    """从历史地形记录里取访问过的格子。"""
    cells = set()
    for row in known:
        if not row.get("visited"):
            continue
        key = tile_key(row.get("tile"))
        if key is not None:
            cells.add(key)
    return cells
