"""快照身份与语义字段纯函数。

来源：``stardew_valley_dfy/bot/memory_store.py`` 的模块级纯函数
（``dumps`` / ``target_id`` / ``semantic`` / ``save_key`` / ``position`` / ``scene``）。
只保留计算逻辑：不创建数据库、不控制游戏、不排任务，也不带入 ``MemoryStore``。

``target_id`` 是语义匹配键，不是游戏实体的永久身份；执行前必须重新绑定当前观察里的
临时 ``ref``。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

#: 参与 ``semantic`` 的实体字段（保持与旧观察格式一致）
ENTITY_FIELDS = (
    "kind",
    "name",
    "tile",
    "passable",
    "diggable",
    "tilled",
    "watered",
    "crop",
    "harvestable",
    "cropPhase",
    "cropDead",
    "refillable",
    "needsWater",
    "action",
    "terrain",
    "objType",
    "objName",
    "health",
    "stump",
)


def dumps(value: Any) -> str:
    """稳定序列化，用于计算身份哈希。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def target_id(location: str | None, entity: Mapping[str, Any]) -> str:
    """按地区 + 种类 + 名称(+地块) 计算语义目标 ID。"""
    identity = [location, entity.get("kind"), entity.get("name")]
    if entity.get("kind") != "npc":
        identity.append(entity.get("tile"))
    return "target:" + hashlib.sha256(dumps(identity).encode()).hexdigest()[:16]


def semantic(entity: Mapping[str, Any]) -> dict:
    """摘出实体的语义字段，供记录与匹配使用。"""
    return {key: entity[key] for key in ENTITY_FIELDS if key in entity}


def save_key(snap: Mapping[str, Any]) -> str:
    """存档身份。没有存档 ID 时按会话隔离，绝不把不同存档的地图混在一起。"""
    if snap.get("saveId") is not None:
        return "save:%s/player:%s" % (snap["saveId"], snap.get("playerId", "local"))
    return "session:%s:%s" % (snap.get("instanceId", "unknown"), snap.get("saveGeneration", 0))


def position(snap: Mapping[str, Any]) -> dict:
    point = (snap.get("player") or {}).get("position") or {}
    return {"x": point.get("x"), "y": point.get("y")}


def scene(snap: Mapping[str, Any]) -> dict:
    """当前场景身份：存档、会话、代次与地区。"""
    return {
        "save": save_key(snap),
        "instance": snap.get("instanceId"),
        "generation": snap.get("saveGeneration"),
        "location": snap.get("location"),
    }
