from __future__ import annotations

import asyncio

import pytest

from trowel_py.model_os.episode_starting import (
    NativeSessionIdentity,
    StartEpisodeCoordinator,
)
from trowel_py.model_os.incubation import (
    IncubationError,
    IncubationPlanStatus,
    IncubationRepository,
)
from trowel_py.model_os.incubation.service import IncubationService
from trowel_py.model_os.routing import (
    CognitiveRouter,
    RouteCandidate,
    RouteMode,
    RoutingConfig,
)
from trowel_py.model_os.types import TaskStatus
from trowel_py.model_os.waking import WakeConditionKind, WakeObservation
from trowel_py.model_os.work_broker import (
    BrokerPolicy,
    ModelTier,
    WorkBroker,
    WorkKind,
    WorkLease,
    WorkRequest,
)
from trowel_py.quota.types import Provider

from tests.model_os.episode_starting.test_coordinator import FakeYieldCoordinator
from tests.model_os.episode_starting.test_coordinator import DenyingBroker
from tests.model_os.incubation.support import create_command, prepared_running_task
from tests.model_os.work_broker._support import FakeClock


class OutputAdapter:
    def __init__(self, output: str) -> None:
        self.output = output
        self.prompts: list[str] = []
        self.identity = NativeSessionIdentity(
            agent_session_id="incubation-agent",
            runtime="codex",
            native_session_id="incubation-thread",
            runtime_generation="codex-connection-1",
            runtime_pid=123,
        )

    async def start_native(self, command, episode):
        return self.identity

    async def persist_binding(self, identity):
        return identity

    async def refresh_identity(self, identity):
        return identity

    def effective_settings(self, identity):
        return "codex-deep", "high"

    async def start_first_turn(self, identity, text):
        self.prompts.append(text)
        yield {
            "type": "turn_start",
            "turn_id": "turn-1",
            "payload": {},
        }
        yield {
            "type": "text",
            "turn_id": "turn-1",
            "payload": {"text": self.output},
        }
        yield {
            "type": "finished",
            "turn_id": "turn-1",
            "payload": {
                "usage": {"input_tokens": 100, "output_tokens": 25}
            },
        }


class CrashAfterTurnStartAdapter(OutputAdapter):
    async def start_first_turn(self, identity, text):
        self.prompts.append(text)
        yield {"type": "turn_start", "turn_id": "turn-1", "payload": {}}
        raise RuntimeError("transport disappeared after request")


class InterruptibleAdapter(OutputAdapter):
    def __init__(self) -> None:
        super().__init__("")
        self.started = asyncio.Event()
        self.interrupted = asyncio.Event()

    async def start_first_turn(self, identity, text):
        self.prompts.append(text)
        self.started.set()
        yield {"type": "turn_start", "turn_id": "turn-1", "payload": {}}
        await self.interrupted.wait()
        yield {"type": "interrupted", "turn_id": "turn-1", "payload": {}}


def _router(store):
    return CognitiveRouter(
        store,
        RoutingConfig(
            RouteMode.CANARY,
            {
                "codex": (
                    RouteCandidate(ModelTier.FAST, "codex-fast", "medium"),
                    RouteCandidate(ModelTier.DEEP, "codex-deep", "high"),
                )
            },
        ),
    )


def _ready_plan(store):
    task, ref = prepared_running_task(store)
    store.append_constraint(task.task_id, "不能自动执行候选方案")
    repository = IncubationRepository(store)
    plan = repository.create_plan(create_command(task.task_id, ref))
    repository.consume_wake(
        WakeObservation(
            observation_id="manual-service-run",
            kind=WakeConditionKind.MANUAL,
            target_ref=plan.plan_id,
            observed_at="2026-07-27T07:00:00+00:00",
            source="user",
            details={},
        )
    )
    return task, ref, plan


