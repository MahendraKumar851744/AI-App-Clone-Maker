# Simple app exploration workflow

This is the first orchestration layer above the Appium HTTP service.

```text
inspect runtime
  -> provision only when components are missing
  -> start only when the device/Appium are not ready
  -> reuse an existing installed app, or install the APK when absent
  -> open installed app
  -> export current screen as LLM context
  -> build system and user prompts
  -> ask the LLM for strict JSON
  -> validate and transform the decision in user-owned logic
  -> call the generic Appium action endpoint
  -> repeat with the returned screen pointer
```

The LLM never calls Appium directly. It selects from captured actions, while
Python validates the result and adds the authoritative `screen_id`.

## Run

1. Start the backend.
2. Copy `simple-app-exploration.example.json` and configure the APK, package, and
   chat-completion-compatible LLM endpoint.
3. If the runtime requires first-time Android setup, explicitly configure the
   provisioning authorization fields.
4. Run:

```powershell
$env:PYTHONPATH = "src"
python -m backend.workflows.exploration workflows\simple-app-exploration.json
```

The workflow stops when the LLM returns `finish` or `max_iterations` is
reached. Every HTTP step, LLM decision, action, and screen transition is
included in the final JSON result.

## Initialization policy

`install_policy` defaults to `if_missing`.

- If the expected package is already installed, it is reused without modifying
  its data.
- If it is absent, the APK is installed using the non-destructive `preserve`
  API mode.
- `clean` removes existing application data and runs only when explicitly set.
- `replace` updates an installation while preserving data when Android permits.

Runtime provisioning follows the same reuse rule. Provisioning is submitted
only when `provisioned` is false, and startup is submitted only when `ready` is
false. Asynchronous jobs are polled to success before the next workflow step.

## Separation of responsibilities

- `client.py`: only communicates with the Appium HTTP wrapper.
- `llm.py`: owns prompts, strict JSON parsing, retries, and action validation.
- `exploration.py`: owns step order, state, history, and the bounded loop.

This initial workflow deliberately does not implement graph-wide backtracking,
persistence/resume, or autonomous branch coverage. Those can be added after
the simple loop is verified with real model decisions.
