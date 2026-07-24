# Android application management API

## Purpose

This feature installs, inspects, replaces, and uninstalls Android applications
on the compatible device managed by the runtime API.

It is intentionally separate from:

- Runtime provisioning and emulator/Appium control
- Opening an application for exploration
- Screen-context export
- Monitored UI actions

The target device must already be booted and compatible. The APK must already
exist on the backend host inside an approved APK directory.

## Administrative access

Application management uses the same protection as runtime administration:

- Requests must originate from `127.0.0.1` or `::1`.
- When `RUNTIME_ADMIN_TOKEN` is configured, requests must include its bearer
  token.

The API never accepts shell fragments, ADB arguments, download URLs, or APK
uploads.

## Approved APK roots

The default approved directory is:

```text
assets/apps/
```

Additional roots can be supplied through the Flask `APP_APK_ROOTS`
configuration when the application is created:

```python
create_app(
    {
        "APP_APK_ROOTS": [
            r"C:\approved-apps",
            r"D:\build-artifacts\android",
        ]
    }
)
```

Every requested path is fully resolved before validation. Relative traversal
and symlink targets outside approved roots are rejected.

## Install an APK

```http
POST /api/v1/apps/install
Content-Type: application/json
```

```json
{
  "apk_path": "C:\\approved-apps\\message.apk",
  "expected_package_id": "message.chat.text.messaging.sms",
  "install_mode": "clean",
  "device_id": "emulator-5554"
}
```

`device_id` is optional. When omitted, the first booted device compatible with
the pinned runtime profile is selected.

Required fields:

| Field | Meaning |
| --- | --- |
| `apk_path` | Backend-local path under an approved APK root |
| `expected_package_id` | Package the caller expects the APK to contain |
| `install_mode` | Explicit installation/data-handling policy |

### Installation modes

`clean`

- Inspect the existing package.
- Close live exploration sessions using that package.
- Run `adb uninstall`, removing application data, accounts, permissions, and
  configuration.
- Install the requested APK from a clean state.

`replace`

- Close live exploration sessions using that package.
- Run `adb install -r`.
- Preserve application data when Android permits it.
- Fail when Android rejects incompatible signatures or downgrade rules.

`preserve`

- Install only when the package is absent.
- If already installed, return `409 conflict`.
- Never close sessions or modify the existing installation.

For deterministic first-launch exploration, use `clean`.

### Server-side workflow

1. Validate the request schema and install mode.
2. Resolve the APK path and enforce the root allow-list.
3. Inspect APK metadata and SHA-256 with `aapt`.
4. Derive the real package ID from the APK.
5. Compare it to `expected_package_id`.
6. Select a booted ABI-compatible device.
7. Check APK native ABI compatibility.
8. Inspect the previous installation and version.
9. Close relevant live exploration sessions when replacement is allowed.
10. Uninstall first when mode is `clean`.
11. Install with a bounded, server-owned ADB command.
12. Inspect the installed package again.
13. Verify package ID, version name, and version code.

No installation command is constructed through a shell.

### Successful response

```json
{
  "result": {
    "contract": "appium.app_install_result",
    "schema_version": 1,
    "status": "installed",
    "package_id": "message.chat.text.messaging.sms",
    "version_name": "1.2.0",
    "version_code": "42",
    "device_id": "emulator-5554",
    "install_mode": "clean",
    "apk": {
      "path": "C:\\approved-apps\\message.apk",
      "sha256": "full-apk-sha256",
      "package": "message.chat.text.messaging.sms"
    },
    "previous_installation": {
      "installed": true,
      "version_name": "1.1.0",
      "version_code": "41",
      "removed": true
    },
    "closed_exploration_runs": [
      "previous-run-id"
    ],
    "verification": {
      "installed": true,
      "package_matches": true,
      "version_name_matches": true,
      "version_code_matches": true,
      "verified": true
    },
    "duration_ms": 4821
  }
}
```

The response also contains bounded stdout, stderr, exit codes, and timings for
the actual install/uninstall commands.

If a clean install fails after successful uninstall, the previous application
is not automatically restored. The `502 external_service_failed` response
states that the previous installation was removed.

## Inspect an installed package

```http
GET /api/v1/apps/{package_id}
GET /api/v1/apps/{package_id}?device_id=emulator-5554
```

Successful response:

```json
{
  "app": {
    "contract": "appium.installed_app",
    "schema_version": 1,
    "installed": true,
    "package_id": "message.chat.text.messaging.sms",
    "device_id": "emulator-5554",
    "version_name": "1.2.0",
    "version_code": "42",
    "paths": [
      "/data/app/.../base.apk"
    ]
  }
}
```

An absent application returns `404 not_found`.

## Uninstall an application

```http
DELETE /api/v1/apps/{package_id}
Content-Type: application/json
```

```json
{
  "confirm": true,
  "device_id": "emulator-5554"
}
```

`confirm: true` is mandatory because uninstall permanently deletes application
data. The operation:

1. Verifies the package currently exists.
2. Closes its live exploration sessions.
3. Runs the bounded uninstall command.
4. Verifies the package no longer exists.

It does not delete the source APK or existing exploration evidence.

## Error contract

| HTTP status | Code | Examples |
| ---: | --- | --- |
| `400` | `validation_error` | Invalid package, missing confirmation, unsupported mode, path outside allowed roots |
| `403` | `access_denied` | Non-loopback request or invalid admin token |
| `404` | `not_found` | APK, device, or installed package does not exist |
| `409` | `conflict` | Package mismatch, preserve conflict, no compatible device, ABI mismatch |
| `502` | `external_service_failed` | `aapt`, ADB inspection, install, uninstall, or verification failure |

## End-to-end sequence

```text
POST admin/runtime/provision -> poll job
POST admin/runtime/start     -> poll job
POST apps/install            -> clean verified installation
POST explorations/open       -> run_id + screen_id
POST explorations/context    -> LLM-ready screen
POST explorations/{run_id}/actions
DELETE apps/{package_id}     -> explicit optional cleanup
POST admin/runtime/stop
```
