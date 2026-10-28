"""跨模块错误。

每个错误带稳定的 ``code``，同时用类型表达调用方要做的决定：

* ``ResultUnknown``：写操作已下发但没拿到确认。调用方必须先观察核对，
  不能无条件重发（购买、制造、放物品）。
* ``PremiseFailed``：当前现场不满足前提。换目标或重新决策，不要原样重试。
* ``Rejected``：参数或决策本身不合法。
* ``BudgetExhausted`` / ``Stopped``：运行环境要求停止，不再下发新动作。
* ``OwnershipBusy``：另一个执行器正持有动作所有权。
"""

from __future__ import annotations


class AgentError(Exception):
    """本系统所有可预期错误的基类。"""

    code = "AGENT_ERROR"


class ConfigError(AgentError):
    code = "CONFIG"


class TransportError(AgentError):
    """通信失败；对读操作来说本次结果不存在/不可用。"""

    code = "TRANSPORT"


class ResultUnknown(TransportError):
    """写操作已发送但结果未知（超时、连接中断、取消）。"""

    code = "RESULT_UNKNOWN"


class ProtocolError(AgentError):
    code = "PROTOCOL"


class Rejected(AgentError):
    code = "REJECTED"


class PremiseFailed(Rejected):
    code = "PREMISE_FAILED"


class BudgetExhausted(AgentError):
    code = "BUDGET_EXHAUSTED"


class OwnershipBusy(AgentError):
    code = "OWNERSHIP_BUSY"


class Stopped(AgentError):
    code = "STOPPED"


class ModelError(AgentError):
    code = "MODEL"


class ModelUnavailable(ModelError):
    code = "MODEL_UNAVAILABLE"


class ExperienceError(AgentError):
    code = "EXPERIENCE"
