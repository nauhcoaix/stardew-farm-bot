"""路径探索能力：挑最近前沿、确认位置、步数上限（替身端口）。"""

from __future__ import annotations

import asyncio

from stardew_agent.abilities import explore_ability
from tests.helpers import menu_row, observe


class FakeMovePort:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def move(self, x=None, y=None, near=None, **kwargs):
        self.calls.append((x, y, kwargs.get("action_id")))
        return {"changed": True}


def _candidates(points):
    return lambda observation: [
        {"mode": "path", "x": x, "y": y, "visited": False} for x, y in points
    ]


def test_探索走向最近的前沿并报告走过点():
    port = FakeMovePort()
    spec = explore_ability(port, _candidates([(20, 20), (11, 10)]))
    start = observe(x=10, y=10)
    delegation = spec.start(spec.validate({"max_steps": 4}), start, "a1")

    asyncio.run(delegation.step(start))
    assert port.calls[0][:2] == (11, 10)
    asyncio.run(delegation.step(observe(x=11, y=10)))  # 到达
    asyncio.run(delegation.step(observe(x=11, y=10)))  # 选下一个
    assert port.calls[1][:2] == (20, 20)
    asyncio.run(delegation.step(observe(x=11, y=10)))  # 没动，重试
    progress = asyncio.run(delegation.step(observe(x=11, y=10)))  # 还没动，放弃该点

    assert progress.state == "completed"
    assert progress.evidence["visited"] == [{"tile": {"x": 11, "y": 10}}]


def test_探索达到步数上限时报告部分完成():
    port = FakeMovePort()
    spec = explore_ability(port, _candidates([(11, 10), (12, 10), (13, 10)]))
    start = observe(x=10, y=10)
    delegation = spec.start(spec.validate({"max_steps": 2}), start, "a2")

    asyncio.run(delegation.step(start))
    asyncio.run(delegation.step(observe(x=11, y=10)))
    asyncio.run(delegation.step(observe(x=11, y=10)))
    progress = asyncio.run(delegation.step(observe(x=11, y=10)))

    assert progress.state == "unknown"
    assert "步数上限" in progress.detail
    assert progress.evidence["partial"] is True
    assert progress.evidence["visited"]


def test_探索没有候选时直接完成():
    port = FakeMovePort()
    spec = explore_ability(port, _candidates([]))
    observation = observe()
    delegation = spec.start(spec.validate({}), observation, "a3")

    progress = asyncio.run(delegation.step(observation))

    assert progress.state == "completed"
    assert port.calls == []


def test_探索前提要求能移动且没有菜单():
    spec = explore_ability(FakeMovePort(), _candidates([]))
    params = spec.validate({})

    checks = spec.check_premises(params, observe(can_move=False))
    assert any(check.met is False for check in checks)

    checks = spec.check_premises(params, observe(menu=menu_row(["@s1:m1"])))
    assert any(check.met is False for check in checks)
