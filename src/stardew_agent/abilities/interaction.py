"""物品与交互能力：选择物品、使用工具、与对象交互、选择菜单项。

这些能力不判断"该种什么、该卖给谁"：目标与预期效果由模型给出，能力只负责下发、
重新观察并如实报告变化。动作语义（till/plant/water/harvest/chop/clear）由
``expect`` 参数说明，验证使用 :func:`stardew_agent.observation.diff.target_progress`。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping, Protocol

from ..contracts.ability import AbilityDescriptor
from ..contracts.errors import AgentError, PremiseFailed, Rejected
from ..contracts.observation import Observation
from ..contracts.results import Progress
from ..observation.diff import target_progress
from ..observation.identity import target_id
from ..observation.state import to_snapshot
from .base import AbilitySpec, DelegationBase, PremiseCheck, reject_unknown_params, require_int, require_str
from .verification import inventory_delta, tile_delta

#: ``use`` 允许声明的预期效果；``any`` 表示只要求"出现了可验证变化"
EXPECTATIONS = ("any", "till", "plant", "water", "harvest", "chop", "clear")

STALE_CODES = frozenset({"STALE_REF", "MENU_MISMATCH", "TRANSITION", "CANCELLED"})


class ActionPort(Protocol):
    async def select(self, slot: int, **kwargs: Any) -> Mapping[str, Any]: ...

    async def use(self, ref: str, **kwargs: Any) -> Mapping[str, Any]: ...

    async def interact(self, ref: str, **kwargs: Any) -> Mapping[str, Any]: ...

    async def menu(self, ref: str, **kwargs: Any) -> Mapping[str, Any]: ...


def _find_target(target: str, observation: Observation) -> Mapping[str, Any] | None:
    """按当前 ref 或稳定 target_id 在当前观察里找目标。"""
    entity = observation.entity(target)
    if entity is not None:
        return entity
    if target.startswith("target:"):
        for row in observation.entities:
            if target_id(observation.location, row) == target:
                return row
    return None


def _require_target(target: str, observation: Observation) -> Mapping[str, Any]:
    entity = _find_target(target, observation)
    if entity is None:
        raise PremiseFailed(
            "目标 %s 不在当前观察范围（ref 只在单次快照内有效）："
            "先 move_to 到目标地块并重新观察，或改用当前观察里的 ref/target_id" % target
        )
    return entity


class SelectDelegation(DelegationBase):
    """切换当前物品槽；以快照中的 ``player.selectedSlot`` 验证。"""

    def __init__(
        self, port: ActionPort, params: Mapping[str, Any], observation: Observation, *, clock: Callable[[], float]
    ) -> None:
        super().__init__("select_item", params, clock=clock)
        self.port = port
        self.slot = int(params["slot"])
        self.record("before_slot", (observation.player or {}).get("selectedSlot"))

    async def step(self, observation: Observation) -> Progress:
        if self.cancelled:
            return self.cancelled_progress()
        selected = observation.field("player.selectedSlot")
        if self.steps:
            if selected.known and int(selected.value) == self.slot:
                return self.finish("completed", "当前槽位已切换", selected_slot=selected.value)
            if selected.known:
                return self.finish("failed", "槽位没有切换（期望 %d，实际 %s）" % (self.slot, selected.value))
            return self.finish("unknown", "接口已下发，但快照没有提供当前槽位", selected_slot=None)
        self.steps += 1
        try:
            await self.port.select(self.slot, action_id=self.action_id)
        except AgentError as exc:
            if getattr(exc, "code", "") in STALE_CODES:
                return self.finish("failed", "选择物品被拒绝：%s" % exc, error=exc.code)
            raise
        return Progress("running", "已下发选择请求", dict(self.evidence))


class EntityActionDelegation(DelegationBase):
    """对某个现场实体执行一次 use / interact，并核对前后观察。"""

    def __init__(
        self,
        ability: str,
        port: ActionPort,
        params: Mapping[str, Any],
        observation: Observation,
        *,
        clock: Callable[[], float],
    ) -> None:
        super().__init__(ability, params, clock=clock)
        self.port = port
        self.requested_target = str(params["target"])
        self.expect = str(params.get("expect") or "any")
        self.target = _require_target(self.requested_target, observation)
        # 调用桥必须用当前快照的 ref（target_id 解析后也是这里取 ref）。
        self.ref = str(self.target.get("ref") or "")
        self.before = observation
        self.response: Mapping[str, Any] | None = None
        self.record(
            "target",
            {"kind": self.target.get("kind"), "name": self.target.get("name"), "tile": self.target.get("tile")},
        )
        self.record("expect", self.expect)

    async def step(self, observation: Observation) -> Progress:
        if self.cancelled:
            return self.cancelled_progress()
        if self.steps:
            # 第二次进入即核对结果，不再重复下发写操作。
            return self._verify(observation)
        self.steps += 1
        try:
            if self.ability == "use":
                self.response = await self.port.use(self.ref, action_id=self.action_id)
            else:
                self.response = await self.port.interact(self.ref, action_id=self.action_id)
        except AgentError as exc:
            if getattr(exc, "code", "") in STALE_CODES:
                return self.finish("failed", "动作被拒绝：%s" % exc, error=exc.code)
            raise
        return Progress("running", "已下发动作，等待下一次观察核对", dict(self.evidence))

    def _verify(self, observation: Observation) -> Progress:
        tile = self.target.get("tile") or {}
        delta = tile_delta(self.before, observation, tile) if tile else {"changed": False, "fields": {}}
        inventory = inventory_delta(self.before, observation)
        claimed = bool((self.response or {}).get("changed"))
        self.record(
            "response",
            {"changed": claimed, "detail": str((self.response or {}).get("detail") or "")[:200]},
        )
        self.record("tile_delta", delta.get("fields"))
        self.record("inventory_delta", inventory.get("slots"))
        self.record("scene_changed", not self.before.same_scene_as(observation))

        if self.expect != "any":
            progressed = target_progress(
                self.expect, to_snapshot(self.before), to_snapshot(observation), self.target, verified=claimed
            )
            if progressed:
                return self.finish("completed", "观察证明 %s 已发生" % self.expect, verified=True)
            if delta.get("changed") or inventory.get("changed"):
                return self.finish("failed", "现场有变化，但不符合 %s 的完成判据" % self.expect, verified=False)
            return self.finish(
                "unknown",
                "接口%s已执行，但前后观察没有可验证的 %s 变化"
                % ("返回 changed" if claimed else "未返回 changed", self.expect),
                verified=None,
            )

        if delta.get("changed") or inventory.get("changed"):
            return self.finish("completed", "观察到与目标相关的变化", verified=True)
        return self.finish(
            "unknown",
            "接口%s已执行，但快照没有暴露可验证变化" % ("返回 changed" if claimed else "未返回 changed"),
            verified=None,
        )


class MenuDelegation(DelegationBase):
    """选择一个菜单项；以菜单身份/选项是否变化来验证，关闭菜单也算结果。"""

    def __init__(self, port: ActionPort, params: Mapping[str, Any], observation: Observation, *, clock: Callable[[], float]) -> None:
        super().__init__("choose_menu_option", params, clock=clock)
        self.port = port
        self.ref = str(params["option"])
        menu = observation.menu or {}
        options = {str(option.get("ref")): option for option in menu.get("options") or []}
        if self.ref not in options:
            raise PremiseFailed("菜单项 ref 不属于当前菜单：%s" % self.ref)
        self.before_menu = dict(menu)
        self.record("menu_type", menu.get("type"))
        self.record("option_label", options[self.ref].get("label"))

    async def step(self, observation: Observation) -> Progress:
        if self.cancelled:
            return self.cancelled_progress()
        if self.steps:
            menu_now = observation.menu
            if not menu_now:
                return self.finish("completed", "菜单已关闭", menu_after=None)
            changed = (menu_now.get("identity"), menu_now.get("text"), len(menu_now.get("options") or [])) != (
                self.before_menu.get("identity"),
                self.before_menu.get("text"),
                len(self.before_menu.get("options") or []),
            )
            if changed:
                return self.finish("completed", "菜单内容已变化", menu_after=menu_now.get("text"))
            return self.finish("unknown", "接口已下发，但菜单看起来没有变化", menu_after=menu_now.get("text"))
        self.steps += 1
        try:
            await self.port.menu(self.ref, action_id=self.action_id)
        except AgentError as exc:
            if getattr(exc, "code", "") in STALE_CODES:
                return self.finish("failed", "菜单操作被拒绝：%s" % exc, error=exc.code)
            raise
        return Progress("running", "已下发菜单选项", dict(self.evidence))


def _target_check(target: str, observation: Observation) -> PremiseCheck:
    entity = _find_target(target, observation)
    if entity is not None:
        return PremiseCheck("game", True, "目标在当前观察范围")
    return PremiseCheck(
        "game",
        False,
        "目标 %s 不在当前观察范围：先 move_to 到目标地块并重新观察，或改用当前观察里的 ref/target_id"
        % target,
    )


def select_item_ability(port: ActionPort, *, clock: Callable[[], float] = time.monotonic) -> AbilitySpec:
    descriptor = AbilityDescriptor(
        id="select_item",
        version="1.0.0",
        summary="切换当前手持物品（工具或种子所在的背包槽）",
        params={"slot": "背包槽位 0～11"},
        observation_needs=("player.selectedSlot", "inventory"),
        scope="只切换当前槽位；不决定用哪个物品",
        completion="重新观察到的 player.selectedSlot 等于目标槽位",
    )

    def validate(params: Mapping[str, Any]) -> Mapping[str, Any]:
        reject_unknown_params(params, ("slot",))
        return {"slot": require_int(params, "slot", low=0, high=11)}

    def premises(params: Mapping[str, Any], observation: Observation) -> list[PremiseCheck]:
        return [
            PremiseCheck(
                "game",
                False if observation.menu else True,
                "菜单打开时不能切换手持物品" if observation.menu else "没有打开的菜单",
            )
        ]

    def start(params: Mapping[str, Any], observation: Observation, action_id: str) -> SelectDelegation:
        delegation = SelectDelegation(port, params, observation, clock=clock)
        delegation.action_id = action_id or delegation.action_id
        return delegation

    return AbilitySpec(descriptor=descriptor, validate=validate, check_premises=premises, start=start)


def use_ability(port: ActionPort, *, clock: Callable[[], float] = time.monotonic) -> AbilitySpec:
    descriptor = AbilityDescriptor(
        id="use",
        version="1.0.0",
        summary="对现场目标使用当前手持物品，并核对预期效果",
        params={
            "target": "现场实体 ref",
            "expect": "预期效果：%s（默认 any，只要求出现可验证变化）" % "/".join(EXPECTATIONS),
        },
        observation_needs=("entities", "inventory", "menu", "player.selectedSlot"),
        scope="只对给定目标下发一次使用；不选择种子、作物或销售对象",
        completion="按 expect 对应的前后观察判据验证；无法验证时报告 unknown 而不是完成",
        limits=("一次委托只下发一次写操作",),
    )

    def validate(params: Mapping[str, Any]) -> Mapping[str, Any]:
        reject_unknown_params(params, ("target", "expect"))
        expect = str(params.get("expect") or "any")
        if expect not in EXPECTATIONS:
            raise Rejected("expect 只能是 %s" % "/".join(EXPECTATIONS))
        return {"target": require_str(params, "target"), "expect": expect}

    def premises(params: Mapping[str, Any], observation: Observation) -> list[PremiseCheck]:
        return [
            _target_check(str(params["target"]), observation),
            PremiseCheck(
                "game",
                False if observation.menu else True,
                "菜单打开时不能使用工具" if observation.menu else "没有打开的菜单",
            ),
        ]

    def start(params: Mapping[str, Any], observation: Observation, action_id: str) -> EntityActionDelegation:
        delegation = EntityActionDelegation("use", port, params, observation, clock=clock)
        delegation.action_id = action_id or delegation.action_id
        return delegation

    return AbilitySpec(descriptor=descriptor, validate=validate, check_premises=premises, start=start)




def interact_ability(port: ActionPort, *, clock: Callable[[], float] = time.monotonic) -> AbilitySpec:
    descriptor = AbilityDescriptor(
        id="interact",
        version="1.0.0",
        summary="与现场对象交互（箱子、门、NPC 等），不切换手持物品",
        params={"target": "现场实体 ref"},
        observation_needs=("entities", "inventory", "menu"),
        scope="只对给定目标下发一次交互；不决定对话选项或购买内容",
        completion="前后观察出现与该目标相关的变化，或出现新菜单；否则报告 unknown",
        limits=("一次委托只下发一次写操作",),
    )

    def validate(params: Mapping[str, Any]) -> Mapping[str, Any]:
        reject_unknown_params(params, ("target",))
        return {"target": require_str(params, "target")}

    def premises(params: Mapping[str, Any], observation: Observation) -> list[PremiseCheck]:
        return [_target_check(str(params["target"]), observation)]

    def start(params: Mapping[str, Any], observation: Observation, action_id: str) -> EntityActionDelegation:
        delegation = EntityActionDelegation("interact", port, params, observation, clock=clock)
        delegation.action_id = action_id or delegation.action_id
        return delegation

    return AbilitySpec(descriptor=descriptor, validate=validate, check_premises=premises, start=start)


def menu_ability(port: ActionPort, *, clock: Callable[[], float] = time.monotonic) -> AbilitySpec:
    descriptor = AbilityDescriptor(
        id="choose_menu_option",
        version="1.0.0",
        summary="点击当前菜单里的一个选项（对话、商店、确认框等）",
        params={"option": "当前菜单选项的 ref"},
        observation_needs=("menu",),
        scope="只点击给定选项；不决定买卖什么",
        completion="菜单关闭或菜单内容发生变化；未变化时报告 unknown",
        limits=("一次委托只点击一次",),
    )

    def validate(params: Mapping[str, Any]) -> Mapping[str, Any]:
        reject_unknown_params(params, ("option",))
        return {"option": require_str(params, "option")}

    def premises(params: Mapping[str, Any], observation: Observation) -> list[PremiseCheck]:
        refs = {str(option.get("ref")) for option in (observation.menu or {}).get("options") or []}
        return [
            PremiseCheck(
                "game",
                bool(observation.menu),
                "当前有打开的菜单" if observation.menu else "当前没有菜单，无法选择选项",
            ),
            PremiseCheck("game", str(params["option"]) in refs, "选项 ref 属于当前菜单"),
        ]

    def start(params: Mapping[str, Any], observation: Observation, action_id: str) -> MenuDelegation:
        delegation = MenuDelegation(port, params, observation, clock=clock)
        delegation.action_id = action_id or delegation.action_id
        return delegation

    return AbilitySpec(descriptor=descriptor, validate=validate, check_premises=premises, start=start)

