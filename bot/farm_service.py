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
WORK_INTERVAL_MIN = 25

sys.path.insert(0, DEV)
from farm_client import Bridge  # noqa: E402
import planner  # noqa: E402
from farmer import (Farmer, ents, water_targets, harvestable, empty_tilled,  # noqa: E402
                    water_can, seeds_in)


DECISIONS_FILE = os.path.join(DEV, "decisions.jsonl")
LAST_DECISION_REPORT = 0
DAY_STATS = {"day": None, "watered": 0, "harvested": 0, "planted": 0}
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


def write_status(snap, money=None, action=None):
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
    try:
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(row, f, ensure_ascii=False)
    except Exception:
        pass


async def refresh_plan(b, f, snap):
    """把当前状态发给模型，让它决定今天怎么干。"""
    d = snap.get("date") or {}
    p = snap.get("player") or {}
    inv = snap.get("inventory") or []
    seeds = sum(i.get("count", 0) for i in inv if "种子" in (i.get("name") or ""))
    wood = sum(i.get("count", 0) for i in inv if (i.get("name") or "") == "木材")
    used = sum(1 for i in inv if (i.get("count") or 0) > 0)
    cap = int(p.get("maxItems") or 12)
    crops = sum(1 for e in (snap.get("entities") or []) if e.get("crop"))
    money = None
    try:
        md = await b.request("menudump", {})
        money = md.get("money")
    except Exception:
        pass
    text = ("第%s天 %s %s点，体力 %s/%s，钱 %s，种子 %s，作物 %s 株，背包 %s/36，木材 %s"
            % (d.get("day"), d.get("season"), snap.get("time"), p.get("energy"), p.get("maxEnergy"),
               money, seeds, crops, used, wood))
    log("问模型：%s" % text)
    return await asyncio.to_thread(planner.ask, text)


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


async def state_text(b, snap):
    d = snap.get("date") or {}
    p = snap.get("player") or {}
    inv = snap.get("inventory") or []
    seeds = sum(i.get("count", 0) for i in inv if "种子" in (i.get("name") or ""))
    wood = sum(i.get("count", 0) for i in inv if (i.get("name") or "") == "木材")
    used = sum(1 for i in inv if (i.get("count") or 0) > 0)
    cap = int(p.get("maxItems") or 12)
    ents = snap.get("entities") or []
    crops = sum(1 for e in ents if e.get("crop"))
    ready = sum(1 for e in ents if e.get("harvestable"))
    dry = sum(1 for e in ents if e.get("kind") == "soil" and e.get("crop") and e.get("needsWater"))
    obst = sum(1 for e in ents if (e.get("name") or "") in ("Tree", "树", "石头", "杂草", "嫩枝", "Grass"))
    _can = water_can(snap) or {}
    money = None
    try:
        md = await b.request("menudump", {})
        money = md.get("money")
    except Exception:
        pass
    return ("第%s天 %s %s点 | 体力 %s/%s | 钱 %s | 种子 %s | 作物 %s 株（成熟 %s，缺水 %s）| 背包 %s/36 | 木材 %s | 水壶 %s | 附近障碍 %s | 位置 %s"
            % (d.get("day"), d.get("season"), snap.get("time"), int(p.get("energy") or 0), int(p.get("maxEnergy") or 0),
               money, seeds, crops, ready, dry, used, wood, _can.get("water"), obst, snap.get("location")))


