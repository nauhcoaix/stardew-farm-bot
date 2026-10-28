# 公开接口契约

只列各模块的对外函数/类。内部实现可调整，但下列签名与语义属于契约；变更需同时更新
`docs/migration.md`、示例与测试，并递增 `contracts/version.py` 中相应版本。

## contracts

纯数据记录与错误，无 I/O。

- `Observation`：`save_key/session/snapshot_id/location/sampled_at/game_time/player/
  inventory/entities/menu/scope/unknowns/source`；方法 `field(path) -> Field`（状态为
  `observed` / `unknown` / `unsupported`）、`entity(ref)`、`entities_at(tile)`、
  `same_scene_as(other)`、`position()`、`to_dict()`。
- `Decision`、`AbilityCall`、`ActionResult`、`Progress`、`Delegation`（协议）、
  `AbilityDescriptor`、`Event`、`ExperienceRecord`、`SkillVersion`、`SkillRun`、`Premise`。
  `ActionResult.to_brief(goal="", recorded_at=None)` 产出给模型看的历史条目。
- 错误：`AgentError` 及子类 `ConfigError/TransportError/ResultUnknown/ProtocolError/
  Rejected/PremiseFailed/BudgetExhausted/OwnershipBusy/Stopped/ModelError/
  ModelUnavailable/ExperienceError`，均带稳定 `code`。
- 版本：`SCHEMA_VERSION=1`、`BRIDGE_PROTOCOL_VERSION=1`、`FISHING_STATUS_VERSION=1`、
  `SKILL_SCHEMA_VERSION=1`。

## observation（白名单 8，纯函数）

- `from_snapshot(snapshot, *, source="bridge", sampled_at=None) -> Observation`
- `to_snapshot(observation) -> dict`、`observed_tiles(observation)`、`position_of(observation)`
- `save_key(snap)`、`scene(snap)`、`position(snap)`、`semantic(entity)`、
  `target_id(location, entity)`、`dumps(value)`
- `at_tile(snap, entity)`、`target_state(snap, entity)`、
  `target_progress(action, before, after, entity, verified=False)`

不创建数据库、不控制游戏；`target_id` 是语义匹配键，不能替代桥的临时 `ref`。

## exploration（白名单 7，纯函数）

- `build_grid(observed, known, *, extra_passable=()) -> dict[Cell, bool]`（本次观察优先）
- `reachable(cells, start, limit=20000)`、`path_to(cells, start, goal, limit=20000)`
- `frontier_candidates(cells, start, *, visited, scope, limit=16, spacing=4) -> dict`
  （含 `items/truncated/considered/order/more`）
- `interaction_candidates(entities, location, *, kinds, limit=16)`
- `candidates(*, observed, known, start, entities, location, visited, scope, limit, spacing)`
- `region_route(links, origin, destination)`、`visited_cells(known)`

## repeat_guard（白名单 9）

- `RepeatPolicy(window_seconds, max_runs, max_tracked=2000, exempt_abilities=frozenset())`：
  前两个参数必填；`exempt_abilities` 是能力 id 白名单，命中者不参与限频检查与计数。
- `RepeatGuard(policy, *, clock)`：
  `check(key, *, ability=None) -> GuardDecision(allowed/reason/kind/runs)`
  （`kind` 为 `recent` / `limit` / `exempt`）、`record(key, *, ability=None)`、
  `runs(key)`、`reset()`、`snapshot()`（含白名单）。

## embeddings（白名单 6）

- `Encoder(model_dir=None, *, batch=16, max_length=128)`：`available`、`load()`、
  `encode(texts) -> list[list[float]] | None`、`encode_one(text)`、`status()`。
- `is_available(dir)`、`cosine(query, matrix)`、`to_blob(vec)`、`from_blob(blob)`、
  `default_model_dir()`（`STARDEW_EMBED_DIR` 优先）。

## bridge_client（白名单 3）

- `Bridge(transport=None, *, endpoint, protocol, default_timeout_ms, connector,
  timeout_grace_seconds)`：异步上下文管理器；`request(command, args, *, timeout_ms,
  action_id)`。
- 薄封装：`hello/snapshot/status/move/select/use/interact/menu/menudump/menuclick/
  menuclickinv/menuclose/shopbuy/chestput/chestget/ship/placechest/bin/reset/recipes/craft/stop`。
