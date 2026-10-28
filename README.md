# 星露谷学习型机器人（重构版）

让模型（任意 OpenAI 兼容聊天接口：DeepSeek、OpenAI、OpenRouter、Ollama/LM Studio、vLLM 等）自主决定“做什么、用什么、去哪”，本地执行器只做有界、
可取消的委托；系统记录真实结果，把经验与可验证的动作组合沉淀下来，重启后仍可复用。

本项目按 `docs_origin/重构需求.md` 的基线重构自 `stardew_valley_dfy`，只复用交接文档
列出的九项白名单模块，不复用旧主循环、旧规划调度与固定农务策略。

## 当前状态

| 项目 | 状态 |
|---|---|
| 两个 C# Mod | `game_bridge_mod` 已改（A* 寻路 + 出口/门切图）并编译安装为 0.1.3；`fishing_mod` 仍为原样、未重新编译 |
| 九项白名单模块 | 已提取为独立包，去掉旧 `MemoryStore` / `WorkPlanner` 接线 |
| 新模块（契约、运行时、决策、经验、能力、CLI） | 已实现，含离线测试 |
| 离线测试 | `pytest` 全绿（不依赖游戏、模型账户或桌面） |
| 实机行为验收 | **未执行**。农务、背包受限探索、条件变化纠错都需要真实存档与预算，尚未验证 |
| 学习效果对照实验 | **未执行**，场景与指标格式见 `evals/` |

任何“已学会种地/钓鱼”的说法都需要先完成 `evals/` 里的保留/禁用经验配对试验。

## 能做什么（模型可见能力）

| 能力 | 用途 | 说明 |
|---|---|---|
| `move_to` | 走到坐标或现场对象旁 | A*（mod 内，4 邻接 + 曼哈顿启发）；卡住会重算路径/等 NPC 让路；坐标 `-1` 表示该轴保持当前位置；出口/门要真的切图才算到达 |
| `explore_area` | 走向未探索前沿 | 本地挑选最近的前沿候选，最多 20 步/180 秒 |
| `farm_routine` | 批量农活与机械操作 | `task=water/till/harvest/plant/refill/chop/clear/mine/auto`；默认一批 16 个目标、剩余自动续跑；**有作物要照料时不做 chop/clear/mine**，这三类一次 ≤8 且不自动续跑 |
| `interact` | 与对象/NPC/门/箱子交互 | 打开商店、对话、箱子等 |
| `choose_menu_option` | 对话选项 | 只能点当前菜单里的 ref |
| `inspect_menu` | 读取菜单 | 商店商品（序号/名称/价格）与可点组件坐标 |
| `buy_item` | 买种子/物品 | 价格与库存来自游戏 `menudump`；校验钱、单价上限、背包变化 |
| `menu_click` | 点击菜单坐标 | 普通菜单或背包区域；坐标来自 `inspect_menu` |
| `close_menu` | 关闭菜单 | 商店/对话/背包通用 |
| `craft_item` | 按配方制作 | 材料由游戏扣除；验证背包该物品增加 |
| `inventory_action` | 箱子/发货/放箱子 | `op=chest_put/chest_get/ship/place_chest`；按背包变化验证 |
| `fish` | 委托独立钓鱼 Mod | 需要已安装 `StandaloneFishingSkill` |
| 组合技能 | 已验证的步骤序列 | 由学习模块晋级后注册 |

买种子不是单独能力：先 `interact` 打开商店 → `inspect_menu` 看价格 → `buy_item` 购买 →
`close_menu`，再 `farm_routine {task:"plant", seed:"…"}` 播种。



## 目录

