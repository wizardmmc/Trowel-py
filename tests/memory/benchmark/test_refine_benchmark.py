"""slice-040 T14: layer-2 benchmarks — real agent, NOT in CI.

These spawn a real cc distillation agent (via CCHost) to judge QUALITY: does
step-7 flag the S4 false conclusion? does dual-track split correctly? They are
marked ``benchmark`` and excluded by default (pyproject ``addopts``). Run
explicitly:

    .venv/bin/python -m pytest -m benchmark tests/memory/benchmark/

They are skipped at runtime unless a real agent + fixture transcript are wired
up. The slice-040 end-to-end validation (run on a REAL day's sessions, in a
standalone terminal — NOT inside an interactive claude session, #46416) is the
strongest instance and the primary check.
"""
import pytest

pytestmark = pytest.mark.benchmark


def test_s4_false_conclusion_flagged_inferred_untested() -> None:
    """S4 case 1 (``docs/milestones/spike-s1/s4-cases.md``): the tcc-stall
    "two-tier threshold" conclusion was confident, passed review + 280 tests,
    but its root-cause hypothesis (GLM non-streaming → generation-period
    silence) was NEVER实测 — later falsified. The distillation agent MUST
    judge it ``verification=inferred-untested`` (or event-data-supported), NOT
    ``verified``.

    Pass criterion (DIRECTION, not wording): produced note's verification ∈
    {inferred-untested, event-data-supported}. FAIL only if verified.
    """
    pytest.skip(
        "benchmark: wire a real CCHost + S4 fixture transcript; see "
        "docs/milestones/spike-s1/s4-refine-benchmark.md"
    )


def test_dualtrack_split_no_knowledge_in_diary() -> None:
    """Real June-diary fragment (含元话语 "本质是 / 我想到") → knowledge content
    must land in notes/, events in diary/. Pass: no knowledge signal word in
    any diary body (allow rare mis-routes; tally, don't hard-fail on one)."""
    pytest.skip("benchmark: wire real CCHost + june-diary fixture")


def test_conflict_with_existing_note_marked() -> None:
    """A new note that conflicts with an existing one must list it in
    ``conflicts_with`` (step 6), not overwrite the existing note."""
    pytest.skip("benchmark: wire real CCHost + conflicting-notes fixture")


def test_pain_rank_monotonic() -> None:
    """S6 (待验): across sessions of escalating severity (small mistake →
    high-cost fix → irreversible loss), pain must be rank-monotonic
    (Spearman correlation, NOT exact values — pain is a semantic judgment)."""
    pytest.skip(
        "benchmark: wire real CCHost + graded-severity fixtures (S6 pending)"
    )
