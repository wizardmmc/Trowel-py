"""slice-065 counter rebuild tests: logs + judgements are truth, the note
count caches (refs / read_sessions / helpful_refs / harmful_refs / last_ref)
are rebuildable, and helpful/harmful count INDEPENDENT user sessions."""
from __future__ import annotations

from datetime import timezone
from pathlib import Path

from trowel_py.memory.access_log import (
    AccessRecord,
    OutcomeRecord,
    log_access,
    log_outcome,
)
from trowel_py.memory.recompute import recompute_counters
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)
from trowel_py.memory.store import MemoryStore

TZ = timezone.utc


def _seed_user(root: Path, cc: str = "c") -> None:
    conn = open_sessions_db(root)
    try:
        create_sessions_repository(conn).register(
            SessionRecord(
                cc_session_id=cc,
                workdir="/p",
                date="2026-07-01",
                registered_at="t",
                session_kind="user",
                trowel_session_id=f"t-{cc}",
            )
        )
    finally:
        conn.close()


def _read(ts: str, nid: str, *, cc: str = "c", read_id: str = "r") -> AccessRecord:
    return AccessRecord(
        ts=ts, trowel_session_id=f"t-{cc}", cc_session_id=cc, toolUseId="tu",
        action="read", search_id="s", read_id=read_id, memory_id=nid,
    )


def _search(ts: str, nid: str, *, cc: str = "c") -> AccessRecord:
    return AccessRecord(
        ts=ts, trowel_session_id=f"t-{cc}", cc_session_id=cc, toolUseId="tu",
        action="search", search_id="s", memory_id=nid, rank=0,
    )


def _outcome(
    ts: str, nid: str, outcome: str, *, cc: str = "c", read_id: str = "r"
) -> OutcomeRecord:
    return OutcomeRecord(
        ts=ts, trowel_session_id=f"t-{cc}", cc_session_id=cc, toolUseId="tu",
        read_id=read_id, memory_id=nid, outcome=outcome,  # type: ignore[arg-type]
    )


def _make_note(root: Path, title: str) -> str:
    return MemoryStore(root).write_note(
        {"type": "note", "title": title, "verification": "verified", "__body": "x"}
    )


def test_recompute_counts_refs_read_sessions_last_ref(tmp_path: Path) -> None:
    nid = _make_note(tmp_path, "A")
    _seed_user(tmp_path)
    log_access(tmp_path, _read("2026-07-01T10:00:00+00:00", nid, read_id="r1"))
    log_access(tmp_path, _read("2026-07-05T10:00:00+00:00", nid, read_id="r2"))
    report = recompute_counters(tmp_path, local_tz=TZ)
    assert report["updated"] == 1
    [n] = MemoryStore(tmp_path).load_notes()
    assert n.refs == 2
    assert n.read_sessions == 1  # both reads are the same user session
    assert n.last_ref == "2026-07-05"
    assert report["read_sessions_total"] == 1


def test_recompute_search_does_not_count_as_ref(tmp_path: Path) -> None:
    # C-1 (040-c): only read counts as retrieved; search is a candidate return.
    nid = _make_note(tmp_path, "A")
    _seed_user(tmp_path)
    log_access(tmp_path, _search("2026-07-01T10:00:00+00:00", nid))
    log_access(tmp_path, _read("2026-07-02T10:00:00+00:00", nid, read_id="r"))
    recompute_counters(tmp_path, local_tz=TZ)
    [n] = MemoryStore(tmp_path).load_notes()
    assert n.refs == 1  # search not counted


def test_recompute_helpful_harmful_are_session_counts(tmp_path: Path) -> None:
    # slice-065: helpful_refs/harmful_refs count INDEPENDENT user sessions,
    # not raw outcome events.
    nid = _make_note(tmp_path, "A")
    for cc in ("c1", "c2", "c3"):
        _seed_user(tmp_path, cc)
        log_access(tmp_path, _read("2026-07-01T10:00:00+00:00", nid, cc=cc, read_id=f"r{cc}"))
    log_outcome(tmp_path, _outcome("2026-07-01T10:01:00+00:00", nid, "helpful", cc="c1", read_id="rc1"))
    log_outcome(tmp_path, _outcome("2026-07-01T10:01:00+00:00", nid, "helpful", cc="c2", read_id="rc2"))
    log_outcome(tmp_path, _outcome("2026-07-01T10:01:00+00:00", nid, "harmful", cc="c3", read_id="rc3"))
    recompute_counters(tmp_path, local_tz=TZ)
    [n] = MemoryStore(tmp_path).load_notes()
    assert n.helpful_refs == 2  # c1, c2 — two independent sessions
    assert n.harmful_refs == 1  # c3


