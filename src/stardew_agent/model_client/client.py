"""模型 HTTP 请求与 JSON 回复解析。

来源：``stardew_valley_dfy/bot/planner.py`` 的 ``_extract_json`` 与 ``plan_work``
中的请求部分。保留：编码消息、发送请求、超时、读取回复、从文本里提取 JSON 对象、
把请求异常与不可解析回复交给调用方。

不保留旧 ``WORK_SYSTEM`` 提示词、旧计划字段含义、每轮步数策略与后台规划生命周期。
本模块只做请求响应；上下文选择与新 Agent 的策略在 :mod:`stardew_agent.decision`。
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from ..contracts.errors import ModelError
from .settings import ModelSettings


@dataclass(frozen=True)
class ModelReply:
    """一次模型回复：原文、解析出的对象与实际用量。"""

    content: str
    parsed: Mapping[str, Any] | None
    model: str
    served_model: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    elapsed_seconds: float = 0.0
    raw_usage: Mapping[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int | None:
        if self.prompt_tokens is None and self.completion_tokens is None:
            return None
        return int(self.prompt_tokens or 0) + int(self.completion_tokens or 0)

    def usage(self) -> dict:
        return {
            "model": self.model,
            "served_model": self.served_model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


def extract_json(text: str | None) -> dict | None:
    """模型常先说一段再给 JSON，甚至改主意；取最后一个能解析的对象。"""
    if not text:
        return None
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except ValueError:
        pass
    best = None
    depth = 0
    start = -1
    for index, char in enumerate(text):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    fragment = text[start : index + 1]
                    try:
                        value = json.loads(fragment)
                    except ValueError:
                        continue
                    if isinstance(value, dict):
                        best = value
    return best


def _post_json(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> dict:
    """默认传输：urllib，不引入额外 HTTP 依赖。"""
    request = urllib.request.Request(url, data=body, method="POST")
    for name, value in headers.items():
        request.add_header(name, value)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


Transport = Callable[[str, bytes, Mapping[str, str], float], dict]


class ChatClient:
    """兼容聊天接口的最小客户端。传输可注入，便于离线测试。"""

    def __init__(
        self,
        settings: ModelSettings,
        *,
        transport: Transport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._transport = transport or _post_json
        self.clock = clock

    async def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        timeout: float | None = None,
        json_object: bool = True,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ModelReply:
        """发送一次请求并返回回复；网络与解析错误统一抛 ``ModelError``。"""
        body: dict[str, Any] = {
            "model": self.settings.model,
            "messages": [dict(message) for message in messages],
            "temperature": self.settings.temperature if temperature is None else float(temperature),
            "max_tokens": self.settings.max_tokens if max_tokens is None else int(max_tokens),
        }
        if json_object:
            body["response_format"] = {"type": "json_object"}
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.settings.api_key,
        }
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        started = self.clock()
        try:
            response = await asyncio.to_thread(
                self._transport,
                self.settings.api_base,
                payload,
                headers,
                float(self.settings.timeout_seconds if timeout is None else timeout),
            )
        except urllib.error.HTTPError as exc:
            raise ModelError("模型请求失败：HTTP %s" % exc.code) from exc
        except Exception as exc:  # noqa: BLE001 - 不打印请求/响应全文，避免密钥与玩家文本进日志
            raise ModelError("模型请求失败：%s" % type(exc).__name__) from exc
        elapsed = self.clock() - started
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelError("模型回复结构不符合聊天接口") from exc
        usage = response.get("usage") or {}
        return ModelReply(
            content=str(content or ""),
            parsed=extract_json(content),
            model=str(response.get("model") or self.settings.model),
            served_model=str(response.get("model") or ""),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            elapsed_seconds=elapsed,
            raw_usage=dict(usage),
        )


async def ask_once(
    settings: ModelSettings,
    messages: Sequence[Mapping[str, Any]],
    *,
    transport: Transport | None = None,
) -> ModelReply:
    """便捷函数：一份配置、一组消息、一次请求。"""
    return await ChatClient(settings, transport=transport).complete(messages)
