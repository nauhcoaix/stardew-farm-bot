# 模型决策全过程（含代码索引）

本文按一次真实运行的时间顺序，说明“模型看到什么 → 决定什么 → 执行器怎么核对 → 结果如何变成证据”。
每一节都标出对应代码位置；**行号会随改动漂移，请优先用函数名搜索**。行号基于当前工作区。

范围：单人存档、一个机器人、实时运行（推理期间游戏不暂停）。不包含联机、多机器人、模型训练。

## 0. 一句话版本

模型只负责“目标 + 一个受限能力调用”（可选、默认不写完成判据），执行器负责观察、改绑临时引用、
校验前提、单一动作所有权、分段执行、重新观察验证，并把成功/失败/未知/取消如实写成事件与经验。

```mermaid
flowchart LR
    A[桥快照] --> B[Observation]
    B --> C[上下文: 经验/候选/预算/上次结果]
    C --> D[模型: 目标+能力调用]
    D --> E[解析与参数校验]
    E --> F[新鲜度检查]
    F --> G[ref 改绑]
    G --> H[限频/前提/所有权/预算]
    H --> I[分段执行]
    I --> J[重新观察并验证]
    J --> K[ActionResult + 事件]
    K --> L[经验库/控制台]
    K --> C
```

## 1. 谁保存什么（唯一拥有者）

| 状态 | 拥有者 | 说明 |
|---|---|---|
| 当前观察 | `runtime/loop.py` | 每轮重新观察，不缓存旧现场 |
| 决策依据的观察 | `loop._decision_based_on` | 用于新鲜度比较和 ref 改绑 |
| 在途决策请求 | `loop._decision_task` | 首版最多一个 |
| 当前委托与租约 | `loop._active` / `loop._lease` | 与 `runtime/ownership.py` 的租约对应 |
| 预算 | `runtime/budget.py` | 墙钟 + 模型请求数，跨子技能累计 |
| 限频计数 | `repeat_guard/guard.py` | 换存档/换游戏日时由 loop 重置 |
| 事件/经验/技能/地图索引 | `experience/store.py` | SQLite，按 `save_key` 隔离事实 |
| 控制台输出 | `runtime/console.py` | 只是 sink，不判断成功 |

## 2. 启动与组装

入口：`cli/main.py:main` → `cli/wiring.py:run_agent`（335）→ `build_runtime`（245）。

组装顺序（`build_runtime`）：

1. 读取模型配置（`model_client/settings.py:load_settings`，104）；没有可用 Key 直接报配置错误，绝不退回固定日程；
2. 连接游戏桥（`bridge_client/client.py`）；连不上抛 `TransportError`，CLI 输出可读的中文错误；
3. 打开经验库 `ExperienceStore`（`experience/store.py:99`）；
4. 注册能力 `build_registry`（90）：移动/选择/使用/交互/菜单/钓鱼 + 已晋级组合技能；
5. 预算 `RunBudget`、限频 `RepeatGuard`、所有权 `ActionOwnership`、停止文件 `StopFile`；
6. 记录器 `Recorder`，sink 由 `combine_sinks`（`runtime/events.py:22`）组合：
   - `make_event_sink(store)`（112）——写入经验库；
   - `ConsoleReporter`（`runtime/console.py:165`）——实时打印；
7. 观察函数 `make_observer`（207）、决策函数 `make_decider`（169）；
8. `AgentLoop`（`runtime/loop.py:90`）。

## 3. 观察：模型看到的世界

`make_observer`（`cli/wiring.py:207`）每次调用：

1. `bridge.snapshot(radius)` → 桥返回结构化快照（人物、背包、地块/作物/对象、NPC、出口、菜单）；
2. `observation.from_snapshot`（`observation/state.py:17`）整理成 `Observation`：
   - 把 `observation.entities` 提升到顶层，统一旧快照两种形状；
   - 计算 `save_key`（存档/玩家，缺失时按会话隔离）、`session`、`snapshot_id`、`sampled_at`；
   - 记录 `unknowns`：观察被截断、没有实体列表、游戏不在运行态等；
