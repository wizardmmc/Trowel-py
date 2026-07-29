"""把上下文样本事件折叠为每个 ``(episode_id, native_session_id)`` 的最新观测。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from trowel_py.model_os.types import EventEnvelope


if TYPE_CHECKING:
    from trowel_py.model_os.reducer import Snapshot


@dataclass(frozen=True)
class ContextFoldRuntime:
    """保存 reducer 门面在每次调用时传入的上下文折叠依赖。

    Attributes:
        decode_sample: 解码事件 payload，并以 event envelope 的
            ``native_session_id`` 作为样本归属。
        context_state_factory: 使用组合键、解码后的样本和事件时间构造最新观测状态。
        snapshot_replace: 复制原快照，替换 ``context_observations`` 并返回新快照。
    """

    decode_sample: Callable[..., Any]
    context_state_factory: Callable[..., Any]
    snapshot_replace: Callable[..., Any]


def apply_context_sample(
    snap: Snapshot,
    event: EventEnvelope,
    *,
    runtime: ContextFoldRuntime,
) -> Snapshot:
    """按事件时间保留每个 ``(episode_id, native_session_id)`` 的最新观测。

    ``event.native_session_id`` 为空时，在解码 payload 前返回原快照。否则先解码
    payload，并只使用 event envelope 的 ``native_session_id`` 确定会话归属，
    不读取 payload 中的同名字段。解码完成后再比较时间：``occurred_at`` 早于已有
    观测时返回原快照，等于或晚于已有观测时由当前事件替换它。

    Args:
        snap: 归约事件前的派生状态快照。
        event: 携带样本 payload、组合键和发生时间的事件 envelope。
        runtime: 解码样本、构造观测状态和更新快照所需的依赖。

    Returns:
        缺少 ``event.native_session_id`` 或事件早于已有观测时返回原快照，否则
        返回包含当前观测的新快照。
    """
    native = event.native_session_id
    if not native:
        return snap
    sample = runtime.decode_sample(event.payload, native)
    episode_id = event.episode_id
    existing = next(
        (
            state
            for state in snap.context_observations
            if state.episode_id == episode_id and state.native_session_id == native
        ),
        None,
    )
    if existing is not None and event.occurred_at < existing.observed_at:
        return snap
    new_state = runtime.context_state_factory(
        episode_id=episode_id,
        native_session_id=native,
        generation=sample.generation,
        latest_sample=sample,
        observed_at=event.occurred_at,
    )
    rest = tuple(
        state
        for state in snap.context_observations
        if not (state.episode_id == episode_id and state.native_session_id == native)
    )
    return runtime.snapshot_replace(
        snap,
        context_observations=rest + (new_state,),
    )
