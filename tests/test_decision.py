"""决策层：受限判据、解析校验、上下文预算与计划新鲜度。"""

from __future__ import annotations

import pytest

from stardew_agent.contracts.errors import Rejected
from stardew_agent.decision import (
    ContextLimits,
    PredicateError,
    build_context,
    build_messages,
    check_freshness,
    evaluate,
    evaluate_all,
    parse_decision,
    validate,
    validate_all,
)
from tests.helpers import entity, inventory, menu_row, observe


def test_判据只接受观察契约内的路径():
    validate({"path": "player.position.x", "op": "==", "value": 10})
    with pytest.raises(PredicateError):
        validate({"path": "secrets.api_key", "op": "==", "value": 1})
    with pytest.raises(PredicateError):
        validate({"path": "player..position", "op": "==", "value": 1})
    with pytest.raises(PredicateError):
        validate({"path": "player.position()", "op": "==", "value": 1})
    with pytest.raises(PredicateError):
        validate({"path": "player.money", "op": "approximately", "value": 1})
    with pytest.raises(PredicateError):
        validate({"path": "player.money", "op": "==", "value": {"nested": object()}})


def test_判据数量有上限():
    rows = [{"path": "player.money", "op": ">=", "value": 0}] * 13
    with pytest.raises(PredicateError):
        validate_all(rows)


def test_未知字段求值为None而不是不满足():
    observation = observe()
    result = evaluate({"path": "player.mood", "op": "==", "value": "happy"}, observation)
    assert result.met is None
    assert "未知" in result.reason or "未提供" in result.reason


def test_存在与不存在():
    observation = observe()
    assert evaluate({"path": "player.energy", "op": "exists"}, observation).met is True
    assert evaluate({"path": "player.health", "op": "exists"}, observation).met is False
    assert evaluate({"path": "player.health", "op": "absent"}, observation).met is True


def test_比较与包含():
    observation = observe(energy=120)
    assert evaluate({"path": "player.energy", "op": ">=", "value": 100}, observation).met is True
    assert evaluate({"path": "location", "op": "in", "value": ["Farm", "Town"]}, observation).met is True
    assert evaluate({"path": "location", "op": "not_in", "value": ["Farm"]}, observation).met is False
    met, _ = evaluate_all(
        [
            {"path": "player.energy", "op": ">=", "value": 100},
            {"path": "location", "op": "==", "value": "Farm"},
        ],
        observation,
    )
    assert met is True


def _validate_params(ability, params):
    if ability != "move_to":
        raise Rejected("未知能力 %s" % ability)
    return dict(params, max_steps=params.get("max_steps", 1))


def test_解析决策绑定我们给出的观察():
    observation = observe(x=10, y=10)
    decision = parse_decision(
        {"goal": "走到 (12,10)", "reason": "开拓视野", "ability": "move_to", "params": {"x": 12, "y": 10}},
        observation=observation,
        validate_params=_validate_params,
    )
    assert decision.call is not None
    assert decision.call.params["max_steps"] == 1
    assert decision.based_on_snapshot_id == observation.snapshot_id
    assert decision.based_on_sampled_at == observation.sampled_at


def test_未知能力和非法参数被拒绝():
    observation = observe()
    with pytest.raises(Rejected):
        parse_decision({"goal": "x", "ability": "fly"}, observation=observation, validate_params=_validate_params)
    with pytest.raises(Rejected):
        parse_decision(
            {"goal": "x", "ability": "move_to", "params": []}, observation=observation, validate_params=_validate_params
        )


def test_允许不行动但必须说明目标():
    observation = observe()
    decision = parse_decision(
        {"goal": "先观察", "ability": None, "note": "缺少体力信息"}, observation=observation
    )
    assert decision.call is None
    assert decision.note == "缺少体力信息"
    with pytest.raises(Rejected):
        parse_decision({"ability": None}, observation=observation)


def test_非法完成判据被忽略但动作保留():
    observation = observe()
    decision = parse_decision(
        {
            "goal": "x",
            "ability": "move_to",
            "params": {"x": 1, "y": 1},
            "completion": {"path": "secrets", "op": "==", "value": 1},
        },
        observation=observation,
        validate_params=_validate_params,
    )
    # 判据是可选的：写错路径不该让动作作废，但必须让调用方看到原因。
    assert decision.call is not None
    assert decision.completion is None
    assert decision.predicate_errors and "越界" in decision.predicate_errors[0]
    # 判据校验本身仍然是严格的（能力/内部代码使用同一个校验器）。
    with pytest.raises(PredicateError):
        validate({"path": "secrets", "op": "==", "value": 1})


