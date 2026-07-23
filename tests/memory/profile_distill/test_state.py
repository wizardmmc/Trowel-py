from __future__ import annotations

import json
from pathlib import Path

from trowel_py.memory.profile_distill.state import (
    ProcessedSession,
    load_processed,
    mark_processed,
)


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
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
    mark_processed(tmp_path, "sess_a", end_offset=2048, at="2026-07-15T02:50:01")
    mark_processed(tmp_path, "sess_a", end_offset=4096, at="2026-07-16T02:50:01")
    loaded = load_processed(tmp_path)
    assert list(loaded) == ["sess_a"]
    assert loaded["sess_a"].end_offset == 4096
    assert loaded["sess_a"].at == "2026-07-16T02:50:01"


def test_state_file_is_independent_of_sessions_db(tmp_path: Path) -> None:
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "sessions.db").write_bytes(b"\x00sqlite-preexisting")
    mark_processed(tmp_path, "sess_a", end_offset=10, at="2026-07-15T02:50:01")
    assert (tmp_path / "meta" / "sessions.db").read_bytes() == b"\x00sqlite-preexisting"
    state = (tmp_path / "meta" / "profile-distill-state.json")
    assert state.exists()
    assert "sess_a" in json.loads(state.read_text(encoding="utf-8"))["processed"][-1]["cc_session_id"]