3. `store.record_tiles(...)`（`experience/store.py:547`）把本次地形写进**该存档**的地图索引。

字段三态（`contracts/observation.py:Field`）：`observed` / `unknown` / `unsupported`。
例如 `player.health`、`enemies` 明确是 `unsupported`——这是能力缺口，不是“没有敌人”。

**ref 的语义**：形如 `@s63:e196`，`s63` 是快照序号（`mods/game_bridge_mod/Observations.cs`：
`var id = "s" + ++sequence`）。ref **只在这一次快照内有效**；这一点决定了第 7 节的改绑。

## 4. 发起决策（委托结算后）

主循环 `AgentLoop.run`（`runtime/loop.py:146`）：

1. `_halt_reason`：停止文件或预算用尽 → 不再下发新动作；
2. `_observe`：失败只记录 `observe_failed` 并等待，不崩溃；
3. `_note_context`：存档/游戏日变化 → `guard.reset()`；
   **换存档时**额外清空 `recent_actions` 与 `last_result`，记录 `context_switched`：
   A 存档的动作历史不会进入 B 存档的决策上下文（短期目标 `state.goal` 也由
   `make_decider` 在换存档时归零，长期目标文字来自配置、不随存档变化）；
4. 只有**当前没有在途委托、也没有在途决策**时才发起 `_ask`（`asyncio.create_task`），
   并把这次观察记为 `_decision_based_on`；
5. 若有在途委托 → `_step_active` 推进一个分段，然后 `sleep(step_interval_seconds)`，避免空转。

**为什么委托结算后才请求决策**：模型需要看到“这一步到底成功还是失败”才能修正下一步。
若在动作执行期间提前请求，模型看到的是上一次的结果，容易重复同一个失败动作。
推理期间游戏照常运行（不暂停），只是不提前请求；这是“实时”与“看到最新结果”之间的取舍。

`_ask`：

- 先看最小请求间隔：`budget.next_request_delay()`（`runtime/budget.py`）若 >0，
  由 `_sleep_unless_halted`（`runtime/loop.py`）分段等待并随时响应停止，
  记录 `model_rate_limited`；等待期间游戏继续、主循环照常观察；
- **再** `budget.consume_model_request()`——失败/重试都算一次；
- 调 `request_decision(observation, last_result, recent_actions)`：除了最近一次结果，
  还传最近几次动作的摘要（最新在前），由 loop 的 `_remember` 维护；
- 抛错 → `decision_failed`（网络/解析），不重发；
- 成功 → 记录 `model_latency` 与 `decision_received`（目标、修订号、快照、**参数**、原因、
  模型原样的完成判据（若写了）、备注）。

## 5. 组装上下文：给模型什么、不给什么

`make_decider`（`cli/wiring.py:169`）在每次请求前：

1. 检索经验：`store.recall(query, save_key=...)`（`experience/store.py:289`）
   - 命中范围：本存档的 `scope='save'` 事实 + 已验证的 `scope='mechanism'` 机制；
   - 排序：关键词 2-gram / 向量重合 + 验证状态 + 新旧；**相似度不会提升验证等级**；
2. 生成候选：`build_candidates`（149）→ `exploration/candidates.py:candidates`（107）
   - 地形前沿候选带 `x/y`、未知邻格数、是否访问过；
   - 交互候选带 `id`（稳定 target_id）、`ref`、`tile`、语义字段；
   - 返回 `truncated / order / more`，明确“看到的不是世界全部”；
