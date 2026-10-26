"""自动探索：朝地图各个方向走格子，发现出口/地图切换就记下来。

用法：
  python bot/explore.py --seconds 300

产出（合并进现有数据，不会覆盖别的）：
  $FARM_DATA/routes/exits-learned.json   地图 -> 目标 -> {leave, enter}
  $FARM_DATA/routes/explore-<时间>.log   过程记录

策略：
  - 每次读快照，记下"能看见的出口"（名字 -> 格子）
  - 朝一个方向按 2 格步进（东/西/南/北轮换），每步都读快照
  - 一旦地图变了：记录 离开格 / 进入格，然后在新地图继续探索
  - 走不动（PATH_BLOCKED/BUSY）就换方向
"""
import argparse
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import places  # noqa: E402
from farm_client import Bridge  # noqa: E402

DATA = os.environ.get("FARM_DATA") or BASE
OUT = os.path.join(DATA, "routes")
EXITS = os.path.join(OUT, "exits-learned.json")


def load_exits():
    try:
        with open(EXITS, encoding="utf-8-sig") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def save_exits(data):
    os.makedirs(OUT, exist_ok=True)
    tmp = EXITS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, EXITS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=300)
    ap.add_argument("--step", type=int, default=3)
    ap.add_argument("--max-steps", type=int, default=18)
    a = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    logp = os.path.join(OUT, "explore-%s.log" % time.strftime("%Y%m%d-%H%M%S"))
    exits = load_exits()

    def say(msg):
        line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
        print(line, flush=True)
        try:
            with open(logp, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    async def run():
        # 用同步 client 简化：外部 asyncio
        pass

    async def explore():
        from farm_client import Bridge as B
        import asyncio
        async with B() as b:
            snap = await b.snapshot(16)
            say("起点 %s" % snap.get("location"))
            dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]
            di = 0
            t_end = time.time() + a.seconds
            seen_exits = {}
            founded = 0
            while time.time() < t_end:
                loc = places.norm(snap.get("location"))
                pos = (snap.get("player") or {}).get("position") or {}
                x, y = pos.get("x"), pos.get("y")
                if x is None:
                    await asyncio.sleep(1)
                    snap = await b.snapshot(16)
                    continue
                # 记录当前地图能看见的出口
                cur_exits = places.exits_of(snap)
                for name, ent in cur_exits.items():
                    key = (loc, places.norm(name))
                    tile = ent.get("tile") or {}
                    t = [tile.get("x"), tile.get("y")]
                    if seen_exits.get(key) != t:
                        seen_exits[key] = t
                        say("发现出口 %-16s -> %-14s 在 %s" % (loc, key[1], t))
                        d = exits.setdefault(loc, {})
                        if key[1] not in d:
                            d[key[1]] = {"leave": t, "enter": [], "path": []}
                            founded += 1
                            save_exits(exits)

                dx, dy = dirs[di % 4]
                moved_ok = False
                for k in range(1, a.max_steps + 1):
                    tx, ty = x + dx * a.step * k, y + dy * a.step * k
                    if tx < 0 or ty < 0 or tx > 110 or ty > 120:
                        break
                    try:
                        r = await b.move(x=tx, y=ty)
                    except Exception as exc:  # noqa: BLE001
                        msg = str(exc)
                        if "BUSY" in msg:
                            await asyncio.sleep(1.5)
                            snap = await b.snapshot(16)
                            continue
                        break
                    snap = (r or {}).get("snapshot") or await b.snapshot(16)
                    new_loc = places.norm(snap.get("location"))
                    npos = (snap.get("player") or {}).get("position") or {}
                    nx, ny = npos.get("x"), npos.get("y")
                    if new_loc != loc:
                        # 记下这条连接
                        leave = [x + dx * a.step * (k - 1), y + dy * a.step * (k - 1)]
                        enter = [nx, ny]
                        exits.setdefault(loc, {})[new_loc] = {
                            "leave": leave, "enter": enter, "path": []}
                        exits.setdefault(new_loc, {}).setdefault(
                            loc, {"leave": enter, "enter": leave, "path": []})
                        save_exits(exits)
                        founded += 1
                        say("新连接 %-14s -> %-14s 离开%s 进入%s" % (loc, new_loc, leave, enter))
                        break
                    if (nx, ny) == (x, y):
                        break          # 没动，换方向
                    x, y = nx, ny
                    moved_ok = True
                di += 1
                if not moved_ok:
                    await asyncio.sleep(1)
                    snap = await b.snapshot(16)
            say("探索结束：记录 %d 条连接，共 %d 张地图" % (
                founded, len(exits)))
            save_exits(exits)

    import asyncio
    asyncio.run(explore())


if __name__ == "__main__":
    main()
