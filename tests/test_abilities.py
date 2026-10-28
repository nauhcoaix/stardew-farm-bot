"""能力层：参数校验、前提、分段执行与结果验证。"""

from __future__ import annotations

import asyncio

import pytest

from stardew_agent.abilities import (
    AbilityRegistry,
    composite_ability,
    fish_ability,
    interact_ability,
    menu_ability,
    move_ability,
    select_item_ability,
    use_ability,
)
from stardew_agent.contracts.decision import AbilityCall
from stardew_agent.contracts.errors import PremiseFailed, Rejected
from stardew_agent.experience import build_skill
from stardew_agent.observation import target_id
from tests.helpers import entity, menu_row, observe


class FakeMovePort:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def move(self, x=None, y=None, near=None, **kwargs):
        self.calls.append((x, y, near))
        return {"ok": True, "changed": True}


class FakeActionPort:
    def __init__(self, *, result=None) -> None:
        self.result = result or {"changed": True}
        self.calls: list[tuple] = []

    async def select(self, slot, **kwargs):
        self.calls.append(("select", slot))
        return self.result

    async def use(self, ref, **kwargs):
        self.calls.append(("use", ref))
        return self.result

    async def interact(self, ref, **kwargs):
        self.calls.append(("interact", ref))
        return self.result

    async def menu(self, ref, **kwargs):
        self.calls.append(("menu", ref))
        return self.result


def run_step(delegation, observation):
    return asyncio.run(delegation.step(observation))


def test_移动参数只接受坐标或ref之一():
    spec = move_ability(FakeMovePort())
    assert spec.validate({"x": 1, "y": 2}) == {"x": 1, "y": 2, "max_steps": 3}
    with pytest.raises(Rejected):
        spec.validate({"x": 1, "y": 2, "near": "@s1:e1"})
    with pytest.raises(Rejected):
        spec.validate({"x": 1, "y": 2, "speed": "run"})
    with pytest.raises(Rejected):
        spec.validate({"x": -1, "y": 2})


def test_移动到达即完成并记录位置():
    port = FakeMovePort()
    spec = move_ability(port)
    start = observe(x=10, y=10)
    delegation = spec.start(spec.validate({"x": 12, "y": 10, "max_steps": 2}), start, "a1")
    first = run_step(delegation, start)
    assert first.state == "running"
    assert port.calls == [(12, 10, None)]
    arrived = observe(x=12, y=10)
    final = run_step(delegation, arrived)
    assert final.state == "completed"
    assert final.evidence["final_position"] == {"x": 12, "y": 10}


def test_移动没有让位置变化就失败():
    spec = move_ability(FakeMovePort())
    start = observe(x=10, y=10)
    delegation = spec.start(spec.validate({"x": 20, "y": 10, "max_steps": 3}), start, "a2")
    run_step(delegation, start)
    progress = run_step(delegation, observe(x=10, y=10))
    assert progress.state == "failed"
    assert "没有改变位置" in progress.detail


def test_菜单打开或不能移动时前提明确失败():
    spec = move_ability(FakeMovePort())
    params = spec.validate({"x": 1, "y": 2})
    checks = spec.check_premises(params, observe(can_move=False))
    assert [check.met for check in checks if check.kind == "game"][0] is False
    checks = spec.check_premises(params, observe(menu=menu_row(["@s1:m1"])))
    assert any(check.met is False for check in checks)


def test_使用能力用前后观察验证浇水():
    port = FakeActionPort()
    spec = use_ability(port)
    target = entity("@s1:e1", "soil", "耕地", 10, 10, tilled=True, watered=False)
    before = observe(entities=[target])
    params = spec.validate({"target": "@s1:e1", "expect": "water"})
    delegation = spec.start(params, before, "a3")
    assert run_step(delegation, before).state == "running"
    after = observe(entities=[dict(target, watered=True)])
    result = run_step(delegation, after)
    assert result.state == "completed"
    assert result.evidence["verified"] is True
    assert port.calls == [("use", "@s1:e1")]


def test_使用能力没有可验证变化时报未知():
    port = FakeActionPort(result={"changed": False})
    spec = use_ability(port)
    target = entity("@s1:e1", "object", "树", 10, 10)
    before = observe(entities=[target])
    delegation = spec.start(spec.validate({"target": "@s1:e1", "expect": "chop"}), before, "a4")
    run_step(delegation, before)
    result = run_step(delegation, observe(entities=[target]))
    assert result.state == "unknown"
    assert result.evidence["verified"] is None


def test_使用能力现场变化不符合判据时报失败():
    port = FakeActionPort()
    spec = use_ability(port)
    target = entity("@s1:e1", "soil", "耕地", 10, 10, tilled=False)
    before = observe(entities=[target])
    delegation = spec.start(spec.validate({"target": "@s1:e1", "expect": "water"}), before, "a5")
    run_step(delegation, before)
    result = run_step(delegation, observe(entities=[dict(target, tilled=True)]))
    assert result.state == "failed"


def test_使用能力拒绝非法expect与失效ref():
    spec = use_ability(FakeActionPort())
    with pytest.raises(Rejected):
        spec.validate({"target": "@s1:e1", "expect": "magic"})
    with pytest.raises(PremiseFailed) as excinfo:
        spec.start({"target": "@s1:missing", "expect": "any"}, observe(), "a6")
    assert "先 move_to" in str(excinfo.value)


