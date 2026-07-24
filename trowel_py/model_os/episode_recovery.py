"""Episode 恢复快照的纯事实折叠；journal 读取与持久化仍由 Store 负责。"""

from __future__ import annotations

from trowel_py.model_os.types import (
    EpisodeSnapshot,
    EventEnvelope,
    EventKind,
    SideEffectRecord,
    SnapshotRef,
    SnapshotSource,
)


def build_recovery_partial(
    *,
    work_item_goal: str,
    task_constraints_ref: str | None,
    prev: EpisodeSnapshot | None,
    journal_through_seq: int,
    prev_ref: SnapshotRef | None = None,
    events: tuple[EventEnvelope, ...] = (),
    snapshot_type: type[EpisodeSnapshot],
    side_effect_type: type[SideEffectRecord],
    recovery_source: SnapshotSource,
    side_effect_event_kind: EventKind,
) -> EpisodeSnapshot:
    done_side_effects = [
        side_effect
        for side_effect in (prev.side_effects if prev else ())
        if side_effect.outcome == "done"
    ]
    unknown_side_effects = [
        side_effect
        for side_effect in (prev.side_effects if prev else ())
        if side_effect.outcome == "unknown_requires_reconcile"
    ]
    completed = list(prev.completed_with_evidence) if prev else []
    done_refs = {side_effect.action_ref for side_effect in done_side_effects}
    unknown_refs = {side_effect.action_ref for side_effect in unknown_side_effects}

    for event in events:
        if event.kind != side_effect_event_kind:
            continue
        payload = event.payload
        action_ref = payload.get("action_ref")
        if not action_ref or action_ref in done_refs:
            continue
        outcome = payload.get("outcome")
        if outcome == "done":
            evidence_ref = payload.get("evidence_ref")
            if not evidence_ref:
                continue
            done_refs.add(action_ref)
            unknown_refs.discard(action_ref)
            unknown_side_effects = [
                side_effect
                for side_effect in unknown_side_effects
                if side_effect.action_ref != action_ref
            ]
            done_side_effects.append(
                side_effect_type(
                    action_ref=action_ref,
                    idempotency_key=payload.get("idempotency_key", ""),
                    outcome="done",
                    evidence_ref=evidence_ref,
                )
            )
            completed.append((action_ref, evidence_ref))
        elif outcome == "unknown_requires_reconcile":
            if action_ref in unknown_refs:
                continue
            unknown_refs.add(action_ref)
            unknown_side_effects.append(
                side_effect_type(
                    action_ref=action_ref,
                    idempotency_key=payload.get("idempotency_key", ""),
                    outcome="unknown_requires_reconcile",
                )
            )

    if prev is not None:
        unknowns = prev.unknowns + (
            "recovery_partial: progress after base snapshot unverified",
        )
        artifacts = prev.artifacts
        transcript = prev.native_transcript_ref
        base_ref = prev_ref
    else:
        unknowns = ("recovery_partial: no base snapshot; full state unverified",)
        artifacts = ()
        transcript = None
        base_ref = None

    return snapshot_type(
        work_item_goal=work_item_goal,
        task_constraints_ref=task_constraints_ref,
        current_judgment="unknown",
        completed_with_evidence=tuple(completed),
        side_effects=tuple(done_side_effects + unknown_side_effects),
        unknowns=unknowns,
        waiting_condition=None,
        next_steps=(),
        artifacts=artifacts,
        native_transcript_ref=transcript,
        source=recovery_source,
        journal_through_seq=journal_through_seq,
        base_snapshot_ref=base_ref,
    )
