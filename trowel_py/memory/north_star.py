"""north-star metrics for the memory system (slice-041).

Two metrics, both approximations from the logs (C-10 — logs are truth):

- ``harmful_memory_rate`` ≈ (notes contradicted/superseded + notes with
  harmful_refs≥threshold) / active notes. Measures how much of the active
  corpus is being flagged as wrong/harmful (correction + retirement signal).
- ``known_issue_repeat_rate`` = None for now — needs session-level outcome
  alignment (did a session err without reading the relevant memory?). The
  raw material (access-log reads + outcome-log harmful) is returned so the
  metric can be wired when session outcome tracking lands.

``trowel memory metrics`` prints this as JSON. (``metrics.py`` is the 038
retrieval precision/recall module — different concern, left untouched.)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from trowel_py.memory.store import MemoryStore
from trowel_py.memory.tidy import HARMFUL_RETIRE_THRESHOLD


def compute_north_star(
    root: Path | str, *, today: str | None = None
) -> dict[str, Any]:
    """Compute the north-star approximations from the memory tree + logs.

    Args:
        root: the memory root directory.
        today: ISO date (for period scoping); None uses the wall clock.

    Returns:
        A metrics dict. ``known_issue_repeat_rate`` is None until session
        outcome alignment lands (TODO).
    """
    from datetime import date as _date

    from trowel_py.memory.access_log import read_access_log, read_outcome_log

    today_str = today or _date.today().isoformat()
    store = MemoryStore(root)
    all_notes = list(store.load_notes_with_id())
    active = [n for _s, n in all_notes if n.status == "active"]
    # W6 (codex): numerator and denominator share the same population — all
    # non-retired notes (active + contradicted + superseded). Counting
    # historical contradicted/superseded in the numerator while dividing by
    # current active let the rate exceed 1.0 as corrections accumulate.
    non_retired = [n for _s, n in all_notes if n.status != "retired"]
    contradicted_superseded = [
        n for n in non_retired if n.status in ("contradicted", "superseded")
    ]
    harmful_high = [
        n for n in non_retired if n.harmful_refs >= HARMFUL_RETIRE_THRESHOLD
    ]
    denom = max(len(non_retired), 1)
    # W3 (auto-cr): set union — a note can be BOTH contradicted AND harmful_high;
    # count it once. With W6's shared population, harmful_set ⊆ non_retired so
    # the rate is bounded by 1.0.
    harmful_set = {n.memory_id for n in contradicted_superseded if n.memory_id} | {
        n.memory_id for n in harmful_high if n.memory_id
    }
    harmful_rate = len(harmful_set) / denom

    reads = sum(1 for r in read_access_log(root) if r.action == "read")
    harmful_outcomes = sum(
        1 for r in read_outcome_log(root) if r.outcome == "harmful"
    )

    return {
        "as_of": today_str,
        "harmful_memory_rate": round(harmful_rate, 4),
        "active_notes": len(active),
        "contradicted_or_superseded": len(contradicted_superseded),
        "harmful_high_notes": len(harmful_high),
        "harmful_threshold": HARMFUL_RETIRE_THRESHOLD,
        # TODO(041): align session outcomes with access-log to compute the
        # repeat rate (sessions that erred without reading relevant memory).
        "known_issue_repeat_rate": None,
        "raw_reads": reads,
        "raw_harmful_outcomes": harmful_outcomes,
    }