```text
mods/
  game_bridge_mod/       # 白名单 1：AgentStardew 游戏桥（C# / SMAPI）
  fishing_mod/           # 白名单 2：独立钓鱼 Mod（C# / SMAPI）
src/stardew_agent/
  contracts/             # 观察/决策/结果/经验/技能/错误与版本
  bridge_client/         # 白名单 3：游戏桥 WebSocket 客户端
  fishing_client/        # 白名单 4：钓鱼邮箱客户端
  model_client/          # 白名单 5：模型请求与 JSON 解析
  embeddings/            # 白名单 6：本地 ONNX 向量
  exploration/           # 白名单 7：格网、寻路、前沿与交互候选
  observation/           # 白名单 8：存档/目标身份、状态比较
  repeat_guard/          # 白名单 9：动作限频
  runtime/               # 预算、单一动作所有权、停止、事件、实时循环
  decision/              # 上下文、提示词、受限判据、回复校验、计划新鲜度
  experience/            # 证据库、检索、经验状态、技能版本与晋级
  abilities/             # 各能力的描述、参数检查、执行与结果验证
  cli/                   # 配置、依赖组装、run/status/stop/doctor/export
tests/                   # 模块测试；integration/ 跨模块契约场景
evals/                   # 脱敏验收场景、配对试验协议与报告格式
docs/                    # 架构、接口、迁移台账、贡献说明
docs_origin/             # 重构前的需求与交接原文（只读参考）
```

## 快速开始

### 0. 先启动游戏：必须经过 SMAPI

Steam 的"开始游戏"不会加载 Mod。双击 `start-game.bat`：

- 先定位游戏目录：环境变量 `STARDEW_GAME_PATH` → `config.json` 的 `game_path` → 默认 Steam 路径；
- 若 `Stardew Valley.exe`（原版）正在运行，会拒绝启动并提示先完全退出；
- 若 SMAPI 已在运行，则不会重复启动；
- 启动 `StardewModdingAPI.exe` 后最多等 90 秒检测桥端口 `17654`，就绪后提示进入专用存档；
- 不会自动运行机器人：先确认存档已载入，再运行 `start-agent.bat`。

可选参数：`start-game.bat --no-wait` 跳过等待；`DRY_RUN=1` 只打印将要执行的命令、
不启动游戏（排查路径解析用）。

### 方式一：Windows 双击 `start-agent.bat`（推荐）

前置条件：游戏已启动、已载入专用测试存档，且 `Mods\AgentStardew` 已安装。

| 命令 | 作用 |
|---|---|
| `start-agent.bat` | 启动一次实时运行（等同 `run`） |
| `start-agent.bat stop` | 请求停止：下一次循环检查时退出并释放动作控制 |
| `start-agent.bat status` | 查看上次运行、经验库与技能版本 |
| `start-agent.bat doctor` | 只读自检：模型配置、游戏桥、钓鱼 Mod、向量模型、存储路径 |
| `start-agent.bat export` | 导出脱敏证据到 `data\evidence.json` |
| `start-agent.bat stats` | 统计历史行动的拦截/失败占比（可加 `--days 7`、`--save-key`） |
| `start-agent.bat skills` | 列出组合技能版本（可加 `list` / `retire`） |
| `start-agent.bat help` | 用法 |

额外参数会原样传给 CLI，例如限制这次只跑 120 秒、最多 10 次模型请求：

```powershell
start-agent.bat run --seconds 120 --requests 10
start-agent.bat doctor --no-probe
start-agent.bat skills list
```

### 运行窗口会实时显示什么

运行期间同一窗口会打印模型目标、动作与真实结果（离线替身运行的示例，真实游戏会慢很多）：

```text
[22:20:41] 启动：预算 15.0 秒 / 8 次模型请求
[22:20:41] 目标：走到耕地旁边
[22:20:41] 计划动作：move_to {"max_steps": 3, "x": 12, "y": 10}
[22:20:41] 开始执行：move_to {"max_steps": 3, "x": 12, "y": 10}（目标：走到耕地旁边）
[22:20:41]   … 已下发移动请求，等待下一次观察确认位置
[22:20:41] 成功：已到达目标地块或相邻格
[22:20:41] 目标：锄这块地
[22:20:41] 计划动作：use {"expect": "till", "target": "@snap:e1"}
[22:20:41] 开始执行：use {"expect": "till", "target": "@snap:e1"}（目标：锄这块地）
[22:20:42] 成功：观察证明 till 已发生
[22:20:42] 汇总：动作 2（成功 2 / 失败 0 / 未知 0 / 取消 0 / 拦截 0），用时 0.2 秒，停止原因 max_actions
```

- `目标` / `计划动作`：模型这一轮的决定；每次委托结算后才请求下一次决策，
  所以通常按“目标 → 执行 → 结果”顺序出现；
- `开始执行`：已拿到动作所有权、真正下发前的确认，括号里是它服务的目标；
- `成功` / `失败` / `结果未知` / `已取消` / `拦截` / `丢弃过期决策`：真实结果，
  `结果未知` 会提醒先观察核对、不要重发同一写操作；
