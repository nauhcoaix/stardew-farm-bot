"""查看统一记忆库或导出同一格式的JSONL，不操纵游戏。"""
import argparse
import json
from pathlib import Path
import sqlite3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("stats", "recall", "export"))
    parser.add_argument("--db", type=Path, default=Path(__file__).with_name("memory.sqlite3"))
    parser.add_argument("--scope", help="stats列出的save_id")
    parser.add_argument("--kind")
    parser.add_argument("--query", default="")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if not args.db.exists():
        parser.error("记忆库尚未创建：" + str(args.db))
    with sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True) as db:
        if args.command == "stats":
            result = [{"save_id": r[0], "kind": r[1], "count": r[2]} for r in db.execute(
                "SELECT save_id,kind,COUNT(*) FROM events GROUP BY save_id,kind ORDER BY save_id,kind")]
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        scopes = [r[0] for r in db.execute("SELECT DISTINCT save_id FROM events")]
        scope = args.scope or (scopes[0] if len(scopes) == 1 else None)
        if not scope:
            parser.error("库中有多个存档，请先stats并用--scope选择")
        clauses, values = ["save_id=?"], [scope]
        if args.kind:
            clauses.append("kind=?")
            values.append(args.kind)
        if args.query:
            clauses.append("instr(search,?)>0")
            values.append(args.query)
        sql = "SELECT body FROM events WHERE " + " AND ".join(clauses) + " ORDER BY ts DESC,rowid DESC"
        if args.command == "export":
            if not args.out:
                parser.error("export需要--out路径")
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open("x", encoding="utf-8") as f:
                for row in db.execute(sql, values):
                    f.write(row[0] + "\n")
            print("已导出：" + str(args.out))
        else:
            values.append(max(1, min(100, args.limit)))
            rows = [json.loads(r[0]) for r in db.execute(sql + " LIMIT ?", values)]
            print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
