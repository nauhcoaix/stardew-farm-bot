"""经验库：证据、检索、存档隔离、技能版本与晋级判据。"""

from __future__ import annotations

from pathlib import Path

import pytest

from stardew_agent.contracts.errors import ExperienceError
from stardew_agent.contracts.experience import Event, Premise, SkillRun
from stardew_agent.contracts.results import ActionResult
from stardew_agent.experience import (
    ExperienceStore,
    SkillDefinitionError,
    build_skill,
    evaluate_promotion,
    handle_failure,
    validate_skill,
)

SAVE_A = "save:a/player:p1"
SAVE_B = "save:b/player:p2"


@pytest.fixture()
def store(tmp_path: Path):
    instance = ExperienceStore(tmp_path / "experience.sqlite3", clock=lambda: 1000.0)
    yield instance
    instance.close()


def result(state: str = "completed", ability: str = "use") -> ActionResult:
    return ActionResult(
        action_id="a1", ability=ability, params={"target": "@s1:e1"}, state=state, progress=state == "completed"
    )


def test_机制经验可跨存档但存档事实不行(store):
    store.add_experience(
        "浇水会让耕地变湿", status="verified", scope="mechanism", evidence_ids=["ev:water-1"]
    )
    store.add_experience(
        "农场东南角有箱子",
        scope="save",
        scope_save=SAVE_A,
        status="verified",
        evidence_ids=["ev:chest-1"],
    )
    in_a = {row["claim"] for row in store.recall("农场 箱子", save_key=SAVE_A)}
    in_b = {row["claim"] for row in store.recall("农场 箱子", save_key=SAVE_B)}
    assert "农场东南角有箱子" in in_a
    assert "农场东南角有箱子" not in in_b
    assert "浇水会让耕地变湿" in in_b


def test_候选经验带状态返回不会自动变成事实(store):
    store.add_experience("在门口挥锄可能会翻地", status="candidate")
    rows = store.recall("挥锄 翻地", save_key=SAVE_A)
    assert rows and rows[0]["status"] == "candidate"


def test_支持证据与反例分别计数(store):
    record = store.add_experience("手持种子时使用锄地会播种", premises=[Premise("location", "==", "Farm")])
    store.add_evidence(record.id, "ev:1")
    store.add_evidence(record.id, "ev:2", counterexample=True)
    assert store.evidence_counts(record.id) == {"support": 1, "counter": 1}
    stored = store.experience(record.id)
    assert stored is not None
    assert stored["premises"] == [{"path": "location", "op": "==", "value": "Farm"}]


def test_节流拦截不会让已验证技能降级(store):
    from stardew_agent.cli.wiring import make_event_sink

    record = store.add_skill(_definition(), status="verified")
    sink = make_event_sink(store)
    sink(
        Event(
            id="ev:blocked",
            kind="repeat_blocked",
            recorded_at=1.0,
            save_key=SAVE_A,
            ability=record.id,
            status="blocked",
            payload={"reason": "8 秒内重复"},
        )
    )
    assert store.skill(record.id)["status"] == "verified"
    assert store.skill_runs(record.id) == []


def test_验证经验必须带证据不能只靠文字(store):
    with pytest.raises(ExperienceError):
        store.add_experience("模型说浇水会变湿", status="verified", confidence="model")
    record = store.add_experience("浇水会让耕地变湿", status="verified", evidence_ids=["ev:water-1"])
    assert record.status == "verified"
    assert store.evidence_counts(record.id)["support"] == 1


def test_经验可以修订或撤销并保留历史(store):
    record = store.add_experience("下雨天不用浇水", status="verified", evidence_ids=["ev:rain-1"])
    store.set_experience_status(record.id, "retired", note="雨不覆盖温室")
    stored = store.experience(record.id)
    assert stored["status"] == "retired"
    assert stored["version"] >= 2
    assert store.recall("下雨天", save_key=SAVE_A) == []
    assert store.recall("下雨天", save_key=SAVE_A, include_retired=True)


def test_存档经验必须指明存档(store):
    with pytest.raises(ExperienceError):
        store.add_experience("某处有水源", scope="save")


def test_事件落库可回读(store):
    event_id = store.record_action(result(), save_key=SAVE_A, goal="给作物浇水")
    payload = store.event(event_id)
    assert payload is not None
    assert payload["payload"]["result"]["state"] == "completed"
    assert payload["confidence"] == "execution"


