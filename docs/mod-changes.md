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
