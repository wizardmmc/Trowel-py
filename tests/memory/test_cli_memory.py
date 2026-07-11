"""tests for the `trowel-py memory tidy` CLI (slice-038 T5)."""
from __future__ import annotations

from pathlib import Path

from trowel_py import cli
from trowel_py.memory.hooks import HookRegistry


def test_run_memory_tidy_dispatches_registered_job(tmp_path: Path, capsys) -> None:
    reg = HookRegistry()
    called: list[str] = []
    reg.register_tidy_job(lambda ev: called.append(ev["root"]))
    rc = cli._run_memory_tidy(reg, tmp_path)
    assert rc == 0
    assert called == [str(tmp_path)]
    out = capsys.readouterr().out
    assert "tidy" in out


def test_run_memory_tidy_empty_registry_is_noop(tmp_path: Path, capsys) -> None:
    # 空跑: no jobs registered in 038 -> dispatch must not error.
    rc = cli._run_memory_tidy(HookRegistry(), tmp_path)
    assert rc == 0
    assert "registered jobs: 0" in capsys.readouterr().out


def test_memory_cli_routes_tidy(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, str] = {}
    def fake(registry: object, root: Path) -> int:
        seen["root"] = str(root)
        return 0
    monkeypatch.setattr(cli, "_run_memory_tidy", fake)
    rc = cli._run_memory_cli(["tidy", "--root", str(tmp_path)])
    assert rc == 0
    assert seen["root"] == str(tmp_path)


def test_main_intercepts_memory_subcommand(monkeypatch) -> None:
    # bare `trowel-py memory tidy ...` must route to memory, never reach uvicorn.
    monkeypatch.setattr(cli.sys, "argv", ["trowel-py", "memory", "tidy"])
    routed: list[list[str]] = []
    monkeypatch.setattr(cli, "_run_memory_cli", lambda argv: routed.append(argv) or 0)
    try:
        cli.main()
    except SystemExit as e:
        assert e.code == 0
    assert routed == [["tidy"]]


# ---------- slice-040 T12: `trowel-py memory review` ----------


def test_memory_review_dispatches_write_job(tmp_path: Path, capsys) -> None:
    # dispatches the write loop over root for date_str. With no sessions
    # registered, run_daily_review is a no-op (find_pending empty).
    rc = cli._run_memory_review(HookRegistry(), tmp_path, "2026-07-09")
    assert rc == 0
    out = capsys.readouterr().out
    assert "review" in out
    assert "2026-07-09" in out


def test_memory_cli_routes_review(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, str] = {}

    def fake(registry: object, root: Path, date_str: str) -> int:
        seen["date"] = date_str
        seen["root"] = str(root)
        return 0

    monkeypatch.setattr(cli, "_run_memory_review", fake)
    rc = cli._run_memory_cli(["review", "--date", "2026-07-09", "--root", str(tmp_path)])
    assert rc == 0
    assert seen["date"] == "2026-07-09"
    assert seen["root"] == str(tmp_path)


def test_memory_review_default_date_today(tmp_path: Path, monkeypatch) -> None:
    from datetime import date

    seen: dict[str, str] = {}

    def fake(registry: object, root: Path, date_str: str) -> int:
        seen["date"] = date_str
        return 0

    monkeypatch.setattr(cli, "_run_memory_review", fake)
    cli._run_memory_cli(["review", "--root", str(tmp_path)])
    assert seen["date"] == date.today().isoformat()


# ---------- slice-040-a: `trowel-py memory repair` ----------


