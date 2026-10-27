@echo off
chcp 65001 >nul
setlocal
rem 给农场机器人下任务：输入一句话，写进 inbox
rem 数据目录优先用环境变量 FARM_DATA，否则用默认路径
set "DEV=%FARM_DATA%"
if "%DEV%"=="" set "DEV=D:\1\steamapps\common\Stardew Valley\_dev"
set "PS1=%~dp0send-task.ps1"

echo ============================================
echo  给农场机器人下任务
echo  例：只清农舍 12 格内的杂草，别出圈
echo  例：把农舍南边的空地锄了，种上现有种子
echo  例：记住农场去小镇要走 (81,0) 再往北一格
echo ============================================
echo.
set /p "TEXT=任务内容（直接回车=取消）: "
if "%TEXT%"=="" goto :cancel

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -Task "%TEXT%" -DataDir "%DEV%"
echo.
echo 服务几秒内读到，进度看 %DEV%\farm-service.log
timeout /t 3 >nul
goto :eof

:cancel
echo 已取消
timeout /t 2 >nul
