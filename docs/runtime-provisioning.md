# Appium runtime provisioning API

## Purpose

These administrative APIs prepare and control the pinned Android/Appium
environment required by exploration APIs. They do not install or remove target
applications.

The project intentionally exposes a fixed, reviewed runtime profile instead of
accepting package names, download URLs, or shell commands from clients:

```json
{
  "name": "appium-arm32-api30",
  "android_api_level": 30,
  "build_tools_version": "35.0.0",
  "avd_name": "Appium_Arm32_API30",
  "required_abi": "armeabi-v7a",
  "appium_version": "3.5.2",
  "uiautomator2_version": "8.1.1"
}
```

The versions come from the repository's reviewed PowerShell and npm
configuration. Change those files through normal code review when the profile
must be upgraded.

## Administrative access

All `/api/v1/admin/...` endpoints are restricted to loopback clients
(`127.0.0.1` or `::1`).

An optional bearer token adds another local control:

```powershell
$env:RUNTIME_ADMIN_TOKEN = "replace-with-a-local-secret"
```

When configured, requests must include:

```http
Authorization: Bearer replace-with-a-local-secret
```

The default backend binding is `127.0.0.1`. Do not expose provisioning
operations through a public reverse proxy.

## Inspect runtime status

```http
GET /api/v1/admin/runtime/status
```

The response reports:

- Python, Node.js, npm, Java, and Git paths and versions
- Android SDK root and required tool paths
- Pinned platform, build tools, system image, and AVD availability
- Connected device state, boot completion, ABI list, and compatibility
- Local Appium and UiAutomator2 package availability
- Appium server readiness
- Project Python environment availability
- PID-tracked Appium and emulator processes
- Overall `provisioned` and `ready` flags

`provisioned` means all required software is installed. `ready` additionally
requires a booted compatible device and a responsive Appium server.

## Provision the runtime

```http
POST /api/v1/admin/runtime/provision
Content-Type: application/json
```

```json
{
  "install_missing_prerequisites": false,
  "accept_android_licenses": false,
  "force": false
}
```

Options:

| Field | Default | Meaning |
| --- | ---: | --- |
| `install_missing_prerequisites` | `false` | Allow the reviewed bootstrap script to use `winget` for missing prerequisites |
| `accept_android_licenses` | `false` | Explicit authorization to pass license acceptance to Android `sdkmanager` |
| `force` | `false` | Rerun project setup even when status is already provisioned |

Android license acceptance is never inferred. A full SDK bootstrap fails its
job unless `accept_android_licenses` is explicitly `true`.

The operation is asynchronous:

```json
{
  "job": {
    "contract": "appium.runtime_job",
    "job_id": "job_0123456789abcdef",
    "operation": "provision",
    "status": "queued",
    "step": "queued",
    "progress": 0,
    "status_endpoint": "/api/v1/admin/jobs/job_0123456789abcdef"
  },
  "reused": false
}
```

Response status is `202 Accepted`. If provisioning is already active, the same
job is returned with `reused: true`.

Provisioning is idempotent:

1. If already provisioned, run the doctor only.
2. If Android is ready but project dependencies are missing, run project setup
   and the doctor.
3. If Android is incomplete, run the full pinned bootstrap.

Job output keeps the most recent 200 lines in the response. The complete job
record is persisted under `.runtime/jobs/{job_id}.json`.

## Inspect a job

```http
GET /api/v1/admin/jobs/{job_id}
```

Job statuses:

- `queued`
- `running`
- `succeeded`
- `failed`

A job records timestamps, current step, progress, recent output, structured
result, and failure message. A non-terminal job discovered after backend
restart is marked `failed` with step `interrupted`; commands are never silently
replayed.

## Start the runtime

```http
POST /api/v1/admin/runtime/start
Content-Type: application/json
```

```json
{}
```

This is also asynchronous and returns a runtime job. It:

1. Requires `provisioned: true`.
2. Reuses an already-compatible booted device when present.
3. Otherwise starts the pinned AVD and tracks its PID.
4. Waits for Android boot completion.
5. Starts or reuses the local Appium server.
6. Succeeds only when final runtime status is `ready: true`.

Concurrent starts reuse the active start job. Start is rejected while
provisioning is active.

## Stop managed runtime processes

```http
POST /api/v1/admin/runtime/stop
Content-Type: application/json
```

```json
{
  "stop_appium": true,
  "stop_emulator": true
}
```

Stop is synchronous. It only terminates processes whose PID was recorded by
this project and whose live executable identity matches the expected Appium
Node.js or Android emulator process. Missing, stale, or disabled entries are
returned as `skipped`. A PID identity mismatch returns `409 conflict` rather
than terminating an unrelated process.

Runtime stop is rejected while provisioning or startup is active. It does not
delete SDK files, AVD data, exploration evidence, or installed applications.

## Complete platform sequence

```text
GET  runtime/status
POST runtime/provision -> poll job
POST runtime/start     -> poll job
POST apps/install      (next feature)
POST explorations/open
POST explorations/context
POST explorations/{run_id}/actions
POST runtime/stop
```

Provisioning and runtime control are administrative operations. App
installation and exploration remain separate reusable modules.
