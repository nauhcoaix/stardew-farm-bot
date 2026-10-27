"""给记忆库批量补向量（bge-small-zh ONNX）。

用法：
  python scripts/backfill-embeddings.py --dry-run
  python scripts/backfill-embeddings.py --apply
  python scripts/backfill-embeddings.py --apply --limit 20000

只给"有文本内容"的事件算向量；observation/skill_call/movement 这类结构化记录跳过。
"""
import argparse
import os
import sys
import time

BOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot")
sys.path.insert(0, BOT)
DEV_DEFAULT = r"D:\1\steamapps\common\Stardew Valley\_dev"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=os.environ.get("FARM_DATA") or DEV_DEFAULT)
    ap.add_argument("--limit", type=int, default=50000)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    import embeddings
    info = embeddings.self_test_dir()
    print("模型: %s  可用=%s dim=%s" % (info["dir"], info["available"], info["dim"]))
    if not info["available"]:
        print("模型不可用，先跑 scripts/fetch-embedding-model.py")
        return 1

    from memory_store import MemoryStore
    db = os.path.join(a.data_dir, "memory.sqlite3")
    store = MemoryStore(db)
    total = store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    have = store.db.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    print("事件 %d 条，已有向量 %d 条，待补 %d 条" % (total, have, max(0, total - have)))

    if not a.apply:
        print("干跑结束（加 --apply 才会计算并写入）")
        store.close()
        return 0

    t0 = time.time()
    done = 0
    while done < a.limit:
        n = store.embed_events(limit=500, batch=32)
        if not n:
            break
        done += n
        print("  已补 %d 条（%.1f 秒）" % (done, time.time() - t0), flush=True)
    size = os.path.getsize(db) / 1048576
    print("\n完成：补 %d 条，耗时 %.1f 秒，库 %.1f MB" % (done, time.time() - t0, size))
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