- 写命令（`move/select/use/interact/menu/menuclick/menuclickinv/menuclose/shopbuy/craft/
  chestput/chestget/ship/placechest`）带 `actionId`；读命令（`snapshot/status/menudump`…）不带。
- 错误语义：写命令超时/断连 → `ResultUnknown`；读命令 → `TransportError`；
  不匹配响应 → `ProtocolError`；桥返回失败 → `BridgeError(code)`；并发请求 → `OwnershipBusy`。
- **副作用**：网络 I/O；一条连接同一时刻只有一个请求在途。

## fishing_client（白名单 4）

- `read_status(runtime, *, max_age=5, attempts=5, sleep)`：过期状态报错。
- `submit_command(runtime, action, limits=None, excluded=None, run_id=None)`：原子写，不覆盖待处理指令。
- `send(runtime, action, limits=None, ack_timeout=5, *, sleep, monotonic)`：等匹配确认。
- `wait_run(runtime, run_id, timeout, *, sleep, monotonic, report)`：runId 被替换即报错。
- `validate_limits(limits)`、`is_installed(game_path)`、`doctor(game_path, runtime=None)`。
- 错误类型 `FishingError`（同时是 `AgentError` 与 `ValueError`）。

## model_client（白名单 5）

- 任意 OpenAI 兼容 `/chat/completions`：DeepSeek、OpenAI、OpenRouter、Ollama/LM Studio、
  vLLM、Azure 等。`ModelSettings(api_key="", api_base, model, timeout_seconds, temperature,
  max_tokens, max_tokens_param, json_mode, auth, api_key_header, headers, extra_body)`；
  `public()` 不含密钥与额外头取值（只列头名）。
- 地址归一：`normalize_api_base(url)` 把 `https://host/v1` 补成 `…/v1/chat/completions`，
  已是完整端点（含 `chat/completions`，例如 Azure 带 `api-version`）原样保留。
- 密钥：`pick_api_key` 接受任意非占位符字符串（不要求 `sk-` 前缀）；远程端点缺 key 报
  `ConfigError`，本地端点（localhost/127.0.0.1/`auth="none"`/`allow_missing_key`）可以不带。
- `settings_from_config` / `settings_from_env`（`OPENAI_API_KEY`、`OPENAI_BASE_URL`、
  `OPENAI_MODEL`）/ `load_settings(paths, required=False)` / `default_config_paths()`。
- `extract_json(text)`、`ChatClient(settings, *, transport, clock).complete(messages,
  *, timeout, json_object, temperature, max_tokens) -> ModelReply`。请求按 `auth` 发送
  `Authorization: Bearer`、自定义头或不发认证；`extra_body` 最后合并；`content` 为分段数组时拼接。
- `ModelReply`：`content/parsed/model/served_model/prompt_tokens/completion_tokens/
  elapsed_seconds/total_tokens/usage()`。
- 不包含提示词、计划结构或后台生命周期；密钥不进入异常文本，HTTP 错误只带状态码与供应商原因。

## decision

- `build_context(*, observation, abilities, candidates, experiences, last_result,
  recent_results, budget, long_term_goal, goal_revision, limits, now) -> dict`
  （含 `provided_knowledge` 与截断说明）。默认压缩：`observation.tiles` 是通行网格，
  `observation.entity_counts` 是农活数量，`observation.entities` 只列非地块实体
  （`ContextLimits.compact_entities=False` 可回到旧的全量视图）；
  上限由 `max_notable_entities`（默认 24）控制；能力目录只保留 `id/summary/params/scope`，
  `last_result` 只保留状态与关键证据（`compact_abilities` / `compact_results` 可关闭）。
  `recent_actions` 是最近 6 条动作摘要
  （最新在前，含状态/原因/多少秒前，`blocked` 也在内），`recent_failures` 是其中失败/未知/拦截的前 3 条。
- `build_messages(context, *, include_completion=False)`、`build_system_prompt(*, include_completion=False)`、
  `SYSTEM_PROMPT`。默认提示词不含 `completion` 字段：完成只由能力的前后观察验证；
  `include_completion=True`（配置 `model_completion_enabled`）才把模型判据纳入结算。
