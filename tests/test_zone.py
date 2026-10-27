"""农舍作业半径在执行层生效：圈外目标一律拒绝，自动选目标的技能只看圈内。"""
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "bot"))

import agent_skills  # noqa: E402
from skill_catalog import target_id  # noqa: E402


class _Memory:
    def blocked(self, action, snap):
        return False

    def note(self, *a, **k):
        return None


def snap_farm():
    """玩家在农舍旁(64,17)；圈内有一处杂草，圈外(20,40)也有一处。"""
    return {
        "location": "Farm",
        "player": {"position": {"x": 64, "y": 17}, "energy": 200, "canMove": True},
        "inventory": [{"slot": 0, "tool": "Scythe", "name": "镰刀"}],
        "entities": [
            {"kind": "door", "name": "农舍入口", "passable": False, "tile": {"x": 64, "y": 14}},
            {"kind": "object", "name": "杂草", "tile": {"x": 64, "y": 19}, "ref": "near"},
            {"kind": "object", "name": "杂草", "tile": {"x": 20, "y": 40}, "ref": "far"},
        ],
    }


class ZoneTests(unittest.TestCase):
    def setUp(self):
        self.rt = agent_skills.SkillRuntime(_Memory(), ".")

    def test_far_target_is_rejected(self):
        snap = snap_farm()
        far_id = target_id("Farm", snap["entities"][2])
        with self.assertRaises(ValueError) as ctx:
            self.rt._enforce_zone("use", {"target": far_id}, snap)
        self.assertIn("超出当前作业半径", str(ctx.exception))

    def test_near_target_passes(self):
        snap = snap_farm()
        near_id = target_id("Farm", snap["entities"][1])
        self.assertIs(self.rt._enforce_zone("use", {"target": near_id}, snap), snap)

    def test_auto_target_skills_only_see_zone(self):
        snap = snap_farm()
        snap["entities"].append({"kind": "soil", "name": "土壤", "tile": {"x": 20, "y": 41},
                                 "crop": "24", "ready": True})
        snap["entities"].append({"kind": "soil", "name": "土壤", "tile": {"x": 64, "y": 20},
                                 "crop": "24", "ready": True})
        filtered = self.rt._enforce_zone("harvest", {}, snap)
        tiles = [((e["tile"]["x"], e["tile"]["y"])) for e in filtered["entities"]]
        self.assertIn((64, 20), tiles, "圈内的成熟作物要保留")
        self.assertNotIn((20, 41), tiles, "圈外的成熟作物不该被自动选中")

    def test_non_farm_map_is_not_restricted(self):
        snap = snap_farm()
        snap["location"] = "Forest"
        far_id = target_id("Forest", snap["entities"][2])
        self.assertIs(self.rt._enforce_zone("use", {"target": far_id}, snap), snap,
                      "森林里不该受农舍半径限制")


if __name__ == "__main__":
    unittest.main()
