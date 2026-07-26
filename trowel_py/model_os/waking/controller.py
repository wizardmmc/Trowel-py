"""可信用户入口与当前 controller 内待发送输入的短期保管。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from trowel_py.model_os.types import ReconcileReason
from trowel_py.model_os.waking.models import (
    WakeConditionKind,
    WakeDisposition,
    WakeEvent,
    WakeObservation,
)


class WakeInputRejected(RuntimeError):
    pass


@dataclass(frozen=True)
class QueuedRuntimeInput:
    episode_id: str
    runtime_generation: str
    payload: dict[str, Any]


class WakeController:
    def __init__(self, store) -> None:
        self._store = store
        self._pending_inputs: dict[str, QueuedRuntimeInput] = {}

    def observe(self, observation: WakeObservation) -> tuple[WakeEvent, ...]:
        return self._store.consume_wake(observation)

    def manages(self, session_id: str) -> bool:
        return self._store.episode_runtime_binding_for_session(session_id) is not None

    def queue_for_session(
        self,
        session_id: str,
        *,
        correlation_id: str,
        runtime_generation: str,
        payload: dict[str, Any],
    ) -> QueuedRuntimeInput:
        binding = self._store.episode_runtime_binding_for_session(session_id)
        if binding is None:
            raise WakeInputRejected("session is not bound to a managed Episode")
        episode = self._store.read_snapshot().episode_by_id(binding.episode_id)
        if episode is None or episode.pending_descriptor is None:
            raise WakeInputRejected("managed Episode has no pending input")
        if episode.pending_descriptor.correlation_id != correlation_id:
            raise WakeInputRejected("pending input correlation no longer matches")
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        payload_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        observation = WakeObservation(
            observation_id=(
                f"user-input:{episode.episode_id}:"
                f"{correlation_id}:{payload_hash}"
            ),
            kind=WakeConditionKind.USER_INPUT,
            target_ref=correlation_id,
            observed_at=_now_iso(),
            source="user",
            details={"runtime_generation": runtime_generation},
        )
        events = self.observe(observation)
        event = next(
            (
                item
                for item in events
                if item.episode_id == episode.episode_id
                and item.disposition is WakeDisposition.SUSPENDED_READY
            ),
            None,
        )
        if event is None:
            raise WakeInputRejected("pending input no longer matches the live generation")
        queued = QueuedRuntimeInput(
            episode_id=episode.episode_id,
            runtime_generation=runtime_generation,
            payload=dict(payload),
        )
        self._pending_inputs[episode.episode_id] = queued
        return queued

    def take_pending_input(
        self, episode_id: str, *, runtime_generation: str
    ) -> dict[str, Any] | None:
        queued = self._pending_inputs.pop(episode_id, None)
        if queued is None:
            return None
        if queued.runtime_generation != runtime_generation:
            self._store.mark_pending_channel_lost(
                episode_id,
                reason=ReconcileReason.REQUIRES_USER_RESTART,
            )
            return None
        return dict(queued.payload)


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
