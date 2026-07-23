param(
    [string]$Udid = "",
    [double]$Wait = 3
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
. (Join-Path $PSScriptRoot "common.ps1")

if (-not $Udid) {
    $Udid = Get-CompatibleDevice
    if (-not $Udid) {
        throw "No connected device supports the APK's armeabi-v7a native libraries."
    }
    Write-Host "Selected ARMv7-compatible device: $Udid"
}

$arguments = @("discover_ui.py", "--wait", $Wait)
$arguments += @("--udid", $Udid)

& ".\.venv\Scripts\python.exe" @arguments
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "Report: $projectRoot\artifacts\first_open\report.html"
