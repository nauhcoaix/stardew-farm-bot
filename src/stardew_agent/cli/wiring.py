"""依赖组装：配置、连接、能力、经验与循环只在这里接线。

其他模块不读取彼此的内部状态：本文件负责打开连接、创建存储、注册能力、
提供时钟与观察来源，并在结束时释放资源。
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

from ..abilities import (
    AbilityRegistry,
    ActionPort,
    FishingPort,
    composite_ability,
    farm_routine_ability,
    fish_ability,
    interact_ability,
    menu_ability,
    move_ability,
    select_item_ability,
    use_ability,
)
from ..bridge_client import Bridge
from ..contracts.decision import Decision
from ..contracts.errors import ModelError, TransportError
from ..contracts.experience import Event
from ..contracts.observation import Observation
from ..contracts.results import ActionResult
from ..decision import ContextLimits, build_context, build_messages, parse_decision
from ..embeddings import Encoder
from ..experience import ExperienceStore, handle_failure
from ..fishing_client import read_status, send as fish_send
from ..model_client import ChatClient, default_config_paths, load_settings
from ..observation import from_snapshot, observed_tiles, position_of
from ..exploration import candidates as explore_candidates
from ..exploration import visited_cells
from ..repeat_guard import RepeatGuard, RepeatPolicy
from ..runtime import (
    ActionOwnership,
    AgentLoop,
    ConsoleReporter,
    Recorder,
    RunBudget,
    StopFile,
    combine_sinks,
    default_action_key,
)
from .config import AgentConfig


def result_from_payload(payload: Mapping[str, Any]) -> ActionResult:
    """从事件载荷还原动作结果（用于持久化证据）。"""
    allowed = {
        "action_id",
        "ability",
        "params",
        "state",
        "detail",
        "progress",
        "spent_seconds",
        "before_sampled_at",
        "after_sampled_at",
        "evidence",
        "error",
    }
    return ActionResult(**{key: payload[key] for key in allowed if key in payload})


class MailboxFishingPort:
    """把同步文件邮箱包装成异步端口，避免阻塞事件循环。"""

    def __init__(self, mailbox: Path) -> None:
        self.mailbox = Path(mailbox)

    async def start(self, limits: Mapping[str, Any]) -> Mapping[str, Any]:
        return await asyncio.to_thread(fish_send, self.mailbox, "start", dict(limits))

    async def status(self) -> Mapping[str, Any]:
        return await asyncio.to_thread(read_status, self.mailbox)

    async def stop(self) -> Mapping[str, Any]:
        return await asyncio.to_thread(fish_send, self.mailbox, "stop")


def build_registry(
    action_port: ActionPort | None,
    fishing_port: FishingPort | None = None,
    *,
    store: ExperienceStore | None = None,
    expose_mechanics: bool = False,
) -> AbilityRegistry:
    """注册基础能力；有持久化的已验证技能时再把它们作为能力注册。

    ``select_item`` / ``use`` 是底层机械动作，默认只给本地例行程序（``farm_routine``）
    使用，不进模型能力目录；``expose_mechanics=True`` 仅用于调试或回归对比。
    """
    registry = AbilityRegistry()

    def mechanic(spec: Any) -> Any:
        return spec if expose_mechanics else replace(spec, exposed=False)

    if action_port is not None:
        registry.register(move_ability(action_port))  # type: ignore[arg-type]
        registry.register(mechanic(select_item_ability(action_port)))
        registry.register(mechanic(use_ability(action_port)))
        registry.register(interact_ability(action_port))
        registry.register(menu_ability(action_port))
        # 例行程序要调用上面的底层能力，所以放在它们之后注册。
        registry.register(farm_routine_ability(registry))
    if fishing_port is not None:
        registry.register(fish_ability(fishing_port))
    if store is not None:
        for row in store.active_skills():
            try:
                registry.register(composite_ability(dict(row["definition"]), registry))
            except Exception:  # noqa: BLE001 - 版本过期/定义损坏的技能不应阻断启动
                continue
    return registry


def make_event_sink(store: ExperienceStore) -> Callable[[Event], None]:
    """事件落库；动作结束事件同时生成证据与技能运行记录。"""

    def sink(event: Event) -> None:
        store.append_event(event)
        if not event.kind.startswith("action_finished_"):
            return
        payload = dict(event.payload or {})
        raw_result = payload.get("result")
        result_payload = raw_result if isinstance(raw_result, Mapping) else payload
        result = result_from_payload(result_payload)
        store.record_action(result, save_key=event.save_key, session=event.session, goal=str(payload.get("goal") or ""))
        ability = str(result.ability)
        if ability.startswith("skill:"):
            version = store.latest_skill_version(ability)
            if version is None:
                return
            store.record_skill_run(
                ability,
                version,
                event.id,
                result.state,
                cancelled_cleanly=result.state == "cancelled",
            )
            predicate = (result.evidence or {}).get("completion_predicate") or {}
            false_completion = result.state == "failed" and predicate.get("met") is False
            if result.state == "failed":
                verdict = handle_failure(premises_held=True, false_completion=false_completion)
                if verdict == "retired":
                    store.retire_skill(ability, "误报完成：完成判据未满足", version=version)
                else:
                    store.demote_skill(ability, version, "满足前提仍失败，退回候选并要求新证据")
            store.promote_skill(ability)

    return sink


def build_candidates(observation: Observation, store: ExperienceStore, *, limit: int = 16) -> dict:
    """用本次观察 + 该存档已知地形生成候选；不读别的存档。"""
    start = position_of(observation)
    if start is None:
        return {"items": [], "truncated": False, "reason": "本次观察没有人物位置"}
    known = []
    if observation.location:
        known = store.known_tiles(observation.save_key, observation.location)
    return explore_candidates(
        observed=observed_tiles(observation),
        known=known,
        start=start,
        entities=observation.entities,
        location=observation.location,
        visited=visited_cells(known),
        scope=observation.scope,
        limit=limit,
    )


def make_decider(
    model: ChatClient,
    registry: AbilityRegistry,
    store: ExperienceStore,
    budget: RunBudget,
    config: AgentConfig,
    state: dict,
) -> Callable[[Observation, ActionResult | None, Sequence[Mapping[str, Any]]], Awaitable[Decision | None]]:
    """组装一次模型决策：检索经验 → 组装上下文 → 请求 → 解析校验。"""

    async def decide(
        observation: Observation,
        last_result: ActionResult | None,
        recent_results: Sequence[Mapping[str, Any]] = (),
    ) -> Decision | None:
        limits = ContextLimits(max_entities=config.max_context_entities)
        recent = list(recent_results)
        if not recent and observation.save_key:
            # 重启后内存历史为空：从证据库补读，让模型仍能看到之前的失败。
            recent = store.recent_actions(observation.save_key, limit=limits.max_recent_actions)
        query = "%s %s" % (observation.location or "", state.get("goal") or config.long_term_goal)
        experiences = store.recall(query, save_key=observation.save_key, limit=8)
        context = build_context(
            observation=observation,
            abilities=registry.descriptors(),
            candidates=build_candidates(observation, store),
            experiences=experiences,
            last_result=last_result.to_dict() if last_result else None,
            recent_results=recent,
            budget=budget.snapshot(),
            long_term_goal=config.long_term_goal,
            goal_revision=int(state.get("goal_revision") or 0),
            limits=limits,
        )
        reply = await model.complete(
            build_messages(context, include_completion=config.model_completion_enabled)
        )
        budget.record_tokens(reply.total_tokens)
        if not reply.parsed:
            raise ModelError("模型回复没有可解析的 JSON 对象")
        decision = parse_decision(
            reply.parsed,
            observation=observation,
            validate_params=registry.validate_model_params,
            model=reply.model,
        )
        state["goal"] = decision.goal
        state["goal_revision"] = decision.goal_revision
        return decision

    return decide


def make_observer(bridge: Bridge, config: AgentConfig, store: ExperienceStore) -> Callable[[], Awaitable[Observation]]:
    """观察一次：桥快照 → 观察记录 → 把地形写入该存档的地图索引。"""

    async def observe() -> Observation:
        snapshot = await bridge.snapshot(config.snapshot_radius)
        observation = from_snapshot(snapshot)
        tiles = observed_tiles(observation)
        if tiles and observation.location:
            # 存储是本地 SQLite 写入（每快照几百行），保持在同一线程，避免把连接跨线程共享。
            store.record_tiles(observation.save_key, observation.location, tiles, mark_visited=True)
        return observation

    return observe


@dataclass
class Runtime:
    """一次运行装配好的对象集合；退出时统一释放。"""

    config: AgentConfig
    store: ExperienceStore
    registry: AbilityRegistry
    loop: AgentLoop
    recorder: Recorder
    budget: RunBudget
    bridge: Bridge | None = None
    model: ChatClient | None = None
    guard: RepeatGuard | None = None
    owns_store: bool = True

    async def close(self) -> None:
        if self.bridge is not None:
            await self.bridge.close()
            self.bridge = None
        if self.owns_store:
            self.store.close()


async def build_runtime(
    config: AgentConfig,
    *,
    bridge: Any | None = None,
    model: ChatClient | None = None,
    fishing_port: FishingPort | None = None,
    observe: Callable[[], Awaitable[Observation]] | None = None,
    store: ExperienceStore | None = None,
    include_fishing: bool | None = None,
    console: bool | None = None,
) -> Runtime:
    """按配置组装运行对象。

    ``bridge`` / ``model`` / ``fishing_port`` / ``observe`` / ``store`` 可注入替身，
    便于离线测试；真实运行时只从配置创建。
    """
    from ..fishing_client import is_installed

    if model is None:
        paths = ([config.model_config] if config.model_config else []) + default_config_paths()
        settings = load_settings(paths, required=True)
        model = ChatClient(settings)  # type: ignore[arg-type]

    encoder = Encoder(config.embedding_dir) if config.embedding_dir else None
    owns_store = store is None
    if bridge is None:
        bridge = Bridge(endpoint=config.bridge_endpoint)
        try:
            await bridge.__aenter__()
        except Exception as exc:  # noqa: BLE001 - 连不上游戏桥是最常见的启动失败
            raise TransportError(
                "无法连接游戏桥 %s（确认游戏已启动并已加载 AgentStardew）：%s"
                % (config.bridge_endpoint, type(exc).__name__)
            ) from exc
    store = store or ExperienceStore(config.experience_db, encoder=encoder)

    if include_fishing is None:
        include_fishing = is_installed(config.game_path)
    port = fishing_port if fishing_port is not None else (MailboxFishingPort(config.fishing_mailbox) if include_fishing else None)

    registry = build_registry(
        bridge, port, store=store, expose_mechanics=config.expose_mechanics
    )
    budget = RunBudget(
        wall_clock_seconds=config.budget_seconds,
        model_requests=config.budget_requests,
        min_interval_seconds=config.model_request_interval_seconds,
    )
    guard = RepeatGuard(
        RepeatPolicy(
            window_seconds=config.repeat_window_seconds,
            max_runs=config.repeat_max_runs,
            exempt_abilities=frozenset(config.repeat_exempt_abilities),
        )
    )
    recorder = Recorder(
        sink=combine_sinks(
            make_event_sink(store),
            ConsoleReporter(enabled=config.console_output if console is None else bool(console)),
        )
    )
    observer = observe or make_observer(bridge, config, store)
    state: dict = {"goal": "", "goal_revision": 0}
    decider = make_decider(model, registry, store, budget, config, state)
    loop = AgentLoop(
        observe=observer,
        request_decision=decider,
        build_delegation=lambda call, observation: registry.start(call, observation),
        budget=budget,
        ownership=ActionOwnership(),
        recorder=recorder,
        guard=guard,
        premises=registry.predicates,
        stop=StopFile(config.stop_path),
        poll_seconds=config.poll_seconds,
        step_interval_seconds=config.step_interval_seconds,
        use_model_completion=config.model_completion_enabled,
        action_key=default_action_key,
    )
    return Runtime(
        config=config,
        store=store,
        registry=registry,
        loop=loop,
        recorder=recorder,
        budget=budget,
        bridge=bridge if isinstance(bridge, Bridge) else None,
        model=model,
        guard=guard,
        owns_store=owns_store,
    )


def write_state(path: Path, payload: Mapping[str, Any]) -> None:
    """原子写运行状态，便于 ``status`` 子命令与外部监控读取。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


