from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from trowel_py.model_os import store as store_module
from tests.model_os._episode_helpers import make_cooperative_snapshot
from trowel_py.model_os.episode_starting import (
    NativeSessionIdentity,
    StartEpisodeCommand,
    StartEpisodeCoordinator,
    StartStage,
)
from trowel_py.model_os.types import (
    EventEnvelope,
    EventKind,
    MemoryEligibility,
    Provenance,
    SessionPurpose,
    WorkItemKind,
    WorkItemStatus,
)
from trowel_py.model_os.work_broker import WorkLease
from trowel_py.model_os.work_broker import DenialReason, WorkDenial
from trowel_py.quota.types import Provider


class FakeBroker:
    def __init__(self) -> None:
        self.requests = []
        self.started = []
        self.released = []

    def request(self, request):
        self.requests.append(request)
        return WorkLease(
            lease_id="work-lease-1",
            slot="codex:account:0",
            provider=Provider.CODEX,
            account_id="codex",
            work_kind=request.kind,
            model_tier=request.model_tier,
            granted_cap=None,
            acquired_at="2026-07-26T00:00:00+00:00",
            expires_at="2026-07-26T00:10:00+00:00",
            fencing_token=1,
            task_id=request.task_id,
            work_item_id=request.work_item_id,
        )

    def begin_call(self, lease_id, token):
        self.started.append((lease_id, token))

    def release(self, lease_id, token):
        self.released.append((lease_id, token))
        return True


class DenyingBroker(FakeBroker):
    def request(self, request):
        self.requests.append(request)
        return WorkDenial(DenialReason.SLOT_BUSY, "test capacity denial")


class FakeYieldCoordinator:
    def __init__(self) -> None:
        self.registrations = []

    async def register_turn(self, registration) -> None:
        self.registrations.append(registration)


class InjectedCrash(BaseException):
    pass


class FakeAdapter:
    def __init__(self, *, runtime: str = "codex") -> None:
        self.runtime = runtime
        self.start_calls = 0
        self.persisted = []
        self.turn_calls = 0
        self.crash_in_start = False
        self.crash_before_accept = False
        self.emit_compaction = False
        self.identity = NativeSessionIdentity(
            agent_session_id="agent-session-1",
            runtime=runtime,
            native_session_id="thread-1" if runtime == "codex" else None,
            runtime_generation=(
                "codex-connection-3" if runtime == "codex" else "cc-process-1"
            ),
            runtime_pid=321,
            runtime_pgid=321,
        )

    async def start_native(self, command, episode):
        self.start_calls += 1
        if self.crash_in_start:
            raise InjectedCrash()
        return self.identity

    async def persist_binding(self, identity):
        self.persisted.append(identity)

    async def refresh_identity(self, identity):
        if self.runtime == "claude_code":
            return replace(identity, native_session_id="cc-native-1")
        return identity

    async def start_first_turn(self, identity, text):
        self.turn_calls += 1
        if self.crash_before_accept:
            raise InjectedCrash()
        if self.runtime == "codex":
            yield {
                "session_id": identity.agent_session_id,
                "runtime": "codex",
                "type": "turn_start",
                "turn_id": "turn-1",
                "payload": {},
            }
        else:
            yield {
                "session_id": identity.agent_session_id,
                "runtime": "claude_code",
                "type": "turn_start",
                "turn_id": "turn-1",
                "payload": {},
            }
            yield {
                "session_id": identity.agent_session_id,
                "runtime": "claude_code",
                "type": "session_started",
                "turn_id": "turn-1",
                "payload": {"cc_session_id": "cc-native-1"},
            }
        if self.emit_compaction:
            yield {
                "session_id": identity.agent_session_id,
                "runtime": self.runtime,
                "type": "compaction",
                "turn_id": "turn-1",
                "payload": {"phase": "completed"},
            }
        yield {
            "session_id": identity.agent_session_id,
            "runtime": self.runtime,
            "type": "finished",
            "turn_id": "turn-1",
            "payload": {},
        }


def _command(work_item_id: str, *, runtime: str = "codex") -> StartEpisodeCommand:
    return StartEpisodeCommand(
        work_item_id=work_item_id,
        task_id=None,
        previous_episode_id=None,
        previous_snapshot_ref=None,
        runtime=runtime,
        model="deep-model",
        effort="high",
        memory_enabled=False,
        profile_enabled=False,
        workdir="/workspace/project",
        session_purpose=SessionPurpose.DEFAULT,
        memory_eligibility=MemoryEligibility.INELIGIBLE,
        permission="danger-full-access",
        idempotency_key="start-command-1",
    )


