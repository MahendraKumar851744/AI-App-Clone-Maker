from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.workflows.registry import WORKFLOWS, get_workflow
from backend.workflows.shared.config import load_workflow_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Run registered workflows.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="List available workflows.")

    run_parser = subparsers.add_parser("run", help="Run one workflow.")
    run_parser.add_argument("workflow", choices=sorted(WORKFLOWS))
    run_parser.add_argument(
        "--config",
        type=Path,
        help="Override the workflow's default configuration.",
    )
    args = parser.parse_args()

    if args.command == "list":
        result = [
            {
                "name": definition.name,
                "description": definition.description,
                "default_config": definition.default_config,
            }
            for definition in WORKFLOWS.values()
        ]
        print(json.dumps(result, indent=2))
        return

    definition = get_workflow(args.workflow)
    config_path = args.config or Path(definition.default_config)
    result = definition.load_runner()(load_workflow_config(config_path))
    viewer_url = result.get("viewer_url")
    if isinstance(viewer_url, str) and viewer_url:
        print(f"Workflow completed.\nViewer: {viewer_url}")
        return
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
