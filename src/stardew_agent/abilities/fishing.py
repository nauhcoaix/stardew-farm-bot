"""钓鱼能力：把一竿委托交给独立钓鱼 Mod，并读取真实结果。

边界（与白名单一致）：本能力不寻找岸边、不替模型选目的地、不开启"额外钓鱼任务"；
它只在模型已经决定时，按给定时长/竿数/收竿时间委托当前站位的钓鱼，并把 Mod 报告的
状态、竿数、收获与停止原因变成证据。

Mod 内部的逐帧小游戏控制是预编程执行器，不算机器人新学会的策略。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping, Protocol

from ..contracts.ability import AbilityDescriptor
from ..contracts.errors import AgentError, Rejected
from ..contracts.observation import Observation
from ..contracts.results import Progress
from .base import AbilitySpec, DelegationBase, PremiseCheck, reject_unknown_params


class FishingPort(Protocol):
    """钓鱼邮箱的窄接口；实现方负责把同步文件操作放进线程。"""

    async def start(self, limits: Mapping[str, Any]) -> Mapping[str, Any]: ...

    async def status(self) -> Mapping[str, Any]: ...

    async def stop(self) -> Mapping[str, Any]: ...


def _min_energy(params: Mapping[str, Any]) -> int:
    return int((params.get("limits") or {}).get("minEnergy") or 0)


def _default_validator(limits: Mapping[str, Any]) -> None:
    from ..fishing_client.client import validate_limits

    validate_limits(limits)


class FishingDelegation(DelegationBase):
    """一次钓鱼委托：交给 Mod，按 runId 跟踪到结束。"""

    def __init__(
        self,
        port: FishingPort,
        params: Mapping[str, Any],
        observation: Observation,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__("fish", params, clock=clock)
        self.port = port
        self.limits = dict(params["limits"])
        self.run_id: str | None = None
        self.record("limits", self.limits)
        self.record("start_location", observation.location)

    async def step(self, observation: Observation) -> Progress:
        if self.cancelled:
            return self.cancelled_progress()
        if self.run_id is None:
            return await self._start(observation)
        try:
            status = await self.port.status()
        except AgentError as exc:
            return self.finish("unknown", "读取钓鱼状态失败：%s" % exc, error=exc.code)
        if str(status.get("runId") or "") != self.run_id:
            return self.finish("failed", "钓鱼任务已被替换或 Mod 重启，停止跟踪旧任务")
        if status.get("active"):
            return Progress(
                "running",
                "钓鱼进行中：阶段=%s 竿=%s 鱼=%s" % (status.get("phase"), status.get("casts"), status.get("fish")),
                {"state": status.get("state"), "casts": status.get("casts"), "fish": status.get("fish")},
            )
        state = str(status.get("state") or "")
        evidence = {
            "fishing_state": state,
            "casts": status.get("casts"),
            "fish": status.get("fish"),
            "stopReason": status.get("stopReason"),
            "limits": self.limits,
        }
        if state == "completed":
            return self.finish("completed", "钓鱼任务完成（Mod 报告）", **evidence)
        if state == "cancelled":
            return self.finish("cancelled", "钓鱼任务被取消：%s" % status.get("stopReason"), **evidence)
        return self.finish("failed", "钓鱼任务结束但未完成：%s" % status.get("stopReason"), **evidence)

    async def _start(self, observation: Observation) -> Progress:
        self.steps += 1
        try:
            status = await self.port.start(self.limits)
        except AgentError as exc:
            return self.finish("failed", "钓鱼指令未被执行：%s" % exc, error=exc.code)
        self.run_id = str(status.get("runId") or "")
        if not self.run_id:
            return self.finish("unknown", "游戏没有返回 runId，结果未知；先查询状态，不要重复 start")
        self.record("start", {"runId": self.run_id, "ack": status.get("ack")})
        if not status.get("active"):
            return self.finish("unknown", "指令已被接受但任务未在运行，需要查询状态确认", runId=self.run_id)
        return Progress("running", "钓鱼任务已开始", dict(self.evidence))

    async def cancel(self, reason: str) -> None:
        await super().cancel(reason)
        if self.run_id is None:
            return
        try:
            await self.port.stop()
        except AgentError as exc:  # 取消失败也要把原因带回证据
            self.record("cancel_error", {"code": exc.code, "detail": str(exc)[:200]})


def fish_ability(
    port: FishingPort,
    *,
    validate_limits: Callable[[Mapping[str, Any]], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> AbilitySpec:
    """构造 ``fish`` 能力。"""

    validator = validate_limits or _default_validator
    descriptor = AbilityDescriptor(
        id="fish",
        version="1.0.0",
        summary="在人物当前站位委托独立钓鱼 Mod 钓一段（抛竿、咬钩、收鱼由 Mod 执行）",
        params={"limits": "预算对象：maxCasts / targetFish / maxSeconds / minEnergy / stopAt / facing"},
        observation_needs=("location", "player.energy", "player.position", "inventory"),
        scope="只钓一竿到给定预算为止；不寻找岸边、不旅行、不决定目的地",
        completion="Mod 报告 state=completed；否则按 failed/cancelled/unknown 记录",
        limits=("maxCasts 1～100", "maxSeconds 10～1800", "stopAt 使用 HHMM"),
    )

    def validate(params: Mapping[str, Any]) -> Mapping[str, Any]:
        reject_unknown_params(params, ("limits",))
        limits = params.get("limits")
        if not isinstance(limits, Mapping):
            raise Rejected("limits 必须是对象")
        limits = dict(limits)
        try:
            validator(limits)
        except AgentError as exc:
            raise Rejected("钓鱼预算参数不合法：%s" % exc) from exc
        return {"limits": limits}

    def premises(params: Mapping[str, Any], observation: Observation) -> list[PremiseCheck]:
        energy = observation.field("player.energy")
        minimum = _min_energy(params)
        if energy.known:
            met = energy.value is not None and int(energy.value) >= minimum
        else:
            met = None
        return [
            PremiseCheck("game", met, "体力 %s，委托要求至少 %d" % (energy.value, minimum)),
            PremiseCheck(
                "budget",
                None,
                "钓鱼预算由 Mod 与运行预算共同限制（maxSeconds=%s）" % (params.get("limits") or {}).get("maxSeconds"),
            ),
        ]

    def start(params: Mapping[str, Any], observation: Observation, action_id: str) -> FishingDelegation:
        delegation = FishingDelegation(port, params, observation, clock=clock)
        delegation.action_id = action_id or delegation.action_id
        return delegation

    def predicates(params: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        return [{"path": "player.energy", "op": ">=", "value": _min_energy(params)}]

    return AbilitySpec(
        descriptor=descriptor,
        validate=validate,
        check_premises=premises,
        start=start,
        predicates=predicates,
    )

