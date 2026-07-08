@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
echo ============================================================
echo  Job Search Agent - one-time setup
echo ============================================================
echo.
echo Creating virtual environment (.venv)...
python -m venv .venv
call .venv\Scripts\activate.bat
echo.
echo Installing dependencies...
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Installing Playwright browser (for Workday autofill)...
pip install playwright
playwright install chromium
echo.
echo Running preflight check...
python main.py doctor
echo.
echo Setup done. Use Scan.bat to pull jobs, Dashboard.bat to view them.
pause
