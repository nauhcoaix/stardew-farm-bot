"""模型接入配置（来源 ``stardew_valley_dfy/bot/planner.py`` 的 ``_cfg`` / ``_pick_key``）。

只负责读配置：地址、模型名、认证信息与请求参数。密钥不写日志，也不进异常文本。
没有可用配置时返回 ``None``，由调用方明确报“没有模型配置”，绝不退化成固定日程。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..contracts.errors import ConfigError

DEFAULT_API_BASE = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_CONFIG_NAME = "model.json"

#: example 里的占位符：万一有人直接把 example 当配置用，不要拿它去请求
PLACEHOLDERS = {"在这里填你的 key", "your-key", "your-api-key", "sk-xxx"}


@dataclass(frozen=True)
class ModelSettings:
    api_key: str
    api_base: str = DEFAULT_API_BASE
    model: str = DEFAULT_MODEL
    timeout_seconds: float = 60.0
    temperature: float = 0.2
    max_tokens: int = 1600

    def public(self) -> dict:
        """可写进日志/状态的字段（不含密钥）。"""
        return {
            "api_base": self.api_base,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }


def pick_api_key(config: Mapping[str, Any] | None) -> str | None:
    """从配置里取密钥：兼容 ``api_key`` / ``apiKey``，跳过空值与占位符。"""
    if not isinstance(config, Mapping):
        return None
    key = str(config.get("api_key") or config.get("apiKey") or "").strip()
    if not key or key in PLACEHOLDERS or not key.startswith("sk-"):
        return None
    return key


def settings_from_config(config: Mapping[str, Any], *, path: str = "<config>") -> ModelSettings:
    key = pick_api_key(config)
    if not key:
        raise ConfigError("%s 里没有可用的 api_key（空值或占位符）" % path)
    return ModelSettings(
        api_key=key,
        api_base=str(config.get("api_base") or DEFAULT_API_BASE),
        model=str(config.get("model") or DEFAULT_MODEL),
        timeout_seconds=float(config.get("timeout_seconds") or 60.0),
        temperature=float(config.get("temperature") if config.get("temperature") is not None else 0.2),
        max_tokens=int(config.get("max_tokens") or 1600),
    )


def default_config_paths(extra: Iterable[str | Path] = ()) -> list[Path]:
    """配置查找顺序：显式路径 → 环境变量 ``STARDEW_MODEL_CONFIG`` → 当前目录 ``model.json``。"""
    import os

    paths: list[Path] = [Path(item) for item in extra]
    env = os.environ.get("STARDEW_MODEL_CONFIG")
    if env:
        paths.append(Path(env))
    paths.append(Path.cwd() / DEFAULT_CONFIG_NAME)
    return paths


def load_settings(paths: Iterable[str | Path], *, required: bool = False) -> ModelSettings | None:
    """按顺序读配置；都没有可用密钥时返回 ``None``（或按 ``required`` 报错）。"""
    for path in paths:
        path = Path(path)
        try:
            config = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            raise ConfigError("模型配置读取失败 %s：%s" % (path, type(exc).__name__)) from exc
        key = pick_api_key(config)
        if not key:
            continue
        return settings_from_config(config, path=str(path))
    if required:
        raise ConfigError("没有可用的模型配置（期望包含 api_key 的 model.json）")
    return None
