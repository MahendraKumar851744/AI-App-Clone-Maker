"""Built-in module implementations."""

from backend.modules.automation import AutomationModule
from backend.modules.http import HTTPModule
from backend.modules.llm import LLMModule
from backend.modules.logic import LogicModule

__all__ = ["AutomationModule", "HTTPModule", "LLMModule", "LogicModule"]
