"""商店与通用菜单交互：读取菜单、购买、点击、关闭。

模型只给“买什么、买多少、点哪里”，执行器负责：

* 用 ``menudump`` 读取商品与坐标（价格/序号来自游戏，不接受模型猜价格）；
* 用 ``shopbuy`` / ``menuclick`` / ``menuclickinv`` / ``menuclose`` 执行；
* 用前后观察验证（钱减少、物品增加、菜单签名变化或关闭），拿不到证据就报 ``unknown``。

写入命令超时按“结果未知”处理：不再自动重发，必须先重新观察。
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Mapping, Protocol, Sequence

from ..contracts.ability import AbilityDescriptor
from ..contracts.errors import AgentError, Rejected
from ..contracts.observation import Observation
from ..contracts.results import Progress
from .base import AbilitySpec, DelegationBase, PremiseCheck, reject_unknown_params, require_int, require_str


class ShopPort(Protocol):
    async def menudump(self) -> Mapping[str, Any]: ...

    async def shopbuy(self, index: int, qty: int = 1, **kwargs: Any) -> Mapping[str, Any]: ...


class MenuPort(Protocol):
    async def menudump(self) -> Mapping[str, Any]: ...

    async def menuclick(self, x: int, y: int, **kwargs: Any) -> Mapping[str, Any]: ...

    async def menuclickinv(self, x: int, y: int, **kwargs: Any) -> Mapping[str, Any]: ...

    async def menuclose(self, **kwargs: Any) -> Mapping[str, Any]: ...


def _menu_type(observation: Observation) -> str:
    return str((observation.menu or {}).get("type") or "")


def _menu_signature(observation: Observation) -> str:
    menu = observation.menu or {}
    return json.dumps(
        [menu.get("type"), menu.get("identity"), menu.get("text"), len(menu.get("options") or [])],
        ensure_ascii=False,
        sort_keys=True,
    )


def _money(observation: Observation) -> int | None:
    field = observation.field("player.money")
    return int(field.value) if field.known and isinstance(field.value, (int, float)) else None


def _count_item(observation: Observation, name: str) -> int | None:
    """背包里名字/id 含给定字符串的物品总数量；快照没有 inventory 字段时为 ``None``。"""
    if "inventory" not in observation.raw:
        return None
    want = name.strip().lower()
    total = 0
    for item in observation.inventory:
        text = " ".join(str(item.get(key) or "") for key in ("name", "itemId")).lower()
        if want and want in text:
            total += int(item.get("count") or 0)
    return total


def _dump_items(dump: Mapping[str, Any]) -> list[dict]:
    rows: list[dict] = []
    for item in dump.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("kind") or "")
        if kind not in ("sale", "component", "field", "invslot", "held"):
            continue
        rows.append(
            {
                "kind": kind,
                "index": item.get("index"),
                "name": item.get("name"),
                "label": item.get("label"),
                "price": item.get("price"),
                "x": item.get("x"),
                "y": item.get("y"),
                "w": item.get("w"),
                "h": item.get("h"),
            }
        )
    return rows


def _short_items(rows: Sequence[Mapping[str, Any]], limit: int = 12) -> list[dict]:
    out = []
    for row in rows[:limit]:
        out.append({key: row.get(key) for key in ("kind", "index", "name", "label", "price", "x", "y") if row.get(key) is not None})
    return out


def _shop_premises(observation: Observation) -> list[PremiseCheck]:
    menu = observation.menu or {}
    menu_type = _menu_type(observation)
    is_shop = "shop" in menu_type.lower()
    return [
        PremiseCheck("game", bool(menu), "当前有打开的菜单" if menu else "当前没有菜单：先走到商人/柜台并 interact"),
        PremiseCheck(
            "game",
            (not menu) or is_shop,
            ("当前菜单是商店（%s）" % menu_type) if is_shop else "当前菜单不是商店（%s）" % (menu_type or "未知"),
        ),
    ]



class InspectMenuDelegation(DelegationBase):
    """读一次菜单：商品/组件与坐标，供模型决定买什么、点哪里。"""

    def __init__(self, port: ShopPort, *, clock: Callable[[], float]) -> None:
        super().__init__("inspect_menu", {}, clock=clock)
        self.port = port

    async def step(self, observation: Observation) -> Progress:
        if self.cancelled:
            return self.cancelled_progress()
        if self.steps:
            return self.finish("completed", self.evidence.get("summary", "菜单已读取"), **self.evidence)
        self.steps += 1
        try:
            dump = await self.port.menudump()
        except AgentError as exc:
            return self.finish("unknown", "读取菜单失败：%s" % exc, error=getattr(exc, "code", ""))
        rows = _dump_items(dump)
        sales = [row for row in rows if row.get("kind") == "sale"]
        summary = "；".join(
            "%s %s%s" % (row.get("index"), row.get("name"), (" %sg" % row.get("price")) if row.get("price") else "")
            for row in sales[:8]
        )
        self.record("menu_type", str(dump.get("type") or _menu_type(observation)))
        self.record("money", dump.get("money"))
        self.record("items", _short_items(rows))
        self.record("sales", sales[:12])
        self.record("summary", ("可买：" + summary) if summary else "菜单里没有可购买商品")
        return Progress("running", self.evidence.get("summary", "菜单已读取"), dict(self.evidence))


class BuyItemDelegation(DelegationBase):
    """买一件商品：menudump 找序号 → shopbuy → 前后观察核对钱与背包。"""

    def __init__(
        self,
        port: ShopPort,
        params: Mapping[str, Any],
        observation: Observation,
        *,
        clock: Callable[[], float],
    ) -> None:
        super().__init__("buy_item", params, clock=clock)
        self.port = port
        self.item = str(params["item"])
        self.qty = int(params["qty"])
        self.max_price = int(params["max_price"]) if params.get("max_price") is not None else None
        self.before_money = _money(observation)
        self.before_count = _count_item(observation, self.item)
        self.index: int | None = None
        self.price: int | None = None
        self.cost: int | None = None
        self.record("item", self.item)
        self.record("qty", self.qty)
        self.record("before_money", self.before_money)
        self.record("before_count", self.before_count)

    async def step(self, observation: Observation) -> Progress:
        if self.cancelled:
            return self.cancelled_progress()
        if not observation.menu:
            return self.finish("failed", "商店菜单已关闭，购买未发生")
        if self.steps == 0:
            self.steps += 1
            try:
                dump = await self.port.menudump()
            except AgentError as exc:
                return self.finish("unknown", "读取商店失败：%s" % exc)
            sales = [row for row in _dump_items(dump) if row.get("kind") == "sale"]
            matches = [row for row in sales if self.item.strip().lower() in str(row.get("name") or "").lower()]
            if not matches:
                names = [str(row.get("name")) for row in sales[:10]]
                return self.finish("failed", "商店里没有匹配 %s 的商品（可买：%s）" % (self.item, "、".join(names) or "无"))
            chosen = matches[0]
            price = chosen.get("price")
            self.index = int(chosen.get("index") or 0)
            self.price = int(price) if isinstance(price, (int, float)) else None
            self.record("shop_item", chosen.get("name"))
            self.record("index", self.index)
            self.record("price", self.price)
            if self.max_price is not None and self.price is not None and self.price > self.max_price:
                return self.finish("failed", "单价 %s 超过上限 %s" % (self.price, self.max_price))
            self.cost = (self.price or 0) * self.qty if self.price is not None else None
            if self.cost is not None and self.before_money is not None and self.cost > self.before_money:
                return self.finish("failed", "钱不够：需要 %s，当前 %s" % (self.cost, self.before_money))
            return Progress("running", "准备购买 %s ×%d" % (self.item, self.qty), dict(self.evidence))

        if self.steps == 1:
            self.steps += 1
            try:
                response = await self.port.shopbuy(self.index or 0, self.qty, action_id=self.action_id)
            except AgentError as exc:
                return self.finish("unknown", "购买请求失败：%s（先重新观察背包与金钱，不要直接重发）" % exc)
            self.record("response", dict(response))
            if response.get("ok") is False:
                return self.finish("failed", "购买被拒绝：%s" % (response.get("reason") or "未知原因"))
            return Progress("running", "已下单，等待观察确认", dict(self.evidence))

        self.steps += 1
        after_money = _money(observation)
        after_count = _count_item(observation, self.item)
        self.record("after_money", after_money)
        self.record("after_count", after_count)
        spent = None
        if self.before_money is not None and after_money is not None:
            spent = self.before_money - after_money
        gained = None
        if self.before_count is not None and after_count is not None:
            gained = after_count - self.before_count
        evidence = {
            "spent": spent,
            "gained": gained,
            "expected_cost": self.cost,
            "response": self.evidence.get("response"),
        }
        if spent is not None and spent > 0 and (gained is None or gained > 0):
            return self.finish("completed", "购买完成：花费 %s，获得 %s 个 %s" % (spent, gained, self.item), **evidence)
        if gained is not None and gained > 0:
            return self.finish("completed", "背包里 %s 增加 %s 个（金钱变化未知）" % (self.item, gained), **evidence)
        if spent == 0:
            return self.finish("failed", "钱没有减少，购买未发生", **evidence)
        return self.finish("unknown", "已购买但当前观察无法确认钱与背包变化", **evidence)


class MenuClickDelegation(DelegationBase):
    """点一下菜单：普通菜单用 menuclick，背包区域用 menuclickinv。"""

    def __init__(
        self,
        port: MenuPort,
        params: Mapping[str, Any],
        observation: Observation,
        *,
        clock: Callable[[], float],
    ) -> None:
        super().__init__("menu_click", params, clock=clock)
        self.port = port
        self.x = int(params["x"])
        self.y = int(params["y"])
        self.inventory = bool(params.get("inventory"))
        self.before_signature = _menu_signature(observation)
        self.before_money = _money(observation)

    async def step(self, observation: Observation) -> Progress:
        if self.cancelled:
            return self.cancelled_progress()
        if self.steps == 0:
            self.steps += 1
            try:
                if self.inventory:
                    response = await self.port.menuclickinv(self.x, self.y, action_id=self.action_id)
                else:
                    response = await self.port.menuclick(self.x, self.y, action_id=self.action_id)
            except AgentError as exc:
                return self.finish("unknown", "点击失败：%s（先重新观察菜单）" % exc)
            self.record("response", dict(response))
            return Progress("running", "已点击 (%d, %d)，等待观察菜单变化" % (self.x, self.y), dict(self.evidence))

        self.steps += 1
        if observation.menu is None:
            return self.finish("completed", "菜单已关闭", menu_closed=True)
        if _menu_signature(observation) != self.before_signature:
            return self.finish("completed", "菜单内容发生变化", menu_closed=False)
        if _money(observation) != self.before_money:
            return self.finish("completed", "金钱发生变化", money_changed=True)
        return self.finish("unknown", "点击已下发，但菜单与金钱都没有可验证变化")


class CloseMenuDelegation(DelegationBase):
    """关闭当前菜单。"""

    def __init__(self, port: MenuPort, *, clock: Callable[[], float]) -> None:
        super().__init__("close_menu", {}, clock=clock)
        self.port = port

    async def step(self, observation: Observation) -> Progress:
        if self.cancelled:
            return self.cancelled_progress()
        if self.steps == 0:
            self.steps += 1
            try:
                response = await self.port.menuclose(action_id=self.action_id)
            except AgentError as exc:
                return self.finish("unknown", "关闭菜单失败：%s" % exc)
            self.record("response", dict(response))
            return Progress("running", "已请求关闭菜单", dict(self.evidence))
        self.steps += 1
        if observation.menu is None:
            return self.finish("completed", "菜单已关闭")
        return self.finish("unknown", "已请求关闭，但观察里仍有菜单")



def inspect_menu_ability(port: ShopPort, *, clock: Callable[[], float] = time.monotonic) -> AbilitySpec:
    descriptor = AbilityDescriptor(
        id="inspect_menu",
        version="1.0.0",
        summary="读取当前菜单：商店商品（序号/名称/价格）与可点组件坐标",
        params={},
        observation_needs=("menu",),
        scope="只读；不购买、不点击，不关闭菜单",
        completion="返回本次菜单内容；菜单已关闭时报告 unknown",
    )

    def validate(params: Mapping[str, Any]) -> Mapping[str, Any]:
        reject_unknown_params(params, ())
        return {}

    def premises(params: Mapping[str, Any], observation: Observation) -> list[PremiseCheck]:
        return [PremiseCheck("game", bool(observation.menu), "当前有打开的菜单" if observation.menu else "当前没有菜单")]

    def start(params: Mapping[str, Any], observation: Observation, action_id: str) -> InspectMenuDelegation:
        delegation = InspectMenuDelegation(port, clock=clock)
        delegation.action_id = action_id or delegation.action_id
        return delegation

    return AbilitySpec(descriptor=descriptor, validate=validate, check_premises=premises, start=start)


def buy_item_ability(port: ShopPort, *, clock: Callable[[], float] = time.monotonic) -> AbilitySpec:
    descriptor = AbilityDescriptor(
        id="buy_item",
        version="1.0.0",
        summary="在已打开的商店里按名称购买商品（价格与库存来自游戏 menudump）",
        params={
            "item": "商品名称（模糊匹配，例如 防风草）",
            "qty": "购买数量（1～50，默认 1）",
            "max_price": "可选：可接受的单价上限（避免被涨价）",
        },
        observation_needs=("menu", "inventory", "player.money"),
        scope="只买指定商品；不选种子、不决定种什么；钱不够或没有该商品时直接失败",
        completion="金钱减少且背包出现该商品；拿不到证据时报告 unknown",
        limits=("qty<=50", "一次委托只下一次购买"),
    )

    def validate(params: Mapping[str, Any]) -> Mapping[str, Any]:
        reject_unknown_params(params, ("item", "qty", "max_price"))
        out: dict[str, Any] = {
            "item": require_str(params, "item"),
            "qty": require_int({"qty": params.get("qty", 1)}, "qty", low=1, high=50),
        }
        if params.get("max_price") is not None:
            out["max_price"] = require_int(params, "max_price", low=1, high=1000000)
        return out

    def premises(params: Mapping[str, Any], observation: Observation) -> list[PremiseCheck]:
        return _shop_premises(observation)

    def start(params: Mapping[str, Any], observation: Observation, action_id: str) -> BuyItemDelegation:
        delegation = BuyItemDelegation(port, params, observation, clock=clock)
        delegation.action_id = action_id or delegation.action_id
        return delegation

    return AbilitySpec(descriptor=descriptor, validate=validate, check_premises=premises, start=start)


def menu_click_ability(port: MenuPort, *, clock: Callable[[], float] = time.monotonic) -> AbilitySpec:
    descriptor = AbilityDescriptor(
        id="menu_click",
        version="1.0.0",
        summary="在当前菜单里按坐标点击（普通菜单或背包区域）",
        params={
            "x": "屏幕坐标 x（来自 inspect_menu 的 items）",
            "y": "屏幕坐标 y",
            "inventory": "true 表示点击菜单里的背包区域（menuclickinv）",
        },
        observation_needs=("menu", "inventory", "player.money"),
        scope="只点击给定坐标；不猜测界面布局",
        completion="菜单关闭、菜单内容变化或金钱变化；否则报告 unknown",
        limits=("一次委托只点击一次",),
    )

    def validate(params: Mapping[str, Any]) -> Mapping[str, Any]:
        reject_unknown_params(params, ("x", "y", "inventory"))
        if "inventory" in params and not isinstance(params["inventory"], bool):
            raise Rejected("参数 inventory 必须是 true 或 false")
        out: dict[str, Any] = {
            "x": require_int(params, "x", low=0, high=10000),
            "y": require_int(params, "y", low=0, high=10000),
        }
        if "inventory" in params:
            out["inventory"] = bool(params["inventory"])
        return out

    def premises(params: Mapping[str, Any], observation: Observation) -> list[PremiseCheck]:
        return [PremiseCheck("game", bool(observation.menu), "当前有打开的菜单" if observation.menu else "当前没有菜单")]

    def start(params: Mapping[str, Any], observation: Observation, action_id: str) -> MenuClickDelegation:
        delegation = MenuClickDelegation(port, params, observation, clock=clock)
        delegation.action_id = action_id or delegation.action_id
        return delegation

    return AbilitySpec(descriptor=descriptor, validate=validate, check_premises=premises, start=start)


def close_menu_ability(port: MenuPort, *, clock: Callable[[], float] = time.monotonic) -> AbilitySpec:
    descriptor = AbilityDescriptor(
        id="close_menu",
        version="1.0.0",
        summary="关闭当前菜单（商店、对话、背包等）",
        params={},
        observation_needs=("menu",),
        scope="只关闭当前菜单；不点击任何选项",
        completion="重新观察确认菜单消失",
    )

    def validate(params: Mapping[str, Any]) -> Mapping[str, Any]:
        reject_unknown_params(params, ())
        return {}

    def premises(params: Mapping[str, Any], observation: Observation) -> list[PremiseCheck]:
        return [PremiseCheck("game", bool(observation.menu), "当前有打开的菜单" if observation.menu else "当前没有菜单")]

    def start(params: Mapping[str, Any], observation: Observation, action_id: str) -> CloseMenuDelegation:
        delegation = CloseMenuDelegation(port, clock=clock)
        delegation.action_id = action_id or delegation.action_id
        return delegation

    return AbilitySpec(descriptor=descriptor, validate=validate, check_premises=premises, start=start)

