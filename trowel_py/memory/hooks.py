"""Memory 的进程内同步 hook registry。"""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import Any

HookFn = Callable[[Any], None]


class HookRegistry:
    def __init__(self) -> None:
        self._inject: list[HookFn] = []
        self._write: list[HookFn] = []
        self._tidy: list[HookFn] = []
        self._registration_lock = Lock()
        self.dispatch_log: list[str] = []

    def register_inject_hook(self, fn: HookFn) -> HookFn:
        return self._register(self._inject, fn)

    def register_write_job(self, fn: HookFn) -> HookFn:
        return self._register(self._write, fn)

    def register_tidy_job(self, fn: HookFn) -> HookFn:
        return self._register(self._tidy, fn)

    def _register(self, fns: list[HookFn], fn: HookFn) -> HookFn:
        # scheduler worker threads 可同时注册，检查与追加必须处于同一临界区。
        with self._registration_lock:
            if fn not in fns:
                fns.append(fn)
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


# 进程级默认 registry；CLI 和测试可注入独立实例。
default = HookRegistry()

register_inject_hook = default.register_inject_hook
register_write_job = default.register_write_job
register_tidy_job = default.register_tidy_job
dispatch_inject = default.dispatch_inject
dispatch_write_job = default.dispatch_write_job
dispatch_tidy_job = default.dispatch_tidy_job
