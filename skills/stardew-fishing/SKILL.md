---
name: stardew-fishing
description: 附加模块：在星露谷里按键触发一段钓鱼（抛竿、咬钩、小游戏、入包核对），不移动、不寻路、不改动农务服务。用户想亲自按触发键钓鱼、或需要脚本化单次钓鱼时使用。
---

# 独立钓鱼（附加模块）

- **执行器**：独立 SMAPI Mod `StandaloneFishingSkill`（`StardewFarmBot.StandaloneFishingSkill`），与 `bot/farm_service.py`、模型动作白名单、AgentStardew 协议**完全解耦**，默认关闭、不自动运行。
- **触发方式**：游戏内按 **F7** 开始一段钓鱼，按 **F8** 立即停止；也可用控制台 `fish_skill_start` / `fish_skill_stop`，或脚本 `scripts/fish.py start|status|stop`。
- **本模块不移动角色**：不做寻路、不选岸边、不跨地图。走到水边是**玩家本人或其它移动模块**（例如仓库里的 `travel` / `routes`）的职责；本模块只在**当前站位**钓鱼。

## 前提（不满足会明确报原因，不会硬动）

1. 单人存档、画面可操作（无菜单/事件/过场）；
2. 背包里有 `FishingRod`，且至少一个空格（只有**完全没空位**时才会拒绝）；
3. 站在水边，**面前 2~6 格内有可钓水面**（朝向就是抛竿方向）；
   要往水边挪，用你自己的腿，或让农务侧代走：`{"action":"travel","arg":{"to":"Beach"}}`（区域名见 `bot/places.py`，需要已有路线 `places.route_learned`，实现见 `bot/explore.py`/`bot/route_recorder.py`）。本模块自己不走路。
4. 体力 ≥ 30，游戏时间 < 22:00；
5. 若农务服务在跑，先用它的 `STOP` 停稳——同一角色不能被两套控制同时驱动。

按 F7 被拒时，游戏控制台会打出原因（例如「面前 2~6 格内没有可钓水面：走到岸边、朝向水面后再按」）。

## 执行

默认一段 = **20 竿 / 1200 秒**（可在 `config.json` 调），命中任一即收工：竿数用完、目标鱼数达到、连续 3 竿无收获、同岸同朝向连 2 次空竿、体力不足、到 22:00、超时、换图/换竿/玩家接管、按 F8。

## 配置（可选）

Mod 目录下的 `config.json`（首次启动自动生成，改完重进存档/重启游戏生效）：

```json
{
  "StartKey": "F7",
  "StopKey": "F8",
  "MaxCasts": 20,
  "TargetFish": 0,
  "MaxSeconds": 1200,
  "MinEnergy": 30,
  "StopAt": 2200
}
```
数值会被夹到安全范围（竿数 1~100、时长 10~1800 秒、体力 15~270、就寝 600~2300 且必须是整点 HH00）。

```powershell
python scripts/fish.py doctor                 # 只查文件，不启动游戏
python scripts/fish.py start --max-casts 3 --wait
python scripts/fish.py status
python scripts/fish.py stop
```

`start` 由脚本发出时同样要求“已经站在岸边”；它不会替玩家走路。

## 动作与证据

`casting → waiting_bite → hooking → minigame → landing → ready` 全部读取真实游戏状态；小游戏通过 Harmony 前缀临时模拟工具键（结束后原样恢复输入），使用鱼位置/速度与绿条位置/速度做预测控制。**不改进度条数值、不加速咬钩、不生成鱼、不改背包**。

收获走游戏原生 `doneHoldingFish` 入包，并按 `itemId|quality` 做**背包差分**核对；鱼（`Category == -4` 或带 `counts_as_fish_catch`）与垃圾分别计数。

产物：`Mods/StandaloneFishingSkill/runtime/status.json`（现场状态：`state/phase/casts/fish/trash/reason`、`summary` 一行中文摘要、`reasonText` 中文原因、`emptySlots`、`nextStop` 预测下一竿会被什么条件拦住、`config` 当前生效预算）、`runtime/history.jsonl`（每竿一行，含 `runId/cast/location/tile/facing/result/...`）。`status.reason` 是唯一可信的停止原因，`active=false` 不等于“钓到了”。

## 安装与更新

1. 正常保存并**退出游戏**；
2. `powershell -NoProfile -File scripts/install.ps1 -GamePath "<游戏目录>"`（会编译、备份旧 DLL、只覆盖本 Mod 目录）；
3. 用 SMAPI 启动游戏 → 控制台出现「独立钓鱼附加模块就绪」即加载成功。

本技能不改动 `bot/` 下任何农务代码，也不改其它 Mod。
