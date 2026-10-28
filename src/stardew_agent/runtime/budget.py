"""运行预算：墙钟上限与模型请求上限，必须显式配置。

按重构需求第 5.4 节：

* 两个上限都必须在启动实机运行前给出，代码不提供隐式无限预算或示例默认值；
* 失败的请求和重试同样计入请求数；
* tokens 可获取则记录，费用未知时标未知；
* 预算跨子技能与重试累计，不因“重开规划阶段”重置。
"""

from __future__ import annotations

import time
from typing import Callable

from ..contracts.errors import BudgetExhausted, ConfigError


class RunBudget:
    """一次运行的预算账本。"""

    def __init__(
        self,
        *,
        wall_clock_seconds: float,
        model_requests: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if wall_clock_seconds is None or model_requests is None:
            raise ConfigError("运行预算必须显式给出墙钟上限与模型请求上限")
        if float(wall_clock_seconds) <= 0:
            raise ConfigError("wall_clock_seconds 必须大于 0")
        if int(model_requests) < 1:
            raise ConfigError("model_requests 至少为 1")
        self.wall_clock_seconds = float(wall_clock_seconds)
        self.model_requests = int(model_requests)
        self.clock = clock
        self.started_at: float | None = None
        self.requests = 0
        self.tokens: int | None = 0
        self.cost_note = "unknown"

    # ---- 生命周期 -------------------------------------------------
    def start(self) -> "RunBudget":
        if self.started_at is None:
            self.started_at = self.clock()
        return self

    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        return max(0.0, self.clock() - self.started_at)

    def remaining_seconds(self) -> float:
        return max(0.0, self.wall_clock_seconds - self.elapsed())

    def requests_left(self) -> int:
        return max(0, self.model_requests - self.requests)

    def expire_reason(self) -> str | None:
        """``wall_clock`` / ``model_requests`` / ``None``。"""
        if self.remaining_seconds() <= 0:
            return "wall_clock"
        if self.requests >= self.model_requests:
            return "model_requests"
        return None

    def require(self) -> None:
        reason = self.expire_reason()
        if reason:
            raise BudgetExhausted(self._message(reason))

    # ---- 记账 -----------------------------------------------------
    def consume_model_request(self, *, tokens: int | None = None) -> int:
        """在请求**开始**时记账，失败与重试都算一次。"""
        if self.requests >= self.model_requests:
            raise BudgetExhausted(self._message("model_requests"))
        self.requests += 1
        self.record_tokens(tokens)
        return self.requests

    def record_tokens(self, tokens: int | None) -> None:
        if tokens is None:
            self.tokens = None
            return
        if self.tokens is not None:
            self.tokens += int(tokens)

    def snapshot(self) -> dict:
        return {
            "wall_clock_seconds": self.wall_clock_seconds,
            "model_requests": self.model_requests,
            "elapsed_seconds": round(self.elapsed(), 3),
            "remaining_seconds": round(self.remaining_seconds(), 3),
            "requests_used": self.requests,
            "requests_left": self.requests_left(),
            "tokens_used": self.tokens,
            "cost": self.cost_note,
            "expire_reason": self.expire_reason(),
        }

    def _message(self, reason: str) -> str:
        if reason == "wall_clock":
            return "运行墙钟预算已用尽（%.1f 秒）" % self.wall_clock_seconds
        return "模型请求预算已用尽（%d 次）" % self.model_requests
