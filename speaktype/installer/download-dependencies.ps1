# Download dependencies for SpeakType installer
param(
    [string]$InstallDir
)

$ErrorActionPreference = "Stop"
$logFile = "$env:TEMP\speaktype_install_log.txt"

Start-Transcript -Path $logFile -Append

if (-not $InstallDir) {
    $InstallDir = $PSScriptRoot
}

Write-Host "Install Dir: $InstallDir"

$toolsDir = "$InstallDir\tools\whisper"
$modelsDir = "$InstallDir\models"

if (-not (Test-Path $toolsDir)) { New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null }
if (-not (Test-Path $modelsDir)) { New-Item -ItemType Directory -Force -Path $modelsDir | Out-Null }

Write-Host "Downloading whisper.cpp..."
$whisperUrl = "https://github.com/ggerganov/whisper.cpp/releases/download/v1.5.4/whisper-bin-x64.zip"
$whisperZip = "$env:TEMP\whisper-bin-x64.zip"

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $whisperUrl -OutFile $whisperZip -UseBasicParsing
    Expand-Archive -Path $whisperZip -DestinationPath $toolsDir -Force
    Remove-Item $whisperZip -Force
    
    if (Test-Path "$toolsDir\main.exe") {
        Copy-Item "$toolsDir\main.exe" "$toolsDir\whisper-cli.exe" -Force
    }
    Write-Host "Whisper downloaded."
} catch {
    Write-Host "Error downloading whisper: $_"
}

Write-Host "Downloading model..."
$modelUrl = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin"
$modelPath = "$modelsDir\ggml-base.en.bin"

try {
    Invoke-WebRequest -Uri $modelUrl -OutFile $modelPath -UseBasicParsing
    Write-Host "Model downloaded."
} catch {
    Write-Host "Error downloading model: $_"
}

Stop-Transcript
Start-Sleep -Seconds 5
