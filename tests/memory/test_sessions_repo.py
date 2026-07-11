"""tests for the sessions registry (slice-040 T3)."""
from __future__ import annotations

import sqlite3

from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
)


def _rec(**over) -> SessionRecord:
    base = dict(
        cc_session_id="abc-1",
        workdir="/tmp/proj",
        date="2026-07-09",
        jsonl_path="/x.jsonl",
        registered_at="2026-07-09T10:00:00",
    )
    base.update(over)
    return SessionRecord(**base)


def _repo() -> object:
    return create_sessions_repository(sqlite3.connect(":memory:"))


def test_register_and_find_pending() -> None:
    repo = _repo()
    repo.register(_rec(cc_session_id="a"))
    repo.register(_rec(cc_session_id="b"))
    assert len(repo.find_pending("2026-07-09")) == 2


def test_find_pending_filters_by_date() -> None:
    repo = _repo()
    repo.register(_rec(cc_session_id="a", date="2026-07-09"))
    repo.register(_rec(cc_session_id="b", date="2026-07-10"))
    assert len(repo.find_pending("2026-07-09")) == 1


def test_mark_extracted_excludes_from_pending() -> None:
    # extraction idempotency: once extracted, a session is not re-extracted.
    repo = _repo()
    repo.register(_rec(cc_session_id="a"))
    repo.mark_extracted("a", "2026-07-10T02:17:00")
    assert repo.find_pending("2026-07-09") == []


def test_find_pending_excludes_review_workdir() -> None:
    # D2: the daily distillation session itself must not be distilled again.
    repo = _repo()
    repo.register(_rec(cc_session_id="user", workdir="/Users/x/proj"))
    repo.register(
        _rec(
            cc_session_id="review",
            workdir="/Users/x/.trowel/review-daily-work/2026-07-09",
        )
    )
    pending = repo.find_pending("2026-07-09", exclude_workdir_substr="review-daily-work")
    assert len(pending) == 1
    assert pending[0].cc_session_id == "user"


def test_register_is_idempotent_on_session_id() -> None:
    # the session-start hook may fire more than once; PK keeps it harmless.
    repo = _repo()
    repo.register(_rec(cc_session_id="a", workdir="/p1"))
    repo.register(_rec(cc_session_id="a", workdir="/p2"))
    rows = repo.find_pending("2026-07-09")
    assert len(rows) == 1
    # INSERT OR IGNORE keeps the first registration's workdir
    assert rows[0].workdir == "/p1"


def test_find_pending_preserves_jsonl_path() -> None:
    repo = _repo()
    repo.register(_rec(cc_session_id="a", jsonl_path="/projects/slug/a.jsonl"))
    [r] = repo.find_pending("2026-07-09")
    assert r.jsonl_path == "/projects/slug/a.jsonl"
