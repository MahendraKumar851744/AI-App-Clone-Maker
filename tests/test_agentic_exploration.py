from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.modules.llm_provider import LLMResponse
from backend.workflows.initialize_appium.agent import (
    SYSTEM_PROMPT,
    ExplorationAgent,
)
from backend.workflows.initialize_appium.graph import ExplorationGraphStore


def decision_response(
    decision: str,
    *,
    action: dict | None = None,
) -> str:
    return json.dumps(
        {
            "decision": decision,
            "blocked_actions": [],
            "action": action,
            "reason": "Increase feature coverage",
            "expected_result": "Observe another screen",
            "exploration_goal": "Understand navigation",
            "return_plan": "Return to this screen after inspecting the result",
            "confidence": 0.9,
            "coverage_assessment": "Partially explored",
            "remaining_areas": ["Next screen"],
        }
    )


def screen_analysis(
    *,
    state_name: str = "default",
    fact: str = "The Next button opens navigation",
) -> dict:
    return {
        "semantic_name": "Navigation screen",
        "screen_type": "navigation",
        "functional_purpose": "Provides access to app features",
        "user_goals": ["Open the next feature"],
        "capabilities": ["Navigate forward"],
        "entry_conditions": ["The app is open"],
        "exit_paths": ["Tap Next"],
        "layout_summary": "A title above a primary action",
        "regions": [{"name": "content", "purpose": "Navigation"}],
        "visible_content": ["Next"],
        "controls": [
            {
                "element_id": "element_0001",
                "label": "Next",
                "role": "button",
                "function": "Open the next screen",
                "interactions": ["tap"],
            }
        ],
        "state_name": state_name,
        "state_description": f"The screen is in its {state_name} state",
        "facts": [fact],
        "hypotheses": [],
        "open_questions": ["What appears after Next?"],
        "workflow_updates": [],
        "branch_discoveries": ["The app has forward navigation"],
        "transition_understanding": None,
        "app_knowledge_update": {
            "app_summary": "An application with navigation",
            "features": ["Navigation"],
            "domain_objects": [],
            "global_ui_patterns": [],
            "navigation_model": [],
            "facts": [fact],
            "hypotheses": [],
            "workflows": [],
            "failures": [],
            "open_questions": [],
            "notes": [],
        },
        "confidence": 0.9,
    }


class FakeProvider:
    def __init__(self, values: list[str]) -> None:
        self.values = list(values)
        self.messages = []

    def generate(self, messages, provider_config):
        self.messages.append(messages)
        text = self.values.pop(0)
        return LLMResponse(
            text=text,
            raw={"choices": [{"message": {"content": text}}]},
            usage={"total_tokens": 100},
        )


