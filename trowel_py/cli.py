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
import sys
import threading
import webbrowser
from pathlib import Path


def main() -> None:
    """Start the trowel-py server and open a browser at it.

    Subcommands are intercepted before the serve parser is built, so the serve
    path (and its flags) stays untouched::

        trowel-py memory tidy   # run registered memory tidy jobs (slice-038)
    """
    # Intercept `memory` subcommands before the serve parser is built, so the
    # serve path (and its --port/--host/--no-open flags) stays byte-for-byte
    # unchanged. Assumption (W6): position-1 == "memory" means the memory
    # subsystem — fine in 038 since serve takes no positional args; revisit if a
    # future slice adds positional serve args.
    if len(sys.argv) >= 2 and sys.argv[1] == "memory":
        raise SystemExit(_run_memory_cli(sys.argv[2:]))
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
    import os

    # slice-030: tell the FastAPI lifespan what port we'll listen on so it can
    # build the reverse-proxy URL (http://127.0.0.1:<port>) the CC subprocess
    # targets as ANTHROPIC_BASE_URL. factory=True means uvicorn creates the app
    # itself, so we can't set app.state directly — env is the channel.
    os.environ["TROWEL_SERVER_PORT"] = str(args.port)

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


def _run_memory_cli(argv: list[str]) -> int:
    """Dispatch ``trowel-py memory <subcommand>``."""
    parser = argparse.ArgumentParser(prog="trowel-py memory", description="memory subsystem")
    sub = parser.add_subparsers(dest="cmd", required=True)
    tidy = sub.add_parser("tidy", help="run registered memory tidy jobs (batch; 空跑 in 038)")
    tidy.add_argument("--root", help="memory root (default: resolved from config.toml)")
    review = sub.add_parser(
        "review", help="run the daily write loop: distill a day's cc sessions"
    )
    review.add_argument("--date", help="target day YYYY-MM-DD (default: today)")
    review.add_argument("--root", help="memory root (default: resolved from config.toml)")
    args = parser.parse_args(argv)

    if args.cmd == "tidy":
        from trowel_py.memory import hooks, paths

        root = Path(args.root) if args.root else paths.resolve_memory_root()
        return _run_memory_tidy(hooks.default, root)
    if args.cmd == "review":
        from datetime import date as _date

        from trowel_py.memory import hooks, paths

        root = Path(args.root) if args.root else paths.resolve_memory_root()
        date_str = args.date or _date.today().isoformat()
        return _run_memory_review(hooks.default, root, date_str)
    return 2  # unreachable: subparser is required


def _run_memory_review(registry: object, root: Path, date_str: str) -> int:
    """Dispatch the daily write loop (slice-040) over ``root`` for ``date_str``.

    Registers the review write-job on the injected registry and dispatches it.
    The job distills the day's pending cc sessions into notes/ + diary/ (see
    ``trowel_py.memory.review_job``).

    Args:
        registry: a ``HookRegistry`` (injected so tests don't touch global state).
        root: the memory root to dispatch over.
        date_str: target day (ISO ``YYYY-MM-DD``).

    Returns:
        Process exit code (0 on success).
    """
    from trowel_py.memory.review_job import run_daily_review_sync

    registry.register_write_job(run_daily_review_sync)
    registry.dispatch_write_job({"date": date_str, "root": str(root)})
    print(
        f"[memory] review dispatched over {root} for {date_str} | "
        f"log: {registry.dispatch_log}"  # noqa: SLF001 — 空跑 trace
    )
    return 0


def _run_memory_tidy(registry: object, root: Path) -> int:
    """Dispatch the tidy jobs registered on ``registry`` over ``root``.

    In slice-038 the registry is empty (空跑): this only confirms the trigger
    fires without error. Business logic (compress / promote / regen dictionary)
    registers via ``register_tidy_job`` in slice-041.

    Args:
        registry: a ``HookRegistry`` (injected so tests don't touch global state).
        root: the memory root to dispatch over.

    Returns:
        Process exit code (0 on success).
    """
    registry.dispatch_tidy_job({"root": str(root)})
    print(
        f"[memory] tidy dispatched over {root} | "
        f"registered jobs: {len(registry._tidy)} | "  # noqa: SLF001 — 空跑 trace
        f"log: {registry.dispatch_log}"
    )
    return 0


if __name__ == "__main__":
    main()