def test_missing_deep_route_creates_no_plan_or_model_call(store, tmp_path) -> None:
    task, ref = prepared_running_task(store)
    router = CognitiveRouter(store, RoutingConfig(RouteMode.OFF, {}))
    service = IncubationService(
        store,
        starter=object(),
        router=router,
        broker=object(),
        work_root=tmp_path,
    )

    with pytest.raises(IncubationError) as raised:
        service.create_plan(create_command(task.task_id, ref))

    assert raised.value.code == "deep_route_unavailable"
    assert service.repository.plan_count() == 0
    assert service.model_calls == 0


@pytest.mark.anyio
async def test_service_runs_one_fresh_deep_cycle_from_frozen_inputs(
    store, db_path, tmp_path
) -> None:
    task, ref, plan = _ready_plan(store)
    output = (
        '{"proposal":"增加 durable correlation",'
        '"source_refs":["snapshot:' + ref.episode_id + ':' + str(ref.version) + '"],'
        '"new_points":["仅保存进程内句柄无法跨重启对账"],'
        '"verification":"重启后查询 durable record",'
        '"uncertainty":"远端任务状态接口可能不完整"}'
    )
    adapter = OutputAdapter(output)
    broker = WorkBroker(
        db_path,
        policy=BrokerPolicy(codex_account_order=("codex",)),
    )
    broker.open()
    router = _router(store)
    starter = StartEpisodeCoordinator(
        store,
        broker=broker,
        adapter=adapter,
        yield_coordinator=FakeYieldCoordinator(),
        router=router,
    )
    service = IncubationService(
        store,
        starter=starter,
        router=router,
        broker=broker,
        work_root=tmp_path / "incubation-work",
    )

    result = await service.run_plan(plan.plan_id)

    assert result.plan.status is IncubationPlanStatus.AWAITING_REVIEW
    assert result.candidate is not None
    assert result.candidate.effective_model == "codex-deep"
    assert service.model_calls == 1
    assert len(adapter.prompts) == 1
    prompt = adapter.prompts[0]
    assert "进程内句柄可用，但重启后会丢失" in prompt
    assert "重启后怎样确认 child 是否已经执行" in prompt
    assert "不能自动执行候选方案" in prompt
    assert "Memory" not in prompt and "Profile" not in prompt
    snapshot = store.read_snapshot()
    current_task = next(item for item in snapshot.tasks if item.task_id == task.task_id)
    episode = snapshot.episode_by_id(result.plan.episode_id)
    assert current_task.status is TaskStatus.WAITING_USER
    assert episode is not None and episode.task_id == task.task_id
    assert episode.status.is_terminal
    assert broker.active_leases() == ()
    broker.close()


@pytest.mark.anyio
async def test_budget_denial_stops_without_model_call_and_restores_task(
    store, tmp_path
) -> None:
    task, _, plan = _ready_plan(store)
    broker = DenyingBroker()
    broker.active_leases = lambda: ()
    broker.settle_incubation_usage = lambda *_args: False
    router = _router(store)
    starter = StartEpisodeCoordinator(
        store,
        broker=broker,
        adapter=OutputAdapter("{}"),
        yield_coordinator=FakeYieldCoordinator(),
        router=router,
    )
    service = IncubationService(
        store,
        starter=starter,
        router=router,
        broker=broker,
        work_root=tmp_path,
    )

    with pytest.raises(IncubationError) as raised:
        await service.run_plan(plan.plan_id)

    assert raised.value.code == "budget_denied"
    failed = service.repository.get_plan(plan.plan_id)
    assert failed.status is IncubationPlanStatus.FAILED
    assert failed.stop_reason == "budget_denied"
    assert service.model_calls == 0
    current_task = next(
        item for item in store.read_snapshot().tasks if item.task_id == task.task_id
    )
    assert current_task.status is TaskStatus.READY


