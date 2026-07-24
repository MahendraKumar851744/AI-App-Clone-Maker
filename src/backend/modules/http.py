from __future__ import annotations

import os
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

import requests

from backend.core.contracts import (
    BaseModule,
    ExecutionContext,
    JsonObject,
    ModuleDefinition,
)
from backend.core.templating import TemplateRenderer
from backend.errors import (
    ExternalServiceError,
    ModuleExecutionError,
    RequestValidationError,
)


ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


class HTTPModule(BaseModule):
    type_name = "http"
    type_description = "Templated outbound HTTP request with structured response."

    def __init__(
        self,
        definition: ModuleDefinition,
        renderer: TemplateRenderer | None = None,
        session: requests.Session | None = None,
    ) -> None:
        super().__init__(definition)
        self.renderer = renderer or TemplateRenderer()
        self.session = session or requests.Session()
        config = definition.config

        url = config.get("url")
        if not isinstance(url, str) or not url:
            raise RequestValidationError("HTTP module config requires a non-empty 'url'.")
        method = config.get("method", "GET")
        if not isinstance(method, str) or method.upper() not in ALLOWED_METHODS:
            raise RequestValidationError(
                "'config.method' must be a supported HTTP method.",
                details={"allowed_methods": sorted(ALLOWED_METHODS)},
            )
        if "json" in config and "data" in config:
            raise RequestValidationError(
                "HTTP module config cannot contain both 'json' and 'data'."
            )
        timeout = config.get("timeout", 30)
        if not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
            raise RequestValidationError(
                "'config.timeout' must be between 0 and 300 seconds."
            )
        max_bytes = config.get("max_response_bytes", 1_048_576)
        if not isinstance(max_bytes, int) or not 1 <= max_bytes <= 10_485_760:
            raise RequestValidationError(
                "'config.max_response_bytes' must be between 1 and 10485760."
            )
        for field_name in ("headers", "params"):
            if field_name in config and not isinstance(config[field_name], dict):
                raise RequestValidationError(
                    f"'config.{field_name}' must be a JSON object."
                )

    def execute(self, module_input: JsonObject, context: ExecutionContext) -> JsonObject:
        config = self.definition.config
        render_context = {
            "input": module_input,
            "env": dict(os.environ),
            "context": {
                "run_id": context.run_id,
                "step_id": context.step_id,
                **dict(context.metadata),
            },
        }
        url = self.renderer.render_text(config["url"], render_context)
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RequestValidationError(
                "Rendered HTTP module URL must use HTTP or HTTPS."
            )

        headers = self.renderer.render_value(config.get("headers", {}), render_context)
        params = self.renderer.render_value(config.get("params", {}), render_context)
        request_kwargs: dict[str, Any] = {
            "method": str(config.get("method", "GET")).upper(),
            "url": url,
            "headers": headers,
            "params": params,
            "timeout": float(config.get("timeout", 30)),
        }
        if "json" in config:
            request_kwargs["json"] = self.renderer.render_value(
                config["json"],
                render_context,
            )
        if "data" in config:
            request_kwargs["data"] = self.renderer.render_value(
                config["data"],
                render_context,
            )

        started = perf_counter()
        try:
            response = self.session.request(**request_kwargs)
        except requests.RequestException as error:
            raise ExternalServiceError(
                "The HTTP module request failed.",
                details={"reason": str(error), "url": url},
            ) from error

        elapsed_ms = round((perf_counter() - started) * 1000)
        max_bytes = int(config.get("max_response_bytes", 1_048_576))
        if len(response.content) > max_bytes:
            raise ModuleExecutionError(
                "The HTTP response exceeded the configured size limit.",
                details={
                    "size_bytes": len(response.content),
                    "max_response_bytes": max_bytes,
                },
            )

        content_type = response.headers.get("Content-Type", "")
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text

        output = {
            "status_code": response.status_code,
            "ok": response.ok,
            "url": response.url,
            "headers": dict(response.headers),
            "content_type": content_type,
            "body": body,
            "elapsed_ms": elapsed_ms,
        }
        if not response.ok and config.get("raise_for_status", True):
            raise ExternalServiceError(
                "The HTTP module received an unsuccessful response.",
                details=output,
            )
        return output
