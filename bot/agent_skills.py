"""模型自主选择的游戏技能；移动和探索依据现场/记忆，不依据预设地点路线。"""
import asyncio
import hashlib
import uuid

from exploration import candidates, path_to, region_route
from farmer import tool_slot, seeds_in, water_can, CLEAR_TOOLS
import farm_layout
from memory_store import dumps, position, scene, semantic, target_id
from skill_catalog import available, catalog
from skill_fishing import FishingSkill


def menu_key(snap):
    menu = snap.get("menu") or {}
    return [snap.get("location"), menu.get("identity"), menu.get("type"), menu.get("text")]


def menu_targets(snap):
    menu = snap.get("menu") or {}
    return [{"id": "menu:" + hashlib.sha256(dumps([menu_key(snap), o.get("label"), i]).encode()).hexdigest()[:16],
             "label": o.get("label"), "ref": o.get("ref")} for i, o in enumerate(menu.get("options") or [])]


def menu_controls(snap, details):
    controls = []
    for item in (details or {}).get("items") or []:
        if all(isinstance(item.get(k), int) for k in ("x", "y", "w", "h")) and item["w"] > 0 and item["h"] > 0:
            identity = "control:" + hashlib.sha256(dumps([menu_key(snap), item]).encode()).hexdigest()[:16]
            controls.append(dict(item, id=identity))
    return controls[:100]


def success(detail, output=None, progress=True, repeat=False, arg=None):
    return {"status": "succeeded" if progress else "no_progress", "progress": progress,
            "detail": detail, "output": output, "repeat": repeat, "remaining_arg": arg}


