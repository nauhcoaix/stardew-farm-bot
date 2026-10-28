"""白名单 3：游戏桥客户端（用替身传输，不需要游戏）。"""

from __future__ import annotations

import asyncio
import json

import pytest

from stardew_agent.bridge_client import Bridge, BridgeError
from stardew_agent.contracts.errors import OwnershipBusy, ProtocolError, ResultUnknown, TransportError


class QueueTransport:
    """把请求记下来，由测试决定什么时候回什么。"""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.queue: asyncio.Queue = asyncio.Queue()
        self.closed = False
        self.fail_on_recv: BaseException | None = None

    async def send(self, text: str) -> None:
        request = json.loads(text)
        self.sent.append(request)
        if request["command"] == "hello":
            await self.reply(request, {"ok": True})

    async def reply(self, request: dict, result, *, status: str = "completed", error=None) -> None:
        message = {"protocolVersion": 1, "requestId": request["requestId"], "status": status}
        if result is not None:
            message["result"] = result
        if error is not None:
            message["error"] = error
        await self.queue.put(json.dumps(message, ensure_ascii=False))

    async def recv(self) -> str:
        if self.fail_on_recv is not None:
            raise self.fail_on_recv
        return await self.queue.get()

    async def close(self) -> None:
        self.closed = True


def test_自动完成hello握手并串行请求():
    async def scenario():
        transport = QueueTransport()
        bridge = Bridge(transport)
        task = asyncio.create_task(bridge.snapshot(radius=6))
        await asyncio.sleep(0.05)
        assert [row["command"] for row in transport.sent] == ["hello", "snapshot"]
        assert transport.sent[1]["args"] == {"radius": 6}
        await transport.reply(transport.sent[1], {"mode": "game"})
        return await task

    result = asyncio.run(scenario())
    assert result == {"mode": "game"}


def test_商店与菜单命令的读写语义():
    async def scenario():
        transport = QueueTransport()
        bridge = Bridge(transport)

        task = asyncio.create_task(bridge.menudump())
        await asyncio.sleep(0.05)
        request = transport.sent[-1]
        assert request["command"] == "menudump"
        assert "actionId" not in request
        await transport.reply(request, {"type": "ShopMenu", "items": []})
        await task

        task = asyncio.create_task(bridge.shopbuy(2, 3))
        await asyncio.sleep(0.05)
        request = transport.sent[-1]
        assert request["command"] == "shopbuy"
        assert request["args"] == {"index": 2, "qty": 3}
        assert request["actionId"].startswith("a")
        await transport.reply(request, {"ok": True})
        await task

        for name, call, args in (
            ("menuclick", lambda: bridge.menuclick(10, 20), {"x": 10, "y": 20}),
            ("menuclickinv", lambda: bridge.menuclickinv(30, 40), {"x": 30, "y": 40}),
            ("menuclose", lambda: bridge.menuclose(), {}),
        ):
            task = asyncio.create_task(call())
            await asyncio.sleep(0.02)
            request = transport.sent[-1]
            assert request["command"] == name
            assert request["args"] == args
            assert request["actionId"].startswith("a")
            await transport.reply(request, {"ok": True})
            await task

    asyncio.run(scenario())


def test_写命令带动作ID读命令不带():
    async def scenario():
        transport = QueueTransport()
        bridge = Bridge(transport)
        task = asyncio.create_task(bridge.use("@s1:e1"))
        await asyncio.sleep(0.05)
        request = transport.sent[-1]
        assert request["command"] == "use"
        assert request["actionId"].startswith("a")
        await transport.reply(request, {"changed": True})
        await task
        status = asyncio.create_task(bridge.status())
        await asyncio.sleep(0.01)
        assert "actionId" not in transport.sent[-1]
        await transport.reply(transport.sent[-1], {"mode": "game"})
        await status

    asyncio.run(scenario())


def test_同一条连接不接受并发请求():
    async def scenario():
        transport = QueueTransport()
        bridge = Bridge(transport)
        first = asyncio.create_task(bridge.snapshot())
        await asyncio.sleep(0.05)
        with pytest.raises(OwnershipBusy):
            await bridge.request("status")
        await transport.reply(transport.sent[-1], {})
        await first

    asyncio.run(scenario())


def test_不匹配的响应是协议错误而不是静默跳过():
    async def scenario():
        transport = QueueTransport()
        bridge = Bridge(transport)
        task = asyncio.create_task(bridge.snapshot())
        await asyncio.sleep(0.05)
        await transport.queue.put(json.dumps({"requestId": "other", "status": "completed", "result": {}}))
        with pytest.raises(ProtocolError):
            await task

    asyncio.run(scenario())


def test_写命令超时是结果未知():
    async def scenario():
        transport = QueueTransport()
        bridge = Bridge(transport, timeout_grace_seconds=0.01)
        with pytest.raises(ResultUnknown):
            await bridge.use("@s1:e1", timeout_ms=1)

    asyncio.run(scenario())


def test_读命令超时是传输错误():
    async def scenario():
        transport = QueueTransport()
        bridge = Bridge(transport, timeout_grace_seconds=0.01)
        with pytest.raises(TransportError):
            await bridge.request("status", timeout_ms=1)

    asyncio.run(scenario())


def test_连接中断区分读写语义():
    async def scenario():
        transport = QueueTransport()
        bridge = Bridge(transport)
        await bridge.hello()  # 完成握手
        transport.fail_on_recv = ConnectionResetError("boom")
        with pytest.raises(TransportError):
            await bridge.request("snapshot", {"radius": 4})
        with pytest.raises(ResultUnknown):
            await bridge.move(1, 2)

    asyncio.run(scenario())


def test_桥返回的错误带错误码():
    async def scenario():
        transport = QueueTransport()
        bridge = Bridge(transport)
        task = asyncio.create_task(bridge.interact("@s1:e2"))
        await asyncio.sleep(0.05)
        await transport.reply(
            transport.sent[-1], None, status="failed", error={"code": "STALE_REF", "message": "引用过期"}
        )
        with pytest.raises(BridgeError) as excinfo:
            await task
        assert excinfo.value.code == "STALE_REF"

    asyncio.run(scenario())
