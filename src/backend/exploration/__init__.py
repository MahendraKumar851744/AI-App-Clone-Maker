"""Ground-truth Android application exploration."""

from typing import Any

from backend.exploration.appium import AppiumExplorer, ApkInspector

__all__ = [
    "ApkInspector",
    "AppiumExplorer",
    "ExplorationService",
    "capture_first_screen",
]


def __getattr__(name: str) -> Any:
    if name in {"ExplorationService", "capture_first_screen"}:
        from backend.exploration.service import (
            ExplorationService,
            capture_first_screen,
        )

        return {
            "ExplorationService": ExplorationService,
            "capture_first_screen": capture_first_screen,
        }[name]
    raise AttributeError(name)
