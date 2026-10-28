"""把游戏桥的原始快照整理成 :class:`Observation`，并提供纯数据视图。

新系统统一在这里处理旧快照的两个不一致：实体列表既可能在顶层，也可能在
``observation.entities`` 下；观察范围信息在 ``observation`` 里。整理后顶层
``entities`` 与 ``scope`` 都可直接使用，纯函数不必再猜位置。
"""

from __future__ import annotations

import time
from typing import Any, Iterable, Mapping

from ..contracts.observation import Observation
from .identity import save_key


def from_snapshot(
    snapshot: Mapping[str, Any],
    *,
    source: str = "bridge",
    sampled_at: float | None = None,
) -> Observation:
    """把桥返回的快照转换为观察记录，不补造缺失字段。"""
    raw = dict(snapshot or {})
    scope = dict(raw.get("observation") or {})
    entities = scope.get("entities")
    if not isinstance(entities, list):
        entities = raw.get("entities")
    entities = list(entities or [])
    flat = dict(raw)
    flat["entities"] = entities
    flat["scope"] = scope
    flat.pop("observation", None)

    instance = str(raw.get("instanceId") or "")
    generation = int(raw.get("saveGeneration") or 0)
    date = dict(raw.get("date") or {})
    game_time = dict(date)
    game_time["time"] = raw.get("time")

    unknowns: dict[str, str] = {}
    if raw.get("mode") != "game":
        unknowns["scene"] = "游戏不在正常运行状态（mode=%s）" % raw.get("mode")
    if not entities:
        unknowns["entities"] = "本次快照没有实体列表"
    if scope.get("truncated"):
        unknowns["entities.outside_scope"] = "观察半径外未观察，不能当作不存在或不可通行"

    return Observation(
        save_key=save_key(flat),
        session="%s:%s" % (instance or "unknown", generation),
        instance=instance,
        generation=generation,
        snapshot_id=str(raw.get("snapshotId") or ""),
        location=raw.get("location"),
        sampled_at=float(sampled_at if sampled_at is not None else time.time()),
        game_time=game_time,
        player=dict(raw.get("player") or {}),
        inventory=tuple(dict(item) for item in raw.get("inventory") or []),
        entities=tuple(dict(entity) for entity in entities),
        menu=dict(raw["menu"]) if isinstance(raw.get("menu"), Mapping) else None,
        scope=scope,
        unknowns=unknowns,
        source=source,
        raw=flat,
    )


def to_snapshot(observation: Observation) -> dict:
    """还原为纯函数使用的扁平快照。"""
    return dict(observation.raw)


def observed_tiles(observation: Observation) -> list[dict]:
    """本次观察到的地块通行性；``passable`` 缺失时保留为未知（不放进结果）。"""
    rows = []
    for entity in observation.entities:
        tile = entity.get("tile")
        if not isinstance(tile, Mapping) or tile.get("x") is None or tile.get("y") is None:
            continue
        row: dict[str, Any] = {"tile": {"x": tile["x"], "y": tile["y"]}}
        if "passable" in entity:
            row["passable"] = bool(entity["passable"])
        rows.append(row)
    return rows


def position_of(observation: Observation) -> tuple[int, int] | None:
    point = observation.position()
    if point.get("x") is None or point.get("y") is None:
        return None
    return int(point["x"]), int(point["y"])


def tiles_of(entities: Iterable[Mapping[str, Any]]) -> set[tuple[int, int]]:
    """实体集合覆盖到的地块，用于判断“目标是否还在观察范围内”。"""
    points = set()
    for entity in entities:
        tile = entity.get("tile") or {}
        if tile.get("x") is None or tile.get("y") is None:
            continue
        points.add((int(tile["x"]), int(tile["y"])))
    return points
