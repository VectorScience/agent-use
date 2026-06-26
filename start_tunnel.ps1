# Expose the local FastAPI bridge over a public Cloudflare Tunnel (HTTPS / WSS).
# Usage: powershell -ExecutionPolicy Bypass -File start_tunnel.ps1
#
# Side effects:
#   - Starts the FastAPI backend on :8000 if it's not already serving.
#   - Starts `cloudflared tunnel --url http://127.0.0.1:8000`.
#   - Parses cloudflared output for the assigned *.trycloudflare.com URL.
#   - On Ctrl+C both child processes are cleaned up.

param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

function Test-Cloudflared {
    $cmd = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($base in @(
        "$env:ProgramFiles\cloudflared\cloudflared.exe",
        "${env:ProgramFiles(x86)}\cloudflared\cloudflared.exe"
    )) {
        if (Test-Path $base) { return $base }
    }
    return $null
}

function Test-BackendUp([int]$Port) {
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2 -UseBasicParsing
        return $resp.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Start-Backend([int]$Port) {
    Write-Host "[tunnel] starting FastAPI backend on :$Port ..." -ForegroundColor Cyan
    $logFile = Join-Path $env:TEMP "cursor-remote-backend.log"
    $proc = Start-Process -FilePath "uv" `
        -ArgumentList @("run", "python", "-m", "server.main", "--port", "$Port") `
        -WorkingDirectory $PSScriptRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $logFile `
        -RedirectStandardError "$logFile.err" `
        -PassThru
    return $proc
}

function Wait-BackendReady([int]$Port, [int]$TimeoutSec = 20) {
    for ($i = 0; $i -lt $TimeoutSec; $i++) {
        if (Test-BackendUp $Port) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

# --- cloudflared check ---------------------------------------------------------

$cloudflaredPath = Test-Cloudflared
if (-not $cloudflaredPath) {
    Write-Host "[error] cloudflared not found on PATH." -ForegroundColor Red
    Write-Host "        Install with:  winget install --id Cloudflare.cloudflared" -ForegroundColor Yellow
    Write-Host "        Re-run this script after install." -ForegroundColor Yellow
    exit 1
}

# --- backend check / start -----------------------------------------------------

$backendStartedByUs = $false
$backendProc = $null
if (Test-BackendUp $Port) {
    Write-Host "[tunnel] reusing existing FastAPI backend on :$Port" -ForegroundColor Green
} else {
    $backendProc = Start-Backend $Port
    $backendStartedByUs = $true
    if (-not (Wait-BackendReady $Port)) {
        Write-Host "[error] FastAPI backend did not become healthy on :$Port within 20s." -ForegroundColor Red
        if ($backendProc -and -not $backendProc.HasExited) { Stop-Process -Id $backendProc.Id -Force }
        exit 1
    }
    Write-Host "[tunnel] backend ready on :$Port" -ForegroundColor Green
}

# --- start cloudflared ---------------------------------------------------------

$tunnelUrlPattern = 'https://[a-z0-9-]+\.trycloudflare\.com'
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $cloudflaredPath
$psi.Arguments = "tunnel --url http://127.0.0.1:$Port"
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.CreateNoWindow = $true

$cf = New-Object System.Diagnostics.Process
$cf.StartInfo = $psi

$tunnelUrl = $null
$urlFound = New-Object System.Threading.ManualResetEvent $false

$errHandler = {
    param($sender, $e)
    if (-not [string]::IsNullOrWhiteSpace($e.Data)) {
        $line = $e.Data
        if ($line -match $tunnelUrlPattern -and -not $tunnelUrl) {
            $tunnelUrl = ([regex]::Matches($line, $tunnelUrlPattern))[0].Value
            $urlFound.Set() | Out-Null
        }
        # cloudflared prints status / errors on stderr.
        Write-Host "[cloudflared] $line" -ForegroundColor DarkGray
    }
}

$outHandler = {
    param($sender, $e)
    if (-not [string]::IsNullOrWhiteSpace($e.Data)) {
        $line = $e.Data
        if ($line -match $tunnelUrlPattern -and -not $tunnelUrl) {
            $tunnelUrl = ([regex]::Matches($line, $tunnelUrlPattern))[0].Value
            $urlFound.Set() | Out-Null
        }
        Write-Host "[cloudflared] $line" -ForegroundColor DarkGray
    }
}

$null = Register-ObjectEvent -InputObject $cf -EventName "OutputDataReceived" -Action $outHandler
$null = Register-ObjectEvent -InputObject $cf -EventName "ErrorDataReceived"  -Action $errHandler

[void]$cf.Start()
$cf.BeginOutputReadLine()
$cf.BeginErrorReadLine()

Write-Host "[tunnel] cloudflared started (pid $($cf.Id)). Waiting for tunnel URL..." -ForegroundColor Cyan

# --- Ctrl+C cleanup ------------------------------------------------------------

$cleanup = {
    Write-Host ""
    Write-Host "[tunnel] shutting down..." -ForegroundColor Yellow
    try {
        if ($script:cf -and -not $script:cf.HasExited) {
            Stop-Process -Id $script:cf.Id -Force -ErrorAction SilentlyContinue
        }
    } catch {}
    try {
        if ($script:backendStartedByUs -and $script:backendProc -and -not $script:backendProc.HasExited) {
            Stop-Process -Id $script:backendProc.Id -Force -ErrorAction SilentlyContinue
        }
    } catch {}
}

# PowerShell doesn't deliver Ctrl+C to handlers reliably while a C# Process is
# foreground-attached; use a finally block instead.
try {
    if ($urlFound.WaitOne(30000)) {
        Write-Host ""
        Write-Host "============================================================" -ForegroundColor Green
        Write-Host "  Tunnel URL: $tunnelUrl" -ForegroundColor Green
        Write-Host "  Set this as VITE_API_BASE in Vercel (no trailing slash)." -ForegroundColor Green
        Write-Host "============================================================" -ForegroundColor Green
        Write-Host ""
        Write-Host "Backend log: $env:TEMP\cursor-remote-backend.log" -ForegroundColor DarkGray
        Write-Host "Press Ctrl+C to stop the tunnel and (if we started it) the backend." -ForegroundColor DarkGray
        Write-Host ""
        # Block until cloudflared exits or is killed.
        $cf.WaitForExit() | Out-Null
    } else {
        Write-Host "[error] did not see a *.trycloudflare.com URL within 30s." -ForegroundColor Red
        Write-Host "        Check the cloudflared output above." -ForegroundColor Yellow
        exit 1
    }
} finally {
    & $cleanup
}
