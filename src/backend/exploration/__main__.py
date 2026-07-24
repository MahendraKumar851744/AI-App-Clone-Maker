from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.exploration.service import capture_first_screen


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT = PROJECT_ROOT / "artifacts" / "explorations"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Launch an APK through Appium and persist one complete ground-truth "
            "screen observation."
        )
    )
    parser.add_argument("apk", type=Path, help="Path to the APK to inspect")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Root folder for exploration runs",
    )
    parser.add_argument("--server", default="http://127.0.0.1:4723")
    parser.add_argument("--device-name", default="Android")
    parser.add_argument("--udid", help="ADB device serial")
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help="Preserve existing application data",
    )
    parser.add_argument(
        "--stability-timeout",
        type=float,
        default=15.0,
        help="Maximum seconds to wait for a stable hierarchy",
    )
    return parser.parse_args()


def main() -> int:
    args = arguments()
    try:
        result = capture_first_screen(
            args.apk,
            output_root=args.output_root,
            server_url=args.server,
            device_name=args.device_name,
            udid=args.udid,
            keep_data=args.keep_data,
            stability_timeout=args.stability_timeout,
        )
        print(json.dumps(result.to_dict(), indent=2))
        return 0
    except Exception as error:
        print(f"Ground-truth capture failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
