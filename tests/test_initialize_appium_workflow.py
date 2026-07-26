from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.workflows.initialize_appium.agent import (
    AgentDecision,
    MemorySummary,
    ScreenAnalysis,
)
from backend.workflows.initialize_appium.workflow import (
    InitializeAppiumWorkflow,
)


def agent_decision(choice: str, *, element_id: str | None = None) -> AgentDecision:
    action = (
        {
            "action": "tap",
            "target": {"element_id": element_id},
            "parameters": {},
            "completion": {"condition": "screen_changed"},
        }
        if choice == "act"
        else None
    )
    return AgentDecision(
        decision=choice,
        blocked_actions=[],
        action=action,
        reason="Continue coverage" if choice == "act" else "Coverage is complete",
        expected_result="A new screen" if choice == "act" else "No action",
        exploration_goal="Understand navigation",
        return_plan="Return through Android back navigation",
        confidence=0.9,
        coverage_assessment="Test coverage assessment",
        remaining_areas=[],
    )


def screen_analysis() -> ScreenAnalysis:
    return ScreenAnalysis(
        semantic_name="Test screen",
        screen_type="navigation",
        functional_purpose="Exercise the current feature",
        user_goals=["Navigate through the application"],
        capabilities=["Open the next screen"],
        entry_conditions=["Application is open"],
        exit_paths=["Tap Next"],
        layout_summary="A simple screen with a navigation control",
        regions=[{"name": "content", "purpose": "Primary navigation"}],
        visible_content=["Next"],
        controls=[
            {
                "element_id": "element_0001",
                "label": "Next",
                "role": "button",
                "likely_function": "Open the next screen",
            }
        ],
        state_name="default",
        state_description="The normal visible state",
        facts=["The current screen was observed"],
        hypotheses=[],
        open_questions=[],
        workflow_updates=[],
        branch_discoveries=["Navigation is available"],
        transition_understanding=None,
        app_knowledge_update={
            "app_summary": "A test application",
            "features": ["Navigation"],
            "domain_objects": [],
            "global_ui_patterns": [],
            "navigation_model": [],
            "facts": ["The current screen was observed"],
            "hypotheses": [],
            "workflows": [],
            "failures": [],
            "open_questions": [],
            "notes": [],
        },
        confidence=0.9,
    )


class FakeAgent:
    def __init__(self, decisions: list[AgentDecision]) -> None:
        self.decisions = list(decisions)
        self.calls: list[dict] = []
        self.exchanges: list[dict] = []

    def analyze_screen(
        self,
        *,
        package_id,
        objective,
        iteration,
        screen_context,
        analysis_context,
    ):
        return screen_analysis()

    def decide(
        self,
        *,
        package_id,
        objective,
        iteration,
        screen_context,
        graph_context,
        validator=None,
    ):
        self.calls.append(
            {
                "package_id": package_id,
                "objective": objective,
                "iteration": iteration,
                "screen_context": screen_context,
                "graph_context": graph_context,
            }
        )
        decision = self.decisions.pop(0)
        if validator is not None:
            validator(decision)
        return decision

    def summarize_branch(self, *, summary_input):
        return MemorySummary(
            branch_summary="A compact branch summary",
            key_discoveries=[],
            confirmed_workflows=[],
            open_questions=[],
            unresolved_frontier=[],
            application_knowledge_patch={},
        )


class FakeRuntimeClient:
    def __init__(
        self,
        statuses,
        *,
        installed_app=None,
        contexts: dict[str, str] | None = None,
    ) -> None:
        self.statuses = list(statuses)
        self.provision_calls = []
        self.start_calls = 0
        self.waited_jobs = []
        self.app = installed_app
        self.install_calls = []
        self.open_calls = []
        self.context_calls = []
        self.action_calls = []
        self.contexts = contexts or {"screen_1": "# Current Screen"}

    def runtime_status(self):
        if len(self.statuses) > 1:
            return self.statuses.pop(0)
        return self.statuses[0]

    def provision_runtime(self, options):
        self.provision_calls.append(options)
        return {"job": {"job_id": "job_provision"}, "reused": False}

    def start_runtime(self):
        self.start_calls += 1
        return {"job": {"job_id": "job_start"}, "reused": False}

    def wait_for_job(self, job_id, *, timeout_seconds):
        self.waited_jobs.append((job_id, timeout_seconds))
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
        self.install_calls.append(
            {
                "apk_path": apk_path,
                "expected_package_id": expected_package_id,
                "install_mode": install_mode,
                "device_id": device_id,
            }
        )
        self.app = {
            "package_id": expected_package_id,
            "version_name": "1.0",
            "version_code": "1",
        }
        return {"status": "installed", **self.app}

    def open_application(self, package_id):
        self.open_calls.append(package_id)
        return {
            "run_id": "run_123",
            "screen_id": "screen_1",
            "package_id": package_id,
            "screen": {
                "fingerprint": "fingerprint_1",
                "package": package_id,
                "activity": ".MainActivity",
            },
        }

    def screen_context(self, screen_ref, options):
        self.context_calls.append(
            {"screen_ref": screen_ref, "options": options}
        )
        return {
            "contract": "appium.llm_screen_context",
            "format": "text/markdown",
            "text": self.contexts[screen_ref["screen_id"]],
            "source": screen_ref,
        }

    def perform_action(self, run_id, action_request):
        self.action_calls.append(
            {"run_id": run_id, "action_request": action_request}
        )
        index = len(self.action_calls) + 1
        return {
            "action_id": f"action_{index}",
            "transition_id": f"transition_{index}",
            "classification": "succeeded_screen_changed",
            "effect": {"status": "screen_changed"},
            "app_health": {
                "status": "healthy",
                "current_package": "com.example.app",
            },
            "after": {
                "screen_id": f"screen_{index}",
                "fingerprint": f"fingerprint_{index}",
            },
            "errors": [],
        }


