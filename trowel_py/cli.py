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
import json
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


def _current_iso_week() -> str:
    """Current ISO week as ``YYYY-Www`` (e.g. ``2026-W28``)."""
    from datetime import date

    y, w, _ = date.today().isocalendar()
    return f"{y}-W{w:02d}"


def _current_month() -> str:
    """Current month as ``YYYY-MM``."""
    from datetime import date

    return date.today().strftime("%Y-%m")


def _run_memory_cli(argv: list[str]) -> int:
    """Dispatch ``trowel-py memory <subcommand>``."""
    parser = argparse.ArgumentParser(prog="trowel-py memory", description="memory subsystem")
    sub = parser.add_subparsers(dest="cmd", required=True)
    tidy = sub.add_parser("tidy", help="weekly/monthly tidy + rollback (041)")
    tidy.add_argument("--weekly", action="store_true",
                      help="run weekly tidy (compress + bypass + TidyPlan) for one ISO week")
    tidy.add_argument("--monthly", action="store_true",
                      help="run monthly tidy (compress + retire + promote + TidyPlan)")
    tidy.add_argument("--iso-week", help="ISO week YYYY-Www (default: current week)")
    tidy.add_argument("--month", help="YYYY-MM (default: current month)")
    tidy.add_argument("--rollback", help="rollback a tidy plan id (restore notes/)")
    tidy.add_argument("--root", help="memory root (default: resolved from config.toml)")
    review = sub.add_parser(
        "review", help="run the daily write loop: distill a day's cc sessions"
    )
    review.add_argument("--date", help="target day YYYY-MM-DD (default: today)")
    review.add_argument("--root", help="memory root (default: resolved from config.toml)")
    repair = sub.add_parser(
        "repair",
        help="backfill per-session episodes from surviving drafts (P1 overwrite fix)",
    )
    repair.add_argument("--date", required=True, help="target day YYYY-MM-DD")
    repair.add_argument(
        "--apply",
        action="store_true",
        help="apply (back up + write); default is a dry-run listing",
    )
    repair.add_argument("--root", help="memory root (default: resolved from config.toml)")
    backfill = sub.add_parser(
        "backfill-completed",
        help="stamp last_completed_offset for legacy rows from jsonl size (040-b)",
    )
    backfill.add_argument("--date", required=True, help="target day YYYY-MM-DD")
    backfill.add_argument(
        "--apply",
        action="store_true",
        help="apply (write); default is a dry-run listing",
    )
    backfill.add_argument("--root", help="memory root (default: resolved from config.toml)")
    dr = sub.add_parser(
        "dict-rebuild", help="regenerate dictionary L0/L1 from notes (040-c C-3)"
    )
    dr.add_argument("--apply", action="store_true", help="write; default is dry-run")
    dr.add_argument("--root", help="memory root (default: resolved from config.toml)")
    mig = sub.add_parser(
        "migrate",
        help="backfill memory_id/status/valid_from on legacy notes (041 C-9)",
    )
    mig.add_argument("--apply", action="store_true", help="write; default is dry-run")
    mig.add_argument("--root", help="memory root (default: resolved from config.toml)")
    core = sub.add_parser("core", help="layer-one promotion: nominate/approve/activate (041)")
    core_sub = core.add_subparsers(dest="core_cmd", required=True)
    core_nom = core_sub.add_parser("nominate", help="nominate a note as core candidate")
    core_nom.add_argument("note_stem", help="note filename stem")
    core_app = core_sub.add_parser("approve", help="candidate → core.md (status=trial)")
    core_app.add_argument("candidate_id", help="candidate memory_id")
    core_act = core_sub.add_parser("activate", help="core item trial → active")
    core_act.add_argument("memory_id", help="core item memory_id")
    core.add_argument("--root", help="memory root (default: resolved from config.toml)")
    metrics_cmd = sub.add_parser(
        "metrics", help="print north-star metrics (041): harmful_memory_rate + raw material"
    )
    metrics_cmd.add_argument("--root", help="memory root (default: resolved from config.toml)")
    args = parser.parse_args(argv)

    if args.cmd == "tidy":
        from trowel_py.memory import paths

        root = Path(args.root) if args.root else paths.resolve_memory_root()
        if args.rollback:
            from trowel_py.memory.tidy import rollback_plan

            rollback_plan(root, args.rollback)
            print(f"[memory] tidy rollback: plan {args.rollback} restored")
            return 0
        if args.weekly:
            from trowel_py.config import load_llm_config
            from trowel_py.llm.client import AnthropicProvider
            from trowel_py.memory.tidy import run_weekly_tidy

            iso_week = args.iso_week or _current_iso_week()
            provider = AnthropicProvider(load_llm_config())
            report = run_weekly_tidy(root, iso_week, provider)
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            return 0
        if args.monthly:
            from trowel_py.config import load_llm_config
            from trowel_py.llm.client import AnthropicProvider
            from trowel_py.memory.tidy import run_monthly_tidy

            month = args.month or _current_month()
            provider = AnthropicProvider(load_llm_config())
            report = run_monthly_tidy(root, month, provider)
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            return 0
        # default (038 behavior): dispatch registered tidy jobs (空跑 trace)
        from trowel_py.memory import hooks

        return _run_memory_tidy(hooks.default, root)
    if args.cmd == "review":
        from datetime import date as _date

        from trowel_py.memory import hooks, paths

        root = Path(args.root) if args.root else paths.resolve_memory_root()
        date_str = args.date or _date.today().isoformat()
        return _run_memory_review(hooks.default, root, date_str)
    if args.cmd == "repair":
        from trowel_py.memory import paths

        root = Path(args.root) if args.root else paths.resolve_memory_root()
        return _run_repair(root, args.date, apply=args.apply)
    if args.cmd == "backfill-completed":
        from trowel_py.memory import paths

        root = Path(args.root) if args.root else paths.resolve_memory_root()
        return _run_backfill_completed(root, args.date, apply=args.apply)
    if args.cmd == "dict-rebuild":
        from trowel_py.config import load_llm_config
        from trowel_py.llm.client import AnthropicProvider
        from trowel_py.memory import paths
        from trowel_py.memory.dictionary import rebuild_dictionary

        root = Path(args.root) if args.root else paths.resolve_memory_root()
        provider = AnthropicProvider(load_llm_config())
        out = rebuild_dictionary(root, apply=args.apply, provider=provider)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "migrate":
        from trowel_py.memory import paths
        from trowel_py.memory.migrate import migrate_memory

        root = Path(args.root) if args.root else paths.resolve_memory_root()
        report = migrate_memory(root, apply=args.apply)
        mode = "apply" if args.apply else "dry-run"
        print(
            f"[memory] migrate {mode}: scanned={report.scanned} "
            f"migrated={report.migrated} skipped={report.skipped}"
        )
        if report.backed_up:
            print(f"[memory] backup -> {report.backed_up}")
        return 0
    if args.cmd == "core":
        from trowel_py.memory import paths
        from trowel_py.memory.core_ops import (
            activate_core_item,
            approve_candidate,
            nominate_candidate,
        )

        root = Path(args.root) if args.root else paths.resolve_memory_root()
        if args.core_cmd == "nominate":
            mid = nominate_candidate(root, args.note_stem)
            print(f"[memory] nominated {args.note_stem} -> candidate {mid}")
        elif args.core_cmd == "approve":
            approve_candidate(root, args.candidate_id)
            print(f"[memory] approved {args.candidate_id} -> core.md (trial)")
        elif args.core_cmd == "activate":
            activate_core_item(root, args.memory_id)
            print(f"[memory] activated {args.memory_id} -> core (active)")
        return 0
    if args.cmd == "metrics":
        from trowel_py.memory import paths
        from trowel_py.memory.north_star import compute_north_star

        root = Path(args.root) if args.root else paths.resolve_memory_root()
        report = compute_north_star(root)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
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


