"""运行事件记录。

事件是证据的最小单位：观察到什么、请求了什么、下发/结束/取消了什么、为什么等待。
记录器只负责汇总与转发，不判断成功，也不提升经验等级。

指标覆盖重构需求要求的延迟与浪费：观察→下发延迟、模型耗时、无委托等待时长、
被丢弃的过期决策数、结果未知次数。
"""

from __future__ import annotations

import time
import uuid
from collections import Counter
from typing import Callable, Mapping

from ..contracts.experience import Event

EventSink = Callable[[Event], None]


def combine_sinks(*sinks: EventSink | None) -> EventSink:
    """把多个接收方合成一个 sink：持久化、控制台输出各司其职。"""
    active = [sink for sink in sinks if sink is not None]

    def sink(event: Event) -> None:
        for receiver in active:
            receiver(event)

    return sink



class Recorder:
    """收集事件与指标，可选转发到持久化接收方（例如经验库）。"""

    def __init__(
        self,
        *,
        sink: EventSink | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        max_events: int = 5000,
    ) -> None:
        self.sink = sink
        self.clock = clock
        self.wall_clock = wall_clock
        self.max_events = int(max_events)
        self.events: list[Event] = []
        self.counts: Counter[str] = Counter()
        self.durations: dict[str, list[float]] = {}

    def emit(
        self,
        kind: str,
        *,
        save_key: str = "",
        session: str = "",
        ability: str | None = None,
        status: str = "",
        source: str = "executor",
        confidence: str = "observed",
        payload: Mapping | None = None,
    ) -> Event:
        event = Event(
            id=uuid.uuid4().hex,
            kind=kind,
            recorded_at=self.wall_clock(),
            save_key=save_key,
            session=session,
            ability=ability,
            status=status,
            source=source,
            confidence=confidence,
            payload=dict(payload or {}),
        )
        self.counts[kind] += 1
        self.events.append(event)
        if len(self.events) > self.max_events:
            del self.events[: len(self.events) - self.max_events]
        if self.sink is not None:
            self.sink(event)
        return event

    def duration(self, name: str, seconds: float) -> None:
        self.durations.setdefault(name, []).append(max(0.0, float(seconds)))

    def metrics(self) -> dict:
        durations = {}
        for name, values in self.durations.items():
            if not values:
                continue
            durations[name] = {
                "count": len(values),
                "total_seconds": round(sum(values), 3),
                "mean_seconds": round(sum(values) / len(values), 3),
                "max_seconds": round(max(values), 3),
            }
        return {
            "counts": dict(self.counts),
            "durations": durations,
            "unknown_results": self.counts.get("action_finished_unknown", 0),
            "discarded_decisions": self.counts.get("decision_discarded", 0),
            "idle_seconds": round(sum(self.durations.get("idle_wait", [])), 3),
        }
