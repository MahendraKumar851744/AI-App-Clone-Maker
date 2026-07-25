from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backend import create_app
from backend.workflow_runs import WorkflowRunStore


class WorkflowViewerTests(unittest.TestCase):

    def test_large_step_payload_is_externalized_and_hydrated(self) -> None:

        with tempfile.TemporaryDirectory() as directory:

            root = Path(directory)
            store = WorkflowRunStore(root)
            run_id = store.create("large-payload", {})["run_id"]
            large_context = "screen context " * 10_000

            step = store.start_step(
                run_id,
                name="llm_decision",
                title="Large LLM request",
                step_input={"screen_context": large_context},
            )

            store.finish_step(
                run_id,
                step["step_id"],
                status="completed",
                output={"provider_response": large_context},
            )

            run_path = root / run_id / "run.json"
            persisted = run_path.read_text(encoding="utf-8")

            self.assertLess(run_path.stat().st_size, 10_000)
            self.assertIn("workflow.payload_reference", persisted)
            self.assertGreaterEqual(
                len(list((root / run_id / "payloads").glob("*.json"))),
                2,
            )

            hydrated = WorkflowRunStore(root).get(run_id)

            self.assertEqual(
                hydrated["steps"][0]["input"]["screen_context"],
                large_context,
            )
            self.assertEqual(
                hydrated["steps"][0]["output"]["provider_response"],
                large_context,
            )

    def test_separate_store_instances_serialize_run_updates(self) -> None:

        with tempfile.TemporaryDirectory() as directory:

            root = Path(directory)
            first_store = WorkflowRunStore(root)
            second_store = WorkflowRunStore(root)
            run_id = first_store.create("concurrent", {})["run_id"]

            def add_steps(store: WorkflowRunStore, prefix: str) -> None:

                for index in range(12):
                    store.start_step(
                        run_id,
                        name=f"{prefix}_{index}",
                        title=f"{prefix} step {index}",
                        step_input={"index": index},
                    )

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(add_steps, first_store, "first"),
                    executor.submit(add_steps, second_store, "second"),
                ]

                for future in futures:
                    future.result()

            run = first_store.get(run_id)

            self.assertEqual(len(run["steps"]), 24)
            self.assertEqual(
                [step["position"] for step in run["steps"]],
                list(range(1, 25)),
            )

    def test_live_run_can_be_created_updated_and_viewed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(
                {
                    "TESTING": True,
                    "WORKFLOW_OUTPUT_ROOT": Path(directory),
                }
            )
            client = app.test_client()

            created = client.post(
                "/api/v1/workflow-runs",
                json={
                    "name": "initialize_appium",
                    "metadata": {"objective": "Test one action"},
                },
            )

            self.assertEqual(created.status_code, 201)
            run_id = created.get_json()["run"]["run_id"]
            self.assertIn(run_id, created.get_json()["viewer_url"])

            started = client.post(
                f"/api/v1/workflow-runs/{run_id}/steps",
                json={
                    "name": "llm_decision",
                    "title": "Ask Qwen for the next action",
                    "input": {
                        "objective": "Test one action",
                        "screen_context": "Set as Default",
                    },
                },
            )
            step_id = started.get_json()["step"]["step_id"]

            completed = client.post(
                (
                    f"/api/v1/workflow-runs/{run_id}/steps/"
                    f"{step_id}/finish"
                ),
                json={
                    "status": "completed",
                    "output": {
                        "decision": "act",
                        "reason": "Open the useful screen",
                    },
                },
            )
            self.assertEqual(completed.status_code, 200)

            client.post(
                f"/api/v1/workflow-runs/{run_id}/finish",
                json={
                    "status": "completed",
                    "summary": {"action": "tap"},
                },
            )

            state = client.get(f"/api/v1/workflow-runs/{run_id}")
            run = state.get_json()["run"]
            self.assertEqual(run["status"], "completed")
            self.assertEqual(run["steps"][0]["status"], "completed")

            viewer = client.get(
                f"/api/v1/workflow-runs/{run_id}/viewer"
            )
            self.assertEqual(viewer.status_code, 200)
            self.assertIn(b"Developer workflow monitor", viewer.data)
            self.assertIn(b"LLM request / input", viewer.data)


if __name__ == "__main__":
    unittest.main()