def test_recompute_unknown_outcome_not_counted(tmp_path: Path) -> None:
    # C-6 (040-c): unvoted reads are unknown, never silently helpful.
    nid = _make_note(tmp_path, "A")
    _seed_user(tmp_path)
    log_access(tmp_path, _read("2026-07-01T10:00:00+00:00", nid, read_id="r"))
    log_outcome(tmp_path, _outcome("2026-07-01T10:01:00+00:00", nid, "unknown", read_id="r"))
    recompute_counters(tmp_path, local_tz=TZ)
    [n] = MemoryStore(tmp_path).load_notes()
    assert n.helpful_refs == 0
    assert n.harmful_refs == 0
    assert n.refs == 1  # read still counts as coverage


def test_recompute_multiple_notes_independent(tmp_path: Path) -> None:
    a = _make_note(tmp_path, "A")
    b = _make_note(tmp_path, "B")
    _seed_user(tmp_path)
    log_access(tmp_path, _read("2026-07-01T10:00:00+00:00", a, read_id="ra1"))
    log_access(tmp_path, _read("2026-07-01T10:00:00+00:00", a, read_id="ra2"))
    log_access(tmp_path, _read("2026-07-01T10:00:00+00:00", b, read_id="rb"))
    log_outcome(tmp_path, _outcome("2026-07-01T10:01:00+00:00", a, "harmful", read_id="ra1"))
    recompute_counters(tmp_path, local_tz=TZ)
    notes = {n.title: n for n in MemoryStore(tmp_path).load_notes()}
    assert notes["A"].refs == 2
    assert notes["A"].harmful_refs == 1
    assert notes["B"].refs == 1
    assert notes["B"].helpful_refs == 0


def test_recompute_idempotent(tmp_path: Path) -> None:
    nid = _make_note(tmp_path, "A")
    _seed_user(tmp_path)
    log_access(tmp_path, _read("2026-07-01T10:00:00+00:00", nid, read_id="r"))
    log_outcome(tmp_path, _outcome("2026-07-01T10:01:00+00:00", nid, "helpful", read_id="r"))
    recompute_counters(tmp_path, local_tz=TZ)
    recompute_counters(tmp_path, local_tz=TZ)  # second run
    [n] = MemoryStore(tmp_path).load_notes()
    assert n.refs == 1
    assert n.helpful_refs == 1


def test_recompute_clears_stale_cache(tmp_path: Path) -> None:
    # C-1: a hand-bumped stale cache with no surviving evidence → reset to 0.
    nid = _make_note(tmp_path, "A")
    MemoryStore(tmp_path).update_note_fields(
        nid, {"refs": 99, "read_sessions": 99, "helpful_refs": 99}
    )
    recompute_counters(tmp_path, local_tz=TZ)  # no logs → counters reset to 0
    [n] = MemoryStore(tmp_path).load_notes()
    assert n.refs == 0
    assert n.read_sessions == 0
    assert n.helpful_refs == 0


def test_recompute_skips_log_for_missing_note(tmp_path: Path) -> None:
    # a log entry referencing a deleted/non-existent note must not crash.
    _seed_user(tmp_path)
    log_access(tmp_path, _read("2026-07-01T10:00:00+00:00", "ghost-id", read_id="r"))
    report = recompute_counters(tmp_path, local_tz=TZ)
    assert report["updated"] == 0  # ghost skipped, no crash


def test_recompute_no_logs_returns_zero(tmp_path: Path) -> None:
    _make_note(tmp_path, "A")
    report = recompute_counters(tmp_path, local_tz=TZ)
    assert report["updated"] == 0


def test_recompute_works_when_note_has_memory_id(tmp_path: Path) -> None:
    # the access-log records the note's STEM (040-c handle_read logs
    # note_id=stem from the URI, not the UUIDv7 memory_id). recompute keys by
    # stem, so it reaches the file even when note.memory_id is a UUIDv7.
    store = MemoryStore(tmp_path)
    stem = store.write_note({
        "type": "note", "title": "A", "verification": "verified",
        "memory_id": "019f5151-0dc4-757f-8b4d-41e7a1aacffa", "__body": "x",
    })
    _seed_user(tmp_path)
    log_access(tmp_path, _read("2026-07-01T10:00:00+00:00", stem, read_id="r"))
    report = recompute_counters(tmp_path, local_tz=TZ)
    assert report["updated"] == 1
    [n] = store.load_notes()
    assert n.refs == 1
    assert n.memory_id == "019f5151-0dc4-757f-8b4d-41e7a1aacffa"
