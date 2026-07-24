from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from backend.core.contracts import JsonObject
from backend.exploration.actions import ExplorationActionManager
from backend.exploration.appium import ApkInspector
from backend.exploration.models import ApkMetadata
from backend.runtime import RuntimeManager


PACKAGE_ID_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$"
)
INSTALL_MODES = {"clean", "replace", "preserve"}


class AppManagementError(RuntimeError):
    pass


class AppValidationError(AppManagementError):
    pass


class AppNotFoundError(AppManagementError):
    pass


class AppConflictError(AppManagementError):
    pass


class AppOperationError(AppManagementError):
    def __init__(self, message: str, *, details: JsonObject | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class AndroidAppManager:
    """Install and inspect APKs through a constrained ADB boundary."""

    def __init__(
        self,
        *,
        allowed_apk_roots: list[str | Path],
        runtime_manager: RuntimeManager,
        action_manager: ExplorationActionManager,
        inspector: ApkInspector | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.allowed_apk_roots = [
            Path(root).expanduser().resolve() for root in allowed_apk_roots
        ]
        self.runtime_manager = runtime_manager
        self.action_manager = action_manager
        self.inspector = inspector or ApkInspector()
        self.runner = runner

    def install(self, payload: JsonObject) -> JsonObject:
        request = self._install_request(payload)
        apk_path = self._resolve_apk(request["apk_path"])
        try:
            metadata = self.inspector.inspect(apk_path)
        except Exception as error:
            raise AppOperationError(f"APK inspection failed: {error}") from error
        expected_package = request["expected_package_id"]
        if not metadata.package:
            raise AppOperationError(
                "APK package identity could not be determined. Ensure Android "
                "build-tools/aapt is available."
            )
        if metadata.package != expected_package:
            raise AppConflictError(
                f"APK package '{metadata.package}' does not match expected "
                f"package '{expected_package}'."
            )

        runtime = self.runtime_manager.status()
        adb, device = self._select_device(runtime, request.get("device_id"))
        self._validate_abi(metadata, device)
        started = time.monotonic()
        previous = self._package_status(
            adb,
            device["serial"],
            expected_package,
            missing_ok=True,
        )
        mode = request["install_mode"]
        if mode == "preserve" and previous["installed"]:
            raise AppConflictError(
                f"Package '{expected_package}' is already installed; "
                "preserve mode refuses to replace it."
            )

        closed_runs: list[str] = []
        uninstall_result: JsonObject | None = None
        if previous["installed"] and mode in {"clean", "replace"}:
            closed_runs = self.action_manager.close_package_sessions(
                expected_package
            )
        if previous["installed"] and mode == "clean":
            uninstall_result = self._adb(
                adb,
                device["serial"],
                ["uninstall", expected_package],
                timeout=120,
                operation="uninstall existing package",
            )

        install_arguments = ["install"]
        if mode == "replace":
            install_arguments.append("-r")
        install_arguments.append(str(apk_path))
        try:
            install_result = self._adb(
                adb,
                device["serial"],
                install_arguments,
                timeout=240,
                operation="install APK",
            )
        except AppOperationError as error:
            error.details.update(
                {
                    "package_id": expected_package,
                    "install_mode": mode,
                    "previous_installation_removed": bool(uninstall_result),
                    "closed_exploration_runs": closed_runs,
                }
            )
            raise

        installed = self._package_status(
            adb,
            device["serial"],
            expected_package,
            missing_ok=False,
        )
        verification = self._verification(metadata, installed)
        if not verification["verified"]:
            raise AppOperationError(
                "APK installation completed but package verification failed.",
                details={"verification": verification, "installed": installed},
            )
        return {
            "contract": "appium.app_install_result",
            "schema_version": 1,
            "status": "installed",
            "package_id": expected_package,
            "version_name": installed.get("version_name"),
            "version_code": installed.get("version_code"),
            "device_id": device["serial"],
            "install_mode": mode,
            "apk": metadata.to_dict(),
            "previous_installation": {
                **previous,
                "removed": bool(uninstall_result),
            },
            "closed_exploration_runs": closed_runs,
            "commands": {
                "uninstall": uninstall_result,
                "install": install_result,
            },
            "verification": verification,
            "duration_ms": round((time.monotonic() - started) * 1000),
        }

    def get(self, package_id: str, *, device_id: str | None = None) -> JsonObject:
        package = self._package_id(package_id)
        runtime = self.runtime_manager.status()
        adb, device = self._select_device(runtime, device_id)
        result = self._package_status(
            adb,
            device["serial"],
            package,
            missing_ok=False,
        )
        return {
            "contract": "appium.installed_app",
            "schema_version": 1,
            "device_id": device["serial"],
            **result,
        }

    def uninstall(self, package_id: str, payload: JsonObject) -> JsonObject:
        package = self._package_id(package_id)
        if not isinstance(payload, dict):
            raise AppValidationError("Request body must be a JSON object.")
        unexpected = sorted(set(payload) - {"confirm", "device_id"})
        if unexpected:
            raise AppValidationError(
                f"Unsupported uninstall fields: {', '.join(unexpected)}."
            )
        if payload.get("confirm") is not True:
            raise AppValidationError(
                "Uninstall requires explicit 'confirm': true."
            )
        device_id = payload.get("device_id")
        if device_id is not None and (
            not isinstance(device_id, str) or not device_id.strip()
        ):
            raise AppValidationError("'device_id' must be a non-empty string.")
        runtime = self.runtime_manager.status()
        adb, device = self._select_device(runtime, device_id)
        previous = self._package_status(
            adb,
            device["serial"],
            package,
            missing_ok=False,
        )
        closed_runs = self.action_manager.close_package_sessions(package)
        command = self._adb(
            adb,
            device["serial"],
            ["uninstall", package],
            timeout=120,
            operation="uninstall package",
        )
        remaining = self._package_status(
            adb,
            device["serial"],
            package,
            missing_ok=True,
        )
        if remaining["installed"]:
            raise AppOperationError(
                f"Package '{package}' still exists after uninstall.",
                details={"package": remaining},
            )
        return {
            "contract": "appium.app_uninstall_result",
            "schema_version": 1,
            "status": "uninstalled",
            "package_id": package,
            "device_id": device["serial"],
            "previous_installation": previous,
            "closed_exploration_runs": closed_runs,
            "command": command,
            "verification": {"installed": False, "verified": True},
        }

    def _resolve_apk(self, value: str) -> Path:
        path = Path(value).expanduser().resolve()
        if not any(self._within(path, root) for root in self.allowed_apk_roots):
            roots = ", ".join(str(root) for root in self.allowed_apk_roots)
            raise AppValidationError(
                f"'apk_path' must be inside an approved APK root: {roots}."
            )
        if not path.is_file():
            raise AppNotFoundError(f"APK does not exist: {path}")
        if path.suffix.lower() != ".apk":
            raise AppValidationError("'apk_path' must reference an .apk file.")
        return path

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _select_device(
        runtime: JsonObject,
        requested: str | None,
    ) -> tuple[str, JsonObject]:
        adb_status = (
            runtime.get("android", {}).get("tools", {}).get("adb", {})
        )
        adb = adb_status.get("path") if adb_status.get("available") else None
        if not adb:
            raise AppConflictError(
                "ADB is unavailable. Provision and start the runtime first."
            )
        devices = list(runtime.get("devices") or [])
        if requested:
            matches = [item for item in devices if item.get("serial") == requested]
            if not matches:
                raise AppNotFoundError(
                    f"Android device '{requested}' is not connected."
                )
            device = matches[0]
        else:
            device = next(
                (
                    item
                    for item in devices
                    if item.get("state") == "device"
                    and item.get("boot_completed")
                    and item.get("compatible")
                ),
                None,
            )
            if device is None:
                raise AppConflictError(
                    "No booted compatible Android device is available. "
                    "Start the runtime first."
                )
        if (
            device.get("state") != "device"
            or not device.get("boot_completed")
            or not device.get("compatible")
        ):
            raise AppConflictError(
                f"Android device '{device.get('serial')}' is not ready and "
                "compatible with the pinned runtime profile."
            )
        return str(adb), device

    @staticmethod
    def _validate_abi(metadata: ApkMetadata, device: JsonObject) -> None:
        if not metadata.native_abis:
            return
        device_abis = {
            item.strip()
            for item in str(device.get("abi_list") or "").split(",")
            if item.strip()
        }
        if not device_abis.intersection(metadata.native_abis):
            raise AppConflictError(
                "APK native ABIs are incompatible with the selected device: "
                f"APK={list(metadata.native_abis)}, "
                f"device={sorted(device_abis)}."
            )

    def _package_status(
        self,
        adb: str,
        device_id: str,
        package_id: str,
        *,
        missing_ok: bool,
    ) -> JsonObject:
        path_result = self._run(
            [adb, "-s", device_id, "shell", "pm", "path", package_id],
            timeout=30,
        )
        paths = [
            line.partition(":")[2].strip()
            for line in (path_result.stdout or "").splitlines()
            if line.startswith("package:")
        ]
        if path_result.returncode != 0 or not paths:
            if missing_ok:
                return {
                    "installed": False,
                    "package_id": package_id,
                    "version_name": None,
                    "version_code": None,
                    "paths": [],
                }
            raise AppNotFoundError(
                f"Package '{package_id}' is not installed on device "
                f"'{device_id}'."
            )
        dump = self._run(
            [adb, "-s", device_id, "shell", "dumpsys", "package", package_id],
            timeout=30,
        )
        if dump.returncode != 0:
            raise AppOperationError(
                f"Could not inspect installed package '{package_id}'.",
                details=self._command_details(dump),
            )
        output = dump.stdout or ""
        version_name = self._match(output, r"(?m)^\s*versionName=([^\r\n]+)")
        version_code = self._match(output, r"(?m)^\s*versionCode=(\d+)")
        return {
            "installed": True,
            "package_id": package_id,
            "version_name": version_name,
            "version_code": version_code,
            "paths": paths,
        }

    def _adb(
        self,
        adb: str,
        device_id: str,
        arguments: list[str],
        *,
        timeout: int,
        operation: str,
    ) -> JsonObject:
        started = time.monotonic()
        completed = self._run(
            [adb, "-s", device_id, *arguments],
            timeout=timeout,
        )
        details = {
            **self._command_details(completed),
            "duration_ms": round((time.monotonic() - started) * 1000),
        }
        output = f"{completed.stdout or ''}\n{completed.stderr or ''}"
        if completed.returncode != 0 or "Failure [" in output:
            raise AppOperationError(
                f"ADB could not {operation}.",
                details=details,
            )
        return {"status": "succeeded", **details}

    def _run(
        self,
        command: list[str],
        *,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return self.runner(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AppOperationError(
                f"ADB command could not complete: {error}"
            ) from error

    @staticmethod
    def _command_details(
        completed: subprocess.CompletedProcess[str],
    ) -> JsonObject:
        return {
            "exit_code": completed.returncode,
            "stdout": (completed.stdout or "").strip()[-4000:],
            "stderr": (completed.stderr or "").strip()[-4000:],
        }

    @staticmethod
    def _verification(
        metadata: ApkMetadata,
        installed: JsonObject,
    ) -> JsonObject:
        package_matches = metadata.package == installed.get("package_id")
        version_name_matches = (
            metadata.version_name is None
            or metadata.version_name == installed.get("version_name")
        )
        version_code_matches = (
            metadata.version_code is None
            or metadata.version_code == installed.get("version_code")
        )
        return {
            "installed": installed.get("installed") is True,
            "package_matches": package_matches,
            "version_name_matches": version_name_matches,
            "version_code_matches": version_code_matches,
            "verified": bool(
                installed.get("installed")
                and package_matches
                and version_name_matches
                and version_code_matches
            ),
        }

    @staticmethod
    def _match(value: str, pattern: str) -> str | None:
        match = re.search(pattern, value)
        return match.group(1).strip() if match else None

    @staticmethod
    def _install_request(payload: JsonObject) -> JsonObject:
        if not isinstance(payload, dict):
            raise AppValidationError("Request body must be a JSON object.")
        required = {"apk_path", "expected_package_id", "install_mode"}
        allowed = {*required, "device_id"}
        missing = sorted(required - set(payload))
        unexpected = sorted(set(payload) - allowed)
        if missing:
            raise AppValidationError(
                f"Missing required install fields: {', '.join(missing)}."
            )
        if unexpected:
            raise AppValidationError(
                f"Unsupported install fields: {', '.join(unexpected)}."
            )
        apk_path = payload.get("apk_path")
        if not isinstance(apk_path, str) or not apk_path.strip():
            raise AppValidationError("'apk_path' must be a non-empty string.")
        package = AndroidAppManager._package_id(
            payload.get("expected_package_id")
        )
        mode = payload.get("install_mode")
        if mode not in INSTALL_MODES:
            raise AppValidationError(
                "'install_mode' must be one of: clean, preserve, replace."
            )
        device_id = payload.get("device_id")
        if device_id is not None and (
            not isinstance(device_id, str) or not device_id.strip()
        ):
            raise AppValidationError("'device_id' must be a non-empty string.")
        return {
            "apk_path": apk_path,
            "expected_package_id": package,
            "install_mode": mode,
            "device_id": device_id,
        }

    @staticmethod
    def _package_id(value: Any) -> str:
        if not isinstance(value, str) or not PACKAGE_ID_PATTERN.fullmatch(value):
            raise AppValidationError(
                "Package ID must be a valid Android application ID."
            )
        return value
