"""状态事实 / 任务板 / 前置条件 的单元测试（记忆层解决“没种子还锄地”）。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "bot"))

import skill_catalog  # noqa: E402
from memory_store import MemoryStore  # noqa: E402

SNAP = {"instanceId": "v1", "saveGeneration": 1, "location": "Farm",
        "player": {"position": {"x": 64, "y": 17}, "energy": 200, "maxItems": 12},
        "date": {"day": 1, "season": "spring"}, "time": 900,
        "inventory": [], "entities": []}


def snap_with(seeds=0, tilled=0, dry=0, ready=0):
    inv = ([{"slot": 0, "name": "防风草种子", "count": seeds}] if seeds else []) + \
          [{"slot": 1, "tool": "Hoe", "name": "锄头"}]
    ents = [{"kind": "door", "name": "农舍入口", "tile": {"x": 64, "y": 14}}]
    for i in range(tilled):
        ents.append({"kind": "soil", "name": "土壤", "tile": {"x": 62 + i, "y": 18},
                     "tilled": True, "watered": False})
    for i in range(dry):
        ents.append({"kind": "soil", "name": "土壤", "tile": {"x": 60 + i, "y": 19},
                     "tilled": True, "watered": False})
    for i in range(ready):
        ents.append({"kind": "soil", "name": "土壤", "tile": {"x": 60 + i, "y": 20},
                     "tilled": True, "watered": True, "crop": "24", "ready": True})
    return dict(SNAP, inventory=inv, entities=ents)


class FactTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.tmp.close()
        self.store = MemoryStore(self.tmp.name)
        self.store.current = SNAP

    def tearDown(self):
        self.store.close()
        os.unlink(self.tmp.name)

    def test_seeds_fact_derived_from_inventory(self):
        self.store.sync_facts(snap_with(seeds=3))
        self.assertEqual(self.store.facts(SNAP)["seeds"], 3)
        self.store.sync_facts(snap_with(seeds=0))
        self.assertEqual(self.store.facts(SNAP)["seeds"], 0, "种子用完后事实必须更新")

    def test_seed_zero_writes_resource_event(self):
        self.store.sync_facts(snap_with(seeds=2))
        self.store.sync_facts(snap_with(seeds=0))
        rows = self.store.db.execute("SELECT body FROM events WHERE kind='resource'").fetchall()
        self.assertTrue(rows, "种子清零要写一条 resource 事件，供后续压缩成教训")
        self.assertIn("种子", rows[0][0])

    def test_facts_are_exported_to_context(self):
        self.store.sync_facts(snap_with(seeds=0, dry=2, ready=1))
        ctx = self.store.context(SNAP)
        self.assertEqual(ctx["state_facts"]["seeds"], 0)
        self.assertEqual(ctx["state_facts"]["crops_dry"], 2)
        self.assertEqual(ctx["state_facts"]["harvestable"], 1)


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.tmp.close()
        self.store = MemoryStore(self.tmp.name)
        self.store.current = SNAP

    def tearDown(self):
        self.store.close()
        os.unlink(self.tmp.name)

    def test_task_blocked_when_no_seeds(self):
        self.store.sync_facts(snap_with(seeds=0))
        self.store.add_task("把农舍南边 6 格种上防风草", requires={"seeds": ">=1"}, snap=SNAP)
        changed = self.store.validate_tasks(SNAP)
        task = self.store.tasks(SNAP)[0]
        self.assertEqual(task["status"], "blocked", changed)
        self.assertIn("种子", task["reason"])

    def test_task_unblocks_when_seeds_bought(self):
        self.store.sync_facts(snap_with(seeds=0))
        self.store.add_task("种下种子", requires={"seeds": ">=1"}, snap=SNAP)
        self.store.validate_tasks(SNAP)
        self.assertEqual(self.store.tasks(SNAP)[0]["status"], "blocked")
        self.store.sync_facts(snap_with(seeds=10))
        self.store.validate_tasks(SNAP)
        self.assertEqual(self.store.tasks(SNAP)[0]["status"], "pending", "买到种子后应自动解锁")

    def test_goal_with_planting_gets_requires(self):
        self.store.sync_facts(snap_with(seeds=0))
        self.store.sync_tasks_from_goal("把南边 6 格种上防风草", snap=SNAP)
        task = self.store.tasks(SNAP)[0]
        self.assertEqual(task["requires"].get("seeds"), ">=1")
        self.store.validate_tasks(SNAP)
        self.assertEqual(self.store.tasks(SNAP)[0]["status"], "blocked")

    def test_context_lists_open_tasks(self):
        self.store.sync_facts(snap_with(seeds=0))
        self.store.add_task("种下种子", requires={"seeds": ">=1"}, snap=SNAP)
        self.store.validate_tasks(SNAP)
        ctx = self.store.context(SNAP)
        self.assertEqual(len(ctx["open_tasks"]), 1)
        self.assertEqual(ctx["open_tasks"][0]["status"], "blocked")


class RequiresCatalogTests(unittest.TestCase):
    def test_till_requires_seeds(self):
        self.assertEqual(skill_catalog.requires("till").get("seeds"), ">=1")
        self.assertEqual(skill_catalog.requires("till_plant").get("seeds"), ">=1")
        self.assertEqual(skill_catalog.requires("water").get("crops_dry"), ">=1")

    def test_catalog_exposes_requires(self):
        snap = snap_with(seeds=0)
        item = next(x for x in skill_catalog.catalog(snap) if x["name"] == "till")
        self.assertIn("requires", item)
        self.assertEqual(item["requires"].get("seeds"), ">=1")


if __name__ == "__main__":
    unittest.main()
