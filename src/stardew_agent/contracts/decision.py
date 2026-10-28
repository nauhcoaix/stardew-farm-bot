"""决策契约。

模型只能提出“选哪个能力、用什么参数、在什么条件下算完成、何时停止”，
不能直接改游戏状态，也不能自己把任务标记为已完成。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .version import SCHEMA_VERSION


@dataclass(frozen=True)
class AbilityCall:
    """一次受限调用：只允许已注册的能力与已校验的参数。"""

    ability: str
    params: Mapping[str, Any] = field(default_factory=dict)
    version: str | None = None

    def to_dict(self) -> dict:
        row = {"ability": self.ability, "params": dict(self.params)}
        if self.version:
            row["version"] = self.version
        return row


@dataclass(frozen=True)
class Decision:
    """模型对“下一步做什么”的回答，绑定到某次观察。"""

    goal: str
    based_on_sampled_at: float = 0.0
    based_on_snapshot_id: str = ""
    reason: str = ""
    call: AbilityCall | None = None
    goal_revision: int = 0
    completion: Mapping[str, Any] | None = None
    stop_conditions: Sequence[Mapping[str, Any]] = ()
    #: 模型写了但被忽略的可选判据（路径越界/运算符不支持等），给下一轮反馈用
    predicate_errors: tuple[str, ...] = ()
    note: str = ""
    model: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        row = {
            "schema_version": self.schema_version,
            "goal": self.goal,
            "based_on_sampled_at": self.based_on_sampled_at,
            "based_on_snapshot_id": self.based_on_snapshot_id,
            "reason": self.reason,
            "call": self.call.to_dict() if self.call else None,
            "goal_revision": self.goal_revision,
            "completion": dict(self.completion) if self.completion else None,
            "stop_conditions": [dict(row) for row in self.stop_conditions],
            "note": self.note,
            "model": self.model,
        }
        if self.predicate_errors:
            row["predicate_errors"] = list(self.predicate_errors)
        return row
