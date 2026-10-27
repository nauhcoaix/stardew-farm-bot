import sys, tempfile, os, unittest
from pathlib import Path
BASE = Path(r"D:\code\stardew-farm-bot")
sys.path.insert(0, str(BASE / "bot"))
from memory_store import MemoryStore, target_id
from agent_skills import SkillRuntime


def snap_with(tiles):
    base = {"instanceId":"z","saveGeneration":1,"location":"Farm",
            "date":{"year":1,"season":"spring","day":2},
            "player":{"position":{"x":64,"y":17},"energy":200,"canMove":True},
            "inventory":[{"slot":0,"tool":"Scythe"}],
            "entities":[{"kind":"door","name":"农舍入口","tile":{"x":64,"y":14}}]}
    for i, (x, y) in enumerate(tiles):
        base["entities"].append({"kind":"object","name":"杂草","tile":{"x":x,"y":y},"ref":"r%d" % i})
    return base


class ZoneAutoExpandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.tmp.close()
        self.store = MemoryStore(self.tmp.name)
        self.rt = SkillRuntime(self.store, ".", log=lambda s: None)

    def tearDown(self):
        self.store.close()
        os.unlink(self.tmp.name)

    def _run(self, x, y):
        snap = snap_with([(x, y)])
        e = snap["entities"][1]
        self.rt._enforce_zone("clear", {"target": target_id("Farm", e)}, snap)
        return getattr(self.rt, "_zone_radius", None)

    def test_slightly_beyond_expands(self):
        self.assertIsNotNone(self._run(58, 21), "13 格应自动扩圈放行")
        self.assertEqual(self.rt._zone_radius, 13)

    def test_boundary_step_expands(self):
        self._run(60, 24)          # 14 格
        self.assertEqual(self.rt._zone_radius, 14)

    def test_far_target_rejected(self):
        with self.assertRaises(ValueError):
            self._run(40, 20)      # 30 格


if __name__ == "__main__":
    unittest.main()