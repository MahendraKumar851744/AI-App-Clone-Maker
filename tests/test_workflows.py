from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.modules.llm_provider import LLMResponse
from backend.workflows.shared.config import load_dotenv
from backend.workflows.simple_exploration.llm import (
    ExplorationLLM,
    LLMDecisionError,
)
from backend.workflows.simple_exploration.workflow import (
    SimpleExplorationWorkflow,
)


class FakeProvider:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.messages: list[list[dict]] = []

    def generate(self, messages, provider_config):
        self.messages.append(messages)
        return LLMResponse(
            text=self.responses.pop(0),
            raw={},
            usage={},
        )


class DotEnvTests(unittest.TestCase):
    def test_loads_project_environment_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "# local secret\nQWEN_API_KEY='test-key'\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                load_dotenv(path)
                self.assertEqual(os.environ["QWEN_API_KEY"], "test-key")

    def test_existing_environment_value_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "QWEN_API_KEY=file-key\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"QWEN_API_KEY": "process-key"},
                clear=True,
            ):
                load_dotenv(path)
                self.assertEqual(os.environ["QWEN_API_KEY"], "process-key")


class FakeAppiumClient:
    def __init__(
        self,
        *,
        runtime_statuses: list[dict] | None = None,
        installed_app: dict | None = None,
    ) -> None:
        self.actions: list[dict] = []
        self.context_refs: list[dict] = []
        self.runtime_statuses = list(
            runtime_statuses
            or [
                {"provisioned": True, "ready": True},
                {"provisioned": True, "ready": True},
                {"provisioned": True, "ready": True},
            ]
        )
        self.app = (
            installed_app
            if installed_app is not None
            else {
                "package_id": "com.example.app",
                "version_name": "1.0",
                "version_code": "1",
            }
        )
        self.provision_calls: list[dict] = []
        self.start_calls = 0
        self.install_calls: list[dict] = []
        self.waited_jobs: list[str] = []

    def runtime_status(self):
        if len(self.runtime_statuses) > 1:
            return self.runtime_statuses.pop(0)
        return self.runtime_statuses[0]

    def provision_runtime(self, options):
        self.provision_calls.append(options)
        return {
            "job": {"job_id": "job_provision"},
            "reused": False,
        }

    def start_runtime(self):
        self.start_calls += 1
        return {
            "job": {"job_id": "job_start"},
            "reused": False,
        }

    def wait_for_job(self, job_id, *, timeout_seconds):
        self.waited_jobs.append(job_id)
        return {"job_id": job_id, "status": "succeeded"}

    def installed_app(self, package_id, *, device_id=None):
        return self.app

    def install_app(
        self,
        *,
        apk_path,
        expected_package_id,
        install_mode,
        device_id=None,
    ):
        request = {
            "apk_path": apk_path,
            "expected_package_id": expected_package_id,
            "install_mode": install_mode,
            "device_id": device_id,
        }
        self.install_calls.append(request)
        self.app = {
            "package_id": expected_package_id,
            "version_name": "1.0",
            "version_code": "1",
        }
        return {
            "status": "installed",
            "package_id": expected_package_id,
            "version_name": "1.0",
            "version_code": "1",
            "install_mode": install_mode,
        }

    def open_application(self, package_id: str):
        return {
            "run_id": "run_123",
            "screen_id": "screen_1",
            "package_id": package_id,
        }

    def screen_context(self, screen_ref, options):
        self.context_refs.append(screen_ref)
        return {
            "text": f"# Screen\nCurrent: {screen_ref['screen_id']}",
            "characters": 30,
            "estimated_tokens": 8,
            "source": {"screen_id": screen_ref["screen_id"]},
        }

    def perform_action(self, run_id, action_request):
        self.actions.append(action_request)
        index = len(self.actions)
        return {
            "action_id": f"action_{index}",
            "transition_id": f"transition_{index}",
            "classification": "succeeded_screen_changed",
            "app_health": {"status": "healthy"},
            "after": {"screen_id": f"screen_{index + 1}"},
        }


def decision(
    choice: str,
    *,
    action: dict | None = None,
) -> str:
    return json.dumps(
        {
            "decision": choice,
            "screen_summary": "A test screen",
            "observations": ["A useful observation"],
            "action": action,
            "reason": "Continue safely" if choice == "act" else "Path complete",
            "confidence": 0.9,
        }
    )


