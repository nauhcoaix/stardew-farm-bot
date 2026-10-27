"""游戏连接的记忆适配器：所有技能共用，不靠各调用方自行补日志。"""
from memory_store import semantic, target_id


class ObservedBridge:
    def __init__(self, bridge, memory):
        self.raw, self.memory = bridge, memory
        self.last = None

    async def __aenter__(self):
        await self.raw.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.raw.__aexit__(*args)

    async def snapshot(self, radius=16):
        snap = await self.raw.snapshot(radius)
        self.memory.observe(snap)
        self.last = snap
        return snap

    async def _call(self, command, args, call):
        before = self.last or await self.snapshot()
        reference = args.get("target_ref") or args.get("near")
        point = {"x": args["x"], "y": args["y"]} if args.get("x") is not None and args.get("y") is not None else None
        target = next((e for e in before.get("entities") or []
                       if ((reference is not None and e.get("ref") == reference)
                           or (point is not None and e.get("tile") == point))
                       and e.get("kind") in ("exit", "door", "action")), None)
        self.memory.transition_target = semantic(target) if target else None
        recorded_args = {k: v for k, v in args.items() if k not in ("target_ref", "near") and v is not None}
        if reference:
            referenced = next((e for e in before.get("entities") or [] if e.get("ref") == reference), None)
            recorded_args["target"] = target_id(before.get("location"), referenced) if referenced else "unknown_reference"
        try:
            result = await call()
            snap = (result or {}).get("snapshot") if isinstance(result, dict) else None
            snap = snap or await self.raw.snapshot(16)
            self.memory.observe(snap)
            self.last = snap
            self.memory.append("skill_call", snap, source="executor", status="failed" if isinstance(result, dict) and result.get("ok") is False else "succeeded",
                data={"skill": "bridge." + command, "args": recorded_args, "detail": {k: v for k, v in (result or {}).items()
                      if k not in ("snapshot", "base64")} if isinstance(result, dict) else result})
            return result
        except Exception as exc:
            self.memory.append("skill_call", before, source="executor", status="failed",
                               data={"skill": "bridge." + command, "args": recorded_args, "error": str(exc)[:500]})
            # 失败也观察现场：工具可能已扣体力，或人物已走了部分路线。
            try:
                self.last = await self.raw.snapshot(16)
                self.memory.observe(self.last)
            except Exception:
                pass
            raise
        finally:
            self.memory.transition_target = None

    async def move(self, x=None, y=None, near=None):
        return await self._call("move", {"x": x, "y": y, "near": near}, lambda: self.raw.move(x=x, y=y, near=near))

    async def select(self, slot):
        return await self._call("select", {"slot": slot}, lambda: self.raw.select(slot))

    async def use(self, ref):
        return await self._call("use", {"target_ref": ref}, lambda: self.raw.use(ref))

    async def interact(self, ref):
        return await self._call("interact", {"target_ref": ref}, lambda: self.raw.interact(ref))

    async def menu(self, ref):
        return await self._call("menu", {"target_ref": ref}, lambda: self.raw.menu(ref))

    async def request(self, command, args=None, **kwargs):
        if command in ("snapshot", "status", "hello", "menudump", "bin"):
            result = await self.raw.request(command, args or {}, **kwargs)
            if command == "snapshot":
                self.memory.observe(result)
                self.last = result
            return result
        return await self._call(command, args or {}, lambda: self.raw.request(command, args or {}, **kwargs))

    async def reset(self):
        return await self._call("reset", {}, self.raw.reset)