def test_同一快照不判过期_仅时间变化也不判过期():
    based_on = observe(game_time=600)
    decision = parse_decision({"goal": "走", "ability": "move_to", "params": {"x": 1, "y": 1}}, observation=based_on)
    assert check_freshness(decision, based_on, based_on).stale is False
    later = observe(game_time=700)
    freshness = check_freshness(decision, based_on, later)
    assert freshness.stale is False


def test_换地图或菜单状态变化判过期():
    based_on = observe(location="Farm")
    decision = parse_decision({"goal": "走", "ability": "move_to", "params": {"x": 1, "y": 1}}, observation=based_on)
    moved = observe(location="Town")
    assert check_freshness(decision, based_on, moved).stale is True
    opened = observe(menu=menu_row(["@s2:m1"]))
    freshness = check_freshness(decision, based_on, opened)
    assert freshness.stale is True
    assert any("菜单" in reason for reason in freshness.reasons)


def test_前提被证明不满足时判过期_未知只记录():
    based_on = observe(energy=200)
    decision = parse_decision({"goal": "钓鱼", "ability": "move_to", "params": {"x": 1, "y": 1}}, observation=based_on)
    low = observe(energy=5)
    stale = check_freshness(decision, based_on, low, premises=[{"path": "player.energy", "op": ">=", "value": 30}])
    assert stale.stale is True
    unknown = check_freshness(decision, based_on, low, premises=[{"path": "player.mood", "op": "==", "value": "ok"}])
    assert unknown.stale is False
    assert unknown.unknowns


def test_上下文截断会报告数量与排序():
    observation = observe(
        entities=[
            entity("@s1:e%d" % i, "soil", "耕地", 10 + i, 10, tilled=True, needsWater=True)
            for i in range(5)
        ]
    )
    context = build_context(observation=observation, limits=ContextLimits(max_notable_entities=2))
    assert len(context["observation"]["entities"]) == 2
    assert context["trimmed"]["entities"]["dropped"] == 3
    assert "距离" in context["trimmed"]["entities"]["order"]
    assert context["provided_knowledge"]


def test_默认把地块压成网格并统计农活数量():
    observation = observe(
        entities=[
            entity("@s1:e1", "tile", "地块", 10, 11),
            entity("@s1:e2", "tile", "地块", 11, 11, diggable=True),
            entity("@s1:e3", "soil", "耕地", 12, 11, tilled=True, needsWater=True, watered=False),
        ],
        inventory_rows=[
            inventory(0, "防风草种子", count=2),
            inventory(1, "锄头", tool="Hoe"),
        ],
        x=10,
        y=10,
    )
    context = build_context(observation=observation)
    view = context["observation"]
    assert [row["kind"] for row in view["entities"]] == ["soil"]
    assert view["entity_counts"]["diggable"] == 1
    assert view["entity_counts"]["needsWater"] == 1
    assert view["entity_counts"]["plantable"] == 1
    assert view["entity_counts"]["seeds"] == 2
    assert view["entities"][0]["needsWater"] is True
    assert "watered" not in view["entities"][0]
    assert view["tiles"]["origin"] == {"x": 10, "y": 11}
    assert view["tiles"]["rows"] == ["..."]
    assert context["trimmed"]["entities"]["compacted"] is True


def test_上下文包含最近动作与失败摘要():
    observation = observe()
    recent = [
        {
            "ability": "use",
            "params": {"target": "@s1:e1"},
            "state": "failed",
            "detail": "判据未满足",
            "progress": False,
            "goal": "浇水",
            "recorded_at": 900.0,
        },
        {
            "ability": "move_to",
            "params": {"x": 1, "y": 2},
            "state": "completed",
            "detail": "到达",
            "progress": True,
            "goal": "走过去",
            "recorded_at": 990.0,
        },
    ]
    context = build_context(observation=observation, recent_results=recent, now=1000.0)
    assert [row["state"] for row in context["recent_actions"]] == ["failed", "completed"]
    assert context["recent_actions"][0]["seconds_ago"] == 100
    assert context["recent_actions"][1]["seconds_ago"] == 10
    assert len(context["recent_failures"]) == 1
    assert context["recent_failures"][0]["ability"] == "use"
    assert '"target": "@s1:e1"' in context["recent_actions"][0]["params"]


