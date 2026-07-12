"""one-shot schema migration for legacy notes (slice-041).

Backfills the slice-041 fields onto notes written before this slice:
- ``memory_id``: fresh UUIDv7 (D1) — the stable identity that survives slug
  edits and threads the supersedes chain.
- ``status``: ``active`` by default; ``retired`` when the legacy file carried
  ``retired:true`` (C-9 — status subsumes the removed retired:bool).
- ``valid_from``: copied from ``created`` (when the conclusion became true).

Strips the removed fields (C-9): ``retired:bool`` and ``confidence`` (the
latter was a derived mirror of ``verification``).

Default is dry-run (reports what would change, writes nothing). ``--apply``
backs up the memory root to a timestamped sibling before writing. Idempotent:
notes already carrying ``memory_id`` are skipped. The filename (slug) is NEVER
renamed — memory_id is a frontmatter field, the slug stays the human-readable
index (D1, grill 2026-07-11).
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from trowel_py.memory.ids import uuid7
from trowel_py.memory.store import _dump_frontmatter, _split_frontmatter

_NOTES_DIR = "notes"
# fields removed by slice-041 (C-9): status subsumes retired; verification
# subsumes confidence. They are dropped from the frontmatter on migrate.
_REMOVED_FIELDS = ("retired", "confidence")


@dataclass(frozen=True)
class MigrateReport:
    """Outcome of one migrate pass.

    Attributes:
        scanned: total note files inspected.
        migrated: notes that needed (and, under apply, received) the backfill.
        skipped: notes already carrying ``memory_id`` (idempotent re-run).
        backed_up: backup directory path when ``apply=True`` and at least one
            note was migrated; None otherwise (no write → no backup needed).
    """

    scanned: int
    migrated: int
    skipped: int
    backed_up: str | None = None


def migrate_memory(root: Path | str, *, apply: bool) -> MigrateReport:
    """Backfill slice-041 fields onto legacy notes (idempotent).

    Args:
        root: the memory root directory.
        apply: False = dry-run (report only, no writes); True = back up the
            root then write the migrated notes in place.

    Returns:
        MigrateReport with scanned/migrated/skipped counts and the backup path.
    """
    root_path = Path(root)
    notes_dir = root_path / _NOTES_DIR
    if not notes_dir.exists():
        return MigrateReport(scanned=0, migrated=0, skipped=0, backed_up=None)

    # Plan first (no writes), so dry-run and apply share one read path.
    plans: list[tuple[Path, dict, str]] = []  # (path, new_fm, body)
    scanned = 0
    skipped = 0
    for p in sorted(notes_dir.glob("*.md")):
        scanned += 1
        text = p.read_text(encoding="utf-8")
        fm, body = _split_frontmatter(text)
        if fm is None or fm.get("type") != "note":
            continue  # not a note (hand-corrupted or non-note) — leave alone
        if fm.get("memory_id"):
            skipped += 1  # already migrated
            continue
        new_fm = {k: v for k, v in fm.items() if k not in _REMOVED_FIELDS}
        new_fm["memory_id"] = str(uuid7())
        # legacy retired:true → status=retired; else active (C-8/C-9).
        if fm.get("retired"):
            new_fm["status"] = "retired"
        else:
            new_fm["status"] = "active"
        new_fm["valid_from"] = str(fm.get("created", ""))
        plans.append((p, new_fm, body))

    if not apply or not plans:
        return MigrateReport(
            scanned=scanned, migrated=len(plans), skipped=skipped, backed_up=None
        )

    # apply: back up once, then write every planned note in place.
    # W6 (auto-cr): uniqued backup path — same-second re-runs won't crash on
    # FileExistsError (040-a repair.py hit this).
    import time

    base = root_path.with_name(
        root_path.name + f".bak-migrate-{int(time.time())}"
    )
    backup = base
    i = 2
    while backup.exists():
        backup = base.with_name(f"{base.name}-{i}")
        i += 1
    shutil.copytree(root_path, backup)
    for p, new_fm, body in plans:
        p.write_text(_dump_frontmatter(new_fm, body), encoding="utf-8")
    return MigrateReport(
        scanned=scanned, migrated=len(plans), skipped=skipped, backed_up=str(backup)
    )