async def run_agent(
    config: AgentConfig,
    *,
    max_actions: int | None = None,
    bridge: Any | None = None,
    model: ChatClient | None = None,
    fishing_port: FishingPort | None = None,
    observe: Callable[[], Awaitable[Observation]] | None = None,
    store: ExperienceStore | None = None,
    include_fishing: bool | None = None,
    console: bool | None = None,
) -> dict:
    """运行一次实时 Agent，返回并保存汇总。"""
    import time as _time

    stop = StopFile(config.stop_path)
    stop.clear()
    runtime = await build_runtime(
        config,
        bridge=bridge,
        model=model,
        fishing_port=fishing_port,
        observe=observe,
        store=store,
        include_fishing=include_fishing,
        console=console,
    )
    try:
        summary = await runtime.loop.run(max_actions=max_actions)
        payload = {
            "finished_at": _time.time(),
            "summary": summary.to_dict(),
            "budget": runtime.budget.snapshot(),
            "guard": runtime.guard.snapshot() if runtime.guard else None,
            "config": config.public(),
            "store": runtime.store.summary(),
            "abilities": [descriptor.id for descriptor in runtime.registry.descriptors()],
            "hidden_abilities": [
                descriptor.id
                for descriptor in runtime.registry.all_descriptors()
                if not runtime.registry.model_visible(descriptor.id)
            ],
        }
    finally:
        await runtime.close()
    write_state(config.state_path, payload)
    return payload


