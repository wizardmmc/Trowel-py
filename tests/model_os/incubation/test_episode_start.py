from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trowel_py.model_os.episode_starting import (
    StartEpisodeCommand,
    StartEpisodeCoordinator,
)
from trowel_py.model_os.incubation import IncubationRepository
from trowel_py.model_os.routing import UserRoutePreference
from trowel_py.model_os.types import MemoryEligibility, SessionPurpose
from trowel_py.model_os.waking import WakeConditionKind, WakeObservation
from trowel_py.model_os.work_broker import WorkKind

from tests.model_os.episode_starting.test_coordinator import (
    FakeAdapter,
    FakeBroker,
    FakeYieldCoordinator,
)
from tests.model_os.incubation.support import create_command, prepared_running_task


@pytest.mark.anyio
async def test_incubation_episode_uses_task_snapshot_without_foreground_claim(
    store,
) -> None:
    task, ref = prepared_running_task(store)
    repository = IncubationRepository(store)
    plan = repository.create_plan(create_command(task.task_id, ref))
    repository.consume_wake(
        WakeObservation(
            observation_id="manual-incubation-start",
            kind=WakeConditionKind.MANUAL,
            target_ref=plan.plan_id,
            observed_at="2026-07-27T07:00:00+00:00",
            source="user",
            details={},
        )
    )
    repository.begin_run(
        plan.plan_id,
        occurred_at=datetime(2026, 7, 27, 7, 0, tzinfo=timezone.utc),
    )
    broker = FakeBroker()
    coordinator = StartEpisodeCoordinator(
        store,
        broker=broker,
        adapter=FakeAdapter(),
        yield_coordinator=FakeYieldCoordinator(),
    )
    command = StartEpisodeCommand(
        work_item_id=plan.work_item_id,
        task_id=task.task_id,
        previous_episode_id=ref.episode_id,
        previous_snapshot_ref=ref,
        runtime="codex",
        model="deep-model",
        effort="high",
        memory_enabled=False,
        profile_enabled=False,
        workdir="/tmp/incubation-test",
        session_purpose=SessionPurpose.INCUBATION,
        memory_eligibility=MemoryEligibility.INELIGIBLE,
        permission="read-only",
        idempotency_key=f"incubation:{plan.plan_id}:1",
        route_preference=UserRoutePreference.DEEP,
        budget_cap=plan.budget,
        first_turn_text="frozen prompt",
    )

    events = [event async for event in coordinator.start(command)]

    assert events[-1]["type"] == "finished"
    assert broker.requests[0].kind is WorkKind.INCUBATION
    assert broker.requests[0].budget_cap == plan.budget
    assert store.read_foreground_task_id() is None