3. `decision/context.py:build_context`：
   - 现场：默认**压缩**——地块合并成 `observation.tiles` 通行网格
     （`origin` + 逐行字符串，`.` 可通行 / `#` 阻挡 / `?` 未知 / 空格 未观察），
     实体列表只列非地块实体（NPC/对象/耕地/水源/门/家具…），每条只留非默认字段，
     上限 `max_notable_entities`（默认 40，按距离排序，`trimmed` 报告 dropped）；
   - `observation.entity_counts`：可锄地/需浇水/可收获/可装水的数量，替代逐格罗列；
   - `abilities`：能力 id、版本、参数说明、观察需求、作用范围、完成判据（隐藏能力不出现）；
   - `candidates`：只留 `id/ref/kind/tile/name`，前沿明细合并为 `considered/truncated`；
   - `experiences` / `last_result`（上一次委托的真实结果）/ `budget`；
   - `recent_actions`：最近 8 条动作（能力、参数摘要、状态、原因、多少秒前），最新在前；
   - `recent_failures`：其中 `failed` / `unknown` / `blocked` 的前 3 条，方便模型优先注意；
   - 刚重启时运行时历史为空，`make_decider` 会从 `store.recent_actions` 补读该存档最近的动作；
   - `long_term_goal` 与 `goal_revision`；
   - `provided_knowledge`：项目主动提供的信息（能力目录、网格与计数含义、ref 时效、
     `blocked` 的含义、长期目标文字）。

实测（真实农场快照 290 个实体、半径 8）：压缩前约 30.7k 字符（≈8.6k–10.6k tokens），
压缩后约 13.1k 字符（≈4.0k–5.0k tokens）；能力目录只保留 `id/summary/params/scope`，
`last_result` 只保留状态与关键证据（不带完整差分）。
`ContextLimits(compact_entities=False)` 可回到旧行为做对比。

不给的东西：每日日程、作物优先级、固定路线、攻略解法、隐藏全局信息、全局地图。

`decision/prompt.py:SYSTEM_PROMPT` 只讲接口与诚实报告规则；`build_messages`（45）把上下文 JSON 化。

## 6. 模型请求、解析与参数校验

1. `model_client/client.py:ChatClient.complete`（112）：urllib 在工作线程里发请求，超时可配；
   异常只报类型，不把密钥或玩家文本写进日志；
2. `extract_json`（55）：从回复文本里取最后一个可解析的 JSON 对象；
3. `ModelReply`（26）：原文、解析结果、服务模型、tokens、耗时；
4. `budget.record_tokens`（`runtime/budget.py:82`）：拿不到 tokens 时标记未知；
5. `decision/parse.py:parse_decision`（27）：
   - `goal` 必填；`ability` 为空表示“暂不行动”，必须写 `note`；
   - `params` 必须是对象，并立即通过 `AbilityRegistry.validate_model_params`
     （`abilities/registry.py:60`）→ 隐藏能力（`use`/`select_item`）在这里被拒绝，能力的 `validate` 再查参数；
   - `completion` / `stop_conditions` 走受限判据校验（`decision/predicates.py:validate`，42）：
     只允许观察字段路径 + `== != < <= > >= in not_in exists absent` + JSON 字面量；
     **判据是可选的**：路径越界（如 `last_result.state`）只忽略该判据并记进
     `Decision.predicate_errors`，不会丢弃整条动作；`decision_received` 与控制台会显示原因。
     `completion` 默认可省略（见第 9 节），`stop_conditions` 仅供参考、不参与结算；
   - 决策**绑定我们给出的那次观察**（`based_on_snapshot_id` / `based_on_sampled_at`），不是模型自称的时间。

任何一步失败都算一次请求消耗，并记录 `decision_failed`；不会带着半成品去执行。

## 7. 执行前：六道闸门

`loop._consume_decision`（290）拿到决策后进入 `_dispatch`（306）。

### 7.1 新鲜度（`decision/freshness.py:check`，31）

比较“决策依据的观察”和“当前观察”：

- 决策声称的 `snapshot_id` 与依据不一致 → 过期（模型不能伪造绑定）；
- 存档/会话/地图变化 → 过期；
- 菜单开合变化 → 过期；
- 能力字段判据（`AbilityRegistry.predicates`，54）明确为假 → 过期；为未知只记录，不阻止；
- **仅游戏时间推进不判过期**，否则实时世界里几乎所有回复都会作废。

过期 → `decision_discarded`，`summary.discarded++`，不下发。

### 7.2 ref 改绑（`decision/binding.py:rebind_call`，36）

这是“为什么委托会被拒绝”一文的关键（见第 13 节）：