@pytest.mark.anyio
async def test_schema_invalid_is_terminal_and_releases_lease(
    store, db_path, tmp_path
) -> None:
    _, _, plan = _ready_plan(store)
    broker = WorkBroker(db_path, policy=BrokerPolicy(codex_account_order=("codex",)))
    broker.open()
    router = _router(store)
    starter = StartEpisodeCoordinator(
        store,
        broker=broker,
        adapter=OutputAdapter("{}"),
        yield_coordinator=FakeYieldCoordinator(),
        router=router,
    )
    service = IncubationService(
        store,
        starter=starter,
        router=router,
        broker=broker,
        work_root=tmp_path,
    )

    with pytest.raises(IncubationError) as raised:
        await service.run_plan(plan.plan_id)

    assert raised.value.code == "output_schema_invalid"
    failed = service.repository.get_plan(plan.plan_id)
    assert failed.status is IncubationPlanStatus.FAILED
    assert failed.stop_reason == "output_schema_invalid"
    assert broker.active_leases() == ()
    broker.close()


@pytest.mark.anyio
async def test_reconcile_commits_durable_output_without_second_model_call(
    store, db_path, tmp_path, monkeypatch
) -> None:
    _, ref, plan = _ready_plan(store)
    output = (
        '{"proposal":"durable proposal",'
        '"source_refs":["snapshot:' + ref.episode_id + ':' + str(ref.version) + '"],'
        '"new_points":["durable point"],'
        '"verification":"restart check",'
        '"uncertainty":"unknown remote state"}'
    )
    broker = WorkBroker(db_path, policy=BrokerPolicy(codex_account_order=("codex",)))
    broker.open()
    router = _router(store)
    starter = StartEpisodeCoordinator(
        store,
        broker=broker,
        adapter=OutputAdapter(output),
        yield_coordinator=FakeYieldCoordinator(),
        router=router,
    )
    first = IncubationService(
        store,
        starter=starter,
        router=router,
        broker=broker,
        work_root=tmp_path,
    )

    class CrashAfterDurableOutput(BaseException):
        pass

    original_commit = first.repository.commit_generation

    def crash_commit(*args, **kwargs):
        raise CrashAfterDurableOutput()

    monkeypatch.setattr(first.repository, "commit_generation", crash_commit)
    with pytest.raises(CrashAfterDurableOutput):
        await first.run_plan(plan.plan_id)
    monkeypatch.setattr(first.repository, "commit_generation", original_commit)

    resumed = IncubationService(
        store,
        starter=starter,
        router=router,
        broker=broker,
        work_root=tmp_path,
    )
    assert await resumed.reconcile() == 1

    recovered = resumed.repository.get_plan(plan.plan_id)
    assert recovered.status is IncubationPlanStatus.AWAITING_REVIEW
    assert resumed.repository.candidate_count() == 1
    assert resumed.model_calls == 0
    assert broker.active_leases() == ()
    broker.close()


@pytest.mark.anyio
async def test_reconcile_settles_committed_candidate_after_broker_crash_once(
    store, db_path, tmp_path, monkeypatch
) -> None:
    _, ref, plan = _ready_plan(store)
    output = (
        '{"proposal":"durable proposal",'
        '"source_refs":["snapshot:' + ref.episode_id + ':' + str(ref.version) + '"],'
        '"new_points":["durable point"],'
        '"verification":"restart check",'
        '"uncertainty":"unknown remote state"}'
    )
    broker = WorkBroker(db_path, policy=BrokerPolicy(codex_account_order=("codex",)))
    broker.open()
    router = _router(store)
    starter = StartEpisodeCoordinator(
        store,
        broker=broker,
        adapter=OutputAdapter(output),
        yield_coordinator=FakeYieldCoordinator(),
        router=router,
    )
    first = IncubationService(
        store,
        starter=starter,
        router=router,
        broker=broker,
        work_root=tmp_path,
    )

    class CrashBeforeBrokerSettlement(BaseException):
        pass

    def crash_settlement(*_args, **_kwargs):
        raise CrashBeforeBrokerSettlement()

    monkeypatch.setattr(first, "_settle_broker", crash_settlement)
    with pytest.raises(CrashBeforeBrokerSettlement):
        await first.run_plan(plan.plan_id)

    committed = first.repository.get_plan(plan.plan_id)
    assert committed.status is IncubationPlanStatus.AWAITING_REVIEW
    assert committed.broker_settled is False
    assert len(broker.active_leases()) == 1

    resumed = IncubationService(
        store,
        starter=starter,
        router=router,
        broker=broker,
        work_root=tmp_path,
    )
    assert await resumed.reconcile() == 1
    assert await resumed.reconcile() == 0

    recovered = resumed.repository.get_plan(plan.plan_id)
    assert recovered.broker_settled is True
    assert broker.active_leases() == ()
    assert broker.usage_totals(
        work_kind=WorkKind.INCUBATION,
        work_item_id=plan.work_item_id,
    ).calls == 1
    assert resumed.model_calls == 0
    broker.close()


