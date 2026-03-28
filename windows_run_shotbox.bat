@echo off
setlocal

cd /d "%~dp0" || exit /b 1

if not exist "%CD%\venv\Scripts\python.exe" (
    echo Missing virtual environment. Run windows_install_shotbox_frontend.bat first.
    exit /b 1
)

"%CD%\venv\Scripts\python.exe" "%CD%\main.py"
if errorlevel 1 (
    echo ShotBox exited with an error.
    pause
    exit /b 1
)

exit /b 0
