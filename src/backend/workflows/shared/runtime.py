from __future__ import annotations

from typing import Any, Callable

from backend.workflows.shared.client import AppiumHTTPClient

JsonObject = dict[str, Any]

Operation = Callable[[], Any]

Summarizer = Callable[[Any], JsonObject]

StepExecutor = Callable[[str, Operation, Summarizer], Any]


class AppiumRuntimeInitializer:

    """Shared idempotent Appium provisioning and startup."""

    def __init__(

        self,

        client: AppiumHTTPClient,

        *,

        execute_step: StepExecutor | None = None,

    ) -> None:

        self.client = client

        self.execute_step = execute_step or self._execute_directly

    def run(

        self,

        *,

        provision_options: JsonObject | None = None,

        job_timeout_seconds: float = 3600,

    ) -> JsonObject:

        if not 1 <= job_timeout_seconds <= 7200:

            raise ValueError("'job_timeout_seconds' must be between 1 and 7200.")

        initial = self._step(

            "runtime_status",

            self.client.runtime_status,

            self._runtime_summary,

        )

        provision_status = "reused"

        start_status = "reused"

        if not initial.get("provisioned"):

            submission = self._step(

                "provision_runtime",

                lambda: self.client.provision_runtime(

                    {

                        **dict(provision_options or {}),

                        "force": False,
                    }

                ),

                self._submission_summary,

            )

            self._wait_for_submission(

                submission,

                operation="provision",

                step_name="wait_for_provisioning",

                timeout_seconds=job_timeout_seconds,

            )

            provision_status = (

                "joined_active_job"

                if submission.get("reused")

                else "completed"

            )

        after_provision = self._step(

            "runtime_status_after_provisioning",

            self.client.runtime_status,

            self._runtime_summary,
        )


        if not after_provision.get("provisioned"):

            raise RuntimeError("Appium runtime provisioning did not complete.")

        if not after_provision.get("ready"):

            submission = self._step(

                "start_runtime",

                self.client.start_runtime,

                self._submission_summary,

            )

            self._wait_for_submission(

                submission,

                operation="start",

                step_name="wait_for_runtime_start",

                timeout_seconds=job_timeout_seconds,

            )

            start_status = (

                "joined_active_job"

                if submission.get("reused")

                else "completed"

            )

        final = self._step(

            "verify_runtime_ready",

            self.client.runtime_status,

            self._runtime_summary,

        )

        if not final.get("ready"):

            raise RuntimeError("Appium runtime did not become ready.")

        return {

            "provisioning": provision_status,

            "startup": start_status,

            "ready": True,

            "runtime": final,

        }

    def _wait_for_submission(

        self,

        submission: JsonObject,

        *,

        operation: str,

        step_name: str,

        timeout_seconds: float,

    ) -> None:

        job = submission.get("job")

        if not isinstance(job, dict):

            raise RuntimeError(

                f"Appium runtime {operation} did not return a job."

            )

        job_id = job.get("job_id")

        if not isinstance(job_id, str) or not job_id:

            raise RuntimeError(

                f"Appium runtime {operation} job is missing 'job_id'."

            )

        self._step(

            step_name,

            lambda: self.client.wait_for_job(

                job_id,

                timeout_seconds=timeout_seconds,

            ),

            lambda result: {

                "job_id": result.get("job_id"),

                "status": result.get("status"),

            },

        )

    def _step(

        self,

        name: str,

        operation: Operation,

        summarize: Summarizer,

    ) -> Any:

        return self.execute_step(name, operation, summarize)

    @staticmethod
    def _execute_directly(

        _name: str,

        operation: Operation,

        _summarize: Summarizer,

    ) -> Any:

        return operation()

    @staticmethod
    def _runtime_summary(result: JsonObject) -> JsonObject:

        return {

            "provisioned": result.get("provisioned"),

            "ready": result.get("ready"),

        }

    @staticmethod
    def _submission_summary(result: JsonObject) -> JsonObject:

        return {

            "job_id": result.get("job", {}).get("job_id"),

            "reused": result.get("reused"),

        }
