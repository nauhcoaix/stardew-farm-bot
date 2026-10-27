"""清障/开垦优先级：农舍周边先，田块内优先；不够再往外扩。"""
import copy
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "bot"))

import farm_layout  # noqa: E402
from farmer import Farmer  # noqa: E402


def snap_with(tiles, house=(64, 15), pos=(64, 18)):
    ents = [{"kind": "农舍入口", "name": "农舍入口", "tile": {"x": house[0], "y": house[1]}}]
    for (x, y), extra in tiles:
        e = {"kind": "tile", "name": "可耕地", "tile": {"x": x, "y": y},
             "diggable": True, "tilled": False, "ref": "%d_%d" % (x, y)}
        e.update(extra or {})
        ents.append(e)
    return {"location": "Farm", "player": {"position": {"x": pos[0], "y": pos[1]}, "energy": 200},
            "inventory": [{"slot": 0, "tool": "Hoe", "name": "锄头"}], "entities": ents}


class HousePriorityTests(unittest.TestCase):
    def test_clear_prefers_house_over_distance_to_player(self):
        ents = [
            {"kind": "农舍入口", "name": "农舍入口", "tile": {"x": 64, "y": 15}},
            # 离玩家近（1 格），离农舍远（5 格）
            {"kind": "杂草", "name": "杂草", "tile": {"x": 64, "y": 19}, "ref": "near_player"},
            # 离玩家远，离农舍近（2 格）
            {"kind": "杂草", "name": "杂草", "tile": {"x": 64, "y": 17}, "ref": "near_house"},
        ]
        snap = {"location": "Farm",
                "player": {"position": {"x": 64, "y": 18}, "energy": 200},
                "entities": ents}
        cands = farm_layout.clear_candidates(snap, zone="all", need=5, max_n=5)
        self.assertTrue(cands, "应该有候选")
        got = (cands[0]["tile"]["x"], cands[0]["tile"]["y"])
        self.assertEqual(got, (64, 17), "应该先清离农舍近的那个")

    def test_clear_window_expands_only_after_near_ones_gone(self):
        ents = [{"kind": "杂草", "name": "杂草", "tile": {"x": 64, "y": 17}, "ref": "h2"}]
        far = [{"kind": "杂草", "name": "杂草", "tile": {"x": 64 + 20, "y": 15}, "ref": "h20"}]
        snap = {"location": "Farm", "player": {"position": {"x": 64, "y": 18}},
                "entities": [{"kind": "农舍入口", "name": "农舍入口", "tile": {"x": 64, "y": 15}}] + ents + far}
        first = farm_layout.clear_candidates(snap, zone="all", need=5, max_n=5)
        self.assertTrue(all(farm_layout.dist((c["tile"]["x"], c["tile"]["y"]), (64, 15)) <= 8 for c in first),
                        "第一圈只该有近的")
        snap2 = copy.deepcopy(snap)
        snap2["entities"] = [e for e in snap2["entities"] if e.get("ref") != "h2"]
        second = farm_layout.clear_candidates(snap2, zone="all", need=5, max_n=5)
        self.assertTrue(second, "近的清完后应该放出更远的")
        self.assertGreaterEqual(farm_layout.dist((second[0]["tile"]["x"], second[0]["tile"]["y"]), (64, 15)), 15)


class TillPriorityTests(unittest.IsolatedAsyncioTestCase):
    async def test_till_prefers_plot_then_near_house(self):
        # 田块内(64,17) / 田块外但离农舍更近(70,16) / 更远(80,15)
        snap = snap_with([((64, 17), {}), ((70, 16), {}), ((80, 15), {})])

        class B:
            def __init__(self):
                self.used = []

            async def snapshot(self, radius=16):
                return copy.deepcopy(snap)

            async def move(self, **kw):
                return {"snapshot": await self.snapshot()}

            async def select(self, slot):
                return {"snapshot": await self.snapshot()}

            async def use(self, ref):
                self.used.append(ref)
                for e in snap["entities"]:
                    if e.get("ref") == ref:
                        e.update(tilled=True, diggable=False)
                return {"snapshot": await self.snapshot()}

        b = B()
        f = Farmer(b, log=lambda m: None)
        await f.till(await b.snapshot(), limit=1)
        self.assertEqual(b.used, ["64_17"], "田块内那块该最先翻")


if __name__ == "__main__":
    unittest.main()
