"""计划新鲜度：执行前核对关键前提，避免用过期观察指挥现在。

按重构需求第 5.4 节：只核对存档、地图、目标身份、菜单状态与动作所需资源；
**仅仅是游戏时间推进不自动废弃整份计划**，否则实时世界里几乎所有回复都会过期。

未知前提不算过期，但会被记录，供验证与经验判定使用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from ..contracts.decision import Decision
from ..contracts.observation import Observation
from .predicates import evaluate_all


@dataclass(frozen=True)
class Freshness:
    """本次决策还能不能用来下发动作。"""

    stale: bool
    reasons: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"stale": self.stale, "reasons": list(self.reasons), "unknowns": list(self.unknowns)}


def check(
    decision: Decision,
    based_on: Observation,
    current: Observation,
    *,
    premises: Iterable[Mapping[str, Any]] = (),
) -> Freshness:
    """比较决策依据与当前观察；同时求值该能力的真实前提。"""
    if decision.based_on_snapshot_id and decision.based_on_snapshot_id == current.snapshot_id:
        return Freshness(False)

    reasons: list[str] = []
    if decision.based_on_snapshot_id and decision.based_on_snapshot_id != based_on.snapshot_id:
        reasons.append(
            "决策绑定的观察（%s）不是本次提供的观察（%s）" % (decision.based_on_snapshot_id, based_on.snapshot_id)
        )
    if not based_on.same_scene_as(current):
        reasons.append("存档/会话/地图已变化（%s → %s）" % (based_on.location, current.location))
    if bool(based_on.menu) != bool(current.menu):
        reasons.append("菜单状态变化（%s → %s）" % ("打开" if based_on.menu else "关闭", "打开" if current.menu else "关闭"))

    met, details = evaluate_all(list(premises), current)
    unknowns: tuple[str, ...] = ()
    if met is False:
        reasons.append("能力前提不再满足：%s" % "；".join(details))
    elif met is None:
        unknowns = tuple(details)

    return Freshness(bool(reasons), tuple(reasons), unknowns)
