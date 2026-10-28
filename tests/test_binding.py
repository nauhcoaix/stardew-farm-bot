"""决策绑定：把旧快照的临时 ref 重新绑定到当前现场。"""

from __future__ import annotations

from stardew_agent.contracts.decision import AbilityCall
from stardew_agent.decision import rebind_call
from tests.helpers import entity, observe


def move_call(near: str) -> AbilityCall:
    return AbilityCall("move_to", {"near": near, "max_steps": 2})


def test_移动的旧ref改绑为坐标():
    old = observe(entities=[entity("@s1:e9", "soil", "耕地", 12, 10)])
    new = observe(entities=[entity("@s2:e1", "soil", "耕地", 12, 10)])
    call, notes = rebind_call(move_call("@s1:e9"), old, new)
    assert call.params == {"x": 12, "y": 10, "max_steps": 2}
    assert notes["near"]["to_tile"] == {"x": 12, "y": 10}


def test_交互目标改绑为当前ref():
    old = observe(entities=[entity("@s1:e9", "soil", "耕地", 12, 10, watered=False)])
    new = observe(entities=[entity("@s2:e7", "soil", "耕地", 12, 10, watered=False)])
    call, notes = rebind_call(AbilityCall("use", {"target": "@s1:e9", "expect": "water"}), old, new)
    assert call.params["target"] == "@s2:e7"
    assert notes["target"]["to"] == "@s2:e7"


def test_当前ref已有效则不改动():
    current = observe(entities=[entity("@s2:e7", "soil", "耕地", 12, 10)])
    call, notes = rebind_call(AbilityCall("use", {"target": "@s2:e7"}), current, current)
    assert notes == {}
    assert call.params["target"] == "@s2:e7"


def test_找不到旧目标时保持原样():
    old = observe(entities=[])
    new = observe(entities=[])
    call, notes = rebind_call(move_call("@s1:e9"), old, new)
    assert notes == {}
    assert call.params["near"] == "@s1:e9"


def test_target_id与坐标参数不被改写():
    old = observe(entities=[entity("@s1:e9", "soil", "耕地", 12, 10)])
    new = observe(entities=[entity("@s2:e7", "soil", "耕地", 12, 10)])
    call, notes = rebind_call(AbilityCall("use", {"target": "target:abcd"}), old, new)
    assert notes == {}
    assert call.params["target"] == "target:abcd"
    call, notes = rebind_call(AbilityCall("move_to", {"x": 3, "y": 4}), old, new)
    assert notes == {}
    assert call.params == {"x": 3, "y": 4}


def test_旧ref所在实体种类不同则不乱绑():
    old = observe(entities=[entity("@s1:e9", "soil", "耕地", 12, 10)])
    new = observe(entities=[entity("@s2:e7", "object", "箱子", 12, 10)])
    call, notes = rebind_call(AbilityCall("use", {"target": "@s1:e9"}), old, new)
    assert notes == {}
    assert call.params["target"] == "@s1:e9"
