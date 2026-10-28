"""上下文组装：只给当前职责需要的数据，不做“大而全”的状态包。

上下文选择属于新 Agent（不是模型供应方，也不是记忆模块）。这里明确记录：

* 这次给了哪些现场字段、哪些被截断、为什么排序；
* 有哪些经验可用（含验证状态与证据数量）；
* 上一次委托的真实结果；
* 运行预算余额；
* 项目主动提供的知识（能力目录、字段含义、长期目标文字）。

不注入每日日程、作物优先级、固定任务路线或攻略解法。

为控制 token：默认把地块压成一张通行网格（``observation.tiles``），实体列表只保留
非地块实体（NPC/对象/耕地/水源/门/家具等），每条只留非默认字段；候选只给 id/ref/坐标。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ..contracts.ability import AbilityDescriptor
from ..contracts.observation import Observation

#: 长期目标默认文字（用户可改）。它不自动变成固定日程或利润目标。
DEFAULT_LONG_TERM_GOAL = "在用户约束和预算内了解世界、学会独立生活，积累能够验证和复用的经验。"

#: 地块本身就是“默认值”，不逐条列给模型；它们由通行网格表示
_PLAIN_KINDS = frozenset({"tile"})

#: 只有为真时才写出的布尔字段（false 就是默认，省略）
_FLAG_FIELDS = (
    "diggable",
    "refillable",
    "needsWater",
    "tilled",
    "watered",
    "harvestable",
    "cropDead",
    "ready",
    "stump",
)

#: 有值时才写出的字段
_EXTRA_FIELDS = ("crop", "cropPhase", "terrain", "objType", "objName", "action", "health", "growthStage")


@dataclass(frozen=True)
class ContextLimits:
    """上下文预算。截断必须显式报告给模型。"""

    max_entities: int = 120
    max_inventory: int = 40
    max_experiences: int = 8
    max_menu_options: int = 12
    max_recent_actions: int = 6
    #: 人类指令最多给几条（按优先级取；指令文字会截断）
    max_instructions: int = 4
    #: 地块压成网格 + 实体只留非默认字段（关掉可回到旧的全量行为，便于对比）
    compact_entities: bool = True
    #: 压缩模式下最多列出多少个非地块实体
    max_notable_entities: int = 24
    #: 能力目录只给 id/summary/params/scope（版本、观察需求、上限由代码保证）
    compact_abilities: bool = True
    #: ``last_result`` 只留状态与关键证据，不带完整差分
    compact_results: bool = True


def _distance(origin: Mapping[str, Any], entity: Mapping[str, Any]) -> int:
    tile = entity.get("tile") or {}
    x, y = tile.get("x"), tile.get("y")
    ox, oy = origin.get("x"), origin.get("y")
    if None in (x, y, ox, oy):
        return 10**6
    return abs(int(x) - int(ox)) + abs(int(y) - int(oy))


def _is_notable(entity: Mapping[str, Any]) -> bool:
    """值得单独列出的实体：不是普通地块，或地块上挂着交互/作物。"""
    kind = entity.get("kind")
    if kind and kind not in _PLAIN_KINDS:
        return True
    return any(entity.get(key) for key in ("action", "crop", "objName", "terrain"))


def _compact_row(entity: Mapping[str, Any]) -> dict:
    """一条实体里只保留非默认字段。"""
    row: dict[str, Any] = {"ref": entity.get("ref"), "kind": entity.get("kind"), "tile": entity.get("tile")}
    if entity.get("name") and entity.get("kind") not in _PLAIN_KINDS:
        row["name"] = entity.get("name")
    for key in _FLAG_FIELDS:
        if entity.get(key):
            row[key] = entity.get(key)
    for key in _EXTRA_FIELDS:
        value = entity.get(key)
        if value is not None and value != "" and value is not False:
            row[key] = value
    if entity.get("passable") is False:
        row["passable"] = False
    return row


def _passable_grid(observation: Observation) -> dict | None:
    """把本次观察到的地块压成按行字符串：``.`` 可通行、``#`` 阻挡、``?`` 未知。"""
    cells: dict[tuple[int, int], str] = {}
    for entity in observation.entities:
        tile = entity.get("tile") or {}
        x, y = tile.get("x"), tile.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            continue
        passable = entity.get("passable")
        cells[(x, y)] = "." if passable is True else "#" if passable is False else "?"
    if not cells:
        return None
    xs = [point[0] for point in cells]
    ys = [point[1] for point in cells]
    min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
    rows = [
        "".join(cells.get((x, y), " ") for x in range(min_x, max_x + 1))
        for y in range(min_y, max_y + 1)
    ]
    return {
        "origin": {"x": min_x, "y": min_y},
        "rows": rows,
        "legend": {".": "可通行", "#": "阻挡", "?": "未知", " ": "未观察"},
    }


