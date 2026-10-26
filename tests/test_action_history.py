"""复现日志中的同格锄地失败循环；只模拟桥，不启动游戏。"""
import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bot"))
from farmer import Farmer
from farm_service import made_progress
import farm_service as service
from action_history import ActionHistory
from work_planner import WorkPlanner
from unittest.mock import patch


class TillBridge:
    def __init__(self):
        self.used = []
        self.state = {
            "instanceId": "test", "saveGeneration": 1,
            "date": {"year": 1, "season": "spring", "day": 1},
            "location": "Farm", "time": 900,
            "player": {"energy": 100, "position": {"x": 66, "y": 20}},
            "inventory": [{"slot": 0, "tool": "Hoe", "name": "锄头"}],
            "entities": [{"kind": "tile", "tile": {"x": x, "y": 21},
                          "diggable": True, "ref": str(x)} for x in (66, 67)]}

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
        self.state["player"]["energy"] -= 2
        if ref == "66":
            raise RuntimeError("UNSUPPORTED: 工具动画结束，但目标未发生可验证变化，请重新观察。")
        self.state["entities"][1].update(kind="soil", tilled=True, diggable=False)
        return {"snapshot": await self.snapshot()}


class HistoryRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_tile_is_skipped_and_next_tile_is_worked(self):
        bridge = TillBridge()
        farmer = Farmer(bridge, log=lambda text: None)
        for _ in range(2):
            await farmer.till(await bridge.snapshot(), limit=1)
        self.assertEqual(bridge.used, ["66", "67"], "失败后应换地块，不应重复锄66,21")

    def test_energy_and_water_consumption_are_not_progress(self):
        before = TillBridge().state
        before["inventory"].append({"slot": 1, "tool": "WateringCan", "water": 40})
        after = copy.deepcopy(before)
        after["player"]["energy"] -= 2
        after["inventory"][1]["water"] -= 1
        self.assertFalse(made_progress(before, after))

    async def test_service_retains_failure_across_farmers_and_continues_model_task(self):
        bridge = TillBridge()
        bridge.state["inventory"].append({"slot": 1, "name": "防风草种子", "count": 10})
        brain = WorkPlanner(lambda *args: {"steps": []}, log=lambda text: None)
        brain.observe(bridge.state)
        brain.set_steps([{"action": "till_plant", "arg": {"limit": 1}}])
        with tempfile.TemporaryDirectory() as tmp, patch.multiple(
                service, Bridge=lambda: bridge, DEV=tmp, INBOX=tmp + "/inbox",
                OUTBOX=tmp + "/outbox", SENT=tmp + "/outbox/sent", BACKUPS=tmp + "/backups",
                STOP_FILE=tmp + "/STOP", STATUS_FILE=tmp + "/status.json",
                DECISIONS_FILE=tmp + "/decisions.jsonl", LOOT_DONE=[True]), patch.object(service, "log"):
            try:
                self.assertFalse(await service.run_once(brain))
                self.assertTrue(brain.allowed("till_plant"), "另有可用地块时不冻结整种技能")
                self.assertTrue(await service.run_once(brain))
                self.assertEqual(bridge.used.count("66"), 1)
                self.assertEqual(bridge.used[1], "67")
                self.assertIn("till@Farm:66,21", service.planning_text(bridge.state, brain.history))
            finally:
                brain.close()

    async def test_persistent_history_survives_executor_restart(self):
        bridge = TillBridge()
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "action-history.json")
            first = ActionHistory(path)
            await Farmer(bridge, log=lambda text: None, history=first).till(await bridge.snapshot(), limit=1)
            second = ActionHistory(path)
            await Farmer(bridge, log=lambda text: None, history=second).till(await bridge.snapshot(), limit=1)
            self.assertEqual(bridge.used, ["66", "67"])

    def test_ref_time_energy_and_position_do_not_bypass_failure_but_target_change_does(self):
        snap = TillBridge().state
        target = snap["entities"][0]
        history = ActionHistory()
        history.record(snap, snap, "till", target, False, "UNSUPPORTED")
        label = history.summary()["recent"][-1]["id"]
        snap["time"] += 10
        snap["player"]["energy"] -= 4
        snap["player"]["position"]["x"] += 1
        target["ref"] = "new-ref"
        self.assertFalse(history.allowed(snap, "till", target))
        self.assertTrue(history.allowed(snap, "water", target))
        self.assertEqual(history.summary()["recent"][-1]["id"], label)
        target["diggable"] = False
        self.assertTrue(history.allowed(snap, "till", target))

    def test_next_day_or_save_releases_old_failure(self):
        for field in ("day", "saveGeneration"):
            snap = TillBridge().state
            target = snap["entities"][0]
            history = ActionHistory()
            history.record(snap, snap, "till", target, False, "UNSUPPORTED")
            if field == "day":
                snap["date"]["day"] += 1
            else:
                snap[field] += 1
            self.assertTrue(history.allowed(snap, "till", target))

    def test_stale_ref_has_one_bounded_retry_then_blocks(self):
        now = [100.0]
        snap = TillBridge().state
        target = snap["entities"][0]
        history = ActionHistory(clock=lambda: now[0])
        history.record(snap, snap, "till", target, False, "STALE_REF")
        self.assertFalse(history.allowed(snap, "till", target))
        now[0] += 3
        self.assertTrue(history.allowed(snap, "till", target))
        history.record(snap, snap, "till", target, False, "STALE_REF")
        now[0] += 1000
        self.assertFalse(history.allowed(snap, "till", target))

    async def test_success_response_without_target_change_is_blocked(self):
        bridge = TillBridge()
        async def no_op(ref):
            bridge.used.append(ref)
            return {"snapshot": await bridge.snapshot()}
        bridge.use = no_op
        farmer = Farmer(bridge, log=lambda text: None)
        await farmer.till(await bridge.snapshot(), limit=1)
        self.assertEqual(farmer.last_till, 0)
        await farmer.till(await bridge.snapshot(), limit=1)
        self.assertEqual(bridge.used, ["66", "67"])
        self.assertEqual(farmer.history.summary()["recent"][0]["status"], "no_progress")

    async def test_verified_tree_swings_can_repeat_but_errors_cannot(self):
        bridge = TillBridge()
        bridge.state["inventory"] = [{"slot": 0, "tool": "Axe"}]
        bridge.state["entities"] = [{"kind": "terrain", "name": "Tree",
                                     "tile": {"x": 66, "y": 21}, "ref": "66"}]
        async def hit(ref):
            bridge.used.append(ref)
            return {"detail": "已验证树的血量减少", "snapshot": await bridge.snapshot()}
        bridge.use = hit
        farmer = Farmer(bridge, log=lambda text: None)
        await farmer.chop_trees(await bridge.snapshot(), max_swings=3)
        self.assertEqual(farmer.last_chops, 3)
        async def fail(ref):
            bridge.used.append(ref)
            raise RuntimeError("UNSUPPORTED")
        bridge.use = fail
        await farmer.chop_trees(await bridge.snapshot(), max_swings=3)
        self.assertEqual(farmer.last_chops, 0)
        self.assertEqual(len(bridge.used), 4)

    async def test_refreshes_refs_between_targets_and_tool_selection(self):
        bridge = TillBridge()
        async def select(slot):
            for e in bridge.state["entities"]:
                e["ref"] += "s"
            return {"snapshot": await bridge.snapshot()}
        async def use(ref):
            current = next(e for e in bridge.state["entities"] if e["ref"] == ref)
            bridge.used.append(ref)
            current.update(kind="soil", tilled=True, diggable=False)
            return {"snapshot": await bridge.snapshot()}
        async def move(**kwargs):
            self.assertIn(kwargs["near"], [e["ref"] for e in bridge.state["entities"]])
            return {"snapshot": await bridge.snapshot()}
        bridge.select, bridge.use, bridge.move = select, use, move
        farmer = Farmer(bridge, log=lambda text: None)
        await farmer.till(await bridge.snapshot(), limit=2)
        self.assertEqual(bridge.used, ["66s", "67ss"])
        self.assertEqual(farmer.last_till, 2)
