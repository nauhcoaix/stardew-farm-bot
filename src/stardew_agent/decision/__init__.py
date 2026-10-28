"""决策层：上下文选择、决策请求、回复校验与计划新鲜度。

* :mod:`context` — 组装可序列化的决策上下文（含截断说明）
* :mod:`prompt` — 只说明接口与诚实报告规则的系统提示词
* :mod:`parse` — 把模型 JSON 变成受校验的决策
* :mod:`predicates` — 受限条件表达式
* :mod:`freshness` — 执行前核对关键前提
"""

from .binding import rebind_call
from .context import DEFAULT_LONG_TERM_GOAL, ContextLimits, build_context, inventory_state
from .freshness import Freshness, check as check_freshness
from .parse import DecisionError, ParamsValidator, parse_decision
from .predicates import Evaluation, PredicateError, evaluate, evaluate_all, validate, validate_all
from .prompt import SYSTEM_PROMPT, build_messages, build_system_prompt

__all__ = [
    "ContextLimits",
    "DEFAULT_LONG_TERM_GOAL",
    "DecisionError",
    "Evaluation",
    "Freshness",
    "ParamsValidator",
    "PredicateError",
    "SYSTEM_PROMPT",
    "build_context",
    "build_messages",
    "build_system_prompt",
    "check_freshness",
    "evaluate",
    "evaluate_all",
    "inventory_state",
    "parse_decision",
    "rebind_call",
    "validate",
    "validate_all",
]
