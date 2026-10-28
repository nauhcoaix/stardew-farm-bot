"""组合技能：把已验证的受限步骤序列当作一个能力执行。

组合技能是**数据**，不是生成的代码：步骤只能引用已注册的底层能力与参数，并带
总步数/时长上限、完成判据与停止条件。每次运行继承运行预算、停止信号与当前目标，
不能因为调用子技能重新获得预算，也不能修改底层能力或自己的验证等级。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping, Sequence

from ..contracts.ability import AbilityDescriptor
from ..contracts.decision import AbilityCall
from ..contracts.errors import Rejected
from ..contracts.observation import Observation
from ..contracts.results import Progress
from ..decision.predicates import evaluate, evaluate_all
from .base import AbilitySpec, DelegationBase, PremiseCheck
from .registry import AbilityRegistry


class CompositeDelegation(DelegationBase):
    """按定义顺序执行步骤；任一步失败即结束整条技能。"""

    def __init__(
        self,
        definition: Mapping[str, Any],
        registry: AbilityRegistry,
        observation: Observation,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(str(definition["id"]), {}, clock=clock)
        self.definition = dict(definition)
        self.registry = registry
        self.steps_def: Sequence[Mapping[str, Any]] = list(self.definition.get("steps") or [])
        self.index = 0
        self.inner = None
        self.inner_steps = 0
        self.total_steps = 0
        self.max_steps = int(self.definition.get("max_steps") or 12)
        self.max_seconds = float(self.definition.get("max_seconds") or 120)
        self.record("skill_id", self.definition.get("id"))
        self.record("declared_abilities", self.definition.get("abilities"))
        self.record("start_location", observation.location)

    async def step(self, observation: Observation) -> Progress:
        if self.cancelled:
            return self.cancelled_progress()
        if self.elapsed() > self.max_seconds:
            return self.finish("failed", "组合技能超过时长上限 %.0f 秒" % self.max_seconds)
        if self.total_steps > self.max_steps:
            return self.finish("failed", "组合技能超过总步数上限 %d" % self.max_steps)
        if self.index >= len(self.steps_def):
            return self._complete(observation)

        step_def = self.steps_def[self.index]
        if self.inner is None:
            call = AbilityCall(ability=str(step_def["ability"]), params=dict(step_def.get("params") or {}))
            try:
                self.inner = self.registry.start(call, observation)
            except Rejected as exc:
                return self.finish("failed", "第 %d 步无法启动：%s" % (self.index + 1, exc), failed_step=self.index)
            self.inner_steps = 0
            self.record("current_step", {"index": self.index, "ability": call.ability, "params": dict(call.params)})

        progress = await self.inner.step(observation)
        if not progress.done:
            self.total_steps += 1
            self.inner_steps += 1
            limit = int(step_def.get("max_steps") or 4)
            if self.inner_steps > limit:
                return self.finish("failed", "第 %d 步超过步数上限 %d" % (self.index + 1, limit))
            return Progress(
                "running",
                "第 %d/%d 步进行中：%s" % (self.index + 1, len(self.steps_def), progress.detail),
                dict(self.evidence),
            )

        if progress.state != "completed":
            return self.finish(
                progress.state if progress.state in ("failed", "unknown", "cancelled") else "failed",
                "第 %d/%d 步未完成（%s）：%s"
                % (self.index + 1, len(self.steps_def), progress.state, progress.detail),
                failed_step=self.index,
            )

        until = step_def.get("until")
        if until:
            verdict = evaluate(until, observation)
            if verdict.met is False:
                return self.finish("failed", "第 %d 步的完成条件未满足：%s" % (self.index + 1, verdict.reason))
            if verdict.met is None:
                self.record("unverified_step", {"index": self.index, "reason": verdict.reason})

        self.index += 1
        self.inner = None
        self.inner_steps = 0
        return Progress(
            "running",
            "已完成第 %d/%d 步，进入下一步" % (self.index, len(self.steps_def)),
            dict(self.evidence),
        )

    def _complete(self, observation: Observation) -> Progress:
        predicate = self.definition.get("completion")
        if predicate:
            verdict = evaluate(predicate, observation)
            if verdict.met is False:
                return self.finish("failed", "步骤已执行，但技能完成判据未满足：%s" % verdict.reason)
            if verdict.met is None:
                return self.finish("completed", "步骤已完成；完成判据无法由当前观察验证", verified=None)
            return self.finish("completed", "技能步骤与完成判据都满足", verified=True)
        return self.finish("completed", "步骤已全部执行（该技能未声明完成判据）", verified=None)

    async def cancel(self, reason: str) -> None:
        await super().cancel(reason)
        if self.inner is not None:
            try:
                await self.inner.cancel(reason)
            except Exception as exc:  # noqa: BLE001
                self.record("inner_cancel_error", type(exc).__name__)


def composite_ability(
    definition: Mapping[str, Any], registry: AbilityRegistry, *, clock: Callable[[], float] = time.monotonic
) -> AbilitySpec:
    """把一份已验证的技能定义包装成可注册的能力。"""

    skill_id = str(definition["id"])
    steps = list(definition.get("steps") or [])
    known = all(registry.known(str(step.get("ability"))) for step in steps)
    needs: tuple[str, ...] = ()
    if known:
        needs = tuple(
            sorted({need for step in steps for need in registry.get(str(step["ability"])).descriptor.observation_needs})
        )
    descriptor = AbilityDescriptor(
        id=skill_id,
        version="1",
        summary=str(definition.get("claim") or "组合技能"),
        params={},
        observation_needs=needs,
        scope="按记录步骤顺序执行已注册能力；不新增底层动作",
        completion=str(definition.get("completion") or "按步骤结束并核对完成判据"),
        limits=(
            "max_steps=%s" % definition.get("max_steps"),
            "max_seconds=%s" % definition.get("max_seconds"),
        ),
    )

    def validate(params: Mapping[str, Any]) -> Mapping[str, Any]:
        if params:
            raise Rejected("组合技能首版不接受额外参数")
        return {}

    def premises(params: Mapping[str, Any], observation: Observation) -> list[PremiseCheck]:
        rows = list(definition.get("premises") or [])
        if not rows:
            return [PremiseCheck("game", None, "该技能没有声明适用前提，调用前需自行确认现场")]
        met, reasons = evaluate_all(rows, observation)
        return [PremiseCheck("game", met, "；".join(reasons) if reasons else "技能适用前提满足")]

    def start(params: Mapping[str, Any], observation: Observation, action_id: str) -> CompositeDelegation:
        delegation = CompositeDelegation(definition, registry, observation, clock=clock)
        delegation.action_id = action_id or delegation.action_id
        return delegation

    def predicates(params: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        return list(definition.get("premises") or [])

    return AbilitySpec(
        descriptor=descriptor,
        validate=validate,
        check_premises=premises,
        start=start,
        predicates=predicates,
    )

