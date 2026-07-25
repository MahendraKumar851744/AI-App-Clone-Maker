from __future__ import annotations

import unittest
from pathlib import Path

from backend.workflows.registry import WORKFLOWS, get_workflow
from backend.workflows.shared.config import PROJECT_ROOT


class WorkflowRegistryTests(unittest.TestCase):
    def test_registered_workflows_have_runners_and_default_configs(self):
        self.assertEqual(
            set(WORKFLOWS),
            {"initialize_appium", "simple_exploration"},
        )
        for definition in WORKFLOWS.values():
            self.assertTrue(callable(definition.load_runner()))
            self.assertTrue(
                (PROJECT_ROOT / Path(definition.default_config)).is_file()
            )

    def test_unknown_workflow_lists_available_names(self):
        with self.assertRaisesRegex(
            ValueError,
            "initialize_appium, simple_exploration",
        ):
            get_workflow("missing")


if __name__ == "__main__":
    unittest.main()
