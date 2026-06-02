# Encode local Hermes WhatsApp session for Railway (fits 32KB variable limit)
# Run: .\scripts\encode-local-session.ps1
# Compatible with Windows PowerShell 5.1+

param(
    [switch]$Full,
    [switch]$Split
)

$ErrorActionPreference = "Stop"
$MaxPartSize = 30000

$Candidates = @(
    "$env:LOCALAPPDATA\hermes\platforms\whatsapp\session",
    "$env:LOCALAPPDATA\hermes\whatsapp\session",
    "$env:USERPROFILE\.hermes\platforms\whatsapp\session",
    "$env:USERPROFILE\.hermes\whatsapp\session",
    (Join-Path $PSScriptRoot "..\local-whatsapp-session"),
    (Join-Path $PSScriptRoot "..\..\.hermes\platforms\whatsapp\session")
)

$SessionDir = $null
foreach ($c in $Candidates) {
    if (Test-Path $c) {
        $resolved = (Resolve-Path $c).Path
        if (Test-Path (Join-Path $resolved "creds.json")) {
            $SessionDir = $resolved
            break
        }
    }
}

if (-not $SessionDir) {
    Write-Host "No local WhatsApp session found." -ForegroundColor Red
    Write-Host "Run: hermes whatsapp   (scan QR first)"
    exit 1
}

Write-Host "Found session: $SessionDir" -ForegroundColor Green

$OutDir = Split-Path $PSScriptRoot -Parent
$TarFile = Join-Path $env:TEMP "whatsapp-session-$(Get-Random).tar.gz"

Push-Location $SessionDir
try {
    if ($Full) {
        Write-Host "Packing ALL session files (may exceed Railway 32KB limit)..." -ForegroundColor Yellow
        tar -czf $TarFile *
    } else {
        $essential = Get-ChildItem -File | Where-Object {
            $_.Name -eq "creds.json" -or
            $_.Name -like "app-state-sync-key-*" -or
            $_.Name -like "session-*" -or
            $_.Name -like "identity-key-*" -or
            $_.Name -like "device-list-*" -or
            $_.Name -like "tctoken-*" -or
            $_.Name -like "lid-mapping-*"
        }
        if ($essential.Count -eq 0) {
            Write-Error "No essential session files found in $SessionDir"
        }
        Write-Host "Packing $($essential.Count) essential files (fits Railway limit)..." -ForegroundColor Green
        tar -czf $TarFile $($essential.Name)
    }
} finally {
    Pop-Location
}

$Bytes = [IO.File]::ReadAllBytes($TarFile)
Remove-Item $TarFile -Force
$B64 = [Convert]::ToBase64String($Bytes)

Write-Host "Base64 length: $($B64.Length) characters" -ForegroundColor Gray

if ($B64.Length -gt 32768) {
    if (-not $Split) {
        Write-Host ""
        Write-Host "WARNING: Exceeds Railway 32KB limit!" -ForegroundColor Red
        Write-Host "Re-run with -Split to create multiple variables, or use default (essential only)." -ForegroundColor Yellow
        exit 1
    }
    $parts = [math]::Ceiling($B64.Length / $MaxPartSize)
    Write-Host "Splitting into $parts parts..." -ForegroundColor Cyan
    for ($i = 0; $i -lt $parts; $i++) {
        $start = $i * $MaxPartSize
        $len = [Math]::Min($MaxPartSize, $B64.Length - $start)
        $part = $B64.Substring($start, $len)
        $partFile = Join-Path $OutDir ("whatsapp-session.part{0}.txt" -f ($i + 1))
        [System.IO.File]::WriteAllText($partFile, $part)
        Write-Host "  Saved: $partFile ($len chars)"
    }
    Write-Host ""
    Write-Host "Railway Variables:" -ForegroundColor Green
    Write-Host "  WHATSAPP_SESSION_B64_PARTS = $parts"
    for ($i = 1; $i -le $parts; $i++) {
        Write-Host "  WHATSAPP_SESSION_B64_$i = contents of whatsapp-session.part$i.txt"
    }
} else {
    $OutFile = Join-Path $OutDir "whatsapp-session.b64.txt"
    [System.IO.File]::WriteAllText($OutFile, $B64)
    Write-Host ""
    Write-Host "Saved: $OutFile" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Railway -> Variables -> add ONE variable:" -ForegroundColor Green
    Write-Host "  WHATSAPP_SESSION_B64 = paste entire whatsapp-session.b64.txt"
    Write-Host ""
    Write-Host "Then redeploy."
}
