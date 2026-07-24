# AI App Clone Maker

A small execution backend for composing logic, HTTP, LLM, and Android
automation modules. Each module accepts JSON and returns JSON, so modules stay
independent and can be combined into workflows.

## Project layout

```text
assets/                  Bundled test applications
docs/                    API documentation
scripts/                 Setup and launch commands
src/backend/
  exploration/           Appium capture, evidence models, and storage
  core/                  Contracts, registry, execution, and templating
  modules/               Logic, HTTP, LLM, and automation modules
  api.py                 HTTP routes
  app.py                 Flask application factory
tests/                   Backend and Appium parser tests
tooling/appium/           Pinned Appium server and driver
```

Generated dependencies and logs live under hidden or dedicated locations:

```text
.runtime/python/         Python virtual environment
.runtime/*.log           Runtime logs
tooling/appium/node_modules/
artifacts/               Automation output
```

## Setup

Install project dependencies:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\setup.ps1
```

For a clean Windows machine, the full Android bootstrap can install missing
prerequisites and create the required emulator:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\bootstrap-windows.ps1 `
  -InstallMissingPrerequisites `
  -AcceptAndroidLicenses
```

Use `-AcceptAndroidLicenses` only after reviewing Google's Android SDK terms.

## Run the backend

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\start-backend.ps1
```

The API listens on `http://127.0.0.1:5000` by default.

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

See [docs/backend-api.md](docs/backend-api.md) for request and response
examples.

## Capture the first Android screen

Milestone 1 accepts an APK path, starts an Appium session, captures one stable
screen, and emits one canonical `appium-result.json`. That document combines
the screenshot metadata, hierarchy, normalized elements, ADB-verified system
state, observation summary, collection errors, and references to every raw
artifact. Each screen directory stays minimal: the canonical JSON, a
`screenshots/` folder, and an `appium_screen_content.html` viewer. Run-level
event, graph, and SQLite records remain available for later traversal:

```powershell
.\scripts\start-appium-background.ps1

.\.runtime\python\Scripts\python.exe -B -m backend.exploration `
  .\assets\apps\message.apk `
  --udid emulator-5554
```

The bundled APK requires an `armeabi-v7a` compatible Android device. The
end-to-end helper starts the compatible emulator and Appium before capture:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\run-all.ps1
```

Each run is stored under `artifacts/explorations/{run_id}/`.
See [docs/exploration.md](docs/exploration.md) for the complete output contract.

Create the compact context block that will be sent to an LLM:

```powershell
.\.runtime\python\Scripts\python.exe -B `
  -m backend.exploration.llm_context `
  .\artifacts\explorations\{run_id}\screens\{screen_id}\appium-result.json
```

The generated viewer shows captured evidence as Step 1, this LLM-ready context
as Step 2, and a placeholder for the future LLM response as Step 3.

To preserve current application data:

```powershell
.\scripts\run-all.ps1 -KeepData
```

## Development

Run tests:

```powershell
.\.runtime\python\Scripts\python.exe -B -m unittest discover -s tests -v
```

Check the complete local environment:

```powershell
.\scripts\doctor.ps1
```

The Flask server currently uses in-memory module definitions and run history.
Authentication, persistent storage, migrations, production WSGI serving, and
distributed workers are future layers.
