from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from trowel_py.model_os.types import EventEnvelope


if TYPE_CHECKING:
    from trowel_py.model_os.reducer import Snapshot


@dataclass(frozen=True)
class ContextFoldRuntime:
    """Reducer 运行时依赖；调用时解析以保留既有 patch seam。"""

    decode_sample: Callable[..., Any]
    context_state_factory: Callable[..., Any]
    snapshot_replace: Callable[..., Any]


def apply_context_sample(
    snap: Snapshot,
    event: EventEnvelope,
    *,
    runtime: ContextFoldRuntime,
) -> Snapshot:
    # session 归属只信任 envelope，不能由 payload 自报。
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
