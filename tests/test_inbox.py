"""人类指令收件箱：写入、展示、回执、过期、撤销与并发写者。"""

from __future__ import annotations

from pathlib import Path

import pytest

from stardew_agent.contracts.errors import Rejected
from stardew_agent.runtime import HumanInbox


def clock_at(value: float):
    state = {"now": value}
    return state, lambda: state["now"]


def test_指令按优先级进入待办(tmp_path: Path):
    state, clock = clock_at(1000.0)
    box = HumanInbox(tmp_path, clock=clock)
    low = box.send("先浇水", priority=3, ttl_seconds=600)
    high = box.send("先收菜", kind="suggestion", priority=8, ttl_seconds=600)

    rows = box.pending()
    assert [row["id"] for row in rows] == [high["id"], low["id"]]
    assert rows[0]["text"] == "先收菜"
    assert rows[0]["kind"] == "suggestion"
    assert box.summary()["pending"] == 2


def test_展示后没回执会再出现_回执后结束(tmp_path: Path):
    _, clock = clock_at(1000.0)
    box = HumanInbox(tmp_path, clock=clock)
    row = box.send("去睡觉", ttl_seconds=600)

    assert box.deliver([row["id"]]) == [row["id"]]
    again = box.pending()
    assert again and again[0]["status"] == "delivered"

    assert box.follow([row["id"]], note="已经上床") == [row["id"]]
    assert box.pending() == []
    done = box.messages()[0]
    assert done["status"] == "done"
    assert done["note"] == "已经上床"


def test_过期指令不再出现(tmp_path: Path):
    state, clock = clock_at(1000.0)
    box = HumanInbox(tmp_path, clock=clock)
    box.send("30 秒内做", ttl_seconds=30)

    state["now"] += 31
    assert box.pending() == []
    assert box.messages()[0]["status"] == "expired"


def test_撤销后不再出现(tmp_path: Path):
    box = HumanInbox(tmp_path)
    row = box.send("别去矿洞", kind="constraint")

    assert box.drop(row["id"], note="改主意了") is True
    assert box.pending() == []
    assert box.summary()["counts"]["dropped"] == 1
    assert box.drop("ins-missing") is False


def test_非法指令直接拒绝(tmp_path: Path):
    box = HumanInbox(tmp_path)
    with pytest.raises(Rejected):
        box.send("   ")
    with pytest.raises(Rejected):
        box.send("x", kind="order")
    with pytest.raises(Rejected):
        box.send("x", ttl_seconds=5)
    with pytest.raises(Rejected):
        box.send("x", priority=0)


def test_新指令与回执分开写不会互相覆盖(tmp_path: Path):
    box = HumanInbox(tmp_path)
    first = box.send("一")
    second = box.send("二")
    box.follow([first["id"]])

    assert {row["id"] for row in box.messages()} == {first["id"], second["id"]}
    assert (tmp_path / "ack.json").exists()
    assert not (tmp_path / "ack.json.tmp").exists()


def test_空收件箱不创建目录(tmp_path: Path):
    root = tmp_path / "missing-inbox"
    box = HumanInbox(root)
    assert box.messages() == []
    assert box.summary()["total"] == 0
    assert not root.exists()


def test_决策展示指令并写回执(tmp_path: Path):
    import asyncio
    import json
    from types import SimpleNamespace

    from stardew_agent.cli.config import AgentConfig
    from stardew_agent.cli.wiring import make_decider
    from stardew_agent.runtime import Recorder, RunBudget
    from tests.helpers import observe

    class Model:
        def __init__(self) -> None:
            self.seen = None

        async def complete(self, messages):
            self.seen = json.loads(messages[1]["content"])
            return SimpleNamespace(
                parsed={
                    "goal": "先浇水",
                    "reason": "按用户指令",
                    "ability": None,
                    "params": {},
                    "followed_instructions": [row["id"]],
                },
                total_tokens=3,
                model="fake",
            )

    class Registry:
        def descriptors(self):
            return []

        def validate_model_params(self, name, params):
            return dict(params)

    class Store:
        def recall(self, query, *, save_key, limit=8):
            return []

        def recent_actions(self, save_key, *, limit=8):
            return []

        def known_tiles(self, save_key, location):
            return []

    box = HumanInbox(tmp_path)
    row = box.send("先给作物浇水", priority=8)
    model = Model()
    recorder = Recorder()
    decide = make_decider(
        model,
        Registry(),
        Store(),
        RunBudget(wall_clock_seconds=10, model_requests=5),
        AgentConfig(
            budget_seconds=10,
            budget_requests=5,
            repeat_window_seconds=1,
            repeat_max_runs=2,
            long_term_goal="测试",
        ),
        {"goal": "", "goal_revision": 0, "save_key": ""},
        inbox=box,
        recorder=recorder,
    )

    decision = asyncio.run(decide(observe(x=10, y=10), None, ()))

    assert decision is not None and decision.goal == "先浇水"
    assert model.seen["instructions"][0]["id"] == row["id"]
    assert model.seen["instructions"][0]["text"] == "先给作物浇水"
    assert box.pending() == []
    assert box.messages()[0]["status"] == "done"
    kinds = [event.kind for event in recorder.events]
    assert "instructions_delivered" in kinds
    assert "instructions_followed" in kinds

