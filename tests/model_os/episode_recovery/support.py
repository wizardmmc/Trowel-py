from __future__ import annotations

from typing import Any

from trowel_py.model_os.episode_recovery import build_recovery_partial
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import (
    EpisodeSnapshot,
    EventEnvelope,
    EventKind,
    Provenance,
    SideEffectRecord,
    SnapshotSource,
)


def build(**overrides: Any) -> EpisodeSnapshot:
    kwargs: dict[str, Any] = {
        "work_item_goal": "目标",
        "task_constraints_ref": None,
        "prev": None,
        "journal_through_seq": 0,
    }
    kwargs.update(overrides)
    return ModelOsStore.build_recovery_partial(**kwargs)


def build_direct(**overrides: Any) -> EpisodeSnapshot:
    kwargs: dict[str, Any] = {
        "work_item_goal": "目标",
        "task_constraints_ref": None,
        "prev": None,
        "journal_through_seq": 0,
    }
    kwargs.update(overrides)
    return build_recovery_partial(
        **kwargs,
        snapshot_type=EpisodeSnapshot,
        side_effect_type=SideEffectRecord,
        recovery_source=SnapshotSource.RECOVERY_PARTIAL,
        side_effect_event_kind=EventKind.EPISODE_SIDE_EFFECT_RECORDED,
    )


def side_effect_event(
    *,
    action_ref: str,
    outcome: str,
    evidence_ref: str | None = None,
    idempotency_key: str = "se-key",
    event_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id or f"se.{action_ref}.{outcome}",
        kind=EventKind.EPISODE_SIDE_EFFECT_RECORDED,
        occurred_at="2026-07-21T00:00:06Z",
        source="runner-A",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload={
            "action_ref": action_ref,
            "idempotency_key": idempotency_key,
            "outcome": outcome,
            "evidence_ref": evidence_ref,
        },
        episode_id="ep-1",
    )
