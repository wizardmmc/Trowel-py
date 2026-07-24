from __future__ import annotations

from trowel_py.model_os.types import (
    ArtifactRef,
    EpisodeSnapshot,
    PendingDescriptor,
    SideEffectRecord,
    SnapshotRef,
    SnapshotSource,
    WaitingSubtype,
)


def full_snapshot() -> EpisodeSnapshot:
    return EpisodeSnapshot(
        work_item_goal="整理候选方案",
        task_constraints_ref=None,
        current_judgment="方案可继续验证",
        completed_with_evidence=(
            ("action-1", "evidence-1"),
            ("action-2", "evidence-2"),
        ),
        side_effects=(
            SideEffectRecord(
                action_ref="write-1",
                idempotency_key="idem-1",
                outcome="done",
                evidence_ref="receipt-1",
            ),
            SideEffectRecord(
                action_ref="write-2",
                idempotency_key="idem-2",
                outcome="unknown_requires_reconcile",
            ),
        ),
        unknowns=("边界条件待确认",),
        waiting_condition=PendingDescriptor(
            kind=WaitingSubtype.APPROVAL,
            native_generation=None,
            correlation_id="corr-1",
            cause="需要确认",
            posed_at="2026-07-23T00:00:00Z",
        ),
        next_steps=("核对结果", "形成结论"),
        artifacts=(ArtifactRef(kind="report", ref="artifact-1"),),
        native_transcript_ref=None,
        source=SnapshotSource.RECOVERY_PARTIAL,
        journal_through_seq=12,
        base_snapshot_ref=SnapshotRef(
            episode_id="episode-base",
            version=3,
            committed_event_id="event-base",
            payload_hash="hash-base",
        ),
    )
