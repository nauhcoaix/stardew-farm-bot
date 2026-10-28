@echo off
chcp 65001 >nul
setlocal EnableExtensions
set "ROOT=%~dp0"
cd /d "%ROOT%"
set "CONFIG=%ROOT%config.json"
set "PYTHONUTF8=1"

REM ============================================================
REM  Stardew learning agent launcher / controller
REM
REM  This batch file is ASCII-only on purpose: cmd.exe cannot
REM  reliably parse UTF-8 text in .bat files, so Chinese text
REM  lives in README.md instead. The console code page is still
REM  set to 65001 so Python output (Chinese) shows correctly.
REM
REM    start-agent.bat              run once (default)
REM    start-agent.bat stop         request stop
REM    start-agent.bat status       last run, memory and skills
REM    start-agent.bat doctor       read-only self check
REM    start-agent.bat export       evidence to data\evidence.json
REM    start-agent.bat skills       list learned composite skills
REM
REM  Extra arguments are passed to the CLI, for example:
REM    start-agent.bat run --seconds 120 --requests 10
REM    start-agent.bat doctor --no-probe
REM
REM  Set NO_PAUSE=1 to skip the final pause.
REM ============================================================

REM ---- 1. pick interpreter (prefer project .venv) ----
if exist "%ROOT%.venv\Scripts\python.exe" (
  set "PY=%ROOT%.venv\Scripts\python.exe"
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] python not found. Install Python 3.10+ and add it to PATH,
    echo         or create .venv in this folder.
    goto :fail
  )
  set "PY=python"
)

REM ---- 2. run directly from src without pip install ----
if defined PYTHONPATH (
  set "PYTHONPATH=%ROOT%src;%PYTHONPATH%"
) else (
  set "PYTHONPATH=%ROOT%src"
)

REM ---- 3. parse command and remaining arguments ----
set "CMD=%~1"
if "%CMD%"=="" set "CMD=run"
shift
set "ARGS="
:collect
if "%~1"=="" goto :collected
set "ARGS=%ARGS% %~1"
shift
goto :collect
:collected

if /i "%CMD%"=="run"    goto :do_run
if /i "%CMD%"=="stop"   goto :do_stop
if /i "%CMD%"=="status" goto :do_status
if /i "%CMD%"=="doctor" goto :do_doctor
if /i "%CMD%"=="export" goto :do_export
if /i "%CMD%"=="stats"  goto :do_stats
if /i "%CMD%"=="skills" goto :do_skills
if /i "%CMD%"=="help"   goto :help
if /i "%CMD%"=="-h"     goto :help
if /i "%CMD%"=="--help" goto :help
echo [ERROR] Unknown command: %CMD%
goto :help

REM ---------------- run: one real-time session ----------------
:do_run
call :ensure_config
if errorlevel 1 goto :fail
call :ensure_model
if errorlevel 1 goto :fail
echo [run] Preconditions:
echo   1. Game is running with a dedicated TEST save loaded
echo   2. Mods\AgentStardew is installed; bridge listens on ws://127.0.0.1:17654/
echo   3. Self check: run "start-agent.bat doctor" in another window
echo.
"%PY%" -m stardew_agent run --config "%CONFIG%" %ARGS%
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo [OK] Run finished. State: %ROOT%data\state.json
) else (
  echo [EXIT %RC%] Run aborted, see the errors above.
  echo           If the bridge is unreachable, run start-game.bat first - it
  echo           launches the game through SMAPI (Steam "Play" loads no mods).
)
echo   Memory / evidence: %ROOT%data\experience.sqlite3
echo   Stop in another window: start-agent.bat stop
goto :done

REM ---------------- stop: write stop request ----------------
:do_stop
call :ensure_config
if errorlevel 1 goto :fail
"%PY%" -m stardew_agent stop --config "%CONFIG%" %ARGS%
set "RC=%ERRORLEVEL%"
echo [stop] Stop request written. The agent exits at its next loop check and releases control.
goto :done

REM ---------------- status ----------------
:do_status
call :ensure_config
if errorlevel 1 goto :fail
"%PY%" -m stardew_agent status --config "%CONFIG%" %ARGS%
set "RC=%ERRORLEVEL%"
goto :done

REM ---------------- doctor ----------------
:do_doctor
call :ensure_config
if errorlevel 1 goto :fail
"%PY%" -m stardew_agent doctor --config "%CONFIG%" %ARGS%
set "RC=%ERRORLEVEL%"
goto :done

REM ---------------- export ----------------
:do_export
call :ensure_config
if errorlevel 1 goto :fail
"%PY%" -m stardew_agent export --config "%CONFIG%" --out "%ROOT%data\evidence.json" %ARGS%
set "RC=%ERRORLEVEL%"
goto :done

REM ---------------- stats ----------------
:do_stats
call :ensure_config
if errorlevel 1 goto :fail
"%PY%" -m stardew_agent stats --config "%CONFIG%" %ARGS%
set "RC=%ERRORLEVEL%"
goto :done

REM ---------------- skills ----------------
:do_skills
call :ensure_config
if errorlevel 1 goto :fail
if "%ARGS%"=="" set "ARGS=list"
"%PY%" -m stardew_agent skills --config "%CONFIG%" %ARGS%
set "RC=%ERRORLEVEL%"
goto :done

REM ---------------- help ----------------
:help
echo Stardew learning agent
echo.
echo   start-agent.bat                 run once (default)
echo   start-agent.bat stop            request stop and release control
echo   start-agent.bat status          last run, memory and skills
echo   start-agent.bat doctor          read-only self check
echo   start-agent.bat export          evidence to data\evidence.json
echo   start-agent.bat stats           blocked/failed ratios from history
echo   start-agent.bat skills [list ^| retire ...]
echo.
echo Extra arguments are passed to the CLI:
echo   start-agent.bat run --seconds 120 --requests 10
echo   start-agent.bat run --quiet     (no live console output)
echo   start-agent.bat run --repeat-exempt select_item   (rate-limit allowlist)
echo   start-agent.bat run --model-interval 2            (min seconds between model requests)
echo   start-agent.bat run --model-completion            (use model completion predicates)
echo   start-agent.bat stats --days 7                    (ratios of the last 7 days)
echo   start-agent.bat doctor --no-probe
echo   start-agent.bat skills list
echo.
echo NO_PAUSE=1 skips the final pause.
set "RC=0"
goto :done

REM ---------------- final pause ----------------
:done
echo.
if not "%NO_PAUSE%"=="1" pause
exit /b %RC%

REM ---------------- prepare config.json ----------------
:ensure_config
if exist "%CONFIG%" exit /b 0
if not exist "%ROOT%config.example.json" (
  echo [ERROR] config.json is missing and config.example.json was not found.
  exit /b 1
)
copy /y "%ROOT%config.example.json" "%CONFIG%" >nul
echo [INFO] config.json created from config.example.json.
echo        Edit budget and rate-limit values there; there are no implicit defaults.
exit /b 0

REM ---------------- prepare model.json ----------------
:ensure_model
if exist "%ROOT%model.json" exit /b 0
if exist "%ROOT%model.example.json" (
  copy /y "%ROOT%model.example.json" "%ROOT%model.json" >nul
)
echo [SETUP] model.json is missing: put your sk-... DeepSeek API key into it.
echo         A template was created and opened in Notepad.
echo         Save it, then run this script again.
start "" notepad "%ROOT%model.json"
exit /b 1

:fail
echo.
echo [ABORT] Fix the problem above and run again.
set "RC=1"
if not "%NO_PAUSE%"=="1" pause
exit /b 1


