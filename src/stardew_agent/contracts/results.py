"""动作结果与分步进展契约。

四种状态必须分开：请求已发送、游戏已接受、动作已结束、目标已达成。
``unknown`` 表示“已下发但无法确认”，调用方必须先重新观察，不能当作成功，
也不能无条件重发。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from .observation import Observation
from .version import SCHEMA_VERSION

#: 一次委托的最终状态
ACTION_STATES = ("completed", "failed", "cancelled", "unknown", "blocked")

#: 分步执行中的状态；``running`` 之外即结束
PROGRESS_STATES = ("running", "completed", "failed", "cancelled", "unknown")


@dataclass(frozen=True)
class Progress:
    """一次有界执行的单步结果。"""

    state: str = "running"
    detail: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.state not in PROGRESS_STATES:
            raise ValueError("未知进展状态：%s" % self.state)

    @property
    def done(self) -> bool:
        return self.state != "running"


@dataclass(frozen=True)
class ActionResult:
    """一次委托的完整记录，用于重放证据与经验判定。"""

    action_id: str
    ability: str
    params: Mapping[str, Any] = field(default_factory=dict)
    state: str = "unknown"
    detail: str = ""
    progress: bool = False
    spent_seconds: float = 0.0
    before_sampled_at: float | None = None
    after_sampled_at: float | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.state not in ACTION_STATES:
            raise ValueError("未知动作状态：%s" % self.state)

    @property
    def settled(self) -> bool:
        """结果已确定？``unknown`` 不是确定结果。"""
        return self.state in ("completed", "failed", "cancelled")

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "action_id": self.action_id,
            "ability": self.ability,
            "params": dict(self.params),
            "state": self.state,
            "detail": self.detail,
            "progress": self.progress,
            "spent_seconds": round(self.spent_seconds, 3),
            "before_sampled_at": self.before_sampled_at,
            "after_sampled_at": self.after_sampled_at,
            "evidence": dict(self.evidence),
            "error": self.error,
        }


class Delegation(Protocol):
    """运行环境与能力之间的最小执行接口。

    运行环境只通过 ``step`` 推进、通过 ``cancel`` 取消；能力自己负责参数检查、
    分段推进与结果证据。实现方可按需要保存内部状态，但不得跨 delegate 复用。
    """

    action_id: str
    ability: str
    params: Mapping[str, Any]

    async def step(self, observation: Observation) -> Progress: ...

    async def cancel(self, reason: str) -> None: ...
