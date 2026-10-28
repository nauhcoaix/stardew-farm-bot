"""把运行事件实时打印到控制台：目标、动作、结果。

设计边界：

* 只做格式化与打印，不判断成功、不改变事件内容；
* 打印失败绝不能影响运行（编码错误、管道关闭时自动停用自己）；
* 默认输出中文短句，时间戳为本地时间 ``HH:MM:SS``；
* 同一动作的重复 ``action_step`` 只在说明变化时打印，避免刷屏。

用法：作为 :class:`stardew_agent.runtime.Recorder` 的 sink 之一，
与持久化 sink 通过 :func:`combine_sinks` 组合。
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any, Mapping, TextIO

from ..contracts.experience import Event

MAX_DETAIL = 160
MAX_PARAMS = 120


def _clip(text: Any, limit: int) -> str:
    value = str(text or "").replace("\n", " ").strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _params_text(params: Mapping[str, Any] | None) -> str:
    if not params:
        return ""
    try:
        return _clip(json.dumps(dict(params), ensure_ascii=False, sort_keys=True), MAX_PARAMS)
    except (TypeError, ValueError):
        return ""


def _detail(payload: Mapping[str, Any]) -> str:
    return _clip(payload.get("detail") or payload.get("state") or "（没有更多说明）", MAX_DETAIL)


def _stamp(recorded_at: float) -> str:
    return time.strftime("[%H:%M:%S] ", time.localtime(recorded_at))


def format_event(event: Event) -> list[str]:
    """把一条事件格式化为若干行；返回空列表表示不打印。"""
    payload: Mapping[str, Any] = dict(event.payload or {})
    prefix = _stamp(event.recorded_at)
    kind = event.kind

    if kind == "run_started":
        budget = dict(payload.get("budget") or {})
        return [
            prefix
            + "启动：预算 %s 秒 / %s 次模型请求"
            % (budget.get("wall_clock_seconds"), budget.get("model_requests"))
        ]

    if kind == "decision_received":
        goal = _clip(payload.get("goal") or "（未说明目标）", 120)
        lines = [prefix + "目标：" + goal]
        if event.ability:
            params = _params_text(payload.get("params"))
            lines.append(prefix + "计划动作：" + event.ability + ((" " + params) if params else ""))
        else:
            note = _clip(payload.get("note") or "", MAX_DETAIL)
            lines.append(prefix + "暂不行动" + ("：" + note if note else ""))
        return lines

    if kind == "action_started":
        params = _params_text(payload.get("params"))
        line = prefix + "开始执行：" + (event.ability or "?") + ((" " + params) if params else "")
        goal = _clip(payload.get("goal") or "", 60)
        return [line + ("（目标：" + goal + "）" if goal else "")]

    if kind == "action_step":
        detail = _clip(payload.get("detail") or "", MAX_DETAIL)
        return [prefix + "  … " + detail] if detail else []

    if kind == "action_finished_completed":
        return [prefix + "成功：" + _detail(payload)]
    if kind == "action_finished_failed":
        return [prefix + "失败：" + _detail(payload)]
    if kind == "action_finished_unknown":
        return [prefix + "结果未知：" + _detail(payload) + "（先观察核对，不要重发同一写操作）"]
    if kind == "action_finished_cancelled":
        return [prefix + "已取消：" + _detail(payload)]

    if kind == "decision_rebound":
        changes = dict(payload.get("changes") or {})
        parts: list[str] = []
        for name, row in changes.items():
            if not isinstance(row, Mapping):
                continue
            tile = row.get("to_tile") or {}
            if tile:
                parts.append("%s 旧ref→坐标(%s,%s)" % (name, tile.get("x"), tile.get("y")))
            elif row.get("to"):
                parts.append("%s 旧ref→当前ref" % name)
        return [prefix + "重新绑定目标：" + "；".join(parts)] if parts else []

    if kind == "repeat_blocked":
        return [prefix + "拦截：" + _clip(payload.get("reason") or "同一动作重复", MAX_DETAIL)]
    if kind == "decision_discarded":
        reasons = payload.get("reasons") or []
        return [prefix + "丢弃过期决策：" + _clip("；".join(str(row) for row in reasons), MAX_DETAIL)]
    if kind == "no_action":
        note = payload.get("note") or payload.get("reason") or "等待信息"
        return [prefix + "暂不行动：" + _clip(note, MAX_DETAIL)]
    if kind == "decision_empty":
        return [prefix + "模型没有给出可执行决策，继续观察"]
    if kind == "decision_cancelled":
        return [prefix + "决策请求已取消"]
    if kind == "delegation_rejected":
        return [prefix + "委托被拒绝：" + _clip(payload.get("detail") or payload.get("error") or "", MAX_DETAIL)]
    if kind == "action_not_dispatched":
        return [prefix + "未下发动作：" + _clip(payload.get("reason") or "", MAX_DETAIL)]

    if kind == "observe_failed":
        return [
            prefix
            + "观察失败：%s（%s）" % (payload.get("error") or "未知错误", _clip(payload.get("detail") or "", 100))
        ]
    if kind == "decision_failed":
        return [prefix + "模型请求/解析失败：" + _clip(payload.get("code") or payload.get("error") or "", 80)]
    if kind == "ownership_busy":
        return [prefix + "动作所有权被占用：" + _clip(payload.get("detail") or "", MAX_DETAIL)]
    if kind == "cancel_failed":
        return [prefix + "取消失败：" + _clip(payload.get("error") or payload.get("detail") or "", MAX_DETAIL)]

    if kind == "idle_wait":
        return [
            prefix
            + "等待：%s（已等 %.0f 秒）"
            % (_clip(payload.get("reason") or "没有可执行委托", 80), float(payload.get("seconds") or 0.0))
        ]
    if kind == "budget_exhausted":
        return [prefix + "预算用尽，停止下发新动作"]
    if kind == "run_stopped":
        return [prefix + "运行结束：%s" % _clip(payload.get("reason") or "", 80)]
    if kind == "run_finished":
        summary = dict(payload.get("summary") or {})
        return [
            prefix
            + "汇总：动作 %s（成功 %s / 失败 %s / 未知 %s / 取消 %s / 拦截 %s），用时 %.1f 秒，停止原因 %s"
            % (
                summary.get("actions", 0),
                summary.get("completed", 0),
                summary.get("failed", 0),
                summary.get("unknown", 0),
                summary.get("cancelled", 0),
                summary.get("blocked", 0),
                float(summary.get("elapsed_seconds") or 0.0),
                summary.get("stop_reason") or "unknown",
            )
        ]

    return []


class ConsoleReporter:
    """事件 sink：实时打印到控制台，并在动作阶段变化时给一行进度。"""

    def __init__(self, *, stream: TextIO | None = None, enabled: bool = True) -> None:
        self.stream = stream if stream is not None else sys.stdout
        self.enabled = enabled
        self._last_step = ""

    def __call__(self, event: Event) -> None:
        if not self.enabled:
            return
        try:
            if event.kind == "action_step":
                detail = str((event.payload or {}).get("detail") or "")
                if not detail or detail == self._last_step:
                    return
                self._last_step = detail
            elif event.kind.startswith("action_finished_"):
                self._last_step = ""
            for line in format_event(event):
                print(line, file=self.stream, flush=True)
        except Exception:  # noqa: BLE001 - 控制台不可用不能影响机器人运行
            self.enabled = False