def _definition(skill_id: str = "skill:water") -> dict:
    return build_skill(
        skill_id,
        "先选水壶再对目标浇水",
        [
            {"ability": "select_item", "params": {"slot": 1}},
            {"ability": "use", "params": {"target": "@s1:e1", "expect": "water"}},
        ],
        abilities=[
            {"id": "select_item", "version": "1.0.0"},
            {"id": "use", "version": "1.0.0"},
        ],
        premises=[{"path": "location", "op": "==", "value": "Farm"}],
        completion={"path": "player.selectedSlot", "op": "==", "value": 1},
    )


def test_技能晋级需要三次可区分成功和取消测试(store):
    record = store.add_skill(_definition())
    assert record.status == "candidate"
    store.record_skill_run(record.id, record.version, "ev:1", "completed")
    store.record_skill_run(record.id, record.version, "ev:2", "completed")
    assert store.promote_skill(record.id).promote is False
    store.record_skill_run(record.id, record.version, "ev:3", "completed")
    assert store.promote_skill(record.id).promote is False  # 还缺取消测试
    store.record_skill_run(record.id, record.version, "ev:4", "cancelled", cancelled_cleanly=True)
    decision = store.promote_skill(record.id)
    assert decision.promote is True
    assert store.skill(record.id)["status"] == "verified"
    assert [row["id"] for row in store.active_skills()] == [record.id]


def test_同一次执行拆成三段不算三次验证():
    runs = [
        SkillRun("skill:x", 1, "ev:same", "completed"),
        SkillRun("skill:x", 1, "ev:same", "completed"),
        SkillRun("skill:x", 1, "ev:same", "completed"),
        SkillRun("skill:x", 1, "ev:cancel", "cancelled", cancelled_cleanly=True),
    ]
    decision = evaluate_promotion(runs)
    assert decision.promote is False
    assert "真实成功证据不足" in decision.reason


def test_新版本晋级后旧版本停用但保留(store):
    first = store.add_skill(_definition())
    for index in range(3):
        store.record_skill_run(first.id, first.version, "ev:%d" % index, "completed")
    store.record_skill_run(first.id, first.version, "ev:c", "cancelled", cancelled_cleanly=True)
    assert store.promote_skill(first.id).promote is True

    second = store.add_skill(_definition())
    assert second.version == first.version + 1
    for index in range(3):
        store.record_skill_run(second.id, second.version, "ev2:%d" % index, "completed")
    store.record_skill_run(second.id, second.version, "ev2:c", "cancelled", cancelled_cleanly=True)
    assert store.promote_skill(second.id).promote is True

    assert store.skill(first.id, first.version)["status"] == "retired"
    assert store.skill(second.id, second.version)["status"] == "verified"
    assert [row["version"] for row in store.active_skills()] == [second.version]


def test_停用技能保留原因(store):
    record = store.add_skill(_definition())
    store.retire_skill(record.id, "违背用户限制")
    assert store.skill(record.id)["retired_reason"] == "违背用户限制"


def test_满足前提仍失败退回候选(store):
    record = store.add_skill(_definition(), status="verified")
    store.demote_skill(record.id, record.version, "满足前提仍失败")
    stored = store.skill(record.id)
    assert stored["status"] == "candidate"
    assert stored["retired_reason"] == "满足前提仍失败"


def test_技能定义拒绝未声明能力与版本不符():
    with pytest.raises(SkillDefinitionError):
        build_skill("skill:bad", "缺声明", [{"ability": "use", "params": {}}], abilities=[])
    with pytest.raises(SkillDefinitionError):
        validate_skill(_definition(), ability_versions={"select_item": "1.0.0", "use": "2.0.0"})
    with pytest.raises(SkillDefinitionError):
        build_skill(
            "skill:too-long",
            "太多步骤",
            [{"ability": "use", "params": {}}] * 13,
            abilities=[{"id": "use", "version": "1.0.0"}],
        )


def test_失败后的状态由前提与违规类型决定():
    assert handle_failure(premises_held=True) == "candidate"
    assert handle_failure(premises_held=True, false_completion=True) == "retired"
    assert handle_failure(premises_held=True, violated_user_limit=True) == "retired"
    assert handle_failure(premises_held=False) == "verified"


