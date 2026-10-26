# 星露谷农场机器人

让模型自己决定今天怎么种地，然后由代码去执行。

**分工**：模型在后台生成当日参数和短期动作计划；执行器在等待模型时继续处理当前可执行农务。
所有具体动作（走路、砍、种、浇、收、卖、睡觉）都由唯一执行器调游戏接口完成。

## 组成

```
bot/
  farm_service.py   常驻服务：白天干活、晚上睡觉、接指令（inbox）、写报告（outbox）
  planner.py        问模型要当日计划（JSON）
  work_planner.py   后台规划、短期队列、过时结果校验、失败退避
  farmer.py         底层动作：走位/清障/翻地/播种/浇水/收获/卖货
  farm_client.py    SMAPI mod 的子进程桥（WebSocket + JSON-RPC）
  launch_farm.py    开游戏 → 进存档 → 把窗口铺满
  shop.py          找商店、买种子
  farm-doctor.py    环境体检（窗口/端口/进程/日志一次打全）
docs/               调研笔记（MCP 桥、协议、同类项目）
```

## 依赖

| 依赖 | 地址 | 说明 |
|---|---|---|
| 星露谷 1.6+ | Steam / GOG | 游戏本体 |
| **SMAPI** | https://github.com/Pathoschild/SMAPI | mod 加载器，4.x |
| **agent-stardew** | https://github.com/rottenpen/agent-stardew | **必需**。它的 `mods/AgentStardew` 提供 `ws://127.0.0.1:17654` 桥，本仓库通过 `farm_client.py` 连它 |
| Python 3.12 | https://www.python.org | `pip install -r requirements.txt` |
| 模型接口 | 默认 DeepSeek | 任何兼容 OpenAI 协议的都行 |

`agent-stardew` 是 MIT 许可（`Copyright (c) 2026 agent-stardew contributors`），
**源码不随本仓库分发**，请自行 clone 后编译 mod，把 `AgentStardew.dll` 放进
`Mods/AgentStardew/`。

本仓库对它做了两处改动（放开镰刀白名单、把树的生命值算进变化判定），见
[`docs/mod-changes.md`](docs/mod-changes.md) 与 [`docs/mod-changes.patch`](docs/mod-changes.patch)。

## 配置

```powershell
copy bot\planner-config.example.json bot\planner-config.json
notepad bot\planner-config.json      # 填 api_key
```

`planner-config.json` 不要提交（已在 .gitignore 里）。

可选环境变量：

| 变量 | 作用 |
|---|---|
| `FARM_CFG` | 指定配置文件路径（默认 `bot/planner-config.json`） |
| `FARM_REPORT_CHAT` | 报告里写的收件人标签（默认 `me`），纯文本作用 |
| `WECHAT_AUTO` | 微信机器人目录（给 `start-wechat.bat` 用，可选） |
| `FARM_DATA` | 运行数据（日志/收发箱/备份）放哪，默认就是本目录 |
| `FARM_RESET_HOURS` | 连续跑多久自动清一次动作记录（默认 8，防 OUTPUT_LIMIT） |
| `FARM_CLEAR_MODE` | 清障决策：`auto`（代码按缺口算）或 `ask`（让模型挑候选名单） |
| `FARM_CLEAR_ZONE` | 默认清障范围：`cluster`（只清开垦区）`path` `all` |
| `FARM_CLEAR_BUDGET` | 每天最多清几格障碍（默认 20） |

### 代码放别处，数据留在游戏目录

游戏目录通常很大又不想进 git，可以只把 `bot/` 放到仓库里，
用目录 junction 挂回游戏目录（Windows 不需要管理员）：

```powershell
mklink /J "<游戏目录>\_dev\bot" "D:\code\stardew-farm-bot\bot"
set FARM_DATA=<游戏目录>\_dev
```

`FARM_DATA` 决定 `farm-service.log`、`inbox/`、`outbox/`、`status.json`、
`input.lock` 写在哪里；代码本身可以只在仓库里维护一份。

## 跑起来

```powershell
pip install -r requirements.txt
copy bot\planner-config.example.json bot\planner-config.json   # 填 api_key
cd bot
.\start-farm.bat
```

它会：开游戏 → 等窗口 → 铺满 → 进存档 → 起服务（`--loop`）。
停：`stop-farm.bat`，或者在 `bot/` 下建一个空文件 `STOP`。

### 持续执行模式

`--loop` 会将模型思考与农务执行分开：

