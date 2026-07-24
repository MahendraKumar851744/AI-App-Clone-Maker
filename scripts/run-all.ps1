[CmdletBinding()]
param(
    [switch]$KeepData,
    [double]$StabilityTimeout = 15
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
Set-Location $script:ProjectRoot

if (-not (Test-Path $script:Python)) {
    throw "Project dependencies are missing. Run .\scripts\bootstrap-windows.ps1 first."
}

$serialOutput = & (Join-Path $PSScriptRoot "start-emulator.ps1")
$serial = @($serialOutput | Where-Object { $_ -match "^emulator-\d+$" })[-1]
if (-not $serial) {
    throw "Could not determine the compatible emulator serial."
}

& (Join-Path $PSScriptRoot "start-appium-background.ps1")

$arguments = @(
    "-m", "backend.exploration",
    $script:Apk,
    "--udid", $serial,
    "--stability-timeout", $StabilityTimeout
)
if ($KeepData) {
    $arguments += "--keep-data"
}
& $script:Python @arguments
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Done. Explore: $script:ProjectRoot\artifacts\explorations"
