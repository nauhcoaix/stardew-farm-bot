"""A/B/C 的单元测试：游戏日记账、生长进度事实、知识规则。"""
import os
import tempfile
import unittest
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "bot"))

import knowledge  # noqa: E402
from memory_store import MemoryStore  # noqa: E402


def snap(day=1, season="spring", year=1, entities=None, inv=None, location="Farm"):
    return {"instanceId": "g1", "saveGeneration": 1, "location": location,
            "date": {"year": year, "season": season, "day": day},
            "player": {"position": {"x": 64, "y": 17}, "energy": 200, "maxItems": 12},
            "inventory": inv or [], "entities": entities or []}


def crop_entity(x, y, phase, name="防风草"):
    return {"kind": "soil", "tile": {"x": x, "y": y}, "tilled": True, "watered": True,
            "crop": "24", "cropPhase": phase, "name": name}


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.tmp.close()
        self.store = MemoryStore(self.tmp.name)
        self.store.current = snap()

    def tearDown(self):
        self.store.close()
        os.unlink(self.tmp.name)

    def test_game_day_arithmetic(self):
        self.assertEqual(self.store.game_day(snap(day=1)), 0)
        self.assertEqual(self.store.game_day(snap(day=28)), 27)
        self.assertEqual(self.store.game_day(snap(day=1, season="summer")), 28)
        self.assertEqual(self.store.game_day(snap(day=1, year=2)), 112)

    def test_planted_keeps_planting_day_and_phase(self):
        self.store.record_planted(snap(day=2), (62, 18), crop="24")
        self.store.sync_facts(snap(day=2, entities=[crop_entity(62, 18, 1)]))
        row = self.store.db.execute(
            "SELECT planted_day, phase_last_seen, crop_name FROM planted").fetchone()
        self.assertEqual(row["planted_day"], 1)      # day=2 -> 第 1 天（0 基）
        self.assertEqual(row["phase_last_seen"], 1)
        self.assertEqual(row["crop_name"], "防风草")

    def test_watering_is_per_game_day(self):
        self.store.record_planted(snap(day=2), (62, 18), crop="24")
        self.store.mark_watered(snap(day=2), [(62, 18)])
        self.assertEqual(self.store.unwatered_planted(snap(day=2)), [],
                         "当天浇过就不算待浇")
        self.assertEqual(len(self.store.unwatered_planted(snap(day=3))), 1,
                         "第二天又要浇（游戏日不同）")

    def test_progress_counts_growing_ready_tomorrow_and_overdue(self):
        # 第 1 天种下防风草(4 天熟)
        self.store.record_planted(snap(day=1), (62, 18), crop="24")
        self.store.sync_facts(snap(day=1, entities=[crop_entity(62, 18, 1)]))
        # 第 4 天：已经长了 3 天 -> 还差 1 天（明天熟）
        self.store.sync_facts(snap(day=4, entities=[crop_entity(62, 18, 3)]))
        f = self.store.facts(snap(day=4))
        self.assertEqual(f["crops_growing"], 1)
        self.assertEqual(f["crops_ready_tomorrow"], 1, f)
        self.assertEqual(f["crops_overdue"], 0, f)
        # 第 6 天还没熟 -> overdue
        self.store.sync_facts(snap(day=6, entities=[crop_entity(62, 18, 3)]))
        f = self.store.facts(snap(day=6))
        self.assertEqual(f["crops_overdue"], 1, f)

    def test_thirsty_flag(self):
        self.store.record_planted(snap(day=1), (62, 18), crop="24")
        self.store.sync_facts(snap(day=2, entities=[crop_entity(62, 18, 2)]))
        self.assertEqual(self.store.facts(snap(day=2))["crops_thirsty"], 1)
        self.store.mark_watered(snap(day=2), [(62, 18)])
        self.store.sync_facts(snap(day=2, entities=[crop_entity(62, 18, 2)]))
        self.assertEqual(self.store.facts(snap(day=2))["crops_thirsty"], 0)

    def test_crop_progress_reports_days_left(self):
        self.store.record_planted(snap(day=1), (62, 18), crop="24")
        self.store.sync_facts(snap(day=3, entities=[crop_entity(62, 18, 2)]))
        item = self.store.crop_progress(snap(day=3))[0]
        self.assertEqual(item["days_total"], 4)
        self.assertEqual(item["grown"], 2)
        self.assertEqual(item["left"], 2)


class KnowledgeRuleTests(unittest.TestCase):
    def test_water_growth_rule_present(self):
        text = "\n".join(knowledge.digest())
        self.assertIn("不浇水", text)

    def test_crop_lines_mention_watering(self):
        text = "\n".join(knowledge.digest())
        self.assertIn("每天浇水才长", text)


if __name__ == "__main__":
    unittest.main()
