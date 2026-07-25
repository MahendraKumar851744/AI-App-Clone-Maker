from __future__ import annotations
import re
from dataclasses import asdict, dataclass, field
from time import perf_counter
from typing import Any, Callable
from uuid import uuid4
from backend.workflows.shared.client import AppiumHTTPClient
from backend.workflows.shared.runtime import AppiumRuntimeInitializer
from backend.workflows.simple_exploration.llm import (
    ExplorationDecision,
    ExplorationLLM,
)

JsonObject = dict[str, Any]
PACKAGE_ID_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$"
)


@dataclass
class WorkflowStepTrace:
    name: str
    iteration: int | None
    status: str
    duration_ms: int
    summary: JsonObject = field(default_factory=dict)


class SimpleExplorationWorkflow:
    """
    One clear loop:
    open -> context -> LLM decision -> validated action -> next screen.
    """

    def __init__(
        self,
        client: AppiumHTTPClient,
        llm: ExplorationLLM,
        *,
        context_options: JsonObject | None = None,
    ) -> None:
        self.client = client
        self.llm = llm
        self.context_options = dict(context_options or {})

    def run(
        self,
        *,
        apk_path: str,
        expected_package_id: str,
        objective: str,
        install_policy: str = "if_missing",
        max_iterations: int = 10,
        device_id: str | None = None,
        provision_options: JsonObject | None = None,
        job_timeout_seconds: float = 3600,
    ) -> JsonObject:
        if not isinstance(apk_path, str) or not apk_path.strip():
            raise ValueError("'apk_path' cannot be empty.")
        if not PACKAGE_ID_PATTERN.fullmatch(expected_package_id):
            raise ValueError(
                "'expected_package_id' must be a valid Android package ID."
            )
        if not isinstance(objective, str) or not objective.strip():
            raise ValueError("'objective' cannot be empty.")
        if install_policy not in {"if_missing", "clean", "replace"}:
            raise ValueError(
                "'install_policy' must be 'if_missing', 'clean', or 'replace'."
            )
        if not 1 <= max_iterations <= 100:
            raise ValueError("'max_iterations' must be between 1 and 100.")
        if not 1 <= job_timeout_seconds <= 7200:
            raise ValueError("'job_timeout_seconds' must be between 1 and 7200.")
        workflow_id = f"workflow_{uuid4().hex}"
        traces: list[WorkflowStepTrace] = []
        history: list[JsonObject] = []
        decisions: list[JsonObject] = []
        transitions: list[JsonObject] = []
        runtime = self._initialize_runtime(
            traces,
            provision_options=dict(provision_options or {}),
            job_timeout_seconds=job_timeout_seconds,
        )
        application = self._initialize_application(
            traces,
            apk_path=apk_path.strip(),
            package_id=expected_package_id,
            install_policy=install_policy,
            device_id=device_id,
        )
        launch = self._step(
            traces,
            "open_application",
            None,
            lambda: self.client.open_application(expected_package_id),
            lambda result: {
                "run_id": result.get("run_id"),
                "screen_id": result.get("screen_id"),
            },
        )
        run_id = self._required_string(launch, "run_id", "open result")
        screen_id = self._required_string(launch, "screen_id", "open result")
        status = "iteration_limit_reached"
        for iteration in range(1, max_iterations + 1):
            screen_ref = {"run_id": run_id, "screen_id": screen_id}
            context = self._step(
                traces,
                "export_screen_context",
                iteration,
                lambda: self.client.screen_context(
                    screen_ref,
                    self.context_options,
                ),
                lambda result: {
                    "screen_id": result.get("source", {}).get("screen_id"),
                    "characters": result.get("characters"),
                    "estimated_tokens": result.get("estimated_tokens"),
                },
            )
            context_text = self._required_string(
                context,
                "text",
                "screen context",
            )
            decision = self._step(
                traces,
                "llm_decision",
                iteration,
                lambda: self.llm.decide(
                    objective=objective.strip(),
                    iteration=iteration,
                    screen_context=context_text,
                    history=history,
                ),
                lambda result: {
                    "decision": result.decision,
                    "confidence": result.confidence,
                },
            )
            assert isinstance(decision, ExplorationDecision)
            decisions.append(decision.to_dict())
            if decision.decision == "finish":
                status = "completed"
                history.append(
                    {
                        "iteration": iteration,
                        "screen_id": screen_id,
                        "decision": "finish",
                        "reason": decision.reason,
                    }
                )
                break
            action_request = self._step(
                traces,
                "build_action_request",
                iteration,
                lambda: decision.to_action_request(screen_id),
                lambda result: {
                    "action": result.get("action"),
                    "screen_id": result.get("screen_id"),
                },
            )
            action_result = self._step(
                traces,
                "perform_action",
                iteration,
                lambda: self.client.perform_action(run_id, action_request),
                lambda result: {
                    "classification": result.get("classification"),
                    "after_screen_id": result.get("after", {}).get("screen_id"),
                },
            )
            after = action_result.get("after")
            if not isinstance(after, dict):
                raise RuntimeError("The action result is missing 'after'.")
            next_screen_id = self._required_string(
                after,
                "screen_id",
                "action result after-screen",
            )
            transition = {
                "iteration": iteration,
                "from_screen_id": screen_id,
                "to_screen_id": next_screen_id,
                "action_id": action_result.get("action_id"),
                "transition_id": action_result.get("transition_id"),
                "action": action_request["action"],
                "classification": action_result.get("classification"),
                "app_health": action_result.get("app_health", {}).get("status"),
            }
            transitions.append(transition)
            history.append(
                {
                    **transition,
                    "screen_summary": decision.screen_summary,
                    "observations": decision.observations,
                    "reason": decision.reason,
                }
            )
            screen_id = next_screen_id
        return {
            "contract": "workflow.simple_app_exploration",
            "schema_version": 1,
            "workflow_id": workflow_id,
            "status": status,
            "objective": objective.strip(),
            "apk_path": apk_path.strip(),
            "package_id": expected_package_id,
            "install_policy": install_policy,
            "initialization": {
                "runtime": runtime,
                "application": application,
            },
            "run_id": run_id,
            "current_screen_id": screen_id,
            "iterations": len(decisions),
            "decisions": decisions,
            "transitions": transitions,
            "steps": [asdict(trace) for trace in traces],
        }

    def _initialize_runtime(
        self,
        traces: list[WorkflowStepTrace],
        *,
        provision_options: JsonObject,
        job_timeout_seconds: float,
    ) -> JsonObject:
        initializer = AppiumRuntimeInitializer(
            self.client,
            execute_step=lambda name, operation, summarize: self._step(
                traces,
                name,
                None,
                operation,
                summarize,
            ),
        )
        result = initializer.run(
            provision_options=provision_options,
            job_timeout_seconds=job_timeout_seconds,
        )
        return {
            "provisioning": result["provisioning"],
            "startup": result["startup"],
            "ready": result["ready"],
        }

    def _initialize_application(
        self,
        traces: list[WorkflowStepTrace],
        *,
        apk_path: str,
        package_id: str,
        install_policy: str,
        device_id: str | None,
    ) -> JsonObject:

        installed = self._step(
            traces,
            "inspect_application",
            None,
            lambda: self.client.installed_app(
                package_id,
                device_id=device_id,
            ),
            lambda result: {
                "installed": result is not None,
                "version_name": (
                    result.get("version_name") if isinstance(result, dict) else None
                ),
            },
        )

        if installed is not None and install_policy == "if_missing":

            return {
                "status": "reused",
                "installed": True,
                "package_id": package_id,
                "version_name": installed.get("version_name"),
                "version_code": installed.get("version_code"),
            }
        install_mode = "preserve" if install_policy == "if_missing" else install_policy
        result = self._step(
            traces,
            "install_application",
            None,
            lambda: self.client.install_app(
                apk_path=apk_path,
                expected_package_id=package_id,
                install_mode=install_mode,
                device_id=device_id,
            ),
            lambda value: {
                "status": value.get("status"),
                "package_id": value.get("package_id"),
                "install_mode": value.get("install_mode"),
            },
        )
        return {
            "status": "installed",
            "installed": True,
            "package_id": result.get("package_id"),
            "version_name": result.get("version_name"),
            "version_code": result.get("version_code"),
            "install_mode": install_mode,
        }

    @staticmethod
    def _step(
        traces: list[WorkflowStepTrace],
        name: str,
        iteration: int | None,
        operation: Callable[[], Any],
        summarize: Callable[[Any], JsonObject],
    ) -> Any:
        started = perf_counter()
        try:
            result = operation()
        except Exception:
            traces.append(
                WorkflowStepTrace(
                    name=name,
                    iteration=iteration,
                    status="failed",
                    duration_ms=round((perf_counter() - started) * 1000),
                )
            )
            raise
        traces.append(
            WorkflowStepTrace(
                name=name,
                iteration=iteration,
                status="completed",
                duration_ms=round((perf_counter() - started) * 1000),
                summary=summarize(result),
            )
        )
        return result

    @staticmethod
    def _required_string(
        value: JsonObject,
        field_name: str,
        source: str,
    ) -> str:
        field = value.get(field_name)
        if not isinstance(field, str) or not field:
            raise RuntimeError(f"The {source} is missing '{field_name}'.")
        return field


def main() -> None:
    """Backward-compatible entry point for the original module command."""
    from backend.workflows.simple_exploration.cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
