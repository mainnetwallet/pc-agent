@echo off
setlocal
cd /d "%~dp0"
title PC Agent

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python 3.10+ not found. Install it from https://www.python.org/downloads/
    pause & exit /b 1
)
where npm >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Node.js 18+ not found. Install it from https://nodejs.org/
    pause & exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [setup] Creating virtual environment...
    python -m venv .venv || goto :fail
)

if not exist ".env" (
    echo [setup] Creating .env from .env.example - edit it to add your tokens.
    copy /y ".env.example" ".env" >nul
)

if not exist ".venv\.deps_installed" (
    echo [setup] Installing Python packages ^(first run only^)...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
    ".venv\Scripts\python.exe" -m playwright install chromium
    echo done> ".venv\.deps_installed"
)

if not exist "web\node_modules" (
    echo [setup] Installing web packages ^(first run only^)...
    pushd web
    call npm install || (popd & goto :fail)
    popd
)

".venv\Scripts\python.exe" run.py %*
goto :eof

:fail
echo.
echo [ERROR] Setup failed. See messages above.
pause
exit /b 1
