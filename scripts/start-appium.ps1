[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
& npx.cmd appium --address 127.0.0.1 --port 4723 --base-path /
