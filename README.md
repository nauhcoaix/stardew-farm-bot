# 星露谷农场机器人

让模型自己决定今天怎么种地，然后由代码去执行。

**分工**：模型只负责「今天怎么安排」（种什么、先浇水还是先清障），
所有具体动作（走路、砍、种、浇、收、卖、睡觉）都由代码调游戏接口完成。

## 组成

```
bot/
  farm_service.py   常驻服务：白天干活、晚上睡觉、接指令（inbox）、写报告（outbox）
  planner.py        问模型要当日计划（JSON）
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
## 许可

MIT，见 LICENSE。游戏本体资源与 mod 版权归各自作者。
