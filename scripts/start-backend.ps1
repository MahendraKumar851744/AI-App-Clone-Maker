[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 5000
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
Set-Location $script:ProjectRoot

if (-not (Test-Path $script:Python)) {
    throw "Python environment is missing. Run .\scripts\setup.ps1 first."
}

$env:BACKEND_HOST = $HostAddress
$env:BACKEND_PORT = "$Port"
& $script:Python -m backend