def _bare_system_work_item(store) -> str:
    item = store.create_work_item(
        kind=WorkItemKind.DEFAULT,
        owner_ref="system.default",
        task_id=None,
        session_purpose=SessionPurpose.DEFAULT,
        memory_eligibility=MemoryEligibility.INELIGIBLE,
    )
    store.append_event(
        EventEnvelope(
            event_id=f"wi.run.{item.work_item_id}",
            kind=EventKind.WORK_ITEM_STATUS_CHANGED,
            occurred_at=store_module._now_iso(),
            source="test",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="v0",
            payload={"new_status": WorkItemStatus.RUNNING.value},
            work_item_id=item.work_item_id,
        )
    )
    return item.work_item_id


async def _collect(coordinator, command):
    return [event async for event in coordinator.start(command)]


@pytest.mark.anyio
@pytest.mark.parametrize("runtime", ["claude_code", "codex"])
async def test_first_and_fresh_turn_use_same_start_command(store, runtime) -> None:
    work_item_id = _bare_system_work_item(store)
    adapter = FakeAdapter(runtime=runtime)
    broker = FakeBroker()
    yielding = FakeYieldCoordinator()
    coordinator = StartEpisodeCoordinator(
        store, broker=broker, adapter=adapter, yield_coordinator=yielding
    )

    events = await _collect(coordinator, _command(work_item_id, runtime=runtime))

    assert events[-1]["type"] == "finished"
    assert adapter.start_calls == adapter.turn_calls == 1
    assert broker.started == [("work-lease-1", 1)]
    assert yielding.registrations[0].native_session_id in {"thread-1", "cc-native-1"}
    assert coordinator.progress("start-command-1").stage is StartStage.TERMINAL


@pytest.mark.anyio
async def test_cc_does_not_run_recreating_persist_after_payload_acceptance(store) -> None:
    work_item_id = _bare_system_work_item(store)
    adapter = FakeAdapter(runtime="claude_code")
    coordinator = StartEpisodeCoordinator(
        store,
        broker=FakeBroker(),
        adapter=adapter,
        yield_coordinator=FakeYieldCoordinator(),
    )

    events = await _collect(
        coordinator, _command(work_item_id, runtime="claude_code")
    )

    assert events[-1]["type"] == "finished"
    assert len(adapter.persisted) == 1
    binding = store.episode_runtime_binding(
        coordinator.progress("start-command-1").episode_id
    )
    assert binding is not None and binding.native_session_id == "cc-native-1"


@pytest.mark.anyio
async def test_work_denial_keeps_intent_retryable_without_creating_episode(store) -> None:
    work_item_id = _bare_system_work_item(store)
    broker = DenyingBroker()
    coordinator = StartEpisodeCoordinator(
        store,
        broker=broker,
        adapter=FakeAdapter(),
        yield_coordinator=FakeYieldCoordinator(),
    )
    command = _command(work_item_id)

    with pytest.raises(RuntimeError, match="WorkBroker denied Episode start"):
        await _collect(coordinator, command)

    progress = coordinator.progress(command.idempotency_key)
    assert progress is not None and progress.stage is StartStage.INTENT
    assert progress.episode_id is None
    assert store.read_snapshot().episodes == ()


@pytest.mark.anyio
async def test_concurrent_duplicate_start_calls_native_once(store) -> None:
    work_item_id = _bare_system_work_item(store)
    adapter = FakeAdapter()
    entered = asyncio.Event()
    release = asyncio.Event()
    original_start = adapter.start_native

    async def blocked_start(command, episode):
        entered.set()
        await release.wait()
        return await original_start(command, episode)

    adapter.start_native = blocked_start
    coordinator = StartEpisodeCoordinator(
        store,
        broker=FakeBroker(),
        adapter=adapter,
        yield_coordinator=FakeYieldCoordinator(),
    )
    command = _command(work_item_id)

    first = asyncio.create_task(_collect(coordinator, command))
    await entered.wait()
    second = asyncio.create_task(_collect(coordinator, command))
    await asyncio.sleep(0)
    release.set()
    first_events, second_events = await asyncio.gather(first, second)

    assert first_events[-1]["type"] == "finished"
    assert second_events == []
    assert adapter.start_calls == 1
    assert len(store.read_snapshot().episodes) == 1


