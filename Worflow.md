Below is the proposed implementation plan. The core principle is strict separation:

```text
Appium = observe and act
Application logic = normalize, validate, store, and control
LLM = understand, plan, summarize, and choose
Exploration graph = permanent source of truth
```

## 1. Keep the code structure compact

Avoid creating a folder for every concept. Use one dedicated exploration package:

```text
src/backend/
  exploration/
    models.py       Shared input/output data models
    appium.py       Appium session, observation, and actions
    storage.py      Screens, artifacts, events, and graph persistence
    planner.py      LLM context construction and response parsing
    service.py      Main exploration loop and traversal control
  modules/
    exploration.py Backend module adapter
```

Existing Appium parsing and reporting code should be absorbed into this package. The backend module remains only a thin adapter; exploration logic must not live in API routes.

## 2. Define the permanent domain model first

Before writing exploration behavior, define stable data structures.

### Exploration run

Represents one complete exploration of an APK:

```json
{
  "run_id": "uuid",
  "apk_path": "assets/apps/message.apk",
  "apk_hash": "sha256",
  "status": "running",
  "started_at": "...",
  "completed_at": null,
  "limits": {
    "max_screens": 200,
    "max_actions": 1000,
    "max_depth": 30,
    "max_duration_minutes": 120
  }
}
```

### Screen state

Represents everything known at one moment:

- Screen ID and fingerprint
- Package and activity
- Screenshot
- UI hierarchy
- Visible text
- Elements and bounds
- Status bar and navigation bar
- Dialogs and overlays
- Keyboard visibility
- Orientation and screen dimensions
- Scrollable regions
- Available actions
- Parent screen and action
- LLM interpretation
- Discovery timestamp

### Element state

Every discovered UI element should contain:

```json
{
  "id": "element_17",
  "resource_id": "message.chat:id/continue_button",
  "class": "android.widget.Button",
  "text": "Continue",
  "content_description": null,
  "bounds": {
    "left": 72,
    "top": 1832,
    "right": 1008,
    "bottom": 1968
  },
  "center": {
    "x": 540,
    "y": 1900
  },
  "clickable": true,
  "enabled": true,
  "visible": true,
  "scrollable": false,
  "editable": false,
  "selected": false,
  "checked": false,
  "source": "app"
}
```

### Action

Use a limited, typed action vocabulary:

- `tap`
- `type_text`
- `clear_text`
- `swipe`
- `scroll`
- `press_back`
- `press_home`
- `wait`
- `dismiss`
- `accept_permission`
- `deny_permission`
- `rotate`
- `relaunch`
- `reset_app`
- `finish_screen`

Every action must have a stable ID so it can be tracked as untried, successful, failed, or unsafe.

### Transition

A transition connects two screen states:

```json
{
  "transition_id": "uuid",
  "from_screen_id": "screen_a",
  "to_screen_id": "screen_b",
  "action": {
    "type": "tap",
    "element_id": "element_17"
  },
  "started_at": "...",
  "duration_ms": 932,
  "result": "screen_changed",
  "before_screenshot": "...",
  "after_screenshot": "...",
  "error": null
}
```

## 3. Design persistent storage

Use SQLite for structured data and regular files for large artifacts.

```text
artifacts/explorations/{run_id}/
  exploration.db
  manifest.json
  graph.json
  summary.json
  screens/
    {screen_id}/
      appium-result.json
      appium_screen_content.html
      screenshots/
        screen.png
        status_bar.png
        navigation_bar.png
  transitions/
    {transition_id}/
      before.png
      after.png
      action.json
      result.json
  logs/
    events.jsonl
    errors.jsonl
```

SQLite should store:

- Runs
- Screens
- Elements
- Actions
- Transitions
- Workflows
- LLM decisions
- Facts
- Errors
- Exploration frontier

Every important event should also be appended to `events.jsonl`. This gives us replay and debugging even if the process crashes.

## 4. Build the Appium ground-work service

The Appium layer should expose a small deterministic interface:

```python
class AppiumExplorer:
    def start(apk_path, device) -> SessionInfo: ...
    def observe() -> RawObservation: ...
    def perform(action) -> ActionResult: ...
    def reset() -> ActionResult: ...
    def relaunch() -> ActionResult: ...
    def close() -> None: ...
```

It must not call the LLM.

### `start()`

1. Validate that the APK exists.
2. Calculate APK SHA-256.
3. Read APK metadata:
   - Package name
   - Launch activity
   - Version
   - Minimum SDK
   - Target SDK
   - Supported ABIs
