# LLM exploration API handoff

## 1. Purpose

This document defines the boundary between Android screen capture, LLM
reasoning, and monitored Appium action execution.

The intended workflow starts by opening an installed package and then has three
screen-processing stages:

```text
Bootstrap API opens installed package and returns screen_ref
                      |
                      v
Step 1: Appium captures complete screen evidence
                      |
                      v
Step 2: Context API creates compact LLM-ready text
                      |
                      v
Step 3: LLM selects one action from the current screen
                      |
                      v
Action API executes and monitors that action
                      |
                      v
New Step 1 screen capture and transition record
```

The API operations covered here are:

1. Open an installed Android package and return the first captured-screen
   pointer.
2. Convert one canonical `appium-result.json` screen into loss-safe,
   LLM-friendly context.
3. Execute any supported action against a captured screen and return a monitored
   before/after result.

## 2. Current implementation status

| Capability                                       | Status                                            |
| ------------------------------------------------ | ------------------------------------------------- |
| Installed-package launch and screen pointer API | Implemented                                       |
| Canonical `appium-result.json` capture           | Implemented and real-device tested                |
| JSON-to-LLM Markdown transformer                 | Implemented and tested as Python/CLI              |
| HTTP context-export endpoint                     | Implemented                                       |
| Generic monitored action HTTP endpoint           | Implemented                                       |
| Target resolution and stale-screen protection    | Implemented                                       |
| Action observation/stabilization loop            | Implemented                                       |
| Before/after capture and transition persistence  | Implemented                                       |
| Real-device action validation                    | `tap` tested end to end                         |
| Remaining action primitives                      | Wired; require action-by-action device validation |
| Asynchronous action jobs/SSE/WebSocket callbacks | Not implemented                                   |
| Authentication/authorization                     | Not implemented                                   |
| Request idempotency keys                         | Not implemented                                   |
| LLM provider invocation and Step 3 parsing       | Not implemented                                   |

The receiving LLM must not assume that a proposed endpoint or unvalidated action
is already production-ready.

## Bootstrap API: open an installed package

```http
POST /api/v1/explorations/open
Content-Type: application/json
```

Request:

```json
{
  "package_id": "com.example.app"
}
```

`package_id` is the Android application ID of an app that is already installed
on the Appium device. The operation does not accept an APK path, install an APK,
clear application data, or reset the application.

The synchronous operation creates a UiAutomator2 session, activates the
package, waits for hierarchy stability, captures the complete canonical screen,
persists a new exploration run, and retains the session for the action API.

Response (`201 Created`):

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

The LLM or orchestrator must retain `screen_ref`. Its `run_id` selects the live
session and its `screen_id` is the exact evidence pointer required by the next
action request.

Errors:

- `400 validation_error`: missing, malformed, or unsupported request fields.
- `502 external_service_failed`: Appium is unavailable, the package is not
  installed, activation failed, or initial capture failed.

## 3. Terminology

### Canonical screen

The complete Appium output stored as:

```text
artifacts/explorations/{run_id}/screens/{screen_id}/appium-result.json
```

Its contract is:

```json
{
  "contract": "appium.screen_capture",
  "schema_version": 1
}
```

It contains the screenshot metadata, raw hierarchy, normalized elements,
Android system state, device state, collection errors, and artifact references.

### LLM context

A compact Markdown representation of the canonical screen. It preserves the
important semantic evidence required for action selection without sending the
raw hierarchy, complete capabilities, hashes, or repetitive layout containers.

### Captured element ID

An identifier such as `element_0015` assigned within one canonical capture.
It is not a permanent Appium element ID.

Before action dispatch, the action executor resolves it back to the live screen
using:

1. Resource ID
2. Accessibility/content description
3. XPath
4. Captured bounds and coordinates

### Semantic screen identity

Package, activity, orientation, and a normalized semantic hash of elements.
Volatile Android window metadata is excluded. This allows the action manager to
detect genuinely stale screens without rejecting the same screen after an
Appium session restart.

### Observable effect

A detectable change to hierarchy, screenshot, package, activity, dialog,
permission prompt, or keyboard state.

An acknowledged action may legitimately produce `no_observable_change`.
Acknowledgement and observable effect are different facts.

