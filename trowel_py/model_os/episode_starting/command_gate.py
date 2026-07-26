"""旧 Agent API 对 Model OS 托管 Session 的 Kernel command 门禁。"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Mapping
from uuid import uuid4

from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import (
    DecisionDisposition,
    DecisionRecord,
    EpisodeRuntimeBinding,
    EventEnvelope,
    EventKind,
    Lease,
    Provenance,
    WorkItemKind,
)
from trowel_py.model_os.work_broker import (
    DenialReason,
    ModelTier,
    WorkDenial,
    WorkKind,
    WorkLease,
    WorkRequest,
)
from trowel_py.model_os.yielding import ForceYieldReason, TurnRegistration
from trowel_py.model_os.yielding.journal import now_iso
from trowel_py.quota.types import Provider
from trowel_py.model_os.routing.journal import route_tier_for_episode


def _hash(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


@dataclass
class CommandTicket:
    command_id: str
    decision_id: str
    correlation_id: str
    command_kind: str
    binding: EpisodeRuntimeBinding
    ownership: Lease
    work_lease: WorkLease | None = None
    turn_registered: bool = False


class ModelOsCommandGate:
    def __init__(
        self,
        store: ModelOsStore,
        *,
        broker: Any,
        yield_coordinator: Any,
        preempt_started_incubation: Callable[[Provider], Awaitable[bool]]
        | None = None,
    ) -> None:
        self._store = store
        self._broker = broker
        self._yield = yield_coordinator
        self._preempt_started_incubation = preempt_started_incubation

    async def before_send(
        self, session_id: str, text: str
    ) -> CommandTicket | None:
        binding = self._store.episode_runtime_binding_for_session(session_id)
        if binding is None:
            return None
        ownership = self._ownership(binding.episode_id)
        work_lease = self._request_work(binding)
        if (
            isinstance(work_lease, WorkDenial)
            and work_lease.reason is DenialReason.SLOT_BUSY
            and self._work_kind(binding) is WorkKind.FOREGROUND
            and self._preempt_started_incubation is not None
        ):
            provider = (
                Provider.CODEX if binding.runtime == "codex" else Provider.GLM
            )
            if await self._preempt_started_incubation(provider):
                work_lease = self._request_work(binding)
        if isinstance(work_lease, WorkDenial):
            raise RuntimeError(
                f"WorkBroker denied managed send: {work_lease.reason.value}"
            )
        self._broker.begin_call(work_lease.lease_id, work_lease.fencing_token)
        try:
            return self._intent(
                binding,
                ownership,
                command_kind="episode.send",
                args=text,
                work_lease=work_lease,
            )
        except BaseException:
            self._broker.release(work_lease.lease_id, work_lease.fencing_token)
            raise

    def before_control(
        self, session_id: str, *, command_kind: str, args: str
    ) -> CommandTicket | None:
        binding = self._store.episode_runtime_binding_for_session(session_id)
        if binding is None:
            return None
        return self._intent(
            binding,
            self._ownership(binding.episode_id),
            command_kind=command_kind,
            args=args,
        )

    async def observe_send_event(
        self,
        ticket: CommandTicket | None,
        event: Mapping[str, Any],
        *,
        hub: Any,
    ) -> None:
        if (
            ticket is None
            or ticket.turn_registered
            or event.get("type") != "turn_start"
        ):
            return
        turn_id = event.get("turn_id")
        if not isinstance(turn_id, str) or not turn_id:
            raise RuntimeError("managed turn_start has no turn_id")
        identity = hub.native_identity(ticket.binding.agent_session_id)
        native_session_id = identity.native_session_id
        if not native_session_id:
            return
        work_lease = ticket.work_lease
        assert work_lease is not None
        await self._yield.register_turn(
            TurnRegistration(
                session_id=ticket.binding.agent_session_id,
                episode_id=ticket.binding.episode_id,
                runtime=ticket.binding.runtime,
                turn_id=turn_id,
                generation=identity.runtime_generation,
                native_session_id=native_session_id,
                ownership_lease_id=ticket.ownership.lease_id,
                ownership_owner=ticket.ownership.owner,
                ownership_token=ticket.ownership.fencing_token,
                work_lease_id=work_lease.lease_id,
            )
        )
        ticket.turn_registered = True

    def complete(
        self,
        ticket: CommandTicket | None,
        *,
        unknown: bool = False,
        result_code: str = "runtime_terminal",
    ) -> None:
        if ticket is None:
            return
        self._store.append_event(
            EventEnvelope(
                event_id=f"event.{ticket.command_id}.{'unknown' if unknown else 'result'}",
                kind=EventKind.COMMAND_UNKNOWN if unknown else EventKind.COMMAND_RESULT,
                occurred_at=now_iso(),
                source="episode_runner",
                provenance=Provenance.MACHINE_OBSERVATION,
                policy_version="episode-command-v1",
                payload=(
                    {
                        "unknown_code": "unknown_requires_reconcile",
                        "evidence_refs": [],
                    }
                    if unknown
                    else {"result_code": result_code, "evidence_refs": []}
                ),
                episode_id=ticket.binding.episode_id,
                native_session_id=ticket.binding.native_session_id,
                cause_id=ticket.decision_id,
                correlation_id=ticket.correlation_id,
            )
        )
        if ticket.work_lease is not None and not ticket.turn_registered and not unknown:
            self._broker.release(
                ticket.work_lease.lease_id, ticket.work_lease.fencing_token
            )

    async def interrupt(self, session_id: str, *, hub: Any) -> bool:
        binding = self._store.episode_runtime_binding_for_session(session_id)
        if binding is None:
            return False
        turn_id = hub.current_turn_id(session_id) or "no-active-turn"
        generation = hub.native_identity(session_id).runtime_generation
        await self._yield.request_forced(
            session_id,
            ForceYieldReason.USER_PREEMPT,
            expected_turn_id=turn_id,
            expected_generation=generation,
        )
        return True

    def _intent(
        self,
        binding: EpisodeRuntimeBinding,
        ownership: Lease,
        *,
        command_kind: str,
        args: str,
        work_lease: WorkLease | None = None,
    ) -> CommandTicket:
        identity = uuid4().hex
        decision_id = f"decision.{command_kind}.{identity}"
        correlation = f"command.{command_kind}.{identity}"
        snapshot = self._store.read_snapshot()
        episode = snapshot.episode_by_id(binding.episode_id)
        if episode is None:
            raise RuntimeError("managed Episode disappeared")
        decision = DecisionRecord(
            decision_id=decision_id,
            kind=command_kind,
            disposition=DecisionDisposition.EXECUTE,
            decided_at=now_iso(),
            signals={"refs": []},
            candidates=["forward", "reject"],
            choice="forward",
            reason="legacy_api_forward",
            policy_version="episode-command-v1",
            work_item_id=episode.work_item_id,
            task_id=episode.task_id,
            episode_id=episode.episode_id,
            correlation_id=correlation,
        )
        intent = EventEnvelope(
            event_id=f"event.{command_kind}.intent.{identity}",
            kind=EventKind.COMMAND_INTENT,
            occurred_at=now_iso(),
            source="kernel",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="episode-command-v1",
            payload={
                "command_kind": command_kind,
                "target_ref": f"episode.{episode.episode_id}",
                "idempotency_key_hash": _hash(correlation),
                "args_hash": _hash(args),
            },
            work_item_id=episode.work_item_id,
            task_id=episode.task_id,
            episode_id=episode.episode_id,
            native_session_id=binding.native_session_id,
            cause_id=decision_id,
            correlation_id=correlation,
        )
        self._store.append_decision_with_intent(decision, intent)
        return CommandTicket(
            command_id=f"{command_kind}.{identity}",
            decision_id=decision_id,
            correlation_id=correlation,
            command_kind=command_kind,
            binding=binding,
            ownership=ownership,
            work_lease=work_lease,
        )

    def _ownership(self, episode_id: str) -> Lease:
        snapshot = self._store.read_snapshot()
        lease = next(
            (
                item
                for item in snapshot.active_leases
                if item.resource_type == "episode_ownership"
                and item.resource_id == episode_id
            ),
            None,
        )
        if lease is None:
            raise RuntimeError("managed Episode has no active ownership lease")
        return lease

    def _request_work(self, binding: EpisodeRuntimeBinding) -> WorkLease | WorkDenial:
        kind, episode = self._work_kind_and_episode(binding)
        return self._broker.request(
            WorkRequest(
                kind=kind,
                provider=(
                    Provider.CODEX if binding.runtime == "codex" else Provider.GLM
                ),
                model_tier=(
                    route_tier_for_episode(self._store, binding.episode_id)
                    or ModelTier.DEEP
                ),
                task_id=episode.task_id,
                work_item_id=episode.work_item_id,
                idempotency_key=f"turn:{uuid4().hex}",
            )
        )

    def _work_kind(self, binding: EpisodeRuntimeBinding) -> WorkKind:
        return self._work_kind_and_episode(binding)[0]

    def _work_kind_and_episode(self, binding: EpisodeRuntimeBinding):
        snapshot = self._store.read_snapshot()
        episode = snapshot.episode_by_id(binding.episode_id)
        if episode is None:
            raise RuntimeError("managed Episode disappeared")
        work_item = next(
            item
            for item in snapshot.work_items
            if item.work_item_id == episode.work_item_id
        )
        if work_item.kind is WorkItemKind.MAINTENANCE:
            kind = WorkKind.MAINTENANCE
        elif work_item.kind is WorkItemKind.TASK:
            kind = WorkKind.FOREGROUND
        elif work_item.kind is WorkItemKind.INCUBATION:
            raise RuntimeError("incubation session does not accept additional turns")
        else:
            kind = WorkKind.DEFAULT
        return kind, episode
