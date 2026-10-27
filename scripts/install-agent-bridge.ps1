[CmdletBinding()]
param(
    [string]$GamePath = 'C:\Program Files (x86)\Steam\steamapps\common\Stardew Valley',
    [string]$BuildPath = '',
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
if (-not $BuildPath) {
    $BuildPath = Join-Path $PSScriptRoot '..\mod\agent-stardew\mods\AgentStardew\bin\Release\net6.0'
}
$gameRoot = (Resolve-Path -LiteralPath $GamePath).Path
$buildRoot = (Resolve-Path -LiteralPath $BuildPath).Path
if (-not (Test-Path -LiteralPath (Join-Path $gameRoot 'Stardew Valley.dll'))) {
    throw 'GamePath不是星露谷游戏目录。'
}
$fileNames = @('AgentStardew.dll', 'manifest.json')
foreach ($fileName in $fileNames) {
    if (-not (Test-Path -LiteralPath (Join-Path $buildRoot $fileName) -PathType Leaf)) {
        throw "缺少编译文件：$fileName；请先编译AgentStardew。"
    }
}
$manifest = Get-Content -LiteralPath (Join-Path $buildRoot 'manifest.json') -Encoding UTF8 -Raw | ConvertFrom-Json
if ($manifest.UniqueID -ne 'AgentStardew.Bridge') {
    throw '构建目录不是 AgentStardew 桥，拒绝覆盖。'
}
if ([version]$manifest.Version -lt [version]'0.1.1') {
    throw "构建版本 $($manifest.Version) 过旧；至少需要 0.1.1（记忆桥接）。"
}
$targetRoot = Join-Path $gameRoot 'Mods\AgentStardew'
$running = @(Get-Process -Name 'StardewModdingAPI', 'Stardew Valley' -ErrorAction SilentlyContinue)
$report = [ordered]@{
    version = $manifest.Version
    build = $buildRoot
    target = $targetRoot
    needsGameExit = ($running.Count -gt 0)
    dllSha256 = (Get-FileHash -LiteralPath (Join-Path $buildRoot 'AgentStardew.dll') -Algorithm SHA256).Hash
}
if ($CheckOnly) {
    $report | ConvertTo-Json
    return
}
if ($running.Count -gt 0) {
    throw '请先正常退出星露谷/SMAPI，再运行安装；脚本不会关闭游戏。'
}

# 备份放在Mods之外，防止SMAPI把备份当成重复Mod加载。
$backupRoot = Join-Path $gameRoot ('agent-update-backups\bridge-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
New-Item -ItemType Directory -Path $targetRoot -Force | Out-Null
foreach ($fileName in $fileNames) {
    $installed = Join-Path $targetRoot $fileName
    if (Test-Path -LiteralPath $installed -PathType Leaf) {
        Copy-Item -LiteralPath $installed -Destination (Join-Path $backupRoot $fileName)
    }
}
try {
    foreach ($fileName in $fileNames) {
        $sourceFile = Join-Path $buildRoot $fileName
        $targetFile = Join-Path $targetRoot $fileName
        Copy-Item -LiteralPath $sourceFile -Destination $targetFile -Force
        if ((Get-FileHash -LiteralPath $sourceFile).Hash -ne (Get-FileHash -LiteralPath $targetFile).Hash) {
            throw "安装后校验失败：$fileName"
        }
    }
} catch {
    foreach ($fileName in $fileNames) {
        $saved = Join-Path $backupRoot $fileName
        if (Test-Path -LiteralPath $saved -PathType Leaf) {
            Copy-Item -LiteralPath $saved -Destination (Join-Path $targetRoot $fileName) -Force
        }
    }
    throw
}
$report['backup'] = $backupRoot
$report | ConvertTo-Json
Write-Output '记忆桥接0.1.1已安装。请重新启动SMAPI和农务服务。'
