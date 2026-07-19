@echo off
setlocal

cd /d "%~dp0"

echo ============================================================
echo                 AXILATTICE v2 LOCAL START
echo ============================================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
  set "PYTHON_CMD=py"
) else (
  set "PYTHON_CMD=python"
)

echo Using Python launcher: %PYTHON_CMD%

start "AxiLattice Backend" cmd /k "cd /d ""%~dp0backend"" && %PYTHON_CMD% -m pip install -r requirements.txt && %PYTHON_CMD% -m uvicorn main:app --reload --host 0.0.0.0 --port 8000"
start "AxiLattice Frontend" cmd /k "cd /d ""%~dp0frontend\public"" && %PYTHON_CMD% -m http.server 3000"

timeout /t 2 >nul
start "" "http://localhost:3000"

echo Started both services in separate terminal windows.
echo Backend:  http://localhost:8000
echo Frontend: http://localhost:3000
echo.
echo Close the two service windows to stop everything.
