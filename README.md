# 星露谷学习型机器人（重构版）

让模型（DeepSeek 兼容聊天接口）自主决定“做什么、用什么、去哪”，本地执行器只做有界、
可取消的委托；系统记录真实结果，把经验与可验证的动作组合沉淀下来，重启后仍可复用。

本项目按 `docs_origin/重构需求.md` 的基线重构自 `stardew_valley_dfy`，只复用交接文档
列出的九项白名单模块，不复用旧主循环、旧规划调度与固定农务策略。

## 当前状态

| 项目 | 状态 |
|---|---|
| 两个 C# Mod | 源码已迁入 `mods/`（原样保留），**未重新编译、未实机加载** |
| 九项白名单模块 | 已提取为独立包，去掉旧 `MemoryStore` / `WorkPlanner` 接线 |
| 新模块（契约、运行时、决策、经验、能力、CLI） | 已实现，含离线测试 |
| 离线测试 | `pytest` 全绿（不依赖游戏、模型账户或桌面） |
| 实机行为验收 | **未执行**。农务、背包受限探索、条件变化纠错都需要真实存档与预算，尚未验证 |
| 学习效果对照实验 | **未执行**，场景与指标格式见 `evals/` |

任何“已学会种地/钓鱼”的说法都需要先完成 `evals/` 里的保留/禁用经验配对试验。

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

- `目标` / `计划动作`：模型这一轮的决定；推理可与当前动作并行，所以可能交错出现；
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

脚本行为：

- 首次运行会从 `config.example.json` 生成 `config.json`（预算与限频按需修改）；
- 缺少 `model.json` 时会生成模板并打开记事本，填入 `sk-` 开头的 DeepSeek Key 后重新运行；
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
Copy-Item model.example.json model.json   # 填入 sk- 开头的 DeepSeek API Key

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
- `repeat_window_seconds` 与 `repeat_max_runs` 是动作限频参数，代码不内置业务默认值。
- `model.json` 支持 `api_key` / `apiKey`，占位符（`your-key` 等）会被拒绝。

密钥不会进入日志、状态文件或证据导出。

## 决策全过程文档

观察 → 上下文 → 模型 → 校验（新鲜度/ref 改绑/限频/前提/所有权/预算）→ 执行 → 验证 → 事件，
每一步对应的代码位置、失败语义与一次真实日志对照，见
[docs/decision-flow.md](docs/decision-flow.md)。

## 设计边界（务必先读）

1. **模型决定，执行器受限。** 能力目录里只有移动、选择物品、使用工具、交互、菜单选项、
   委托钓鱼和已验证组合技能。执行器不会自己选作物、卖货、睡觉或换目的地。
2. **必须实时。** 推理期间游戏继续运行；没有可执行委托时真实等待并记录原因，不为了
   保持忙碌擅自开新任务。分段执行之间有可配置的间隔，避免空转。
3. **结果四态分开。** 请求已发送 / 游戏已接受 / 动作已结束 / 目标已达成不混为一谈；
   写操作超时是“结果未知”，必须先观察核对，不能无条件重发。
4. **单一动作所有权。** 游戏桥与钓鱼 Mod 共享同一份所有权租约，同一时刻只有一个执行器
   能操纵角色。
5. **不注入攻略。** 提示词只说明接口与诚实报告规则；上下文里的 `provided_knowledge`
   列出项目主动提供的信息（能力目录、字段含义、长期目标文字）。
6. **未知不等于禁止。** 观察字段缺失、被截断或桥不支持时保持 `unknown`/`unsupported`，
   例如当前快照没有玩家生命值和敌人状态，挖矿/战斗能力就是明确缺口。
7. **证据与规律分开。** 前后观察差分只证明“发生了变化”；普遍机制结论需要支持证据、反例、
   适用条件与验证状态。组合技能晋级需要三次可区分的真实成功 + 取消测试通过。
8. **存档隔离。** 地图、库存、关系等事实按 `save_key` 隔离；验证过的机制经验可跨存档，
   但调用前仍检查前提。

## 与旧项目的关系

迁移台账见 `docs/migration.md`：每项都注明来源文件、保留的公开函数、做出的改动与测试。
被排除的旧实现（`farm_service.py`、`farmer.py`、`work_planner.py`、`agent_skills.py`、
`MemoryStore` 整体、固定农务/路线规则等）不在新系统中，也不作为隐式依赖。

## 许可证

MIT，见 `LICENSE`。两个 Mod 来自上游项目，各自的来源与版本记录在
`mods/*/ORIGIN.md`，保留原有许可与归属。
