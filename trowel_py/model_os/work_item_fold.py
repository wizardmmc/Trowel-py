"""把 WorkItem 创建和状态变化事件折叠为当前状态。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

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
    """集中保存 WorkItem 状态折叠所需的可替换依赖。

    主 reducer 每次处理状态事件时都会重新组装该对象，因此当次调用会使用
    reducer 中最新的 WorkItem 更新函数、枚举类型和 dataclass 复制函数。

    Attributes:
        replace_work_item: 在快照中替换指定 WorkItem 状态的函数。
        provenance: 用于识别 ``STALE`` 来源的 ``Provenance`` 类型。
        work_item_status: 把事件中的状态字符串解析为 ``WorkItemStatus`` 的类型。
        state_replace: 复制 ``WorkItemState`` 并更新指定字段的函数。
    """

    replace_work_item: Callable[[Snapshot, str, WorkItemState], Snapshot]
    provenance: type[Provenance]
    work_item_status: type[WorkItemStatus]
    state_replace: Callable[..., WorkItemState]


def _work_item_from_created(
    event: EventEnvelope,
    *,
    work_item_state_factory: Callable[..., WorkItemState],
    work_item_kind: type[WorkItemKind] = WorkItemKind,
    work_item_status: type[WorkItemStatus] = WorkItemStatus,
    provenance: type[Provenance] = Provenance,
    session_purpose: type[SessionPurpose] = SessionPurpose,
    memory_eligibility: type[MemoryEligibility] = MemoryEligibility,
) -> WorkItemState:
    """从创建事件构造 WorkItem 的初始派生状态。

    创建事件只证明 WorkItem 已存在，因此 ``status_provenance`` 固定为
    ``STALE``。

    Args:
        event: payload 包含 WorkItem 身份、归属、初始状态、会话用途和 Memory
            资格的创建事件。
        work_item_state_factory: 根据事件字段创建 ``WorkItemState`` 的函数。
        work_item_kind: 把事件中的工作类别解析为 ``WorkItemKind`` 的类型。
        work_item_status: 把事件中的状态解析为 ``WorkItemStatus`` 的类型。
        provenance: 提供初始 ``STALE`` 来源的 ``Provenance`` 类型。
        session_purpose: 把事件中的会话用途解析为 ``SessionPurpose`` 的类型。
        memory_eligibility: 把事件中的 Memory 资格解析为
            ``MemoryEligibility`` 的类型。
    """
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
    """在快照中把所有指定 ID 的 WorkItem 替换为同一状态。

    为兼容含有重复 ID 的畸形快照，所有匹配项都会被替换。

    Args:
        snap: 包含待更新 WorkItem 的当前快照。
        work_item_id: 要替换的 WorkItem ID。
        new_state: 每个匹配项共同使用的新状态。
        snapshot_replace: 复制 ``Snapshot`` 并更新字段的函数。
    """
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
    """按来源强度把 WorkItem 状态变化事件折叠进快照。

    事件未绑定 WorkItem、目标不存在、来源为 ``STALE`` 或弱于当前来源时，
    原样返回快照；强度相同或更高时更新状态及其来源。

    Args:
        snap: 状态变化前的当前快照。
        event: 包含目标 WorkItem ID、新状态和来源的状态变化事件。
        runtime: 本次折叠使用的来源判断、状态解析、复制和替换依赖。
    """
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
