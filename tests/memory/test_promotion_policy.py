"""slice-065 promotion policy: explicit, serializable, overridable gate + the
coverage/sample thresholds that label a metric reliable|partial|insufficient."""
from __future__ import annotations

from pathlib import Path

from trowel_py.memory.promotion_policy import (
    PromotionPolicy,
    default_policy,
    load_policy,
    quality_label,
    save_policy,
)


def test_default_policy_values() -> None:
    p = default_policy()
    assert p.allowed_kinds == ("gotcha", "procedure")
    assert p.allowed_verification == ("verified", "event-data-supported")
    assert p.min_helpful_sessions == 3
    assert p.max_harmful_sessions == 0
    assert p.min_distinct_days == 2


def test_inferred_untested_never_in_default_verification() -> None:
    # §4: inferred-untested must never be allowed — a candidate rests on tested evidence.
    assert "inferred-untested" not in default_policy().allowed_verification


def test_to_from_dict_roundtrip() -> None:
    p = default_policy()
    assert PromotionPolicy.from_dict(p.to_dict()) == p


def test_from_dict_partial_override_keeps_other_defaults() -> None:
    p = PromotionPolicy.from_dict({"min_helpful_sessions": 1})
    assert p.min_helpful_sessions == 1
    assert p.min_distinct_days == 2  # untouched default


def test_from_dict_ignores_unknown_keys() -> None:
    p = PromotionPolicy.from_dict({"bogus": 1, "min_distinct_days": 5})
    assert p.min_distinct_days == 5


def test_from_dict_normalizes_lists_to_tuples() -> None:
    # JSON round-trip turns tuples into lists; from_dict must accept both.
    p = PromotionPolicy.from_dict({"allowed_kinds": ["gotcha"]})
    assert p.allowed_kinds == ("gotcha",)


def test_load_save_policy_roundtrip(tmp_path: Path) -> None:
    p = PromotionPolicy.from_dict({"min_helpful_sessions": 2})
    path = tmp_path / "policy.json"
    save_policy(p, path)
    assert load_policy(path) == p


def test_load_policy_missing_file_returns_default(tmp_path: Path) -> None:
    assert load_policy(tmp_path / "nope.json") == default_policy()


def test_quality_label_reliable() -> None:
    assert quality_label(
        0.9, 100, min_coverage_reliable=0.8, min_sample_reliable=20
    ) == "reliable"


def test_quality_label_partial_when_sample_low() -> None:
    # coverage fine but too few samples → not yet reliable, but not empty either.
    assert quality_label(
        1.0, 3, min_coverage_reliable=0.8, min_sample_reliable=20
    ) == "partial"


def test_quality_label_partial_when_coverage_low() -> None:
    assert quality_label(
        0.3, 100, min_coverage_reliable=0.8, min_sample_reliable=20
    ) == "partial"


def test_quality_label_insufficient_when_no_sample() -> None:
    assert quality_label(
        None, 0, min_coverage_reliable=0.8, min_sample_reliable=20
    ) == "insufficient"


def test_policy_identity_quality_uses_policy_thresholds() -> None:
    p = PromotionPolicy(
        min_identity_coverage_reliable=0.5, min_identity_sample_reliable=10
    )
    assert p.identity_quality(0.6, 12) == "reliable"
    assert p.identity_quality(0.6, 5) == "partial"
    assert p.identity_quality(None, 0) == "insufficient"


def test_policy_judgement_quality_uses_policy_thresholds() -> None:
    p = PromotionPolicy(
        min_judgement_coverage_reliable=0.5, min_judgement_sample_reliable=5
    )
    assert p.judgement_quality(0.6, 6) == "reliable"
    assert p.judgement_quality(0.2, 6) == "partial"
