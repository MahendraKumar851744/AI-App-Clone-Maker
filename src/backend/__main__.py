from __future__ import annotations

import os
import socket
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from backend import create_app


app = create_app()


def _port_is_open(host: str, port: int) -> bool:

    try:

        with socket.create_connection((host, port), timeout=0.4):
            return True

    except OSError:
        return False


def _process_is_running(process_id: int) -> bool:

    if os.name == "nt":
        import ctypes

        query_limited_information = 0x1000
        process_handle = ctypes.windll.kernel32.OpenProcess(
            query_limited_information,
            False,
            process_id,
        )

        if not process_handle:
            return False

        ctypes.windll.kernel32.CloseHandle(process_handle)
        return True

    try:
        os.kill(process_id, 0)
        return True

    except OSError:
        return False


@contextmanager
def _backend_instance_lock() -> Iterator[None]:

    project_root = Path(__file__).resolve().parents[2]
    runtime_root = project_root / ".runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    lock_path = runtime_root / "backend-server.lock"

    descriptor: int | None = None

    while descriptor is None:

        try:
            descriptor = os.open(
                lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )

        except FileExistsError as error:

            try:
                existing_process_id = int(
                    lock_path.read_text(encoding="ascii").strip()
                )

            except (FileNotFoundError, ValueError):
                existing_process_id = 0

            if existing_process_id and _process_is_running(existing_process_id):
                raise RuntimeError(
                    "The backend is already running "
                    f"(process {existing_process_id})."
                ) from error

            lock_path.unlink(missing_ok=True)

    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        yield

    finally:
        os.close(descriptor)
        lock_path.unlink(missing_ok=True)


def main() -> None:

    host = os.environ.get("BACKEND_HOST", "127.0.0.1")
    port = int(os.environ.get("BACKEND_PORT", "5000"))

    with _backend_instance_lock():

        if _port_is_open(host, port):
            raise RuntimeError(
                f"Port {port} is already in use at {host}. "
                "The backend may already be running."
            )

        app.run(
            host=host,
            port=port,
            debug=os.environ.get("BACKEND_DEBUG", "").lower()
            in {"1", "true", "yes"},
            use_reloader=False,
        )


if __name__ == "__main__":

    try:
        main()

    except RuntimeError as error:
        raise SystemExit(f"Backend not started: {error}") from error
