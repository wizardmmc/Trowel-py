"""验证资源 owner 的成功和残留终态都转换成受控遥测。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trowel_py.resource_lifecycle import OwnerCloseObservation, OwnerScope
from trowel_py.telemetry.resource_events import create_owner_close_observer


class RecordingPort:
    """记录资源 observer 发出的 span 与 metric。"""

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


def test_owner_observer_preserves_closed_and_needs_reconcile_outcomes() -> None:
    """残留终态必须是 error，且 gauge 保留实际残留数量。"""

    port = RecordingPort()
    observe = create_owner_close_observer(port)
    started_at = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    observe(
        OwnerCloseObservation(
            owner_scope=OwnerScope.SESSION,
            status="closed",
            started_at=started_at,
            completed_at=started_at + timedelta(milliseconds=20),
            closed_resource_count=2,
            remaining_resource_count=0,
        )
    )
    observe(
        OwnerCloseObservation(
            owner_scope=OwnerScope.RUNTIME_CONNECTION,
            status="needs_reconcile",
            started_at=started_at,
            completed_at=started_at + timedelta(seconds=1),
            closed_resource_count=0,
            remaining_resource_count=3,
        )
    )

    assert [span.status for span in port.spans] == ["ok", "error"]
    assert [metric.status for metric in port.metrics] == ["ok", "error"]
    assert [metric.value for metric in port.metrics] == [0, 3]
    assert port.spans[1].attributes.error_category == "unavailable"
