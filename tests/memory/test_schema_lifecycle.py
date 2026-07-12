"""schema migration tests: note status/memory_id fields, deleted
retired/confidence, CoreStatus trial.

C-9: verification (evidence) + status (lifecycle) are the only two orthogonal
axes. ``retired:bool`` and ``confidence`` are removed — they redundantly
expressed what status / verification already carry.
"""
from __future__ import annotations

from dataclasses import fields

from trowel_py.memory.schema import validate_entry
from trowel_py.memory.types import Note, NoteStatus

_NOTE_FIELD_NAMES = {f.name for f in fields(Note)}


def test_note_has_memory_id_field() -> None:
    assert "memory_id" in _NOTE_FIELD_NAMES


def test_note_has_status_field_default_active() -> None:
    n = Note(type="note", title="x")
    assert n.status == "active"


def test_note_has_supersedes_fields() -> None:
    assert "supersedes" in _NOTE_FIELD_NAMES
    assert "superseded_by" in _NOTE_FIELD_NAMES


def test_note_has_validity_time_fields() -> None:
    assert "valid_from" in _NOTE_FIELD_NAMES
    assert "last_verified_at" in _NOTE_FIELD_NAMES


def test_note_has_helpful_harmful_refs() -> None:
    assert "helpful_refs" in _NOTE_FIELD_NAMES
    assert "harmful_refs" in _NOTE_FIELD_NAMES


def test_note_has_trigger_fields() -> None:
    assert "trigger" in _NOTE_FIELD_NAMES
    assert "do_not_use_when" in _NOTE_FIELD_NAMES


def test_note_has_sources_field() -> None:
    assert "sources" in _NOTE_FIELD_NAMES


def test_note_retired_field_deleted() -> None:
    # C-9: retired:bool removed (subsumed by status='retired')
    assert "retired" not in _NOTE_FIELD_NAMES


def test_note_confidence_field_deleted() -> None:
    # C-9: confidence removed (derived from verification at read time)
    assert "confidence" not in _NOTE_FIELD_NAMES


def test_note_status_literal_is_four_states() -> None:
    # candidate was removed (grill 2026-07-11: notes are for the model, not
    # human-reviewed, so no "awaiting confirmation" state)
    assert NoteStatus.__args__ == ("active", "contradicted", "superseded", "retired")


# ---------- validate_entry on status ----------


def test_validate_note_status_active_accepted() -> None:
    fm = {"type": "note", "title": "x", "verification": "verified", "status": "active"}
    assert validate_entry("note", fm).ok


def test_validate_note_status_superseded_accepted() -> None:
    fm = {"type": "note", "title": "x", "verification": "verified",
          "status": "superseded", "superseded_by": "abc-123"}
    assert validate_entry("note", fm).ok


def test_validate_note_status_retired_accepted() -> None:
    fm = {"type": "note", "title": "x", "verification": "verified", "status": "retired"}
    assert validate_entry("note", fm).ok


def test_validate_note_bad_status_rejected() -> None:
    fm = {"type": "note", "title": "x", "verification": "verified",
          "status": "candidate"}  # candidate no longer exists
    res = validate_entry("note", fm)
    assert not res.ok
    assert any("status" in e for e in res.errors)


def test_validate_note_status_absent_accepted() -> None:
    # legacy/migrate-in-progress notes may lack status; schema must not reject
    # (read layer defaults to 'active', migrate backfills it).
    fm = {"type": "note", "title": "x", "verification": "verified"}
    assert validate_entry("note", fm).ok


def test_validate_note_legacy_confidence_ignored() -> None:
    # C-9: confidence field removed; a legacy file still carrying it must NOT be
    # rejected (unknown fields are ignored) and must NOT be validated.
    fm = {"type": "note", "title": "x", "verification": "verified",
          "confidence": "wild-value"}
    assert validate_entry("note", fm).ok


def test_validate_note_legacy_retired_ignored() -> None:
    # legacy file with retired:true is not rejected at the schema layer; the
    # migrate command reads retired and writes status=retired.
    fm = {"type": "note", "title": "x", "verification": "verified", "retired": True}
    assert validate_entry("note", fm).ok


def test_validate_note_helpful_harmful_refs_must_be_int() -> None:
    fm = {"type": "note", "title": "x", "verification": "verified",
          "helpful_refs": "two", "harmful_refs": 1}
    res = validate_entry("note", fm)
    assert not res.ok
    assert any("helpful_refs" in e for e in res.errors)


def test_validate_note_supersedes_must_be_list() -> None:
    fm = {"type": "note", "title": "x", "verification": "verified",
          "supersedes": "not-a-list"}
    res = validate_entry("note", fm)
    assert not res.ok
    assert any("supersedes" in e for e in res.errors)


# ---------- CoreStatus trial (layer-one probation) ----------


def test_core_status_trial_accepted() -> None:
    # grill 2026-07-11: CoreStatus adds 'trial' (approve → trial → activate)
    fm = {
        "type": "core",
        "items": [
            {"id": "x", "imperative": "先查 memory", "scope": "high-risk",
             "status": "trial", "source": "monthly-promote"},
        ],
    }
    assert validate_entry("core", fm).ok


def test_core_status_seed_still_accepted() -> None:
    fm = {
        "type": "core",
        "items": [
            {"id": "x", "imperative": "先查", "status": "seed"},
        ],
    }
    assert validate_entry("core", fm).ok
