"""统一记忆、真实路径证据与模型选择技能的离线行为测试。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bot"))
from agent_skills import SkillRuntime, menu_targets
from exploration import candidates, path_to, region_route
from farmer import Farmer
from memory_store import MemoryStore, save_key, target_id
from observed_bridge import ObservedBridge
from skill_catalog import clean_steps
from action_history import ActionHistory
from skill_fishing import FishingSkill
from work_planner import WorkPlanner
import farm_service as service
import planner


class World:
    def __init__(self):
        self.p = {"x": 4, "y": 4}
        self.location = "Farm"
        self.day, self.seq = 1, 0
        self.menu_value = None
        self.ops = []
        self.objects = {(7, 4): {"kind": "object", "name": "未知箱子", "objType": "Chest", "passable": False}}
        self.refs = {}
        self.inv = [{"slot": 0, "tool": "WateringCan", "count": 1, "water": 10}]
        self.dead_ref = None
        self.time, self.energy = 800, 100
        self.button_x = 100
        self.noop_click = False
        self.selected = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def snapshot(self, radius=16):
        self.seq += 1
        entities = []
        self.refs = {}
        for y in range(max(0, self.p["y"] - 2), min(12, self.p["y"] + 3)):
            for x in range(max(0, self.p["x"] - 2), min(12, self.p["x"] + 3)):
                entity = dict(kind="tile", name="地块", passable=True, tile={"x": x, "y": y})
                entity.update(self.objects.get((x, y), {}))
                entity["ref"] = "@s%d:e%d" % (self.seq, len(entities))
                entities.append(entity)
                self.refs[entity["ref"]] = entity
        menu = copy.deepcopy(self.menu_value)
        if menu:
            for i, option in enumerate(menu.get("options") or []):
                option["ref"] = "@s%d:m%d" % (self.seq, i)
                self.refs[option["ref"]] = option
        return {"saveId": "save-A", "playerId": "p", "instanceId": "engine-1", "saveGeneration": 0,
                "location": self.location, "date": {"year": 1, "season": "spring", "day": self.day}, "time": self.time,
                "player": {"position": dict(self.p), "canMove": True, "energy": self.energy, "maxItems": 12, "selectedSlot": self.selected},
                "inventory": copy.deepcopy(self.inv), "entities": entities, "menu": menu,
                "observation": {"width": 12, "height": 12, "radius": 2}}

    async def move(self, x=None, y=None, near=None):
        if near is not None:
            tile = self.refs[near]["tile"]
            assert abs(tile["x"] - self.p["x"]) + abs(tile["y"] - self.p["y"]) <= 1
            return {"snapshot": await self.snapshot(), "changed": False}
        assert abs(x - self.p["x"]) + abs(y - self.p["y"]) == 1, "必须逐格记录经过的路"
        assert self.objects.get((x, y), {}).get("passable", True)
        self.ops.append(("move", x, y))
        self.p = {"x": x, "y": y}
        return {"snapshot": await self.snapshot(), "changed": True}

    async def select(self, slot):
        self.ops.append(("select", slot))
        self.selected = slot
        return {"snapshot": await self.snapshot(), "changed": True}

    async def interact(self, ref):
        self.ops.append(("interact", self.refs[ref]["tile"]))
        if self.refs[ref].get("kind") == "door":
            self.location = "FarmHouse"
            self.p = {"x": 1, "y": 1}
            return {"snapshot": await self.snapshot(), "changed": True}
        if self.refs[ref].get("crop"):
            t = self.refs[ref]["tile"]
            self.objects[(t["x"], t["y"])] = {"kind": "soil", "name": "耕地", "tilled": True, "passable": True}
            self.inv.append({"slot": 3, "name": "防风草", "count": 1})
            return {"snapshot": await self.snapshot(), "changed": True}
        self.menu_value = {"type": "DialogueBox", "text": "打开吗？", "options": [{"label": "是"}, {"label": "否"}]}
        return {"snapshot": await self.snapshot(), "changed": True}

    async def use(self, ref):
        self.ops.append(("use", self.refs[ref]["tile"]))
        e = self.refs[ref]
        item = next(i for i in self.inv if i["slot"] == self.selected)
        tile = (e["tile"]["x"], e["tile"]["y"])
        if item.get("tool") == "Pickaxe" and e.get("name") == "石头":
            self.objects[tile] = {"kind": "tile", "name": "地块", "passable": True}
            return {"snapshot": await self.snapshot(), "changed": True}
        if item.get("tool") == "Hoe" and e.get("diggable"):
            self.objects[tile] = {"kind": "soil", "name": "耕地", "tilled": True, "passable": True}
            return {"snapshot": await self.snapshot(), "changed": True}
        if item.get("category") == -74 and e.get("tilled") and not e.get("crop"):
            self.objects[tile] = {"kind": "soil", "name": "作物", "tilled": True, "crop": "24", "harvestable": False, "passable": True}
            item["count"] -= 1
            return {"snapshot": await self.snapshot(), "changed": True}
        raise RuntimeError("UNSUPPORTED: 水壶不能对箱子使用")

    async def menu(self, ref):
        assert ref in self.refs
        self.ops.append(("menu", self.refs[ref]["label"]))
        self.menu_value = None
        return {"snapshot": await self.snapshot(), "changed": True}

    async def request(self, command, args=None):
        if command == "menudump" and self.menu_value:
            return {"items": [{"kind": "field", "name": "okButton", "label": "继续", "x": self.button_x, "y": 100, "w": 40, "h": 20}]}
        if command == "menuclick":
            self.ops.append(("menuclick", args["x"], args["y"]))
            if not self.noop_click:
                self.menu_value = None
            return {"clicked": True}
        return {"items": []}


class MemoryTests(unittest.TestCase):
    def test_unified_events_survive_reopen_and_new_day_or_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.sqlite3"
            memory = MemoryStore(path)
            snap = {"saveId": "A", "playerId": "p", "instanceId": "one", "location": "Farm",
                    "date": {"day": 1}, "player": {"position": {"x": 1, "y": 2}},
                    "entities": [{"kind": "tile", "tile": {"x": 1, "y": 2}, "passable": True}]}
            evidence = memory.observe(snap)
            note = memory.note("这里可以走，但另一侧还不确定", [evidence["id"]])
            self.assertEqual(note["confidence"], "hypothesis")
            memory.close()
            reopened = MemoryStore(path)
            self.addCleanup(reopened.close)
            snap.update(instanceId="two", date={"day": 2})
            reopened.observe(snap)
            self.assertEqual(reopened.query("另一侧", snap=snap)[0]["id"], note["id"])
            self.assertTrue(reopened.tiles(snap=snap)[0]["visited"])
            for event in reopened.query(snap=snap):
                self.assertTrue({"schema_version", "id", "save_id", "session_id", "game_time", "source", "confidence", "kind", "status", "evidence", "data"} <= event.keys())
            reopened.close()

    def test_different_saves_and_forged_evidence_are_isolated(self):
        memory = MemoryStore()
        self.addCleanup(memory.close)
        a = {"saveId": "A", "location": "Farm"}
        b = {"saveId": "B", "location": "Farm"}
        event = memory.note("只属于A", source="player", snap=a)
        self.assertEqual(memory.query("只属于A", snap=b), [])
        with self.assertRaises(ValueError):
            memory.note("试图借用A的证据", [event["id"]], snap=b)

    def test_legacy_bridge_identity_is_explicitly_session_scoped(self):
        self.assertNotEqual(save_key({"instanceId": "one"}), save_key({"instanceId": "two"}))
        self.assertEqual(save_key({"saveId": "A", "instanceId": "one"}), save_key({"saveId": "A", "instanceId": "two"}))

    def test_region_routes_only_use_observed_connections_and_keep_indoor_distinct(self):
        memory = MemoryStore()
        self.addCleanup(memory.close)
        snap = {"saveId": "A", "location": "Farm", "entities": [
            {"kind": "exit", "name": "出口 → FarmHouse", "tile": {"x": 3, "y": 2}, "passable": True}]}
        memory.observe(snap)
        self.assertEqual(region_route(memory, snap, "FarmHouse")[0]["to"], "FarmHouse")
        self.assertIsNone(region_route(memory, snap, "Town"), "不能偷偷使用places.EDGES里的预设路线")

    def test_native_trajectory_is_deduplicated_after_restart_and_gaps_are_not_invented(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.sqlite3"
            snap = {"saveId": "A", "instanceId": "i", "saveGeneration": 0, "location": "Farm", "player": {"position": {"x": 3, "y": 2}},
                    "trajectory": [{"seq": i, "location": "Farm", "tile": {"x": i, "y": 2}, "year": 1, "season": "spring", "day": 1, "time": 800} for i in (1, 2, 3)]}
            memory = MemoryStore(path)
            memory.observe(snap)
            self.assertEqual(len(memory.query(kind="movement")), 2)
            memory.close()
            reopened = MemoryStore(path)
            try:
                reopened.observe(snap)
                self.assertEqual(len(reopened.query(kind="movement")), 2)
                snap["trajectory"].append(dict(snap["trajectory"][-1], seq=5, tile={"x": 5, "y": 2}))
                snap["player"]["position"]["x"] = 5
                reopened.observe(snap)
                self.assertTrue(reopened.query(kind="trace_gap"))
                self.assertTrue(reopened.query(kind="movement")[0]["data"]["sampling_gap"])
                self.assertFalse(any(t["tile"] == {"x": 4, "y": 2} for t in reopened.tiles()), "缺的第4格不能补成走过")
            finally:
                reopened.close()

    def test_legacy_history_imports_once_and_unassigned_records_do_not_pollute_current_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "action-history.json"
            payload = {"context": ["old-engine", 0, 1, "spring", 1], "records": {
                "till@Farm:1,2": {"label": "till@Farm:1,2", "status": "failed", "state": [], "reason": "UNSUPPORTED"}}}
            path.write_text(json.dumps(payload), encoding="utf-8")
            memory = MemoryStore()
            self.addCleanup(memory.close)
            snap = {"saveId": "A", "instanceId": "new-engine", "saveGeneration": 0, "location": "Farm", "date": {"year": 1, "season": "spring", "day": 1}}
            memory.observe(snap)
            for _ in range(2):
                ActionHistory(str(path), memory=memory).observe(snap)
            self.assertEqual(memory.query(kind="skill_target", snap=snap), [])
            rows = [r for r in memory.db.execute("SELECT save_id,body FROM events WHERE kind='skill_target'") if json.loads(r[1])["source"] == "migration"]
            self.assertEqual(len(rows), 1)
            self.assertIn("unassigned-legacy", rows[0][0])

    def test_model_cannot_upgrade_its_claim_to_observed_fact(self):
        memory = MemoryStore()
        self.addCleanup(memory.close)
        snap = {"saveId": "A", "location": "Farm"}
        event = memory.append("reflection", snap, source="model", confidence="observed", data={"text": "Farm可直达Town"})
        self.assertEqual(event["confidence"], "hypothesis")
        self.assertIsNone(region_route(memory, snap, "Town"))


class SkillTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.world = World()
        self.memory = MemoryStore()
        self.addCleanup(self.memory.close)
        self.bridge = ObservedBridge(self.world, self.memory)
        self.runtime = SkillRuntime(self.memory, ".", log=lambda text: None)
        self.farmer = Farmer(self.bridge, history=None, log=lambda text: None)
        self.snap = await self.bridge.snapshot()

    async def run_skill(self, name, arg):
        self.snap, result = await self.runtime.execute({"action": name, "arg": arg, "reason": "测试中由模型选择"},
                                                      self.bridge, self.farmer, self.snap)
        return result

    async def test_exploration_reveals_new_tiles_and_remembers_each_visited_step(self):
        before = len(self.memory.tiles())
        options = candidates(self.snap, self.memory)
        self.assertTrue(any(o["mode"] == "path" for o in options))
        result = await self.run_skill("explore", {"mode": "path", "x": 6, "y": 4})
        self.assertTrue(result["progress"])
        self.assertGreater(len(self.memory.tiles()), before)
        steps = self.memory.query(kind="movement")
        self.assertEqual(len(steps), 2)
        self.assertTrue(all(e["data"]["intermediate_tiles"] == [] for e in steps))
        self.assertEqual(self.world.ops, [("move", 5, 4), ("move", 6, 4)])
        self.assertEqual(sum(e["visited"] for e in self.memory.tiles()), 3)
        chest = next(e for e in self.snap["entities"] if e.get("name") == "未知箱子")
        self.assertIn(target_id("Farm", chest), [e["id"] for e in self.runtime.context(self.snap)["targets"]])

    async def test_interaction_opens_menu_and_uses_fresh_option_ids(self):
        await self.run_skill("move", {"x": 6, "y": 4})
        chest = next(e for e in self.snap["entities"] if e.get("name") == "未知箱子")
        result = await self.run_skill("explore", {"mode": "interaction", "target": target_id("Farm", chest)})
        self.assertTrue(result["progress"])
        option = menu_targets(self.snap)[0]["id"]
        self.snap = await self.bridge.snapshot()  # native ref已经换了，稳定选项ID仍应解析。
        result = await self.run_skill("menu", {"target": option})
        self.assertTrue(result["progress"])
        self.assertIsNone(self.snap["menu"])

    async def test_capability_failure_is_recorded_and_same_unchanged_probe_is_blocked(self):
        await self.run_skill("move", {"x": 6, "y": 4})
        chest = next(e for e in self.snap["entities"] if e.get("name") == "未知箱子")
        args = {"mode": "capability", "skill": "use", "target": target_id("Farm", chest), "slot": 0, "question": "水壶能打开箱子吗？"}
        result = await self.run_skill("explore", args)
        self.assertEqual(result["status"], "failed")
        count = len(self.world.ops)
        self.assertFalse(self.runtime.ready({"action": "explore", "arg": args}, self.snap))
        await self.run_skill("explore", args)
        self.assertEqual(len(self.world.ops), count)
        self.assertTrue(any(e["status"] == "failed" for e in self.memory.query(kind="skill_call")))
        self.assertEqual(self.memory.query(kind="reflection")[0]["confidence"], "hypothesis")

    async def test_unknown_target_or_route_cannot_move_the_player(self):
        result = await self.run_skill("move", {"x": 11, "y": 11})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.world.ops, [])
        result = await self.run_skill("interact", {"target": "invented-target"})
        self.assertFalse(result["progress"])
        self.assertEqual(self.world.ops, [])

    async def test_changing_selected_tool_allows_a_new_capability_attempt(self):
        self.world.inv.append({"slot": 1, "tool": "Pickaxe", "count": 1})
        self.world.objects[(5, 4)] = {"kind": "object", "name": "石头", "passable": False}
        self.snap = await self.bridge.snapshot()
        stone = next(e for e in self.snap["entities"] if e.get("name") == "石头")
        arg = {"target": target_id("Farm", stone)}
        result = await self.run_skill("use", arg)
        self.assertFalse(result["progress"], "水壶处理石头失败")
        action = {"action": "use", "arg": arg}
        self.assertTrue(self.memory.blocked(action, self.snap))
        await self.run_skill("select", {"slot": 1})
        self.assertFalse(self.memory.blocked(action, self.snap), "更换工具是新的试验前提")
        result = await self.run_skill("use", arg)
        self.assertTrue(result["progress"])

    async def test_memory_skill_returns_real_evidence_and_notes_remain_hypotheses(self):
        await self.run_skill("move", {"x": 5, "y": 4})
        result = await self.run_skill("memory", {"op": "recall", "kind": "movement"})
        events = result["output"]["events"]
        self.assertTrue(events)
        result = await self.run_skill("memory", {"op": "remember", "text": "这条路可能通向箱子", "evidence": [events[0]["id"]]})
        self.assertEqual(result["output"]["confidence"], "hypothesis")

    async def test_actual_door_transition_teaches_connection_without_assuming_reverse_route(self):
        self.world.objects[(5, 4)] = {"kind": "door", "name": "农舍入口", "passable": False}
        self.snap = await self.bridge.snapshot()
        door = next(e for e in self.snap["entities"] if e.get("kind") == "door")
        result = await self.run_skill("interact", {"target": target_id("Farm", door)})
        self.assertTrue(result["progress"])
        links = self.memory.links()
        self.assertTrue(any(e["from"] == "Farm" and e["to"] == "FarmHouse" and e["confirmed"] for e in links))
        self.assertIsNone(region_route(self.memory, self.snap, "Farm"), "没看到回程出口，不能凭印象补一条反向路线")

    async def test_repeated_observations_do_not_make_recursive_model_context(self):
        sizes = []
        for _ in range(12):
            await self.run_skill("observe", {})
            sizes.append(len(json.dumps(self.runtime.context(self.snap), ensure_ascii=False)))
        self.assertLess(max(sizes), 45000)
        self.assertLess(sizes[-1], sizes[2] * 2)

    async def test_travel_approaches_a_remembered_impassable_door_then_rebinds_it(self):
        self.world.objects[(5, 4)] = {"kind": "door", "name": "农舍入口", "passable": False}
        self.snap = await self.bridge.snapshot()
        door = next(e for e in self.snap["entities"] if e.get("kind") == "door")
        await self.run_skill("interact", {"target": target_id("Farm", door)})
        self.world.location, self.world.p = "Farm", {"x": 1, "y": 4}
        self.snap = await self.bridge.snapshot()
        self.assertFalse(any(e.get("kind") == "door" for e in self.snap["entities"]))
        result = await self.run_skill("travel", {"to": "FarmHouse"})
        self.assertTrue(result["progress"])
        self.assertTrue(result["repeat"])
        self.assertEqual(self.snap["location"], "Farm")
        result = await self.run_skill("travel", {"to": "FarmHouse"})
        self.assertTrue(result["progress"])
        self.assertEqual(self.snap["location"], "FarmHouse")

    async def test_unknown_menu_controls_are_observed_revalidated_then_clicked(self):
        self.world.menu_value = {"type": "CustomMenu", "identity": "one", "text": "原生菜单", "options": []}
        self.snap = await self.bridge.snapshot()
        await self.run_skill("observe", {})
        old = self.runtime.context(self.snap)["menu"]["controls"][0]["id"]
        self.world.button_x = 150
        result = await self.run_skill("menu_click", {"target": old})
        self.assertFalse(result["progress"])
        self.assertEqual(self.world.ops, [], "布局变了时不能按旧坐标点击")
        await self.run_skill("observe", {})
        current = self.runtime.context(self.snap)["menu"]["controls"][0]["id"]
        result = await self.run_skill("menu_click", {"target": current})
        self.assertTrue(result["progress"])
        self.assertEqual(self.world.ops, [("menuclick", 170, 110)])
        self.assertIsNone(self.snap["menu"])

    async def test_menu_click_ack_without_visible_effect_is_not_progress(self):
        self.world.menu_value = {"type": "CustomMenu", "text": "无变化", "options": []}
        self.world.noop_click = True
        self.snap = await self.bridge.snapshot()
        await self.run_skill("observe", {})
        control = self.runtime.context(self.snap)["menu"]["controls"][0]["id"]
        result = await self.run_skill("menu_click", {"target": control})
        self.assertFalse(result["progress"])

    async def test_using_energy_without_target_effect_does_not_count_as_work(self):
        await self.run_skill("move", {"x": 6, "y": 4})
        chest = next(e for e in self.snap["entities"] if e.get("name") == "未知箱子")
        async def no_effect(ref):
            self.world.energy -= 2
            return {"snapshot": await self.world.snapshot(), "changed": False}
        self.world.use = no_effect
        result = await self.run_skill("use", {"target": target_id("Farm", chest), "slot": 0})
        self.assertFalse(result["progress"])
        self.assertFalse(self.runtime.ready({"action": "use", "arg": {"target": target_id("Farm", chest), "slot": 0}}, self.snap))

    async def test_explicit_planting_works_outside_the_old_fixed_plot(self):
        self.world.inv = [{"slot": 0, "tool": "Hoe", "count": 1}, {"slot": 1, "category": -74, "name": "防风草种子", "count": 1}]
        self.world.objects[(5, 4)] = {"kind": "tile", "name": "可耕地", "diggable": True, "passable": True}
        self.snap = await self.bridge.snapshot()
        result = await self.run_skill("till_plant", {"limit": 1})
        self.assertTrue(result["repeat"])
        self.assertTrue(self.world.objects[(5, 4)]["tilled"])
        result = await self.run_skill("till_plant", result["remaining_arg"])
        self.assertTrue(result["progress"])
        self.assertFalse(result["repeat"])
        self.assertEqual(self.world.objects[(5, 4)]["crop"], "24")

    async def test_harvesting_does_not_implicitly_plant_or_sell(self):
        self.world.inv.append({"slot": 1, "name": "防风草种子", "count": 3})
        self.world.objects[(5, 4)] = {"kind": "soil", "name": "作物", "crop": "24", "harvestable": True, "tilled": True, "passable": True}
        self.snap = await self.bridge.snapshot()
        result = await self.run_skill("harvest", {"limit": 1})
        self.assertTrue(result["progress"])
        self.assertIsNone(self.world.objects[(5, 4)].get("crop"))
        self.assertEqual(self.world.inv[1]["count"], 3)
        self.assertFalse(any(op[0] == "use" for op in self.world.ops))

    async def test_clear_uses_observed_obstacle_without_fixed_farm_cluster(self):
        self.world.location = "Forest"
        self.world.inv = [{"slot": 0, "tool": "Pickaxe", "count": 1}]
        self.world.objects[(5, 4)] = {"kind": "object", "name": "石头", "passable": False}
        self.snap = await self.bridge.snapshot()
        with patch.object(self.farmer, "clear_obstacles", side_effect=AssertionError("不可调用旧固定开垦区策略")):
            result = await self.run_skill("clear", {"limit": 1})
        self.assertTrue(result["progress"])
        self.assertFalse(result["repeat"])
        self.assertEqual(self.world.objects[(5, 4)]["name"], "地块")
        self.assertIn(("use", {"x": 5, "y": 4}), self.world.ops)

    async def test_fishing_release_allows_model_to_handle_reward_menu(self):
        class Client:
            @staticmethod
            def read_status(runtime):
                return {"active": False, "playerBusy": True, "menu": "ItemGrabMenu", "runId": "old"}
        fishing = FishingSkill(".", client=Client())
        fishing.saw_active = True
        self.assertFalse(await fishing.busy(), "钓鱼已经停止后，奖励菜单应交给有菜单技能的planner")

    def test_model_parameters_cannot_escape_catalog_or_inject_raw_protocol(self):
        steps = clean_steps([{"action": "warp", "arg": {"location": "Town"}},
                             {"action": "explore", "arg": {"mode": "path", "candidate": "frontier:6:4"}},
                             {"action": "use", "arg": {"ref": "@old:e1"}},
                             {"action": "memory", "arg": {"op": "remember", "text": "可能有门"}}])
        self.assertEqual([s["action"] for s in steps], ["explore", "memory"])


class OfflineFishing:
    async def busy(self):
        return False

    async def stop(self):
        pass

    def status(self):
        raise ValueError("离线测试不读取真实钓鱼现场")


class ServiceChoiceTests(unittest.IsolatedAsyncioTestCase):
    async def service(self, world, brain):
        with tempfile.TemporaryDirectory() as directory, patch.multiple(service,
            Bridge=lambda: world, DEV=directory, INBOX=directory + "/inbox", OUTBOX=directory + "/outbox",
            SENT=directory + "/outbox/sent", BACKUPS=directory + "/backups", STOP_FILE=directory + "/STOP",
            STATUS_FILE=directory + "/status.json", DECISIONS_FILE=directory + "/decisions.jsonl"), patch.object(service, "log"):
            return await service.run_once(brain)

    async def test_model_receives_capabilities_and_can_choose_exploration_as_first_action(self):
        world, captured, started = World(), [], threading.Event()
        def model(text, execution_context, task):
            data = json.loads(text)
            captured.append(data)
            chosen = next(x for x in data["exploration_candidates"] if x["mode"] == "path")
            started.set()
            return {"goal": "探索这片未知边缘", "steps": [{"action": "explore", "arg": {"mode": "path", "candidate": chosen["id"]}}]}
        brain = WorkPlanner(model, log=lambda text: None)
        brain.runtime = SkillRuntime(brain.memory, ".", log=lambda text: None, fishing=OfflineFishing())
        try:
            await self.service(world, brain)
            self.assertTrue(started.wait(1))
            brain._pending[0].result(timeout=1)
            self.assertTrue(await self.service(world, brain))
            self.assertTrue(any(x[0] == "move" for x in world.ops))
            self.assertTrue({"memory", "explore", "move", "interact", "use", "menu", "fish"} <= {x["name"] for x in captured[0]["skills"]})
            self.assertTrue(brain.memory.query(kind="movement"))
        finally:
            brain.close()

    async def test_no_selected_action_never_forces_home_sleep_menu_or_fishing(self):
        world = World()
        world.location, world.time, world.energy = "Town", 2300, 10
        world.menu_value = {"type": "UnrecognizedMenu", "text": "等待玩家", "options": []}
        brain = WorkPlanner(lambda *args: {"daily": {"chop": True, "sleep_at": 1800}, "steps": []}, log=lambda text: None)
        brain.runtime = SkillRuntime(brain.memory, ".", log=lambda text: None, fishing=OfflineFishing())
        try:
            self.assertFalse(await self.service(world, brain))
            brain._pending[0].result(timeout=1)
            self.assertFalse(await self.service(world, brain))
            self.assertEqual(world.location, "Town")
            self.assertEqual(world.ops, [])
            self.assertFalse(hasattr(brain, "daily"), "daily不再承担行为日程")
        finally:
            brain.close()

    def test_model_wire_input_keeps_structured_skills_memory_and_player_intent(self):
        captured = []
        class Reply:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                return json.dumps({"choices": [{"message": {"content": '{"steps":[]}'}}]}).encode()
        def send(request, timeout):
            captured.append(json.loads(request.data))
            return Reply()
        with patch.object(planner, "_cfg", return_value=("test-key", "https://example.invalid/api", "test")), \
             patch.object(planner.urllib.request, "urlopen", send):
            planner.plan_work('{"skills":[{"name":"explore"}],"memory":{"save_id":"A"}}', {"objective": "探索村庄"}, "不要卖东西")
        user = json.loads(captured[0]["messages"][1]["content"])
        self.assertIsInstance(user["state"], dict)
        self.assertEqual(user["state"]["skills"][0]["name"], "explore")
        self.assertEqual(user["player_task"], "不要卖东西")
        self.assertEqual(user["execution_context"]["objective"], "探索村庄")


if __name__ == "__main__":
    unittest.main()
