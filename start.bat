@echo off
rem Windows start: same as "python run.py". Double-click opens the browser; "start.bat --app" opens a desktop window.
cd /d "%~dp0"
py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 (
  py -3 run.py %*
  goto end
)
python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 (
  python run.py %*
  goto end
)
echo [KnowFlow] Python 3.11+ not found. Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
:end
pause
