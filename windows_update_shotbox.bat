@echo off
setlocal

for %%I in ("%~dp0.") do set "REPO_DIR=%%~fI"
set "WAIT_PID=%~1"
set "BRANCH=main"

cd /d "%REPO_DIR%" || exit /b 1

where git >nul 2>&1
if errorlevel 1 (
    echo Git is not installed or not on PATH.
    exit /b 1
)

if not exist "%REPO_DIR%\.git" (
    echo This folder is not a git checkout.
    exit /b 1
)

if not exist "%REPO_DIR%\venv\Scripts\python.exe" (
    echo Missing virtual environment at "%REPO_DIR%\venv".
    exit /b 1
)

for /f "delims=" %%B in ('git branch --show-current 2^>nul') do set "CURRENT_BRANCH=%%B"
if /I not "%CURRENT_BRANCH%"=="%BRANCH%" (
    echo Self-update only supports the "%BRANCH%" branch. Current branch: %CURRENT_BRANCH%
    exit /b 1
)

if not "%WAIT_PID%"=="" (
    :wait_loop
    tasklist /FI "PID eq %WAIT_PID%" | find "%WAIT_PID%" >nul 2>&1
    if not errorlevel 1 (
        timeout /t 1 /nobreak >nul
        goto wait_loop
    )
)

for /f "delims=" %%S in ('git status --porcelain') do (
    echo Local changes detected. Commit or discard them before updating.
    exit /b 1
)

git pull --ff-only origin %BRANCH%
if errorlevel 1 exit /b 1

call "%REPO_DIR%\venv\Scripts\activate.bat"
if errorlevel 1 exit /b 1

python -m pip install -r "%REPO_DIR%\requirements.txt"
if errorlevel 1 exit /b 1

start "" /D "%REPO_DIR%" "%REPO_DIR%\venv\Scripts\python.exe" "%REPO_DIR%\main.py"
exit /b 0
