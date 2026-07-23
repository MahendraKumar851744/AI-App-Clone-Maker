# AI App Clone Maker — Appium UI Discovery

This repository bootstraps an Android/Appium environment on a clean Windows x64
machine, launches the included APK in a compatible emulator, and extracts its
first-screen UI hierarchy.

## What it produces

Each run writes to `artifacts/first_open/`:

- `report.html` — screenshot plus searchable element table
- `screen.png` — captured emulator screen
- `hierarchy.xml` — raw UiAutomator/Appium page source
- `elements.json` and `elements.csv` — flattened element inventories
- `summary.json` — clickable, visible, labeled, and scrollable counts
- `metadata.json` — package, activity, contexts, screen size, and capabilities

Records include text, resource ID, accessibility description, class, bounds,
center coordinates, XPath, state flags, hierarchy depth, and an inferred
interaction type.

## Clean Windows machine: install and run

Requirements:

- Windows 10 or 11 x64
- Hardware virtualization enabled in BIOS/UEFI
- Microsoft `winget` (App Installer)
- Approximately 10 GB of free disk space

First install Git if the machine does not have it:

```powershell
winget install --id Git.Git --exact
```

Open a new PowerShell terminal, then:

```powershell
git clone https://github.com/MahendraKumar851744/AI-App-Clone-Maker.git
cd AI-App-Clone-Maker

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\bootstrap-windows.ps1 `
  -InstallMissingPrerequisites `
  -AcceptAndroidLicenses

powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\run-all.ps1
```

`-AcceptAndroidLicenses` means the person running the command has reviewed and
accepted Google's Android SDK licenses. The bootstrap does not change the
machine-wide PowerShell execution policy.

The bootstrap:

1. Installs missing Node.js LTS, Python 3.11, Git, and JDK 17 through `winget`.
2. Downloads Google's Android command-line tools and verifies the published
   SHA-256 checksum.
3. configures `ANDROID_HOME`, `ANDROID_SDK_ROOT`, `JAVA_HOME`, and user `PATH`.
4. Installs ADB, Android Emulator, API 30, build-tools, and the required image.
5. Creates the `Appium_Arm32_API30` virtual device.
6. Creates `.venv` and installs the pinned Python Appium client.
7. Installs project-local Appium and UiAutomator2 using `package-lock.json`.
8. Runs the environment doctor.

The Android toolchain steps follow Google's
[sdkmanager](https://developer.android.com/tools/sdkmanager) and
[avdmanager](https://developer.android.com/tools/avdmanager) workflows.
UiAutomator2 follows Appium's
[official Android driver setup](https://appium.io/docs/en/latest/quickstart/uiauto2-driver/).

## Normal daily use

Run everything:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-all.ps1
```

This starts or reuses the compatible emulator, starts the local Appium server,
clears app data to recreate first launch, installs/opens the APK, and captures
the UI. The APK remains installed after the Appium session.

Preserve existing app data:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\run-all.ps1 -KeepData
```

Check the environment:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
```

Individual operations are also available:

```powershell
.\scripts\setup.ps1
.\scripts\start-emulator.ps1
.\scripts\start-appium.ps1
.\scripts\extract-first-open.ps1
```

Use the `powershell.exe -ExecutionPolicy Bypass -File ...` form if direct script
execution is restricted.

## Why this project uses Android 11/API 30 x86

APK metadata:

- File: `_Message_1.39_APKPure.apk`
- Package: `message.chat.text.messaging.sms`
- Version: `1.39` (`versionCode=40`)
- Launcher:
  `message.chat.text.messaging.sms.launcher.activities.SetDefaultLauncherActivity`
- Minimum SDK: 29
- Target SDK: 36
- Native ABI: `armeabi-v7a` only

Most newer x86_64 Android emulator images translate ARM64 applications but no
longer support 32-bit ARMv7. They reject this APK with
`INSTALL_FAILED_NO_MATCHING_ABIS`. The API 30 Google Play x86 image advertises
`x86,armeabi-v7a,armeabi`, allowing Android's native bridge to run this APK.

Do not replace this AVD with an arbitrary modern x86_64 image unless the APK is
replaced by a universal, x86/x86_64, or ARM64 build.

## Project layout

```text
discover_ui.py                  Appium session and artifact capture
ui_discovery/parser.py          XML hierarchy parser
ui_discovery/report.py          JSON, CSV, and HTML reports
scripts/bootstrap-windows.ps1   Clean Windows machine bootstrap
scripts/doctor.ps1              Required-tool diagnostics
scripts/run-all.ps1             One-command emulator-to-report workflow
scripts/start-emulator.ps1      Compatible AVD startup and boot wait
scripts/start-appium*.ps1       Foreground/background Appium startup
tests/test_parser.py            Offline parser tests
```

## Limitations

UiAutomator exposes Android automation/accessibility nodes. Custom canvas
drawing, raw pixels, and some WebView content can appear in the screenshot
without appearing as individual XML elements. Later stages can add WebView
context discovery or image-based analysis.
