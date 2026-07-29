"""在进程内保存各模型服务商账号的最新额度快照。

进程重启后数据清空。读取年龄超过阈值的 ``OK`` 快照时，只返回状态为
``STALE`` 的副本，不修改存储内容；其他状态保持不变。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace

from trowel_py.quota.types import Provider, QuotaSnapshot, QuotaStatus

# 默认阈值为两个轮询周期，避免漏掉一次轮询就误报过期。
DEFAULT_STALE_AFTER_MS = 600_000


def _default_now_ms() -> int:
    """返回当前 Unix 时间戳，单位为毫秒。"""

    return int(time.time() * 1000)


class QuotaReadModel:
    """按服务商和账号保存最新额度快照，并在读取时判断是否过期。"""

    def __init__(
        self,
        *,
        now_ms: Callable[[], int] | None = None,
        stale_after_ms: int = DEFAULT_STALE_AFTER_MS,
    ) -> None:
        """配置判断快照是否过期所用的时钟和最长有效时间。

        Args:
            now_ms: 返回当前 Unix 毫秒时间戳的函数；为 None 时读取系统时间。
            stale_after_ms: 判断 ``OK`` 快照过期的年龄阈值，单位为毫秒；年龄
                等于该值时仍有效，超过后读取为 ``STALE``。
        """

        self._latest: dict[tuple[Provider, str], QuotaSnapshot] = {}
        self._now_ms = now_ms or _default_now_ms
        self._stale_after_ms = stale_after_ms

    def update(self, snapshot: QuotaSnapshot) -> None:
        """用新快照替换同一服务商、同一账号的旧快照。"""

        self._latest[(snapshot.provider, snapshot.account_id)] = snapshot

    def get(self, provider: Provider, account_id: str) -> QuotaSnapshot | None:
        """返回指定服务商账号的最新快照，并按当前时间计算过期状态。

        Args:
            provider: 账号所属的模型服务商。
            account_id: Trowel 用来区分额度账号的本地 ID。

        Returns:
            尚无记录时为 None。``OK`` 快照年龄超过阈值时，返回仅把状态改为
            ``STALE`` 的副本，不修改存储内容；其余情况返回存储的快照。
        """

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
        """返回所有已记录账号的最新快照，并逐个计算过期状态。"""

        # 固定本次遍历的账号键，避免其他线程写入时触发字典大小变化错误。
        return tuple(
            snap
            for key in tuple(self._latest.copy())
            if (snap := self.get(*key)) is not None
        )

    def mark_stale(self, provider: Provider, account_id: str) -> None:
        """把指定账号在存储中的 ``OK`` 快照标记为 ``STALE``。

        窗口等其他字段保持不变；尚无快照或状态不是 ``OK`` 时不修改存储内容。

        Args:
            provider: 账号所属的模型服务商。
            account_id: Trowel 用来区分额度账号的本地 ID。
        """

        snap = self._latest.get((provider, account_id))
        if snap is not None and snap.status is QuotaStatus.OK:
            self._latest[(provider, account_id)] = replace(
                snap, status=QuotaStatus.STALE
            )
