"""把决策里的临时 ref 重新绑定到当前现场。

桥的 ref 形如 ``@s63:e196``，``s63`` 是快照序号：每次 ``snapshot`` 都会重编号。
模型看到 ref、思考数秒后才执行，这时现场已经是另一个快照，旧 ref 在当前观察里
通常找不到。让模型“重新取 ref”不可行——它拿不到未来的快照，所以由执行层负责
重新绑定：

* 当前观察里已有该 ref → 不动；
* 旧观察里能按 ref 找到目标 → 取它的地块：
  - ``move_to.near`` 换成坐标 ``x/y``（移动只需要坐标，目标可以在观察范围外）；
  - ``use`` / ``interact`` 若当前观察同地块有同类实体 → 换成当前 ref；
* 都找不到 → 原样返回，由能力拒绝并给出可操作提示。

绑定结果写入 ``notes``，供事件证据与调试使用。
"""

from __future__ import annotations

from typing import Any, Mapping

from ..contracts.decision import AbilityCall
from ..contracts.observation import Observation


def _tile_of(entity: Mapping[str, Any] | None) -> dict | None:
    tile = (entity or {}).get("tile") or {}
    if tile.get("x") is None or tile.get("y") is None:
        return None
    return {"x": int(tile["x"]), "y": int(tile["y"])}


def _same_target(row: Mapping[str, Any], old: Mapping[str, Any]) -> bool:
    return row.get("kind") == old.get("kind") and row.get("name") == old.get("name")


def rebind_call(
    call: AbilityCall, based_on: Observation | None, current: Observation
) -> tuple[AbilityCall, dict]:
    """返回（可能改写参数的调用，绑定说明）。"""
    if based_on is None:
        return call, {}
    params: dict[str, Any] = dict(call.params)
    notes: dict[str, Any] = {}

    near = params.get("near")
    if isinstance(near, str) and near and current.entity(near) is None:
        tile = _tile_of(based_on.entity(near))
        if tile is not None:
            params.pop("near", None)
            params["x"] = tile["x"]
            params["y"] = tile["y"]
            notes["near"] = {"from": near, "to_tile": tile, "reason": "旧快照 ref 改为坐标"}

    target = params.get("target")
    if isinstance(target, str) and target and not target.startswith("target:"):
        if current.entity(target) is None:
            old = based_on.entity(target)
            tile = _tile_of(old)
            if old is not None and tile is not None:
                for row in current.entities:
                    if row.get("tile") == tile and _same_target(row, old) and row.get("ref"):
                        params["target"] = str(row["ref"])
                        notes["target"] = {"from": target, "to": row["ref"], "reason": "旧快照 ref 改为当前 ref"}
                        break

    if not notes:
        return call, {}
    return AbilityCall(ability=call.ability, params=params, version=call.version), notes
