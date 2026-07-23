# AI App Clone Maker

AI App Clone Maker is a modular Flask execution backend. Independent modules
receive JSON input, perform one responsibility, and return JSON output.
Workflows compose those modules without coupling their implementations.

Built-in module types:

- `logic`: safe template-based transformations
- `http`: bounded, templated outbound HTTP calls
- `llm`: system/user prompts with pluggable providers
- `automation`: reserved main-module extension point; contract pending

The existing Appium code is an automation service capability, not the backend's
core architecture. Its public module operations will be defined separately.

## Backend quick start

After cloning and running setup:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\start-backend.ps1
```

The server binds to `http://127.0.0.1:5000` by default.

```powershell
Invoke-RestMethod http://127.0.0.1:5000/api/v1/health
Invoke-RestMethod http://127.0.0.1:5000/api/v1/module-types
```

Main endpoints:

```text
GET    /api/v1/health
GET    /api/v1/module-types
GET    /api/v1/modules
POST   /api/v1/modules
GET    /api/v1/modules/{module_id}
DELETE /api/v1/modules/{module_id}
POST   /api/v1/modules/{module_id}/execute
POST   /api/v1/workflows/execute
GET    /api/v1/runs
GET    /api/v1/runs/{run_id}
```

See [docs/backend-api.md](docs/backend-api.md) for module schemas, HTTP and LLM
configuration, workflow mappings, and complete examples.

## Example module

Create a decoupled logic module:

```powershell
$module = @{
  id = "welcome"
  type = "logic"
  config = @{
    output = @{
      message = "Hello {{ input.name }}"
    }
  }
} | ConvertTo-Json -Depth 10

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:5000/api/v1/modules `
  -ContentType application/json `
  -Body $module
```

Execute it:

```powershell
$input = @{
  input = @{
    name = "Mahendra"
  }
} | ConvertTo-Json -Depth 10

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:5000/api/v1/modules/welcome/execute `
  -ContentType application/json `
  -Body $input
```

Each execution produces a run record containing status, timestamps, duration,
input, output, errors, and step-level traces.

## Clean Windows machine setup

Requirements:

- Windows 10 or 11 x64
- Hardware virtualization enabled in BIOS/UEFI
- Microsoft `winget` (App Installer)
- Approximately 10 GB of free disk space for Android tooling

Install Git if necessary:

```powershell
winget install --id Git.Git --exact
```

Open a new PowerShell terminal:

```powershell
git clone https://github.com/MahendraKumar851744/AI-App-Clone-Maker.git
cd AI-App-Clone-Maker

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\bootstrap-windows.ps1 `
  -InstallMissingPrerequisites `
  -AcceptAndroidLicenses
```

`-AcceptAndroidLicenses` means the person running the command has reviewed and
accepted Google's Android SDK licenses. The bootstrap does not change the
machine-wide PowerShell execution policy.

The bootstrap:

1. Installs missing Node.js LTS, Python 3.11, Git, and JDK 17 using `winget`.
2. Downloads Google's Android command-line tools and verifies their checksum.
3. Configures Android and Java environment variables.
4. Installs ADB, Emulator, API 30, build-tools, and the required image.
5. Creates the ARMv7-compatible `Appium_Arm32_API30` AVD.
6. Creates `.venv` and installs pinned backend/Appium Python dependencies.
7. Installs project-local Appium and UiAutomator2 from `package-lock.json`.
8. Runs the environment doctor.

The Android steps follow Google's
[sdkmanager](https://developer.android.com/tools/sdkmanager) and
[avdmanager](https://developer.android.com/tools/avdmanager) workflows.
UiAutomator2 follows Appium's
[official driver setup](https://appium.io/docs/en/latest/quickstart/uiauto2-driver/).

## Current automation service

Run the current Appium UI-discovery service:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\run-all.ps1
```

This starts or reuses the compatible emulator, starts Appium, resets the app to
first-open state, opens the included APK, and captures:

- `report.html`: screenshot and element inventory
- `screen.png`: captured screen
- `hierarchy.xml`: raw UiAutomator hierarchy
- `elements.json` and `elements.csv`: flattened UI elements
- `summary.json`: element counts
- `metadata.json`: package, activity, contexts, and screen information

Artifacts are written to `artifacts/first_open/` and are intentionally excluded
from Git. The APK remains installed after Appium closes the session.

To preserve current app data:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\run-all.ps1 -KeepData
```

## APK emulator compatibility

The included APK has these properties:

- Package: `message.chat.text.messaging.sms`
- Version: `1.39`
- Minimum SDK: 29
- Target SDK: 36
- Native ABI: `armeabi-v7a` only

Modern x86_64 images commonly translate ARM64 but not 32-bit ARMv7. This project
therefore provisions an Android 11/API 30 Google Play x86 image that advertises
`x86,armeabi-v7a,armeabi`.

## Development

Run all tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Check the environment:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\doctor.ps1
```

Project layout:

```text
backend/app.py                   Flask application factory
backend/api.py                   Versioned HTTP API
backend/core/                    Contracts, registry, workflows, run tracking
backend/modules/                 HTTP, LLM, logic, automation module types
backend/services/llm.py          Pluggable LLM provider boundary
docs/backend-api.md              Backend module and workflow documentation
server.py                        Local backend entry point
discover_ui.py                   Current Appium discovery service
ui_discovery/                    UI hierarchy parsing and reporting
scripts/bootstrap-windows.ps1    Clean-machine setup
scripts/start-backend.ps1        Flask backend launcher
scripts/run-all.ps1              Current automation-service runner
```

The Flask server is currently a local development server with in-memory module
definitions and run history. Authentication, persistent storage, migrations,
production WSGI serving, and distributed workers are deliberate future layers.
