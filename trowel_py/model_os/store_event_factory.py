"""Model OS Store 的内核事件构造策略。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from trowel_py.model_os.types import EventEnvelope, Provenance, WorkItemStatus


def make_task_event(
    kind: str,
    task_id: str,
    payload: dict[str, Any],
    *,
    event_id: str,
    occurred_at: str,
    provenance: Provenance,
    work_item_id: str | None,
    policy_version: str,
    event_type: Callable[..., EventEnvelope],
) -> EventEnvelope:
    return event_type(
        event_id=event_id,
        kind=kind,
        occurred_at=occurred_at,
        source="kernel",
        provenance=provenance,
        policy_version=policy_version,
        payload=payload,
        task_id=task_id,
        work_item_id=work_item_id,
    )


def make_work_item_event(
    work_item_id: str,
    new_status: WorkItemStatus,
    task_id: str | None,
    occurred_at: str,
    *,
    event_id: str,
    event_kind: str,
    provenance: Provenance,
    policy_version: str,
    event_type: Callable[..., EventEnvelope],
) -> EventEnvelope:
    return event_type(
        event_id=event_id,
        kind=event_kind,
        occurred_at=occurred_at,
        source="kernel",
        provenance=provenance,
        policy_version=policy_version,
        payload={"new_status": new_status.value},
        work_item_id=work_item_id,
        task_id=task_id,
    )


def make_episode_event(
    kind: str,
    episode_id: str,
    payload: dict[str, Any],
    *,
    event_id: str,
    occurred_at: str,
    work_item_id: str | None,
    task_id: str | None,
    provenance: Provenance,
    lease_id: str | None,
    owner: str | None,
    fencing_token: int | None,
    policy_version: str,
    event_type: Callable[..., EventEnvelope],
) -> EventEnvelope:
    return event_type(
        event_id=event_id,
        kind=kind,
        occurred_at=occurred_at,
        source="kernel",
        provenance=provenance,
        policy_version=policy_version,
        payload=payload,
        work_item_id=work_item_id,
        task_id=task_id,
        episode_id=episode_id,
        lease_id=lease_id,
        owner=owner,
        fencing_token=fencing_token,
    )
