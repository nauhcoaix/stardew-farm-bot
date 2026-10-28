"""白名单 8：快照身份、语义字段与目标状态比较（纯函数）。

* :mod:`identity` — 存档/场景/目标身份、语义字段
* :mod:`diff` — 动作前后目标状态比较
* :mod:`state` — 快照整理成观察记录与纯数据视图
"""

from .diff import TARGET_FIELDS, at_tile, target_progress, target_state
from .identity import ENTITY_FIELDS, dumps, position, save_key, scene, semantic, target_id
from .state import from_snapshot, observed_tiles, position_of, tiles_of, to_snapshot

__all__ = [
    "ENTITY_FIELDS",
    "TARGET_FIELDS",
    "at_tile",
    "dumps",
    "from_snapshot",
    "observed_tiles",
    "position",
    "position_of",
    "save_key",
    "scene",
    "semantic",
    "target_id",
    "target_progress",
    "target_state",
    "tiles_of",
    "to_snapshot",
]

