"""模型明确选择fish时才移交给现有独立Mod；不选择岸边或路线。"""
import asyncio
import importlib.util
import os
from pathlib import Path
import time


class FishingSkill:
    def __init__(self, data_dir, client=None, runtime=None, clock=time.monotonic):
        parent = Path(data_dir).resolve().parent
        game = Path(os.environ.get("STARDEW_GAME_PATH") or
                    (parent if (parent / "Stardew Valley.dll").exists() else r"C:\Program Files (x86)\Steam\steamapps\common\Stardew Valley"))
        self.runtime = Path(runtime) if runtime else game / "Mods/StandaloneFishingSkill/runtime"
        self.client, self.clock = client, clock
        self.paths = [Path(__file__).resolve().parents[1] / "skills/stardew-fishing/scripts/fish.py",
                      game / "Mods/StandaloneFishingSkill/fish_client.py"]
        self.owned = None
        self.confirmed = False
        self.cancel_id = None
        self.saw_active = False

    def _client(self):
        if self.client is None:
            path = next((p for p in self.paths if p.exists()), None)
            if not path:
                raise ValueError("未安装独立钓鱼Skill")
            spec = importlib.util.spec_from_file_location("agent_fishing_client", path)
            self.client = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.client)
        return self.client

    def status(self):
        result = self._client().read_status(self.runtime)
        if result.get("active"):
            self.saw_active = True
        return result

    async def stop(self):
        if not self.owned:
            return
        try:
            status = self.status()
            if status.get("runId") == self.owned and not status.get("active"):
                self.owned = None
                return
            if self.confirmed and status.get("runId") != self.owned:
                self.owned = None
                return
        except (OSError, ValueError):
            pass
        if not self.cancel_id:
            try:
                self.cancel_id = self._client().submit_command(self.runtime, "stop", run_id=self.owned)["id"]
            except (OSError, ValueError):
                return
        deadline = self.clock() + 5
        while self.clock() < deadline:
            try:
                status = self.status()
                ack = status.get("ack") or {}
                if (status.get("runId") == self.owned and not status.get("active")) or (
                        ack.get("id") == self.cancel_id and ack.get("reason") == "different_run" and not status.get("active")):
                    self.owned = None
                    return
            except (OSError, ValueError):
                pass
            await asyncio.sleep(0.1)

    async def busy(self):
        if self.owned:
            await self.stop()
            if self.owned:
                return True
        try:
            status = self.status()
        except (OSError, ValueError):
            return self.saw_active
        if status.get("active") or (self.saw_active and
                (status.get("menu") == "BobberBar" or (status.get("playerBusy") and not status.get("menu")))):
            return True
        self.saw_active = False
        return False

    async def run(self, seconds, interrupted, observe):
        status = self.status()
        if status.get("active") or status.get("automaticPaused"):
            raise ValueError("钓鱼已被其他控制者占用或玩家F8暂停")
        cmd = self._client().submit_command(self.runtime, "start", {
            "maxCasts": 1, "targetFish": 0, "maxSeconds": seconds,
            "minEnergy": 20, "stopAt": 2300, "facing": -1})
        self.owned, self.confirmed, self.cancel_id = cmd["id"], False, None
        self.saw_active = True
        deadline = self.clock() + 5
        try:
            while self.clock() < deadline:
                status = self.status()
                ack = status.get("ack") or {}
                if ack.get("id") == self.owned:
                    if not ack.get("accepted"):
                        self.owned = None
                        raise ValueError("钓鱼被拒绝：" + str(ack.get("reason")))
                    self.confirmed = True
                    break
                await asyncio.sleep(0.1)
            if not self.confirmed:
                raise ValueError("钓鱼启动未确认；等待释放控制，不能继续农务")
            deadline = self.clock() + seconds + 5
            while status.get("active"):
                if interrupted() or self.clock() >= deadline:
                    break
                await asyncio.sleep(0.5)
                status = self.status()
                if status.get("runId") != self.owned:
                    raise ValueError("钓鱼任务被替换")
                await observe()
            gained = (status.get("fish") or 0) + (status.get("trash") or 0)
            return {"status": "succeeded" if gained else "no_progress", "progress": bool(gained),
                    "detail": status.get("reason") or "钓鱼被中断", "output": status}
        finally:
            await self.stop()
