"""剪记忆库噪声：保留反思/计划/路线，删掉大量重复的观察与技能记录。

用法：
  python scripts/prune-memory.py                 # 干跑，只报告
  python scripts/prune-memory.py --apply         # 真删 + VACUUM
  python scripts/prune-memory.py --apply --keep-observations 3000

数据目录取 $FARM_DATA，默认内置路径。**必须先停服务**再改库。
"""
import argparse
import os
import sqlite3

DEFAULT_DATA = r"D:\1\steamapps\common\Stardew Valley\_dev"

# 这些事件只留最近若干条（其余全是重复噪声）
PRUNABLE = {
    "observation": 3000,
    "skill_call": 3000,
    "skill": 3000,
    "movement": 1000,
    "skill_target": 100,
}
# 这些一律保留：路线/反思/计划是"学到的东西"
KEEP = ("reflection", "plan", "intent", "transition", "import", "note")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=os.environ.get("FARM_DATA") or DEFAULT_DATA)
    ap.add_argument("--apply", action="store_true", help="真的删除并压缩")
    ap.add_argument("--keep-observations", type=int, default=0, help="覆盖 observation 的保留条数")
    a = ap.parse_args()

    db = os.path.join(a.data_dir, "memory.sqlite3")
    if not os.path.exists(db):
        print("找不到 %s" % db)
        return 1

    plan = dict(PRUNABLE)
    if a.keep_observations:
        plan["observation"] = a.keep_observations

    con = sqlite3.connect(db, timeout=30)
    cur = con.cursor()
    before = os.path.getsize(db)
    print("库: %s  %.1f MB" % (db, before / 1048576))
    print("现有事件：")
    for kind, n in cur.execute("select kind, count(*) from events group by kind order by count(*) desc"):
        mark = "（保留全部）" if kind in KEEP else ("（留 %d）" % plan.get(kind, 0) if kind in plan else "（不动）")
        print("  %-14s %-7d %s" % (kind, n, mark))

    if not a.apply:
        con.close()
        print("\n干跑结束。加 --apply 才会真删。")
        return 0

    for kind, keep in plan.items():
        ids = [r[0] for r in cur.execute(
            "select id from events where kind=? order by id desc limit -1 offset ?", (kind, keep))]
        if ids:
            cur.executemany("delete from events where id=?", [(i,) for i in ids])
            print("  删除 %-12s %d" % (kind, cur.rowcount))
    cur.execute("delete from tiles where event_id not in (select id from events)")
    print("  孤儿 tile: %d" % cur.rowcount)
    con.commit()
    try:
        cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass
    con.close()

    con = sqlite3.connect(db, timeout=120)
    con.execute("VACUUM")
    con.close()

    after = os.path.getsize(db)
    print("\n体积 %.1f MB -> %.1f MB（省 %.1f MB）" % (
        before / 1048576, after / 1048576, (before - after) / 1048576))
    con = sqlite3.connect(db, timeout=30)
    cur = con.cursor()
    print("剩余事件 %d，tiles %d，links %d" % (
        cur.execute("select count(*) from events").fetchone()[0],
        cur.execute("select count(*) from tiles").fetchone()[0],
        cur.execute("select count(*) from links").fetchone()[0]))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
