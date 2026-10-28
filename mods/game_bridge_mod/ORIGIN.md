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

## 本轮修改（2026-10-28，版本 0.1.2）

- 新增 `Pathfinder.cs`：4 邻接 A*（曼哈顿启发，避开门/切图点，搜索上限 24000 格）。
- `GameActions.cs` 的 `MoveAction` 改用 A*：
  - 找不到路径时明确报 `PATH_BLOCKED` 并给出“换目标/先走到中间点”的提示；
  - 中途被家具/NPC 挡住会等待 NPC 让路（≤1.5 秒），仍不通则最多重算 3 次路径；
  - 连续 1.2 秒没有位移触发重算；到点容差从 5px 放宽到 12px，减少来回抖动。
- 构建与部署：`dotnet build -c Release`，DLL 已复制到
  `Mods/AgentStardew/AgentStardew.dll`（旧 DLL 备份为 `.bak-before-astar`）。

## 本轮修改（2026-10-28，版本 0.1.3）

- 修“出农舍时站在门口不动”：
  - `MoveAction` 对出口不再用 12px 容差提前结束，改用 3px 并**等游戏真正切图**（≤2 秒）；
  - 出口格是 `warps` / `TouchAction` / `Action` 上的 `Warp` 时，A* 允许把它当作终点（仍不允许中途穿过）；
  - 走不进去的门先站到相邻格，自动按一次交互键（`checkAction`），再等切图；
  - 到门口却切不了图时返回明确原因（门没开/需要钥匙/需要先对话），不再反复“已到达”。
- 构建与部署：`dotnet build -c Release` 通过，DLL 已复制到
  `Mods/AgentStardew/AgentStardew.dll`（旧 DLL 备份为 `.bak-before-exitfix`）。

## 本轮修改（2026-10-28，版本 0.1.4）

- 继续加固寻路（“这个版本还是难以寻路”）：
  - A* 把 NPC/宠物/牲畜所在格当**高代价格**（不是墙）：能绕就绕，绕不开才穿；
  - 卡住判定改成**看格子**：贴着墙/角色抖动时像素在变但格子不变，也会在 1.2 秒后重算；
  - 等角色让路时不会触发“卡死重算”，最多等 2.5 秒再绕行；
  - 到点容差 12px → 6px，减少拐角处提前转向导致的卡墙；
  - 目标格本身走不进去（树/石头/家具/水）时自动退化为“走到相邻格”，不再直接报不可达；
  - `PATH_BLOCKED` 现在带起点/终点/挡路原因，方便定位是哪一格不通。
- 构建与部署：`dotnet build -c Release` 通过，DLL 已复制到
  `Mods/AgentStardew/AgentStardew.dll`（旧 DLL 备份为 `.bak-before-pathfix`）。

## 本轮修改（2026-10-28，版本 0.1.5）

- 修“出口在墙格上时反而卡住”：上一版先判断出口格能不能站，站不进去就退化成“站旁边按交互”，
  交互失败直接返回失败，人一直停在门口。
  - 现在出口**一律朝传送格踩过去**（游戏靠撞到传送格/门切图，不要求格子可站立）；
  - 离出口格心 40px 且只剩最后一步时就算“到门口”，持续朝门口压，0.6 秒后补按一次交互；
  - 等 2 秒仍不切图时区分原因：角色/动物站在出口、门需要钥匙/对话、或目标格不是门。
- 等角色让路的判定也包含农场牲畜。
- 构建与部署：`dotnet build -c Release` 通过，DLL 已复制到
  `Mods/AgentStardew/AgentStardew.dll`（旧 DLL 备份为 `.bak-before-warpexit`）。

## 本轮修改（2026-10-28，版本 0.1.6）

- 修“出口在地图边界外”：农舍出口传送格在 (3,12)，但地图可见边界只到 y=11，
  A* 直接把界外格当终点 → 永远走不到 → `PATH_BLOCKED`。
  - A* 终点先夹回地图内（最近格），人走到该格后**继续朝真正的传送格方向压**，
    靠游戏“撞到传送格”切图；
  - `Observations.InBounds` 改用所有图层尺寸的最大值，避免用错层导致边界判断偏小；
  - 普通坐标超出地图时也夹到最近格并提示“目标在地图外”，不再直接报 `INVALID_ARGUMENT`。
