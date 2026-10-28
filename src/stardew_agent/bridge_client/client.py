"""游戏桥 WebSocket 客户端（来源 ``stardew_valley_dfy/bot/farm_client.py``）。

保留：连接与 hello 握手、带请求 ID 的 JSON 请求、超时与协议错误、核心写命令的
动作 ID、快照/状态/移动/选择/使用/交互/菜单/配方/制作的薄封装。

相对旧实现的行为修正（重构需求第 2 节）:

1. **一条连接同时只有一个请求在途**。旧客户端没有请求分发，跳过不匹配响应；
   这里并发调用直接报 ``OwnershipBusy``，让调用方显式排队。
2. **不匹配的响应是协议错误**，不再被静默跳过，避免"以为拿到了结果"。
3. **超时按命令类型区分**：写命令超时抛 ``ResultUnknown``（可能已作用于游戏），
   读命令超时抛 ``TransportError``。调用方据此先观察，而不是重发购买/制造。

本模块只做通信：不认识 Farmer、SkillRuntime、旧 WorkPlanner，也不决定做什么。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Awaitable, Callable, Mapping, Protocol

from ..contracts.errors import AgentError, OwnershipBusy, ProtocolError, ResultUnknown, TransportError
from ..contracts.version import BRIDGE_PROTOCOL_VERSION

DEFAULT_ENDPOINT = "ws://127.0.0.1:17654/"
#: 用于游戏桥有"动作 ID 去重 + 结果缓存"保障的写命令
WRITE_COMMANDS = frozenset(
    {
        "move",
        "select",
        "use",
        "interact",
        "menu",
        "menuclick",
        "menuclickinv",
        "menuclose",
        "shopbuy",
        "craft",
        "chestput",
        "chestget",
        "ship",
        "placechest",
    }
)
MAX_MESSAGE_BYTES = 8 * 1024 * 1024


class BridgeError(AgentError):
    """游戏桥明确返回失败（例如 STALE_REF、BUSY）。"""

    code = "BRIDGE"

    def __init__(self, message: str, *, code: str = "BRIDGE", retryable: bool = False, status: str = "failed") -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status = status


class Transport(Protocol):
    """最小传输接口，便于用替身测试（不启动游戏）。"""

    async def send(self, text: str) -> None: ...

    async def recv(self) -> str: ...

    async def close(self) -> None: ...


async def connect_websocket(endpoint: str) -> Transport:
    """默认传输：本机 WebSocket。"""
    import websockets

    return await websockets.connect(endpoint, origin=None, max_size=MAX_MESSAGE_BYTES)


class Bridge:
    """游戏桥连接。请求-响应严格串行，错误语义明确。"""

    def __init__(
        self,
        transport: Transport | None = None,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        protocol: int = BRIDGE_PROTOCOL_VERSION,
        default_timeout_ms: int = 15000,
        connector: Callable[[str], Awaitable[Transport]] | None = None,
        timeout_grace_seconds: float = 5.0,
    ) -> None:
        self.endpoint = endpoint
        self.protocol = int(protocol)
        self.default_timeout_ms = int(default_timeout_ms)
        self.timeout_grace_seconds = float(timeout_grace_seconds)
        self._transport = transport
        self._connector = connector or connect_websocket
        self._hello_done = False
        self._inflight = False
        self.last_action_id: str | None = None

    # ---- 生命周期 -------------------------------------------------
    async def __aenter__(self) -> "Bridge":
        if self._transport is None:
            self._transport = await self._connector(self.endpoint)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._transport is not None:
            await self._transport.close()
            self._transport = None
        self._hello_done = False

    # ---- 请求 -----------------------------------------------------
    async def request(
        self,
        command: str,
        args: Mapping[str, Any] | None = None,
        *,
        timeout_ms: int | None = None,
        action_id: str | None = None,
    ) -> Any:
        """发送一次请求并等待匹配的响应。

        抛出：``ResultUnknown``（写命令超时/连接中断）、``TransportError``（读命令失败）、
        ``ProtocolError``、``BridgeError``、``OwnershipBusy``。
        """
        if self._transport is None:
            raise TransportError("游戏桥未连接")
        if self._inflight:
            raise OwnershipBusy("同一条游戏桥连接已有请求在途；调用方需要自行串行")
        if command != "hello" and not self._hello_done:
            await self.hello()
        self._inflight = True
        try:
            request_id = "r" + uuid.uuid4().hex[:12]
            write = command in WRITE_COMMANDS
            if write and action_id is None:
                action_id = "a" + uuid.uuid4().hex[:16]
            payload: dict[str, Any] = {
                "protocolVersion": self.protocol,
                "requestId": request_id,
                "command": command,
                "args": dict(args or {}),
                "timeoutMs": int(timeout_ms or self.default_timeout_ms),
            }
            if write and action_id:
                payload["actionId"] = action_id
            self.last_action_id = action_id if write else None
            try:
                await self._transport.send(json.dumps(payload, ensure_ascii=False))
            except Exception as exc:  # noqa: BLE001 - 传输层异常按类型归一
                raise self._unconfirmed(command, "发送失败：%s" % type(exc).__name__) from exc
            message = await self._recv_matching(request_id, command, payload["timeoutMs"])
            if command == "hello" and message.get("status") == "completed":
                self._hello_done = True
            if message.get("status") == "completed":
                return message.get("result")
            error = message.get("error") or {}
            raise BridgeError(
                "%s: %s" % (error.get("code") or message.get("status"), error.get("message") or ""),
                code=str(error.get("code") or message.get("status") or "FAILED"),
                retryable=bool(error.get("retryable")),
                status=str(message.get("status") or "failed"),
            )
        finally:
            self._inflight = False

    async def _recv_matching(self, request_id: str, command: str, timeout_ms: int) -> dict:
        assert self._transport is not None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_ms / 1000.0 + self.timeout_grace_seconds
        while True:
            left = deadline - loop.time()
            if left <= 0:
                raise self._unconfirmed(command, "等待响应超时：%s" % command)
            try:
                raw = await asyncio.wait_for(self._transport.recv(), timeout=left)
            except asyncio.TimeoutError as exc:
                raise self._unconfirmed(command, "等待响应超时：%s" % command) from exc
            except Exception as exc:  # noqa: BLE001
                raise self._unconfirmed(command, "连接中断：%s" % type(exc).__name__) from exc
            try:
                message = json.loads(raw)
            except (TypeError, ValueError) as exc:
                raise ProtocolError("游戏桥返回了无法解析的消息") from exc
            if message.get("requestId") != request_id:
                raise ProtocolError(
                    "收到不属于本请求的响应（requestId=%s），不能当作本次结果" % message.get("requestId")
                )
            return message

    def _unconfirmed(self, command: str, detail: str) -> AgentError:
        if command in WRITE_COMMANDS:
            return ResultUnknown("%s；写操作可能已作用于游戏，先重新观察再决定" % detail)
        return TransportError(detail)

    # ---- 薄封装 ---------------------------------------------------
    async def hello(self) -> Any:
        return await self.request("hello")

    async def snapshot(self, radius: int = 8) -> dict:
        result = await self.request("snapshot", {"radius": int(radius)})
        return result if isinstance(result, dict) else {}

    async def status(self) -> dict:
        result = await self.request("status")
        return result if isinstance(result, dict) else {}

    async def move(
        self, x: int | None = None, y: int | None = None, near: str | None = None, **kwargs: Any
    ) -> dict:
        if near:
            args: dict[str, Any] = {"near": near}
        else:
            args = {"tile": {"x": int(x), "y": int(y)}}  # type: ignore[arg-type]
        return await self.request("move", args, **kwargs)

    async def select(self, slot: int, **kwargs: Any) -> dict:
        return await self.request("select", {"slot": int(slot)}, **kwargs)

    async def use(self, ref: str, **kwargs: Any) -> dict:
        return await self.request("use", {"target": ref}, **kwargs)

    async def interact(self, ref: str, **kwargs: Any) -> dict:
        return await self.request("interact", {"target": ref}, **kwargs)

    async def menu(self, ref: str, **kwargs: Any) -> dict:
        return await self.request("menu", {"target": ref}, **kwargs)

    async def menudump(self) -> dict:
        """只读：当前菜单里可点的组件与商店商品（含 index/name/price/x/y）。"""
        return await self.request("menudump", {})

    async def menuclick(self, x: int, y: int, **kwargs: Any) -> dict:
        return await self.request("menuclick", {"x": int(x), "y": int(y)}, **kwargs)

    async def menuclickinv(self, x: int, y: int, **kwargs: Any) -> dict:
        return await self.request("menuclickinv", {"x": int(x), "y": int(y)}, **kwargs)

    async def menuclose(self, **kwargs: Any) -> dict:
        return await self.request("menuclose", {}, **kwargs)

    async def shopbuy(self, index: int, qty: int = 1, **kwargs: Any) -> dict:
        """在已打开的商店里购买指定序号与数量（价格由游戏结算）。"""
        return await self.request("shopbuy", {"index": int(index), "qty": int(qty)}, **kwargs)

    async def chestput(self, tx: int, ty: int, slot: int, **kwargs: Any) -> dict:
        return await self.request("chestput", {"x": int(tx), "y": int(ty), "slot": int(slot)}, **kwargs)

    async def chestget(self, tx: int, ty: int, name: str = "", **kwargs: Any) -> dict:
        return await self.request("chestget", {"x": int(tx), "y": int(ty), "name": str(name)}, **kwargs)

    async def ship(self, slot: int, **kwargs: Any) -> dict:
        return await self.request("ship", {"slot": int(slot)}, **kwargs)

    async def placechest(self, tx: int, ty: int, **kwargs: Any) -> dict:
        return await self.request("placechest", {"x": int(tx), "y": int(ty)}, **kwargs)

    async def bin(self) -> dict:
        return await self.request("bin", {})

    async def reset(self) -> dict:
        """清空 Mod 的动作记录（幂等表）。"""
        return await self.request("reset", {}, timeout_ms=10000)

    async def recipes(self, limit: int = 80, learned_only: bool = False) -> dict:
        return await self.request("recipes", {"limit": int(limit), "learned": bool(learned_only)})

    async def craft(self, item: str, count: int = 1) -> dict:
        return await self.request("craft", {"item": str(item), "count": int(count)})

    async def stop(self, action_id: str | None = None) -> dict:
        """停止当前在途动作（Mod 侧取消），用于停止信号和取消委托。"""
        return await self.request("stop", {"actionId": action_id} if action_id else {})

