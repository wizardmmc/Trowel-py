"""trowel-py CLI entry point.

Installed as the ``trowel-py`` script via ``[project.scripts]`` in pyproject.
Starts the FastAPI app (API + served frontend) and opens a browser at it.

Usage:
    trowel-py            # default port 8000, auto-open browser
    trowel-py --no-open  # skip the browser
    trowel-py --port 9000

Backend deps (cc CLI, ~/.claude/) are NOT bundled — the user installs
claude-code separately. This command only starts trowel's own server.
"""
from __future__ import annotations

import argparse
import threading
import webbrowser


def main() -> None:
    """Start the trowel-py server and open a browser at it."""
    parser = argparse.ArgumentParser(prog="trowel-py", description=__doc__)
    parser.add_argument(
        "--port", type=int, default=8000, help="port to listen on (default 8000)"
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="host to bind (default 127.0.0.1)"
    )
    parser.add_argument(
        "--no-open", action="store_true", help="don't open a browser window"
    )
    args = parser.parse_args()

    # Replicate server.py bootstrap: logging + db migrations. Without these,
    # `trowel-py` starts but every DB endpoint 500s in a fresh environment
    # (no tables). server.py did this in bootstrap(); the CLI must too.
    import logging
    import sys
    from pathlib import Path

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_dir / "trowel.log"),
            logging.StreamHandler(sys.stdout),
        ],
    )

    from trowel_py.db.connection import create_db
    from trowel_py.db.migrate import run_migrations

    conn = create_db()
    run_migrations(conn)
    conn.close()  # release the write lock so request connections can write

    # Open the browser a beat after the server starts listening. uvicorn.run
    # blocks the main thread, so schedule the open on a timer before it.
    # Cancel the timer if uvicorn fails to start (port in use, etc.) so we
    # don't pop a browser tab pointing at a dead URL.
    timer = None
    if not args.no_open:
        url = f"http://{args.host}:{args.port}"
        timer = threading.Timer(1.0, lambda: webbrowser.open(url))
        timer.start()

    # Imported here so `trowel-py --help` doesn't pay the uvicorn import cost.
    import uvicorn

    try:
        uvicorn.run(
            "trowel_py.app:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            log_level="info",
        )
    except BaseException:
        if timer is not None:
            timer.cancel()
        raise


if __name__ == "__main__":
    main()
