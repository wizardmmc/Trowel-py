"""检索 precision、recall 与 top-k 指标测试。"""

from __future__ import annotations

from trowel_py.memory import metrics


def test_empty_retrieved() -> None:
    assert metrics.precision([], {"a"}) == 0.0
    assert metrics.recall([], {"a"}) == 0.0


def test_empty_relevant_recall_is_zero() -> None:
    assert metrics.recall(["a"], set()) == 0.0


def test_full_hit() -> None:
    assert metrics.precision(["a", "b"], {"a", "b"}) == 1.0
    assert metrics.recall(["a", "b"], {"a", "b"}) == 1.0


def test_partial() -> None:
    assert metrics.precision(["a", "b", "c"], {"a", "d"}) == 1 / 3
    assert metrics.recall(["a", "b", "c"], {"a", "d"}) == 1 / 2


def test_all_miss() -> None:
    assert metrics.precision(["x", "y"], {"a"}) == 0.0
    assert metrics.recall(["x", "y"], {"a"}) == 0.0


def test_retrieved_deduped() -> None:
    assert metrics.precision(["a", "a", "b"], {"a"}) == 1 / 2


def test_precision_at_k() -> None:
    assert metrics.precision_at_k(["a", "x", "b"], {"a", "b"}, k=2) == 1 / 2
    assert metrics.precision_at_k(["a", "x", "b"], {"a", "b"}, k=1) == 1.0
    assert metrics.precision_at_k(["a", "x", "b"], {"a", "b"}, k=0) == 0.0