def _run_repair(root: Path, date_str: str, *, apply: bool) -> int:
    """Backfill per-session episodes from surviving drafts (slice-040-a P1 fix).

    Reads ``review-daily-work/<date>/*/draft.json`` and replays each into an
    ``episodes/<sid>.md`` file, then rebuilds the derived daily. Dry-run by
    default; ``apply`` backs up the memory root first. Never re-runs the agent
    and never touches notes.

    Args:
        root: the memory root.
        date_str: target day ``YYYY-MM-DD``.
        apply: True to back up + write; False to print the plan only.

    Returns:
        Process exit code (0 on success, 1 if apply verification failed).
    """
    from trowel_py.memory.repair import repair_memory

    report = repair_memory(root, date_str, apply=apply)
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[memory] repair {mode} over {root} for {date_str}")
    print(f"  drafts found: {sum(1 for p in report.planned if p.has_draft)}")
    print(f"  missing drafts (session registered, no draft): {len(report.missing_drafts)}")
    for sid in report.missing_drafts:
        print(f"    - {sid}")
    if apply:
        print(f"  episodes created: {report.episodes_created}")
        print(f"  daily rebuilt: {report.daily_rebuilt}")
        print(f"  backup: {report.backup_dir}")
        print(f"  notes unchanged: {report.notes_before}")
        if not report.ok:
            print("  VERIFICATION FAILED: episodes_created != draft count")
            return 1
    return 0


