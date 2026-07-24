from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

import requests


JsonObject = dict[str, Any]


class WorkflowHTTPError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.details = details


class AppiumHTTPClient:
    """Small client for the three Appium HTTP operations used by workflows."""

    def __init__(
        self,
        base_url: str,
        *,
        session: requests.Session | None = None,
        timeout_seconds: float = 90,
        headers: dict[str, str] | None = None,
    ) -> None:
        base_url = base_url.rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("'base_url' must be an HTTP or HTTPS URL.")
        if not 0 < timeout_seconds <= 300:
            raise ValueError("'timeout_seconds' must be between 0 and 300.")
        self.base_url = base_url
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self.headers = dict(headers or {})

    def runtime_status(self) -> JsonObject:
        return self._request(
            "GET",
            "/api/v1/admin/runtime/status",
            {},
            result_key="runtime",
        )

    def provision_runtime(self, options: JsonObject | None = None) -> JsonObject:
        return self._request_envelope(
            "POST",
            "/api/v1/admin/runtime/provision",
            dict(options or {}),
        )

    def start_runtime(self) -> JsonObject:
        return self._request_envelope(
            "POST",
            "/api/v1/admin/runtime/start",
            {},
        )

    def wait_for_job(
        self,
        job_id: str,
        *,
        timeout_seconds: float = 3600,
        poll_interval_seconds: float = 1,
    ) -> JsonObject:
        deadline = time.monotonic() + timeout_seconds
        while True:
            job = self._request(
                "GET",
                f"/api/v1/admin/jobs/{job_id}",
                {},
                result_key="job",
            )
            if job.get("status") == "succeeded":
                return job
            if job.get("status") == "failed":
                raise WorkflowHTTPError(
                    "A runtime initialization job failed.",
                    details=job,
                )
            if time.monotonic() >= deadline:
                raise WorkflowHTTPError(
                    "Timed out waiting for runtime initialization.",
                    details={"job_id": job_id, "last_job": job},
                )
            time.sleep(poll_interval_seconds)

    def installed_app(
        self,
        package_id: str,
        *,
        device_id: str | None = None,
    ) -> JsonObject | None:
        query = f"?device_id={device_id}" if device_id else ""
        try:
            return self._request(
                "GET",
                f"/api/v1/apps/{package_id}{query}",
                {},
                result_key="app",
            )
        except WorkflowHTTPError as error:
            if error.status_code == 404:
                return None
            raise

    def install_app(
        self,
        *,
        apk_path: str,
        expected_package_id: str,
        install_mode: str,
        device_id: str | None = None,
    ) -> JsonObject:
        payload = {
            "apk_path": apk_path,
            "expected_package_id": expected_package_id,
            "install_mode": install_mode,
        }
        if device_id:
            payload["device_id"] = device_id
        return self._request(
            "POST",
            "/api/v1/apps/install",
            payload,
            result_key="result",
        )

    def open_application(self, package_id: str) -> JsonObject:
        return self._request(
            "POST",
            "/api/v1/explorations/open",
            {"package_id": package_id},
            result_key="result",
        )

    def screen_context(
        self,
        screen_ref: JsonObject,
        options: JsonObject | None = None,
    ) -> JsonObject:
        return self._request(
            "POST",
            "/api/v1/explorations/context",
            {
                "screen_ref": screen_ref,
                "options": dict(options or {}),
            },
            result_key="context",
        )

    def perform_action(
        self,
        run_id: str,
        action_request: JsonObject,
    ) -> JsonObject:
        return self._request(
            "POST",
            f"/api/v1/explorations/{run_id}/actions",
            action_request,
            result_key="result",
        )

    def _request(
        self,
        method: str,
        path: str,
        payload: JsonObject,
        *,
        result_key: str,
    ) -> JsonObject:
        body = self._request_envelope(method, path, payload)
        if not isinstance(body.get(result_key), dict):
            raise WorkflowHTTPError(
                f"The Appium HTTP response is missing '{result_key}'.",
                details=body,
            )
        return body[result_key]

    def _request_envelope(
        self,
        method: str,
        path: str,
        payload: JsonObject,
    ) -> JsonObject:
        url = f"{self.base_url}{path}"
        try:
            response = self.session.request(
                method,
                url,
                json=payload,
                headers=self.headers,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as error:
            raise WorkflowHTTPError(
                "The Appium HTTP service could not be reached.",
                details={"url": url, "reason": str(error)},
            ) from error

        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text}
        if not response.ok:
            raise WorkflowHTTPError(
                "The Appium HTTP service rejected a workflow step.",
                status_code=response.status_code,
                details=body,
            )
        if not isinstance(body, dict):
            raise WorkflowHTTPError(
                "The Appium HTTP response must be a JSON object.",
                status_code=response.status_code,
                details=body,
            )
        return body
