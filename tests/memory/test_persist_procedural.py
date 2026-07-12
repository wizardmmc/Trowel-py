"""tests for the procedural-memory persist upgrades: full-field landing,
idempotence, episode + reflection + escalation + completion manifest.

These sit on top of the base persist contract (verification three-tier,
dual-track, never writes layer one — all unchanged, see test_persist.py) and
add: PersistContext-driven landing, procedural kind, full rationale fields,
(cc_session_id, content_hash) idempotence, per-session reflection/escalation
files, and a completion manifest written last.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from trowel_py.memory.draft import Draft, DraftDiary, DraftNote
from trowel_py.memory.persist import persist_draft
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import PersistContext

_TODAY = "2026-07-09"


def _ctx(sid: str = "s1", review_date: str = _TODAY) -> PersistContext:
    return PersistContext(
        segment_id=f"{sid}:0:end",
        cc_session_id=sid,
        workdir="/proj",
        registered_at="2026-07-09T10:00:00",
        review_date=review_date,
        source_jsonl=f"/jsonl/{sid}.jsonl",
    )


def _note(**over) -> DraftNote:
    base = DraftNote(
        title="缓存坑",
        summary="build 没生效先查缓存",
        body="根因实测内容",
        verification="event-data-supported",
        verification_reason="会话内直接观测",
        pain=3,
        pain_reason="浪费两小时",
        conflicts_with=("existing-note",),
        kind="gotcha",
    )
    return replace(base, **over) if over else base


def _draft(**over) -> Draft:
    base = Draft(
        notes=(_note(),),
        diary=(DraftDiary(date=_TODAY, events="卡两小时在缓存"),),
        reflection="温故反思正文",
        escalate_to_human=("未解的问题",),
    )
    return replace(base, **over) if over else base


# ---------- P2: full-field landing ----------


def test_persist_lands_all_rationale_fields(tmp_path: Path) -> None:
    # P2: DraftNote.verification_reason / pain_reason / conflicts_with must land
    # in the note frontmatter (040 dropped them → agent rationale evaporated).
    store = MemoryStore(tmp_path)
    report = persist_draft(store, _draft(), _ctx())
    [n] = store.load_notes()
    assert n.verification_reason == "会话内直接观测"
    assert n.pain_reason == "浪费两小时"
    assert n.conflicts_with == ("existing-note",)
    assert n.kind == "gotcha"
    assert report.ok


def test_persist_lands_provenance_fields(tmp_path: Path) -> None:
    # 溯源 + 幂等 substrate: source_sessions + content_hash on every note.
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(), _ctx("s1"))
    [n] = store.load_notes()
    assert "s1" in n.source_sessions
    assert n.content_hash  # non-empty


def test_persist_writes_episode_file(tmp_path: Path) -> None:
    # the diary now lands as a per-session episode, NOT a daily overwrite.
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(), _ctx("s1"))
    ep = tmp_path / "episodes" / "s1.md"
    assert ep.exists()
    assert "卡两小时在缓存" in ep.read_text(encoding="utf-8")


def test_persist_writes_reflection_file(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(), _ctx("s1"))
    refl = tmp_path / "meta" / "reflections" / "s1.md"
    assert refl.exists()
    assert "温故反思正文" in refl.read_text(encoding="utf-8")


def test_persist_skips_empty_reflection(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(reflection=""), _ctx("s1"))
    assert not (tmp_path / "meta" / "reflections" / "s1.md").exists()


def test_persist_writes_escalation_file(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(), _ctx("s1"))
    esc = tmp_path / "meta" / "escalations" / "s1.md"
    assert esc.exists()
    assert "未解的问题" in esc.read_text(encoding="utf-8")


def test_persist_skips_empty_escalation(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(escalate_to_human=()), _ctx("s1"))
    assert not (tmp_path / "meta" / "escalations" / "s1.md").exists()


# ---------- idempotence ----------


def test_persist_idempotent_same_content_no_dup(tmp_path: Path) -> None:
    # C-4: re-running the same session+draft must NOT double the notes.
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(), _ctx("s1"))
    # remove manifest so the second run goes through the landing path (simulates
    # a partial prior run that wrote notes but not the manifest).
    _clear_manifests(tmp_path)
    persist_draft(store, _draft(), _ctx("s1"))
    assert len(store.load_notes()) == 1


def test_persist_rejudge_updates_mutable_not_new(tmp_path: Path) -> None:
    # D4: same source + same content (hash stable) but verification/pain
    # re-judged → UPDATE the existing note's mutable fields, do NOT create a
    # new one.
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(), _ctx("s1"))
    _clear_manifests(tmp_path)
    # re-judge: verification + pain change, content (title/summary/body/kind) same
    rejudged = _draft(notes=(_note(verification="verified", pain=8),))
    report = persist_draft(store, rejudged, _ctx("s1"))
    notes = store.load_notes()
    assert len(notes) == 1
    [n] = notes
    assert n.verification == "verified"
    assert n.pain == 8
    assert len(report.notes_created) == 0
    assert len(report.notes_updated) == 1


def test_persist_different_content_creates_new(tmp_path: Path) -> None:
    # content changed (new hash) → new note (the old one stays for 041 to supersede).
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(), _ctx("s1"))
    _clear_manifests(tmp_path)
    persist_draft(
        store,
        _draft(notes=(_note(title="完全不同的结论", body="新正文"),)),
        _ctx("s1"),
    )
    assert len(store.load_notes()) == 2


# ---------- manifest + atomic ----------


def test_persist_manifest_written_last(tmp_path: Path) -> None:
    # the completion manifest records the product ids and is written last.
    store = MemoryStore(tmp_path)
    report = persist_draft(store, _draft(), _ctx("s1"))
    manifest = _read_manifest(tmp_path, "s1:0:end")
    assert manifest is not None
    assert manifest["cc_session_id"] == "s1"
    assert manifest["segment_id"] == "s1:0:end"
    assert "episode_file" in manifest
    assert report.ok


def test_persist_manifest_intact_is_noop(tmp_path: Path) -> None:
    # spec 接口契约: re-run with an intact manifest → no-op (no new writes).
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(), _ctx("s1"))
    mtime_before = (tmp_path / "episodes" / "s1.md").stat().st_mtime_ns
    report = persist_draft(store, _draft(), _ctx("s1"))
    mtime_after = (tmp_path / "episodes" / "s1.md").stat().st_mtime_ns
    assert mtime_before == mtime_after  # episode file not rewritten
    assert len(report.notes_created) == 0
    assert len(report.notes_updated) == 0
    assert report.ok


def test_persist_mid_failure_leaves_no_manifest(tmp_path: Path, monkeypatch) -> None:
    # C-7: a mid-landing failure (episode write blows up) must NOT write the
    # manifest — so the session stays retryable and re-run lands exactly one each.
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(), _ctx("s1"))  # first lands cleanly
    _clear_manifests(tmp_path)  # force re-landing path

    def boom(_ctx, _d):
        raise OSError("disk full")

    monkeypatch.setattr(store, "write_episode", boom)
    with pytest.raises(OSError):
        persist_draft(store, _draft(), _ctx("s1"))
    # no manifest → re-run is allowed
    assert not list((tmp_path / "meta" / "persisted-segments").glob("*.json"))
    assert len(store.load_notes()) == 1  # the note from the first landing


# ---------- inherited 040 invariants ----------


def test_persist_inferred_untested_never_stable(tmp_path: Path) -> None:
    # C-6 (继承 040 C-2): inferred-untested lands as inferred-untested, never
    # upgraded. (slice-041: confidence removed — verification is the only
    # evidence axis, C-9.)
    store = MemoryStore(tmp_path)
    persist_draft(
        store,
        _draft(notes=(_note(verification="inferred-untested"),)),
        _ctx("s1"),
    )
    [n] = store.load_notes()
    assert n.verification == "inferred-untested"


def test_persist_never_writes_core(tmp_path: Path) -> None:
    # C-6 (继承 040 C-4): persist never creates/touches core.md.
    store = MemoryStore(tmp_path)
    persist_draft(store, _draft(), _ctx("s1"))
    assert not (tmp_path / "core.md").exists()
    assert store.load_core() == ""


def test_persist_verification_counts(tmp_path: Path) -> None:
    # 040 contract preserved: report still carries per-tier verification tally.
    store = MemoryStore(tmp_path)
    report = persist_draft(
        store,
        _draft(
            notes=(
                _note(title="a", verification="verified"),
                _note(title="b", verification="inferred-untested"),
                _note(title="c", verification="inferred-untested"),
            )
        ),
        _ctx("s1"),
    )
    assert report.verification_counts == {
        "verified": 1,
        "inferred-untested": 2,
    }


# ---------- helpers ----------


def _clear_manifests(root: Path) -> None:
    seg_dir = root / "meta" / "persisted-segments"
    if seg_dir.exists():
        for f in seg_dir.glob("*.json"):
            f.unlink()


def _read_manifest(root: Path, segment_id: str) -> dict | None:
    p = root / "meta" / "persisted-segments" / f"{segment_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))
