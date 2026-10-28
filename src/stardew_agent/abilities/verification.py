"""委托结果的可验证证据。

所有能力都用这里的前后观察差分来回答“目标真的变了吗”。差分能证明发生了变化，
但单次变化不足以证明普遍因果规律——那属于经验模块的验证范围。
"""

from __future__ import annotations

from typing import Any, Mapping

from ..contracts.observation import Observation
from ..observation.diff import TARGET_FIELDS


def _field_rows(entities: list[Mapping[str, Any]]) -> list[dict]:
    return [{key: entity.get(key) for key in TARGET_FIELDS} for entity in entities]


def tile_delta(before: Observation, after: Observation, tile: Mapping[str, Any]) -> dict:
    """同一地块上的非 NPC 实体状态差异。"""
    old = _field_rows(before.entities_at(tile))
    new = _field_rows(after.entities_at(tile))
    fields: dict[str, dict] = {}
    for row_old, row_new in zip(old, new):
        for key in TARGET_FIELDS:
            if row_old.get(key) != row_new.get(key):
                fields[key] = {"before": row_old.get(key), "after": row_new.get(key)}
    return {
        "tile": dict(tile),
        "changed": old != new,
        "fields": fields,
        "before": old,
        "after": new,
    }


def inventory_delta(before: Observation, after: Observation) -> dict:
    """背包变化：新增/减少/数量变化。"""

    def index(observation: Observation) -> dict[int, dict]:
        rows = {}
        for item in observation.inventory:
            slot = item.get("slot")
            if slot is None:
                continue
            rows[int(slot)] = {
                "name": item.get("name"),
                "count": item.get("count"),
                "itemId": item.get("itemId"),
                # 水壶补水只改变水量，数量和物品 id 都不变，必须单独比较才能验证。
                "water": item.get("water"),
                "waterCapacity": item.get("waterCapacity"),
                "tool": item.get("tool"),
            }
        return rows

    old, new = index(before), index(after)
    changes = {}
    for slot in set(old) | set(new):
        if old.get(slot) != new.get(slot):
            changes[slot] = {"before": old.get(slot), "after": new.get(slot)}
    return {"changed": bool(changes), "slots": changes}


def position_delta(before: Observation, after: Observation) -> dict:
    old, new = before.position(), after.position()
    return {"before": old, "after": new, "changed": old != new}


def scene_changed(before: Observation, after: Observation) -> bool:
    return not before.same_scene_as(after)
