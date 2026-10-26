# ---------- 农场（星露谷）----------
function Invoke-FarmCommand {
  param([string]$Text, [string]$Chat)
  $dev = [string]$cfg.farm_dev
  if (-not $dev) { return $false }
  $wantChat = [string]$cfg.farm_chat
  if ($wantChat -and (Normalize-Text $Chat) -ne (Normalize-Text $wantChat)) { return $false }
  $t = [string]$Text
  if (-not $t) { return $false }
  $cmd = ''
  if ($t -match '浇水|浇地|浇一|浇下') { $cmd = 'water' }
  elseif ($t -match '睡觉|睡吧|去睡') { $cmd = 'sleep' }
  elseif ($t -match '备份|存档') { $cmd = 'backup' }
  elseif ($t -match '农场|地里|作物|长势|种了|收成|进度|情况|怎么样|看看') { $cmd = 'status' }
  if (-not $cmd) { return $false }
  $inbox = Join-Path $dev 'inbox'
  if (-not (Test-Path $inbox)) { New-Item -ItemType Directory -Path $inbox -Force | Out-Null }
  $id = 'c' + (Get-Date -Format 'yyyyMMddHHmmss')
  $payload = [ordered]@{ id = $id; cmd = $cmd; arg = '' }
  try {
    ($payload | ConvertTo-Json -Compress) | Set-Content -Path (Join-Path $inbox ($id + '.json')) -Encoding UTF8
    Write-Log ('[农场] 收到指令 ' + $cmd + '：「' + $t + '」')
  } catch { Write-Log ('[农场] 写指令失败: ' + $_.Exception.Message) 'WARN'; return $false }
  try { [void](Send-Reply -Text '行，我去弄') } catch { }
  return $true
}

function Send-FarmReports {
  $dev = [string]$cfg.farm_dev
  if (-not $dev) { return $false }
  $ob = Join-Path $dev 'outbox'
  if (-not (Test-Path $ob)) { return $false }
  $sentDir = Join-Path $ob 'sent'
  if (-not (Test-Path $sentDir)) { New-Item -ItemType Directory -Path $sentDir -Force | Out-Null }
  $did = $false
  foreach ($f in @(Get-ChildItem $ob -Filter '*.json' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime)) {
    $j = $null
    try { $j = (Get-Content $f.FullName -Raw -Encoding UTF8) | ConvertFrom-Json } catch { continue }
    if (-not $j -or -not $j.text) { continue }
    $to = [string]$j.chat
    if (-not (Ensure-Chat -Chat $to -Win $win -Layout $L -Targets $targets)) { continue }
    try {
      foreach ($ln in @([string]$j.text -split "`r?`n")) {
        $s = $ln.Trim()
        if (-not $s) { continue }
        if ($DryRun) { Write-Log ('[农场][空跑] 会发: ' + $s) } else { [void](Send-Reply -Text $s) }
        Start-Sleep -Milliseconds 500
      }
      if (-not $DryRun) { Write-Log ('[农场] 日报已发 -> [' + $to + ']') }
      Move-Item $f.FullName (Join-Path $sentDir $f.Name) -Force
      $did = $true
    } catch { Write-Log ('[农场] 发日报失败: ' + $_.Exception.Message) 'WARN' }
  }
  return $did
}

