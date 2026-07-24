from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping

from backend.errors import RequestValidationError


JsonObject = dict[str, Any]


@dataclass(frozen=True)
class ExecutionContext:
    run_id: str
    step_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModuleDefinition:
    module_id: str
    module_type: str
    name: str
    config: JsonObject

    def to_dict(self) -> JsonObject:
        return {
            "id": self.module_id,
            "type": self.module_type,
            "name": self.name,
            "config": self.config,
        }


class BaseModule(ABC):
    type_name = "base"
    type_description = "Base module"
    type_status = "available"

    def __init__(self, definition: ModuleDefinition) -> None:
        self.definition = definition

    @abstractmethod
    def execute(self, module_input: JsonObject, context: ExecutionContext) -> JsonObject:
        """Receive a JSON object and return a JSON object."""

    def describe(self) -> JsonObject:
        return self.definition.to_dict()

    @classmethod
    def describe_type(cls) -> JsonObject:
        return {
            "type": cls.type_name,
            "description": cls.type_description,
            "status": cls.type_status,
        }


def require_object(value: Any, field_name: str) -> JsonObject:
    if not isinstance(value, dict):
        raise RequestValidationError(f"'{field_name}' must be a JSON object.")
    return value


def require_string(
    value: Any,
    field_name: str,
    *,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise RequestValidationError(f"'{field_name}' must be a string.")
    if not allow_empty and not value.strip():
        raise RequestValidationError(f"'{field_name}' cannot be empty.")
    return value
