$ErrorActionPreference = "Stop"

Set-Location "$HOME\Music\Music_player"

Write-Host "Pulling latest Music Engine files from GitHub..." -ForegroundColor Cyan

# Make sure we're on the main branch
git checkout main
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Could not switch to main branch." -ForegroundColor Red
    exit 1
}

# Pull newest changes from GitHub
git pull --ff-only origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Could not update from GitHub." -ForegroundColor Red
    Write-Host "Your local repository may have changes that need attention." -ForegroundColor Yellow
    exit 1
}

Write-Host "Latest files pulled successfully." -ForegroundColor Green

# Activate virtual environment
$venvPython = ".\.venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "ERROR: Virtual environment not found at .venv" -ForegroundColor Red
    exit 1
}

Write-Host "Using virtual environment..." -ForegroundColor Cyan

# Clean previous builds
Write-Host "Cleaning previous builds..." -ForegroundColor Cyan

if (Test-Path ".\build") {
    Remove-Item ".\build" -Recurse -Force
}

if (Test-Path ".\dist") {
    Remove-Item ".\dist" -Recurse -Force
}

# Build with PyInstaller
Write-Host "Building Music Engine with PyInstaller..." -ForegroundColor Cyan

& $venvPython -m PyInstaller --clean MusicEngine.spec

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: PyInstaller build failed." -ForegroundColor Red
    exit 1
}

# Remove previous installation
$installPath = "$HOME\.local\opt\MusicEngine"

Write-Host "Removing previous installation..." -ForegroundColor Cyan

if (Test-Path $installPath) {
    Remove-Item $installPath -Recurse -Force
}

# Create installation directory
Write-Host "Creating installation directory..." -ForegroundColor Cyan

New-Item -ItemType Directory -Path $installPath -Force | Out-Null

# Install new build
Write-Host "Installing new build..." -ForegroundColor Cyan

Copy-Item ".\dist\MusicEngine\*" $installPath -Recurse -Force

Write-Host ""
Write-Host "Music Engine rebuilt and installed successfully." -ForegroundColor Green