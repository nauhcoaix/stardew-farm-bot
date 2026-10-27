# 星露谷自主机器人

模型根据能力目录、现场与统一记忆选择行动，包含记忆、探索、走路、交互、工具、菜单、农务和钓鱼。
执行器动态寻路、复核结果并写回记忆。当前主服务已切换为自主模式，不再生成固定的每日农务日程。
设计、记忆格式与使用方式见 [自主规划与统一记忆](docs/autonomous-agent.md)。

## 组成

```
bot/
  farm_service.py   常驻服务：收意图、派发模型选择的技能、记录结果
  planner.py        能力目录 + 现场 + 记忆 → 下一组技能（JSON）
  memory_store.py   按存档保存统一事件、地图与连接索引
  exploration.py    根据实际观察/记忆生成探索前沿和路径
  agent_skills.py   模型可调用的技能执行器
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
当前桥接源码在 `mod/agent-stardew/mods/AgentStardew/`。除工具兼容改动外，
0.1.1增加存档/玩家ID、菜单身份和人物实际移动轨迹，供统一记忆使用。
编译后正常退出游戏，运行 `powershell -File scripts/install-agent-bridge.ps1` 安装；
脚本备份并校验桥接DLL及manifest，不修改独立钓鱼Mod、配置和存档。
工具改动的历史说明见 [`docs/mod-changes.md`](docs/mod-changes.md)。

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

### 自主执行模式

- 模型获得每项技能的参数、前提和不可用原因，自行决定当前小目标与行动。
- memory、explore是可主动选择的技能；走过的路、看过的对象和技能结果写入同一个SQLite库。
- 一次最多选择6步；执行器与后台推理并行，模型思考时继续已经委托的技能片段。
- 移动依据已观察/记忆地形，每段最多3格；区域连接来自观察或真实过图，目标由模型选择。
- 失败反馈、来源和证据保留；模型笔记标记为hypothesis，不能变成游戏事实。
- 没有模型或模型尚未选择行动时等待，不切换到预设农务、固定路线或自动钓鱼。
- status.json显示planner.goal、objective及最近技能结果。STOP仍由原入口控制。

需要AgentStardew桥0.1.1提供稳定存档ID与菜单身份，正常退出游戏后更新Mod并重启SMAPI及服务。
旧桥会明确退化为会话级记忆，避免错误混合存档。没有新增Python依赖。

```powershell
python -m unittest discover -s tests -v
python bot/memory_cli.py stats
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

## 历史独立脚本：清障策略

下面保留旧规则实现的说明；新主服务由模型选择技能，不会自动执行这套每日策略。

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
