"""晋级与降级判据：候选技能只有在真实、可区分的运行证据下才变成可复用版本。

按重构需求第 5.4 节：

* 候选只在实验委托内试运行；
* 至少三次满足已登记前提的真实成功证据，且停止/取消测试通过，才登记为可复用版本；
* 同一执行拆成三段不算三次；
* 模拟成功不足以晋级；三次成功也不证明所有局面通用；
* 条件不符时禁用该次调用；违背用户限制或误报完成立即停用该版本；
* 满足前提仍失败则退回候选并要求新证据；旧版本与证据保留。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..contracts.experience import SkillRun

DEFAULT_REQUIRED_SUCCESSES = 3


@dataclass(frozen=True)
class PromotionDecision:
    promote: bool
    reason: str
    successes: int = 0
    distinct_evidence: int = 0
    cancel_checked: bool = False

    def to_dict(self) -> dict:
        return {
            "promote": self.promote,
            "reason": self.reason,
            "successes": self.successes,
            "distinct_evidence": self.distinct_evidence,
            "cancel_checked": self.cancel_checked,
        }


def evaluate_promotion(
    runs: Sequence[SkillRun], *, required_successes: int = DEFAULT_REQUIRED_SUCCESSES
) -> PromotionDecision:
    """判断一个候选技能能否晋级为已验证版本。"""
    successes = [run for run in runs if run.success]
    distinct = {run.event_id for run in successes}
    cancel_checked = any(run.cancelled_cleanly for run in runs)
    if len(distinct) < required_successes:
        return PromotionDecision(
            False,
            "真实成功证据不足：%d/%d（同一次执行拆成多段不算）" % (len(distinct), required_successes),
            len(successes),
            len(distinct),
            cancel_checked,
        )
    if not cancel_checked:
        return PromotionDecision(
            False,
            "缺少停止/取消测试通过的证据",
            len(successes),
            len(distinct),
            cancel_checked,
        )
    return PromotionDecision(
        True,
        "%d 次可区分的真实成功 + 取消测试通过（仅限已登记前提范围内）" % len(distinct),
        len(successes),
        len(distinct),
        cancel_checked,
    )


def handle_failure(*, premises_held: bool, violated_user_limit: bool = False, false_completion: bool = False) -> str:
    """失败后的状态：``candidate``（退回）、``retired``（立即停用）或 ``verified``（保持不变）。"""
    if violated_user_limit or false_completion:
        return "retired"
    if premises_held:
        return "candidate"
    return "verified"
