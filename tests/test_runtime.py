"""运行时：预算、所有权、实时循环、取消与结果未知。"""

from __future__ import annotations

import asyncio

import pytest

from stardew_agent.contracts.decision import AbilityCall
from stardew_agent.contracts.errors import BudgetExhausted, ConfigError, OwnershipBusy
from stardew_agent.contracts.results import Progress
from stardew_agent.decision import parse_decision
from stardew_agent.repeat_guard import RepeatGuard, RepeatPolicy
from stardew_agent.runtime import ActionOwnership, AgentLoop, Lease, Recorder, RunBudget, StopSignal
from tests.helpers import entity, observe


def decision_payload(*, ability="move_to", params=None, completion=None, goal="走一步", note=""):
    return {
        "goal": goal,
        "ability": ability,
        "params": params if params is not None else {"x": 11, "y": 10},
        "completion": completion,
        "note": note,
    }


def make_decision(observation, *, ability="move_to", params=None, completion=None, goal="走一步"):
    """把决策绑定到指定观察（用于构造“过期决策”）。"""
    return parse_decision(
        decision_payload(ability=ability, params=params, completion=completion, goal=goal),
        observation=observation,
        validate_params=lambda name, values: dict(values),
    )


class FakeDelegation:
    """按脚本推进的委托：默认先 running 再 completed。"""

    def __init__(self, ability: str = "move_to", progresses=None) -> None:
        self.action_id = "a" + ability
        self.ability = ability
        self.params = {}
        self.cancelled = False
        self.cancel_reason = ""
        self._progresses = list(progresses) if progresses is not None else [Progress("running", "第一步"), Progress("completed", "完成")]

    async def step(self, observation):
        if self.cancelled:
            return Progress("cancelled", self.cancel_reason)
        if self._progresses:
            return self._progresses.pop(0)
        return Progress("running", "还在跑")

    async def cancel(self, reason: str) -> None:
        self.cancelled = True
        self.cancel_reason = reason


class Harness:
    def __init__(self, decisions, *, requests=None, guard=None, observes=None, progresses=None):
        self.observations = observe(x=10, y=10)
        self._pending = list(decisions)
        self.delegations: list[FakeDelegation] = []
        self.builds = 0
        self.stop = StopSignal()
        self.budget = RunBudget(
            wall_clock_seconds=30, model_requests=requests if requests is not None else max(6, len(self._pending) + 4)
        )
        self.recorder = Recorder()
        self.ownership = ActionOwnership()
        self.guard = guard
        self.progresses = progresses
        self.loop = AgentLoop(
            observe=self.observe,
            request_decision=self.decide,
            build_delegation=self.build,
            budget=self.budget,
            ownership=self.ownership,
            recorder=self.recorder,
            guard=guard,
            stop=self.stop,
            poll_seconds=0.001,
            step_interval_seconds=0.001,
            idle_log_seconds=0.0,
        )

    async def observe(self):
        return self.observations

    async def decide(self, observation, last_result):
        if not self._pending:
            return None
        item = self._pending.pop(0)
        if callable(item):
            return await item(self, observation, last_result)
        if isinstance(item, dict):
            # 与真实接线一致：决策必须绑定本次提供的观察。
            return parse_decision(
                item, observation=observation, validate_params=lambda name, values: dict(values)
            )
        return item

    def build(self, call: AbilityCall, observation):
        self.builds += 1
        delegation = FakeDelegation(call.ability, self.progresses)
        self.delegations.append(delegation)
        return delegation

    async def run(self, **kwargs):
        return await self.loop.run(**kwargs)


def kinds(recorder: Recorder) -> list[str]:
    return [event.kind for event in recorder.events]


def test_预算必须显式给出且不接受无限值():
    with pytest.raises(ConfigError):
        RunBudget(wall_clock_seconds=None, model_requests=5)  # type: ignore[arg-type]
    with pytest.raises(ConfigError):
        RunBudget(wall_clock_seconds=10, model_requests=None)  # type: ignore[arg-type]
    with pytest.raises(ConfigError):
        RunBudget(wall_clock_seconds=0, model_requests=5)
    budget = RunBudget(wall_clock_seconds=10, model_requests=2)
    budget.start()
    budget.consume_model_request(tokens=10)
    budget.consume_model_request()
    assert budget.expire_reason() == "model_requests"
    assert budget.snapshot()["requests_left"] == 0
    with pytest.raises(BudgetExhausted):
        budget.consume_model_request()


def test_所有权同一时刻只有一个执行者():
    async def scenario():
        ownership = ActionOwnership(clock=lambda: 1.0)
        lease = await ownership.acquire("agent", "a1")
        with pytest.raises(OwnershipBusy):
            await ownership.acquire("fishing", "a2")
        with pytest.raises(OwnershipBusy):
            await ownership.release(Lease(owner="fishing", action_id="a2", acquired_at=0.0))
        await ownership.release(lease)
        assert ownership.current is None
        await ownership.release(lease)  # 重复释放是幂等的清理动作
        assert await ownership.force_release() is None

    asyncio.run(scenario())