class SkillRuntime:
    DEFAULT_STEPS = 24      # 模型没指定 steps 时的上限（整段路径）
    def __init__(self, memory, data_dir, log=print, interrupted=lambda: False, fishing=None):
        self.memory, self.log, self.interrupted = memory, log, interrupted
        self.fishing = fishing or FishingSkill(data_dir)
        self.menu_details = None
        self.menu_identity = None
        self.last_result = {}
        self._last_observation = None

    def ready(self, action, snap):
        allowed, _ = available(action["action"], snap, action.get("arg"))
        return allowed and not self.memory.blocked(action, snap)

    def context(self, snap, objective=""):
        p = position(snap)
        entities = list(snap.get("entities") or [])
        important = {"npc", "exit", "door", "bed", "furniture", "action", "object"}
        entities.sort(key=lambda e: (e.get("kind") not in important,
            abs((e.get("tile") or {}).get("x", 0) - (p["x"] or 0)) + abs((e.get("tile") or {}).get("y", 0) - (p["y"] or 0))))
        targets = [{"id": target_id(snap.get("location"), e), **semantic(e)} for e in entities[:64]]
        skills = catalog(snap)
        fish = next(x for x in skills if x["name"] == "fish")
        if fish["available"]:
            try:
                state = self.fishing.status()
                if state.get("active") or state.get("automaticPaused"):
                    fish.update(available=False, unavailable_reason="钓鱼被占用或玩家暂停")
            except (OSError, ValueError):
                fish.update(available=False, unavailable_reason="独立钓鱼Mod没有有效心跳，需要加载Mod")
        menu = snap.get("menu") or {}
        details = self.menu_details if self.menu_identity == menu_key(snap) else None
        return {"scene": scene(snap), "date": snap.get("date"), "time": snap.get("time"),
                "player": snap.get("player"), "inventory": snap.get("inventory") or [],
                "menu": {"type": menu.get("type"), "text": menu.get("text"),
                         "options": [{k: v for k, v in o.items() if k != "ref"} for o in menu_targets(snap)],
                         "controls": menu_controls(snap, details)},
                "menu_details": details if menu else None, "targets": targets,
                "skills": skills, "exploration_candidates": candidates(snap, self.memory),
                "memory": self.memory.context(snap), "objective": objective,
                "previous_result": self.last_result}

    def _target(self, snap, identity):
        target = next((e for e in snap.get("entities") or [] if target_id(snap.get("location"), e) == identity), None)
        if target is None:
            raise ValueError("目标不在当前观察中；请observe或先走近已记住的位置")
        return target

    async def _move(self, b, snap, x, y, steps=None):
        """走一步棋：本次最多走 steps 格（由模型决定，1..24；不给就按整段路径）。"""
        path = path_to(snap, self.memory, (x, y))
        if path is None:
            raise ValueError("没有已观察的可通行路径；请从探索候选选择前沿，不猜路线")
        try:
            limit = int(steps) if steps is not None else self.DEFAULT_STEPS
        except (TypeError, ValueError):
            limit = self.DEFAULT_STEPS
        limit = max(1, min(24, limit))
        initial = scene(snap)
        for px, py in path[:limit]:
            if self.interrupted():
                return snap, success("移动被新指令或STOP中断", progress=False)
            await b.move(x=px, y=py)
            snap = await b.snapshot(16)
            if scene(snap) != initial:
                return snap, success("实际进入了新区域", output={"scene": scene(snap)})
            if position(snap) != {"x": px, "y": py}:
                raise ValueError("当前位置与计划步不符，停止继续走旧路径")
        arrived = position(snap) == {"x": x, "y": y}
        return snap, success("已到达" if arrived else "沿已知路径推进，下一段重新观察", progress=bool(path), repeat=not arrived)

    async def _near(self, b, snap, entity):
        p, t = position(snap), entity.get("tile") or {}
        distance = abs(p["x"] - t["x"]) + abs(p["y"] - t["y"])
        if distance <= 1:
            return snap, None
        choices = []
        for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
            end = (t["x"] + dx, t["y"] + dy)
            path = path_to(snap, self.memory, end)
            if path is not None:
                choices.append((len(path), end))
        if not choices:
            raise ValueError("目标旁边没有已知可达位置，先探索路径")
        _, end = min(choices)
        snap, result = await self._move(b, snap, *end)
        result["repeat"] = True
        result["detail"] = "正在靠近目标；交互/工具动作尚未发生"
        return snap, result

    async def _target_call(self, b, snap, identity, operation, slot=None):
        entity = self._target(snap, identity)
        if entity.get("kind") == "exit" and operation == "interact":
            t = entity["tile"]
            return await self._move(b, snap, t["x"], t["y"])
        snap, approach = await self._near(b, snap, entity)
        if approach:
            return snap, approach
        if slot is not None and (snap.get("player") or {}).get("selectedSlot") != slot:
            if not any(i.get("slot") == slot for i in snap.get("inventory") or []):
                raise ValueError("槽位中没有物品")
            await b.select(slot)
            snap = await b.snapshot(16)
        entity = self._target(snap, identity)  # 重新绑定当前ref，绝不从记忆重放旧ref。
        before = snap
        response = await (b.interact(entity["ref"]) if operation == "interact" else b.use(entity["ref"]))
        snap = await b.snapshot(16)
        def menu_state(value):
            menu = value.get("menu") or {}
            return [menu.get("type"), menu.get("text"), [o.get("label") for o in menu.get("options") or []]]
        def items(value):
            return [{k: i.get(k) for k in ("slot", "itemId", "name", "count", "water")} for i in value.get("inventory") or []]
        old_target = [semantic(e) for e in before.get("entities") or [] if e.get("tile") == entity.get("tile")]
        new_target = [semantic(e) for e in snap.get("entities") or [] if e.get("tile") == entity.get("tile")]
        changed = (response.get("changed") is True or old_target != new_target or items(before) != items(snap)
                   or menu_state(before) != menu_state(snap) or scene(before) != scene(snap))
        return snap, success(response.get("detail") or "操作返回，已重新观察", progress=bool(changed))

    async def _travel(self, b, snap, destination):
        if snap.get("location") == destination:
            return snap, success("已经在目标区域", progress=False)
        route = region_route(self.memory, snap, destination)
        if not route:
            raise ValueError("记忆中尚无到该区域的连接，请先探索可见出口")
        edge = route[0]
        known = edge["target"]
        identity = target_id(snap.get("location"), known)
        visible = next((e for e in snap.get("entities") or [] if target_id(snap.get("location"), e) == identity), None)
        if visible:
            snap, result = await self._target_call(b, snap, identity, "interact")
        else:
            snap, approach = await self._near(b, snap, known)
            result = approach or success("已到记忆连接附近，但现场未发现该目标；请观察验证", progress=False)
        result["repeat"] = snap.get("location") != destination and result["progress"]
        return snap, result

    async def execute(self, action, b, farmer, snap):
        before = snap
        intent = self.memory.append("intent", snap, source="model", confidence="hypothesis", status="selected", data=action)
        self.memory.active_action, self.memory.evidence = intent["id"], []
        try:
            if not self.ready(action, snap):
                raise ValueError("技能前提不满足或相同现场近期已失败，请查看记忆后换行动")
            snap, result = await self._execute(action["action"], action.get("arg") or {}, b, farmer, snap)
        except Exception as exc:
            result = {"status": "failed", "progress": False, "detail": str(exc)[:600], "error_type": type(exc).__name__}
            try:
                snap = await b.snapshot(16)
            except Exception:
                pass
        finally:
            self.memory.active_action = intent["id"]
        event = self.memory.result(action, before, snap, result)
        self.memory.active_action, self.memory.evidence = None, []
        self.last_result = {k: result.get(k) for k in ("status", "progress", "detail", "output")}
        self.last_result["skill"] = action["action"]
        if len(dumps(self.last_result)) > 10000:
            self.last_result["output"] = {"event_id": event["id"], "note": "详细结果已入库，可用memory检索"}
        self.log("技能 %s：%s（%s）" % (action["action"], result["status"], result.get("detail", "")))
        return snap, result

    FARM_WORK = {"clear", "use", "till", "plant", "harvest", "chop", "water", "till_plant"}
    ZONE_RADIUS = 12

    def _house_zone(self, snap):
        """农场 + 看得见农舍时，返回 (农舍格, 半径)；否则 None。"""
        if snap.get("location") != "Farm" or not farm_layout.has_house(snap):
            return None
        return farm_layout.house_tile(snap), int(getattr(self, "_zone_radius", self.ZONE_RADIUS))

    def _in_zone(self, snap, entity):
        zone = self._house_zone(snap)
        if not zone:
            return True
        house, radius = zone
        t = entity.get("tile") or {}
        return farm_layout.dist((t.get("x"), t.get("y")), house) <= radius

    def _zone_snap(self, snap, pred):
        """只保留圈内的"该技能关心的实体"，其余实体原样保留。"""
        zone = self._house_zone(snap)
        if not zone:
            return snap
        house, radius = zone
        keep = []
        for e in snap.get("entities") or []:
            try:
                relevant = bool(pred(e))
            except Exception:
                relevant = True
            if not relevant:
                keep.append(e)
                continue
            t = e.get("tile") or {}
            if farm_layout.dist((t.get("x"), t.get("y")), house) <= radius:
                keep.append(e)
        out = dict(snap)
        out["entities"] = keep
        return out

    def _enforce_zone(self, name, arg, snap):
        """执行前统一把关：圈外目标一律拒绝，自动选目标的技能只留圈内候选。"""
        if name not in self.FARM_WORK:
            return snap
        zone = self._house_zone(snap)
        if not zone:
            return snap
        house, radius = zone
        if arg.get("target"):
            entity = self._target(snap, arg["target"])
            if not self._in_zone(snap, entity):
                t = entity.get("tile") or {}
                raise ValueError(
                    "目标 (%s,%s) 离农舍 %d 格，超出当前作业半径 %d；先在农舍附近干活，"
                    "要扩圈用 explore 换个探索候选" % (t.get("x"), t.get("y"),
                                                        farm_layout.dist((t.get("x"), t.get("y")), house), radius))
            return snap
        if name == "harvest":
            return self._zone_snap(snap, lambda e: bool(e.get("crop")))
        if name == "water":
            return self._zone_snap(snap, lambda e: e.get("kind") == "soil" or bool(e.get("crop")))
        if name == "chop":
            return self._zone_snap(snap, lambda e: (e.get("kind") or "") in ("tree", "stump", "log")
                                  or (e.get("name") or "") in ("树", "树桩", "圆木"))
        if name == "till_plant":
            return self._zone_snap(snap, lambda e: bool(e.get("diggable") or e.get("tilled")))
        return snap

    async def _execute(self, name, arg, b, f, snap):
        snap = self._enforce_zone(name, arg or {}, snap)
        if name == "observe":
            snap = await b.snapshot(arg.get("radius", 16))
            if snap.get("menu"):
                self.menu_details = await b.request("menudump", {})
                self.menu_identity = menu_key(snap)
                self.memory.append("observation", snap, data={"menu_details": self.menu_details})
            signature = dumps([scene(snap), [semantic(e) for e in snap.get("entities") or []],
                               snap.get("inventory"), (snap.get("menu") or {}).get("text")])
            fresh = signature != self._last_observation
            self._last_observation = signature
            return snap, success("观察已更新" if fresh else "观察与上次相同，没有新证据", output=self.context(snap), progress=fresh)
        if name == "memory":
            if arg.get("op", "recall") == "remember":
                event = self.memory.note(arg["text"], arg.get("evidence") or [], snap=snap)
                return snap, success("假设笔记已记录，不等于已验证事实", output=event)
            return snap, success("已查询统一记忆", output={"events": self.memory.query(arg.get("query", ""), arg.get("kind"),
                arg.get("location"), arg.get("limit", 12), snap), "connections": self.memory.links(snap),
                "known_tiles": self.memory.tiles(arg.get("location"), snap)[:64]})
        if name == "wait":
            await asyncio.sleep(arg.get("seconds", 1))
            return await b.snapshot(16), success("短等待完成")
        if name == "select":
            if not any(i.get("slot") == arg.get("slot") for i in snap.get("inventory") or []):
                raise ValueError("只能选择现场存在的物品槽位")
            if (snap.get("player") or {}).get("selectedSlot") == arg["slot"]:
                return snap, success("该物品已经选中", progress=False)
            await b.select(arg["slot"])
            return await b.snapshot(16), success("已选择槽位")
        if name == "menu":
            option = next((o for o in menu_targets(snap) if o["id"] == arg.get("target")), None)
            if not option:
                raise ValueError("菜单已变化或没有该选项；重新observe")
            await b.menu(option["ref"])
            self.menu_details = None
            return await b.snapshot(16), success("已选择菜单实际选项", output={"label": option["label"]})
        if name == "menu_click":
            details = await b.request("menudump", {})
            control = next((c for c in menu_controls(snap, details) if c["id"] == arg.get("target")), None)
            if not control:
                raise ValueError("控件已变化或没有该控件；先observe，不猜屏幕坐标")
            result = await b.request("menuclick", {"x": control["x"] + control["w"] // 2, "y": control["y"] + control["h"] // 2})
            after = await b.snapshot(16)
            after_details = await b.request("menudump", {}) if after.get("menu") else {}
            changed = (menu_key(snap) != menu_key(after) or details != after_details
                       or snap.get("inventory") != after.get("inventory")
                       or (snap.get("player") or {}).get("money") != (after.get("player") or {}).get("money"))
            self.menu_details, self.menu_identity = after_details, menu_key(after)
            return after, success("已点击并复核现场控件", result, progress=changed)
        if name == "menu_close":
            result = await b.request("menuclose", {})
            self.menu_details = None
            after = await b.snapshot(16)
            return after, success("已请求关闭当前菜单", result, progress=menu_key(snap) != menu_key(after))
        if name == "move":
            if arg.get("location") and arg["location"] != snap.get("location"):
                return await self._travel(b, snap, arg["location"])
            if arg.get("target"):
                e = self._target(snap, arg["target"])
                if e.get("passable"):
                    return await self._move(b, snap, e["tile"]["x"], e["tile"]["y"])
                current, result = await self._near(b, snap, e)
                return current, result or success("已在目标旁边", progress=False)
            return await self._move(b, snap, arg["x"], arg["y"], steps=arg.get("steps"))
        if name == "travel":
            return await self._travel(b, snap, arg["to"])
        if name in ("interact", "use", "sleep", "till", "plant", "refill"):
            identity, slot = arg.get("target"), arg.get("slot")
            if name == "sleep" and not identity:
                e = next(e for e in snap.get("entities") or [] if e.get("kind") == "bed")
                identity = target_id(snap.get("location"), e)
            if name == "refill":
                e = self._target(snap, identity) if identity else next(e for e in snap.get("entities") or [] if e.get("refillable"))
                if not e.get("refillable"):
                    raise ValueError("该目标不是观察到的水源")
                identity, slot = target_id(snap.get("location"), e), water_can(snap)["slot"]
            if name == "till":
                e = self._target(snap, identity)
                if not e.get("diggable") or e.get("tilled") or e.get("crop"):
                    raise ValueError("目标不是空置可耕地")
                slot = tool_slot(snap, "Hoe")["slot"]
            if name == "plant" and slot is None:
                seed = next((i for i in snap.get("inventory") or [] if i.get("category") == -74 or "种子" in (i.get("name") or "") or "Seeds" in (i.get("name") or "")), None)
                if not seed:
                    raise ValueError("没有可用种子")
                slot = seed["slot"]
            return await self._target_call(b, snap, identity, "interact" if name in ("interact", "sleep") else "use", slot)
        if name == "explore":
            mode = arg.get("mode", "path")
            if mode == "path":
                chosen = next((c for c in candidates(snap, self.memory) if c["id"] == arg.get("candidate") and c["mode"] == "path"), None)
                x, y = (chosen["x"], chosen["y"]) if chosen else (arg.get("x"), arg.get("y"))
                if x is None or y is None:
                    raise ValueError("路径探索需要模型选择候选ID或已知可达坐标")
                return await self._move(b, snap, x, y, steps=arg.get("steps"))
            identity = arg.get("target") or arg.get("candidate")
            if mode == "capability":
                self.memory.note(arg.get("question") or "验证此技能在当前对象上的效果", snap=snap)
            return await self._target_call(b, snap, identity, arg.get("skill", "interact") if mode == "capability" else "interact", arg.get("slot"))
        if name == "water":
            count = min(3, arg.get("limit", 3), int((water_can(snap) or {}).get("water") or 0))
            if not count:
                raise ValueError("水壶空了，模型可选择refill或探索水源")
            snap = await f.water_crops(snap, limit=count)
            done = f.last_watered
        elif name == "harvest":
            snap = await f.harvest(snap, limit=min(3, arg.get("limit", 3)))
            done = f.last_harvest
        elif name == "chop":
            snap = await f.chop_trees(snap, want_wood=None, max_swings=min(3, arg.get("limit", 3)))
            done = f.last_chops
        elif name == "clear":
            if float((snap.get("player") or {}).get("energy") or 0) < arg.get("min_energy", 15):
                raise ValueError("体力低于本次委托的清理下限")
            if arg.get("target"):
                entity = self._target(snap, arg["target"])
            else:
                zone = self._house_zone(snap)
                if zone:
                    house, radius = zone
                    ents_clear = [e for e in snap.get("entities") or [] if e.get("name") in CLEAR_TOOLS
                                  and tool_slot(snap, CLEAR_TOOLS[e["name"]])]
                    near = [e for e in ents_clear
                            if farm_layout.dist(((e.get("tile") or {}).get("x"), (e.get("tile") or {}).get("y")),
                                                house) <= radius]
                    if not near:
                        # 圈内清完了：放大半径，交给下一轮继续
                        self._zone_radius = radius + 6
                        res = success("%d 格内已清完，作业半径放宽到 %d" % (radius, radius + 6),
                                      repeat=True, progress=False)
                        res["remaining_arg"] = dict(arg)
                        return snap, res
                    near.sort(key=lambda e: farm_layout.dist(
                        ((e.get("tile") or {}).get("x"), (e.get("tile") or {}).get("y")), house))
                    entity = near[0]
                    p0 = position(snap)
                    if farm_layout.dist((p0["x"], p0["y"]), house) > radius:
                        moved = await self._move(b, snap, house[0], min(house[1] + 2, 30))
                        snap = moved[0]
                        res = success("先去农舍 %d 格作业圈（现在离农舍 %d 格）" % (
                            radius, farm_layout.dist((p0["x"], p0["y"]), house)), repeat=True)
                        res["remaining_arg"] = dict(arg)
                        return snap, res
                else:
                    ents_clear = [e for e in snap.get("entities") or [] if e.get("name") in CLEAR_TOOLS
                                  and tool_slot(snap, CLEAR_TOOLS[e["name"]])]
                    if not ents_clear:
                        raise ValueError("观察中没有可用工具处理的障碍")
                    p0 = position(snap)
                    ents_clear.sort(key=lambda e: abs(e["tile"]["x"] - p0["x"]) + abs(e["tile"]["y"] - p0["y"]))
                    entity = ents_clear[0]
            tool = tool_slot(snap, CLEAR_TOOLS.get(entity.get("name")))
            if not tool:
                raise ValueError("当前工具无法处理该障碍")
            snap, result = await self._target_call(b, snap, target_id(snap.get("location"), entity),
                                                   "use", tool["slot"])
            if arg.get("target"):
                return snap, result
            remaining = arg.get("limit", 3) - (0 if result.get("repeat") else 1)
            result["repeat"] = result["progress"] and remaining > 0
            result["remaining_arg"] = dict(arg, limit=remaining) if remaining else None
            return snap, result
        elif name == "till_plant":
            targets = [e for e in snap.get("entities") or [] if not e.get("crop") and (e.get("tilled") or e.get("diggable"))]
            if not targets:
                raise ValueError("当前没有已观察到的可种地")
            # 农舍周边优先；同一圈里先种已经翻好的（不用再挖）
            house = farm_layout.house_tile(snap)
            targets.sort(key=lambda e: (farm_layout.dist(
                ((e.get("tile") or {}).get("x"), (e.get("tile") or {}).get("y")), house),
                0 if e.get("tilled") else 1))
            e = targets[0]
            if not e.get("tilled"):
                snap, result = await self._execute("till", {"target": target_id(snap.get("location"), e)}, b, f, snap)
                result["repeat"] = result["progress"]
                result["remaining_arg"] = arg
                return snap, result
            snap, result = await self._execute("plant", {"target": target_id(snap.get("location"), e)}, b, f, snap)
            remaining = arg.get("limit", 3) - (0 if result.get("repeat") else 1)
            result["repeat"] = result["progress"] and remaining > 0
            result["remaining_arg"] = dict(arg, limit=remaining) if remaining else None
            return snap, result
        elif name == "buy_seeds":
            details = await b.request("menudump", {})
            item = next((i for i in details.get("items") or [] if i.get("index") == arg.get("index")), None)
            if not item:
                raise ValueError("商品不在当前商店列表，先observe查商品index")
            if not arg.get("item") or item.get("name") != arg["item"]:
                raise ValueError("需要明确商品名称并与当前商店复核，不能只按旧index购买")
            result = await b.request("shopbuy", {"index": arg["index"], "qty": arg.get("qty", 1)})
            if not result.get("ok"):
                raise ValueError(str(result.get("reason") or "购买失败"))
            return await b.snapshot(16), success("已按模型选择购买", result)
        elif name in ("sell", "store"):
            slot = arg.get("slot")
            item = next((i for i in snap.get("inventory") or [] if i.get("slot") == slot), None)
            if not item:
                raise ValueError("需要模型明确选择一个当前物品槽位")
            args = {"slot": slot}
            if name == "store":
                entity = self._target(snap, arg.get("target"))
                if "Chest" not in str(entity.get("objType")) and "箱" not in str(entity.get("name")):
                    raise ValueError("目标没有箱子证据")
                snap, approach = await self._near(b, snap, entity)
                if approach:
                    return snap, approach
                args.update(entity["tile"])
            result = await b.request("ship" if name == "sell" else "chestput", args)
            if not result.get("ok"):
                raise ValueError(str(result.get("reason") or "存放/出售失败"))
            return await b.snapshot(16), success("已处理指定物品", result)
        elif name == "fish":
            async def observe():
                await b.snapshot(16)
            result = await self.fishing.run(arg.get("max_seconds", 60), self.interrupted, observe)
            return await b.snapshot(16), result
        else:
            raise ValueError("未实现的技能")
        remaining = max(0, arg.get("limit", 3) - done)
        return snap, success("实际完成%d个目标" % done, output={"count": done}, progress=done > 0,
            repeat=remaining > 0 and done > 0, arg=dict(arg, limit=remaining) if remaining else None)

    async def close(self):
        await self.fishing.stop()
