[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 5000
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    throw "Python environment is missing. Run .\scripts\setup.ps1 first."
}

$env:BACKEND_HOST = $HostAddress
$env:BACKEND_PORT = "$Port"
& ".\.venv\Scripts\python.exe" server.py
