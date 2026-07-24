from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from backend.apps import (
    AndroidAppManager,
    AppConflictError,
    AppValidationError,
)
from backend.exploration.models import ApkMetadata


PACKAGE = "message.chat.text.messaging.sms"


class FakeRuntimeManager:
    def status(self):
        return {
            "android": {
                "tools": {
                    "adb": {
                        "available": True,
                        "path": "adb.exe",
                    }
                }
            },
            "devices": [
                {
                    "serial": "emulator-5554",
                    "state": "device",
                    "boot_completed": True,
                    "compatible": True,
                    "abi_list": "x86,armeabi-v7a,armeabi",
                }
            ],
        }


class FakeActionManager:
    def __init__(self):
        self.closed_packages = []

    def close_package_sessions(self, package_id):
        self.closed_packages.append(package_id)
        return ["run-live"]


class FakeInspector:
    def __init__(self, metadata):
        self.metadata = metadata
        self.paths = []

    def inspect(self, path):
        self.paths.append(Path(path))
        return self.metadata


class FakeAdb:
    def __init__(self, *, installed=True):
        self.installed = installed
        self.calls = []

    def __call__(self, command, **_kwargs):
        self.calls.append(command)
        if command[-3:-1] == ["pm", "path"]:
            output = (
                f"package:/data/app/{PACKAGE}/base.apk\n"
                if self.installed
                else ""
            )
            return subprocess.CompletedProcess(command, 0, output, "")
        if command[-3:-1] == ["dumpsys", "package"]:
            output = (
                "Packages:\n"
                f"  Package [{PACKAGE}]\n"
                "    versionCode=42 minSdk=23 targetSdk=35\n"
                "    versionName=1.2.0\n"
            )
            return subprocess.CompletedProcess(command, 0, output, "")
        if "uninstall" in command:
            self.installed = False
            return subprocess.CompletedProcess(command, 0, "Success\n", "")
        if "install" in command:
            self.installed = True
            return subprocess.CompletedProcess(command, 0, "Success\n", "")
        raise AssertionError(f"Unexpected ADB command: {command}")


class AndroidAppManagerTests(unittest.TestCase):
    def manager(self, root, adb, *, package=PACKAGE):
        apk = root / "message.apk"
        apk.write_bytes(b"apk")
        metadata = ApkMetadata(
            path=apk,
            sha256="a" * 64,
            size_bytes=3,
            package=package,
            version_name="1.2.0",
            version_code="42",
            native_abis=("armeabi-v7a",),
        )
        actions = FakeActionManager()
        manager = AndroidAppManager(
            allowed_apk_roots=[root],
            runtime_manager=FakeRuntimeManager(),
            action_manager=actions,
            inspector=FakeInspector(metadata),
            runner=adb,
        )
        return manager, actions, apk

    def test_clean_install_uninstalls_closes_sessions_and_verifies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adb = FakeAdb(installed=True)
            manager, actions, apk = self.manager(root, adb)

            result = manager.install(
                {
                    "apk_path": str(apk),
                    "expected_package_id": PACKAGE,
                    "install_mode": "clean",
                }
            )

            self.assertEqual(result["status"], "installed")
            self.assertEqual(result["version_name"], "1.2.0")
            self.assertEqual(result["version_code"], "42")
            self.assertTrue(result["previous_installation"]["removed"])
            self.assertEqual(result["closed_exploration_runs"], ["run-live"])
            self.assertTrue(result["verification"]["verified"])
            self.assertEqual(actions.closed_packages, [PACKAGE])
            uninstall_index = next(
                index
                for index, command in enumerate(adb.calls)
                if "uninstall" in command
            )
            install_index = next(
                index
                for index, command in enumerate(adb.calls)
                if "install" in command
            )
            self.assertLess(uninstall_index, install_index)

    def test_replace_uses_adb_replace_without_uninstall(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adb = FakeAdb(installed=True)
            manager, actions, apk = self.manager(root, adb)

            result = manager.install(
                {
                    "apk_path": str(apk),
                    "expected_package_id": PACKAGE,
                    "install_mode": "replace",
                }
            )

            install = next(command for command in adb.calls if "install" in command)
            self.assertIn("-r", install)
            self.assertFalse(any("uninstall" in command for command in adb.calls))
            self.assertEqual(actions.closed_packages, [PACKAGE])
            self.assertFalse(result["previous_installation"]["removed"])

    def test_preserve_refuses_existing_package_without_closing_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adb = FakeAdb(installed=True)
            manager, actions, apk = self.manager(root, adb)

            with self.assertRaises(AppConflictError):
                manager.install(
                    {
                        "apk_path": str(apk),
                        "expected_package_id": PACKAGE,
                        "install_mode": "preserve",
                    }
                )

            self.assertEqual(actions.closed_packages, [])
            self.assertFalse(any("install" in command for command in adb.calls))

    def test_package_identity_mismatch_is_rejected_before_adb(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adb = FakeAdb(installed=False)
            manager, _actions, apk = self.manager(
                root,
                adb,
                package="different.application",
            )

            with self.assertRaises(AppConflictError):
                manager.install(
                    {
                        "apk_path": str(apk),
                        "expected_package_id": PACKAGE,
                        "install_mode": "clean",
                    }
                )

            self.assertEqual(adb.calls, [])

    def test_apk_path_outside_allow_list_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            allowed = root / "allowed"
            outside = root / "outside.apk"
            allowed.mkdir()
            outside.write_bytes(b"apk")
            manager, _actions, _apk = self.manager(allowed, FakeAdb())

            with self.assertRaises(AppValidationError):
                manager.install(
                    {
                        "apk_path": str(outside),
                        "expected_package_id": PACKAGE,
                        "install_mode": "clean",
                    }
                )

    def test_uninstall_requires_confirmation_and_verifies_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adb = FakeAdb(installed=True)
            manager, actions, _apk = self.manager(root, adb)

            with self.assertRaises(AppValidationError):
                manager.uninstall(PACKAGE, {})
            result = manager.uninstall(PACKAGE, {"confirm": True})

            self.assertEqual(result["status"], "uninstalled")
            self.assertTrue(result["verification"]["verified"])
            self.assertEqual(actions.closed_packages, [PACKAGE])


if __name__ == "__main__":
    unittest.main()
