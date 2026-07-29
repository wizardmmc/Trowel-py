"""把 Store 提供的字段组装为 Task、WorkItem 和 Episode 内核事件。"""

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
    """组装一条来源固定为 Model OS 内核的 Task 事件。

    Args:
        kind: 要记录的 Task 事件类型。
        task_id: 事件所属的 Task ID。
        payload: 要随事件原样保存的结构化内容。
        event_id: Store 为本条事件生成的 ID。
        occurred_at: Store 捕获的事件发生时间。
        provenance: 事件的来源类别，用于归约时比较来源强度。
        work_item_id: 事件关联的 WorkItem ID；没有关联时为 None。
        policy_version: Store 当前使用的策略版本。
        event_type: 使用关键字参数创建 Event 的构造函数。

    Returns:
        由 ``event_type`` 创建的 Task Event。
    """

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
    """组装一条来源固定为 Model OS 内核的 WorkItem 状态事件。

    Args:
        work_item_id: 状态发生变化的 WorkItem ID。
        new_status: 要写入 payload 的新状态。
        task_id: WorkItem 所属的 Task ID；系统工作没有 Task 时为 None。
        occurred_at: 调用方为状态变化指定的发生时间。
        event_id: Store 为本条事件生成的 ID。
        event_kind: WorkItem 状态变化对应的事件类型。
        provenance: 事件的来源类别，用于归约时比较来源强度。
        policy_version: Store 当前使用的策略版本。
        event_type: 使用关键字参数创建 Event 的构造函数。

    Returns:
        payload 只含 ``new_status`` 的 WorkItem Event。
    """

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
    """组装一条来源固定为 Model OS 内核的 Episode 事件。

    本函数只转交可选的 lease 三元组，不判断事件类型是否需要 fencing。

    Args:
        kind: 要记录的 Episode 事件类型。
        episode_id: 事件所属的 Episode ID。
        payload: 要随事件原样保存的结构化内容。
        event_id: Store 生成或由调用方预先指定的事件 ID。
        occurred_at: Store 捕获的事件发生时间。
        work_item_id: Episode 所属的 WorkItem ID；没有关联时为 None。
        task_id: Episode 所属的 Task ID；没有关联时为 None。
        provenance: 事件的来源类别，用于归约时比较来源强度。
        lease_id: 保护本次写入的 lease ID；不需要 fencing 时为 None。
        owner: lease 当前持有者；不需要 fencing 时为 None。
        fencing_token: lease 的 fencing token；不需要 fencing 时为 None。
        policy_version: Store 当前使用的策略版本。
        event_type: 使用关键字参数创建 Event 的构造函数。

    Returns:
        由 ``event_type`` 创建的 Episode Event。
    """

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
