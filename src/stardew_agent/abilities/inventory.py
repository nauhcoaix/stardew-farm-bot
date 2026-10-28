"""背包与世界容器：制作、箱子取放、发货箱、放箱子。

这些命令由 mod 直接执行（不依赖菜单点击）；执行器只负责校验参数、按前后观察验证，
并在结果未知时要求重新观察，不自动重发。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping, Protocol

from ..contracts.ability import AbilityDescriptor
from ..contracts.errors import AgentError, Rejected
from ..contracts.observation import Observation
from ..contracts.results import Progress
from .base import AbilitySpec, DelegationBase, PremiseCheck, reject_unknown_params, require_int, require_str


class InventoryPort(Protocol):
    async def craft(self, item: str, count: int = 1, **kwargs: Any) -> Mapping[str, Any]: ...

    async def chestput(self, tx: int, ty: int, slot: int, **kwargs: Any) -> Mapping[str, Any]: ...

    async def chestget(self, tx: int, ty: int, name: str = "", **kwargs: Any) -> Mapping[str, Any]: ...

    async def ship(self, slot: int, **kwargs: Any) -> Mapping[str, Any]: ...

    async def placechest(self, tx: int, ty: int, **kwargs: Any) -> Mapping[str, Any]: ...


def _slot_count(observation: Observation, slot: int) -> int | None:
    for item in observation.inventory:
        if int(item.get("slot", -1)) == slot:
            return int(item.get("count") or 0)
    return 0


def _name_count(observation: Observation, name: str) -> int | None:
    if "inventory" not in observation.raw:
        return None
    want = name.strip().lower()
    total = 0
    for item in observation.inventory:
        text = " ".join(str(item.get(key) or "") for key in ("name", "itemId")).lower()
        if want and want in text:
            total += int(item.get("count") or 0)
    return total


def _total_items(observation: Observation) -> int:
    return sum(int(item.get("count") or 0) for item in observation.inventory)


class CraftDelegation(DelegationBase):
    """制作一件物品：craft → 观察背包里该物品是否增加。"""

    def __init__(
        self,
        port: InventoryPort,
        params: Mapping[str, Any],
        observation: Observation,
        *,
        clock: Callable[[], float],
    ) -> None:
        super().__init__("craft_item", params, clock=clock)
        self.port = port
        self.item = str(params["item"])
        self.qty = int(params["qty"])
        self.before = _name_count(observation, self.item)
        self.record("item", self.item)
        self.record("qty", self.qty)
        self.record("before_count", self.before)

    async def step(self, observation: Observation) -> Progress:
        if self.cancelled:
            return self.cancelled_progress()
        if self.steps == 0:
            self.steps += 1
            try:
                response = await self.port.craft(self.item, self.qty, action_id=self.action_id)
            except AgentError as exc:
                return self.finish("unknown", "制作请求失败：%s（先重新观察背包）" % exc)
            self.record("response", dict(response))
            if response.get("ok") is False:
                return self.finish(
                    "failed",
                    "制作被拒绝：%s" % (response.get("reason") or "未知原因"),
                    error=str(response.get("code") or "") or None,
                )
            return Progress("running", "已请求制作 %s ×%d" % (self.item, self.qty), dict(self.evidence))
        self.steps += 1
        after = _name_count(observation, self.item)
        self.record("after_count", after)
        gained = None
        if self.before is not None and after is not None:
            gained = after - self.before
        if gained is not None and gained > 0:
            return self.finish("completed", "制作完成：%s +%d" % (self.item, gained), gained=gained)
        return self.finish("unknown", "已请求制作，但背包里没有看到 %s 增加" % self.item, gained=gained)


class InventoryActionDelegation(DelegationBase):
    """箱子取放 / 发货 / 放箱子：执行后按背包变化验证。"""

    def __init__(
        self,
        port: InventoryPort,
        params: Mapping[str, Any],
        observation: Observation,
        *,
        clock: Callable[[], float],
    ) -> None:
        super().__init__("inventory_action", params, clock=clock)
        self.port = port
        self.op = str(params["op"])
        self.slot = int(params["slot"]) if params.get("slot") is not None else None
        self.name = str(params.get("name") or "")
        self.tx = int(params["tx"]) if params.get("tx") is not None else None
        self.ty = int(params["ty"]) if params.get("ty") is not None else None
        self.before_slot = _slot_count(observation, self.slot) if self.slot is not None else None
        self.before_items = _total_items(observation)
        self.before_name = _name_count(observation, self.name) if self.name else None
        self.record("op", self.op)
        self.record("before_slot_count", self.before_slot)
        self.record("before_items", self.before_items)

    async def _perform(self) -> Mapping[str, Any]:
        if self.op == "chest_put":
            return await self.port.chestput(self.tx or 0, self.ty or 0, int(self.slot or 0), action_id=self.action_id)
        if self.op == "chest_get":
            return await self.port.chestget(self.tx or 0, self.ty or 0, self.name, action_id=self.action_id)
        if self.op == "ship":
            return await self.port.ship(int(self.slot or 0), action_id=self.action_id)
        return await self.port.placechest(int(self.tx or 0), int(self.ty or 0), action_id=self.action_id)

    async def step(self, observation: Observation) -> Progress:
        if self.cancelled:
            return self.cancelled_progress()
        if self.steps == 0:
            self.steps += 1
            try:
                response = await self._perform()
            except AgentError as exc:
                return self.finish("unknown", "%s 请求失败：%s（先重新观察背包）" % (self.op, exc))
            self.record("response", dict(response))
            if response.get("ok") is False:
                return self.finish(
                    "failed",
                    "%s 被拒绝：%s" % (self.op, response.get("reason") or "未知原因"),
                    error=str(response.get("code") or "") or None,
                )
            return Progress("running", "已请求 %s，等待观察背包" % self.op, dict(self.evidence))

        self.steps += 1
        after_items = _total_items(observation)
        after_slot = _slot_count(observation, self.slot) if self.slot is not None else None
        after_name = _name_count(observation, self.name) if self.name else None
        self.record("after_items", after_items)
        evidence = {
            "before_items": self.before_items,
            "after_items": after_items,
            "before_slot_count": self.before_slot,
            "after_slot_count": after_slot,
            "before_name_count": self.before_name,
            "after_name_count": after_name,
        }
        if self.op in ("chest_put", "ship"):
            if self.before_slot is not None and after_slot is not None and after_slot < self.before_slot:
                return self.finish("completed", "背包槽位 %s 已清空/减少" % self.slot, **evidence)
        if self.op == "chest_get":
            if self.before_name is not None and after_name is not None and after_name > self.before_name:
                return self.finish("completed", "背包里 %s 增加" % self.name, **evidence)
            if after_items > self.before_items:
                return self.finish("completed", "背包物品增加", **evidence)
        if self.op == "place_chest":
            if after_items < self.before_items:
                return self.finish("completed", "木材被消耗，箱子已放置", **evidence)
        return self.finish("unknown", "%s 已下发，但背包没有可验证变化" % self.op, **evidence)



def craft_item_ability(port: InventoryPort, *, clock: Callable[[], float] = time.monotonic) -> AbilitySpec:
    descriptor = AbilityDescriptor(
        id="craft_item",
        version="1.0.0",
        summary="按配方制作物品（产物需要背包空位或可叠加余量，材料由游戏扣除）",
        params={
            "item": "配方/产物名称（模糊匹配，例如 箱子）",
            "qty": "制作数量（1～10，默认 1）",
        },
        observation_needs=("inventory", "menu"),
        scope="只调用游戏制作接口；不学习配方、不切换工具、不买材料",
        completion="背包里该物品数量增加；看不到变化时报告 unknown",
        limits=("qty<=10", "背包满时拒绝（INVENTORY_FULL）"),
    )

    def validate(params: Mapping[str, Any]) -> Mapping[str, Any]:
        reject_unknown_params(params, ("item", "qty"))
        return {
            "item": require_str(params, "item"),
            "qty": require_int({"qty": params.get("qty", 1)}, "qty", low=1, high=10),
        }

    def premises(params: Mapping[str, Any], observation: Observation) -> list[PremiseCheck]:
        return [
            PremiseCheck("game", False if observation.menu else True, "菜单打开时先关闭菜单" if observation.menu else "没有打开的菜单"),
            PremiseCheck(
                "game",
                True if observation.inventory else None,
                "背包已知" if observation.inventory else "背包未知，无法核对材料与产物",
            ),
        ]

    def start(params: Mapping[str, Any], observation: Observation, action_id: str) -> CraftDelegation:
        delegation = CraftDelegation(port, params, observation, clock=clock)
        delegation.action_id = action_id or delegation.action_id
        return delegation

    return AbilitySpec(descriptor=descriptor, validate=validate, check_premises=premises, start=start)


OPS = ("chest_put", "chest_get", "ship", "place_chest")


def inventory_action_ability(port: InventoryPort, *, clock: Callable[[], float] = time.monotonic) -> AbilitySpec:
    descriptor = AbilityDescriptor(
        id="inventory_action",
        version="1.0.0",
        summary="箱子取放、把物品放进发货箱、在空地上放箱子",
        params={
            "op": "chest_put / chest_get / ship / place_chest（place_chest 直接消耗 50 木材并放置箱子，不需要空背包格）",
            "tx": "目标容器所在格 x（chest_* 与 place_chest 必填）",
            "ty": "目标容器所在格 y",
            "slot": "背包槽位（chest_put 与 ship 必填）",
            "name": "chest_get 可选：只取名字包含该字符串的物品",
        },
        observation_needs=("inventory", "entities", "menu"),
        scope="只操作指定格子/槽位；不移动、不买、不卖；一次只做一个动作",
        completion="背包槽位减少（put/ship）或背包物品增加（get/place 消耗木材）；否则 unknown",
        limits=(
            "一次委托只执行一个容器动作",
            "place_chest 只能放在人物 1～2 格内的空格，且消耗 50 木材",
        ),
    )

    def validate(params: Mapping[str, Any]) -> Mapping[str, Any]:
        reject_unknown_params(params, ("op", "tx", "ty", "slot", "name"))
        op = str(params.get("op") or "")
        if op not in OPS:
            raise Rejected("op 只能是 %s" % "/".join(OPS))
        out: dict[str, Any] = {"op": op}
        if op in ("chest_put", "chest_get", "place_chest"):
            out["tx"] = require_int(params, "tx", low=0, high=400)
            out["ty"] = require_int(params, "ty", low=0, high=400)
        if op in ("chest_put", "ship"):
            out["slot"] = require_int(params, "slot", low=0, high=35)
        if op == "chest_get" and params.get("name") is not None:
            out["name"] = require_str(params, "name")
        return out

    def premises(params: Mapping[str, Any], observation: Observation) -> list[PremiseCheck]:
        return [
            PremiseCheck("game", False if observation.menu else True, "菜单打开时先关闭菜单" if observation.menu else "没有打开的菜单"),
            PremiseCheck(
                "game",
                True if observation.inventory else None,
                "背包已知" if observation.inventory else "背包未知，无法核对容器动作",
            ),
        ]

    def start(params: Mapping[str, Any], observation: Observation, action_id: str) -> InventoryActionDelegation:
        delegation = InventoryActionDelegation(port, params, observation, clock=clock)
        delegation.action_id = action_id or delegation.action_id
        return delegation

    return AbilitySpec(descriptor=descriptor, validate=validate, check_premises=premises, start=start)

