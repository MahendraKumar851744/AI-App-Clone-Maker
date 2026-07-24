# Modular backend API

The backend is an execution platform. Automation is one module type, not the
application's central contract.

## Module contract

Every module:

1. Receives a JSON object.
2. Performs one isolated responsibility.
3. Returns a JSON object.
4. Has no direct dependency on the module before or after it.

The workflow executor owns composition and passes explicitly mapped data between
modules.

## Start the server

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\start-backend.ps1
```

The development server binds to `127.0.0.1:5000` by default.

```text
GET  /api/v1/health
GET  /api/v1/module-types
GET  /api/v1/modules
POST /api/v1/modules
GET  /api/v1/modules/{module_id}
DELETE /api/v1/modules/{module_id}
POST /api/v1/modules/{module_id}/execute
POST /api/v1/workflows/execute
GET  /api/v1/runs
GET  /api/v1/runs/{run_id}
GET  /api/v1/admin/runtime/status
POST /api/v1/admin/runtime/provision
POST /api/v1/admin/runtime/start
POST /api/v1/admin/runtime/stop
GET  /api/v1/admin/jobs/{job_id}
POST /api/v1/explorations/open
POST /api/v1/explorations/context
POST /api/v1/explorations/{run_id}/actions
```

Module definitions and run history are currently process-local. Persistence,
authentication, authorization, and multi-worker coordination belong in later
infrastructure layers.

## Runtime administration

The loopback-only runtime API reports the complete pinned Android/Appium
environment, provisions missing components through asynchronous persisted
jobs, starts the compatible emulator and Appium server, and safely stops only
PID-tracked project processes.

```http
GET  /api/v1/admin/runtime/status
POST /api/v1/admin/runtime/provision
GET  /api/v1/admin/jobs/{job_id}
POST /api/v1/admin/runtime/start
POST /api/v1/admin/runtime/stop
```

Full contracts, license behavior, job states, safety boundaries, and examples
are documented in
[runtime-provisioning.md](runtime-provisioning.md).

## Open an installed Android application

This endpoint bootstraps an exploration from only an Android package ID:

```http
POST /api/v1/explorations/open
Content-Type: application/json
```

```json
{
  "package_id": "com.example.app"
}
```

The package must already be installed on the selected Appium device. The
endpoint does not install an APK, clear application data, or reset the app. It:

1. Starts an Appium UiAutomator2 session without an APK capability.
2. Activates the requested package.
3. Waits for the UI hierarchy to stabilize.
4. Captures and persists the canonical screen evidence.
5. Keeps the run-scoped Appium session alive for subsequent actions.

Successful response (`201 Created`):

```json
{
  "result": {
    "contract": "appium.launch_result",
    "schema_version": 1,
    "status": "opened",
    "package_id": "com.example.app",
    "run_id": "c709922b-cce1-4b9c-986d-5eac25f3caad",
    "screen_id": "screen_819fbab46fa2cd6d",
    "screen_ref": {
      "run_id": "c709922b-cce1-4b9c-986d-5eac25f3caad",
      "screen_id": "screen_819fbab46fa2cd6d"
    },
    "screen": {
      "fingerprint": "full-screen-fingerprint",
      "package": "com.example.app",
      "activity": ".MainActivity",
      "stable": true,
      "element_count": 18,
      "appium_result": "artifacts/explorations/.../appium-result.json",
      "viewer": "artifacts/explorations/.../appium_screen_content.html"
    },
    "actions_endpoint": "/api/v1/explorations/c709922b-cce1-4b9c-986d-5eac25f3caad/actions"
  }
}
```

Use `screen_ref.run_id` in the action endpoint path and
`screen_ref.screen_id` as the action request's `screen_id`. An invalid package
ID returns `400 validation_error`. Appium connection failures, missing
installed packages, or activation failures return `502
external_service_failed`.

## Export LLM-ready screen context

Convert either a stored screen pointer or an inline canonical capture into
compact Markdown:

```http
POST /api/v1/explorations/context
Content-Type: application/json
```

```json
{
  "screen_ref": {
    "run_id": "c709922b-cce1-4b9c-986d-5eac25f3caad",
    "screen_id": "screen_819fbab46fa2cd6d"
  },
  "options": {}
}
```

The response includes the LLM-ready `text`, character and estimated-token
counts, source identity, structured coverage, screenshot reference, truncation
status, and warnings. Important evidence is unlimited by default. Explicit
options can limit actions, text, semantic elements, or final characters and can
exclude screenshot, device, system, or capture-quality sections.

The endpoint also accepts a complete canonical `appium.screen_capture` object
in `screen` instead of `screen_ref`. Exactly one source is required.

## Monitored exploration actions

All Android interactions use one endpoint and an `action` discriminator:

```http
POST /api/v1/explorations/{run_id}/actions
Content-Type: application/json
```

```json
{
  "screen_id": "screen_819fbab46fa2cd6d",
  "action": "tap",
  "target": {
    "element_id": "element_0015"
  },
  "parameters": {},
  "completion": {
    "any_of": [
      {"condition": "screen_changed"},
      {"condition": "dialog_present"}
    ],
    "timeout_ms": 15000,
    "interval_ms": 400,
    "stable_samples": 3
  }
}
```

The endpoint is synchronous and returns only after the bounded observation loop
has reached a stable result or timed out. The result separates:

- Appium delivery and target resolution
- Observable hierarchy, screenshot, activity, package, dialog, permission, and
  keyboard effects
- Application process, foreground, crash, and ANR health
- First-change, stabilization, capture, and total timings
- Before/after canonical screen references
- Final classification and errors

The result is stored under
`artifacts/explorations/{run_id}/transitions/{transition_id}/result.json`, in
SQLite, and as an edge in `graph.json`.

Supported action families include element interaction, text entry, gestures,
Android navigation and keys, dialogs and permissions, app lifecycle, hybrid
contexts, coordinate fallbacks, assertions, capture, wait, and recovery.

Captured element IDs are resolved fresh for each action using resource ID,
accessibility description, XPath, and finally coordinates. The requested
screen is validated by package, activity, orientation, and a normalized
semantic-element hash before dispatch. If it is stale, the endpoint returns
`409 conflict` and stores
the actual live screen so the caller can make a new decision.

After an acknowledged state-changing action, the executor never retries the
action automatically. It observes and classifies the outcome to avoid duplicate
submissions, sends, payments, or deletions.

## Logic module

Logic modules provide safe template-based mapping without accepting arbitrary
Python code over HTTP.

```json
{
  "id": "normalize-user",
  "type": "logic",
  "name": "Normalize user input",
  "config": {
    "merge_input": false,
    "output": {
      "display_name": "{{ input.first_name }} {{ input.last_name }}",
      "source": "{{ input }}"
    }
  }
}
```

An exact reference such as `{{ input }}` preserves its native JSON type.
References embedded in larger text render as strings.

## HTTP module

```json
{
  "id": "load-profile",
  "type": "http",
  "config": {
    "method": "GET",
    "url": "https://example.com/users/{{ input.user_id }}",
    "headers": {
      "Authorization": "Bearer {{ env.PROFILE_API_TOKEN }}"
    },
    "timeout": 30,
    "max_response_bytes": 1048576,
    "raise_for_status": true
  }
}
```

The output contains `status_code`, `ok`, `url`, response `headers`, parsed
`body`, `content_type`, and `elapsed_ms`. HTTP calls always have an explicit
timeout and bounded response size.

## LLM module

Prompts belong to the module definition and are rendered from module input:

```json
{
  "id": "summarize",
  "type": "llm",
  "config": {
    "provider": "http_chat",
    "system_prompt": "Return a concise summary for {{ input.audience }}.",
    "user_prompt": "{{ input.content }}",
    "output_format": "text",
    "provider_config": {
      "endpoint": "https://provider.example/v1/chat/completions",
      "model": "provider-model-name",
      "api_key_env": "LLM_API_KEY",
      "timeout": 60,
      "options": {
        "temperature": 0.2
      }
    }
  }
}
```

Secrets are referenced by environment-variable name rather than stored in module
definitions. `echo` is a deterministic provider for development and tests.
`http_chat` supports chat-completion-style JSON APIs. New providers implement
the `LLMProvider` protocol and register with `LLMProviderRegistry`.

## Workflow

If a step omits `input`, it receives the previous step's output. A step can map
from `initial`, `previous`, or named `steps`:

```json
{
  "input": {
    "user_id": 42
  },
  "steps": [
    {
      "id": "profile",
      "module_id": "load-profile"
    },
    {
      "id": "summary",
      "module_id": "summarize",
      "input": {
        "audience": "support",
        "content": "{{ steps.profile.body }}"
      }
    }
  ]
}
```

Each response contains the final output plus a run record with per-step timing,
input, output, status, and errors.

## Automation extension point

The `automation` module type is registered with `status=contract_pending`.
Executing it without an injected implementation returns HTTP 501. Its future
operations and input/output schemas will be added only after that contract is
defined.

## Adding trusted custom logic

Backend-owned Python modules implement `BaseModule.execute(input, context)` and
register a factory with `ModuleRegistry.register_type`. This is the extension
point for application-specific logic that is more complex than safe mapping.

The HTTP API intentionally does not execute arbitrary submitted Python source.
Code modules should be reviewed, tested, and deployed with the backend.
