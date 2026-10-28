# 迁移台账

来源仓库：`C:\Users\34861\Desktop\ideas\stardew_valley_dfy`（HANDOFF 记录 HEAD
`d6c276376b191efcd960cfa66c941011c2cf8aa9`）。本文件记录每一项白名单模块的迁入位置、
保留内容、做出的改动与对应测试。九项白名单之外没有迁入任何旧业务代码。

## 一、两个 Mod（整体保留）

| 白名单 | 来源 | 去向 | 改动 |
|---|---|---|---|
| 1 游戏桥 | `mod/agent-stardew/mods/AgentStardew/`（13 个文件，manifest 0.1.1） | `mods/game_bridge_mod/` | 原样复制，排除 `bin/obj`；新增 `ORIGIN.md` |
| 2 钓鱼 Mod | `skills/stardew-fishing/mod/`（5 个文件，manifest 0.4.0） | `mods/fishing_mod/` | 原样复制，排除 `bin/obj`；新增 `ORIGIN.md` |

未重新编译、未安装、未在游戏内加载；本轮不声称构建或运行通过。

## 二、Python 白名单模块

### 3. 游戏桥客户端 → `src/stardew_agent/bridge_client/`

- 保留：连接/关闭、hello 握手、带请求 ID 的 JSON 请求、超时与协议错误、核心写命令动作 ID、
  快照/状态/移动/选择/使用/交互/菜单/配方/制作薄封装。
- 改动：
  1. 去掉 import 时读取环境与 CLI `main()`；
  2. 一条连接同一时刻只允许一个在途请求（并发 → `OwnershipBusy`），替代旧实现“跳过不匹配响应”；
  3. 不匹配响应改为 `ProtocolError`；
  4. 写命令超时/断连 → `ResultUnknown`，读命令 → `TransportError`；
  5. 传输层可注入（`Transport` 协议），便于离线测试；
  6. 新增 `stop(action_id)` 与可配置的超时宽限。
- 测试：`tests/test_bridge_client.py`（8 项）。

### 4. 钓鱼通信客户端 → `src/stardew_agent/fishing_client/`

- 保留：`read_status`、`submit_command`、`send`、`wait_run`、`validate_limits` 的通信能力。
- 改动：去掉 CLI 入口与“检查旧 `farm_service.py` 进程”的规则；错误类型改为
  `FishingError(AgentError, ValueError)`；`validate_limits` 额外要求所有预算字段齐备并校验
  `facing`；新增 `is_installed` / `doctor` 只读自检。
- 测试：`tests/test_fishing_client.py`（7 项）。

### 5. 模型请求与 JSON 解析 → `src/stardew_agent/model_client/`

- 保留：配置读取（`_cfg`/`_pick_key` 语义）、消息编码、HTTP 请求与超时、回复 JSON 提取。
- 改动：拆成 `settings.py` + `client.py`；删除旧 `WORK_SYSTEM`、旧计划字段、后台规划生命周期；
  传输可注入；异常只报类型，不打印请求/响应全文；`ModelReply` 记录用量与耗时；
  没有可用配置时返回 `None` 或报配置错误，**不会退回固定日程**。
- 测试：`tests/test_model_client.py`（8 项，含参数化）。

### 6. 本地向量模块 → `src/stardew_agent/embeddings/`

- 保留：模型目录检查、ONNX + tokenizer 加载、CPU 批量编码为 L2 归一化向量、余弦相似度、
  blob 互转、不可用状态。
- 改动：改为 `Encoder` 类（资源生命周期明确）；模型目录环境变量 `STARDEW_EMBED_DIR`
  （兼容旧 `FARM_EMBED_DIR`）；返回普通列表而不是 numpy 数组，避免类型泄漏；
  去掉 `__main__` 演示。
- 测试：`tests/test_embeddings.py`（4 项）。

### 7. 探索与寻路 → `src/stardew_agent/exploration/`

- 保留：四邻域 BFS 与前驱还原、可达集合、前沿候选（未知邻格/访问记录/距离/间距）、
  交互候选、地区连接链。
- 改动：不再接收 `MemoryStore`；改为显式输入 `cells / observed / known / visited / scope /
  entities`；`grid` 拆成 `build_grid`（本次观察优先于历史）；候选集合显式返回
  `truncated / considered / order / more`；`path_to` 不再自己寻找 memory。
- 测试：`tests/test_exploration.py`（9 项）。

### 8. 快照身份与状态比较 → `src/stardew_agent/observation/`

- 保留：`dumps`、`target_id`、`semantic`、`save_key`、`position`、`scene`、
  `at_tile`、`target_state`、`target_progress`。
