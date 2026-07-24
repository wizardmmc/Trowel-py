"""内存中的跨 provider 额度快照，不跨进程重启持久化。

过期的正常快照保留窗口并标记为 ``STALE``；已有错误状态不改写。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace

from trowel_py.quota.types import Provider, QuotaSnapshot, QuotaStatus

# 过期阈值留出两个默认轮询周期，避免正常调度抖动造成误报。
DEFAULT_STALE_AFTER_MS = 600_000


def _default_now_ms() -> int:
    return int(time.time() * 1000)


class QuotaReadModel:
    def __init__(
        self,
        *,
        now_ms: Callable[[], int] | None = None,
        stale_after_ms: int = DEFAULT_STALE_AFTER_MS,
    ) -> None:
        self._latest: dict[tuple[Provider, str], QuotaSnapshot] = {}
        self._now_ms = now_ms or _default_now_ms
        self._stale_after_ms = stale_after_ms

    def update(self, snapshot: QuotaSnapshot) -> None:
        self._latest[(snapshot.provider, snapshot.account_id)] = snapshot

    def get(self, provider: Provider, account_id: str) -> QuotaSnapshot | None:
        snap = self._latest.get((provider, account_id))
        if snap is None:
            return None
        if (
            snap.status is QuotaStatus.OK
            and int(self._now_ms()) - snap.fetched_at > self._stale_after_ms
        ):
            return replace(snap, status=QuotaStatus.STALE)
        return snap

    def all(self) -> tuple[QuotaSnapshot, ...]:
        # 先复制 key，避免其他线程写入时迭代器失效。
        return tuple(
            snap
            for key in tuple(self._latest.copy())
            if (snap := self.get(*key)) is not None
        )

    def mark_stale(self, provider: Provider, account_id: str) -> None:
        """只把正常快照标记为过期，并保留最近一次窗口数据。"""
        snap = self._latest.get((provider, account_id))
        if snap is not None and snap.status is QuotaStatus.OK:
            self._latest[(provider, account_id)] = replace(
                snap, status=QuotaStatus.STALE
            )
