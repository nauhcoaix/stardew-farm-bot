"""实时主循环：一个在途决策请求、一个动作所有者、有界委托、可取消。

职责边界：

* 本模块只处理统一生命周期（观察、请求、新鲜度、所有权、步进、取消、记录），
  不包含“种什么、去哪、何时睡”这类业务决定；
* 具体动作由能力实现，决策由模型给出，经验判定由经验模块负责；
* 推理期间不暂停游戏：没有可执行委托时真实等待并记录原因；
* 收到停止或预算用尽后不再下发新动作，取消在途委托并确认控制权释放。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Sequence

from ..contracts.decision import AbilityCall, Decision
from ..contracts.errors import AgentError, BudgetExhausted, OwnershipBusy, PremiseFailed, ResultUnknown, Stopped
from ..contracts.observation import Observation
from ..contracts.results import ActionResult, Delegation, Progress
from ..decision.binding import rebind_call
from ..decision.freshness import check as check_freshness
from ..decision.predicates import PredicateError, evaluate_all
from ..repeat_guard.guard import GuardDecision, RepeatGuard
from .budget import RunBudget
from .events import Recorder
from .ownership import ActionOwnership, Lease
from .stop import StopSource

DecisionRequester = Callable[
    [Observation, ActionResult | None, Sequence[Mapping[str, Any]]], Awaitable[Decision | None]
]
DelegationFactory = Callable[[AbilityCall, Observation], Delegation]
PremiseSupplier = Callable[[AbilityCall, Observation], Sequence[Mapping[str, Any]]]
KeyFunction = Callable[[Any], str]


def default_action_key(call: AbilityCall) -> str:
    """动作键：能力 + 规范化参数；不同地块/目标因此是不同动作。"""
    return "%s:%s" % (call.ability, json.dumps(dict(call.params), sort_keys=True, ensure_ascii=False))


def default_context_key(observation: Observation) -> str:
    """限频清零的上下文：存档 + 游戏日。"""
    game_time = dict(observation.game_time or {})
    return "%s|%s-%s-%s" % (
        observation.save_key,
        game_time.get("year"),
        game_time.get("season"),
        game_time.get("day"),
    )


@dataclass
class LoopSummary:
    """一次运行的汇总（用于状态输出与验收报告）。"""

    actions: int = 0
    completed: int = 0
    failed: int = 0
    unknown: int = 0
    cancelled: int = 0
    blocked: int = 0
    decisions: int = 0
    discarded: int = 0
    elapsed_seconds: float = 0.0
    stop_reason: str = ""
    last_result: Mapping[str, Any] | None = None
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "actions": self.actions,
            "completed": self.completed,
            "failed": self.failed,
            "unknown": self.unknown,
            "cancelled": self.cancelled,
            "blocked": self.blocked,
            "decisions": self.decisions,
            "discarded": self.discarded,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "stop_reason": self.stop_reason,
            "last_result": dict(self.last_result) if self.last_result else None,
            "metrics": self.metrics,
        }



class AgentLoop:
    """实时循环。依赖全部由入口提供，不含隐式全局资源。"""

    def __init__(
        self,
        *,
        observe: Callable[[], Awaitable[Observation]],
        request_decision: DecisionRequester,
        build_delegation: DelegationFactory,
        budget: RunBudget,
        ownership: ActionOwnership,
        recorder: Recorder,
        guard: RepeatGuard | None = None,
        premises: PremiseSupplier | None = None,
        action_key: Callable[[AbilityCall], str] = default_action_key,
        context_key: Callable[[Observation], str] = default_context_key,
        stop: StopSource | None = None,
        poll_seconds: float = 0.5,
        step_interval_seconds: float = 0.2,
        recent_history_limit: int = 8,
        use_model_completion: bool = False,
        idle_log_seconds: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.observe = observe
        self.request_decision = request_decision
        self.build_delegation = build_delegation
        self.budget = budget
        self.ownership = ownership
        self.recorder = recorder
        self.guard = guard
        self.premises = premises
        self.action_key = action_key
        self.context_key = context_key
        self.stop_source = stop
        self.poll_seconds = float(poll_seconds)
        self.step_interval_seconds = float(step_interval_seconds)
        self.use_model_completion = bool(use_model_completion)
        self.idle_log_seconds = float(idle_log_seconds)
        self.clock = clock

        self.summary = LoopSummary()
        self._active: Delegation | None = None
        self._lease: Lease | None = None
        self._active_before: Observation | None = None
        self._active_started = 0.0
        self._pending_completion: Mapping[str, Any] | None = None
        self._active_goal = ""
        self._decision_task: asyncio.Task | None = None
        self._decision_based_on: Observation | None = None
        self._last_result: ActionResult | None = None
        self._context: str | None = None
        self._save_key = ""
        self._session = ""
        self._idle_accum = 0.0
        self._halted: str | None = None
        self._observation_at = 0.0
        self._recent: deque[dict] = deque(maxlen=max(1, int(recent_history_limit)))
        self._no_action_streak = 0
        self._context_save: str | None = None

    # ---- 对外入口 -------------------------------------------------
    async def run(self, *, max_actions: int | None = None) -> LoopSummary:
        """运行到停止、预算用尽或达到动作数上限。"""
        started = self.clock()
        self.budget.start()
        self._record("run_started", payload={"budget": self.budget.snapshot()})
        try:
            while True:
                halt = self._halt_reason()
                if halt:
                    self._halted = halt
                    break
                if max_actions is not None and self.summary.actions >= max_actions and self._active is None:
                    self._halted = "max_actions"
                    break
                observation = await self._observe()
                if observation is None:
                    await self._idle()
                    continue
                self._note_context(observation)
                if self._active is None and self._decision_task is None:
                    # 委托结算后才发起下一次决策：模型看到的 last_result 与最近历史是最新的。
                    # 推理期间游戏继续运行（不暂停），只是不提前请求下一步。
                    self._decision_based_on = observation
                    self._decision_task = asyncio.create_task(self._ask(observation))
                if self._active is not None:
                    await self._step_active(observation)
                    if self._active is not None:
                        # 分段执行之间留出间隔：让游戏推进、让其他任务运行，避免空转。
                        remaining = self.budget.remaining_seconds()
                        await asyncio.sleep(min(self.step_interval_seconds, remaining) if remaining > 0 else 0.001)
                    continue
                if self._decision_task is not None and self._decision_task.done():
                    halt = await self._consume_decision(observation)
                    if halt:
                        self._halted = halt
                        break
                    continue
                await self._idle()
        finally:
            await self._shutdown()
        self.summary.elapsed_seconds = self.clock() - started
        self.summary.stop_reason = self._halted or "unknown"
        self.summary.metrics = self.recorder.metrics()
        self.summary.last_result = self._last_result.to_dict() if self._last_result else None
        self._record(
            "run_finished",
            payload={"summary": self.summary.to_dict(), "budget": self.budget.snapshot()},
        )
        return self.summary

    async def cancel(self, reason: str = "cancelled") -> None:
        """外部取消（例如收到停止文件）：立即停止推进并释放控制。"""
        self._halted = reason

    # ---- 循环内部 -------------------------------------------------
    def _halt_reason(self) -> str | None:
        if self.stop_source is not None:
            reason = self.stop_source.reason()
            if reason:
                return "stop:" + str(reason)
        expired = self.budget.expire_reason()
        if expired:
            return "budget:" + expired
        return None

    async def _observe(self) -> Observation | None:
        try:
            observation = await self.observe()
        except Exception as exc:  # noqa: BLE001 - 观察失败不等于运行失败，但要留证据
            self._record(
                "observe_failed",
                status="failed",
                payload={"error": type(exc).__name__, "detail": str(exc)[:300]},
            )
            return None
        self._save_key = observation.save_key
        self._session = observation.session
        self._observation_at = self.clock()
        return observation

    def _note_context(self, observation: Observation) -> None:
        key = self.context_key(observation)
        if self._context == key:
            return
        self._context = key
        previous_save = self._context_save
        self._context_save = observation.save_key
        if previous_save is not None and previous_save != observation.save_key:
            # 换存档：A 的最近动作与上次结果不能出现在 B 的决策上下文里。
            self._recent.clear()
            self._last_result = None
            self._record(
                "context_switched",
                payload={
                    "from_save": previous_save,
                    "to_save": observation.save_key,
                    "cleared": ["recent_actions", "last_result"],
                },
            )
        if self.guard is not None:
            self.guard.reset()
            self._record("guard_reset", payload={"context": key})

    async def _ask(self, observation: Observation) -> Decision | None:
        started = self.clock()
        delay = self.budget.next_request_delay()
        if delay > 0:
            self._record(
                "model_rate_limited",
                payload={"wait_seconds": round(delay, 3), "min_interval_seconds": self.budget.min_interval_seconds},
            )
            await self._sleep_unless_halted(delay)
            if self._halt_reason() is not None:
                return None
        try:
            self.budget.consume_model_request()
        except BudgetExhausted:
            self._record("budget_exhausted", payload={"budget": self.budget.snapshot()})
            return None
        try:
            decision = await self.request_decision(observation, self._last_result, tuple(reversed(self._recent)))
        except Exception as exc:  # noqa: BLE001 - 请求/解析失败属于运行语义
            ability = str(getattr(exc, "ability", "") or "")
            if ability:
                # 解析层拒绝（隐藏能力、参数非法）也要让模型下一轮看得到，避免原样重发。
                self._remember_blocked(ability, str(exc))
            self._record(
                "decision_failed",
                status="failed",
                payload={
                    "error": type(exc).__name__,
                    "code": getattr(exc, "code", ""),
                    "ability": ability or None,
                    "detail": str(exc)[:300],
                },
            )
            return None
        finally:
            self.recorder.duration("model_latency", self.clock() - started)
        if decision is not None:
            self.summary.decisions += 1
            self._record(
                "decision_received",
                ability=decision.call.ability if decision.call else None,
                payload={
                    "goal": decision.goal,
                    "goal_revision": decision.goal_revision,
                    "snapshot_id": decision.based_on_snapshot_id,
                    "params": dict(decision.call.params) if decision.call else None,
                    "reason": decision.reason,
                    "completion": dict(decision.completion) if decision.completion else None,
                    "predicate_errors": list(decision.predicate_errors),
                    "note": decision.note,
                },
            )
        return decision

    async def _sleep_unless_halted(self, seconds: float) -> None:
        """等待模型请求间隔；期间响应停止与预算，避免长时间不可中断。"""
        deadline = self.clock() + max(0.0, seconds)
        while True:
            if self._halt_reason() is not None:
                return
            left = deadline - self.clock()
            if left <= 0:
                return
            await asyncio.sleep(min(0.2, left))

    async def _idle(self, extra_seconds: float = 0.0) -> None:
        """没有可执行委托：真实等待并记录原因，不擅自开新任务。"""
        started = self.clock()
        wanted = self.poll_seconds + max(0.0, float(extra_seconds))
        sleep_for = min(wanted, max(0.0, self.budget.remaining_seconds()) or wanted)
        await asyncio.sleep(sleep_for)
        spent = self.clock() - started
        self.recorder.duration("idle_wait", spent)
        self.summary.metrics = self.recorder.metrics()
        self._idle_accum += spent
        if self._idle_accum >= self.idle_log_seconds:
            self._record(
                "idle_wait",
                payload={"seconds": round(self._idle_accum, 3), "reason": "没有可执行委托，等待模型决策"},
            )
            self._idle_accum = 0.0

    def _record(self, kind: str, **kwargs: Any) -> None:
        kwargs.setdefault("save_key", self._save_key)
        kwargs.setdefault("session", self._session)
        self.recorder.emit(kind, **kwargs)

    def _remember(self, result: ActionResult, goal: str = "") -> None:
        """记录给模型看的精简历史（含 blocked/failed/unknown）。"""
        self._recent.append(result.to_brief(goal=goal, recorded_at=time.time()))

    def _remember_blocked(self, ability: str, detail: str) -> None:
        """解析层拒绝的决策没有 ActionResult，也要给模型留一条 blocked 记录。"""
        self._recent.append(
            {
                "ability": ability or "decision",
                "params": {},
                "state": "blocked",
                "detail": str(detail)[:160],
                "progress": False,
                "goal": "",
                "recorded_at": time.time(),
            }
        )

    async def _consume_decision(self, observation: Observation) -> str | None:
        task, self._decision_task = self._decision_task, None
        if task is None:
            return None
        try:
            decision = task.result()
        except asyncio.CancelledError:
            return None
        except Exception:
            decision = None
        if decision is None:
            self._record("decision_empty")
            await self._idle()
            return None
        return await self._dispatch(decision, observation)

    async def _dispatch(self, decision: Decision, observation: Observation) -> str | None:
        """校验新鲜度与限频后启动委托；返回非空表示应当结束运行。"""
        call = decision.call
        premises = self.premises(call, observation) if (call is not None and self.premises) else ()
        freshness = check_freshness(decision, self._decision_based_on, observation, premises=premises)  # type: ignore[arg-type]
        if freshness.stale:
            self.summary.discarded += 1
            self._record(
                "decision_discarded",
                ability=call.ability if call else None,
                status="stale",
                payload={
                    "goal": decision.goal,
                    "reasons": list(freshness.reasons),
                    "unknowns": list(freshness.unknowns),
                },
            )
            return None
        if call is None:
            self._no_action_streak += 1
            backoff = min(2 ** (self._no_action_streak - 1) * self.poll_seconds, 5.0)
            self._record(
                "no_action",
                payload={
                    "goal": decision.goal,
                    "note": decision.note,
                    "reason": decision.reason,
                    "streak": self._no_action_streak,
                    "backoff_seconds": round(backoff, 2),
                },
            )
            await self._idle(backoff)
            return None

        # ref 只在所属快照内有效；模型思考期间现场已经换了几次快照。
        # 用决策依据的观察把临时 ref 重新绑定到当前现场（移动退化为坐标）。
        rebound, notes = rebind_call(call, self._decision_based_on, observation)
        if notes:
            call = rebound
            self._record(
                "decision_rebound",
                ability=call.ability,
                payload={"goal": decision.goal, "changes": notes},
            )

        key = self.action_key(call)
        if self.guard is not None:
            verdict: GuardDecision = self.guard.check(key, ability=call.ability)
            if not verdict.allowed:
                self.summary.blocked += 1
                self._last_result = ActionResult(
                    action_id="blocked:" + key[:24],
                    ability=call.ability,
                    params=call.params,
                    state="blocked",
                    detail=verdict.reason,
                    evidence={"repeat": verdict.to_dict()},
                    before_sampled_at=observation.sampled_at,
                )
                self._record("repeat_blocked", ability=call.ability, status="blocked", payload=verdict.to_dict())
                self._remember(self._last_result, decision.goal)
                await self._idle()
                return None

        try:
            delegation = self.build_delegation(call, observation)
        except AgentError as exc:
            self.summary.blocked += 1
            self._last_result = ActionResult(
                action_id="rejected:" + key[:24],
                ability=call.ability,
                params=call.params,
                state="blocked",
                detail=str(exc),
                error=exc.code,
                before_sampled_at=observation.sampled_at,
            )
            self._record(
                "delegation_rejected",
                ability=call.ability,
                status="blocked",
                payload={"error": exc.code, "detail": str(exc)[:300]},
            )
            self._remember(self._last_result, decision.goal)
            return None

        halt = self._halt_reason()
        if halt:
            self._record("action_not_dispatched", ability=call.ability, payload={"reason": halt})
            return halt

        try:
            lease = await self.ownership.acquire("agent", delegation.action_id)
        except OwnershipBusy as exc:
            self._record("ownership_busy", ability=call.ability, status="blocked", payload={"detail": str(exc)})
            return None

        if self.guard is not None:
            self.guard.record(key, ability=call.ability)
        self._active = delegation
        self._lease = lease
        self._active_before = observation
        self._active_started = self.clock()
        self._active_goal = decision.goal
        # 默认不采用模型自报的完成判据：完成由能力的前后观察验证决定。
        self._pending_completion = decision.completion if self.use_model_completion else None
        completion_ignored = bool(decision.completion) and not self.use_model_completion
        self._no_action_streak = 0
        self.summary.actions += 1
        self._record(
            "action_started",
            ability=call.ability,
            payload={
                "action_id": delegation.action_id,
                "params": dict(call.params),
                "goal": decision.goal,
                "completion": dict(self._pending_completion) if self._pending_completion else None,
                "completion_ignored": completion_ignored,
                "stop_conditions": list(decision.stop_conditions),
                "observation_to_dispatch_seconds": round(self.clock() - self._observation_at, 3),
                "lease": lease.to_dict(),
            },
        )
        return None

    async def _step_active(self, observation: Observation) -> None:
        """推进当前委托一步；每步之后重新观察，不累积陈旧现场。"""
        delegation = self._active
        if delegation is None:
            return
        halt = self._halt_reason()
        if halt:
            self._halted = halt
            await self._cancel_active("运行停止：" + halt)
            return
        try:
            progress = await delegation.step(observation)
        except ResultUnknown as exc:
            progress = Progress("unknown", str(exc))
        except Stopped as exc:
            progress = Progress("cancelled", str(exc))
        except PremiseFailed as exc:
            progress = Progress("failed", str(exc))
        except AgentError as exc:
            progress = Progress("unknown", "%s: %s" % (exc.code, exc))

        after = await self._observe() or observation
        if not progress.done:
            self._active_before = after
            self._record(
                "action_step",
                ability=delegation.ability,
                payload={"detail": progress.detail, "evidence": dict(progress.evidence)},
            )
            return
        await self._finish(progress, after)

    async def _finish(self, progress: Progress, after: Observation) -> None:
        """结算委托：模型判据只能否决成功，不能把失败/未知升级成完成。"""
        delegation = self._active
        if delegation is None:
            return
        state, detail = progress.state, progress.detail
        evidence = dict(progress.evidence)
        if state == "completed" and self._pending_completion:
            try:
                met, reasons = evaluate_all([self._pending_completion], after)
            except PredicateError as exc:
                # 类型不可比较等运行期错误：当作“无法验证”，不能反过来判失败或让整轮运行崩掉。
                met, reasons = None, ["判据无法求值：%s" % exc]
                evidence["completion_predicate_error"] = str(exc)
            evidence["completion_predicate"] = {"met": met, "reasons": list(reasons)}
            if met is False:
                state = "failed"
                detail = "能力自报完成，但完成判据未满足：%s" % "；".join(reasons)

        before = self._active_before
        result = ActionResult(
            action_id=delegation.action_id,
            ability=delegation.ability,
            params=delegation.params,
            state=state,
            detail=detail,
            progress=state == "completed" or bool(evidence.get("partial")),
            spent_seconds=self.clock() - self._active_started,
            before_sampled_at=before.sampled_at if before else None,
            after_sampled_at=after.sampled_at,
            evidence=evidence,
        )
        self._active = None
        self._pending_completion = None
        self._last_result = result
        self._remember(result, self._active_goal)
        self._count_result(state)
        await self._release_lease()
        self._record(
            "action_finished_" + state,
            ability=result.ability,
            status=state,
            payload=dict(result.to_dict(), goal=self._active_goal),
        )

    def _count_result(self, state: str) -> None:
        if state == "completed":
            self.summary.completed += 1
        elif state == "failed":
            self.summary.failed += 1
        elif state == "cancelled":
            self.summary.cancelled += 1
        else:
            self.summary.unknown += 1

    async def _release_lease(self) -> None:
        lease, self._lease = self._lease, None
        if lease is None:
            return
        try:
            await self.ownership.release(lease)
        except OwnershipBusy as exc:
            self._record("ownership_release_failed", status="failed", payload={"detail": str(exc)})

    async def _cancel_active(self, reason: str) -> None:
        delegation = self._active
        if delegation is None:
            await self._release_lease()
            return
        try:
            await delegation.cancel(reason)
        except Exception as exc:  # noqa: BLE001 - 取消失败也要记录并继续释放
            self._record(
                "cancel_failed",
                ability=delegation.ability,
                status="failed",
                payload={"error": type(exc).__name__, "detail": str(exc)[:300]},
            )
        result = ActionResult(
            action_id=delegation.action_id,
            ability=delegation.ability,
            params=delegation.params,
            state="cancelled",
            detail=reason,
            spent_seconds=self.clock() - self._active_started,
            before_sampled_at=self._active_before.sampled_at if self._active_before else None,
            evidence={"cancel_reason": reason},
        )
        self._active = None
        self._pending_completion = None
        self._last_result = result
        self._remember(result, self._active_goal)
        self.summary.cancelled += 1
        await self._release_lease()
        self._record(
            "action_finished_cancelled",
            ability=result.ability,
            status="cancelled",
            payload=dict(result.to_dict(), goal=self._active_goal),
        )

    async def _shutdown(self) -> None:
        if self._decision_task is not None:
            task, self._decision_task = self._decision_task, None
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._record("decision_cancelled")
        await self._cancel_active("运行结束")
        stolen = await self.ownership.force_release()
        if stolen is not None:
            self._record("ownership_forced_release", payload=stolen.to_dict())
        self._record(
            "run_stopped",
            payload={
                "reason": self._halted or "finished",
                "budget": self.budget.snapshot(),
                "guard": self.guard.snapshot() if self.guard is not None else None,
            },
        )



