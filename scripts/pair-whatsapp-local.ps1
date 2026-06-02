# Pair WhatsApp locally, then upload session to Railway (no deploy logs needed)
# Run from: hermes/docker/railway/

$ErrorActionPreference = "Stop"
$SessionDir = Join-Path $PSScriptRoot "local-whatsapp-session"
$HermesDir = Join-Path $PSScriptRoot ".hermes-local"

Write-Host "==> Xenko WhatsApp — Local Pairing for Railway" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path (Join-Path $PSScriptRoot "Dockerfile"))) {
    Write-Error "Run this script from hermes/docker/railway/"
}

New-Item -ItemType Directory -Force -Path $SessionDir | Out-Null
New-Item -ItemType Directory -Force -Path $HermesDir | Out-Null

Write-Host "Building Docker image (one time)..." -ForegroundColor Yellow
docker build -t xenko-whatsapp:local $PSScriptRoot

Write-Host ""
Write-Host "Starting pairing wizard — SCAN THE QR CODE IN THIS TERMINAL" -ForegroundColor Green
Write-Host "WhatsApp -> Settings -> Linked Devices -> Link a Device" -ForegroundColor Green
Write-Host ""

docker run --rm -it `
  -v "${SessionDir}:/app/.hermes/platforms/whatsapp/session" `
  -v "${HermesDir}:/app/.hermes" `
  -e WHATSAPP_ENABLED=true `
  -e WHATSAPP_MODE=bot `
  -e WHATSAPP_ALLOW_ALL_USERS=true `
  xenko-whatsapp:local `
  hermes whatsapp

$Creds = Join-Path $SessionDir "creds.json"
if (-not (Test-Path $Creds)) {
    Write-Host ""
    Write-Error "Pairing failed — creds.json not found in $SessionDir"
}

Write-Host ""
Write-Host "==> Pairing successful!" -ForegroundColor Green

# Create base64 blob for Railway variable
$TarFile = Join-Path $env:TEMP "whatsapp-session.tar.gz"
if (Get-Command tar -ErrorAction SilentlyContinue) {
    Push-Location $SessionDir
    tar -czf $TarFile *
    Pop-Location
    $B64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($TarFile))
    Remove-Item $TarFile -Force
} else {
    $B64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($Creds))
}

$OutFile = Join-Path $PSScriptRoot "whatsapp-session.b64.txt"
Set-Content -Path $OutFile -Value $B64 -NoNewline

Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "1. Railway -> hermes-Whatsapp-Agent -> Variables"
Write-Host "2. Add variable: WHATSAPP_SESSION_B64"
Write-Host "3. Paste contents of: $OutFile"
Write-Host "4. Mount volume at: /app/.hermes/platforms/whatsapp/session"
Write-Host "5. Redeploy — no QR scan needed on Railway"
Write-Host ""
Write-Host "Saved base64 to: $OutFile" -ForegroundColor Yellow
