# Initialize Appium

This workflow ensures Appium is ready, verifies and opens the configured
application, and then starts a graph-based Qwen exploration agent.

The agent repeatedly observes the current screen, updates its durable app
knowledge, selects the action that provides the most unexplored functional
coverage, executes it through Appium, and records the resulting screen
transition. The target is treated as running in a controlled, disposable
emulator, so permissions, settings, default-app prompts, destructive test-data
operations, and system-UI paths may all be explored. Screens are stored as
fingerprinted graph nodes and actions as directed edges.

```cmd
.\.runtime\python\Scripts\python.exe -B -m backend.workflows run initialize_appium
```

Every live run persists:

- `exploration-graph.json` for screens, actions, and transitions.
- `knowledge-box.json` for app features, facts, workflows, failures, notes, and
  open questions.

The iteration budget and objective are configured in `config.json`.
