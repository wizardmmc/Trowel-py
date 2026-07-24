from __future__ import annotations

from pathlib import Path

from trowel_py import cli
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)

from .support import completed_offset, seed_legacy_session


def test_memory_cli_routes_repair_dry_run(tmp_path: Path, capsys) -> None:
    rc = cli._run_memory_cli(
        ["repair", "--date", "2026-07-09", "--root", str(tmp_path)]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "DRY-RUN" in output
    assert "2026-07-09" in output
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


def test_backfill_completed_dry_run(tmp_path: Path, capsys) -> None:
    memory_root = tmp_path / "memory"
    memory_root.mkdir()
    jsonl = tmp_path / "session.jsonl"
    jsonl.write_text("x" * 500)
    seed_legacy_session(memory_root, "s1", str(jsonl))

    rc = cli._run_backfill_completed(
        memory_root,
        "2026-07-09",
        apply=False,
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "DRY-RUN" in output
    assert "500" in output
    assert completed_offset(memory_root, "s1") is None


def test_backfill_completed_apply(tmp_path: Path, capsys) -> None:
    memory_root = tmp_path / "memory"
    memory_root.mkdir()
    jsonl = tmp_path / "session.jsonl"
    jsonl.write_text("x" * 500)
    seed_legacy_session(memory_root, "s1", str(jsonl))

    rc = cli._run_backfill_completed(
        memory_root,
        "2026-07-09",
        apply=True,
    )

    assert rc == 0
    assert "APPLY" in capsys.readouterr().out
    assert completed_offset(memory_root, "s1") == 500


def test_backfill_completed_skips_missing_jsonl(tmp_path: Path, capsys) -> None:
    memory_root = tmp_path / "memory"
    memory_root.mkdir()
    seed_legacy_session(memory_root, "gone", "/does/not/exist.jsonl")

    rc = cli._run_backfill_completed(
        memory_root,
        "2026-07-09",
        apply=True,
    )

    assert rc == 0
    assert "skipped" in capsys.readouterr().out.lower()
    assert completed_offset(memory_root, "gone") is None


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
    tmp_path: Path,
    capsys,
) -> None:
    memory_root = tmp_path / "memory"
    memory_root.mkdir()
    jsonl = tmp_path / "session.jsonl"
    jsonl.write_text("x" * 500)
    conn = open_sessions_db(memory_root)
    create_sessions_repository(conn).register(
        SessionRecord(
            cc_session_id="s1",
            workdir="/project",
            date="2026-07-09",
            jsonl_path=str(jsonl),
            registered_at="2026-07-09T10:00:00",
            extracted_at="2026-07-10T08:00:00",
        )
    )
    conn.close()

    rc = cli._run_backfill_completed(
        memory_root,
        "2026-07-09",
        apply=True,
    )

    assert rc == 0
    conn = open_sessions_db(memory_root)
    record = create_sessions_repository(conn).find_by_date("2026-07-09")[0]
    conn.close()
    assert record.last_completed_offset == 500
    assert record.last_extracted_offset == 500
    assert record.last_extracted_at == "2026-07-10T08:00:00"
