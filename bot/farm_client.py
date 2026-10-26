"""AgentStardew 桥的 Python 客户端。

连 ws://127.0.0.1:17654/，发 JSON 请求，收 JSON 响应。
官方协议：{protocolVersion, requestId, command, args, timeoutMs, actionId}

用法：
  python farm_client.py hello
  python farm_client.py status
  python farm_client.py snapshot --radius 8
  python farm_client.py move 12 8
  python farm_client.py select 2
  python farm_client.py use @s3:e12
"""
import argparse
import asyncio
import json
import sys
import uuid

ENDPOINT = "ws://127.0.0.1:17654/"
PROTO = 1
WRITE_CMDS = {"move", "select", "use", "interact", "menu"}


class BridgeError(Exception):
    pass


class Bridge:
    def __init__(self, endpoint=ENDPOINT):
        self.endpoint = endpoint
        self.ws = None
        self._hello_done = False

    async def __aenter__(self):
        import websockets
        self.ws = await websockets.connect(self.endpoint, origin=None, max_size=8 * 1024 * 1024)
        return self

    async def __aexit__(self, *exc):
        if self.ws is not None:
            await self.ws.close()
            self.ws = None

    async def request(self, command, args=None, timeout_ms=15000):
        if command != "hello" and not self._hello_done:
            await self.hello()
        args = args or {}
        req_id = "r" + uuid.uuid4().hex[:12]
        payload = {
            "protocolVersion": PROTO,
            "requestId": req_id,
            "command": command,
            "args": args,
            "timeoutMs": int(timeout_ms),
        }
        if command in WRITE_CMDS:
            payload["actionId"] = "a" + uuid.uuid4().hex[:16]
        await self.ws.send(json.dumps(payload, ensure_ascii=False))
        deadline = asyncio.get_event_loop().time() + timeout_ms / 1000.0 + 5
        while True:
            left = deadline - asyncio.get_event_loop().time()
            if left <= 0:
                raise BridgeError("等响应超时：" + command)
            raw = await asyncio.wait_for(self.ws.recv(), timeout=left)
            msg = json.loads(raw)
            if msg.get("requestId") != req_id:
                continue
            if command == "hello" and msg.get("status") == "completed":
                self._hello_done = True
            if msg.get("status") == "completed":
                return msg.get("result")
            err = msg.get("error") or {}
            raise BridgeError("%s: %s" % (err.get("code") or msg.get("status"), err.get("message") or ""))

    async def hello(self):
        return await self.request("hello")

    async def snapshot(self, radius=8):
        return await self.request("snapshot", {"radius": int(radius)})

    async def status(self):
        return await self.request("status")

    async def move(self, x=None, y=None, near=None):
        if near:
            return await self.request("move", {"near": near})
        return await self.request("move", {"tile": {"x": int(x), "y": int(y)}})

    async def select(self, slot):
        return await self.request("select", {"slot": int(slot)})

    async def use(self, ref):
        return await self.request("use", {"target": ref})

    async def interact(self, ref):
        return await self.request("interact", {"target": ref})

    async def menu(self, ref):
        return await self.request("menu", {"target": ref})


def summarize(snap):
    if not snap:
        return {}
    p = snap.get("player") or {}
    obs = snap.get("observation") or {}
    inv = snap.get("inventory") or []
    ents = obs.get("entities") or []
    tiles = obs.get("tiles") or []
    return {
        "mode": snap.get("mode"),
        "date": snap.get("date"),
        "time": snap.get("time"),
        "player": p,
        "inventory": inv,
        "entities": len(ents),
        "tiles": len(tiles),
        "menu": snap.get("menu"),
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--radius", type=int, default=8)
    ap.add_argument("--endpoint", default=ENDPOINT)
    ap.add_argument("--raw", action="store_true")
    a = ap.parse_args()

    try:
        async with Bridge(a.endpoint) as b:
            if a.cmd == "hello":
                out = await b.hello()
            elif a.cmd == "status":
                out = await b.status()
            elif a.cmd == "snapshot":
                out = await b.snapshot(a.radius)
                if not a.raw:
                    out = summarize(out)
            elif a.cmd == "move":
                out = await b.move(int(a.args[0]), int(a.args[1]))
            elif a.cmd == "select":
                out = await b.select(int(a.args[0]))
            elif a.cmd in ("use", "interact", "menu"):
                out = await getattr(b, a.cmd)(a.args[0])
            if not a.raw and isinstance(out, dict) and isinstance(out.get("snapshot"), dict):
                snap = out["snapshot"]
                out = {k: v for k, v in out.items() if k != "snapshot"}
                out["snapshot"] = summarize(snap)
            else:
                print("未知命令", file=sys.stderr)
                return 2
    except Exception as e:  # noqa: BLE001
        print("ERR %s: %s" % (type(e).__name__, e), file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
