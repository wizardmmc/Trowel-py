"""tests for the online-read-path access/outcome logs (slice-040-c)."""
from __future__ import annotations

from pathlib import Path

from trowel_py.memory.access_log import (
    AccessRecord,
    OutcomeRecord,
    log_access,
    log_outcome,
    read_access_log,
    read_outcome_log,
)


def test_log_access_roundtrip(tmp_path: Path) -> None:
    """log_access writes one JSONL line; read_access_log returns it parsed."""
    rec = AccessRecord(
        ts="2026-07-11T10:00:00",
        trowel_session_id="trowel-1",
        cc_session_id="cc-1",
        toolUseId="call_abc",
        action="search",
        search_id="search-1",
        query="how to handle X",
        memory_id="",
    )
    log_access(tmp_path, rec)
    out = read_access_log(tmp_path)
    assert len(out) == 1
    assert out[0].action == "search"
    assert out[0].toolUseId == "call_abc"
    assert out[0].search_id == "search-1"
    assert out[0].query == "how to handle X"


def test_log_outcome_roundtrip(tmp_path: Path) -> None:
    rec = OutcomeRecord(
        ts="2026-07-11T10:00:01",
        trowel_session_id="trowel-1",
        cc_session_id="cc-1",
        toolUseId="call_abc",
        read_id="read-1",
        memory_id="note-x",
        outcome="helpful",
        reason="changed my decision",
    )
    log_outcome(tmp_path, rec)
    out = read_outcome_log(tmp_path)
    assert len(out) == 1
    assert out[0].outcome == "helpful"
    assert out[0].memory_id == "note-x"
    assert out[0].reason == "changed my decision"


def test_corrupt_line_skipped(tmp_path: Path) -> None:
    """A single corrupt line must not break reading the rest (038 W1)."""
    rec = AccessRecord(
        ts="t", trowel_session_id="s", cc_session_id="c", toolUseId="u",
        action="read", search_id="s1", read_id="r1", memory_id="m1",
    )
    log_access(tmp_path, rec)
    bad = tmp_path / "meta" / "access-log.jsonl"
    with bad.open("a", encoding="utf-8") as f:
        f.write("{not valid json\n")
    log_access(tmp_path, AccessRecord(
        ts="t2", trowel_session_id="s", cc_session_id="c", toolUseId="u2",
        action="read", search_id="s2", read_id="r2", memory_id="m2",
    ))
    out = read_access_log(tmp_path)
    assert len(out) == 2  # corrupt line skipped, both good lines kept


def test_read_empty_when_absent(tmp_path: Path) -> None:
    assert read_access_log(tmp_path) == []
    assert read_outcome_log(tmp_path) == []
