"""跨天隔离的单元测试。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "bot"))

from memory_store import MemoryStore  # noqa: E402


def snap(day=1, season="spring", year=1):
    return {"instanceId": "d1", "saveGeneration": 1, "location": "Farm",
            "date": {"year": year, "season": season, "day": day},
            "player": {"position": {"x": 64, "y": 17}, "energy": 300, "maxItems": 24},
            "inventory": [], "entities": []}


class DayScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.tmp.close()
        self.store = MemoryStore(self.tmp.name)
        self.store.current = snap(day=1)

    def tearDown(self):
        self.store.close()
        os.unlink(self.tmp.name)

    def test_old_day_plan_not_recalled(self):
        self.store.append("plan", snap(day=1), source="model",
                          data={"goal": "去小镇买防风草种子", "steps": []})
        hit_day1 = self.store.query("买防风草种子", limit=5, snap=snap(day=1))
        self.assertTrue(hit_day1, "当天的计划应该能查回来")
        hit_day2 = self.store.query("买防风草种子", limit=5, snap=snap(day=2))
        self.assertEqual(hit_day2, [], "昨天的计划不该在第 2 天被想起")

    def test_day_change_clears_open_tasks(self):
        self.store.sync_facts(snap(day=1))
        self.store.add_task("把农舍南边种上防风草", requires={"seeds": ">=1"}, snap=snap(day=1))
        self.assertEqual(len(self.store.tasks(snap(day=1))), 1)
        self.store.sync_facts(snap(day=2))
        self.assertEqual(self.store.tasks(snap(day=2)), [], "跨天要清掉未完成任务")

    def test_day_change_writes_session_event(self):
        self.store.sync_facts(snap(day=1))
        self.store.sync_facts(snap(day=2))
        rows = self.store.db.execute("SELECT body FROM events WHERE kind='session'").fetchall()
        self.assertTrue(rows, "跨天应写一条 session 事件")
        self.assertIn("新的一天", rows[0][0])

    def test_day_brief_mentions_new_day(self):
        self.store.sync_facts(snap(day=1))
        brief1 = self.store.day_brief(snap(day=1))
        self.assertIn("第1天", brief1)
        brief2 = self.store.day_brief(snap(day=2))
        self.assertIn("新的一天", brief2)

    def test_schedule_rebuilt_for_new_day(self):
        self.store.sync_facts(snap(day=1))
        self.store.build_schedule(snap(day=1))
        self.store.sync_facts(snap(day=2))
        stages = self.store.build_schedule(snap(day=2))
        self.assertTrue(stages, "第 2 天要有自己的日程")


if __name__ == "__main__":
    unittest.main()
