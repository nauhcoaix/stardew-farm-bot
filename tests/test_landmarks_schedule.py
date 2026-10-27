"""地标地图 + 日程表 的单元测试。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "bot"))

from memory_store import MemoryStore  # noqa: E402


def snap(day=1, entities=None, inv=None, pos=(64, 17), location="Farm"):
    return {"instanceId": "m1", "saveGeneration": 1, "location": location,
            "date": {"year": 1, "season": "spring", "day": day},
            "player": {"position": {"x": pos[0], "y": pos[1]}, "energy": 200, "maxItems": 12},
            "inventory": inv or [], "entities": entities or []}


def obstacle(x, y, name="杂草"):
    return {"kind": "object", "name": name, "tile": {"x": x, "y": y}}


class LandmarkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.tmp.close()
        self.store = MemoryStore(self.tmp.name)
        self.store.current = snap()

    def tearDown(self):
        self.store.close()
        os.unlink(self.tmp.name)

    def test_landmarks_persist_after_leaving(self):
        self.store.update_landmarks(snap(entities=[obstacle(60, 20), obstacle(61, 20)]))
        away = snap(location="Town", entities=[])
        self.store.sync_facts(away)
        # 回到农场但视野里不含那些格子 -> 依然记得
        self.store.sync_facts(snap(pos=(40, 40), entities=[]))
        items = self.store.landmarks(snap(pos=(40, 40)), kind="obstacle")
        self.assertEqual(len(items), 2, items)

    def test_landmark_marked_gone_only_when_seen_empty_nearby(self):
        self.store.update_landmarks(snap(entities=[obstacle(63, 18)]))
        # 站在旁边（距离 1）但视野里没有 -> 判定消失
        self.store.update_landmarks(snap(pos=(64, 18), entities=[]))
        self.assertEqual(self.store.landmarks(snap(pos=(64, 18)), kind="obstacle"), [])
        # 老记录仍在库（gone=1）
        all_items = self.store.landmarks(snap(pos=(64, 18)), kind="obstacle", only_alive=False)
        self.assertEqual(len(all_items), 1)

    def test_far_absence_does_not_kill_landmark(self):
        self.store.update_landmarks(snap(entities=[obstacle(20, 40)]))
        # 站在远处看不到它，不该标 gone
        self.store.update_landmarks(snap(pos=(70, 10), entities=[]))
        self.assertEqual(len(self.store.landmarks(snap(pos=(70, 10)), kind="obstacle")), 1)

    def test_digest_and_facts(self):
        self.store.update_landmarks(snap(entities=[
            obstacle(60, 20), obstacle(61, 20),
            {"kind": "object", "name": "水源", "tile": {"x": 62, "y": 20}, "refillable": True},
            {"kind": "object", "name": "箱子", "tile": {"x": 63, "y": 20}, "objType": "Chest"},
        ]))
        far = snap(pos=(40, 40), entities=[])
        self.store.sync_facts(far)
        f = self.store.facts(far)
        self.assertEqual(f["obstacles_known"], 2)
        self.assertEqual(f["water_known"], 1)
        self.assertEqual(f["chests_known"], 1)
        digest = "\n".join(self.store.landmark_digest(far))
        self.assertIn("障碍 2 个", digest)
        self.assertIn("水源 1 个", digest)


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.tmp.close()
        self.store = MemoryStore(self.tmp.name)
        self.store.current = snap()

    def tearDown(self):
        self.store.close()
        os.unlink(self.tmp.name)

    def test_schedule_built_from_facts(self):
        s = snap(entities=[{"kind": "soil", "tile": {"x": 62, "y": 18}, "tilled": True,
                            "watered": False, "crop": "24", "cropPhase": 2, "name": "防风草"}])
        self.store.record_planted(s, (62, 18), crop="24")
        self.store.sync_facts(s)
        stages = self.store.build_schedule(s)
        texts = [x["text"] for x in stages]
        self.assertTrue(any("浇" in t for t in texts), texts)
        self.assertLessEqual(len(stages), 5, "一天最多 5 个阶段")

    def test_schedule_blocked_when_seeds_missing(self):
        self.store.sync_facts(snap(inv=[]))
        self.store.build_schedule(snap(inv=[]))
        self.store.validate_schedule(snap(inv=[]))
        stages = self.store.schedule(snap(inv=[]))
        buy = [x for x in stages if "买" in x["text"]]
        self.assertTrue(buy, stages)
        self.assertEqual(buy[0]["status"], "pending", "买种子不该被拦")

    def test_auto_advance_when_watering_done(self):
        s = snap(entities=[{"kind": "soil", "tile": {"x": 62, "y": 18}, "tilled": True,
                            "watered": False, "crop": "24", "cropPhase": 2, "name": "防风草"}])
        self.store.record_planted(s, (62, 18), crop="24")
        self.store.sync_facts(s)
        self.store.build_schedule(s)
        cur = self.store.current_stage(s)
        self.assertIn("浇", cur["text"])
        # 浇完水后自动推进
        self.store.mark_watered(s, [(62, 18)])
        self.store.sync_facts(s)
        moved = self.store.auto_advance_schedule(s)
        self.assertIsNotNone(moved, "浇完水应该自动进入下一阶段")
        nxt = self.store.current_stage(s)
        self.assertNotEqual(nxt["text"], cur["text"])

    def test_schedule_is_per_game_day(self):
        self.store.sync_facts(snap(day=1))
        a = self.store.build_schedule(snap(day=1))
        self.store.sync_facts(snap(day=2))
        self.store.build_schedule(snap(day=2))
        d1 = self.store.game_day(snap(day=1))
        d2 = self.store.game_day(snap(day=2))
        n1 = self.store.db.execute("SELECT COUNT(*) FROM schedule WHERE day=?", (d1,)).fetchone()[0]
        n2 = self.store.db.execute("SELECT COUNT(*) FROM schedule WHERE day=?", (d2,)).fetchone()[0]
        self.assertGreaterEqual(n1, 1)
        self.assertGreaterEqual(n2, 1, "第二天要有自己的一份日程")
        self.assertEqual(len(a), n1)


if __name__ == "__main__":
    unittest.main()
