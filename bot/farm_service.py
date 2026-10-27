"""自主游戏服务：模型选择能力，单执行器行动，统一记忆保留证据。"""
import argparse
import asyncio
import datetime
import glob
import json
import os
import shutil
import sys
import time
import zipfile

CODE = os.path.dirname(os.path.abspath(__file__))
DEV = os.environ.get("FARM_DATA") or CODE
GAME = os.path.dirname(DEV)
INBOX = os.path.join(DEV, "inbox")
OUTBOX = os.path.join(DEV, "outbox")
SENT = os.path.join(OUTBOX, "sent")
BACKUPS = os.path.join(DEV, "backups")
STOP_FILE = os.path.join(DEV, "STOP")
STATUS_FILE = os.path.join(DEV, "status.json")
DECISIONS_FILE = os.path.join(DEV, "decisions.jsonl")
SAVES = os.path.join(os.environ.get("APPDATA", ""), "StardewValley", "Saves")
REPORT_CHAT = os.environ.get("FARM_REPORT_CHAT") or "me"
sys.path.insert(0, CODE)
from farm_client import Bridge
from observed_bridge import ObservedBridge
from farmer import Farmer, water_can, seeds_in, ents
from work_planner import WorkPlanner, context_key
from agent_skills import SkillRuntime
from skill_catalog import available
from memory_store import save_key
import planner

SEASON_CN = {"spring": "春", "summer": "夏", "fall": "秋", "winter": "冬"}
DAY_STATS = {}
PLAN, PLAN_DAY, PLAN_REASON = {}, None, ""
LAST_DECISION_REPORT = 0
LAST_ACTION = ""
LAST_MONEY = None
LAST_SNAP = {}
STARTED_AT = time.time()
_last_bridge_err, _last_bridge_at = "", 0
ACTION_CN = {"memory": "记忆", "explore": "探索", "move": "走路", "interact": "交互", "fish": "钓鱼"}
# 旧外部脚本的兼容标记，不再触发自动开包裹/买种子。
BOUGHT_DAY, LOOT_DONE = [None], [False]


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
        row["planner"] = {"thinking": brain.thinking, "queued_steps": brain.queued,
                          "objective": brain.intent, "goal": brain.goal}
        row["memory"] = {"database": brain.memory.path, "save_id": save_key(snap), "schema_version": 1}
        runtime = getattr(brain, "runtime", None)
        if runtime:
            row["skill_result"] = {k: runtime.last_result.get(k) for k in ("skill", "status", "progress", "detail")}
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


def planning_text(snap, history=None, runtime=None, objective=""):
    return json.dumps(runtime.context(snap, objective) if runtime else {"snapshot": snap}, ensure_ascii=False)


def action_ready(snap, act, plan=None, history=None):
    return available(act.get("action"), snap, act.get("arg"))[0]


def local_action(snap, brain):
    """兼容入口：所有新行动都由模型或玩家选择，不再注入本地工作日程。"""
    return None


def runtime_for(brain):
    if not hasattr(brain, "runtime"):
        brain.runtime = SkillRuntime(brain.memory, DEV, log, interrupted=lambda: stop_requested() or any(
            (c.get("cmd") or "").lower() in ("task", "water", "work", "sleep", "reset", "play") for _, c in take_commands()))
    return brain.runtime


async def run_once(brain=None):
    owned = brain is None
    brain = brain or WorkPlanner(planner.plan_work, log, queue_report,
                                history_path=os.path.join(DEV, "action-history.json"))
    try:
        return await _run_once(brain)
    finally:
        if owned:
            await runtime_for(brain).close()
            brain.close()


async def _run_once(brain):
    global PLAN_REASON, _last_bridge_err, _last_bridge_at
    ensure_dirs()
    runtime = runtime_for(brain)
    if stop_requested():
        await runtime.close()
        return False
    if await runtime.fishing.busy():
        write_status(LAST_SNAP, action="等待钓鱼释放控制或处理奖励菜单", brain=brain)
        return False
    for path, command in take_commands():
        if (command.get("cmd") or "").lower() == "play":
            from launch_farm import ensure_loaded
            await ensure_loaded()
            os.remove(path)
    b = ObservedBridge(Bridge(), brain.memory)
    try:
        await b.__aenter__()
    except Exception as exc:
        now, message = time.time(), str(exc)
        if message != _last_bridge_err or now - _last_bridge_at > 60:
            log("连不上游戏桥：" + message)
            _last_bridge_err, _last_bridge_at = message, now
        return False
    try:
        f = Farmer(b, state_path=os.path.join(DEV, "farm-state.json"), log=log, history=brain.history)
        try:
            snap = await b.snapshot(16)
        except Exception as exc:
            log("暂不能观察游戏：%s；等待现场恢复，不盲点屏幕" % type(exc).__name__)
            return False
        brain.observe(snap)
        remember(snap)
        for path, command in take_commands():
            name = (command.get("cmd") or "").lower()
            try:
                if name == "status":
                    queue_report(summary_text(snap, DAY_STATS))
                elif name == "decisions":
                    queue_report(last_decisions(5))
                elif name == "task":
                    brain.submit_task(str(command.get("arg") or "自主选择下一步行动"))
                elif name == "water":
                    brain.set_steps([{"action": "water", "arg": {"limit": 24}}], mission="给观察到的缺水作物浇水，必要时先补水")
                elif name in ("work", "sleep"):
                    brain.submit_task("找到床并回屋睡觉" if name == "sleep" else str(command.get("arg") or "依据记忆和现场自主行动"))
                elif name == "reset":
                    await b.reset()
                    brain.interrupt()
                    snap = await b.snapshot(16)
                elif name == "backup":
                    backup_saves()
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
        brain.poll(snap, planning_text(snap, runtime=runtime, objective=brain.intent))
        PLAN_REASON = brain.goal
        if stop_requested():
            return False
        action = brain.take_action(lambda a: runtime.ready(a, snap))
        if action is None:
            write_status(snap, action="模型选择技能中" if brain.thinking else "等待模型或新的可执行选择", brain=brain)
            return False
        before = snap
        record_decision(action, snap)
        write_status(snap, action=action["action"], brain=brain)
        snap, result = await runtime.execute(action, b, f, snap)
        if result.get("remaining_arg") is not None:
            action = dict(action, arg=result["remaining_arg"])
        brain.complete(action, result.get("progress", False), repeat=result.get("repeat", False))
        remember(snap)
        write_status(snap, action=action["action"] + "：" + result["status"], brain=brain)
        if context_key(before) != context_key(snap) and before.get("date") != snap.get("date"):
            backup_saves()
        return bool(result.get("progress"))
    finally:
        await b.__aexit__(None, None, None)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    ensure_dirs()
    brain = WorkPlanner(planner.plan_work, log, queue_report, history_path=os.path.join(DEV, "action-history.json"))
    pump = None
    try:
        if args.once or not args.loop:
            await run_once(brain)
            return 0
        log("游戏自主服务启动：模型选技能、统一记忆、现场探索")
        pump = asyncio.create_task(pump_loop())
        while not stop_requested():
            try:
                progressed = await run_once(brain)
            except Exception as exc:
                log("本轮受阻：%s" % exc)
                progressed = False
            if not stop_requested():
                await asyncio.sleep(0.1 if progressed else 1.0)
    finally:
        if hasattr(brain, "runtime"):
            await brain.runtime.close()
        brain.close()
        if pump:
            pump.cancel()
            try:
                await pump
            except asyncio.CancelledError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
