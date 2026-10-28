"""端到端：真实循环 + 真实能力 + 真实经验库；桥、模型、钓鱼用替身。

这一层验证的是“模块之间的契约”，不代表游戏内行为已通过实机验收。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from stardew_agent.cli.config import AgentConfig
from stardew_agent.cli.wiring import run_agent
from stardew_agent.contracts.errors import ConfigError
from stardew_agent.experience import ExperienceStore
from stardew_agent.model_client import ChatClient, ModelSettings
from tests.helpers import entity
from tests.helpers import snapshot as make_snapshot


class FakeBridge:
    """结构化替身：位置、选中槽位与实体状态会随动作真实变化。"""

    def __init__(self) -> None:
        self.x, self.y = 10, 10
        self.slot = 0
        self.tilled = False
        self.watered = False
        self.calls: list[tuple] = []
        self.closed = False

    def _entities(self) -> list[dict]:
        return [
            entity("@snap:e1", "soil", "耕地", 12, 10, tilled=self.tilled, watered=self.watered),
            entity("@snap:e2", "exit", "出口 → FarmHouse", 9, 10),
        ]

    async def snapshot(self, radius: int = 8) -> dict:
        return make_snapshot(
            x=self.x,
            y=self.y,
            entities=self._entities(),
            inventory_rows=[{"slot": 0, "itemId": "(T)Hoe", "name": "锄头", "count": 1, "tool": "Hoe"}],
            selected_slot=self.slot,
        )

    async def move(self, x=None, y=None, near=None, **kwargs):
        self.calls.append(("move", x, y, near))
        if near is None and x is not None and y is not None:
            self.x, self.y = int(x), int(y)
        return {"changed": True, "detail": "已移动"}

    async def select(self, slot, **kwargs):
        self.calls.append(("select", slot))
        self.slot = int(slot)
        return {"changed": True}

    async def use(self, ref, **kwargs):
        self.calls.append(("use", ref))
        self.tilled = True
        return {"changed": True, "detail": "锄地"}

    async def interact(self, ref, **kwargs):
        self.calls.append(("interact", ref))
        return {"changed": True}

    async def menu(self, ref, **kwargs):
        self.calls.append(("menu", ref))
        return {"changed": True}

    async def close(self) -> None:
        self.closed = True


def model_with(decisions: list[dict]) -> ChatClient:
    state = {"index": 0}

    def transport(url, body, headers, timeout):
        index = min(state["index"], len(decisions) - 1)
        state["index"] += 1
        return {
            "model": "fake-model",
            "choices": [{"message": {"content": json.dumps(decisions[index], ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20},
        }

    return ChatClient(ModelSettings(api_key="sk-test", api_base="http://example.invalid/chat"), transport=transport)


def config_for(tmp_path: Path) -> AgentConfig:
    return AgentConfig(
        budget_seconds=15,
        budget_requests=3,
        repeat_window_seconds=0,
        repeat_max_runs=5,
        experience_db=tmp_path / "experience.sqlite3",
        state_path=tmp_path / "state.json",
        stop_path=tmp_path / "stop.json",
        poll_seconds=0.05,
        step_interval_seconds=0.05,
    )


SAVE_KEY = "save:save-1/player:player-1"


def test_移动委托在真实循环里完成并留下证据(tmp_path: Path):
    bridge = FakeBridge()
    model = model_with([{"goal": "走到耕地旁", "ability": "move_to", "params": {"x": 12, "y": 10}}])
    payload = asyncio.run(
        run_agent(config_for(tmp_path), bridge=bridge, model=model, include_fishing=False, max_actions=1)
    )

    assert payload["summary"]["completed"] == 1
    assert payload["summary"]["stop_reason"] == "max_actions"
    assert ("move", 12, 10, None) in bridge.calls
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["summary"]["completed"] == 1
    assert "move_to" in state["abilities"]

    store = ExperienceStore(tmp_path / "experience.sqlite3")
    try:
        events = store.events_for_save(SAVE_KEY, kind="action")
        assert events
        assert events[0]["payload"]["result"]["state"] == "completed"
        assert store.known_tiles(SAVE_KEY, "Farm")
    finally:
        store.close()


def test_使用能力按前后观察验证并把证据写入经验库(tmp_path: Path):
    bridge = FakeBridge()
    bridge.x = 12
    model = model_with([{"goal": "锄这块地", "ability": "use", "params": {"target": "@snap:e1", "expect": "till"}}])
    payload = asyncio.run(
        run_agent(config_for(tmp_path), bridge=bridge, model=model, include_fishing=False, max_actions=1)
    )
    assert payload["summary"]["completed"] == 1
    assert ("use", "@snap:e1") in bridge.calls

    store = ExperienceStore(tmp_path / "experience.sqlite3")
    try:
        events = store.events_for_save(SAVE_KEY, kind="action")
        evidence = events[0]["payload"]["result"]["evidence"]
        assert evidence["verified"] is True
        assert evidence["tile_delta"]["tilled"]["after"] is True
    finally:
        store.close()


def test_没有模型配置时不会退回固定日程(tmp_path: Path, monkeypatch):
    import pytest

    from stardew_agent.contracts.errors import ConfigError

    # 在隔离目录里运行，避免读到开发者本机的 model.json。
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STARDEW_MODEL_CONFIG", raising=False)

    bridge = FakeBridge()
    config = config_for(tmp_path)
    config = AgentConfig(
        budget_seconds=config.budget_seconds,
        budget_requests=config.budget_requests,
        repeat_window_seconds=config.repeat_window_seconds,
        repeat_max_runs=config.repeat_max_runs,
        experience_db=config.experience_db,
        state_path=config.state_path,
        stop_path=config.stop_path,
        model_config=tmp_path / "missing-model.json",
    )
    with pytest.raises(ConfigError):
        asyncio.run(run_agent(config, bridge=bridge, include_fishing=False))
    assert bridge.calls == []


def test_控制台实时输出目标动作与结果(tmp_path: Path, capsys):
    bridge = FakeBridge()
    model = model_with([{"goal": "走到耕地旁", "ability": "move_to", "params": {"x": 12, "y": 10}}])
    asyncio.run(
        run_agent(
            config_for(tmp_path),
            bridge=bridge,
            model=model,
            include_fishing=False,
            max_actions=1,
            console=True,
        )
    )
    out = capsys.readouterr().out
    assert "目标：走到耕地旁" in out
    assert "开始执行：move_to" in out
    assert "成功：" in out
    assert "汇总：" in out


def test_连不上游戏桥时给出可读错误(tmp_path: Path):
    import pytest

    from stardew_agent.cli.wiring import build_runtime
    from stardew_agent.contracts.errors import TransportError

    config = AgentConfig(
        budget_seconds=10,
        budget_requests=2,
        repeat_window_seconds=1,
        repeat_max_runs=2,
        bridge_endpoint="ws://127.0.0.1:1/",
        experience_db=tmp_path / "exp.sqlite3",
        state_path=tmp_path / "state.json",
        stop_path=tmp_path / "stop.json",
    )
    with pytest.raises(TransportError, match="无法连接游戏桥"):
        asyncio.run(
            build_runtime(
                config,
                model=model_with([{"goal": "走", "ability": None}]),
                include_fishing=False,
            )
        )


def test_技能失败会退回候选_误报完成会停用(tmp_path: Path):
    from stardew_agent.cli.wiring import make_event_sink
    from stardew_agent.contracts.experience import Event
    from stardew_agent.experience import ExperienceStore, build_skill

    store = ExperienceStore(tmp_path / "skills.sqlite3")
    try:
        definition = build_skill(
            "skill:walk",
            "走到 x=12",
            [{"ability": "move_to", "params": {"x": 12, "y": 10}, "max_steps": 2}],
            abilities=[{"id": "move_to", "version": "1.0.0"}],
        )
        record = store.add_skill(definition, status="verified")
        sink = make_event_sink(store)

        def finished(event_id: str, state: str, evidence: dict) -> None:
            sink(
                Event(
                    id=event_id,
                    kind="action_finished_" + state,
                    recorded_at=1.0,
                    save_key=SAVE_KEY,
                    ability="skill:walk",
                    status=state,
                    payload={
                        "action_id": "a1",
                        "ability": "skill:walk",
                        "params": {},
                        "state": state,
                        "detail": "测试",
                        "evidence": evidence,
                        "goal": "走到 x=12",
                    },
                )
            )

        finished("ev:1", "failed", {})
        assert store.skill("skill:walk")["status"] == "candidate"

        store.promote_skill("skill:walk", required_successes=1)  # 失败证据不足以晋级
        assert store.skill("skill:walk")["status"] == "candidate"

        store.add_skill(definition, status="verified")
        finished("ev:2", "failed", {"completion_predicate": {"met": False, "reasons": ["未满足"]}})
        latest = store.latest_skill_version("skill:walk")
        assert store.skill("skill:walk", latest)["status"] == "retired"
    finally:
        store.close()