4. Select a compatible device.
5. Confirm Appium is available.
6. Install or reset the application.
7. Create the Appium session.
8. Wait until the initial screen stabilizes.
9. Return session and device information.

### `observe()`

Collect all ground-truth information:

1. Take a screenshot.
2. Fetch the Appium page source.
3. Get package and activity.
4. Get screen dimensions and orientation.
5. Get current contexts.
6. Detect the keyboard.
7. Parse every UI element.
8. Separate app UI from system UI.
9. Detect dialogs and overlays.
10. Detect permission prompts.
11. Identify clickable and editable elements.
12. Identify scrollable containers.
13. Record status and navigation bar regions.
14. Return a normalized observation.

### `perform()`

1. Validate the requested action.
2. Capture a before-action state.
3. Execute the action.
4. Wait for UI stabilization.
5. Capture an after-action state.
6. Compare before and after.
7. Return:
   - Whether the screen changed
   - Whether navigation occurred
   - Whether a dialog appeared
   - Whether the app crashed
   - Whether an external app opened
   - Action duration
   - Any Appium error

## 5. Implement screen stabilization

Screens must not be captured while still loading or animating.

After each action:

1. Capture a lightweight hierarchy fingerprint.
2. Wait a short interval.
3. Capture it again.
4. Repeat until fingerprints match for a defined duration.
5. Stop waiting after a maximum timeout.
6. Record whether the screen stabilized or timed out.

Also capture intermediate behavior when useful:

- Loading spinners
- Progress bars
- Splash screens
- Transient dialogs
- Toast-like changes
- Screen transitions

## 6. Implement screen identity and deduplication

A screen cannot be identified using only its screenshot or activity.

Create a composite fingerprint from:

- Package
- Activity
- Normalized hierarchy
- Resource IDs
- Visible text structure
- Element classes
- Element positions
- Dialog/overlay state
- Screenshot perceptual hash

Dynamic values such as timestamps, message counts, and generated IDs should be normalized.

Classify observations as:

- Exact known screen
- Known screen with changed data
- Known screen with a new dialog
- Known screen at a different scroll position
- Completely new screen

This prevents infinite loops and duplicate exploration.

## 7. Generate available actions deterministically

The LLM should not invent coordinates or Appium commands.

Application logic generates candidates from the observation:

```json
[
  {
    "action_id": "action_1",
    "type": "tap",
    "element_id": "element_17",
    "label": "Continue",
    "tried": false,
    "risk": "safe"
  },
  {
    "action_id": "action_2",
    "type": "scroll",
    "container_id": "element_4",
    "direction": "down",
    "tried": false,
    "risk": "safe"
  }
]
```

Candidate generation should include:

- Clickable elements
- Editable fields
- Scrollable containers
- Back navigation
- Dialog actions
- System permission choices
- Expandable menus
- Tabs and navigation items

Duplicate or invisible actions should be removed.

## 8. Add an action-safety policy

Exploration must not blindly perform potentially harmful actions.

Classify actions:

- `safe`: navigation, tabs, expanding menus, scrolling
- `controlled`: permissions, data entry, external links
- `dangerous`: sending messages, making calls, deleting data, purchases, account changes

The controller—not the LLM—enforces policy.

Possible rules:

- Never perform payments.
- Never confirm account deletion.
- Never place calls.
- Never send real messages.
- Never leave the allowed package unless explicitly permitted.
- Use generated test text for input fields.
- Require explicit approval for destructive actions.

## 9. Build the exploration graph

Maintain:

- Screen nodes
- Action edges
- Tried and untried actions
- Current path
- Backtracking path
- Failed transitions
- Workflow labels
- Known loops

Example:

```text
Launch
 ├─ Continue → Permission explanation
 │                ├─ Allow → System permission dialog
 │                │            ├─ Allow → Inbox
 │                │            └─ Deny → Permission error
 │                └─ Back → Launch
 └─ Privacy policy → Web view
```

Internally, this remains a graph because multiple paths may lead to the same screen.

## 10. Build the deterministic exploration controller

The exploration controller owns the loop.

```text
Start application
Capture screen
Identify/deduplicate screen
Generate actions
Load exploration context
Ask LLM for analysis and choice
Validate choice
Execute through Appium
Capture new state
Store transition
Update graph and memory
Continue or backtrack
```

The controller must always retain authority to:

- Reject malformed LLM output
- Reject nonexistent actions
- Reject unsafe actions
- Retry transient Appium failures
- Backtrack
- Relaunch the app
- Stop at limits
- Resume an interrupted run

