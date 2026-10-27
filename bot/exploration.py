"""根据观察和记忆生成探索候选、规划路径；目的地由模型选择。"""
from collections import deque
from memory_store import position, semantic, target_id


def grid(snap, memory):
    cells = {}
    for e in memory.tiles(snap=snap):
        t = e.get("tile") or {}
        if "passable" in e:
            cells[(t.get("x"), t.get("y"))] = bool(e["passable"])
    for e in snap.get("entities") or []:
        t = e.get("tile") or {}
        if "passable" in e:
            cells[(t.get("x"), t.get("y"))] = bool(e["passable"])
    p = position(snap)
    cells[(p["x"], p["y"])] = True
    return cells


def reachable(cells, start):
    prior, queue = {start: None}, deque([start])
    while queue and len(prior) <= 20000:
        current = queue.popleft()
        for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
            nxt = current[0] + dx, current[1] + dy
            if nxt in prior or not cells.get(nxt):
                continue
            prior[nxt] = current
            queue.append(nxt)
    return prior


def path_to(snap, memory, goal):
    p = position(snap)
    start = (p["x"], p["y"])
    prior = reachable(grid(snap, memory), start)
    if goal not in prior:
        return None
    path = []
    while goal != start:
        path.append(goal)
        goal = prior[goal]
    return list(reversed(path))


def candidates(snap, memory, limit=16):
    cells = grid(snap, memory)
    p = position(snap)
    connected = reachable(cells, (p["x"], p["y"]))
    scope = snap.get("observation") or {}
    width, height = scope.get("width"), scope.get("height")
    visits = {(e["tile"]["x"], e["tile"]["y"]) for e in memory.tiles(snap=snap) if e.get("visited")}
    points = []
    for (x, y), passable in cells.items():
        if not passable or x is None or y is None or (x, y) == (p["x"], p["y"]):
            continue
        unknown = sum((x + dx, y + dy) not in cells and (width is None or 0 <= x + dx < width)
                      and (height is None or 0 <= y + dy < height)
                      for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)))
        if unknown and (x, y) in connected:
            points.append({"id": "frontier:%s:%s" % (x, y), "mode": "path", "x": x, "y": y,
                           "unknown_neighbors": unknown, "visited": (x, y) in visits,
                           "distance": abs(x - p["x"]) + abs(y - p["y"])})
    points.sort(key=lambda c: (c["visited"], -c["unknown_neighbors"], c["distance"]))
    # 同一片边缘只给少量分散候选，减少上下文；不是固定探索路线。
    selected = []
    for point in points:
        if all(abs(point["x"] - x["x"]) + abs(point["y"] - x["y"]) >= 4 for x in selected):
            selected.append(point)
        if len(selected) >= limit:
            break
    for e in snap.get("entities") or []:
        if e.get("kind") in ("npc", "object", "action", "door", "exit", "bed", "furniture"):
            selected.append({"id": target_id(snap.get("location"), e), "mode": "interaction", "target": semantic(e)})
    return selected[:limit * 2]


def region_route(memory, snap, destination):
    origin = snap.get("location")
    links = memory.links(snap)
    prior, queue = {origin: None}, deque([origin])
    while queue:
        current = queue.popleft()
        if current == destination:
            result = []
            while prior[current] is not None:
                edge = prior[current]
                result.append(edge)
                current = edge["from"]
            return list(reversed(result))
        for edge in links:
            if edge["from"] == current and edge["to"] not in prior:
                prior[edge["to"]] = edge
                queue.append(edge["to"])
    return None
