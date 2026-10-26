import os
"""地点（区域）之间的移动：把"相邻区域互走"做成可规划的技能。

星露谷的区域是一张图，相邻才能走。这里维护这张图 + 从快照里解析当前区域的出口，
上层只需要说「去 Town」，由 travel 技能逐跳走过去。

用法：
    route("Farm", "Town")        -> ["Forest", "Town"]      # 下一跳逐跳
    exits_of(snap)              -> {"BusStop": ref, "Forest": ref, ...}
    exit_to(snap, "Forest")     -> 出口实体（没有就 None）
"""
from collections import deque

# 常见相邻关系（真实游戏里的陆路/传送连接）。
# 有出口实体时会以快照里的出口为准，这张表只是兜底/规划用。
INDOOR = {
    # 室内/建筑：先走到所在区域，进门是另一回事
    "SeedShop": "Town", "Saloon": "Town", "Hospital": "Town", "Blacksmith": "Town",
    "Mine": "Mountain", "FarmHouse": "Farm",
}

EDGES = {
    "FarmHouse": {"Farm"},
    "Farm": {"FarmHouse", "Forest", "BusStop", "Backwoods"},
    "Backwoods": {"Farm", "BusStop", "Mountain", "Railroad"},
    "BusStop": {"Farm", "Backwoods", "Town", "Desert"},
    "Forest": {"Farm", "Town", "Backwoods"},
    "Town": {"Forest", "BusStop", "Mountain", "Beach"},
    "Beach": {"Town"},
    "Mountain": {"Town", "Backwoods", "BusStop", "Railroad"},
    "Railroad": {"Mountain", "BusStop", "Backwoods"},
    "SeedShop": {"Town"},
    "Saloon": {"Town"},
    "Hospital": {"Town"},
    "Blacksmith": {"Town"},
    "Mine": {"Mountain"},
    "Desert": {"BusStop"},
}

# 中文/显示名 -> 内部名
ALIASES = {
    "农舍": "FarmHouse", "农场": "Farm", "森林": "Forest", "小镇": "Town", "镇上": "Town",
    "镇子": "Town", "公交站": "BusStop", "车站": "BusStop", "火车站": "Railroad", "铁路": "Railroad", "铁路": "Railroad",
    "山区": "Mountain", "山": "Mountain", "海滩": "Beach", "海滩边": "Beach",
    "种子店": "SeedShop", "皮埃尔": "SeedShop", "酒吧": "Saloon", "医院": "Hospital",
    "铁匠铺": "Blacksmith", "矿洞": "Mine", "沙漠": "Desert", "后院": "Backwoods",
}


def norm(name):
    """把「出口 → BusStop」「农场」这类写法统一成内部名。"""
    if not name:
        return ""
    s = str(name)
    for sep in ("→", "->", "：", ":"):
        if sep in s:
            s = s.split(sep)[-1].strip()
    s = s.strip().strip("「」\"'")
    for k in sorted(ALIASES, key=len, reverse=True):
        if s == k or s.endswith(k):
            return ALIASES[k]
    for v in EDGES:
        if s.lower() == v.lower():
            return v
    return s


def neighbors(name, extra=None):
    name = norm(name)
    out = set(EDGES.get(name) or [])
    if extra:
        out |= set(extra)
    return {n for n in out if n}


def route(src, dst, extra_edges=None):
    """返回从 src 到 dst 的逐跳列表（不含 src）。已经在那里就返回 []。不可达返回 None。"""
    src, dst = norm(src), norm(dst)
    if not src or not dst:
        return None
    # 室内目标先到它所在的区域
    dst = INDOOR.get(dst, dst)
    src = INDOOR.get(src, src) if src not in EDGES else src
    if src == dst:
        return []
    seen = {src: None}
    q = deque([src])
    while q:
        cur = q.popleft()
        for nxt in neighbors(cur, (extra_edges or {}).get(cur)):
            if nxt in seen:
                continue
            seen[nxt] = cur
            if nxt == dst:
                path = [nxt]
                c = cur
                while c is not None:
                    path.append(c)
                    c = seen[c]
                path.reverse()
                return path[1:]
            q.append(nxt)
    return None


def exits_of(snap):
    """从快照里找当前地图的所有出口：{目标地点: 实体}。"""
    out = {}
    for e in snap.get("entities") or []:
        kind = (e.get("kind") or "")
        name = (e.get("name") or "")
        if kind in ("出口", "warp", "Warp") or name.startswith("出口"):
            dest = norm(name)
            if dest:
                out.setdefault(dest, e)
    return out


def exit_to(snap, dest):
    dest = norm(dest)
    ex = exits_of(snap)
    if dest in ex:
        return ex[dest]
    # 出口名可能带 A 面/B 面后缀，退化成前缀匹配
    for k, e in ex.items():
        if k.startswith(dest) or dest.startswith(k):
            return e
    return None


def describe_route(src, dst):
    hops = route(src, dst)
    if hops is None:
        return "不可达"
    if not hops:
        return "就在这里"
    return " → ".join([norm(src)] + hops)

# ---------- 录下来的真实出口坐标 ----------
_LEARNED = {"path": None, "data": {}}


def learned_path():
    data = os.environ.get("FARM_DATA") or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(data, "routes", "exits-learned.json")


def learned_all():
    """读录到的出口表；文件变了会自动重读。"""
    import json
    path = learned_path()
    if _LEARNED["path"] == path and _LEARNED["data"]:
        return _LEARNED["data"]
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f) or {}
    except Exception:
        data = {}
    _LEARNED["path"], _LEARNED["data"] = path, data
    return data


def learned_hop(src, dst):
    """录到的 (src -> dst) 出口坐标。"""
    data = learned_all()
    src, dst = norm(src), norm(dst)
    for key in (src,):
        m = data.get(key) or {}
        for k, v in m.items():
            if norm(k) == dst:
                try:
                    return {"leave": list(v.get("leave") or []),
                            "enter": list(v.get("enter") or []),
                            "path": [list(w) for w in (v.get("path") or [])]}
                except Exception:
                    return None
    return None
