from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests
from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

from backend.api import api
from backend.apps import AndroidAppManager
from backend.core.executor import ModuleExecutor, RunStore
from backend.core.registry import ModuleRegistry
from backend.core.templating import TemplateRenderer
from backend.device_selection import AndroidDeviceCoordinator
from backend.errors import PlatformError
from backend.exploration.actions import ExplorationActionManager
from backend.exploration.context_export import ScreenContextExporter
from backend.modules import AutomationModule, HTTPModule, LLMModule, LogicModule
from backend.modules.llm_provider import (
    EchoLLMProvider,
    HTTPChatLLMProvider,
    LLMProviderRegistry,
)
from backend.runtime import RuntimeManager
from backend.workflow_runs import WorkflowRunStore


def create_app(config: dict[str, Any] | None = None) -> Flask:
    project_root = Path(__file__).resolve().parents[2]
    app = Flask(__name__)
    app.config.from_mapping(
        JSON_SORT_KEYS=False,
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        RUN_STORE_MAX_ITEMS=500,
        RUNTIME_ADMIN_TOKEN=os.environ.get("RUNTIME_ADMIN_TOKEN"),
        APP_APK_ROOTS=[
            project_root / "assets" / "apps"
        ],
        EXPLORATION_OUTPUT_ROOT=(
            project_root
            / "artifacts"
            / "explorations"
        ),
        WORKFLOW_OUTPUT_ROOT=(
            project_root
            / "artifacts"
            / "workflows"
        ),
    )
    if config:
        app.config.update(config)

    renderer = TemplateRenderer()
    http_session = app.config.get("HTTP_SESSION") or requests.Session()

    llm_providers = LLMProviderRegistry()
    llm_providers.register("echo", EchoLLMProvider())
    llm_providers.register("http_chat", HTTPChatLLMProvider(http_session))

    module_registry = ModuleRegistry()
    module_registry.register_type(
        LogicModule,
        lambda definition: LogicModule(definition, renderer),
    )
    module_registry.register_type(
        HTTPModule,
        lambda definition: HTTPModule(definition, renderer, http_session),
    )
    module_registry.register_type(
        LLMModule,
        lambda definition: LLMModule(definition, llm_providers, renderer),
    )
    module_registry.register_type(
        AutomationModule,
        lambda definition: AutomationModule(
            definition,
            app.config.get("AUTOMATION_SERVICE"),
        ),
    )

    run_store = RunStore(max_runs=int(app.config["RUN_STORE_MAX_ITEMS"]))
    module_executor = ModuleExecutor(module_registry, run_store, renderer)

    app.extensions["module_registry"] = module_registry
    app.extensions["module_executor"] = module_executor
    app.extensions["run_store"] = run_store
    app.extensions["llm_provider_registry"] = llm_providers
    app.extensions["exploration_action_manager"] = app.config.get(
        "EXPLORATION_ACTION_MANAGER"
    ) or ExplorationActionManager(app.config["EXPLORATION_OUTPUT_ROOT"])
    app.extensions["screen_context_exporter"] = app.config.get(
        "SCREEN_CONTEXT_EXPORTER"
    ) or ScreenContextExporter(app.config["EXPLORATION_OUTPUT_ROOT"])
    app.extensions["runtime_manager"] = app.config.get(
        "RUNTIME_MANAGER"
    ) or RuntimeManager(project_root)
    app.extensions["android_app_manager"] = app.config.get(
        "ANDROID_APP_MANAGER"
    ) or AndroidAppManager(
        allowed_apk_roots=list(app.config["APP_APK_ROOTS"]),
        runtime_manager=app.extensions["runtime_manager"],
        action_manager=app.extensions["exploration_action_manager"],
        device_coordinator=AndroidDeviceCoordinator(
            project_root=project_root,
            runtime_manager=app.extensions["runtime_manager"],
        ),
    )
    app.extensions["workflow_run_store"] = app.config.get(
        "WORKFLOW_RUN_STORE"
    ) or WorkflowRunStore(app.config["WORKFLOW_OUTPUT_ROOT"])

    app.register_blueprint(api)
    register_error_handlers(app)

    @app.get("/")
    def index():
        return jsonify(
            {
                "service": "ai-app-clone-maker",
                "api": "/api/v1",
                "health": "/api/v1/health",
            }
        )

    return app


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(PlatformError)
    def handle_platform_error(error: PlatformError):
        return jsonify(error.to_dict()), error.status_code

    @app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException):
        return (
            jsonify(
                {
                    "error": {
                        "code": error.name.lower().replace(" ", "_"),
                        "message": error.description,
                    }
                }
            ),
            error.code,
        )

    @app.errorhandler(Exception)
    def handle_unexpected_error(error: Exception):
        app.logger.exception("Unhandled backend error", exc_info=error)
        return (
            jsonify(
                {
                    "error": {
                        "code": "internal_server_error",
                        "message": "An unexpected server error occurred.",
                    }
                }
            ),
            500,
        )
