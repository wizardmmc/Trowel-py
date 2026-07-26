"""统一首段与 fresh Episode 的启动、恢复和首轮注册。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from trowel_py.model_os.episode_starting.context import build_episode_context
from trowel_py.model_os.episode_starting.journal import (
    correlation_id,
    read_progress,
    record_intent,
    record_native_compact_degraded,
    record_stage,
    record_terminal,
)
from trowel_py.model_os.episode_starting.models import (
    NativeSessionIdentity,
    StartEpisodeCommand,
    StartProgress,
    StartStage,
)
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import Episode, EpisodeStatus, Lease, WorkItemKind
from trowel_py.model_os.work_broker import (
    ModelTier,
    WorkDenial,
    WorkKind,
    WorkLease,
    WorkRequest,
)
from trowel_py.model_os.yielding import TurnRegistration
from trowel_py.quota.types import Provider


class EpisodeRuntimeAdapter(Protocol):
    async def start_native(
        self, command: StartEpisodeCommand, episode: Episode
    ) -> NativeSessionIdentity: ...

    async def persist_binding(
        self, identity: NativeSessionIdentity
    ) -> NativeSessionIdentity | None: ...

    async def refresh_identity(
        self, identity: NativeSessionIdentity
    ) -> NativeSessionIdentity: ...

    def start_first_turn(
        self, identity: NativeSessionIdentity, text: str
    ) -> AsyncIterator[dict[str, Any]]: ...


@dataclass
class _StartLock:
    lock: asyncio.Lock
    users: int = 0


class StartEpisodeCoordinator:
    def __init__(
        self,
        store: ModelOsStore,
        *,
        broker: Any,
        adapter: EpisodeRuntimeAdapter,
        yield_coordinator: Any,
        fault_hook: Callable[[StartStage], None] | None = None,
    ) -> None:
        self._store = store
        self._broker = broker
        self._adapter = adapter
        self._yield = yield_coordinator
        self._fault_hook = fault_hook or (lambda _stage: None)
        self._start_locks: dict[str, _StartLock] = {}

    def progress(self, idempotency_key: str) -> StartProgress | None:
        return read_progress(self._store, idempotency_key)

    async def start(
        self, command: StartEpisodeCommand
    ) -> AsyncIterator[dict[str, Any]]:
        entry = self._start_locks.setdefault(
            command.work_item_id, _StartLock(asyncio.Lock())
        )
        entry.users += 1
        try:
            async with entry.lock:
                async for event in self._start_serialized(command):
                    yield event
        finally:
            entry.users -= 1
            if entry.users == 0:
                self._start_locks.pop(command.work_item_id, None)

    async def _start_serialized(
        self, command: StartEpisodeCommand
    ) -> AsyncIterator[dict[str, Any]]:
        progress = self.progress(command.idempotency_key)
        self._validate_command(command, progress)
        record_intent(self._store, command)
        if progress is None:
            progress = self.progress(command.idempotency_key)
            assert progress is not None
            self._fault_hook(StartStage.INTENT)
        if progress.stage in {StartStage.TERMINAL, StartStage.UNKNOWN}:
            return
        if progress.stage is StartStage.NATIVE_REQUESTED:
            assert progress.episode_id is not None
            record_terminal(
                self._store, command, episode_id=progress.episode_id, unknown=True
            )
            return
        if progress.stage in {
            StartStage.FIRST_TURN_REQUESTED,
            StartStage.FIRST_TURN_ACCEPTED,
        }:
            assert progress.episode_id is not None
            record_terminal(
                self._store, command, episode_id=progress.episode_id, unknown=True
            )
            return

        lease = self._work_lease(command)
        if isinstance(lease, WorkDenial):
            raise RuntimeError(
                f"WorkBroker denied Episode start: {lease.reason.value}"
            )
        try:
            episode, ownership = self._episode(command, progress)
        except BaseException:
            self._broker.release(lease.lease_id, lease.fencing_token)
            raise

        identity = progress.identity
        if progress.stage is StartStage.INTENT:
            self._broker.begin_call(lease.lease_id, lease.fencing_token)
            record_stage(
                self._store,
                command,
                StartStage.NATIVE_REQUESTED,
                episode_id=episode.episode_id,
                work_lease_id=lease.lease_id,
            )
            self._fault_hook(StartStage.NATIVE_REQUESTED)
            identity = await self._adapter.start_native(command, episode)
            record_stage(
                self._store,
                command,
                StartStage.NATIVE_RESPONDED,
                episode_id=episode.episode_id,
                identity=identity,
                work_lease_id=lease.lease_id,
            )
            self._fault_hook(StartStage.NATIVE_RESPONDED)

        if identity is None:
            raise RuntimeError("native response did not provide durable identity")
        progress = self.progress(command.idempotency_key)
        assert progress is not None
        if progress.stage is StartStage.NATIVE_RESPONDED:
            persisted_identity = await self._adapter.persist_binding(identity)
            if persisted_identity is not None:
                identity = persisted_identity
            self._bind(
                command,
                episode,
                ownership,
                identity,
                activate=identity.native_session_id is not None,
            )
            record_stage(
                self._store,
                command,
                StartStage.BINDING_PERSISTED,
                episode_id=episode.episode_id,
                identity=identity,
                work_lease_id=lease.lease_id,
            )
            self._fault_hook(StartStage.BINDING_PERSISTED)

        context = build_episode_context(
            self._store,
            command,
            episode_id=episode.episode_id,
            native_session_id=identity.native_session_id,
        )
        accepted = False
        turn_id: str | None = None
        terminal = False
        compact_degraded = progress.degraded_native_compact
        record_stage(
            self._store,
            command,
            StartStage.FIRST_TURN_REQUESTED,
            episode_id=episode.episode_id,
            identity=identity,
            work_lease_id=lease.lease_id,
        )
        self._fault_hook(StartStage.FIRST_TURN_REQUESTED)
        async for event in self._adapter.start_first_turn(identity, context.render()):
            event_type = str(event.get("type", ""))
            raw_turn_id = event.get("turn_id")
            if isinstance(raw_turn_id, str) and raw_turn_id:
                turn_id = raw_turn_id
            if not accepted and self._is_accepted(command.runtime, event_type):
                if turn_id is None:
                    raise RuntimeError("accepted first turn has no turn_id")
                refreshed = await self._adapter.refresh_identity(identity)
                identity = refreshed
                self._bind(
                    command, episode, ownership, identity, activate=True
                )
                record_stage(
                    self._store,
                    command,
                    StartStage.FIRST_TURN_ACCEPTED,
                    episode_id=episode.episode_id,
                    identity=identity,
                    turn_id=turn_id,
                    work_lease_id=lease.lease_id,
                )
                await self._yield.register_turn(
                    TurnRegistration(
                        session_id=identity.agent_session_id,
                        episode_id=episode.episode_id,
                        runtime=command.runtime,
                        turn_id=turn_id,
                        generation=identity.runtime_generation,
                        native_session_id=identity.native_session_id or "",
                        ownership_lease_id=ownership.lease_id,
                        ownership_owner=ownership.owner,
                        ownership_token=ownership.fencing_token,
                        work_lease_id=lease.lease_id,
                    )
                )
                accepted = True
                self._fault_hook(StartStage.FIRST_TURN_ACCEPTED)
            if event_type == "compaction" and not compact_degraded:
                record_native_compact_degraded(
                    self._store, command, episode_id=episode.episode_id
                )
                compact_degraded = True
            yield event
            if event_type in {"finished", "interrupted", "error", "session_exited"}:
                terminal = True
        if not accepted or not terminal:
            record_terminal(
                self._store, command, episode_id=episode.episode_id, unknown=True
            )
            return
        record_terminal(
            self._store, command, episode_id=episode.episode_id, unknown=False
        )
        self._fault_hook(StartStage.TERMINAL)

    def _episode(
        self, command: StartEpisodeCommand, progress: StartProgress
    ) -> tuple[Episode, Lease]:
        return self._store.start_episode(
            work_item_id=command.work_item_id,
            owner=command.owner,
            ttl_seconds=command.ownership_ttl_seconds,
            idempotency_key=f"episode:{command.idempotency_key}",
            task_id=command.task_id,
            previous_snapshot_ref=command.previous_snapshot_ref,
        )

    def _validate_command(
        self, command: StartEpisodeCommand, progress: StartProgress | None
    ) -> None:
        snapshot = self._store.read_snapshot()
        work_item = next(
            (
                item
                for item in snapshot.work_items
                if item.work_item_id == command.work_item_id
            ),
            None,
        )
        if work_item is None:
            raise ValueError(f"unknown work_item_id={command.work_item_id!r}")
        if work_item.task_id != command.task_id:
            raise ValueError("task_id does not match WorkItem")
        if work_item.session_purpose is not command.session_purpose:
            raise ValueError("session_purpose does not match WorkItem policy")
        if work_item.memory_eligibility is not command.memory_eligibility:
            raise ValueError("memory_eligibility does not match WorkItem policy")
        allowed_episode_id = progress.episode_id if progress is not None else None
        other_open = tuple(
            episode
            for episode in snapshot.non_terminal_episodes_for_work_item(
                command.work_item_id
            )
            if episode.episode_id != allowed_episode_id
        )
        if other_open:
            raise ValueError("WorkItem already has a non-terminal Episode")
        if command.previous_snapshot_ref is not None:
            previous = snapshot.episode_by_id(command.previous_episode_id)
            if previous is None or previous.work_item_id != command.work_item_id:
                raise ValueError("previous Episode does not belong to WorkItem")
            if previous.status is not EpisodeStatus.CLOSED:
                raise ValueError("previous Episode must be closed before fresh start")
            if previous.last_snapshot_ref != command.previous_snapshot_ref:
                raise ValueError("previous_snapshot_ref is not the committed head")
            self._store.read_episode_snapshot(command.previous_snapshot_ref)

    def _work_lease(self, command: StartEpisodeCommand) -> WorkLease | WorkDenial:
        snapshot = self._store.read_snapshot()
        work_item = next(
            item
            for item in snapshot.work_items
            if item.work_item_id == command.work_item_id
        )
        if work_item.kind is WorkItemKind.MAINTENANCE:
            kind = WorkKind.MAINTENANCE
        elif work_item.kind is WorkItemKind.TASK:
            kind = WorkKind.FOREGROUND
        else:
            kind = WorkKind.DEFAULT
        provider = Provider.CODEX if command.runtime == "codex" else Provider.GLM
        return self._broker.request(
            WorkRequest(
                kind=kind,
                provider=provider,
                model_tier=ModelTier.DEEP,
                task_id=command.task_id,
                work_item_id=command.work_item_id,
                idempotency_key=f"work:{command.idempotency_key}",
            )
        )

    def _bind(
        self,
        command: StartEpisodeCommand,
        episode: Episode,
        ownership: Lease,
        identity: NativeSessionIdentity,
        *,
        activate: bool,
    ) -> None:
        self._store.bind_episode_runtime(
            episode.episode_id,
            expected_lease_id=ownership.lease_id,
            expected_owner=ownership.owner,
            expected_token=ownership.fencing_token,
            agent_session_id=identity.agent_session_id,
            runtime=identity.runtime,
            native_session_id=identity.native_session_id,
            runtime_generation=identity.runtime_generation,
            runtime_pid=identity.runtime_pid,
            runtime_pgid=identity.runtime_pgid,
            correlation_id=correlation_id(command.idempotency_key),
            activate=activate,
        )

    @staticmethod
    def _is_accepted(runtime: str, event_type: str) -> bool:
        if runtime == "codex":
            return event_type == "turn_start"
        return event_type == "session_started"
