#requires -Version 5.1
# setup.ps1 — llm-gateway one-shot setup (Windows PowerShell 5.1+). Checks Docker,
# generates .env from .env.example, probes local AI servers, starts the stack and
# waits for health. Idempotent: never touches .env.
# Usage: .\setup.ps1 [-SkipStart] [-NonInteractive]

param(
    [switch]$SkipStart,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"

$RepoRoot = $PSScriptRoot
$EnvFile = Join-Path $RepoRoot ".env"
$EnvExample = Join-Path $RepoRoot ".env.example"

# --- helpers ---
function Write-Step { param([string]$Msg) Write-Host "`n==> $Msg" -ForegroundColor Cyan }
function Write-Info { param([string]$Msg) Write-Host "  $Msg" -ForegroundColor Gray }
function Write-Ok   { param([string]$Msg) Write-Host "  $Msg" -ForegroundColor Green }
function Write-Fail { param([string]$Msg) Write-Host "  $Msg" -ForegroundColor Red }

# Native-command check: $true on exit 0 (safe under EAP=Stop on PS5.1 AND PS7.3+).
function Test-Command {
    param([scriptblock]$Cmd)
    try { & $Cmd 2>$null | Out-Null; return ($LASTEXITCODE -eq 0) } catch { return $false }
}

# Random alphanumeric token. Get-Random -Count can't exceed the 62-char pool, so
# loop (repeats allowed = more entropy); guid fallback if that ever fails.
function New-RandomToken {
    param([int]$Length)
    try {
        $pool = (48..57) + (65..90) + (97..122)   # 0-9 A-Z a-z
        $out = New-Object System.Text.StringBuilder
        while ($out.Length -lt $Length) {
            $take = [Math]::Min($Length - $out.Length, 62)
            foreach ($code in ($pool | Get-Random -Count $take)) { [void]$out.Append([char]$code) }
        }
        return $out.ToString()
    } catch {
        $fallback = [guid]::NewGuid().ToString().Replace("-", "")
        while ($fallback.Length -lt $Length) { $fallback += [guid]::NewGuid().ToString().Replace("-", "") }
        return $fallback.Substring(0, $Length)
    }
}

# Replace the KEY= line in a $Lines array (append if missing). The comma
# prevents PowerShell from unrolling the returned array into its elements.
function Set-EnvLine {
    param([string[]]$Lines, [string]$Key, [string]$Value)
    $re = "^$([regex]::Escape($Key))="
    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i] -match $re) { $Lines[$i] = "$Key=$Value"; return ,$Lines }
    }
    return ,($Lines + "$Key=$Value")
}

# Quick TCP probe (bounded ~8s) for host:port. Report-only — never fatal.
function Test-Port {
    param([string]$HostName, [int]$Port)
    try {
        $job = Start-Job -ScriptBlock { param($h, $p) Test-NetConnection -ComputerName $h -Port $p -InformationLevel Quiet -WarningAction SilentlyContinue } -ArgumentList $HostName, $Port
        $null = Wait-Job $job -Timeout 8
        $result = ($job.State -eq "Completed") -and (Receive-Job $job)
        Remove-Job $job -Force
        return [bool]$result
    } catch { return $false }
}

# --- Step 1 — prerequisites ---
Write-Step "Step 1/5 — Checking prerequisites"
if (-not (Test-Command { docker --version })) {
    Write-Fail "Docker not found. Install Docker Desktop (https://www.docker.com/products/docker-desktop/) and re-run."; exit 1
}
if (Test-Command { docker compose version }) {
    $ComposeExec = @("docker", "compose")
} elseif (Test-Command { docker-compose --version }) {
    $ComposeExec = @("docker-compose")
} else {
    Write-Fail "Docker Compose not found. Install Docker Desktop (includes Compose) or the compose plugin and re-run."; exit 1
}
if (-not (Test-Command { docker info })) {
    Write-Fail "Docker is installed but the daemon is not running. Start Docker Desktop and re-run."; exit 1
}
Write-Ok "Docker + Compose OK."

