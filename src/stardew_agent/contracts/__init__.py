"""跨模块共用的数据记录、错误与版本。

这里只放真正的共用定义，不放调度、策略或业务目标。
"""

from .ability import AbilityDescriptor
from .decision import AbilityCall, Decision
from .errors import (
    AgentError,
    BudgetExhausted,
    ConfigError,
    ExperienceError,
    ModelError,
    ModelUnavailable,
    OwnershipBusy,
    PremiseFailed,
    ProtocolError,
    Rejected,
    ResultUnknown,
    Stopped,
    TransportError,
)
from .experience import (
    CONFIDENCE_LEVELS,
    EVIDENCE_KINDS,
    SCOPES,
    STATUSES,
    Event,
    ExperienceRecord,
    Premise,
    SkillRun,
    SkillVersion,
)
from .observation import FIELD_ROOTS, UNSUPPORTED_FIELDS, Field, Observation
from .results import ACTION_STATES, PROGRESS_STATES, ActionResult, Delegation, Progress
from .version import BRIDGE_PROTOCOL_VERSION, FISHING_STATUS_VERSION, SCHEMA_VERSION, SKILL_SCHEMA_VERSION

__all__ = [
    "ACTION_STATES",
    "AbilityCall",
    "AbilityDescriptor",
    "ActionResult",
    "AgentError",
    "BRIDGE_PROTOCOL_VERSION",
    "BudgetExhausted",
    "CONFIDENCE_LEVELS",
    "ConfigError",
    "Decision",
    "Delegation",
    "EVIDENCE_KINDS",
    "Event",
    "ExperienceError",
    "ExperienceRecord",
    "FIELD_ROOTS",
    "FISHING_STATUS_VERSION",
    "Field",
    "ModelError",
    "ModelUnavailable",
    "Observation",
    "OwnershipBusy",
    "PROGRESS_STATES",
    "Premise",
    "PremiseFailed",
    "Progress",
    "ProtocolError",
    "Rejected",
    "ResultUnknown",
    "SCHEMA_VERSION",
    "SCOPES",
    "SKILL_SCHEMA_VERSION",
    "STATUSES",
    "SkillRun",
    "SkillVersion",
    "Stopped",
    "TransportError",
    "UNSUPPORTED_FIELDS",
]
