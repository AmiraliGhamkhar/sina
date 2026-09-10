# Kill existing instances to prevent file locking
Write-Host "Cleaning up existing SpeakType processes..." -ForegroundColor Cyan
Get-Process SpeakType.App -ErrorAction SilentlyContinue | Stop-Process -Force

# Run the app
Write-Host "Launching SpeakType.App..." -ForegroundColor Green
dotnet run --project src\SpeakType.App\SpeakType.App.csproj
