"""目标状态比较纯函数。

来源：``stardew_valley_dfy/bot/action_history.py`` 的模块级纯函数
（``at_tile`` / ``target_state`` / ``target_progress``，以及 ``FIELDS``）。
不带入 ``ActionHistory`` 类：没有文件读写、没有重试记录、没有记忆接线。

这些函数只回答“动作前后这个格子上的东西变了吗”。一次变化通常不足以证明因果规律，
所以它们用于判“本次委托是否达成”，不用于自动提升经验为通用事实。
"""

from __future__ import annotations

import json
from typing import Any, Mapping

#: 参与状态比较的实体字段
TARGET_FIELDS = (
    "kind",
    "name",
    "crop",
    "watered",
    "tilled",
    "diggable",
    "harvestable",
    "ready",
    "cropPhase",
    "cropDead",
    "health",
    "stump",
    "growthStage",
)


def at_tile(snap: Mapping[str, Any], entity: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """同一地块上的非 NPC 实体。"""
    return [
        e
        for e in snap.get("entities") or []
        if e.get("tile") == entity.get("tile") and e.get("kind") != "npc"
    ]


def target_state(snap: Mapping[str, Any], entity: Mapping[str, Any]) -> list[str]:
    """该地块的规范化状态行，用于比较前后变化。"""
    rows = [
        json.dumps({key: e.get(key) for key in TARGET_FIELDS}, sort_keys=True, ensure_ascii=False)
        for e in at_tile(snap, entity)
    ]
    return sorted(rows)


def target_progress(
    action: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    entity: Mapping[str, Any],
    verified: bool = False,
) -> bool:
    """动作是否让目标出现可验证进展。

    ``verified`` 表示桥返回了动作自身的证据（例如树的 health/stump 变化）；
    快照不一定暴露树血量，因此砍/清障只有在该证据存在时才判为有进展。
    """
    if before.get("location") != after.get("location"):
        return False
    old, new = at_tile(before, entity), at_tile(after, entity)
    if action == "till":
        return not any(e.get("tilled") for e in old) and any(e.get("tilled") for e in new)
    if action == "plant":
        return not any(e.get("crop") for e in old) and any(e.get("crop") for e in new)
    if action == "water":
        return not any(e.get("watered") for e in old) and any(e.get("watered") for e in new)
    if action == "harvest":
        return any(e.get("crop") and (e.get("harvestable") or e.get("ready")) for e in old) and bool(new) and not any(
            e.get("crop") and (e.get("harvestable") or e.get("ready")) for e in new
        )
    if action == "chop":
        return verified
    if action == "clear":
        return verified and not any(
            e.get("kind") == entity.get("kind") and e.get("name") == entity.get("name") for e in new
        )
    return False
