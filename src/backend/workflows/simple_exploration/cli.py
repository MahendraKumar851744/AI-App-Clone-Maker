from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.modules.llm_provider import HTTPChatLLMProvider
from backend.workflows.shared.client import AppiumHTTPClient
from backend.workflows.shared.config import JsonObject, load_workflow_config
from backend.workflows.simple_exploration.llm import (
    SYSTEM_PROMPT,
    USER_PROMPT,
    ExplorationLLM,
)
from backend.workflows.simple_exploration.workflow import (
    SimpleExplorationWorkflow,
)


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
    llm = ExplorationLLM(
        HTTPChatLLMProvider(),
        provider_config=llm_config.get("provider_config", {}),
        system_prompt=llm_config.get("system_prompt") or SYSTEM_PROMPT,
        user_prompt=llm_config.get("user_prompt") or USER_PROMPT,
        max_attempts=int(llm_config.get("max_attempts", 2)),
    )
    workflow = SimpleExplorationWorkflow(
        client,
        llm,
        context_options=workflow_config.get("context_options", {}),
    )
    return workflow.run(
        apk_path=workflow_config["apk_path"],
        expected_package_id=workflow_config["expected_package_id"],
        objective=workflow_config["objective"],
        install_policy=workflow_config.get("install_policy", "if_missing"),
        max_iterations=int(workflow_config.get("max_iterations", 10)),
        device_id=workflow_config.get("device_id"),
        provision_options=workflow_config.get("provision_options", {}),
        job_timeout_seconds=float(
            workflow_config.get("job_timeout_seconds", 3600)
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the simple LLM-guided Appium exploration workflow."
    )
    parser.add_argument("config", type=Path, help="Path to workflow JSON.")
    args = parser.parse_args()
    result = run_config(load_workflow_config(args.config))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