def status_report(config: AgentConfig) -> dict:
    """读取上次运行状态、经验库汇总与已验证技能；不启动游戏。"""
    state: dict = {}
    try:
        state = json.loads(config.state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    store_summary: dict = {}
    skills: list[dict] = []
    actions: dict = {}
    if Path(config.experience_db).exists():
        store = ExperienceStore(config.experience_db)
        try:
            store_summary = store.summary()
            actions = store.action_stats()
            skills = [
                {"id": row["id"], "version": row["version"], "status": row["status"]}
                for row in store.list_skills()
            ]
        finally:
            store.close()
    return {
        "state": state,
        "store": store_summary,
        "actions": actions,
        "skills": skills,
        "stop_requested": StopFile(config.stop_path).reason(),
    }


def export_evidence(config: AgentConfig, out_path: str | Path, *, event_limit: int = 5000) -> dict:
    """导出脱敏证据：事件、经验、技能与汇总。

    不包含模型密钥；模型回复原文不落库，导出内容只含结构化字段。
    """
    out = Path(out_path)
    if not Path(config.experience_db).exists():
        raise FileNotFoundError("经验库不存在：%s" % config.experience_db)
    store = ExperienceStore(config.experience_db)
    try:
        payload = {
            "exported_at": __import__("time").time(),
            "summary": store.summary(),
            "events": store.all_events(limit=event_limit),
            "experiences": store.list_experiences(),
            "skills": store.list_skills(),
        }
    finally:
        store.close()
    write_state(out, payload)
    return {
        "path": str(out),
        "events": len(payload["events"]),
        "experiences": len(payload["experiences"]),
        "skills": len(payload["skills"]),
    }


async def doctor_report(config: AgentConfig, *, probe_bridge: bool = True, timeout: float = 3.0) -> dict:
    """只读自检：配置、模型、游戏桥、钓鱼 Mod、向量模型与存储路径。"""
    from ..embeddings import Encoder
    from ..fishing_client import doctor as fishing_doctor
    from ..fishing_client import is_installed

    report: dict[str, Any] = {"config": config.public()}
    paths = ([config.model_config] if config.model_config else []) + default_config_paths()
    try:
        settings = load_settings(paths, required=False)
        report["model"] = dict(settings.public(), available=True) if settings else {"available": False}
    except Exception as exc:  # noqa: BLE001
        report["model"] = {"available": False, "error": type(exc).__name__}

    if probe_bridge:
        bridge = Bridge(endpoint=config.bridge_endpoint)
        try:
            await bridge.__aenter__()
            await asyncio.wait_for(bridge.hello(), timeout)
            report["bridge"] = {"connected": True, "endpoint": config.bridge_endpoint}
        except Exception as exc:  # noqa: BLE001
            report["bridge"] = {
                "connected": False,
                "endpoint": config.bridge_endpoint,
                "error": type(exc).__name__,
                "detail": str(exc)[:200],
            }
        finally:
            try:
                await bridge.close()
            except Exception:  # noqa: BLE001
                pass
    else:
        report["bridge"] = {"connected": None, "reason": "未探测"}

    report["fishing"] = {
        "installed": is_installed(config.game_path),
        "mailbox": str(config.fishing_mailbox),
        "doctor": fishing_doctor(config.game_path, runtime=config.fishing_mailbox),
    }
    encoder = Encoder(config.embedding_dir) if config.embedding_dir else Encoder()
    report["embeddings"] = encoder.status()

    db_path = Path(config.experience_db)
    writable = False
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        probe = db_path.parent / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        writable = True
    except OSError:
        writable = False
    report["store"] = {"path": str(db_path), "writable": writable, "exists": db_path.exists()}
    return report



