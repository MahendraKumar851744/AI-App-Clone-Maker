from __future__ import annotations

import io
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from PIL import Image

from backend.exploration.models import (
    ApkMetadata,
    CaptureResult,
    JsonObject,
    ScreenCapture,
    SessionInfo,
)
from backend.exploration.viewer import write_screen_viewer


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


class ExplorationStore:
    """Persistent evidence store for exploration runs."""

    def __init__(self, output_root: str | Path) -> None:
        self.output_root = Path(output_root).expanduser().resolve()
        self.run_id: str | None = None
        self.run_root: Path | None = None
        self.database_path: Path | None = None

    def create_run(self, apk: ApkMetadata, session: SessionInfo) -> str:
        if self.run_id is not None:
            raise RuntimeError("This store already owns an exploration run.")
        self.run_id = str(uuid4())
        self.run_root = self.output_root / self.run_id
        self.database_path = self.run_root / "exploration.db"
        (self.run_root / "screens").mkdir(parents=True, exist_ok=False)
        (self.run_root / "transitions").mkdir()
        (self.run_root / "logs").mkdir()
        (self.run_root / "logs" / "events.jsonl").touch()
        (self.run_root / "logs" / "errors.jsonl").touch()

        started_at = utc_now()
        manifest = {
            "schema_version": 1,
            "run_id": self.run_id,
            "status": "running",
            "started_at": started_at,
            "completed_at": None,
            "apk": apk.to_dict(),
            "session": session.to_dict(),
            "screens": [],
        }
        write_json(self.run_root / "manifest.json", manifest)
        write_json(
            self.run_root / "graph.json",
            {"schema_version": 1, "run_id": self.run_id, "nodes": [], "edges": []},
        )
        write_json(
            self.run_root / "summary.json",
            {"run_id": self.run_id, "status": "running", "screens_discovered": 0},
        )
        self._initialize_database()
        with self._connect() as database:
            database.execute(
                """
                INSERT INTO runs (
                    run_id, status, started_at, apk_path, apk_sha256,
                    apk_json, session_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.run_id,
                    "running",
                    started_at,
                    str(apk.path),
                    apk.sha256,
                    json.dumps(apk.to_dict(), default=str),
                    json.dumps(session.to_dict(), default=str),
                ),
            )
        self.event("run_started", {"apk_path": str(apk.path)})
        return self.run_id

    def save_screen(self, capture: ScreenCapture) -> CaptureResult:
        run_id, run_root = self._require_run()
        screen_root = run_root / "screens" / capture.screen_id
        screen_root.mkdir(parents=False, exist_ok=False)
        document = json.loads(json.dumps(capture.observation, default=str))
        elements = list(document["elements"])
        document.setdefault("hierarchy", {})["raw_xml"] = capture.hierarchy_xml

        artifact_prefix = Path("screens") / capture.screen_id
        screenshot_root = screen_root / "screenshots"
        screenshot_root.mkdir()
        screenshot_prefix = artifact_prefix / "screenshots"
        full_screenshot_path = screenshot_root / "screen.png"
        full_screenshot_path.write_bytes(capture.screenshot_png)
        document_path = screen_root / "appium-result.json"
        viewer_path = screen_root / "appium_screen_content.html"
        document["artifacts"] = {
            "root": str(artifact_prefix).replace("\\", "/"),
            "appium_result": str(
                artifact_prefix / "appium-result.json"
            ).replace("\\", "/"),
            "viewer": str(
                artifact_prefix / "appium_screen_content.html"
            ).replace("\\", "/"),
            "screenshots": {
                "root": str(screenshot_prefix).replace("\\", "/"),
                "full": str(screenshot_prefix / "screen.png").replace("\\", "/"),
            },
        }
        document.setdefault("screenshot", {})["artifact"] = document["artifacts"][
            "screenshots"
        ]["full"]
        self._write_region_crops(
            screenshot_root,
            screenshot_prefix,
            capture.screenshot_png,
            document,
        )
        write_json(document_path, document)
        write_screen_viewer(viewer_path, document)

        hierarchy_reference = (
            f"{document_path}#hierarchy.raw_xml"
        )

        with self._connect() as database:
            database.execute(
                """
                INSERT INTO screens (
                    run_id, screen_id, fingerprint, captured_at, package,
                    activity, screenshot_path, hierarchy_path, observation_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    capture.screen_id,
                    document["fingerprint"],
                    document["captured_at"],
                    document["screen"].get("package"),
                    document["screen"].get("activity"),
                    str(full_screenshot_path),
                    hierarchy_reference,
                    json.dumps(document, default=str),
                ),
            )
            database.executemany(
                """
                INSERT INTO elements (
                    run_id, screen_id, element_id, source, class_name,
                    resource_id, text, interaction, element_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        capture.screen_id,
                        element["id"],
                        element["source"],
                        element["class"],
                        element["resource_id"],
                        element["text"],
                        element["interaction"],
                        json.dumps(element, default=str),
                    )
                    for element in elements
                ],
            )

        manifest = self._read_json(run_root / "manifest.json")
        manifest["screens"].append(capture.screen_id)
        write_json(run_root / "manifest.json", manifest)
        write_json(
            run_root / "graph.json",
            {
                "schema_version": 1,
                "run_id": run_id,
                "nodes": [
                    {
                        "screen_id": capture.screen_id,
                        "fingerprint": document["fingerprint"],
                        "activity": document["screen"].get("activity"),
                        "artifact_path": str(screen_root),
                    }
                ],
                "edges": [],
            },
        )
        self.event(
            "screen_captured",
            {
                "screen_id": capture.screen_id,
                "element_count": len(elements),
            },
        )
        return CaptureResult(
            run_id=run_id,
            screen_id=capture.screen_id,
            artifact_root=run_root,
            screen_root=screen_root,
            document_path=document_path,
            viewer_path=viewer_path,
            element_count=len(elements),
        )

    def complete(self, result: CaptureResult) -> None:
        _, run_root = self._require_run()
        completed_at = utc_now()
        with self._connect() as database:
            database.execute(
                "UPDATE runs SET status = ?, completed_at = ? WHERE run_id = ?",
                ("completed", completed_at, result.run_id),
            )
        manifest = self._read_json(run_root / "manifest.json")
        manifest["status"] = "completed"
        manifest["completed_at"] = completed_at
        write_json(run_root / "manifest.json", manifest)
        write_json(
            run_root / "summary.json",
            {
                **result.to_dict(),
                "completed_at": completed_at,
                "screens_discovered": 1,
            },
        )
        self.event("run_completed", result.to_dict())

    def fail(self, error: Exception) -> None:
        if self.run_id is None or self.run_root is None:
            return
        completed_at = utc_now()
        with self._connect() as database:
            database.execute(
                "UPDATE runs SET status = ?, completed_at = ?, error = ? WHERE run_id = ?",
                ("failed", completed_at, str(error), self.run_id),
            )
        manifest = self._read_json(self.run_root / "manifest.json")
        manifest["status"] = "failed"
        manifest["completed_at"] = completed_at
        manifest["error"] = str(error)
        write_json(self.run_root / "manifest.json", manifest)
        self.event("run_failed", {"error": str(error)}, level="error")

    def event(
        self,
        event_type: str,
        data: JsonObject,
        *,
        level: str = "info",
    ) -> None:
        run_id, run_root = self._require_run()
        occurred_at = utc_now()
        event = {
            "occurred_at": occurred_at,
            "level": level,
            "event": event_type,
            "data": data,
        }
        with (run_root / "logs" / "events.jsonl").open(
            "a",
            encoding="utf-8",
        ) as stream:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        if level == "error":
            with (run_root / "logs" / "errors.jsonl").open(
                "a",
                encoding="utf-8",
            ) as stream:
                stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        with self._connect() as database:
            database.execute(
                """
                INSERT INTO events (run_id, occurred_at, level, event_type, data_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    occurred_at,
                    level,
                    event_type,
                    json.dumps(data, default=str),
                ),
            )

    def _initialize_database(self) -> None:
        with self._connect() as database:
            database.executescript(
                """
                CREATE TABLE runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    apk_path TEXT NOT NULL,
                    apk_sha256 TEXT NOT NULL,
                    apk_json TEXT NOT NULL,
                    session_json TEXT NOT NULL,
                    error TEXT
                );
                CREATE TABLE screens (
                    run_id TEXT NOT NULL,
                    screen_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    package TEXT,
                    activity TEXT,
                    screenshot_path TEXT NOT NULL,
                    hierarchy_path TEXT NOT NULL,
                    observation_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, screen_id)
                );
                CREATE TABLE elements (
                    run_id TEXT NOT NULL,
                    screen_id TEXT NOT NULL,
                    element_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    class_name TEXT NOT NULL,
                    resource_id TEXT,
                    text TEXT,
                    interaction TEXT,
                    element_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, screen_id, element_id)
                );
                CREATE TABLE events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    data_json TEXT NOT NULL
                );
                CREATE TABLE actions (
                    run_id TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    screen_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    action_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, action_id)
                );
                CREATE TABLE transitions (
                    run_id TEXT NOT NULL,
                    transition_id TEXT NOT NULL,
                    from_screen_id TEXT,
                    to_screen_id TEXT,
                    status TEXT NOT NULL,
                    transition_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, transition_id)
                );
                CREATE TABLE workflows (
                    run_id TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    workflow_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, workflow_id)
                );
                CREATE TABLE llm_decisions (
                    run_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    decision_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, decision_id)
                );
                CREATE TABLE facts (
                    run_id TEXT NOT NULL,
                    fact_id TEXT NOT NULL,
                    fact_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, fact_id)
                );
                CREATE TABLE frontier (
                    run_id TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    PRIMARY KEY (run_id, action_id)
                );
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self.database_path is None:
            raise RuntimeError("Exploration run has not been created.")
        database = sqlite3.connect(self.database_path)
        try:
            yield database
            database.commit()
        except Exception:
            database.rollback()
            raise
        finally:
            database.close()

    def _require_run(self) -> tuple[str, Path]:
        if self.run_id is None or self.run_root is None:
            raise RuntimeError("Exploration run has not been created.")
        return self.run_id, self.run_root

    @staticmethod
    def _read_json(path: Path) -> JsonObject:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_region_crops(
        screenshot_root: Path,
        screenshot_prefix: Path,
        screenshot: bytes,
        document: JsonObject,
    ) -> None:
        regions = document.get("screenshot", {}).get("regions", {})
        if not regions:
            return
        with Image.open(io.BytesIO(screenshot)) as image:
            image.load()
            for name, region in regions.items():
                bounds = region.get("bounds")
                if not bounds:
                    continue
                box = (
                    max(0, int(bounds["left"])),
                    max(0, int(bounds["top"])),
                    min(image.width, int(bounds["right"])),
                    min(image.height, int(bounds["bottom"])),
                )
                if box[2] <= box[0] or box[3] <= box[1]:
                    continue
                filename = f"{name}.png"
                image.crop(box).save(screenshot_root / filename, format="PNG")
                artifact = str(screenshot_prefix / filename).replace("\\", "/")
                region["artifact"] = artifact
                document["artifacts"]["screenshots"][name] = artifact
