@echo off
echo Starting Meituan Pharmacy Scraper...

REM Add adb to PATH for this session (if it exists in the delivery package)
set "ADB_DIR=%~dp0adb"
if exist "%ADB_DIR%" (
    set "PATH=%ADB_DIR%;%PATH%"
    echo Added adb to PATH: %ADB_DIR%
)

cd app
start "" "meituan_pharmacy_scraper.exe"

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to start application.
    pause
)
