@echo off
cd /d "%~dp0"
call venv\Scripts\activate
python pyqt_frontend\main.py
pause