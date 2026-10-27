# mod 改动（镰刀 + 砍树）

本仓库依赖 [agent-stardew](https://github.com/rottenpen/agent-stardew)（MIT）的 SMAPI mod，
但原版有两处会影响农场自动化的判断，需要打两个补丁：

| # | 问题 | 影响 |
|---|---|---|
| 1 | `use` 动作的工具白名单里没有镰刀 | **杂草永远清不掉**（斧头/镐对杂草无效） |
| 2 | `State()` 只比地形类型/作物/物体ID，不含树的生命值 | **砍树每一下都报"未发生可验证变化"**（树要砍好多下才倒） |

完整 diff：[`mod-changes.patch`](mod-changes.patch)

## 1. 镰刀

`mods/AgentStardew/GameActions.cs`

```csharp
// 原版
else if (selected is Tool tool && (tool is Hoe || tool is WateringCan || tool is Axe || tool is Pickaxe))
// 改成
else if (selected is Tool tool && (tool is Hoe || tool is WateringCan || tool is Axe || tool is Pickaxe || IsScythe(tool)))
```

新增判断（镰刀在游戏里是 `MeleeWeapon`，不是普通 `Tool`）：

```csharp
private static bool IsScythe(Tool tool)
{
    if (tool is not MeleeWeapon) return false;
    var name = tool.GetType().Name ?? "";
    if (name.Contains("Scythe", StringComparison.OrdinalIgnoreCase)) return true;
    var id = tool.ItemId ?? "";
    return id is "47" or "53" || id.EndsWith(")47", StringComparison.Ordinal) || id.EndsWith(")53", StringComparison.Ordinal);
}
```

机器人侧对应：`bot/farmer.py` 里杂草映射到 `Scythe`，镰刀是范围攻击（一次约 3 格）。

## 2. 砍树

`UseAction.State()` 决定"这一次工具使用有没有产生可验证变化"。
原版只拼了地形类型、`HoeDirt` 状态、作物、物体 ID ——
**树的 health 不在里面**，所以砍一下（树没倒）会被判成"没变化"并抛错。

```csharp
private static string State(Point tile)
{
    Game1.currentLocation.terrainFeatures.TryGetValue(tile.ToVector2(), out var terrain);
    Game1.currentLocation.objects.TryGetValue(tile.ToVector2(), out var obj);
    // 树要好几下才倒：把 health/树桩/倒下 也算进状态，否则每一下都被判"没变化"
    var tree = terrain as Tree;
    var extra = tree == null ? "-" : $"h{tree.health.Value}{tree.stump.Value}{tree.falling.Value}";
    return $"{terrain?.GetType().Name}/{(terrain as HoeDirt)?.state.Value}/{(terrain as HoeDirt)?.crop?.indexOfHarvest.Value}/{obj?.QualifiedItemId}/{extra}";
}
```

> `Object.health` 是 protected，没法直接读，所以石头/杂草这种多下才碎的对象没有加进状态
> （小石头一下就没，不影响）。

## 编译

需要 .NET 6 SDK（目标 `net6.0`）。没有 SDK 可以用便携版：

```powershell
curl.exe -L -o D:\tools\dotnet-sdk-6.zip https://dotnetcli.azureedge.net/dotnet/Sdk/6.0.428/dotnet-sdk-6.0.428-win-x64.zip
mkdir D:\tools\dotnet6
tar.exe -xf D:\tools\dotnet-sdk-6.zip -C D:\tools\dotnet6

$env:STARDEW_GAME_PATH = "D:\1\steamapps\common\Stardew Valley"
D:\tools\dotnet6\dotnet.exe build "<agent-stardew>\mods\AgentStardew\AgentStardew.csproj" -c Release

copy "<agent-stardew>\mods\AgentStardew\bin\Release\net6.0\AgentStardew.dll" "<游戏>\Mods\AgentStardew\AgentStardew.dll"
```

**换 DLL 必须重启游戏才生效。**

## 实测

### 镰刀割草

```
农场上的杂草: 105
  选 镰刀 -> 已选择槽位 4
  清 Grass {x:65,y:25} -> 割草完成，已检查目标状态
  清 Grass {x:53,y:16} -> 割草完成，已检查目标状态
结果：杂草 105 -> 90（5 次挥镰清掉 15 格）
```

### 砍树

```
砍之前木头: 19
     这棵砍倒了，换一棵
     这棵砍倒了，换一棵
结果：木头 19 -> 27 (+8)，挥斧 24 次，报错 0 条
```

改之前同样这 24 下里，除了砍倒的那两下，其余每一挥都会抛
`UNSUPPORTED: 工具动画结束，但目标未发生可验证变化`。

## 3. 动作记录清零（防 OUTPUT_LIMIT）

`ActionCoordinator` 会把每个 actionId 记在 `seen` 字典里做幂等保护，
**上限 20 万条**，超了就一律返回：

```
OUTPUT_LIMIT: 本次存档会话已达动作记录上限，请在存档后重新加载。
```

只有「读档 / 回主菜单」才清零。农场机器人一天干几万个动作，几天就顶满，
表现是「服务还在跑，但什么都不做」。

加一个 `reset` 命令，让客户端能主动清空这张表（不用重启游戏）：

```csharp
// ModEntry.cs
if (r.Command == "reset")
{
    actions.Reset();
    Send(pending, Response.Ok(r, new { kind = "reset", reset = true }));
    continue;
}
```

```csharp
// Protocol.cs：允许 reset 通过校验
"reset" => Array.Empty<string>(),
```

客户端 `farm_client.py`：

```python
async def reset(self):
    return await self.request("reset", {}, timeout_ms=10000)
```

`farm_service.py` 的策略：连续跑满 `FARM_RESET_HOURS`（默认 8 小时）就自动清零一次，
也可以在收件箱里发 `{"cmd": "reset"}` 手动清。

## 制造（craft / recipes）

mod 侧新增：
- `craft` 命令：直接解析 `Data/CraftingRecipes`，扣材料 + 产出（不弹菜单）
- `recipes` 命令：列出配方（key / 中文名 / 材料 / 是否已学）

编译（需要 .NET SDK）：
``powershell
D:\dotnet-sdk\dotnet.exe build -c Release -p:GamePath="D:\1\steamapps\common\Stardew Valley"
# 产物复制到 <游戏>\Mods\AgentStardew\AgentStardew.dll
``

bot 侧：`craft` / `recipes` 两个技能；`knowledge.json` 里配方名必须用游戏中文名（箱子叫"宝箱"）。
