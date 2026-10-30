@echo off
cd /d "%~dp0"
if exist "backend\.venv\Scripts\python.exe" (
  "backend\.venv\Scripts\python.exe" -X utf8 demo\run_demo.py %*
  goto end
)
py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 (
  py -3 -X utf8 demo\run_demo.py %*
  goto end
)
python -X utf8 demo\run_demo.py %*
:end
pause
