"""Memory hook 注册与同步分发契约。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, BrokenBarrierError

from trowel_py.memory.hooks import HookFn, HookRegistry


class _CoordinatedContainsList(list[HookFn]):
    def __init__(self) -> None:
        super().__init__()
        self._barrier = Barrier(2)
        self.coordinated: list[bool] = []

    def __contains__(self, item: object) -> bool:
        present = super().__contains__(item)
        try:
            self._barrier.wait(timeout=1.0)
        except BrokenBarrierError:
            self.coordinated.append(False)
        else:
            self.coordinated.append(True)
        return present


def test_inject_hook_fires_on_dispatch() -> None:
    reg = HookRegistry()
    called: list[object] = []
    reg.register_inject_hook(lambda ev: called.append(ev))
    reg.dispatch_inject("session-start")
    assert called == ["session-start"]
    assert reg.dispatch_log == ["inject:'session-start'"]


def test_write_job_dispatches_batch_event() -> None:
    reg = HookRegistry()
    called: list[object] = []
    reg.register_write_job(lambda ev: called.append(ev))
    reg.dispatch_write_job("daily-review")
    assert called == ["daily-review"]


def test_tidy_job_dispatches() -> None:
    reg = HookRegistry()
    called: list[object] = []
    reg.register_tidy_job(lambda ev: called.append(ev))
    reg.dispatch_tidy_job({"period": "weekly"})
    assert called == [{"period": "weekly"}]


def test_empty_dispatch_is_noop_no_error() -> None:
    reg = HookRegistry()
    reg.dispatch_inject()
    reg.dispatch_write_job()
    reg.dispatch_tidy_job()
    assert len(reg.dispatch_log) == 3


def test_multiple_hooks_per_slot_run_in_order() -> None:
    reg = HookRegistry()
    order: list[int] = []
    reg.register_inject_hook(lambda ev: order.append(1))
    reg.register_inject_hook(lambda ev: order.append(2))
    reg.dispatch_inject("e")
    assert order == [1, 2]


def test_concurrent_registration_of_same_job_is_idempotent() -> None:
    reg = HookRegistry()
    write_jobs = _CoordinatedContainsList()
    reg._write = write_jobs  # noqa: SLF001
    workers_ready = Barrier(2)

    def job(_event: object) -> None:
        pass

    def register(_index: int) -> HookFn:
        workers_ready.wait(timeout=1.0)
        return reg.register_write_job(job)

    with ThreadPoolExecutor(max_workers=2) as executor:
        registered = list(executor.map(register, range(2)))

    assert registered == [job, job]
    assert write_jobs.coordinated == [False, False]
    assert write_jobs == [job]
