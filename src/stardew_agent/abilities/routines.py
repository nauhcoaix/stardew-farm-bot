"""机械农活例行程序：把“逐格浇水/锄地/收获/播种/装水”从模型手里收回来。

模型只给任务与上限（``farm_routine``），本地执行器负责：
选工具 → 走近 → 使用 → 用前后观察验证 → 换下一个目标。

边界与其它能力一致：只处理当前观察范围内的目标，不扩大范围、不决定种什么、
不买卖物品；每次委托有目标数、时长与步数上限，取消即时生效。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping

from ..contracts.ability import AbilityDescriptor
from ..contracts.decision import AbilityCall
from ..contracts.errors import AgentError, PremiseFailed, Rejected
from ..contracts.observation import Observation
from ..contracts.results import Progress
from ..observation.identity import target_id
from .base import AbilitySpec, DelegationBase, PremiseCheck, reject_unknown_params, require_int, require_str

#: 例行程序支持的任务；``auto`` 由现场目标决定
TASKS = ("auto", "water", "till", "harvest", "plant", "refill")

#: 每个任务用哪个底层能力。收菜是“把作物拿起来”，必须用 interact：
#: 桥的 use 只会挥动当前手持工具/种子，用镰刀之类的工具会毁掉作物。
ABILITY = {"water": "use", "till": "use", "plant": "use", "refill": "use", "harvest": "interact"}

#: use 任务对应的预期效果（interact 不需要）
EXPECT = {"water": "water", "till": "till", "plant": "plant", "refill": "any"}

#: 任务需要的工具（按 inventory 的 tool 字段或名称匹配，全部小写）
TOOL_HINTS = {
    "water": ("wateringcan", "watering can", "水壶", "洒水壶"),
    "refill": ("wateringcan", "watering can", "水壶", "洒水壶"),
    "till": ("hoe", "锄头"),
}

AUTO_ORDER = ("water", "till", "harvest", "refill")
DEFAULT_TARGETS = 8
DEFAULT_SECONDS = 45


def _matches(entity: Mapping[str, Any], task: str) -> bool:
    if task == "water":
        return entity.get("needsWater") is True
    if task == "till":
        return entity.get("diggable") is True and entity.get("kind") == "tile"
    if task == "harvest":
        return entity.get("harvestable") is True
    if task == "plant":
        return entity.get("kind") == "soil" and bool(entity.get("tilled")) and not entity.get("crop")
    if task == "refill":
        return entity.get("refillable") is True
    return False


def remaining_targets(observation: Observation, task: str) -> list[Mapping[str, Any]]:
    """当前观察里该任务还没做的目标，按与人物距离升序。"""
    origin = observation.position()
    rows = [entity for entity in observation.entities if _matches(entity, task)]
    rows.sort(key=lambda entity: _distance(origin, entity))
    return rows


def _distance(origin: Mapping[str, Any], entity: Mapping[str, Any]) -> int:
    tile = entity.get("tile") or {}
    try:
        return abs(int(tile["x"]) - int(origin.get("x"))) + abs(int(tile["y"]) - int(origin.get("y")))
    except (KeyError, TypeError, ValueError):
        return 10**6


def _item_text(item: Mapping[str, Any]) -> str:
    return " ".join(str(item.get(key) or "") for key in ("tool", "name", "itemId")).lower()


def tool_slot(observation: Observation, task: str) -> int | None:
    """该任务要用的工具槽位；不需要工具时返回 ``None``。"""
    hints = TOOL_HINTS.get(task)
    if not hints:
        return None
    for item in observation.inventory:
        text = _item_text(item)
        if any(hint in text for hint in hints):
            slot = item.get("slot")
            return int(slot) if isinstance(slot, int) else None
    return None


def water_left(observation: Observation) -> int | None:
    """水壶剩余次数；背包里没有水壶或快照没给 ``water`` 时为 ``None``。"""
    for item in observation.inventory:
        if any(hint in _item_text(item) for hint in TOOL_HINTS["water"]):
            left = item.get("water")
            return int(left) if isinstance(left, int) else None
    return None


def seed_slot(observation: Observation, seed: str) -> int | None:
    """按名称/物品 id 找种子槽位。"""
    want = (seed or "").strip().lower()
    if not want:
        return None
    for item in observation.inventory:
        name = str(item.get("name") or "").lower()
        item_id = str(item.get("itemId") or "").lower()
        if want in (name, item_id) or want in name:
            slot = item.get("slot")
            return int(slot) if isinstance(slot, int) else None
    return None


def _task_label(task: str) -> str:
    return {
        "water": "浇水",
        "till": "锄地",
        "harvest": "收获",
        "plant": "播种",
        "refill": "装水",
        "auto": "自动农活",
    }.get(task, task)


class FarmRoutineDelegation(DelegationBase):
    """一次有界的农活委托：逐个目标“选工具 → 走近 → 使用 → 验证”。"""

    def __init__(
        self,
        registry: Any,
        params: Mapping[str, Any],
        observation: Observation,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__("farm_routine", params, clock=clock)
        self.registry = registry
        self.location = observation.location
        self.requested_task = str(params.get("task") or "auto")
        self.seed = str(params.get("seed") or "")
        self.max_targets = int(params.get("max_targets") or DEFAULT_TARGETS)
        self.max_seconds = float(params.get("max_seconds") or DEFAULT_SECONDS)
        self.max_steps = self.max_targets * 8 + 12
        self.task = self._resolve_task(observation)
        self.attempted: set[str] = set()
        self.done: list[dict] = []
        self.failed: list[dict] = []
        self.inner: Any = None
        self.inner_kind = ""
        self.inner_target: Mapping[str, Any] | None = None
        self.record("requested_task", self.requested_task)
        self.record("task", self.task)
        self.record("max_targets", self.max_targets)
        self.record("start_position", observation.position())

    # ---- 目标选择 ---------------------------------------------------
    def _key(self, entity: Mapping[str, Any]) -> str:
        return target_id(self.location, entity)

    def _resolve_task(self, observation: Observation) -> str:
        if self.requested_task != "auto":
            return self.requested_task
        for task in AUTO_ORDER:
            if remaining_targets(observation, task):
                return task
        return "water"

    def _describe(self, entity: Mapping[str, Any]) -> str:
        tile = entity.get("tile") or {}
        return "%s (%s, %s)" % (entity.get("name") or entity.get("kind"), tile.get("x"), tile.get("y"))

    def _current_ref(self, entity: Mapping[str, Any], observation: Observation) -> str | None:
        want = self._key(entity)
        for row in observation.entities:
            if self._key(row) == want and row.get("ref"):
                return str(row["ref"])
        return None

    def _needed_slot(self, observation: Observation) -> int | None:
        if self.task == "plant":
            return seed_slot(observation, self.seed)
        return tool_slot(observation, self.task)

    # ---- 单步推进 ---------------------------------------------------
    async def step(self, observation: Observation) -> Progress:
        if self.cancelled:
            return self.cancelled_progress()
        if observation.location != self.location:
            return self._stop("unknown", "场景已变化（%s → %s），停止本次农活" % (self.location, observation.location))
        if self.elapsed() > self.max_seconds:
            return self._stop("unknown", "超过时长上限 %.0f 秒" % self.max_seconds)
        if self.steps > self.max_steps:
            return self._stop("unknown", "超过步数上限 %d" % self.max_steps)
        energy = observation.field("player.energy")
        if energy.known and isinstance(energy.value, (int, float)) and energy.value <= 0:
            return self._stop("failed", "体力不足（energy=%s），停止处理" % energy.value)

        if self.inner is not None:
            progress = await self.inner.step(observation)
            self.steps += 1
            if not progress.done:
                return Progress(
                    "running",
                    "%s进行中：%s" % (_task_label(self.task), progress.detail),
                    dict(self.evidence),
                )
            finished = self._after_inner(progress, observation)
            if finished is not None:
                return finished
            if self.inner is not None:
                target_text = self._describe(self.inner_target) if self.inner_target else ""
                return Progress(
                    "running",
                    ("%s：%s %s" % (_task_label(self.task), self.inner_kind, target_text)).strip(),
                    dict(self.evidence),
                )

        pending = [
            entity
            for entity in remaining_targets(observation, self.task)
            if self._key(entity) not in self.attempted
        ]
        if not pending:
            if self.done:
                detail = "本次%s：完成 %d 个" % (_task_label(self.task), len(self.done))
                if self.failed:
                    detail += "，失败 %d 个（%s）" % (len(self.failed), self.failed[0]["detail"])
                return self.finish(
                    "completed",
                    detail,
                    done=list(self.done),
                    failed=list(self.failed),
                    remaining=0,
                    partial=bool(self.failed),
                )
            if self.failed:
                return self.finish(
                    "failed",
                    "本次%s没有成功的目标，%d 个失败（%s）"
                    % (_task_label(self.task), len(self.failed), self.failed[0]["detail"]),
                    done=[],
                    failed=list(self.failed),
                    remaining=0,
                )
            return self.finish(
                "unknown",
                "当前观察范围内没有可执行的%s目标" % _task_label(self.task),
                done=[],
                failed=list(self.failed),
                remaining=0,
            )
        if len(self.done) >= self.max_targets:
            return self._stop(
                "unknown",
                "达到本次目标上限 %d，仍有 %d 个未处理" % (self.max_targets, len(pending)),
            )

        target = pending[0]
        self.inner_target = target
        self._begin(target, observation)
        return Progress(
            "running",
            "开始处理%s：%s（已完成 %d 个）" % (_task_label(self.task), self._describe(target), len(self.done)),
            dict(self.evidence),
        )


    # ---- 子委托 -----------------------------------------------------
    def _begin(self, target: Mapping[str, Any], observation: Observation) -> None:
        slot = self._needed_slot(observation)
        selected = (observation.player or {}).get("selectedSlot")
        if slot is not None and selected != slot:
            self._start("select_item", {"slot": slot}, target, observation)
            return
        ref = self._current_ref(target, observation)
        if ref is None:
            self._mark_failed(target, "目标已不在当前观察范围")
            return
        self._start("move_to", {"near": ref, "max_steps": 6}, target, observation)

    def _start(
        self,
        ability: str,
        params: Mapping[str, Any],
        target: Mapping[str, Any],
        observation: Observation,
    ) -> bool:
        try:
            delegation = self.registry.start(
                AbilityCall(ability=ability, params=dict(params)), observation
            )
        except (Rejected, PremiseFailed, AgentError) as exc:
            self._mark_failed(target, "%s 无法启动：%s" % (ability, exc))
            return False
        self.inner = delegation
        self.inner_kind = ability
        self.inner_target = target
        self.record("current", {"ability": ability, "target": self._describe(target)})
        return True

    def _after_inner(self, progress: Progress, observation: Observation) -> Progress | None:
        kind = self.inner_kind
        target = self.inner_target
        self.inner = None
        self.inner_kind = ""
        if target is None:
            return None
        if kind == "select_item":
            if progress.state != "completed":
                self._mark_failed(target, "切换工具失败：%s" % progress.detail)
            return None
        if kind == "move_to":
            if progress.state != "completed":
                self._mark_failed(target, "无法走近目标：%s" % progress.detail)
                return None
            ref = self._current_ref(target, observation)
            if ref is None:
                self._mark_failed(target, "目标已不在当前观察范围")
                return None
            ability = ABILITY[self.task]
            params: dict[str, Any] = {"target": ref}
            if ability == "use":
                params["expect"] = EXPECT[self.task]
            self._start(ability, params, target, observation)
            return None
        if progress.state == "completed":
            tile = target.get("tile") or {}
            self.done.append({"tile": {"x": tile.get("x"), "y": tile.get("y")}, "task": self.task})
            self.attempted.add(self._key(target))
        else:
            self._mark_failed(target, "%s未验证通过：%s" % (_task_label(self.task), progress.detail))
        return None

    def _mark_failed(self, target: Mapping[str, Any], detail: str) -> None:
        self.attempted.add(self._key(target))
        tile = target.get("tile") or {}
        self.failed.append({"tile": {"x": tile.get("x"), "y": tile.get("y")}, "detail": detail})
        self.record("last_error", detail)

    def _stop(self, state: str, detail: str) -> Progress:
        return self.finish(
            state,
            detail,
            done=list(self.done),
            failed=list(self.failed),
            partial=bool(self.done),
        )

    async def cancel(self, reason: str) -> None:
        await super().cancel(reason)
        if self.inner is not None:
            try:
                await self.inner.cancel(reason)
            except Exception as exc:  # noqa: BLE001 - 取消失败也要如实记录
                self.record("inner_cancel_error", type(exc).__name__)


def _validate(params: Mapping[str, Any]) -> Mapping[str, Any]:
    reject_unknown_params(params, ("task", "max_targets", "max_seconds", "seed"))
    task = str(params.get("task") or "auto")
    if task not in TASKS:
        raise Rejected("task 只能是 %s" % "/".join(TASKS))
    max_targets = require_int(
        {"max_targets": params.get("max_targets", DEFAULT_TARGETS)}, "max_targets", low=1, high=40
    )
    seconds = params.get("max_seconds", DEFAULT_SECONDS)
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise Rejected("参数 max_seconds 必须是数字")
    if not 5 <= float(seconds) <= 120:
        raise Rejected("参数 max_seconds 应在 5～120 秒")
    out: dict[str, Any] = {"task": task, "max_targets": max_targets, "max_seconds": float(seconds)}
    if task == "plant":
        out["seed"] = require_str(params, "seed")
    return out


def _premises(params: Mapping[str, Any], observation: Observation) -> list[PremiseCheck]:
    task = str(params.get("task") or "auto")
    checks = [
        PremiseCheck(
            "game",
            False if observation.menu else True,
            "菜单打开时不能做农活" if observation.menu else "没有打开的菜单",
        )
    ]
    energy = observation.field("player.energy")
    if energy.known and isinstance(energy.value, (int, float)):
        checks.append(PremiseCheck("game", float(energy.value) > 0, "体力 %s" % energy.value))
    else:
        checks.append(PremiseCheck("game", None, "体力未知"))

    if task in ("water", "refill", "till"):
        slot = tool_slot(observation, task)
        label = "水壶" if task in ("water", "refill") else "锄头"
        if not observation.inventory:
            checks.append(PremiseCheck("game", None, "背包未知，无法确认是否带%s" % label))
        else:
            checks.append(
                PremiseCheck(
                    "game",
                    slot is not None,
                    "背包里有%s" % label if slot is not None else "背包里没有%s" % label,
                )
            )
        if slot is not None and task in ("water", "refill"):
            left = water_left(observation)
            if left is not None:
                checks.append(
                    PremiseCheck(
                        "game",
                        left > 0,
                        "水壶剩余 %d 次" % left if left > 0 else "水壶没水：先执行 task=refill",
                    )
                )
    if task == "plant":
        seed = str(params.get("seed") or "")
        slot = seed_slot(observation, seed)
        if not observation.inventory:
            checks.append(PremiseCheck("game", None, "背包未知，无法确认种子"))
        elif slot is None:
            checks.append(PremiseCheck("game", False, "背包里没有匹配 %s 的种子" % seed))
        else:
            checks.append(PremiseCheck("game", True, "种子在第 %d 格" % slot))

    counts = {row: len(remaining_targets(observation, row)) for row in AUTO_ORDER}
    if task == "auto":
        if any(counts.values()):
            summary = "，".join("%s %d" % (_task_label(row), value) for row, value in counts.items() if value)
            checks.append(PremiseCheck("game", True, "可处理目标：" + summary))
        elif observation.entities:
            checks.append(PremiseCheck("game", False, "当前观察范围内没有可浇/可锄/可收/可装水的目标"))
        else:
            checks.append(PremiseCheck("game", None, "本次观察没有实体列表，无法确认目标"))
    else:
        count = counts.get(task, 0)
        if count:
            checks.append(PremiseCheck("game", True, "可处理%s目标 %d 个" % (_task_label(task), count)))
        elif observation.entities:
            checks.append(PremiseCheck("game", False, "当前观察范围内没有可%s的目标" % _task_label(task)))
        else:
            checks.append(PremiseCheck("game", None, "本次观察没有实体列表，无法确认目标"))
    return checks


def farm_routine_ability(registry: Any, *, clock: Callable[[], float] = time.monotonic) -> AbilitySpec:
    """构造 ``farm_routine``：模型只给任务与上限，机械步骤由本地执行。"""
    descriptor = AbilityDescriptor(
        id="farm_routine",
        version="1.0.0",
        summary="批量做机械农活：自动选工具、逐个走近并处理当前观察里的目标（浇水/锄地/收获/播种/装水）",
        params={
            "task": "water/till/harvest/plant/refill/auto（默认 auto，按现场目标决定）",
            "max_targets": "本次最多处理几个目标（1～40，默认 8）",
            "max_seconds": "本次最长秒数（5～120，默认 45）",
            "seed": "task=plant 时必填：背包里种子的名称或物品 id（模糊匹配）",
        },
        observation_needs=("entities", "inventory", "player.position", "player.selectedSlot", "player.energy", "menu"),
        scope="只处理当前观察范围内的目标；不决定种什么、不买卖、不离开当前场景",
        completion="该任务在当前观察范围内已没有剩余目标；达到上限或时长时如实报告剩余与失败",
        limits=("max_targets<=40", "max_seconds<=120", "每次只处理一个目标", "取消立即停止"),
    )

    def start(params: Mapping[str, Any], observation: Observation, action_id: str) -> FarmRoutineDelegation:
        delegation = FarmRoutineDelegation(registry, params, observation, clock=clock)
        delegation.action_id = action_id or delegation.action_id
        return delegation

    return AbilitySpec(
        descriptor=descriptor,
        validate=_validate,
        check_premises=_premises,
        start=start,
    )

