"""把单条 live AgentEvent 记为当前 Episode 的上下文观测。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from trowel_py.model_os.context_observer import (
    ContextSample,
    NormalizedCodexCompaction,
    cc_context_events_from_agent,
    codex_context_events_from_agent,
    extract_cc_samples,
    extract_codex_samples,
    resolve_window,
)
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.yielding.models import TurnState


@dataclass(frozen=True)
class ContextObservation:
    sample: ContextSample | None = None
    compacted: bool = False
    compact_trigger: str | None = None


def record_context_event(
    store: ModelOsStore,
    state: TurnState,
    event: Mapping[str, Any],
) -> ContextObservation | None:
    registration = state.registration
    occurred_at = _occurred_at(event)
    episode = store.read_snapshot().episode_by_id(registration.episode_id)
    task_id = episode.task_id if episode is not None else None

    if registration.runtime == "claude_code":
        cc_events = cc_context_events_from_agent([event])
        if not cc_events:
            return None
        cc_event = cc_events[0]
        if cc_event.subtype == "compact_boundary":
            return _record_boundary(
                store,
                state,
                event,
                occurred_at=occurred_at,
                task_id=task_id,
                trigger=cc_event.compact_trigger,
            )
        previous = store.read_snapshot().context_observation(
            registration.episode_id, registration.native_session_id
        )
        previous_window = (
            previous.latest_sample.effective_window_tokens
            if previous is not None
            else None
        )
        samples = extract_cc_samples(
            [cc_event],
            native_session_id=registration.native_session_id,
            main_or_subagent="main",
            window_resolver=lambda model: resolve_window(model) or previous_window,
        )
    else:
        codex_events = codex_context_events_from_agent([event])
        if not codex_events:
            return None
        codex_event = codex_events[0]
        if isinstance(codex_event, NormalizedCodexCompaction):
            if codex_event.phase != "completed":
                return None
            return _record_boundary(
                store,
                state,
                event,
                occurred_at=occurred_at,
                task_id=task_id,
                trigger="native",
            )
        samples = extract_codex_samples(
            [codex_event], native_session_id=registration.native_session_id
        )

    if not samples:
        return None
    sample = replace(
        samples[-1],
        generation=state.context_generation,
        request_identity=_live_request_identity(samples[-1], event),
    )
    store.record_context_sample(
        sample,
        episode_id=registration.episode_id,
        occurred_at=occurred_at,
        task_id=task_id,
        source="yield_context_observer",
    )
    return ContextObservation(sample=sample)


def _record_boundary(
    store: ModelOsStore,
    state: TurnState,
    event: Mapping[str, Any],
    *,
    occurred_at: str,
    task_id: str | None,
    trigger: str | None,
) -> ContextObservation | None:
    identity = ":".join(
        str(event.get(field, "")) for field in ("runtime", "seq", "type", "turn_id")
    )
    if identity in state.context_boundary_ids:
        return None
    state.context_boundary_ids.add(identity)
    state.context_generation += 1
    store.record_context_boundary(
        state.registration.native_session_id,
        episode_id=state.registration.episode_id,
        generation=state.context_generation,
        occurred_at=occurred_at,
        trigger=trigger,
        task_id=task_id,
        source="yield_context_observer",
    )
    return ContextObservation(compacted=True, compact_trigger=trigger)


def _live_request_identity(
    sample: ContextSample, event: Mapping[str, Any]
) -> str:
    seq = event.get("seq")
    if seq is None:
        return sample.request_identity
    return f"{sample.request_identity}:event:{seq}"


def _occurred_at(event: Mapping[str, Any]) -> str:
    payload = event.get("payload")
    payload = payload if isinstance(payload, Mapping) else {}
    timestamp = payload.get("timestamp") or event.get("timestamp")
    if isinstance(timestamp, str) and timestamp:
        return timestamp
    return datetime.now(timezone.utc).isoformat()