---

# API 1: Export LLM-ready screen context

## 4. Endpoint

Implemented endpoint:

```http
POST /api/v1/explorations/context
Content-Type: application/json
```

This endpoint should accept exactly one screen source:

- A stored screen reference; or
- An inline canonical screen document.

## 5. Stored-screen request

Preferred for screens already captured by this project:

```json
{
  "screen_ref": {
    "run_id": "1f529b48-3722-4321-a0cc-1b4522102319",
    "screen_id": "screen_08740d9a5d914505"
  },
  "options": {}
}
```

## 6. Inline-screen request

Useful when the caller already has an Appium result:

```json
{
  "screen": {
    "contract": "appium.screen_capture",
    "schema_version": 1,
    "screen_id": "screen_08740d9a5d914505",
    "...": "complete canonical screen object"
  },
  "options": {}
}
```

The request must not contain both `screen_ref` and `screen`.

## 7. Context options

Important content is unlimited by default.

```json
{
  "options": {
    "format": "markdown",
    "max_actions": 0,
    "max_text_items": 0,
    "max_elements": 0,
    "max_characters": 0,
    "include_screenshot_reference": true,
    "include_device_context": true,
    "include_system_context": true,
    "include_capture_quality": true
  }
}
```

| Field                            |    Type |      Default | Meaning                                        |
| -------------------------------- | ------: | -----------: | ---------------------------------------------- |
| `format`                       |  string | `markdown` | Initially only`markdown`                     |
| `max_actions`                  | integer |        `0` | `0` retains every action                     |
| `max_text_items`               | integer |        `0` | `0` retains every visible text occurrence    |
| `max_elements`                 | integer |        `0` | `0` retains every important semantic element |
| `max_characters`               | integer |        `0` | `0` disables final character truncation      |
| `include_screenshot_reference` | boolean |     `true` | Include screenshot artifact path               |
| `include_device_context`       | boolean |     `true` | Include Android/display context                |
| `include_system_context`       | boolean |     `true` | Include bars, keyboard, dialogs, permissions   |
| `include_capture_quality`      | boolean |     `true` | Include stability, coverage, and errors        |

Limits must only be applied when explicitly requested. The server should not
silently remove important evidence.

## 8. Context response

Recommended response:

```json
{
  "context": {
    "contract": "appium.llm_screen_context",
    "schema_version": 1,
    "format": "text/markdown",
    "text": "# CURRENT ANDROID SCREEN CONTEXT\n...",
    "characters": 2929,
    "estimated_tokens": 733,
    "source": {
      "run_id": "1f529b48-3722-4321-a0cc-1b4522102319",
      "screen_id": "screen_08740d9a5d914505",
      "screen_contract": "appium.screen_capture",
      "screen_schema_version": 1
    },
    "coverage": {
      "actions": {
        "included": 3,
        "available": 3,
        "complete": true
      },
      "visible_text_occurrences": {
        "included": 5,
        "available": 5,
        "complete": true
      },
      "semantic_elements": {
        "included": 0,
        "available": 0,
        "complete": true
      },
      "truncated": false
    },
    "visual_evidence": {
      "screenshot": "screens/screen_08740d9a5d914505/screenshots/screen.png",
      "should_accompany_context": true
    },
    "warnings": []
  }
}
```

## 9. Required context sections

The Markdown text should contain:

```text
CURRENT ANDROID SCREEN CONTEXT
SCREEN
AVAILABLE ACTIONS
VISIBLE TEXT
UI STATE
DEVICE CONTEXT
OTHER IMPORTANT ELEMENTS
CAPTURE QUALITY
CONTEXT COVERAGE
```

### Screen

- Screen ID
- App label
- Package
- Activity
- Foreground state
- Orientation
- Window dimensions
- Capture timestamp
- Stability
- Screenshot reference

### Available actions

For every actionable element:

- Captured element ID
- Recommended primitive
- Direct or inferred label
- Android class
- Resource ID
- Bounds
- Explicit state values

Example:

```text
- [element_0015] tap | "Set as Default" | FrameLayout |
  id=message.chat.text.messaging.sms:id/flSetDefault |
  bounds=[62,1831][1018,1996] |
  state=displayed=yes,enabled=yes,clickable=yes,long_clickable=no,
  editable=no,scrollable=no,checkable=no,checked=no,selected=no,focused=no
```

