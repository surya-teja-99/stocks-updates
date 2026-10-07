@echo off
REM ============================================================================
REM  Stocks Updates - one-click launcher for Windows
REM  Save this file inside your "stocks-updates" project folder and double-click
REM  it. It does everything: finds Python, creates the virtual environment,
REM  installs Flask, loads the stock data (first run only), starts the server,
REM  and opens the app in your browser once the server is actually ready.
REM  Safe to double-click again any time.
REM ============================================================================
cd /d "%~dp0"

echo ============================================================
echo  Stocks Updates - starting
echo ============================================================

REM --- Find Python (prefer the py launcher, fall back to python) ---
set "PY=py"
where py >nul 2>nul
if errorlevel 1 (
  set "PY=python"
  where python >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] Python was not found.
    echo Install Python 3 from python.org with "Add python.exe to PATH" ticked,
    echo then double-click this file again.
    pause
    exit /b 1
  )
)
echo Using Python: %PY%

set "VENV_PY=venv\Scripts\python.exe"

REM --- [1/4] Virtual environment ---
if not exist "%VENV_PY%" (
  echo [1/4] Creating virtual environment...
  %PY% -m venv venv
  if errorlevel 1 (
    echo [ERROR] Could not create the virtual environment.
    pause
    exit /b 1
  )
) else (
  echo [1/4] Virtual environment found, skipping.
)

if not exist "%VENV_PY%" (
  echo [ERROR] The virtual environment is incomplete.
  echo Delete the "venv" folder inside your project folder,
  echo then double-click this file again.
  pause
  exit /b 1
)

REM --- [2/4] Dependencies, installed into the virtual environment ---
echo [2/4] Installing dependencies (Flask)...
"%VENV_PY%" -m pip install --quiet -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Could not install Flask. Check your internet connection,
  echo then double-click this file again.
  pause
  exit /b 1
)

REM --- [3/4] First-run data load ---
if not exist stocks.db (
  echo [3/4] First run - loading stock data (takes about a minute)...
  "%VENV_PY%" backend\seed.py
  if errorlevel 1 (
    echo [ERROR] Loading stock data failed.
    pause
    exit /b 1
  )
) else (
  echo [3/4] Database found, skipping data load.
)

REM --- [4/4] Start the server, open the browser once it is ready ---
echo [4/4] Starting the server...
echo.
echo  The app will open at http://localhost:5000 once the server is ready.
echo  Keep this window open while you use the app.
echo  Press Ctrl+C in this window when you are done.
echo.
start "" /min powershell -NoProfile -Command "$t=0; while ($t -lt 120) { try { $r = Invoke-WebRequest -UseBasicParsing 'http://localhost:5000/api/meta' -TimeoutSec 2; if ($r.StatusCode -eq 200) { Start-Process 'http://localhost:5000'; break } } catch { }; Start-Sleep -Seconds 2; $t += 2 }"
"%VENV_PY%" backend\app.py
echo.
echo Server stopped.
pause