# --- Step 2 — .env ---
Write-Step "Step 2/5 — Environment (.env)"

# Compose mounts a Docker secret file even when the key itself is optional (S1);
# make sure a fresh clone has the file. Never touch an existing one.
$secretsFile = Join-Path $RepoRoot "secrets\openrouter_api_key.txt"
if (-not (Test-Path $secretsFile)) {
    New-Item -ItemType Directory -Path (Split-Path $secretsFile) -Force | Out-Null
    New-Item -ItemType File -Path $secretsFile -Force | Out-Null
    Write-Info "Created secrets/openrouter_api_key.txt (empty = no OpenRouter key; add one later if you like)."
}

if (Test-Path $EnvFile) {
    # Idempotency: never clobber an existing .env.
    Write-Info "Using existing .env (leaving unchanged)."
} else {
    if (-not (Test-Path $EnvExample)) {
        Write-Fail ".env.example not found next to setup.ps1 — is this the llm-gateway repo root?"; exit 1
    }
    $envGenerated = $true; $lines = Get-Content $EnvExample

    # Generated secrets (64-char JWT secret, 24-char DB/Grafana passwords):
    $secretKey = New-RandomToken 64; $pgPassword = New-RandomToken 24; $grafanaPw = New-RandomToken 24
    $lines = Set-EnvLine $lines "SECRET_KEY" $secretKey
    $lines = Set-EnvLine $lines "POSTGRES_PASSWORD" $pgPassword
    # User/db must match the POSTGRES_USER / POSTGRES_DB defaults the compose
    # file now reads from .env (ishaab / llmgateway — added below if missing):
    $lines = Set-EnvLine $lines "DATABASE_URL" "postgresql://ishaab:$pgPassword@postgres/llmgateway"
    $lines = Set-EnvLine $lines "GRAFANA_ADMIN_PASSWORD" $grafanaPw

    # Prompted values (defaults in non-interactive mode):
    $lmUrl = "http://host.docker.internal:1234"; $comfyUrl = "http://host.docker.internal:8188"; $lmChat = ""; $orKey = ""
    if (-not $NonInteractive) {
        if ($v = Read-Host "LM Studio URL (Enter for default: $lmUrl)") { $lmUrl = $v.Trim() }
        if ($v = Read-Host "ComfyUI URL (Enter for default: $comfyUrl)") { $comfyUrl = $v.Trim() }
        if ($v = Read-Host "LM Studio default model id (used for prompt rewriting; Enter keeps placeholder)") { $lines = Set-EnvLine $lines "LM_DEFAULT_MODEL" $v.Trim() }
        if ($v = Read-Host "LM Studio chat model id (Enter for empty = falls back to default model)") { $lmChat = $v.Trim() }
        Write-Info "OpenRouter API key is OPTIONAL — the app now boots without it."
        if ($v = Read-Host "OpenRouter API key (sk-or-v1-...; Enter to leave empty)") { $orKey = $v.Trim() }
    }
    $lines = Set-EnvLine $lines "LM_URL" $lmUrl
    $lines = Set-EnvLine $lines "COMFY_URL" $comfyUrl
    $lines = Set-EnvLine $lines "LM_CHAT_MODEL" $lmChat
    $lines = Set-EnvLine $lines "OPENROUTER_API_KEY" $orKey

    # Port probes (interactive only; never fatal):
    if (-not $NonInteractive) {
        Write-Info "Probing for local servers (report-only)..."
        if (Test-Port "host.docker.internal" 1234)  { Write-Ok "✓ LM Studio detected on 1234" }
        if (Test-Port "host.docker.internal" 8188)  { Write-Ok "✓ ComfyUI detected on 8188" }
        if (Test-Port "host.docker.internal" 11434) { Write-Ok "✓ Ollama detected on 11434" }
    }

    # Write .env as UTF-8 WITHOUT BOM (pydantic-settings chokes on a BOM):
    [System.IO.File]::WriteAllLines($EnvFile, $lines, (New-Object System.Text.UTF8Encoding($false)))

    # Masked summary:
    Write-Ok ".env written."
    Write-Info "  LM_URL: $lmUrl"
    Write-Info "  LM_CHAT_MODEL: $(if ($lmChat) { $lmChat } else { 'not set (falls back to LM_DEFAULT_MODEL)' })"
    Write-Info "  COMFY_URL: $comfyUrl"
    Write-Info "  OPENROUTER_API_KEY: $(if ($orKey) { 'set' } else { 'not set (app boots without it)' })"
}