@pytest.mark.anyio
async def test_transport_exception_after_model_request_becomes_result_unknown(
    store, db_path, tmp_path
) -> None:
    task, _, plan = _ready_plan(store)
    broker = WorkBroker(db_path, policy=BrokerPolicy(codex_account_order=("codex",)))
    broker.open()
    router = _router(store)
    starter = StartEpisodeCoordinator(
        store,
        broker=broker,
        adapter=CrashAfterTurnStartAdapter(""),
        yield_coordinator=FakeYieldCoordinator(),
        router=router,
    )
    interrupted = []

    async def interrupt_runtime(
        session_id: str, expected_turn_id: str | None
    ) -> bool:
        assert expected_turn_id == "turn-1"
        interrupted.append((session_id, len(broker.active_leases())))
        return True

    service = IncubationService(
        store,
        starter=starter,
        router=router,
        broker=broker,
        interrupt_runtime=interrupt_runtime,
        work_root=tmp_path,
    )

    with pytest.raises(IncubationError) as raised:
        await service.run_plan(plan.plan_id)

    assert raised.value.code == "result_unknown"
    unknown = service.repository.get_plan(plan.plan_id)
    assert unknown.status is IncubationPlanStatus.RESULT_UNKNOWN
    assert unknown.model_called is True
    current = next(
        item for item in store.read_snapshot().tasks if item.task_id == task.task_id
    )
    assert current.status is TaskStatus.READY
    assert interrupted == [("incubation-agent", 1)]
    assert broker.active_leases() == ()
    broker.close()


@pytest.mark.anyio
async def test_unknown_interrupt_ack_does_not_release_lease_before_terminal_confirmation(
    store, db_path, tmp_path
) -> None:
    _, _, plan = _ready_plan(store)
    broker = WorkBroker(db_path, policy=BrokerPolicy(codex_account_order=("codex",)))
    broker.open()
    router = _router(store)
    starter = StartEpisodeCoordinator(
        store,
        broker=broker,
        adapter=CrashAfterTurnStartAdapter(""),
        yield_coordinator=FakeYieldCoordinator(),
        router=router,
    )

    async def interrupt_ack_only(
        _session_id: str, expected_turn_id: str | None
    ) -> bool:
        assert expected_turn_id == "turn-1"
        return False

    service = IncubationService(
        store,
        starter=starter,
        router=router,
        broker=broker,
        interrupt_runtime=interrupt_ack_only,
        work_root=tmp_path,
    )

    with pytest.raises(IncubationError) as raised:
        await service.run_plan(plan.plan_id)

    assert raised.value.code == "result_unknown"
    unknown = service.repository.get_plan(plan.plan_id)
    assert unknown.broker_settled is False
    assert len(broker.active_leases()) == 1
    await service.drain()
    broker.close()


