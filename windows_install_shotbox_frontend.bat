@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "REPO_URL=https://github.com/chiefjackvfx/shotbox_frontend.git"
set "PUBLISHED_BRANCH=main"
set "LAUNCH_DIR=%CD%"

for %%I in ("%~dp0.") do set "SCRIPT_DIR=%%~fI"

if /I "%~1"=="/?" goto :usage
if /I "%~1"=="--help" goto :usage

echo [ShotBox] Starting frontend installer...
echo [ShotBox] Launch folder: "%LAUNCH_DIR%"
echo [ShotBox] Script folder: "%SCRIPT_DIR%"
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

set "STEP=Checking Python"
echo [Step] !STEP!
set "PYTHON_CMD="

where py >nul 2>&1
if not errorlevel 1 (
    py -3.11 --version >nul 2>&1 && set "PYTHON_CMD=py -3.11"
    if not defined PYTHON_CMD (
        py -3 --version >nul 2>&1 && set "PYTHON_CMD=py -3"
    )
)

if not defined PYTHON_CMD (
    where python >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    set "FAIL_MSG=Could not find a usable Python installation via 'py' or 'python'."
    goto :fail
)

echo [Info] Selected Python command: !PYTHON_CMD!
echo.

set "MODE="
set "REPO_DIR="

if exist "%SCRIPT_DIR%\main.py" if exist "%SCRIPT_DIR%\.git" (
    set "MODE=existing checkout"
    set "REPO_DIR=%SCRIPT_DIR%"
) else (
    if not "%~1"=="" (
        set "REPO_DIR=%~1"
    ) else (
        set "REPO_DIR=%LAUNCH_DIR%\shotbox_frontend"
    )

    if exist "!REPO_DIR!\.git" (
        set "MODE=existing checkout"
    ) else (
        set "MODE=standalone bootstrap"
    )
)

echo [Info] Installer mode: !MODE!
echo [Info] Repo target: "!REPO_DIR!"
echo.

if /I "!MODE!"=="standalone bootstrap" goto :bootstrap
goto :repair_enter

:bootstrap
set "STEP=Preparing bootstrap target"
echo [Step] !STEP!

if exist "!REPO_DIR!\.git" (
    echo [Info] Existing git checkout found at "!REPO_DIR!".
    echo.
    goto :repair_enter
)

if exist "!REPO_DIR!" (
    dir /b "!REPO_DIR!" 2>nul | findstr . >nul
    if not errorlevel 1 (
        set "FAIL_MSG=Target folder exists and is not an empty git checkout: !REPO_DIR!"
        goto :fail
    )
)

for %%P in ("!REPO_DIR!") do set "REPO_PARENT=%%~dpP"
if not exist "!REPO_PARENT!" mkdir "!REPO_PARENT!" 2>nul
if not exist "!REPO_PARENT!" (
    set "FAIL_MSG=Could not create parent folder: !REPO_PARENT!"
    goto :fail
)

echo [Info] Bootstrap target is ready.
echo.

set "STEP=Cloning frontend repo"
echo [Step] !STEP!
git clone "%REPO_URL%" "!REPO_DIR!"
if errorlevel 1 (
    set "FAIL_MSG=Git clone failed. Check network access and GitHub permissions."
    goto :fail
)
echo [Info] Clone completed.
echo.

:repair_enter
set "STEP=Opening repo"
echo [Step] !STEP!
cd /d "!REPO_DIR!"
if errorlevel 1 (
    set "FAIL_MSG=Could not change into repo target: !REPO_DIR!"
    goto :fail
)
echo [Info] Working in "!REPO_DIR!".
echo.

set "STEP=Checking published branch"
echo [Step] !STEP!
set "CURRENT_BRANCH="
for /f "delims=" %%B in ('git branch --show-current 2^>nul') do set "CURRENT_BRANCH=%%B"

if not defined CURRENT_BRANCH (
    set "FAIL_MSG=Could not determine the current git branch."
    goto :fail
)

if /I not "!CURRENT_BRANCH!"=="%PUBLISHED_BRANCH%" (
    set "FAIL_MSG=Expected the published checkout to be on '%PUBLISHED_BRANCH%'. Current branch: !CURRENT_BRANCH!"
    goto :fail
)

echo [Info] Current branch: !CURRENT_BRANCH!
echo.

set "STEP=Checking working tree"
echo [Step] !STEP!
for /f "delims=" %%S in ('git status --porcelain') do (
    set "FAIL_MSG=Local changes detected in !REPO_DIR!. Commit or discard them before install or repair."
    goto :fail
)
echo [Info] Working tree is clean.
echo.

set "STEP=Pulling latest frontend code"
echo [Step] !STEP!
git pull --ff-only origin %PUBLISHED_BRANCH%
if errorlevel 1 (
    set "FAIL_MSG=Git pull failed. Resolve the repository state manually and retry."
    goto :fail
)
echo [Info] Repo is up to date on origin/%PUBLISHED_BRANCH%.
echo.

set "STEP=Preparing virtual environment"
echo [Step] !STEP!
if not exist "!REPO_DIR!\venv\Scripts\python.exe" (
    echo [Info] Creating virtual environment at "!REPO_DIR!\venv"
    !PYTHON_CMD! -m venv "!REPO_DIR!\venv"
    if errorlevel 1 (
        set "FAIL_MSG=Virtual environment creation failed."
        goto :fail
    )
) else (
    echo [Info] Reusing existing virtual environment at "!REPO_DIR!\venv"
)
echo.

set "STEP=Installing Python requirements"
echo [Step] !STEP!
call "!REPO_DIR!\venv\Scripts\activate.bat"
if errorlevel 1 (
    set "FAIL_MSG=Could not activate the virtual environment."
    goto :fail
)

python -m pip install -r "!REPO_DIR!\requirements.txt"
if errorlevel 1 (
    set "FAIL_MSG=Requirements install failed. Review the pip output above."
    goto :fail
)
echo [Info] Requirements install completed.
echo.

set "STEP=Final run prompt"
echo [Step] !STEP!
echo [Info] Run script: "!REPO_DIR!\windows_run_shotbox.bat"
set /p RUN_NOW=Launch ShotBox now? [Y/N]: 

if /I "!RUN_NOW!"=="Y" (
    call "!REPO_DIR!\windows_run_shotbox.bat"
    if errorlevel 1 (
        set "FAIL_MSG=ShotBox failed to launch from windows_run_shotbox.bat."
        goto :fail
    )
) else (
    echo [Info] Installation completed. Launch later with:
    echo        "!REPO_DIR!\windows_run_shotbox.bat"
)

echo.
echo [Success] ShotBox frontend install or repair completed successfully.
exit /b 0

:usage
echo Usage: windows_install_shotbox_frontend.bat [TARGET_FOLDER]
echo.
echo If this script is run inside an existing ShotBox frontend checkout,
echo it repairs and updates that checkout in place.
echo.
echo If this script is run from a normal folder without a checkout:
echo   - with no argument, it clones into:
echo     "%CD%\shotbox_frontend"
echo   - with a target argument, it clones into that path instead
echo.
echo Examples:
echo   windows_install_shotbox_frontend.bat
echo   windows_install_shotbox_frontend.bat "C:\Apps\ShotBox\shotbox_frontend"
exit /b 0

:fail
echo.
echo [Error] Step failed: !STEP!
echo [Error] !FAIL_MSG!
echo.
pause
exit /b 1
