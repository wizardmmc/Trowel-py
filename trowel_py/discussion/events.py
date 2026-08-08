"""广播研讨持久状态唤醒和不落库的 participant 实时事件。"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

_COALESCED_EVENT_TYPES = frozenset({"text", "thinking"})
_DROPPED_LIVE_EVENT_TYPES = frozenset({"context_usage", "usage_updated"})
_DEFAULT_COALESCE_SECONDS = 0.05


@dataclass(frozen=True)
class DiscussionLiveDelivery:
    """表示一条 SSE 订阅待处理的进程内消息。

    Attributes:
        kind: 持久状态变化、attempt 事件或 attempt 缺口。
        payload: 仅 live/gap 使用的公开事件负载；状态变化时为空。
    """

    kind: Literal["state_changed", "attempt_event", "attempt_gap"]
    payload: dict[str, Any] | None = None


@dataclass(eq=False)
class _Subscriber:
    """保存一条订阅在所属事件循环内可变的有界积压。"""

    loop: asyncio.AbstractEventLoop
    wake: asyncio.Event
    capacity: int
    attempts: deque[dict[str, Any]] = field(default_factory=deque)
    gaps: dict[str, dict[str, Any]] = field(default_factory=dict)
    state_pending: bool = False


class DiscussionEventSubscription:
    """持有一个 discussion SSE 客户端的独立有界积压。"""

    def __init__(
        self,
        bus: DiscussionEventBus,
        discussion_id: str,
        subscriber: _Subscriber,
    ) -> None:
        """记录总线、研讨身份和当前订阅状态。"""

        self._bus = bus
        self._discussion_id = discussion_id
        self._subscriber = subscriber
        self._closed = False

    async def receive(self, *, timeout: float) -> DiscussionLiveDelivery | None:
        """按缺口、实时事件、持久唤醒的优先级读取一条消息。

        Args:
            timeout: heartbeat 最长等待秒数。

        Returns:
            待发送消息；超时返回 None。
        """

        subscriber = self._subscriber
        if not self._has_pending(subscriber):
            try:
                await asyncio.wait_for(subscriber.wake.wait(), timeout=timeout)
            except TimeoutError:
                return None
        if subscriber.gaps:
            _, gap = subscriber.gaps.popitem()
            self._reset_wake(subscriber)
            return DiscussionLiveDelivery("attempt_gap", gap)
        if subscriber.attempts:
            event = subscriber.attempts.popleft()
            self._reset_wake(subscriber)
            return DiscussionLiveDelivery("attempt_event", event)
        if subscriber.state_pending:
            subscriber.state_pending = False
            self._reset_wake(subscriber)
            return DiscussionLiveDelivery("state_changed")
        subscriber.wake.clear()
        return None

    async def wait(self, *, timeout: float) -> bool:
        """兼容只关心唤醒与否的调用方。"""

        return await self.receive(timeout=timeout) is not None

    def close(self) -> None:
        """幂等移除当前订阅。"""

        if self._closed:
            return
        self._closed = True
        self._bus._remove(self._discussion_id, self._subscriber)

    @staticmethod
    def _has_pending(subscriber: _Subscriber) -> bool:
        """判断订阅是否已有无需等待的新消息。"""

        return bool(
            subscriber.gaps or subscriber.attempts or subscriber.state_pending
        )

    @classmethod
    def _reset_wake(cls, subscriber: _Subscriber) -> None:
        """只在积压已清空时复位事件，避免并发入队丢唤醒。"""

        if not cls._has_pending(subscriber):
            subscriber.wake.clear()


class AttemptLiveEventPublisher:
    """把一个 attempt 的高频 AgentEvent 压成有界、连续的展示流。

    文本和思考分片在短窗口内合并；每段思考的首个进度立即发送，后续进度按
    同一窗口只保留最新累计值。纯用量事件不进入 discussion SSE。原始 runtime
    流仍由 coordinator 唯一消费，历史恢复仍读取原生记录。
    """

    def __init__(
        self,
        bus: DiscussionEventBus,
        *,
        discussion_id: str,
        round_number: int,
        participant_id: str,
        attempt_id: str,
        coalesce_seconds: float = _DEFAULT_COALESCE_SECONDS,
    ) -> None:
        """绑定 attempt 定位事实和当前事件循环的短窗口定时器。

        Args:
            bus: 接收压缩后事件的研讨总线。
            discussion_id: 所属研讨 ID。
            round_number: 所属逻辑轮号。
            participant_id: 所属稳定参与者 ID。
            attempt_id: 所属物理尝试 ID。
            coalesce_seconds: 文本或思考分片最长等待秒数。
        """

        if coalesce_seconds < 0:
            raise ValueError("discussion live coalesce window must not be negative")
        self._bus = bus
        self._discussion_id = discussion_id
        self._round_number = round_number
        self._participant_id = participant_id
        self._attempt_id = attempt_id
        self._coalesce_seconds = coalesce_seconds
        self._sequence = 0
        self._pending: dict[str, Any] | None = None
        self._timer: asyncio.TimerHandle | None = None
        self._pending_progress: dict[str, Any] | None = None
        self._progress_timer: asyncio.TimerHandle | None = None
        self._thinking_progress_published = False
        self._closed = False

    def publish(self, event: dict[str, Any]) -> None:
        """接收一条根 turn 事件，并按展示价值立即发送或短暂合并。

        Args:
            event: coordinator 已核对 attempt 根 turn 的统一 AgentEvent。
        """

        if self._closed:
            raise RuntimeError("discussion live publisher is closed")
        event_type = event.get("type")
        if event_type == "thinking_progress":
            if not self._thinking_progress_published:
                self._flush_pending()
                self._emit(event)
                self._thinking_progress_published = True
                return
            self._pending_progress = self._copy_event(event)
            if self._progress_timer is None:
                loop = asyncio.get_running_loop()
                self._progress_timer = loop.call_later(
                    self._coalesce_seconds,
                    self._flush_progress,
                )
            return
        if event_type in _DROPPED_LIVE_EVENT_TYPES:
            return
        self._flush_progress()
        self._thinking_progress_published = False
        if event_type in _COALESCED_EVENT_TYPES and self._text(event) is not None:
            if self._pending is not None and self._can_merge(self._pending, event):
                self._pending = self._merge(self._pending, event)
                return
            self._flush_pending()
            self._pending = self._copy_event(event)
            loop = asyncio.get_running_loop()
            self._timer = loop.call_later(
                self._coalesce_seconds,
                self._flush_pending,
            )
            return
        self._flush_pending()
        self._emit(event)

    def close(self) -> None:
        """幂等冲刷最后一段文字，并取消尚未触发的定时器。"""

        if self._closed:
            return
        self._flush_progress()
        self._flush_pending()
        self._closed = True

    def _flush_pending(self) -> None:
        """把当前合并块作为一条连续 attempt 事件发出。"""

        timer = self._timer
        self._timer = None
        if timer is not None:
            timer.cancel()
        pending = self._pending
        self._pending = None
        if pending is not None:
            self._emit(pending)

    def _flush_progress(self) -> None:
        """发送窗口内最新思考进度，并取消对应定时器。"""

        timer = self._progress_timer
        self._progress_timer = None
        if timer is not None:
            timer.cancel()
        pending = self._pending_progress
        self._pending_progress = None
        if pending is not None:
            self._emit(pending)

    def _emit(self, event: dict[str, Any]) -> None:
        """分配展示序号并交给每订阅隔离的有界总线。"""

        self._sequence += 1
        self._bus.publish_attempt_event(
            discussion_id=self._discussion_id,
            round_number=self._round_number,
            participant_id=self._participant_id,
            attempt_id=self._attempt_id,
            attempt_sequence=self._sequence,
            event=event,
        )

    @classmethod
    def _can_merge(cls, pending: dict[str, Any], event: dict[str, Any]) -> bool:
        """判断两条相邻分片是否属于同一根内容块。"""

        return all(
            pending.get(field) == event.get(field)
            for field in ("session_id", "runtime", "type", "turn_id", "item_id")
        ) and cls._text(event) is not None

    @classmethod
    def _merge(
        cls,
        pending: dict[str, Any],
        event: dict[str, Any],
    ) -> dict[str, Any]:
        """合并正文并保留最新原生事件序号。"""

        pending_payload = dict(pending.get("payload") or {})
        event_payload = dict(event.get("payload") or {})
        pending_payload["text"] = f"{pending_payload.get('text', '')}{event_payload['text']}"
        return {
            **pending,
            "seq": event.get("seq"),
            "payload": pending_payload,
        }

    @staticmethod
    def _copy_event(event: dict[str, Any]) -> dict[str, Any]:
        """复制会被定时器持有的事件和首层 payload。"""

        return {**event, "payload": dict(event.get("payload") or {})}

    @staticmethod
    def _text(event: dict[str, Any]) -> str | None:
        """只接受 payload 中真实字符串分片。"""

        payload = event.get("payload")
        if not isinstance(payload, dict):
            return None
        text = payload.get("text")
        return text if isinstance(text, str) else None


class DiscussionEventBus:
    """广播状态唤醒和 participant 实时事件，不充当第二份历史存储。"""

    def __init__(self, *, attempt_capacity: int = 512) -> None:
        """创建总线并设置每个客户端的实时事件积压上限。

        Args:
            attempt_capacity: 每条订阅最多保留的 participant 事件数。
        """

        if attempt_capacity < 1:
            raise ValueError("discussion attempt event capacity must be positive")
        self._attempt_capacity = attempt_capacity
        self._subscribers: dict[str, set[_Subscriber]] = {}
        self._lock = threading.Lock()

    def subscribe(self, discussion_id: str) -> DiscussionEventSubscription:
        """订阅指定研讨的状态变化和实时 participant 事件。"""

        subscriber = _Subscriber(
            loop=asyncio.get_running_loop(),
            wake=asyncio.Event(),
            capacity=self._attempt_capacity,
        )
        with self._lock:
            self._subscribers.setdefault(discussion_id, set()).add(subscriber)
        return DiscussionEventSubscription(self, discussion_id, subscriber)

    def publish(self, discussion_id: str) -> None:
        """通知订阅者重新从 SQLite 读取持久事件。"""

        self._schedule(discussion_id, self._mark_state_changed)

    def publish_attempt_event(
        self,
        *,
        discussion_id: str,
        round_number: int,
        participant_id: str,
        attempt_id: str,
        attempt_sequence: int,
        event: dict[str, Any],
    ) -> None:
        """广播协调器唯一 reader 已消费的根 turn 事件。

        Args:
            discussion_id: 事件所属研讨。
            round_number: 事件所属逻辑轮。
            participant_id: 事件所属稳定参与者。
            attempt_id: 事件所属物理尝试。
            attempt_sequence: 该 attempt 根事件从 1 开始的连续外发序号。
            event: 统一 AgentEvent 信封；总线不修改其中正文。
        """

        payload = {
            "type": "attempt_event",
            "discussion_id": discussion_id,
            "round_number": round_number,
            "participant_id": participant_id,
            "attempt_id": attempt_id,
            "attempt_sequence": attempt_sequence,
            "event": dict(event),
        }
        self._schedule(
            discussion_id,
            lambda subscriber: self._append_attempt(subscriber, payload),
        )

    def _schedule(self, discussion_id: str, action: Any) -> None:
        """把订阅状态修改调度回各自所属事件循环。"""

        with self._lock:
            subscribers = tuple(self._subscribers.get(discussion_id, ()))
        for subscriber in subscribers:
            try:
                subscriber.loop.call_soon_threadsafe(action, subscriber)
            except RuntimeError:
                self._remove(discussion_id, subscriber)

    @staticmethod
    def _mark_state_changed(subscriber: _Subscriber) -> None:
        """合并重复持久状态唤醒。"""

        subscriber.state_pending = True
        subscriber.wake.set()

    @staticmethod
    def _append_attempt(
        subscriber: _Subscriber,
        payload: dict[str, Any],
    ) -> None:
        """追加实时事件；溢出时独立记录被丢 attempt 的补偿身份。"""

        if len(subscriber.attempts) >= subscriber.capacity:
            dropped = subscriber.attempts.popleft()
            attempt_id = str(dropped["attempt_id"])
            subscriber.gaps[attempt_id] = {
                "type": "attempt_gap",
                "discussion_id": dropped["discussion_id"],
                "round_number": dropped["round_number"],
                "participant_id": dropped["participant_id"],
                "attempt_id": attempt_id,
            }
        subscriber.attempts.append(payload)
        subscriber.wake.set()

    def _remove(self, discussion_id: str, subscriber: _Subscriber) -> None:
        """从内部集合移除一个关闭订阅。"""

        with self._lock:
            subscribers = self._subscribers.get(discussion_id)
            if subscribers is None:
                return
            subscribers.discard(subscriber)
            if not subscribers:
                self._subscribers.pop(discussion_id, None)
