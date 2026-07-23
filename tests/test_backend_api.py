from __future__ import annotations

import unittest
from typing import Any

from backend import create_app


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


class BackendAPITests(unittest.TestCase):
    def setUp(self):
        self.http_session = FakeHTTPSession()
        self.app = create_app(
            {
                "TESTING": True,
                "HTTP_SESSION": self.http_session,
                "RUN_STORE_MAX_ITEMS": 20,
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


if __name__ == "__main__":
    unittest.main()
