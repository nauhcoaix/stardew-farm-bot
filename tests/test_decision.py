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
from tests.helpers import entity, menu_row, observe


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


def test_非法完成判据被拒绝():
    observation = observe()
    with pytest.raises(Rejected):
        parse_decision(
            {"goal": "x", "ability": "move_to", "params": {"x": 1, "y": 1}, "completion": {"path": "secrets", "op": "==", "value": 1}},
            observation=observation,
            validate_params=_validate_params,
        )


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
    observation = observe(entities=[entity("@s1:e%d" % i, "tile", "地块", 10 + i, 10) for i in range(5)])
    context = build_context(observation=observation, limits=ContextLimits(max_entities=2))
    assert len(context["observation"]["entities"]) == 2
    assert context["trimmed"]["entities"]["dropped"] == 3
    assert "距离" in context["trimmed"]["entities"]["order"]
    assert context["provided_knowledge"]


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


def test_默认提示词不要求模型写完成判据():
    system = build_messages({"player": {}})[0]["content"]
    assert "completion" not in system
    assert "stop_conditions" in system


def test_开启开关后提示词才出现完成判据():
    system = build_messages({"player": {}}, include_completion=True)[0]["content"]
    assert "completion" in system
    assert "判据不成立时该次动作会被判为失败" in system
