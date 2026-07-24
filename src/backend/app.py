from __future__ import annotations

from typing import Any

import requests
from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

from backend.api import api
from backend.core.executor import ModuleExecutor, RunStore
from backend.core.registry import ModuleRegistry
from backend.core.templating import TemplateRenderer
from backend.errors import PlatformError
from backend.modules import AutomationModule, HTTPModule, LLMModule, LogicModule
from backend.modules.llm_provider import (
    EchoLLMProvider,
    HTTPChatLLMProvider,
    LLMProviderRegistry,
)


def create_app(config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        JSON_SORT_KEYS=False,
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        RUN_STORE_MAX_ITEMS=500,
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
