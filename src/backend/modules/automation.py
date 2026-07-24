from __future__ import annotations

from typing import Protocol

from backend.core.contracts import (
    BaseModule,
    ExecutionContext,
    JsonObject,
    ModuleDefinition,
)
from backend.errors import AutomationContractPendingError


class AutomationService(Protocol):
    def execute(self, module_input: JsonObject, context: ExecutionContext) -> JsonObject:
        """Future automation service boundary."""


class AutomationModule(BaseModule):
    type_name = "automation"
    type_description = (
        "Reserved extension point for the automation service contract."
    )
    type_status = "contract_pending"

    def __init__(
        self,
        definition: ModuleDefinition,
        service: AutomationService | None = None,
    ) -> None:
        super().__init__(definition)
        self.service = service

    def execute(self, module_input: JsonObject, context: ExecutionContext) -> JsonObject:
        if self.service is None:
            raise AutomationContractPendingError(
                "The automation module contract has not been defined yet."
            )
        return self.service.execute(module_input, context)
