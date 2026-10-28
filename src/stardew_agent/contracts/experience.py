"""经验、证据、组合技能与事件契约。

经验不是“模型说过的话”。每条经验都带主张、适用条件、支持证据、反例、
验证状态和可迁移范围；组合技能是受限的可执行数据，参考底层能力版本。

来源等级（confidence）：

* ``observed``：游戏桥直接观察到的结构化状态；
* ``execution``：本系统下发动作后的真实结果；
* ``reported``：用户陈述或迁移来源（未验证）；
* ``hypothesis``：模型假设/反思。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .version import SCHEMA_VERSION

#: 经验/技能的验证状态
STATUSES = ("candidate", "verified", "retired")

#: 经验适用范围：存档事实，还是可迁移的机制知识
SCOPES = ("save", "mechanism")

#: 证据来源类型
EVIDENCE_KINDS = ("observation", "execution", "reported", "hypothesis")

CONFIDENCE_LEVELS = ("observed", "execution", "reported", "hypothesis")


@dataclass(frozen=True)
class Premise:
    """受限条件：``path op value``，用于技能前提与完成判据。"""

    path: str
    op: str
    value: Any = None

    def to_dict(self) -> dict:
        return {"path": self.path, "op": self.op, "value": self.value}

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "Premise":
        return cls(path=str(row.get("path") or ""), op=str(row.get("op") or ""), value=row.get("value"))


@dataclass(frozen=True)
class Event:
    """不可变证据事件：观察到什么、执行了什么、结果如何。"""

    id: str
    kind: str
    recorded_at: float
    save_key: str
    session: str = ""
    ability: str | None = None
    status: str = ""
    source: str = "executor"
    confidence: str = "observed"
    payload: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "kind": self.kind,
            "recorded_at": self.recorded_at,
            "save_key": self.save_key,
            "session": self.session,
            "ability": self.ability,
            "status": self.status,
            "source": self.source,
            "confidence": self.confidence,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class ExperienceRecord:
    """一条经验（主张 + 条件 + 证据 + 状态）。"""

    id: str
    claim: str
    kind: str = "mechanism"
    premises: tuple[Premise, ...] = ()
    source: str = "executor"
    confidence: str = "execution"
    evidence_ids: tuple[str, ...] = ()
    counterexample_ids: tuple[str, ...] = ()
    status: str = "candidate"
    scope: str = "mechanism"
    scope_save: str | None = None
    version: int = 1
    created_at: float = 0.0
    updated_at: float = 0.0
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError("未知经验状态：%s" % self.status)
        if self.scope not in SCOPES:
            raise ValueError("未知经验范围：%s" % self.scope)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "claim": self.claim,
            "kind": self.kind,
            "premises": [p.to_dict() for p in self.premises],
            "source": self.source,
            "confidence": self.confidence,
            "evidence_ids": list(self.evidence_ids),
            "counterexample_ids": list(self.counterexample_ids),
            "status": self.status,
            "scope": self.scope,
            "scope_save": self.scope_save,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class SkillVersion:
    """组合技能的一个不可变版本。"""

    id: str
    version: int
    definition: Mapping[str, Any]
    status: str = "candidate"
    evidence_ids: tuple[str, ...] = ()
    promoted_at: float | None = None
    retired_reason: str | None = None
    created_at: float = 0.0
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError("未知技能状态：%s" % self.status)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "version": self.version,
            "definition": dict(self.definition),
            "status": self.status,
            "evidence_ids": list(self.evidence_ids),
            "promoted_at": self.promoted_at,
            "retired_reason": self.retired_reason,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class SkillRun:
    """组合技能的一次真实运行结果，作为晋级/降级证据。"""

    skill_id: str
    skill_version: int
    event_id: str
    outcome: str
    premises_key: str = ""
    cancelled_cleanly: bool = False
    recorded_at: float = 0.0

    @property
    def success(self) -> bool:
        return self.outcome == "completed"
