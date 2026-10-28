"""能力层公共定义：前提分级、参数校验、委托执行。

前提必须区分四类，语义不同：

* ``game`` — 游戏真实规则/现场事实（例如人物当前不能移动）；
* ``user`` — 用户设置的硬限制（禁止某类行为）；
* ``budget`` — 本次委托或本次运行还剩多少预算；
* ``advice`` — 策略建议，可以被模型否决。

``met`` 为 ``None`` 表示未知：未知的游戏事实保留为未知，不当作禁止。
能力不替模型决定“种什么、卖什么、去哪”，只执行被委托的动作并给出真实结果。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Sequence

from ..contracts.ability import AbilityDescriptor
from ..contracts.errors import PremiseFailed, Rejected
from ..contracts.observation import Observation
from ..contracts.results import Delegation, Progress

PREMISE_KINDS = ("game", "user", "budget", "advice")


@dataclass(frozen=True)
class PremiseCheck:
    """一条前提检查的结果。"""

    kind: str
    met: bool | None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.kind not in PREMISE_KINDS:
            raise ValueError("未知前提类型：%s" % self.kind)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "met": self.met, "detail": self.detail}


ParamsValidator = Callable[[Mapping[str, Any]], Mapping[str, Any]]
PremiseChecker = Callable[[Mapping[str, Any], Observation], Sequence[PremiseCheck]]
Starter = Callable[[Mapping[str, Any], Observation, str], Delegation]


@dataclass(frozen=True)
class AbilitySpec:
    """一个能力：描述、参数校验、前提检查与启动函数。"""

    descriptor: AbilityDescriptor
    validate: ParamsValidator
    check_premises: PremiseChecker
    start: Starter
    predicates: Callable[[Mapping[str, Any]], Sequence[Mapping[str, Any]]] = lambda params: ()

    @property
    def id(self) -> str:
        return self.descriptor.id

    @property
    def version(self) -> str:
        return self.descriptor.version


def require_int(params: Mapping[str, Any], name: str, *, low: int | None = None, high: int | None = None) -> int:
    value = params.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise Rejected("参数 %s 必须是整数" % name)
    if low is not None and value < low:
        raise Rejected("参数 %s 不能小于 %d" % (name, low))
    if high is not None and value > high:
        raise Rejected("参数 %s 不能大于 %d" % (name, high))
    return value


def require_str(params: Mapping[str, Any], name: str, *, allow_empty: bool = False) -> str:
    value = params.get(name)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise Rejected("参数 %s 必须是非空字符串" % name)
    return value


def reject_unknown_params(params: Mapping[str, Any], allowed: Sequence[str]) -> None:
    extra = sorted(set(params) - set(allowed))
    if extra:
        raise Rejected("不支持的参数：%s（允许：%s）" % (", ".join(extra), ", ".join(allowed)))


class DelegationBase:
    """带序号、计时与取消状态的委托基类。"""

    def __init__(self, ability: str, params: Mapping[str, Any], *, clock: Callable[[], float] = time.monotonic) -> None:
        self.action_id = "a" + uuid.uuid4().hex[:16]
        self.ability = ability
        self.params = dict(params)
        self.clock = clock
        self.started_at = clock()
        self.cancelled = False
        self.cancel_reason = ""
        self.steps = 0
        self.evidence: dict[str, Any] = {}

    def elapsed(self) -> float:
        return self.clock() - self.started_at

    def record(self, key: str, value: Any) -> None:
        self.evidence[key] = value

    async def cancel(self, reason: str) -> None:
        self.cancelled = True
        self.cancel_reason = reason

    def cancelled_progress(self) -> Progress:
        return Progress("cancelled", self.cancel_reason or "已取消", dict(self.evidence))

    def finish(self, state: str, detail: str, **evidence: Any) -> Progress:
        payload = dict(self.evidence)
        payload.update(evidence)
        payload.setdefault("steps", self.steps)
        payload.setdefault("elapsed_seconds", round(self.elapsed(), 3))
        return Progress(state, detail, payload)


def blocking_premises(checks: Sequence[PremiseCheck]) -> list[PremiseCheck]:
    """会阻止下发的检查：游戏事实/用户限制/预算明确不满足。建议不阻止。"""
    return [check for check in checks if check.met is False and check.kind in ("game", "user", "budget")]


def raise_for_premises(checks: Sequence[PremiseCheck]) -> None:
    blocking = blocking_premises(checks)
    if blocking:
        raise PremiseFailed("；".join("%s：%s" % (check.kind, check.detail) for check in blocking))


@dataclass
class SimpleDelegate(DelegationBase):
    """子类只需实现 :meth:`step` 的委托基类。"""

    async def step(self, observation: Observation) -> Progress:  # pragma: no cover - 由子类实现
        raise NotImplementedError


AwaitableSleep = Callable[[float], Awaitable[None]]
