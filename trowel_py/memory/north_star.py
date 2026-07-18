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
from typing import TYPE_CHECKING, Any

from trowel_py.memory.store import MemoryStore
from trowel_py.memory.tidy import HARMFUL_RETIRE_THRESHOLD

if TYPE_CHECKING:
    from trowel_py.memory.promotion_policy import PromotionPolicy


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


def memory_usage_metrics(
    root: Path | str,
    *,
    policy: "PromotionPolicy | None" = None,
    local_tz: Any | None = None,
) -> dict[str, Any]:
    """Coverage-aware memory-usage metrics (slice-065 §3).

    Four blocks, each reporting its numerator/denominator AND a
    ``reliable | partial | insufficient`` label whose thresholds come from the
    in-force policy (C-5 — a rate without coverage/sample size is never
    reported as reliable):

    - ``identity``: how many access records resolve to a real cc session
      (attributed vs unattributed, never guessed — slice-061 C-7).
    - ``retrieval``: user reads vs search hits (hard, from access-log).
    - ``effect``: session-level helpful/harmful/unused over BOTH outcome-log
      and segment judgement (slice-065 — helpful evidence no longer needs the
      model to call ``memory.outcome``), plus judge coverage.
    - ``recall``: recall-miss count over judged user sessions (soft), split by
      attribution.

    ``known_issue_repeat_rate`` is None — its strict definition needs an
    objective "this session failed AND the relevant note existed" label that
    does not exist yet (C-6); ``recall.recall_miss_rate`` is the soft proxy
    under its OWN name, no longer impersonating the strict metric.

    Args:
        root: the memory root directory.
        policy: the promotion policy supplying the quality thresholds (default
            ``default_policy()``). The policy in force is echoed in the output.
        local_tz: timezone for the day boundary (None → system local).

    Returns:
        A nested metrics dict (rates are None when the denominator is zero).
    """
    from trowel_py.memory.access_log import read_access_log
    from trowel_py.memory.attribution import AttributionIndex
    from trowel_py.memory.judgements import load_all_judgement_reports
    from trowel_py.memory.promotion_policy import default_policy
    from trowel_py.memory.recompute import compute_note_effects
    from trowel_py.memory.store import MemoryStore

    active_policy = policy or default_policy()
    root_path = Path(root)
    index = AttributionIndex.from_root(root_path)
    effects = compute_note_effects(root_path, local_tz=local_tz)

    # ---- identity: every access record attributed or not (slice-061 C-7) ----
    resolved = [
        (r, index.resolve(r.trowel_session_id, r.cc_session_id))
        for r in read_access_log(root_path)
    ]
    records_total = len(resolved)
    attributed = sum(1 for _r, a in resolved if a.attributed)
    unattributed = records_total - attributed
    coverage = round(attributed / records_total, 4) if records_total else None
    identity_quality = active_policy.identity_quality(coverage, records_total)

    # ---- retrieval: user-session reads vs search hits ----
    user_records = [r for r, a in resolved if a.is_user]
    reads = sum(1 for r in user_records if r.action == "read")
    # a "search hit" = a returned candidate (one record per candidate, carrying
    # memory_id+rank); the per-call summary record (no memory_id) is a call,
    # not a hit — counting it would understate read_rate.
    search_hits = sum(
        1 for r in user_records if r.action == "search" and r.memory_id
    )
    # a search CALL is the per-call summary record (no memory_id); the N
    # per-candidate records it produces are hits, not calls. Counting both
    # would report N+1 calls for one search.
    search_calls = sum(
        1 for r in user_records if r.action == "search" and not r.memory_id
    )
    read_sessions = len(
        {cc for eff in effects.values() for cc in eff.read_sessions}
    )
    read_rate = round(reads / search_hits, 4) if search_hits else None
    # retrieval inherits identity coverage (a read's attribution is the same
    # resolution path); the sample that backs read_rate is its denominator
    # (search_hits), NOT the numerator (reads) — a 0/20 rate with 20 hits is a
    # real measurement, not "insufficient".
    retrieval_quality = active_policy.identity_quality(coverage, search_hits)

    # ---- effect: session-level helpful/harmful (from compute_note_effects) +
    #      unused (judgement-only) over USER-judged segments ----
    helpful_sessions = sum(eff.helpful_refs for eff in effects.values())
    harmful_sessions = sum(eff.harmful_refs for eff in effects.values())
    # unused is read from the shared effect (it already folds outcome +
    # judgement unused, with helpful/harmful taking precedence in a session).
    unused_sessions = sum(eff.unused_refs for eff in effects.values())

    reports = load_all_judgement_reports(root_path)
    id_to_stem = {
        n.memory_id: stem
        for stem, n in MemoryStore(root_path).load_notes_with_id()
        if n.memory_id
    }
    judged_user_cc: set[str] = set()
    retrieval_miss = 0
    awareness_miss = 0
    for report in reports:
        cc = report.cc_session_id
        if not cc or not index.resolve("", cc).is_user:
            continue
        judged_user_cc.add(cc)
        for m in report.recall_miss:
            if id_to_stem.get(m.memory_id) is None:
                continue  # fabricated or since-deleted memory_id (C-6 parity)
            if m.attribution == "retrieval_miss":
                retrieval_miss += 1
            elif m.attribution == "awareness_miss":
                awareness_miss += 1
    effect_denom = helpful_sessions + harmful_sessions + unused_sessions
    hit_quality = (
        round(helpful_sessions / effect_denom, 4) if effect_denom else None
    )
    judged_user_segments = len(judged_user_cc)
    # eligible = the union of attributed user cc sessions (from access-log) and
    # judged user cc sessions (from reports). A judged session may have no
    # attributed access-log (its reads pre-date the binding), so the union —
    # not access-only — is the denominator that keeps coverage <= 1.0.
    access_user_cc = {attr.cc_session_id for _r, attr in resolved if attr.is_user}
    eligible_user_segments = len(access_user_cc | judged_user_cc)
    judgement_coverage = (
        round(judged_user_segments / eligible_user_segments, 4)
        if eligible_user_segments
        else None
    )
    effect_quality = active_policy.judgement_quality(
        judgement_coverage, judged_user_segments
    )

    # ---- recall: soft proxy, its own name (NOT known_issue_repeat_rate) ----
    recall_miss_total = retrieval_miss + awareness_miss
    recall_miss_rate = (
        round(recall_miss_total / judged_user_segments, 4)
        if judged_user_segments
        else None
    )
    recall_quality = active_policy.judgement_quality(
        judgement_coverage, judged_user_segments
    )

    return {
        "policy": active_policy.to_dict(),
        "identity": {
            "records_total": records_total,
            "attributed": attributed,
            "unattributed": unattributed,
            "coverage": coverage,
            "quality": identity_quality,
        },
        "retrieval": {
            "search_calls": search_calls,
            "search_hits": search_hits,
            "reads": reads,
            "read_sessions": read_sessions,
            "read_rate": read_rate,
            "read_rate_numerator": reads,
            "read_rate_denominator": search_hits,
            "quality": retrieval_quality,
        },
        "effect": {
            "judged_user_segments": judged_user_segments,
            "eligible_user_segments": eligible_user_segments,
            "judgement_coverage": judgement_coverage,
            "helpful_sessions": helpful_sessions,
            "harmful_sessions": harmful_sessions,
            "unused_sessions": unused_sessions,
            "hit_quality": hit_quality,
            "hit_quality_numerator": helpful_sessions,
            "hit_quality_denominator": effect_denom,
            "quality": effect_quality,
        },
        "recall": {
            "retrieval_miss": retrieval_miss,
            "awareness_miss": awareness_miss,
            "recall_miss_rate": recall_miss_rate,
            "recall_miss_rate_numerator": recall_miss_total,
            "recall_miss_rate_denominator": judged_user_segments,
            "quality": recall_quality,
        },
        # C-6: no objective session-failure ground truth yet.
        "known_issue_repeat_rate": None,
    }
