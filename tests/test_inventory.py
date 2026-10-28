"""制作与容器动作：craft / chest_put / chest_get / ship / place_chest（替身端口）。"""

from __future__ import annotations

import asyncio

import pytest

from stardew_agent.abilities import craft_item_ability, inventory_action_ability
from stardew_agent.contracts.errors import Rejected
from tests.helpers import inventory, menu_row, observe


class FakeInventoryPort:
    def __init__(self, result=None) -> None:
        self.result = result or {"ok": True}
        self.calls: list[tuple] = []

    async def craft(self, item, count=1, **kwargs):
        self.calls.append(("craft", item, count, kwargs.get("action_id")))
        return dict(self.result)

    async def chestput(self, tx, ty, slot, **kwargs):
        self.calls.append(("chestput", tx, ty, slot, kwargs.get("action_id")))
        return dict(self.result)

    async def chestget(self, tx, ty, name="", **kwargs):
        self.calls.append(("chestget", tx, ty, name, kwargs.get("action_id")))
        return dict(self.result)

    async def ship(self, slot, **kwargs):
        self.calls.append(("ship", slot, kwargs.get("action_id")))
        return dict(self.result)

    async def placechest(self, tx, ty, **kwargs):
        self.calls.append(("placechest", tx, ty, kwargs.get("action_id")))
        return dict(self.result)


def _run(delegation, observations):
    progress = None
    for row in observations:
        progress = asyncio.run(delegation.step(row))
        if progress.done:
            break
    return progress


def test_制作以背包数量增加为证据():
    port = FakeInventoryPort()
    spec = craft_item_ability(port)
    before = observe()
    delegation = spec.start(spec.validate({"item": "箱子", "qty": 2}), before, "a1")

    after = observe(inventory_rows=[inventory(0, "箱子", count=2)])
    progress = _run(delegation, [before, after])

    assert progress is not None and progress.state == "completed"
    assert progress.evidence["gained"] == 2
    assert port.calls[0] == ("craft", "箱子", 2, "a1")


def test_放箱子以木材减少为证据():
    port = FakeInventoryPort()
    spec = inventory_action_ability(port)
    before = observe(inventory_rows=[inventory(0, "木材", count=60)])
    delegation = spec.start(spec.validate({"op": "place_chest", "tx": 64, "ty": 18}), before, "a2")

    after = observe(inventory_rows=[inventory(0, "木材", count=10)])
    progress = _run(delegation, [before, after])

    assert progress is not None and progress.state == "completed"
    assert port.calls[0] == ("placechest", 64, 18, "a2")


def test_存箱与发货以背包槽位减少为证据():
    for op, expected in (("chest_put", "chestput"), ("ship", "ship")):
        port = FakeInventoryPort()
        spec = inventory_action_ability(port)
        params = (
            spec.validate({"op": op, "tx": 64, "ty": 18, "slot": 0})
            if op == "chest_put"
            else spec.validate({"op": op, "slot": 0})
        )
        before = observe(inventory_rows=[inventory(0, "石头", count=5)])
        delegation = spec.start(params, before, "a3")
        after = observe(inventory_rows=[])

        progress = _run(delegation, [before, after])

        assert progress is not None and progress.state == "completed", progress
        assert port.calls[0][0] == expected


def test_从箱子取物以背包增加为证据():
    port = FakeInventoryPort()
    spec = inventory_action_ability(port)
    before = observe()
    delegation = spec.start(spec.validate({"op": "chest_get", "tx": 64, "ty": 18, "name": "木材"}), before, "a4")

    after = observe(inventory_rows=[inventory(0, "木材", count=10)])
    progress = _run(delegation, [before, after])

    assert progress is not None and progress.state == "completed"
    assert port.calls[0] == ("chestget", 64, 18, "木材", "a4")


def test_容器动作参数与前提校验():
    spec = inventory_action_ability(FakeInventoryPort())
    with pytest.raises(Rejected):
        spec.validate({"op": "drop"})
    with pytest.raises(Rejected):
        spec.validate({"op": "chest_put", "tx": 1, "ty": 1})
    with pytest.raises(Rejected):
        spec.validate({"op": "ship"})
    with pytest.raises(Rejected):
        spec.validate({"op": "ship", "slot": 1, "unknown": 1})

    checks = spec.check_premises(spec.validate({"op": "ship", "slot": 0}), observe(menu=menu_row(["@s1:m1"])))
    assert any(check.met is False for check in checks)



def test_满包制作被拒绝时保留错误码():
    port = FakeInventoryPort({"ok": False, "reason": "背包满了", "code": "INVENTORY_FULL"})
    spec = craft_item_ability(port)
    observation = observe(inventory_rows=[inventory(0, "木材", 50, itemId="(O)388")])
    delegation = spec.start(spec.validate({"item": "箱子"}), observation, "a-full")

    progress = asyncio.run(delegation.step(observation))

    assert progress.state == "failed"
    assert progress.evidence["error"] == "INVENTORY_FULL"

