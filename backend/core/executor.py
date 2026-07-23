from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from threading import RLock
from time import perf_counter
from typing import Any
from uuid import uuid4

from backend.core.contracts import ExecutionContext, JsonObject, require_object
from backend.core.registry import ModuleRegistry
from backend.core.templating import TemplateRenderer
from backend.errors import PlatformError, RequestValidationError, ResourceNotFoundError


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StepTrace:
    step_id: str
    module_id: str
    status: str = "running"
    started_at: str = field(default_factory=utc_now)
    completed_at: str | None = None
    duration_ms: int | None = None
    input: JsonObject | None = None
    output: JsonObject | None = None
    error: JsonObject | None = None


@dataclass
class RunRecord:
    run_id: str
    kind: str
    status: str = "running"
    started_at: str = field(default_factory=utc_now)
    completed_at: str | None = None
    duration_ms: int | None = None
    input: JsonObject = field(default_factory=dict)
    output: JsonObject | None = None
    error: JsonObject | None = None
    steps: list[StepTrace] = field(default_factory=list)

    def to_dict(self) -> JsonObject:
        return asdict(self)


class RunStore:
    def __init__(self, max_runs: int = 500) -> None:
        self.max_runs = max_runs
        self._runs: OrderedDict[str, RunRecord] = OrderedDict()
        self._lock = RLock()

    def add(self, run: RunRecord) -> None:
        with self._lock:
            self._runs[run.run_id] = run
            while len(self._runs) > self.max_runs:
                self._runs.popitem(last=False)

    def get(self, run_id: str) -> RunRecord:
        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            raise ResourceNotFoundError(f"Run '{run_id}' was not found.")
        return run

    def list(self, limit: int = 50) -> list[JsonObject]:
        safe_limit = max(1, min(limit, 200))
        with self._lock:
            runs = list(self._runs.values())[-safe_limit:]
        return [run.to_dict() for run in reversed(runs)]


class ModuleExecutor:
    def __init__(
        self,
        registry: ModuleRegistry,
        run_store: RunStore,
        renderer: TemplateRenderer,
    ) -> None:
        self.registry = registry
        self.run_store = run_store
        self.renderer = renderer

    def execute_module(self, module_id: str, module_input: JsonObject) -> RunRecord:
        module = self.registry.get(module_id)
        run = RunRecord(
            run_id=str(uuid4()),
            kind="module",
            input=dict(module_input),
        )
        self.run_store.add(run)
        started = perf_counter()
        trace = StepTrace(
            step_id="module",
            module_id=module_id,
            input=dict(module_input),
        )
        run.steps.append(trace)
        try:
            output = module.execute(
                dict(module_input),
                ExecutionContext(run_id=run.run_id, step_id=trace.step_id),
            )
            require_object(output, "module output")
            trace.output = output
            trace.status = "completed"
            run.output = output
            run.status = "completed"
            return run
        except PlatformError as error:
            trace.error = error.to_dict()["error"]
            trace.status = "failed"
            run.error = trace.error
            run.status = "failed"
            raise
        except Exception as error:
            wrapped = PlatformError(
                "The module raised an unexpected error.",
                details={"module_id": module_id, "reason": str(error)},
            )
            trace.error = wrapped.to_dict()["error"]
            trace.status = "failed"
            run.error = trace.error
            run.status = "failed"
            raise wrapped from error
        finally:
            duration = round((perf_counter() - started) * 1000)
            trace.duration_ms = duration
            trace.completed_at = utc_now()
            run.duration_ms = duration
            run.completed_at = trace.completed_at

    def execute_workflow(self, payload: JsonObject) -> RunRecord:
        initial_input = require_object(payload.get("input", {}), "input")
        steps = payload.get("steps")
        if not isinstance(steps, list) or not steps:
            raise RequestValidationError("'steps' must be a non-empty array.")

        run = RunRecord(
            run_id=str(uuid4()),
            kind="workflow",
            input=dict(initial_input),
        )
        self.run_store.add(run)
        run_started = perf_counter()
        previous: JsonObject = dict(initial_input)
        step_outputs: dict[str, JsonObject] = {}
        seen_ids: set[str] = set()

        try:
            for index, raw_step in enumerate(steps, start=1):
                step = require_object(raw_step, f"steps[{index - 1}]")
                module_id = step.get("module_id")
                if not isinstance(module_id, str) or not module_id:
                    raise RequestValidationError(
                        f"'steps[{index - 1}].module_id' must be a non-empty string."
                    )
                step_id = step.get("id", f"step_{index}")
                if not isinstance(step_id, str) or not step_id:
                    raise RequestValidationError(
                        f"'steps[{index - 1}].id' must be a non-empty string."
                    )
                if step_id in seen_ids:
                    raise RequestValidationError(f"Duplicate workflow step id '{step_id}'.")
                seen_ids.add(step_id)

                context_data = {
                    "initial": initial_input,
                    "previous": previous,
                    "steps": step_outputs,
                }
                if "input" in step:
                    rendered = self.renderer.render_value(step["input"], context_data)
                    module_input = require_object(
                        rendered,
                        f"rendered input for step '{step_id}'",
                    )
                else:
                    module_input = dict(previous)

                trace = StepTrace(
                    step_id=step_id,
                    module_id=module_id,
                    input=dict(module_input),
                )
                run.steps.append(trace)
                step_started = perf_counter()
                try:
                    module = self.registry.get(module_id)
                    output = module.execute(
                        dict(module_input),
                        ExecutionContext(run_id=run.run_id, step_id=step_id),
                    )
                    output = require_object(output, f"output from step '{step_id}'")
                    trace.output = output
                    trace.status = "completed"
                    previous = output
                    step_outputs[step_id] = output
                except PlatformError as error:
                    trace.error = error.to_dict()["error"]
                    trace.status = "failed"
                    raise
                except Exception as error:
                    wrapped = PlatformError(
                        "The workflow module raised an unexpected error.",
                        details={"module_id": module_id, "reason": str(error)},
                    )
                    trace.error = wrapped.to_dict()["error"]
                    trace.status = "failed"
                    raise wrapped from error
                finally:
                    trace.duration_ms = round((perf_counter() - step_started) * 1000)
                    trace.completed_at = utc_now()

            run.output = previous
            run.status = "completed"
            return run
        except PlatformError as error:
            run.error = error.to_dict()["error"]
            run.status = "failed"
            raise
        finally:
            run.duration_ms = round((perf_counter() - run_started) * 1000)
            run.completed_at = utc_now()