## 11. Define the LLM input contract

The LLM receives structured context:

```json
{
  "objective": "Explore the application sufficiently to recreate it",
  "current_screen": {
    "screen_id": "screen_12",
    "screenshot": "...",
    "activity": "...",
    "summary": "...",
    "elements": [],
    "available_actions": []
  },
  "current_path": [],
  "recent_transitions": [],
  "known_workflows": [],
  "global_app_facts": [],
  "unexplored_frontier": [],
  "actions_already_tried": [],
  "safety_policy": {}
}
```

The screenshot should be provided to a vision-capable model when available.

## 12. Define the LLM output contract

Require strict JSON:

```json
{
  "screen": {
    "title": "SMS permission introduction",
    "purpose": "Explains why SMS access is required",
    "workflow": "onboarding",
    "screen_type": "permission_explanation"
  },
  "observations": [
    {
      "category": "behavior",
      "text": "The user cannot reach the inbox without continuing"
    }
  ],
  "new_app_facts": [],
  "recommended_action_id": "action_1",
  "reason": "The primary onboarding path has not been explored",
  "screen_complete": false,
  "confidence": 0.95
}
```

The parser must verify:

- Valid JSON
- Required fields
- Known action ID
- No arbitrary command
- Valid confidence value
- No unsupported action type

Invalid output should trigger a correction request or deterministic fallback.

## 13. Create hierarchical exploration memory

The LLM needs all relevant previous context, but sending every raw screenshot and hierarchy on every step will exceed context limits.

Use four memory layers:

1. **Current state**

   - Full screenshot
   - Full parsed layout
   - All available actions
2. **Current workflow path**

   - Every screen and action from workflow start to the current screen
3. **Relevant historical memory**

   - Similar screens
   - Earlier attempts
   - Related dialogs
   - Previous workflow facts
4. **Global application memory**

   - App purpose
   - Navigation model
   - Design system
   - Recurring components
   - Permissions
   - Known workflows
   - Unexplored branches

All historical knowledge remains stored, but the LLM receives compact summaries plus references to relevant raw evidence.

## 14. Implement traversal and backtracking

Recommended strategy:

1. Let the LLM prioritize meaningful actions.
2. Maintain a deterministic frontier of every untried action.
3. Prefer actions likely to reveal new workflows.
4. Deprioritize repeated navigation and obvious external links.
5. When a screen has no useful untried actions:
   - Press back
   - Confirm return to the parent state
   - Select the next frontier action
6. If back navigation fails:
   - Relaunch
   - Replay the saved action path
7. If a path cannot be replayed:
   - Mark it blocked
   - Store the failure
   - Continue elsewhere

The deterministic frontier ensures the LLM cannot simply forget unexplored buttons.

## 15. Detect complete workflows

A workflow is a meaningful path, such as:

- First launch
- Onboarding
- Permission handling
- Compose message
- Search
- Settings
- Theme selection
- Error handling

Mark a workflow complete when:

- Its reachable screens have been visited.
- Relevant actions have been attempted.
- Alternate dialogs or error paths are recorded.
- Back navigation is understood.
- The LLM produces a final workflow summary.
- The controller finds no remaining safe frontier actions.

## 16. Produce a clone dossier

At the end, export a structured package for future clone generation:

```text
clone-dossier/
  app-summary.json
  navigation-graph.json
  workflows.json
  screens.json
  components.json
  design-system.json
  behaviors.json
  permissions.json
  errors-and-dialogs.json
  assets/
  screenshots/
```

It should describe:

- Every screen
- Every workflow
- Navigation behavior
- Reusable components
- Text content
- Layout measurements
- Colors and typography where detectable
- System bars
- Dialogs and overlays
- Loading and error states
- Permission behavior
- Transition behavior
- Unexplored or blocked areas

## 17. Add recovery and resumability

The exploration may take hours, so every action must be recoverable.

Before executing an action:

1. Persist the selected action.
2. Record the source screen.
3. Mark the transition as started.

After execution:

1. Save the resulting observation.
2. Complete the transition.
3. Update the frontier.
4. Save the current location.

On restart:

1. Load the exploration database.
2. Start the emulator and application.
3. Replay the most reliable path to the last screen.
4. Verify its fingerprint.
5. Continue from the saved frontier.

## 18. Expose it as a module

Initial input:

```json
{
  "apk_path": "assets/apps/message.apk",
  "objective": "Explore all safe user workflows",
  "device": {
    "udid": null
  },
  "limits": {
    "max_screens": 200,
    "max_actions": 1000,
    "max_duration_minutes": 120
  },
  "policy": {
    "allow_external_apps": false,
    "allow_dangerous_actions": false
  }
}
```

