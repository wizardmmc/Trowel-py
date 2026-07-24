"""离线检索评估的集合 precision/recall 与 ranked top-k 指标。"""

from __future__ import annotations

from collections.abc import Iterable


def precision(retrieved: Iterable[str], relevant: Iterable[str]) -> float:
    rel = set(relevant)
    got = set(retrieved)
    if not got:
        return 0.0
    return len(got & rel) / len(got)


def recall(retrieved: Iterable[str], relevant: Iterable[str]) -> float:
    rel = set(relevant)
    if not rel:
        return 0.0
    got = set(retrieved)
    return len(got & rel) / len(rel)


def precision_at_k(retrieved: list[str], relevant: Iterable[str], k: int) -> float:
    """只取前 k 项，但结果不足 k 时仍以 k 为分母；k <= 0 返回 0.0。"""
    if k <= 0:
        return 0.0
    rel = set(relevant)
    top = retrieved[:k]
    hits = sum(1 for r in top if r in rel)
    return hits / k