@pytest.mark.anyio
async def test_restart_reconcile_settles_unknown_after_durable_runtime_exit(
    store, db_path, tmp_path
) -> None:
    _, _, plan = _ready_plan(store)
    broker = WorkBroker(db_path, policy=BrokerPolicy(codex_account_order=("codex",)))
    broker.open()
    router = _router(store)
    starter = StartEpisodeCoordinator(
        store,
        broker=broker,
        adapter=CrashAfterTurnStartAdapter(""),
        yield_coordinator=FakeYieldCoordinator(),
        router=router,
    )

    async def unconfirmed(_session_id: str, _turn_id: str | None) -> bool:
        return False

    first = IncubationService(
        store,
        starter=starter,
        router=router,
        broker=broker,
        interrupt_runtime=unconfirmed,
        work_root=tmp_path,
    )
    with pytest.raises(IncubationError):
        await first.run_plan(plan.plan_id)
    await first.drain()
    assert len(broker.active_leases()) == 1

    async def hub_unavailable(_session_id: str, _turn_id: str | None) -> bool:
        raise RuntimeError("live hub session disappeared during restart")

    resumed = IncubationService(
        store,
        starter=starter,
        router=router,
        broker=broker,
        interrupt_runtime=hub_unavailable,
        reconcile_runtime=lambda _binding: "already_exited",
        work_root=tmp_path,
    )

    assert await resumed.reconcile() == 1
    assert resumed.repository.get_plan(plan.plan_id).broker_settled is True
    assert broker.active_leases() == ()
    await resumed.drain()
    broker.close()


@pytest.mark.anyio
async def test_call_before_start_preemption_keeps_plan_ready_without_model_call(
    store, db_path, tmp_path, monkeypatch
) -> None:
    _, _, plan = _ready_plan(store)
    broker = WorkBroker(db_path, policy=BrokerPolicy(codex_account_order=("codex",)))
    broker.open()
    original_begin = broker.begin_call

    def preempt_before_call(lease_id: str, fencing_token: int) -> None:
        broker.release(lease_id, fencing_token)
        original_begin(lease_id, fencing_token)

    monkeypatch.setattr(broker, "begin_call", preempt_before_call)
    router = _router(store)
    starter = StartEpisodeCoordinator(
        store,
        broker=broker,
        adapter=OutputAdapter("{}"),
        yield_coordinator=FakeYieldCoordinator(),
        router=router,
    )
    service = IncubationService(
        store,
        starter=starter,
        router=router,
        broker=broker,
        work_root=tmp_path,
    )

    result = await service.run_plan(plan.plan_id)

    assert result.plan.status is IncubationPlanStatus.READY
    assert result.plan.stop_reason is None
    assert result.plan.cycle == 1
    assert result.plan.broker_settled is True
    assert service.model_calls == 0
    assert broker.active_leases() == ()
    broker.close()


@pytest.mark.anyio
async def test_started_incubation_interrupt_waits_for_unknown_and_lease_release(
    store, db_path, tmp_path
) -> None:
    _, _, plan = _ready_plan(store)
    broker = WorkBroker(db_path, policy=BrokerPolicy(codex_account_order=("codex",)))
    broker.open()
    adapter = InterruptibleAdapter()
    router = _router(store)
    starter = StartEpisodeCoordinator(
        store,
        broker=broker,
        adapter=adapter,
        yield_coordinator=FakeYieldCoordinator(),
        router=router,
    )
    service = IncubationService(
        store,
        starter=starter,
        router=router,
        broker=broker,
        work_root=tmp_path,
    )
    running = service.trigger_plan(plan.plan_id)
    await adapter.started.wait()

    async def interrupt(session_id: str) -> None:
        assert session_id == adapter.identity.agent_session_id
        adapter.interrupted.set()

    assert await service.interrupt_for_foreground(plan.work_item_id, interrupt)
    with pytest.raises(IncubationError) as raised:
        await running

    assert raised.value.code == "result_unknown"
    unknown = service.repository.get_plan(plan.plan_id)
    assert unknown.status is IncubationPlanStatus.RESULT_UNKNOWN
    assert unknown.broker_settled is True
    assert broker.active_leases() == ()
    broker.close()


