from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from trowel_py.model_os.types import (
    EventEnvelope,
    MemoryEligibility,
    Provenance,
    SessionPurpose,
    WorkItemKind,
    WorkItemStatus,
)


if TYPE_CHECKING:
    from trowel_py.model_os.reducer import Snapshot, WorkItemState


@dataclass(frozen=True)
class WorkItemFoldRuntime:
    """依赖保持动态解析，以保留 Reducer 既有的 monkeypatch seam。"""

    replace_work_item: Callable[..., Any]
    provenance: Any
    work_item_status: Any
    state_replace: Callable[..., Any]


def _work_item_from_created(
    event: EventEnvelope,
    *,
    work_item_state_factory: Callable[..., WorkItemState],
    work_item_kind: Any = WorkItemKind,
    work_item_status: Any = WorkItemStatus,
    provenance: Any = Provenance,
    session_purpose: Any = SessionPurpose,
    memory_eligibility: Any = MemoryEligibility,
) -> WorkItemState:
    payload = event.payload
    return work_item_state_factory(
        work_item_id=payload["work_item_id"],
        kind=work_item_kind(payload["kind"]),
        owner_ref=payload["owner_ref"],
        task_id=payload.get("task_id"),
        status=work_item_status(payload["status"]),
        status_provenance=provenance.STALE,
        session_purpose=session_purpose(payload["session_purpose"]),
        memory_eligibility=memory_eligibility(payload["memory_eligibility"]),
    )


def _replace_work_item(
    snap: Snapshot,
    work_item_id: str,
    new_state: WorkItemState,
    *,
    snapshot_replace: Callable[..., Snapshot] = replace,
) -> Snapshot:
    updated = tuple(
        new_state if item.work_item_id == work_item_id else item
        for item in snap.work_items
    )
    return snapshot_replace(snap, work_items=updated)


def _apply_status_change(
    snap: Snapshot,
    event: EventEnvelope,
    *,
    runtime: WorkItemFoldRuntime,
) -> Snapshot:
    work_item_id = event.work_item_id
    if work_item_id is None:
        return snap
    current = next(
        (item for item in snap.work_items if item.work_item_id == work_item_id),
        None,
    )
    if current is None:
        return snap
    if event.provenance == runtime.provenance.STALE:
        return snap
    if event.provenance.strength < current.status_provenance.strength:
        return snap
    new_state = runtime.state_replace(
        current,
        status=runtime.work_item_status(event.payload["new_status"]),
        status_provenance=event.provenance,
    )
    return runtime.replace_work_item(snap, work_item_id, new_state)
