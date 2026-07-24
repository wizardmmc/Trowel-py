"""旧 note frontmatter 迁移与备份测试。"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest

from trowel_py.memory.migrate import migrate_memory

_LEGACY_NOTE = """\
---
type: note
title: {title}
tags: []
summary: a legacy note
confidence: evolving
created: '{created}'
updated: '{created}'
verification: event-data-supported
refs: 0
last_ref: ''
retired: {retired}
pain: 1
---
body of {title}
"""


def _write_legacy(
    root: Path,
    slug: str,
    title: str,
    *,
    retired: bool = False,
    created: str = "2026-07-01",
) -> Path:
    path = root / "notes" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _LEGACY_NOTE.format(title=title, created=created, retired=str(retired).lower()),
        encoding="utf-8",
    )
    return path


def _read_fm(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---")
    parts = text.split("---", 2)
    import yaml

    fm = yaml.safe_load(parts[1])
    assert isinstance(fm, dict)
    return fm


def test_migrate_dry_run_does_not_write(tmp_path: Path) -> None:
    _write_legacy(tmp_path, "a", "Note A")
    before = (tmp_path / "notes" / "a.md").read_text(encoding="utf-8")
    report = migrate_memory(tmp_path, apply=False)
    after = (tmp_path / "notes" / "a.md").read_text(encoding="utf-8")
    assert before == after
    assert report.scanned == 1
    assert report.migrated == 1
    assert report.skipped == 0
    assert report.backed_up is None


def test_migrate_apply_backs_up_root(tmp_path: Path) -> None:
    _write_legacy(tmp_path, "a", "Note A")
    report = migrate_memory(tmp_path, apply=True)
    assert report.backed_up is not None
    backup = Path(report.backed_up)
    assert backup.exists()
    bak_fm = _read_fm(backup / "notes" / "a.md")
    assert "confidence" in bak_fm
    assert "retired" in bak_fm


def test_migrate_apply_adds_memory_id_and_status(tmp_path: Path) -> None:
    _write_legacy(tmp_path, "a", "Note A")
    migrate_memory(tmp_path, apply=True)
    fm = _read_fm(tmp_path / "notes" / "a.md")
    assert fm.get("memory_id")
    uuid.UUID(fm["memory_id"])
    assert fm.get("status") == "active"
    assert fm.get("valid_from") == "2026-07-01"


def test_migrate_strips_confidence_and_retired(tmp_path: Path) -> None:
    _write_legacy(tmp_path, "a", "Note A", retired=False)
    migrate_memory(tmp_path, apply=True)
    fm = _read_fm(tmp_path / "notes" / "a.md")
    assert "confidence" not in fm
    assert "retired" not in fm


def test_migrate_retired_true_becomes_status_retired(tmp_path: Path) -> None:
    _write_legacy(tmp_path, "old", "Old Note", retired=True)
    migrate_memory(tmp_path, apply=True)
    fm = _read_fm(tmp_path / "notes" / "old.md")
    assert fm.get("status") == "retired"


def test_migrate_memory_ids_unique(tmp_path: Path) -> None:
    _write_legacy(tmp_path, "a", "Note A")
    _write_legacy(tmp_path, "b", "Note B")
    migrate_memory(tmp_path, apply=True)
    fm_a = _read_fm(tmp_path / "notes" / "a.md")
    fm_b = _read_fm(tmp_path / "notes" / "b.md")
    assert fm_a["memory_id"] != fm_b["memory_id"]


def test_migrate_apply_uses_suffix_when_timestamped_backup_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "memory"
    _write_legacy(root, "a", "Note A")
    timestamp = 1_700_000_000
    occupied = tmp_path / f"memory.bak-migrate-{timestamp}"
    occupied.mkdir()
    marker = occupied / "existing.txt"
    marker.write_text("existing backup", encoding="utf-8")
    monkeypatch.setattr(time, "time", lambda: timestamp)

    report = migrate_memory(root, apply=True)

    assert report.backed_up == str(tmp_path / f"memory.bak-migrate-{timestamp}-2")
    assert marker.read_text(encoding="utf-8") == "existing backup"


def test_migrate_idempotent_skips_already_migrated(tmp_path: Path) -> None:
    _write_legacy(tmp_path, "a", "Note A")
    migrate_memory(tmp_path, apply=True)
    report = migrate_memory(tmp_path, apply=True)
    assert report.scanned == 1
    assert report.migrated == 0
    assert report.skipped == 1


def test_migrate_preserves_body_and_other_fields(tmp_path: Path) -> None:
    _write_legacy(tmp_path, "a", "Note A")
    migrate_memory(tmp_path, apply=True)
    text = (tmp_path / "notes" / "a.md").read_text(encoding="utf-8")
    assert "body of Note A" in text
    fm = _read_fm(tmp_path / "notes" / "a.md")
    assert fm.get("title") == "Note A"
    assert fm.get("verification") == "event-data-supported"
    assert fm.get("pain") == 1


def test_migrate_dry_run_report_counts_mixed(tmp_path: Path) -> None:
    _write_legacy(tmp_path, "a", "A")
    migrate_memory(tmp_path, apply=True)
    _write_legacy(tmp_path, "b", "B")
    report = migrate_memory(tmp_path, apply=False)
    assert report.scanned == 2
    assert report.migrated == 1
    assert report.skipped == 1
