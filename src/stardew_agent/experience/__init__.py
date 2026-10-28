"""经验与组合技能：证据、检索、验证状态和技能版本。

* :mod:`store` — SQLite 经验库（事件、经验、技能版本、地图索引、存档事实）
* :mod:`verdict` — 晋级/降级判据
* :mod:`skills` — 组合技能的受限定义格式与校验
"""

from .skills import (
    MAX_SECONDS,
    MAX_STEP_LIMIT,
    MAX_STEPS,
    SkillDefinitionError,
    build_skill,
    validate_skill,
)
from .store import ExperienceStore, grams
from .verdict import DEFAULT_REQUIRED_SUCCESSES, PromotionDecision, evaluate_promotion, handle_failure

__all__ = [
    "DEFAULT_REQUIRED_SUCCESSES",
    "ExperienceStore",
    "MAX_SECONDS",
    "MAX_STEPS",
    "MAX_STEP_LIMIT",
    "PromotionDecision",
    "SkillDefinitionError",
    "build_skill",
    "evaluate_promotion",
    "grams",
    "handle_failure",
    "validate_skill",
]
