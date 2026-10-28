"""白名单 5：模型配置与请求解析（替身传输，不产生费用）。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from stardew_agent.contracts.errors import ConfigError, ModelError
from stardew_agent.model_client import (
    ChatClient,
    ModelSettings,
    extract_json,
    load_settings,
    pick_api_key,
    settings_from_config,
)

GOOD = {"api_key": "sk-test-123", "model": "deepseek-chat", "api_base": "https://example.invalid/chat"}


def test_密钥取值兼容两种写法并跳过占位符():
    assert pick_api_key({"api_key": "sk-a"}) == "sk-a"
    assert pick_api_key({"apiKey": "sk-b"}) == "sk-b"
    assert pick_api_key({"api_key": "your-key"}) is None
    assert pick_api_key({"api_key": "  "}) is None
    assert pick_api_key(None) is None


def test_配置缺少密钥时报错():
    with pytest.raises(ConfigError):
        settings_from_config({"api_key": "sk-xxx"})


def test_没有可用配置时返回None而不是退回固定日程(tmp_path: Path):
    assert load_settings([tmp_path / "missing.json"], required=False) is None
    with pytest.raises(ConfigError):
        load_settings([tmp_path / "missing.json"], required=True)


def test_按顺序读取第一份可用配置(tmp_path: Path):
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"api_key": ""}), encoding="utf-8")
    good = tmp_path / "model.json"
    good.write_text(json.dumps(GOOD), encoding="utf-8")
    settings = load_settings([empty, good])
    assert settings is not None
    assert settings.model == "deepseek-chat"
    assert "sk-test" not in json.dumps(settings.public())


@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"a":1}', {"a": 1}),
        ('先说明一下\n{"a":1}\n{"b":2}', {"b": 2}),
        ("没有 JSON", None),
        ("", None),
        ("[1,2,3]", None),
    ],
)
def test_从回复文本里提取最后一个JSON对象(text, expected):
    assert extract_json(text) == expected


def test_请求带上认证与用量():
    captured = {}

    def transport(url, body, headers, timeout):
        captured.update({"url": url, "body": json.loads(body), "headers": dict(headers), "timeout": timeout})
        return {
            "model": "deepseek-chat",
            "choices": [{"message": {"content": '{"goal":"看看四周"}'}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        }

    client = ChatClient(ModelSettings(api_key="sk-test-123", api_base="https://example.invalid/chat"), transport=transport)
    reply = asyncio.run(client.complete([{"role": "user", "content": "hi"}]))
    assert reply.parsed == {"goal": "看看四周"}
    assert reply.total_tokens == 120
    assert captured["headers"]["Authorization"] == "Bearer sk-test-123"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert reply.usage()["model"] == "deepseek-chat"


def test_请求失败只报错误类型不带密钥():
    def transport(url, body, headers, timeout):
        raise TimeoutError("timed out")

    client = ChatClient(ModelSettings(api_key="sk-secret-value"), transport=transport)
    with pytest.raises(ModelError) as excinfo:
        asyncio.run(client.complete([{"role": "user", "content": "hi"}]))
    assert "sk-secret-value" not in str(excinfo.value)


def test_回复结构不符合聊天接口时报错():
    def transport(url, body, headers, timeout):
        return {"choices": []}

    client = ChatClient(ModelSettings(api_key="sk-test-123"), transport=transport)
    with pytest.raises(ModelError):
        asyncio.run(client.complete([{"role": "user", "content": "hi"}]))