def test_地图索引按存档隔离并合并访问标记(store):
    tiles = [
        {"tile": {"x": 1, "y": 1}, "passable": True},
        {"tile": {"x": 2, "y": 1}, "passable": False},
        {"tile": {"x": 3, "y": 1}},
    ]
    assert store.record_tiles(SAVE_A, "Farm", tiles, mark_visited=True) == 2
    store.record_tiles(SAVE_A, "Farm", [{"tile": {"x": 1, "y": 1}, "passable": True}])
    rows = store.known_tiles(SAVE_A, "Farm")
    assert {(row["tile"]["x"], row["tile"]["y"]) for row in rows} == {(1, 1), (2, 1)}
    assert all(row["visited"] for row in rows)
    assert store.known_tiles(SAVE_B, "Farm") == []


def test_地区连接与事实按存档隔离(store):
    store.record_link(SAVE_A, "Farm", "Town", "东侧路口", confirmed=True)
    assert store.links(SAVE_A)[0]["to"] == "Town"
    assert store.links(SAVE_B) == []
    store.set_fact(SAVE_A, "inventory.slots", 12)
    assert store.get_fact(SAVE_A, "inventory.slots") == 12
    assert store.get_fact(SAVE_B, "inventory.slots") is None


def test_行动统计包含拦截与失败占比(store):
    store.record_action(result("completed", ability="move_to"), save_key=SAVE_A)
    store.record_action(result("completed", ability="move_to"), save_key=SAVE_A)
    store.record_action(result("failed", ability="use"), save_key=SAVE_A)
    store.append_event(
        Event(id="ev:b1", kind="repeat_blocked", recorded_at=1.0, save_key=SAVE_A, ability="use", status="blocked")
    )
    store.append_event(
        Event(id="ev:b2", kind="delegation_rejected", recorded_at=1.0, save_key=SAVE_A, ability="move_to", status="blocked")
    )

    stats = store.action_stats()
    assert stats["attempts"] == 5
    assert stats["executed"] == 3
    assert stats["blocked"] == 2
    assert stats["blocked_repeat"] == 1
    assert stats["blocked_premise"] == 1
    assert stats["ratios"]["failed"] == pytest.approx(0.2)
    assert stats["ratios"]["blocked"] == pytest.approx(0.4)
    assert stats["failure_ratio_executed"] == pytest.approx(1 / 3, rel=1e-3)
    assert "拦截" in stats["summary_line"]
    by = {row["ability"]: row for row in stats["by_ability"]}
    assert by["move_to"]["attempts"] == 3
    assert by["use"]["attempts"] == 2


def test_行动统计按存档过滤(store):
    store.record_action(result("completed", ability="move_to"), save_key=SAVE_A)
    store.record_action(result("failed", ability="use"), save_key=SAVE_B)
    assert store.action_stats()["attempts"] == 2
    only_a = store.action_stats(save_key=SAVE_A)
    assert only_a["attempts"] == 1
    assert only_a["failed"] == 0
    assert only_a["completed"] == 1


def test_最近动作可从证据库补读(store):
    store.append_event(
        Event(
            id="ev:a1",
            kind="action",
            recorded_at=100.0,
            save_key=SAVE_A,
            ability="move_to",
            status="completed",
            payload={
                "goal": "走过去",
                "result": {"ability": "move_to", "params": {}, "state": "completed", "detail": "到达", "progress": True},
            },
        )
    )
    store.append_event(
        Event(
            id="ev:a2",
            kind="action",
            recorded_at=200.0,
            save_key=SAVE_A,
            ability="use",
            status="failed",
            payload={
                "goal": "浇水",
                "result": {
                    "ability": "use",
                    "params": {"target": "@s1:e1"},
                    "state": "failed",
                    "detail": "判据未满足",
                },
            },
        )
    )
    rows = store.recent_actions(SAVE_A, limit=5)
    assert [row["state"] for row in rows] == ["failed", "completed"]
    assert rows[0]["goal"] == "浇水"
    assert rows[0]["params"] == {"target": "@s1:e1"}
    assert store.recent_actions(SAVE_B) == []


