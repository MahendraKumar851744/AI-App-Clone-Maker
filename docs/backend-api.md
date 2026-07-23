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
```

Module definitions and run history are currently process-local. Persistence,
authentication, authorization, and multi-worker coordination belong in later
infrastructure layers.

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
