"""去 Pierre 店买防风草种子：全流程 + menuclick 购买。"""
import asyncio
import json
import os
import sys

DEV = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DEV)
from farm_client import Bridge  # noqa: E402
from farmer import Farmer, ents  # noqa: E402


def log(*a):
    print(*a, flush=True)


def seeds(snap):
    return [(i.get("name"), i.get("count")) for i in snap.get("inventory") or [] if "种子" in (i.get("name") or "")]


async def main():
    async with Bridge() as b:
        f = Farmer(b, state_path=DEV + r"\farm-state.json")
        s = await f.snap(16)
        log("起点", s.get("location"), (s.get("player") or {}).get("position"), seeds(s))

        if s.get("location") == "FarmHouse":
            s = await f.go_outside(s)
        if s.get("location") == "Farm":
            for wp in [(60, 40), (55, 55), (45, 61)]:
                s = await f.clear_toward(s, wp[0], wp[1], actions=8)
                s = await f.walk_to(s, wp[0], wp[1], tries=25)
            try:
                log("出农场:", (await b.move(41, 65)).get("detail"))
            except Exception as e:
                log("出农场失败", e)
            await asyncio.sleep(2.5)
            s = await b.snapshot(16)

        if s.get("location") == "Forest":
            s = await f.walk_to(s, 115, 24, tries=30)
            try:
                log("进镇:", (await b.move(120, 25)).get("detail"))
            except Exception as e:
                log("进镇失败", e)
            await asyncio.sleep(2.5)
            s = await b.snapshot(16)

        if s.get("location") != "Town":
            log("没到镇上，停在", s.get("location"))
            return

        s = await f.walk_to(s, 46, 52, tries=25)
        s = await f.walk_to(s, 43, 57, tries=12)
        log("店门口", (s.get("player") or {}).get("position"), "时间", s.get("time"))

        for _ in range(60):
            s = await b.snapshot(8)
            if int(s.get("time") or 0) >= 900:
                break
            await asyncio.sleep(2.0)
        log("等到", s.get("time"))

        door = next((e for e in ents(s) if "SeedShop" in (e.get("action") or "")), None)
        if door:
            try:
                log("进门:", (await b.interact(door["ref"])).get("detail"))
            except Exception as e:
                log("进门失败", e)
                return
            await asyncio.sleep(2.5)
            s = await b.snapshot(14)
        if s.get("location") != "SeedShop":
            log("没进店", s.get("location"))
            return

        s = await f.walk_to(s, 4, 19, tries=12)
        try:
            await b.move(4, 19)
        except Exception as e:
            log("挪柜台", e)
        await asyncio.sleep(0.8)
        s = await b.snapshot(12)
        counter = next((e for e in ents(s) if (e.get("action") or "") == "Buy General"), None)
        if not counter:
            log("找不到柜台")
            return
        try:
            log("开柜台:", (await b.interact(counter["ref"])).get("detail"))
        except Exception as e:
            log("开柜台失败", e)
            return
        await asyncio.sleep(1.2)

        d = await b.request("menudump", {})
        items = d.get("items") or []
        log("菜单", d.get("type"), "商品数", len(items))
        target = next((it for it in items if it.get("name") == "防风草种子"), None)
        if not target:
            log("没有防风草种子")
            return
        idx = int(target.get("index"))
        r = await b.request("shopbuy", {"index": idx, "qty": 10})
        log("购买结果:", r)
        log("防风草种子 序号", target.get("index"), "价格", target.get("price"))

        before = seeds(await b.snapshot(6))
        r = await b.request("menuclick", {"x": x, "y": y})
        log("点击一次:", r.get("clicked"))
        await asyncio.sleep(0.8)
        d2 = await b.request("menudump", {})
        held = [it for it in (d2.get("items") or []) if it.get("kind") == "held"]
        fields = [it for it in (d2.get("items") or []) if it.get("kind") == "field"]
        log("点了之后 held=", held, " fields=", len(fields))
        for it in fields[:14]:
            log("   field", it.get("name"), it.get("label"), it.get("x"), it.get("y"), it.get("w"), it.get("h"))
        slots = [it for it in (d2.get("items") or []) if it.get("kind") == "invslot"]
        log("背包格子数", len(slots))
        empties = [s2 for s2 in slots if s2.get("label") in (None, "", "0")]
        origin = next((it for it in (d2.get("items") or []) if it.get("kind") == "invorigin"), None)
        log("菜单原点", origin and (origin.get("x"), origin.get("y")))
        target_slot = slots[20] if len(slots) > 20 else (slots[0] if slots else None)
        if target_slot:
            ox = int((origin or {}).get("x") or 0)
            oy = int((origin or {}).get("y") or 0)
            sx = int(ox + target_slot["x"] + target_slot["w"] / 2)
            sy = int(oy + target_slot["y"] + target_slot["h"] / 2)
            log("点背包格子", (sx, sy), target_slot.get("name"))
            r2 = await b.request("menuclickinv", {"x": sx, "y": sy})
            log("放下:", r2.get("clicked"), r2.get("reason") or "")
            await asyncio.sleep(0.8)
            log("背包:", seeds(await b.snapshot(8)))
        r = await b.request("menuclose", {})
        log("关菜单", r.get("closed"))
        await asyncio.sleep(1.5)
        s = await b.snapshot(12)
        log("菜单关闭后", (s.get("menu") or {}).get("type"), "种子", seeds(s), "之前", before)


asyncio.run(main())