def test_一次委托完成并释放控制():
    harness = Harness([decision_payload()], requests=6)
    summary = asyncio.run(harness.run(max_actions=1))
    assert harness.builds == 1
    assert summary.actions == 1
    assert summary.completed == 1
    assert harness.ownership.current is None
    assert harness.delegations[0].cancelled is False
    events = kinds(harness.recorder)
    assert "action_started" in events
    assert "action_finished_completed" in events
    assert harness.recorder.metrics()["counts"]["action_finished_completed"] == 1


def test_结果未知会被如实记录():
    harness = Harness(
        [decision_payload()],
        requests=6,
        progresses=[Progress("unknown", "写操作超时，先观察")],
    )
    summary = asyncio.run(harness.run(max_actions=1))
    assert summary.unknown == 1
    finished = [event for event in harness.recorder.events if event.kind == "action_finished_unknown"]
    assert finished and finished[0].status == "unknown"
    assert harness.recorder.metrics()["unknown_results"] == 1


def test_模型判据能否决自报完成():
    harness = Harness(
        [
            decision_payload(
                completion={"path": "player.position.x", "op": "==", "value": 99},
            )
        ],
        requests=6,
    )
    summary = asyncio.run(harness.run(max_actions=1))
    assert summary.failed == 1
    result = summary.last_result
    assert result is not None
    assert result["evidence"]["completion_predicate"]["met"] is False


def test_停止会取消在途委托并释放控制():
    harness = Harness([decision_payload()], requests=8, progresses=[Progress("running", "长任务")])

    async def scenario():
        task = asyncio.create_task(harness.loop.run(max_actions=5))
        for _ in range(60):
            await asyncio.sleep(0.005)
            if harness.delegations:
                break
        harness.stop.request("测试停止")
        return await asyncio.wait_for(task, timeout=5)

    summary = asyncio.run(scenario())
    assert summary.stop_reason.startswith("stop:")
    assert summary.cancelled == 1
    assert harness.delegations[0].cancelled is True
    assert harness.ownership.current is None
    assert "action_finished_cancelled" in kinds(harness.recorder)


def test_过期决策被丢弃且不下发动作():
    stale = make_decision(observe(location="Town"))
    harness = Harness([stale], requests=3)
    summary = asyncio.run(harness.run(max_actions=5))
    assert harness.builds == 0
    assert summary.discarded == 1
    assert "decision_discarded" in kinds(harness.recorder)
    assert harness.recorder.metrics()["discarded_decisions"] == 1


def test_重复动作被限频拦下():
    guard = RepeatGuard(RepeatPolicy(window_seconds=0, max_runs=1))
    harness = Harness([decision_payload(), decision_payload()], requests=6, guard=guard)
    summary = asyncio.run(harness.run(max_actions=2))
    assert summary.actions == 1
    assert summary.blocked == 1
    assert "repeat_blocked" in kinds(harness.recorder)


def test_没有可执行委托时记录等待而不是擅自开新任务():
    harness = Harness([], requests=3)
    summary = asyncio.run(harness.run(max_actions=1))
    assert summary.actions == 0
    assert "idle_wait" in kinds(harness.recorder)
    assert "no_action" in kinds(harness.recorder) or "decision_empty" in kinds(harness.recorder)
    assert summary.stop_reason.startswith("budget:")
    assert harness.recorder.metrics()["idle_seconds"] >= 0


def test_停止会取消在途决策请求():
    async def slow_decision(harness, observation, last_result):
        harness.stop.request("模型还在思考")
        await asyncio.sleep(5)
        return None

    harness = Harness([slow_decision], requests=1)
    summary = asyncio.run(asyncio.wait_for(harness.loop.run(max_actions=1), timeout=3))
    assert summary.stop_reason.startswith("stop:")
    assert "decision_cancelled" in kinds(harness.recorder)


def test_观察失败不会让循环崩溃():
    harness = Harness([decision_payload()], requests=6)
    attempts = {"n": 0}

    async def flaky():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("桥断了")
        return harness.observations

    harness.loop.observe = flaky
    summary = asyncio.run(harness.loop.run(max_actions=1))
    assert "observe_failed" in kinds(harness.recorder)
    assert summary.actions >= 1


def test_旧ref会在执行前改绑为坐标():
    old = observe(entities=[entity("@s1:e9", "soil", "耕地", 12, 10)])
    new = observe(entities=[entity("@s2:e1", "soil", "耕地", 12, 10)])
    harness = Harness([], requests=8)
    harness.observations = old

    async def decide(harness_ref, observation, last_result):
        # 模型思考期间现场又观察了几次：ref 已经换成 @s2 前缀。
        harness_ref.observations = new
        return parse_decision(
            {"goal": "走到田边", "ability": "move_to", "params": {"near": "@s1:e9"}},
            observation=observation,
            validate_params=lambda name, values: dict(values),
        )

    harness._pending = [decide]
    summary = asyncio.run(harness.run(max_actions=1))

    assert summary.completed == 1
    started = [event for event in harness.recorder.events if event.kind == "action_started"]
    assert started, "动作应当被下发"
    params = started[0].payload["params"]
    assert params.get("x") == 12 and params.get("y") == 10
    assert "near" not in params
    assert "decision_rebound" in kinds(harness.recorder)

