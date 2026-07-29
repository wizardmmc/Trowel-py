"""Memory 的进程内同步 hook registry。"""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import Any

HookFn = Callable[[Any], None]


class HookRegistry:
    """按用途登记并同步派发进程内 Memory hook。

    登记锁只保护查重和追加；派发直接遍历共享列表，执行 hook 和写入
    ``dispatch_log`` 时均不持锁。并发登记本身可安全查重；登记与派发并发
    时，本次派发可见的 hook 集合不确定。若要求集合固定，或多个线程可能同时
    派发，调用方须自行串行化。
    """

    def __init__(self) -> None:
        """创建与模块级默认实例隔离的空 registry。"""
        self._inject: list[HookFn] = []
        self._write: list[HookFn] = []
        self._tidy: list[HookFn] = []
        self._registration_lock = Lock()
        self.dispatch_log: list[str] = []

    def register_inject_hook(self, fn: HookFn) -> HookFn:
        """幂等登记会话注入 hook，并返回传入的 callable。"""
        return self._register(self._inject, fn)

    def register_write_job(self, fn: HookFn) -> HookFn:
        """幂等登记 Memory 写入任务，并返回传入的 callable。"""
        return self._register(self._write, fn)

    def register_tidy_job(self, fn: HookFn) -> HookFn:
        """幂等登记 Memory 整理任务，并返回传入的 callable。"""
        return self._register(self._tidy, fn)

    def _register(self, fns: list[HookFn], fn: HookFn) -> HookFn:
        """按 callable 相等性查重，并在共享登记锁内追加。"""
        # 查重和追加必须处于同一临界区，避免并发登记彼此相等的 callable 时重复追加。
        with self._registration_lock:
            if fn not in fns:
                fns.append(fn)
        return fn

    def dispatch_inject(self, event: Any = None) -> None:
        """把事件同步传给全部会话注入 hook。"""
        self._run("inject", self._inject, event)

    def dispatch_write_job(self, event: Any = None) -> None:
        """把事件同步传给全部 Memory 写入任务。"""
        self._run("write_job", self._write, event)

    def dispatch_tidy_job(self, event: Any = None) -> None:
        """把事件同步传给全部 Memory 整理任务。"""
        self._run("tidy_job", self._tidy, event)

    def _run(self, kind: str, fns: list[HookFn], event: Any) -> None:
        """先把事件的 repr 记入日志，再按登记顺序同步调用 hook。

        原事件对象会传给每个 hook。即使列表为空也会记录；hook 异常不做隔离，
        会停止后续调用并原样传播。
        """
        self.dispatch_log.append(f"{kind}:{event!r}")
        for fn in fns:
            fn(event)


# 进程级默认 registry；下方模块函数在导入时绑定到该实例。
default = HookRegistry()

register_inject_hook = default.register_inject_hook
register_write_job = default.register_write_job
register_tidy_job = default.register_tidy_job
dispatch_inject = default.dispatch_inject
dispatch_write_job = default.dispatch_write_job
dispatch_tidy_job = default.dispatch_tidy_job
