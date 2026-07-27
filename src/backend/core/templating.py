from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from jinja2 import StrictUndefined
from jinja2.exceptions import TemplateError
from jinja2.sandbox import SandboxedEnvironment

from backend.errors import RequestValidationError


EXACT_REFERENCE = re.compile(

    r"^\s*{{\s*(?P<path>[A-Za-z_]\w*(?:\.(?:[A-Za-z_]\w*|\d+))*)\s*}}\s*$"

)


class TemplateRenderer:

    def __init__(self) -> None:

        self.environment = SandboxedEnvironment(

            undefined=StrictUndefined,

            autoescape=False,

        )

    def render_text(self, template: str, context: Mapping[str, Any]) -> str:

        try:

            return self.environment.from_string(template).render(**context)

        except TemplateError as error:

            raise RequestValidationError(

                "Template rendering failed.",

                details={"reason": str(error)},

            ) from error

    def render_value(self, value: Any, context: Mapping[str, Any]) -> Any:

        if isinstance(value, str):

            match = EXACT_REFERENCE.match(value)

            if match:

                return self._resolve(match.group("path"), context)

            return self.render_text(value, context)

        if isinstance(value, list):

            return [self.render_value(item, context) for item in value]

        if isinstance(value, dict):

            return {

                str(key): self.render_value(item, context)

                for key, item in value.items()

            }

        return value

    @staticmethod
    def _resolve(path: str, context: Mapping[str, Any]) -> Any:

        current: Any = context

        try:

            for part in path.split("."):

                if isinstance(current, Mapping):

                    current = current[part]

                elif isinstance(current, (list, tuple)) and part.isdigit():

                    current = current[int(part)]

                else:

                    current = getattr(current, part)

            return current

        except (KeyError, IndexError, AttributeError, TypeError) as error:

            raise RequestValidationError(

                "Template reference could not be resolved.",

                details={"path": path},

            ) from error