class InitializeAppiumWorkflowTests(unittest.TestCase):
    def test_ready_runtime_and_terminal_screen_complete_without_action(self):
        ready = {"provisioned": True, "ready": True}
        client = FakeRuntimeClient(
            [ready, ready, ready],
            installed_app={"package_id": "com.example.app"},
        )

        result = InitializeAppiumWorkflow(
            client,
            FakeAgent([agent_decision("finish")]),
        ).run(
            apk_path="assets/apps/example.apk",
            expected_package_id="com.example.app",
            objective="Understand the app",
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["provisioning"], "reused")
        self.assertEqual(result["startup"], "reused")
        self.assertEqual(result["iterations"], 1)
        self.assertEqual(result["graph"]["coverage"]["screens_discovered"], 1)
        self.assertEqual(result["knowledge_box"]["app_summary"], "A test application")
        self.assertEqual(client.action_calls, [])

    def test_provisions_and_installs_only_when_missing(self):
        client = FakeRuntimeClient(
            [
                {"provisioned": False, "ready": False},
                {"provisioned": True, "ready": False},
                {"provisioned": True, "ready": True},
            ],
        )

        result = InitializeAppiumWorkflow(
            client,
            FakeAgent([agent_decision("finish")]),
        ).run(
            apk_path="assets/apps/example.apk",
            expected_package_id="com.example.app",
            objective="Understand the app",
            provision_options={"accept_android_licenses": True},
            job_timeout_seconds=120,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["provisioning"], "completed")
        self.assertEqual(result["startup"], "completed")
        self.assertEqual(client.start_calls, 1)
        self.assertEqual(client.install_calls[0]["install_mode"], "preserve")

    def test_agent_action_creates_graph_edge_and_next_screen_node(self):
        ready = {"provisioned": True, "ready": True}
        contexts = {
            "screen_1": (
                "# CURRENT SCREEN\n"
                '- [element_0001] tap | "Next" | Button\n'
            ),
            "screen_2": "# SECOND SCREEN\nNo available actions.",
        }
        client = FakeRuntimeClient(
            [ready, ready, ready],
            installed_app={"package_id": "com.example.app"},
            contexts=contexts,
        )
        agent = FakeAgent(
            [
                agent_decision("act", element_id="element_0001"),
                agent_decision("finish"),
            ]
        )

        result = InitializeAppiumWorkflow(client, agent).run(
            apk_path="assets/apps/example.apk",
            expected_package_id="com.example.app",
            objective="Understand navigation",
            max_iterations=5,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["iterations"], 2)
        self.assertEqual(result["graph"]["coverage"]["screens_discovered"], 2)
        self.assertEqual(result["graph"]["coverage"]["transitions_recorded"], 1)
        edge = result["graph"]["edges"][0]
        self.assertEqual(edge["from_node_id"], "node_1")
        self.assertEqual(edge["to_node_id"], "node_2")
        self.assertEqual(edge["action_key"], "tap:element_0001")
        self.assertEqual(len(client.action_calls), 1)

    def test_finish_at_iteration_budget_reports_incomplete_coverage(self):

        ready = {"provisioned": True, "ready": True}
        client = FakeRuntimeClient(
            [ready, ready, ready],
            installed_app={"package_id": "com.example.app"},
            contexts={
                "screen_1": (
                    "# CURRENT SCREEN\n"
                    '- [element_0001] tap | "Next" | Button\n'
                ),
            },
        )

        result = InitializeAppiumWorkflow(
            client,
            FakeAgent([agent_decision("finish")]),
        ).run(
            apk_path="assets/apps/example.apk",
            expected_package_id="com.example.app",
            objective="Understand navigation",
            max_iterations=1,
        )

        self.assertEqual(result["status"], "iteration_limit_reached")
        self.assertEqual(result["iterations"], 1)
        self.assertEqual(client.action_calls, [])

    def test_graph_and_knowledge_are_persisted_separately(self):
        ready = {"provisioned": True, "ready": True}
        client = FakeRuntimeClient(
            [ready, ready, ready],
            installed_app={"package_id": "com.example.app"},
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            InitializeAppiumWorkflow(
                client,
                FakeAgent([agent_decision("finish")]),
                graph_output_directory=output,
            ).run(
                apk_path="assets/apps/example.apk",
                expected_package_id="com.example.app",
                objective="Understand the app",
            )

            graph = json.loads(
                (output / "exploration-graph.json").read_text("utf-8")
            )
            knowledge = json.loads(
                (output / "knowledge-box.json").read_text("utf-8")
            )

        self.assertEqual(
            graph["contract"],
            "workflow.screen_knowledge_graph",
        )
        self.assertEqual(
            knowledge["contract"],
            "workflow.application_knowledge",
        )
        self.assertEqual(graph["status"], "completed")

    def test_application_is_not_opened_when_install_cannot_be_verified(self):
        ready = {"provisioned": True, "ready": True}
        client = FakeRuntimeClient([ready, ready, ready])
        client.install_app = lambda **_kwargs: {"status": "installed"}

        with self.assertRaisesRegex(RuntimeError, "is not installed"):
            InitializeAppiumWorkflow(
                client,
                FakeAgent([agent_decision("finish")]),
            ).run(
                apk_path="assets/apps/example.apk",
                expected_package_id="com.example.app",
                objective="Understand the app",
            )

        self.assertEqual(client.open_calls, [])


if __name__ == "__main__":
    unittest.main()
