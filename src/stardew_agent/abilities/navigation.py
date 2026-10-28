"""移动能力：把人物带到坐标或某个现场对象旁边。

只做"已委托的移动"：目的地由模型给出，这里不猜路线、不选目标、不在失败后换地方。
分段推进（每次 :meth:`step` 一次桥请求），每步之后由运行环境重新观察再决定是否继续。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping, Protocol

from ..contracts.ability import AbilityDescriptor
from ..contracts.errors import AgentError, PremiseFailed, Rejected
from ..contracts.observation import Observation
from ..contracts.results import Progress
from ..observation.state import position_of
from .base import AbilitySpec, DelegationBase, PremiseCheck, reject_unknown_params, require_int, require_str
from .verification import scene_changed

#: 现场引用失效类错误：重新观察比原地重试更有意义
STALE_CODES = frozenset({"STALE_REF", "MENU_MISMATCH", "TRANSITION", "CANCELLED"})


class MovePort(Protocol):
    """游戏桥里本能力用到的唯一方法。"""

    async def move(
        self, x: int | None = None, y: int | None = None, near: str | None = None, **kwargs: Any
    ) -> Mapping[str, Any]: ...


def _resolve_target(params: Mapping[str, Any], observation: Observation) -> tuple[tuple[int, int], str | None]:
    """解析目标：坐标或现场对象的 ``ref``（取其地块）。坐标为 -1 的轴保持当前位置。"""
    near = params.get("near")
    if near:
        entity = observation.entity(str(near))
        if entity is None:
            raise PremiseFailed(
                "目标 %s 无法定位：ref 只在单次快照内有效，决策时的观察里也没有它。"
                "请改用坐标 x/y，或先观察现场的 ref 再执行。" % near
            )
        tile = entity.get("tile") or {}
        if tile.get("x") is None or tile.get("y") is None:
            raise PremiseFailed("目标实体没有地块坐标：%s" % near)
        return (int(tile["x"]), int(tile["y"])), str(near)
    x, y = int(params["x"]), int(params["y"])
    if x == -1 or y == -1:
        here = position_of(observation)
        if here is None:
            raise PremiseFailed("x/y 用 -1 表示保持当前坐标，但这次观察里没有玩家位置")
        x = here[0] if x == -1 else x
        y = here[1] if y == -1 else y
    return (x, y), None


class MoveDelegation(DelegationBase):
    """一次移动委托：到达即完成，无进展或步数用尽即失败。"""

    def __init__(
        self,
        port: MovePort,
        params: Mapping[str, Any],
        observation: Observation,
        *,
        max_steps: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__("move_to", params, clock=clock)
        self.port = port
        self.max_steps = int(max_steps)
        self.target, self.ref = _resolve_target(params, observation)
        self.start_position = observation.position()
        self.last_request_position = position_of(observation)
        self._start_observation = observation
        self.exit_goal = self._detect_exit(observation)
        self.record("target_tile", {"x": self.target[0], "y": self.target[1]})
        self.record("target_ref", self.ref)
        self.record("exit_goal", self.exit_goal)
        self.record("start_position", self.start_position)

    def _detect_exit(self, observation: Observation) -> bool:
        """目标是出口/门时，只有真的切图才算完成；站在门边不算。"""
        entity = observation.entity(self.ref) if self.ref else None
        if entity is None:
            for candidate in observation.entities:
                tile = candidate.get("tile") or {}
                if (tile.get("x"), tile.get("y")) != (self.target[0], self.target[1]):
                    continue
                if str(candidate.get("kind") or "") in {"exit", "door"}:
                    entity = candidate
                    break
        if entity is None:
            return False
        kind = str(entity.get("kind") or "")
        action = str(entity.get("action") or "").lower()
        return kind in {"exit", "door"} or "warp" in action

    def _reached(self, observation: Observation) -> bool:
        if self.exit_goal:
            return False  # 出口以真正切换地图为准，避免“站在门口=已到达”
        here = position_of(observation)
        if here is None:
            return False
        return abs(here[0] - self.target[0]) + abs(here[1] - self.target[1]) <= 1

    async def step(self, observation: Observation) -> Progress:
        if self.cancelled:
            return self.cancelled_progress()
        if self.steps and scene_changed(self._start_observation, observation):
            #: A* 不会把切图点当中间格，所以移动中切图基本就是走到了出口/门。
            return self.finish(
                "completed",
                "已通过出口进入新场景" if self.exit_goal else "移动过程中切换了地图",
                final_position=observation.position(),
                location=observation.location,
                requests=self.steps,
                positions=self.evidence.get("positions"),
            )
        here = position_of(observation)
        if self._reached(observation):
            return self.finish(
                "completed",
                "已到达目标地块或相邻格",
                final_position=observation.position(),
                requests=self.steps,
                positions=self.evidence.get("positions"),
            )
        if self.steps and here == self.last_request_position:
            return self.finish(
                "failed",
                "上一次移动没有改变位置：门可能没开、需要钥匙或目标不可达"
                if self.exit_goal
                else "上一次移动没有改变位置（碰撞、菜单或目标不可达）",
                final_position=observation.position(),
            )
        if self.steps >= self.max_steps:
            return self.finish(
                "failed",
                "达到移动步数上限 %d，仍未到达" % self.max_steps,
                final_position=observation.position(),
            )
        self.steps += 1
        try:
            # 同一个逻辑委托始终用同一个 actionId：传输重试/停止都能对上号。
            if self.ref is not None:
                await self.port.move(near=self.ref, action_id=self.action_id)
            else:
                await self.port.move(x=self.target[0], y=self.target[1], action_id=self.action_id)
        except AgentError as exc:
            if getattr(exc, "code", "") in STALE_CODES:
                return self.finish("failed", "移动被拒绝：%s" % exc, error=exc.code)
            raise
        self.last_request_position = here
        positions = list(self.evidence.get("positions") or [])
        positions.append({"x": here[0], "y": here[1]})
        self.record("positions", positions)
        return Progress("running", "已下发移动请求，等待下一次观察确认位置", dict(self.evidence))


def move_ability(port: MovePort, *, clock: Callable[[], float] = time.monotonic) -> AbilitySpec:
    """构造 ``move_to`` 能力。"""

    descriptor = AbilityDescriptor(
        id="move_to",
        version="1.0.0",
        summary="让人物走到指定坐标，或走到某个现场对象的相邻格",
        params={
            "x": "目标格 x（与 near 二选一；-1 表示保持当前 x）",
            "y": "目标格 y（与 near 二选一；-1 表示保持当前 y）",
            "near": "现场实体 ref（与 x/y 二选一）",
            "max_steps": "本次委托最多下发几次移动（1～6，默认 3）",
        },
        observation_needs=("player.position", "player.canMove", "entities", "menu", "location"),
        scope="只移动当前人物；不选择目的地、不改变物品或对象状态",
        completion="人物位于目标地块或其四邻格之一；目标是出口/门时，以真正切换地图为完成",
        limits=("max_steps<=6", "每次委托只服务一个目标"),
    )

    def validate(params: Mapping[str, Any]) -> Mapping[str, Any]:
        reject_unknown_params(params, ("x", "y", "near", "max_steps"))
        max_steps = require_int({"max_steps": params.get("max_steps", 3)}, "max_steps", low=1, high=6)
        if params.get("near") is not None:
            if params.get("x") is not None or params.get("y") is not None:
                raise Rejected("near 与 x/y 不能同时给出")
            return {"near": require_str(params, "near"), "max_steps": max_steps}
        x = require_int(params, "x", low=-1, high=400)
        y = require_int(params, "y", low=-1, high=400)
        if x == -1 and y == -1:
            raise Rejected("x 和 y 不能同时为 -1：-1 表示该轴保持当前位置，至少给一个真实坐标")
        return {"x": x, "y": y, "max_steps": max_steps}

    def premises(params: Mapping[str, Any], observation: Observation) -> list[PremiseCheck]:
        can_move = observation.field("player.canMove")
        checks = [
            PremiseCheck(
                "game",
                bool(can_move.value) if can_move.known else None,
                "player.canMove=%s" % can_move.value,
            ),
            PremiseCheck(
                "game",
                False if observation.menu else True,
                "菜单打开时不能自由移动" if observation.menu else "没有打开的菜单",
            ),
        ]
        if params.get("near"):
            exists = observation.entity(str(params["near"])) is not None
            checks.append(
                PremiseCheck(
                    "game",
                    True if exists else None,
                    "目标 ref 在当前观察里"
                    if exists
                    else "目标 ref 不在当前观察里（ref 只在单次快照内有效）；执行层会尝试按决策时的观察改绑坐标",
                )
            )
        else:
            in_scope = observation.field("entities")
            checks.append(
                PremiseCheck(
                    "game",
                    True if in_scope.known else None,
                    "目标坐标是否在观察范围内：%s" % ("是" if in_scope.known else "未知（观察范围有限）"),
                )
            )
        return checks

    def start(params: Mapping[str, Any], observation: Observation, action_id: str) -> MoveDelegation:
        delegation = MoveDelegation(port, params, observation, max_steps=int(params["max_steps"]), clock=clock)
        delegation.action_id = action_id or delegation.action_id
        return delegation

    return AbilitySpec(descriptor=descriptor, validate=validate, check_premises=premises, start=start)

