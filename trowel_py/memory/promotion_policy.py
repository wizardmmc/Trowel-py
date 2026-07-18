"""configurable promotion policy for layer-one core candidates (slice-065 §4).

The promotion gate is an explicit, serializable, overridable config — NOT
scattered code constants. ``default_policy()`` is the conservative baseline
(helpful evidence from >=3 independent user sessions, >=2 distinct days, no
harmful counter-evidence, tested provenance). CLI/report always print the
policy in force so a reader never has to guess which thresholds produced a
candidate — or the absence of one.

The policy also carries the coverage/sample thresholds that label a metric
``reliable | partial | insufficient`` (C-5 — a rate without coverage/sample
size is never reported as reliable). ``quality_label`` is the single place
that maps (coverage, sample) -> label, so the same rule labels every metric.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

QualityLabel = Literal["reliable", "partial", "insufficient"]

#: stamped on every candidate; bump when the default policy changes shape so an
#: old candidate names the policy that produced it (C-7 replayability).
_POLICY_VERSION = "slice-065-2026-07-18"


@dataclass(frozen=True)
class PromotionPolicy:
    """The promotion gate + metric quality thresholds (slice-065 §4).

    Attributes:
        version: stamp recorded on candidates; a policy change is traceable.
        allowed_kinds: note kinds eligible for layer one (gotcha + procedure).
        allowed_verification: provenance that may promote. ``inferred-untested``
            is NEVER allowed — a candidate must rest on tested evidence.
        min_helpful_sessions: independent USER cc sessions with a helpful
            session-level effect (C-2 — one session re-reading 10x counts once).
        max_harmful_sessions: upper bound on harmful sessions; 0 means ANY
            harmful counter-evidence blocks promotion (C-3).
        min_distinct_days: helpful evidence must span >= N distinct calendar
            days so a single day's traffic cannot promote a note.
        min_identity_coverage_reliable / min_identity_sample_reliable:
            coverage + sample an identity metric needs to be labelled reliable.
        min_judgement_coverage_reliable / min_judgement_sample_reliable:
            same for the judgement-coverage metric.
    """

    version: str = _POLICY_VERSION
    allowed_kinds: tuple[str, ...] = ("gotcha", "procedure")
    allowed_verification: tuple[str, ...] = ("verified", "event-data-supported")
    min_helpful_sessions: int = 3
    max_harmful_sessions: int = 0
    min_distinct_days: int = 2
    min_identity_coverage_reliable: float = 0.8
    min_identity_sample_reliable: int = 20
    min_judgement_coverage_reliable: float = 0.5
    min_judgement_sample_reliable: int = 5

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # dataclass -> dict turns tuples into tuples; JSON callers pass lists,
        # so normalize to lists here for a stable on-disk shape.
        d["allowed_kinds"] = list(self.allowed_kinds)
        d["allowed_verification"] = list(self.allowed_verification)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "PromotionPolicy":
        """Build a policy, letting ``d`` override the defaults (partial ok).

        Unknown keys are ignored (a stale config file must not crash a newer
        code version); list values for the tuple fields are normalized.
        """
        base = cls()
        if not d:
            return base
        names = {f.name for f in fields(cls)}
        kwargs: dict[str, Any] = {}
        for key, val in d.items():
            if key not in names:
                logger.debug("ignoring unknown policy key %r", key)
                continue
            if key in ("allowed_kinds", "allowed_verification"):
                if isinstance(val, list):
                    val = tuple(val)
                # §4: inferred-untested must NEVER be allowed — a candidate
                # rests on tested evidence. Override that tries to re-enable it
                # is rejected loudly (load_policy then falls back to default).
                if key == "allowed_verification" and "inferred-untested" in val:
                    raise ValueError(
                        "inferred-untested must never be allowed_verification (C-7)"
                    )
            kwargs[key] = val
        return replace(base, **kwargs)

    def identity_quality(
        self, coverage: float | None, sample: int
    ) -> QualityLabel:
        """Label an identity metric reliable|partial|insufficient (C-5)."""
        return quality_label(
            coverage,
            sample,
            min_coverage_reliable=self.min_identity_coverage_reliable,
            min_sample_reliable=self.min_identity_sample_reliable,
        )

    def judgement_quality(
        self, coverage: float | None, sample: int
    ) -> QualityLabel:
        """Label a judgement-coverage metric reliable|partial|insufficient."""
        return quality_label(
            coverage,
            sample,
            min_coverage_reliable=self.min_judgement_coverage_reliable,
            min_sample_reliable=self.min_judgement_sample_reliable,
        )


def quality_label(
    coverage: float | None,
    sample: int,
    *,
    min_coverage_reliable: float,
    min_sample_reliable: int,
) -> QualityLabel:
    """Map (coverage, sample) -> reliable | partial | insufficient.

    - insufficient: no samples at all (the metric has nothing to say).
    - reliable: coverage AND sample both clear their thresholds.
    - partial: there is data, but not enough coverage or sample to trust the
      rate as a firm conclusion (C-5 — a trend number, flagged).
    """
    if sample <= 0:
        return "insufficient"
    if (
        coverage is not None
        and coverage >= min_coverage_reliable
        and sample >= min_sample_reliable
    ):
        return "reliable"
    return "partial"


def default_policy() -> PromotionPolicy:
    """The conservative baseline policy (slice-065 §4 initial values)."""
    return PromotionPolicy()


def load_policy(path: Path | str) -> PromotionPolicy:
    """Load a policy from JSON, falling back to the default when absent/unreadable.

    A corrupt policy file must NOT crash the caller (a monthly run should keep
    the default gate, not die) — the failure is logged and the default returned.
    """
    p = Path(path)
    if not p.exists():
        return default_policy()
    try:
        return PromotionPolicy.from_dict(
            json.loads(p.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError) as exc:
        logger.warning("policy %s unreadable (%s); using default", p, exc)
        return default_policy()


def save_policy(policy: PromotionPolicy, path: Path | str) -> None:
    """Persist a policy as JSON (for replay/override)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(policy.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
