"""tests for precision/recall metrics and the reflection template (slice-038 T4)."""
from __future__ import annotations

from trowel_py.memory import metrics
from trowel_py.memory.reflection import REFLECTION_PROMPT_TEMPLATE


def test_empty_retrieved() -> None:
    assert metrics.precision([], {"a"}) == 0.0
    assert metrics.recall([], {"a"}) == 0.0


def test_empty_relevant_recall_is_zero() -> None:
    # avoid div-by-zero: no relevant notes -> recall 0 by definition.
    assert metrics.recall(["a"], set()) == 0.0


def test_full_hit() -> None:
    assert metrics.precision(["a", "b"], {"a", "b"}) == 1.0
    assert metrics.recall(["a", "b"], {"a", "b"}) == 1.0


def test_partial() -> None:
    # retrieved {a,b,c}, relevant {a,d}: precision 1/3, recall 1/2.
    assert metrics.precision(["a", "b", "c"], {"a", "d"}) == 1 / 3
    assert metrics.recall(["a", "b", "c"], {"a", "d"}) == 1 / 2


def test_all_miss() -> None:
    assert metrics.precision(["x", "y"], {"a"}) == 0.0
    assert metrics.recall(["x", "y"], {"a"}) == 0.0


def test_retrieved_deduped() -> None:
    assert metrics.precision(["a", "a", "b"], {"a"}) == 1 / 2  # dedup -> {a,b}


def test_precision_at_k() -> None:
    # ranked [a,x,b]; top-2 = a,x -> 1 hit / 2.
    assert metrics.precision_at_k(["a", "x", "b"], {"a", "b"}, k=2) == 1 / 2
    assert metrics.precision_at_k(["a", "x", "b"], {"a", "b"}, k=1) == 1.0
    assert metrics.precision_at_k(["a", "x", "b"], {"a", "b"}, k=0) == 0.0


def test_reflection_template_pins_the_question() -> None:
    assert "已存在笔记" in REFLECTION_PROMPT_TEMPLATE
    assert "绕弯路" in REFLECTION_PROMPT_TEMPLATE
    # the three attribution branches must be named:
    assert "召回 miss" in REFLECTION_PROMPT_TEMPLATE
    assert "新颖问题" in REFLECTION_PROMPT_TEMPLATE