class AgenticExplorationTests(unittest.TestCase):

    def test_system_prompt_authorizes_exhaustive_emulator_exploration(
        self,
    ) -> None:

        self.assertIn("controlled disposable emulator", SYSTEM_PROMPT)
        self.assertIn("default-app prompts", SYSTEM_PROMPT)
        self.assertIn("data creation, edits, deletion", SYSTEM_PROMPT)
        self.assertIn("maximizes new functional knowledge", SYSTEM_PROMPT)
        self.assertNotIn("Do not send messages", SYSTEM_PROMPT)

    def test_invalid_agent_action_is_retried_with_correction(self) -> None:
        provider = FakeProvider(
            [
                decision_response(
                    "act",
                    action={
                        "action": "tap",
                        "target": {"id": "element_0001"},
                        "parameters": {},
                        "completion": {"condition": "screen_changed"},
                    },
                ),
                decision_response(
                    "act",
                    action={
                        "action": "tap",
                        "target": {"element_id": "element_0001"},
                        "parameters": {},
                        "completion": {"condition": "screen_changed"},
                    },
                ),
            ]
        )
        agent = ExplorationAgent(provider, max_attempts=2)

        decision = agent.decide(
            package_id="com.example.app",
            objective="Understand the app",
            iteration=1,
            screen_context=(
                '- [element_0001] tap | "Next" | Button'
            ),
            graph_context={"current_node_id": "node_1"},
            knowledge_box={"app_summary": ""},
        )

        self.assertEqual(
            decision.action["target"]["element_id"],
            "element_0001",
        )
        self.assertEqual(len(agent.exchanges), 2)
        self.assertIn(
            "response was invalid",
            provider.messages[1][-1]["content"],
        )

    def test_revisited_fingerprint_reuses_graph_node(self) -> None:
        graph = ExplorationGraphStore(
            package_id="com.example.app",
            objective="Explore",
        )
        first = graph.observe_screen(
            screen_id="screen_1",
            fingerprint="same",
            context_text='- [element_0001] tap | "Next" | Button',
        )
        second = graph.observe_screen(
            screen_id="screen_2",
            fingerprint="same",
            context_text='- [element_0001] tap | "Next" | Button',
        )

        self.assertEqual(first["node_id"], second["node_id"])
        self.assertEqual(len(graph.graph["nodes"]), 1)
        self.assertEqual(second["visit_count"], 2)
        self.assertEqual(second["screen_ids"], ["screen_1", "screen_2"])

    def test_crash_transition_becomes_graph_and_knowledge_evidence(self) -> None:
        graph = ExplorationGraphStore(
            package_id="com.example.app",
            objective="Explore",
        )
        source = graph.observe_screen(
            screen_id="screen_1",
            fingerprint="one",
            context_text='- [element_0001] tap | "Crash" | Button',
        )

        edge = graph.record_transition(
            iteration=1,
            from_node_id=source["node_id"],
            action_request={
                "screen_id": "screen_1",
                "action": "tap",
                "target": {"element_id": "element_0001"},
                "parameters": {},
                "completion": {},
            },
            action_result={
                "classification": "app_crashed",
                "after": {
                    "screen_id": "screen_2",
                    "fingerprint": "two",
                },
                "app_health": {
                    "status": "crashed",
                    "current_package": "com.android.launcher",
                },
                "effect": {"status": "screen_changed"},
                "errors": [],
            },
        )

        self.assertEqual(edge["classification"], "app_crashed")
        self.assertEqual(len(graph.graph["nodes"]), 2)
        self.assertIn("caused crashed", graph.knowledge["failures"][0])
        self.assertEqual(
            graph.graph["branches"][0]["node_ids"],
            [source["node_id"]],
        )
        self.assertEqual(
            graph.active_branch()["node_ids"],
            [source["node_id"], edge["to_node_id"]],
        )

    def test_graph_and_knowledge_use_separate_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graph = ExplorationGraphStore(
                package_id="com.example.app",
                objective="Explore",
                output_directory=root,
            )
            graph.observe_screen(
                screen_id="screen_1",
                fingerprint="one",
                context_text="No controls",
            )

            self.assertTrue((root / "exploration-graph.json").is_file())
            self.assertTrue((root / "knowledge-box.json").is_file())

    def test_revisit_enriches_one_dossier_and_preserves_distinct_states(
        self,
    ) -> None:
        graph = ExplorationGraphStore(
            package_id="com.example.app",
            objective="Explore",
        )
        node = graph.observe_screen(
            screen_id="screen_1",
            fingerprint="same",
            context_text='- [element_0001] tap | "Next" | Button',
        )
        graph.apply_screen_analysis(
            node["node_id"],
            "screen_1",
            screen_analysis(),
        )
        graph.observe_screen(
            screen_id="screen_2",
            fingerprint="same",
            context_text='- [element_0001] tap | "Next" | Button',
        )
        graph.apply_screen_analysis(
            node["node_id"],
            "screen_2",
            screen_analysis(
                state_name="validation error",
                fact="An error appears when navigation is unavailable",
            ),
        )

        dossier = graph.node(node["node_id"])["dossier"]

        self.assertEqual(len(dossier["states"]), 2)
        self.assertEqual(len(dossier["revision_history"]), 2)
        self.assertIn(
            "The Next button opens navigation",
            dossier["facts"],
        )
        self.assertIn(
            "An error appears when navigation is unavailable",
            dossier["facts"],
        )

    def test_planning_context_is_focused_and_branch_memory_is_compiled(
        self,
    ) -> None:
        graph = ExplorationGraphStore(
            package_id="com.example.app",
            objective="Explore",
        )
        node = graph.observe_screen(
            screen_id="screen_1",
            fingerprint="one",
            context_text='- [element_0001] tap | "Next" | Button',
        )
        graph.apply_screen_analysis(
            node["node_id"],
            "screen_1",
            screen_analysis(),
        )

        context = graph.agent_context(node["node_id"])

        self.assertTrue(
            context["context_metrics"]["full_graph_omitted"]
        )
        self.assertEqual(
            context["active_branch"]["current_node_id"],
            node["node_id"],
        )
        self.assertTrue(
            graph.needs_branch_summary(
                node["node_id"],
                summarize_after_changes=1,
            )
        )

        graph.apply_branch_summary(
            {
                "branch_summary": "The entry branch exposes navigation.",
                "key_discoveries": ["Next opens another feature"],
                "confirmed_workflows": [],
                "open_questions": ["What is on the next screen?"],
                "unresolved_frontier": ["tap:element_0001"],
                "application_knowledge_patch": {},
            }
        )

        branch = graph.active_branch()

        self.assertEqual(branch["summary_revision"], 1)
        self.assertEqual(branch["changes_since_summary"], 0)
        self.assertIn("entry branch", branch["summary"])
        compressed = graph.branch_context(node["node_id"])
        self.assertEqual(compressed["recent_discoveries"], [])
        self.assertEqual(compressed["open_questions"], [])


if __name__ == "__main__":
    unittest.main()
