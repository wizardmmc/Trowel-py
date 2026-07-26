from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trowel_py.memory.store import MemoryStore
from trowel_py.model_os.default_work import DefaultWorkError, sample_sources


def _write_note(root, stem: str, *, updated: str, status: str = "active") -> None:
    notes = root / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    (notes / f"{stem}.md").write_text(
        "---\n"
        "type: note\n"
        f"title: {stem}\n"
        "summary: contact me@example.com at https://example.com/private\n"
        "created: 2026-07-25\n"
        f"updated: {updated}\n"
        f"status: {status}\n"
        f"memory_id: memory-{stem}\n"
        "content_hash: stale-frontmatter-hash\n"
        "---\n"
        "secret: token-value /Users/alice/private/project "
        "550e8400-e29b-41d4-a716-446655440000\n",
        encoding="utf-8",
    )


def test_samples_selected_recent_notes_and_hashes_actual_redacted_text(
    tmp_path,
) -> None:
    _write_note(tmp_path, "recent", updated="2026-07-25")

    sampled = sample_sources(
        MemoryStore(tmp_path),
        ("memory://notes/recent",),
        occurred_at=datetime(2026, 7, 26, 1, tzinfo=timezone.utc),
    )

    assert len(sampled) == 1
    source = sampled[0]
    assert source.uri_at_generation == "memory://notes/recent"
    assert source.memory_id == "memory-recent"
    assert source.updated == "2026-07-25"
    assert source.sampled_content_hash != "stale-frontmatter-hash"
    assert source.chars <= 900
    assert "token-value" not in source.text
    assert "alice" not in source.text
    assert "example.com" not in source.text
    assert "550e8400" not in source.text


def test_accepts_unicode_note_stems(tmp_path) -> None:
    _write_note(tmp_path, "路径顺序差异", updated="2026-07-25")
    sampled = sample_sources(
        MemoryStore(tmp_path),
        ("memory://notes/路径顺序差异",),
        occurred_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
    )
    assert sampled[0].memory_id == "memory-路径顺序差异"


@pytest.mark.parametrize(
    ("refs", "code"),
    [
        ((), "invalid_source_count"),
        (("memory://notes/a",) * 4, "invalid_source_count"),
        (("memory://notes/../core",), "source_missing"),
        (("memory://notes/a/b",), "source_missing"),
        (("file:///tmp/a",), "source_missing"),
    ],
)
def test_rejects_invalid_count_and_unsafe_uris(tmp_path, refs, code) -> None:
    with pytest.raises(DefaultWorkError) as raised:
        sample_sources(
            MemoryStore(tmp_path),
            refs,
            occurred_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
        )
    assert raised.value.code == code


@pytest.mark.parametrize(
    ("updated", "status", "code"),
    [
        ("2026-07-25", "retired", "source_inactive"),
        ("2026-07-24", "active", "source_not_recent"),
        ("2026-07-27", "active", "source_not_recent"),
        ("20260725", "active", "source_not_recent"),
        ("not-a-date", "active", "source_not_recent"),
    ],
)
def test_rejects_inactive_or_non_recent_notes(tmp_path, updated, status, code) -> None:
    _write_note(tmp_path, "note", updated=updated, status=status)
    with pytest.raises(DefaultWorkError) as raised:
        sample_sources(
            MemoryStore(tmp_path),
            ("memory://notes/note",),
            occurred_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
        )
    assert raised.value.code == code


def test_rejects_note_symlink_that_escapes_memory_root(tmp_path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("private", encoding="utf-8")
    notes = tmp_path / "memory" / "notes"
    notes.mkdir(parents=True)
    (notes / "escape.md").symlink_to(outside)

    with pytest.raises(DefaultWorkError) as raised:
        sample_sources(
            MemoryStore(tmp_path / "memory"),
            ("memory://notes/escape",),
            occurred_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
        )
    assert raised.value.code == "source_missing"