### Visible text

Every visible occurrence must be retained. Identical text at different bounds
must not be deduplicated.

### UI and system state

- Dialog presence
- Permission prompt presence
- Keyboard state
- Status bar visibility and bounds
- Navigation bar visibility, bounds, and mode

### Context coverage

The response must explicitly say how much was included and whether truncation
occurred. Shortness must never be confused with completeness.

## 10. Context-generation invariants

1. Every action is retained by default.
2. Every visible text occurrence is retained by default.
3. Explicit `false` action states are retained.
4. System dialogs and permission state are retained.
5. Collection errors are retained.
6. The screenshot reference is retained.
7. The full canonical result remains the full-fidelity fallback.
8. Optional limits produce visible coverage warnings.
9. The server never claims `complete: true` after truncation.
10. Context generation has no side effects on the device.

## 11. Context API errors

### Invalid request

```http
400 Bad Request
```

```json
{
  "error": {
    "code": "validation_error",
    "message": "Provide exactly one of 'screen_ref' or 'screen'."
  }
}
```

### Stored run or screen missing

```http
404 Not Found
```

```json
{
  "error": {
    "code": "not_found",
    "message": "Screen 'screen_123' does not exist in run 'run_123'."
  }
}
```

### Unsupported source contract

```http
422 Unprocessable Entity
```

```json
{
  "error": {
    "code": "unsupported_screen_contract",
    "message": "Expected appium.screen_capture schema version 1."
  }
}
```

### Payload too large

```http
413 Payload Too Large
```

Inline canonical documents are subject to the backend request-size limit.
Stored references are preferred for large captures.

## 12. CLI usage

The same transformer remains available without the web server:

```powershell
.\.runtime\python\Scripts\python.exe -B `
  -m backend.exploration.llm_context `
  .\artifacts\explorations\{run_id}\screens\{screen_id}\appium-result.json
```

Optional file output:

```powershell
... --output .\context.md
```

---

# API 2: Execute and monitor one action

## 13. Endpoint

Implemented endpoint:

```http
POST /api/v1/explorations/{run_id}/actions
Content-Type: application/json
```

One endpoint handles all action types through the `action` discriminator.

The current endpoint is synchronous. It returns after:

- The action reaches a stable observed result;
- A completion condition is satisfied and stabilized;
- The action fails; or
- The configured timeout expires.

## 14. Common action request

```json
{
  "screen_id": "screen_08740d9a5d914505",
  "action": "tap",
  "target": {
    "element_id": "element_0015"
  },
  "parameters": {},
  "completion": {
    "any_of": [
      {"condition": "screen_changed"},
      {"condition": "dialog_present"},
      {"condition": "permission_prompt_present"}
    ],
    "timeout_ms": 15000,
    "interval_ms": 400,
    "stable_samples": 3
  }
}
```

## 15. Top-level request fields

| Field          | Type   |         Required | Meaning                                        |
| -------------- | ------ | ---------------: | ---------------------------------------------- |
| `screen_id`  | string |              yes | Captured screen on which the decision was made |
| `action`     | string |              yes | Supported action discriminator                 |
| `target`     | object | action-dependent | Captured element target                        |
| `parameters` | object | action-dependent | Action-specific arguments                      |
| `completion` | object |               no | Completion and monitoring policy               |

Unknown actions must be rejected before Appium dispatch.

## 16. Target schema

Preferred target:

```json
{
  "target": {
    "element_id": "element_0015"
  }
}
```

The client/LLM should not send a live Appium element ID. Live IDs are
session-specific and are resolved by the action executor.

Coordinate-only actions use `parameters.point`, `parameters.start`, or
`parameters.end`.

## 17. Completion schema

### Single condition

```json
{
  "completion": {
    "condition": "activity_changed",
    "timeout_ms": 15000,
    "interval_ms": 400,
    "stable_samples": 3
  }
}
```

### Any-of conditions

```json
{
  "completion": {
    "any_of": [
      {"condition": "screen_changed"},
      {"condition": "dialog_present"},
      {"condition": "package", "value": "com.android.settings"}
    ]
  }
}
```

