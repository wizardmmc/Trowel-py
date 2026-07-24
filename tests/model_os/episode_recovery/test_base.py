from __future__ import annotations

from tests.model_os._episode_helpers import make_cooperative_snapshot
from tests.model_os.episode_recovery.support import build
from trowel_py.model_os.types import (
    ArtifactRef,
    EpisodeSnapshot,
    SideEffectRecord,
    SnapshotRef,
    SnapshotSource,
)


def test_recovery_with_no_base_fills_unknowns() -> None:
    snapshot = build(journal_through_seq=10)
    assert snapshot.current_judgment == "unknown"
    assert snapshot.completed_with_evidence == ()
    assert snapshot.side_effects == ()
    assert snapshot.next_steps == ()
    assert snapshot.waiting_condition is None
    assert snapshot.native_transcript_ref is None
    assert snapshot.unknowns == (
        "recovery_partial: no base snapshot; full state unverified",
    )
    assert snapshot.source == SnapshotSource.RECOVERY_PARTIAL
    assert snapshot.journal_through_seq == 10


def test_recovery_drops_current_judgment_and_next_steps() -> None:
    prev = make_cooperative_snapshot(
        current_judgment="有待确认",
        next_steps=("继续核对", "整理结论"),
    )
    snapshot = build(
        work_item_goal=prev.work_item_goal,
        prev=prev,
        journal_through_seq=99,
    )
    assert snapshot.current_judgment == "unknown"
    assert snapshot.next_steps == ()


def test_recovery_unknown_side_effects_survive_as_unknown() -> None:
    done = SideEffectRecord(
        action_ref="action/confirmed-write",
        idempotency_key="done-1",
        outcome="done",
        evidence_ref="evidence/write.txt",
    )
    unknown = SideEffectRecord(
        action_ref="action/pending-delivery",
        idempotency_key="unknown-1",
        outcome="unknown_requires_reconcile",
    )
    snapshot = build(
        prev=make_cooperative_snapshot(side_effects=(done, unknown)),
        journal_through_seq=5,
    )
    by_ref = {
        side_effect.action_ref: side_effect for side_effect in snapshot.side_effects
    }
    assert by_ref[done.action_ref].outcome == "done"
    assert by_ref[unknown.action_ref].outcome == "unknown_requires_reconcile"
    assert all(
        action_ref != unknown.action_ref
        for action_ref, _ in snapshot.completed_with_evidence
    )


def test_recovery_carries_completed_with_evidence_forward() -> None:
    completed = (
        ("action/read-config", "evidence/config.txt"),
        ("action/parse-log", "evidence/parsed.txt"),
    )
    snapshot = build(
        prev=make_cooperative_snapshot(completed_with_evidence=completed),
        journal_through_seq=7,
    )
    assert snapshot.completed_with_evidence == completed


def test_recovery_inherits_artifacts_and_transcript_ref() -> None:
    prev = make_cooperative_snapshot(
        artifacts=(ArtifactRef(kind="commit", ref="abc123"),),
        native_transcript_ref="transcript://ep-1",
    )
    snapshot = build(prev=prev, journal_through_seq=3)
    assert snapshot.artifacts == prev.artifacts
    assert snapshot.native_transcript_ref == prev.native_transcript_ref


def test_recovery_appends_unverified_marker_to_unknowns() -> None:
    prev = make_cooperative_snapshot(unknowns=("边界外状态待确认",))
    snapshot = build(prev=prev, journal_through_seq=12)
    assert snapshot.unknowns == (
        "边界外状态待确认",
        "recovery_partial: progress after base snapshot unverified",
    )


def test_recovery_passes_journal_through_seq_through() -> None:
    for sequence in (0, 1, 42, 1000):
        assert build(journal_through_seq=sequence).journal_through_seq == sequence


def test_recovery_source_is_recovery_partial_even_from_cooperative_base() -> None:
    snapshot = build(prev=make_cooperative_snapshot())
    assert snapshot.source == SnapshotSource.RECOVERY_PARTIAL


def test_recovery_uses_provided_goal_and_constraints_ref() -> None:
    snapshot = build(
        work_item_goal="实时任务目标",
        task_constraints_ref="task-123",
        prev=make_cooperative_snapshot(work_item_goal="旧目标"),
    )
    assert snapshot.work_item_goal == "实时任务目标"
    assert snapshot.task_constraints_ref == "task-123"


def test_recovery_base_snapshot_ref_is_not_a_corrupt_dummy() -> None:
    snapshot = build(
        prev=make_cooperative_snapshot(),
        prev_ref=None,
    )
    assert snapshot.base_snapshot_ref is None


def test_recovery_base_points_at_supplied_prev_ref() -> None:
    prev_ref = SnapshotRef(
        episode_id="ep-1",
        version=3,
        committed_event_id="event-parent",
        payload_hash="sha256:abc",
    )
    snapshot = build(
        prev=make_cooperative_snapshot(),
        prev_ref=prev_ref,
        journal_through_seq=42,
    )
    assert snapshot.base_snapshot_ref == prev_ref


def test_recovery_chain_does_not_collapse_to_grandparent() -> None:
    grandparent_ref = SnapshotRef(
        episode_id="ep-1",
        version=1,
        committed_event_id="event-grandparent",
        payload_hash="hash-grandparent",
    )
    parent = EpisodeSnapshot(
        work_item_goal="目标",
        task_constraints_ref=None,
        current_judgment="unknown",
        completed_with_evidence=(),
        side_effects=(),
        unknowns=("待确认",),
        waiting_condition=None,
        next_steps=(),
        artifacts=(),
        native_transcript_ref=None,
        source=SnapshotSource.RECOVERY_PARTIAL,
        journal_through_seq=10,
        base_snapshot_ref=grandparent_ref,
    )
    parent_ref = SnapshotRef(
        episode_id="ep-1",
        version=2,
        committed_event_id="event-parent",
        payload_hash="hash-parent",
    )
    snapshot = build(
        prev=parent,
        prev_ref=parent_ref,
        journal_through_seq=20,
    )
    assert snapshot.base_snapshot_ref == parent_ref


def test_prev_ref_is_ignored_without_previous_snapshot() -> None:
    prev_ref = SnapshotRef(
        episode_id="ep-1",
        version=1,
        committed_event_id="event-1",
        payload_hash="hash-1",
    )
    assert build(prev=None, prev_ref=prev_ref).base_snapshot_ref is None