def _learn(store, event_id: str, *, state="completed", ability="farm_routine", params=None, location="Farm"):
    from stardew_agent.experience import learn_action

    return learn_action(
        store,
        event_id=event_id,
        save_key=SAVE_A,
        location=location,
        ability=ability,
        state=state,
        params=params if params is not None else {"task": "water"},
        ability_versions={"farm_routine": "1.0.0"},
    )


def test_动作结果写入候选经验并合并证据(store):
    first = _learn(store, "ev:a1")
    assert first["learned"] is True and first["created"] is True
    assert first["status"] == "candidate"
    assert first["support"] == 1

    second = _learn(store, "ev:a2")
    assert second["experience_id"] == first["experience_id"]
    assert second["support"] == 2 and second["created"] is False
    assert store.summary()["experiences"]["candidate"] == 1

    third = _learn(store, "ev:a3")
    assert third["support"] == 3
    assert third["upgraded"] is True
    stored = store.list_experiences(save_key=SAVE_A)
    assert len(stored) == 1 and stored[0]["status"] == "verified"


def test_已验证经验遇到反例退回候选(store):
    for index in range(3):
        _learn(store, "ev:s%d" % index)
    assert store.list_experiences(save_key=SAVE_A)[0]["status"] == "verified"

    failed = _learn(store, "ev:f1", state="failed")
    assert failed["experience_id"]
    assert failed["demoted"] is True
    assert failed["status"] == "candidate"
    assert failed["counter"] == 1


def test_未知结果不写经验(store):
    skipped = _learn(store, "ev:u1", state="unknown")
    assert skipped["learned"] is False
    assert store.summary()["experiences"] == {}


def test_重复成功的农活生成候选技能(store):
    for index in range(3):
        _learn(store, "ev:w%d" % index)
    skills = store.list_skills()
    assert skills, "三次真实成功后应登记候选技能"
    assert skills[0]["status"] == "candidate"
    assert skills[0]["definition"]["steps"][0]["ability"] == "farm_routine"
    assert skills[0]["definition"]["steps"][0]["params"]["task"] == "water"


def test_不学习移动这类局部动作(store):
    skipped = _learn(store, "ev:m1", ability="move_to", params={"x": 1, "y": 2})
    assert skipped["learned"] is False
    assert store.summary()["experiences"] == {}



def test_动作结束事件会写入经验(tmp_path: Path):
    from stardew_agent.cli.wiring import make_event_sink

    store = ExperienceStore(tmp_path / "wired.sqlite3", clock=lambda: 1000.0)
    try:
        sink = make_event_sink(store, ability_versions={"farm_routine": "1.0.0"})
        completed = ActionResult(
            action_id="a1",
            ability="farm_routine",
            params={"task": "water"},
            state="completed",
            detail="完成",
            progress=True,
        )
        sink(
            Event(
                id="ev:finished",
                kind="action_finished_completed",
                recorded_at=1000.0,
                save_key=SAVE_A,
                ability="farm_routine",
                status="completed",
                payload={"result": completed.to_dict(), "goal": "浇水", "location": "Farm"},
            )
        )
        assert store.summary()["experiences"]["candidate"] == 1
        assert store.list_experiences(save_key=SAVE_A)[0]["claim"].startswith("Farm")
    finally:
        store.close()



def test_取消测试通过后派生技能晋级(tmp_path: Path):
    from stardew_agent.cli.wiring import make_event_sink

    store = ExperienceStore(tmp_path / "skill.sqlite3", clock=lambda: 1000.0)
    try:
        sink = make_event_sink(store, ability_versions={"farm_routine": "1.0.0"})

        def emit(index: int, state: str) -> None:
            result = ActionResult(
                action_id="a%d" % index,
                ability="farm_routine",
                params={"task": "water"},
                state=state,
            )
            sink(
                Event(
                    id="ev:%d" % index,
                    kind="action_finished_" + state,
                    recorded_at=1000.0,
                    save_key=SAVE_A,
                    ability="farm_routine",
                    status=state,
                    payload={"result": result.to_dict(), "goal": "浇水", "location": "Farm"},
                )
            )

        for index in range(3):
            emit(index, "completed")
        assert store.list_skills()[0]["status"] == "candidate"

        emit(9, "cancelled")
        assert store.list_skills()[0]["status"] == "verified"
    finally:
        store.close()