- 当前观察已有该 ref → 不动；
- 旧观察里能按 ref 找到目标 → 取其地块：
  - `move_to.near` 换成 `x/y` 坐标（移动只需要坐标，目标可以在观察范围外）；
  - `use` / `interact.target` → 当前观察同地块、同 `kind`/`name` 的实体换成当前 ref；
- 解析不到 → 原样交给能力，由能力拒绝并给可操作提示；
- 改绑写入 `decision_rebound` 事件（控制台显示“重新绑定目标：near 旧ref→坐标(64,18)”）。

### 7.3 限频（`repeat_guard/guard.py:check`，59）

动作键 = 能力 + 规范化参数（`loop.default_action_key`，75）。窗口内重复或累计次数用尽 →
`repeat_blocked`，记 `_last_result(state=blocked)`，模型下一轮能在上下文里看到。
**只有真正下发时才 `record`（79）**，执行器自己的有进展续跑不计数；换存档/换日 `reset`（90）。

`RepeatPolicy.exempt_abilities`（配置 `repeat_exempt_abilities`，默认示例为 `["select_item"]`）
里的能力不参与限频检查与计数：`check`/`record` 收到对应 `ability` 时直接放行，
返回 `kind="exempt"`。它只跳过限频，不跳过参数、前提、所有权与预算校验；写操作
（`use`/`interact`/`fish`）不建议列入白名单，否则会失去“无进展重复”的保护。

### 7.4 前提（`abilities/registry.py:start`，58 → `abilities/base.py`）

`start` 先 `validate` 参数，再 `check_premises`，然后 `raise_for_premises`（133）：

- `PremiseCheck(kind, met, detail)`（30），`kind ∈ game/user/budget/advice`；
- 只有 `game/user/budget` 且 `met is False` 才阻止（`blocking_premises`，128）；
- `met is None`（未知）不阻止，也不当作“不满足”；
- `advice` 永远不阻止，模型可以否决建议。

被拒绝 → `delegation_rejected`，`_last_result(state=blocked, detail=可操作建议)`。

### 7.5 预算二次检查与所有权

- `_halt_reason()` 再查一次：若在决策期间预算用完 → `action_not_dispatched`，不下发；
- `ActionOwnership.acquire("agent", action_id)`（`runtime/ownership.py:43`）：同一时刻只有一个执行器
  能操纵角色（游戏桥与钓鱼 Mod 共用），被占用 → `ownership_busy`。

### 7.6 记账并下发

- `guard.record(key)`（只有到这里才算“新的一次执行”）；
- 记录 `action_started`：动作 ID、参数、目标、实际采用的完成判据、`completion_ignored`、停止条件、
  **观察→下发延迟**、租约；
- 委托进入 `_active`，循环开始分段推进。

## 8. 分段执行（`loop._step_active`，417）

每次只调 `delegation.step(observation)` 一步，随后**重新观察**（`after = await self._observe()`），
不累积旧现场。异常映射：

| 异常 | 结果状态 |
|---|---|
| `ResultUnknown`（写操作超时/断连） | `unknown` |
| `Stopped` | `cancelled` |
| `PremiseFailed` | `failed` |
| 其他 `AgentError` | `unknown`（带错误码） |

各能力的分段与验证：

