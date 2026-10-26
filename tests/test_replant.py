"""收获后的地不该再翻：till 只挑没翻过的，plant 直接种在翻好的空地上。"""
import copy
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "bot"))

from farmer import Farmer, empty_tilled  # noqa: E402


class FieldBridge:
    """最小假桥：一块已翻的空地(64,20) + 一块没翻的地(64,21) + 一块有作物的地(64,22)。"""

    def __init__(self):
        self.used = []
        self.state = {
            "instanceId": "test", "saveGeneration": 1,
            "date": {"year": 1, "season": "spring", "day": 1},
            "location": "Farm", "time": 900,
            "player": {"energy": 200, "position": {"x": 64, "y": 20}},
            "inventory": [
                {"slot": 0, "tool": "Hoe", "name": "锄头"},
                {"slot": 1, "name": "防风草种子", "count": 5},
            ],
            "entities": [
                # 刚收完的地：已翻、没作物 —— 应该直接种，不该再锄
                {"kind": "soil", "name": "耕地", "tile": {"x": 64, "y": 20},
                 "tilled": True, "diggable": False, "crop": None, "ref": "s1"},
                # 没翻过的地：可以锄
                {"kind": "tile", "name": "可耕地", "tile": {"x": 64, "y": 21},
                 "diggable": True, "tilled": False, "ref": "t1"},
                # 还有作物的地：既不该锄也不该种
                {"kind": "soil", "name": "作物", "tile": {"x": 64, "y": 22},
                 "tilled": True, "diggable": False, "crop": "24", "ref": "c1"},
            ],
        }

    async def snapshot(self, radius=16):
        return copy.deepcopy(self.state)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def request(self, command, args=None):
        return {"money": 500} if command == "menudump" else {"ok": False}

    async def move(self, **kwargs):
        return {"snapshot": await self.snapshot()}

    async def select(self, slot):
        return {"snapshot": await self.snapshot()}

    async def use(self, ref):
        self.used.append(ref)
        for e in self.state["entities"]:
            if e.get("ref") == ref:
                if e.get("crop"):
                    e["crop"] = None
                    e["harvestable"] = False
                else:
                    e.update(tilled=True, diggable=False)
        return {"snapshot": await self.snapshot()}

    async def interact(self, ref):
        return await self.use(ref)


class ReplantTests(unittest.IsolatedAsyncioTestCase):
    async def test_till_skips_already_tilled_and_crop_tiles(self):
        bridge = FieldBridge()
        farmer = Farmer(bridge, log=lambda text: None)
        await farmer.till(await bridge.snapshot(), limit=5)
        self.assertEqual(bridge.used, ["t1"], "只该锄那块没翻过的地")
        self.assertNotIn("s1", bridge.used, "刚收完的空地不该再锄")
        self.assertNotIn("c1", bridge.used, "还在长作物的地不该锄")

    async def test_plant_uses_harvested_soil_without_tilling(self):
        bridge = FieldBridge()
        farmer = Farmer(bridge, log=lambda text: None)
        snap = await bridge.snapshot()
        plots = [e.get("ref") for e in empty_tilled(snap)]
        self.assertEqual(plots, ["s1"], "已翻的空地应被识别成可种")
        before = list(bridge.used)
        await farmer.plant(snap, limit=1)
        self.assertEqual(bridge.used, before + ["s1"], "应该直接种在刚收完的地上")
        self.assertNotIn("t1", bridge.used, "种的时候不该顺带翻地")
