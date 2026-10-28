"""白名单 3：游戏桥客户端。

只负责通信：连接、握手、请求、响应、协议错误与动作 ID。
不引入 Farmer、SkillRuntime、旧 WorkPlanner，也不含任何经营目标。
"""

from .client import DEFAULT_ENDPOINT, WRITE_COMMANDS, Bridge, BridgeError, Transport, connect_websocket

__all__ = ["Bridge", "BridgeError", "DEFAULT_ENDPOINT", "Transport", "WRITE_COMMANDS", "connect_websocket"]
