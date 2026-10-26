@echo off
chcp 65001 >nul
rem 改成你微信机器人（wechat-auto）的实际路径，或者设环境变量 WECHAT_AUTO
if "%WECHAT_AUTO%"=="" set "WX=%~dp0..\..\wechat-auto"
if not "%WECHAT_AUTO%"=="" set "WX=%WECHAT_AUTO%"
if not exist "%WX%\auto.ps1" (
  echo 找不到 wechat-auto，路径变了就改这个文件里的 WX。
  pause
  exit /b 1
)
cd /d "%WX%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%WX%\auto.ps1"
pause