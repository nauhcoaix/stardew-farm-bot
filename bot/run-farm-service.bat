@echo off
chcp 65001 >nul
setlocal
set "DEV=%~dp0"
set "GAME=%DEV%.."
REM 依赖：pip install -r requirements.txt
set "PY=python"
if exist "%DEV%pylibs" set "PYTHONPATH=%DEV%pylibs"
where %PY% >nul 2>nul || (echo 找不到 python，请先安装 Python 3.12 并加进 PATH & pause & exit /b 1)
"%PY%" "%DEV%farm_service.py" --loop >> "%DEV%_svc-log.txt" 2>&1
echo [exit %errorlevel%] >> "%DEV%_svc-log.txt"
