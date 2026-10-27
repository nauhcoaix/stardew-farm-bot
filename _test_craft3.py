import asyncio, sys, json
sys.path.insert(0, r"D:\code\stardew-farm-bot\bot")
from farm_client import Bridge

async def main():
    async with Bridge() as b:
        print("  craft(宝箱) ->", json.dumps(await b.craft("宝箱", 1), ensure_ascii=False)[:120])
        print("  craft(火把) ->", json.dumps(await b.craft("火把", 1), ensure_ascii=False)[:120])
asyncio.run(main())