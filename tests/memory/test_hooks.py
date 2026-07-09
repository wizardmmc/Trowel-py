"""tests for the trigger framework (slice-038 T4)."""
from __future__ import annotations

from trowel_py.memory.hooks import HookRegistry


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
    assert len(reg.dispatch_log) == 3  # traced but nothing ran


def test_multiple_hooks_per_slot_run_in_order() -> None:
    reg = HookRegistry()
    order: list[int] = []
    reg.register_inject_hook(lambda ev: order.append(1))
    reg.register_inject_hook(lambda ev: order.append(2))
    reg.dispatch_inject("e")
    assert order == [1, 2]
