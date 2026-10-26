"""路线录制：玩家手动走，脚本只读位置，记录走过的格子和地图切换点。

用法：
  python bot/route_recorder.py --seconds 600
  python bot/route_recorder.py --seconds 300 --interval 0.4

输出（默认写到 $FARM_DATA/routes/）：
  trail-<时间>.jsonl     原始采样（每行一个点）
  routes-<时间>.json     压缩后的分段路线（可被 travel 复用）

分段规则：
  - 同一张地图内连续走 => 一段
  - 地图变了 => 结束上一段，记下 离开格 / 进入格，开始新一段
  - 段内路径做"共线压缩"：只保留拐点和端点
"""
import argparse
import asyncio
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import places  # noqa: E402
from farm_client import Bridge  # noqa: E402

DATA = os.environ.get("FARM_DATA") or BASE
OUT_DIR = os.path.join(DATA, "routes")


def compress(points):
    """共线压缩：连续同方向的点只留首尾。"""
    if len(points) <= 2:
        return points
    out = [points[0]]
    for i in range(1, len(points) - 1):
        x0, y0 = points[i - 1]
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        d1 = (x1 - x0, y1 - y0)
        d2 = (x2 - x1, y2 - y1)
        if d1 != d2:
            out.append(points[i])
    out.append(points[-1])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=600)
    ap.add_argument("--interval", type=float, default=0.4)
    ap.add_argument("--radius", type=int, default=4)
    ap.add_argument("--out", default=OUT_DIR)
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    trail_path = os.path.join(a.out, "trail-%s.jsonl" % ts)
    routes_path = os.path.join(a.out, "routes-%s.json" % ts)

    async def run():
        segments = []
        cur = None          # 当前段
        last_loc = None
        last_tile = None
        t_end = time.time() + a.seconds
        samples = 0
        with open(trail_path, "w", encoding="utf-8") as trail:
            async with Bridge() as b:
                print("录制开始（%.0f 秒）。你在游戏里走，我记录。Ctrl+C 或到时间自动停。" % a.seconds,
                      flush=True)
                while time.time() < t_end:
                    try:
                        snap = await b.snapshot(a.radius)
                    except Exception as exc:  # noqa: BLE001
                        if "BUSY" not in str(exc):
                            print("  读快照失败：%s" % exc, flush=True)
                        await asyncio.sleep(a.interval)
                        continue
                    samples += 1
                    loc = places.norm(snap.get("location"))
                    pos = (snap.get("player") or {}).get("position") or {}
                    tile = (pos.get("x"), pos.get("y"))
                    if tile[0] is None:
                        await asyncio.sleep(a.interval)
                        continue
                    trail.write(json.dumps({"t": round(time.time(), 2), "loc": loc,
                                            "x": tile[0], "y": tile[1]}, ensure_ascii=False) + "\n")

                    if last_loc is None:
                        last_loc, last_tile = loc, tile
                        cur = {"from": loc, "to": None, "enter_tile": list(tile),
                               "leave_tile": None, "points": [list(tile)],
                               "started": time.time()}
                        print("  起点：%s (%d,%d)" % (loc, tile[0], tile[1]), flush=True)
                    elif loc != last_loc:
                        # 地图切换
                        cur["to"] = loc
                        cur["leave_tile"] = list(last_tile)
                        cur["ended"] = time.time()
                        cur["points"] = compress(cur["points"])
                        segments.append(cur)
                        print("  切换：%s → %s   离开(%d,%d) 进入(%d,%d)  该段 %d 个拐点" % (
                            cur["from"], loc, last_tile[0], last_tile[1], tile[0], tile[1],
                            len(cur["points"])), flush=True)
                        last_loc, last_tile = loc, tile
                        cur = {"from": loc, "to": None, "enter_tile": list(tile),
                               "leave_tile": None, "points": [list(tile)],
                               "started": time.time()}
                    else:
                        if tuple(cur["points"][-1]) != tile:
                            cur["points"].append(list(tile))
                        last_tile = tile
                    await asyncio.sleep(a.interval)

        if cur:
            cur["points"] = compress(cur["points"])
            cur.pop("to", None)
            cur["ended"] = time.time()
            segments.append(cur)

        data = {"recorded_at": ts, "samples": samples, "segments": segments}
        with open(routes_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)

        print("\n=== 汇总 ===", flush=True)
        print("采样 %d 次，%d 段" % (samples, len(segments)), flush=True)
        for s in segments:
            head = "%s%s" % (s["from"], (" → " + s["to"]) if s.get("to") else "（未离开）")
            print("  %-28s 拐点 %2d  从 (%s,%s) 到 (%s,%s)" % (
                head, len(s["points"]),
                s["points"][0][0], s["points"][0][1],
                s["points"][-1][0], s["points"][-1][1]), flush=True)
        print("原始: %s" % trail_path, flush=True)
        print("路线: %s" % routes_path, flush=True)

    asyncio.run(run())


if __name__ == "__main__":
    main()
