"""运行配置：预算、限频、路径与长期目标文字。

预算与限频**必须显式给出**：模块不提供隐式无限预算，也不把示例数值当默认业务政策。
示例见 ``config.example.json``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ..contracts.errors import ConfigError
from ..decision.context import DEFAULT_LONG_TERM_GOAL

REQUIRED_KEYS = (
    "budget_seconds",
    "budget_requests",
    "repeat_window_seconds",
    "repeat_max_runs",
)
KNOWN_KEYS = REQUIRED_KEYS + (
    "bridge_endpoint",
    "snapshot_radius",
    "game_path",
    "fishing_runtime",
    "experience_db",
    "model_config",
    "long_term_goal",
    "poll_seconds",
    "step_interval_seconds",
    "console_output",
    "state_path",
    "stop_path",
    "embedding_dir",
    "max_context_entities",
)


@dataclass(frozen=True)
class AgentConfig:
    """一次运行的完整配置。"""

    budget_seconds: float
    budget_requests: int
    repeat_window_seconds: float
    repeat_max_runs: int
    bridge_endpoint: str = "ws://127.0.0.1:17654/"
    snapshot_radius: int = 8
    game_path: Path = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Stardew Valley")
    fishing_runtime: Path | None = None
    experience_db: Path = Path("data/experience.sqlite3")
    model_config: Path | None = None
    long_term_goal: str = DEFAULT_LONG_TERM_GOAL
    poll_seconds: float = 0.5
    step_interval_seconds: float = 0.2
    console_output: bool = True
    state_path: Path = Path("data/state.json")
    stop_path: Path = Path("data/stop.json")
    embedding_dir: Path | None = None
    max_context_entities: int = 120

    def __post_init__(self) -> None:
        if self.budget_seconds <= 0:
            raise ConfigError("budget_seconds 必须大于 0")
        if self.budget_requests < 1:
            raise ConfigError("budget_requests 至少为 1")
        if self.repeat_window_seconds < 0:
            raise ConfigError("repeat_window_seconds 不能为负数")
        if self.repeat_max_runs < 1:
            raise ConfigError("repeat_max_runs 至少为 1")
        if not 1 <= self.snapshot_radius <= 24:
            raise ConfigError("snapshot_radius 应在 1～24")
        if not 0.05 <= self.poll_seconds <= 10:
            raise ConfigError("poll_seconds 应在 0.05～10")
        if not 0.05 <= self.step_interval_seconds <= 10:
            raise ConfigError("step_interval_seconds 应在 0.05～10")

    @property
    def fishing_mailbox(self) -> Path:
        if self.fishing_runtime is not None:
            return self.fishing_runtime
        return self.game_path / "Mods" / "StandaloneFishingSkill" / "runtime"

    def public(self) -> dict:
        """可写入状态文件的配置摘要（不含密钥）。"""
        return {
            "budget_seconds": self.budget_seconds,
            "budget_requests": self.budget_requests,
            "repeat_window_seconds": self.repeat_window_seconds,
            "repeat_max_runs": self.repeat_max_runs,
            "bridge_endpoint": self.bridge_endpoint,
            "snapshot_radius": self.snapshot_radius,
            "experience_db": str(self.experience_db),
            "long_term_goal": self.long_term_goal,
            "poll_seconds": self.poll_seconds,
            "console_output": self.console_output,
        }


def config_from_mapping(data: Mapping[str, Any], *, base_dir: Path | None = None) -> AgentConfig:
    """从 JSON 对象构造配置；缺少必需项或拼错键都会报错。"""
    if not isinstance(data, Mapping):
        raise ConfigError("配置必须是 JSON 对象")
    unknown = sorted(set(data) - set(KNOWN_KEYS))
    if unknown:
        raise ConfigError("配置含未知字段：%s" % ", ".join(unknown))
    missing = [key for key in REQUIRED_KEYS if key not in data]
    if missing:
        raise ConfigError("配置缺少必需字段：%s（预算与限频必须显式给出）" % ", ".join(missing))
    base = base_dir or Path.cwd()

    def path_of(key: str) -> Path | None:
        value = data.get(key)
        if value in (None, ""):
            return None
        path = Path(str(value))
        return path if path.is_absolute() else (base / path)

    console_output = True
    if "console_output" in data:
        value = data["console_output"]
        if not isinstance(value, bool):
            raise ConfigError("console_output 必须是 true 或 false")
        console_output = value

    return AgentConfig(
        budget_seconds=float(data["budget_seconds"]),
        budget_requests=int(data["budget_requests"]),
        repeat_window_seconds=float(data["repeat_window_seconds"]),
        repeat_max_runs=int(data["repeat_max_runs"]),
        bridge_endpoint=str(data.get("bridge_endpoint") or "ws://127.0.0.1:17654/"),
        snapshot_radius=int(data.get("snapshot_radius") or 8),
        game_path=path_of("game_path") or AgentConfig.__dataclass_fields__["game_path"].default,
        fishing_runtime=path_of("fishing_runtime"),
        experience_db=path_of("experience_db") or Path("data/experience.sqlite3"),
        model_config=path_of("model_config"),
        long_term_goal=str(data.get("long_term_goal") or DEFAULT_LONG_TERM_GOAL),
        poll_seconds=float(data.get("poll_seconds") or 0.5),
        step_interval_seconds=float(data.get("step_interval_seconds") or 0.2),
        console_output=console_output,
        state_path=path_of("state_path") or Path("data/state.json"),
        stop_path=path_of("stop_path") or Path("data/stop.json"),
        embedding_dir=path_of("embedding_dir"),
        max_context_entities=int(data.get("max_context_entities") or 120),
    )


def load_config(path: str | Path | None, *, overrides: Mapping[str, Any] | None = None) -> AgentConfig:
    """读取 JSON 配置并应用命令行覆盖。"""
    data: dict[str, Any] = {}
    base_dir = Path.cwd()
    if path:
        config_path = Path(path)
        try:
            data = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError as exc:
            raise ConfigError("配置文件不存在：%s" % config_path) from exc
        except (OSError, ValueError) as exc:
            raise ConfigError("配置文件无法解析：%s" % config_path) from exc
        base_dir = config_path.resolve().parent
    data.update({key: value for key, value in (overrides or {}).items() if value is not None})
    return config_from_mapping(data, base_dir=base_dir)
