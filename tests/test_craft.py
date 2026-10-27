"""craft/recipes 的单元测试（bot 侧，不依赖游戏）。"""
import os
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "bot"))

import skill_catalog  # noqa: E402

SNAP = {"instanceId": "c1", "saveGeneration": 1, "location": "Farm",
        "player": {"position": {"x": 64, "y": 17}, "energy": 200, "canMove": True},
        "date": {"year": 1, "season": "spring", "day": 1},
        "inventory": [{"slot": 0, "tool": "Axe"}], "entities": []}


class CraftCatalogTests(unittest.TestCase):
    def test_craft_step_parses(self):
        out = skill_catalog.clean_steps([{"action": "craft", "arg": {"item": "宝箱", "count": 2}}])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["arg"]["item"], "宝箱")
        self.assertEqual(out[0]["arg"]["count"], 2)

    def test_craft_count_range(self):
        self.assertEqual(skill_catalog.clean_steps([{"action": "craft", "arg": {"item": "宝箱", "count": 0}}]), [])
        self.assertEqual(skill_catalog.clean_steps([{"action": "craft", "arg": {"item": "宝箱", "count": 11}}]), [])

    def test_craft_requires_item(self):
        self.assertEqual(skill_catalog.clean_steps([{"action": "craft", "arg": {"count": 1}}]), [],
                         "没有配方名应丢弃")

    def test_recipes_step_parses(self):
        out = skill_catalog.clean_steps([{"action": "recipes", "arg": {"limit": 10}}])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["arg"]["limit"], 10)

    def test_craft_and_recipes_available_anywhere(self):
        self.assertTrue(skill_catalog.available("craft", SNAP)[0])
        self.assertTrue(skill_catalog.available("recipes", SNAP)[0])

    def test_catalog_exposes_craft(self):
        names = [x["name"] for x in skill_catalog.catalog(SNAP)]
        self.assertIn("craft", names)
        self.assertIn("recipes", names)


if __name__ == "__main__":
    unittest.main()
