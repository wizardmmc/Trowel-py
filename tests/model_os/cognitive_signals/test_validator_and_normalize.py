from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from trowel_py.model_os.cognitive_signals import (
    AttemptComparisonKey,
    AttemptRef,
    EvidenceRef,
    ExecutionOutcomePayload,
    PendingLostPayload,
    Reliability,
    SignalFamily,
    SignalNormalizationContext,
    ToolFailureCategory,
)
from trowel_py.model_os.signal_normalizer import normalize_and_classify
from trowel_py.model_os.validator_intent import (
    build_validator_outcome,
    match_validator_intent_from_argv,
    match_validator_intent_from_shell,
    normalize_validator_target,
    validator_persistence_argv,
)


FIXTURE_PATH = Path(__file__).parents[2] / "fixtures" / "model-os-signals-083.jsonl"


@pytest.mark.parametrize(
    "command,expected",
    [
        ("pytest -q tests/example.py", "pytest"),
        ("mypy trowel_py", "mypy"),
        ("ruff check trowel_py", "ruff"),
        ("tsc -b", "tsc"),
        (".venv/bin/python -m pytest -q tests/model_os", "pytest"),
        (".venv/bin/mypy trowel_py/model_os", "mypy"),
        (".venv/bin/ruff check trowel_py/model_os", "ruff"),
        ("bun run typecheck", "tsc"),
    ],
)
def test_seed_validator_registry_matches_structural_commands(
    command: str, expected: str
) -> None:
    intent = match_validator_intent_from_shell(command)
    assert intent is not None
    assert intent.intent_id == expected


@pytest.mark.parametrize(
    "argv,expected",
    [
        ((".venv/bin/python", "-m", "pytest", "-q", "tests/model_os"), "tests/model_os"),
        ((".venv/bin/ruff", "check", "trowel_py/model_os"), "trowel_py/model_os"),
        (("bun", "run", "typecheck"), "."),
        (("tsc", "--project", "web/tsconfig.json"), "web/tsconfig.json"),
    ],
)
def test_validator_target_is_derived_from_structural_argv(
    argv: tuple[str, ...], expected: str
) -> None:
    intent = match_validator_intent_from_argv(argv)
    assert intent is not None
    assert normalize_validator_target(intent, argv) == expected


@pytest.mark.parametrize(
    "command",
    [
        "pytest x; true",
        "pytest x && true",
        "pytest x || true",
        "pytest x | tee log",
        "pytest $(whoami)",
        "pytest `whoami`",
        "pytest x > log",
        "pytest x\ntrue",
        "bash -c 'pytest x'",
        "pytest --unknown x",
        "pytest -q=not-valid tests",
        "mypy --strict=yes trowel_py",
        "ruff check --fix=yes trowel_py",
        "tsc -b=yes",
        "python-wrapper -m pytest tests",
        "pytest 'my complete private conversation goes here'",
    ],
)
def test_shell_validator_match_fails_closed(command: str) -> None:
    assert match_validator_intent_from_shell(command) is None


def test_validator_outcome_rejects_cross_invocation_refs() -> None:
    intent = match_validator_intent_from_argv(("pytest", "tests/example.py"))
    assert intent is not None
    exit_ref = EvidenceRef("journal", "exit-1", invocation_id="item-1")
    crossed_output = EvidenceRef("journal", "output-2", invocation_id="item-2")
    assert build_validator_outcome(
        intent=intent,
        argv=("pytest", "tests/example.py"),
        exit_event_ref=exit_ref,
        output_ref=crossed_output,
        native_tool_item_id="item-1",
        exit_code=1,
        completed=True,
    ) is None
    assert build_validator_outcome(
        intent=intent,
        argv=("pytest", "tests/example.py"),
        exit_event_ref=exit_ref,
        output_ref=None,
        native_tool_item_id="item-1",
        exit_code=1,
        completed=True,
    ) is None


def test_validator_persistence_shape_contains_no_target_text() -> None:
    argv = ("pytest", "-q", "tests/private_topic.py")
    intent = match_validator_intent_from_argv(argv)
    assert intent is not None
    persisted = validator_persistence_argv(intent, argv)
    assert persisted is not None
    assert persisted[0] == "validator:pytest"
    assert persisted[1].startswith("argv_sha256:")
    assert "private_topic" not in " ".join(persisted)


def _context(runtime: str, scenario: str, ordinal: int) -> SignalNormalizationContext:
    attempt_id = f"{runtime}-{scenario}-{ordinal}"
    return SignalNormalizationContext(
        attempt_id=attempt_id,
        subject=AttemptRef(attempt_id),
        comparison_key=AttemptComparisonKey(
            runtime=runtime,
            attempt_category="turn:main",
            task_id=None,
            target_ref=scenario,
        ),
        evidence_refs=(
            EvidenceRef(
                namespace=runtime,
                ref_id=f"{scenario}-{ordinal}",
                runtime=runtime,
                invocation_id=attempt_id,
            ),
        ),
        native_event_id=f"{scenario}-{ordinal}",
        observation_ordinal=ordinal,
        observed_at="2026-07-25T01:00:00+00:00",
    )


