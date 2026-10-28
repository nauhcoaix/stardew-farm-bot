@echo off
chcp 65001 >nul
setlocal EnableExtensions
set "ROOT=%~dp0"
cd /d "%ROOT%"
set "WAIT=1"
if /i "%~1"=="--no-wait" set "WAIT=0"
if /i "%~1"=="/nowait" set "WAIT=0"
if /i "%~1"=="help" goto :help
if /i "%~1"=="-h" goto :help
if /i "%~1"=="--help" goto :help

REM ============================================================
REM  Launch Stardew Valley through SMAPI (ASCII-only on purpose).
REM
REM    start-game.bat              launch SMAPI and wait for bridge
REM    start-game.bat --no-wait    launch SMAPI without waiting
REM
REM  Game path resolution order:
REM    1. STARDEW_GAME_PATH environment variable
REM    2. game_path in config.json
REM    3. default Steam library path
REM ============================================================

REM ---- 1. pick interpreter (used to read config.json) ----
set "PY="
if exist "%ROOT%.venv\Scripts\python.exe" (
  set "PY=%ROOT%.venv\Scripts\python.exe"
) else (
  where python >nul 2>nul
  if not errorlevel 1 set "PY=python"
)

REM ---- 2. resolve game path ----
set "GAME="
if defined STARDEW_GAME_PATH set "GAME=%STARDEW_GAME_PATH%"
if defined GAME goto :game_path_ready
if not defined PY goto :default_game_path
set "CFG=%ROOT%config.json"
if not exist "%CFG%" goto :default_game_path
REM Read via a temp file: cmd mis-parses a quoted path at the start of a for /f command.
set "TMP_PATH=%TEMP%\stardew_game_path.txt"
"%PY%" "%ROOT%tools\read_game_path.py" "%CFG%" > "%TMP_PATH%" 2>nul
set "CFG_GAME="
set /p CFG_GAME=<"%TMP_PATH%"
del "%TMP_PATH%" 2>nul
if not defined CFG_GAME goto :default_game_path
set "GAME=%CFG_GAME%"
goto :game_path_ready

:default_game_path
set "GAME=C:\Program Files (x86)\Steam\steamapps\common\Stardew Valley"
:game_path_ready

if exist "%GAME%\StardewModdingAPI.exe" goto :have_smapi
echo [ERROR] StardewModdingAPI.exe not found under:
echo         %GAME%
echo         Set STARDEW_GAME_PATH or fix "game_path" in config.json.
goto :fail
:have_smapi

echo [1/3] Game folder: %GAME%

REM ---- 2. warn about missing mods (do not block) ----
set "MISSING="
if not exist "%GAME%\Mods\AgentStardew\AgentStardew.dll" set "MISSING=%MISSING% AgentStardew"
if not exist "%GAME%\Mods\StandaloneFishingSkill\StandaloneFishingSkill.dll" set "MISSING=%MISSING% StandaloneFishingSkill"
if defined MISSING (
  echo [WARN] Missing mod DLLs under Mods:%MISSING%
  echo        The agent needs AgentStardew for the bridge; fishing needs the other one.
)

REM ---- 3. make sure the running game is not the vanilla one ----
tasklist /NH 2>nul | find /I "StardewModdingAPI.exe" >nul
if not errorlevel 1 goto :already_running
tasklist /NH 2>nul | find /I "Stardew Valley.exe" >nul
if not errorlevel 1 goto :vanilla_running

echo [2/3] Launching SMAPI...
if defined DRY_RUN echo [DRY] would start: "%GAME%\StardewModdingAPI.exe"
if not defined DRY_RUN start "" /D "%GAME%" "%GAME%\StardewModdingAPI.exe"
goto :wait_bridge

:already_running
echo [INFO] SMAPI is already running; not launching a second copy.
goto :wait_bridge

:vanilla_running
echo [ERROR] Stardew Valley.exe is running WITHOUT SMAPI, so mods are not loaded.
echo         Quit the game completely, then run this script again.
echo         Do not use the Steam "Play" button for agent runs.
goto :fail

:wait_bridge
if defined DRY_RUN goto :hint
if "%WAIT%"=="0" goto :hint
echo [3/3] Waiting for the game bridge on 127.0.0.1:17654 (up to 90 seconds)...
powershell -NoProfile -Command "$deadline=(Get-Date).AddSeconds(90); while((Get-Date) -lt $deadline){ $c=New-Object System.Net.Sockets.TcpClient; $ok=$false; try { $c.Connect('127.0.0.1',17654); $ok=$true } catch { } finally { $c.Dispose() }; if ($ok) { exit 0 }; Start-Sleep -Milliseconds 700 }; exit 1"
if errorlevel 1 (
  echo [WARN] Bridge did not answer within 90 seconds.
  echo        Check the SMAPI console window for mod load errors,
  echo        then run: start-agent.bat doctor
) else (
  echo [OK] Bridge is listening on 127.0.0.1:17654
)

:hint
echo.
echo Next steps:
echo   1. In the game, load your dedicated TEST save.
echo   2. Run: start-agent.bat          (self check: start-agent.bat doctor)
echo   3. Stop: start-agent.bat stop
echo.
echo Note: starting the game from Steam loads no mods; always use this script.
if not "%NO_PAUSE%"=="1" pause
exit /b 0

:help
echo Launch Stardew Valley through SMAPI
echo.
echo   start-game.bat              launch SMAPI and wait for the bridge
echo   start-game.bat --no-wait    launch SMAPI without waiting
echo.
echo Game path: STARDEW_GAME_PATH, else game_path in config.json,
echo            else the default Steam library path.
echo.
echo Use start-agent.bat afterwards to run the agent.
if not "%NO_PAUSE%"=="1" pause
exit /b 0

:fail
echo.
echo [ABORT] Fix the problem above and run again.
if not "%NO_PAUSE%"=="1" pause
exit /b 1

