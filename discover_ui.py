from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ui_discovery.parser import parse_hierarchy, summarize
from ui_discovery.report import write_csv, write_html, write_json


ROOT = Path(__file__).resolve().parent
DEFAULT_APK = ROOT / "_Message_1.39_APKPure.apk"
DEFAULT_OUTPUT = ROOT / "artifacts" / "first_open"
APP_PACKAGE = "message.chat.text.messaging.sms"
APP_ACTIVITY = "message.chat.text.messaging.sms.launcher.activities.SetDefaultLauncherActivity"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch the APK with Appium and extract the current Android UI."
    )
    parser.add_argument("--apk", type=Path, default=DEFAULT_APK)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--server", default="http://127.0.0.1:4723")
    parser.add_argument("--device-name", default="Android")
    parser.add_argument("--udid", help="ADB device serial when more than one device is attached")
    parser.add_argument(
        "--wait",
        type=float,
        default=3.0,
        help="Seconds to wait after Appium opens the first screen",
    )
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help="Do not clear application data before launch",
    )
    return parser.parse_args()


def build_driver(args: argparse.Namespace):
    try:
        from appium import webdriver
        from appium.options.android import UiAutomator2Options
    except ImportError as error:
        raise RuntimeError(
            "The Appium Python client is missing. Run .\\scripts\\setup.ps1 first."
        ) from error

    apk = args.apk.resolve()
    if not apk.is_file():
        raise FileNotFoundError(f"APK not found: {apk}")

    capabilities: dict[str, Any] = {
        "platformName": "Android",
        "appium:automationName": "UiAutomator2",
        "appium:deviceName": args.device_name,
        "appium:app": str(apk),
        "appium:appPackage": APP_PACKAGE,
        "appium:appActivity": APP_ACTIVITY,
        "appium:autoGrantPermissions": False,
        # noReset=false clears application data for a first-open state. Keeping
        # fullReset=false prevents Appium from uninstalling the APK on session end.
        "appium:noReset": args.keep_data,
        "appium:fullReset": False,
        "appium:newCommandTimeout": 180,
        "appium:disableWindowAnimation": True,
        # ARM translation and first-boot package optimization can be slow on x86 AVDs.
        "appium:adbExecTimeout": 120_000,
        "appium:androidInstallTimeout": 180_000,
        "appium:uiautomator2ServerInstallTimeout": 120_000,
        "appium:uiautomator2ServerLaunchTimeout": 120_000,
        "appium:appWaitDuration": 120_000,
        "appium:appWaitActivity": "*",
    }
    if args.udid:
        capabilities["appium:udid"] = args.udid
    options = UiAutomator2Options().load_capabilities(capabilities)
    return webdriver.Remote(command_executor=args.server, options=options)


def safe_value(function, fallback: Any = None) -> Any:
    try:
        return function()
    except Exception as error:  # Metadata should not prevent the hierarchy capture.
        return fallback if fallback is not None else f"Unavailable: {error}"


def capture(driver, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    source = driver.page_source
    records = parse_hierarchy(source)
    summary = summarize(records)

    (output / "hierarchy.xml").write_text(source, encoding="utf-8")
    driver.save_screenshot(str(output / "screen.png"))
    write_csv(output / "elements.csv", records)
    write_json(output / "elements.json", records)

    metadata = {
        "captured_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "session_id": driver.session_id,
        "current_package": safe_value(lambda: driver.current_package),
        "current_activity": safe_value(lambda: driver.current_activity),
        "contexts": safe_value(lambda: list(driver.contexts), []),
        "orientation": safe_value(lambda: driver.orientation),
        "window_size": safe_value(lambda: driver.get_window_size(), {}),
        "capabilities": dict(driver.capabilities),
    }
    write_json(output / "metadata.json", metadata)
    write_json(output / "summary.json", summary)
    write_html(output / "report.html", metadata, summary, records)
    return {"output": str(output), **summary}


def main() -> int:
    args = arguments()
    driver = None
    try:
        driver = build_driver(args)
        time.sleep(max(0, args.wait))
        result = capture(driver, args.output.resolve())
        print(json.dumps(result, indent=2))
        return 0
    except Exception as error:
        print(f"UI discovery failed: {error}", file=sys.stderr)
        return 1
    finally:
        if driver is not None:
            driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