- 动作进行中只在阶段变化时打印一行 `…`，不会刷屏；
- 不需要额外日志窗口：完整事件同时写进 `data/experience.sqlite3`，可用
  `start-agent.bat export` 导出。

不想看实时输出时加 `--quiet`（或在 `config.json` 里设 `"console_output": false`）：

```powershell
start-agent.bat run --quiet
```

### 行动统计：拦截 / 失败占比

`start-agent.bat stats` 从经验库聚合**全部历史行动**（只读，不启动游戏）。可加
`--days 7` 只看最近 7 天，用 `--save-key save:xxx` 只看某个存档。

```powershell
start-agent.bat stats
start-agent.bat stats --days 7
```

输出字段与口径：

| 字段 | 含义 |
|---|---|
| `attempts` | 行动尝试总数 = `executed + blocked` |
| `executed` | 真正下发并结算的动作数 |
| `blocked` | 未下发就被挡下：限频（`blocked_repeat`）、前提拒绝（`blocked_premise`）、所有权占用（`blocked_ownership`） |
| `completed/failed/unknown/cancelled` | 已执行动作的四种结果 |
| `ratios` | 各类占 `attempts` 的比例（0～1） |
| `failure_ratio_executed` | `failed / executed`，单看已执行动作的失败率 |
| `by_ability` | 按能力分组的同一组数字 |
| `summary_line` | 一行中文摘要，便于直接看 |

本机历史示例（加白名单之前的数据）：

```text
行动 454 次：成功 146 (32.2%)、失败 48 (10.6%)、未知 119 (26.2%)、取消 3 (0.7%)、拦截 138 (30.4%)
move_to     180 次：失败 25.6%、拦截 47.2%
use         173 次：失败 1.2%、未知 65.3%
select_item 101 次：拦截 51.5%
```

`select_item` 的拦截是白名单生效前的历史记录；新运行不会再产生这类拦截。运行结束时
控制台也会打印本次的拦截率/失败率（分母同样是“动作 + 拦截”）。

脚本行为：

- 首次运行会从 `config.example.json` 生成 `config.json`（预算与限频按需修改）；
- 缺少 `model.json` 时会生成模板并打开记事本，填入任意 OpenAI 兼容服务的 Key 后重新运行；
- 优先使用项目内 `.venv\Scripts\python.exe`，否则用 PATH 里的 `python`；
- 无需 `pip install`：脚本把 `src` 加入 `PYTHONPATH`；
- 控制台代码页设为 UTF-8，Python 的中文输出正常显示；脚本本身是纯 ASCII（cmd 无法可靠解析
  UTF-8 批处理里的中文）；
- 结束时会暂停，设置环境变量 `NO_PAUSE=1` 可关闭（便于脚本/计划任务调用）。

### 方式二：命令行

```powershell
# 安装（Python 3.10+，推荐 3.12）
python -m pip install -e ".[dev]"

# 模块检查：不需要游戏与模型账户
python -m pytest

# 配置（预算与限频必须显式填写，没有隐式默认值）
Copy-Item config.example.json config.json
Copy-Item model.example.json model.json   # 填入任意 OpenAI 兼容服务的 Key 与端点

# 只读自检：模型配置、游戏桥、钓鱼 Mod、向量模型与存储路径
python -m stardew_agent doctor --config config.json

# 实机运行（需要已加载 AgentStardew 的存档）
python -m stardew_agent run --config config.json

# 另开一个终端停止
python -m stardew_agent stop --config config.json
```

也可以安装后使用控制台命令 `stardew-agent`。`skills` 子命令的全局参数要写在动作之前：

```powershell
stardew-agent skills --config config.json list
stardew-agent skills --config config.json retire --id skill:xxx --reason "前提失效"
```

## 配置

`config.example.json` 是完整示例。硬性要求：

- `budget_seconds` 与 `budget_requests` 决定本次运行上限；达到任一上限即停止下发新动作、
  取消在途委托并释放控制，不会额外回家或睡觉。
- `model_request_interval_seconds` 限制两次模型请求之间的**最小间隔**（秒，0 表示不限制）：
  到点前循环继续观察游戏并等待，不会丢请求；可用
  `start-agent.bat run --model-interval 2` 临时覆盖。