Supported conditions:

| Condition                     | Additional fields                            | Meaning                                  |
| ----------------------------- | -------------------------------------------- | ---------------------------------------- |
| `screen_changed`            | none                                         | Any observable screen change             |
| `activity_changed`          | none                                         | Android activity differs                 |
| `dialog_present`            | none                                         | Dialog appears                           |
| `permission_prompt_present` | none                                         | Android permission/role UI appears       |
| `keyboard_visible`          | `value: boolean`                           | Keyboard reaches requested visibility    |
| `app_foreground`            | `value: boolean`                           | Target app reaches foreground/background |
| `package`                   | `value: string`                            | A specific package is foreground         |
| `element_present`           | `element_id`, `resource_id`, or `text` | Matching element appears                 |

Monitoring defaults:

| Field              |   Default |      Range |
| ------------------ | --------: | ---------: |
| `timeout_ms`     | `15000` | 500–60000 |
| `interval_ms`    |   `400` |   50–2000 |
| `stable_samples` |     `3` |      1–10 |

Without an explicit condition, completion means:

```text
Appium acknowledgement
+ application remains queryable
+ resulting semantic state is stable
```

## 18. Supported action names

### Element interaction

```text
tap
double_tap
long_press
focus
type_text
replace_text
clear_text
set_checked
select_option
submit
```

### Gestures

```text
swipe
scroll
scroll_to
fling
drag
drag_and_drop
pinch_open
pinch_close
gesture_sequence
```

### Android navigation and system UI

```text
back
home
recent_apps
press_key
hide_keyboard
set_orientation
open_notifications
close_system_panel
```

### Dialogs and permissions

```text
accept_dialog
dismiss_dialog
choose_dialog_action
allow_permission
deny_permission
tap_outside
```

### Application lifecycle

```text
activate_app
background_app
terminate_app
restart_app
reset_app
start_activity
open_deep_link
return_to_start
```

### Hybrid/WebView

```text
switch_context
switch_window
switch_frame
web_select_option
web_submit
```

### Coordinate fallback

```text
tap_point
long_press_point
swipe_points
drag_points
```

### Exploration control

```text
wait
wait_until_stable
capture_screen
assert_screen
assert_element
no_action
recover
mark_terminal
```

## 19. Action parameter schemas

### Tap, focus, and direct selection

Actions:

```text
tap
focus
select_option
choose_dialog_action
web_select_option
```

```json
{
  "target": {"element_id": "element_0015"},
  "parameters": {}
}
```

`select_option` and `web_select_option` currently use basic click behavior and
require deeper semantic handling before being called production-complete.

### Double tap

```json
{
  "action": "double_tap",
  "target": {"element_id": "element_0020"},
  "parameters": {}
}
```

### Long press

```json
{
  "action": "long_press",
  "target": {"element_id": "element_0020"},
  "parameters": {
    "duration": 1000
  }
}
```

### Type text

```json
{
  "action": "type_text",
  "target": {"element_id": "element_0042"},
  "parameters": {
    "text": "Mahendra"
  },
  "completion": {
    "condition": "screen_changed"
  }
}
```

### Replace text

```json
{
  "action": "replace_text",
  "target": {"element_id": "element_0042"},
  "parameters": {
    "text": "Mahendra Kumar"
  }
}
```

### Clear text

```json
{
  "action": "clear_text",
  "target": {"element_id": "element_0042"}
}
```

### Set checkbox or switch state

```json
{
  "action": "set_checked",
  "target": {"element_id": "element_0030"},
  "parameters": {
    "checked": true
  }
}
```

The executor reads the current state and clicks only when it differs from the
requested state.

### Submit keyboard editor action

```json
{
  "action": "submit",
  "parameters": {
    "editor_action": "search"
  }
}
```

Common values:

```text
done
go
next
previous
search
send
```

### Swipe

```json
{
  "action": "swipe",
  "target": {"element_id": "element_0025"},
  "parameters": {
    "direction": "up",
    "percent": 0.75,
    "speed": 1200
  }
}
```

The target is optional for a screen-level swipe.

### Scroll

