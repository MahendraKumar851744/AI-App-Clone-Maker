from __future__ import annotations

import unittest
from typing import Any

from backend import create_app
from backend.runtime import RuntimeJobNotFoundError


class FakeResponse:
    status_code = 200
    ok = True
    url = "https://example.test/items/42"
    headers = {"Content-Type": "application/json"}
    content = b'{"received": true}'
    text = '{"received": true}'

    def json(self):
        return {"received": True}


class FakeHTTPSession:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def request(self, **kwargs):
        self.requests.append(kwargs)
        return FakeResponse()

    def post(self, *args, **kwargs):
        raise AssertionError("Unexpected LLM HTTP call in unit tests.")


class FakeExplorationActionManager:
    def __init__(self):
        self.calls = []

    def execute(self, run_id, payload):
        self.calls.append((run_id, payload))
        return {
            "contract": "appium.action_result",
            "action_id": "action-1",
            "run_id": run_id,
            "status": "completed",
            "classification": "succeeded_screen_changed",
            "request": payload,
        }

    def launch(self, payload):
        self.calls.append(("launch", payload))
        return {
            "contract": "appium.launch_result",
            "status": "opened",
            "package_id": payload["package_id"],
            "run_id": "run-new",
            "screen_id": "screen-new",
            "screen_ref": {
                "run_id": "run-new",
                "screen_id": "screen-new",
            },
        }


class FakeScreenContextExporter:
    def __init__(self):
        self.calls = []

    def export(self, payload):
        self.calls.append(payload)
        return {
            "contract": "appium.llm_screen_context",
            "format": "text/markdown",
            "text": "# CURRENT ANDROID SCREEN CONTEXT\n",
            "source": {
                "run_id": payload["screen_ref"]["run_id"],
                "screen_id": payload["screen_ref"]["screen_id"],
            },
        }


class FakeRuntimeManager:
    def __init__(self):
        self.calls = []
        self.job = {
            "job_id": "job_123",
            "operation": "provision",
            "status": "queued",
            "status_endpoint": "/api/v1/admin/jobs/job_123",
        }

    def status(self):
        self.calls.append(("status", None))
        return {"contract": "appium.runtime_status", "ready": False}

    def provision(self, payload):
        self.calls.append(("provision", payload))
        return self.job, False

    def start(self, payload):
        self.calls.append(("start", payload))
        return {**self.job, "operation": "start"}, False

    def stop(self, payload):
        self.calls.append(("stop", payload))
        return {
            "contract": "appium.runtime_stop_result",
            "status": "completed",
        }

    def get_job(self, job_id):
        self.calls.append(("get_job", job_id))
        if job_id != "job_123":
            raise RuntimeJobNotFoundError("Runtime job does not exist.")
        return self.job


class FakeAndroidAppManager:
    def __init__(self):
        self.calls = []

    def install(self, payload):
        self.calls.append(("install", payload))
        return {
            "contract": "appium.app_install_result",
            "status": "installed",
            "package_id": payload["expected_package_id"],
        }

    def preflight(self, payload):
        self.calls.append(("preflight", payload))
        return {
            "contract": "appium.apk_device_preflight",
            "status": "inspected",
            "requirements": {
                "native_abis": ["arm64-v8a"],
                "minimum_api_level": 24,
            },
        }

    def prepare_device(self, payload):
        self.calls.append(("prepare_device", payload))
        return {
            "contract": "appium.apk_device_preparation",
            "status": "ready",
            "device_id": "emulator-5556",
        }

    def get(self, package_id, *, device_id=None):
        self.calls.append(("get", package_id, device_id))
        return {
            "contract": "appium.installed_app",
            "installed": True,
            "package_id": package_id,
            "device_id": device_id or "emulator-5554",
        }

    def uninstall(self, package_id, payload):
        self.calls.append(("uninstall", package_id, payload))
        return {
            "contract": "appium.app_uninstall_result",
            "status": "uninstalled",
            "package_id": package_id,
        }


