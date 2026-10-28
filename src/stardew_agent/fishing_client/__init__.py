"""白名单 4：独立钓鱼 Mod 的文件邮箱客户端。

只提供状态读取、心跳新鲜度检查、原子命令提交、确认匹配、runId 等待与预算校验。
不包含检查旧 farm_service 进程的 CLI 规则，也不含与旧执行器的接线。
"""

from .client import (
    DEFAULT_GAME,
    FACING,
    LIMIT_RANGES,
    MOD_ID,
    FishingError,
    doctor,
    is_installed,
    read_status,
    send,
    submit_command,
    validate_limits,
    wait_run,
)

__all__ = [
    "DEFAULT_GAME",
    "FACING",
    "LIMIT_RANGES",
    "MOD_ID",
    "FishingError",
    "doctor",
    "is_installed",
    "read_status",
    "send",
    "submit_command",
    "validate_limits",
    "wait_run",
]
