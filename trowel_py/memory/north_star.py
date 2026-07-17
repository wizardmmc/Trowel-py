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


def memory_usage_metrics(root: Path | str) -> dict[str, Any]:
    """Three effectiveness indicators for memory usage (slice-053).

    Plugs the memory system's biggest blind spot: a search hit does not tell
    you whether the model USED the note, whether that use HELPED, or whether a
    relevant note SHOULD have been used but wasn't.

    - ``read_rate`` = read records / search-hit records, over USER sessions'
      access-log (hard). "search hits" = the returned candidates (mcp_server
      writes one search record per candidate, carrying memory_id+rank); the
      per-call summary record (no memory_id) is NOT a hit. Non-user sessions
      (review/distill/eval) are excluded (C-3). None when no search hits.
    - ``hit_quality`` = helpful / (helpful+harmful+unused), over judgement hits
      (soft, LLM-judged). None when no judged hits with a usable outcome.
    - ``recall_miss_rate`` = recall-miss count / judged-session count (soft),
      split by attribution (retrieval_miss / awareness_miss). None when no
      judgements. ``known_issue_repeat_rate`` is approximated by it (the strict
      definition stays a TODO — north_star.compute_north_star still returns
      None for the 041 metric; this soft proxy is what unblocks it).

    Args:
        root: the memory root directory.

    Returns:
        A metrics dict (rates are None when the denominator is zero).
    """
    from trowel_py.memory.access_log import read_access_log
    from trowel_py.memory.attribution import AttributionIndex
    from trowel_py.memory.judgements import load_all_judgement_reports

    root_path = Path(root)
    index = AttributionIndex.from_root(root_path)
    resolved = [
        (r, index.resolve(r.trowel_session_id, r.cc_session_id))
        for r in read_access_log(root_path)
    ]
    user_records = [r for r, a in resolved if a.is_user]
    # slice-061: attribution coverage (C-7 — unattributed counted, never guessed
    # into the user population). Historical empty-cc_session_id rows that now
    # resolve via a trowel binding re-enter the user denominator; rows with no
    # verifiable mapping stay unattributed and are excluded from read_rate.
    attributed = sum(1 for _r, a in resolved if a.attributed)
    total = len(resolved)
    unattributed = total - attributed
    coverage = round(attributed / total, 4) if total else None
    reads = sum(1 for r in user_records if r.action == "read")
    # "search 命中数" = returned candidates (one search record per candidate,
    # carrying memory_id+rank). The per-call summary record mcp_server writes
    # without a memory_id is a call, not a hit — counting it would understate
    # read_rate (one search = 1 summary + N candidate records).
    search_hits = sum(
        1 for r in user_records if r.action == "search" and r.memory_id
    )
    read_rate = round(reads / search_hits, 4) if search_hits else None

    reports = load_all_judgement_reports(root_path)
    hits = [h for report in reports for h in report.hits]
    helpful = sum(1 for h in hits if h.outcome == "helpful")
    harmful = sum(1 for h in hits if h.outcome == "harmful")
    unused = sum(1 for h in hits if h.outcome == "unused")
    outcome_denom = helpful + harmful + unused
    hit_quality = round(helpful / outcome_denom, 4) if outcome_denom else None

    all_miss = [m for report in reports for m in report.recall_miss]
    retrieval = sum(1 for m in all_miss if m.attribution == "retrieval_miss")
    awareness = sum(1 for m in all_miss if m.attribution == "awareness_miss")
    # slice-061: a cc session judged across multiple segments counts ONCE at
    # the session level (hits/miss still aggregate per segment above).
    judged_sessions = len({r.cc_session_id for r in reports})
    recall_miss_rate = (
        round(len(all_miss) / judged_sessions, 4) if judged_sessions else None
    )

    return {
        "reads": reads,
        "search_hits": search_hits,
        "read_rate": read_rate,
        "attributed": attributed,
        "unattributed": unattributed,
        "coverage": coverage,
        "hits_total": len(hits),
        "hits_helpful": helpful,
        "hits_harmful": harmful,
        "hits_unused": unused,
        "hit_quality": hit_quality,
        "recall_miss_total": len(all_miss),
        "retrieval_miss": retrieval,
        "awareness_miss": awareness,
        "judged_sessions": judged_sessions,
        "recall_miss_rate": recall_miss_rate,
        # soft proxy for the 041 north-star metric (strict def is a TODO).
        "known_issue_repeat_rate": recall_miss_rate,
    }
