"""农场布局摘要 + 清障候选（纯计算，不碰桥，方便单测）。

给模型看的东西：
  farm_digest(snap) -> {
      "counts": {"tillable":..,"tilled":..,"crops":..,"obstacles":..,"obstacles_in_cluster":..},
      "free_slots": 已开垦但空着的格子数（能直接种）,
      "regions": [{"zone":"B3","x":..,"y":..,"tilled":..,"crops":..,"obstacles":..,"water":..}],
      "ascii": "..." 十几行的字符地图,
      "where": "农舍东南约 12 格，开垦区边上"
  }

给执行层的：
  clear_candidates(snap, zone=..., need=...) -> [{i,tile,kind,name,ref,tool,why,cost,in_cluster}]
  zone: none 不清 / cluster 只清开垦区内外 1 格 / path 只清挡路的 / all 全农场
"""
import math
from collections import deque

# 实体名 -> 工具（和 farmer.CLEAR_TOOLS 保持一致）
OBSTACLE_TOOLS = {
    "杂草": "Scythe", "Grass": "Scythe", "草": "Scythe", "杂草丛": "Scythe",
    "嫩枝": "Axe", "树枝": "Axe", "木头": "Axe",
    "石头": "Pickaxe", "岩石": "Pickaxe",
}
# 清一格要多少体力（估算，用于排序和预算）
OBSTACLE_COST = {"Scythe": 0, "Axe": 2, "Pickaxe": 4}   # 镰刀不耗体力

TILLED_KINDS = ("耕地",)
CROP_KINDS = ("作物",)


def ents(snap):
    return snap.get("entities") or []


def _tile(e):
    t = e.get("tile") or {}
    return (t.get("x"), t.get("y"))


def _kindname(e):
    return ((e.get("kind") or ""), (e.get("name") or ""))


def _tiles_of(snap, kinds=(), names=()):
    out = set()
    for e in ents(snap):
        k, n = _kindname(e)
        if (kinds and k in kinds) or (names and n in names):
            if e.get("tile"):
                out.add(_tile(e))
    return out


def cluster_tiles(snap):
    """已开垦区（耕地/作物）外扩 1 格 —— 清障的默认作用域。"""
    base = _tiles_of(snap, TILLED_KINDS + CROP_KINDS)
    grown = set(base)
    for (x, y) in base:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
            grown.add((x + dx, y + dy))
    return grown, base


HOUSE_FALLBACK = (64, 15)     # 农舍门口（找不到 农舍入口 实体时用）
PLOT = {"x1": 60, "x2": 69, "y1": 15, "y2": 22}   # 和 farmer.PLOT 保持一致


def in_plot(tile):
    if not tile:
        return False
    return PLOT["x1"] <= tile[0] <= PLOT["x2"] and PLOT["y1"] <= tile[1] <= PLOT["y2"]
NEAR_WINDOW = 6               # 只放出"最近候选 + 6 格"以内的，防止跳远


def house_tile(snap):
    """农舍参照点：优先"农舍入口"实体，否则用固定坐标。"""
    for e in ents(snap):
        if (e.get("kind") or e.get("name")) == "农舍入口" and e.get("tile"):
            t = _tile(e)
            if t[0] is not None:
                return t
    return HOUSE_FALLBACK


def dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def obstacle_list(snap):
    """所有可清的障碍（不含树，树走 chop）。"""
    out = []
    for e in ents(snap):
        k, n = _kindname(e)
        nm = n or k
        if nm in OBSTACLE_TOOLS and e.get("tile") and e.get("ref"):
            out.append((nm, e))
    return out


def passable_grid(snap):
    grid = {}
    for e in ents(snap):
        if "passable" not in e:
            continue
        grid[_tile(e)] = bool(e.get("passable"))
    return grid


def bfs_prev(grid, start):
    prev = {start: None}
    q = deque([start])
    while q:
        cur = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nxt = (cur[0] + dx, cur[1] + dy)
            if nxt in prev or not grid.get(nxt):
                continue
            prev[nxt] = cur
            q.append(nxt)
    return prev


def path_tiles(snap, targets):
    """从角色到目标点（宝箱/水源/农舍入口/出口）的路径格子集合。"""
    pos = (snap.get("player") or {}).get("position") or {}
    start = (pos.get("x"), pos.get("y"))
    grid = passable_grid(snap)
    if start not in grid:
        return set()
    prev = bfs_prev(grid, start)
    out = set()
    for goal in targets:
        if goal in prev:
            c = goal
            while c is not None:
                out.add(c)
                c = prev[c]
        else:
            # 目标不可达：取离目标最近的可达格子连过去（近似）
            best = None
            for p in prev:
                d = abs(p[0] - goal[0]) + abs(p[1] - goal[1])
                if best is None or d < best[0]:
                    best = (d, p)
            if best and best[0] <= 3:
                c = best[1]
                while c is not None:
                    out.add(c)
                    c = prev[c]
    return out


