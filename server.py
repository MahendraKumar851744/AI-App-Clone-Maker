from __future__ import annotations

import os

from backend import create_app


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.environ.get("BACKEND_HOST", "127.0.0.1"),
        port=int(os.environ.get("BACKEND_PORT", "5000")),
        debug=os.environ.get("BACKEND_DEBUG", "").lower() in {"1", "true", "yes"},
    )
