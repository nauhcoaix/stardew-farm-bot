"""模型接入配置：任何 OpenAI 兼容的聊天接口（DeepSeek、OpenAI、OpenRouter、vLLM、Ollama…）。

只负责读配置：地址、模型名、认证方式与请求参数。密钥不写日志，也不进异常文本。
没有可用配置时返回 ``None``，由调用方明确报“没有模型配置”，绝不退化成固定日程。

配置查找顺序：显式路径 → 环境变量 ``STARDEW_MODEL_CONFIG`` → 当前目录 ``model.json`` →
``OPENAI_API_KEY`` / ``OPENAI_BASE_URL`` / ``OPENAI_MODEL``。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from ..contracts.errors import ConfigError

DEFAULT_API_BASE = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_CONFIG_NAME = "model.json"

#: example 里的占位符：万一有人直接把 example 当配置用，不要拿它去请求
PLACEHOLDERS = {"在这里填你的 key", "your-key", "your-api-key", "sk-xxx", "sk-your-key"}

#: 鉴权方式：bearer=Authorization: Bearer <key>；header=自定义头（Azure 的 api-key）；none=本地服务
AUTH_MODES = ("bearer", "header", "none")

#: 不写 api_key 也允许的本地主机（LM Studio / Ollama / vLLM 常见）
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"}


@dataclass(frozen=True)
class ModelSettings:
    api_key: str = ""
    api_base: str = DEFAULT_API_BASE
    model: str = DEFAULT_MODEL
    timeout_seconds: float = 60.0
    temperature: float = 0.2
    max_tokens: int = 1600
    #: 生成上限的字段名：新 OpenAI 推理模型用 ``max_completion_tokens``
    max_tokens_param: str = "max_tokens"
    #: 是否发送 ``response_format={"type":"json_object"}``；不支持的端点设为 false
    json_mode: bool = True
    #: 鉴权方式、头名与额外请求头（OpenRouter 的 HTTP-Referer/X-Title、Azure 等）
    auth: str = "bearer"
    api_key_header: str = "Authorization"
    headers: Mapping[str, str] = field(default_factory=dict)
    #: 额外请求体字段（top_p、seed、enable_thinking…），最后合并，可覆盖上面的默认值
    extra_body: Mapping[str, Any] = field(default_factory=dict)

    def public(self) -> dict:
        """可写进日志/状态的字段（不含密钥；额外头只报名字）。"""
        return {
            "api_base": self.api_base,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "max_tokens_param": self.max_tokens_param,
            "json_mode": self.json_mode,
            "auth": self.auth,
            "headers": sorted(str(name) for name in self.headers),
        }


def pick_api_key(config: Mapping[str, Any] | None) -> str | None:
    """从配置里取密钥：兼容 ``api_key`` / ``apiKey``，跳过空值与占位符。

    不要求 ``sk-`` 前缀：Azure、自建网关和本地服务的密钥格式各不相同。
    """
    if not isinstance(config, Mapping):
        return None
    key = str(config.get("api_key") or config.get("apiKey") or "").strip()
    if not key or key in PLACEHOLDERS:
        return None
    return key


def normalize_api_base(value: str | None) -> str:
    """把 ``base_url`` 归一成聊天补全端点：``https://host/v1`` → ``…/v1/chat/completions``。

    已经是完整端点（含 ``chat/completions``，例如 Azure 带 ``api-version`` 的地址）时原样返回。
    """
    url = str(value or "").strip()
    if not url:
        return DEFAULT_API_BASE
    lowered = url.lower()
    if "chat/completions" in lowered or lowered.rstrip("/").endswith("/completions"):
        return url
    return url.rstrip("/") + "/chat/completions"


def _is_local(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in LOCAL_HOSTS


def _key_optional(config: Mapping[str, Any]) -> bool:
    auth = str(config.get("auth") or "bearer").strip().lower()
    if auth == "none" or bool(config.get("allow_missing_key")):
        return True
    return _is_local(normalize_api_base(config.get("api_base") or config.get("base_url")))



def _string_map(value: Any, field_name: str) -> dict[str, str]:
    if value in (None, ""):
        return {}
    if not isinstance(value, Mapping):
        raise ConfigError("%s 必须是对象（字符串键值）" % field_name)
    rows: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, (str, int, float)):
            raise ConfigError("%s 的键必须是字符串、值必须是字符串或数字" % field_name)
        rows[key] = str(item)
    return rows


def _extra_body(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if not isinstance(value, Mapping):
        raise ConfigError("extra_body 必须是对象")
    if any(not isinstance(key, str) for key in value):
        raise ConfigError("extra_body 的键必须是字符串")
    return dict(value)


def _bool(value: Any, field_name: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ConfigError("%s 必须是 true 或 false" % field_name)
    return value


def settings_from_config(config: Mapping[str, Any], *, path: str = "<config>") -> ModelSettings:
    """从一份 JSON 配置构造设置；支持 ``base_url`` 简写与任意非空 api_key。"""
    if not isinstance(config, Mapping):
        raise ConfigError("%s 必须是 JSON 对象" % path)
    api_base = normalize_api_base(config.get("api_base") or config.get("base_url"))
    key = pick_api_key(config) or ""
    auth = str(config.get("auth") or "bearer").strip().lower()
    if auth not in AUTH_MODES:
        raise ConfigError("auth 只能是 %s" % "/".join(AUTH_MODES))
    if not key and auth != "none" and not _key_optional(config):
        raise ConfigError(
            "%s 里没有可用的 api_key（远程端点必须提供；本地端点可设 auth=\"none\" 或 allow_missing_key=true）"
            % path
        )
    max_tokens_param = str(config.get("max_tokens_param") or "max_tokens").strip()
    if not max_tokens_param:
        raise ConfigError("max_tokens_param 不能为空")
    return ModelSettings(
        api_key=key,
        api_base=api_base,
        model=str(config.get("model") or DEFAULT_MODEL),
        timeout_seconds=float(config.get("timeout_seconds") or 60.0),
        temperature=float(config.get("temperature") if config.get("temperature") is not None else 0.2),
        max_tokens=int(config.get("max_tokens") or 1600),
        max_tokens_param=max_tokens_param,
        json_mode=_bool(config.get("json_mode"), "json_mode", True),
        auth=auth,
        api_key_header=str(config.get("api_key_header") or "Authorization"),
        headers=_string_map(config.get("headers"), "headers"),
        extra_body=_extra_body(config.get("extra_body")),
    )


def default_config_paths(extra: Iterable[str | Path] = ()) -> list[Path]:
    """配置查找顺序：显式路径 → 环境变量 ``STARDEW_MODEL_CONFIG`` → 当前目录 ``model.json``。"""
    paths: list[Path] = [Path(item) for item in extra]
    env = os.environ.get("STARDEW_MODEL_CONFIG")
    if env:
        paths.append(Path(env))
    paths.append(Path.cwd() / DEFAULT_CONFIG_NAME)
    return paths


def settings_from_env() -> ModelSettings | None:
    """用 ``OPENAI_*`` 环境变量构造设置；两个变量都没有时返回 ``None``。"""
    key = (os.environ.get("OPENAI_API_KEY") or os.environ.get("STARDEW_MODEL_API_KEY") or "").strip()
    base = (os.environ.get("OPENAI_BASE_URL") or os.environ.get("STARDEW_MODEL_API_BASE") or "").strip()
    model = (os.environ.get("OPENAI_MODEL") or os.environ.get("STARDEW_MODEL_NAME") or "").strip()
    if not key and not base:
        return None
    config: dict[str, Any] = {
        "api_key": key,
        "api_base": base or DEFAULT_API_BASE,
        "model": model or DEFAULT_MODEL,
    }
    if not key:
        config["auth"] = "none"
    return settings_from_config(config, path="<env OPENAI_*>")


def load_settings(paths: Iterable[str | Path], *, required: bool = False) -> ModelSettings | None:
    """按顺序读配置；都没有可用配置时返回 ``None``（或按 ``required`` 报错）。"""
    for path in paths:
        path = Path(path)
        try:
            config = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            raise ConfigError("模型配置读取失败 %s：%s" % (path, type(exc).__name__)) from exc
        if not isinstance(config, Mapping):
            raise ConfigError("模型配置必须是 JSON 对象：%s" % path)
        if not pick_api_key(config) and not _key_optional(config):
            continue
        return settings_from_config(config, path=str(path))
    env = settings_from_env()
    if env is not None:
        return env
    if required:
        raise ConfigError(
            "没有可用的模型配置（model.json 的 api_key，或环境变量 "
            "OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL）"
        )
    return None
