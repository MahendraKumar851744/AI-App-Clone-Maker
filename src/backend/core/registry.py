from __future__ import annotations

import re
from collections.abc import Callable
from threading import RLock
from typing import Any

from backend.core.contracts import (
    BaseModule,
    JsonObject,
    ModuleDefinition,
    require_object,
    require_string,
)
from backend.errors import (
    RequestValidationError,
    ResourceConflictError,
    ResourceNotFoundError,
)


ModuleFactory = Callable[[ModuleDefinition], BaseModule]
MODULE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class ModuleRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, ModuleFactory] = {}
        self._type_descriptions: dict[str, JsonObject] = {}
        self._modules: dict[str, BaseModule] = {}
        self._lock = RLock()

    def register_type(
        self,
        module_class: type[BaseModule],
        factory: ModuleFactory | None = None,
    ) -> None:
        type_name = module_class.type_name
        with self._lock:
            if type_name in self._factories:
                raise ResourceConflictError(f"Module type '{type_name}' is already registered.")
            self._factories[type_name] = factory or module_class
            self._type_descriptions[type_name] = module_class.describe_type()

    def create(self, payload: JsonObject) -> BaseModule:
        module_id = require_string(payload.get("id"), "id")
        if not MODULE_ID_PATTERN.fullmatch(module_id):
            raise RequestValidationError(
                "'id' must start with a lowercase letter and contain only "
                "lowercase letters, numbers, hyphens, or underscores (max 64)."
            )
        module_type = require_string(payload.get("type"), "type")
        name = require_string(payload.get("name", module_id), "name")
        config = require_object(payload.get("config", {}), "config")

        with self._lock:
            if module_id in self._modules:
                raise ResourceConflictError(f"Module '{module_id}' already exists.")
            factory = self._factories.get(module_type)
            if factory is None:
                raise RequestValidationError(
                    f"Unknown module type '{module_type}'.",
                    details={"available_types": sorted(self._factories)},
                )
            definition = ModuleDefinition(
                module_id=module_id,
                module_type=module_type,
                name=name,
                config=dict(config),
            )
            module = factory(definition)
            self._modules[module_id] = module
            return module

    def get(self, module_id: str) -> BaseModule:
        with self._lock:
            module = self._modules.get(module_id)
        if module is None:
            raise ResourceNotFoundError(f"Module '{module_id}' was not found.")
        return module

    def delete(self, module_id: str) -> None:
        with self._lock:
            if module_id not in self._modules:
                raise ResourceNotFoundError(f"Module '{module_id}' was not found.")
            del self._modules[module_id]

    def list_modules(self) -> list[JsonObject]:
        with self._lock:
            return [
                self._modules[module_id].describe()
                for module_id in sorted(self._modules)
            ]

    def list_types(self) -> list[JsonObject]:
        with self._lock:
            return [
                dict(self._type_descriptions[type_name])
                for type_name in sorted(self._type_descriptions)
            ]
