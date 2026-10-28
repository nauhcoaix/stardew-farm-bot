"""商店与菜单能力：读取、购买、点击、关闭（替身端口，不连游戏）。"""

from __future__ import annotations

import asyncio

import pytest

from stardew_agent.abilities import buy_item_ability, close_menu_ability, inspect_menu_ability, menu_click_ability
from stardew_agent.contracts.errors import Rejected
from tests.helpers import inventory, menu_row, observe


class FakeShopPort:
    def __init__(self, dump=None, *, buy_result=None) -> None:
        self.dump = dump or {"type": "ShopMenu", "money": 500, "items": []}
        self.buy_result = buy_result or {
            "ok": True,
            "name": "防风草",
            "qty": 2,
            "price": 100,
            "cost": 200,
            "money": 300,
        }
        self.calls: list[tuple] = []

    async def menudump(self):
        self.calls.append(("menudump",))
        return dict(self.dump)

    async def shopbuy(self, index, qty=1, **kwargs):
        self.calls.append(("shopbuy", index, qty, kwargs.get("action_id")))
        return dict(self.buy_result)


class FakeMenuPort:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def menudump(self):
        return {"type": "ShopMenu", "money": 500, "items": []}

    async def menuclick(self, x, y, **kwargs):
        self.calls.append(("menuclick", x, y, kwargs.get("action_id")))
        return {"kind": "menuclick", "clicked": True}

    async def menuclickinv(self, x, y, **kwargs):
        self.calls.append(("menuclickinv", x, y, kwargs.get("action_id")))
        return {"kind": "menuclickinv", "clicked": True}

    async def menuclose(self, **kwargs):
        self.calls.append(("menuclose", kwargs.get("action_id")))
        return {"kind": "menuclose", "closed": True}


SHOP_MENU = {"type": "ShopMenu", "identity": "shop-1", "text": "商店", "options": []}
SALES = [
    {"kind": "sale", "index": 0, "name": "防风草种子", "price": 20, "x": 10, "y": 20, "w": 64, "h": 64},
    {"kind": "sale", "index": 1, "name": "土豆种子", "price": 50, "x": 10, "y": 90, "w": 64, "h": 64},
]


def _run(delegation, observations):
    progress = None
    for row in observations:
        progress = asyncio.run(delegation.step(row))
        if progress.done:
            break
    return progress


def test_读取商店菜单会带出商品与价格():
    port = FakeShopPort({"type": "ShopMenu", "money": 500, "items": SALES})
    spec = inspect_menu_ability(port)
    observation = observe(menu=SHOP_MENU)
    delegation = spec.start(spec.validate({}), observation, "a1")

    progress = _run(delegation, [observation, observation])

    assert progress is not None and progress.state == "completed"
    assert "防风草种子" in progress.detail
    assert progress.evidence["sales"][0]["price"] == 20
    assert progress.evidence["items"]


def test_购买成功以金钱减少和背包增加为证据():
    port = FakeShopPort({"type": "ShopMenu", "money": 500, "items": SALES})
    spec = buy_item_ability(port)
    before = observe(menu=SHOP_MENU, money=500)
    params = spec.validate({"item": "防风草", "qty": 2})
    delegation = spec.start(params, before, "a2")

    after = observe(menu=SHOP_MENU, money=300, inventory_rows=[inventory(0, "防风草种子", count=2)])
    progress = _run(delegation, [before, before, after, after])

    assert progress is not None and progress.state == "completed"
    assert progress.evidence["spent"] == 200
    assert progress.evidence["gained"] == 2
    assert port.calls[1] == ("shopbuy", 0, 2, "a2")


def test_商店没有该商品时直接失败并列出可买项():
    port = FakeShopPort({"type": "ShopMenu", "money": 500, "items": SALES})
    spec = buy_item_ability(port)
    before = observe(menu=SHOP_MENU, money=500)
    delegation = spec.start(spec.validate({"item": "草莓"}), before, "a3")

    progress = _run(delegation, [before])

    assert progress is not None and progress.state == "failed"
    assert "没有匹配" in progress.detail
    assert "防风草种子" in progress.detail


def test_钱不够或超过单价上限时不下单():
    port = FakeShopPort({"type": "ShopMenu", "money": 100, "items": SALES})
    spec = buy_item_ability(port)
    before = observe(menu=SHOP_MENU, money=100)

    delegation = spec.start(spec.validate({"item": "土豆", "qty": 3}), before, "a4")
    progress = _run(delegation, [before])
    assert progress is not None and progress.state == "failed"
    assert "钱不够" in progress.detail
    assert port.calls == [("menudump",)]

    delegation = spec.start(spec.validate({"item": "土豆", "qty": 1, "max_price": 30}), before, "a5")
    progress = _run(delegation, [before])
    assert progress is not None and progress.state == "failed"
    assert "超过上限" in progress.detail


def test_购买前提要求商店菜单():
    spec = buy_item_ability(FakeShopPort())
    params = spec.validate({"item": "防风草"})

    no_menu = spec.check_premises(params, observe())
    assert any(check.met is False for check in no_menu)

    dialogue = spec.check_premises(params, observe(menu=menu_row(["@s1:m1"])))
    assert any(check.met is False and "不是商店" in check.detail for check in dialogue)


def test_点击菜单后菜单关闭即完成():
    port = FakeMenuPort()
    spec = menu_click_ability(port)
    before = observe(menu=SHOP_MENU)
    delegation = spec.start(spec.validate({"x": 10, "y": 20}), before, "a6")

    progress = _run(delegation, [before, observe(), observe()])

    assert progress is not None and progress.state == "completed"
    assert port.calls[0] == ("menuclick", 10, 20, "a6")
    assert progress.evidence["menu_closed"] is True


def test_关闭菜单以菜单消失为验证():
    port = FakeMenuPort()
    spec = close_menu_ability(port)
    before = observe(menu=SHOP_MENU)
    delegation = spec.start(spec.validate({}), before, "a7")

    progress = _run(delegation, [before, observe(), observe()])

    assert progress is not None and progress.state == "completed"
    assert port.calls[0] == ("menuclose", "a7")


def test_购买参数校验():
    spec = buy_item_ability(FakeShopPort())
    with pytest.raises(Rejected):
        spec.validate({"item": ""})
    with pytest.raises(Rejected):
        spec.validate({"item": "防风草", "qty": 0})
    with pytest.raises(Rejected):
        spec.validate({"item": "防风草", "unknown": 1})
    assert spec.validate({"item": "防风草"}) == {"item": "防风草", "qty": 1}

