"""控制台输出层：目标、动作、结果的格式化与健壮性。"""

from __future__ import annotations

import io
import time

from stardew_agent.contracts.experience import Event
from stardew_agent.runtime import ConsoleReporter, combine_sinks, format_event

NOW = 1_700_000_000.0


def make(kind: str, *, ability: str | None = None, status: str = "", payload: dict | None = None) -> Event:
    return Event(
        id="ev:1",
        kind=kind,
        recorded_at=NOW,
        save_key="save:1",
        ability=ability,
        status=status,
        payload=payload or {},
    )


def text(event: Event) -> str:
    return "\n".join(format_event(event))


def test_目标与动作一起显示():
    out = text(
        make(
            "decision_received",
            ability="move_to",
            payload={"goal": "走到耕地旁", "params": {"x": 12, "y": 10}, "goal_revision": 0},
        )
    )
    assert "目标：走到耕地旁" in out
    assert "计划动作：move_to" in out
    assert '"x": 12' in out


def test_不行动时说明原因():
    out = text(make("decision_received", payload={"goal": "先观察", "note": "缺少体力信息"}))
    assert "目标：先观察" in out
    assert "暂不行动：缺少体力信息" in out
    assert "选择动作" not in out


def test_成功失败未知取消四种结果可区分():
    completed = text(make("action_finished_completed", ability="use", payload={"detail": "观察到浇水变化"}))
    failed = text(make("action_finished_failed", ability="use", payload={"detail": "判据未满足"}))
    unknown = text(make("action_finished_unknown", ability="use", payload={"detail": "快照没有暴露变化"}))
    cancelled = text(make("action_finished_cancelled", ability="use", payload={"detail": "运行结束"}))
    assert "成功：观察到浇水变化" in completed
    assert "失败：判据未满足" in failed
    assert "结果未知：快照没有暴露变化" in unknown
    assert "不要重发" in unknown
    assert "已取消：运行结束" in cancelled


def test_超长说明被截断():
    out = text(make("action_finished_failed", payload={"detail": "x" * 500}))
    assert out.count("x") <= 200
    assert out.endswith("…")


def test_拦截与丢弃决策有明确提示():
    blocked = text(make("repeat_blocked", payload={"reason": "这个动作 8 秒内刚执行过"}))
    discarded = text(make("decision_discarded", payload={"reasons": ["场景已变化", "菜单状态变化"]}))
    assert "拦截：这个动作 8 秒内刚执行过" in blocked
    assert "丢弃过期决策：场景已变化；菜单状态变化" in discarded


def test_旧ref改绑会提示坐标():
    rebound = text(
        make(
            "decision_rebound",
            ability="move_to",
            payload={"changes": {"near": {"from": "@s1:e9", "to_tile": {"x": 12, "y": 10}}}},
        )
    )
    assert "重新绑定目标" in rebound
    assert "旧ref→坐标(12,10)" in rebound


def test_等待与预算用尽():
    waiting = text(make("idle_wait", payload={"reason": "没有可执行委托", "seconds": 12.3}))
    assert "等待：没有可执行委托（已等 12 秒）" in waiting
    assert "预算用尽" in text(make("budget_exhausted"))


def test_运行汇总包含各类计数():
    out = text(
        make(
            "run_finished",
            payload={
                "summary": {
                    "actions": 3,
                    "completed": 2,
                    "failed": 1,
                    "unknown": 0,
                    "cancelled": 0,
                    "blocked": 1,
                    "elapsed_seconds": 42.4,
                    "stop_reason": "max_actions",
                }
            },
        )
    )
    assert "动作 3" in out
    assert "成功 2" in out
    assert "失败 1" in out
    assert "42.4 秒" in out
    assert "max_actions" in out


def test_未知事件不输出():
    assert format_event(make("guard_reset")) == []
    assert format_event(make("action_step", payload={})) == []


def test_报告器打印并去重进度():
    stream = io.StringIO()
    reporter = ConsoleReporter(stream=stream)
    reporter(make("action_started", ability="move_to", payload={"params": {"x": 1, "y": 2}, "goal": "走到田边"}))
    reporter(make("action_step", ability="move_to", payload={"detail": "已下发移动请求"}))
    reporter(make("action_step", ability="move_to", payload={"detail": "已下发移动请求"}))
    reporter(make("action_finished_completed", ability="move_to", payload={"detail": "到达"}))
    out = stream.getvalue()
    assert out.count("已下发移动请求") == 1
    assert "开始执行：move_to" in out
    assert "（目标：走到田边）" in out
    assert "成功：到达" in out


def test_关闭后不打印():
    stream = io.StringIO()
    reporter = ConsoleReporter(stream=stream, enabled=False)
    reporter(make("run_started", payload={"budget": {"wall_clock_seconds": 60, "model_requests": 5}}))
    assert stream.getvalue() == ""


def test_打印失败不影响运行():
    class BrokenStream:
        def write(self, *_args, **_kwargs):
            raise UnicodeEncodeError("gbk", "", 0, 1, "boom")

        def flush(self):
            raise UnicodeEncodeError("gbk", "", 0, 1, "boom")

    reporter = ConsoleReporter(stream=BrokenStream())  # type: ignore[arg-type]
    reporter(make("run_started", payload={"budget": {}}))  # 不应抛出
    assert reporter.enabled is False


def test_组合sink会转发给每一个():
    first: list[str] = []
    second: list[str] = []

    def sink_a(event: Event) -> None:
        first.append(event.kind)

    def sink_b(event: Event) -> None:
        second.append(event.kind)

    combined = combine_sinks(sink_a, None, sink_b)
    combined(make("run_started"))
    assert first == ["run_started"]
    assert second == ["run_started"]
