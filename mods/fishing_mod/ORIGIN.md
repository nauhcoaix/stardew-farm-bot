# mods/fishing_mod — 来源与许可

| 项目 | 值 |
|---|---|
| 白名单编号 | 2 |
| 上游仓库 | `C:\Users\34861\Desktop\ideas\stardew_valley_dfy`（`skills/stardew-fishing`） |
| 原始路径 | `skills/stardew-fishing/mod/` |
| 迁入日期 | 2026-10-27 |
| manifest 版本 | 0.4.0 |
| UniqueID | `StardewFarmBot.StandaloneFishingSkill` |

## 迁移方式

Mod 源码原样复制（`ModEntry.cs`、`FishingPolicy.cs`、`BarInput.cs`、`manifest.json`、
`StandaloneFishingSkill.csproj`），未修改；排除 `bin/`、`obj/`。
旧的 Python CLI（`scripts/fish.py`）按其白名单范围只保留通信函数，已重写为
`src/stardew_agent/fishing_client`；安装脚本 `scripts/install.ps1` 未迁入（本轮不安装 Mod）。

## 提供的功能

在人物当前站位执行抛竿、等待咬钩、提竿、小游戏逐帧控制与收鱼；按预算结束；通过状态/
命令文件公布心跳、runId 与结果；只在自己拥有的小游戏内接管输入。

**不负责**：寻找岸边、旅行、选择钓鱼目的地或主动开启钓鱼任务。逐帧小游戏控制是预编程
执行器，不算机器人新学会的策略。

## 构建与安装（未在本轮执行）

```powershell
$env:STARDEW_GAME_PATH = "C:\Program Files (x86)\Steam\steamapps\common\Stardew Valley"
dotnet build .\StandaloneFishingSkill.csproj -c Release
# 复制输出到 <游戏目录>/Mods/StandaloneFishingSkill/
```

Python 侧通过 `<游戏目录>/Mods/StandaloneFishingSkill/runtime/` 读写 `status.json` /
`command.json`。