```json
{
  "action": "scroll",
  "target": {"element_id": "element_0025"},
  "parameters": {
    "direction": "down",
    "percent": 0.7,
    "speed": 900
  }
}
```

### Scroll to selector

```json
{
  "action": "scroll_to",
  "parameters": {
    "strategy": "text",
    "selector": "Privacy Policy"
  }
}
```

### Fling

```json
{
  "action": "fling",
  "target": {"element_id": "element_0025"},
  "parameters": {
    "direction": "down",
    "speed": 5000
  }
}
```

### Drag

```json
{
  "action": "drag",
  "target": {"element_id": "element_0031"},
  "parameters": {
    "end": {"x": 850, "y": 1200},
    "speed": 1000
  }
}
```

### Drag and drop

```json
{
  "action": "drag_and_drop",
  "target": {"element_id": "element_0031"},
  "parameters": {
    "destination_element_id": "element_0048"
  }
}
```

### Pinch

```json
{
  "action": "pinch_open",
  "target": {"element_id": "element_0022"},
  "parameters": {
    "percent": 0.7,
    "speed": 800
  }
}
```

`pinch_close` uses the same schema.

### Generic W3C gesture sequence

```json
{
  "action": "gesture_sequence",
  "parameters": {
    "actions": [
      {
        "type": "pointer",
        "id": "finger1",
        "parameters": {"pointerType": "touch"},
        "actions": [
          {"type": "pointerMove", "duration": 0, "x": 100, "y": 500},
          {"type": "pointerDown", "button": 0},
          {"type": "pointerMove", "duration": 600, "x": 900, "y": 500},
          {"type": "pointerUp", "button": 0}
        ]
      }
    ]
  }
}
```

### Android key

```json
{
  "action": "press_key",
  "parameters": {
    "keycode": 66,
    "metastate": 0,
    "flags": 0,
    "isLongPress": false
  }
}
```

`home` and `recent_apps` are semantic aliases for Android key events.

### Orientation

```json
{
  "action": "set_orientation",
  "parameters": {
    "orientation": "LANDSCAPE"
  }
}
```

Allowed values:

```text
PORTRAIT
LANDSCAPE
```

### Dialog and permission actions

With a captured button:

```json
{
  "action": "allow_permission",
  "target": {"element_id": "element_0008"}
}
```

Without a captured target:

```json
{
  "action": "accept_dialog"
}
```

Targeted dialog buttons are preferred because their meaning is explicit.

### App activation and termination

```json
{
  "action": "activate_app",
  "parameters": {
    "package": "message.chat.text.messaging.sms"
  }
}
```

The `package` defaults to the package from the requested screen.

### Background app

```json
{
  "action": "background_app",
  "parameters": {
    "seconds": 2
  }
}
```

### Restart app

```json
{
  "action": "restart_app"
}
```

Restart preserves application data.

### Reset app

```json
{
  "action": "reset_app",
  "parameters": {
    "allow_destructive": true
  }
}
```

`reset_app` clears application data and must be explicitly authorized.

### Start activity

```json
{
  "action": "start_activity",
  "parameters": {
    "intent": "com.example.app/.SettingsActivity"
  }
}
```

### Open deep link

```json
{
  "action": "open_deep_link",
  "parameters": {
    "url": "myapp://profile/42",
    "package": "com.example.app"
  }
}
```

### Switch native/WebView context

```json
{
  "action": "switch_context",
  "parameters": {
    "context": "WEBVIEW_com.example.app"
  }
}
```

Return to native:

```json
{
  "action": "switch_context",
  "parameters": {
    "context": "NATIVE_APP"
  }
}
```

### Coordinate tap

```json
{
  "action": "tap_point",
  "parameters": {
    "point": {"x": 540, "y": 1800}
  }
}
```

Coordinates are currently absolute pixels. Normalized percentage coordinates
are a known requirement but are not implemented.

### Coordinate swipe or drag

```json
{
  "action": "swipe_points",
  "parameters": {
    "start": {"x": 540, "y": 1700},
    "end": {"x": 540, "y": 500},
    "duration_ms": 600
  }
}
```

`drag_points` uses the same schema.

### Wait

```json
{
  "action": "wait",
  "parameters": {
    "seconds": 2
  }
}
```