- 构建与部署：`dotnet build -c Release` 通过，DLL 已复制到
  `Mods/AgentStardew/AgentStardew.dll`（旧 DLL 备份为 `.bak-before-clamp`）。

## 本轮修改（2026-10-28，版本 0.1.7）

- 修“退出交互/菜单后 `CanMove` 一直是 false”：菜单关闭、直接 `checkAction`、取消工具动画等情况下，
  游戏偶尔不会把 `Farmer.CanMove` 复位，导致之后所有写动作都被 `BUSY` 拦下。
  - 新增 `PlayerRecovery`：只有 **没有菜单/事件/切图/新一天/小游戏/工具动画/进食/睡觉** 这些
    明确阻塞时，连续 2 帧仍为 false 才调用 `forceCanMove`（反射，缺失时退回赋值）并置 true；
  - 挂接点：`UpdateTicked`（每帧兜底）、`Snapshot`（保证观察值一致）、
    `GameActions.Create`（动作下发前自愈）、`MenuDump.Click/Close`（菜单刚关就恢复）；
  - 观察里的 `player` 增加 `canMoveLock`（仅锁定时出现）：`menu / event / fade / new-day /
    minigame / tool / eating / in-bed / stale`，Python 前提提示会带上原因。
- 构建与部署：`dotnet build -c Release` 通过，DLL 已复制到
  `Mods/AgentStardew/AgentStardew.dll`（旧 DLL 备份为 `.bak-before-canmove`）。

## 本轮修改（2026-10-28，版本 0.1.8）

- 背包满时不再“制作后掉地上”：`Crafting.Craft` 在扣材料前检查产物能否进背包
  （空格或可叠加余量），放不下就返回 `ok=false`，提示先 `place_chest` / `ship`；
- 观察的 `player` 增加 `freeSlots`，模型能直接看到还剩几格；
- `PlaceChest` 增加放置校验：只能放在人物 1～2 格内、不能是出口/门/家具/角色/已有物体格；
- `ShopBuy` 满包时不再把商品丢到地上：买不进背包就拒绝并提示先清空间；扣钱按实际买入数量；
- 构建与部署：`dotnet build -c Release` 通过，DLL 已复制到
  `Mods/AgentStardew/AgentStardew.dll`（旧 DLL 备份为 `.bak-before-inventory`）。

## 本轮修改（2026-10-28，版本 0.1.9）

- 去掉人为策略：移除“满包优先 place_chest/ship”的提示词与 placement 候选置顶；
  背包满只作为事实（`free_slots/full/wood`）与结构化错误码出现；
- 结构化错误码：`craft`/`shopbuy` 满包返回 `INVENTORY_FULL`，
  `placechest` 返回 `NO_WOOD / TOO_FAR / WARP_TILE / FURNITURE / CHARACTER / OCCUPIED`；
- 经验学习与召回：带 `INVENTORY_FULL` 或 `free_slots_before=0` 的动作写成
  “背包满时 …”，前提 `player.freeSlots == 0`；召回 query 带“背包满”状态词；
- 能力描述补充机制（不是策略）：`craft_item` 产物需要空位；`place_chest` 直接消耗 50 木材、
  不需要空背包格；
- 构建与部署：`dotnet build -c Release` 通过，DLL 已复制到
  `Mods/AgentStardew/AgentStardew.dll`（旧 DLL 备份为 `.bak-before-learning`）。

## 构建

```powershell
$env:STARDEW_GAME_PATH = "C:\Program Files (x86)\Steam\steamapps\common\Stardew Valley"
dotnet build .\AgentStardew.csproj -c Release
```

2026-10-28 已用 .NET SDK 9 构建通过（0 错误，1 个既有的 `Netcode` 引用警告），
产物 `bin/Release/net6.0/AgentStardew.dll` 已部署到游戏 `Mods/AgentStardew/`。
