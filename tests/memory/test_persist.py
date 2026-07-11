"""tests for persist_draft.

slice-040 landed notes + a per-session daily; slice-040-a re-routes the
experience track to a per-session EPISODE file (P1 overwrite fix) and gates
the landing on a completion manifest. These tests keep the slice-040
invariants that 040-a INHERITS (C-6): verification field round-trips,
inferred-untested never stable, never writes core, per-tier counts. The new
full-field / idempotence / manifest behavior is covered in
``test_persist_040a.py``.
"""
from __future__ import annotations

from pathlib import Path

from trowel_py.memory.draft import Draft, DraftDiary, DraftNote
from trowel_py.memory.persist import persist_draft
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import PersistContext


def _ctx(sid: str = "s1") -> PersistContext:
    return PersistContext(
        segment_id=f"{sid}:0:end",
        cc_session_id=sid,
        workdir="/proj",
        registered_at="2026-07-09T10:00:00",
        review_date="2026-07-09",
        source_jsonl=f"/jsonl/{sid}.jsonl",
    )


def _draft(**over) -> Draft:
    return Draft(
        notes=(DraftNote(title="浏览器缓存", verification="event-data-supported", pain=3, body="build 没生效先查缓存"),),
        diary=(DraftDiary(date="2026-07-09", events="卡两小时在浏览器缓存"),),
    )


def test_persist_writes_notes_and_episode(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    report = persist_draft(store, _draft(), _ctx())
    assert report.notes_written == 1
    assert report.episode_written
    assert len(store.load_notes()) == 1
    assert (tmp_path / "episodes" / "s1.md").exists()


def test_persist_verification_field_written_consistently(tmp_path: Path) -> None:
    # C-2 (inherited): draft verification → write_note → load_notes round-trips.
    store = MemoryStore(tmp_path)
    draft = Draft(notes=(DraftNote(title="错结论", verification="inferred-untested", body="x"),))
    persist_draft(store, draft, _ctx())
    [n] = store.load_notes()
    assert n.verification == "inferred-untested"


def test_persist_inferred_untested_never_stable(tmp_path: Path) -> None:
    # C-2 hard rule (inherited): inferred-untested never lands as stable.
    store = MemoryStore(tmp_path)
    draft = Draft(notes=(DraftNote(title="x", verification="inferred-untested"),))
    persist_draft(store, draft, _ctx())
    [n] = store.load_notes()
    assert n.confidence != "stable"


def test_persist_never_writes_core(tmp_path: Path) -> None:
    # C-4 (inherited): persist must never create/modify core.md.
    store = MemoryStore(tmp_path)
    assert not (tmp_path / "core.md").exists()
    persist_draft(store, _draft(), _ctx())
    assert not (tmp_path / "core.md").exists()
    assert store.load_core() == ""


def test_persist_episode_lands_under_episodes_dir(tmp_path: Path) -> None:
    # 040-a: the experience track lands as episodes/<sid>.md (one file per
    # session), NOT a daily overwrite target.
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(), _ctx())
    assert (tmp_path / "episodes" / "s1.md").exists()


def test_persist_verification_counts(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    draft = Draft(
        notes=(
            DraftNote(title="a", verification="verified"),
            DraftNote(title="b", verification="inferred-untested"),
            DraftNote(title="c", verification="inferred-untested"),
        )
    )
    report = persist_draft(store, draft, _ctx())
    assert report.verification_counts == {
        "verified": 1,
        "inferred-untested": 2,
    }
