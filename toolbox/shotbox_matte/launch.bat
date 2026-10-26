@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch_windows.ps1" %*
set "SHOTBOX_EXIT=%ERRORLEVEL%"
if not "%SHOTBOX_EXIT%"=="0" pause
exit /b %SHOTBOX_EXIT%