async def decide_next(b, f, snap):
    """把状态发给模型，让它挑下一个动作。"""
    d = snap.get("date") or {}
    p = snap.get("player") or {}
    inv = snap.get("inventory") or []
    seeds = sum(i.get("count", 0) for i in inv if "种子" in (i.get("name") or ""))
    wood = sum(i.get("count", 0) for i in inv if (i.get("name") or "") == "木材")
    used = sum(1 for i in inv if (i.get("count") or 0) > 0)
    cap = int(p.get("maxItems") or 12)
    ents = snap.get("entities") or []
    crops = sum(1 for e in ents if e.get("crop"))
    ready = sum(1 for e in ents if e.get("harvestable"))
    dry = sum(1 for e in ents if e.get("kind") == "soil" and e.get("crop") and e.get("needsWater"))
    obst = sum(1 for e in ents if (e.get("name") or "") in ("Tree", "树", "石头", "杂草", "嫩枝", "Grass"))
    _can = water_can(snap) or {}
    money = None
    try:
        md = await b.request("menudump", {})
        money = md.get("money")
    except Exception:
        pass
    text = ("第%s天 %s %s点 | 体力 %s/%s | 钱 %s | 种子 %s | 作物 %s 株（成熟 %s，缺水 %s）| 背包 %s/36 | 木材 %s | 水壶 %s | 附近障碍 %s | 位置 %s"
            % (d.get("day"), d.get("season"), snap.get("time"), p.get("energy"), p.get("maxEnergy"),
               money, seeds, crops, ready, dry, used, wood, _can.get("water"), obst, snap.get("location")))
    return await asyncio.to_thread(planner.next_action, text)


async def run_action(b, f, snap, act):
    """执行模型挑的动作。"""
    snap = await f.ensure_free(snap)
    a = (act.get("action") or "").lower()
    arg = act.get("arg") or {}
    if a == "harvest":
        return await f.harvest(snap)
    if a == "clear":
        return await f.clear_obstacles(snap, limit=int(arg.get("limit") or 8), min_energy=int(arg.get("min_energy") or 70))
    if a == "till_plant":
        snap = await f.till(snap, limit=int(arg.get("limit") or 4))
        return await f.plant(snap)
    if a == "water":
        return await f.water_crops(snap)
    if a == "chop":
        return await f.chop_trees(snap, want_wood=100, max_swings=10)
    if a == "buy_seeds":
        from shop import buy_seeds
        return await buy_seeds(b, f, snap, want=int(arg.get("qty") or 20))
    if a == "sell":
        return await stash_items(b, snap)
    if a == "sleep":
        await f.sleep(snap)
        return await b.snapshot(12)
    return snap


async def rule_work(b, f, snap):
    """模型没回话时的老规则。"""
    snap = await f.clear_menu(snap)
    snap = await f.go_outside(snap)
    snap = await f.harvest(snap)
    snap = await f.clear_obstacles(snap, limit=6, min_energy=70)
    _seeds = seeds_in(snap)
    if _seeds:
        snap = await f.till(snap, limit=4)
        snap = await f.plant(snap)
    return await f.water_crops(snap)


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


