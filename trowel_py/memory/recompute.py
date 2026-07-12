"""rebuild note count caches from the access/outcome logs (slice-041 C-10).

The logs (``meta/access-log.jsonl`` + ``meta/outcome-log.jsonl``) are the source
of truth; the ``refs`` / ``helpful_refs`` / ``harmful_refs`` / ``last_ref``
fields on each Note are rebuildable caches. ``recompute_counters`` is run by
tidy before any retirement decision so the numbers are exact, not stale.

C-1 (040-c): only ``action=read`` counts as retrieved (and increments refs);
``action=search`` (candidate returned) does NOT. C-6: an unvoted read is
``unknown`` — never silently counted as helpful.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from trowel_py.memory.access_log import read_access_log, read_outcome_log
from trowel_py.memory.store import MemoryStore


def recompute_counters(root: Path | str) -> dict[str, Any]:
    """Rebuild every note's count fields from the logs.

    Args:
        root: the memory root directory.

    Returns:
        A report dict: ``updated`` (notes rewritten), ``refs_total`` (sum of
        all read events), ``helpful_total``, ``harmful_total``.
    """
    root_path = Path(root)
    store = MemoryStore(root_path)

    # refs + last_ref from access-log (action=read only — C-1).
    # NOTE: the access-log ``memory_id`` field holds the note's STEM (040-c's
    # handle_read logs note_id=stem from the URI), NOT the UUIDv7 memory_id.
    # So ``refs``/``last_ref`` are keyed by stem, and ``load_note(stem)``
    # reaches the file. If the log ever switches to UUIDv7, build a
    # {memory_id: stem} map here first (like tidy._memory_id_to_stem).
    refs: dict[str, int] = defaultdict(int)
    last_ref: dict[str, str] = {}
    for rec in read_access_log(root_path):
        if rec.action != "read" or not rec.memory_id:
            continue
        refs[rec.memory_id] += 1
        day = rec.ts[:10]  # ISO date prefix
        prev = last_ref.get(rec.memory_id)
        if prev is None or day > prev:
            last_ref[rec.memory_id] = day

    # helpful / harmful from outcome-log (unknown/unused ignored — C-6).
    helpful: dict[str, int] = defaultdict(int)
    harmful: dict[str, int] = defaultdict(int)
    for rec in read_outcome_log(root_path):
        if not rec.memory_id:
            continue
        if rec.outcome == "helpful":
            helpful[rec.memory_id] += 1
        elif rec.outcome == "harmful":
            harmful[rec.memory_id] += 1

    # write back to each note that has ANY log activity. Notes with no log
    # activity but a stale non-zero cache get reset to 0 too (C-10 truth).
    touched = set(refs) | set(helpful) | set(harmful)
    # also reset stale caches on notes that have a non-zero cache but no logs
    for nid, note in store.load_notes_with_id():
        if nid in touched:
            continue
        if note.refs or note.helpful_refs or note.harmful_refs:
            touched.add(nid)

    updated = 0
    for nid in touched:
        if store.load_note(nid) is None:
            continue  # log references a deleted note — skip, don't crash
        store.update_note_fields(
            nid,
            {
                "refs": refs.get(nid, 0),
                "helpful_refs": helpful.get(nid, 0),
                "harmful_refs": harmful.get(nid, 0),
                "last_ref": last_ref.get(nid, ""),
            },
        )
        updated += 1

    return {
        "updated": updated,
        "refs_total": sum(refs.values()),
        "helpful_total": sum(helpful.values()),
        "harmful_total": sum(harmful.values()),
    }
