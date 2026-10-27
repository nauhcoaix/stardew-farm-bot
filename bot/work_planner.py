"""异步选择技能：玩家意图、能力目录、现场和统一记忆，不生成固定日程。"""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time

from action_history import ActionHistory
from memory_store import MemoryStore
from skill_catalog import ACTIONS, clean_steps


def context_key(snap):
    d = snap.get("date") or {}
    return (snap.get("saveId"), snap.get("instanceId"), snap.get("saveGeneration"),
            d.get("year"), d.get("season"), d.get("day"))


class WorkPlanner:
    def __init__(self, plan_fn, log=print, report=lambda text: None, clock=time.monotonic,
                 refill_interval=1, history_path=None, memory_path=None):
        self.plan_fn, self.log, self.report, self.clock = plan_fn, log, report, clock
        self.refill_interval = refill_interval
        memory_path = memory_path or (str(Path(history_path).with_name("memory.sqlite3")) if history_path else ":memory:")
        self.memory = MemoryStore(memory_path)
        self.history = ActionHistory(history_path, log=log, memory=self.memory)
        self.context = None
        self.intent = ""
        self.goal = ""
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="skill-planner")
        self._pending = None
        self._queue = deque()
        self._revision = 0
        self._retry_at = 0
        self._failures = 0
        self._cooldowns = {}
        self._mission = None
        self._report_pending = False
        self._completed = self._skipped = 0
        self.closed = False

    @property
    def thinking(self):
        return self._pending is not None

    @property
    def queued(self):
        return len(self._queue)

    def interrupt(self):
        self._revision += 1
        self._queue.clear()
        self._mission = None
        self._retry_at = 0

    def submit_task(self, text):
        self.interrupt()
        self.intent = str(text)[:4000]
        self._mission = self.intent
        self._report_pending = True
        self._completed = self._skipped = 0
        self.memory.note(self.intent, source="player")

    def set_steps(self, steps, mission=None):
        self.interrupt()
        self._queue.extend(clean_steps(steps))
        self.intent, self._mission = str(mission or ""), mission
        self._report_pending = bool(mission)
        self._completed = self._skipped = 0
        if mission:
            self.memory.note(self.intent, source="player")

    def allowed(self, name):
        return self.clock() >= self._cooldowns.get(name, 0)

    def cool_down(self, name, seconds=3):
        self._cooldowns[name] = self.clock() + seconds

    def observe(self, snap):
        self.memory.observe(snap)
        self.history.observe(snap)
        current = context_key(snap)
        if current != self.context:
            self.context = current
            self._queue.clear()
            self._cooldowns.clear()
            self._retry_at = 0

    def poll(self, snap, state_text):
        if self.closed:
            return
        self.observe(snap)
        if self._pending and self._pending[0].done():
            future, context, revision, started = self._pending
            self._pending = None
            try:
                result = future.result()
            except Exception as exc:
                self.log("技能规划失败：%s" % type(exc).__name__)
                result = None
            if revision != self._revision or context != self.context:
                self.log("丢弃旧指令、跨日或跨存档的规划结果")
            elif isinstance(result, dict):
                steps = clean_steps(result.get("steps"))
                self._queue.extend(steps)
                self.goal = str(result.get("goal") or "")[:400]
                self._failures = 0
                self._retry_at = self.clock() + (self.refill_interval if steps else 3)
                self.memory.append("plan", snap, source="model", confidence="hypothesis", status="proposed",
                    data={"goal": self.goal, "steps": steps, "reply": str(result.get("reply") or "")[:800]})
                reflection = result.get("reflection")
                if isinstance(reflection, str) and reflection:
                    self.memory.note(reflection, snap=snap)
                self.log("模型选择%d项技能，当前小目标：%s（%.1f秒）" % (len(steps), self.goal, self.clock() - started))
                if self._mission and self._report_pending and result.get("reply"):
                    self.report("任务「%s」当前规划：%s" % (self._mission, str(result["reply"])[:300]))
                    self._report_pending = False
            else:
                self._failures += 1
                self._retry_at = self.clock() + min(60, 3 * 2 ** min(self._failures - 1, 4))
                self.log("模型暂不可用；只继续已经选定的技能，未选择新行动")
        if self._pending or self.clock() < self._retry_at or len(self._queue) > 1:
            return
        context = {"objective": self.intent, "current_goal": self.goal,
                   "remaining_steps": list(self._queue), "max_steps": 6}
        self._pending = (self._pool.submit(self.plan_fn, str(state_text), context, self.intent or None),
                         self.context, self._revision, self.clock())
        self.log("后台选择技能中，执行器继续已选行动")

    def take_action(self, eligible):
        while self._queue:
            action = self._queue.popleft()
            if self.allowed(action["action"]) and eligible(action):
                return action
            self._skipped += 1
            self.memory.result(action, self.memory.current, self.memory.current,
                               {"status": "blocked", "progress": False, "detail": "派发前复核：技能不可用或同一现场已失败"})
            self._revision += 1  # 正在预取的规划仍基于旧前提，不可再接回队列。
            self._retry_at = self.clock() + 1
            self.log("跳过不可用的已选技能：%s" % action["action"])
        return None

    def has_action(self, eligible):
        return any(self.allowed(a["action"]) and eligible(a) for a in self._queue)

    def complete(self, action, progress, repeat=False, retry_other=False):
        if progress and repeat:
            self._queue.appendleft(action)
            return
        if progress:
            self._completed += 1
        else:
            self._skipped += 1
            self._queue.clear()
            self._revision += 1  # 连同失败前发出的后台规划一起失效。
            self.cool_down(action["action"])
        if not self._queue:
            self._retry_at = 0 if progress else self.clock() + 1

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._revision += 1
        if self._pending:
            self._pending[0].cancel()
        self._pool.shutdown(wait=False, cancel_futures=True)
        self.memory.close()