def _fixture_records(runtime: str) -> list[dict[str, Any]]:
    records = [
        json.loads(line) for line in FIXTURE_PATH.read_text().splitlines() if line
    ]
    return [record for record in records if record["runtime"] == runtime]


def _atomic_observations(record: dict[str, Any]) -> list[dict[str, Any]]:
    return list(record["atoms"])


def _expected_tuple(runtime: str, scenario: str, atom: dict[str, Any]) -> tuple:
    if scenario in {"fresh", "context_usage", "compact", "model_switch"}:
        return (None, None, None, None)
    if scenario == "success":
        return (SignalFamily.TURN_OUTCOME, "success", None, Reliability.RELIABLE)
    if scenario == "failure" and atom.get("type") == "commandExecution":
        return (
            SignalFamily.EXECUTION_OBSERVATION,
            "tool_error",
            ToolFailureCategory.UNKNOWN,
            Reliability.RELIABLE,
        )
    if scenario == "failure":
        return (SignalFamily.TURN_OUTCOME, "failure", None, Reliability.RELIABLE)
    if scenario == "permission_wait":
        return (
            SignalFamily.EXECUTION_OBSERVATION,
            "tool_error",
            ToolFailureCategory.PERMISSION,
            Reliability.RELIABLE,
        )
    if scenario == "pending_restart":
        return (
            SignalFamily.EXECUTION_OBSERVATION,
            "pending_lost",
            None,
            Reliability.RELIABLE,
        )
    if atom.get("type") == "tool_result":
        return (
            SignalFamily.EXECUTION_OBSERVATION,
            "timeout",
            ToolFailureCategory.RESOURCE,
            Reliability.RELIABLE,
        )
    return (
        SignalFamily.EXECUTION_OBSERVATION,
        "retry",
        ToolFailureCategory.NETWORK,
        Reliability.RELIABLE if runtime == "cc" else Reliability.WEAK,
    )


@pytest.mark.parametrize("runtime", ["cc", "codex"])
def test_real_083_atoms_match_full_frozen_classification(runtime: str) -> None:
    seen: set[str] = set()
    ordinal = 0
    for record in _fixture_records(runtime):
        scenario = record["scenario"]
        seen.add(scenario)
        for atom in _atomic_observations(record):
            draft = normalize_and_classify(atom, _context(runtime, scenario, ordinal))
            expected = _expected_tuple(runtime, scenario, atom)
            ordinal += 1
            if expected[0] is None:
                assert draft is None
                continue
            assert draft is not None
            category = (
                draft.payload.category
                if isinstance(draft.payload, ExecutionOutcomePayload)
                else None
            )
            assert (
                draft.kind.family,
                draft.kind.subtype,
                category,
                draft.reliability,
            ) == expected
    assert seen == {
        "success",
        "failure",
        "retry_or_timeout",
        "permission_wait",
        "pending_restart",
        "fresh",
        "context_usage",
        "compact",
        "model_switch",
    }


def test_retry_payload_keeps_cc_fields_and_codex_unknowns() -> None:
    cc_record = next(
        item
        for item in _fixture_records("cc")
        if item["scenario"] == "retry_or_timeout"
        and item["atoms"][0].get("type") == "system"
    )
    codex_record = next(
        item
        for item in _fixture_records("codex")
        if item["scenario"] == "retry_or_timeout"
    )
    cc_atom = _atomic_observations(cc_record)[-1]
    codex_atom = _atomic_observations(codex_record)[0]
    cc_draft = normalize_and_classify(
        cc_atom, _context("cc", "retry_or_timeout", 0)
    )
    codex_draft = normalize_and_classify(
        codex_atom, _context("codex", "retry_or_timeout", 0)
    )
    assert cc_draft is not None and codex_draft is not None
    assert isinstance(cc_draft.payload, ExecutionOutcomePayload)
    assert isinstance(codex_draft.payload, ExecutionOutcomePayload)
    assert (
        cc_draft.payload.retry_attempt,
        cc_draft.payload.retry_max,
        cc_draft.payload.retry_delay_ms,
    ) == (6, 10, 16817.3812)
    assert (
        codex_draft.payload.retry_attempt,
        codex_draft.payload.retry_max,
        codex_draft.payload.retry_delay_ms,
    ) == (None, None, None)


@pytest.mark.parametrize(
    "resolution_state,effect_certainty,required_action",
    [
        ("requires_user_restart", "not_sent", "ask_user_again"),
        ("requires_reconcile", "unknown", "inspect_reality"),
    ],
)
def test_pending_lost_keeps_both_dispositions(
    resolution_state: str, effect_certainty: str, required_action: str
) -> None:
    context = _context("cc", "pending_restart", 0)
    context = SignalNormalizationContext(
        **{
            **context.__dict__,
            "pending_disposition": (
                resolution_state,
                effect_certainty,
                required_action,
            ),
        }
    )
    draft = normalize_and_classify({"type": "pending_lost"}, context)
    assert draft is not None
    assert isinstance(draft.payload, PendingLostPayload)
    assert (
        draft.payload.resolution_state,
        draft.payload.effect_certainty,
        draft.payload.required_action,
    ) == (resolution_state, effect_certainty, required_action)
