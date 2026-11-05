@echo off
rem Developer shortcut: runs the background service (tray + /settings) without building the exe.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call run.bat --help >nul 2>nul
".venv\Scripts\python.exe" service_main.py %*