def test_使用能力支持稳定target_id():
    port = FakeActionPort()
    spec = use_ability(port)
    target = entity("@s1:e1", "soil", "耕地", 10, 10, tilled=False, watered=False)
    observation = observe(entities=[target])
    stable = target_id(observation.location, target)
    delegation = spec.start(spec.validate({"target": stable, "expect": "any"}), observation, "a20")
    assert delegation.ref == "@s1:e1"
    run_step(delegation, observation)
    assert port.calls == [("use", "@s1:e1")]


def test_选择物品以槽位变化验证():
    spec = select_item_ability(FakeActionPort())
    before = observe(selected_slot=0)
    delegation = spec.start(spec.validate({"slot": 3}), before, "a7")
    run_step(delegation, before)
    result = run_step(delegation, observe(selected_slot=3))
    assert result.state == "completed"


def test_菜单选项必须属于当前菜单():
    spec = menu_ability(FakeActionPort())
    observation = observe(menu=menu_row(["@s1:m1"]))
    with pytest.raises(PremiseFailed):
        spec.start(spec.validate({"option": "@s1:m9"}), observation, "a8")
    delegation = spec.start(spec.validate({"option": "@s1:m1"}), observation, "a9")
    run_step(delegation, observation)
    result = run_step(delegation, observe(menu=None))
    assert result.state == "completed"


def test_交互能力使用前后观察():
    spec = interact_ability(FakeActionPort(result={"changed": True}))
    before = observe(entities=[entity("@s1:e1", "object", "箱子", 10, 10)])
    delegation = spec.start(spec.validate({"target": "@s1:e1"}), before, "a10")
    run_step(delegation, before)
    result = run_step(delegation, observe(menu=menu_row(["@s1:m1"], identity="chest")))
    assert result.state in ("completed", "unknown")


def test_注册表拒绝未知能力并检查前提():
    registry = AbilityRegistry([move_ability(FakeMovePort())])
    with pytest.raises(Rejected):
        registry.start(AbilityCall("fly", {}), observe())
    call = AbilityCall("move_to", {"x": 1, "y": 1})
    with pytest.raises(PremiseFailed):
        registry.start(call, observe(can_move=False))
    delegation = registry.start(call, observe())
    assert delegation.ability == "move_to"
    assert [descriptor.id for descriptor in registry.descriptors()] == ["move_to"]


class FakeFishingPort:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.state = {"runId": "", "active": False, "state": "idle"}

    async def start(self, limits):
        self.calls.append(("start", dict(limits)))
        self.state = {"runId": "run-1", "active": True, "state": "running", "casts": 0, "fish": 0}
        return dict(self.state, ack={"id": "run-1", "accepted": True})

    async def status(self):
        self.calls.append(("status",))
        return dict(self.state)

    async def stop(self):
        self.calls.append(("stop",))
        self.state = dict(self.state, active=False, state="cancelled", stopReason="user")
        return dict(self.state)


def test_钓鱼能力按runId跟踪到完成():
    port = FakeFishingPort()
    spec = fish_ability(port, validate_limits=lambda limits: None)
    params = spec.validate({"limits": {"maxCasts": 2}})
    observation = observe(energy=200)
    delegation = spec.start(params, observation, "a11")
    assert run_step(delegation, observation).state == "running"
    port.state = {"runId": "run-1", "active": False, "state": "completed", "casts": 2, "fish": 2}
    result = run_step(delegation, observation)
    assert result.state == "completed"
    assert result.evidence["fish"] == 2


def test_钓鱼任务被替换不当成功():
    port = FakeFishingPort()
    spec = fish_ability(port, validate_limits=lambda limits: None)
    observation = observe(energy=200)
    delegation = spec.start(spec.validate({"limits": {"maxCasts": 2}}), observation, "a12")
    run_step(delegation, observation)
    port.state = {"runId": "other", "active": False, "state": "completed"}
    result = run_step(delegation, observation)
    assert result.state == "failed"


def test_钓鱼取消会通知Mod():
    port = FakeFishingPort()
    spec = fish_ability(port, validate_limits=lambda limits: None)
    observation = observe(energy=200)
    delegation = spec.start(spec.validate({"limits": {"maxCasts": 2}}), observation, "a13")
    run_step(delegation, observation)
    asyncio.run(delegation.cancel("运行结束"))
    assert ("stop",) in port.calls


def test_组合技能按步骤执行并核对完成判据():
    port = FakeMovePort()
    registry = AbilityRegistry([move_ability(port)])
    definition = build_skill(
        "skill:walk-east",
        "向东走两格并停在 x=12",
        [{"ability": "move_to", "params": {"x": 12, "y": 10, "max_steps": 2}, "max_steps": 2}],
        abilities=[{"id": "move_to", "version": "1.0.0"}],
        completion={"path": "player.position.x", "op": "==", "value": 12},
    )
    registry.register(composite_ability(definition, registry))
    start = observe(x=10, y=10)
    delegation = registry.start(AbilityCall("skill:walk-east", {}), start)
    assert run_step(delegation, start).state == "running"
    arrived = observe(x=12, y=10)
    run_step(delegation, arrived)
    final = run_step(delegation, arrived)
    assert final.state == "completed"
    assert final.evidence["verified"] is True

