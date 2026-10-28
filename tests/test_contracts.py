"""契约测试：未知/不支持/确定为否必须可区分。"""

from __future__ import annotations

import pytest

from stardew_agent.contracts import ActionResult, AbilityCall, Decision, Observation, Progress, SCHEMA_VERSION
from tests.helpers import entity, menu_row, observe, snapshot


def test_观察字段区分观察到未知与不支持():
    observation = observe(entities=[entity("@s1:e1", "tile", "地块", 10, 10)])
    assert observation.field("location").known
    unknown = observation.field("player.health")
    assert unknown.state == "unsupported"
    assert "生命值" in unknown.reason
    missing = observation.field("player.energy")
    assert missing.state == "observed"
    assert observation.field("player.mood").state == "unknown"


def test_缺失字段不会被编造():
    observation = Observation(save_key="save:1", sampled_at=1.0)
    assert observation.field("player.canMove").state == "unknown"
    assert observation.position() == {"x": None, "y": None}
    assert observation.entities == ()


def test_观察序列化包含版本与未知说明():
    observation = observe(truncated=True)
    payload = observation.to_dict()
    assert payload["schema_version"] == SCHEMA_VERSION
    assert "entities.outside_scope" in payload["unknowns"]
    assert payload["location"] == "Farm"


def test_菜单选项会如实进入观察():
    observation = observe(menu=menu_row(["@s1:m1", "@s1:m2"]))
    assert observation.menu is not None
    assert [option["ref"] for option in observation.menu["options"]] == ["@s1:m1", "@s1:m2"]


def test_动作结果未知不算已确定():
    unknown = ActionResult(action_id="a1", ability="move_to", state="unknown", detail="超时")
    assert not unknown.settled
    done = ActionResult(action_id="a1", ability="move_to", state="completed")
    assert done.settled
    with pytest.raises(ValueError):
        ActionResult(action_id="a1", ability="move_to", state="done")


def test_进展状态必须合法():
    assert Progress("running").done is False
    assert Progress("completed").done is True
    with pytest.raises(ValueError):
        Progress("finished")


def test_决策携带绑定观察与受限调用():
    decision = Decision(
        goal="走到田边",
        based_on_sampled_at=12.0,
        based_on_snapshot_id="s1",
        call=AbilityCall(ability="move_to", params={"x": 1, "y": 2}),
        completion={"path": "player.position.x", "op": "==", "value": 1},
    )
    payload = decision.to_dict()
    assert payload["call"]["ability"] == "move_to"
    assert payload["completion"]["op"] == "=="
    assert payload["based_on_snapshot_id"] == "s1"
