"""提供不携带 participant 封闭正文的 discussion 状态唤醒总线。"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class _Subscriber:
    """绑定唤醒队列及其唯一允许操作该队列的事件循环。"""

    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[None]


class DiscussionEventSubscription:
    """持有一个 discussion SSE 客户端的有界唤醒队列。"""

    def __init__(
        self,
        bus: DiscussionEventBus,
        discussion_id: str,
        subscriber: _Subscriber,
    ) -> None:
        """记录总线、研讨 ID 和当前订阅队列。

        Args:
            bus: 创建当前订阅的事件总线。
            discussion_id: 所属研讨 ID。
            subscriber: 只传递“有新持久事件”信号的事件循环与容量一队列。
        """

        self._bus = bus
        self._discussion_id = discussion_id
        self._subscriber = subscriber
        self._closed = False

    async def wait(self, *, timeout: float) -> bool:
        """等待数据库可能出现新事件或 heartbeat 到期。

        Args:
            timeout: heartbeat 最长等待秒数。

        Returns:
            收到唤醒信号时为 True；超时为 False。
        """

        try:
            await asyncio.wait_for(self._subscriber.queue.get(), timeout=timeout)
        except TimeoutError:
            return False
        return True

    def close(self) -> None:
        """幂等移除当前订阅。"""

        if self._closed:
            return
        self._closed = True
        self._bus._remove(self._discussion_id, self._subscriber)


class DiscussionEventBus:
    """只做进程内唤醒，事件事实和断线重放仍由 SQLite 提供。"""

    def __init__(self) -> None:
        """创建一个尚无订阅者的事件总线。"""

        self._subscribers: dict[str, set[_Subscriber]] = {}
        self._lock = threading.Lock()

    def subscribe(self, discussion_id: str) -> DiscussionEventSubscription:
        """订阅指定研讨的持久事件变化。

        Args:
            discussion_id: 要等待变化的研讨 ID。

        Returns:
            容量为一且可幂等关闭的订阅。
        """

        subscriber = _Subscriber(
            loop=asyncio.get_running_loop(),
            queue=asyncio.Queue(maxsize=1),
        )
        with self._lock:
            self._subscribers.setdefault(discussion_id, set()).add(subscriber)
        return DiscussionEventSubscription(self, discussion_id, subscriber)

    def publish(self, discussion_id: str) -> None:
        """通知订阅者重新从 SQLite 读取，不携带任何正文。

        Args:
            discussion_id: 已经提交新事件的研讨 ID。
        """

        with self._lock:
            subscribers = tuple(self._subscribers.get(discussion_id, ()))
        for subscriber in subscribers:
            try:
                subscriber.loop.call_soon_threadsafe(self._wake, subscriber.queue)
            except RuntimeError:
                self._remove(discussion_id, subscriber)

    @staticmethod
    def _wake(queue: asyncio.Queue[None]) -> None:
        """在 queue 所属事件循环内合并一次唤醒信号。

        Args:
            queue: 当前 subscriber 的容量一队列。
        """

        if not queue.full():
            queue.put_nowait(None)

    def _remove(self, discussion_id: str, subscriber: _Subscriber) -> None:
        """从内部集合移除一个关闭订阅。

        Args:
            discussion_id: 订阅所属研讨 ID。
            subscriber: 要移除的事件循环与队列记录。
        """

        with self._lock:
            subscribers = self._subscribers.get(discussion_id)
            if subscribers is None:
                return
            subscribers.discard(subscriber)
            if not subscribers:
                self._subscribers.pop(discussion_id, None)
