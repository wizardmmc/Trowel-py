"""slice-050 profile distill watermark state tests (TDD RED → GREEN).

The distill job tracks which sessions it has already distilled-for-profile
independently from review_job's memory watermark (C-7: never touches
sessions.db). This file is that independent marker. Covers missing-file → {},
mark → load round-trip, accumulation, and overwrite-on-reprocess.
"""
from __future__ import annotations

import json
from pathlib import Path

from trowel_py.memory.profile_distill_state import (
    ProcessedSession,
    load_processed,
    mark_processed,
)


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    # fresh start: no distill state yet → {} (so every session is a candidate)
    assert load_processed(tmp_path) == {}


def test_mark_then_load_roundtrips(tmp_path: Path) -> None:
    mark_processed(tmp_path, "sess_a", end_offset=2048, at="2026-07-15T02:50:01")
    loaded = load_processed(tmp_path)
    assert loaded == {
        "sess_a": ProcessedSession(
            cc_session_id="sess_a", end_offset=2048, at="2026-07-15T02:50:01"
        )
    }


def test_mark_accumulates(tmp_path: Path) -> None:
    mark_processed(tmp_path, "sess_a", end_offset=2048, at="2026-07-15T02:50:01")
    mark_processed(tmp_path, "sess_b", end_offset=4096, at="2026-07-15T02:50:02")
    loaded = load_processed(tmp_path)
    assert set(loaded) == {"sess_a", "sess_b"}
    assert loaded["sess_b"].end_offset == 4096


def test_mark_overwrites_same_session(tmp_path: Path) -> None:
    # a resumed session distilled again advances its offset; the old record is
    # replaced (keyed by cc_session_id), not duplicated.
    mark_processed(tmp_path, "sess_a", end_offset=2048, at="2026-07-15T02:50:01")
    mark_processed(tmp_path, "sess_a", end_offset=4096, at="2026-07-16T02:50:01")
    loaded = load_processed(tmp_path)
    assert list(loaded) == ["sess_a"]
    assert loaded["sess_a"].end_offset == 4096
    assert loaded["sess_a"].at == "2026-07-16T02:50:01"


def test_state_file_is_independent_of_sessions_db(tmp_path: Path) -> None:
    # C-7: the marker lives in its own file under meta/, never in sessions.db.
    # A sessions.db present alongside must be untouched by the distill state.
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "sessions.db").write_bytes(b"\x00sqlite-preexisting")
    mark_processed(tmp_path, "sess_a", end_offset=10, at="2026-07-15T02:50:01")
    # sessions.db bytes unchanged
    assert (tmp_path / "meta" / "sessions.db").read_bytes() == b"\x00sqlite-preexisting"
    # state in its own file
    state = (tmp_path / "meta" / "profile-distill-state.json")
    assert state.exists()
    assert "sess_a" in json.loads(state.read_text(encoding="utf-8"))["processed"][-1]["cc_session_id"]
