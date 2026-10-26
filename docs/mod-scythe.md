# 镰刀支持（mod 补丁）

## 问题

农场上的杂草（`Weeds` / `Grass`）**只能用镰刀割**，斧头和镐砍不掉。

但 Agent Stardew mod 的 `use` 动作有个白名单：

```csharp
// GameActions.cs:169
else if (selected is Tool tool && (tool is Hoe || tool is WateringCan || tool is Axe || tool is Pickaxe))
```

镰刀在游戏里是 `MeleeWeapon`（不是普通 `Tool`），被这条挡掉，
所以 `farmer.py` 里杂草只能映射成斧头 —— 砍不掉，白跑一趟。

## 改法

### 1) mod 侧（C#）

`mods/AgentStardew/GameActions.cs`：

- 白名单加上 `|| IsScythe(tool)`
- 操作名加 `"割草"`
- 报错文案更新
- 新增判断：

```csharp
/// <summary>镰刀在游戏里是 MeleeWeapon（47 普通镰刀 / 53 金镰刀），不是普通 Tool。</summary>
private static bool IsScythe(Tool tool)
{
    if (tool is not MeleeWeapon) return false;
    var name = tool.GetType().Name ?? "";
    if (name.Contains("Scythe", StringComparison.OrdinalIgnoreCase)) return true;
    var id = tool.ItemId ?? "";
    return id is "47" or "53" || id.EndsWith(")47", StringComparison.Ordinal) || id.EndsWith(")53", StringComparison.Ordinal);
}
```

完整 diff 见 [`mod-scythe.patch`](mod-scythe.patch)。

### 2) 机器人侧（Python）

`bot/farmer.py`：

```python
CLEAR_TOOLS = {
    "嫩枝": "Axe", "树枝": "Axe", "木头": "Axe",
    "石头": "Pickaxe", "岩石": "Pickaxe",
    "杂草": "Scythe", "Grass": "Scythe", "草": "Scythe", "杂草丛": "Scythe",
}
```

`tool_slot()` 增加镰刀识别（`MeleeWeapon` + 名字含"镰刀"/`Scythe`，或 ID `47`/`53`），
清障顺序里杂草排最前（一刀一片，不耗体力）。

## 编译

需要 .NET 6 SDK（net6.0 目标）。没有 SDK 时可以用便携版：

```powershell
# 下载便携 SDK（约 250 MB）
curl.exe -L -o D:\tools\dotnet-sdk-6.zip https://dotnetcli.azureedge.net/dotnet/Sdk/6.0.428/dotnet-sdk-6.0.428-win-x64.zip
mkdir D:\tools\dotnet6
tar.exe -xf D:\tools\dotnet-sdk-6.zip -C D:\tools\dotnet6

# 编译（GamePath 指向游戏目录）
$env:STARDEW_GAME_PATH = "D:\1\steamapps\common\Stardew Valley"
D:\tools\dotnet6\dotnet.exe build "<源码>\mods\AgentStardew\AgentStardew.csproj" -c Release

# 部署
copy "<源码>\mods\AgentStardew\bin\Release\net6.0\AgentStardew.dll" "<游戏>\Mods\AgentStardew\AgentStardew.dll"
```

**改完 DLL 必须重启游戏才生效。**

## 实测

```
开始位置 {'x': 9, 'y': 9} 地图 FarmHouse
出来后在 Farm {'x': 64, 'y': 15}
农场上的杂草: 105
  选 镰刀 -> 已选择槽位 4。
  清 Grass {'x': 65, 'y': 25} -> 割草完成，已检查目标状态。
  清 Grass {'x': 53, 'y': 16} -> 割草完成，已检查目标状态。
  …
结果：杂草 105 -> 90 （5 次挥镰清掉 15 格）
```

镰刀是范围攻击，一次能清 3 格左右。
