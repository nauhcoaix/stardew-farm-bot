"""路径探索：把“走到未探索前沿”这件重复的事从模型手里收回来。

模型只给步数/时长上限，执行器用前沿候选（``exploration.candidates`` 的
``mode="path"`` 项）挑最近、未访问的点，走过去并确认位置变化。

候选来源由接线注入（运行时用观察 + 已知地形计算），本模块不读数据库。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping, Protocol, Sequence

from ..contracts.ability import AbilityDescriptor
from ..contracts.errors import Rejected
from ..contracts.observation import Observation
from ..contracts.results import Progress
from ..observation.state import position_of
from .base import AbilitySpec, DelegationBase, PremiseCheck, reject_unknown_params, require_int


class ExplorePort(Protocol):
    async def move(
        self, x: int | None = None, y: int | None = None, near: str | None = None, **kwargs: Any
    ) -> Mapping[str, Any]: ...


CandidatesSupplier = Callable[[Observation], Sequence[Mapping[str, Any]]]


class ExploreDelegation(DelegationBase):
    """在前沿候选之间移动；位置没有变化就换下一个点，步数/时长有上限。"""

    def __init__(
        self,
        port: ExplorePort,
        candidates: CandidatesSupplier,
        params: Mapping[str, Any],
        observation: Observation,
        *,
        clock: Callable[[], float],
    ) -> None:
        super().__init__("explore_area", params, clock=clock)
        self.port = port
        self.candidates = candidates
        self.max_steps = int(params["max_steps"])
        self.max_seconds = float(params["max_seconds"])
        self.target: tuple[int, int] | None = None
        self.attempted: set[tuple[int, int]] = set()
        self.visited: list[dict] = []
        self.attempts = 0
        self.start_position = observation.position()
        self.last_position = position_of(observation)
        self.record("start_position", self.start_position)

    def _pick(self, observation: Observation) -> tuple[int, int] | None:
        origin = position_of(observation)
        points: list[tuple[int, tuple[int, int]]] = []
        for row in self.candidates(observation) or ():
            if row.get("mode") != "path" or row.get("visited"):
                continue
            try:
                point = (int(row["x"]), int(row["y"]))
            except (KeyError, TypeError, ValueError):
                continue
            if point in self.attempted or point == origin:
                continue
            distance = abs(point[0] - origin[0]) + abs(point[1] - origin[1]) if origin else 10**6
            points.append((distance, point))
        points.sort()
        return points[0][1] if points else None

    def _stop(self, state: str, detail: str) -> Progress:
        return self.finish(state, detail, visited=self.visited, partial=bool(self.visited))

    async def _move(self, target: tuple[int, int]) -> None:
        self.steps += 1
        await self.port.move(x=target[0], y=target[1], action_id="%s:%d" % (self.action_id, self.steps))

    async def step(self, observation: Observation) -> Progress:
        if self.cancelled:
            return self.cancelled_progress()
        if self.elapsed() > self.max_seconds:
            return self._stop("unknown", "探索超过时长上限 %.0f 秒" % self.max_seconds)
        here = position_of(observation)
        if here is None:
            return self._stop("unknown", "观察里没有人物位置")

        if self.target is not None:
            if here != self.last_position:
                self.last_position = here
                self.attempts = 0
                self.record("last_position", {"x": here[0], "y": here[1]})
                if abs(here[0] - self.target[0]) + abs(here[1] - self.target[1]) <= 1:
                    self.visited.append({"tile": {"x": self.target[0], "y": self.target[1]}})
                    self.attempted.add(self.target)
                    self.target = None
                    return Progress("running", "已到达 (%d, %d)" % here, dict(self.evidence))
                return Progress("running", "移动中：现在 (%d, %d)" % here, dict(self.evidence))
            self.attempts += 1
            if self.attempts >= 2:
                self.record("last_error", "目标 (%d, %d) 没有让位置变化" % self.target)
                self.attempted.add(self.target)
                self.target = None
            else:
                if self.steps >= self.max_steps:
                    return self._stop("unknown", "达到步数上限 %d" % self.max_steps)
                await self._move(self.target)
                return Progress("running", "重试移动到 (%d, %d)" % self.target, dict(self.evidence))

        if self.steps >= self.max_steps:
            return self._stop("unknown", "达到步数上限 %d" % self.max_steps)
        target = self._pick(observation)
        if target is None:
            return self._stop("completed", "没有更多未访问的前沿候选（走过 %d 个点）" % len(self.visited))
        self.target = target
        self.attempts = 0
        await self._move(target)
        return Progress("running", "走向前沿 (%d, %d)" % target, dict(self.evidence))



def explore_ability(
    port: ExplorePort,
    candidates: CandidatesSupplier,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> AbilitySpec:
    descriptor = AbilityDescriptor(
        id="explore_area",
        version="1.0.0",
        summary="走到观察范围内的未探索前沿（自动挑最近的点并确认位置变化）",
        params={
            "max_steps": "本次最多移动几次（1～20，默认 6）",
            "max_seconds": "本次最长秒数（5～180，默认 60）",
        },
        observation_needs=("player.position", "player.canMove", "entities", "menu", "location"),
        scope="只走向当前观察里的前沿候选；不跨地图、不决定长期路线、不交互",
        completion="没有更多未访问前沿候选，或到达步数/时长上限（如实报告走过几个点）",
        limits=("max_steps<=20", "max_seconds<=180", "取消立即停止"),
    )

    def validate(params: Mapping[str, Any]) -> Mapping[str, Any]:
        reject_unknown_params(params, ("max_steps", "max_seconds"))
        max_steps = require_int({"max_steps": params.get("max_steps", 6)}, "max_steps", low=1, high=20)
        seconds = params.get("max_seconds", 60)
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise Rejected("参数 max_seconds 必须是数字")
        if not 5 <= float(seconds) <= 180:
            raise Rejected("参数 max_seconds 应在 5～180 秒")
        return {"max_steps": max_steps, "max_seconds": float(seconds)}

    def premises(params: Mapping[str, Any], observation: Observation) -> list[PremiseCheck]:
        can_move = observation.field("player.canMove")
        return [
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
            PremiseCheck(
                "game",
                True if observation.entities else None,
                "本次观察有实体，可计算前沿" if observation.entities else "本次观察没有实体，无法计算前沿",
            ),
        ]

    def start(params: Mapping[str, Any], observation: Observation, action_id: str) -> ExploreDelegation:
        delegation = ExploreDelegation(port, candidates, params, observation, clock=clock)
        delegation.action_id = action_id or delegation.action_id
        return delegation

    return AbilitySpec(descriptor=descriptor, validate=validate, check_premises=premises, start=start)

