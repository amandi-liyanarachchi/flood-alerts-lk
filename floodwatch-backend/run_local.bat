@echo off
REM Flood Alerts LK -- run the backend on a laptop. No Docker, no PostgreSQL.
REM
REM   run_local.bat              start (seeds the database on first run)
REM   run_local.bat --reset      wipe the database and re-seed, then start
REM   run_local.bat --no-seed    start against whatever is already there
REM   run_local.bat --live       enable live ingestion from the Irrigation Dept
REM
REM Windows. macOS and Linux: use run_local.sh.

setlocal enabledelayedexpansion
cd /d "%~dp0"

if "%PORT%"=="" set PORT=8000
set RESET=0
set SEED=1
set LIVE=0

:parseargs
if "%~1"=="" goto doneargs
if /i "%~1"=="--reset"   set RESET=1
if /i "%~1"=="--no-seed" set SEED=0
if /i "%~1"=="--live"    set LIVE=1
shift
goto parseargs
:doneargs

REM --- python ---------------------------------------------------------------

set PY=
where py >nul 2>&1 && set PY=py -3
if "%PY%"=="" (
  where python >nul 2>&1 && set PY=python
)
if "%PY%"=="" (
  echo ERROR: Python 3.10 or newer is required and was not found.
  echo Install it from https://www.python.org/downloads/
  echo IMPORTANT: tick "Add Python to PATH" in the installer.
  pause
  exit /b 1
)
%PY% --version

REM --- virtual environment --------------------------------------------------

if not exist .venv (
  echo Creating virtual environment ^(.venv^)...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo ERROR: could not create the virtual environment.
    pause
    exit /b 1
  )
)

call .venv\Scripts\activate.bat

if not exist .venv\.installed (
  echo Installing dependencies ^(once, about a minute^)...
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -r requirements-local.txt
  if errorlevel 1 (
    echo ERROR: dependency installation failed. See the messages above.
    pause
    exit /b 1
  )
  type nul > .venv\.installed
)

REM --- configuration --------------------------------------------------------

if "%DATABASE_URL%"=="" set DATABASE_URL=sqlite:///./floodwatch.db
if "%JWT_SECRET%"==""   set JWT_SECRET=local-demo-secret-not-for-deployment
if "%ADMIN_TOKEN%"==""  set ADMIN_TOKEN=demo-admin
if "%ENVIRONMENT%"==""  set ENVIRONMENT=development

if "%LIVE%"=="1" (
  set INGEST_ENABLED=true
  echo Live ingestion ON -- the server will poll the Irrigation Department every 10 minutes.
) else (
  REM Off by default for a presentation: no dependence on the room's wifi and no
  REM error noise in the terminal you are projecting. The dashboard's
  REM "Pull readings" button fetches live data on demand.
  set INGEST_ENABLED=false
)

REM --- database -------------------------------------------------------------

if "%RESET%"=="1" (
  echo Resetting the database...
  if exist floodwatch.db del /q floodwatch.db
)

if "%SEED%"=="1" (
  if not exist floodwatch.db (
    echo Seeding demo data ^(18 participants, 3 days of pings, one river in flood^)...
    python -m scripts.seed_demo --flood
  )
)

REM --- go -------------------------------------------------------------------

echo.
echo Admin token: %ADMIN_TOKEN%
echo Starting on port %PORT%.  Press Ctrl-C to stop.
echo.

python -m uvicorn app.main:app --host 0.0.0.0 --port %PORT%

pause
