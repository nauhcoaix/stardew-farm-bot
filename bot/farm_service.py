r"""农场服务：常驻跑，白天干活，晚上睡觉，日报进 outbox，指令从 inbox 读。

目录（都在游戏目录 _dev 下）：
  inbox\    微信来的指令，JSON：{"id":"c1","cmd":"water|status|sleep|plant","arg":""}
  outbox\   给微信发的日报，JSON：{"id":"r1","chat":"<会话名>","text":"..."}
  backups\  存档备份 zip
  STOP      放一个同名空文件就停

用法：
  python farm_service.py --once       跑一轮就退出（调试用）
  python farm_service.py --loop       常驻
"""
import argparse
import asyncio
import ctypes
import datetime
import glob
import json
import os
import shutil
import sys
import time
import zipfile

DEV = os.environ.get("FARM_DATA") or os.path.dirname(os.path.abspath(__file__))  # 代码可在别处，数据留在游戏目录
GAME = os.path.dirname(DEV)
INBOX = os.path.join(DEV, "inbox")
OUTBOX = os.path.join(DEV, "outbox")
SENT = os.path.join(OUTBOX, "sent")
BACKUPS = os.path.join(DEV, "backups")


# 节日：日期、开放时间、优先买的种子（id, 名字, 数量）
FESTIVAL_INFO = {
    ("spring", 13): {"name": "复活节", "start": 900, "end": 1400,
                     "seeds": [("745", "草莓种子", 20)], "mayor": "刘易斯"},
    ("spring", 24): {"name": "花舞节", "start": 900, "end": 1400, "seeds": [], "mayor": "刘易斯"},
    ("summer", 11): {"name": "夏威夷宴会", "start": 900, "end": 1400, "seeds": [], "mayor": "刘易斯"},
    ("summer", 28): {"name": "月光水母节", "start": 900, "end": 1400, "seeds": [], "mayor": "刘易斯"},
    ("fall", 16):   {"name": "星露谷集会", "start": 900, "end": 1400, "seeds": [], "mayor": "刘易斯"},
    ("fall", 27):   {"name": "万灵节", "start": 900, "end": 1400, "seeds": [], "mayor": "刘易斯"},
    ("winter", 8):  {"name": "冰雪节", "start": 900, "end": 1400, "seeds": [], "mayor": "刘易斯"},
    ("winter", 25): {"name": "冬星节", "start": 900, "end": 1400, "seeds": [], "mayor": "刘易斯"},
}
ATTENDED = set()
LOOT_DONE = [False]
BOUGHT_DAY = [None]
REPORT_DAY = [None]
SEASON_CN = {'spring': '春', 'summer': '夏', 'fall': '秋', 'winter': '冬'}


def festival_today(snap):
    d = snap.get("date") or {}
    return FESTIVAL_INFO.get((d.get("season"), d.get("day")))


FESTIVALS = {
    ("spring", 13), ("spring", 24),
    ("summer", 11), ("summer", 28),
    ("fall", 16), ("fall", 27),
    ("winter", 8), ("winter", 25),
}

STOP_FILE = os.path.join(DEV, "STOP")
SAVES = os.path.join(os.environ.get("APPDATA", ""), "StardewValley", "Saves")
REPORT_CHAT = os.environ.get("FARM_REPORT_CHAT") or "me"   # 日报发给谁（微信会话名）
WORK_CHUNK = 3  # 每段最多处理少量地块，随后检查控制指令和新计划

sys.path.insert(0, DEV)
from farm_client import Bridge  # noqa: E402
import farm_layout  # noqa: E402
import places  # noqa: E402
import planner  # noqa: E402
from farmer import (Farmer, ents, water_targets, harvestable, empty_tilled,  # noqa: E402
                    water_can, seeds_in, in_plot, tool_slot, CLEAR_TOOLS)
from work_planner import WorkPlanner, context_key  # noqa: E402


DECISIONS_FILE = os.path.join(DEV, "decisions.jsonl")
LAST_DECISION_REPORT = 0
DAY_STATS = {"day": None, "watered": 0, "harvested": 0, "planted": 0}
# 清障策略（上游 db771a1 引入；可用环境变量调）
CLEAR_MODE = (os.environ.get("FARM_CLEAR_MODE") or "auto").lower()      # auto | ask
CLEAR_ZONE_DEFAULT = (os.environ.get("FARM_CLEAR_ZONE") or "cluster").lower()
try:
    CLEAR_BUDGET = int(os.environ.get("FARM_CLEAR_BUDGET") or 20)        # 每天最多清几格
except Exception:
    CLEAR_BUDGET = 20
DAY_CLEAR = {"day": None, "cleared": 0, "in": 0, "out": 0, "skipped": 0}
LAST_ACTION = ""
PLAN = {}
PLAN_DAY = None
PLAN_REASON = ""


def seed_names(snap):
    return [(i.get("name"), i.get("count")) for i in snap.get("inventory") or [] if "种子" in (i.get("name") or "")]


def log(msg):
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(os.path.join(DEV, "farm-service.log"), "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d"), line))
    except Exception:
        pass


def ensure_dirs():
    for d in (INBOX, OUTBOX, SENT, BACKUPS):
        os.makedirs(d, exist_ok=True)


def backup_saves(keep=7):
    if not os.path.isdir(SAVES):
        return None
    os.makedirs(BACKUPS, exist_ok=True)
    name = "saves-%s.zip" % time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(BACKUPS, name)
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _, files in os.walk(SAVES):
                for fn in files:
                    full = os.path.join(root, fn)
                    z.write(full, os.path.relpath(full, SAVES))
    except Exception as e:  # noqa: BLE001
        log("备份失败: %s" % e)
        return None
    olds = sorted(glob.glob(os.path.join(BACKUPS, "saves-*.zip")))
    for old in olds[:-keep]:
        try:
            os.remove(old)
        except Exception:
            pass
    log("存档已备份 -> %s" % name)
    return path


