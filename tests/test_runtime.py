from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from backend.runtime import RuntimeManager


def wait_for_job(manager: RuntimeManager, job_id: str) -> dict:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = manager.get_job(job_id)
        if job["status"] in {"succeeded", "failed"}:
            return job
        time.sleep(0.01)
    raise AssertionError("Runtime job did not reach a terminal state.")


class RuntimeManagerTests(unittest.TestCase):
    def test_provision_runs_setup_and_doctor_and_persists_success(self):
        with tempfile.TemporaryDirectory() as directory:
            state = {
                "provisioned": False,
                "android": {"ready": True},
            }
            commands = []

            def runner(command, _cwd, output):
                commands.append(command)
                output(f"completed {command[0]}")
                if command[0] == "doctor.ps1":
                    state["provisioned"] = True
                return 0

            manager = RuntimeManager(
                directory,
                command_runner=runner,
                status_provider=lambda: dict(state),
            )
            manager._powershell_script = lambda name: [name]

            submitted, reused = manager.provision({})
            completed = wait_for_job(manager, submitted["job_id"])

            self.assertFalse(reused)
            self.assertEqual(completed["status"], "succeeded")
            self.assertEqual(completed["progress"], 100)
            self.assertEqual(
                commands,
                [["setup.ps1"], ["doctor.ps1"]],
            )
            self.assertTrue(
                (
                    Path(directory)
                    / ".runtime"
                    / "jobs"
                    / f"{submitted['job_id']}.json"
                ).is_file()
            )

    def test_active_provision_job_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            entered = threading.Event()
            release = threading.Event()

            def runner(_command, _cwd, _output):
                entered.set()
                release.wait(timeout=2)
                return 1

            manager = RuntimeManager(
                directory,
                command_runner=runner,
                status_provider=lambda: {
                    "provisioned": True,
                    "android": {"ready": True},
                },
            )
            manager._powershell_script = lambda name: [name]

            first, first_reused = manager.provision({"force": True})
            self.assertTrue(entered.wait(timeout=1))
            second, second_reused = manager.provision({"force": True})
            release.set()
            wait_for_job(manager, first["job_id"])

            self.assertFalse(first_reused)
            self.assertTrue(second_reused)
            self.assertEqual(first["job_id"], second["job_id"])

    def test_full_provision_refuses_implicit_android_license_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeManager(
                directory,
                command_runner=lambda *_args: 0,
                status_provider=lambda: {
                    "provisioned": False,
                    "android": {"ready": False},
                },
            )
            manager._powershell_script = lambda name: [name]

            submitted, _ = manager.provision({})
            completed = wait_for_job(manager, submitted["job_id"])

            self.assertEqual(completed["status"], "failed")
            self.assertIn(
                "licenses must be accepted explicitly",
                completed["error"],
            )

    def test_start_job_requires_provisioned_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RuntimeManager(
                directory,
                status_provider=lambda: {
                    "provisioned": False,
                    "ready": False,
                },
            )

            submitted, reused = manager.start({})
            completed = wait_for_job(manager, submitted["job_id"])

            self.assertFalse(reused)
            self.assertEqual(completed["status"], "failed")
            self.assertIn("Complete provisioning first", completed["error"])


if __name__ == "__main__":
    unittest.main()
