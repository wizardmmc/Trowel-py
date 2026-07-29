"""根据已有快照和 journal 事实构造 Episode 恢复快照，不负责读写持久化。"""

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
    """用上次快照和后续 journal 事实构造保守的恢复快照。

    只保留上次快照中已完成或需要核对的副作用。后续事件只有在 outcome 为
    ``done`` 且带 ``evidence_ref`` 时才算完成；已完成事实会取代同一
    ``action_ref`` 的未知记录。恢复结果把当前判断置为 unknown，清除等待条件和
    下一步，不自动重放无法确认的工作。

    Args:
        work_item_goal: 恢复后继续遵循的 WorkItem 目标。
        task_constraints_ref: 关联 Task 的 ID，用于指向其约束；没有关联 Task 时为
            None。
        prev: 上次已提交的 Episode 快照；没有可用基线时为 None。
        journal_through_seq: 调用方确认的 journal 高水位，原样记录到恢复快照。
        prev_ref: ``prev`` 对应的已提交快照引用；没有 ``prev`` 时忽略。
        events: 调用方预先筛选出的当前 Episode 在
            ``(prev.journal_through_seq, journal_through_seq]`` 内的事件。本函数只
            折叠其中类型为 ``side_effect_event_kind`` 的副作用结果；没有 ``prev``
            时区间下界为 0。
        snapshot_type: 构造恢复快照的类型。
        side_effect_type: 构造副作用记录的类型。
        recovery_source: 恢复快照 ``source`` 字段使用的来源标识。
        side_effect_event_kind: 表示副作用结果的事件类型；其他事件会被忽略。

    Returns:
        保留已证实副作用、基线产物和原生 transcript 引用的恢复快照；没有基线时
        产物和 transcript 为空。
    """

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