def _run_backfill_completed(root: Path, date_str: str, *, apply: bool) -> int:
    """Stamp ``last_completed_offset`` for legacy rows from the jsonl size (040-b).

    Pre-040-b rows have no completed water mark (the column was NULL until the
    schema evolved). This reads each row's jsonl size and stamps it as the
    completed offset so the incremental queue picks the session up. Dry-run by
    default; ``apply`` writes. Rows whose jsonl no longer exists are skipped
    (reported), never crashed on — the assumption is the whole session is a
    completed turn boundary (conservative: never guess a partial offset).

    Args:
        root: the memory root.
        date_str: target day ``YYYY-MM-DD``.
        apply: True to write; False to print the plan only.

    Returns:
        Process exit code (0 on success).
    """
    from trowel_py.memory.review_job import _review_lock  # noqa: SLF001 — shared C-3 mutex
    from trowel_py.memory.sessions_repo import (
        create_sessions_repository,
        open_sessions_db,
    )

    try:
        with _review_lock(root):
            conn = open_sessions_db(root)
            try:
                repo = create_sessions_repository(conn)
                plan: list[tuple[str, str, str | None]] = []
                for rec in repo.find_by_date(date_str):
                    if rec.last_completed_offset is not None:
                        continue  # already has a water mark — leave it alone
                    plan.append((rec.cc_session_id, rec.jsonl_path, rec.extracted_at))

                mode = "APPLY" if apply else "DRY-RUN"
                print(
                    f"[memory] backfill-completed {mode} over {root} for {date_str}"
                )
                print(f"  legacy rows needing backfill: {len(plan)}")

                def _size_of(jp: str) -> int | None:
                    """Stat the jsonl now (CR M-5: not a stale plan snapshot)."""
                    if not jp:
                        return None
                    p = Path(jp)
                    return p.stat().st_size if p.is_file() else None

                skipped = 0
                if apply:
                    backfilled = 0
                    already_extracted = 0
                    for sid, jp, extracted_at in plan:
                        size = _size_of(jp)
                        if size is None:
                            skipped += 1
                            continue
                        repo.update_completed(sid, size)
                        if extracted_at:
                            # 040-a already extracted → push the extracted mark
                            # to comp so find_incremental won't re-distill it
                            # (avoids the 07-09 segment-duplication regression).
                            repo.advance_extracted(sid, size, when=extracted_at)
                            already_extracted += 1
                        backfilled += 1
                    print(f"  backfilled: {backfilled}")
                    if already_extracted:
                        print(
                            f"    (of which already-extracted by 040-a: {already_extracted})"
                        )
                    print(f"  skipped (jsonl missing): {skipped}")
                else:
                    for sid, jp, extracted_at in plan:
                        size = _size_of(jp)
                        marker = str(size) if size is not None else "MISSING"
                        tag = " [040-a extracted]" if extracted_at else ""
                        print(f"  - {sid}: {marker}{tag}  ({jp})")
                        if size is None:
                            skipped += 1
                    if skipped:
                        print(
                            f"  ({skipped} jsonl missing, would be skipped on apply)"
                        )
                return 0
            finally:
                conn.close()
    except BlockingIOError:
        print(
            f"[memory] backfill-completed skipped (a review job is running) for {date_str}"
        )
        return 0


if __name__ == "__main__":
    main()