| 能力 | 位置 | 一步做什么 | 怎么验证 |
|---|---|---|---|
| `move_to` | `abilities/navigation.py:78` | 下发一次桥移动（坐标或 near） | 重新观察位置，到达目标格或四邻格；位置没变即失败 |
| `explore_area` | `abilities/explore.py` | 从注入的前沿候选里挑最近的点，逐步移动 | 每步用位置变化确认；没有候选即完成，到上限报部分完成 |
| `farm_routine` | `abilities/routines.py` | 从现场挑一个目标，内部依次起 `select_item`/`move_to`/`use` 子委托 | 每个目标沿用 `use` 的 `expect` 前后观察判定；预算/步数/体力不足时如实报告部分完成 |
| `select_item` | `abilities/interaction.py:72` | 一次 `select` | 快照 `player.selectedSlot` 是否变化 |
| `use` | `abilities/interaction.py:119/137` | 一次 `use`，**第二次进入只核对** | `expect` 对应字段前后差分（`observation/diff.py:target_progress`）；无证据报 `unknown` |
| `interact` | 同上 | 一次 `interact` | 同地块状态变化或新菜单 |
| `choose_menu_option` | `abilities/interaction.py:189` | 一次 `menu` | 菜单关闭或内容变化，否则 `unknown` |
| `inspect_menu` | `abilities/shop.py` | 一次 `menudump`（只读） | 返回商品/组件与价格；菜单已关闭报 `unknown` |
| `buy_item` | `abilities/shop.py` | `menudump` 找序号 → `shopbuy` | 金钱减少且背包该物品增加；钱不够/没有该商品直接失败 |
| `menu_click` | `abilities/shop.py` | 一次 `menuclick` 或 `menuclickinv` | 菜单关闭、菜单签名变化或金钱变化；否则 `unknown` |
| `close_menu` | `abilities/shop.py` | 一次 `menuclose` | 重新观察菜单消失 |
| `craft_item` | `abilities/inventory.py` | 一次 `craft` | 背包里该物品数量增加；否则 `unknown` |
| `inventory_action` | `abilities/inventory.py` | `chestput` / `chestget` / `ship` / `placechest` 之一 | 背包槽位减少、物品增加或木材消耗；否则 `unknown` |
| `fish` | `abilities/fishing.py:60` | 启动/轮询独立钓鱼 Mod 的 runId | Mod 报告 `completed` 才算成功；任务被替换即失败 |
| 组合技能 | `abilities/composite.py:48` | 按步骤调用已注册能力 | 每步完成判据 + 技能总完成判据 |

`select_item` 与 `use` 默认 `exposed=False`：只作为 `farm_routine` 的内部步骤存在，
模型能力目录里看不到，`AbilityRegistry.validate_model_params` 会拒绝模型直接下发。
`expose_mechanics=true` 只用于调试对比。

`farm_routine` 的任务与边界：

| 参数/任务 | 含义 |
|---|---|
| `task=water` | 处理 `needsWater=true` 的地块；自动切到水壶，水壶没水时前提直接失败并提示 `refill` |
| `task=till` | 处理 `diggable=true` 的普通地块；自动切到锄头 |
| `task=harvest` | 处理 `harvestable=true` 的地块；用 `interact` 取走作物（**不挥动工具**，否则镰刀会毁掉作物） |
| `task=plant` | 处理已耕且无作物的地块；必须给 `seed`（名称或物品 id 模糊匹配） |
| `task=refill` | 处理 `refillable=true` 的水源格；**空壶也能补水**（只有“已满”才拒绝），补满后立即结束 |
| `task=chop` | 处理 `terrain="Tree"` 的野生树；自动切斧头（果树不列入，避免误砍资产）；一次 ≤8 |
| `task=clear` | 处理 `terrain ∈ {Weeds, Grass, Bush}` 与 `objName ∈ {Weeds, Twig}` 的杂草/灌木/嫩枝；优先镰刀，嫩枝改切斧头；一次 ≤8 |
| `task=mine` | 处理名称含 stone/node/ore/geode/quartz/石/矿/晶 的对象（石头、矿石）；自动切镐；一次 ≤8 |
| `task=auto` | 按 浇水 → 锄地 → 收获 → 装水 的顺序选第一个有目标的任务；水壶空且要浇时先 `refill`；**不含**砍树/清理/挖矿 |
| `task=plant` | 处理已耕且无作物的地块；必须给 `seed`，且背包里要有匹配种子（`entity_counts.seeds>0`） |

**照料优先（`care_first=true`）**：有作物需要浇水（水壶有水或附近可补水）或有作物可收获时，
`chop/clear/mine` 会多出一条 `user` 前提并直接阻断，提示“先照顾作物”。
破坏性任务一次最多 8 个目标，且 `remaining>0` 时不会自动续跑——每批结束后由模型重新判断，
避免“除草 76 次、种菜 0 次”。需要允许先清理时把 `care_first` 设为 `false`。

