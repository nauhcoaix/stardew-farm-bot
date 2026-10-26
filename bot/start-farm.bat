@echo off
chcp 65001 >nul
setlocal
set "DEV=%~dp0"
set "GAME=%DEV%.."
REM 依赖：pip install -r requirements.txt
set "PY=python"
if exist "%DEV%pylibs" set "PYTHONPATH=%DEV%pylibs"
where %PY% >nul 2>nul || (echo 找不到 python，请先安装 Python 3.12 并加进 PATH & pause & exit /b 1)
echo [1/2] 开游戏并进存档...
"%PY%" "%DEV%launch_farm.py"
if errorlevel 1 (
  echo 游戏没进存档，先不启动农场服务。
  pause
  exit /b 1
)
echo [2/2] 启动农场服务（新窗口）...
start "farm-service" /min "%PY%" "%DEV%farm_service.py" --loop
echo 完成。要停的话双击 stop-farm.bat
ping -n 3 127.0.0.1 >nul