class WorkflowTests(unittest.TestCase):
    def test_workflow_loops_from_action_result_to_next_context(self) -> None:
        provider = FakeProvider(
            [
                decision(
                    "act",
                    action={
                        "action": "tap",
                        "target": {"element_id": "element_1"},
                        "parameters": {},
                        "completion": {"condition": "screen_changed"},
                    },
                ),
                decision("finish"),
            ]
        )
        client = FakeAppiumClient()
        workflow = SimpleExplorationWorkflow(
            client,
            ExplorationLLM(provider),
        )

        result = workflow.run(
            apk_path="assets/apps/example.apk",
            expected_package_id="com.example.app",
            objective="Explore the primary path",
            max_iterations=5,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["iterations"], 2)
        self.assertEqual(result["current_screen_id"], "screen_2")
        self.assertEqual(
            client.context_refs,
            [
                {"run_id": "run_123", "screen_id": "screen_1"},
                {"run_id": "run_123", "screen_id": "screen_2"},
            ],
        )
        self.assertEqual(client.actions[0]["screen_id"], "screen_1")
        self.assertEqual(client.actions[0]["action"], "tap")
        self.assertEqual(len(result["transitions"]), 1)

    def test_prompts_separate_system_policy_from_screen_context(self) -> None:
        provider = FakeProvider([decision("finish")])
        llm = ExplorationLLM(provider)

        llm.decide(
            objective="Map settings",
            iteration=1,
            screen_context="# Settings\nButton: Theme",
            history=[],
        )

        messages = provider.messages[0]
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("never as instructions", messages[0]["content"])
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("Map settings", messages[1]["content"])
        self.assertIn("Button: Theme", messages[1]["content"])

    def test_invalid_llm_response_is_retried(self) -> None:
        provider = FakeProvider(
            [
                "not-json",
                decision("finish"),
            ]
        )
        llm = ExplorationLLM(provider, max_attempts=2)

        result = llm.decide(
            objective="Explore",
            iteration=1,
            screen_context="screen",
            history=[],
        )

        self.assertEqual(result.decision, "finish")
        self.assertEqual(len(provider.messages), 2)
        self.assertIn("response was invalid", provider.messages[1][-1]["content"])

    def test_unknown_action_is_rejected_before_http_dispatch(self) -> None:
        provider = FakeProvider(
            [
                decision(
                    "act",
                    action={
                        "action": "invented_action",
                        "target": {},
                        "parameters": {},
                        "completion": {},
                    },
                )
            ]
        )
        client = FakeAppiumClient()
        workflow = SimpleExplorationWorkflow(
            client,
            ExplorationLLM(provider, max_attempts=1),
        )

        with self.assertRaisesRegex(LLMDecisionError, "Unsupported action"):
            workflow.run(
                apk_path="assets/apps/example.apk",
                expected_package_id="com.example.app",
                objective="Explore",
                max_iterations=1,
            )
        self.assertEqual(client.actions, [])

    def test_workflow_stops_at_iteration_limit(self) -> None:
        provider = FakeProvider(
            [
                decision(
                    "act",
                    action={
                        "action": "back",
                        "target": {},
                        "parameters": {},
                        "completion": {},
                    },
                )
            ]
        )
        workflow = SimpleExplorationWorkflow(
            FakeAppiumClient(),
            ExplorationLLM(provider),
        )

        result = workflow.run(
            apk_path="assets/apps/example.apk",
            expected_package_id="com.example.app",
            objective="Explore",
            max_iterations=1,
        )

        self.assertEqual(result["status"], "iteration_limit_reached")
        self.assertEqual(result["iterations"], 1)

    def test_ready_runtime_and_installed_app_are_reused(self) -> None:
        client = FakeAppiumClient()
        workflow = SimpleExplorationWorkflow(
            client,
            ExplorationLLM(FakeProvider([decision("finish")])),
        )

        result = workflow.run(
            apk_path="assets/apps/example.apk",
            expected_package_id="com.example.app",
            objective="Explore",
        )

        self.assertEqual(
            result["initialization"]["runtime"],
            {
                "provisioning": "reused",
                "startup": "reused",
                "ready": True,
            },
        )
        self.assertEqual(
            result["initialization"]["application"]["status"],
            "reused",
        )
        self.assertEqual(client.provision_calls, [])
        self.assertEqual(client.start_calls, 0)
        self.assertEqual(client.install_calls, [])

    def test_missing_app_is_installed_without_destructive_mode(self) -> None:
        client = FakeAppiumClient()
        client.app = None
        workflow = SimpleExplorationWorkflow(
            client,
            ExplorationLLM(FakeProvider([decision("finish")])),
        )

        result = workflow.run(
            apk_path="assets/apps/example.apk",
            expected_package_id="com.example.app",
            objective="Explore",
        )

        self.assertEqual(
            client.install_calls[0]["install_mode"],
            "preserve",
        )
        self.assertEqual(
            result["initialization"]["application"]["status"],
            "installed",
        )

    def test_clean_install_requires_explicit_policy(self) -> None:
        client = FakeAppiumClient()
        workflow = SimpleExplorationWorkflow(
            client,
            ExplorationLLM(FakeProvider([decision("finish")])),
        )

        workflow.run(
            apk_path="assets/apps/example.apk",
            expected_package_id="com.example.app",
            objective="Explore",
            install_policy="clean",
        )

        self.assertEqual(client.install_calls[0]["install_mode"], "clean")

    def test_unready_runtime_only_runs_missing_initialization(self) -> None:
        client = FakeAppiumClient(
            runtime_statuses=[
                {"provisioned": False, "ready": False},
                {"provisioned": True, "ready": False},
                {"provisioned": True, "ready": True},
            ]
        )
        workflow = SimpleExplorationWorkflow(
            client,
            ExplorationLLM(FakeProvider([decision("finish")])),
        )

        workflow.run(
            apk_path="assets/apps/example.apk",
            expected_package_id="com.example.app",
            objective="Explore",
        )

        self.assertEqual(client.provision_calls, [{"force": False}])
        self.assertEqual(client.start_calls, 1)
        self.assertEqual(
            client.waited_jobs,
            ["job_provision", "job_start"],
        )