- `parse_decision(payload, *, observation, validate_params, model)`：非法/未知能力/非法参数
  抛 `Rejected`（错误上带 `ability`）；`completion` / `stop_conditions` 校验失败只忽略该判据，
  记入 `Decision.predicate_errors`，动作照常执行。决策绑定“我们给的观察”。
- `validate(predicate)` / `validate_all` / `evaluate` / `evaluate_all`：只允许
  `== != < <= > >= in not_in exists absent` 与观察字段路径；未知返回 `None`。
- `check_freshness(decision, based_on, current, *, premises) -> Freshness`。
- `rebind_call(call, based_on, current) -> (AbilityCall, notes)`：把旧快照的临时 ref 改绑到当前现场；
  移动的 `near` 退化为 `x/y` 坐标，交互目标改为当前观察里同地块同类的 ref。

## experience

- `ExperienceStore(path=":memory:", *, clock, encoder)`：
  - 事件：`append_event(event)`、`record_action(result, *, save_key, session, goal,
    decision, source)`、`event(id)`、`events_for_save(save_key, *, kind, limit)`、
    `recent_actions(save_key, *, limit=8)`（最近动作摘要，最新在前，供重启后补读）。
  - 经验：`add_experience(...)`、`add_evidence(id, event_id, counterexample=False)`、
    `set_experience_status(id, status, note="")`、`experience(id)`、
    `evidence_counts(id)`、`recall(query, *, save_key, limit, include_candidates,
    include_retired)`、`list_experiences(...)`。
    写成 `status="verified"` 时必须给 `evidence_ids`（或反例），否则抛 `ExperienceError`：
    模型文字与 `confidence` 不能把无证据的主张升级成已验证。
  - 技能：`add_skill(definition, *, ability_versions, status)`、`record_skill_run(...)`、
    `promote_skill(id, *, required_successes=3)`、`retire_skill(id, reason, version=None)`、
    `active_skills()`、`skill(id, version=None)`、`list_skills()`。
  - 地图/事实：`record_tiles(...)`、`known_tiles(save_key, location)`、`record_link(...)`、
    `links(save_key)`、`set_fact(...)`、`get_fact(...)`、`summary()`。
  - 统计：`action_stats(*, save_key=None, since_ts=None)`：聚合全部历史行动，
    `attempts = executed + blocked`，返回 `ratios`（以 attempts 为分母）、
    `failure_ratio_executed`、`by_ability` 与 `summary_line`。
- `validate_skill(definition, *, ability_versions=None)`、`build_skill(...)`。
- `evaluate_promotion(runs, *, required_successes=3)`、`handle_failure(...)`。

## abilities

- `AbilityRegistry(specs=())`：`register/get/known/descriptors/all_descriptors/
  model_visible/validate_params/validate_model_params/check_premises/predicates/start`。
  `start` 统一校验参数与前提；`descriptors` 只返回 `exposed=True` 的能力，
  `validate_model_params` 拒绝模型下发隐藏能力（机械动作）。
- 能力工厂：`move_ability(port)`、`explore_ability(port, candidates, *, clock)`、
  `select_item_ability(port)`、`use_ability(port)`、`interact_ability(port)`、
  `menu_ability(port)`、`inspect_menu_ability(port)`、`buy_item_ability(port)`、
  `menu_click_ability(port)`、`close_menu_ability(port)`、`craft_item_ability(port)`、
  `inventory_action_ability(port)`、`fish_ability(port, *,
  validate_limits)`、`farm_routine_ability(registry, *, care_first=True)`、
  `composite_ability(definition, registry)`。
  `explore_ability` 的候选由接线注入（`build_candidates(...)["items"]`），能力本身不读数据库；
  商店/菜单能力用 `ShopPort` / `MenuPort`（`menudump/shopbuy/menuclick/menuclickinv/menuclose`）。
- `farm_routine_ability`：任务与上限
  `{task: auto|water|till|harvest|plant|refill|chop|clear|mine, max_targets, max_seconds, seed}`；
  内部调用 `select_item`/`move_to`/`use`（收获走 `interact`，不挥动工具），
  因此这些底层动作默认 `exposed=False`。`auto` 只做浇水/锄地/收获/装水
  （水壶空时先补水），砍树、清理、挖矿必须显式指定任务。
  `care_first=True`（默认）时，有作物要浇水/可收获会把 `chop/clear/mine` 用 `user` 前提挡住；
  这三类任务一次最多 8 个目标，且剩余目标不自动续跑。
