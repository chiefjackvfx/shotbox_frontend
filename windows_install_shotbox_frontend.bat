@echo off
setlocal

set "REPO_URL=https://github.com/chiefjackvfx/shotbox_frontend.git"
set "DEFAULT_TARGET=%USERPROFILE%\ShotBox\shotbox_frontend"

for %%I in ("%~dp0.") do set "SCRIPT_DIR=%%~fI"

set "PYTHON_CMD="
where py >nul 2>&1
if not errorlevel 1 (
    py -3.11 --version >nul 2>&1 && set "PYTHON_CMD=py -3.11"
    if not defined PYTHON_CMD (
        py -3 --version >nul 2>&1 && set "PYTHON_CMD=py -3"
    )
)

if not defined PYTHON_CMD (
    where python >nul 2>&1 && set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    echo Could not find a usable Python installation.
    exit /b 1
)

where git >nul 2>&1
if errorlevel 1 (
    echo Git is not installed or not on PATH.
    exit /b 1
)

if exist "%SCRIPT_DIR%\main.py" if exist "%SCRIPT_DIR%\.git" (
    set "REPO_DIR=%SCRIPT_DIR%"
) else (
    if "%~1"=="" (
        set "REPO_DIR=%DEFAULT_TARGET%"
    ) else (
        set "REPO_DIR=%~1"
    )
)

if exist "%REPO_DIR%\.git" (
    echo Using existing checkout: %REPO_DIR%
) else (
    if exist "%REPO_DIR%" (
        dir /b "%REPO_DIR%" 2>nul | findstr . >nul
        if not errorlevel 1 (
            echo Target folder exists and is not a git checkout: %REPO_DIR%
            exit /b 1
        )
    )

    for %%P in ("%REPO_DIR%") do set "REPO_PARENT=%%~dpP"
    if not exist "%REPO_PARENT%" mkdir "%REPO_PARENT%"

    git clone "%REPO_URL%" "%REPO_DIR%"
    if errorlevel 1 exit /b 1
)

cd /d "%REPO_DIR%" || exit /b 1

for /f "delims=" %%B in ('git branch --show-current 2^>nul') do set "CURRENT_BRANCH=%%B"
if /I not "%CURRENT_BRANCH%"=="main" (
    echo Expected the published checkout to be on "main". Current branch: %CURRENT_BRANCH%
    exit /b 1
)

for /f "delims=" %%S in ('git status --porcelain') do (
    echo Local changes detected. Commit or discard them before running install/repair.
    exit /b 1
)

git pull --ff-only origin main
if errorlevel 1 exit /b 1

if not exist "%REPO_DIR%\venv\Scripts\python.exe" (
    %PYTHON_CMD% -m venv "%REPO_DIR%\venv"
    if errorlevel 1 exit /b 1
)

call "%REPO_DIR%\venv\Scripts\activate.bat"
if errorlevel 1 exit /b 1

python -m pip install -r "%REPO_DIR%\requirements.txt"
if errorlevel 1 exit /b 1

set /p RUN_NOW=Launch ShotBox now? [Y/N]:
if /I "%RUN_NOW%"=="Y" call "%REPO_DIR%\windows_run_shotbox.bat"

exit /b 0