def _entity_counts(observation: Observation) -> dict:
    counts = {
        "diggable": 0,
        "needsWater": 0,
        "harvestable": 0,
        "refillable": 0,
        "notable": 0,
        "plantable": 0,
        "seeds": 0,
    }
    for entity in observation.entities:
        for key in ("diggable", "needsWater", "harvestable", "refillable"):
            if entity.get(key) is True:
                counts[key] += 1
        if _is_notable(entity):
            counts["notable"] += 1
        if entity.get("kind") == "soil" and entity.get("tilled") and not entity.get("crop"):
            counts["plantable"] += 1
    for item in observation.inventory:
        text = " ".join(str(item.get(key) or "") for key in ("name", "itemId")).lower()
        if "种子" in text or "seed" in text:
            counts["seeds"] += int(item.get("count") or 0)
    return counts


def _trim_entities(
    observation: Observation, limits: ContextLimits
) -> tuple[list[dict], dict, dict | None, dict]:
    origin = observation.position()
    rows = sorted(observation.entities, key=lambda entity: _distance(origin, entity))
    counts = _entity_counts(observation)
    if not limits.compact_entities:
        kept = [dict(entity) for entity in rows[: limits.max_entities]]
        return kept, {
            "kept": len(kept),
            "dropped": max(0, len(rows) - len(kept)),
            "order": "按与人物距离升序；被丢弃的只是上下文预算，不是世界的一部分",
        }, None, counts

    notable = [entity for entity in rows if _is_notable(entity)]
    kept = [_compact_row(entity) for entity in notable[: limits.max_notable_entities]]
    trim = {
        "kept": len(kept),
        "dropped": max(0, len(notable) - len(kept)),
        "compacted": True,
        "order": "非地块实体按与人物距离升序；地块改由 tiles 网格表示",
    }
    return kept, trim, _passable_grid(observation), counts


