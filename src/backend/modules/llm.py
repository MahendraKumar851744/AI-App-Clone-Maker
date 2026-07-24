from __future__ import annotations

import json
import os

from backend.core.contracts import (
    BaseModule,
    ExecutionContext,
    JsonObject,
    ModuleDefinition,
)
from backend.core.templating import TemplateRenderer
from backend.errors import ModuleExecutionError, RequestValidationError
from backend.modules.llm_provider import LLMProviderRegistry


class LLMModule(BaseModule):
    type_name = "llm"
    type_description = (
        "System/user prompt rendering with a pluggable LLM provider."
    )

    def __init__(
        self,
        definition: ModuleDefinition,
        provider_registry: LLMProviderRegistry,
        renderer: TemplateRenderer | None = None,
    ) -> None:
        super().__init__(definition)
        self.provider_registry = provider_registry
        self.renderer = renderer or TemplateRenderer()
        config = definition.config

        user_prompt = config.get("user_prompt")
        if not isinstance(user_prompt, str) or not user_prompt:
            raise RequestValidationError(
                "LLM module config requires a non-empty 'user_prompt'."
            )
        system_prompt = config.get("system_prompt", "")
        if not isinstance(system_prompt, str):
            raise RequestValidationError("'config.system_prompt' must be a string.")
        provider = config.get("provider", "echo")
        if not isinstance(provider, str) or not provider:
            raise RequestValidationError("'config.provider' must be a string.")
        provider_config = config.get("provider_config", {})
        if not isinstance(provider_config, dict):
            raise RequestValidationError(
                "'config.provider_config' must be a JSON object."
            )
        output_format = config.get("output_format", "text")
        if output_format not in {"text", "json"}:
            raise RequestValidationError(
                "'config.output_format' must be 'text' or 'json'."
            )
        self.provider_registry.get(provider)

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
        messages: list[JsonObject] = []
        system_prompt = config.get("system_prompt", "")
        if system_prompt:
            messages.append(
                {
                    "role": "system",
                    "content": self.renderer.render_text(
                        system_prompt,
                        render_context,
                    ),
                }
            )
        messages.append(
            {
                "role": "user",
                "content": self.renderer.render_text(
                    config["user_prompt"],
                    render_context,
                ),
            }
        )

        provider_name = config.get("provider", "echo")
        provider = self.provider_registry.get(provider_name)
        response = provider.generate(messages, config.get("provider_config", {}))

        common = {
            "provider": provider_name,
            "usage": response.usage,
        }
        if config.get("include_raw_response", False):
            common["raw"] = response.raw

        if config.get("output_format", "text") == "json":
            try:
                parsed = json.loads(response.text)
            except json.JSONDecodeError as error:
                raise ModuleExecutionError(
                    "The LLM response was not valid JSON.",
                    details={"reason": str(error)},
                ) from error
            return {"data": parsed, **common}
        return {"text": response.text, **common}