补水只改变水壶的 `water` 字段（数量和物品 id 都不变），所以 `inventory_delta`
同时比较 `water/waterCapacity/tool`；否则补水会被误判成 `unknown`。
| `max_targets` | 1～40，默认 16；达到上限时状态为 `unknown` 并报告剩余数量 |
| `max_seconds` | 5～120，默认 90；体力为 0、场景变化、步数用尽都会提前如实结束 |

被拦截的决策（限频、前提不满足、解析层拒绝：隐藏能力/参数非法）会以 `state=blocked`
写进 `recent_actions`，并附上原因，所以模型下一轮就看得到“刚才那一下根本没下发”，
不会原样重发。模型连续输出 `ability=null` 时，循环按 `poll_seconds × 2^(n-1)`
（上限 15 秒）退避，避免“没活干”时继续按 2 秒一次烧请求。

**自动续跑（省请求）**：`farm_routine` 结束时若证据里有 `remaining > 0`（达到
`max_targets`/`max_seconds`/步数上限），`loop` 直接用同一参数再下发一次，最多
`max_auto_continue=2` 次，不请求模型；记录 `action_continued`。续跑跳过限频检查
（它不是新的逻辑动作），仍受预算、停止信号与所有权约束。`task=plant` 不自动续跑
（种子有限，容易空转）。模型给出新决策后计数清零。

进度变化只写 `action_step`；控制台对重复说明去重，避免刷屏。

## 9. 结算与验证（`loop._finish`，449）

1. 若启用 `model_completion_enabled` 且决策带 `completion`：
   `predicates.evaluate_all([completion], after)`
   - `False` → 把 `completed` 改判为 `failed`（**判据只能否决成功，不能把失败/未知升级为完成**）；
   - `None`（当前感知无法验证）→ 保留能力给出的状态，并在证据里标 `met=None`；
   - 求值本身出错（类型不可比较、`in` 需要列表等）→ 同样按 `met=None` 处理，
     证据里写 `completion_predicate_error`，不会反过来判失败，也不会让整轮运行崩掉；
   - 默认关闭：模型写的 `completion` 只记进 `decision_received`，
     `action_started` 里标 `completion_ignored=true`，不参与结算。
2. 构造 `ActionResult`（`contracts/results.py`）：四态 `completed/failed/cancelled/unknown`、
   `progress`、耗时、`before_sampled_at`/`after_sampled_at`、证据（差分、位置、校验标记）；
3. `_release_lease`（498）释放动作所有权；
4. 记录 `action_finished_<state>`，payload 含完整结果与目标。

“请求已发送 / 游戏已接受 / 动作已结束 / 目标已达成”四件事不混为一谈：
接口返回 `changed` 只当证据之一，最终以重新观察为准；看不见就说 `unknown`。

## 10. 事件与学习

`Recorder.emit`（`runtime/events.py:34`）把事件同时交给两个 sink（`combine_sinks`，22）：

1. **经验库**：`make_event_sink`（`cli/wiring.py:112`）
   - `action_finished_*` → `store.record_action`（`experience/store.py:143`）写成不可变证据；
   - 能力 id 以 `skill:` 开头时，记录 `record_skill_run`（414）：
     - 成功 → `promote_skill`（492，需 3 次可区分成功 + 取消测试）；
     - 满足前提仍失败 → `demote_skill`（523）退回候选；
     - 完成判据被证伪（误报完成）→ `retire_skill`（512）立即停用。
2. **控制台**：`ConsoleReporter`（`runtime/console.py:165`）→ `format_event`（49）打印
   `目标 / 计划动作 / 开始执行 / 进度 / 成功|失败|结果未知|已取消 / 重新绑定 / 汇总`。
   打印异常会自我停用，不影响运行。

经验检索在下一轮 `store.recall` 生效：机制经验跨存档但必须已验证，存档事实按 `save_key` 隔离。

### 行动统计口径

`ExperienceStore.action_stats`（`experience/store.py`）从事件表聚合：

