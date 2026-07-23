from __future__ import annotations

from pathlib import Path


from trowel_py.model_os.work_broker import (
    DenialReason,
    WorkBroker,
    WorkLease,
)
from tests.model_os.work_broker._support import (
    FakeClock,
    _begin,
    _default,
    _fg,
    _maint,
    _policy,
)


def test_concurrency_cap_one_slot_per_account(broker: WorkBroker) -> None:
    first = broker.request(_default(account_id="glm-a"))
    assert isinstance(first, WorkLease)
    second = broker.request(_default(account_id="glm-a"))
    assert isinstance(second, WorkLease) is False
    assert second.reason is DenialReason.SLOT_BUSY
    assert second.retry_after_seconds is not None
    other = broker.request(_default(account_id="glm-b"))
    assert isinstance(other, WorkLease)


def test_foreground_preempts_unstarted_default(broker: WorkBroker) -> None:
    d = broker.request(_default(account_id="glm-a"))
    assert isinstance(d, WorkLease)
    fg = broker.request(_fg(account_id="glm-a"))
    assert isinstance(fg, WorkLease)
    assert fg.slot == d.slot
    assert all(lse.lease_id != d.lease_id for lse in broker.active_leases())


def test_foreground_cannot_preempt_started_default(
    broker: WorkBroker, clock: FakeClock
) -> None:

    d = broker.request(_default(account_id="glm-a", task_id="task-d"))
    assert isinstance(d, WorkLease)
    _begin(broker, d)
    fg = broker.request(_fg(account_id="glm-a"))
    assert isinstance(fg, WorkLease) is False
    assert fg.reason is DenialReason.SLOT_BUSY


def test_foreground_cannot_preempt_maintenance(broker: WorkBroker) -> None:
    m = broker.request(_maint(account_id="glm-a"))
    assert isinstance(m, WorkLease)
    fg = broker.request(_fg(account_id="glm-a"))
    assert isinstance(fg, WorkLease) is False
    assert fg.reason is DenialReason.SLOT_BUSY


def test_account_failover_on_busy_slot(broker: WorkBroker, clock: FakeClock) -> None:
    d = broker.request(_default(account_id="glm-a", task_id="task-d"))
    assert isinstance(d, WorkLease)
    _begin(broker, d)
    fg = broker.request(_fg())
    assert isinstance(fg, WorkLease)
    assert fg.account_id == "glm-b"


def test_slots_free_after_release(broker: WorkBroker) -> None:
    a = broker.request(_default(account_id="glm-a"))
    assert isinstance(a, WorkLease)
    broker.release(a.lease_id, a.fencing_token)
    b = broker.request(_default(account_id="glm-a"))
    assert isinstance(b, WorkLease)


def test_arbitration_is_atomic_under_concurrent_threads(
    db_path: Path, clock: FakeClock
) -> None:
    import threading

    broker = WorkBroker(db_path, policy=_policy(), read_model=None, now_fn=clock)
    broker.open()
    results: list[object] = []
    lock = threading.Lock()

    def worker() -> None:
        outcome = broker.request(_default(account_id="glm-a"))
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    leases = [r for r in results if isinstance(r, WorkLease)]
    denials = [r for r in results if not isinstance(r, WorkLease)]
    assert len(leases) == 1
    assert len(denials) == 1
    broker.close()
