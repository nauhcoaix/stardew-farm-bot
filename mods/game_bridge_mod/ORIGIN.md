# mods/game_bridge_mod — 来源与许可

| 项目 | 值 |
|---|---|
| 白名单编号 | 1 |
| 上游仓库 | `C:\Users\34861\Desktop\ideas\stardew_valley_dfy`（`mod/agent-stardew`） |
| 原始路径 | `mod/agent-stardew/mods/AgentStardew/` |
| 迁入日期 | 2026-10-27 |
| manifest 版本 | 0.1.1 |
| 目标框架 | net6.0 / SMAPI |

## 迁移方式

整个 Mod 目录原样复制，未修改任何 `.cs` 文件；仅排除了 `bin/`、`obj/` 构建产物。
`AgentStardew.csproj` 通过 `GamePath` / `STARDEW_GAME_PATH` 引用游戏程序集，未改动。

## 提供的功能

本机通信与协议、游戏状态观察、存档与目标身份、位置轨迹、原生移动、原生工具与交互、
核心动作协调、菜单与物品操作、配方与制造、截图、后台更新。

**不负责**：决定今天先种什么、去哪、何时清障；调试命令存在也不代表新 Agent 默认可调用。

## 许可

上游为 MIT（见 `stardew_valley_dfy/LICENSE`）。本仓库根目录 `LICENSE` 同为 MIT。
上游项目另有 `UPSTREAM.md` / `SPEC.md` 等说明文件留在原仓库，未复制到这里；
如需追溯协议细节，请对照原路径。

## 构建（未在本轮执行）

```powershell
$env:STARDEW_GAME_PATH = "C:\Program Files (x86)\Steam\steamapps\common\Stardew Valley"
dotnet build .\AgentStardew.csproj -c Release
```

本轮没有编译或安装 Mod，不能声称新目录下的构建已通过。