- 同时最多一个后台模型请求；请求只读取状态摘要，不操作游戏连接。
- 模型一次返回最多 6 步；自主计划剩一项且达到补充间隔时提前请求下一段。
- 没有模型结果时，按当前体力、物资和当日参数继续收获、浇水、播种等可执行工作。
- 收获、浇水、播种与清障每段最多处理 3 格；任务未完成则在后续轮次继续。
- 有实际进展时约 0.1 秒后进入下一轮；无可执行任务时等 2 秒，异常时等 5 秒。取消原来每轮固定的约 30 秒等待。
- 模型失败按 15～120 秒退避；锄地、种植、浇水、收获、清障、砍树按“动作＋地图＋目标坐标”记忆结果。目标失败或无进展时跳过，先做其他目标；仅消耗体力或水不算进展。
- 历史保存到数据目录 `action-history.json`，同一游戏会话内重启服务也保留。目标状态变化、跨日或换存档后重新评估；`STALE_REF/BUSY/TRANSITION` 只允许一次延迟重试。整项技能受阻时仍有 30 秒冷却。
- 日志用 `[动作历史 till@Farm:66,21 #…] failed/no_progress/done/progress` 标记结果，后台模型也会收到近期历史。具体规则见 `docs/action-history.md`。
- 派发前根据最新状态检查；跨日期、存档会话或玩家新指令的旧结果不会覆盖当前计划。

`task` 指令会立即回执并进入后台规划；新任务或直接控制指令替换尚未完成的旧任务。
规划期间继续默认农务或现有当日设置，模型返回后再逐段执行新任务。`status.json` 新增
`planner.thinking` 和 `planner.queued_steps`，可观察是否在思考及剩余步骤数。

`--once` 只执行一段当前可执行农务并退出；需要持续接收和执行模型结果时使用 `--loop`。
采购往返、回家睡觉、节日仍保留原有完整技能流程；新计划和停止指令在技能边界检查。
停止时不再派发游戏动作；在途 HTTP 线程可能仍需等待网络超时才退出进程。

本次只调整 Python，无需重编 Mod，也没有新增依赖。运行中的旧服务需要重启才会加载新代码。

离线回归测试（不启动游戏、不调用真实模型）：

```powershell
python -m unittest discover -s tests -v
```

## 体检

```powershell
python bot\farm-doctor.py
```

打印：所有星露谷窗口（类名/尺寸/是不是控制台）、程序会选哪个、进程、
桥端口通不通、收件箱/状态/日志尾巴。

## 几条踩过的坑

- `farm_service.py` 不加 `--loop` 只跑一轮就退出（退出码 0，看着像"莫名其妙没了"）
- SMAPI 控制台窗口标题里也有 `Stardew Valley`，按标题找窗口会认错 → 必须按窗口类名排除 `console`/`terminal`
- `SetWindowPos` 拉窗口前要先 `ShowWindow(SW_RESTORE)`，最大化状态下直接拉不动

## 清障策略

**缺地才清，而且只在开垦区清。** 细节见 [`docs/clear-policy.md`](docs/clear-policy.md)：
缺口驱动、区域闸门（none/cluster/path/all）、每日预算、把农场地图摘要喂给模型。

## 和微信机器人解耦

这个项目**跑起来不需要微信**。它只跟文件打交道：

```
<FARM_DATA>/inbox/<id>.json    {"id","cmd","arg","from","ts"}   指令（status/decisions/water/sleep/backup/task/reset）
<FARM_DATA>/outbox/r*.json     {"id","chat","text","at"}        报告，没人读就会堆在目录里
```

以前微信机器人会读 `outbox` 发到微信、也往 `inbox` 塞指令；
**现在两边已经拆开**，微信那边不含任何农场代码。
想让报告重新进微信，自己做个小脚本读 `outbox/*.json` 就行，
格式就上面那个。
## 校验文件一致性

仓库里带 `CHECKSUMS.sha256`（每个文件的 sha256，按 LF 归一化算，Windows/Linux 结果一样）。

```powershell
# 校验（不一致会列出哪个文件、返回码 1）
python scripts/checksums.py --verify

# 改了文件之后重新生成
python scripts/checksums.py --write
```

Linux/macOS 上也能直接用标准工具（注意必须没开 autocrlf，否则换行会不一致）：

```bash
sha256sum -c CHECKSUMS.sha256
```

## 许可

MIT，见 LICENSE。游戏本体资源与 mod 版权归各自作者。
