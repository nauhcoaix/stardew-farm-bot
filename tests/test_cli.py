"""CLI：配置校验、状态/技能查询与失败路径（不启动游戏）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stardew_agent.cli.config import AgentConfig, config_from_mapping, load_config
from stardew_agent.cli.main import main
from stardew_agent.contracts.errors import ConfigError

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
