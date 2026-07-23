"""slice-040-a historical repair: backfill episodes from surviving drafts.

The P1 overwrite bug left each day's ``diary/daily/<date>.md`` with only the
LAST session's experience — the other sessions' raw events were lost from the
daily (though their distillation drafts survive in
``review-daily-work/<date>/<sid>/draft.json``).

This module replays those drafts into per-session ``episodes/<sid>.md`` files
and rebuilds the derived daily — WITHOUT re-running the distillation agent
(spec: 只读已有 draft，不重新跑 agent). It never touches notes (they already
landed during 040; repair only restores the experience track).

Safety: dry-run is the default; ``--apply`` backs up the memory root to a
timestamped sibling directory first (trowel CLAUDE.md 铁律: 动 memory 前先备份).
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from trowel_py.memory.draft import parse_draft
from trowel_py.memory.daily_review.workspace import review_workdir_root
from trowel_py.memory.sessions_repo import (
    create_sessions_repository,
    open_sessions_db,
)
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import PersistContext


@dataclass(frozen=True)
class RepairPlan:
    """One session's repair plan (dry-run row)."""

    cc_session_id: str
    has_draft: bool
    has_session_record: bool
    diary_dates: tuple[str, ...]


@dataclass(frozen=True)
class RepairReport:
    """Outcome of a repair (dry-run or apply)."""

    date: str
    applied: bool
    backup_dir: str | None
    planned: tuple[RepairPlan, ...]
    missing_drafts: tuple[str, ...]
    episodes_created: int
    daily_rebuilt: bool
    notes_before: int

    @property
    def ok(self) -> bool:
        """apply-side verification: episodes created == drafts that had a session."""
        if not self.applied:
            return True
        return self.episodes_created == sum(1 for p in self.planned if p.has_draft)


def _scan(memory_root: Path, date_str: str) -> tuple[list[RepairPlan], list[str], dict]:
    """Discover drafts + sessions for ``date_str``.

    Returns:
        (plans, missing_draft_sids, sessions_by_id).
    """
    review_root = review_workdir_root(memory_root) / date_str
    conn = open_sessions_db(memory_root)
    try:
        repo = create_sessions_repository(conn)
        sessions = {s.cc_session_id: s for s in repo.find_by_date(date_str)}
    finally:
        conn.close()

    plans: list[RepairPlan] = []
    draft_sids: set[str] = set()
    if review_root.exists():
        for dp in sorted(review_root.glob("*/draft.json")):
            sid = dp.parent.name
            draft_sids.add(sid)
            try:
                draft = parse_draft(dp.read_text(encoding="utf-8"))
                diary_dates = tuple(d.date for d in draft.diary)
                plans.append(
                    RepairPlan(
                        cc_session_id=sid,
                        has_draft=True,
                        has_session_record=sid in sessions,
                        diary_dates=diary_dates,
                    )
                )
            except (ValueError, OSError, AttributeError, TypeError):
                # unreadable / malformed draft (bad JSON, wrong-shaped notes, …)
                # → record as "no usable draft" for this sid; repair never
                # re-runs the agent, it just skips this one.
                plans.append(
                    RepairPlan(
                        cc_session_id=sid,
                        has_draft=False,
                        has_session_record=sid in sessions,
                        diary_dates=(),
                    )
                )
    missing = [sid for sid in sessions if sid not in draft_sids]
    return plans, missing, sessions


def _unique_backup_path(memory_root: Path, date_str: str) -> Path:
    """Return a non-clashing backup dir (uniqued on collision).

    A same-second re-run must not crash on ``FileExistsError`` — the backup is
    the safety net for touching memory, so its creation can never fail. Appends
    ``-2``, ``-3``, … when the timestamped name already exists.
    """
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = memory_root.parent / f"memory.bak-repair-{date_str}-{ts}"
    n = 2
    while base.exists():
        base = memory_root.parent / f"memory.bak-repair-{date_str}-{ts}-{n}"
        n += 1
    return base


def repair_memory(
    memory_root: Path, date_str: str, *, apply: bool = False
) -> RepairReport:
    """Backfill episodes + rebuild the daily from surviving drafts.

    Args:
        memory_root: the memory root (``~/.trowel/memory``).
        date_str: target day ``YYYY-MM-DD``.
        apply: False (default) = dry-run (no writes); True = backup then write.

    Returns:
        RepairReport. ``report.ok`` verifies episodes_created == draft count.
    """
    plans, missing, sessions = _scan(memory_root, date_str)
    notes_before = (
        len(list((memory_root / "notes").glob("*.md")))
        if (memory_root / "notes").exists()
        else 0
    )

    if not apply:
        return RepairReport(
            date=date_str,
            applied=False,
            backup_dir=None,
            planned=tuple(plans),
            missing_drafts=tuple(missing),
            episodes_created=0,
            daily_rebuilt=False,
            notes_before=notes_before,
        )

    # back up the memory root (铁律: 动 memory 前先备份). The backup path is
    # uniqued so a same-second re-run can't crash on FileExistsError — every
    # apply gets its own snapshot.
    backup = _unique_backup_path(memory_root, date_str)
    if memory_root.exists():
        shutil.copytree(memory_root, backup)

    store = MemoryStore(memory_root)
    review_root = review_workdir_root(memory_root) / date_str
    created = 0
    if review_root.exists():
        for dp in sorted(review_root.glob("*/draft.json")):
            sid = dp.parent.name
            try:
                draft = parse_draft(dp.read_text(encoding="utf-8"))
            except (ValueError, OSError, AttributeError, TypeError):
                continue
            s = sessions.get(sid)
            ctx = PersistContext(
                segment_id=f"{sid}:0:end",
                cc_session_id=sid,
                workdir=s.workdir if s else "",
                registered_at=s.registered_at if s else "",
                review_date=date_str,
                source_jsonl=s.jsonl_path if s else "",
            )
            store.write_episode(ctx, draft.diary)
            created += 1

    daily_date = store.derive_daily_from_episodes(date_str)
    notes_after = (
        len(list((memory_root / "notes").glob("*.md")))
        if (memory_root / "notes").exists()
        else 0
    )
    # repair must NEVER touch notes (they already landed during 040).
    assert notes_after == notes_before, "repair mutated notes — aborting"

    return RepairReport(
        date=date_str,
        applied=True,
        backup_dir=str(backup),
        planned=tuple(plans),
        missing_drafts=tuple(missing),
        episodes_created=created,
        daily_rebuilt=bool(daily_date),
        notes_before=notes_before,
    )