The maximum direct wait is 30 seconds.

## 20. Execution lifecycle

Every request follows:

```text
Validate request
      |
Load requested canonical screen
      |
Acquire run action lock
      |
Resume or reuse live Appium session
      |
Capture live before-state
      |
Validate semantic screen identity
      |
Resolve target fresh
      |
Dispatch action exactly once
      |
Record Appium acknowledgement/error
      |
Monitor lightweight state in bounded loop
      |
Detect first observable change
      |
Wait for consecutive stable semantic samples
      |
Collect process/crash/ANR evidence
      |
Capture complete after-state
      |
Classify and persist transition
      |
Return complete result
```

## 21. Action result

Contract:

```json
{
  "contract": "appium.action_result",
  "schema_version": 1
}
```

Representative response:

```json
{
  "result": {
    "contract": "appium.action_result",
    "schema_version": 1,
    "action_id": "action_97b7f95d3b3c4176a83c441ceffa57ca",
    "transition_id": "transition_action_97b7f95d3b3c4176a83c441ceffa57ca",
    "run_id": "1f529b48-3722-4321-a0cc-1b4522102319",
    "status": "completed",
    "classification": "succeeded_expected_condition",
    "request": {
      "screen_id": "screen_08740d9a5d914505",
      "action": "tap",
      "target": {"element_id": "element_0015"},
      "parameters": {},
      "completion": {
        "timeout_ms": 15000,
        "interval_ms": 400,
        "stable_samples": 3
      }
    },
    "before": {
      "screen_id": "screen_08740d9a5d914505",
      "fingerprint": "...",
      "live_screen_id": "screen_7b8bf732bb823bc7",
      "live_fingerprint": "..."
    },
    "delivery": {
      "status": "acknowledged",
      "target_resolved": true,
      "dispatched": true,
      "acknowledged": true,
      "resolved_target": {
        "element_id": "element_0015",
        "locator_used": {
          "strategy": "id",
          "value": "message.chat.text.messaging.sms:id/flSetDefault"
        },
        "appium_element_id": "00000000-0000-0056-ffff-ffff0000000e"
      },
      "result": null,
      "error": null
    },
    "effect": {
      "status": "screen_changed",
      "observable_change": true,
      "hierarchy_changed": true,
      "screenshot_changed": true,
      "package_changed": true,
      "activity_changed": true,
      "dialog_appeared": false,
      "permission_prompt_appeared": true,
      "keyboard_changed": false
    },
    "app_health": {
      "status": "external_app_foreground",
      "expected_package": "message.chat.text.messaging.sms",
      "current_package": "com.google.android.permissioncontroller",
      "foreground": false,
      "process_alive": true,
      "process_ids": [11077],
      "crash_detected": false,
      "anr_detected": false,
      "recent_error_log": ""
    },
    "stability": {
      "stable": true,
      "samples": 4,
      "stable_samples": 3,
      "duration_ms": 6844,
      "first_change_after_ms": 2109,
      "screenshot_changed": true,
      "session_unresponsive": false,
      "completion_condition_met": true,
      "errors": []
    },
    "timings": {
      "target_resolution_ms": 47,
      "dispatch_ms": 141,
      "monitoring_ms": 6844,
      "after_capture_ms": 3609,
      "total_ms": 10969
    },
    "after": {
      "screen_id": "screen_b2ebbaeaed2b1d27",
      "fingerprint": "...",
      "appium_result": ".../screens/screen_b2ebbaeaed2b1d27/appium-result.json",
      "viewer": ".../screens/screen_b2ebbaeaed2b1d27/appium_screen_content.html"
    },
    "errors": [],
    "artifact": ".../transitions/transition_action_.../result.json"
  }
}
```

## 22. Delivery statuses

```text
not_started
target_not_found
action_rejected
dispatched
acknowledged
driver_failed
```

Delivery answers whether Appium accepted the action, not whether the app
produced an effect.

## 23. Effect statuses

```text
screen_changed
no_observable_change
```

Detailed boolean fields identify the individual changes.

## 24. Application-health statuses

```text
healthy
external_app_foreground
process_terminated
crashed
anr
```

