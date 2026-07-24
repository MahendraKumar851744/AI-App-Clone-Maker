param(
    [string]$Udid = "",
    [double]$StabilityTimeout = 15
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
Set-Location $script:ProjectRoot

if (-not $Udid) {
    $Udid = Get-CompatibleDevice
    if (-not $Udid) {
        throw "No connected device supports the APK's armeabi-v7a native libraries."
    }
    Write-Host "Selected ARMv7-compatible device: $Udid"
}

$arguments = @(
    "-m", "backend.exploration",
    $script:Apk,
    "--udid", $Udid,
    "--stability-timeout", $StabilityTimeout
)

& $script:Python @arguments
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "Exploration artifacts: $script:ProjectRoot\artifacts\explorations"
