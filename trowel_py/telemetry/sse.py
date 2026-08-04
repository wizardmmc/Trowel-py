"""在现有 SSE 生成器旁记录建连、首事件、断线、重连和关闭。"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from datetime import UTC, datetime

from trowel_py.telemetry.events import emit_metric, emit_span
from trowel_py.telemetry.port import TelemetryPort


class SseConnectionTracker:
    """用有界会话摘要判断持续事件流是否属于重新连接。"""

    def __init__(self, port: TelemetryPort, *, capacity: int = 256) -> None:
        """保存遥测端口和最多保留的近期连接身份数量。

        Args:
            port: 当前应用持有的非阻塞遥测端口。
            capacity: 进程内最多保存的不可逆会话摘要数。
        """

        self._port = port
        self._capacity = max(capacity, 1)
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()

    def begin(
        self,
        session_ref: str,
        *,
        reconnect_eligible: bool,
        started_at: datetime | None = None,
    ) -> "SseObservation":
        """开始观察一条流，并在近期见过同一会话时标记重连。

        Args:
            session_ref: 只在进程内摘要的 Trowel 会话身份。
            reconnect_eligible: 持续 GET watcher 才参与重连判断。
            started_at: 测试可注入的建连墙钟时刻。

        Returns:
            与原生成器并行记录阶段、但不读取事件的观察器。
        """

        reconnect = False
        if reconnect_eligible:
            key = hashlib.sha256(session_ref.encode("utf-8")).hexdigest()[:20]
            with self._lock:
                reconnect = key in self._seen
                self._seen[key] = None
                self._seen.move_to_end(key)
                while len(self._seen) > self._capacity:
                    self._seen.popitem(last=False)
        return SseObservation(
            self._port,
            reconnect=reconnect,
            started_at=started_at or datetime.now(UTC),
        )


class SseObservation:
    """记录一条 SSE 生成器的阶段，不创建第二个 reader。"""

    def __init__(
        self,
        port: TelemetryPort,
        *,
        reconnect: bool,
        started_at: datetime,
    ) -> None:
        """立即记录建连，并在需要时记录重连事实。

        Args:
            port: 当前应用持有的非阻塞遥测端口。
            reconnect: 是否是近期同一 watcher 的再次连接。
            started_at: 当前连接开始时刻。
        """

        self._port = port
        self._started_at = started_at
        self._first_event_recorded = False
        self._disconnected = False
        self._closed = False
        common = {"quality": "reliable", "transport": "sse"}
        emit_span(
            port,
            component="fastapi",
            operation="sse.connect",
            started_at=started_at,
            ended_at=started_at,
            attributes=common,
        )
        if reconnect:
            emit_span(
                port,
                component="fastapi",
                operation="sse.reconnect",
                started_at=started_at,
                ended_at=started_at,
                attributes=common,
            )
            emit_metric(
                port,
                component="fastapi",
                name="sse.reconnect",
                kind="counter",
                unit="1",
                value=1,
                operation="sse.reconnect",
                observed_at=started_at,
                attributes=common,
            )

    def first_event(self, observed_at: datetime | None = None) -> None:
        """首个实际产出事件时记录一次从建连到首事件的耗时。

        Args:
            observed_at: 测试可注入的首事件时刻。
        """

        if self._first_event_recorded:
            return
        self._first_event_recorded = True
        emit_span(
            self._port,
            component="fastapi",
            operation="sse.first_event",
            started_at=self._started_at,
            ended_at=observed_at or datetime.now(UTC),
            attributes={"quality": "reliable", "transport": "sse"},
        )

    def disconnect(self, observed_at: datetime | None = None) -> None:
        """客户端取消或传输异常时记录断线，并保持原取消异常向上传播。

        Args:
            observed_at: 测试可注入的断线时刻。
        """

        if self._closed or self._disconnected:
            return
        self._disconnected = True
        stamp = observed_at or datetime.now(UTC)
        common = {
            "quality": "reliable",
            "transport": "sse",
            "error_category": "cancelled",
        }
        emit_span(
            self._port,
            component="fastapi",
            operation="sse.disconnect",
            started_at=stamp,
            ended_at=stamp,
            status="error",
            attributes=common,
        )
        emit_metric(
            self._port,
            component="fastapi",
            name="sse.disconnect",
            kind="counter",
            unit="1",
            value=1,
            status="error",
            operation="sse.disconnect",
            observed_at=stamp,
            attributes=common,
        )

    def close(
        self,
        *,
        error: bool = False,
        observed_at: datetime | None = None,
    ) -> None:
        """幂等记录流生成器结束，不改变下游事件终态。

        Args:
            error: 生成器是否因服务错误结束。
            observed_at: 测试可注入的关闭时刻。
        """

        if self._closed:
            return
        self._closed = True
        emit_span(
            self._port,
            component="fastapi",
            operation="sse.close",
            started_at=self._started_at,
            ended_at=observed_at or datetime.now(UTC),
            status="error" if error else "ok",
            attributes={"quality": "reliable", "transport": "sse"},
        )