- `executed`：`kind='action'` 的动作数；
- `blocked`：`repeat_blocked` + `delegation_rejected` + `ownership_busy`；
- `attempts = executed + blocked`，`ratios` 以 `attempts` 为分母；
- `failure_ratio_executed = failed / executed`，单看已执行动作的失败率；
- `by_ability` 给出每个能力的同一组数字，`summary_line` 是一行中文摘要。

`start-agent.bat stats`（可加 `--days N`、`--save-key`）输出这份统计；运行结束时的
控制台汇总也打印本次拦截率/失败率（分母同样是“动作 + 拦截”）。

## 11. 停止与收尾（`loop._shutdown`，543）

触发条件（`_halt_reason`，200）：停止文件（`start-agent.bat stop`）、墙钟上限、模型请求上限、
`max_actions`（工程验收用）。收尾顺序：

1. 取消在途决策任务（`decision_cancelled`）；
2. `_cancel_active("运行结束")`：调用 `delegation.cancel`（钓鱼会通知 Mod `stop`），
   结果记为 `cancelled`，释放租约；
3. `ownership.force_release()` 兜底，确认没有残留控制权；
4. 记录 `run_stopped`（预算快照 + 限频快照）与 `run_finished`（汇总）。

停止后不会额外回家、存货或睡觉；清理过程允许必要观察，但不开启新任务。

## 12. 失败语义速查

| 控制台/事件 | 含义 | 下一步（模型或人） |
|---|---|---|
| `委托被拒绝：game：…` | 参数/前提明确不满足（例如目标不在观察范围、菜单打开） | 按提示先移动/观察或换目标；不要原样重试 |
| `拦截：…` | 限频：同一动作键窗口内重复或次数用尽 | 换目标、换能力或先汇报 |
| `丢弃过期决策：…` | 决策依据的现场已变化，未下发 | 重新决策（通常自动进行） |
| `结果未知：…` | 写操作已下发但无法确认 | 先观察核对；**不要重发购买/制造/使用** |
| `观察失败：…` | 快照请求失败 | 等待重试；检查游戏是否卡住 |
| `模型请求/解析失败：…` | 网络或 JSON 解析失败 | 检查模型配置/额度；请求已计入预算 |
| `动作所有权被占用：…` | 另一个执行器仍持有控制权 | 等待或停止旧任务 |
| `预算用尽` | 墙钟或请求数到上限 | 调整 `budget_seconds`/`budget_requests` |

## 13. 一次真实日志对照

修复前（用户日志节选）：模型每轮都拿“上一次快照”的 ref，执行时已是新快照，全部被拒。

```text
[22:23:59] 计划动作：move_to {"max_steps": 3, "near": "@s63:e196"}
[22:23:59] 委托被拒绝：game：目标 ref @s63:e196 是否在当前观察里
[22:24:02] 计划动作：move_to {"max_steps": 3, "near": "@s74:e196"}
[22:24:02] 委托被拒绝：game：目标 ref @s74:e196 是否在当前观察里
```

修复后（离线复现同一场景）：执行层用决策依据的观察把 ref 改成坐标，再下发。

```text
[22:30:38] 计划动作：move_to {"max_steps": 3, "near": "@s63:e196"}
[22:30:38] 重新绑定目标：near 旧ref→坐标(64,18)
[22:30:38] 开始执行：move_to {"max_steps": 3, "x": 64, "y": 18}（目标：给农舍南侧的作物浇水，先走到作物旁）
[22:30:38] 成功：完成
```

根因与修复细节见 `decision/binding.py` 的模块文档、`tests/test_binding.py`、
`tests/test_runtime.py::test_旧ref会在执行前改绑为坐标`。

## 14. 代码索引

