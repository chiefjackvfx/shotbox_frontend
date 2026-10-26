@echo off
setlocal EnableExtensions

cd /d "%~dp0" || exit /b 1

if not exist "%CD%\venv\Scripts\python.exe" (
    echo Missing virtual environment. Run windows_install_shotbox_frontend.bat first.
    pause
    exit /b 1
)

"%CD%\venv\Scripts\python.exe" -u -X faulthandler "%CD%\main.py"
set "SHOTBOX_EXIT_CODE=%ERRORLEVEL%"
if not "%SHOTBOX_EXIT_CODE%"=="0" (
    echo ShotBox exited with code %SHOTBOX_EXIT_CODE%.
    echo Copy the error output above when reporting this failure.
    pause
    exit /b %SHOTBOX_EXIT_CODE%
)

exit /b 0
