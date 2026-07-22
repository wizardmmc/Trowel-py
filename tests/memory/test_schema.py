"""tests for memory frontmatter schema validation (slice-038 T2).

The knowledge-note fixture uses REAL wiki/pages frontmatter (desensitized) to
prove the note schema is field-compatible with the existing 311-note corpus.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from trowel_py.memory.schema import validate_entry

WIKI_PAGES = Path(os.environ.get("TROWEL_WIKI_PAGES", ""))
HAS_WIKI = bool(os.environ.get("TROWEL_WIKI_PAGES")) and WIKI_PAGES.exists()


def test_note_valid_minimal() -> None:
    fm = {
        "type": "note",
        "title": "前端 build 不生效先查浏览器缓存",
        "tags": ["frontend", "build"],
        "summary": "build 没生效多半是浏览器缓存",
        "confidence": "evolving",
        "verification": "event-data-supported",
        "refs": 0,
        "last_ref": "",
        "retired": False,
        "pain": 2,
        "created": "2026-07-08",
        "updated": "2026-07-08",
    }
    res = validate_entry("note", fm)
    assert res.ok, res.errors


def test_note_missing_verification_rejected() -> None:
    # C-3: knowledge entry without `verification` must be rejected.
    fm = {"type": "note", "title": "x", "tags": [], "summary": "", "confidence": "draft"}
    res = validate_entry("note", fm)
    assert not res.ok
    assert any("verification" in e for e in res.errors)


def test_note_bad_verification_rejected() -> None:
    fm = {"type": "note", "title": "x", "verification": "probably-true"}
    res = validate_entry("note", fm)
    assert not res.ok
    assert any("verification" in e for e in res.errors)


def test_note_bad_confidence_silently_ignored() -> None:
    # slice-041 (C-9): confidence field removed. A legacy file still carrying
    # a confidence value must NOT be rejected — it is an unknown field now,
    # silently ignored (verification is the only evidence axis).
    fm = {"type": "note", "title": "x", "verification": "verified", "confidence": "wild"}
    res = validate_entry("note", fm)
    assert res.ok


def test_note_refs_must_be_int() -> None:
    fm = {"type": "note", "title": "x", "verification": "verified", "refs": "two"}
    res = validate_entry("note", fm)
    assert not res.ok


def test_diary_valid() -> None:
    fm = {"type": "diary", "date": "2026-07-08", "layer": "day", "period": "2026-07-08"}
    assert validate_entry("diary", fm).ok


def test_diary_bad_layer_rejected() -> None:
    fm = {"type": "diary", "date": "2026-07-08", "layer": "decade"}
    res = validate_entry("diary", fm)
    assert not res.ok


def test_core_valid() -> None:
    fm = {
        "type": "core",
        "items": [
            {"id": "lookup-first", "imperative": "先查 memory", "scope": "high-risk",
             "status": "seed", "source": "CLAUDE.md"},
        ],
    }
    assert validate_entry("core", fm).ok


def test_core_item_missing_id_rejected() -> None:
    fm = {"type": "core", "items": [{"imperative": "先查"}]}
    res = validate_entry("core", fm)
    assert not res.ok


def test_dictionary_l0_and_l1_valid() -> None:
    assert validate_entry("dictionary", {"type": "dictionary", "layer": "L0"}).ok
    assert validate_entry(
        "dictionary", {"type": "dictionary", "layer": "L1", "domain": "frontend"}
    ).ok


def test_dictionary_bad_layer_rejected() -> None:
    assert not validate_entry("dictionary", {"type": "dictionary", "layer": "L2"}).ok


def test_unknown_type_rejected() -> None:
    assert not validate_entry("mystery", {"type": "mystery"}).ok


# ---------- slice-040-a: kind field on notes (procedural memory) ----------


def test_note_kind_fact_accepted() -> None:
    # kind is optional but, when present, must be one of the allowed kinds.
    fm = {"type": "note", "title": "x", "verification": "verified", "kind": "fact"}
    assert validate_entry("note", fm).ok


def test_note_kind_procedure_accepted() -> None:
    fm = {"type": "note", "title": "x", "verification": "verified", "kind": "procedure"}
    assert validate_entry("note", fm).ok


def test_note_bad_kind_rejected() -> None:
    fm = {"type": "note", "title": "x", "verification": "verified", "kind": "rule-of-thumb"}
    res = validate_entry("note", fm)
    assert not res.ok
    assert any("kind" in e for e in res.errors)


def test_note_kind_absent_accepted() -> None:
    # backward compat: the existing 45 notes carry no `kind` field; the schema
    # must still accept them (defaults to `fact` at the read/persist layer).
    fm = {"type": "note", "title": "x", "verification": "verified"}
    assert validate_entry("note", fm).ok


@pytest.mark.skipif(not HAS_WIKI, reason="wiki/pages corpus not present on this machine")
def test_note_schema_accepts_real_wiki_frontmatter() -> None:
    # C-2/compat: take real wiki frontmatter (the shared field subset) + add the
    # memory-only extension (verification), and the note validator must accept.
    candidates = sorted(WIKI_PAGES.glob("*.md"))
    assert candidates, "wiki/pages unexpectedly empty"
    used = 0
    for path in candidates:
        fm = _read_frontmatter(path)
        if fm is None or "title" not in fm:
            continue
        adapted = dict(fm)
        adapted["type"] = "note"
        adapted.setdefault("verification", "inferred-untested")
        res = validate_entry("note", adapted)
        assert res.ok, f"wiki note {path.name} rejected: {res.errors}"
        used += 1
        if used >= 12:
            break
    assert used > 0, "no wiki page had usable frontmatter"


def _read_frontmatter(path: Path) -> dict | None:
    """Parse the leading ``---`` YAML block; None if absent/malformed."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        loaded = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None
    return loaded if isinstance(loaded, dict) else None
