"""一路走到 Pierre 店，打开商店菜单，把菜单坐标 dump 出来。"""
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


async def main():
    async with Bridge() as b:
        f = Farmer(b, state_path=DEV + r"\farm-state.json")
        s = await f.snap(16)
        log("起点", s.get("location"), (s.get("player") or {}).get("position"))

        if s.get("location") == "FarmHouse":
            s = await f.go_outside(s)
            log("出门", s.get("location"), (s.get("player") or {}).get("position"))

        if s.get("location") == "Farm":
            for wp in [(60, 40), (55, 55), (45, 61)]:
                s = await f.clear_toward(s, wp[0], wp[1], actions=10)
                s = await f.walk_to(s, wp[0], wp[1], tries=25)
                log("农场路过", wp, (s.get("player") or {}).get("position"))
            try:
                log("出农场:", (await b.move(41, 65)).get("detail"))
            except Exception as e:
                log("出农场失败", e)
            await asyncio.sleep(2.5)
            s = await b.snapshot(16)

        if s.get("location") == "Forest":
            s = await f.walk_to(s, 115, 24, tries=30)
            log("森林东边", (s.get("player") or {}).get("position"))
            try:
                log("进镇:", (await b.move(120, 25)).get("detail"))
            except Exception as e:
                log("进镇失败", e)
            await asyncio.sleep(2.5)
            s = await b.snapshot(16)

        if s.get("location") == "Town":
            s = await f.walk_to(s, 46, 52, tries=25)
            log("镇上", (s.get("player") or {}).get("position"))
            s = await f.walk_to(s, 43, 57, tries=12)
            log("店门口", (s.get("player") or {}).get("position"))
            door = next((e for e in ents(s) if "SeedShop" in (e.get("action") or "")), None)
            if door:
                try:
                    log("进门:", (await b.interact(door["ref"])).get("detail"))
                except Exception as e:
                    log("进门失败", e)
                    # 站到门正下方再试
                    try:
                        await b.move(43, 57)
                        await asyncio.sleep(0.8)
                        log("再进:", (await b.interact(door["ref"])).get("detail"))
                    except Exception as e2:
                        log("再进失败", e2)
                await asyncio.sleep(2.5)
                s = await b.snapshot(14)

        if s.get("location") == "SeedShop":
            log("店里", (s.get("player") or {}).get("position"))
            s = await f.walk_to(s, 5, 19, tries=12)
            try:
                await b.move(4, 19)
            except Exception as e:
                log("挪到柜台失败", e)
            await asyncio.sleep(0.8)
            s = await b.snapshot(12)
            shop = next((e for e in ents(s) if (e.get("action") or "") == "Buy General"), None)
            if shop:
                try:
                    log("开柜台:", (await b.interact(shop["ref"])).get("detail"))
                except Exception as e:
                    log("开柜台失败", e)
                await asyncio.sleep(1.5)
            dump = await b.request("menudump", {})
            log("MENUDUMP " + json.dumps(dump, ensure_ascii=False))

        log("结束", s.get("location"), (s.get("player") or {}).get("position"))


asyncio.run(main())
