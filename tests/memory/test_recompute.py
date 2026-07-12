"""slice-041 counter rebuild tests (C-10): logs are truth, note count fields
are rebuildable caches."""
from __future__ import annotations

from pathlib import Path

from trowel_py.memory.access_log import AccessRecord, OutcomeRecord, log_access, log_outcome
from trowel_py.memory.recompute import recompute_counters
from trowel_py.memory.store import MemoryStore


def _read(ts: str, nid: str, *, search_id: str = "s1") -> AccessRecord:
    return AccessRecord(
        ts=ts, trowel_session_id="t", cc_session_id="c", toolUseId="tu",
        action="read", search_id=search_id, read_id="r", memory_id=nid,
    )


def _search(ts: str, nid: str, rank: int = 0) -> AccessRecord:
    return AccessRecord(
        ts=ts, trowel_session_id="t", cc_session_id="c", toolUseId="tu",
        action="search", search_id="s", memory_id=nid, rank=rank,
    )


def _outcome(ts: str, nid: str, outcome: str) -> OutcomeRecord:
    return OutcomeRecord(
        ts=ts, trowel_session_id="t", cc_session_id="c", toolUseId="tu",
        read_id="r", memory_id=nid, outcome=outcome,  # type: ignore[arg-type]
    )


def _make_note(root: Path, title: str) -> str:
    store = MemoryStore(root)
    return store.write_note(
        {"type": "note", "title": title, "verification": "verified", "__body": "x"}
    )


def test_recompute_counts_refs_and_last_ref(tmp_path: Path) -> None:
    nid = _make_note(tmp_path, "A")
    log_access(tmp_path, _read("2026-07-01T10:00:00+00:00", nid))
    log_access(tmp_path, _read("2026-07-05T10:00:00+00:00", nid))
    report = recompute_counters(tmp_path)
    assert report["updated"] == 1
    [n] = MemoryStore(tmp_path).load_notes()
    assert n.refs == 2
    assert n.last_ref == "2026-07-05"


def test_recompute_search_does_not_count_as_ref(tmp_path: Path) -> None:
    # C-1 (040-c): only read counts as retrieved; search is a candidate return.
    nid = _make_note(tmp_path, "A")
    log_access(tmp_path, _search("2026-07-01T10:00:00+00:00", nid))
    log_access(tmp_path, _read("2026-07-02T10:00:00+00:00", nid))
    recompute_counters(tmp_path)
    [n] = MemoryStore(tmp_path).load_notes()
    assert n.refs == 1  # search not counted


def test_recompute_helpful_harmful_split(tmp_path: Path) -> None:
    nid = _make_note(tmp_path, "A")
    log_access(tmp_path, _read("2026-07-01T10:00:00+00:00", nid))
    log_outcome(tmp_path, _outcome("2026-07-01T10:01:00+00:00", nid, "helpful"))
    log_outcome(tmp_path, _outcome("2026-07-02T10:01:00+00:00", nid, "helpful"))
    log_outcome(tmp_path, _outcome("2026-07-03T10:01:00+00:00", nid, "harmful"))
    recompute_counters(tmp_path)
    [n] = MemoryStore(tmp_path).load_notes()
    assert n.refs == 1
    assert n.helpful_refs == 2
    assert n.harmful_refs == 1


def test_recompute_unknown_outcome_not_counted(tmp_path: Path) -> None:
    # C-6 (040-c): unvoted reads are unknown, never silently helpful.
    nid = _make_note(tmp_path, "A")
    log_access(tmp_path, _read("2026-07-01T10:00:00+00:00", nid))
    log_outcome(tmp_path, _outcome("2026-07-01T10:01:00+00:00", nid, "unknown"))
    recompute_counters(tmp_path)
    [n] = MemoryStore(tmp_path).load_notes()
    assert n.helpful_refs == 0
    assert n.harmful_refs == 0


def test_recompute_multiple_notes_independent(tmp_path: Path) -> None:
    a = _make_note(tmp_path, "A")
    b = _make_note(tmp_path, "B")
    log_access(tmp_path, _read("2026-07-01T10:00:00+00:00", a))
    log_access(tmp_path, _read("2026-07-01T10:00:00+00:00", a))
    log_access(tmp_path, _read("2026-07-01T10:00:00+00:00", b))
    log_outcome(tmp_path, _outcome("2026-07-01T10:01:00+00:00", a, "harmful"))
    recompute_counters(tmp_path)
    notes = {n.title: n for n in MemoryStore(tmp_path).load_notes()}
    assert notes["A"].refs == 2
    assert notes["A"].harmful_refs == 1
    assert notes["B"].refs == 1
    assert notes["B"].helpful_refs == 0


def test_recompute_idempotent(tmp_path: Path) -> None:
    nid = _make_note(tmp_path, "A")
    log_access(tmp_path, _read("2026-07-01T10:00:00+00:00", nid))
    log_outcome(tmp_path, _outcome("2026-07-01T10:01:00+00:00", nid, "helpful"))
    recompute_counters(tmp_path)
    recompute_counters(tmp_path)  # second run
    [n] = MemoryStore(tmp_path).load_notes()
    assert n.refs == 1
    assert n.helpful_refs == 1


def test_recompute_clears_stale_cache(tmp_path: Path) -> None:
    # C-10: if a note's cached counts were hand-bumped stale, recompute
    # overwrites them with the log truth.
    nid = _make_note(tmp_path, "A")
    # poison the cache: write refs=99 without any log
    MemoryStore(tmp_path).update_note_fields(nid, {"refs": 99, "helpful_refs": 99})
    recompute_counters(tmp_path)  # no logs → counters reset to 0
    [n] = MemoryStore(tmp_path).load_notes()
    assert n.refs == 0
    assert n.helpful_refs == 0


def test_recompute_skips_log_for_missing_note(tmp_path: Path) -> None:
    # a log entry referencing a deleted/non-existent note must not crash
    log_access(tmp_path, _read("2026-07-01T10:00:00+00:00", "ghost-id"))
    report = recompute_counters(tmp_path)
    assert report["updated"] == 0  # ghost skipped, no crash


def test_recompute_no_logs_returns_zero(tmp_path: Path) -> None:
    _make_note(tmp_path, "A")
    report = recompute_counters(tmp_path)
    assert report["updated"] == 0


def test_recompute_works_when_note_has_memory_id(tmp_path: Path) -> None:
    # post-migrate notes carry a UUIDv7 memory_id, but the access-log records
    # the note's STEM (040-c handle_read logs note_id=stem from the URI, not
    # the memory_id). recompute uses rec.memory_id (=stem) to reach the file,
    # so it works even when note.memory_id is a UUIDv7.
    store = MemoryStore(tmp_path)
    stem = store.write_note({
        "type": "note", "title": "A", "verification": "verified",
        "memory_id": "019f5151-0dc4-757f-8b4d-41e7a1aacffa", "__body": "x",
    })
    log_access(tmp_path, _read("2026-07-01T10:00:00+00:00", stem))
    report = recompute_counters(tmp_path)
    assert report["updated"] == 1
    [n] = store.load_notes()
    assert n.refs == 1
    assert n.memory_id == "019f5151-0dc4-757f-8b4d-41e7a1aacffa"
