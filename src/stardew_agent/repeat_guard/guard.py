"""按调用方提供的动作键做限频。

来源：``stardew_valley_dfy/bot/repeat_guard.py`` 的 ``RepeatGuard``。只保留算法：
按动作键记录执行次数与最近执行时刻、窗口内拦截、次数用尽拦截、清零与缓存上限。

不继承旧接线与隐含业务规则：

* **动作键由调用方定义**（运行时用“能力 + 参数”，因此不同地块是不同动作）；
* **只拦模型重新下发的同一动作**；执行器自己的有进展续跑不调用 :meth:`record`；
* 计数何时清零（换存档、换游戏日、重启）由运行时决定，不写死在这里；
* 窗口与次数必须由配置显式给出，模块内不埋默认业务数值。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class RepeatPolicy:
    """限频参数。``window_seconds`` / ``max_runs`` 来自运行配置，不提供隐式默认值。

    ``exempt_abilities`` 是能力 id 白名单：其中的能力完全不参与限频检查与计数
    （例如只切换手持槽位的 ``select_item``）。它只跳过限频，不跳过参数/前提/所有权/预算校验。
    """

    window_seconds: float
    max_runs: int
    max_tracked: int = 2000
    exempt_abilities: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.window_seconds < 0:
            raise ValueError("window_seconds 不能为负数")
        if self.max_runs < 1:
            raise ValueError("max_runs 至少为 1")
        if self.max_tracked < 1:
            raise ValueError("max_tracked 至少为 1")
        if not isinstance(self.exempt_abilities, frozenset):
            object.__setattr__(self, "exempt_abilities", frozenset(self.exempt_abilities))
        if any(not isinstance(name, str) or not name.strip() for name in self.exempt_abilities):
            raise ValueError("exempt_abilities 必须是非空能力 id")

    def exempts(self, ability: str | None) -> bool:
        return bool(ability) and ability in self.exempt_abilities



@dataclass(frozen=True)
class GuardDecision:
    """是否允许本次下发，以及被拦下的类别。"""

    allowed: bool
    reason: str = ""
    kind: str = ""
    runs: int = 0

    def to_dict(self) -> dict:
        return {"allowed": self.allowed, "reason": self.reason, "kind": self.kind, "runs": self.runs}


class RepeatGuard:
    """同一动作键：窗口内不重复，累计次数用尽后不再下发。"""

    def __init__(self, policy: RepeatPolicy, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.policy = policy
        self.clock = clock
        self._runs: dict[str, list[float]] = {}

    def check(self, key: str, *, ability: str | None = None) -> GuardDecision:
        """能否执行；``kind`` 为 ``recent``（窗口内）、``limit``（次数用尽）或 ``exempt``（白名单）。

        白名单按能力 id 判断：同一个能力的不同参数（不同地块/槽位）都豁免。
        """
        runs, last = self._row(key)
        if self.policy.exempts(ability):
            return GuardDecision(True, "能力 %s 在限频白名单中" % ability, "exempt", int(runs))
        now = self.clock()
        if last is not None and now - last < self.policy.window_seconds:
            return GuardDecision(
                False,
                "动作 %s 在 %.0f 秒内刚执行过（%.1f 秒前），先观察现场变化" % (key, self.policy.window_seconds, now - last),
                "recent",
                int(runs),
            )
        if runs >= self.policy.max_runs:
            return GuardDecision(
                False,
                "动作 %s 已执行 %d 次，不再下发；换目标、换能力或先记录结论" % (key, int(runs)),
                "limit",
                int(runs),
            )
        return GuardDecision(True, "", "", int(runs))

    def record(self, key: str, *, ability: str | None = None) -> int:
        """记录一次“模型要求的新执行”，返回累计次数；白名单能力不计数。"""
        if self.policy.exempts(ability):
            return 0
        runs, _ = self._row(key)
        self._runs[key] = [runs + 1, self.clock()]
        if len(self._runs) > self.policy.max_tracked:
            self._prune()
        return int(runs + 1)

    def runs(self, key: str) -> int:
        return int(self._row(key)[0])

    def reset(self) -> None:
        """清零全部计数（换存档、换游戏日或重启后由调用方决定）。"""
        self._runs.clear()

    def snapshot(self) -> dict:
        return {
            "tracked": len(self._runs),
            "policy": {
                "window_seconds": self.policy.window_seconds,
                "max_runs": self.policy.max_runs,
                "max_tracked": self.policy.max_tracked,
                "exempt_abilities": sorted(self.policy.exempt_abilities),
            },
        }

    def _row(self, key: str) -> tuple[float, float | None]:
        row = self._runs.get(key)
        if not row:
            return 0.0, None
        return row[0], row[1]

    def _prune(self) -> None:
        ordered = sorted(self._runs.items(), key=lambda item: item[1][1] or 0.0)
        self._runs = dict(ordered[-(self.policy.max_tracked // 2) :])
