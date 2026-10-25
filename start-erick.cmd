@echo off
rem Campus Rick: one-step local start on Windows.
rem Starts PostGIS (Docker), applies migrations, loads campus data and demo accounts, then runs the server.
setlocal
cd /d "%~dp0"
set PY=.venv313\Scripts\python.exe
if not exist %PY% (
  echo Creating Python 3.13 virtual environment...
  py -3.13 -m venv .venv313 || goto :error
  %PY% -m pip install -r backend\requirements.txt || goto :error
)
docker compose up -d --wait database || (
  echo PostGIS could not start. Start Docker Desktop, or set DATABASE_URL to SQLite in backend\.env.
  goto :error
)
%PY% -m backend.manage bootstrap-dev || goto :error
echo.
echo   Passenger: http://127.0.0.1:8091/passenger/
echo   Driver:    http://127.0.0.1:8091/driver/
echo   Admin:     http://127.0.0.1:8091/admin/
echo.
%PY% run-local.py
goto :eof
:error
echo Startup failed.
pause
exit /b 1
