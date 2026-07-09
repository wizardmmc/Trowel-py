"""trigger framework for the three closed loops (slice-038).

Three registration slots, all empty/空跑 in 038 — business logic registers later:

- ``inject`` (event-driven, 039): fires at cc session start; the real system
  injection mounts on the reverse-proxy layer.
- ``write_job`` (batch, 040): the daily review that extracts knowledge AND runs
  the review-reflection. NOT per-turn.
- ``tidy_job`` (batch, 041): weekly/monthly compress + promote + dictionary
  regen + retire. Triggered via ``trowel memory tidy``.

Dispatching in 038 only calls the registered callables and appends to
``dispatch_log``; with nothing registered, it is a no-op trace (C-6).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

HookFn = Callable[[Any], None]


class HookRegistry:
    """A registry + dispatcher for one set of memory triggers.

    ``dispatch_log`` records every dispatch (kind + event) so the空跑 framework
    is observable in tests and CLI smoke runs.
    """

    def __init__(self) -> None:
        self._inject: list[HookFn] = []
        self._write: list[HookFn] = []
        self._tidy: list[HookFn] = []
        self.dispatch_log: list[str] = []

    def register_inject_hook(self, fn: HookFn) -> HookFn:
        self._inject.append(fn)
        return fn

    def register_write_job(self, fn: HookFn) -> HookFn:
        self._write.append(fn)
        return fn

    def register_tidy_job(self, fn: HookFn) -> HookFn:
        self._tidy.append(fn)
        return fn

    def dispatch_inject(self, event: Any = None) -> None:
        self._run("inject", self._inject, event)

    def dispatch_write_job(self, event: Any = None) -> None:
        self._run("write_job", self._write, event)

    def dispatch_tidy_job(self, event: Any = None) -> None:
        self._run("tidy_job", self._tidy, event)

    def _run(self, kind: str, fns: list[HookFn], event: Any) -> None:
        self.dispatch_log.append(f"{kind}:{event!r}")
        for fn in fns:
            fn(event)


#: Process-wide default registry (CLI / wiring convenience). Tests use a fresh
#: ``HookRegistry()`` for isolation.
default = HookRegistry()

register_inject_hook = default.register_inject_hook
register_write_job = default.register_write_job
register_tidy_job = default.register_tidy_job
dispatch_inject = default.dispatch_inject
dispatch_write_job = default.dispatch_write_job
dispatch_tidy_job = default.dispatch_tidy_job
