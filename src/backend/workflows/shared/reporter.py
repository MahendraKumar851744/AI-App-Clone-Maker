from __future__ import annotations

from typing import Any, Callable

from backend.workflows.shared.client import AppiumHTTPClient


JsonObject = dict[str, Any]
Operation = Callable[[], Any]
Summarizer = Callable[[Any], Any]


STEP_TITLES = {
    "runtime_status": "Check Appium runtime",
    "provision_runtime": "Provision Appium runtime",
    "wait_for_provisioning": "Wait for provisioning",
    "runtime_status_after_provisioning": "Verify provisioning",
    "start_runtime": "Start emulator and Appium",
    "wait_for_runtime_start": "Wait for runtime startup",
    "verify_runtime_ready": "Verify Appium is ready",
    "inspect_application": "Check whether the app is installed",
    "install_application": "Install the missing application",
    "verify_application": "Verify the application",
    "open_application": "Open the application",
    "export_screen_context": "Generate LLM screen context",
    "llm_decision": "Ask Qwen for the next action",
    "perform_action": "Execute the selected action",
    "observe_graph_node": "Match the screen in the traversal graph",
    "update_exploration_memory": "Update graph and app knowledge",
    "record_graph_transition": "Record the screen transition",
}


class WorkflowLiveReporter:
    """Publishes readable workflow progress to the backend viewer."""

    def __init__(
        self,
        client: AppiumHTTPClient,
        *,
        name: str,
        metadata: JsonObject,
    ) -> None:
        self.client = client
        created = client.create_workflow_run(name, metadata)
        run = created.get("run")
        if not isinstance(run, dict) or not isinstance(run.get("run_id"), str):
            raise RuntimeError("The backend did not create a live workflow run.")
        self.run_id = run["run_id"]
        self.viewer_url = str(created.get("viewer_url") or "")

    def step(
        self,
        name: str,
        operation: Operation,
        summarize: Summarizer,
        *,
        step_input: Any = None,
        title: str | None = None,
    ) -> Any:
        step = self.client.start_workflow_step(
            self.run_id,
            {
                "name": name,
                "title": title or STEP_TITLES.get(name, name.replace("_", " ").title()),
                "input": step_input,
            },
        )
        step_id = step.get("step_id")
        if not isinstance(step_id, str):
            raise RuntimeError("The live workflow step is missing 'step_id'.")
        try:
            result = operation()
            output = summarize(result)
        except Exception as error:
            self.client.finish_workflow_step(
                self.run_id,
                step_id,
                {
                    "status": "failed",
                    "error": self._error(error),
                },
            )
            raise
        self.client.finish_workflow_step(
            self.run_id,
            step_id,
            {
                "status": "completed",
                "output": output,
            },
        )
        return result

    def complete(self, summary: Any) -> None:
        self.client.finish_workflow_run(
            self.run_id,
            {
                "status": "completed",
                "summary": summary,
            },
        )

    def fail(self, error: Exception) -> None:
        self.client.finish_workflow_run(
            self.run_id,
            {
                "status": "failed",
                "error": self._error(error),
            },
        )

    @staticmethod
    def _error(error: Exception) -> JsonObject:
        value = {
            "type": type(error).__name__,
            "message": str(error),
        }
        details = getattr(error, "details", None)
        if details is not None:
            value["details"] = details
        return value
