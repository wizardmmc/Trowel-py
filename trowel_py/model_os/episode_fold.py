from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from trowel_py.model_os.types import (
    EpisodeStatus,
    EventEnvelope,
    PendingDescriptor,
    WaitingSubtype,
)


if TYPE_CHECKING:
    from trowel_py.model_os.reducer import EpisodeState, Snapshot


@dataclass(frozen=True)
class EpisodeFoldRuntime:
    """依赖保持动态解析，以保留 Reducer 既有的 monkeypatch seam。"""

    find_episode: Callable[..., Any]
    replace_episode: Callable[..., Any]
    pending_from_payload: Callable[..., Any]
    episode_status: Any
    reconcile_reason: Callable[..., Any]
    snapshot_ref: Callable[..., Any]
    state_replace: Callable[..., Any]


def episode_from_created(
    event: EventEnvelope,
    *,
    episode_state_factory: Callable[..., EpisodeState],
    episode_status: Any = EpisodeStatus,
) -> EpisodeState:
    p = event.payload
    return episode_state_factory(
        episode_id=p["episode_id"],
        work_item_id=p["work_item_id"],
        task_id=p.get("task_id"),
        status=episode_status(p.get("status", episode_status.STARTING.value)),
        status_provenance=event.provenance,
        native_session_id=p.get("native_session_id"),
        pending_descriptor=None,
        reconcile_reason=None,
        last_snapshot_ref=None,
        created_at=event.occurred_at,
        updated_at=event.occurred_at,
    )


def _find_episode(snap: Snapshot, episode_id: str | None) -> EpisodeState | None:
    if episode_id is None:
        return None
    return next((e for e in snap.episodes if e.episode_id == episode_id), None)


def _replace_episode(
    snap: Snapshot,
    episode_id: str | None,
    new_state: EpisodeState,
    *,
    snapshot_replace: Callable[..., Snapshot] = replace,
) -> Snapshot:
    if episode_id is None:
        return snap
    return snapshot_replace(
        snap,
        episodes=tuple(
            new_state if e.episode_id == episode_id else e for e in snap.episodes
        ),
    )


def _pending_from_payload(
    p: dict[str, Any],
    *,
    pending_descriptor_factory: Callable[..., PendingDescriptor] = PendingDescriptor,
    waiting_subtype: Any = WaitingSubtype,
) -> PendingDescriptor:
    return pending_descriptor_factory(
        kind=waiting_subtype(p["kind"]),
        native_generation=p.get("native_generation"),
        correlation_id=p["correlation_id"],
        cause=p.get("cause", ""),
        posed_at=p["posed_at"],
    )


def _apply_episode_status_change(
    snap: Snapshot,
    event: EventEnvelope,
    *,
    runtime: EpisodeFoldRuntime,
) -> Snapshot:
    current = runtime.find_episode(snap, event.episode_id)
    if current is None:
        return snap
    return runtime.replace_episode(
        snap,
        event.episode_id,
        runtime.state_replace(
            current,
            status=runtime.episode_status(event.payload["new_status"]),
            status_provenance=event.provenance,
            updated_at=event.occurred_at,
        ),
    )


def _apply_episode_checkpoint(
    snap: Snapshot,
    event: EventEnvelope,
    *,
    runtime: EpisodeFoldRuntime,
) -> Snapshot:
    current = runtime.find_episode(snap, event.episode_id)
    if current is None:
        return snap
    p = event.payload
    ref = runtime.snapshot_ref(
        episode_id=current.episode_id,
        version=int(p["version"]),
        committed_event_id=p.get("committed_event_id", event.event_id),
        payload_hash=p["payload_hash"],
    )
    updates: dict[str, Any] = {
        "last_snapshot_ref": ref,
        "updated_at": event.occurred_at,
    }
    new_status = p.get("new_status")
    if new_status is not None:
        updates["status"] = runtime.episode_status(new_status)
        updates["status_provenance"] = event.provenance
    return runtime.replace_episode(
        snap, event.episode_id, runtime.state_replace(current, **updates)
    )


def _apply_episode_suspended(
    snap: Snapshot,
    event: EventEnvelope,
    *,
    runtime: EpisodeFoldRuntime,
) -> Snapshot:
    current = runtime.find_episode(snap, event.episode_id)
    if current is None:
        return snap
    pending = runtime.pending_from_payload(event.payload)
    return runtime.replace_episode(
        snap,
        event.episode_id,
        runtime.state_replace(
            current,
            status=runtime.episode_status(event.payload["new_status"]),
            status_provenance=event.provenance,
            pending_descriptor=pending,
            updated_at=event.occurred_at,
        ),
    )


def _apply_episode_wait_resolved(
    snap: Snapshot,
    event: EventEnvelope,
    *,
    runtime: EpisodeFoldRuntime,
) -> Snapshot:
    current = runtime.find_episode(snap, event.episode_id)
    if current is None:
        return snap
    return runtime.replace_episode(
        snap,
        event.episode_id,
        runtime.state_replace(
            current,
            status=runtime.episode_status.SUSPENDED_READY,
            status_provenance=event.provenance,
            pending_descriptor=None,
            updated_at=event.occurred_at,
        ),
    )


def _apply_episode_reconcile_required(
    snap: Snapshot,
    event: EventEnvelope,
    *,
    runtime: EpisodeFoldRuntime,
) -> Snapshot:
    current = runtime.find_episode(snap, event.episode_id)
    if current is None:
        return snap
    reason = runtime.reconcile_reason(event.payload["reason"])
    return runtime.replace_episode(
        snap,
        event.episode_id,
        runtime.state_replace(
            current,
            status=runtime.episode_status.RECONCILE_REQUIRED,
            status_provenance=event.provenance,
            reconcile_reason=reason,
            updated_at=event.occurred_at,
        ),
    )


def _apply_episode_reconcile_resolved(
    snap: Snapshot,
    event: EventEnvelope,
    *,
    runtime: EpisodeFoldRuntime,
) -> Snapshot:
    current = runtime.find_episode(snap, event.episode_id)
    if current is None:
        return snap
    p = event.payload
    updates: dict[str, Any] = {
        "status": runtime.episode_status(p["new_status"]),
        "status_provenance": event.provenance,
        "reconcile_reason": None,
        "updated_at": event.occurred_at,
    }
    # 外部 reconcile close 没有 lease，不能另发受 fencing 保护的 checkpoint；
    # 字段齐全时由本次 resolve 事件同时携带恢复快照身份。
    if p.get("version") is not None and p.get("payload_hash"):
        updates["last_snapshot_ref"] = runtime.snapshot_ref(
            episode_id=current.episode_id,
            version=int(p["version"]),
            committed_event_id=p.get("committed_event_id", event.event_id),
            payload_hash=p["payload_hash"],
        )
    return runtime.replace_episode(
        snap, event.episode_id, runtime.state_replace(current, **updates)
    )
