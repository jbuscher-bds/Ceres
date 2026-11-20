<#
.SYNOPSIS
Creates a local Python virtual environment and installs Ceres and dev tools.
#>
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$PyCmd = $null
foreach ($candidate in @("py -3.14", "py -3.13", "py -3.12", "python")) {
    try {
        $parts = $candidate -split " "
        $exe = $parts[0]
        $args = if ($parts.Length -gt 1) { $parts[1..($parts.Length - 1)] } else { @() }
        & $exe @args --version *> $null
        if ($LASTEXITCODE -eq 0) {
            $PyCmd = $candidate
            break
        }
    } catch {}
}

if (-not $PyCmd) {
    throw "Python 3.12+ is required. Install Python from https://www.python.org/downloads/windows/ and re-run setup.ps1."
}

Write-Host "Using Python: $PyCmd" -ForegroundColor Cyan
if (-not (Test-Path ".venv")) {
    $parts = $PyCmd -split " "
    $exe = $parts[0]
    $args = if ($parts.Length -gt 1) { $parts[1..($parts.Length - 1)] } else { @() }
    & $exe @args -m venv .venv
}

$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"
& $VenvPy -m pip install --upgrade pip setuptools
& $VenvPy -m pip install -r requirements.txt
& $VenvPy -m pip install --no-build-isolation --no-deps -e .

Write-Host ""
Write-Host "Ceres setup complete!" -ForegroundColor Green
Write-Host "Launch with: .\run_ceres.bat (or double-click launch_ceres.vbs)"
