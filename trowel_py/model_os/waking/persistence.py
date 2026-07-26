"""WakeObservation 到 journal 与 Task/Episode 状态的原子消费。"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol

from trowel_py.model_os.types import (
    EventEnvelope,
    EventKind,
    EpisodeStatus,
    Provenance,
)
from trowel_py.model_os.waking.matcher import condition_from_waiting, matches_condition
from trowel_py.model_os.waking.models import (
    WakeCondition,
    WakeDisposition,
    WakeEvent,
    WakeObservation,
)


class WakeStore(Protocol):
    _policy_version: str

    def _tx(self): ...

    def replay(self): ...

    def list_events(self, from_seq: int = 0): ...

    def _insert_event_in_tx(self, event: EventEnvelope) -> int | None: ...

    def clear_waiting(self, task_id: str) -> None: ...

    def resolve_episode_wait(
        self, episode_id: str, *, answer_correlation_id: str
    ) -> None: ...

    def mark_pending_channel_lost(self, episode_id: str, *, reason): ...


def _dedupe_key(condition_id: str, observation_id: str) -> str:
    identity = f"{condition_id}\0{observation_id}".encode()
    return hashlib.sha256(identity).hexdigest()


def _payload(event: WakeEvent) -> dict[str, Any]:
    return {
        "wake_id": event.wake_id,
        "condition_id": event.condition_id,
        "observation_id": event.observation_id,
        "task_id": event.task_id,
        "episode_id": event.episode_id,
        "dedupe_key": event.dedupe_key,
        "observed_at": event.observed_at,
        "source": event.source,
        "catchup_policy": event.catchup_policy.value,
        "disposition": event.disposition.value,
        "observation_fingerprint": event.observation_fingerprint,
    }


def _from_payload(payload: dict[str, Any]) -> WakeEvent:
    from trowel_py.model_os.waking.models import (
        WakeCatchupPolicy,
        WakeDisposition,
    )

    return WakeEvent(
        wake_id=payload["wake_id"],
        condition_id=payload["condition_id"],
        observation_id=payload["observation_id"],
        task_id=payload["task_id"],
        episode_id=payload.get("episode_id"),
        dedupe_key=payload["dedupe_key"],
        observed_at=payload["observed_at"],
        source=payload["source"],
        catchup_policy=WakeCatchupPolicy(payload["catchup_policy"]),
        disposition=WakeDisposition(payload["disposition"]),
        observation_fingerprint=payload["observation_fingerprint"],
    )


def _prior(store: WakeStore, observation: WakeObservation) -> tuple[WakeEvent, ...]:
    prior = tuple(
        _from_payload(event.payload)
        for _, event in store.list_events()
        if event.kind == EventKind.WAKE_CONSUMED
        and event.payload.get("observation_id") == observation.observation_id
    )
    if any(
        event.observation_fingerprint != observation.fingerprint for event in prior
    ):
        raise ValueError(
            f"observation_id {observation.observation_id!r} was reused with different content"
        )
    return prior


def _event(
    condition: WakeCondition,
    observation: WakeObservation,
    disposition: WakeDisposition,
) -> WakeEvent:
    dedupe = _dedupe_key(condition.condition_id, observation.observation_id)
    return WakeEvent(
        wake_id=f"wake.{dedupe}",
        condition_id=condition.condition_id,
        observation_id=observation.observation_id,
        task_id=condition.task_id,
        episode_id=condition.episode_id,
        dedupe_key=dedupe,
        observed_at=observation.observed_at,
        source=observation.source,
        catchup_policy=condition.catchup_policy,
        disposition=disposition,
        observation_fingerprint=observation.fingerprint,
    )


def consume_wake(
    store: WakeStore, observation: WakeObservation
) -> tuple[WakeEvent, ...]:
    with store._tx():
        prior = _prior(store, observation)
        if prior:
            return prior
        snapshot = store.replay()
        matches: list[tuple[Any, WakeCondition]] = []
        for task in snapshot.tasks:
            waiting = task.waiting_condition
            if waiting is None:
                continue
            condition = condition_from_waiting(task.task_id, waiting)
            if condition is not None and matches_condition(condition, observation):
                matches.append((task, condition))
        consumed: list[WakeEvent] = []
        for task, condition in matches:
            disposition = WakeDisposition.READY
            if condition.episode_id is not None:
                episode = snapshot.episode_by_id(condition.episode_id)
                if episode is None or episode.pending_descriptor is None:
                    continue
                expected_generation = episode.pending_descriptor.native_generation
                observed_generation = observation.details.get("runtime_generation")
                if expected_generation != observed_generation:
                    from trowel_py.model_os.types import ReconcileReason

                    disposition = WakeDisposition.UNKNOWN_REQUIRES_USER_RESTART
                    wake = _event(condition, observation, disposition)
                    store._insert_event_in_tx(
                        EventEnvelope(
                            event_id=wake.wake_id,
                            kind=EventKind.WAKE_CONSUMED,
                            occurred_at=observation.observed_at,
                            source=observation.source,
                            provenance=Provenance.MACHINE_OBSERVATION,
                            policy_version=store._policy_version,
                            payload=_payload(wake),
                            work_item_id=task.primary_work_item_id,
                            task_id=task.task_id,
                            episode_id=condition.episode_id,
                            correlation_id=episode.pending_descriptor.correlation_id,
                        )
                    )
                    store.mark_pending_channel_lost(
                        condition.episode_id,
                        reason=ReconcileReason.REQUIRES_USER_RESTART,
                    )
                    consumed.append(wake)
                    continue
                if episode.status not in {
                    EpisodeStatus.SUSPENDED_WAITING_INPUT,
                    EpisodeStatus.SUSPENDED_WAITING_APPROVAL,
                }:
                    continue
                disposition = WakeDisposition.SUSPENDED_READY
            wake = _event(condition, observation, disposition)
            store._insert_event_in_tx(
                EventEnvelope(
                    event_id=wake.wake_id,
                    kind=EventKind.WAKE_CONSUMED,
                    occurred_at=observation.observed_at,
                    source=observation.source,
                    provenance=Provenance.MACHINE_OBSERVATION,
                    policy_version=store._policy_version,
                    payload=_payload(wake),
                    work_item_id=task.primary_work_item_id,
                    task_id=task.task_id,
                    episode_id=condition.episode_id,
                    correlation_id=task.waiting_condition.correlation_id,
                )
            )
            if condition.episode_id is not None:
                assert task.waiting_condition.correlation_id is not None
                store.resolve_episode_wait(
                    condition.episode_id,
                    answer_correlation_id=task.waiting_condition.correlation_id,
                )
            else:
                store.clear_waiting(task.task_id)
            consumed.append(wake)
        return tuple(consumed)