Opening Android Settings or the permission controller is external navigation,
not automatically a crash.

## 25. Final classifications

Success/completed:

```text
succeeded_expected_condition
succeeded_screen_changed
succeeded_external_navigation
acknowledged_no_observable_change
```

Failure:

```text
failed_target_missing
failed_precondition
failed_appium_command
failed_session_lost
app_crashed
app_anr
app_terminated
timed_out_unstable
timed_out_no_change
```

## 26. HTTP-level errors

Errors occurring before an action transaction return standard HTTP errors.

### Invalid action or request

```http
400 Bad Request
```

```json
{
  "error": {
    "code": "validation_error",
    "message": "Unsupported action 'fly'."
  }
}
```

### Run or screen not found

```http
404 Not Found
```

```json
{
  "error": {
    "code": "not_found",
    "message": "Screen 'screen_123' does not exist in run 'run_123'."
  }
}
```

### Stale screen

```http
409 Conflict
```

```json
{
  "error": {
    "code": "conflict",
    "message": "The live application is not on the requested captured screen.",
    "details": {
      "expected": {
        "package": "com.example.app",
        "activity": ".MainActivity",
        "orientation": "PORTRAIT",
        "elements_semantic_sha256": "..."
      },
      "actual": {
        "package": "com.example.app",
        "activity": ".SettingsActivity",
        "orientation": "PORTRAIT",
        "elements_semantic_sha256": "..."
      },
      "actual_screen_id": "screen_actual",
      "actual_appium_result": ".../appium-result.json"
    }
  }
}
```

The actual live screen is persisted before returning the conflict so the LLM
can receive fresh context and decide again.

## 27. Persistence

Every completed action is stored in:

```text
artifacts/explorations/{run_id}/
  exploration.db
  graph.json
  logs/events.jsonl
  screens/
    {before_screen_id}/
    {after_screen_id}/
  transitions/
    {transition_id}/
      result.json
```

SQLite tables:

```text
actions
transitions
screens
elements
events
```

The transition becomes an edge:

```json
{
  "transition_id": "transition_action_...",
  "action_id": "action_...",
  "action": "tap",
  "from_screen_id": "screen_before",
  "to_screen_id": "screen_after",
  "classification": "succeeded_screen_changed",
  "artifact_path": ".../result.json"
}
```

## 28. Retry and idempotency rules

### Safe automatic retries

Only before dispatch:

- Temporary target lookup failure
- Temporary Appium read/query failure
- Failed stabilization sample

### Unsafe automatic retries

Never automatically repeat after acknowledgement:

- Tap
- Submit
- Send
- Delete
- Payment/confirmation
- Permission choice
- Lifecycle mutation

The system observes and classifies the result. It does not assume that
`no_observable_change` means the action failed.

### Required future idempotency support

Before high-risk autonomous execution, add a caller-provided key:

```json
{
  "idempotency_key": "decision_123_attempt_1"
}
```

The same key and action payload should return the original result rather than
dispatching again.

## 29. Synchronous result versus future callbacks

Current behavior:

```text
POST action
  -> connection remains open
  -> monitored result returned
```

This gives a complete result but may take up to the configured timeout plus
final capture time.

Potential future asynchronous form:

```text
POST /actions                  -> 202 + action_id
GET  /actions/{action_id}      -> current/final result
GET  /actions/{action_id}/events -> SSE event stream
```

Possible events:

```text
queued
validating
target_resolved
dispatching
acknowledged
change_detected
stabilizing
capturing_after_state
completed
failed
```

These additional endpoints do not exist yet.

---

# LLM integration

## 30. What should be sent to the LLM

Recommended model input:

1. System instructions defining the exploration objective and safety policy.
2. Step 2 Markdown context from the context-export API.
3. Current screenshot when the selected model accepts image input.
4. Previous workflow context and transition summaries.
5. The exact supported action contract from this document.

The complete raw Appium JSON should normally remain outside the model context.
It is available as evidence when detailed investigation is required.

## 31. Required LLM output

The LLM should return strict JSON:

