@echo off
setlocal

echo ==========================================
echo      Meituan Pharmacy Scraper Build
echo ==========================================

REM 1. Clean previous build
echo [1/5] Cleaning previous build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist *.spec del *.spec

REM 2. Run PyInstaller
echo [2/5] Running PyInstaller...
python -m PyInstaller --noconfirm --onedir --windowed --name "meituan_pharmacy_scraper" ^
    --icon "NONE" ^
    --collect-all "uiautomator2" ^
    --clean ^
    main.py

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] PyInstaller failed!
    REM pause
    exit /b %ERRORLEVEL%
)

REM 3. Prepare delivery directory
echo [3/5] Preparing delivery directory...
set "DELIVERY_DIR=meituan_pharmacy_demo"
if exist "%DELIVERY_DIR%" rmdir /s /q "%DELIVERY_DIR%"
mkdir "%DELIVERY_DIR%"
mkdir "%DELIVERY_DIR%\app"

REM 4. Copy files
echo [4/5] Copying files...

REM Copy exe folder content
xcopy /E /I /Y "dist\meituan_pharmacy_scraper" "%DELIVERY_DIR%\app"

REM Copy config.json
if exist config.json (
    copy /Y config.json "%DELIVERY_DIR%\app\"
) else (
    echo [WARNING] config.json not found!
)

REM Copy adb folder if exists
if exist adb (
    echo Copying adb folder...
    xcopy /E /I /Y "adb" "%DELIVERY_DIR%\adb"
) else (
    echo [WARNING] adb folder not found in project root. Please manually add 'adb' folder to the delivery package.
)

REM Copy run_check.bat and README
copy /Y run_check.bat "%DELIVERY_DIR%\"
copy /Y README_运行说明.txt "%DELIVERY_DIR%\"

REM 5. Cleanup
echo [5/5] Cleaning up...
REM Optional: keep build/dist for debugging if needed, or remove them
REM rmdir /s /q build
REM rmdir /s /q dist

echo.
echo ==========================================
echo      Build Complete!
echo ==========================================
echo Output directory: %DELIVERY_DIR%
echo.
REM pause
