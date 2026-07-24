from __future__ import annotations

from backend.core.contracts import (
    BaseModule,
    ExecutionContext,
    JsonObject,
    ModuleDefinition,
    require_object,
)
from backend.core.templating import TemplateRenderer
from backend.errors import RequestValidationError


class LogicModule(BaseModule):
    type_name = "logic"
    type_description = "Safe template-based data mapping and transformation."

    def __init__(
        self,
        definition: ModuleDefinition,
        renderer: TemplateRenderer | None = None,
    ) -> None:
        super().__init__(definition)
        self.renderer = renderer or TemplateRenderer()
        output_template = definition.config.get("output")
        if not isinstance(output_template, dict):
            raise RequestValidationError(
                "Logic module config requires an 'output' JSON object."
            )
        merge_input = definition.config.get("merge_input", False)
        if not isinstance(merge_input, bool):
            raise RequestValidationError("'config.merge_input' must be a boolean.")

    def execute(self, module_input: JsonObject, context: ExecutionContext) -> JsonObject:
        rendered = self.renderer.render_value(
            self.definition.config["output"],
            {
                "input": module_input,
                "context": {
                    "run_id": context.run_id,
                    "step_id": context.step_id,
                    **dict(context.metadata),
                },
            },
        )
        output = require_object(rendered, "logic module output")
        if self.definition.config.get("merge_input", False):
            return {**module_input, **output}
        return output