- `inventory_delta` 比较物品数量与工具的 `water/waterCapacity/tool`：补水这类只改水量的
  动作才能被验证，而不是被判成 `unknown`。
- `PremiseCheck(kind, met, detail)`，`kind ∈ {game, user, budget, advice}`；
  只有 `game/user/budget` 明确为假时才阻止下发。

## runtime

- `RunBudget(*, wall_clock_seconds, model_requests, min_interval_seconds=0, clock)`：
  `start/elapsed/remaining_seconds/requests_left/expire_reason/require/
  next_request_delay/consume_model_request/record_tokens/snapshot`。
  `next_request_delay()` 返回距下次允许请求的秒数（0 表示可以立即请求），
  由运行时 `await sleep` 后再扣减请求数。
- `ActionOwnership(*, clock)`：`acquire(owner, action_id)`、`release(lease)`、
  `force_release()`、`current`。
- `StopSignal()` / `StopFile(path)`：`reason()`、`request(reason)`、`clear()`。
- `HumanInbox(root, *, clock)`：`send(text, kind, ttl_seconds, priority)`、`messages(now, include_finished)`、
  `pending(limit)`、`deliver(ids)`、`follow(ids, note)`、`drop(id, note)`、`summary()`。
  指令文件由 CLI 创建；运行进程只写 `ack.json`，不覆盖别人的新指令。
- `Recorder(*, sink, clock, wall_clock)`：`emit(...)`、`duration(name, seconds)`、`metrics()`；
  `combine_sinks(*sinks)` 把多个接收方（持久化、控制台）合成一个 sink。
- `ConsoleReporter(stream=None, enabled=True)` / `format_event(event)`：实时打印目标、动作与结果；
  打印失败会自我停用，不影响运行。
- `AgentLoop(*, observe, request_decision, build_delegation, budget, ownership, recorder,
  guard, premises, action_key, context_key, stop, poll_seconds, step_interval_seconds,
  recent_history_limit=8, idle_log_seconds, auto_continue=None, max_auto_continue=2,
  clock).run(max_actions=None) -> LoopSummary`。
  `request_decision(observation, last_result, recent_actions)` 在委托结算后调用；
  `recent_actions` 由 `_remember` 维护（最新在前），包含 failed/unknown/blocked。
  存档（`save_key`）变化时清空 `recent_actions` 与 `last_result`，并记录 `context_switched`：
  A 存档的动作历史不会进入 B 存档的决策上下文。
  `auto_continue(result)` 返回同一逻辑调用的 `AbilityCall` 时，循环直接续跑（最多
  `max_auto_continue` 次，跳过限频、记录 `action_continued`），用于 `farm_routine`
  还有剩余目标的情况；`cli/wiring.routine_continuation` 是默认策略。

## cli

- `AgentConfig(...)`：预算与限频必填；字段校验在 `__post_init__`；`console_output` 控制实时输出；
  `repeat_exempt_abilities` 是限频白名单（能力 id 元组，可选，默认空）；
  `model_request_interval_seconds` 是两次模型请求的最小间隔（秒，0 表示不限制）。
- `config_from_mapping(data, *, base_dir)`、`load_config(path, *, overrides)`。
- `build_runtime(...)`、`run_agent(config, *, max_actions, bridge, model, fishing_port,
  observe, store, include_fishing, console)`、`status_report(config)`、
  `export_evidence(config, out, *, event_limit)`、`doctor_report(config, *,
  probe_bridge, timeout)`、`write_state(path, payload)`。
- `main(argv=None) -> int`；子命令 `run/status/stop/doctor/export/stats/skills/tell/inbox`；
  `run --quiet` 关闭实时控制台输出；`stats` 输出行动拦截/失败占比（`--days`、`--save-key`）；
  `tell "指令" [--kind instruction|suggestion|constraint] [--ttl 秒] [--priority 1-9]` 写入人类指令；
  `inbox list [--all]` / `inbox drop --id ...` 查看或撤销指令（`--inbox` 可指定目录）。

