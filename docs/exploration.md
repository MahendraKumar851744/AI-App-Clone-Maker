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

SQLite stores the same structured evidence and reserves tables for later
actions, transitions, workflows, LLM decisions, facts, and exploration
frontier data.

## Milestone boundary

This milestone ends after persisting the first screen and closing the Appium
session. Typed actions, traversal, backtracking, LLM planning, and autonomous
exploration belong to later milestones.
