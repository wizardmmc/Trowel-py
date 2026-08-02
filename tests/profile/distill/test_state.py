from __future__ import annotations

import json
from pathlib import Path

from trowel_py.profile.distill.state import (
    ProcessedCodexTurn,
    ProcessedSession,
    load_codex_processed,
    load_processed,
    mark_codex_processed,
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
    state = tmp_path / "meta" / "profile-distill-state.json"
    assert state.exists()
    assert (
        "sess_a"
        in json.loads(state.read_text(encoding="utf-8"))["processed"][-1][
            "cc_session_id"
        ]
    )


def test_codex_turn_records_roundtrip_without_changing_claude_watermarks(
    tmp_path: Path,
) -> None:
    mark_processed(tmp_path, "sess_a", end_offset=2048, at="2026-07-15T02:50:01")
    mark_codex_processed(
        tmp_path,
        "thread-a",
        "turn-a",
        at="2026-07-15T02:50:02",
    )

    assert load_processed(tmp_path) == {
        "sess_a": ProcessedSession(
            cc_session_id="sess_a",
            end_offset=2048,
            at="2026-07-15T02:50:01",
        )
    }
    assert load_codex_processed(tmp_path) == {
        ("thread-a", "turn-a"): ProcessedCodexTurn(
            thread_id="thread-a",
            turn_id="turn-a",
            at="2026-07-15T02:50:02",
        )
    }


def test_legacy_claude_only_state_loads_with_empty_codex_records(
    tmp_path: Path,
) -> None:
    meta = tmp_path / "meta"
    meta.mkdir()
    (meta / "profile-distill-state.json").write_text(
        json.dumps(
            {
                "processed": [
                    {
                        "cc_session_id": "legacy",
                        "end_offset": 99,
                        "at": "2026-07-01T02:30:00",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert load_processed(tmp_path)["legacy"].end_offset == 99
    assert load_codex_processed(tmp_path) == {}


def test_codex_records_allow_a_failed_gap_in_one_thread(tmp_path: Path) -> None:
    mark_codex_processed(
        tmp_path,
        "thread-a",
        "turn-2",
        at="2026-07-15T02:50:02",
    )

    assert ("thread-a", "turn-1") not in load_codex_processed(tmp_path)
    assert ("thread-a", "turn-2") in load_codex_processed(tmp_path)
