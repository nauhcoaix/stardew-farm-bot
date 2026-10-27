"""模型可以自己决定本次走几格（steps），而不是固定 3 格。"""
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "bot"))

from skill_catalog import DEFINITIONS, clean_steps  # noqa: E402


class MoveStepsTests(unittest.TestCase):
    def test_move_accepts_steps(self):
        out = clean_steps([{"action": "move", "arg": {"x": 10, "y": 5, "steps": 7}, "reason": "走远一点"}])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["arg"]["steps"], 7, "模型给的步数应原样保留")

    def test_steps_range_is_one_to_24(self):
        out = clean_steps([{"action": "move", "arg": {"x": 1, "y": 1, "steps": 1}}])
        self.assertEqual(out[0]["arg"]["steps"], 1)
        out = clean_steps([{"action": "move", "arg": {"x": 1, "y": 1, "steps": 24}}])
        self.assertEqual(out[0]["arg"]["steps"], 24)
        self.assertEqual(clean_steps([{"action": "move", "arg": {"x": 1, "y": 1, "steps": 25}}]), [],
                         "超过 24 应当拒绝")
        self.assertEqual(clean_steps([{"action": "move", "arg": {"x": 1, "y": 1, "steps": 0}}]), [],
                         "0 格没有意义，应当拒绝")

    def test_steps_is_documented_for_move_and_only_for_move(self):
        self.assertIn("steps", DEFINITIONS["move"][1], "move 的说明里要有 steps")
        self.assertNotIn("steps", DEFINITIONS["clear"][1], "clear 不该接受 steps")
        self.assertEqual(clean_steps([{"action": "clear", "arg": {"limit": 2, "steps": 5}}]), [],
                         "给 clear 传 steps 应整条丢弃（参数白名单）")

    def test_missing_steps_keeps_working(self):
        out = clean_steps([{"action": "move", "arg": {"x": 3, "y": 4}}])
        self.assertEqual(out[0]["arg"], {"x": 3, "y": 4}, "不传 steps 时行为不变")


if __name__ == "__main__":
    unittest.main()
