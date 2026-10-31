@echo off
rem Double-click opens the demo website; use --dev for frontend development.
cd /d "%~dp0"
set "KNOWFLOW_ARGS=%*"
if exist "backend\.venv\Scripts\python.exe" (
  "backend\.venv\Scripts\python.exe" run.py %KNOWFLOW_ARGS%
  goto end
)
py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 (
  py -3 run.py %KNOWFLOW_ARGS%
  goto end
)
python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 (
  python run.py %KNOWFLOW_ARGS%
  goto end
)
echo [KnowFlow] Python 3.11+ not found. Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
:end
pause
