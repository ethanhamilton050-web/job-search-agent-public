@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
title Job Search Agent

REM ---- First run: build the environment automatically -----------------------
if not exist ".venv\Scripts\python.exe" (
    echo ============================================================
    echo   First-time setup - this runs once, give it a few minutes
    echo ============================================================
    python -m venv .venv
    call ".venv\Scripts\activate.bat"
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    echo.
    echo Setup complete.
    timeout /t 2 >nul
) else (
    call ".venv\Scripts\activate.bat"
)

:menu
cls
echo ============================================================
echo                    JOB SEARCH AGENT
echo ============================================================
echo.
echo   1.  Scan for new jobs  (then show your top matches)
echo   2.  Open the dashboard (web view in your browser)
echo   3.  Show top matches
echo   4.  Tailor a resume for a job
echo   5.  Finish a tailored draft  (validate + make Word/PDF)
echo   6.  Update a job's status
echo   7.  Export the tracker to Excel
echo   8.  Apply via Workday (autofill, host only)
echo   9.  Health check
echo   0.  Exit
echo.
set /p opt=Pick a number, then press Enter:

if "%opt%"=="1" goto scan
if "%opt%"=="2" goto dashboard
if "%opt%"=="3" goto list
if "%opt%"=="4" goto tailor
if "%opt%"=="5" goto render
if "%opt%"=="6" goto status
if "%opt%"=="7" goto export
if "%opt%"=="8" goto apply
if "%opt%"=="9" goto doctor
if "%opt%"=="0" goto end
goto menu

:scan
echo.
python main.py scan
echo.
echo --- Your top matches ---
python main.py list --limit 30
echo.
pause
goto menu

:dashboard
echo Opening the dashboard in a new window. Close that window to stop it.
start "" http://127.0.0.1:5000
start "Job Dashboard (close this window to stop)" "%~dp0.venv\Scripts\python.exe" "%~dp0dashboard.py"
timeout /t 2 >nul
goto menu

:list
echo.
python main.py list --limit 30
echo.
pause
goto menu

:tailor
echo.
python main.py list --limit 15
echo.
set /p id=Enter the job id to tailor (from the list above):
if "%id%"=="" goto menu
python main.py tailor %id%
echo.
echo ------------------------------------------------------------
echo  NEXT STEPS:
echo   1) Copy the brief above into a Claude Code session.
echo   2) Save Claude's reply to:
echo        output\tailored\%id%_draft.txt
echo   3) Come back here and choose option 5 to finish it.
echo ------------------------------------------------------------
echo.
pause
goto menu

:render
echo.
set /p id=Enter the job id whose draft you saved:
if "%id%"=="" goto menu
python main.py render %id%
echo.
pause
goto menu

:status
echo.
set /p id=Enter the job id:
if "%id%"=="" goto menu
echo Statuses: found  tailored  applied  interview  offer  rejected
set /p st=Enter the new status:
if "%st%"=="" goto menu
python main.py status %id% %st%
echo.
pause
goto menu

:export
echo.
python main.py export
echo.
pause
goto menu

:apply
echo.
set /p id=Enter the job id to apply to:
if "%id%"=="" goto menu
python -c "import playwright" 2>nul
if errorlevel 1 goto apply_install
goto apply_run

:apply_install
echo Workday autofill needs Playwright, which isn't installed yet.
set /p go=Install it now? This downloads a browser, ~150 MB. [y/N]
if /i not "%go%"=="y" goto menu
pip install playwright
playwright install chromium

:apply_run
python main.py apply %id%
echo.
pause
goto menu

:doctor
echo.
python main.py doctor
echo.
pause
goto menu

:end
endlocal
