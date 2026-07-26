from __future__ import annotations

import argparse
from pathlib import Path

from backend.modules.llm_provider import HTTPChatLLMProvider
from backend.workflows.initialize_appium.agent import (
    MEMORY_COMPILER_SYSTEM_PROMPT,
    MEMORY_COMPILER_USER_PROMPT,
    SCREEN_ANALYST_SYSTEM_PROMPT,
    SCREEN_ANALYST_USER_PROMPT,
    ExplorationAgent,
    SYSTEM_PROMPT,
    USER_PROMPT,
)
from backend.workflows.initialize_appium.workflow import InitializeAppiumWorkflow
from backend.workflows.shared.client import AppiumHTTPClient
from backend.workflows.shared.config import (
    PROJECT_ROOT,
    JsonObject,
    load_workflow_config,
)
from backend.workflows.shared.reporter import WorkflowLiveReporter


def run_config(config: JsonObject) -> JsonObject:
    api = config.get("api", {})
    llm_config = config.get("llm", {})
    workflow_config = config.get("workflow", {})
    if llm_config.get("provider", "http_chat") != "http_chat":
        raise ValueError(
            "The CLI currently supports llm.provider='http_chat'."
        )
    client = AppiumHTTPClient(
        api.get("base_url", "http://127.0.0.1:5000"),
        timeout_seconds=float(api.get("timeout_seconds", 90)),
        headers=api.get("headers", {}),
    )
    agent = ExplorationAgent(
        HTTPChatLLMProvider(),
        provider_config=llm_config.get("provider_config", {}),
        system_prompt=llm_config.get("system_prompt") or SYSTEM_PROMPT,
        user_prompt=llm_config.get("user_prompt") or USER_PROMPT,
        screen_analyst_system_prompt=(
            llm_config.get("screen_analyst_system_prompt")
            or SCREEN_ANALYST_SYSTEM_PROMPT
        ),
        screen_analyst_user_prompt=(
            llm_config.get("screen_analyst_user_prompt")
            or SCREEN_ANALYST_USER_PROMPT
        ),
        memory_compiler_system_prompt=(
            llm_config.get("memory_compiler_system_prompt")
            or MEMORY_COMPILER_SYSTEM_PROMPT
        ),
        memory_compiler_user_prompt=(
            llm_config.get("memory_compiler_user_prompt")
            or MEMORY_COMPILER_USER_PROMPT
        ),
        max_attempts=int(llm_config.get("max_attempts", 3)),
    )
    reporter = WorkflowLiveReporter(
        client,
        name="agentic_app_exploration",
        metadata={
            "mode": "graph_based_agent",
            "objective": workflow_config.get("objective"),
            "max_iterations": workflow_config.get("max_iterations", 25),
            "memory": workflow_config.get("memory", {}),
            "application": {
                "apk_path": workflow_config.get("apk_path"),
                "package_id": workflow_config.get("expected_package_id"),
                "device_id": workflow_config.get("device_id"),
            },
            "llm": {
                "provider": llm_config.get("provider", "http_chat"),
                "endpoint": llm_config.get("provider_config", {}).get("endpoint"),
                "model": llm_config.get("provider_config", {}).get("model"),
            },
        },
    )
    graph_output_directory = (
        PROJECT_ROOT
        / "artifacts"
        / "workflows"
        / reporter.run_id
    )
    print(
        f"\nLive workflow viewer:\n{reporter.viewer_url}\n",
        flush=True,
    )
    try:
        result = InitializeAppiumWorkflow(
            client,
            agent,
            reporter,
            graph_output_directory=graph_output_directory,
        ).run(
            apk_path=workflow_config["apk_path"],
            expected_package_id=workflow_config["expected_package_id"],
            objective=workflow_config["objective"],
            max_iterations=int(workflow_config.get("max_iterations", 25)),
            device_id=workflow_config.get("device_id"),
            context_options=workflow_config.get("context_options", {}),
            memory_options=workflow_config.get("memory", {}),
            provision_options=workflow_config.get("provision_options", {}),
            job_timeout_seconds=float(
                workflow_config.get("job_timeout_seconds", 3600)
            ),
        )
    except Exception as error:
        reporter.fail(error)
        raise
    reporter.complete(
        {
            "status": result.get("status"),
            "application": result.get("application"),
            "iterations": result.get("iterations"),
            "graph": result.get("graph"),
            "knowledge_box": result.get("knowledge_box"),
            "graph_artifact": result.get("graph_artifact"),
            "knowledge_artifact": result.get("knowledge_artifact"),
        }
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provision and start Appium only when it is not ready."
    )
    parser.add_argument("config", type=Path, help="Path to workflow JSON.")
    args = parser.parse_args()
    result = run_config(load_workflow_config(args.config))
    print(f"Workflow completed.\nViewer: {result['viewer_url']}")


if __name__ == "__main__":
    main()
