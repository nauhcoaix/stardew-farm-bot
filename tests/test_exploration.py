"""白名单 7：格网、寻路、前沿候选与地区连接。"""

from __future__ import annotations

from stardew_agent.exploration import (
    build_grid,
    candidates,
    frontier_candidates,
    interaction_candidates,
    path_to,
    reachable,
    region_route,
    visited_cells,
)
from tests.helpers import entity


def rows(points, *, passable=True):
    return [{"tile": {"x": x, "y": y}, "passable": passable} for x, y in points]


def test_本次观察优先于历史地形():
    cells = build_grid(rows([(1, 1)], passable=True), rows([(1, 1)], passable=False))
    assert cells[(1, 1)] is True
    cells = build_grid(rows([(2, 2)], passable=False), rows([(2, 2)], passable=True))
    assert cells[(2, 2)] is False


def test_通行性缺失视为未知不进入格网():
    cells = build_grid([{"tile": {"x": 2, "y": 2}}])
    assert (2, 2) not in cells


def test_起点强制可通行():
    cells = build_grid([], [], extra_passable=[(5, 5)])
    assert cells[(5, 5)] is True


def test_寻路沿四邻域返回坐标序列():
    cells = {(x, 0): True for x in range(4)}
    assert path_to(cells, (0, 0), (3, 0)) == [(1, 0), (2, 0), (3, 0)]
    assert path_to(cells, (0, 0), (3, 3)) is None
    assert (3, 0) in reachable(cells, (0, 0))


def test_前沿候选优先未访问且暴露截断说明():
    cells = {(x, 0): True for x in range(10)}
    border = {(0, 1): True, (9, 1): True}
    cells.update(border)
    result = frontier_candidates(cells, (4, 0), visited={(4, 0)}, limit=2, spacing=1)
    assert result["items"]
    assert all(item["mode"] == "path" for item in result["items"])
    assert "order" in result and "more" in result
    assert isinstance(result["truncated"], bool)


def test_交互候选保留临时ref与语义目标():
    entities = [
        entity("@s1:e1", "npc", "Abigail", 3, 3),
        entity("@s1:e2", "object", "箱子", 4, 4),
        entity("@s1:e3", "tile", "地块", 5, 5),
    ]
    result = interaction_candidates(entities, "Farm")
    kinds = {row["kind"] for row in result}
    assert kinds == {"npc", "object"}
    assert all(row["ref"].startswith("@s1:") for row in result)


def test_候选集合报告范围与交互数量():
    entities = [entity("@s1:e1", "exit", "出口 → FarmHouse", 11, 10)]
    result = candidates(
        observed=rows([(x, 10) for x in range(8, 13)]),
        known=[],
        start=(10, 10),
        entities=entities,
        location="Farm",
        scope={"width": 40, "height": 30},
    )
    assert result["interaction_count"] == 1
    assert {item["mode"] for item in result["items"]} <= {"path", "interaction"}
    assert result["scope"]["width"] == 40


def test_访问记录来自历史地形():
    known = [
        {"tile": {"x": 1, "y": 1}, "passable": True, "visited": True},
        {"tile": {"x": 2, "y": 2}, "passable": True, "visited": False},
    ]
    assert visited_cells(known) == {(1, 1)}


def test_地区连接只走已记录有向边():
    links = [
        {"from": "FarmHouse", "to": "Farm", "target": "门口"},
        {"from": "Farm", "to": "Town", "target": "东侧"},
    ]
    route = region_route(links, "FarmHouse", "Town")
    assert route is not None and [step["to"] for step in route] == ["Farm", "Town"]
    assert region_route(links, "Town", "Farm") is None
    assert region_route(links, "Farm", "Farm") == []

