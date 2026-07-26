"""StartEpisode 的命令账本与崩溃阶段回放。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from trowel_py.model_os.episode_starting.models import (
    NativeSessionIdentity,
    StartEpisodeCommand,
    StartProgress,
    StartStage,
)
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import (
    DecisionDisposition,
    DecisionRecord,
    EventEnvelope,
    EventKind,
    Provenance,
)
from trowel_py.model_os.yielding.journal import now_iso

_STAGE_KINDS = {
    StartStage.OWNERSHIP_ACQUIRED: "episode.start.ownership_acquired",
    StartStage.NATIVE_REQUESTED: "episode.start.native_requested",
    StartStage.NATIVE_RESPONDED: "episode.start.native_responded",
    StartStage.BINDING_PERSISTED: "episode.start.binding_persisted",
    StartStage.FIRST_TURN_REQUESTED: "episode.start.first_turn_requested",
    StartStage.FIRST_TURN_ACCEPTED: "episode.start.first_turn_accepted",
}

_NATIVE_COMPACT_DEGRADED = "episode.start.native_compact_degraded"


def token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def correlation_id(idempotency_key: str) -> str:
    return f"command.episode.start.{token(idempotency_key)}"


def _hash(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _command_fingerprint(command: StartEpisodeCommand) -> str:
    payload = asdict(command)
    if command.schedule_decision_id is None:
        payload.pop("schedule_decision_id")
    payload["session_purpose"] = command.session_purpose.value
    payload["memory_eligibility"] = command.memory_eligibility.value
    payload["route_preference"] = command.route_preference.value
    payload["route_mandatory_markers"] = [
        item.value for item in command.route_mandatory_markers
    ]
    payload["route_pre_route_markers"] = [
        item.value for item in command.route_pre_route_markers
    ]
    payload["selected_model_tier"] = (
        command.selected_model_tier.value
        if command.selected_model_tier is not None
        else None
    )
    ref = command.previous_snapshot_ref
    payload["previous_snapshot_ref"] = (
        {
            "episode_id": ref.episode_id,
            "version": ref.version,
            "committed_event_id": ref.committed_event_id,
            "payload_hash": ref.payload_hash,
        }
        if ref is not None
        else None
    )
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def record_intent(store: ModelOsStore, command: StartEpisodeCommand) -> str:
    identity = token(command.idempotency_key)
    corr = correlation_id(command.idempotency_key)
    decision_id = f"decision.episode.start.{identity}"
    decision = DecisionRecord(
        decision_id=decision_id,
        kind="episode.start",
        disposition=DecisionDisposition.EXECUTE,
        decided_at=now_iso(),
        signals={"refs": [command.route_decision_id]},
        candidates=[command.runtime],
        choice=command.runtime,
        reason="start_work_item_episode",
        policy_version="episode-start-v1",
        work_item_id=command.work_item_id,
        task_id=command.task_id,
        cause_id=command.route_decision_id,
        correlation_id=corr,
    )
    intent = EventEnvelope(
        event_id=f"event.episode.start.intent.{identity}",
        kind=EventKind.COMMAND_INTENT,
        occurred_at=now_iso(),
        source="kernel",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="episode-start-v1",
        payload={
            "command_kind": "episode.start",
            "target_ref": f"work_item.{command.work_item_id}",
            "idempotency_key_hash": _hash(command.idempotency_key),
            "args_hash": _hash(_command_fingerprint(command)),
        },
        work_item_id=command.work_item_id,
        task_id=command.task_id,
        cause_id=decision_id,
        correlation_id=corr,
    )
    store.append_decision_with_intent(decision, intent)
    return corr


def record_stage(
    store: ModelOsStore,
    command: StartEpisodeCommand,
    stage: StartStage,
    *,
    episode_id: str,
    identity: NativeSessionIdentity | None = None,
    turn_id: str | None = None,
    work_lease_id: str | None = None,
) -> None:
    kind = _STAGE_KINDS[stage]
    corr = correlation_id(command.idempotency_key)
    payload: dict[str, object] = {"stage": stage.value}
    if identity is not None:
        payload["identity"] = asdict(identity)
    if turn_id is not None:
        payload["turn_id"] = turn_id
    if work_lease_id is not None:
        payload["work_lease_id"] = work_lease_id
    event_identity = (
        token(f"{corr}:{json.dumps(asdict(identity), sort_keys=True)}")
        if identity is not None
        else token(corr)
    )
    store.append_event(
        EventEnvelope(
            event_id=f"event.{kind}.{event_identity}",
            kind=kind,
            occurred_at=now_iso(),
            source="episode_runner",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="episode-start-v1",
            payload=payload,
            work_item_id=command.work_item_id,
            task_id=command.task_id,
            episode_id=episode_id,
            native_session_id=identity.native_session_id if identity else None,
            cause_id=f"decision.episode.start.{token(command.idempotency_key)}",
            correlation_id=corr,
        )
    )


def record_terminal(
    store: ModelOsStore,
    command: StartEpisodeCommand,
    *,
    episode_id: str,
    unknown: bool,
) -> None:
    corr = correlation_id(command.idempotency_key)
    store.append_event(
        EventEnvelope(
            event_id=(
                f"event.episode.start.{'unknown' if unknown else 'result'}."
                f"{token(corr)}"
            ),
            kind=EventKind.COMMAND_UNKNOWN if unknown else EventKind.COMMAND_RESULT,
            occurred_at=now_iso(),
            source="episode_runner",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="episode-start-v1",
            payload=(
                {
                    "unknown_code": "unknown_requires_reconcile",
                    "evidence_refs": ["possible_orphan_native_session"],
                }
                if unknown
                else {"result_code": "terminal_observed", "evidence_refs": []}
            ),
            work_item_id=command.work_item_id,
            task_id=command.task_id,
            episode_id=episode_id,
            cause_id=f"decision.episode.start.{token(command.idempotency_key)}",
            correlation_id=corr,
        )
    )


def record_native_compact_degraded(
    store: ModelOsStore,
    command: StartEpisodeCommand,
    *,
    episode_id: str,
) -> None:
    corr = correlation_id(command.idempotency_key)
    store.append_event(
        EventEnvelope(
            event_id=f"event.{_NATIVE_COMPACT_DEGRADED}.{token(corr)}",
            kind=_NATIVE_COMPACT_DEGRADED,
            occurred_at=now_iso(),
            source="episode_runner",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="episode-start-v1",
            payload={"fresh_guarantee": "degraded_by_native_compact"},
            work_item_id=command.work_item_id,
            task_id=command.task_id,
            episode_id=episode_id,
            cause_id=f"decision.episode.start.{token(command.idempotency_key)}",
            correlation_id=corr,
        )
    )


def read_progress(store: ModelOsStore, idempotency_key: str) -> StartProgress | None:
    corr = correlation_id(idempotency_key)
    matching = [
        event for _, event in store.list_events() if event.correlation_id == corr
    ]
    if not matching:
        return None
    stage = StartStage.INTENT
    episode_id = None
    identity = None
    turn_id = None
    degraded_native_compact = False
    kind_to_stage = {value: key for key, value in _STAGE_KINDS.items()}
    for event in matching:
        if event.episode_id is not None:
            episode_id = event.episode_id
        if event.kind in kind_to_stage:
            stage = kind_to_stage[event.kind]
            raw_identity = event.payload.get("identity")
            if isinstance(raw_identity, dict):
                identity = NativeSessionIdentity(**raw_identity)
            raw_turn = event.payload.get("turn_id")
            if isinstance(raw_turn, str):
                turn_id = raw_turn
        elif event.kind == EventKind.COMMAND_UNKNOWN:
            stage = StartStage.UNKNOWN
        elif event.kind == EventKind.COMMAND_RESULT:
            stage = StartStage.TERMINAL
        elif event.kind == _NATIVE_COMPACT_DEGRADED:
            degraded_native_compact = True
    if episode_id is not None:
        binding = store.episode_runtime_binding(episode_id)
        if binding is not None:
            identity = NativeSessionIdentity(
                agent_session_id=binding.agent_session_id,
                runtime=binding.runtime,
                native_session_id=binding.native_session_id,
                runtime_generation=binding.runtime_generation,
                runtime_pid=binding.runtime_pid,
                runtime_pgid=binding.runtime_pgid,
            )
    return StartProgress(
        idempotency_key=idempotency_key,
        correlation_id=corr,
        stage=stage,
        episode_id=episode_id,
        identity=identity,
        turn_id=turn_id,
        degraded_native_compact=degraded_native_compact,
    )