async def run_once():
    ensure_dirs()
    if stop_requested():
        log("发现 STOP，退出")
        return False

    # 先处理「开游戏」：这时候桥可能还没起来
    packs = take_commands()
    for _p, _c in packs:
        if (_c.get("cmd") or "").lower() == "play":
            try:
                from launch_farm import ensure_loaded
                _ok = await ensure_loaded()
            except Exception as _e:  # noqa: BLE001
                log("开游戏出错：%s" % _e)
                _ok = False
            log("开游戏：%s" % ("成功" if _ok else "失败"))
            queue_report("星露谷开好了：" + ("进存档了" if _ok else "没进去，我再试试"))
            try:
                os.remove(_p)
            except Exception:
                pass

    b = Bridge()
    try:
        await b.__aenter__()
    except Exception as e:  # noqa: BLE001
        log("连不上游戏桥：%s" % e)
        return True

    # 跑太久就把 mod 的动作记录清零（等价于重启，但不用真重启）
    global STARTED_AT
    if _reset_h > 0 and (time.time() - STARTED_AT) > _reset_h * 3600:
        try:
            await b.reset()
            ran = (time.time() - STARTED_AT) / 3600
            STARTED_AT = time.time()
            log("动作记录已清零（已连续跑 %0.1f 小时），避免 OUTPUT_LIMIT" % ran)
        except Exception as _e:  # noqa: BLE001
            log("清零动作记录失败：%s" % _e)

    try:
        f = Farmer(b, state_path=os.path.join(DEV, "farm-state.json"), log=log)
        try:
            snap = await f.snap(16)
        except Exception as _e:
            log("读快照失败（%s），点一下屏幕试试" % _e)
            try:
                from farmer import click_game_center
                for _i in range(3):
                    click_game_center()
                    await asyncio.sleep(1.5)
            except Exception:
                pass
            snap = await f.snap(16)
        if not snap or snap.get("ok") is False:
            log("没有存档 / 桥未就绪：%s" % (snap or {}).get("reason"))
            return True

        remember(snap)
        stats = {"watered": 0, "harvested": 0}
        cmds = take_commands()
        do_sleep = False
        for path, cmd in cmds:
            c = (cmd.get("cmd") or "").lower()
            log("收到指令：%s %s" % (c, cmd.get("arg") or ""))
            try:
                if c == "status":
                    await queue_status(f, snap)
                elif c in ("water", "work"):
                    snap = await f.clear_menu(snap)
                    snap = await f.go_outside(snap)
                    stats_w = await count_watered(f, snap)
                    stats["watered"] += stats_w
                    snap = await f.snap(16)
                    _can = water_can(snap) or {}
                    queue_report("浇完了：%d 格，水壶剩 %s" % (stats_w, _can.get("water")))
                elif c == "sleep":
                    do_sleep = True
                elif c == "task":
                    _t = cmd.get("arg") or ""
                    log("收到任务：%s" % _t)
                    _st = await state_text(b, snap)
                    _plan = await asyncio.to_thread(planner.plan_task, _t, _st)
                    if not _plan:
                        queue_report("任务没想明白，先不动：%s" % _t)
                    else:
                        _done = []
                        for _s in (_plan.get("steps") or [])[:6]:
                            try:
                                snap = await run_action(b, f, snap, _s)
                                _done.append("%s %s" % (_s.get("action"), _s.get("arg") or ""))
                            except Exception as _e:  # noqa: BLE001
                                _done.append("%s 失败(%s)" % (_s.get("action"), _e))
                        _reply = _plan.get("reply") or ""
                        queue_report("任务「%s」完成 %d 步：%s。%s" % (_t, len(_done), "；".join(_done), _reply))
                        log("任务结果：%s" % _done)
                elif c == "decisions":
                    queue_report(last_decisions(5))
                elif c == "reset":
                    await b.reset()
                    STARTED_AT = time.time()
                    queue_report("动作记录已清零，接着干")
                elif c == "backup":
                    backup_saves()
            except Exception as e:  # noqa: BLE001
                log("指令 %s 出错：%s" % (c, e))
            finally:
                try:
                    os.remove(path)
                except Exception:
                    pass

        # 每天问一次模型：今天怎么安排
        global PLAN, PLAN_DAY, PLAN_REASON
        _day = (snap.get("date") or {}).get("day")
        if _day != PLAN_DAY:
            try:
                PLAN = await refresh_plan(b, f, snap) or {}
                PLAN_DAY = _day
                PLAN_REASON = str(PLAN.get("reason") or "")
                log("今日计划：%s（%s）" % (PLAN, PLAN_REASON))
            except Exception as _e:  # noqa: BLE001
                log("出计划失败：%s" % _e)
                PLAN, PLAN_DAY = {}, _day

        t = int(snap.get("time") or 0)
        _sleep_at = int(PLAN.get("sleep_at") or 2200)
        if do_sleep or t >= _sleep_at:
            before = (snap.get("date") or {}).get("day")
            log("回屋睡觉（%s 点）" % t)
            snap2 = await f.sleep(snap)
            after = ((snap2 or {}).get("date") or {}).get("day")
            if snap2 and after != before:
                backup_saves()
                try:
                    _md = await b.request("menudump", {})
                    _money = _md.get("money")
                except Exception:
                    _money = None
                _report = build_report(before, snap2, _money)
                try:
                    _rd = os.path.join(DEV, "reports")
                    os.makedirs(_rd, exist_ok=True)
                    with open(os.path.join(_rd, "day-%s.txt" % before), "w", encoding="utf-8") as _rf:
                        _rf.write(_report)
                except Exception:
                    pass
                queue_report(_report)
                log("新的一天：第 %s 天" % after)
        else:
            _used = sum(1 for i in snap.get("inventory") or [] if (i.get("count") or 0) > 0)
            if _used >= 30:
                log("背包快满了（%d/36），先整理" % _used)
                snap = await stash_items(b, snap)
            # 卡在奇怪的地图（节日/公交/临时图）就直接传送
            if (snap.get("location") or "") not in ("Farm", "FarmHouse", "Forest", "Town", "SeedShop"):
                log("卡在 %s，传送回农场" % snap.get("location"))
                try:
                    await b.request("warp", {"location": "Farm", "x": 64, "y": 15})
                    await asyncio.sleep(2.0)
                    snap = await b.snapshot(16)
                except Exception as _e:
                    log("传送失败：%s" % _e)
            # 不在农场就先回家
            if (snap.get("location") or "") not in ("Farm", "FarmHouse"):
                try:
                    from shop import _go_home
                    snap = await _go_home(b, f, snap)
                    log("先回了趟家：%s" % snap.get("location"))
                except Exception as _e:  # noqa: BLE001
                    log("回家出错：%s" % _e)
            # 种子不够就去买
            _total = sum(s.get("count", 0) for s in seeds_in(snap))
            _below = int(PLAN.get("buy_when_below") or 5)
            _want = int(PLAN.get("buy_qty") or 20)
            if _total < _below and (snap.get("location") or "") == "Farm":
                try:
                    from shop import buy_seeds
                    snap = await buy_seeds(b, f, snap, want=_want)
                    log("买完回来，种子 %d" % sum(s.get("count", 0) for s in seeds_in(snap)))
                except Exception as _e:  # noqa: BLE001
                    log("买种子出错：%s" % _e)
            snap = await f.clear_menu(snap)
            snap = await f.go_outside(snap)
            snap = await f.harvest(snap)
            stats["harvested"] += f.last_harvest
            snap = await f.clear_obstacles(snap, limit=int(PLAN.get("clear_limit") or 8), min_energy=int(PLAN.get("clear_min_energy") or 70))
            _wood = sum(i.get("count", 0) for i in snap.get("inventory") or [] if (i.get("name") or "") == "木材")
            if PLAN.get("chop") and _wood < 100:
                snap = await f.chop_trees(snap, want_wood=100, max_swings=12)
            if not LOOT_DONE[0]:
                log("开局：先回屋翻包裹")
                try:
                    if (snap.get("location") or "") != "FarmHouse":
                        log("  人不在屋里，先走回屋")
                        snap = await f.go_inside(snap)
                    snap = await f.loot_container(snap)
                    log("  翻完背包里的种子：%s" % (seed_names(snap),))
                except Exception as _e:
                    log("  翻包裹出错：%s" % _e)
                LOOT_DONE[0] = True
            _seeds = seeds_in(snap)
            _d = snap.get("date") or {}
            _today = (_d.get("season") or "") + str(_d.get("day") or "")
            if BOUGHT_DAY[0] != _today:
                BOUGHT_DAY[0] = None
            if not _seeds:
                # 先翻箱子找种子（开局包裹就在家里的箱子里）
                for e in snap.get("entities") or []:
                    if e.get("kind") == "object" and (e.get("objType") or "") == "Chest":
                        log("先翻箱子找种子：%s" % (e.get("tile"),))
                        mv = await f.do("走到箱子旁", b.move(near=e["ref"]))
                        if mv is not None or f.dry:
                            snap = await f.chest_get(snap, e["tile"], "种子")
                            if seeds_in(snap):
                                break
            if _seeds:
                _n = sum(s.get("count", 0) for s in _seeds)
                snap = await f.till(snap, limit=min(8, _n))
                snap = await f.plant(snap)
            snap = await f.water_crops(snap)
            stats["watered"] += f.last_watered
            # 每轮让模型挑一个动作
            _dnow = snap.get("date") or {}
            _dkey = str(_dnow.get("day") or "")
            if REPORT_DAY[0] and _dkey and _dkey != REPORT_DAY[0]:
                try:
                    _rep = build_report(REPORT_DAY[0], snap, (snap.get("player") or {}).get("money"))
                    queue_report(_rep)
                    log("跨天，日报已排队（%s）" % REPORT_DAY[0])
                except Exception as _e:
                    log("日报生成失败：%s" % _e)
            if _dkey:
                REPORT_DAY[0] = _dkey
            fest = await is_festival(b, snap)
            energy = int((snap.get("player") or {}).get("energy") or 0)
            f._energy_low = energy <= 20
            if energy <= 12 or getattr(f, "no_stamina", False):
                log("体力见底（%d），今天提前收工睡觉" % energy)
                try:
                    snap = await f.sleep(snap)
                    f.no_stamina = False
                    return
                except Exception as _e:  # noqa: BLE001
                    log("睡觉失败：%s" % _e)
            info = festival_today(snap)
            d0 = snap.get("date") or {}
            day_key = (d0.get("season") or "") + str(d0.get("day") or "")
            tnow = int(snap.get("time") or 0)
            if info and day_key not in ATTENDED and info["start"] <= tnow < info["end"]:
                try:
                    snap = await asyncio.wait_for(festival_attend(b, f, snap, info), timeout=480)
                except asyncio.TimeoutError:
                    log("  节日流程超时，放弃并回农场")
                    ATTENDED.add(day_key)
                act = None
            else:
                act = await decide_next(b, f, snap)
            if act and (act.get("action") or "").startswith("buy"):
                _d2 = snap.get("date") or {}
                _td = (_d2.get("season") or "") + str(_d2.get("day") or "")
                _t = int(snap.get("time") or 0)
                if seeds_in(snap):
                    log("背包里还有种子，不用去店里")
                    act = None
                elif _t >= 1600:
                    log("已经 %d 点，店里快关门了，不去了" % _t)
                    act = None
                elif BOUGHT_DAY[0] == _td:
                    log("今天已经买过一次，不再去店里")
                    act = None
                else:
                    BOUGHT_DAY[0] = _td
            if fest and act and (act.get("action") or "").startswith("buy"):
                log("今天是节日，镇上全是活动，先不去买种子")
                act = {"action": "water", "arg": {}, "reason": "节日不去镇上"}
            if act:
                log("模型决定：%s %s（%s）" % (act.get("action"), act.get("arg") or "", act.get("reason") or ""))
                try:
                    write_status(snap, None, act.get("action"))
                except Exception:
                    pass
                record_decision(act, snap)
                try:
                    snap = await run_action(b, f, snap, act)
                except Exception as _e:  # noqa: BLE001
                    log("执行 %s 出错：%s" % (act.get("action"), _e))
            else:
                # 模型没回话就走老规则
                snap = await rule_work(b, f, snap)
            await ensure_chest(b, snap)
            snap = await stash_items(b, snap)
            _d = (snap.get("date") or {}).get("day")
            if DAY_STATS.get("day") != _d:
                DAY_STATS.update({"day": _d, "watered": 0, "harvested": 0, "planted": 0})
            DAY_STATS["watered"] += f.last_watered
            DAY_STATS["harvested"] += f.last_harvest
            DAY_STATS["planted"] += f.last_planted
            if f.last_harvest or f.last_cleared or f.last_till or f.last_planted or f.last_watered:
                log("这轮：收 %d，清 %d，翻 %d，种 %d，浇 %d，种子 %d" % (f.last_harvest, f.last_cleared, f.last_till, f.last_planted, f.last_watered, sum(s.get("count", 0) for s in seeds_in(snap))))
    finally:
        try:
            await b.__aexit__(None, None, None)
        except Exception:
            pass
    return True


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
        if nm in KEEP_NAMES or "种子" in nm or nm in ("木材", "硬木"):
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
    a = ap.parse_args()
    ensure_dirs()
    if a.once or not a.loop:
        ok = await run_once()
        return 0 if ok else 1

    log("农场服务启动")
    pump = asyncio.create_task(pump_loop())
    while not stop_requested():
        try:
            await run_once()
        except Exception as e:  # noqa: BLE001
            log("这轮出错：%s" % e)
        # 分段睡，这样 STOP 文件能被及时发现
        for _ in range(6):
            if stop_requested():
                break
            await asyncio.sleep(5)
    try:
        pump.cancel()
    except Exception:
        pass
    log("农场服务退出")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
