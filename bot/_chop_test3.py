import asyncio, sys
sys.path.insert(0, '.')
import farmer
from farm_client import Bridge

async def main():
    async with Bridge() as b:
        errs = []
        def log(m):
            s = str(m)
            if "失败" in s: errs.append(s)
            print("   " + s)
        f = farmer.Farmer(b, dry=False, log=log)
        snap = await f.snap(14)
        if (snap.get("location") or "") != "Farm":
            snap = await f.go_outside(snap)
        def wood(s):
            return sum(i.get("count", 0) for i in s.get("inventory") or [] if (i.get("name") or "") in ("木材","木头"))
        w0 = wood(snap)
        print("砍之前木头:", w0)
        snap = await f.chop_trees(snap, want_wood=w0+15, max_swings=24)
        w1 = wood(snap)
        print("\n结果：木头 %d -> %d (+%d)，挥斧 %d 次，报错 %d 条" % (w0, w1, w1-w0, f.last_chops, len(errs)))
        for e in errs[:5]: print("   !", e)
asyncio.run(main())