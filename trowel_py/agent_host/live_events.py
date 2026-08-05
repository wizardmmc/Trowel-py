"""为 renderer 提供应用级 AgentEvent 多路复用与慢消费者隔离。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ApplicationEventDelivery:
    """表示应用事件订阅的一次事件投递或单会话缺口通知。

    Attributes:
        event: 正常投递的 AgentEvent wire 字典；缺口通知时为 None。
        gapped_session_id: 因订阅队列溢出而丢过事件的用户会话 ID；正常投递时
            为 None。
    """

    event: dict[str, Any] | None = None
    gapped_session_id: str | None = None


class ApplicationEventSubscription:
    """保存一个 renderer 的有界事件队列，并按 session 报告局部缺口。

    Attributes:
        queue_capacity: 最多保留的普通 AgentEvent 数量。达到上限时淘汰最旧事件，
            但先向接收方报告被淘汰事件所属的会话，供其单独对账。
    """

    def __init__(self, queue_capacity: int) -> None:
        """创建尚未关闭的应用事件订阅。

        Args:
            queue_capacity: 普通事件队列容量，必须为正数。

        Raises:
            ValueError: queue_capacity 小于 1。
        """

        if queue_capacity < 1:
            raise ValueError("application event queue capacity must be positive")
        self.queue_capacity = queue_capacity
        self._events: deque[dict[str, Any]] = deque()
        self._gapped_session_ids: deque[str] = deque()
        self._pending_gaps: set[str] = set()
        self._wake = asyncio.Event()
        self._closed = False
        self._on_close: Callable[[], None] | None = None

    def bind_close(self, callback: Callable[[], None]) -> None:
        """登记订阅关闭时执行一次的移除回调。

        Args:
            callback: 从 broadcaster 订阅集合移除此对象的无参函数。
        """

        self._on_close = callback

    def publish(self, event: dict[str, Any]) -> None:
        """保存一条事件；容量不足时记录被淘汰事件所属的会话缺口。

        Args:
            event: 已校验且包含 session_id 的 AgentEvent wire 字典。
        """

        if self._closed:
            return
        if len(self._events) >= self.queue_capacity:
            dropped = self._events.popleft()
            self._mark_gap(str(dropped["session_id"]))
        self._events.append(event)
        self._wake.set()

    async def receive(self, timeout: float | None = None) -> ApplicationEventDelivery:
        """等待下一条事件或缺口通知；超时只用于发送 SSE heartbeat。

        Args:
            timeout: 最长等待秒数；None 表示一直等待。

        Returns:
            正常事件、局部缺口通知，或 heartbeat 超时时两个字段均为 None 的结果。
        """

        while not self._closed:
            if self._gapped_session_ids:
                session_id = self._gapped_session_ids.popleft()
                self._pending_gaps.discard(session_id)
                return ApplicationEventDelivery(gapped_session_id=session_id)
            if self._events:
                return ApplicationEventDelivery(event=self._events.popleft())
            self._wake.clear()
            try:
                if timeout is None:
                    await self._wake.wait()
                else:
                    await asyncio.wait_for(self._wake.wait(), timeout=timeout)
            except TimeoutError:
                return ApplicationEventDelivery()
        return ApplicationEventDelivery()

    def close(self) -> None:
        """停止本次订阅并从 broadcaster 中移除。"""

        if self._closed:
            return
        self._closed = True
        self._wake.set()
        callback = self._on_close
        self._on_close = None
        if callback is not None:
            callback()

    def _mark_gap(self, session_id: str) -> None:
        """把同一会话的连续溢出合并成一条待对账通知。"""

        if session_id in self._pending_gaps:
            return
        self._pending_gaps.add(session_id)
        self._gapped_session_ids.append(session_id)


class ApplicationEventBroadcaster:
    """把全部 user session 的 AgentEvent 复制给应用级订阅者。"""

    def __init__(self) -> None:
        """创建没有订阅者的 broadcaster。"""

        self._subscriptions: set[ApplicationEventSubscription] = set()

    def subscribe(self, queue_capacity: int) -> ApplicationEventSubscription:
        """创建并登记一个有界应用事件订阅。

        Args:
            queue_capacity: 该 renderer 最多积压的普通事件数。

        Returns:
            可显式关闭的订阅对象。
        """

        subscription = ApplicationEventSubscription(queue_capacity)
        self._subscriptions.add(subscription)
        subscription.bind_close(lambda: self._subscriptions.discard(subscription))
        return subscription

    def publish(self, event: dict[str, Any]) -> None:
        """把一条用户会话事件复制给所有应用订阅者。

        Args:
            event: 已转换为 AgentEvent wire 字典的用户会话事件。
        """

        for subscription in tuple(self._subscriptions):
            subscription.publish(event)
