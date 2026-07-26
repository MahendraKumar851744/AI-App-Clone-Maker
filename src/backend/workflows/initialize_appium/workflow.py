from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from backend.workflows.initialize_appium.agent import (

    AgentDecision,

    AgentDecisionError,

    ExplorationAgent,

    MemorySummary,

    ScreenAnalysis,

)

from backend.workflows.initialize_appium.graph import ExplorationGraphStore

from backend.workflows.shared.client import AppiumHTTPClient

from backend.workflows.shared.reporter import WorkflowLiveReporter

from backend.workflows.shared.runtime import AppiumRuntimeInitializer


JsonObject = dict[str, Any]


class InitializeAppiumWorkflow:

    """Initialize Appium and run persistent graph-based app exploration."""

    def __init__(

        self,

        client: AppiumHTTPClient,

        agent: ExplorationAgent,

        reporter: WorkflowLiveReporter | None = None,

        graph_output_directory: Path | None = None,

    ) -> None:

        self.client = client

        self.agent = agent

        self.reporter = reporter

        self.graph_output_directory = graph_output_directory

    def run(

        self,

        *,

        apk_path: str,

        expected_package_id: str,

        objective: str,

        max_iterations: int = 25,

        device_id: str | None = None,

        context_options: JsonObject | None = None,

        memory_options: JsonObject | None = None,

        provision_options: JsonObject | None = None,

        job_timeout_seconds: float = 3600,

    ) -> JsonObject:

        self._validate(

            apk_path=apk_path,

            expected_package_id=expected_package_id,

            objective=objective,

            max_iterations=max_iterations,

            memory_options=memory_options,

        )

        memory_settings = dict(memory_options or {})

        graph = ExplorationGraphStore(

            package_id=expected_package_id,

            objective=objective.strip(),

            output_directory=self.graph_output_directory,

        )

        initialization = AppiumRuntimeInitializer(

            self.client,

            execute_step=self._execute_runtime_step,

        ).run(

            provision_options=provision_options,

            job_timeout_seconds=job_timeout_seconds,

        )

        application_status, verified_application = self._initialize_application(

            apk_path=apk_path.strip(),

            expected_package_id=expected_package_id,

            device_id=device_id,

        )

        launch = self._step(

            "open_application",

            lambda: self.client.open_application(expected_package_id),

            lambda result: result,

            step_input={

                "package_id": expected_package_id,

            },

        )

        run_id = self._required_string(launch, "run_id", "open result")

        screen_id = self._required_string(launch, "screen_id", "open result")

        fingerprint = launch.get("screen", {}).get("fingerprint")

        current_package = launch.get("screen", {}).get("package")

        current_activity = launch.get("screen", {}).get("activity")

        decisions: list[JsonObject] = []

        analyses: list[JsonObject] = []

        memory_summaries: list[JsonObject] = []

        status = "iteration_limit_reached"

        current_context: JsonObject = {}

        for iteration in range(1, max_iterations + 1):

            screen_ref = {

                "run_id": run_id,

                "screen_id": screen_id,

            }

            current_context = self._step(

                "export_screen_context",

                lambda: self.client.screen_context(

                    screen_ref,

                    dict(context_options or {}),

                ),

                lambda result: result,

                step_input={

                    "iteration": iteration,

                    "screen_reference": screen_ref,

                    "context_options": dict(context_options or {}),

                },

                title=f"Iteration {iteration}: capture screen context",

            )

            context_text = self._required_string(

                current_context,

                "text",

                "screen context",

            )

            inferred_package, inferred_activity = self._screen_meta(context_text)

            current_package = inferred_package or current_package

            current_activity = inferred_activity or current_activity

            node = self._step(

                "observe_graph_node",

                lambda: graph.observe_screen(

                    screen_id=screen_id,

                    fingerprint=(

                        str(fingerprint)

                        if fingerprint

                        else None

                    ),

                    context_text=context_text,

                    package=(

                        str(current_package)

                        if current_package

                        else None

                    ),

                    activity=(

                        str(current_activity)

                        if current_activity

                        else None

                    ),

                    screenshot=(

                        str(

                            current_context.get(

                                "visual_evidence",

                                {},

                            ).get("screenshot")

                        )

                        if current_context.get(

                            "visual_evidence",

                            {},

                        ).get("screenshot")

                        else None

                    ),

                ),

                lambda result: {

                    "node": result,

                    "coverage": graph.coverage(),

                },

                step_input={

                    "iteration": iteration,

                    "screen_id": screen_id,

                    "fingerprint": fingerprint,

                },

                title=f"Iteration {iteration}: identify graph node",

            )

            node_id = str(node["node_id"])

            analysis_context = graph.screen_analysis_context(node_id)

            analysis = self._step(

                "analyze_screen",

                lambda: self.agent.analyze_screen(

                    package_id=expected_package_id,

                    objective=objective.strip(),

                    iteration=iteration,

                    screen_context=context_text,

                    analysis_context=analysis_context,

                ),

                lambda result: {

                    "analysis": result.to_dict(),

                    "provider_exchanges": self.agent.exchanges,

                },

                step_input={

                    "iteration": iteration,

                    "node_id": node_id,

                    "screen_context": context_text,

                    "analysis_context": analysis_context,

                },

                title=f"Iteration {iteration}: understand screen dossier",

            )

            if not isinstance(analysis, ScreenAnalysis):

                raise RuntimeError(

                    "The LLM did not return a screen analysis."

                )

            analyses.append(analysis.to_dict())

            self._step(

                "update_screen_dossier",

                lambda: graph.apply_screen_analysis(

                    node_id,

                    screen_id,

                    analysis.to_dict(),

                    evidence={

                        "screenshot": current_context.get(

                            "visual_evidence",

                            {},

                        ).get("screenshot"),

                        "context_contract": current_context.get("contract"),

                    },

                ),

                lambda _result: graph.viewer_snapshot(),

                step_input={

                    "iteration": iteration,

                    "node_id": node_id,

                    "screen_id": screen_id,

                    "analysis": analysis.to_dict(),

                },

                title=f"Iteration {iteration}: update screen dossier",

            )

            if graph.needs_branch_summary(

                node_id,

                context_budget_characters=int(

                    memory_settings.get(

                        "context_budget_characters",

                        55_000,

                    )

                ),

                summarize_after_changes=int(

                    memory_settings.get(

                        "summarize_after_changes",

                        6,

                    )

                ),

            ):

                summary_input = graph.branch_summary_input(node_id)

                summary = self._step(

                    "summarize_branch_memory",

                    lambda: self.agent.summarize_branch(summary_input),

                    lambda result: {

                        "summary": result.to_dict(),

                        "provider_exchanges": self.agent.exchanges,

                    },

                    step_input={

                        "iteration": iteration,

                        "node_id": node_id,

                        "branch_context": summary_input,

                    },

                    title=(

                        f"Iteration {iteration}: compile branch memory"

                    ),

                )

                if not isinstance(summary, MemorySummary):

                    raise RuntimeError(

                        "The LLM did not return a memory summary."

                    )

                memory_summaries.append(summary.to_dict())

                self._step(

                    "update_branch_memory",

                    lambda: graph.apply_branch_summary(

                        summary.to_dict()

                    ),

                    lambda _result: graph.viewer_snapshot(),

                    step_input={

                        "iteration": iteration,

                        "node_id": node_id,

                        "summary": summary.to_dict(),

                    },

                    title=(

                        f"Iteration {iteration}: store branch summary"

                    ),

                )

            planning_context = graph.agent_context(node_id)

            decision = self._step(

                "llm_decision",

                lambda: self.agent.decide(

                    package_id=expected_package_id,

                    objective=objective.strip(),

                    iteration=iteration,

                    screen_context=context_text,

                    graph_context=planning_context,

                    validator=lambda value: self._validate_decision(

                        graph,

                        node_id,

                        screen_id,

                        value,

                        allow_incomplete_finish=(

                            iteration == max_iterations

                        ),

                    ),

                ),

                lambda result: {

                    "decision": result.to_dict(),

                    "provider_exchanges": self.agent.exchanges,

                },

                step_input={

                    "iteration": iteration,

                    "objective": objective.strip(),

                    "current_node": node,

                    "screen_context": context_text,

                    "planning_context": planning_context,

                },

                title=f"Iteration {iteration}: agent reasoning",

            )

            if not isinstance(decision, AgentDecision):

                raise RuntimeError("The LLM did not return an agent decision.")

            decisions.append(decision.to_dict())

            self._step(

                "update_traversal_plan",

                lambda: graph.apply_planning_decision(

                    node_id,

                    decision.to_dict(),

                ),

                lambda _result: graph.viewer_snapshot(),

                step_input={

                    "iteration": iteration,

                    "node_id": node_id,

                    "blocked_actions": decision.blocked_actions,

                    "exploration_goal": decision.exploration_goal,

                    "return_plan": decision.return_plan,

                },

                title=f"Iteration {iteration}: store traversal plan",

            )

            if decision.decision == "finish":

                unresolved_at_finish = graph.total_unresolved_actions(

                    node_id,

                    decision.blocked_actions,

                )

                status = (

                    "iteration_limit_reached"

                    if unresolved_at_finish

                    else "completed"

                )

                break

            action_request = decision.to_action_request(screen_id)

            action_result = self._step(

                "perform_action",

                lambda: self.client.perform_action(

                    run_id,

                    action_request,

                ),

                lambda result: result,

                step_input={

                    "iteration": iteration,

                    "node_id": node_id,

                    "exploration_goal": decision.exploration_goal,

                    "expected_result": decision.expected_result,

                    "reason": decision.reason,

                    "action": action_request,

                },

                title=f"Iteration {iteration}: execute agent action",

            )

            edge = self._step(

                "record_graph_transition",

                lambda: graph.record_transition(

                    iteration=iteration,

                    from_node_id=node_id,

                    action_request=action_request,

                    action_result=action_result,

                    expectation={

                        "expected_result": decision.expected_result,

                        "preconditions": [],

                    },

                ),

                lambda result: {

                    "edge": result,

                    **graph.viewer_snapshot(),

                },

                step_input={

                    "iteration": iteration,

                    "from_node_id": node_id,

                    "classification": action_result.get("classification"),

                    "app_health": action_result.get("app_health"),

                },

                title=f"Iteration {iteration}: record transition",

            )

            after = action_result.get("after")

            if not isinstance(after, dict):

                raise RuntimeError("The action result is missing 'after'.")

            screen_id = self._required_string(

                after,

                "screen_id",

                "action result",

            )

            fingerprint = after.get("fingerprint")

            health = action_result.get("app_health", {})

            current_package = health.get("current_package")

            current_activity = None

            if edge.get("to_node_id") is None:

                raise RuntimeError("The graph transition is missing its target.")

        self._step(

            "update_exploration_memory",

            lambda: graph.finish(status),

            lambda _result: graph.viewer_snapshot(),

            step_input={

                "final_status": status,

                "iterations": len(decisions),

            },

            title="Finalize exploration graph and knowledge",

        )

        graph_snapshot = graph.viewer_snapshot()

        return {

            "contract": "workflow.agentic_app_exploration",

            "schema_version": 2,

            "status": status,

            **initialization,

            "application": {

                "status": application_status,

                "installed": True,

                "package_id": verified_application.get("package_id"),

                "version_name": verified_application.get("version_name"),

                "version_code": verified_application.get("version_code"),

            },

            "run_id": run_id,

            "current_screen_id": screen_id,

            "iterations": len(decisions),

            "decisions": decisions,

            "screen_analyses": analyses,

            "memory_summaries": memory_summaries,

            **graph_snapshot,

            "graph_artifact": (

                str(self.graph_output_directory / "exploration-graph.json")

                if self.graph_output_directory is not None

                else None

            ),

            "knowledge_artifact": (

                str(self.graph_output_directory / "knowledge-box.json")

                if self.graph_output_directory is not None

                else None

            ),

            "viewer_url": (

                self.reporter.viewer_url

                if self.reporter is not None

                else None

            ),

        }

    def _initialize_application(

        self,

        *,

        apk_path: str,

        expected_package_id: str,

        device_id: str | None,

    ) -> tuple[str, JsonObject]:

        application = self._step(

            "inspect_application",

            lambda: self.client.installed_app(

                expected_package_id,

                device_id=device_id,

            ),

            lambda result: {

                "installed": result is not None,

                "application": result,

            },

            step_input={

                "package_id": expected_package_id,

                "device_id": device_id,

            },

        )

        status = "reused"

        if application is None:

            self._step(

                "install_application",

                lambda: self.client.install_app(

                    apk_path=apk_path,

                    expected_package_id=expected_package_id,

                    install_mode="preserve",

                    device_id=device_id,

                ),

                lambda result: result,

                step_input={

                    "apk_path": apk_path,

                    "expected_package_id": expected_package_id,

                    "install_mode": "preserve",

                    "device_id": device_id,

                },

            )

            status = "installed"

        verified = self._step(

            "verify_application",

            lambda: self.client.installed_app(

                expected_package_id,

                device_id=device_id,

            ),

            lambda result: {

                "verified": result is not None,

                "application": result,

            },

            step_input={

                "package_id": expected_package_id,

                "device_id": device_id,

            },

        )

        if not isinstance(verified, dict):

            raise RuntimeError(

                f"Application '{expected_package_id}' is not installed."

            )

        return status, verified

    @staticmethod
    def _validate(

        *,

        apk_path: str,

        expected_package_id: str,

        objective: str,

        max_iterations: int,

        memory_options: JsonObject | None,

    ) -> None:

        if not isinstance(apk_path, str) or not apk_path.strip():

            raise ValueError("'apk_path' cannot be empty.")

        if not isinstance(expected_package_id, str) or not expected_package_id:

            raise ValueError("'expected_package_id' cannot be empty.")

        if not isinstance(objective, str) or not objective.strip():

            raise ValueError("'objective' cannot be empty.")

        if not 1 <= max_iterations <= 100:

            raise ValueError("'max_iterations' must be between 1 and 100.")

        memory = dict(memory_options or {})

        for field, default in (
            ("context_budget_characters", 55_000),
            ("summarize_after_changes", 6),
        ):

            value = memory.get(field, default)

            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 1
            ):

                raise ValueError(

                    f"'memory.{field}' must be a positive integer."

                )

    @staticmethod
    def _validate_decision(

        graph: ExplorationGraphStore,

        node_id: str,

        screen_id: str,

        decision: AgentDecision,

        *,

        allow_incomplete_finish: bool = False,

    ) -> None:

        unresolved = graph.total_unresolved_actions(

            node_id,

            decision.blocked_actions,

        )

        if (

            decision.decision == "finish"

            and unresolved

            and not allow_incomplete_finish

        ):

            keys = ", ".join(

                str(item.get("action_key"))

                for item in unresolved[:8]

            )

            raise AgentDecisionError(

                "Exploration cannot finish while reachable actions remain "

                f"unresolved: {keys}. Explore them, backtrack, or explicitly "

                "mark technically impossible actions in blocked_actions."

            )

        if decision.decision != "act":

            return

        action_request = decision.to_action_request(screen_id)

        action_name = str(action_request.get("action"))

        recovery_actions = {

            "back",

            "recover",

            "wait",

            "activate_app",

            "restart_app",

            "return_to_start",

        }

        if (

            action_name not in recovery_actions

            and not graph.known_action(node_id, action_request)

        ):

            raise AgentDecisionError(

                "The selected action is not available on the current graph node."

            )

        if (

            graph.is_explored(node_id, action_request)

            and unresolved

            and action_name not in recovery_actions

        ):

            raise AgentDecisionError(

                "That node/action pair was already explored. Choose an "

                "unexplored action or a recovery/backtracking action."

            )

    def _execute_runtime_step(

        self,

        name: str,

        operation: Callable[[], Any],

        summarize: Callable[[Any], JsonObject],

    ) -> Any:

        return self._step(name, operation, summarize)

    def _step(

        self,

        name: str,

        operation: Callable[[], Any],

        summarize: Callable[[Any], Any],

        *,

        step_input: Any = None,

        title: str | None = None,

    ) -> Any:

        if self.reporter is None:

            return operation()

        return self.reporter.step(

            name,

            operation,

            summarize,

            step_input=step_input,

            title=title,

        )

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

    @staticmethod
    def _screen_meta(context_text: str) -> tuple[str | None, str | None]:

        package_match = re.search(r"^- App: .+ \(([^)]+)\)$", context_text, re.M)

        activity_match = re.search(r"^- Activity: (.+)$", context_text, re.M)

        return (

            package_match.group(1) if package_match else None,

            activity_match.group(1).strip() if activity_match else None,

        )
