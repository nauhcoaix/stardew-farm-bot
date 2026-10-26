"""去 Pierre 店买种子，再走回农场。"""
import asyncio
import json

FESTIVALS = {
    ("spring", 13), ("spring", 24),
    ("summer", 11), ("summer", 28),
    ("fall", 16), ("fall", 27),
    ("winter", 8), ("winter", 25),
}


SEED_NAME = "防风草种子"
SEED_ID = "472"
SHOP_OPEN, SHOP_CLOSE = 900, 2000


LOG_FILE = __file__.replace("shop.py", "farm-service.log")


def log(*a):
    msg = " ".join(str(x) for x in a)
    print(msg, flush=True)
    try:
        import time as _t
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (_t.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


async def seeds_in(b):
    s = await b.snapshot(8)
    return [(i.get("name"), i.get("count")) for i in s.get("inventory") or [] if "种子" in (i.get("name") or "")]


async def _goto_farm_corridor(b, f, snap):
    for wp in [(60, 40), (55, 55), (45, 61)]:
        snap = await f.clear_toward(snap, wp[0], wp[1], actions=8)
        snap = await f.walk_to(snap, wp[0], wp[1], tries=25)
    await b.move(41, 65)
    await asyncio.sleep(2.5)
    return await b.snapshot(16)


async def _goto_town(b, f, snap):
    snap = await f.walk_to(snap, 115, 24, tries=30)
    await b.move(120, 25)
    await asyncio.sleep(2.5)
    return await b.snapshot(16)


async def _goto_shop(b, f, snap):
    snap = await f.walk_to(snap, 46, 52, tries=25)
    snap = await f.walk_to(snap, 43, 57, tries=12)
    door = next((e for e in (snap.get("entities") or []) if "SeedShop" in (e.get("action") or "")), None)
    if not door:
        return snap
    await b.interact(door["ref"])
    await asyncio.sleep(2.5)
    return await b.snapshot(14)


async def _buy(b, want):
    snap = await b.snapshot(12)
    counter = next((e for e in (snap.get("entities") or []) if (e.get("action") or "") == "Buy General"), None)
    if not counter:
        return None
    if not snap.get("menu"):
        await b.interact(counter["ref"])
        await asyncio.sleep(1.2)
    d = await b.request("menudump", {})
    sale = next((it for it in (d.get("items") or []) if it.get("name") == SEED_NAME), None)
    if not sale:
        return None
    idx = int(sale["index"])
    price = int(sale.get("price") or 20)
    money = None
    try:
        md = await b.request("menudump", {})
        money = md.get("money")
    except Exception:
        pass
    affordable = int((money or 0) // price) if money is not None else want
    qty = min(want, affordable)
    log("钱 %s，单价 %s，能买 %s 个，计划买 %s 个" % (money, price, affordable, qty))
    if qty < 1:
        log("钱不够，一个也买不了")
        return None
    r = await b.request("shopbuy", {"index": idx, "qty": qty})
    if r.get("ok"):
        log("买了 %s x%s，花 %s，剩钱 %s" % (r.get("name"), r.get("qty"), r.get("cost"), r.get("money")))
        return r
    log("买 %d 个失败：%s" % (qty, r.get("reason")))
    return None


async def _go_home(b, f, snap):
    if snap.get("location") == "SeedShop":
        snap = await f.walk_to(snap, 6, 29, tries=8)
        try:
            await b.move(6, 30)
        except Exception as e:
            log("出店失败", e)
        await asyncio.sleep(2.5)
        snap = await b.snapshot(16)
    if snap.get("location") == "Town":
        snap = await f.walk_to(snap, 2, 90, tries=25)
        try:
            await b.move(-1, 90)
        except Exception as e:
            log("回森林失败", e)
        await asyncio.sleep(2.5)
        snap = await b.snapshot(16)
    if snap.get("location") == "Forest":
        snap = await f.walk_to(snap, 68, 2, tries=25)
        try:
            await b.move(68, -1)
        except Exception as e:
            log("回农场失败", e)
        await asyncio.sleep(2.5)
        snap = await b.snapshot(16)
    return snap


async def buy_seeds(b, f, snap, want=20):
    """去店里买种子并回家；返回最后的快照。"""
    d = snap.get("date") or {}
    if (d.get("season"), d.get("day")) in FESTIVALS:
        log("今天是节日，镇上进不去，先不买")
        return snap
    if (d.get("season"), d.get("day")) in FESTIVALS:
        log("今天是节日（%s %s），镇上进不去，先不买" % (d.get("season"), d.get("day")))
        return snap
    loc = snap.get("location") or ""
    if loc not in ("Farm", "FarmHouse", "Forest", "Town", "SeedShop"):
        log("卡在 %s 这张地图，直接传送回农场" % loc)
        try:
            await b.request("warp", {"location": "Farm", "x": 64, "y": 15})
        except Exception as e:
            log("传送失败：%s" % e)
        await asyncio.sleep(2.0)
        return await b.snapshot(16)
    t = int(snap.get("time") or 0)
    if not (SHOP_OPEN <= t <= SHOP_CLOSE):
        log("现在 %s 点，店里没开，先不买" % t)
        return snap
    # 按钱选作物：钱多买土豆（回本更高），钱少买防风草
    global SEED_NAME, SEED_ID
    money0 = int((snap.get("player") or {}).get("money") or 0)
    if money0 >= 200:
        SEED_NAME, SEED_ID = "土豆种子", "475"
    else:
        SEED_NAME, SEED_ID = "防风草种子", "472"
    want = max(1, min(want, money0 // 50))
    log("买 %s x%d（现有 %dg）" % (SEED_NAME, want, money0))
    log("种子不够，去镇上买")
    if snap.get("location") == "FarmHouse":
        snap = await f.go_outside(snap)
    if snap.get("location") != "Farm":
        snap = await _go_home(b, f, snap)
    if snap.get("location") != "Farm":
        log("不在农场，去不了商店：%s" % snap.get("location"))
        return snap
    snap = await _goto_farm_corridor(b, f, snap)
    if snap.get("location") != "Forest":
        log("没进森林：%s" % snap.get("location"))
        return snap
    snap = await _goto_town(b, f, snap)
    if snap.get("location") != "Town":
        log("没进镇：%s" % snap.get("location"))
        return snap
    snap = await _goto_shop(b, f, snap)
    if snap.get("location") != "SeedShop":
        log("没进店：%s" % snap.get("location"))
        return snap
    snap = await f.walk_to(snap, 4, 19, tries=12)
    await _buy(b, want)
    await b.request("menuclose", {})
    await asyncio.sleep(1.0)
    snap = await _go_home(b, f, await b.snapshot(14))
    if (snap.get("location") or "") not in ("Farm", "FarmHouse", "Forest", "Town", "SeedShop"):
        log("回程也卡在 %s，传送回农场" % snap.get("location"))
        try:
            await b.request("warp", {"location": "Farm", "x": 64, "y": 15})
            await asyncio.sleep(2.0)
            snap = await b.snapshot(16)
        except Exception as e:
            log("传送失败：%s" % e)
    log("回程结束在", snap.get("location"), (snap.get("player") or {}).get("position"))
    return snap
