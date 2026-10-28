"""运行时：预算、动作所有权、停止信号、事件记录与实时循环。

主循环只处理统一生命周期；具体动作由能力实现，决策由模型给出，
经验判定由经验模块负责。
"""

from .budget import RunBudget
from .console import ConsoleReporter, format_event
from .events import EventSink, Recorder, combine_sinks
from .loop import AgentLoop, DecisionRequester, DelegationFactory, LoopSummary, default_action_key, default_context_key
from .ownership import ActionOwnership, Lease
from .stop import StopFile, StopSignal, StopSource

__all__ = [
    "ActionOwnership",
    "AgentLoop",
    "ConsoleReporter",
    "DecisionRequester",
    "DelegationFactory",
    "EventSink",
    "Lease",
    "LoopSummary",
    "Recorder",
    "RunBudget",
    "StopFile",
    "StopSignal",
    "StopSource",
    "combine_sinks",
    "default_action_key",
    "default_context_key",
    "format_event",
]