def test_memory_cli_routes_repair_dry_run(tmp_path: Path, capsys) -> None:
    # bare dry-run over an empty root prints the plan + exit 0 (no writes).
    rc = cli._run_memory_cli(
        ["repair", "--date", "2026-07-09", "--root", str(tmp_path)]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "2026-07-09" in out
    assert not (tmp_path / "episodes").exists()


def test_memory_cli_routes_repair_apply(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake(root: Path, date_str: str, *, apply: bool) -> int:
        seen["date"] = date_str
        seen["root"] = str(root)
        seen["apply"] = apply
        return 0

    monkeypatch.setattr(cli, "_run_repair", fake)
    rc = cli._run_memory_cli(
        ["repair", "--date", "2026-07-09", "--apply", "--root", str(tmp_path)]
    )
    assert rc == 0
    assert seen == {"date": "2026-07-09", "root": str(tmp_path), "apply": True}


# ---------- slice-040-b: `trowel-py memory backfill-completed` ----------


def _seed_session(mem: Path, sid: str, jsonl_path: str) -> None:
    """Register one legacy session (no completed water mark) into tmp sessions.db."""
    from trowel_py.memory.sessions_repo import (
        SessionRecord,
        create_sessions_repository,
        open_sessions_db,
    )

    conn = open_sessions_db(mem)
    try:
        repo = create_sessions_repository(conn)
        repo.register(
            SessionRecord(
                cc_session_id=sid,
                workdir="/proj",
                date="2026-07-09",
                jsonl_path=jsonl_path,
                registered_at="2026-07-09T10:00:00",
            )
        )
    finally:
        conn.close()


def _completed_offset(mem: Path, sid: str) -> int | None:
    from trowel_py.memory.sessions_repo import (
        create_sessions_repository,
        open_sessions_db,
    )

    conn = open_sessions_db(mem)
    try:
        for rec in create_sessions_repository(conn).find_by_date("2026-07-09"):
            if rec.cc_session_id == sid:
                return rec.last_completed_offset
    finally:
        conn.close()
    return None


def test_backfill_completed_dry_run(tmp_path: Path, capsys) -> None:
    mem = tmp_path / "memory"
    mem.mkdir()
    jsonl = tmp_path / "s.jsonl"
    jsonl.write_text("x" * 500)
    _seed_session(mem, "s1", str(jsonl))

    rc = cli._run_backfill_completed(mem, "2026-07-09", apply=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "500" in out
    # dry-run does not write
    assert _completed_offset(mem, "s1") is None


def test_backfill_completed_apply(tmp_path: Path, capsys) -> None:
    mem = tmp_path / "memory"
    mem.mkdir()
    jsonl = tmp_path / "s.jsonl"
    jsonl.write_text("x" * 500)
    _seed_session(mem, "s1", str(jsonl))

    rc = cli._run_backfill_completed(mem, "2026-07-09", apply=True)
    assert rc == 0
    out = capsys.readouterr().out
    assert "APPLY" in out
    assert _completed_offset(mem, "s1") == 500


def test_backfill_completed_skips_missing_jsonl(tmp_path: Path, capsys) -> None:
    mem = tmp_path / "memory"
    mem.mkdir()
    _seed_session(mem, "gone", "/does/not/exist.jsonl")

    rc = cli._run_backfill_completed(mem, "2026-07-09", apply=True)
    assert rc == 0  # missing jsonl is skipped, not a crash
    out = capsys.readouterr().out
    assert "skipped" in out.lower()
    assert _completed_offset(mem, "gone") is None


def test_memory_cli_routes_backfill_completed(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake(root: Path, date_str: str, *, apply: bool) -> int:
        seen["date"] = date_str
        seen["root"] = str(root)
        seen["apply"] = apply
        return 0

    monkeypatch.setattr(cli, "_run_backfill_completed", fake)
    rc = cli._run_memory_cli(
        ["backfill-completed", "--date", "2026-07-09", "--root", str(tmp_path)]
    )
    assert rc == 0
    assert seen == {"date": "2026-07-09", "root": str(tmp_path), "apply": False}


def test_backfill_completed_sets_extracted_for_already_extracted(
    tmp_path: Path, capsys
) -> None:
    """A session 040-a already extracted (extracted_at set) gets extr=comp.

    Otherwise find_incremental sees comp>0>extr(NULL=0) and re-distills the
    whole session, duplicating every episode segment (the 07-09 regression).
    """
    from trowel_py.memory.sessions_repo import (
        SessionRecord,
        create_sessions_repository,
        open_sessions_db,
    )

    mem = tmp_path / "memory"
    mem.mkdir()
    jsonl = tmp_path / "s.jsonl"
    jsonl.write_text("x" * 500)
    conn = open_sessions_db(mem)
    create_sessions_repository(conn).register(
        SessionRecord(
            cc_session_id="s1",
            workdir="/proj",
            date="2026-07-09",
            jsonl_path=str(jsonl),
            registered_at="t",
            extracted_at="2026-07-10T08:00:00",  # 040-a already extracted
        )
    )
    conn.close()

    rc = cli._run_backfill_completed(mem, "2026-07-09", apply=True)
    assert rc == 0

    conn2 = open_sessions_db(mem)
    rec = create_sessions_repository(conn2).find_by_date("2026-07-09")[0]
    conn2.close()
    assert rec.last_completed_offset == 500
    assert rec.last_extracted_offset == 500  # extr = comp (already extracted)
    assert rec.last_extracted_at == "2026-07-10T08:00:00"  # preserved


# ---------- slice-040-b: `trowel-py memory schedule` ----------


def test_memory_cli_routes_schedule_install(monkeypatch) -> None:
    seen: dict[str, str] = {}

    def fake_install(time: str = "02:30", home=None, *, load: bool = True):  # noqa: ANN001
        seen["time"] = time
        return Path("/fake/plist")

    monkeypatch.setattr("trowel_py.memory.schedule.install", fake_install)
    rc = cli._run_memory_cli(["schedule", "install", "--time", "03:05"])
    assert rc == 0
    assert seen["time"] == "03:05"


def test_memory_cli_routes_schedule_status(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "trowel_py.memory.schedule.status", lambda home=None: (True, False)
    )
    rc = cli._run_memory_cli(["schedule", "status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "installed=True" in out
    assert "loaded=False" in out


def test_memory_cli_routes_schedule_uninstall(monkeypatch, capsys) -> None:
    monkeypatch.setattr("trowel_py.memory.schedule.uninstall", lambda home=None: True)
    rc = cli._run_memory_cli(["schedule", "uninstall"])
    assert rc == 0
    assert "uninstalled: True" in capsys.readouterr().out

