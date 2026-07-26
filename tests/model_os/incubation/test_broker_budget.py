from __future__ import annotations

from trowel_py.model_os.incubation.service import IncubationService
from trowel_py.model_os.work_broker import (
    BrokerPolicy,
    BudgetDimensions,
    DenialReason,
    ModelTier,
    UsageRecord,
    WorkBroker,
    WorkDenial,
    WorkKind,
    WorkLease,
    WorkRequest,
)
from trowel_py.quota.types import Provider

from tests.model_os.work_broker._support import FakeClock


def test_incubation_budget_is_scoped_to_one_work_item(
    db_path,
) -> None:
    clock = FakeClock()
    broker = WorkBroker(
        db_path,
        policy=BrokerPolicy(
            concurrency_per_account=1,
            glm_account_order=("glm-a",),
            codex_account_order=("codex",),
        ),
        now_fn=clock,
    )
    broker.open()
    request = WorkRequest(
        kind=WorkKind.INCUBATION,
        provider=Provider.GLM,
        model_tier=ModelTier.DEEP,
        task_id="task-1",
        work_item_id="incubation-1",
        budget_cap=BudgetDimensions(calls=1),
    )
    first = broker.request(request)
    assert isinstance(first, WorkLease)
    assert first.granted_cap == BudgetDimensions(calls=1)
    broker.record_usage(
        first.lease_id,
        first.fencing_token,
        UsageRecord(calls=1, occurred_at=clock.iso(), observation_id="cycle-1"),
    )
    broker.release(first.lease_id, first.fencing_token)

    denied = broker.request(request)
    assert isinstance(denied, WorkDenial)
    assert denied.reason is DenialReason.BUDGET_EXHAUSTED

    other = broker.request(
        WorkRequest(
            kind=WorkKind.INCUBATION,
            provider=Provider.GLM,
            model_tier=ModelTier.DEEP,
            task_id="task-1",
            work_item_id="incubation-2",
            budget_cap=BudgetDimensions(calls=1),
        )
    )
    assert isinstance(other, WorkLease)
    broker.close()


def test_service_cleanup_recovers_only_incubation_leases_and_replays_result(
    store, db_path
) -> None:
    clock = FakeClock()
    broker = WorkBroker(
        db_path,
        policy=BrokerPolicy(glm_account_order=("glm-a", "glm-b")),
        now_fn=clock,
    )
    broker.open()
    incubation = broker.request(
        WorkRequest(
            kind=WorkKind.INCUBATION,
            provider=Provider.GLM,
            account_id="glm-a",
            work_item_id="cleanup-incubation-1",
            budget_cap=BudgetDimensions(calls=1),
        )
    )
    maintenance = broker.request(
        WorkRequest(
            kind=WorkKind.MAINTENANCE,
            provider=Provider.GLM,
            account_id="glm-b",
        )
    )
    assert isinstance(incubation, WorkLease)
    assert isinstance(maintenance, WorkLease)
    clock.advance(broker.policy.lease_ttl_seconds + 1)
    cutoff = clock.iso()
    service = IncubationService(
        store,
        starter=object(),
        router=object(),
        broker=broker,
    )

    first = service.cleanup_artifacts(
        command_id="service-cleanup-1",
        before=cutoff,
        occurred_at=clock(),
    )
    replay = service.cleanup_artifacts(
        command_id="service-cleanup-1",
        before=cutoff,
        occurred_at=clock(),
    )

    assert first == replay == {
        "cleaned_candidates": 0,
        "recovered_expired_leases": 1,
    }
    assert broker._conn is not None
    rows = {
        row["lease_id"]: row["released_at"]
        for row in broker._conn.execute(
            "SELECT lease_id, released_at FROM work_leases WHERE lease_id IN (?,?)",
            (incubation.lease_id, maintenance.lease_id),
        ).fetchall()
    }
    assert rows[incubation.lease_id] is not None
    assert rows[maintenance.lease_id] is None
    broker.close()
