"""往农场南边走，找去公交站的出口。"""
import asyncio
import os
import sys

DEV = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DEV)
from farm_client import Bridge  # noqa: E402
from farmer import Farmer, ents  # noqa: E402


async def main():
    async with Bridge() as b:
        f = Farmer(b, state_path=os.path.join(DEV, "farm-state.json"))
        s = await f.snap(16)
        print("start", s.get("location"), (s.get("player") or {}).get("position"), flush=True)
        for target in [(60, 40), (55, 55), (45, 62)]:
            s = await f.clear_toward(s, target[0], target[1], actions=8)
            s = await f.walk_to(s, target[0], target[1], tries=25)
            pos = (s.get("player") or {}).get("position")
            print("到", target, "->", pos, s.get("location"), flush=True)
            if s.get("location") != "Farm":
                break
        exits = [(e.get("name"), e.get("tile")) for e in ents(s) if e.get("kind") in ("exit", "door")]
        print("附近的出口/门:", exits, flush=True)
        print("pos", (s.get("player") or {}).get("position"), "time", s.get("time"), flush=True)


asyncio.run(main())
