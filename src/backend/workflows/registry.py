from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Callable

from backend.workflows.shared.config import JsonObject


WorkflowRunner = Callable[[JsonObject], JsonObject]


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    description: str
    runner: str
    default_config: str

    def load_runner(self) -> WorkflowRunner:
        module_name, function_name = self.runner.split(":", 1)
        function = getattr(import_module(module_name), function_name)
        return function


WORKFLOWS = {
    "initialize_appium": WorkflowDefinition(
        name="initialize_appium",
        description=(
            "Initialize Appium and run graph-based agentic app exploration."
        ),
        runner="backend.workflows.initialize_appium.cli:run_config",
        default_config="workflows/initialize_appium/config.json",
    ),
    "simple_exploration": WorkflowDefinition(
        name="simple_exploration",
        description="Run bounded Qwen-guided Android app exploration.",
        runner="backend.workflows.simple_exploration.cli:run_config",
        default_config="workflows/simple_exploration/config.json",
    ),
}


def get_workflow(name: str) -> WorkflowDefinition:
    try:
        return WORKFLOWS[name]
    except KeyError as error:
        available = ", ".join(sorted(WORKFLOWS))
        raise ValueError(
            f"Unknown workflow '{name}'. Available: {available}"
        ) from error