Initial output:

```json
{
  "run_id": "uuid",
  "status": "completed",
  "screens_discovered": 83,
  "transitions_recorded": 217,
  "workflows_discovered": 12,
  "artifact_root": "artifacts/explorations/uuid",
  "graph": "artifacts/explorations/uuid/graph.json",
  "clone_dossier": "artifacts/explorations/uuid/clone-dossier"
}
```

Because exploration is long-running, it should eventually expose:

```text
POST /api/v1/explorations
GET  /api/v1/explorations/{run_id}
POST /api/v1/explorations/{run_id}/pause
POST /api/v1/explorations/{run_id}/resume
POST /api/v1/explorations/{run_id}/cancel
GET  /api/v1/explorations/{run_id}/graph
```

For simplicity, begin with a local CLI and synchronous service. Add background API execution only after the exploration engine works reliably.

## 19. Testing strategy

### Unit tests

- UI hierarchy parsing
- Element normalization
- Action generation
- Fingerprinting
- Screen deduplication
- Graph updates
- LLM response validation
- Context construction
- Safety rules

### Fake-Appium integration tests

Simulate:

- Screen changes
- Dialogs
- Loops
- Failed taps
- App crashes
- External app launches
- Backtracking

### Emulator tests

Verify:

- APK installation
- First-screen capture
- Tap transition
- Dialog detection
- Screenshot and hierarchy storage
- Relaunch and path replay

### Golden-run test

Maintain a small deterministic APK with known screens and workflows. A successful run must discover the expected graph.

## 20. Recommended delivery milestones

### Milestone 1: Ground-truth capture

- APK input
- Appium session
- One complete screen observation
- Screenshot, hierarchy, elements, system state
- Persistent storage

### Milestone 2: Deterministic navigation

- Typed actions
- Before/after capture
- Screen fingerprints
- Graph construction
- Backtracking
- No LLM yet

### Milestone 3: LLM-guided exploration

- Structured LLM input/output
- Action selection
- Screen interpretation
- Workflow classification
- Memory summaries

### Milestone 4: Autonomous traversal

- Frontier management
- Loop prevention
- Recovery
- Pause/resume
- Exploration limits
- Safety policies

### Milestone 5: Clone knowledge export

- Workflow summaries
- Component inventory
- Design-system observations
- Behavioral documentation
- Clone dossier

### Milestone 6: Backend integration

- Exploration module
- Long-running API
- Status endpoints
- Graph and artifact access

The first implementation should stop after Milestone 1: given an APK path, reliably launch the app and create one extremely complete, structured screen-state record. Everything else depends on that observation contract being correct.

## Implementation status

Milestone 1 is implemented in `src/backend/exploration/`.

- APK path validation, hashing, and `aapt` metadata extraction
- Appium readiness check, session creation, stabilization, and cleanup
- One complete screen observation with app, system, dialog, permission, keyboard,
  status bar, navigation bar, viewport, element, screenshot, and hierarchy data
- One decoupled `appium-result.json` contract combining all current-screen
  evidence, observations, collection errors, and relative artifact references
- A minimal per-screen output containing only the canonical result, a screenshot
  folder, and `appium_screen_content.html` for visual inspection
- Raw hierarchy and normalized elements embedded in the canonical result instead
  of duplicated XML, CSV, and projection JSON files
- A deterministic `appium-result.json` to compact Markdown transformer that
  prioritizes actions, visible text, UI/system state, device context, and capture
  quality while omitting repetitive or token-heavy raw evidence
- Loss-safe defaults that retain every action, visible text occurrence, explicit
  action state, and semantic element; size limits apply only when requested
- A three-step viewer flow: captured evidence, LLM-ready context, and a reserved
  LLM-output stage for the next milestone
- Read-only ADB system probing for authoritative insets, navigation mode, display
  metrics, rotation, focused window, System UI flags, Android build, and IME state
- Exact status-bar and navigation-bar crops derived from reported system insets
- Persistent SQLite records plus canonical JSON, PNG, HTML, graph, manifest,
  summary, and event-log artifacts
- Synchronous CLI entry point through `python -m backend.exploration`
- Context CLI entry point through `python -m backend.exploration.llm_context`
- Fake-Appium and persistence tests for the complete Milestone 1 boundary

Navigation, action execution, graph traversal, and LLM planning intentionally
remain outside this milestone.
