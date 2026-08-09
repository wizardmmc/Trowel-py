"""把 Episode 生命周期事件纯归约为快照中的当前状态。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from trowel_py.model_os.types import (
    EpisodeStatus,
    EventEnvelope,
    PendingDescriptor,
    ReconcileReason,
    SnapshotRef,
    WaitingSubtype,
)


if TYPE_CHECKING:
    from trowel_py.model_os.reducer import EpisodeState, Snapshot


@dataclass(frozen=True)
class EpisodeFoldRuntime:
    """提供 Episode 归约所需的查找、替换和值对象构造函数。

    主 reducer 每次归约时从当前绑定组装这些依赖，使运行时替换仍然生效，也让
    本模块不依赖具体的快照实现。

    Attributes:
        find_episode: 按 ID 查找当前 Episode 状态的函数。
        replace_episode: 在快照中替换 Episode 状态的函数。
        pending_from_payload: 把事件 payload 转换为待决请求的函数。
        episode_status: 把持久化字符串转换为 Episode 状态的类型。
        reconcile_reason: 把持久化字符串转换为现实核对原因的类型。
        snapshot_ref: 构造已提交快照引用的类型。
        state_replace: 复制 Episode 状态并覆盖指定字段的函数。
    """

    find_episode: Callable[[Snapshot, str | None], EpisodeState | None]
    replace_episode: Callable[[Snapshot, str | None, EpisodeState], Snapshot]
    pending_from_payload: Callable[[dict[str, Any]], PendingDescriptor]
    episode_status: type[EpisodeStatus]
    reconcile_reason: type[ReconcileReason]
    snapshot_ref: type[SnapshotRef]
    state_replace: Callable[..., EpisodeState]


def episode_from_created(
    event: EventEnvelope,
    *,
    episode_state_factory: Callable[..., EpisodeState],
    episode_status: type[EpisodeStatus] = EpisodeStatus,
) -> EpisodeState:
    """从创建事件构造 Episode 的初始派生状态。

    payload 未提供状态时使用 ``starting``；创建时间和更新时间均取事件发生时间。

    Args:
        event: 包含 Episode、WorkItem、可选 Task 和初始状态的创建事件。
        episode_state_factory: 构造主 reducer 所用 EpisodeState 的函数。
        episode_status: 把 payload 状态字符串转换为 Episode 状态的类型。

    Returns:
        尚无待决请求、核对原因和快照引用的初始 Episode 状态。
    """

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
    """返回快照中第一个匹配 ID 的 Episode。

    ``episode_id`` 为 None 或没有匹配项时返回 None。
    """

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
    """用新状态替换快照中所有匹配 ID 的 Episode。

    Args:
        snap: 当前 Model OS 快照。
        episode_id: 需要替换的 Episode ID；为 None 时原样返回快照。
        new_state: 匹配项的新 Episode 状态。
        snapshot_replace: 复制快照并覆盖 Episode 集合的函数。

    Returns:
        替换后的新快照，或 ``episode_id`` 为 None 时的原快照。
    """

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
    waiting_subtype: type[WaitingSubtype] = WaitingSubtype,
) -> PendingDescriptor:
    """从暂停事件 payload 还原原生会话的待决请求。

    Args:
        p: 包含等待类型、``correlation_id``、提出时间及可选原因和运行代次的
            payload。
        pending_descriptor_factory: 构造待决请求值对象的函数。
        waiting_subtype: 把持久化字符串转换为等待类型的类型。

    Returns:
        供 Episode 状态保存的待决请求。
    """

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
    """更新 Episode 的状态、状态来源和更新时间。

    事件没有关联到现有 Episode 时原样返回快照。

    Args:
        snap: 当前 Model OS 快照。
        event: payload 含 ``new_status`` 的 Episode 状态事件。
        runtime: 本次归约使用的查找、替换和值对象依赖。

    Returns:
        应用状态变化后的快照。
    """

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
    """记录 Episode 已提交的快照引用，并按事件要求更新状态。

    ``committed_event_id`` 缺失时使用当前事件 ID。事件没有关联到现有 Episode 时
    原样返回快照。

    Args:
        snap: 当前 Model OS 快照。
        event: 包含快照版本、内容哈希和可选新状态的 checkpoint 事件。
        runtime: 本次归约使用的查找、替换和值对象依赖。

    Returns:
        应用 checkpoint 后的快照。
    """

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
    """记录待决请求，并把 Episode 更新为事件指定的等待状态。

    事件没有关联到现有 Episode 时原样返回快照。

    Args:
        snap: 当前 Model OS 快照。
        event: 同时包含等待信息和 ``new_status`` 的暂停事件。
        runtime: 本次归约使用的查找、替换和值对象依赖。

    Returns:
        保存待决请求后的快照。
    """

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
    """清除已解决的待决请求，并把 Episode 置为 ``suspended_ready``。

    事件没有关联到现有 Episode 时原样返回快照。

    Args:
        snap: 当前 Model OS 快照。
        event: 表示等待条件已经解决的 Episode 事件。
        runtime: 本次归约使用的查找、替换和值对象依赖。

    Returns:
        可以继续恢复的 Episode 快照。
    """

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
    """记录现实核对原因，并把 Episode 置为 ``reconcile_required``。

    原待决请求会被清除；事件没有关联到现有 Episode 时原样返回快照。

    Args:
        snap: 当前 Model OS 快照。
        event: payload 含稳定核对原因的 Episode 事件。
        runtime: 本次归约使用的查找、替换和值对象依赖。

    Returns:
        等待现实核对的 Episode 快照。
    """

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
    """清除现实核对原因，并恢复事件指定的状态和可选快照引用。

    只有 ``version`` 不是 None 且 ``payload_hash`` 为非空字符串时才更新快照引用；
    ``committed_event_id`` 缺失时使用当前事件 ID。事件没有关联到现有 Episode 时
    原样返回快照。

    Args:
        snap: 当前 Model OS 快照。
        event: 包含核对后状态及可选快照身份的 resolve 事件。
        runtime: 本次归约使用的查找、替换和值对象依赖。

    Returns:
        应用现实核对结果后的快照。
    """

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
