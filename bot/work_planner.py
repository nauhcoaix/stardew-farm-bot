"""后台只处理模型；主线程轮询结果，独占游戏连接与动作执行。"""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import time
from action_history import ActionHistory


ACTIONS = {"harvest", "water", "till_plant", "clear", "chop", "buy_seeds", "sell", "sleep"}
DEFAULT_PLAN = {"buy_qty": 20, "buy_when_below": 5, "clear_limit": 3,
                "clear_min_energy": 70, "chop": False, "sleep_at": 2200}


def context_key(snap):
    d = snap.get("date") or {}
    return (snap.get("instanceId"), snap.get("saveGeneration"),
            d.get("year"), d.get("season"), d.get("day"))


def bounded_int(value, default, low, high):
    try:
        return max(low, min(high, int(value)))
    except (ValueError, TypeError, OverflowError):
        return default


def clean_steps(steps):
    """只接受已有技能与参数；模型不能传目标引用或任意底层命令。"""
    if not isinstance(steps, list):
        return []
    result = []
    for step in steps[:6]:
        if not isinstance(step, dict) or not isinstance(step.get("action"), str) or step["action"] not in ACTIONS:
            continue
        name = step["action"]
        raw = step.get("arg") if isinstance(step.get("arg"), dict) else {}
        arg = {}
        if name in ("clear", "till_plant"):
            arg["limit"] = bounded_int(raw.get("limit"), 3, 0, 24)
        if name == "clear":
            arg["min_energy"] = bounded_int(raw.get("min_energy"), 70, 40, 270)
        if name == "buy_seeds":
            arg["qty"] = bounded_int(raw.get("qty"), 20, 0, 100)
        result.append({"action": name, "arg": arg,
                       "reason": str(step.get("reason") or "按短期计划执行")[:160],
                       "source": "model"})
    return result


