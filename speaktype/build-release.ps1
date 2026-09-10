# Build script for creating a release build of SpeakType
# This creates a self-contained deployment ready for the installer

param(
    [string]$Configuration = "Release",
    [string]$OutputDir = "installer\staging"
)

Write-Host "Building SpeakType Release..." -ForegroundColor Cyan

# Clean previous build
if (Test-Path $OutputDir) {
    Remove-Item -Path $OutputDir -Recurse -Force
}

# Build self-contained release
Write-Host "Publishing self-contained build..." -ForegroundColor Yellow
dotnet publish src\SpeakType.App\SpeakType.App.csproj `
    --configuration $Configuration `
    --runtime win-x64 `
    --self-contained true `
    --output $OutputDir `
    -p:PublishSingleFile=false `
    -p:PublishReadyToRun=true `
    -p:IncludeNativeLibrariesForSelfExtract=true

if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed!" -ForegroundColor Red
    exit 1
}

Write-Host "Build completed successfully!" -ForegroundColor Green
Write-Host "Output directory: $OutputDir" -ForegroundColor Cyan

# Build Installer
$IsccPath = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if (Test-Path $IsccPath) {
    Write-Host "Compiling Installer..." -ForegroundColor Yellow
    & $IsccPath "installer\SpeakType.iss"
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Installer compilation failed!" -ForegroundColor Red
        exit 1
    }
    
    Write-Host "Installer created successfully!" -ForegroundColor Green
    Write-Host "Installer Path: installer\Output\SpeakTypeSetup.exe" -ForegroundColor Cyan
} else {
    Write-Host "Inno Setup compiler (ISCC.exe) not found at default location." -ForegroundColor Red
    Write-Host "Please install Inno Setup 6 or update the path in this script." -ForegroundColor Yellow
}
