"""能力层：参数校验、前提、分段执行与结果验证。"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from stardew_agent.abilities import (
    AbilityRegistry,
    composite_ability,
    farm_routine_ability,
    fish_ability,
    interact_ability,
    menu_ability,
    move_ability,
    select_item_ability,
    use_ability,
)
from stardew_agent.contracts.decision import AbilityCall
from stardew_agent.contracts.errors import AgentError, PremiseFailed, Rejected
from stardew_agent.experience import build_skill
from stardew_agent.observation import target_id
from tests.helpers import entity, inventory, menu_row, observe


class FakeMovePort:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.kwargs: list[dict] = []

    async def move(self, x=None, y=None, near=None, **kwargs):
        self.calls.append((x, y, near))
        self.kwargs.append(dict(kwargs))
        return {"ok": True, "changed": True}


class PathBlocked(AgentError):
    code = "PATH_BLOCKED"


class BlockedMovePort:
    """模拟 mod 明确返回 PATH_BLOCKED：应当作为失败结果交给模型，而不是抛出去。"""

    async def move(self, x=None, y=None, near=None, **kwargs):
        raise PathBlocked("PATH_BLOCKED: 起点 (5,10) → 目标 (40,10)")


class FakeActionPort:
    def __init__(self, *, result=None) -> None:
        self.result = result or {"changed": True}
        self.calls: list[tuple] = []
        self.kwargs: list[dict] = []

    async def select(self, slot, **kwargs):
        self.calls.append(("select", slot))
        self.kwargs.append(dict(kwargs))
        return self.result

    async def use(self, ref, **kwargs):
        self.calls.append(("use", ref))
        self.kwargs.append(dict(kwargs))
        return self.result

    async def interact(self, ref, **kwargs):
        self.calls.append(("interact", ref))
        self.kwargs.append(dict(kwargs))
        return self.result

    async def menu(self, ref, **kwargs):
        self.calls.append(("menu", ref))
        self.kwargs.append(dict(kwargs))
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
        spec.validate({"x": -2, "y": 2})


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


def test_寻路失败作为失败结果返回():
    spec = move_ability(BlockedMovePort())
    start = observe(x=5, y=10)
    delegation = spec.start(spec.validate({"x": 40, "y": 10}), start, "a-blocked")
    result = run_step(delegation, start)
    assert result.state == "failed"
    assert result.evidence["error"] == "PATH_BLOCKED"
    assert "走不过去" in result.detail


def test_出口切图算移动完成而不是失败():
    port = FakeMovePort()
    spec = move_ability(port)
    door = entity("@s1:e9", "exit", "出口 → Farm", 5, 9, action="Warp 64 15 Farm")
    inside = observe(x=5, y=10, location="FarmHouse", entities=[door])
    delegation = spec.start(spec.validate({"near": "@s1:e9", "max_steps": 2}), inside, "a-exit")
    assert run_step(delegation, inside).state == "running"
    outside = observe(x=64, y=15, location="Farm", entities=[])
    result = run_step(delegation, outside)
    assert result.state == "completed"
    assert "新场景" in result.detail
    assert result.evidence["location"] == "Farm"
    assert result.evidence["exit_goal"] is True


def test_站在门口没切图不算到达():
    port = FakeMovePort()
    spec = move_ability(port)
    door = entity("@s1:e9", "exit", "出口 → Farm", 5, 9)
    inside = observe(x=5, y=10, location="FarmHouse", entities=[door])
    delegation = spec.start(spec.validate({"near": "@s1:e9", "max_steps": 2}), inside, "a-exit2")
    run_step(delegation, inside)
    result = run_step(delegation, inside)
    assert result.state == "failed"
    assert "门可能没开" in result.detail


def test_坐标目标相邻不再算到达():
    port = FakeMovePort()
    spec = move_ability(port)
    start = observe(x=11, y=10)
    delegation = spec.start(spec.validate({"x": 12, "y": 10}), start, "a-exact")
    assert run_step(delegation, start).state == "running"
    assert port.calls == [(12, 10, None)]


def test_坐标目标格不可站立时接受相邻格():
    port = FakeMovePort()
    spec = move_ability(port)
    tree = entity("@s1:e1", "object", "树", 12, 10, passable=False)
    start = observe(x=11, y=10, entities=[tree])
    delegation = spec.start(spec.validate({"x": 12, "y": 10}), start, "a-adjacent")
    assert run_step(delegation, start).state == "completed"
    assert port.calls == []


def test_坐标负一表示保持当前轴():
    port = FakeMovePort()
    spec = move_ability(port)
    params = spec.validate({"x": -1, "y": 15})
    assert params == {"x": -1, "y": 15, "max_steps": 3}
    both = spec.validate({"x": -1, "y": -1})
    assert both == {"x": -1, "y": -1, "max_steps": 3}
    start = observe(x=10, y=10)
    delegation = spec.start(params, start, "a-axis")
    assert delegation.target == (10, 15)
    assert run_step(delegation, start).state == "running"
    assert port.calls == [(10, 15, None)]
    stay = spec.start(both, start, "a-stay")
    assert stay.target == (10, 10)
    assert run_step(stay, start).state == "completed"


def test_坐标移动中途切图也按完成处理():
    port = FakeMovePort()
    spec = move_ability(port)
    start = observe(x=5, y=10, location="FarmHouse")
    delegation = spec.start(spec.validate({"x": 5, "y": 5}), start, "a-warp")
    assert delegation.exit_goal is False
    run_step(delegation, start)
    outside = observe(x=64, y=15, location="Farm")
    result = run_step(delegation, outside)
    assert result.state == "completed"
    assert "切换了地图" in result.detail


def test_坐标落在出口格也会等切图():
    port = FakeMovePort()
    spec = move_ability(port)
    door = entity("@s1:e9", "exit", "出口 → Farm", 5, 9)
    inside = observe(x=5, y=10, location="FarmHouse", entities=[door])
    delegation = spec.start(spec.validate({"x": 5, "y": 9, "max_steps": 2}), inside, "a-exit3")
    assert delegation.exit_goal is True
    run_step(delegation, inside)
    outside = observe(x=64, y=15, location="Farm", entities=[])
    assert run_step(delegation, outside).state == "completed"


def test_移动前提会说明_canMove_锁定原因():
    spec = move_ability(FakeMovePort())
    params = spec.validate({"x": 1, "y": 2})
    checks = spec.check_premises(params, observe(can_move=False, can_move_lock="menu"))
    detail = [check.detail for check in checks if check.kind == "game"][0]
    assert "player.canMove=False" in detail
    assert "menu" in detail


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


def _routine_registry(*, expose_mechanics: bool = False):
    """装一套最小注册表：底层动作 + farm_routine。"""
    move_port = FakeMovePort()
    action_port = FakeActionPort()
    registry = AbilityRegistry()
    registry.register(move_ability(move_port))
    select = select_item_ability(action_port)
    use = use_ability(action_port)
    if not expose_mechanics:
        select = replace(select, exposed=False)
        use = replace(use, exposed=False)
    registry.register(select)
    registry.register(use)
    registry.register(interact_ability(action_port))
    registry.register(farm_routine_ability(registry))
    return registry, move_port, action_port


def test_机械动作默认不对模型开放():
    registry, _move, _action = _routine_registry()
    assert [descriptor.id for descriptor in registry.descriptors()] == [
        "farm_routine",
        "interact",
        "move_to",
    ]
    assert [descriptor.id for descriptor in registry.all_descriptors()] == [
        "farm_routine",
        "interact",
        "move_to",
        "select_item",
        "use",
    ]
    with pytest.raises(Rejected) as info:
        registry.validate_model_params("use", {"target": "@s1:e1"})
    assert "farm_routine" in str(info.value)
    assert registry.validate_model_params("farm_routine", {"task": "water"})["task"] == "water"

    debug, _m, _a = _routine_registry(expose_mechanics=True)
    assert "use" in [descriptor.id for descriptor in debug.descriptors()]


def test_农活例行程序自己选工具并逐块浇水():
    registry, _move_port, action_port = _routine_registry()
    spec = registry.get("farm_routine")
    soil_a = entity("@s1:e1", "soil", "耕地", 11, 10, tilled=True, watered=False, needsWater=True)
    soil_b = entity("@s1:e2", "soil", "耕地", 12, 10, tilled=True, watered=False, needsWater=True)
    can = inventory(2, "浇水壶", tool="WateringCan", water=40, waterCapacity=40, category=-99)
    params = spec.validate({"task": "water", "max_targets": 4})
    before = observe(entities=[soil_a, soil_b], inventory_rows=[can], selected_slot=0)
    delegation = spec.start(params, before, "a20")

    done_a = dict(soil_a, watered=True, needsWater=False)
    both = observe(entities=[soil_a, soil_b], inventory_rows=[can], selected_slot=2)
    observations = [
        before,  # 选工具
        both,  # 下发 select
        both,  # 校验 select → 开始走向 A
        observe(x=11, entities=[soil_a, soil_b], inventory_rows=[can], selected_slot=2),  # 到达 A
        observe(x=11, entities=[soil_a, soil_b], inventory_rows=[can], selected_slot=2),  # 下发 use(A)
        observe(x=11, entities=[done_a, soil_b], inventory_rows=[can], selected_slot=2),  # 校验 A，转向 B
        observe(x=12, entities=[done_a, soil_b], inventory_rows=[can], selected_slot=2),  # 到达 B
        observe(x=12, entities=[done_a, soil_b], inventory_rows=[can], selected_slot=2),  # 下发 use(B)
        observe(
            x=12,
            entities=[done_a, dict(soil_b, watered=True, needsWater=False)],
            inventory_rows=[can],
            selected_slot=2,
        ),  # 校验 B
    ]
    progress = None
    for row in observations:
        progress = asyncio.run(delegation.step(row))
        if progress.done:
            break

    assert progress is not None and progress.state == "completed"
    assert [call for call in action_port.calls if call[0] == "select"] == [("select", 2)]
    assert [call for call in action_port.calls if call[0] == "use"] == [("use", "@s1:e1"), ("use", "@s1:e2")]
    assert progress.evidence["done"] == [
        {"tile": {"x": 11, "y": 10}, "task": "water"},
        {"tile": {"x": 12, "y": 10}, "task": "water"},
    ]


def test_收获用交互而不是挥动工具():
    registry, _move_port, action_port = _routine_registry()
    spec = registry.get("farm_routine")
    crop = entity(
        "@s1:e1", "soil", "作物", 11, 10, tilled=True, watered=True, harvestable=True, crop=24, cropPhase=4
    )
    hoe = inventory(1, "锄头", tool="Hoe")
    harvested = inventory(2, "防风草", count=1)
    params = spec.validate({"task": "harvest", "max_targets": 2})
    before = observe(entities=[crop], inventory_rows=[hoe], selected_slot=1)
    delegation = spec.start(params, before, "a21")

    after = observe(entities=[], inventory_rows=[hoe, harvested], selected_slot=1)
    observations = [before, before, before, after, after]
    progress = None
    for row in observations:
        progress = asyncio.run(delegation.step(row))
        if progress.done:
            break

    assert progress is not None and progress.state == "completed"
    assert [call for call in action_port.calls if call[0] == "interact"] == [("interact", "@s1:e1")]
    assert not [call for call in action_port.calls if call[0] == "use"]
    assert not [call for call in action_port.calls if call[0] == "select"]
    assert progress.evidence["done"] == [{"tile": {"x": 11, "y": 10}, "task": "harvest"}]


def test_砍树会切斧头并用砍伐判据():
    registry, _move_port, action_port = _routine_registry()
    spec = registry.get("farm_routine")
    tree = entity("@s1:e1", "terrain", "Tree", 11, 10, terrain="Tree", passable=False)
    axe = inventory(3, "斧头", tool="Axe")
    params = spec.validate({"task": "chop", "max_targets": 2})
    before = observe(entities=[tree], inventory_rows=[axe], selected_slot=0)
    picked = observe(entities=[tree], inventory_rows=[axe], selected_slot=3)
    delegation = spec.start(params, before, "a22")

    progress = None
    for row in [before, picked, picked, picked, picked, picked]:
        progress = asyncio.run(delegation.step(row))
        if progress.done:
            break

    assert progress is not None and progress.state == "completed"
    assert ("select", 3) in action_port.calls
    assert [call for call in action_port.calls if call[0] == "use"] == [("use", "@s1:e1")]
    assert progress.evidence["done"] == [{"tile": {"x": 11, "y": 10}, "task": "chop"}]


def test_清理与挖矿的目标识别和工具前提():
    registry, _move, _action = _routine_registry()
    spec = registry.get("farm_routine")
    weeds = entity("@s1:e1", "object", "杂草", 10, 11, objName="Weeds", objType="Object", passable=False)
    grass = entity("@s1:e2", "terrain", "Grass", 11, 11, terrain="Grass", passable=True)
    twig = entity("@s1:e4", "object", "嫩枝", 13, 11, objName="Twig", objType="Object", passable=False)
    stone = entity("@s1:e3", "object", "石头", 12, 11, objName="Stone", objType="Object", passable=False)
    scythe = inventory(4, "镰刀", tool="MeleeWeapon")
    axe = inventory(3, "斧头", tool="Axe")
    pick = inventory(5, "镐", tool="Pickaxe")
    obs = observe(entities=[weeds, grass, twig, stone], inventory_rows=[scythe, axe, pick])

    clear_checks = spec.check_premises(spec.validate({"task": "clear"}), obs)
    assert any(check.met is True and "可处理清理目标 3 个" in check.detail for check in clear_checks)
    mine_checks = spec.check_premises(spec.validate({"task": "mine"}), obs)
    assert any(check.met is True and "可处理挖矿目标 1 个" in check.detail for check in mine_checks)


def test_auto不把砍树清障挖矿算作农活():
    registry, _move, _action = _routine_registry()
    spec = registry.get("farm_routine")
    tree = entity("@s1:e1", "terrain", "Tree", 10, 11, terrain="Tree", passable=False)
    stone = entity("@s1:e2", "object", "石头", 11, 11, objName="Stone", objType="Object", passable=False)
    axe = inventory(3, "斧头", tool="Axe")
    pick = inventory(5, "镐", tool="Pickaxe")
    obs = observe(entities=[tree, stone], inventory_rows=[axe, pick])

    checks = spec.check_premises(spec.validate({"task": "auto"}), obs)
    assert any(check.met is False and "没有可浇" in check.detail for check in checks)


def test_补水前提允许空壶_满壶才拒绝():
    registry, _move, _action = _routine_registry()
    spec = registry.get("farm_routine")
    params = spec.validate({"task": "refill"})
    water = entity("@s1:e9", "water", "水源", 11, 10, refillable=True, passable=False)

    empty = observe(
        entities=[water],
        inventory_rows=[inventory(2, "喷壶", tool="WateringCan", water=0, waterCapacity=40)],
    )
    checks = spec.check_premises(params, empty)
    assert not [check for check in checks if check.met is False], checks
    assert any("可以补水" in check.detail for check in checks)

    full = observe(
        entities=[water],
        inventory_rows=[inventory(2, "喷壶", tool="WateringCan", water=40, waterCapacity=40)],
    )
    checks = spec.check_premises(params, full)
    assert any(check.met is False and "已满" in check.detail for check in checks)


def test_补水成功以水量变化作为证据():
    registry, _move, _action = _routine_registry()
    spec = registry.get("farm_routine")
    water = entity("@s1:e9", "water", "水源", 11, 10, refillable=True, passable=False)
    can = inventory(2, "喷壶", tool="WateringCan", water=0, waterCapacity=40)
    params = spec.validate({"task": "refill", "max_targets": 1})
    before = observe(entities=[water], inventory_rows=[can], selected_slot=2)
    delegation = spec.start(params, before, "a23")

    refilled = observe(
        entities=[water],
        inventory_rows=[inventory(2, "喷壶", tool="WateringCan", water=40, waterCapacity=40)],
        selected_slot=2,
    )
    progress = None
    for row in [before, before, before, refilled]:
        progress = asyncio.run(delegation.step(row))
        if progress.done:
            break

    assert progress is not None and progress.state == "completed"
    assert progress.evidence["done"] == [{"tile": {"x": 11, "y": 10}, "task": "refill"}]


def test_auto在水壶空时先去补水():
    registry, _move, _action = _routine_registry()
    spec = registry.get("farm_routine")
    crop = entity("@s1:e1", "soil", "作物", 12, 10, tilled=True, watered=False, needsWater=True)
    water = entity("@s1:e9", "water", "水源", 10, 11, refillable=True, passable=False)
    empty = inventory(2, "喷壶", tool="WateringCan", water=0, waterCapacity=40)
    observation = observe(entities=[crop, water], inventory_rows=[empty], selected_slot=2)

    delegation = spec.start(spec.validate({"task": "auto"}), observation, "a24")
    assert delegation.task == "refill"


def test_补水成功后水壶满了就停止():
    registry, _move, action_port = _routine_registry()
    spec = registry.get("farm_routine")
    water_a = entity("@s1:e9", "water", "水源", 11, 10, refillable=True, passable=False)
    water_b = entity("@s1:e10", "water", "水源", 12, 10, refillable=True, passable=False)
    can = inventory(2, "喷壶", tool="WateringCan", water=0, waterCapacity=40)
    params = spec.validate({"task": "refill", "max_targets": 4})
    before = observe(entities=[water_a, water_b], inventory_rows=[can], selected_slot=2)
    delegation = spec.start(params, before, "a25")

    refilled = observe(
        entities=[water_a, water_b],
        inventory_rows=[inventory(2, "喷壶", tool="WateringCan", water=40, waterCapacity=40)],
        selected_slot=2,
    )
    progress = None
    for row in [before, before, before, refilled]:
        progress = asyncio.run(delegation.step(row))
        if progress.done:
            break

    assert progress is not None and progress.state == "completed"
    assert len(progress.evidence["done"]) == 1
    assert progress.evidence["failed"] == []
    assert [call for call in action_port.calls if call[0] == "use"] == [("use", "@s1:e9")]


def test_有作物要照料时不做破坏性任务():
    registry, _move, _action = _routine_registry()
    spec = registry.get("farm_routine")
    crop = entity("@s1:e1", "soil", "作物", 11, 10, tilled=True, needsWater=True, watered=False)
    can = inventory(2, "浇水壶", tool="WateringCan", water=40, waterCapacity=40)
    observation = observe(entities=[crop], inventory_rows=[can], selected_slot=2)

    checks = spec.check_premises(spec.validate({"task": "clear"}), observation)
    blocking = [check for check in checks if check.met is False and check.kind == "user"]
    assert blocking and "先照顾作物" in blocking[0].detail

    idle = observe(entities=[entity("@s1:e2", "tile", "地块", 10, 11)])
    checks = spec.check_premises(spec.validate({"task": "clear"}), idle)
    assert not [check for check in checks if check.kind == "user" and check.met is False]


def test_破坏性任务一次最多八个目标():
    registry, _move, _action = _routine_registry()
    spec = registry.get("farm_routine")
    assert spec.validate({"task": "clear"})["max_targets"] == 6
    assert spec.validate({"task": "mine", "max_targets": 8})["max_targets"] == 8
    with pytest.raises(Rejected):
        spec.validate({"task": "chop", "max_targets": 12})


def test_农活前提在没有目标或缺工具时明确失败():
    registry, _move, _action = _routine_registry()
    spec = registry.get("farm_routine")
    params = spec.validate({"task": "water"})

    no_crops = observe(entities=[entity("@s1:e2", "tile", "地块", 10, 11)], inventory_rows=[inventory(2, "浇水壶", tool="WateringCan")])
    checks = spec.check_premises(params, no_crops)
    assert any(check.met is False and "没有可浇水" in check.detail for check in checks)

    soil = entity("@s1:e3", "soil", "耕地", 10, 12, tilled=True, needsWater=True, watered=False)
    dry = observe(entities=[soil], inventory_rows=[inventory(2, "浇水壶", tool="WateringCan", water=0)])
    checks = spec.check_premises(params, dry)
    assert any(check.met is False and "没水" in check.detail for check in checks)

    no_tool = observe(entities=[soil], inventory_rows=[inventory(0, "锄头", tool="Hoe")])
    checks = spec.check_premises(params, no_tool)
    assert any(check.met is False and "没有水壶" in check.detail for check in checks)



def test_委托把稳定动作id传给端口():
    move_port = FakeMovePort()
    spec = move_ability(move_port)
    start = observe(x=10, y=10)
    delegation = spec.start(spec.validate({"x": 12, "y": 10, "max_steps": 2}), start, "a-fixed")
    run_step(delegation, start)
    assert move_port.kwargs[-1]["action_id"] == "a-fixed"
    assert move_port.kwargs[-1]["timeout_ms"] == 30000

    action_port = FakeActionPort()
    use_spec = use_ability(action_port)
    soil = entity("@s1:e1", "soil", "耕地", 10, 10, tilled=True, watered=False)
    observation = observe(entities=[soil])
    delegation = use_spec.start(
        use_spec.validate({"target": "@s1:e1", "expect": "water"}), observation, "a-use"
    )
    run_step(delegation, observation)
    assert action_port.kwargs[-1]["action_id"] == "a-use"


def test_农活默认批量更大以减少请求次数():
    registry, _move, _action = _routine_registry()
    spec = registry.get("farm_routine")
    params = spec.validate({})
    assert params["max_targets"] == 16
    assert params["max_seconds"] == 90.0


def test_农活委托取消后不再下发写操作():
    registry, _move, action_port = _routine_registry()
    spec = registry.get("farm_routine")
    soil = entity("@s1:e1", "soil", "耕地", 11, 10, tilled=True, needsWater=True)
    can = inventory(2, "浇水壶", tool="WateringCan", water=40, waterCapacity=40)
    observation = observe(entities=[soil], inventory_rows=[can], selected_slot=2)
    delegation = spec.start(spec.validate({"task": "water"}), observation, "a26")

    asyncio.run(delegation.step(observation))  # 开始走向目标
    asyncio.run(delegation.cancel("测试停止"))
    progress = asyncio.run(delegation.step(observation))
    assert progress.state == "cancelled"
    assert action_port.calls == []


def test_每个能力都有描述与可执行入口():
    registry, _move, _action = _routine_registry()
    observation = observe(
        entities=[entity("@s1:e1", "soil", "耕地", 11, 10, tilled=True, needsWater=True)],
        inventory_rows=[inventory(2, "浇水壶", tool="WateringCan", water=40, waterCapacity=40)],
        menu=menu_row(["@s1:m1"]),
        selected_slot=2,
    )
    minimal = {
        "move_to": {"x": 10, "y": 11},
        "select_item": {"slot": 2},
        "use": {"target": "@s1:e1"},
        "interact": {"target": "@s1:e1"},
        "choose_menu_option": {"option": "@s1:m1"},
        "farm_routine": {},
    }
    started = 0
    for descriptor in registry.all_descriptors():
        spec = registry.get(descriptor.id)
        assert descriptor.version and descriptor.summary
        assert isinstance(dict(descriptor.params), dict)
        with pytest.raises(Rejected):
            spec.validate({"__unexpected__": True})
        params = spec.validate(minimal[descriptor.id])
        checks = spec.check_premises(params, observation)
        assert all(check.kind in ("game", "user", "budget", "advice") for check in checks)
        try:
            delegation = spec.start(params, observation, "a-contract")
        except (Rejected, PremiseFailed):
            continue
        assert delegation.action_id == "a-contract"
        assert hasattr(delegation, "step") and hasattr(delegation, "cancel")
        started += 1
    assert started >= 4

