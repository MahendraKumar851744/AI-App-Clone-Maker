[CmdletBinding()]
param(
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$existing = Get-CompatibleDevice
if ($existing) {
    Write-Host "Using compatible device: $existing"
    Write-Output $existing
    exit 0
}

$emulator = Get-SdkTool "emulator\emulator.exe"
$avds = & $emulator -list-avds
if ($avds -notcontains $script:AvdName) {
    throw "AVD '$script:AvdName' does not exist. Run .\scripts\bootstrap-windows.ps1 -AcceptAndroidLicenses"
}

Write-Host "Starting $script:AvdName..."
Start-Process -FilePath $emulator -ArgumentList @(
    "-avd", $script:AvdName,
    "-netdelay", "none",
    "-netspeed", "full"
)

$serial = Wait-ForCompatibleDevice -TimeoutSeconds $TimeoutSeconds
Write-Host "Emulator ready: $serial"
Write-Output $serial
