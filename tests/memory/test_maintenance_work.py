from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trowel_py.memory.maintenance_work import MaintenanceLeaseGate
from trowel_py.model_os.work_broker import (
    BrokerPolicy,
    CatchupPolicy,
    DenialReason,
    WorkBroker,
    WorkKind,
    WorkLease,
    WorkRequest,
)
from trowel_py.quota.types import Provider


@pytest.fixture
def broker(tmp_path: Path) -> WorkBroker:
    opened = WorkBroker(
        tmp_path / "model-os.db",
        policy=BrokerPolicy(glm_account_order=("glm",)),
    )
    opened.open()
    try:
        yield opened
    finally:
        opened.close()


def test_success_records_usage_and_merges_same_period(broker: WorkBroker) -> None:
    gate = MaintenanceLeaseGate(
        broker,
        now_fn=lambda: datetime(2026, 7, 25, 3, 0, tzinfo=timezone.utc),
    )
    observed: list[WorkLease] = []

    with gate.claim("memory.review", "2026-07-24") as claim:
        assert claim.granted is True
        observed.extend(broker.active_leases())
        claim.complete()

    assert len(observed) == 1
    assert observed[0].account_id == "glm"
    totals = broker.usage_totals(work_kind=WorkKind.MAINTENANCE)
    assert totals.calls == 1
    assert totals.cost is None
    assert broker.active_leases() == ()

    with gate.claim("memory.review", "2026-07-24") as duplicate:
        assert duplicate.granted is False
        assert duplicate.denial is not None
        assert duplicate.denial.reason is DenialReason.CATCHUP_ALREADY_DONE


def test_exception_releases_claim_without_completing_period(
    broker: WorkBroker,
) -> None:
    gate = MaintenanceLeaseGate(broker)

    with pytest.raises(RuntimeError, match="provider failed"):
        with gate.claim("profile.distill", "2026-07-25"):
            raise RuntimeError("provider failed")

    assert broker.active_leases() == ()
    assert broker.usage_totals(work_kind=WorkKind.MAINTENANCE).calls == 1
    with gate.claim("profile.distill", "2026-07-25") as retry:
        assert retry.granted is True
        retry.complete()


def test_existing_maintenance_lease_denies_other_scheduler(
    broker: WorkBroker,
) -> None:
    held = broker.request(
        WorkRequest(
            kind=WorkKind.MAINTENANCE,
            provider=Provider.GLM,
            account_id="glm",
            catchup=CatchupPolicy.MAINTENANCE_MERGE,
            catchup_scope="memory.review",
            catchup_period="2026-07-24",
        )
    )
    assert isinstance(held, WorkLease)

    gate = MaintenanceLeaseGate(broker)
    with gate.claim("profile.distill", "2026-07-25") as denied:
        assert denied.granted is False
        assert denied.denial is not None
        assert denied.denial.reason is DenialReason.SLOT_BUSY

    broker.release(held.lease_id, held.fencing_token)


def test_gate_without_broker_preserves_standalone_scheduler_behavior() -> None:
    gate = MaintenanceLeaseGate(None)

    with gate.claim("memory.review", "2026-07-24") as claim:
        assert claim.granted is True
        claim.complete()


def test_long_job_renews_before_original_expiry(tmp_path: Path) -> None:
    lock = threading.Lock()
    current = [datetime(2026, 7, 25, 3, 0, tzinfo=timezone.utc)]

    def now() -> datetime:
        with lock:
            return current[0]

    broker = WorkBroker(
        tmp_path / "model-os.db",
        policy=BrokerPolicy(lease_ttl_seconds=3, glm_account_order=("glm",)),
        now_fn=now,
    )
    broker.open()
    try:
        gate = MaintenanceLeaseGate(
            broker,
            now_fn=now,
            renew_interval_seconds=0.01,
        )
        with gate.claim("memory.review", "2026-07-24") as claim:
            with lock:
                current[0] += timedelta(seconds=2)
            time.sleep(0.03)
            with lock:
                current[0] += timedelta(seconds=2)
            time.sleep(0.03)
            claim.complete()

        assert broker.active_leases() == ()
        assert broker.usage_totals(work_kind=WorkKind.MAINTENANCE).calls == 1
    finally:
        broker.close()