@pytest.mark.anyio
async def test_started_incubation_preemption_timeout_keeps_lease(
    store, db_path, tmp_path
) -> None:
    _, _, plan = _ready_plan(store)
    broker = WorkBroker(db_path, policy=BrokerPolicy(codex_account_order=("codex",)))
    broker.open()
    adapter = InterruptibleAdapter()
    router = _router(store)
    starter = StartEpisodeCoordinator(
        store,
        broker=broker,
        adapter=adapter,
        yield_coordinator=FakeYieldCoordinator(),
        router=router,
    )
    service = IncubationService(
        store,
        starter=starter,
        router=router,
        broker=broker,
        work_root=tmp_path,
        interrupt_wait_timeout_seconds=0.01,
    )
    running = service.trigger_plan(plan.plan_id)
    await adapter.started.wait()

    async def interrupt_ack_only(_session_id: str) -> None:
        return None

    assert not await service.interrupt_for_foreground(
        plan.work_item_id, interrupt_ack_only
    )
    assert len(broker.active_leases()) == 1

    adapter.interrupted.set()
    with pytest.raises(IncubationError):
        await running
    broker.close()


@pytest.mark.anyio
async def test_drain_does_not_wait_forever_for_stuck_runtime(
    store, db_path, tmp_path
) -> None:
    _, _, plan = _ready_plan(store)
    broker = WorkBroker(db_path, policy=BrokerPolicy(codex_account_order=("codex",)))
    broker.open()
    adapter = InterruptibleAdapter()
    router = _router(store)
    starter = StartEpisodeCoordinator(
        store,
        broker=broker,
        adapter=adapter,
        yield_coordinator=FakeYieldCoordinator(),
        router=router,
    )
    service = IncubationService(
        store,
        starter=starter,
        router=router,
        broker=broker,
        work_root=tmp_path,
        interrupt_wait_timeout_seconds=0.01,
    )
    running = service.trigger_plan(plan.plan_id)
    await adapter.started.wait()

    await asyncio.wait_for(service.drain(), timeout=0.1)

    assert running.cancelled()
    assert service.repository.get_plan(plan.plan_id).broker_settled is False
    assert len(broker.active_leases()) == 1
    broker.close()


@pytest.mark.anyio
async def test_running_incubation_renews_work_lease_until_runtime_terminal(
    store, db_path, tmp_path
) -> None:
    _, _, plan = _ready_plan(store)
    clock = FakeClock()
    broker = WorkBroker(
        db_path,
        policy=BrokerPolicy(
            codex_account_order=("codex",),
            lease_ttl_seconds=3,
        ),
        now_fn=clock,
    )
    broker.open()
    adapter = InterruptibleAdapter()
    router = _router(store)
    starter = StartEpisodeCoordinator(
        store,
        broker=broker,
        adapter=adapter,
        yield_coordinator=FakeYieldCoordinator(),
        router=router,
    )
    service = IncubationService(
        store,
        starter=starter,
        router=router,
        broker=broker,
        work_root=tmp_path,
        lease_renew_interval_seconds=0.01,
    )
    running = service.trigger_plan(plan.plan_id)
    await adapter.started.wait()

    for _ in range(3):
        clock.advance(2)
        await asyncio.sleep(0.02)

    foreground = broker.request(
        WorkRequest(
            kind=WorkKind.FOREGROUND,
            provider=Provider.CODEX,
            model_tier=ModelTier.DEEP,
            task_id="foreground-task",
            work_item_id="foreground-work",
            idempotency_key="foreground-during-long-incubation",
        )
    )
    assert not isinstance(foreground, WorkLease)

    adapter.interrupted.set()
    with pytest.raises(IncubationError):
        await running
    broker.close()
