from __future__ import annotations

import pytest

from tests.model_os.episode_snapshot.support import full_snapshot
from trowel_py.model_os.episode_snapshot_codec import (
    pending_from_payload,
    pending_to_payload,
    snapshot_from_payload,
    snapshot_to_payload,
)
from trowel_py.model_os.types import (
    ArtifactRef,
    EpisodeSnapshot,
    PendingDescriptor,
    SideEffectRecord,
    SnapshotRef,
    SnapshotSource,
    WaitingSubtype,
)


def decode(payload: dict) -> EpisodeSnapshot:
    return snapshot_from_payload(
        payload,
        decode_pending=lambda pending: pending_from_payload(
            pending,
            pending_type=PendingDescriptor,
            waiting_subtype=WaitingSubtype,
        ),
        snapshot_type=EpisodeSnapshot,
        side_effect_type=SideEffectRecord,
        artifact_type=ArtifactRef,
        snapshot_ref_type=SnapshotRef,
        snapshot_source=SnapshotSource,
    )


def test_full_snapshot_exact_payload_and_round_trip() -> None:
    snapshot = full_snapshot()
    payload = snapshot_to_payload(
        snapshot,
        encode_pending=pending_to_payload,
    )

    assert payload == {
        "work_item_goal": "整理候选方案",
        "task_constraints_ref": None,
        "current_judgment": "方案可继续验证",
        "completed_with_evidence": [
            ["action-1", "evidence-1"],
            ["action-2", "evidence-2"],
        ],
        "side_effects": [
            {
                "action_ref": "write-1",
                "idempotency_key": "idem-1",
                "outcome": "done",
                "evidence_ref": "receipt-1",
            },
            {
                "action_ref": "write-2",
                "idempotency_key": "idem-2",
                "outcome": "unknown_requires_reconcile",
                "evidence_ref": None,
            },
        ],
        "unknowns": ["边界条件待确认"],
        "waiting_condition": {
            "kind": "approval",
            "native_generation": None,
            "correlation_id": "corr-1",
            "cause": "需要确认",
            "posed_at": "2026-07-23T00:00:00Z",
        },
        "next_steps": ["核对结果", "形成结论"],
        "artifacts": [{"kind": "report", "ref": "artifact-1"}],
        "native_transcript_ref": None,
        "source": "recovery_partial",
        "journal_through_seq": 12,
        "base_snapshot_ref": {
            "episode_id": "episode-base",
            "version": 3,
            "committed_event_id": "event-base",
            "payload_hash": "hash-base",
        },
    }
    assert decode(payload) == snapshot


def test_decoder_keeps_historical_defaults_and_ignores_unknown_keys() -> None:
    snapshot = decode(
        {
            "unknown_field": "ignored",
            "waiting_condition": {},
            "base_snapshot_ref": {},
        }
    )

    assert snapshot == EpisodeSnapshot(
        work_item_goal="",
        task_constraints_ref=None,
        current_judgment="unknown",
        completed_with_evidence=(),
        side_effects=(),
        unknowns=(),
        waiting_condition=None,
        next_steps=(),
        artifacts=(),
        native_transcript_ref=None,
        source=SnapshotSource.COOPERATIVE,
        journal_through_seq=0,
        base_snapshot_ref=None,
    )


@pytest.mark.parametrize(
    ("payload", "exception"),
    [
        ({"waiting_condition": {"correlation_id": "x"}}, KeyError),
        (
            {
                "waiting_condition": {
                    "kind": "invalid",
                    "correlation_id": "x",
                    "posed_at": "now",
                }
            },
            ValueError,
        ),
        ({"side_effects": [{}]}, KeyError),
        ({"side_effects": None}, TypeError),
        ({"artifacts": [{}]}, KeyError),
        ({"source": "invalid"}, ValueError),
        ({"journal_through_seq": "invalid"}, ValueError),
        (
            {
                "base_snapshot_ref": {
                    "episode_id": "e",
                    "version": "invalid",
                    "committed_event_id": "c",
                    "payload_hash": "h",
                }
            },
            ValueError,
        ),
    ],
)
def test_decoder_keeps_existing_error_types(
    payload: dict,
    exception: type[Exception],
) -> None:
    with pytest.raises(exception):
        decode(payload)
