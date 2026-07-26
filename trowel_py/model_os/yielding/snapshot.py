"""Yield checkpoint 内容与 Episode 关闭后的父状态结算。"""

from __future__ import annotations

from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import EpisodeSnapshot, SnapshotSource
from trowel_py.model_os.yielding.journal import episode
from trowel_py.model_os.yielding.models import (
    TurnState,
    YieldProposal,
    YieldSuggestedState,
)


def cooperative_snapshot(
    store: ModelOsStore, state: TurnState, proposal: YieldProposal
) -> EpisodeSnapshot:
    current = episode(store, state)
    snap = store.read_snapshot()
    task = next((item for item in snap.tasks if item.task_id == current.task_id), None)
    previous = (
        store.read_episode_snapshot(current.last_snapshot_ref)
        if current.last_snapshot_ref is not None
        else None
    )
    return EpisodeSnapshot(
        work_item_goal=(
            task.original_goal
            if task is not None
            else f"work_item:{current.work_item_id}"
        ),
        task_constraints_ref=current.task_id,
        current_judgment=proposal.current_judgment,
        completed_with_evidence=(
            previous.completed_with_evidence if previous is not None else ()
        ),
        side_effects=previous.side_effects if previous is not None else (),
        unknowns=previous.unknowns if previous is not None else (),
        waiting_condition=None,
        next_steps=proposal.next_steps,
        artifacts=previous.artifacts if previous is not None else (),
        native_transcript_ref=(
            previous.native_transcript_ref if previous is not None else None
        ),
        source=SnapshotSource.COOPERATIVE,
        journal_through_seq=snap.last_seq,
        base_snapshot_ref=current.last_snapshot_ref,
    )


def settle_parent_state(
    store: ModelOsStore, state: TurnState, proposal: YieldProposal | None
) -> None:
    current = episode(store, state)
    if current.task_id is not None:
        if (
            proposal is not None
            and proposal.suggested_task_state == YieldSuggestedState.WAITING_EVENT
            and proposal.waiting_condition is not None
        ):
            waiting = proposal.waiting_condition
            store.settle_closed_episode_waiting_event(
                current.episode_id,
                cause=waiting.cause,
                condition_kind=waiting.condition_kind,
                target_ref=waiting.target_ref,
                match_params=waiting.match_params,
                deadline=waiting.deadline,
            )
        else:
            store.settle_closed_episode(current.episode_id)
        return
    store.settle_closed_episode(current.episode_id)
