# App exploration

Milestone 1 captures one stable, ground-truth Android screen from an APK. It
does not navigate the application or call an LLM.

## Run

Start a compatible emulator and Appium, then provide the APK path:

```powershell
.\scripts\start-appium-background.ps1

.\.runtime\python\Scripts\python.exe -B -m backend.exploration `
  .\assets\apps\message.apk `
  --udid emulator-5554
```

Use `.\scripts\run-all.ps1` to start the project emulator and Appium
automatically before capture.

## Open an already-installed package through the API

With the backend and Appium running, create a live exploration run from an
installed Android package:

```powershell
$opened = Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:5000/api/v1/explorations/open `
  -ContentType application/json `
  -Body '{"package_id":"com.example.app"}'

$opened.result.screen_ref
```

The response contains the durable `run_id` and `screen_id` pointer, canonical
capture paths, observed package/activity, stability, and the URL template for
performing subsequent actions. This path does not install an APK or clear
application data.

## Stored evidence

Each run is isolated under `artifacts/explorations/{run_id}/`:

```text
exploration.db
manifest.json
graph.json
summary.json
logs/
  events.jsonl
  errors.jsonl
screens/{screen_id}/
  appium-result.json
  appium_screen_content.html
  screenshots/
    screen.png
    status_bar.png
    navigation_bar.png
```

`appium-result.json` is the canonical, self-contained Appium output for the
current screen. It embeds the normalized elements and raw UI hierarchy so no
separate XML, CSV, or projection JSON files are needed. The document contains:

- APK identity and SHA-256
- Appium session and device capabilities
- Package, activity, foreground state, orientation, viewport, and window size
- UI stabilization result
- Screenshot dimensions, format, hash, byte size, and system-bar pixel analysis
- Hierarchy format, hash, character count, and node count
- Full normalized element inventory
- App, system, external, and unknown UI ownership
- ADB-verified status/navigation bar visibility, bounds, height, and UI flags
- Navigation mode, rotation settings, density, locale, Android build, and ABI
- Focused window/activity, permission prompt, dialog, keyboard, and IME state
- Relative references to all raw and derived artifacts
- Non-fatal collection errors

`screenshots/status_bar.png` and `screenshots/navigation_bar.png` are exact
crops derived from the ADB-reported inset bounds.

`appium_screen_content.html` is a self-contained visual viewer for the canonical
document. It presents capture metrics, screenshots, application/device/system
details, searchable elements, diagnostics, the complete JSON, and raw hierarchy.
Its final section is a static roadmap of important capture data to add later.
Open it directly in a browser; no backend is required.

The viewer presents the processing boundary as three steps:

1. Captured Appium/Android evidence
2. Compact, deterministic LLM context
3. A placeholder for the future LLM output

## Generate LLM context

Convert any canonical result to a compact Markdown context block:

```powershell
.\.runtime\python\Scripts\python.exe -B `
  -m backend.exploration.llm_context `
  .\artifacts\explorations\{run_id}\screens\{screen_id}\appium-result.json
```

The command prints to standard output. Use `--output context.md` when a file is
needed, without changing the minimal screen directory contract.

The context keeps screen/app identity, every available action, every visible
text occurrence (distinguished by bounds), overlay and system state, relevant
device/display conditions, important semantic elements, explicit action states,
stability, and collection errors. It deliberately excludes raw XML, full
capabilities, duplicate layout containers, hashes, and pixel statistics while
pointing back to the canonical result.

Important semantic content is not truncated by default. Optional limits are
available through `--max-actions`, `--max-text-items`, `--max-elements`, and
`--max-chars` only when a caller deliberately needs a fixed budget.

The same transformer is available through the backend API:

```powershell
$body = @{
  screen_ref = @{
    run_id = "your-run-id"
    screen_id = "your-screen-id"
  }
  options = @{}
} | ConvertTo-Json -Depth 5

$context = Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:5000/api/v1/explorations/context `
  -ContentType application/json `
  -Body $body

$context.context.text
```

SQLite stores the same structured evidence and reserves tables for later
actions, transitions, workflows, LLM decisions, facts, and exploration
frontier data.

## Execute a monitored action

The backend exposes one generic endpoint for every supported action:

```http
POST /api/v1/explorations/{run_id}/actions
```

```json
{
  "screen_id": "screen_819fbab46fa2cd6d",
  "action": "tap",
  "target": {"element_id": "element_0015"},
  "parameters": {},
  "completion": {
    "condition": "screen_changed",
    "timeout_ms": 15000,
    "interval_ms": 400,
    "stable_samples": 3
  }
}
```

The manager keeps one live Appium session and lock per exploration run. For a
run created by the one-screen CLI, the first action resumes a session from the
stored APK with application data preserved and verifies that the live semantic
screen still matches the requested capture.

Each action records command delivery, resolved locator, observable effect,
process/foreground/crash/ANR health, stabilization samples, phase timings,
errors, and complete before/after screen references. The loop is bounded:

- 15-second default timeout, configurable up to 60 seconds
- 400 ms default sampling interval
- Three equal semantic samples required for stability
- Full canonical capture after stabilization
- No automatic repeat after an acknowledged state-changing action

Transitions are persisted in JSON, SQLite, the event log, and `graph.json`.

## Milestone boundary

Ground-truth capture, compact LLM context creation, monitored typed actions,
before/after evidence, and transition persistence are implemented. Automated
action selection, backtracking, frontier traversal, and LLM planning belong to
later milestones.

See [llm-exploration-api.md](llm-exploration-api.md) for the complete two-API
handoff specification intended for LLM requirements and integration work.
