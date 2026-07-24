"""Tidy 计划的快照、应用与回滚事务。"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

from trowel_py.memory.store import MemoryStore

from .models import TidyPlan
from .validation import (
    _REVISE_ALLOWED_FIELDS,
    _memory_id_to_stem,
    validate_plan,
)

_SNAPSHOTS_DIR = "meta/snapshots"


@contextlib.contextmanager
def _tidy_lock(root: Path):
    """串行化同一 memory root 的 recompute、快照和应用序列。"""
    if fcntl is None:
        yield
        return
    lock_path = root / "meta" / ".tidy.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def apply_plan(root: Path | str, plan: TidyPlan) -> dict[str, Any]:
    """校验并应用计划；任一步失败都会从应用前快照恢复 notes。"""
    root_path = Path(root)
    errors = validate_plan(root_path, plan)
    if errors:
        raise ValueError(f"invalid plan: {errors}")

    store = MemoryStore(root_path)
    id_map = _memory_id_to_stem(root_path)

    for op in plan.operations:
        stem = id_map[op.target]
        note = store.load_note(stem)
        if note is None:
            raise ValueError(f"stale: target {op.target} vanished before apply")
        expected = op.expected_revision or plan.source_snapshot.get(op.target)
        if expected and note.content_hash != expected:
            raise ValueError(
                f"stale: {op.target} changed (expected {expected}, "
                f"got {note.content_hash})"
            )

    snap_dir = root_path / _SNAPSHOTS_DIR / plan.plan_id
    snap_dir.parent.mkdir(parents=True, exist_ok=True)
    notes_src = root_path / "notes"
    snap_notes = snap_dir / "notes"
    if snap_notes.exists():
        shutil.rmtree(snap_notes)
    if notes_src.exists():
        shutil.copytree(notes_src, snap_notes)
    (snap_dir / "plan.json").write_text(
        json.dumps(_plan_to_dict(plan), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    applied: list[str] = []
    try:
        for op in plan.operations:
            stem = id_map[op.target]
            if op.type == "keep":
                applied.append(op.target)
                continue
            if op.type == "retire":
                store.update_note_fields(stem, {"status": "retired"})
            elif op.type == "revise":
                safe = {
                    key: value
                    for key, value in op.new_fields.items()
                    if key in _REVISE_ALLOWED_FIELDS
                }
                store.update_note_fields(stem, safe)
            elif op.type in ("supersede", "contradict"):
                store.update_note_fields(
                    stem,
                    {
                        "status": (
                            "superseded" if op.type == "supersede" else "contradicted"
                        ),
                        "superseded_by": op.by,
                    },
                )
                replacer_stem = id_map[op.by]
                replacer = store.load_note(replacer_stem)
                if replacer is not None:
                    new_super = tuple(sorted(set(replacer.supersedes) | {op.target}))
                    store.update_note_fields(replacer_stem, {"supersedes": new_super})
            elif op.type == "merge_sources":
                store.update_note_fields(
                    stem,
                    {"status": "superseded", "superseded_by": op.canonical},
                )
                canon_stem = id_map[op.canonical]
                canon = store.load_note(canon_stem)
                target = store.load_note(stem)
                if canon is not None and target is not None:
                    merged = tuple(sorted(set(canon.sources) | set(target.sources)))
                    store.update_note_fields(canon_stem, {"sources": merged})
            applied.append(op.target)
    except Exception:
        notes_dst = root_path / "notes"
        if (snap_dir / "notes").exists():
            if notes_dst.exists():
                shutil.rmtree(notes_dst)
            shutil.copytree(snap_dir / "notes", notes_dst)
        raise

    report = {
        "plan_id": plan.plan_id,
        "applied": applied,
        "operations": len(plan.operations),
    }
    (snap_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def rollback_plan(root: Path | str, plan_id: str) -> None:
    """从应用前快照恢复 notes，复制失败时保留回滚前状态。"""
    snap_dir = Path(root) / _SNAPSHOTS_DIR / plan_id
    notes_backup = snap_dir / "notes"
    if not notes_backup.exists():
        raise FileNotFoundError(f"no snapshot for plan {plan_id!r}")
    notes_dst = Path(root) / "notes"
    trash = notes_dst.with_name(notes_dst.name + ".trash")
    if notes_dst.exists():
        notes_dst.rename(trash)
    try:
        shutil.copytree(notes_backup, notes_dst)
    except Exception:
        if notes_dst.exists():
            shutil.rmtree(notes_dst)
        if trash.exists():
            trash.rename(notes_dst)
        raise
    if trash.exists():
        shutil.rmtree(trash)


def _plan_to_dict(plan: TidyPlan) -> dict[str, Any]:
    return {
        "plan_id": plan.plan_id,
        "source_snapshot": plan.source_snapshot,
        "operations": [
            {
                "type": op.type,
                "target": op.target,
                "reason": op.reason,
                "evidence": list(op.evidence),
                "expected_revision": op.expected_revision,
                "canonical": op.canonical,
                "by": op.by,
                "new_fields": op.new_fields,
            }
            for op in plan.operations
        ],
        "dictionary_rebuild_required": plan.dictionary_rebuild_required,
        "core_candidates": list(plan.core_candidates),
    }
