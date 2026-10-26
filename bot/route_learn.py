"""把录到的 trail 整理成：各地图出口坐标表（供 travel 复用）。

输入：$FARM_DATA/routes/trail-*.jsonl
输出：routes/exits-learned.json

要点：切图当帧的坐标还是旧地图的，所以进入点要往后找几个采样。
"""
import glob
import json
import os
import sys

DATA = os.environ.get("FARM_DATA") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(DATA, "routes")


def dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def main():
    trails = sorted(glob.glob(os.path.join(OUT, "trail-*.jsonl")))
    if not trails:
        print("没有 trail")
        return 1
    trail = trails[-1]
    rows = []
    with open(trail, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    print("读:", os.path.basename(trail), "采样", len(rows))

    def seg_points(start_idx, loc_name):
        """从 start_idx 开始、同一地图内连续的点（共线压缩）。"""
        pts = []
        for j in range(start_idx, len(rows)):
            r = rows[j]
            if r.get("loc") != loc_name:
                break
            t = (r["x"], r["y"])
            if not pts or pts[-1] != t:
                pts.append(t)
        return pts

    exits = {}
    for i in range(1, len(rows)):
        a, b = rows[i - 1], rows[i]
        if a.get("loc") == b.get("loc"):
            continue
        old, new = a["loc"], b["loc"]
        leave = (a["x"], a["y"])
        enter = None
        for j in range(i, min(i + 4, len(rows))):
            r = rows[j]
            if r.get("loc") != new:
                continue
            t = (r["x"], r["y"])
            # 第一帧常常还是旧地图坐标（离 leave 很近），跳过它
            if dist(t, leave) <= 3 and j == i:
                continue
            enter = t
            break
        if enter is None:
            enter = (b["x"], b["y"])
        # 旧地图上"朝出口走的那一截"（含拐点），供 travel 复用
        tail = []
        k = i - 1
        while k >= 0 and rows[k].get("loc") == old and len(tail) < 40:
            t = (rows[k]["x"], rows[k]["y"])
            if not tail or tail[-1] != t:
                tail.append(t)
            k -= 1
        tail.reverse()
        exits.setdefault(old, {})[new] = {"leave": list(leave), "enter": list(enter),
                                          "path": [list(p) for p in tail]}
        if old not in exits.setdefault(new, {}):
            exits[new][old] = {"leave": list(enter), "enter": list(leave)}

    exits_path = os.path.join(OUT, "exits-learned.json")
    with open(exits_path, "w", encoding="utf-8") as f:
        json.dump(exits, f, ensure_ascii=False, indent=1)

    print("\n=== 学到的出口 ===")
    for a in sorted(exits):
        for b, v in sorted(exits[a].items()):
            print("  %-18s -> %-18s 走到 %-12s 进去后 %-12s" % (
                a, b, v.get("leave"), v.get("enter")))
    print("\n写出:", exits_path, "（%d 张地图，%d 条连接）" % (
        len(exits), sum(len(v) for v in exits.values())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