- `repeat_window_seconds` 与 `repeat_max_runs` 是动作限频参数，代码不内置业务默认值。
- `repeat_exempt_abilities` 是限频白名单（能力 id 数组，示例默认 `["select_item"]`）：
  其中的能力不参与限频检查与计数，比如反复切换手持槽位不会被当成“重复动作”。
  它只跳过限频；参数、前提、所有权、预算校验照常执行。可用
  `start-agent.bat run --repeat-exempt select_item,choose_menu_option` 临时覆盖。
- `model_completion_enabled` 决定是否采用模型自己写的 `completion` 判据：
  默认 `false`，提示词里不出现该字段，成败只由能力的前后观察验证决定；
  设为 `true`（或 `start-agent.bat run --model-completion`）才启用，
  并且判据只能否决成功，不能把失败或未知升级为完成。
- `expose_mechanics` 默认 `false`：`use` / `select_item` 是 `farm_routine` 的内部动作，
  不出现在模型能力目录里，模型写了也会被 `parse_decision` 拒绝；
  `start-agent.bat run --expose-mechanics` 仅供调试或与旧行为对比。
- `care_first` 默认 `true`：有作物需要浇水或可收获时，`chop/clear/mine` 会被
  `user` 前提直接拦下（“先照顾作物”）；关掉它才会允许“边上有活先除草”。
  破坏性任务本身还有一条硬限制：一次最多 8 个目标，且剩余目标不会自动续跑。
- `max_notable_entities` 控制每次请求最多列出多少个非地块实体（默认 24，1～120）。
  地块压成 `observation.tiles` 通行网格，实体只保留非默认字段，另给
  `observation.entity_counts`（可锄/需浇/可收/可装水数量）。实测真实农场（半径 8）：
  默认压缩约 13.1k 字符（≈4–5k tokens），旧的全量视图约 30.7k 字符（≈8.6–10.6k tokens）。
  另外 `farm_routine` 默认一批做 16 个目标（`max_targets`），剩余目标会在本地自动续跑
  （最多 2 次），因此同一片农田的模型请求数显著减少。可用
  `start-agent.bat run --max-notable-entities 16` 继续压缩。
- `model.json` 支持 `api_key` / `apiKey`，占位符（`your-key` 等）会被拒绝。

密钥不会进入日志、状态文件或证据导出。

## 模型接入（任意 OpenAI 兼容）

`model.json` 只需要 `api_key`、`api_base`（或 `base_url`）与 `model`，任何 OpenAI 兼容的
`/chat/completions` 端点都能用：

| 服务 | `api_base` | 备注 |
|---|---|---|
| DeepSeek | `https://api.deepseek.com` | 默认示例 |
| OpenAI | `https://api.openai.com/v1` | 自动补 `/chat/completions` |
| OpenRouter | `https://openrouter.ai/api/v1` | 可加 `headers: {"HTTP-Referer": "...", "X-Title": "..."}` |
| Ollama | `http://127.0.0.1:11434/v1` | 本地端点不需要 key |
| LM Studio | `http://127.0.0.1:1234/v1` | 本地端点不需要 key |
| vLLM / 自建 | `http://<host>:<port>/v1` | 需要时用 `auth` |
| Azure OpenAI | `https://<res>.openai.azure.com/openai/deployments/<name>/chat/completions?api-version=...` | `auth: "header"` + `api_key_header: "api-key"` |

可选字段：

- `base_url`：与 `api_base` 等价，允许只写根地址（自动补 `/chat/completions`）；
- `auth`：`bearer`（默认）/ `header` / `none`；本地端点没有 key 时自动不发送认证头；
- `api_key_header`：`auth: "header"` 时的头名（Azure 用 `api-key`）；
- `headers`：额外请求头（值不进日志/状态，`public()` 只列名字）；
- `extra_body`：额外请求体字段（`top_p`、`seed`、`enable_thinking`…，最后合并，可覆盖默认值）；
- `json_mode`：是否发送 `response_format={"type":"json_object"}`，默认 `true`；端点不支持就设 `false`；
- `max_tokens_param`：生成上限字段名，默认 `max_tokens`；OpenAI 新推理模型用 `max_completion_tokens`；
  有些思考型模型可以用 `extra_body` 关闭思考（例如 `{"thinking": {"type": "disabled"}}`），
  能显著降低延迟与 completion tokens——是否支持取决于端点。

