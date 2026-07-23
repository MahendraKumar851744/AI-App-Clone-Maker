[CmdletBinding()]
param(
    [int]$TimeoutSeconds = 45
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
Set-Location $script:ProjectRoot

if (Test-AppiumReady) {
    Write-Host "Appium is already ready at http://127.0.0.1:4723"
    exit 0
}

$node = Get-Command node.exe -ErrorAction SilentlyContinue
if (-not $node) {
    throw "Node.js was not found. Run the bootstrap script first."
}
$entryPoint = Join-Path $script:ProjectRoot "node_modules\appium\build\lib\main.js"
if (-not (Test-Path $entryPoint)) {
    throw "Local Appium is not installed. Run .\scripts\setup.ps1 first."
}

$runtime = Join-Path $script:ProjectRoot ".runtime"
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
$appiumArguments = "`"$entryPoint`" --address 127.0.0.1 --port 4723 --base-path /"
$process = Start-Process `
    -FilePath $node.Source `
    -ArgumentList $appiumArguments `
    -WorkingDirectory $script:ProjectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $runtime "appium.stdout.log") `
    -RedirectStandardError (Join-Path $runtime "appium.stderr.log") `
    -PassThru
$process.Id | Set-Content -Path (Join-Path $runtime "appium.pid")

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
do {
    if (Test-AppiumReady) {
        Write-Host "Appium ready at http://127.0.0.1:4723 (PID $($process.Id))"
        exit 0
    }
    if ($process.HasExited) {
        $errorLog = Get-Content (Join-Path $runtime "appium.stderr.log") -Raw -ErrorAction SilentlyContinue
        throw "Appium exited during startup. $errorLog"
    }
    Start-Sleep -Seconds 1
} while ((Get-Date) -lt $deadline)

throw "Appium did not become ready within $TimeoutSeconds seconds."
