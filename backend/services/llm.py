from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

import requests

from backend.core.contracts import JsonObject
from backend.errors import ExternalServiceError, RequestValidationError


@dataclass(frozen=True)
class LLMResponse:
    text: str
    raw: JsonObject
    usage: JsonObject = field(default_factory=dict)


class LLMProvider(Protocol):
    def generate(
        self,
        messages: list[JsonObject],
        provider_config: JsonObject,
    ) -> LLMResponse:
        """Generate a response from chat messages."""


class EchoLLMProvider:
    """Deterministic provider for local development and workflow testing."""

    def generate(
        self,
        messages: list[JsonObject],
        provider_config: JsonObject,
    ) -> LLMResponse:
        user_messages = [
            message["content"]
            for message in messages
            if message.get("role") == "user"
        ]
        text = str(user_messages[-1] if user_messages else "")
        return LLMResponse(text=text, raw={"provider": "echo", "messages": messages})


class HTTPChatLLMProvider:
    """
    Adapter for chat-completion-style HTTP endpoints.

    Provider-specific settings stay inside provider_config so the LLM module
    contract does not depend on a particular vendor SDK.
    """

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    def generate(
        self,
        messages: list[JsonObject],
        provider_config: JsonObject,
    ) -> LLMResponse:
        endpoint = provider_config.get("endpoint")
        model = provider_config.get("model")
        if not isinstance(endpoint, str) or not endpoint.startswith(("http://", "https://")):
            raise RequestValidationError(
                "'provider_config.endpoint' must be an HTTP or HTTPS URL."
            )
        if not isinstance(model, str) or not model:
            raise RequestValidationError("'provider_config.model' is required.")

        timeout = provider_config.get("timeout", 60)
        if not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
            raise RequestValidationError(
                "'provider_config.timeout' must be between 0 and 300 seconds."
            )

        headers = {"Content-Type": "application/json"}
        custom_headers = provider_config.get("headers", {})
        if not isinstance(custom_headers, dict):
            raise RequestValidationError("'provider_config.headers' must be an object.")
        headers.update({str(key): str(value) for key, value in custom_headers.items()})

        api_key_env = provider_config.get("api_key_env")
        if api_key_env:
            if not isinstance(api_key_env, str):
                raise RequestValidationError(
                    "'provider_config.api_key_env' must be a string."
                )
            api_key = os.environ.get(api_key_env)
            if not api_key:
                raise RequestValidationError(
                    f"Required LLM API key environment variable '{api_key_env}' is not set."
                )
            auth_header = str(provider_config.get("auth_header", "Authorization"))
            auth_prefix = str(provider_config.get("auth_prefix", "Bearer"))
            headers[auth_header] = f"{auth_prefix} {api_key}".strip()

        options = provider_config.get("options", {})
        if not isinstance(options, dict):
            raise RequestValidationError("'provider_config.options' must be an object.")
        request_body = {**options, "model": model, "messages": messages}

        try:
            response = self.session.post(
                endpoint,
                headers=headers,
                json=request_body,
                timeout=float(timeout),
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as error:
            raise ExternalServiceError(
                "The LLM provider request failed.",
                details={"reason": str(error), "endpoint": endpoint},
            ) from error
        except ValueError as error:
            raise ExternalServiceError(
                "The LLM provider returned invalid JSON.",
                details={"endpoint": endpoint},
            ) from error

        if not isinstance(data, dict):
            raise ExternalServiceError("The LLM provider response must be a JSON object.")
        response_path = provider_config.get(
            "response_path",
            "choices.0.message.content",
        )
        if not isinstance(response_path, str):
            raise RequestValidationError(
                "'provider_config.response_path' must be a string."
            )
        text = self._resolve_path(data, response_path)
        if not isinstance(text, str):
            raise ExternalServiceError(
                "The configured LLM response path did not resolve to text.",
                details={"response_path": response_path},
            )
        usage = data.get("usage", {})
        return LLMResponse(
            text=text,
            raw=data,
            usage=usage if isinstance(usage, dict) else {},
        )

    @staticmethod
    def _resolve_path(data: Any, path: str) -> Any:
        current = data
        try:
            for part in path.split("."):
                if isinstance(current, dict):
                    current = current[part]
                elif isinstance(current, list) and part.isdigit():
                    current = current[int(part)]
                else:
                    raise KeyError(part)
            return current
        except (KeyError, IndexError, TypeError) as error:
            raise ExternalServiceError(
                "The configured LLM response path was not found.",
                details={"response_path": path},
            ) from error


class LLMProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, LLMProvider] = {}

    def register(self, name: str, provider: LLMProvider) -> None:
        if name in self._providers:
            raise ValueError(f"LLM provider '{name}' is already registered.")
        self._providers[name] = provider

    def get(self, name: str) -> LLMProvider:
        provider = self._providers.get(name)
        if provider is None:
            raise RequestValidationError(
                f"Unknown LLM provider '{name}'.",
                details={"available_providers": sorted(self._providers)},
            )
        return provider

    def list(self) -> list[str]:
        return sorted(self._providers)
