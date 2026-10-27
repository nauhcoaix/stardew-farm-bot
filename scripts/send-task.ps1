#!/usr/bin/env pwsh
<#
.SYNOPSIS
  给农场机器人下任务：写一条 JSON 到 <DataDir>\inbox\，服务几秒内读到并当成当前目标。

.EXAMPLE
  .\send-task.ps1 -Task "只清农舍 12 格内的杂草，别出圈"
  .\send-task.ps1 -Task "把农舍南边空地锄了种上现有种子" -DataDir "D:\1\steamapps\common\Stardew Valley\_dev"
  .\send-task.ps1 -Command status
#>
param(
    [string]$Task = "",
    [ValidateSet("status", "decisions", "task", "water", "sleep", "reset", "play")]
    [string]$Command = "task",
    [string]$DataDir = $(if ($env:FARM_DATA) { $env:FARM_DATA } else { "D:\1\steamapps\common\Stardew Valley\_dev" })
)

$ErrorActionPreference = "Stop"
$inbox = Join-Path $DataDir "inbox"
New-Item -ItemType Directory -Force -Path $inbox | Out-Null

$payload = if ($Command -eq "task") {
    if (-not $Task) { throw "task 需要 -Task 文本" }
    @{ cmd = "task"; arg = $Task }
} else {
    @{ cmd = $Command }
}

$json = $payload | ConvertTo-Json -Compress
$file = Join-Path $inbox ("{0}-{1}.json" -f $Command, (Get-Date -Format "yyyyMMdd-HHmmss"))
[System.IO.File]::WriteAllText($file, $json, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "[已发送] $json"
Write-Host "         文件: $file"
