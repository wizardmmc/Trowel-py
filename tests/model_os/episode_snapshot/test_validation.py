from __future__ import annotations

from dataclasses import replace

import pytest

from tests.model_os.episode_snapshot.support import full_snapshot
from trowel_py.model_os.episode_snapshot_codec import validate_snapshot
from trowel_py.model_os.types import EpisodeSnapshot, SideEffectRecord


class SnapshotValidationError(Exception):
    pass


def missing_evidence_side_effect() -> SideEffectRecord:
    return SideEffectRecord(
        action_ref="write",
        idempotency_key="idem",
        outcome="done",
    )


def test_validation_uses_strict_utf8_byte_limit() -> None:
    snapshot = full_snapshot()
    validate_snapshot(
        snapshot,
        "汉",
        max_payload_bytes=3,
        error_type=SnapshotValidationError,
    )
    with pytest.raises(
        SnapshotValidationError,
        match=(
            r"snapshot payload exceeds 3 bytes \(got 4\); "
            r"reduce content or reference instead of copying"
        ),
    ):
        validate_snapshot(
            snapshot,
            "汉a",
            max_payload_bytes=3,
            error_type=SnapshotValidationError,
        )


@pytest.mark.parametrize(
    ("snapshot", "message"),
    [
        (
            replace(
                full_snapshot(),
                next_steps=("1", "2", "3", "4"),
            ),
            "next_steps must have at most 3 items (got 4)",
        ),
        (
            replace(
                full_snapshot(),
                completed_with_evidence=(("", "evidence"),),
            ),
            (
                "completed_with_evidence entries must be non-empty "
                "(action_ref, evidence_ref)"
            ),
        ),
        (
            replace(
                full_snapshot(),
                side_effects=(missing_evidence_side_effect(),),
            ),
            (
                "side effect 'write' marked done without an evidence_ref; "
                "record it unknown_requires_reconcile instead"
            ),
        ),
    ],
)
def test_validation_keeps_exact_messages(
    snapshot: EpisodeSnapshot,
    message: str,
) -> None:
    with pytest.raises(SnapshotValidationError, match=".*") as raised:
        validate_snapshot(
            snapshot,
            "{}",
            max_payload_bytes=100,
            error_type=SnapshotValidationError,
        )
    assert str(raised.value) == message


def test_validation_stops_at_byte_limit() -> None:
    snapshot = replace(
        full_snapshot(),
        next_steps=("1", "2", "3", "4"),
        completed_with_evidence=(("", ""),),
    )
    with pytest.raises(SnapshotValidationError) as raised:
        validate_snapshot(
            snapshot,
            "oversized",
            max_payload_bytes=1,
            error_type=SnapshotValidationError,
        )
    assert str(raised.value).startswith("snapshot payload exceeds")


@pytest.mark.parametrize(
    ("snapshot", "message"),
    [
        (
            replace(
                full_snapshot(),
                next_steps=("1", "2", "3", "4"),
                completed_with_evidence=(("", ""),),
                side_effects=(missing_evidence_side_effect(),),
            ),
            "next_steps must have at most 3 items (got 4)",
        ),
        (
            replace(
                full_snapshot(),
                completed_with_evidence=(("", ""),),
                side_effects=(missing_evidence_side_effect(),),
            ),
            (
                "completed_with_evidence entries must be non-empty "
                "(action_ref, evidence_ref)"
            ),
        ),
        (
            replace(
                full_snapshot(),
                side_effects=(missing_evidence_side_effect(),),
            ),
            (
                "side effect 'write' marked done without an evidence_ref; "
                "record it unknown_requires_reconcile instead"
            ),
        ),
    ],
)
def test_validation_keeps_rule_order(
    snapshot: EpisodeSnapshot,
    message: str,
) -> None:
    with pytest.raises(SnapshotValidationError) as raised:
        validate_snapshot(
            snapshot,
            "{}",
            max_payload_bytes=100,
            error_type=SnapshotValidationError,
        )
    assert str(raised.value) == message
