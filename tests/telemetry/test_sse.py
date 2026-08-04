"""验证 SSE 观察器不读取事件也能稳定记录断线和重连。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trowel_py.telemetry.sse import SseConnectionTracker


class RecordingPort:
    """记录 SSE 测试发出的 span 与 metric。"""

    def __init__(self) -> None:
        """创建空事实列表。"""

        self.spans = []
        self.metrics = []

    def emit_span(self, span):
        """保存一条 span。"""

        self.spans.append(span)
        return None

    def emit_metric(self, metric):
        """保存一条 metric。"""

        self.metrics.append(metric)
        return None


def test_tracker_records_first_event_disconnect_and_reconnect_without_ids() -> None:
    """同一会话第二次 watcher 建连形成重连，事实中不保存会话 ID。"""

    port = RecordingPort()
    tracker = SseConnectionTracker(port)
    start = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)

    first = tracker.begin("private-session", reconnect_eligible=True, started_at=start)
    first.first_event(start + timedelta(milliseconds=25))
    first.first_event(start + timedelta(milliseconds=50))
    first.disconnect(start + timedelta(seconds=1))
    first.disconnect(start + timedelta(seconds=1, milliseconds=50))
    first.close(observed_at=start + timedelta(seconds=1))
    second = tracker.begin(
        "private-session",
        reconnect_eligible=True,
        started_at=start + timedelta(seconds=2),
    )
    second.close(observed_at=start + timedelta(seconds=3))

    operations = [span.operation for span in port.spans]
    assert operations.count("sse.first_event") == 1
    assert operations.count("sse.reconnect") == 1
    assert operations.count("sse.disconnect") == 1
    assert [metric.name for metric in port.metrics].count("sse.disconnect") == 1
    assert "private-session" not in "".join(span.model_dump_json() for span in port.spans)