class BackendAPITests(unittest.TestCase):
    def setUp(self):
        self.http_session = FakeHTTPSession()
        self.app = create_app(
            {
                "TESTING": True,
                "HTTP_SESSION": self.http_session,
                "RUN_STORE_MAX_ITEMS": 20,
                "RUNTIME_ADMIN_TOKEN": None,
            }
        )
        self.client = self.app.test_client()

    def create_module(self, payload):
        response = self.client.post("/api/v1/modules", json=payload)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["module"]

    def test_health_and_module_types(self):
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")

        types = self.client.get("/api/v1/module-types").get_json()
        self.assertEqual(
            [item["type"] for item in types["items"]],
            ["automation", "http", "llm", "logic"],
        )
        automation = next(
            item for item in types["items"] if item["type"] == "automation"
        )
        self.assertEqual(automation["status"], "contract_pending")
        self.assertEqual(types["llm_providers"], ["echo", "http_chat"])

    def test_create_execute_and_delete_logic_module(self):
        self.create_module(
            {
                "id": "welcome",
                "type": "logic",
                "config": {
                    "output": {
                        "message": "Hello {{ input.name }}",
                        "profile": "{{ input.profile }}",
                    }
                },
            }
        )

        response = self.client.post(
            "/api/v1/modules/welcome/execute",
            json={"input": {"name": "Mahendra", "profile": {"role": "owner"}}},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["output"]["message"], "Hello Mahendra")
        self.assertEqual(
            response.get_json()["output"]["profile"],
            {"role": "owner"},
        )
        self.assertEqual(response.get_json()["run"]["status"], "completed")

        self.assertEqual(self.client.delete("/api/v1/modules/welcome").status_code, 204)
        self.assertEqual(
            self.client.get("/api/v1/modules/welcome").status_code,
            404,
        )

    def test_workflow_composes_named_step_outputs(self):
        self.create_module(
            {
                "id": "greet",
                "type": "logic",
                "config": {"output": {"greeting": "Hello {{ input.name }}"}},
            }
        )
        self.create_module(
            {
                "id": "decorate",
                "type": "logic",
                "config": {
                    "output": {
                        "message": "{{ input.greeting }}!",
                        "source": "{{ input.source }}",
                    }
                },
            }
        )

        response = self.client.post(
            "/api/v1/workflows/execute",
            json={
                "input": {"name": "World"},
                "steps": [
                    {"id": "greeting", "module_id": "greet"},
                    {
                        "id": "final",
                        "module_id": "decorate",
                        "input": {
                            "greeting": "{{ steps.greeting.greeting }}",
                            "source": "{{ initial.name }}",
                        },
                    },
                ],
            },
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(
            response.get_json()["output"],
            {"message": "Hello World!", "source": "World"},
        )
        self.assertEqual(len(response.get_json()["run"]["steps"]), 2)

    def test_llm_echo_provider_renders_system_and_user_prompts(self):
        self.create_module(
            {
                "id": "prompt",
                "type": "llm",
                "config": {
                    "provider": "echo",
                    "system_prompt": "You process {{ input.kind }}.",
                    "user_prompt": "Summarize: {{ input.text }}",
                    "include_raw_response": True,
                },
            }
        )
        response = self.client.post(
            "/api/v1/modules/prompt/execute",
            json={"input": {"kind": "messages", "text": "Hello there"}},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        output = response.get_json()["output"]
        self.assertEqual(output["text"], "Summarize: Hello there")
        self.assertEqual(output["provider"], "echo")
        self.assertEqual(
            output["raw"]["messages"][0]["content"],
            "You process messages.",
        )

    def test_http_module_renders_request_and_normalizes_response(self):
        self.create_module(
            {
                "id": "fetch-item",
                "type": "http",
                "config": {
                    "method": "POST",
                    "url": "https://example.test/items/{{ input.id }}",
                    "headers": {"X-Request": "{{ input.request_id }}"},
                    "json": {"value": "{{ input.value }}"},
                    "timeout": 5,
                },
            }
        )
        response = self.client.post(
            "/api/v1/modules/fetch-item/execute",
            json={
                "input": {
                    "id": 42,
                    "request_id": "request-1",
                    "value": {"nested": True},
                }
            },
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["output"]["body"], {"received": True})
        sent = self.http_session.requests[0]
        self.assertEqual(sent["url"], "https://example.test/items/42")
        self.assertEqual(sent["headers"], {"X-Request": "request-1"})
        self.assertEqual(sent["json"], {"value": {"nested": True}})
        self.assertEqual(sent["timeout"], 5.0)

    def test_automation_contract_is_explicitly_pending_and_run_is_recorded(self):
        self.create_module(
            {
                "id": "automation-main",
                "type": "automation",
                "config": {},
            }
        )
        response = self.client.post(
            "/api/v1/modules/automation-main/execute",
            json={"input": {}},
        )
        self.assertEqual(response.status_code, 501)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "automation_contract_pending",
        )

        runs = self.client.get("/api/v1/runs").get_json()["items"]
        self.assertEqual(runs[0]["status"], "failed")
        self.assertEqual(runs[0]["steps"][0]["module_id"], "automation-main")

    def test_duplicate_module_returns_structured_conflict(self):
        payload = {
            "id": "same",
            "type": "logic",
            "config": {"output": {}},
        }
        self.create_module(payload)
        response = self.client.post("/api/v1/modules", json=payload)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"]["code"], "conflict")

    def test_single_exploration_action_endpoint_dispatches_any_action(self):
        manager = FakeExplorationActionManager()
        self.app.extensions["exploration_action_manager"] = manager
        payload = {
            "screen_id": "screen_123",
            "action": "tap",
            "target": {"element_id": "element_0015"},
            "completion": {"timeout_ms": 15000},
        }

        response = self.client.post(
            "/api/v1/explorations/run_123/actions",
            json=payload,
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(
            response.get_json()["result"]["classification"],
            "succeeded_screen_changed",
        )
        self.assertEqual(manager.calls, [("run_123", payload)])

    def test_open_installed_application_returns_screen_pointer(self):
        manager = FakeExplorationActionManager()
        self.app.extensions["exploration_action_manager"] = manager

        response = self.client.post(
            "/api/v1/explorations/open",
            json={"package_id": "com.example.app"},
        )

        self.assertEqual(response.status_code, 201, response.get_json())
        result = response.get_json()["result"]
        self.assertEqual(result["status"], "opened")
        self.assertEqual(
            result["screen_ref"],
            {"run_id": "run-new", "screen_id": "screen-new"},
        )
        self.assertEqual(
            manager.calls,
            [("launch", {"package_id": "com.example.app"})],
        )

    def test_open_installed_application_rejects_invalid_package_id(self):
        response = self.client.post(
            "/api/v1/explorations/open",
            json={"package_id": "not a package"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "validation_error",
        )

    def test_export_screen_context_dispatches_stored_screen_reference(self):
        exporter = FakeScreenContextExporter()
        self.app.extensions["screen_context_exporter"] = exporter
        payload = {
            "screen_ref": {
                "run_id": "run_123",
                "screen_id": "screen_456",
            },
            "options": {"max_characters": 4000},
        }

        response = self.client.post(
            "/api/v1/explorations/context",
            json=payload,
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        context = response.get_json()["context"]
        self.assertEqual(context["contract"], "appium.llm_screen_context")
        self.assertEqual(context["source"]["screen_id"], "screen_456")
        self.assertEqual(exporter.calls, [payload])

    def test_export_screen_context_requires_exactly_one_source(self):
        response = self.client.post(
            "/api/v1/explorations/context",
            json={},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "validation_error",
        )

    def test_export_screen_context_rejects_unsupported_contract(self):
        response = self.client.post(
            "/api/v1/explorations/context",
            json={
                "screen": {
                    "contract": "something.else",
                    "schema_version": 1,
                }
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "unsupported_screen_contract",
        )

    def test_runtime_administration_endpoints(self):
        manager = FakeRuntimeManager()
        self.app.extensions["runtime_manager"] = manager

        status = self.client.get("/api/v1/admin/runtime/status")
        provision = self.client.post(
            "/api/v1/admin/runtime/provision",
            json={
                "install_missing_prerequisites": False,
                "accept_android_licenses": True,
            },
        )
        start = self.client.post("/api/v1/admin/runtime/start", json={})
        stop = self.client.post(
            "/api/v1/admin/runtime/stop",
            json={"stop_appium": True, "stop_emulator": False},
        )
        job = self.client.get("/api/v1/admin/jobs/job_123")

        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.get_json()["runtime"]["ready"], False)
        self.assertEqual(provision.status_code, 202)
        self.assertFalse(provision.get_json()["reused"])
        self.assertEqual(start.status_code, 202)
        self.assertEqual(start.get_json()["job"]["operation"], "start")
        self.assertEqual(stop.status_code, 200)
        self.assertEqual(stop.get_json()["result"]["status"], "completed")
        self.assertEqual(job.status_code, 200)
        self.assertEqual(job.get_json()["job"]["job_id"], "job_123")
        self.assertEqual(
            manager.calls,
            [
                ("status", None),
                (
                    "provision",
                    {
                        "accept_android_licenses": True,
                        "install_missing_prerequisites": False,
                    },
                ),
                ("start", {}),
                (
                    "stop",
                    {"stop_appium": True, "stop_emulator": False},
                ),
                ("get_job", "job_123"),
            ],
        )

    def test_unknown_runtime_job_returns_not_found(self):
        manager = FakeRuntimeManager()
        self.app.extensions["runtime_manager"] = manager

        response = self.client.get("/api/v1/admin/jobs/job_missing")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"]["code"], "not_found")

    def test_runtime_administration_rejects_non_loopback_clients(self):
        response = self.client.get(
            "/api/v1/admin/runtime/status",
            environ_base={"REMOTE_ADDR": "192.168.1.50"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "access_denied",
        )

    def test_android_application_management_endpoints(self):
        manager = FakeAndroidAppManager()
        self.app.extensions["android_app_manager"] = manager
        payload = {
            "apk_path": "C:\\approved\\message.apk",
            "expected_package_id": "message.chat.text.messaging.sms",
            "install_mode": "clean",
        }

        installed = self.client.post("/api/v1/apps/install", json=payload)
        preflight = self.client.post(
            "/api/v1/apps/preflight",
            json={
                "apk_path": payload["apk_path"],
                "expected_package_id": payload["expected_package_id"],
            },
        )
        prepared = self.client.post(
            "/api/v1/apps/prepare-device",
            json={
                "apk_path": payload["apk_path"],
                "expected_package_id": payload["expected_package_id"],
                "options": {"auto_start_avd": True},
            },
        )
        inspected = self.client.get(
            "/api/v1/apps/message.chat.text.messaging.sms"
            "?device_id=emulator-5554"
        )
        removed = self.client.delete(
            "/api/v1/apps/message.chat.text.messaging.sms",
            json={"confirm": True},
        )

        self.assertEqual(installed.status_code, 201)
        self.assertEqual(installed.get_json()["result"]["status"], "installed")
        self.assertEqual(preflight.status_code, 200)
        self.assertEqual(
            preflight.get_json()["result"]["requirements"]["native_abis"],
            ["arm64-v8a"],
        )
        self.assertEqual(prepared.status_code, 200)
        self.assertEqual(
            prepared.get_json()["result"]["device_id"],
            "emulator-5556",
        )
        self.assertEqual(inspected.status_code, 200)
        self.assertTrue(inspected.get_json()["app"]["installed"])
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(removed.get_json()["result"]["status"], "uninstalled")
        self.assertEqual(
            manager.calls,
            [
                ("install", payload),
                (
                    "preflight",
                    {
                        "apk_path": payload["apk_path"],
                        "expected_package_id": payload[
                            "expected_package_id"
                        ],
                    },
                ),
                (
                    "prepare_device",
                    {
                        "apk_path": payload["apk_path"],
                        "expected_package_id": payload[
                            "expected_package_id"
                        ],
                        "options": {"auto_start_avd": True},
                    },
                ),
                (
                    "get",
                    "message.chat.text.messaging.sms",
                    "emulator-5554",
                ),
                (
                    "uninstall",
                    "message.chat.text.messaging.sms",
                    {"confirm": True},
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()