| 文件 | 职责 | 关键符号 |
|---|---|---|
| `cli/main.py` | 命令行入口 | `main`, `build_parser` |
| `cli/config.py` | 预算/限频/路径/实时输出配置 | `AgentConfig`, `load_config` |
| `cli/wiring.py` | 组装与 sink | `build_runtime`, `make_observer`, `make_decider`, `build_candidates`, `make_event_sink`, `run_agent` |
| `runtime/loop.py` | 实时主循环与生命周期 | `AgentLoop.run`, `_ask`, `_dispatch`, `_step_active`, `_finish`, `_shutdown` |
| `runtime/budget.py` | 预算账本 | `RunBudget.consume_model_request`, `expire_reason` |
| `runtime/ownership.py` | 单一动作所有权 | `ActionOwnership.acquire`, `release`, `force_release` |
| `runtime/events.py` | 事件与指标 | `Recorder.emit`, `combine_sinks` |
| `runtime/console.py` | 实时控制台输出 | `format_event`, `ConsoleReporter` |
| `decision/context.py` | 上下文组装与截断 | `build_context`, `ContextLimits` |
| `decision/prompt.py` | 系统提示词 | `SYSTEM_PROMPT`, `build_messages` |
| `decision/parse.py` | 决策解析与校验 | `parse_decision` |
| `decision/predicates.py` | 受限判据 | `validate`, `evaluate`, `evaluate_all` |
| `decision/freshness.py` | 计划新鲜度 | `check` |
| `decision/binding.py` | ref 改绑 | `rebind_call` |
| `abilities/base.py` | 前提分级与委托基类 | `PremiseCheck`, `blocking_premises`, `DelegationBase` |
| `abilities/registry.py` | 能力注册与统一入口 | `AbilityRegistry.start`, `validate_params` |
| `abilities/navigation.py` | 移动 | `move_ability`, `MoveDelegation.step` |
| `abilities/interaction.py` | 选择/使用/交互/菜单 | `use_ability`, `EntityActionDelegation._verify`, `_find_target` |
| `abilities/fishing.py` | 委托独立钓鱼 Mod | `fish_ability`, `FishingDelegation.step` |
| `abilities/composite.py` | 组合技能 | `composite_ability`, `CompositeDelegation.step` |
| `observation/state.py` | 快照 → 观察 | `from_snapshot`, `observed_tiles` |
| `observation/diff.py` | 前后状态比较 | `target_progress`, `target_state` |
| `exploration/candidates.py` | 前沿/交互候选 | `candidates`, `frontier_candidates` |
| `repeat_guard/guard.py` | 限频 | `RepeatGuard.check`, `record`, `reset` |
| `model_client/client.py` | 模型请求 | `ChatClient.complete`, `extract_json` |
| `experience/store.py` | 证据/经验/技能/地图索引 | `record_action`, `recall`, `promote_skill` |
| `contracts/*` | 数据记录、错误与版本 | `Observation`, `Decision`, `ActionResult`, `Delegation` |

## 15. 改动指南

| 想改什么 | 改哪里 | 必须同步 |
|---|---|---|
| 提示词措辞/输出格式 | `decision/prompt.py` | `docs/interfaces.md`；检查 `provided_knowledge` 是否仍准确 |
| 上下文预算/截断 | `decision/context.py:ContextLimits` | 确认截断仍会报告给模型 |
| 新增能力 | `abilities/<模块>.py` + `wiring.build_registry` | 参数校验、前提、完成验证、`tests/test_abilities.py`、接口与迁移文档 |
| 改 ref 改绑策略 | `decision/binding.py` | `tests/test_binding.py`、`docs/interfaces.md` |
| 改限频/预算语义 | `repeat_guard/guard.py`、`runtime/budget.py`、`cli/config.py` | 配置示例与验收口径 |
| 新增控制台提示 | `runtime/console.format_event` | `tests/test_console.py` |
| 改完成判据语义 | `runtime/loop._finish`、`abilities/*._verify` | 说明“判据只能否决成功”仍成立 |

行号会随改动漂移；请用上表的函数名定位。

## 16. 这份文档不证明什么

- 不证明已在真实存档中验证过上述行为；本轮只做离线测试与替身运行；
- 不证明学习有效；那需要 `evals/` 的保留/禁用经验配对试验；
- 不教授任何攻略、作物优先级或固定路线——文档描述的是机制，不是玩法答案。