# --- Step 2b — metrics token (S2) ---
# METRICS_TOKEN gates GET /metrics (empty disables the endpoint). Generate one
# when .env lacks it (idempotent) and mirror it into monitoring/metrics_token so
# Prometheus can authenticate. The file must contain EXACTLY the token — the
# backend compares the header string, so no BOM, no trailing whitespace.
$metricsTokenFile = Join-Path $RepoRoot "monitoring\metrics_token"
New-Item -ItemType Directory -Path (Split-Path $metricsTokenFile) -Force | Out-Null
$lines = Get-Content $EnvFile
$metricsRe = '^METRICS_TOKEN='
$metricsLine = $lines | Where-Object { $_ -match $metricsRe } | Select-Object -First 1
if ($metricsLine) {
    # Trim any whitespace padding and write the trimmed value back so .env and
    # monitoring/metrics_token can never drift (idempotent: when the value
    # already matches, the rewrite is a byte-for-byte no-op).
    $metricsToken = ($metricsLine -replace $metricsRe, '').Trim()
    $lines = Set-EnvLine $lines "METRICS_TOKEN" $metricsToken
    [System.IO.File]::WriteAllLines($EnvFile, $lines, (New-Object System.Text.UTF8Encoding($false)))
    Write-Info "Syncing monitoring/metrics_token from existing METRICS_TOKEN in .env."
} else {
    $metricsToken = [Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Maximum 256 }))
    $lines = Set-EnvLine $lines "METRICS_TOKEN" $metricsToken
    [System.IO.File]::WriteAllLines($EnvFile, $lines, (New-Object System.Text.UTF8Encoding($false)))
    Write-Info "Generated METRICS_TOKEN in .env."
}
[System.IO.File]::WriteAllText($metricsTokenFile, $metricsToken, (New-Object System.Text.UTF8Encoding($false)))
Write-Ok "Wrote monitoring/metrics_token (matches METRICS_TOKEN)."

# --- Step 2c — security defaults (S5+S7) ---
# Add TRUSTED_PROXIES + REGISTRATION_ENABLED only when missing; never
# overwrite a value the user set deliberately.
$lines = Get-Content $EnvFile
$addedDefaults = @()
foreach ($entry in @(
    @{ Key = "TRUSTED_PROXIES";      Value = "172.16.0.0/12" }
    @{ Key = "REGISTRATION_ENABLED"; Value = "true" }
)) {
    $re = "^$([regex]::Escape($entry.Key))="
    if (-not ($lines | Where-Object { $_ -match $re })) {
        $lines = Set-EnvLine $lines $entry.Key $entry.Value
        $addedDefaults += "$($entry.Key)=$($entry.Value)"
    }
}
if ($addedDefaults.Count -gt 0) {
    [System.IO.File]::WriteAllLines($EnvFile, $lines, (New-Object System.Text.UTF8Encoding($false)))
    Write-Info "Added $($addedDefaults -join ', ') to .env."
} else {
    Write-Info "Security defaults already present in .env (TRUSTED_PROXIES, REGISTRATION_ENABLED left unchanged)."
}

