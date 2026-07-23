[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    throw "Python was not found. Run .\scripts\bootstrap-windows.ps1 -InstallMissingPrerequisites -AcceptAndroidLicenses"
}
if (-not (Get-Command node.exe -ErrorAction SilentlyContinue)) {
    throw "Node.js was not found. Run the Windows bootstrap script first."
}
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
    throw "npm.cmd was not found. Reinstall the Node.js LTS package."
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $pythonCommand.Source -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt

if (Test-Path "package-lock.json") {
    & npm.cmd ci
} else {
    & npm.cmd install
}

$installedDrivers = & npx.cmd appium driver list --installed 2>&1
if ($installedDrivers -notmatch "uiautomator2") {
    & npx.cmd appium driver install uiautomator2
}

Write-Host ""
Write-Host "Project dependencies are ready."
