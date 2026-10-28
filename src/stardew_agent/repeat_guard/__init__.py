"""白名单 9：动作重复限频器。

只依赖时钟与动作键，不依赖旧 ``WorkPlanner`` 的计数、续跑、跨日和重启规则。
"""

from .guard import GuardDecision, RepeatGuard, RepeatPolicy

__all__ = ["GuardDecision", "RepeatGuard", "RepeatPolicy"]
