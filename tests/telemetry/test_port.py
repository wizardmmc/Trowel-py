"""验证 Python 组件使用不等待落盘的窄采集 port。"""

from __future__ import annotations

from tests.telemetry.support import span_payload
from trowel_py.telemetry.contracts import TelemetrySpanInput
from trowel_py.telemetry.port import BufferedTelemetryPort, NoopTelemetryPort


class RecordingCollector:
    def __init__(self) -> None:
        self.requests = []

    def submit(self, request):
        self.requests.append(request)
        return type(
            "Result",
            (),
            {
                "accepted": 1,
                "rejected": 0,
                "dropped": 0,
                "duplicate": False,
                "error_categories": {},
            },
        )()


def test_buffered_port_builds_a_versioned_single_record_batch() -> None:
    collector = RecordingCollector()
    port = BufferedTelemetryPort(collector)

    result = port.emit_span(TelemetrySpanInput.model_validate(span_payload()))

    assert result.accepted == 1
    assert len(collector.requests) == 1
    request = collector.requests[0]
    assert request.schema_version == 1
    assert request.source_component == "fastapi"
    assert len(request.spans) == 1


def test_noop_port_preserves_business_flow() -> None:
    result = NoopTelemetryPort().emit_span(
        TelemetrySpanInput.model_validate(span_payload())
    )

    assert result.accepted == 0
    assert result.dropped == 0
