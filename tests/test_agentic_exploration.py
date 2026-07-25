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


def response(
    decision: str,
    *,
    action: dict | None = None,
) -> str:
    return json.dumps(
        {
            "decision": decision,
            "screen_summary": "A navigation screen",
            "screen_purpose": "Provides access to a feature",
            "observations": ["A Next button is visible"],
            "controls": [
                {
                    "element_id": "element_0001",
                    "label": "Next",
                    "likely_function": "Open the next screen",
                }
            ],
            "knowledge_update": {
                "app_summary": "An app with navigation",
                "features": ["Navigation"],
                "facts": ["Next is available"],
                "hypotheses": [],
                "workflows": [],
                "failures": [],
                "open_questions": [],
                "notes": [],
            },
            "blocked_actions": [],
            "action": action,
            "reason": "Increase feature coverage",
            "expected_result": "Observe another screen",
            "exploration_goal": "Understand navigation",
            "confidence": 0.9,
            "coverage_assessment": "Partially explored",
            "remaining_areas": ["Next screen"],
        }
    )


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

        self.assertIn("controlled, disposable test emulator", SYSTEM_PROMPT)
        self.assertIn("default-app prompts", SYSTEM_PROMPT)
        self.assertIn("data creation, edits, deletion", SYSTEM_PROMPT)
        self.assertIn("every reachable action and workflow", SYSTEM_PROMPT)
        self.assertNotIn("Do not send messages", SYSTEM_PROMPT)

    def test_invalid_agent_action_is_retried_with_correction(self) -> None:
        provider = FakeProvider(
            [
                response(
                    "act",
                    action={
                        "action": "tap",
                        "target": {"id": "element_0001"},
                        "parameters": {},
                        "completion": {"condition": "screen_changed"},
                    },
                ),
                response(
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
            "Your response was invalid",
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


if __name__ == "__main__":
    unittest.main()
