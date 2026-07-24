from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from backend.core.contracts import JsonObject, require_object
from backend.core.executor import ModuleExecutor, RunStore
from backend.core.registry import ModuleRegistry
from backend.errors import RequestValidationError
from backend.errors import ResourceConflictError, ResourceNotFoundError
from backend.exploration.actions import (
    ActionValidationError,
    ExplorationActionManager,
    ExplorationRunNotFoundError,
    ScreenNotFoundError,
    StaleScreenError,
)
from backend.modules.llm_provider import LLMProviderRegistry


api = Blueprint("api", __name__, url_prefix="/api/v1")


def registry() -> ModuleRegistry:
    return current_app.extensions["module_registry"]


def executor() -> ModuleExecutor:
    return current_app.extensions["module_executor"]


def run_store() -> RunStore:
    return current_app.extensions["run_store"]


def action_manager() -> ExplorationActionManager:
    return current_app.extensions["exploration_action_manager"]


def json_body() -> JsonObject:
    payload = request.get_json(silent=True)
    if payload is None:
        raise RequestValidationError("Request body must contain valid JSON.")
    return require_object(payload, "request body")


@api.get("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "service": "ai-app-clone-maker",
            "version": "0.2.0",
        }
    )


@api.get("/module-types")
def list_module_types():
    llm_registry: LLMProviderRegistry = current_app.extensions[
        "llm_provider_registry"
    ]
    return jsonify(
        {
            "items": registry().list_types(),
            "llm_providers": llm_registry.list(),
        }
    )


@api.get("/modules")
def list_modules():
    items = registry().list_modules()
    return jsonify({"items": items, "count": len(items)})


@api.post("/modules")
def create_module():
    module = registry().create(json_body())
    return jsonify({"module": module.describe()}), 201


@api.get("/modules/<module_id>")
def get_module(module_id: str):
    return jsonify({"module": registry().get(module_id).describe()})


@api.delete("/modules/<module_id>")
def delete_module(module_id: str):
    registry().delete(module_id)
    return "", 204


@api.post("/modules/<module_id>/execute")
def execute_module(module_id: str):
    payload = json_body()
    module_input = require_object(payload.get("input", {}), "input")
    run = executor().execute_module(module_id, module_input)
    return jsonify({"run": run.to_dict(), "output": run.output})


@api.post("/workflows/execute")
def execute_workflow():
    run = executor().execute_workflow(json_body())
    return jsonify({"run": run.to_dict(), "output": run.output})


@api.get("/runs")
def list_runs():
    raw_limit = request.args.get("limit", "50")
    try:
        limit = int(raw_limit)
    except ValueError as error:
        raise RequestValidationError("'limit' must be an integer.") from error
    items = run_store().list(limit=limit)
    return jsonify({"items": items, "count": len(items)})


@api.get("/runs/<run_id>")
def get_run(run_id: str):
    return jsonify({"run": run_store().get(run_id).to_dict()})


@api.post("/explorations/<run_id>/actions")
def execute_exploration_action(run_id: str):
    try:
        result = action_manager().execute(run_id, json_body())
    except ActionValidationError as error:
        raise RequestValidationError(str(error)) from error
    except (ExplorationRunNotFoundError, ScreenNotFoundError) as error:
        raise ResourceNotFoundError(str(error)) from error
    except StaleScreenError as error:
        raise ResourceConflictError(
            str(error),
            details=error.details,
        ) from error
    return jsonify({"result": result})
