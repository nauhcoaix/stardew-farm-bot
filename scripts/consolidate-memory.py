"""L4 经验压缩：把当天的反思/任务/计划压成几条可执行教训，写进长期记忆。

用法：
  python scripts/consolidate-memory.py --dry-run      # 只列出候选，不调模型
  python scripts/consolidate-memory.py --apply        # 调模型压缩并写入
  python scripts/consolidate-memory.py --apply --hours 12 --max-lessons 5

教训写进 memory.sqlite3 的 kind='lesson'，规划时永远先看这几条。
"""
import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime

DEV_DEFAULT = r"D:\1\steamapps\common\Stardew Valley\_dev"
BOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot")
sys.path.insert(0, BOT)

PROMPT = """你是星露谷农场机器人的经验整理员。

下面是我今天运行时的记录（反思、玩家指令、计划目标）。请压缩成最多 {n} 条「可执行教训」。

要求：
1. 每条一行，必须是具体的：包含坐标、动作名、条件（例如“没有种子时”“离农舍超过12格时”）
2. 只写反复出现或有因果关系的规律，不要写一次性的琐事
3. 不要编造没出现过的坐标或物品
4. 直接输出 JSON：{{"lessons": ["...", "..."]}}，不要其他文字

记录：
{rows}
"""


def load_events(db, hours, limit=400):
    import sqlite3
    con = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT kind, ts, location, body FROM events "
        "WHERE kind IN ('reflection','instruction','plan','lesson','resource') AND ts > strftime('%s','now') - ? "
        "ORDER BY ts DESC LIMIT ?", (int(hours * 3600), limit)).fetchall()
    con.close()
    out = []
    for r in rows:
        try:
            data = json.loads(r["body"]).get("data") or {}
        except Exception:
            continue
        text = data.get("text") or data.get("goal") or data.get("reply") or ""
        if not str(text).strip():
            continue
        out.append("(%s) %s" % (r["kind"], str(text)[:160]))
    # 去重，保留顺序
    seen, uniq = set(), []
    for line in out:
        key = line[6:]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(line)
    return uniq


def load_cfg(data_dir):
    import glob
    for path in (os.environ.get("FARM_CFG"),
                 os.path.join(data_dir, "planner-config.json"),
                 os.path.join(BOT, "planner-config.json")):
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8-sig") as f:
                return json.load(f)
    for path in glob.glob(os.path.join(BOT, "..", "**", "planner-config.json"), recursive=True):
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    raise SystemExit("找不到 planner-config.json")


def call_llm(cfg, prompt, timeout=120):
    base = str(cfg.get("api_base") or "https://api.deepseek.com").rstrip("/")
    if not base.endswith("/chat/completions"):
        base = base + "/chat/completions"
    body = json.dumps({
        "model": cfg.get("model") or "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 900,
    }).encode("utf-8")
    req = urllib.request.Request(base, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer %s" % cfg.get("api_key", ""),
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def parse_lessons(text):
    text = str(text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            items = obj.get("lessons") or []
            if isinstance(items, list):
                return [str(x).strip() for x in items if str(x).strip()]
        except Exception:
            pass
    out = []
    for line in text.splitlines():
        line = line.strip(" -•\t")
        if line and not line.startswith("{"):
            out.append(line)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=os.environ.get("FARM_DATA") or DEV_DEFAULT)
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--max-lessons", type=int, default=5)
    ap.add_argument("--keep", type=int, default=40, help="库里最多保留多少条 lesson")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if not a.dry_run and not a.apply:
        a.dry_run = True

    db = os.path.join(a.data_dir, "memory.sqlite3")
    if not os.path.exists(db):
        raise SystemExit("找不到 %s" % db)

    rows = load_events(db, a.hours)
    print("近 %.0f 小时的相关记录：%d 条（去重后）" % (a.hours, len(rows)))
    for line in rows[:12]:
        print("  " + line[:100])
    if a.dry_run:
        print("\n干跑结束（加 --apply 才会调模型并写入）")
        return 0
    if not rows:
        print("没有可压缩的记录")
        return 0

    cfg = load_cfg(a.data_dir)
    prompt = PROMPT.format(n=a.max_lessons, rows="\n".join(rows[:150]))
    print("\n调模型压缩中…")
    try:
        text = call_llm(cfg, prompt)
    except Exception as exc:
        print("调用失败：%s" % exc)
        return 1
    lessons = parse_lessons(text)[:a.max_lessons]
    if not lessons:
        print("模型没有给出教训：%s" % str(text)[:200])
        return 1

    from memory_store import MemoryStore
    store = MemoryStore(db)
    store.current = {"instanceId": "consolidate", "saveGeneration": 0, "location": "Farm"}
    existing = set(store.lessons(50, snap=store.current))
    added = 0
    for lesson in lessons:
        if lesson in existing:
            continue
        store.add_lesson(lesson, snap=store.current)
        added += 1
        print("  + %s" % lesson)
    # 只保留最近 N 条教训
    keep_ids = [r[0] for r in store.db.execute(
        "SELECT id FROM events WHERE kind='lesson' ORDER BY ts DESC LIMIT ?", (a.keep,)).fetchall()]
    if keep_ids:
        marks = ",".join("?" for _ in keep_ids)
        with store.db:
            store.db.execute("DELETE FROM events WHERE kind='lesson' AND id NOT IN (%s)" % marks, keep_ids)
    store.close()
    print("\n写入 %d 条新教训（共保留最近 %d 条）" % (added, a.keep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