- 改动：只保留纯函数，不带 `MemoryStore` / `ActionHistory` 类；新增 `from_snapshot`
  统一处理“实体在顶层还是 `observation.entities` 下”的旧不一致，并记录 `unknowns`。
- 测试：`tests/test_observation.py`（9 项）。

### 9. 动作限频 → `src/stardew_agent/repeat_guard/`

- 保留：按动作键记录次数与最近时刻、窗口内拦截、次数用尽拦截、清零、缓存上限。
- 改动：参数改为必填 `RepeatPolicy(window_seconds, max_runs)`，不内置业务默认值；
  去掉 `WorkPlanner` 的计数/续跑/跨日接线；返回 `GuardDecision` 而不是三元组。
- 测试：`tests/test_repeat_guard.py`（8 项）。

## 三、新写模块

| 模块 | 职责 | 测试 |
|---|---|---|
| `contracts` | 观察/决策/结果/经验/技能记录、错误、版本 | `tests/test_contracts.py` |
| `runtime` | 预算、动作所有权、停止、事件与指标、实时循环 | `tests/test_runtime.py` |
| `decision` | 上下文、提示词、受限判据、解析校验、新鲜度 | `tests/test_decision.py` |
| `experience` | SQLite 证据库、检索、状态、技能版本与晋级、地图索引 | `tests/test_experience.py` |
| `abilities` | 移动/选择/使用/交互/菜单/钓鱼/组合技能 | `tests/test_abilities.py` |
| `cli` | 配置、依赖组装、run/status/stop/doctor/export/skills | `tests/test_cli.py`、`tests/integration/test_end_to_end.py` |

## 四、明确排除的旧实现

以下文件/类**没有**进入新项目，新系统也不以它们为隐式依赖：

- `bot/farm_service.py`：旧常驻主循环与编排；
- `bot/farmer.py`：混合农务、路径、菜单、窗口输入与业务规则的大文件；
- `bot/work_planner.py`：旧后台规划、短队列、mission 与重试生命周期；
- `bot/agent_skills.py`：技能派发、农务圈、记忆、菜单与结果管理的总执行器；
- `bot/memory_store.py` 的 `MemoryStore` 整体：证据、检索、地标、事实、任务板、
  作物台账与每日排程混在一起；
- `bot/observed_bridge.py`：与旧记忆上下文、过图目标与调用记录相互接线；
- `bot/skill_catalog.py` 的旧能力可用性/农务前提策略、旧系统提示词中的规划约定；
- `bot/farm_layout.py`、`places.py`、`shop.py`、`shop_hunt.py`、`go_*.py` 等固定地点与路线；
- 旧服务启停、自动进存档、收发箱汇报、自动记忆维护脚本；
- `skills/stardew-fishing/scripts/fish.py` 的 CLI 与进程检查部分（只保留通信函数）。

旧数据库（`bot/memory.sqlite3`）未迁移、未读取。来源未知的旧记录不能自动灌入新实验。

## 五、行为差异（迁移时必须知道）

| 旧行为 | 新行为 |
|---|---|
| 桥客户端跳过不匹配响应，可能拿错结果 | 不匹配即 `ProtocolError`；并发即 `OwnershipBusy` |
| 写超时后可能重发购买/制造 | `ResultUnknown` → 先观察核对 |
| `use` 成功后按固定动作名判进展 | `expect` 参数选择判据；无证据报 `unknown` |
| 限频次数固定 8 秒 / 5 次且绑定 WorkPlanner | 参数来自配置；只有新委托才计数 |
| 限频没有白名单，所有动作一律受限 | `repeat_exempt_abilities` 按能力 id 豁免（示例默认 `select_item`），只跳过限频 |
| 旧 ref 跨快照直接执行，找不到就整体拒绝 | 执行前按决策时的观察改绑：移动退化为坐标，交互换当前 ref |
| 记忆检索混用存档与通用教训 | `scope='save'` 隔离；机制经验需 `verified` |
| 反思整理可直接把教训升级为 observed | 状态只能显式变更；晋级有证据门槛 |
| 农务执行器“连种三格自动浇水” | 不存在隐藏连招；一切按委托执行 |

## 六、本轮未做

- 未编译/安装两个 Mod，未启动游戏，未调用付费模型，未连接真实存档；
- 未迁移旧数据，未做学习效果对照实验（`evals/` 只有协议与格式）；
- 未实现购买/制造/箱子等扩展写操作的能力封装（协议保障未定义）；
- 未实现联机、多机器人、GUI 与其他平台。

