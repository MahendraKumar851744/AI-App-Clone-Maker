# Workflow catalog

Each workflow owns one configuration directory under `workflows/` and one
implementation package under `src/backend/workflows/`.

```text
src/backend/workflows/
  shared/                 Common client, config, and Appium runtime logic
  initialize_appium/      Appium initialization and application launch workflow
  simple_exploration/     Qwen-guided exploration workflow
  registry.py             Registered workflow names and default configs
  __main__.py             Common list/run CLI

workflows/
  initialize_appium/
    config.json
  simple_exploration/
    config.json
    example.json
```

## Commands

List registered workflows:

```cmd
.\.runtime\python\Scripts\python.exe -B -m backend.workflows list
```

Run a workflow with its default configuration:

```cmd
.\.runtime\python\Scripts\python.exe -B -m backend.workflows run initialize_appium
```

```cmd
.\.runtime\python\Scripts\python.exe -B -m backend.workflows run simple_exploration
```

Override a workflow configuration:

```cmd
.\.runtime\python\Scripts\python.exe -B -m backend.workflows run simple_exploration --config workflows\simple_exploration\example.json
```

Start the backend before running a workflow:

```cmd
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\start-backend.ps1"
```

The shared config loader automatically reads the project-root `.env` without
overwriting variables already set by the shell. Never commit `.env`.

## Adding another workflow

1. Add `src/backend/workflows/<name>/workflow.py`.
2. Add `src/backend/workflows/<name>/cli.py` with `run_config(config)`.
3. Add `workflows/<name>/config.json`.
4. Register the name, runner, and default config in `registry.py`.
5. Add isolated workflow tests under `tests/`.
