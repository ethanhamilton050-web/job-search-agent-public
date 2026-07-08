@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
call .venv\Scripts\activate.bat
echo Scanning configured job boards...
python main.py scan
echo.
echo Top matches:
python main.py list --limit 30
echo.
pause
