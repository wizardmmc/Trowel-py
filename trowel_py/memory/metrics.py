"""precision / recall metrics for the memory retrieval eval (slice-038).

Pure functions over id sets — no I/O, no LLM. ``retrieved`` is the set of notes
the model actually opened during one lookup; ``relevant`` is the ground-truth
set that should have been opened.

Note: spec接口契约 names these ``precision_at`` / ``recall_at``; the set-based
``precision`` / ``recall`` below ARE those functions (there is no k cut because
retrieval here is the set of opened notes, not a ranked list). ``precision_at_k``
is the ranked variant for top-k scoring.

- precision = |retrieved ∩ relevant| / |retrieved|  (low → noise / ownership表演)
- recall    = |retrieved ∩ relevant| / |relevant|   (low → 该用的没翻到 → 改目录结构)

Recall is the primary metric for the "is the directory structure good" question.
"""
from __future__ import annotations

from collections.abc import Iterable


def precision(retrieved: Iterable[str], relevant: Iterable[str]) -> float:
    """Precision over the (deduped) retrieved set vs the relevant set."""
    rel = set(relevant)
    got = set(retrieved)
    if not got:
        return 0.0
    return len(got & rel) / len(got)


def recall(retrieved: Iterable[str], relevant: Iterable[str]) -> float:
    """Recall over the retrieved set vs the relevant set (0 when no relevant)."""
    rel = set(relevant)
    if not rel:
        return 0.0
    got = set(retrieved)
    return len(got & rel) / len(rel)


def precision_at_k(retrieved: list[str], relevant: Iterable[str], k: int) -> float:
    """Precision of the top-``k`` ranked retrieved ids.

    Args:
        retrieved: ranked retrieved ids (order matters).
        relevant: ground-truth relevant id set.
        k: how many of the top retrieved to score.

    Returns:
        |top-k ∩ relevant| / k, or 0.0 when k <= 0.
    """
    if k <= 0:
        return 0.0
    rel = set(relevant)
    top = retrieved[:k]
    hits = sum(1 for r in top if r in rel)
    return hits / k
