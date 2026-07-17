"""tests for the sessions registry (slice-040 T3 + slice-040-b kind/Protocol)."""
from __future__ import annotations

import sqlite3

from trowel_py.memory.sessions_repo import (
    SessionBinding,
    SessionRecord,
    SessionRegistrar,
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


# ---------- slice-050: find_all_completed_sessions (distill candidates) ----------


def _complete(repo: object, *sids: str) -> None:
    """stamp a completed offset on each sid so they surface as distill candidates."""
    for sid in sids:
        repo.update_completed(sid, 1000)


def test_find_all_completed_returns_sessions_with_completed_offset() -> None:
    repo = _repo()
    repo.register(_rec(cc_session_id="a"))
    repo.register(_rec(cc_session_id="b"))
    repo.register(_rec(cc_session_id="c"))  # no completed offset → excluded
    _complete(repo, "a", "b")
    found = repo.find_all_completed_sessions()
    assert {s.cc_session_id for s in found} == {"a", "b"}


def test_find_all_completed_excludes_review_and_distill_kinds() -> None:
    # distill must not re-distill its own agent runs NOR the review agent's.
    repo = _repo()
    repo.register(_rec(cc_session_id="user", session_kind="user"))
    repo.register(_rec(cc_session_id="rev", session_kind="review"))
    repo.register(_rec(cc_session_id="dist", session_kind="distill"))
    _complete(repo, "user", "rev", "dist")
    found = repo.find_all_completed_sessions()
    assert {s.cc_session_id for s in found} == {"user"}


def test_find_all_completed_ignores_review_extracted_state() -> None:
    # C-7: distill uses its OWN watermark. A session review already extracted
    # is STILL a distill candidate (review's last_extracted_offset is irrelevant).
    repo = _repo()
    repo.register(_rec(cc_session_id="a"))
    repo.update_completed("a", 1000)
    repo.advance_extracted("a", 1000)  # review says "done"
    found = repo.find_all_completed_sessions()
    assert {s.cc_session_id for s in found} == {"a"}


def test_find_all_completed_ordered_by_registered_at() -> None:
    repo = _repo()
    repo.register(_rec(cc_session_id="late", registered_at="2026-07-10T10:00:00"))
    repo.register(_rec(cc_session_id="early", registered_at="2026-07-09T10:00:00"))
    _complete(repo, "late", "early")
    found = repo.find_all_completed_sessions()
    assert [s.cc_session_id for s in found] == ["early", "late"]


def test_find_all_completed_custom_exclude_kinds() -> None:
    repo = _repo()
    repo.register(_rec(cc_session_id="a", session_kind="user"))
    repo.register(_rec(cc_session_id="b", session_kind="custom"))
    _complete(repo, "a", "b")
    found = repo.find_all_completed_sessions(exclude_kinds=["custom"])
    assert {s.cc_session_id for s in found} == {"a"}


def test_find_pending_preserves_jsonl_path() -> None:
    repo = _repo()
    repo.register(_rec(cc_session_id="a", jsonl_path="/projects/slug/a.jsonl"))
    [r] = repo.find_pending("2026-07-09")
    assert r.jsonl_path == "/projects/slug/a.jsonl"


# --- slice-040-b: session_kind + Protocol + exclude_kinds -------------------


def test_session_kind_defaults_user() -> None:
    """SessionRecord defaults to the 'user' kind (no kwarg needed)."""
    assert _rec().session_kind == "user"


def test_register_writes_session_kind() -> None:
    """register persists session_kind so review sessions can be excluded by kind."""
    repo = _repo()
    repo.register(_rec(cc_session_id="r1", session_kind="review"))
    row = repo._conn.execute(  # noqa: SLF001 — read raw column for the assertion
        "SELECT session_kind FROM sessions WHERE cc_session_id = ?", ("r1",)
    ).fetchone()
    assert row["session_kind"] == "review"


def test_old_row_null_kind_backfills_user() -> None:
    """A legacy row with NULL session_kind (pre-040-b) reads back as 'user'."""
    repo = _repo()
    repo._conn.execute(  # noqa: SLF001 — simulate a pre-040-b legacy row
        "INSERT INTO sessions(cc_session_id, workdir, date, registered_at, session_kind)"
        " VALUES ('legacy', '/w', '2026-07-09', 't', NULL)"
    )
    repo._conn.commit()  # noqa: SLF001
    [rec] = repo.find_pending("2026-07-09")
    assert rec.session_kind == "user"


def test_find_pending_exclude_kinds() -> None:
    """find_pending filters by session_kind, independent of workdir (C-5: no path guess)."""
    repo = _repo()
    # same workdir, distinguished only by kind — proves kind (not path) does the filtering
    repo.register(_rec(cc_session_id="user1", session_kind="user", workdir="/proj"))
    repo.register(_rec(cc_session_id="rev1", session_kind="review", workdir="/proj"))
    pending = repo.find_pending("2026-07-09", exclude_kinds=["review"])
    assert len(pending) == 1
    assert pending[0].cc_session_id == "user1"


def test_session_registrar_protocol_accepts_fake() -> None:
    """A duck-typed registrar with register + update_completed satisfies the Protocol."""

    class FakeRegistrar:
        def register(self, rec: SessionRecord) -> None:
            self.recorded = rec

        def update_completed(
            self, cc_session_id: str, completed_bytes: int, when: str | None = None
        ) -> None:
            self.completed = (cc_session_id, completed_bytes)

    assert isinstance(FakeRegistrar(), SessionRegistrar)


# --- slice-040-b T9/T10: schema migration + offset water marks --------------


def test_old_schema_migrates_offset_columns(tmp_path) -> None:
    """A pre-040-b db (6-column schema) is upgraded on connect, rows survive."""
    db = tmp_path / "sessions.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        "CREATE TABLE sessions (cc_session_id TEXT PRIMARY KEY, workdir TEXT NOT NULL,"
        " date TEXT NOT NULL, jsonl_path TEXT, registered_at TEXT NOT NULL, extracted_at TEXT);"
    )
    conn.execute(
        "INSERT INTO sessions(cc_session_id, workdir, date, registered_at)"
        " VALUES ('legacy', '/w', '2026-07-09', 't')"
    )
    conn.commit()
    conn.close()

    conn = sqlite3.connect(str(db))
    repo = create_sessions_repository(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
    assert {
        "session_kind",
        "last_completed_offset",
        "last_completed_at",
        "last_extracted_offset",
        "last_extracted_at",
    } <= cols
    assert len(repo.find_pending("2026-07-09")) == 1  # legacy row survives
    conn.close()


def test_ensure_columns_idempotent(tmp_path) -> None:
    """Connecting twice must not raise (SQLite has no ADD COLUMN IF NOT EXISTS)."""
    db = tmp_path / "sessions.db"
    conn1 = sqlite3.connect(str(db))
    create_sessions_repository(conn1)
    conn1.close()
    conn2 = sqlite3.connect(str(db))
    create_sessions_repository(conn2)  # second connect: no duplicate-column error
    conn2.close()


def test_update_completed_stamps_offset() -> None:
    repo = _repo()
    repo.register(_rec(cc_session_id="a"))
    repo.update_completed("a", 2048, when="2026-07-11T02:30:00")
    row = repo._conn.execute(  # noqa: SLF001
        "SELECT last_completed_offset, last_completed_at FROM sessions WHERE cc_session_id='a'"
    ).fetchone()
    assert row["last_completed_offset"] == 2048
    assert row["last_completed_at"] == "2026-07-11T02:30:00"


def test_find_incremental_returns_segment() -> None:
    repo = _repo()
    repo.register(_rec(cc_session_id="a"))
    repo.update_completed("a", 2048, when="t")
    segs = repo.find_incremental()
    assert len(segs) == 1
    assert segs[0].session.cc_session_id == "a"
    assert segs[0].start == 0  # last_extracted_offset NULL → 0
    assert segs[0].end == 2048


def test_find_incremental_excludes_equal_offsets() -> None:
    """completed == extracted → fully distilled, no new work."""
    repo = _repo()
    repo.register(_rec(cc_session_id="a"))
    repo.update_completed("a", 2048, when="t")
    repo.advance_extracted("a", 2048, when="t2")
    assert repo.find_incremental() == []


def test_find_incremental_excludes_review() -> None:
    """review sessions never re-enter the queue (C-5)."""
    repo = _repo()
    repo.register(_rec(cc_session_id="rev", session_kind="review"))
    repo.update_completed("rev", 2048, when="t")
    assert repo.find_incremental() == []


def test_find_incremental_excludes_distill_and_eval_kinds() -> None:
    """slice-053: distill + eval agent sessions never enter the review queue
    (otherwise review would distill an agent's own run + recurse on the judge)."""
    repo = _repo()
    repo.register(_rec(cc_session_id="user", session_kind="user"))
    repo.register(_rec(cc_session_id="dist", session_kind="distill"))
    repo.register(_rec(cc_session_id="ev", session_kind="eval"))
    repo.update_completed("user", 2048, when="t")
    repo.update_completed("dist", 2048, when="t")
    repo.update_completed("ev", 2048, when="t")
    segs = repo.find_incremental()
    assert [s.session.cc_session_id for s in segs] == ["user"]


def test_find_incremental_excludes_half_turn() -> None:
    """No completed water mark (result not seen yet) → not pending (C-6 half-turn)."""
    repo = _repo()
    repo.register(_rec(cc_session_id="a"))
    assert repo.find_incremental() == []


def test_advance_extracted_stamps() -> None:
    repo = _repo()
    repo.register(_rec(cc_session_id="a"))
    repo.advance_extracted("a", 4096, when="2026-07-11T02:35:00")
    row = repo._conn.execute(  # noqa: SLF001
        "SELECT last_extracted_offset, last_extracted_at FROM sessions WHERE cc_session_id='a'"
    ).fetchone()
    assert row["last_extracted_offset"] == 4096
    assert row["last_extracted_at"] == "2026-07-11T02:35:00"


# --- slice-061: persistent session binding (trowel_id -> cc_id + kind) -------


def _binding(**over) -> SessionBinding:
    base = dict(
        trowel_session_id="t1",
        cc_session_id="cc-1",
        session_kind="user",
        workdir="/w",
        bound_at="2026-07-17T10:00:00",
    )
    base.update(over)
    return SessionBinding(**base)


def test_bind_and_find_cc_by_trowel() -> None:
    repo = _repo()
    repo.bind_session(_binding())
    b = repo.find_cc_by_trowel("t1")
    assert b is not None
    assert b.cc_session_id == "cc-1"
    assert b.session_kind == "user"
    assert b.workdir == "/w"


def test_bind_many_trowel_to_one_cc() -> None:
    """C-4: one cc id may map to many trowel ids (a cc session resumed from two
    different trowel sessions). The second bind must NOT overwrite the first."""
    repo = _repo()
    repo.bind_session(_binding(trowel_session_id="t1", bound_at="t1"))
    repo.bind_session(_binding(trowel_session_id="t2", bound_at="t2"))
    trowels = repo.find_trowels_by_cc("cc-1")
    assert {b.trowel_session_id for b in trowels} == {"t1", "t2"}
    # each keeps its own bound_at (C-4 — no overwrite of prior binds)
    by_id = {b.trowel_session_id: b for b in trowels}
    assert by_id["t1"].bound_at == "t1"
    assert by_id["t2"].bound_at == "t2"


def test_bind_idempotent_on_trowel_id() -> None:
    """re-binding the same trowel id is a no-op (PK keeps it harmless)."""
    repo = _repo()
    repo.bind_session(_binding())
    repo.bind_session(_binding())
    assert len(repo.find_trowels_by_cc("cc-1")) == 1


def test_find_cc_by_trowel_missing() -> None:
    assert _repo().find_cc_by_trowel("nope") is None


def test_find_trowels_by_cc_empty() -> None:
    assert _repo().find_trowels_by_cc("nope") == []


def test_binding_kind_independent_of_sessions_row() -> None:
    """A trowel binding carries its OWN kind, so a non-user session is
    identifiable even when its access-log cc_session_id is empty (C-3)."""
    repo = _repo()
    repo.bind_session(_binding(trowel_session_id="te", cc_session_id="cce",
                               session_kind="eval"))
    b = repo.find_cc_by_trowel("te")
    assert b is not None and b.session_kind == "eval"


def test_bindings_table_created_on_old_db(tmp_path) -> None:
    """A pre-061 db (sessions table only) gains the bindings table on connect."""
    db = tmp_path / "s.db"
    conn = sqlite3.connect(str(db))
    create_sessions_repository(conn)
    conn.close()
    # second connect on a db that already has the table — no duplicate error
    conn = sqlite3.connect(str(db))
    create_sessions_repository(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(session_bindings)")}
    assert {
        "trowel_session_id", "cc_session_id", "session_kind", "workdir", "bound_at"
    } <= cols
    conn.close()


def test_register_persists_trowel_binding() -> None:
    """register() with a trowel_session_id also writes the binding (C-3)."""
    repo = _repo()
    repo.register(
        _rec(cc_session_id="cc-x", trowel_session_id="t-x", session_kind="user")
    )
    b = repo.find_cc_by_trowel("t-x")
    assert b is not None
    assert b.cc_session_id == "cc-x"
    assert b.session_kind == "user"


def test_register_without_trowel_id_skips_bind() -> None:
    """legacy register (no trowel_session_id) writes no binding."""
    repo = _repo()
    repo.register(_rec(cc_session_id="cc-y"))
    assert repo.find_cc_by_trowel("anything") is None
    assert repo.find_trowels_by_cc("cc-y") == []


