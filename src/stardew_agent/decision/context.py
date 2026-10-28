"""上下文组装：只给当前职责需要的数据，不做“大而全”的状态包。

上下文选择属于新 Agent（不是模型供应方，也不是记忆模块）。这里明确记录：

* 这次给了哪些现场字段、哪些被截断、为什么排序；
* 有哪些经验可用（含验证状态与证据数量）；
* 上一次委托的真实结果；
* 运行预算余额；
* 项目主动提供的知识（能力目录、字段含义、长期目标文字）。

不注入每日日程、作物优先级、固定任务路线或攻略解法。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ..contracts.ability import AbilityDescriptor
from ..contracts.observation import Observation

#: 长期目标默认文字（用户可改）。它不自动变成固定日程或利润目标。
DEFAULT_LONG_TERM_GOAL = "在用户约束和预算内了解世界、学会独立生活，积累能够验证和复用的经验。"


@dataclass(frozen=True)
class ContextLimits:
    """上下文预算。截断必须显式报告给模型。"""

    max_entities: int = 120
    max_inventory: int = 40
    max_experiences: int = 8
    max_menu_options: int = 12


def _distance(origin: Mapping[str, Any], entity: Mapping[str, Any]) -> int:
    tile = entity.get("tile") or {}
    x, y = tile.get("x"), tile.get("y")
    ox, oy = origin.get("x"), origin.get("y")
    if None in (x, y, ox, oy):
        return 10**6
    return abs(int(x) - int(ox)) + abs(int(y) - int(oy))


def _trim_entities(
    observation: Observation, limits: ContextLimits
) -> tuple[list[dict], dict]:
    origin = observation.position()
    rows = sorted(observation.entities, key=lambda entity: _distance(origin, entity))
    kept = [dict(entity) for entity in rows[: limits.max_entities]]
    return kept, {
        "kept": len(kept),
        "dropped": max(0, len(rows) - len(kept)),
        "order": "按与人物距离升序；被丢弃的只是上下文预算，不是世界的一部分",
    }


def build_context(
    *,
    observation: Observation,
    abilities: Sequence[AbilityDescriptor] = (),
    candidates: Mapping[str, Any] | None = None,
    experiences: Iterable[Mapping[str, Any]] = (),
    last_result: Mapping[str, Any] | None = None,
    budget: Mapping[str, Any] | None = None,
    long_term_goal: str = DEFAULT_LONG_TERM_GOAL,
    goal_revision: int = 0,
    limits: ContextLimits | None = None,
) -> dict:
    """组装一份可序列化的决策上下文。"""
    limits = limits or ContextLimits()
    entities, entity_trim = _trim_entities(observation, limits)
    inventory = [dict(item) for item in observation.inventory[: limits.max_inventory]]
    experiences = list(experiences)[: limits.max_experiences]
    menu = observation.menu
    if menu:
        options = list(menu.get("options") or [])[: limits.max_menu_options]
        menu = dict(menu, options=options, options_dropped=max(0, len(menu.get("options") or []) - len(options)))

    context: dict[str, Any] = {
        "long_term_goal": long_term_goal,
        "goal_revision": goal_revision,
        "observation": {
            "save_key": observation.save_key,
            "session": observation.session,
            "snapshot_id": observation.snapshot_id,
            "sampled_at": observation.sampled_at,
            "location": observation.location,
            "game_time": dict(observation.game_time),
            "player": dict(observation.player),
            "inventory": inventory,
            "inventory_dropped": max(0, len(observation.inventory) - len(inventory)),
            "entities": entities,
            "scope": dict(observation.scope),
            "menu": menu,
            "unknowns": dict(observation.unknowns),
            "source": observation.source,
        },
        "trimmed": {"entities": entity_trim},
        "abilities": [descriptor.to_dict() for descriptor in abilities],
        "candidates": dict(candidates or {}),
        "experiences": experiences,
        "last_result": dict(last_result) if last_result else None,
        "budget": dict(budget or {}),
        "provided_knowledge": [
            "能力目录：能力 id、参数、观察需求、真实前提、完成判据",
            "观察字段含义：可通行/可耕/需浇水等是游戏桥提供的结构化属性",
            "ref 的时效：ref 只在单次快照内有效；坐标与 target_id 跨快照稳定",
            "长期目标文字（用户可修改）",
        ],
    }
    return context
