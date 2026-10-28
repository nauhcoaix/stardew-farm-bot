"""CLI：配置校验、状态/技能查询与失败路径（不启动游戏）。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from stardew_agent.cli.config import AgentConfig, config_from_mapping, load_config
from stardew_agent.cli.main import main
from stardew_agent.contracts.errors import ConfigError, Rejected
from stardew_agent.contracts.experience import Event
from stardew_agent.contracts.results import ActionResult
from stardew_agent.experience import ExperienceStore

REQUIRED = {
    "budget_seconds": 60,
    "budget_requests": 5,
    "repeat_window_seconds": 8,
    "repeat_max_runs": 5,
}


def test_配置缺少预算或限频直接报错():
    with pytest.raises(ConfigError, match="必需字段"):
        config_from_mapping({"budget_seconds": 60})


def test_配置拼错字段不会被静默忽略():
    with pytest.raises(ConfigError, match="未知字段"):
        config_from_mapping(dict(REQUIRED, budget_second=60))


def test_配置校验数值范围():
    with pytest.raises(ConfigError):
        AgentConfig(budget_seconds=0, budget_requests=1, repeat_window_seconds=1, repeat_max_runs=1)
    with pytest.raises(ConfigError):
        AgentConfig(budget_seconds=10, budget_requests=1, repeat_window_seconds=-1, repeat_max_runs=1)
    with pytest.raises(ConfigError):
        AgentConfig(budget_seconds=10, budget_requests=1, repeat_window_seconds=1, repeat_max_runs=1, snapshot_radius=99)


def test_相对路径相对配置文件解析(tmp_path: Path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(dict(REQUIRED, experience_db="data/exp.sqlite3")), encoding="utf-8")
    config = load_config(config_file)
    assert config.experience_db == tmp_path / "data" / "exp.sqlite3"


def test_命令行覆盖优先于配置文件(tmp_path: Path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(REQUIRED), encoding="utf-8")
    config = load_config(config_file, overrides={"budget_seconds": 5, "long_term_goal": "先认识农场"})
    assert config.budget_seconds == 5
    assert config.long_term_goal == "先认识农场"


def test_run缺少预算时返回错误码且不连接游戏(tmp_path: Path, capsys):
    code = main(["run", "--db", str(tmp_path / "exp.sqlite3")])
    assert code == 2
    assert "必需字段" in capsys.readouterr().err


def test_status在没有经验库时也能输出(tmp_path: Path, capsys):
    code = main(
        [
            "status",
            "--seconds",
            "10",
            "--requests",
            "2",
            "--repeat-window",
            "1",
            "--repeat-max",
            "2",
            "--db",
            str(tmp_path / "exp.sqlite3"),
            "--state",
            str(tmp_path / "state.json"),
            "--stop-file",
            str(tmp_path / "stop.json"),
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["store"] == {}
    assert payload["skills"] == []


def test_stop写入停止文件(tmp_path: Path, capsys):
    stop_path = tmp_path / "stop.json"
    code = main(
        [
            "stop",
            "--seconds",
            "10",
            "--requests",
            "2",
            "--repeat-window",
            "1",
            "--repeat-max",
            "2",
            "--stop-file",
            str(stop_path),
        ]
    )
    assert code == 0
    assert json.loads(stop_path.read_text(encoding="utf-8"))["reason"] == "cli-stop"


def test_skills_list在没有库时说明原因(tmp_path: Path, capsys):
    code = main(
        [
            "skills",
            "--seconds",
            "10",
            "--requests",
            "2",
            "--repeat-window",
            "1",
            "--repeat-max",
            "2",
            "--db",
            str(tmp_path / "missing.sqlite3"),
            "list",
        ]
    )
    assert code == 0
    assert "经验库还不存在" in capsys.readouterr().out


def test_限频白名单从配置读取():
    config = config_from_mapping(dict(REQUIRED, repeat_exempt_abilities=["select_item"]))
    assert config.repeat_exempt_abilities == ("select_item",)
    assert config.public()["repeat_exempt_abilities"] == ["select_item"]

    for bad in ("select_item", [1], [""]):
        with pytest.raises(ConfigError):
            config_from_mapping(dict(REQUIRED, repeat_exempt_abilities=bad))


def test_命令行可覆盖限频白名单(tmp_path: Path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(REQUIRED), encoding="utf-8")
    config = load_config(config_file, overrides={"repeat_exempt_abilities": ["select_item", "choose_menu_option"]})
    assert config.repeat_exempt_abilities == ("select_item", "choose_menu_option")


def test_命令行参数解析白名单():
    from stardew_agent.cli.main import build_parser

    args = build_parser().parse_args(
        [
            "run",
            "--seconds",
            "10",
            "--requests",
            "2",
            "--repeat-window",
            "1",
            "--repeat-max",
            "2",
            "--repeat-exempt",
            "select_item, choose_menu_option",
        ]
    )
    assert args.repeat_exempt == "select_item, choose_menu_option"


def test_模型请求间隔配置():
    config = config_from_mapping(dict(REQUIRED, model_request_interval_seconds=2.5))
    assert config.model_request_interval_seconds == 2.5
    assert config.public()["model_request_interval_seconds"] == 2.5

    for bad in (-1, 9999):
        with pytest.raises(ConfigError):
            config_from_mapping(dict(REQUIRED, model_request_interval_seconds=bad))


def test_命令行可覆盖模型请求间隔(tmp_path: Path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(REQUIRED), encoding="utf-8")
    config = load_config(config_file, overrides={"model_request_interval_seconds": 3.0})
    assert config.model_request_interval_seconds == 3.0


def test_模型完成判据开关默认关闭():
    config = config_from_mapping(dict(REQUIRED))
    assert config.model_completion_enabled is False
    assert config.public()["model_completion_enabled"] is False

    enabled = config_from_mapping(dict(REQUIRED, model_completion_enabled=True))
    assert enabled.model_completion_enabled is True

    with pytest.raises(ConfigError):
        config_from_mapping(dict(REQUIRED, model_completion_enabled="yes"))


def test_命令行可开启模型完成判据(tmp_path: Path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(REQUIRED), encoding="utf-8")
    config = load_config(config_file, overrides={"model_completion_enabled": True})
    assert config.model_completion_enabled is True


def test_机械动作开关默认关闭():
    config = config_from_mapping(dict(REQUIRED))
    assert config.expose_mechanics is False
    assert config.public()["expose_mechanics"] is False

    enabled = config_from_mapping(dict(REQUIRED, expose_mechanics=True))
    assert enabled.expose_mechanics is True

    with pytest.raises(ConfigError):
        config_from_mapping(dict(REQUIRED, expose_mechanics="yes"))


def test_上下文实体上限配置():
    config = config_from_mapping(dict(REQUIRED, max_notable_entities=24))
    assert config.max_notable_entities == 24
    assert config.public()["max_notable_entities"] == 24
    default = config_from_mapping(dict(REQUIRED))
    assert default.max_notable_entities == 24

    for bad in (0, 200):
        with pytest.raises(ConfigError):
            config_from_mapping(dict(REQUIRED, max_notable_entities=bad))


def test_照料优先开关默认开启():
    config = config_from_mapping(dict(REQUIRED))
    assert config.care_first is True
    assert config.public()["care_first"] is True

    off = config_from_mapping(dict(REQUIRED, care_first=False))
    assert off.care_first is False

    with pytest.raises(ConfigError):
        config_from_mapping(dict(REQUIRED, care_first="no"))


def test_破坏性任务不会自动续跑():
    from stardew_agent.cli.wiring import routine_continuation
    from stardew_agent.contracts.results import ActionResult

    def result(task: str) -> ActionResult:
        return ActionResult(
            action_id="a1",
            ability="farm_routine",
            params={"task": task},
            state="unknown",
            evidence={"remaining": 5},
        )

    assert routine_continuation(result("clear")) is None
    assert routine_continuation(result("mine")) is None
    assert routine_continuation(result("water")) is not None


def test_机械动作默认不暴露给模型():
    class Port:
        pass

    from stardew_agent.cli.wiring import build_registry

    registry = build_registry(Port(), None)
    visible = [descriptor.id for descriptor in registry.descriptors()]
    assert "farm_routine" in visible
    assert "use" not in visible and "select_item" not in visible
    assert {"use", "select_item"} <= {descriptor.id for descriptor in registry.all_descriptors()}
    with pytest.raises(Rejected):
        registry.validate_model_params("use", {"target": "@s1:e1"})

    debug = build_registry(Port(), None, expose_mechanics=True)
    assert "use" in [descriptor.id for descriptor in debug.descriptors()]


def test_stats命令输出占比(tmp_path: Path, capsys):
    db = tmp_path / "exp.sqlite3"
    store = ExperienceStore(db)
    try:
        store.record_action(
            ActionResult(action_id="a1", ability="move_to", state="completed"), save_key="save:1"
        )
        store.record_action(ActionResult(action_id="a2", ability="use", state="failed"), save_key="save:1")
        store.append_event(
            Event(
                id="ev:b1",
                kind="repeat_blocked",
                recorded_at=time.time(),
                save_key="save:1",
                ability="use",
                status="blocked",
            )
        )
    finally:
        store.close()

    code = main(
        [
            "stats",
            "--seconds",
            "10",
            "--requests",
            "2",
            "--repeat-window",
            "1",
            "--repeat-max",
            "2",
            "--db",
            str(db),
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["attempts"] == 3
    assert payload["executed"] == 2
    assert payload["blocked"] == 1
    assert payload["ratios"]["blocked"] == pytest.approx(1 / 3, rel=1e-3)
    assert payload["by_ability"][0]["ability"] in {"use", "move_to"}


def test_stats命令在没有经验库时也能输出(tmp_path: Path, capsys):
    code = main(
        [
            "stats",
            "--seconds",
            "10",
            "--requests",
            "2",
            "--repeat-window",
            "1",
            "--repeat-max",
            "2",
            "--db",
            str(tmp_path / "missing.sqlite3"),
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["attempts"] == 0
    assert "经验库" in payload["reason"]
