@echo off
rem Builds dist\HomsScanner\HomsScanner.exe and (if Inno Setup is installed) dist\HomsScanner-Setup.exe
rem Run this on a Windows PC that has Python 3.10+ (employees do NOT need Python, only this build PC).
cd /d "%~dp0"

set "PY=py -3"
where py >nul 2>nul
if errorlevel 1 set "PY=python"

if not exist ".venv-build\Scripts\python.exe" (
    %PY% -m venv .venv-build || goto :fail
)
set "VPY=.venv-build\Scripts\python.exe"
"%VPY%" -m pip install -q -r requirements.txt -r requirements-twain.txt -r requirements-build.txt || goto :fail

"%VPY%" packaging\make_icon.py || goto :fail
"%VPY%" -m PyInstaller --noconfirm --clean packaging\HomsScanner.spec || goto :fail

where iscc >nul 2>nul
if errorlevel 1 (
    if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" (set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe") else if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" (set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe")
) else (
    set "ISCC=iscc"
)
if not exist release mkdir release
if defined ISCC (
    "%ISCC%" packaging\HomsScanner.iss || goto :fail
    echo.
    echo DONE: dist\HomsScanner-Setup.exe  ^(give this single file to the employees^)
    powershell -NoProfile -Command "Compress-Archive -Force -Path dist\HomsScanner-Setup.exe -DestinationPath release\homeScanner1.1.0.zip" || goto :fail
    echo RELEASE ZIP: release\homeScanner1.1.0.zip  ^(upload it to GitHub Releases^)
) else (
    echo.
    echo Built dist\HomsScanner\HomsScanner.exe. Install Inno Setup 6 ^(jrsoftware.org^) and run again to get the Setup file.
)
pause
exit /b 0

:fail
echo.
echo Build failed. Read the messages above.
pause
exit /b 1
