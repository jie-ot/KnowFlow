@echo off
rem KnowFlow one-click start for Windows: backend 127.0.0.1:8000, frontend 127.0.0.1:3000
rem Written without ( ) blocks around paths, so folders like "KnowFlow-main (1)" still work.
setlocal EnableExtensions
cd /d "%~dp0"
set "ROOT=%CD%"
set "VENV=%ROOT%\backend\.venv"
if not defined PIP_INDEX set "PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple"
if not defined NPM_REGISTRY set "NPM_REGISTRY=https://registry.npmmirror.com"

echo [KnowFlow] Project folder: "%ROOT%"
if exist "%ROOT%\backend\app\main.py" goto have_project
echo [KnowFlow] ERROR: backend\app\main.py not found next to start.bat.
echo            Unzip the whole project first, then run start.bat inside the KnowFlow folder.
goto fail
:have_project

rem ---- find a real Python 3.11+: prefer the py launcher, skip the Microsoft Store alias ----
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto have_python
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto have_python
echo [KnowFlow] ERROR: Python 3.11+ not found.
echo            Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
echo            If typing python opens the Microsoft Store, turn off the python.exe App execution alias in Settings.
goto fail
:have_python
echo [KnowFlow] Python command: %PY%

where node >nul 2>nul
if not errorlevel 1 goto have_node
echo [KnowFlow] ERROR: Node.js not found. Install Node 20+ from https://nodejs.org and open a new window.
goto fail
:have_node

rem ---- backend dependencies ----
if exist "%VENV%\Scripts\uvicorn.exe" goto backend_ready
if not exist "%VENV%" goto make_venv
if exist "%VENV%\Scripts\python.exe" goto install_backend
rem A .venv copied from macOS/Linux has bin\ instead of Scripts\ and cannot be used on Windows.
echo [KnowFlow] Removing a .venv that was not created on Windows ...
rmdir /s /q "%VENV%"
:make_venv
echo [KnowFlow] Creating Python virtual environment ...
%PY% -m venv "%VENV%"
if errorlevel 1 goto fail
if exist "%VENV%\Scripts\python.exe" goto install_backend
echo [KnowFlow] ERROR: the virtual environment was not created at "%VENV%".
goto fail
:install_backend
echo [KnowFlow] Installing backend dependencies from %PIP_INDEX% ...
"%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check -i "%PIP_INDEX%" fastapi "uvicorn[standard]" httpx python-multipart "pypdf[crypto]" python-pptx python-docx markdown-it-py mdit-py-plugins latex2mathml pygments
if errorlevel 1 goto fail
:backend_ready

rem ---- frontend dependencies ----
if exist "%ROOT%\frontend\node_modules\.bin\next.cmd" goto frontend_ready
echo [KnowFlow] Installing frontend dependencies from %NPM_REGISTRY% ...
cd /d "%ROOT%\frontend"
call npm install --no-audit --no-fund --registry="%NPM_REGISTRY%"
if errorlevel 1 goto fail
cd /d "%ROOT%"
:frontend_ready

echo [KnowFlow] Starting backend and frontend in two new windows ...
start "KnowFlow backend" /D "%ROOT%\backend" "%VENV%\Scripts\uvicorn.exe" app.main:app --host 127.0.0.1 --port 8000
start "KnowFlow frontend" /D "%ROOT%\frontend" cmd /k npm run dev

echo [KnowFlow] Waiting for the frontend ...
set /a N=0
:wait
curl -s -o nul http://127.0.0.1:3000 >nul 2>nul
if not errorlevel 1 goto ready
set /a N+=1
if %N% geq 90 goto ready
timeout /t 1 /nobreak >nul
goto wait
:ready
start "" http://127.0.0.1:3000
echo.
echo   KnowFlow is running: http://127.0.0.1:3000
echo   Close the two "KnowFlow" windows to stop it.
echo.
pause
exit /b 0

:fail
echo.
echo [KnowFlow] Start failed. Take a screenshot of this whole window.
pause
exit /b 1
