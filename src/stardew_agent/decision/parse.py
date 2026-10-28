"""解析模型回复：形状校验、能力与参数校验、判据校验。

模型回复不是可信输入。这里把它变成 :class:`Decision`，并保证：

* 决策绑定的是“我们给它的那次观察”，而不是模型自称的时刻；
* 未知能力、非法参数、非法判据一律拒绝（``Rejected``）；
* 允许不下发动作（``ability: null``），但必须说明原因。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from ..contracts.decision import AbilityCall, Decision
from ..contracts.errors import Rejected
from ..contracts.observation import Observation
from . import predicates

#: 参数校验器：返回规范化参数，非法时抛 ``Rejected``
ParamsValidator = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]


class DecisionError(Rejected):
    code = "DECISION"


def parse_decision(
    payload: Mapping[str, Any] | None,
    *,
    observation: Observation,
    validate_params: ParamsValidator | None = None,
    model: str = "",
) -> Decision:
    """把模型 JSON 变成决策记录。"""
    if not isinstance(payload, Mapping):
        raise DecisionError("模型回复不是 JSON 对象")
    goal = str(payload.get("goal") or "").strip()
    if not goal:
        raise DecisionError("决策缺少 goal：必须说明当前小目标")

    call_row = payload.get("call") if isinstance(payload.get("call"), Mapping) else {}
    ability = payload.get("ability", call_row.get("ability"))
    params = payload.get("params", call_row.get("params"))
    revision = payload.get("goal_revision") or 0
    if not isinstance(revision, int) or revision < 0:
        raise DecisionError("goal_revision 必须是非负整数")

    completion = payload.get("completion")
    if completion is not None:
        completion = predicates.validate(completion)
    stops = predicates.validate_all(payload.get("stop_conditions"))

    call = None
    if ability not in (None, "", "null"):
        if not isinstance(ability, str):
            raise DecisionError("ability 必须是字符串或 null")
        if params is None:
            params = {}
        if not isinstance(params, Mapping):
            raise DecisionError("params 必须是对象")
        params = dict(params)
        if validate_params is not None:
            params = dict(validate_params(ability, params))
        call = AbilityCall(ability=ability, params=params)

    return Decision(
        goal=goal,
        based_on_sampled_at=observation.sampled_at,
        based_on_snapshot_id=observation.snapshot_id,
        reason=str(payload.get("reason") or ""),
        call=call,
        goal_revision=revision,
        completion=completion,
        stop_conditions=tuple(stops),
        note=str(payload.get("note") or ""),
        model=model,
        raw=dict(payload),
    )