def test_最近动作按上限截断():
    observation = observe()
    recent = [
        {"ability": "a%d" % index, "state": "completed", "detail": "", "recorded_at": 100.0 + index}
        for index in range(5)
    ]
    context = build_context(
        observation=observation,
        recent_results=recent,
        limits=ContextLimits(max_recent_actions=2),
        now=200.0,
    )
    assert [row["ability"] for row in context["recent_actions"]] == ["a0", "a1"]


def test_能力目录与上次结果会被压缩():
    from stardew_agent.contracts.ability import AbilityDescriptor

    descriptor = AbilityDescriptor(
        id="demo",
        version="9.9.9",
        summary="示例",
        params={"a": "说明"},
        observation_needs=("entities",),
        scope="只做示例",
        completion="前后观察变化",
        limits=("max_steps<=1",),
    )
    context = build_context(
        observation=observe(),
        abilities=[descriptor],
        last_result={
            "ability": "use",
            "state": "completed",
            "params": {"target": "@s1:e1"},
            "evidence": {
                "tile_delta": {"before": ["x" * 200], "after": ["y" * 200]},
                "done": [{"tile": {"x": 1, "y": 1}}],
                "remaining": 3,
            },
        },
    )
    assert set(context["abilities"][0]) == {"id", "summary", "params", "scope"}
    result = context["last_result"]
    assert "tile_delta" not in result["evidence"]
    assert result["evidence"]["remaining"] == 3
    assert result["evidence"]["done"]


def test_非法可选判据被忽略而不是丢弃动作():
    observation = observe()
    decision = parse_decision(
        {
            "goal": "走到耕地边",
            "ability": "move_to",
            "params": {"x": 11, "y": 10},
            "completion": {"path": "last_result.state", "op": "==", "value": "completed"},
            "stop_conditions": [{"path": "recent_actions", "op": "exists"}],
        },
        observation=observation,
        validate_params=lambda name, values: dict(values),
    )
    assert decision.call is not None
    assert decision.call.ability == "move_to"
    assert decision.completion is None
    assert decision.stop_conditions == ()
    assert len(decision.predicate_errors) == 2
    assert "越界" in decision.predicate_errors[0]
    assert "predicate_errors" in decision.to_dict()


def test_参数校验失败时保留能力id便于反馈():
    observation = observe()

    def reject(name, params):
        raise Rejected("该能力不直接对模型开放")

    with pytest.raises(Rejected) as info:
        parse_decision(
            {"goal": "试一下", "ability": "use", "params": {}},
            observation=observation,
            validate_params=reject,
        )
    assert info.value.ability == "use"
    assert "use" in str(info.value)


def test_默认提示词不要求模型写完成判据():
    system = build_messages({"player": {}})[0]["content"]
    assert "completion" not in system
    assert "stop_conditions" in system


def test_开启开关后提示词才出现完成判据():
    system = build_messages({"player": {}}, include_completion=True)[0]["content"]
    assert "completion" in system
    assert "判据不成立时该次动作会被判为失败" in system


def test_上下文包含人类指令并限制条数():
    observation = observe(x=10, y=10)
    rows = [
        {"id": "ins-1", "kind": "instruction", "text": "先给作物浇水", "priority": 7, "age_seconds": 3.0},
        {"id": "ins-2", "kind": "constraint", "text": "不要砍树", "priority": 4, "age_seconds": 8.0},
    ]
    context = build_context(
        observation=observation, instructions=rows, limits=ContextLimits(max_instructions=1)
    )
    assert [row["id"] for row in context["instructions"]] == ["ins-1"]
    assert context["instructions"][0]["text"] == "先给作物浇水"
    assert any("instructions" in line for line in context["provided_knowledge"])


def test_满包时召回查询带状态词():
    from stardew_agent.cli.wiring import recall_query

    observation = observe(
        inventory_rows=[inventory(slot, "木材", 50, itemId="(O)388") for slot in range(12)]
    )
    query = recall_query(observation, "整理背包")
    assert "背包满" in query


def test_上下文报告背包满与可放箱子的空格():
    wood = [inventory(slot, "木材", 50, itemId="(O)388") for slot in range(12)]
    spot = entity("@s1:t1", "tile", "地块", 11, 10)
    observation = observe(inventory_rows=wood, entities=[spot])

    context = build_context(observation=observation)
    state = context["observation"]["inventory_state"]

    assert state["free_slots"] == 0
    assert state["full"] is True
    assert state["wood"] == 600


def test_提示词要求回执用户指令():
    system = build_messages({})[0]["content"]
    assert "followed_instructions" in system
    assert "instructions 是用户直接给你的指令或建议" in system