def queue_report(text):
    os.makedirs(OUTBOX, exist_ok=True)
    rid = "r" + time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(OUTBOX, rid + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"id": rid, "chat": REPORT_CHAT, "text": text,
                   "at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                  f, ensure_ascii=False)
    log("日报已排队: %s" % text)
    return path


def take_commands():
    cmds = []
    for p in sorted(glob.glob(os.path.join(INBOX, "*.json"))):
        try:
            with open(p, "r", encoding="utf-8-sig") as f:
                cmds.append((p, json.load(f)))
        except Exception:
            try:
                os.remove(p)
            except Exception:
                pass
    return cmds


# 动作记录跑满就清零（mod 侧上限 20 万条，超了就 OUTPUT_LIMIT 卡死）
try:
    _reset_h = float(os.environ.get("FARM_RESET_HOURS") or 8)
except Exception:
    _reset_h = 8.0
STARTED_AT = time.time()
_last_bridge_err = ""
_last_bridge_at = 0.0
LAST_SNAP = {}          # 最近一次快照，给"马上回"用


def remember(snap):
    try:
        if snap:
            LAST_SNAP.clear()
            LAST_SNAP.update(snap)
    except Exception:
        pass


def take_quick_commands():
    """收件箱里的 status/decisions：不等这一轮干完，立刻用最近快照回。

    返回处理掉的条数。其余指令（water/sleep/task/backup）留给本轮正常流程。
    """
    handled = 0
    for p in sorted(glob.glob(os.path.join(INBOX, "*.json"))):
        try:
            with open(p, "r", encoding="utf-8-sig") as fh:
                cmd = json.load(fh)
        except Exception:
            continue
        c = (cmd.get("cmd") or "").lower()
        try:
            if c == "status":
                if LAST_SNAP:
                    queue_report("（先报个大概）" + summary_text(LAST_SNAP, {"watered": 0, "harvested": 0}))
                else:
                    queue_report("收到了，还没读到农场状态，这一轮干完就报。")
            elif c == "decisions":
                queue_report(last_decisions(5))
            else:
                continue
            handled += 1
            try:
                os.remove(p)
            except Exception:
                pass
        except Exception as e:  # noqa: BLE001
            log("即时回执出错：%s" % e)
    return handled


async def pump_loop():
    """每 5 秒看一次收件箱，专管"马上回"的那两种指令。"""
    while not stop_requested():
        try:
            n = take_quick_commands()
            if n:
                log("即时回执 %d 条" % n)
        except Exception as e:  # noqa: BLE001
            log("回执循环出错：%s" % e)
        await asyncio.sleep(5)


def stop_requested():
    return os.path.exists(STOP_FILE)


SEASON_CN = {"spring": "春", "summer": "夏", "fall": "秋", "winter": "冬"}


def summary_text(snap, stats):
    d = snap.get("date") or {}
    p = snap.get("player") or {}
    can = water_can(snap) or {}
    seeds = seeds_in(snap)
    parts = [
        "第 %s 天 · %s" % (d.get("day"), SEASON_CN.get(d.get("season"), d.get("season"))),
        "浇了 %d 格" % stats.get("watered", 0),
        "收了 %d 个" % stats.get("harvested", 0),
        "体力 %s/%s" % (p.get("energy"), p.get("maxEnergy")),
        "水壶 %s" % can.get("water"),
        "种子 %s" % sum(s.get("count", 0) for s in seeds),
    ]
    return "｜".join(parts)


STATUS_FILE = os.path.join(DEV, "status.json")
LAST_ACTION = ""
LAST_MONEY = None


def status_line_text(snap, money=None):
    d = snap.get("date") or {}
    p = snap.get("player") or {}
    inv = snap.get("inventory") or []
    used = sum(1 for i in inv if (i.get("count") or 0) > 0)
    cap = int(p.get("maxItems") or 12)
    season = SEASON_CN.get(d.get("season"), d.get("season"))
    t = int(snap.get("time") or 0)
    return "第%s天 %s %02d:%02d · 体力 %d/%d · %s g · 背包 %d/%d · 在 %s" % (
        d.get("day"), season, t // 100, t % 100 * 60 // 100,
        int(p.get("energy") or 0), int(p.get("maxEnergy") or 0),
        int(money if money is not None else (p.get("money") or 0)), used, cap, snap.get("location"))


def write_status(snap, money=None, action=None, brain=None):
    """把当前状态写出来，微信那边好直接回答。"""
    global LAST_ACTION, LAST_MONEY
    if money is not None:
        LAST_MONEY = money
    money = LAST_MONEY
    d = snap.get("date") or {}
    p = snap.get("player") or {}
    inv = snap.get("inventory") or []
    ents = snap.get("entities") or []
    seeds = sum(i.get("count", 0) for i in inv if "种子" in (i.get("name") or ""))
    used = sum(1 for i in inv if (i.get("count") or 0) > 0)
    cap = int(p.get("maxItems") or 12)
    crops = sum(1 for e in ents if e.get("crop"))
    ready = sum(1 for e in ents if e.get("harvestable"))
    dry = sum(1 for e in ents if e.get("kind") == "soil" and e.get("crop") and e.get("needsWater"))
    can = water_can(snap) or {}
    if action:
        LAST_ACTION = action
    row = {
        "day": d.get("day"),
        "season": SEASON_CN.get(d.get("season"), d.get("season")),
        "time": snap.get("time"),
        "energy": int(p.get("energy") or 0),
        "maxEnergy": int(p.get("maxEnergy") or 0),
        "money": money,
        "seeds": seeds,
        "crops": crops,
        "ready": ready,
        "dry": dry,
        "used": used, "cap": cap, "text": status_line_text(snap, money),
        "water": can.get("water"),
        "location": snap.get("location"),
        "action": LAST_ACTION,
        "updated": time.strftime("%H:%M:%S"),
    }
    if brain is not None:
        row["planner"] = {"thinking": brain.thinking, "queued_steps": brain.queued}
    try:
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(row, f, ensure_ascii=False)
    except Exception:
        pass




def record_decision(act, snap):
    """把每轮决策记到文件里，顺便决定要不要发微信。"""
    global LAST_DECISION_REPORT, LAST_ACTION
    d = snap.get("date") or {}
    p = snap.get("player") or {}
    row = {
        "ts": time.strftime("%H:%M:%S"),
        "day": d.get("day"),
        "time": snap.get("time"),
        "energy": p.get("energy"),
        "action": (act or {}).get("action"),
        "arg": (act or {}).get("arg"),
        "reason": (act or {}).get("reason"),
    }
    try:
        with open(DECISIONS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


ACTION_CN = {"clear": "清障", "water": "浇水", "till_plant": "翻地播种", "harvest": "收获",
             "chop": "砍树", "buy_seeds": "买种子", "sell": "卖货", "sleep": "睡觉", "idle": "发呆"}


def _fmt_time(t):
    t = int(t or 0)
    return "%02d:%02d" % (t // 100, t % 100)


def read_decisions(day):
    rows = []
    try:
        with open(DECISIONS_FILE, "r", encoding="utf-8") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if r.get("day") == day:
                    rows.append(r)
    except Exception:
        pass
    return rows


def build_report(day, snap, money=None):
    """生成当天完整日报：做了什么 + 模型怎么决定的。"""
    rows = read_decisions(day)
    d = snap.get("date") or {}
    p = snap.get("player") or {}
    can = water_can(snap) or {}
    seeds = sum(s.get("count", 0) for s in seeds_in(snap))
    cnt = {}
    for r in rows:
        a = r.get("action") or "?"
        cnt[a] = cnt.get(a, 0) + 1

    out = []
    out.append("第%s天 · %s 日报" % (day, SEASON_CN.get(d.get("season"), d.get("season"))))
    bits = []
    if cnt.get("water") or DAY_STATS.get("watered"):
        bits.append("浇水 %d 格" % DAY_STATS.get("watered", 0))
    if cnt.get("harvest") or DAY_STATS.get("harvested"):
        bits.append("收获 %d 个" % DAY_STATS.get("harvested", 0))
    if cnt.get("till_plant") or DAY_STATS.get("planted"):
        bits.append("播种 %d 格" % DAY_STATS.get("planted", 0))
    if cnt.get("clear"):
        bits.append("清障 %d 轮" % cnt["clear"])
    if cnt.get("chop"):
        bits.append("砍树 %d 轮" % cnt["chop"])
    if cnt.get("buy_seeds"):
        bits.append("买种子 %d 次" % cnt["buy_seeds"])
    if bits:
        out.append("，".join(bits))
    out.append("收工：钱 %s，体力 %s/%s，种子 %s，水壶 %s" % (
        money if money is not None else "?", int(p.get("energy") or 0), int(p.get("maxEnergy") or 0), seeds, can.get("water")))

    # 时间线：把连续同一个动作合并
    out.append("")
    out.append("当天经过：")
    runs = []
    for r in rows:
        a = r.get("action") or "?"
        if runs and runs[-1][0] == a:
            runs[-1][2] += 1
            runs[-1][3] = r.get("time")
        else:
            runs.append([a, r.get("time"), 1, r.get("time")])
    for a, t0, n, t1 in runs:
        span = _fmt_time(t0) if n == 1 or t0 == t1 else "%s-%s" % (_fmt_time(t0), _fmt_time(t1))
        out.append("  %s %s x%d" % (span, ACTION_CN.get(a, a), n))

    # 决策明细
    out.append("")
    out.append("模型决策（共 %d 条）：" % len(rows))
    for r in rows:
        out.append("  %s %s：%s" % (_fmt_time(r.get("time")), ACTION_CN.get(r.get("action"), r.get("action")), (r.get("reason") or "")[:60]))
    return "\n".join(out)


def day_summary(day):
    """统计今天的动作次数，放进日报。"""
    counts = {}
    try:
        with open(DECISIONS_FILE, "r", encoding="utf-8") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if r.get("day") != day:
                    continue
                a = r.get("action") or "?"
                counts[a] = counts.get(a, 0) + 1
    except Exception:
        return ""
    name = {"clear": "清障", "water": "浇水", "till_plant": "翻种", "harvest": "收",
            "chop": "砍树", "buy_seeds": "买种", "sell": "卖货", "sleep": "睡觉", "idle": "发呆"}
    parts = ["%s %d 次" % (name.get(k, k), v) for k, v in sorted(counts.items(), key=lambda x: -x[1])]
    return "，".join(parts)


def last_decisions(n=5):
    try:
        with open(DECISIONS_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
    except Exception:
        return "还没有决策记录"
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except Exception:
            continue
        out.append("%s %s（%s）" % (r.get("time"), r.get("action"), (r.get("reason") or "")[:24]))
    return "最近决策：" + "；".join(out) if out else "还没有决策记录"






async def auto_clear(b, f, snap, plan):
    """按「缺口」决定要不要清、清哪几格（上游清障策略）。

    缺口 = 想种的格数 - (已翻地空格 + 可耕地)
    zone: none / cluster / path / all；每天有格数预算，超了就停。
    """
    try:
        digest = farm_layout.farm_digest(snap)
    except Exception as e:  # noqa: BLE001
        log("算地图摘要失败：%s" % e)
        digest = {"free_slots": 0, "counts": {}}
    c = digest.get("counts") or {}
    available = int(digest.get("free_slots") or 0) + int(c.get("tillable") or 0)

    _d = (snap.get("date") or {}).get("day")
    if DAY_CLEAR.get("day") != _d:
        DAY_CLEAR.update({"day": _d, "cleared": 0, "in": 0, "out": 0, "skipped": 0})
    left = max(0, CLEAR_BUDGET - int(DAY_CLEAR.get("cleared") or 0))

    target = plan.get("plant_target")
    if target is None:
        seeds = sum(i.get("count", 0) for i in snap.get("inventory") or [] if "种子" in (i.get("name") or ""))
        target = seeds + int(plan.get("buy_qty") or 0)
    try:
        target = int(target)
    except Exception:
        target = 0
    need = max(0, target - available)

    zone = str(plan.get("clear_zone") or CLEAR_ZONE_DEFAULT).lower()
    if zone not in ("none", "cluster", "path", "all"):
        zone = CLEAR_ZONE_DEFAULT
    if need <= 0:
        zone = "none"
    if left <= 0:
        log("[清障] 今天的预算用完了（%d 格），不再清" % CLEAR_BUDGET)
        return snap

    log("[清障] zone=%s 目标=%d 可用=%d 缺口=%d 预算剩=%d 理由=%s" % (
        zone, target, available, need, left, plan.get("clear_reason") or "-"))
    if zone == "none":
        return snap

    min_energy = int(plan.get("clear_min_energy") or 70)
    picks = None
    if CLEAR_MODE == "ask" and need > 0:
        try:
            cands = farm_layout.clear_candidates(snap, zone=zone, need=need, max_n=min(8, left))
            if cands:
                lines = ["想种 %d 格，能直接种 %d 格，缺 %d 格。候选：" % (target, available, need)]
                for x in cands:
                    lines.append("%d. %s 在 (%d,%d)：%s，体力 %d" % (
                        x["i"], x["name"], x["tile"]["x"], x["tile"]["y"], x["why"], x["cost"]))
                picked = await asyncio.to_thread(planner.pick_candidates, "\n".join(lines))
                if picked and isinstance(picked.get("clear"), list):
                    picks = picked["clear"]
                    log("[清障] 模型挑了 %s（%s）" % (picks, picked.get("reason") or ""))
        except Exception as e:  # noqa: BLE001
            log("[清障] 选择题失败，退回自动：%s" % e)

    snap = await f.clear_obstacles(snap, limit=min(WORK_CHUNK, 8, left), min_energy=min_energy,
                                   zone=zone, need=need, picks=picks)
    DAY_CLEAR["cleared"] += int(f.last_cleared or 0)
    DAY_CLEAR["in"] += int(getattr(f, "last_cleared_in", 0) or 0)
    DAY_CLEAR["out"] += int(getattr(f, "last_cleared_out", 0) or 0)
    if f.last_cleared:
        log("[清障] 清了 %d 格（区内 %d / 区外 %d），今天累计 %d/%d" % (
            f.last_cleared, getattr(f, "last_cleared_in", 0), getattr(f, "last_cleared_out", 0),
            DAY_CLEAR["cleared"], CLEAR_BUDGET))
    return snap


def _map_center(snap):
    """用可走格子的中位数当"往地图中间走"的目标，比硬编码坐标稳。"""
    pts = []
    for e in snap.get("entities") or []:
        if e.get("passable") and e.get("tile"):
            t = e["tile"]
            if t.get("x") is not None:
                pts.append((t["x"], t["y"]))
    if not pts:
        return None
    xs = sorted(p[0] for p in pts)
    ys = sorted(p[1] for p in pts)
    return (xs[len(xs) // 2], ys[len(ys) // 2])


def _edge_points(snap):
    """从当前位置向外推的点（快照只有 16 格，靠这个一点点往地图边界走）。"""
    pos = (snap.get("player") or {}).get("position") or {}
    x, y = pos.get("x"), pos.get("y")
    if x is None:
        return []
    out = []
    for r in (14, 28, 42):
        for dx, dy in ((0, -r), (0, r), (r, 0), (-r, 0)):
            tx, ty = x + dx, y + dy
            if 0 <= tx <= 120 and 0 <= ty <= 120:
                out.append((tx, ty))
    return out


async def _snap_settled(f, snap, want_exits=False, tries=5, delay=0.8):
    """等场景稳定：刚切完地图时快照常常没有实体/出口，重试几次。"""
    for i in range(tries):
        loc = (snap.get("location") or "")
        ents = len(snap.get("entities") or [])
        exits = places.exits_of(snap)
        ok = ents > 0 and loc and (not want_exits or exits)
        if ok:
            return snap
        await asyncio.sleep(delay)
        try:
            snap = await f.snap(16)
        except Exception:
            pass
    return snap


async def _ensure_game_active(f, snap, tries=25):
    """等世界就绪：select(0) 是无害动作，能成功就说明 CanMove 正常（不在读档/过场里）。"""
    for i in range(tries):
        try:
            r = await f.b.select(0)
            if r:
                try:
                    snap = r.get("snapshot") or snap
                except Exception:
                    pass
                return snap
        except Exception as exc:
            if "BUSY" not in str(exc) and "INCOMPATIBLE" not in str(exc):
                log("  探活失败：%s" % exc)
        await asyncio.sleep(1.0)
    log("  等了 %d 秒游戏还不能操作（可能在读档/过场）" % tries)
    return snap


async def travel(b, f, snap, dest):
    """区域间移动：每次只看眼前，能看见哪个出口就往哪跳，直到到达目标区域。"""
    dest = places.norm(dest)
    if not dest:
        return snap
    snap = await _ensure_game_active(f, snap)
    snap = await _snap_settled(f, snap, want_exits=True)
    here = places.norm(snap.get("location"))
    target = places.norm(places.INDOOR.get(dest, dest))
    if places.norm(places.INDOOR.get(here, here)) == target:
        log("  已经在 %s，不用走" % here)
        return snap

    came_from = None
    for step_no in range(8):
        here = places.norm(snap.get("location"))
        if places.norm(places.INDOOR.get(here, here)) == target:
            log("  到 %s 了" % here)
            return snap

        visible = {places.norm(k): v for k, v in places.exits_of(snap).items()}
        learned_here = {places.norm(k) for k in (places.learned_all().get(here) or {})}
        cand_names = set(visible) | set(learned_here)
        if not cand_names:
            log("  %s 里没有出口，也没有录过的路线，先往地图中间走" % here)
            center = _map_center(snap)
            if center:
                walked = await f.do("走向地图中间", b.move(x=center[0], y=center[1]))
                snap = (walked or {}).get("snapshot") or await f.snap(16)
                snap = await _snap_settled(f, snap, want_exits=True)
                continue
            log("  看不到路，放弃这次移动")
            return snap

        # 视野里的出口 + 录到的出口一起做候选：挑剩余跳数最少、尽量不走回头路
        best = None
        for name in cand_names:
            if name == here or (came_from and name == came_from and len(cand_names) > 1):
                continue
            sub = places.route(name, target)
            if sub is None:
                continue
            cost = 1 + len(sub) + (3 if name == came_from else 0)
            if best is None or cost < best[0]:
                best = (cost, name, visible.get(name), sub)
        if best is None:
            log("  %s 的出口（%s）都到不了 %s" % (here, ", ".join(sorted(cand_names)), dest))
            return snap

        cost, hop, ex, sub = best
        tile = (ex or {}).get("tile") or {}
        log("  %s → %s（再 %s，共 %d 跳）%s" % (
            here, hop, " → ".join(sub) or "就到", cost,
            "" if ex else "（按录到的坐标走）"))

        # 1) 有录到的路线就按拐点一段段走（一次走太远会 PATH_BLOCKED）
        learned = places.learned_hop(here, hop)
        if learned and learned.get("leave"):
            wps = [tuple(w[:2]) for w in (learned.get("path") or []) if w and w[0] is not None]
            last = tuple(learned["leave"][:2])
            if not wps or wps[-1] != last:
                wps.append(last)
            # 只保留"越走越靠近终点"的拐点，避免绕回头
            log("  按录到的路线走：%d 个拐点，终点 (%d,%d)" % (len(wps), last[0], last[1]))
            for idx, (wx, wy) in enumerate(wps):
                if places.norm(snap.get("location")) == hop:
                    break
                for attempt in range(3):
                    walked = await f.do("拐点%d (%d,%d)" % (idx + 1, wx, wy), b.move(x=wx, y=wy))
                    snap = (walked or {}).get("snapshot") or await f.snap(16)
                    snap = await _snap_settled(f, snap, want_exits=True)
                    if places.norm(snap.get("location")) == hop:
                        break
                    if "BUSY" not in (f.last_error or ""):
                        break
                    await asyncio.sleep(1.5)
            if places.norm(snap.get("location")) == hop:
                log("  到 %s 了" % hop)
                came_from = here
                continue
            log("  没切到 %s（现在 %s），换别的办法" % (hop, places.norm(snap.get("location"))))
        if places.exit_to(snap, hop) is None:
            # 这个出口在视野外（实测 BusStop 去小镇的出口在往东 30 多格外）
            found = None
            for edge in _edge_points(snap):
                log("  去 %s 的出口不在视野里，先往地图边上 (%d,%d) 走" % (hop, edge[0], edge[1]))
                walked = None
                for attempt in range(3):
                    walked = await f.do("走向地图边缘", b.move(x=edge[0], y=edge[1]))
                    if walked:
                        break
                    if "BUSY" not in (f.last_error or ""):
                        break
                    await asyncio.sleep(1.2)
                    snap = await f.snap(16)
                snap = (walked or {}).get("snapshot") or await f.snap(16)
                snap = await _snap_settled(f, snap, want_exits=True)
                found = places.exit_to(snap, hop)
                if found:
                    break
            if not found:
                log("  还是找不到去 %s 的出口（能看见：%s）" % (
                    hop, ", ".join(sorted(places.exits_of(snap))) or "（没有）"))
                return snap
            ex = found

        if not ex:
            log("  %s 既没录到路线、视野里也看不到去 %s 的出口，放弃这一跳" % (here, hop))
            return snap
        moved = None
        for attempt in range(5):
            moved = await f.do("走到出口 %s" % (hop,), b.move(near=ex["ref"]))
            if moved:
                break
            if "BUSY" not in (f.last_error or ""):
                break
            await asyncio.sleep(2.0)
            try:
                snap = await f.snap(16)
                ex = places.exit_to(snap, hop) or ex
            except Exception:
                pass
        if not moved:
            log("  走不到出口（%s）" % (f.last_error or "未知原因"))
            return snap
        snap = moved.get("snapshot") or await f.snap(16)
        if places.norm(snap.get("location")) != hop:
            stepped = await f.do("踩上出口 %s" % (hop,), b.move(tile={"x": tile.get("x"), "y": tile.get("y")}))
            if stepped:
                snap = stepped.get("snapshot") or await f.snap(16)
        if places.norm(snap.get("location")) != hop:
            got = await f.do("交互出口 %s" % (hop,), b.interact(ex["ref"]))
            if got:
                snap = got.get("snapshot") or await f.snap(16)
        snap = await _snap_settled(f, snap, want_exits=True)
        now = places.norm(snap.get("location"))
        if now == hop:
            log("  到 %s 了" % hop)
            came_from = here
        else:
            log("  还在 %s，没切到 %s" % (now, hop))
            return snap
    log("  走了太多跳，先停下（当前 %s）" % places.norm(snap.get("location")))
    return snap


async def run_action(b, f, snap, act):
    """执行模型挑的动作。"""
    snap = await f.ensure_free(snap)
    a = (act.get("action") or "").lower()
    arg = act.get("arg") or {}
    if a == "harvest":
        snap = await f.harvest(snap, limit=WORK_CHUNK)
        # 收完那块地还是翻好的，直接补种；不要再走一遍翻地
        if f.last_harvest and sum(i.get("count", 0) for i in seeds_in(snap)) > 0:
            log("  收完就地补种 %d 格（地还是翻好的，不用再挖）" % f.last_harvest)
            snap = await f.plant(snap, limit=f.last_harvest)
        return snap
    if a == "clear":
        plan = dict(PLAN or {})
        plan["clear_min_energy"] = int(arg.get("min_energy", plan.get("clear_min_energy", 70)) or 70)
        return await auto_clear(b, f, snap, plan)
    if a == "till_plant":
        limit = min(WORK_CHUNK, int(arg.get("limit", 3)))
        if not f.available_targets(snap, "plant", empty_tilled(snap)):
            snap = await f.till(snap, limit=limit)
        return await f.plant(snap, limit=limit)
    if a == "travel":
        return await travel(b, f, snap, arg.get("to") or arg.get("dest"))
    if a == "water":
        return await f.water_crops(snap, limit=WORK_CHUNK)
    if a == "chop":
        return await f.chop_trees(snap, want_wood=100, max_swings=WORK_CHUNK)
    if a == "buy_seeds":
        from shop import buy_seeds
        return await buy_seeds(b, f, snap, want=int(arg.get("qty", 20)))
    if a == "sell":
        return await stash_items(b, snap)
    if a == "sleep":
        await f.sleep(snap)
        return await b.snapshot(12)
    return snap




async def is_festival(b, snap):
    d = snap.get("date") or {}
    key = (d.get("season"), d.get("day"))
    return key in FESTIVALS




async def festival_attend(b, f, snap, info):
    """节日：进场 → 找镇长对话 → 买种子 → 离场。整段有超时兜底。"""
    import shop as shopmod

    day_key = (snap.get("date") or {}).get("season", "") + str((snap.get("date") or {}).get("day", ""))
    log("今天是%s（%s），去参加" % (info["name"], day_key))
    try:
        # 1) 先到镇上
        loc = snap.get("location") or ""
        if loc not in ("Town", "Forest", "SeedShop"):
            from farmer import Farmer  # noqa: F401
            snap = await shopmod._goto_town(b, f, snap)
            log("  到镇上了：%s" % snap.get("location"))
        # 2) 找节日入口（镇上的节日活动点）
        for _ in range(6):
            ent = None
            for e in snap.get("entities") or []:
                name = e.get("name") or ""
                if e.get("kind") == "action" and ("节日" in name or "Festival" in name):
                    ent = e
                    break
            if ent:
                log("  节日入口：%s" % ent.get("name"))
                r = await f.do("走进节日", b.interact(ent["ref"]))
                snap = (r or {}).get("snapshot") or await f.snap(14)
                break
            # 没有明确的入口标识，就在镇上广场附近找 NPC
            break
        await asyncio.sleep(1.5)
        snap = await f.snap(16)
        # 3) 跟镇长说话（有些节日必须对话才能开场/结束）
        mayor = info.get("mayor")
        for _ in range(8):
            npc = None
            for e in snap.get("entities") or []:
                if e.get("kind") == "npc" and mayor and mayor in (e.get("name") or ""):
                    npc = e
                    break
            if not npc:
                break
            r = await f.do("找镇长", b.move(near=npc["ref"]))
            snap = (r or {}).get("snapshot") or await f.snap(14)
            r = await f.do("和镇长说话", b.interact(npc["ref"]))
            snap = (r or {}).get("snapshot") or await f.snap(14)
            menu = snap.get("menu") or {}
            if menu:
                await f.clear_menu(snap)
                snap = await f.snap(14)
            await asyncio.sleep(0.6)
        # 4) 逛摊位买东西
        for sid, sname, want in info.get("seeds") or []:
            try:
                md = await b.request("menudump", {})
                rows = md.get("shop") or []
                idx = None
                for i, row in enumerate(rows):
                    if str(row.get("itemId") or "").endswith(sid) or sname in (row.get("name") or ""):
                        idx = i
                        break
                if idx is None:
                    log("  摊位上没找到 %s" % sname)
                    continue
                r = await b.request("shopbuy", {"index": idx, "qty": want})
                log("  买 %s：%s" % (sname, r.get("detail") or r.get("reason")))
            except Exception as e:  # noqa: BLE001
                log("  买 %s 出错：%s" % (sname, e))
        # 5) 离场：走到出口
        await asyncio.sleep(0.5)
        snap = await f.snap(16)
        exits = [e for e in snap.get("entities") or [] if e.get("kind") == "exit"]
        if exits:
            r = await f.do("往出口走", b.move(near=exits[0]["ref"]))
            snap = (r or {}).get("snapshot") or await f.snap(14)
    except Exception as e:  # noqa: BLE001
        log("  节日流程出错：%s" % e)
    finally:
        ATTENDED.add(day_key)
        snap = await f.snap(16)
        if (snap.get("location") or "") not in ("Farm", "FarmHouse"):
            log("  兜底：没回农场就传送回去")
            try:
                await b.request("warp", {"location": "Farm", "x": 64, "y": 15})
                snap = await f.snap(16)
            except Exception as _e:
                log("  传送失败：%s" % _e)
    return snap


def _digest_text(snap):
    """地图摘要：给后台规划器判断「缺多少可种格」（上游清障策略的输入）。"""
    try:
        return farm_layout.describe_digest(farm_layout.farm_digest(snap))
    except Exception as e:  # noqa: BLE001
        return "（地图摘要算不出来：%s）" % e


def _no_land_in_view(snap):
    """快照里完全没有耕地/作物/可耕地 —— 多半是站得太远，摘要会失真。"""
    try:
        import farm_layout as _fl
        d = _fl.farm_digest(snap)
        c = d.get("counts") or {}
        return (c.get("tilled", 0) + c.get("tillable", 0) + c.get("crops", 0)) == 0
    except Exception:
        return False


_plot_view_next = [0.0]


async def _snap_with_plot(b, f, snap):
    """站得远看不到农田时，先走到农舍门口补一张快照，让规划有地可看。"""
    if (snap.get("location") or "") != "Farm" or not _no_land_in_view(snap):
        return snap
    # 只有真快照才值得走一趟；测试/伪快照实体很少，直接跳过
    if len(snap.get("entities") or []) < 60:
        return snap
    # 刚试过没成功就先别试，免得每轮都走一遍
    if time.time() < _plot_view_next[0]:
        return snap
    log("视野里看不到农田，先走到农舍门口再看")
    try:
        await b.move(x=64, y=16)
        await asyncio.sleep(0.8)
        snap2 = await f.snap(16)
        if snap2 and snap2.get("ok") is not False:
            if _no_land_in_view(snap2):
                _plot_view_next[0] = time.time() + 120
                log("  到了门口还是看不到农田，两分钟内不再尝试")
            return snap2
    except Exception as exc:  # noqa: BLE001
        _plot_view_next[0] = time.time() + 60
        log("  走过去失败（%s），60 秒内不再尝试" % exc)
    return snap


def planning_text(snap, history=None):
    """构造纯文本状态副本；后台线程不能读取Bridge或修改当前快照。"""
    p = snap.get("player") or {}
    es = ents(snap)
    return json.dumps({
        "date": snap.get("date"), "time": snap.get("time"), "location": snap.get("location"),
        "energy": p.get("energy"), "money": p.get("money"),
        "inventory": [{k: it.get(k) for k in ("name", "count", "tool", "water")}
                      for it in snap.get("inventory") or []],
        "ready": len(harvestable(snap)), "dry": len(water_targets(snap)),
        "empty_tilled": len(empty_tilled(snap)),
        "diggable_in_plot": sum(bool(e.get("diggable") and in_plot(e.get("tile"))) for e in es),
        "obstacles": sum(e.get("name") in CLEAR_TOOLS for e in es),
        "plot": "x60..69,y15..22；目标仅限当前观察范围",
        "map": _digest_text(snap),
        "action_history": history.summary() if history is not None else {},
    }, ensure_ascii=False)


def action_ready(snap, act, plan, history=None):
    """无论计划何时生成，派发前都用当前状态检查。"""
    a, arg = act.get("action"), act.get("arg") or {}
    p = snap.get("player") or {}
    energy = float(p.get("energy") or 0)
    t = int(snap.get("time") or 0)
    def available(operation, targets):
        return [e for e in targets if history is None or history.allowed(snap, operation, e)]
    if a == "sleep":
        return True
    if a == "travel":
        here = places.norm(snap.get("location"))
        dest = places.norm(arg.get("to") or arg.get("dest"))
        if not dest or dest == here:
            return False
        return places.route(here, dest, extra_edges={here: set(places.exits_of(snap))}) is not None
    if snap.get("location") != "Farm":
        return False
    if a == "harvest":
        return bool(available("harvest", harvestable(snap)))
    if a == "water":
        return energy >= 25 and bool(water_can(snap)) and bool(available("water", water_targets(snap)))
    if a == "till_plant":
        return energy >= 40 and int(arg.get("limit", 3)) > 0 and bool(seeds_in(snap)) and (
            bool(available("plant", empty_tilled(snap))) or bool(tool_slot(snap, "Hoe")) and
            any(e.get("diggable") and not e.get("tilled") and in_plot(e.get("tile"))
                for e in available("till", ents(snap))))
    if a == "clear":
        if energy < int(arg.get("min_energy", plan.get("clear_min_energy", 70))):
            return False
        if int(arg.get("limit", 3)) <= 0:
            return False
        # 按"缺口驱动 + 区域闸门"判断到底有没有该清的：没候选就不算可执行
        try:
            import farm_layout
            zone = str(plan.get("clear_zone") or "cluster").lower()
            if zone == "none":
                return False
            need = int(plan.get("plant_target") or 0) - (
                len(empty_tilled(snap)) + sum(1 for e in ents(snap) if e.get("diggable") and in_plot(e.get("tile"))))
            cands = farm_layout.clear_candidates(snap, zone=zone, need=max(1, need), max_n=8)
            if not cands:
                return False
            return any(tool_slot(snap, c["tool"]) for c in cands)
        except Exception:
            pass
        return any(
            tool_slot(snap, CLEAR_TOOLS[e["name"]]) for e in available("clear", ents(snap)) if e.get("name") in CLEAR_TOOLS)
    if a == "chop":
        wood = sum(i.get("count", 0) for i in snap.get("inventory") or [] if i.get("name") == "木材")
        return energy >= 70 and wood < 100 and bool(tool_slot(snap, "Axe")) and any(
            e.get("name") in ("Tree", "树") for e in available("chop", ents(snap)))
    if a == "buy_seeds":
        d = snap.get("date") or {}
        total = sum(i.get("count", 0) for i in seeds_in(snap))
        return (900 <= t < 1600 and energy >= 40 and (p.get("money") or 0) >= 20
                and total < plan.get("buy_when_below", 5) and plan.get("buy_qty", 20) > 0
                and int(arg.get("qty", 20)) > 0 and BOUGHT_DAY[0] != context_key(snap)
                and (d.get("season"), d.get("day")) not in FESTIVALS)
    if a == "sell":
        return any((it.get("count") or 0) > 0 and it.get("name") not in KEEP_NAMES
                   and not it.get("tool") and it.get("name") not in ("木材", "硬木")
                   and "种子" not in (it.get("name") or "")
                   and "Seeds" not in (it.get("name") or "") for it in snap.get("inventory") or [])
    return False


def local_action(snap, brain):
    """模型未返回也能做事；仅挑当前可执行且不在失败冷却中的技能。"""
    plan = brain.daily
    candidates = [
        {"action": "harvest", "arg": {}, "reason": "本地策略：收成熟作物"},
        {"action": "water", "arg": {}, "reason": "本地策略：照料缺水地块"},
        {"action": "till_plant", "arg": {"limit": WORK_CHUNK}, "reason": "本地策略：使用现有种子"},
        {"action": "buy_seeds", "arg": {"qty": plan.get("buy_qty", 20)}, "reason": "本地策略：按预算补货"},
        {"action": "clear", "arg": {"limit": plan.get("clear_limit", 3),
                                     "min_energy": plan.get("clear_min_energy", 70)},
         "reason": "本地策略：按体力预算清障"},
    ]
    if plan.get("chop"):
        candidates.append({"action": "chop", "arg": {}, "reason": "本地策略：按计划攒木材"})
    candidates.append({"action": "sell", "arg": {}, "reason": "本地策略：整理背包"})
    used = sum((i.get("count") or 0) > 0 for i in snap.get("inventory") or [])
    if used >= int((snap.get("player") or {}).get("maxItems") or 12) - 1:
        candidates.insert(0, {"action": "sell", "arg": {}, "reason": "本地策略：背包将满，先腾空间"})
    for action in candidates:
        if brain.allowed(action["action"]) and action_ready(snap, action, plan, brain.history):
            action["source"] = "local"
            return action
    # 体力不足且已无可做农务时恢复到下一天，而非在原地重复空操作。
    if float((snap.get("player") or {}).get("energy") or 0) < 40 and brain.allowed("sleep"):
        return {"action": "sleep", "arg": {}, "reason": "本地策略：体力不足，恢复后继续", "source": "local"}
    return None


def made_progress(before, after):
    """比较资源及共同观察格；视野变了不能单独算有效劳动。"""
    def resources(snap):
        p = snap.get("player") or {}
        inventory = [{k: i.get(k) for k in ("slot", "itemId", "name", "count")}
                     for i in snap.get("inventory") or []]
        return json.dumps([snap.get("date"), p.get("money"), inventory], sort_keys=True)
    if resources(before) != resources(after):
        return True
    def tiles(snap):
        return {(e["tile"].get("x"), e["tile"].get("y")):
                tuple(e.get(k) for k in ("kind", "name", "crop", "watered", "tilled", "cropPhase", "cropDead"))
                for e in ents(snap) if e.get("tile") and e.get("kind") != "npc"}
    old, new = tiles(before), tiles(after)
    return any(old[t] != new[t] for t in old.keys() & new.keys())


async def run_once(brain=None):
    owned = brain is None
    brain = brain or WorkPlanner(planner.plan_work, log, queue_report,
                                history_path=os.path.join(DEV, "action-history.json"))
    try:
        return await _run_once(brain)
    finally:
        if owned:
            brain.close()


async def _run_once(brain):
    """每轮只执行一个短技能；规划器在多轮间保留队列和唯一HTTP请求。"""
    ensure_dirs()
    if stop_requested():
        return False
    global STARTED_AT, PLAN, PLAN_DAY, PLAN_REASON
    for path, cmd in take_commands():
        if (cmd.get("cmd") or "").lower() == "play":
            from launch_farm import ensure_loaded
            ok = await ensure_loaded()
            queue_report("星露谷开好了：" + ("进存档了" if ok else "没进去"))
            os.remove(path)

    b = Bridge()
    try:
        await b.__aenter__()
    except Exception as exc:
        global _last_bridge_err, _last_bridge_at
        now = time.time()
        msg = str(exc)
        # 同一条错误 60 秒内只记一次，别把日志刷满
        if msg != _last_bridge_err or now - _last_bridge_at > 60:
            log("连不上游戏桥：%s" % msg)
            _last_bridge_err, _last_bridge_at = msg, now
        return False
    try:
        if _reset_h > 0 and time.time() - STARTED_AT > _reset_h * 3600:
            await b.reset()
            STARTED_AT = time.time()
            brain.interrupt()
            log("动作记录已清零，继续工作")
        f = Farmer(b, state_path=os.path.join(DEV, "farm-state.json"), log=log, history=brain.history)
        try:
            snap = await f.snap(16)
        except Exception as exc:
            log("读快照失败（%s），尝试恢复过场界面" % type(exc).__name__)
            from farmer import click_game_center
            for _ in range(3):
                if stop_requested():
                    return False
                click_game_center()
                await asyncio.sleep(1.5)
            snap = await f.snap(16)
        if not snap or snap.get("ok") is False:
            return False
        snap = await _snap_with_plot(b, f, snap)
        remember(snap)
        brain.observe(snap)
        # 先接收新控制意图，再轮询旧结果，防止旧计划抢先执行。
        do_sleep = False
        for path, cmd in take_commands():
            c = (cmd.get("cmd") or "").lower()
            try:
                if c == "status":
                    queue_report("现在的情况：" + summary_text(snap, {}))
                elif c == "decisions":
                    queue_report(last_decisions(5))
                elif c in ("water", "work"):
                    brain.set_steps([{"action": "water"}], mission="浇水")
                    queue_report("浇水已接入执行队列")
                elif c == "task":
                    text = str(cmd.get("arg") or "").strip()
                    if text:
                        brain.submit_task(text)
                        queue_report("已收到任务「%s」，后台规划期间继续农务" % text)
                elif c == "sleep":
                    brain.interrupt()
                    do_sleep = True
                elif c == "reset":
                    await b.reset()
                    STARTED_AT = time.time()
                    brain.interrupt()
                    queue_report("动作记录已清零，接着干")
                elif c == "backup":
                    backup_saves()
            except Exception as exc:
                log("指令 %s 出错：%s" % (c, exc))
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass

        brain.poll(snap, planning_text(snap, brain.history))
        PLAN, PLAN_DAY = dict(brain.daily), context_key(snap)
        PLAN_REASON = str(PLAN.get("reason") or "")
        if stop_requested():
            return False
        # 玩家指令、体力和就寝时间无需等模型判断。
        energy = float((snap.get("player") or {}).get("energy") or 0)
        must_sleep = do_sleep or int(snap.get("time") or 0) >= PLAN["sleep_at"] or energy <= 20
        if must_sleep and brain.allowed("sleep"):
            act = {"action": "sleep", "arg": {}, "reason": "玩家指令、就寝时间或低体力", "source": "local"}
        else:
            snap = await f.clear_menu(snap)
            info = festival_today(snap)
            day_key = str(context_key(snap))
            if info and day_key not in ATTENDED and info["start"] <= int(snap.get("time") or 0) < info["end"]:
                snap = await asyncio.wait_for(festival_attend(b, f, snap, info), timeout=480)
                ATTENDED.add(day_key)
                remember(snap)
                return True
            if snap.get("location") not in ("Farm", "FarmHouse"):
                if snap.get("location") not in ("Forest", "Town", "SeedShop"):
                    # 保留原有异常地图回农场的恢复策略。
                    await b.request("warp", {"location": "Farm", "x": 64, "y": 15})
                    await asyncio.sleep(2)
                    snap = await f.snap(16)
                from shop import _go_home
                snap = await _go_home(b, f, snap)
            if snap.get("location") == "FarmHouse":
                if not LOOT_DONE[0] and not seeds_in(snap):
                    snap = await f.loot_container(snap)
                    LOOT_DONE[0] = True
                snap = await f.go_outside(snap)
            if not LOOT_DONE[0]:
                LOOT_DONE[0] = True
                if not seeds_in(snap):
                    snap = await f.go_inside(snap)
                    snap = await f.loot_container(snap)
                    snap = await f.go_outside(snap)
            if stop_requested():
                return False
            # 新快照可能跨日或已经完成旧目标，再轮询并检查计划。
            brain.poll(snap, planning_text(snap, brain.history))
            PLAN = dict(brain.daily)
            ready = lambda a: action_ready(snap, a, PLAN, brain.history)
            act = brain.take_action(ready)
            if act is None:
                act = local_action(snap, brain)
        if not act:
            remember(snap)
            write_status(snap, (snap.get("player") or {}).get("money"), "等待可执行任务（后台规划中）" if brain.thinking else "暂无可执行任务", brain)
            return False
        if stop_requested():
            return False

        before = snap
        name = act["action"]
        log("%s：%s %s（%s）" % ("计划执行" if act.get("source") == "model" else "本地执行",
                                  name, act.get("arg") or {}, act.get("reason") or ""))
        record_decision(act, before)
        write_status(before, (before.get("player") or {}).get("money"), name, brain)
        try:
            snap = await run_action(b, f, snap, act) or await f.snap(16)
        except Exception as exc:
            brain.complete(act, False)
            log("执行 %s 受阻：%s" % (name, exc))
            return False
        # 统一取新范围，下一段只使用当前引用。
        snap = await f.snap(16)
        counts = {"water": f.last_watered, "harvest": f.last_harvest,
                  "till_plant": max(f.last_till, f.last_planted),
                  "clear": f.last_cleared, "chop": f.last_chops}
        progress = counts[name] > 0 if name in counts else made_progress(before, snap)
        repeat = False
        if name in ("water", "harvest", "chop"):
            repeat = action_ready(snap, act, PLAN, brain.history)
        elif name in ("clear", "till_plant"):
            done = f.last_cleared if name == "clear" else max(f.last_till, f.last_planted)
            remaining = max(0, int((act.get("arg") or {}).get("limit", 3)) - done)
            if remaining:
                act = dict(act, arg=dict(act.get("arg") or {}, limit=remaining))
                repeat = action_ready(snap, act, PLAN, brain.history)
        # repeat 现在由 action_ready 严格判定（clear 会真的问"有没有候选"），
        # 所以这里可以恢复原语义：还有别的目标就换目标重排，没有就交给 complete 冷却。
        retry_other = name in counts and repeat
        brain.complete(act, progress, repeat=repeat and act.get("source") == "model",
                       retry_other=retry_other)

        if name == "buy_seeds" and sum(i.get("count", 0) for i in seeds_in(snap)) > sum(i.get("count", 0) for i in seeds_in(before)):
            BOUGHT_DAY[0] = context_key(snap)
        if context_key(before) != context_key(snap):
            backup_saves()
            report = build_report((before.get("date") or {}).get("day"), snap,
                                  (snap.get("player") or {}).get("money"))
            report_dir = os.path.join(DEV, "reports")
            os.makedirs(report_dir, exist_ok=True)
            date = before.get("date") or {}
            filename = "day-%s-%s-%s.txt" % (date.get("year"), date.get("season"), date.get("day"))
            with open(os.path.join(report_dir, filename), "w", encoding="utf-8") as fh:
                fh.write(report)
            queue_report(report)
        if snap.get("location") == "Farm" and brain.allowed("chest"):
            # 建箱子也要避免每个短技能结束都重复创建。
            await ensure_chest(b, snap)
            brain.cool_down("chest", 120)
        remember(snap)
        write_status(snap, (snap.get("player") or {}).get("money"), name, brain)
        day = context_key(snap)
        if DAY_STATS.get("day") != day:
            DAY_STATS.update(day=day, watered=0, harvested=0, planted=0)
        DAY_STATS["watered"] += f.last_watered
        DAY_STATS["harvested"] += f.last_harvest
        DAY_STATS["planted"] += f.last_planted
        return progress
    finally:
        await b.__aexit__(None, None, None)


async def queue_status(f, snap):
    await f.clear_menu(snap)
    text = summary_text(snap, {"watered": 0, "harvested": 0})
    queue_report("现在的情况：" + text)


CHEST_X, CHEST_Y = 63, 17
KEEP_NAMES = ("斧头", "锄头", "喷壶", "十字镐", "镰刀")


async def ensure_chest(b, snap):
    """没箱子就造一个放在农舍旁边。"""
    if (snap.get("location") or "") != "Farm":
        return
    if any(e.get("objType") == "Chest" and (e.get("tile") or {}).get("x") in (CHEST_X - 1, CHEST_X)
           and (e.get("tile") or {}).get("y") in (CHEST_Y, CHEST_Y + 1) for e in ents(snap)):
        return
    wood = sum(i.get("count", 0) for i in snap.get("inventory") or [] if (i.get("name") or "") == "木材")
    if wood < 50:
        return
    for tx, ty in [(CHEST_X, CHEST_Y), (CHEST_X - 1, CHEST_Y), (CHEST_X, CHEST_Y + 1)]:
        r = await b.request("placechest", {"x": tx, "y": ty})
        if r.get("ok"):
            log("放了个箱子在 %s,%s" % (tx, ty))
            return


JUNK_NAMES = ("纤维", "粘土", "石头", "煤炭", "硬木", "铜矿石", "铁矿石", "晶球")


async def stash_items(b, snap):
    """作物卖掉，杂物进箱子，工具/种子/木材留着。"""
    if (snap.get("location") or "") != "Farm":
        return snap
    for it in list(snap.get("inventory") or []):
        nm = it.get("name") or ""
        if it.get("tool") or nm in KEEP_NAMES or "种子" in nm or "Seeds" in nm or nm in ("木材", "硬木"):
            continue
        if (it.get("count") or 0) <= 0:
            continue
        if nm in JUNK_NAMES:
            r = await b.request("chestput", {"x": CHEST_X, "y": CHEST_Y, "slot": int(it.get("slot"))})
            if r.get("ok"):
                log("  收进箱子：%s x%s" % (r.get("name"), r.get("count")))
        else:
            r = await b.request("ship", {"slot": int(it.get("slot"))})
            if r.get("ok"):
                log("  卖了：%s x%s" % (r.get("name"), r.get("count")))
        await asyncio.sleep(0.25)
    return await b.snapshot(12)

async def count_watered(f, snap):
    targets = water_targets(snap)
    if not targets:
        return 0
    snap2 = await f.water_crops(snap)
    left = len(water_targets(snap2))
    return max(0, len(targets) - left)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    args = ap.parse_args()
    ensure_dirs()
    brain = WorkPlanner(planner.plan_work, log, queue_report,
                        history_path=os.path.join(DEV, "action-history.json"))
    pump = None
    try:
        if args.once or not args.loop:
            await run_once(brain)
            return 0
        log("农场服务启动：后台规划，连续执行")
        pump = asyncio.create_task(pump_loop())
        streak = 0
        while not stop_requested():
            try:
                progressed = await run_once(brain)
            except Exception as exc:
                log("这轮出错：%s" % exc)
                progressed = False
            if progressed:
                streak = 0
                delay = 0.1          # 有进展：几乎不停
            else:
                streak += 1
                delay = min(30.0, 2.0 ** min(streak, 5))   # 2,4,8,16,32 -> 封顶 30
            # 有工作只短暂让出事件循环；空闲/断线时退避，避免空转。
            if not stop_requested():
                await asyncio.sleep(delay)
    finally:
        brain.close()
        if pump:
            pump.cancel()
            try:
                await pump
            except asyncio.CancelledError:
                pass
    log("农场服务退出")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