def _dir_words(dx, dy):
    hor = "东" if dx > 0 else ("西" if dx < 0 else "")
    ver = "南" if dy > 0 else ("北" if dy < 0 else "")
    return (ver + hor) or "原地"


def farm_digest(snap, zone=8):
    """把农场压成模型能读的摘要。zone 是分区边长（格）。"""
    pos = (snap.get("player") or {}).get("position") or {}
    px, py = pos.get("x"), pos.get("y")
    tilled = _tiles_of(snap, TILLED_KINDS)
    crops = _tiles_of(snap, CROP_KINDS)
    tillable = _tiles_of(snap, ("可耕地",))
    obs = obstacle_list(snap)
    obs_tiles = {_tile(e) for _n, e in obs}
    water_tiles = _tiles_of(snap, ("水源",))
    chest_tiles = _tiles_of(snap, ("宝箱",))
    door_tiles = _tiles_of(snap, ("农舍入口",))
    grown, base = cluster_tiles(snap)
    cluster_core = base
    free_slots = len(tilled - crops)

    # 分区统计
    regions = {}
    def bump(pt, key):
        zx, zy = int(pt[0] // zone), int(pt[1] // zone)
        r = regions.setdefault((zx, zy), {"zone": "%s%d" % (chr(65 + zx % 26), zy + 1),
                                          "x": zx * zone, "y": zy * zone,
                                          "tilled": 0, "crops": 0, "obstacles": 0, "water": 0})
        r[key] += 1

    for t in tilled:
        bump(t, "tilled")
    for t in crops:
        bump(t, "crops")
    for t in obs_tiles:
        bump(t, "obstacles")
    for t in water_tiles:
        bump(t, "water")
    region_list = sorted(regions.values(), key=lambda r: (-r["crops"], -r["tilled"], r["zone"]))[:8]

    # ASCII 地图：只看玩家周边（半径 14，2 格一个字符）
    ascii_map = ""
    if px is not None:
        R, STEP = 14, 2
        x0, x1 = px - R, px + R
        y0, y1 = py - R, py + R

        def block_has(pts, bx, by):
            for dx in range(STEP):
                for dy in range(STEP):
                    if (bx + dx, by + dy) in pts:
                        return True
            return False

        rows = []
        for by in range(y0, y1 + 1, STEP):
            line = []
            for bx in range(x0, x1 + 1, STEP):
                if bx <= px < bx + STEP and by <= py < by + STEP:
                    ch = "@"
                elif block_has(crops, bx, by):
                    ch = "c"
                elif block_has(tilled, bx, by):
                    ch = "t"
                elif block_has(tillable, bx, by):
                    ch = "."
                elif block_has(obs_tiles, bx, by):
                    ch = "#"
                elif block_has(water_tiles, bx, by):
                    ch = "w"
                elif block_has(chest_tiles, bx, by):
                    ch = "b"
                elif block_has(door_tiles, bx, by):
                    ch = "H"
                else:
                    ch = " "
                line.append(ch)
            rows.append("".join(line).rstrip())
        ascii_map = "\n".join(rows)

    # 方位：相对农舍入口
    where = "农场里"
    doors = list(door_tiles)
    if doors and px is not None:
        dx0, dy0 = doors[0]
        dx, dy = px - dx0, py - dy0
        dist = abs(dx) + abs(dy)
        where = "农舍%s约 %d 格" % (_dir_words(dx, dy), dist)
        if (px, py) in cluster_core:
            where += "，就在开垦区里"
        elif (px, py) in grown:
            where += "，开垦区边上"
        else:
            where += "，不在开垦区"

    return {
        "counts": {
            "tillable": len(tillable),
            "tilled": len(tilled),
            "crops": len(crops),
            "obstacles": len(obs_tiles),
            "obstacles_in_cluster": len(obs_tiles & grown),
        },
        "free_slots": free_slots,
        "regions": region_list,
        "ascii": ascii_map,
        "where": where,
        "player": {"x": px, "y": py},
    }


def clear_candidates(snap, zone="cluster", need=0, max_n=12, max_cost=None):
    """按"该不该清"排出候选。

    zone:
      none     不清
      cluster  只清开垦区内部 + 外扩 1 格（默认）
      path     只清挡在去 宝箱/水源/农舍/出口 路上的
      all      全农场（相当于老行为）
    need: 还缺几格耕地（>0 才有理由清）
    """
    zone = (zone or "cluster").lower()
    if zone == "none" or need <= 0:
        return []

    grown, base = cluster_tiles(snap)
    obs = obstacle_list(snap)

    # 目标点：宝箱/水源/农舍入口/出口
    targets = []
    for kinds in (("宝箱",), ("水源",), ("农舍入口",), ("出口",)):
        targets.extend(list(_tiles_of(snap, kinds))[:2])
    paths = path_tiles(snap, targets) if zone == "path" else set()

    pos = (snap.get("player") or {}).get("position") or {}
    px, py = pos.get("x", 0), pos.get("y", 0)
    house = house_tile(snap)

    out = []
    for nm, e in obs:
        t = _tile(e)
        in_cluster = t in grown
        on_path = t in paths
        if zone == "cluster":
            if in_cluster:
                why, prio = "在开垦区内，清完能种", 1
            elif not base and in_plot(t):
                # 农场还没开垦：只允许在固定田块里开荒（仍按离农舍远近排）
                why, prio = "开荒：田块内", 2
                in_cluster = True      # 算"允许区域"，统计里不显示成区外
            else:
                continue
        elif zone == "path":
            if not on_path:
                continue
            why, prio = "挡在去目标点的路上", 2
        else:  # all
            why = "在开垦区内，清完能种" if in_cluster else ("挡路上" if on_path else "开荒（区外没用）")
            prio = 1 if in_cluster else (2 if on_path else 3)
        tool = OBSTACLE_TOOLS.get(nm, "Axe")
        cost = OBSTACLE_COST.get(tool, 2)
        d = abs(t[0] - px) + abs(t[1] - py)
        out.append({
            "tile": {"x": t[0], "y": t[1]},
            # kind/name 必须保留快照里的原值：pantent 的 work_target 按 kind+name+坐标
            # 重新绑定目标，改写成显示名会导致"找不到目标"而静默跳过
            "kind": e.get("kind") or nm,
            "name": e.get("name") or nm,
            "ref": e.get("ref"),
            "tool": tool,
            "why": why,
            "cost": cost,
            "in_cluster": in_cluster,
            "d_house": dist(t, house),
            "_sort": (dist(t, house), prio, d),
        })
    # 先清离农舍近的：按到农舍的距离升序，其次才是价值等级、离自己的远近
    out.sort(key=lambda c: c["_sort"])
    if out and zone != "all":
        nearest = out[0]["d_house"]
        out = [c for c in out if c["d_house"] <= nearest + NEAR_WINDOW]
    elif out and zone == "all":
        nearest = out[0]["d_house"]
        out = [c for c in out if c["d_house"] <= nearest + NEAR_WINDOW]
    if max_cost is not None:
        picked, used = [], 0
        for c in out:
            if used + c["cost"] > max_cost:
                continue
            picked.append(c)
            used += c["cost"]
            if len(picked) >= max_n:
                break
        out = picked
    else:
        out = out[:max_n]
    for i, c in enumerate(out, 1):
        c["i"] = i
        c.pop("_sort", None)
    return out


def describe_digest(d, limit_regions=6):
    """把 digest 拍成一段文本，塞进 planner 的输入。"""
    c = d.get("counts") or {}
    lines = [
        "你在：%s（坐标 %s,%s）" % (d.get("where"), (d.get("player") or {}).get("x"),
                                   (d.get("player") or {}).get("y")),
        "农场统计：可耕地 %d 格，已翻地 %d 格，其中空着能直接种 %d 格，作物 %d 格，障碍 %d 个（开垦区内 %d 个）" % (
            c.get("tillable", 0), c.get("tilled", 0), d.get("free_slots", 0),
            c.get("crops", 0), c.get("obstacles", 0), c.get("obstacles_in_cluster", 0)),
    ]
    regs = d.get("regions") or []
    if regs:
        lines.append("分区：" + "；".join(
            "%s(地%d/苗%d/障%d)" % (r["zone"], r["tilled"], r["crops"], r["obstacles"])
            for r in regs[:limit_regions]))
    if d.get("ascii"):
        lines.append("地图（@你 c作物 t空地 .可耕地 #障碍 w水 b箱子 H农舍）：")
        lines.append(d["ascii"])
    return "\n".join(lines)
