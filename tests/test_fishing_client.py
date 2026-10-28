"""白名单 4：钓鱼邮箱客户端（文件替身，不启动游戏）。"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stardew_agent.fishing_client import FishingError, read_status, send, submit_command, validate_limits, wait_run


def status(**kwargs) -> dict:
    row = {
        "version": 1,
        "mode": "game",
        "observedUtc": datetime.now(timezone.utc).isoformat(),
        "state": "idle",
        "active": False,
        "runId": "",
    }
    row.update(kwargs)
    return row


def publish(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(path)


def test_过期状态不算当前成功(tmp_path: Path):
    row = status(state="completed")
    row["observedUtc"] = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
    publish(tmp_path / "status.json", row)
    with pytest.raises(FishingError, match="过期"):
        read_status(tmp_path)


def test_启动要等到匹配的实际确认(tmp_path: Path):
    publish(tmp_path / "status.json", status())

    def game() -> None:
        deadline = time.monotonic() + 2
        while not (tmp_path / "command.json").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        command = json.loads((tmp_path / "command.json").read_text(encoding="utf-8"))
        row = status(ack={"id": command["id"], "accepted": True})
        row.update(runId=command["id"], state="running", active=True)
        publish(tmp_path / "status.json", row)

    thread = threading.Thread(target=game)
    thread.start()
    result = send(tmp_path, "start", {"maxCasts": 3}, ack_timeout=2)
    thread.join()
    assert result["active"] is True
    assert result["runId"] == result["ack"]["id"]


def test_已有待处理指令不被覆盖(tmp_path: Path):
    publish(tmp_path / "status.json", status())
    (tmp_path / "command.json").write_text('{"id":"original"}', encoding="utf-8")
    with pytest.raises(FishingError, match="待处理"):
        send(tmp_path, "start")
    assert json.loads((tmp_path / "command.json").read_text(encoding="utf-8"))["id"] == "original"


def test_任务被替换不能被当成自己的成功(tmp_path: Path):
    row = status(runId="different-run", state="completed")
    publish(tmp_path / "status.json", row)
    with pytest.raises(FishingError, match="替换"):
        wait_run(tmp_path, "our-run", 1)


def test_未确认指令报结果未知(tmp_path: Path):
    publish(tmp_path / "status.json", status())
    with pytest.raises(FishingError, match="结果未知"):
        send(tmp_path, "start", {"maxCasts": 2}, ack_timeout=0.05)


def test_预算参数范围与完整性():
    valid = {"maxCasts": 5, "targetFish": 2, "maxSeconds": 300, "minEnergy": 30, "stopAt": 2200}
    validate_limits(valid)
    for key, bad in (("maxCasts", 0), ("maxSeconds", 99999), ("stopAt", 2260), ("targetFish", -1)):
        row = dict(valid, **{key: bad})
        with pytest.raises(FishingError):
            validate_limits(row)
    with pytest.raises(FishingError, match="缺少参数"):
        validate_limits({"maxCasts": 5})
    with pytest.raises(FishingError):
        validate_limits(dict(valid, facing=9))


def test_提交指令会带上runId与排除项(tmp_path: Path):
    command = submit_command(tmp_path, "start", {"maxCasts": 1}, excluded=["a", "b"], run_id="run-1")
    stored = json.loads((tmp_path / "command.json").read_text(encoding="utf-8"))
    assert stored["runId"] == "run-1"
    assert stored["excluded"] == ["a", "b"]
    assert stored["id"] == command["id"]
    assert not (tmp_path / "command.lock").exists()