```json
{
  "schema_version": 1,
  "screen_id": "screen_08740d9a5d914505",
  "decision": "perform_action",
  "action": {
    "action": "tap",
    "target": {
      "element_id": "element_0015"
    },
    "parameters": {},
    "completion": {
      "any_of": [
        {"condition": "screen_changed"},
        {"condition": "permission_prompt_present"}
      ],
      "timeout_ms": 15000,
      "stable_samples": 3
    }
  },
  "reasoning_summary": "This is the primary enabled setup action.",
  "expected_result": "Android should ask to set the app as the default SMS app.",
  "confidence": 0.96
}
```

Other decisions:

```text
perform_action
wait
go_back
mark_terminal
request_human_review
```

The executor must ignore any LLM action that is not in the supported action
registry or references an element not present in the stated screen.

## 32. End-to-end loop

```text
Capture screen
    |
    v
POST context export
    |
    v
Send context + screenshot + history to LLM
    |
    v
Validate strict LLM JSON
    |
    v
Apply policy and safety checks
    |
    v
POST monitored action
    |
    v
Inspect action classification
    |
    +--> crash/ANR/failure -> record, recover, or request review
    |
    +--> stale screen -> export context for actual live screen and decide again
    |
    +--> completed -> export context for after-screen
                          |
                          v
                    Continue exploration
```

## 33. Context that must travel across workflow depth

The LLM should receive:

- Current screen context
- Current screenshot
- Path of actions taken to reach the screen
- Important observations from ancestor screens
- Known workflows
- Previously attempted actions on this screen
- Failed/no-change actions
- Loop-detection information
- Exploration objective
- Safety restrictions

Full raw screen evidence remains stored and is referenced, not repeatedly placed
inside every prompt.

## 34. Safety requirements

Before autonomous use:

1. Mark actions as read-only, reversible, state-changing, destructive, or
   externally consequential.
2. Require explicit authorization for destructive actions.
3. Require confirmation or policy approval for send, purchase, delete,
   permission, account, and external communication actions.
4. Serialize actions per device/session.
5. Add idempotency keys.
6. Add total action/time/depth budgets.
7. Prevent actions outside the target application unless system interaction is
   expected.
8. Never execute arbitrary ADB shell supplied by the LLM.
9. Store the exact LLM decision and action request.
10. Preserve before/after evidence for audit.

## 35. Known implementation gaps the receiving LLM should consider

### Context API

- Stored-screen and inline-screen sources are implemented.
- Structured coverage, warnings, source metadata, and visual evidence are
  returned separately from Markdown.
- Only Markdown output is currently supported.

### Action API

- Only `tap` has real-device end-to-end validation.
- Normalized percentage coordinates are not implemented.
- `select_option` needs native spinner/list semantics.
- `web_select_option` needs actual HTML option selection.
- Permission actions need testing across Android versions.
- Complex multi-touch gestures require device validation.
- Lifecycle actions require real-device result validation.
- Crash classification needs a deliberately crashing test APK.
- ANR classification needs a deterministic ANR test.
- `recover` currently performs Back only.
- `return_to_start` restarts rather than following a learned navigation path.
- Async status/events APIs are not implemented.
- Idempotency keys are not implemented.
- Authentication, authorization, and rate limiting are not implemented.
- Multi-process/multi-worker session coordination is not implemented.

## 36. Acceptance criteria

### Context-export API

- Accepts stored and inline canonical screens.
- Rejects invalid contracts.
- Retains all important semantic evidence by default.
- Returns structured coverage and token estimates.
- Produces deterministic output for the same input/options.
- Has no device side effects.
- Never silently truncates.

### Action API

- Supports one generic endpoint and validated action discriminator.
- Never dispatches against a stale semantic screen.
- Resolves captured IDs fresh.
- Reports delivery independently from effect.
- Reports app health independently from effect.
- Uses a bounded stabilization loop.
- Captures the complete after-screen.
- Persists action, transition, graph edge, events, and timings.
- Does not retry acknowledged state-changing actions.
- Correctly distinguishes system/external navigation from crashes.
- Returns explicit errors and classifications.

### LLM integration

- LLM output follows a strict schema.
- Unsupported actions are rejected.
- Unknown element IDs are rejected.
- Safety policy runs before execution.
- Current screen ID is always included.
- Each completed action leads to new context or a terminal/recovery decision.
