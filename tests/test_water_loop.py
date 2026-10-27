"""浇水死循环防护的单元测试。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "bot"))

from memory_store import MemoryStore  # noqa: E402


def snap(entities=None, pos=(64, 17)):
    return {"instanceId": "w1", "saveGeneration": 1, "location": "Farm",
            "date": {"year": 1, "season": "spring", "day": 1},
            "player": {"position": {"x": pos[0], "y": pos[1]}, "energy": 200, "maxItems": 12},
            "inventory": [{"slot": 0, "tool": "WateringCan", "water": 30}],
            "entities": entities or []}


class ForgetPlantedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.tmp.close()
        self.store = MemoryStore(self.tmp.name)
        self.store.current = snap()

    def tearDown(self):
        self.store.close()
        os.unlink(self.tmp.name)

    def test_forget_planted_removes_row(self):
        self.store.record_planted(snap(), (57, 14), crop="24")
        self.assertEqual(len(self.store.unwatered_planted(snap())), 1)
        n = self.store.forget_planted(snap(), (57, 14))
        self.assertEqual(n, 1)
        self.assertEqual(self.store.unwatered_planted(snap()), [],
                         "清掉后不该再被当成待浇")

    def test_forget_missing_tile_is_noop(self):
        self.assertEqual(self.store.forget_planted(snap(), (99, 99)), 0)

    def test_fact_drops_after_forget(self):
        self.store.record_planted(snap(), (57, 14), crop="24")
        self.store.sync_facts(snap())
        self.assertEqual(self.store.facts(snap()).get("planted_unwatered"), 1)
        self.store.forget_planted(snap(), (57, 14))
        self.store.sync_facts(snap())
        self.assertEqual(self.store.facts(snap()).get("planted_unwatered"), 0,
                         "清掉顽固格后事实要归零，日程才能推进")


if __name__ == "__main__":
    unittest.main()
