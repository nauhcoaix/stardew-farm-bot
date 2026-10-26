# 星露谷农场机器人

让模型自己决定今天怎么种地，然后由代码去执行。

**分工**：模型只负责「今天怎么安排」（种什么、先浇水还是先清障），
所有具体动作（走路、砍、种、浇、收、卖、睡觉）都由代码调游戏接口完成。

## 组成

```
bot/
  farm_service.py   常驻服务：白天干活、晚上睡觉、读微信指令、写日报
  planner.py        问模型要当日计划（JSON）
  farmer.py         底层动作：走位/清障/翻地/播种/浇水/收获/卖货
  farm_client.py    SMAPI mod 的子进程桥（WebSocket + JSON-RPC）
  launch_farm.py    开游戏 → 进存档 → 把窗口铺满
  shop.py          找商店、买种子
  farm-doctor.py    环境体检（窗口/端口/进程/日志一次打全）
docs/               调研笔记（MCP 桥、协议、同类项目）
```

## 依赖

1. **星露谷 1.6+** + **SMAPI**
2. **Agent Stardew 的 SMAPI mod**（提供 `ws://127.0.0.1:17654` 桥）
   - 开源项目，MIT 许可，源码不随本仓库分发
3. **Python 3.12** + `pip install -r requirements.txt`
4. 一个兼容 OpenAI 协议的模型接口（默认 DeepSeek）

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
| `FARM_REPORT_CHAT` | 日报发给哪个微信会话（默认 `me`） |
| `WECHAT_AUTO` | 微信机器人目录（给 `start-wechat.bat` 用） |

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

## 许可

MIT，见 LICENSE。游戏本体资源与 mod 版权归各自作者。
