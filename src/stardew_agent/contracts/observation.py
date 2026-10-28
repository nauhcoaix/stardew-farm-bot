"""观察契约。

一次观察只描述“这一时刻、这个存档、这个范围内真的看到了什么”。三类状态必须
可区分：

* ``observed``：游戏桥真的提供了该字段；
* ``unknown``：字段本可能变化，但这次没看到（未提供、被截断、被菜单遮挡）；
* ``unsupported``：当前桥/Mod 版本没有这个能力，不能假装是“否”。

判断“某地块可通行”这类属性时，``passable`` 缺失表示未知，不等于禁止。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .version import SCHEMA_VERSION

#: 观察里可以按点路径读取的根字段
FIELD_ROOTS = (
    "player",
    "inventory",
    "entities",
    "menu",
    "location",
    "game_time",
    "scope",
    "save_key",
    "session",
    "snapshot_id",
    "source",
)

#: 已知当前游戏桥不提供的信息；用来说明能力缺口，而不是猜一个值
UNSUPPORTED_FIELDS = {
    "player.health": "当前快照不含玩家生命值（Observations.cs 未读取）",
    "enemies": "当前快照不含敌人/战斗状态",
    "combat": "当前桥没有战斗相关写操作",
    "world_map": "只提供当前观察范围内地图，不提供全局地图",
}


@dataclass(frozen=True)
class Field:
    """带状态的字段读取结果。"""

    value: Any = None
    state: str = "unknown"
    reason: str = ""

    @property
    def known(self) -> bool:
        return self.state == "observed"

    def to_dict(self) -> dict:
        row = {"state": self.state}
        if self.state == "observed":
            row["value"] = self.value
        else:
            row["reason"] = self.reason
        return row


@dataclass(frozen=True)
class Observation:
    """某一时刻的结构化现场。"""

    save_key: str
    session: str = ""
    instance: str = ""
    generation: int = 0
    snapshot_id: str = ""
    location: str | None = None
    sampled_at: float = 0.0
    game_time: Mapping[str, Any] = field(default_factory=dict)
    player: Mapping[str, Any] = field(default_factory=dict)
    inventory: Sequence[Mapping[str, Any]] = ()
    entities: Sequence[Mapping[str, Any]] = ()
    menu: Mapping[str, Any] | None = None
    scope: Mapping[str, Any] = field(default_factory=dict)
    unknowns: Mapping[str, str] = field(default_factory=dict)
    source: str = "unknown"
    raw: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    # ---- 便捷读取 -------------------------------------------------
    def field(self, path: str) -> Field:
        """按点路径读取字段；缺失时明确说明是未知还是不支持。"""
        if path in self.unknowns:
            return Field(None, "unknown", str(self.unknowns[path]))
        if path in UNSUPPORTED_FIELDS:
            return Field(None, "unsupported", UNSUPPORTED_FIELDS[path])
        root = path.split(".", 1)[0]
        if root not in FIELD_ROOTS:
            return Field(None, "unsupported", "该字段不在当前观察契约内")
        current: Any = self._root(root)
        for part in path.split(".")[1:]:
            if isinstance(current, Mapping):
                if part not in current:
                    return Field(None, "unknown", "字段未提供：%s" % path)
                current = current[part]
            elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
                try:
                    current = current[int(part)]
                except (ValueError, IndexError):
                    return Field(None, "unknown", "列表下标不可用：%s" % path)
            else:
                return Field(None, "unknown", "路径无法继续：%s" % path)
        return Field(current, "observed")

    def _root(self, name: str) -> Any:
        return {
            "player": self.player,
            "inventory": self.inventory,
            "entities": self.entities,
            "menu": self.menu,
            "location": self.location,
            "game_time": self.game_time,
            "scope": self.scope,
            "save_key": self.save_key,
            "session": self.session,
            "snapshot_id": self.snapshot_id,
            "source": self.source,
        }[name]

    def position(self) -> dict:
        point = (self.player or {}).get("position") or {}
        return {"x": point.get("x"), "y": point.get("y")}

    def entity(self, reference: str) -> Mapping[str, Any] | None:
        for entity in self.entities:
            if entity.get("ref") == reference:
                return entity
        return None

    def entities_at(self, tile: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        return [e for e in self.entities if e.get("tile") == tile and e.get("kind") != "npc"]

    def same_scene_as(self, other: "Observation") -> bool:
        return (self.save_key, self.instance, self.generation, self.location) == (
            other.save_key,
            other.instance,
            other.generation,
            other.location,
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "save_key": self.save_key,
            "session": self.session,
            "instance": self.instance,
            "generation": self.generation,
            "snapshot_id": self.snapshot_id,
            "location": self.location,
            "sampled_at": self.sampled_at,
            "game_time": dict(self.game_time),
            "player": dict(self.player),
            "inventory": [dict(item) for item in self.inventory],
            "entities": [dict(entity) for entity in self.entities],
            "menu": dict(self.menu) if self.menu else None,
            "scope": dict(self.scope),
            "unknowns": dict(self.unknowns),
            "source": self.source,
        }