也可以完全用环境变量（不需要 `model.json`）：

```powershell
$env:OPENAI_API_KEY = "..."
$env:OPENAI_BASE_URL = "http://127.0.0.1:11434/v1"
$env:OPENAI_MODEL = "qwen2.5:14b"
```

`doctor` 只打印端点、模型名与请求头名字，不打印密钥；回复内容为分段数组、缺少 `usage`、
HTTP 错误带供应商原因等常见兼容差异都已处理。

## 决策全过程文档

观察 → 上下文 → 模型 → 校验（新鲜度/ref 改绑/限频/前提/所有权/预算）→ 执行 → 验证 → 事件，
每一步对应的代码位置、失败语义与一次真实日志对照，见
[docs/decision-flow.md](docs/decision-flow.md)。

## 设计边界（务必先读）

1. **模型决定，执行器受限。** 能力目录里只有移动、交互、菜单选项、农活例行程序
   （`farm_routine`）、委托钓鱼和已验证组合技能。逐格浇水/锄地/收获/播种/装水的底层动作
   （`use`、`select_item`）只给例行程序内部使用，模型不能直接下发；执行器不会自己选作物、
   卖货、睡觉或换目的地。
2. **机械重复不下放给模型。** `farm_routine` 只接受任务与上限（`task` / `max_targets` /
   `max_seconds`，播种还要 `seed`），自己选工具、走近目标、逐格执行并用前后观察验证；
   支持浇水、锄地、收获、播种、装水，以及砍树、清理杂草/嫩枝、挖矿石。
   `auto` 只做照料类农活，砍树/清理/挖矿必须显式指定，避免模型顺手清空农场。
   每次委托有目标数/时长/步数上限，取消立即停止。
3. **必须实时。** 推理期间游戏继续运行；没有可执行委托时真实等待并记录原因，不为了
   保持忙碌擅自开新任务。分段执行之间有可配置的间隔，避免空转。
4. **结果四态分开。** 请求已发送 / 游戏已接受 / 动作已结束 / 目标已达成不混为一谈；
   写操作超时是“结果未知”，必须先观察核对，不能无条件重发。
5. **单一动作所有权。** 游戏桥与钓鱼 Mod 共享同一份所有权租约，同一时刻只有一个执行器
   能操纵角色。
6. **不注入攻略。** 提示词只说明接口与诚实报告规则；上下文里的 `provided_knowledge`
   列出项目主动提供的信息（能力目录、字段含义、长期目标文字）。
7. **未知不等于禁止。** 观察字段缺失、被截断或桥不支持时保持 `unknown`/`unsupported`，
   例如当前快照没有玩家生命值和敌人状态，挖矿/战斗能力就是明确缺口。
8. **证据与规律分开。** 前后观察差分只证明“发生了变化”；普遍机制结论需要支持证据、反例、
   适用条件与验证状态。组合技能晋级需要三次可区分的真实成功 + 取消测试通过。
9. **存档隔离。** 地图、库存、关系等事实按 `save_key` 隔离；验证过的机制经验可跨存档，
   但调用前仍检查前提。
10. **模型能看到最近动作结果。** 上下文包含最近 8 条动作（状态、原因、多少秒前）与失败摘要；
    刚重启时从证据库补读，不会“忘记刚才失败了什么”。委托结算后才请求下一次决策，
    避免模型拿旧结果去重复同一个失败动作。

## 修改记录

每次改动都要在 `修改记录.md` 追加一条记录（修改文件 / 修改逻辑 / 验证），回复总结里也要
列出同样的两项。提交前检查清单与记录模板见 `docs/contributing.md` 的
“修改记录（提交前必做）”。

## 与旧项目的关系

迁移台账见 `docs/migration.md`：每项都注明来源文件、保留的公开函数、做出的改动与测试。
被排除的旧实现（`farm_service.py`、`farmer.py`、`work_planner.py`、`agent_skills.py`、
`MemoryStore` 整体、固定农务/路线规则等）不在新系统中，也不作为隐式依赖。

## 许可证

MIT，见 `LICENSE`。两个 Mod 来自上游项目，各自的来源与版本记录在
`mods/*/ORIGIN.md`，保留原有许可与归属。