# --- Step 2d — Postgres credentials (M3) ---
# docker-compose requires POSTGRES_USER + POSTGRES_DB in .env; add the defaults
# only when missing, never overwrite a value the operator set deliberately.
$lines = Get-Content $EnvFile
$addedPgDefaults = @()
foreach ($entry in @(
    @{ Key = "POSTGRES_USER"; Value = "ishaab" }
    @{ Key = "POSTGRES_DB";   Value = "llmgateway" }
)) {
    $re = "^$([regex]::Escape($entry.Key))="
    if (-not ($lines | Where-Object { $_ -match $re })) {
        $lines = Set-EnvLine $lines $entry.Key $entry.Value
        $addedPgDefaults += "$($entry.Key)=$($entry.Value)"
    }
}
if ($addedPgDefaults.Count -gt 0) {
    [System.IO.File]::WriteAllLines($EnvFile, $lines, (New-Object System.Text.UTF8Encoding($false)))
    Write-Info "Added $($addedPgDefaults -join ', ') to .env."
} else {
    Write-Info "Postgres credentials already present in .env (POSTGRES_USER, POSTGRES_DB left unchanged)."
}

# --- Step 3 — SkipStart escape hatch ---
if ($SkipStart) {
    Write-Info "Skipping stack start (-SkipStart)."
    Write-Info "Run 'docker compose up -d --build' yourself when ready (or re-run setup.ps1 without -SkipStart)."
    exit 0
}

# --- Step 4 — start the stack ---
Write-Step "Step 4/5 — Starting the stack (docker compose up -d --build)"
if (-not $NonInteractive) {
    $answer = Read-Host "Start the full stack now? (Y/n)"
    if ($answer -and $answer.Trim().ToLowerInvariant() -match "^(n|no)$") {
        Write-Info "OK — not starting. Run 'docker compose up -d --build' when ready. Docs: http://localhost:2727/docs"; exit 0
    }
}
try {
    & $ComposeExec up -d --build
    $exitCode = $LASTEXITCODE
} catch {
    $exitCode = 1; Write-Fail "docker compose failed: $($_.Exception.Message)"
}
if ($exitCode -ne 0) {
    Write-Fail "Stack failed to start. Tail of backend logs:"
    try { & $ComposeExec logs --tail=50 backend 2>&1 | ForEach-Object { Write-Host $_ -ForegroundColor Red } } catch { Write-Info "(could not fetch backend logs)" }
    exit 1
}

# --- Step 5 — wait for backend health ---
Write-Step "Step 5/5 — Waiting for backend health (up to 60s)"
$deadline = (Get-Date).AddSeconds(60); $healthy = $false; $elapsed = 0
while ((Get-Date) -lt $deadline) {
    $elapsed++
    try {
        $resp = & curl.exe -s --max-time 3 http://localhost:2727/health 2>$null
        if ($resp -match '"ok"') { $healthy = $true; break }
    } catch { }
    if ($elapsed % 5 -eq 0) { Write-Info "Waiting for backend… ($elapsed s)" }
    Start-Sleep -Seconds 1
}
if (-not $healthy) {
    Write-Fail "Backend not healthy after 60s. Check: docker compose logs backend"; exit 1
}

# --- summary ---
Write-Ok ""
Write-Ok "llm-gateway is up!"
Write-Info "  App:             http://localhost:2727"
Write-Info "  Docs (Swagger):  http://localhost:2727/docs"
Write-Info "  Prometheus:      http://localhost:9090"
if ($envGenerated) { Write-Info "  Grafana:         http://localhost:3000  (admin / $grafanaPw)" }
else { Write-Info "  Grafana:         http://localhost:3000  (admin / see GRAFANA_ADMIN_PASSWORD in .env)" }
Write-Info "  Next steps: 1) open /docs and register, 2) add your providers (LM Studio local or your own API keys), 3) start chatting."
Write-Info "  Tailscale note: keep port 2727 internal; reach the app via Caddy on :80 over your tailnet."
exit 0
