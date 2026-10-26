@echo off
chcp 65001 >nul
setlocal
set "DEV=%~dp0"
echo [1/3] 通知农场服务退出...
echo stop> "%DEV%STOP"
echo [2/3] 等它自己收尾（最多 40 秒）...
powershell -NoProfile -Command "$dead=(Get-Date).AddSeconds(40); while((Get-Date) -lt $dead){ $p=Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*farm_service*' }; if(-not $p){ exit 0 }; Start-Sleep -Milliseconds 800 }; exit 1"
if errorlevel 1 (
  echo     没等到，强制结束（之后若报 BUSY，重启游戏即可）
  powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*farm_service*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
)
del /q "%DEV%STOP" 2>nul
echo [3/3] 停微信机器人...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'powershell.exe' -and $_.CommandLine -like '*-File*auto.ps1*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo 完成。游戏没动，随时可以再点 start-farm.bat
ping -n 3 127.0.0.1 >nul