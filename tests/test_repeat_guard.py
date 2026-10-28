"""白名单 9：限频算法（窗口、次数、清零、缓存上限、显式参数）。"""

from __future__ import annotations

import pytest

from stardew_agent.repeat_guard import RepeatGuard, RepeatPolicy


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def make_guard(**kwargs) -> tuple[RepeatGuard, Clock]:
    clock = Clock()
    policy = RepeatPolicy(**{"window_seconds": 8.0, "max_runs": 5, **kwargs})
    return RepeatGuard(policy, clock=clock), clock


def test_必须显式给出限频参数():
    with pytest.raises(TypeError):
        RepeatPolicy()  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        RepeatPolicy(window_seconds=-1, max_runs=5)
    with pytest.raises(ValueError):
        RepeatPolicy(window_seconds=8, max_runs=0)


def test_窗口内同一动作被拦下():
    guard, clock = make_guard()
    guard.record("move_to:{\"x\":1}")
    clock.now = 7.9
    verdict = guard.check("move_to:{\"x\":1}")
    assert verdict.allowed is False
    assert verdict.kind == "recent"
    assert "8 秒" in verdict.reason


def test_过了窗口可以再执行():
    guard, clock = make_guard()
    guard.record("k")
    clock.now = 8.0
    assert guard.check("k").allowed is True


def test_不同动作键互不影响():
    guard, _ = make_guard()
    guard.record("move_to:1,1")
    assert guard.check("move_to:2,1").allowed is True


def test_次数用尽后拒绝且类别为limit():
    guard, clock = make_guard()
    for index in range(5):
        clock.now = 10.0 * (index + 1)
        assert guard.check("chop").allowed is True
        guard.record("chop")
    clock.now = 100.0
    verdict = guard.check("chop")
    assert verdict.allowed is False
    assert verdict.kind == "limit"
    assert "5 次" in verdict.reason


def test_清零后重新可以执行():
    guard, clock = make_guard()
    for index in range(5):
        clock.now = 10.0 * (index + 1)
        guard.record("water")
    assert guard.check("water").allowed is False
    guard.reset()
    assert guard.runs("water") == 0
    assert guard.check("water").allowed is True


def test_缓存规模有上限():
    guard, clock = make_guard(max_tracked=8)
    for index in range(40):
        clock.now += 1
        guard.record("k%d" % index)
    assert len(guard._runs) <= 8


def test_白名单能力不参与限频():
    guard, clock = make_guard(exempt_abilities=frozenset({"select_item"}))
    for slot in range(10):
        key = "select_item:{\"slot\":%d}" % slot
        verdict = guard.check(key, ability="select_item")
        assert verdict.allowed is True
        assert verdict.kind == "exempt"
        assert guard.record(key, ability="select_item") == 0
    assert guard.runs("select_item:x") == 0
    clock.now = 1.0
    assert guard.check("select_item:x", ability="select_item").allowed is True


def test_白名单不影响其他能力():
    guard, clock = make_guard(exempt_abilities=frozenset({"select_item"}))
    assert guard.record("use:{\"target\":\"@s1:e1\"}", ability="use") == 1
    clock.now = 1.0
    blocked = guard.check("use:{\"target\":\"@s1:e1\"}", ability="use")
    assert blocked.allowed is False
    assert blocked.kind == "recent"


def test_白名单为空时行为不变():
    guard, clock = make_guard()
    assert guard.record("select_item:1", ability="select_item") == 1
    clock.now = 0.5
    assert guard.check("select_item:1", ability="select_item").allowed is False


def test_白名单必须是能力id():
    with pytest.raises(ValueError):
        RepeatPolicy(window_seconds=8, max_runs=5, exempt_abilities=frozenset({""}))
    policy = RepeatPolicy(window_seconds=8, max_runs=5, exempt_abilities={"select_item"})  # type: ignore[arg-type]
    assert policy.exempts("select_item") is True
    assert policy.exempts("use") is False
    assert policy.exempts(None) is False


def test_快照包含白名单():
    guard, _ = make_guard(exempt_abilities=frozenset({"select_item", "choose_menu_option"}))
    assert guard.snapshot()["policy"]["exempt_abilities"] == ["choose_menu_option", "select_item"]
