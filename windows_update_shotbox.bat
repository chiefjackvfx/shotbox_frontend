@echo off
setlocal EnableExtensions EnableDelayedExpansion

for %%I in ("%~dp0.") do set "REPO_DIR=%%~fI"
set "WAIT_PID=%~1"
set "BRANCH=main"
set "HAS_BLOCKING_CHANGES="

echo [ShotBox] Starting frontend updater...
echo [ShotBox] Repo folder: "%REPO_DIR%"
if not "%WAIT_PID%"=="" echo [ShotBox] Waiting for PID: %WAIT_PID%
echo.

set "STEP=Opening repo"
echo [Step] !STEP!
cd /d "%REPO_DIR%"
if errorlevel 1 (
    set "FAIL_MSG=Could not change into repo folder: %REPO_DIR%"
    goto :fail
)
echo [Info] Working in "%REPO_DIR%".
echo.

set "STEP=Checking Git"
echo [Step] !STEP!
where git >nul 2>&1
if errorlevel 1 (
    set "FAIL_MSG=Git is not installed or not on PATH."
    goto :fail
)
echo [Info] Git detected.
echo.

set "STEP=Checking git checkout"
echo [Step] !STEP!
if not exist "%REPO_DIR%\.git" (
    set "FAIL_MSG=This folder is not a git checkout."
    goto :fail
)
echo [Info] Git checkout detected.
echo.

set "STEP=Checking virtual environment"
echo [Step] !STEP!
if not exist "%REPO_DIR%\venv\Scripts\python.exe" (
    set "FAIL_MSG=Missing virtual environment at %REPO_DIR%\venv"
    goto :fail
)
echo [Info] Virtual environment detected.
echo.

set "STEP=Checking published branch"
echo [Step] !STEP!
set "CURRENT_BRANCH="
for /f "delims=" %%B in ('git branch --show-current 2^>nul') do set "CURRENT_BRANCH=%%B"
if not defined CURRENT_BRANCH (
    set "FAIL_MSG=Could not determine the current git branch."
    goto :fail
)
if /I not "!CURRENT_BRANCH!"=="%BRANCH%" (
    set "FAIL_MSG=Self-update only supports the %BRANCH% branch. Current branch: !CURRENT_BRANCH!"
    goto :fail
)
echo [Info] Current branch: !CURRENT_BRANCH!
echo.

if not "%WAIT_PID%"=="" (
    set "STEP=Waiting for ShotBox to close"
    echo [Step] !STEP!
    call :wait_for_pid "%WAIT_PID%"
    echo [Info] PID %WAIT_PID% has exited.
    echo.
)

set "STEP=Checking working tree"
echo [Step] !STEP!
set "HAS_BLOCKING_CHANGES="
for /f "delims=" %%S in ('git status --porcelain') do (
    call :handle_status_line "%%S"
)
if defined HAS_BLOCKING_CHANGES (
    if not defined FAIL_MSG set "FAIL_MSG=Local changes detected. Commit or discard them before updating."
    goto :fail
)
echo [Info] Working tree is clean for self-update.
echo.

set "STEP=Pulling latest frontend code"
echo [Step] !STEP!
git pull --ff-only origin %BRANCH%
if errorlevel 1 (
    set "FAIL_MSG=Git pull failed. Resolve the repository state manually and retry."
    goto :fail
)
echo [Info] Repo is up to date on origin/%BRANCH%.
echo.

set "STEP=Activating virtual environment"
echo [Step] !STEP!
call "%REPO_DIR%\venv\Scripts\activate.bat"
if errorlevel 1 (
    set "FAIL_MSG=Could not activate the virtual environment."
    goto :fail
)
echo [Info] Virtual environment activated.
echo.

set "STEP=Installing Python requirements"
echo [Step] !STEP!
python -m pip install -r "%REPO_DIR%\requirements.txt"
if errorlevel 1 (
    set "FAIL_MSG=Requirements install failed. Review the pip output above."
    goto :fail
)
echo [Info] Requirements install completed.
echo.

set "STEP=Restarting ShotBox"
echo [Step] !STEP!
start "" /D "%REPO_DIR%" "%REPO_DIR%\venv\Scripts\python.exe" "%REPO_DIR%\main.py"
if errorlevel 1 (
    set "FAIL_MSG=ShotBox failed to restart."
    goto :fail
)
echo [Info] ShotBox restart launched.
echo.
echo [Success] Frontend update completed successfully.
exit /b 0

:handle_status_line
set "STATUS_LINE=%~1"
set "STATUS_CODE=%STATUS_LINE:~0,2%"
set "STATUS_PATH=%STATUS_LINE:~3%"

if /I "%STATUS_CODE%"=="??" (
    echo(%STATUS_PATH%| findstr /R /I ".*_settings\.yaml$" >nul
    if not errorlevel 1 (
        echo [Info] Ignoring untracked local settings file: "%STATUS_PATH%"
        exit /b 0
    )
)

if not defined HAS_BLOCKING_CHANGES (
    echo [Info] Blocking git status line: %STATUS_LINE%
)
set "HAS_BLOCKING_CHANGES=1"
set "FAIL_MSG=Local changes detected. Commit or discard them before updating."
exit /b 0

:wait_for_pid
tasklist /FI "PID eq %~1" | find "%~1" >nul 2>&1
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait_for_pid
)
exit /b 0

:fail
echo.
echo [Error] Step failed: !STEP!
echo [Error] !FAIL_MSG!
echo.
pause
exit /b 1