@pytest.mark.anyio
async def test_request_sent_without_response_is_unknown_and_never_retried(store) -> None:
    work_item_id = _bare_system_work_item(store)
    adapter = FakeAdapter()
    adapter.crash_in_start = True
    coordinator = StartEpisodeCoordinator(
        store,
        broker=FakeBroker(),
        adapter=adapter,
        yield_coordinator=FakeYieldCoordinator(),
    )
    command = _command(work_item_id)

    with pytest.raises(InjectedCrash):
        await _collect(coordinator, command)
    assert coordinator.progress(command.idempotency_key).stage is StartStage.NATIVE_REQUESTED

    adapter.crash_in_start = False
    events = await _collect(coordinator, command)
    assert adapter.start_calls == 1
    assert events == []
    assert coordinator.progress(command.idempotency_key).stage is StartStage.UNKNOWN


@pytest.mark.anyio
async def test_durable_response_can_resume_at_binding_without_new_native_start(store) -> None:
    work_item_id = _bare_system_work_item(store)
    adapter = FakeAdapter()
    crash_once = True

    def fault(stage):
        nonlocal crash_once
        if crash_once and stage is StartStage.NATIVE_RESPONDED:
            crash_once = False
            raise InjectedCrash()

    coordinator = StartEpisodeCoordinator(
        store,
        broker=FakeBroker(),
        adapter=adapter,
        yield_coordinator=FakeYieldCoordinator(),
        fault_hook=fault,
    )
    command = _command(work_item_id)

    with pytest.raises(InjectedCrash):
        await _collect(coordinator, command)
    events = await _collect(coordinator, command)

    assert events[-1]["type"] == "finished"
    assert adapter.start_calls == 1
    assert len(adapter.persisted) == 1


@pytest.mark.anyio
async def test_first_turn_accepted_without_terminal_is_unknown_and_not_replayed(store) -> None:
    work_item_id = _bare_system_work_item(store)
    adapter = FakeAdapter()
    crash_once = True

    def fault(stage):
        nonlocal crash_once
        if crash_once and stage is StartStage.FIRST_TURN_ACCEPTED:
            crash_once = False
            raise InjectedCrash()

    coordinator = StartEpisodeCoordinator(
        store,
        broker=FakeBroker(),
        adapter=adapter,
        yield_coordinator=FakeYieldCoordinator(),
        fault_hook=fault,
    )
    command = _command(work_item_id)

    with pytest.raises(InjectedCrash):
        await _collect(coordinator, command)
    events = await _collect(coordinator, command)

    assert events == []
    assert adapter.turn_calls == 1
    assert coordinator.progress(command.idempotency_key).stage is StartStage.UNKNOWN


@pytest.mark.anyio
async def test_first_turn_request_without_acceptance_is_unknown_and_not_replayed(store) -> None:
    work_item_id = _bare_system_work_item(store)
    adapter = FakeAdapter(runtime="claude_code")
    adapter.crash_before_accept = True
    coordinator = StartEpisodeCoordinator(
        store,
        broker=FakeBroker(),
        adapter=adapter,
        yield_coordinator=FakeYieldCoordinator(),
    )
    command = _command(work_item_id, runtime="claude_code")

    with pytest.raises(InjectedCrash):
        await _collect(coordinator, command)
    assert coordinator.progress(command.idempotency_key).stage is StartStage.FIRST_TURN_REQUESTED

    adapter.crash_before_accept = False
    assert await _collect(coordinator, command) == []
    assert adapter.turn_calls == 1
    assert coordinator.progress(command.idempotency_key).stage is StartStage.UNKNOWN


@pytest.mark.anyio
async def test_intent_without_request_retries_same_command_identity(store) -> None:
    work_item_id = _bare_system_work_item(store)
    crash_once = True

    def fault(stage):
        nonlocal crash_once
        if crash_once and stage is StartStage.INTENT:
            crash_once = False
            raise InjectedCrash()

    adapter = FakeAdapter()
    coordinator = StartEpisodeCoordinator(
        store,
        broker=FakeBroker(),
        adapter=adapter,
        yield_coordinator=FakeYieldCoordinator(),
        fault_hook=fault,
    )
    command = _command(work_item_id)

    with pytest.raises(InjectedCrash):
        await _collect(coordinator, command)
    events = await _collect(coordinator, command)

    assert events[-1]["type"] == "finished"
    assert adapter.start_calls == 1


