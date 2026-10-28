"""白名单 8：快照身份与目标状态比较纯函数。"""

from __future__ import annotations

from stardew_agent.observation import (
    at_tile,
    dumps,
    from_snapshot,
    position,
    save_key,
    scene,
    semantic,
    target_id,
    target_progress,
    target_state,
)
from tests.helpers import entity, snapshot


def test_存档身份优先使用存档ID():
    assert save_key({"saveId": "abc", "playerId": "p9"}) == "save:abc/player:p9"


def test_没有存档ID时按会话隔离():
    assert save_key({"instanceId": "i1", "saveGeneration": 3}) == "session:i1:3"


def test_目标身份对NPC不包含地块():
    row = entity("@s1:e1", "npc", "Abigail", 3, 4)
    assert target_id("Farm", row) == target_id("Farm", dict(row, tile={"x": 99, "y": 99}))
    soil = entity("@s1:e2", "soil", "耕地", 3, 4, tilled=True)
    assert target_id("Farm", soil) != target_id("Farm", dict(soil, tile={"x": 5, "y": 4}))


def test_语义字段只摘出白名单字段():
    row = entity("@s1:e1", "soil", "耕地", 1, 2, tilled=True, watered=False, ref="ignored")
    assert semantic(row) == {
        "kind": "soil",
        "name": "耕地",
        "tile": {"x": 1, "y": 2},
        "passable": True,
        "tilled": True,
        "watered": False,
    }
    assert "ref" not in semantic(row)


def test_位置与场景来自观察():
    snap = snapshot(x=7, y=8)
    assert position(snap) == {"x": 7, "y": 8}
    assert scene(snap)["location"] == "Farm"
    assert scene(snap)["save"] == "save:save-1/player:player-1"


def test_稳定序列化不受字典顺序影响():
    assert dumps({"b": 1, "a": 2}) == dumps({"a": 2, "b": 1})


def test_观察把嵌套实体提升到顶层():
    observation = from_snapshot(snapshot(entities=[entity("@s1:e1", "soil", "耕地", 10, 10, tilled=True)]))
    assert observation.entities[0]["kind"] == "soil"
    assert observation.raw["entities"] == list(observation.entities)
    assert observation.raw["scope"]["radius"] == 8


def test_地块状态比较能识别浇水变化():
    target = entity("@s1:e1", "soil", "耕地", 10, 10, tilled=True, watered=False)
    before = snapshot(entities=[target])
    after = snapshot(entities=[dict(target, watered=True)])
    assert target_state(before, target) != target_state(after, target)
    assert target_progress("water", before, after, target) is True
    assert target_progress("till", before, after, target) is False
    assert at_tile(after, target)[0]["watered"] is True


def test_换地图时不能声称有进展():
    target = entity("@s1:e1", "soil", "耕地", 10, 10, tilled=True)
    before = snapshot(entities=[target], location="Farm")
    after = snapshot(entities=[dict(target, watered=True)], location="FarmHouse")
    assert target_progress("water", before, after, target) is False