def _recent_rows(rows: Iterable[Mapping[str, Any]], limit: int, now: float) -> list[dict]:
    """把历史动作压缩成模型可读的条目：字段截断，附“多少秒前”。"""
    out: list[dict] = []
    for row in list(rows)[: max(0, int(limit))]:
        params = row.get("params") or {}
        try:
            params_text = json.dumps(dict(params), ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            params_text = str(params)
        at = row.get("recorded_at")
        out.append(
            {
                "ability": str(row.get("ability") or "?"),
                "params": params_text[:120],
                "state": str(row.get("state") or "unknown"),
                "detail": str(row.get("detail") or "")[:160],
                "progress": bool(row.get("progress")),
                "goal": str(row.get("goal") or "")[:80],
                "seconds_ago": int(max(0.0, now - float(at))) if isinstance(at, (int, float)) else None,
            }
        )
    return out


_RESULT_FIELDS = ("ability", "params", "state", "detail", "progress", "error")
_EVIDENCE_FIELDS = (
    "task",
    "done",
    "failed",
    "remaining",
    "verified",
    "steps",
    "completion_predicate",
    "completion_predicate_error",
    "error",
    # 商店/菜单/探索结果
    "menu_type",
    "items",
    "sales",
    "summary",
    "response",
    "shop_item",
    "index",
    "price",
    "qty",
    "spent",
    "gained",
    "expected_cost",
    "visited",
    "menu_closed",
    "money_changed",
)


def _compact_abilities(abilities: Sequence[AbilityDescriptor]) -> list[dict]:
    """模型只需要知道“有哪些能力、参数怎么填、作用范围”；版本/观察需求/上限由代码保证。"""
    return [
        {"id": descriptor.id, "summary": descriptor.summary, "params": dict(descriptor.params), "scope": descriptor.scope}
        for descriptor in abilities
    ]


def _compact_result(result: Mapping[str, Any]) -> dict:
    """``last_result`` 只留状态与关键证据，避免每轮重发完整差分。"""
    row = {key: result.get(key) for key in _RESULT_FIELDS if result.get(key) is not None}
    evidence = result.get("evidence")
    if isinstance(evidence, Mapping):
        kept = {}
        for key in _EVIDENCE_FIELDS:
            value = evidence.get(key)
            if value is None:
                continue
            if isinstance(value, list):
                value = value[:12]
            kept[key] = value
        if kept:
            row["evidence"] = kept
    return row


def _compact_candidates(candidates: Mapping[str, Any]) -> dict:
    """候选只留“去哪/点谁”必需的信息：完整实体与前沿明细不重复塞。"""
    items = []
    for row in candidates.get("items") or []:
        item = {
            "id": row.get("id"),
            "mode": row.get("mode"),
            "kind": row.get("kind"),
            "ref": row.get("ref"),
            "tile": row.get("tile"),
        }
        for key in ("op", "tx", "ty", "name"):
            if row.get(key) is not None:
                item[key] = row.get(key)
        target = row.get("target") or {}
        if isinstance(target, Mapping) and target.get("name") and "name" not in item:
            item["name"] = target.get("name")
        items.append({key: value for key, value in item.items() if value is not None})
    frontier = candidates.get("frontier") or {}
    out: dict[str, Any] = {"items": items, "truncated": candidates.get("truncated")}
    if frontier:
        out["frontier"] = {"considered": frontier.get("considered"), "truncated": frontier.get("truncated")}
    if candidates.get("interaction_count") is not None:
        out["interaction_count"] = candidates.get("interaction_count")
    for key in ("order", "more"):
        if candidates.get(key):
            out[key] = candidates.get(key)
    return out


def _placement_tiles(observation: Observation, limit: int = 3) -> list[dict]:
    """背包满时给模型几个“可以放箱子”的相邻空格，省得它自己猜空地。"""
    here = dict(observation.player or {}).get("position") or {}
    ox, oy = here.get("x"), here.get("y")
    if not isinstance(ox, int) or not isinstance(oy, int):
        return []
    rows: list[dict] = []
    for entity in observation.entities:
        if entity.get("kind") != "tile" or entity.get("passable") is not True:
            continue
        if entity.get("refillable") or entity.get("objName") or entity.get("terrain") or entity.get("action"):
            continue
        tile = entity.get("tile") or {}
        x, y = tile.get("x"), tile.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            continue
        distance = abs(x - ox) + abs(y - oy)
        if 1 <= distance <= 2:
            rows.append({"x": x, "y": y, "distance": distance})
    rows.sort(key=lambda row: (row["distance"], row["y"], row["x"]))
    return rows[:limit]


def inventory_state(observation: Observation) -> dict:
    """把“背包还剩几格、有没有 50 木材、旁边哪里能放箱子”直接算给模型。"""
    items = list(observation.inventory)
    player = dict(observation.player or {})
    max_items = player.get("maxItems")
    used = {int(item.get("slot")) for item in items if isinstance(item.get("slot"), int)}
    free = player.get("freeSlots")
    if not isinstance(free, int):
        free = int(max_items) - len(used) if isinstance(max_items, int) else None
    wood = 0
    for item in items:
        if str(item.get("itemId") or "") == "(O)388":
            wood += int(item.get("count") or 0)
            continue
        name = str(item.get("name") or "").lower()
        if "wood" in name or "木材" in name:
            wood += int(item.get("count") or 0)
    return {
        "free_slots": free,
        "full": (free == 0) if free is not None else None,
        "wood": wood,
        "place_chest_tiles": _placement_tiles(observation),
    }


def _compact_instruction(row: Mapping[str, Any]) -> dict:
    """人类指令只给模型 id/类型/正文/年龄，不带文件路径等内部字段。"""
    text = str(row.get("text") or "").replace("\n", " ").strip()
    if len(text) > 240:
        text = text[:239] + "…"
    return {
        "id": row.get("id"),
        "kind": row.get("kind") or "instruction",
        "text": text,
        "priority": row.get("priority"),
        "age_seconds": row.get("age_seconds"),
    }


def build_context(
    *,
    observation: Observation,
    abilities: Sequence[AbilityDescriptor] = (),
    candidates: Mapping[str, Any] | None = None,
    experiences: Iterable[Mapping[str, Any]] = (),
    instructions: Iterable[Mapping[str, Any]] = (),
    last_result: Mapping[str, Any] | None = None,
    recent_results: Iterable[Mapping[str, Any]] = (),
    budget: Mapping[str, Any] | None = None,
    long_term_goal: str = DEFAULT_LONG_TERM_GOAL,
    goal_revision: int = 0,
    limits: ContextLimits | None = None,
    now: float | None = None,
) -> dict:
    """组装一份可序列化的决策上下文。"""
    limits = limits or ContextLimits()
    entities, entity_trim, tiles, counts = _trim_entities(observation, limits)
    inventory = [dict(item) for item in observation.inventory[: limits.max_inventory]]
    experiences = list(experiences)[: limits.max_experiences]
    instruction_rows = [_compact_instruction(row) for row in list(instructions)[: limits.max_instructions]]
    recent = _recent_rows(recent_results, limits.max_recent_actions, time.time() if now is None else now)
    failures = [row for row in recent if row["state"] in ("failed", "unknown", "blocked")][:3]
    menu = observation.menu
    if menu:
        options = list(menu.get("options") or [])[: limits.max_menu_options]
        menu = dict(menu, options=options, options_dropped=max(0, len(menu.get("options") or []) - len(options)))

    observe_view: dict[str, Any] = {
        "save_key": observation.save_key,
        "session": observation.session,
        "snapshot_id": observation.snapshot_id,
        "sampled_at": observation.sampled_at,
        "location": observation.location,
        "game_time": dict(observation.game_time),
        "player": dict(observation.player),
        "inventory": inventory,
        "inventory_dropped": max(0, len(observation.inventory) - len(inventory)),
        "inventory_state": inventory_state(observation),
        "entities": entities,
        "entity_counts": counts,
        "scope": dict(observation.scope),
        "menu": menu,
        "unknowns": dict(observation.unknowns),
        "source": observation.source,
    }
    if tiles is not None:
        observe_view["tiles"] = tiles

    context: dict[str, Any] = {
        "long_term_goal": long_term_goal,
        "goal_revision": goal_revision,
        "observation": observe_view,
        "trimmed": {"entities": entity_trim},
        "abilities": _compact_abilities(abilities)
        if limits.compact_abilities
        else [descriptor.to_dict() for descriptor in abilities],
        "candidates": _compact_candidates(candidates or {}),
        "experiences": experiences,
        "instructions": instruction_rows,
        "recent_actions": recent,
        "recent_failures": failures,
        "last_result": _compact_result(last_result)
        if (last_result and limits.compact_results)
        else (dict(last_result) if last_result else None),
        "budget": dict(budget or {}),
        "provided_knowledge": [
            "observation.tiles：可通行网格（origin 为左上角；. 可通行 / # 阻挡 / ? 未知 / 空格 未观察）；"
            "entity_counts 是可锄/需浇/可收/可装水/可播种（plantable）/种子数量（seeds）",
            "inventory_state：free_slots=0/full=true 表示背包满；wood 是木材总数；"
            "place_chest_tiles 是旁边可放箱子的空格（配合 inventory_action op=place_chest）；"
            "满包时 craft_item 会被拒绝，不要指望产物掉地上",
            "ref 只在单次快照内有效；坐标与 target_id 跨快照稳定；判据 path 只能引用观察根字段",
            "recent_actions 的 state=blocked 表示执行器没下发，原样重发会被再次拦",
            "instructions 是用户直接给的指令/建议：优先级最高，但不能绕过能力参数与安全边界；"
            "照做后把对应 id 写进 followed_instructions，做不到就在 reason 里说明",
        ],
    }
    return context