@pytest.mark.anyio
async def test_binding_persisted_restarts_only_first_turn(store) -> None:
    work_item_id = _bare_system_work_item(store)
    crash_once = True

    def fault(stage):
        nonlocal crash_once
        if crash_once and stage is StartStage.BINDING_PERSISTED:
            crash_once = False
            raise InjectedCrash()

    adapter = FakeAdapter()
    coordinator = StartEpisodeCoordinator(
        store,
        broker=FakeBroker(),
        adapter=adapter,
        yield_coordinator=FakeYieldCoordinator(),
        fault_hook=fault,
    )
    command = _command(work_item_id)

    with pytest.raises(InjectedCrash):
        await _collect(coordinator, command)
    events = await _collect(coordinator, command)

    assert events[-1]["type"] == "finished"
    assert adapter.start_calls == 1
    assert adapter.turn_calls == 1


@pytest.mark.anyio
async def test_terminal_retry_does_not_create_duplicate_episode_or_session(store) -> None:
    work_item_id = _bare_system_work_item(store)
    adapter = FakeAdapter()
    coordinator = StartEpisodeCoordinator(
        store,
        broker=FakeBroker(),
        adapter=adapter,
        yield_coordinator=FakeYieldCoordinator(),
    )
    command = _command(work_item_id)

    await _collect(coordinator, command)
    episode_count = len(store.read_snapshot().episodes)
    assert await _collect(coordinator, command) == []

    assert adapter.start_calls == adapter.turn_calls == 1
    assert len(store.read_snapshot().episodes) == episode_count


@pytest.mark.anyio
async def test_first_episode_and_three_fresh_episodes_form_one_snapshot_chain(store) -> None:
    work_item_id = _bare_system_work_item(store)
    previous_episode_id = None
    previous_ref = None
    episode_ids = []

    for index in range(4):
        adapter = FakeAdapter()
        coordinator = StartEpisodeCoordinator(
            store,
            broker=FakeBroker(),
            adapter=adapter,
            yield_coordinator=FakeYieldCoordinator(),
        )
        command = replace(
            _command(work_item_id),
            idempotency_key=f"start-command-{index}",
            previous_episode_id=previous_episode_id,
            previous_snapshot_ref=previous_ref,
        )
        await _collect(coordinator, command)
        progress = coordinator.progress(command.idempotency_key)
        assert progress is not None and progress.episode_id is not None
        episode_ids.append(progress.episode_id)

        ownership = next(
            lease
            for lease in store.read_snapshot().active_leases
            if lease.resource_id == progress.episode_id
        )
        store.request_yield(
            progress.episode_id,
            expected_lease_id=ownership.lease_id,
            expected_owner=ownership.owner,
            expected_token=ownership.fencing_token,
            reason="continue_fresh",
        )
        previous_ref = store.commit_checkpoint(
            progress.episode_id,
            expected_lease_id=ownership.lease_id,
            expected_owner=ownership.owner,
            expected_token=ownership.fencing_token,
            snapshot=make_cooperative_snapshot(),
            checkpoint_key=f"checkpoint-{index}",
        )
        store.close_episode(
            progress.episode_id,
            expected_lease_id=ownership.lease_id,
            expected_owner=ownership.owner,
            expected_token=ownership.fencing_token,
        )
        previous_episode_id = progress.episode_id

    assert len(set(episode_ids)) == 4


@pytest.mark.anyio
async def test_new_command_refuses_second_non_terminal_episode(store) -> None:
    work_item_id = _bare_system_work_item(store)
    first = StartEpisodeCoordinator(
        store,
        broker=FakeBroker(),
        adapter=FakeAdapter(),
        yield_coordinator=FakeYieldCoordinator(),
    )
    await _collect(first, _command(work_item_id))

    second = StartEpisodeCoordinator(
        store,
        broker=FakeBroker(),
        adapter=FakeAdapter(),
        yield_coordinator=FakeYieldCoordinator(),
    )
    with pytest.raises(ValueError, match="non-terminal Episode"):
        await _collect(
            second,
            replace(_command(work_item_id), idempotency_key="other-command"),
        )


@pytest.mark.anyio
async def test_native_compact_marks_fresh_guarantee_degraded(store) -> None:
    work_item_id = _bare_system_work_item(store)
    adapter = FakeAdapter()
    adapter.emit_compaction = True
    coordinator = StartEpisodeCoordinator(
        store,
        broker=FakeBroker(),
        adapter=adapter,
        yield_coordinator=FakeYieldCoordinator(),
    )
    command = _command(work_item_id)

    await _collect(coordinator, command)

    assert coordinator.progress(command.idempotency_key).degraded_native_compact is True
