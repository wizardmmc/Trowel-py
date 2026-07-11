"""tests for the dual-track audit backstop (slice-040 T8)."""
from __future__ import annotations

from trowel_py.memory.draft import Draft, DraftDiary, DraftNote
from trowel_py.memory.dualtrack import audit_draft


def test_diary_with_signal_word_flagged() -> None:
    d = Draft(diary=(DraftDiary(date="2026-07-09", events="本质是 GLM 非流式后端"),))
    rep = audit_draft(d)
    assert not rep.clean
    assert rep.leaks[0].signal == "本质是"
    assert rep.leaks[0].date == "2026-07-09"


def test_diary_without_signal_clean() -> None:
    d = Draft(diary=(DraftDiary(date="2026-07-09", events="10点开会 11点改 build 卡两小时"),))
    assert audit_draft(d).clean


def test_note_not_scanned() -> None:
    # C-1 boundary: notes ARE the knowledge track — a signal word in a note
    # body is correct, not a leak. audit_draft only scans diary entries.
    d = Draft(notes=(DraftNote(title="x", body="本质是 GLM 非流式后端"),))
    assert audit_draft(d).clean


def test_one_leak_per_diary_entry() -> None:
    # multiple signals in one entry still flag once (not N times)
    d = Draft(
        diary=(DraftDiary(date="2026-07-09", events="本质是 X。原理是 Y。"),)
    )
    rep = audit_draft(d)
    assert len(rep.leaks) == 1