class WorkPlanner:
    def __init__(self, plan_fn, log=print, report=lambda text: None, clock=time.monotonic,
                 refill_interval=15, history_path=None):
        self.plan_fn, self.log, self.report, self.clock = plan_fn, log, report, clock
        self.refill_interval = refill_interval
        self.daily = dict(DEFAULT_PLAN)
        self.history = ActionHistory(history_path, log=log)
        self.context = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="farm-planner")
        self._pending = None
        self._queue = deque()
        self._tasks = deque()
        self._revision = 0
        self._retry_at = 0
        self._failures = 0
        self._cooldowns = {}
        self._mission = None
        self._completed = self._skipped = 0
        self.closed = False

    @property
    def thinking(self):
        return self._pending is not None

    @property
    def queued(self):
        return len(self._queue)

    def interrupt(self):
        """废弃旧计划；正在进行的 HTTP 不取消，以免遗留线程后再堆新请求。"""
        self._revision += 1
        self._queue.clear()
        self._tasks.clear()
        if self._mission:
            self.report("任务「%s」已被新指令中断" % self._mission)
        self._mission = None
        self._retry_at = 0

    def submit_task(self, text):
        # 玩家新意图优先；旧模型结果即使晚到也不能覆盖它。
        self.interrupt()
        self._tasks.append(str(text))

    def set_steps(self, steps, mission=None):
        self.interrupt()
        self._queue.extend(clean_steps(steps))
        self._mission = mission
        self._completed = self._skipped = 0

    def allowed(self, name):
        return self.clock() >= self._cooldowns.get(name, 0)

    def cool_down(self, name, seconds=30):
        self._cooldowns[name] = self.clock() + seconds

    def observe(self, snap):
        """控制指令入队前更新会话，避免首次观察清掉刚收到的指令。"""
        self.history.observe(snap)
        current = context_key(snap)
        if current != self.context:
            if self._mission:
                self.report("任务「%s」因日期或存档变化结束，需重新检查目标" % self._mission)
            self.context = current
            self.daily = dict(DEFAULT_PLAN)
            self._queue.clear()
            self._mission = None
            self._cooldowns.clear()
            self._retry_at = 0

    def poll(self, snap, state_text):
        """从不等待模型。状态摘要必须由唯一执行器预先生成，不含 Bridge。"""
        if self.closed:
            return
        self.observe(snap)
        current = self.context

        if self._pending and self._pending[0].done():
            future, key, revision, task, started = self._pending
            self._pending = None
            try:
                result = future.result()
            except Exception as exc:
                self.log("后台规划失败：%s" % type(exc).__name__)
                result = None
            elapsed = self.clock() - started
            if revision != self._revision:
                self.log("丢弃旧指令的规划结果（%.1f秒）" % elapsed)
            elif key != current:
                self.log("丢弃跨日/跨存档的规划结果（%.1f秒）" % elapsed)
                if task:
                    self._tasks.appendleft(task)
            elif isinstance(result, dict):
                raw_daily = result.get("daily")
                if isinstance(raw_daily, dict):
                    for name, low, high in (("buy_qty", 0, 100), ("buy_when_below", 0, 100),
                                            ("clear_limit", 0, 24), ("clear_min_energy", 40, 270),
                                            ("sleep_at", 1800, 2300)):
                        if name in raw_daily:
                            self.daily[name] = bounded_int(raw_daily[name], self.daily[name], low, high)
                    if isinstance(raw_daily.get("chop"), bool):
                        self.daily["chop"] = raw_daily["chop"]
                    for name in ("crop", "reason"):
                        if name in raw_daily:
                            self.daily[name] = str(raw_daily[name])[:160]
                steps = clean_steps(result.get("steps"))
                self._queue.extend(steps)
                self._failures = 0
                self._retry_at = self.clock() + self.refill_interval
                self.log("后台计划就绪：%d步，耗时%.1f秒" % (len(steps), elapsed))
                if task:
                    self._mission = task if steps else None
                    self._completed = self._skipped = 0
                    self.report("任务「%s」规划%s：%s" % (
                        task, "已接入执行队列" if steps else "没有可执行步骤",
                        str(result.get("reply") or "")[:300]))
            else:
                self._failures += 1
                self._retry_at = self.clock() + min(120, 15 * 2 ** min(self._failures - 1, 3))
                self.log("模型暂不可用，继续本地农务，稍后再规划")
                if task:
                    self._tasks.appendleft(task)

        if self._pending or self.clock() < self._retry_at:
            return
        # 当前玩家任务执行完再处理下一计划；自主计划剩一项时提前补充。
        if self._mission or len(self._queue) > 1:
            return
        task = self._tasks.popleft() if self._tasks and not self._queue else None
        self._pending = (self._pool.submit(self.plan_fn, str(state_text), dict(self.daily), task),
                         current, self._revision, task, self.clock())
        self.log("后台规划中，执行器继续工作%s" % ("（玩家任务）" if task else ""))

    def take_action(self, eligible):
        while self._queue:
            action = self._queue.popleft()
            if self.allowed(action["action"]) and eligible(action):
                return action
            self.log("跳过已完成或不可执行的计划步骤：%s" % action["action"])
            self._skipped += 1
        self._finish_mission()
        return None

    def complete(self, action, progress, repeat=False, retry_other=False):
        if (progress or retry_other) and repeat:
            self._queue.appendleft(action)
            return
        if progress:
            self._completed += 1
        else:
            self._skipped += 1
            if not retry_other:
                self.cool_down(action["action"])
        self._finish_mission()

    def _finish_mission(self):
        if self._mission and not self._queue:
            self.report("任务「%s」执行结束：有效完成%d步，跳过或受阻%d步" % (
                self._mission, self._completed, self._skipped))
            self._mission = None
            self._retry_at = 0

    def close(self):
        self.closed = True
        self._revision += 1
        if self._pending:
            self._pending[0].cancel()
        self._pool.shutdown(wait=False, cancel_futures=True)
