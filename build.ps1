Write-Host "=========================================="
Write-Host "     Meituan Pharmacy Scraper Build"
Write-Host "=========================================="

# 1. Clean previous build
Write-Host "[1/5] Cleaning previous build..."
if (Test-Path "build") { Remove-Item -Path "build" -Recurse -Force }
if (Test-Path "dist") { Remove-Item -Path "dist" -Recurse -Force }
if (Test-Path "*.spec") { Remove-Item -Path "*.spec" -Force }

# 2. Run PyInstaller
Write-Host "[2/5] Running PyInstaller..."
python -m PyInstaller --noconfirm --onedir --windowed --name "meituan_pharmacy_scraper" `
    --icon "NONE" `
    --collect-all "uiautomator2" `
    --clean `
    main.py

if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] PyInstaller failed!" -ForegroundColor Red
    Read-Host "Press Enter to exit..."
    exit $LASTEXITCODE
}

# 3. Prepare delivery directory
Write-Host "[3/5] Preparing delivery directory..."
$DELIVERY_DIR = "meituan_pharmacy_demo"
if (Test-Path $DELIVERY_DIR) { Remove-Item -Path $DELIVERY_DIR -Recurse -Force }
New-Item -ItemType Directory -Path "$DELIVERY_DIR\app" -Force | Out-Null

# 4. Copy files
Write-Host "[4/5] Copying files..."

# Copy exe folder content
Copy-Item -Path "dist\meituan_pharmacy_scraper\*" -Destination "$DELIVERY_DIR\app" -Recurse -Force

# Copy config.json
if (Test-Path "config.json") {
    Copy-Item -Path "config.json" -Destination "$DELIVERY_DIR\app\" -Force
} else {
    Write-Host "[WARNING] config.json not found!" -ForegroundColor Yellow
}

# Copy adb folder if exists
if (Test-Path "adb") {
    Write-Host "Copying adb folder..."
    Copy-Item -Path "adb" -Destination "$DELIVERY_DIR\adb" -Recurse -Force
} else {
    Write-Host "[WARNING] adb folder not found in project root. Please manually add 'adb' folder to the delivery package." -ForegroundColor Yellow
}

# Copy run_check.bat and README
Copy-Item -Path "run_check.bat" -Destination "$DELIVERY_DIR\" -Force
Copy-Item -Path "README_运行说明.txt" -Destination "$DELIVERY_DIR\" -Force

# 5. Cleanup
Write-Host "[5/5] Cleaning up..."
# Optional: keep build/dist for debugging if needed, or remove them
# Remove-Item -Path "build" -Recurse -Force
# Remove-Item -Path "dist" -Recurse -Force

Write-Host ""
Write-Host "=========================================="
Write-Host "     Build Complete!"
Write-Host "=========================================="
Write-Host "Output directory: $DELIVERY_DIR"
Write-Host ""
Read-Host "Press Enter to finish..."
