@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
call .venv\Scripts\activate.bat
echo Starting dashboard at http://127.0.0.1:5000  (close this window to stop)
start "" http://127.0.0.1:5000
python dashboard.py
pause
