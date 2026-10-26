"""离线行为回归：慢模型不能阻止实际服务派发农务。"""
import asyncio
import copy
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bot"))
import farm_service as service
from farmer import Farmer
from work_planner import WorkPlanner, clean_steps


def snapshot():
    return {
        "instanceId": "test", "saveGeneration": 1,
        "date": {"year": 1, "season": "spring", "day": 1},
        "time": 800, "location": "Farm",
        "player": {"energy": 100, "maxEnergy": 270, "money": 500,
                   "maxItems": 12, "canMove": True, "position": {"x": 64, "y": 17}},
        "inventory": [{"slot": 0, "tool": "WateringCan", "water": 40, "count": 1},
                      {"slot": 1, "name": "防风草种子", "count": 10}],
        "entities": [{"kind": "soil", "tile": {"x": 65, "y": 17},
                      "ref": "@s1:e1", "crop": "24", "needsWater": True,
                      "watered": False, "tilled": True, "harvestable": False}],
        "menu": None,
    }


class FakeBridge:
    def __init__(self):
        self.state = snapshot()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def snapshot(self, radius=8):
        return copy.deepcopy(self.state)

    async def request(self, command, args=None):
        if command == "menudump":
            return {"money": 500}
        return {"ok": False}


class FakeFarmer:
    worked = None

    def __init__(self, bridge, **kwargs):
        self.b = bridge
        self.last_harvest = self.last_till = self.last_planted = 0
        self.last_watered = self.last_cleared = self.last_chops = 0
        self.no_stamina = False

    async def snap(self, radius=8):
        return await self.b.snapshot(radius)

    async def unchanged(self, snap, **kwargs):
        return snap

    ensure_free = clear_menu = go_outside = harvest = clear_obstacles = unchanged
    till = plant = chop_trees = unchanged

    async def water_crops(self, snap, **kwargs):
        self.b.state["entities"][0].update(watered=True, needsWater=False)
        self.last_watered = 1
        self.worked.set()
        return await self.snap()


class WaterBridge(FakeBridge):
    def __init__(self):
        super().__init__()
        soil = self.state["entities"][0]
        self.state["entities"] = [dict(soil, ref="@s1:e%d" % i, tile={"x": 60 + i, "y": 17})
                                  for i in range(7)]
        self.used = 0

    async def move(self, *args, **kwargs):
        return {"snapshot": await self.snapshot()}

    async def select(self, slot):
        return {"snapshot": await self.snapshot()}

    async def use(self, ref):
        for e in self.state["entities"]:
            if e["ref"] == ref:
                e.update(watered=True, needsWater=False)
                self.used += 1
        return {"snapshot": await self.snapshot()}


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_farming_skill_runs_in_chunks_across_rounds_with_model_pending(self):
        release, started = threading.Event(), threading.Event()
        def model(*args):
            started.set()
            release.wait(2)
            return {"steps": []}
        brain = WorkPlanner(model, log=lambda text: None)
        bridge = WaterBridge()
        with tempfile.TemporaryDirectory() as tmp, \
                patch.multiple(service, Bridge=lambda: bridge, Farmer=Farmer,
                               DEV=tmp, INBOX=tmp + "/inbox", OUTBOX=tmp + "/outbox",
                               SENT=tmp + "/outbox/sent", BACKUPS=tmp + "/backups",
                               STOP_FILE=tmp + "/STOP", STATUS_FILE=tmp + "/status.json",
                               LOOT_DONE=[True]), patch.object(service, "log"):
            try:
                await asyncio.wait_for(service.run_once(brain), 0.4)
                self.assertEqual(bridge.used, 3)
                await asyncio.wait_for(service.run_once(brain), 0.4)
                self.assertEqual(bridge.used, 6)
                await asyncio.wait_for(service.run_once(brain), 0.4)
                self.assertEqual(bridge.used, 7)
                self.assertTrue(started.is_set())
                self.assertTrue(brain.thinking)
            finally:
                release.set()
                brain.close()

    async def test_service_does_work_before_slow_model_finishes(self):
        release = threading.Event()
        called = threading.Event()
        worked = asyncio.Event()
        FakeFarmer.worked = worked

        def slow_model(*args, **kwargs):
            called.set()
            release.wait(2)
            return {"daily": {}, "steps": [{"action": "water", "arg": {}}]}

        with tempfile.TemporaryDirectory() as tmp, \
                patch.multiple(service, Bridge=FakeBridge, Farmer=FakeFarmer,
                               DEV=tmp, INBOX=tmp + "/inbox", OUTBOX=tmp + "/outbox",
                               SENT=tmp + "/outbox/sent", BACKUPS=tmp + "/backups",
                               STOP_FILE=tmp + "/STOP", STATUS_FILE=tmp + "/status.json",
                               PLAN_DAY=None, PLAN={}, LOOT_DONE=[True]), \
                patch.object(service, "log"), \
                patch.object(service.planner, "ask", slow_model), \
                patch.object(service.planner, "next_action", slow_model), \
                patch.object(service.planner, "plan_work", slow_model, create=True):
            task = asyncio.create_task(service.run_once())
            try:
                await asyncio.wait_for(worked.wait(), 0.4)
                self.assertFalse(release.is_set())
                self.assertTrue(called.is_set(), "应在干活前就提前发起后台规划")
            finally:
                release.set()
                await asyncio.wait_for(task, 3)

    async def test_player_task_planning_does_not_stop_current_farm_work(self):
        release = threading.Event()
        started = threading.Event()
        calls = []

        def model(text, daily, task):
            calls.append(task)
            started.set()
            release.wait(2)
            return {"steps": [{"action": "water"}]}

        brain = WorkPlanner(model, log=lambda text: None)
        worked = asyncio.Event()
        FakeFarmer.worked = worked
        with tempfile.TemporaryDirectory() as tmp, \
                patch.multiple(service, Bridge=FakeBridge, Farmer=FakeFarmer,
                               DEV=tmp, INBOX=tmp + "/inbox", OUTBOX=tmp + "/outbox",
                               SENT=tmp + "/outbox/sent", BACKUPS=tmp + "/backups",
                               STOP_FILE=tmp + "/STOP", STATUS_FILE=tmp + "/status.json",
                               LOOT_DONE=[True]), patch.object(service, "log"):
            service.ensure_dirs()
            (Path(tmp) / "inbox/task.json").write_text(
                '{"cmd":"task","arg":"今天帮我浇水"}', encoding="utf-8")
            try:
                await asyncio.wait_for(service.run_once(brain), 0.4)
                self.assertTrue(worked.is_set())
                self.assertTrue(started.is_set())
                self.assertEqual(calls, ["今天帮我浇水"])
            finally:
                release.set()
                brain.close()

    async def test_loop_continues_after_work_and_stops_without_thirty_second_gap(self):
        calls, delays = [], []

        async def work(brain):
            calls.append("work")
            return True

        async def sleep(delay):
            delays.append(delay)

        # 走真实main生命周期；第一次工作后的下一次STOP检查退出。
        with patch.object(service, "run_once", work), \
                patch.object(service, "stop_requested", side_effect=[False, False, True]), \
                patch.object(service, "ensure_dirs"), patch.object(service, "log"), \
                patch.object(service.asyncio, "sleep", sleep), \
                patch.object(sys, "argv", ["farm_service.py", "--loop"]):
            self.assertEqual(await service.main(), 0)
        self.assertEqual(calls, ["work"])
        self.assertEqual(delays, [0.1])


