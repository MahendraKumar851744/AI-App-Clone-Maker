# Initialize Appium

This workflow ensures Appium is ready, verifies and opens the configured
application, and then starts an LLM-centered application research agent.

Before installation, it now inspects the selected APK and derives its native
ABIs, minimum/target Android API, package identity, launch activity, and 16 KB
native-library alignment. It inventories connected devices and configured
AVDs, reuses a compatible device when possible, otherwise starts and verifies
AVD candidates, and can provision a compatible profile when enabled.

Static AVD metadata is treated only as a candidate signal. After boot, ADB must
confirm a matching ABI, sufficient API level, compatible memory page size, and
completed boot. The verified device ID is then used consistently for package
inspection, APK installation, and Appium launch.

Each iteration deliberately separates three responsibilities:

1. The screen analyst builds or revises the current screen's detailed dossier.
2. The traversal planner chooses the next Appium action from focused graph,
   branch-memory, and frontier context.
3. The memory compiler summarizes a growing branch before its context becomes
   too large.

Appium remains the observation and execution layer. The LLM maintains the
meaning of the application: what every screen does, how it is composed, which
states and controls were observed, what transitions mean, which workflows cross
multiple screens, and what still needs investigation.

Canonical screens, individual visits, semantic transitions, and traversal
branches are stored separately. Revisiting the same screen enriches its dossier
without erasing earlier facts or state observations.

```cmd
.\.runtime\python\Scripts\python.exe -B -m backend.workflows run initialize_appium
```

Every live run persists:

- `exploration-graph.json` for canonical screens, visits, controls, states,
  semantic transitions, branches, summaries, and the unresolved frontier.
- `knowledge-box.json` for application-wide features, domain objects, UI
  patterns, navigation, facts, workflows, failures, notes, and open questions.

The live viewer presents the graph without raw JSON. Select a screen node to
inspect its complete dossier, including UI composition, functionality,
controls, states, workflows, evidence, visits, and revision history. LLM steps
show their formatted request and response independently.

The iteration budget, objective, and memory thresholds are configured in
`config.json`. `memory.context_budget_characters` triggers summarization when a
focused context grows too large, while `memory.summarize_after_changes` keeps
long-running branches compact even before the size limit is reached.

Device behavior is configured under `workflow.device_selection`:

```json
{
  "auto_start_avd": true,
  "allow_provision": true,
  "boot_timeout_seconds": 300
}
```

Set `workflow.device_id` to a serial only when a particular connected device
must be used. Leave it `null` for automatic evidence-based selection.
