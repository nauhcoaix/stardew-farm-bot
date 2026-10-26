param(
    [string]$GamePath = $env:STARDEW_GAME_PATH
)
$ErrorActionPreference = 'Stop'
if (-not $GamePath) { $GamePath = 'C:\Program Files (x86)\Steam\steamapps\common\Stardew Valley' }
$resolvedGame = (Resolve-Path -LiteralPath $GamePath).Path
foreach ($name in @('Stardew Valley.dll', 'StardewModdingAPI.dll', 'smapi-internal\0Harmony.dll')) {
    if (-not (Test-Path -LiteralPath (Join-Path $resolvedGame $name))) { throw "缺少游戏依赖：$name" }
}
if (Get-Process -Name 'StardewModdingAPI', 'Stardew Valley' -ErrorAction SilentlyContinue) {
    throw '先正常保存并退出游戏，再安装独立钓鱼 Mod。'
}
$skillRoot = Split-Path -Parent $PSScriptRoot
$project = Join-Path $skillRoot 'mod\StandaloneFishingSkill.csproj'
& dotnet build $project -c Release "-p:GamePath=$resolvedGame" --nologo -v minimal
if ($LASTEXITCODE -ne 0) { throw '钓鱼 Mod 编译失败，未安装。' }
$target = [IO.Path]::GetFullPath((Join-Path $resolvedGame 'Mods\StandaloneFishingSkill'))
$expected = [IO.Path]::GetFullPath((Join-Path $resolvedGame 'Mods')) + [IO.Path]::DirectorySeparatorChar
if (-not $target.StartsWith($expected, [StringComparison]::OrdinalIgnoreCase)) { throw '安装目标越界。' }
$manifest = Join-Path $target 'manifest.json'
if (Test-Path -LiteralPath $target) {
    if (-not (Test-Path -LiteralPath $manifest)) { throw '目标目录已存在但没有本技能 manifest，停止安装。' }
    $installed = Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($installed.UniqueID -ne 'StardewFarmBot.StandaloneFishingSkill') { throw '目标目录属于其他 Mod，停止安装。' }
}
New-Item -ItemType Directory -Path $target -Force | Out-Null
$built = Join-Path $skillRoot 'mod\bin\Release\net6.0'
foreach ($name in @('StandaloneFishingSkill.dll', 'manifest.json')) {
    $destination = Join-Path $target $name
    if (Test-Path -LiteralPath $destination) {
        Copy-Item -LiteralPath $destination -Destination ($destination + '.bak-' + (Get-Date -Format 'yyyyMMddHHmmss'))
    }
    Copy-Item -LiteralPath (Join-Path $built $name) -Destination $destination -Force
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'fish.py') -Destination (Join-Path $target 'fish_client.py') -Force
Write-Output "独立钓鱼技能已安装：$target"
Write-Output '下次启动 SMAPI 后加载；默认关闭。准备好鱼竿与岸边位置，再运行 fish.py start。'