class PlannerTests(unittest.TestCase):
    def make_brain(self, fn, **kwargs):
        brain = WorkPlanner(fn, log=lambda text: None, **kwargs)
        self.addCleanup(brain.close)
        return brain

    def settle(self, brain, snap):
        deadline = time.monotonic() + 1
        while brain.thinking and time.monotonic() < deadline:
            time.sleep(0.001)
            brain.poll(snap, "state")
        self.assertFalse(brain.thinking)

    def test_only_one_request_is_in_flight(self):
        release, started = threading.Event(), threading.Event()
        calls = []

        def slow(*args):
            calls.append(args)
            started.set()
            release.wait(2)
            return {"steps": [{"action": "water"}]}

        brain = self.make_brain(slow)
        try:
            brain.poll(snapshot(), "copied-state")
            self.assertTrue(started.wait(1))
            for _ in range(100):
                brain.poll(snapshot(), "new-state")
            self.assertEqual(len(calls), 1)
            self.assertIsInstance(calls[0][0], str)
            self.assertEqual(brain.queued, 0)
        finally:
            release.set()
        self.settle(brain, snapshot())
        self.assertEqual(brain.queued, 1)

    def test_plan_is_revalidated_and_completed_work_is_skipped(self):
        brain = self.make_brain(lambda *args: {"steps": [{"action": "water"}, {"action": "sell"}]})
        snap = snapshot()
        brain.poll(snap, "state")
        self.settle(brain, snap)
        snap["entities"][0].update(watered=True, needsWater=False)
        snap["inventory"].append({"slot": 2, "name": "防风草", "count": 1})
        action = brain.take_action(lambda a: service.action_ready(snap, a, brain.daily))
        self.assertEqual(action["action"], "sell")

    def test_remaining_last_step_triggers_prefetch(self):
        clock, calls = [0.0], []
        def model(*args):
            calls.append(1)
            return {"steps": [{"action": "water"}, {"action": "sell"}]}
        brain = self.make_brain(model, clock=lambda: clock[0])
        snap = snapshot()
        brain.poll(snap, "state")
        self.settle(brain, snap)
        first = brain.take_action(lambda a: True)
        brain.complete(first, True)
        self.assertEqual(brain.queued, 1)
        clock[0] = 16
        brain.poll(snap, "updated-state")
        self.assertTrue(brain.thinking)
        self.assertEqual(brain.queued, 1, "下一计划请求已发出，最后一个任务仍可执行")

    def test_in_flight_previous_day_result_is_discarded(self):
        release, started = threading.Event(), threading.Event()
        def model(*args):
            started.set()
            release.wait(2)
            return {"daily": {"chop": True}, "steps": [{"action": "chop"}]}
        brain = self.make_brain(model)
        old, new = snapshot(), snapshot()
        new["date"]["day"] = 2
        try:
            brain.poll(old, "old-state")
            self.assertTrue(started.wait(1))
            release.set()
            # 等待工作线程返回，但尚不把结果交给规划器。
            brain._pending[0].result(timeout=1)
            brain.poll(new, "new-state")
            self.assertFalse(brain.daily["chop"])
            self.assertEqual(brain.queued, 0)
        finally:
            release.set()

    def test_date_and_save_changes_invalidate_queued_work(self):
        for field in ("season", "year", "saveGeneration", "instanceId"):
            with self.subTest(field=field):
                snap = snapshot()
                brain = self.make_brain(lambda *args: {"steps": [{"action": "water"}]})
                brain.observe(snap)
                brain.set_steps([{"action": "water"}])
                if field in ("season", "year"):
                    snap["date"][field] = "summer" if field == "season" else 2
                else:
                    snap[field] = "another" if field == "instanceId" else 2
                brain.observe(snap)
                self.assertIsNone(brain.take_action(lambda a: True))

    def test_old_in_flight_result_cannot_override_new_manual_task(self):
        release, started = threading.Event(), threading.Event()

        def slow(*args):
            started.set()
            release.wait(2)
            return {"daily": {"chop": True}, "steps": [{"action": "chop"}]}

        brain = self.make_brain(slow)
        snap = snapshot()
        try:
            brain.poll(snap, "state")
            self.assertTrue(started.wait(1))
            brain.set_steps([{"action": "water"}], mission="浇水")
        finally:
            release.set()
        self.settle(brain, snap)
        self.assertFalse(brain.daily["chop"])
        self.assertEqual(brain.take_action(lambda a: True)["action"], "water")

    def test_failed_model_uses_backoff_without_queueing_more_threads(self):
        clock = [0.0]
        calls = []
        def failed(*args):
            calls.append(1)
            raise RuntimeError("offline")
        brain = self.make_brain(failed, clock=lambda: clock[0])
        snap = snapshot()
        brain.poll(snap, "state")
        self.settle(brain, snap)
        for _ in range(10):
            brain.poll(snap, "state")
        self.assertEqual(len(calls), 1)
        self.assertEqual(service.local_action(snap, brain)["action"], "water")

    def test_chunk_is_requeued_until_work_is_complete(self):
        reports = []
        brain = self.make_brain(lambda *args: None, report=reports.append)
        brain.observe(snapshot())
        brain.set_steps([{"action": "water"}], mission="浇水")
        step = brain.take_action(lambda a: True)
        brain.complete(step, True, repeat=True)
        self.assertEqual(brain.queued, 1)
        self.assertEqual(reports, [])
        step = brain.take_action(lambda a: True)
        brain.complete(step, True)
        self.assertEqual(brain.queued, 0)
        self.assertIn("有效完成1步", reports[-1])

    def test_no_progress_enters_cooldown_and_different_task_can_run(self):
        brain = self.make_brain(lambda *args: None)
        brain.complete({"action": "water"}, False)
        self.assertFalse(brain.allowed("water"))
        self.assertTrue(brain.allowed("harvest"))

    def test_model_cannot_send_arbitrary_commands_or_stale_refs(self):
        steps = clean_steps([{"action": ["water"]}, {"action": "warp"},
                             {"action": "water", "arg": {"ref": "@s1:e1"}},
                             {"action": "clear", "arg": {"limit": 999, "min_energy": -1}}])
        self.assertEqual([s["action"] for s in steps], ["water", "clear"])
        self.assertEqual(steps[0]["arg"], {})
        self.assertEqual(steps[1]["arg"], {"limit": 24, "min_energy": 40})

    def test_zero_purchase_budget_is_respected(self):
        snap = snapshot()
        snap["time"] = 1000
        snap["inventory"] = []
        self.assertFalse(service.action_ready(snap, {"action": "buy_seeds"}, {"buy_qty": 0}))

    def test_pure_walk_or_new_observation_does_not_count_as_work(self):
        before = snapshot()
        after = copy.deepcopy(before)
        after["time"] = 810
        after["player"]["position"]["x"] += 1
        after["entities"].append({"kind": "tile", "tile": {"x": 80, "y": 30}})
        after["entities"][0]["ref"] = "@s2:e1"
        self.assertFalse(service.made_progress(before, after))
        after["entities"][0]["watered"] = True
        self.assertTrue(service.made_progress(before, after))


if __name__ == "__main__":
    unittest.main()
