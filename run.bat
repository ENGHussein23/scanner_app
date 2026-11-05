@echo off
cd /d "%~dp0"

set "PY=py -3"
where py >nul 2>nul
if errorlevel 1 set "PY=python"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment, first run only...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo.
        echo Python was not found. Install Python 3.10+ from python.org and tick "Add python.exe to PATH".
        pause
        exit /b 1
    )
)

rem Install packages ONLY when requirements.txt is new or changed (no internet needed otherwise).
fc /b requirements.txt ".venv\requirements.installed" >nul 2>nul
if errorlevel 1 (
    echo Installing packages ^(first run or requirements changed^)...
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Package install failed. Check your internet connection and try again.
        pause
        exit /b 1
    )
    copy /y requirements.txt ".venv\requirements.installed" >nul
)

rem TWAIN support (optional): tried once per requirements-twain.txt version, never blocks start.
fc /b requirements-twain.txt ".venv\requirements-twain.tried" >nul 2>nul
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install -q -r requirements-twain.txt >nul 2>nul
    copy /y requirements-twain.txt ".venv\requirements-twain.tried" >nul
)

".venv\Scripts\python.exe" app.py %*
pause
